#!/usr/bin/env python3
"""Record CTP NDJSON ticks into a short-retention minute-bar SQLite spool."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import signal
import socket
import sys
import threading
import time

try:
    from .minute_store import MinuteAggregator, MinuteStore, TZ
except ImportError:
    from minute_store import MinuteAggregator, MinuteStore, TZ


RUNNING = True


def stop(*_) -> None:
    global RUNNING
    RUNNING = False


class QuotePublisher:
    """Non-blocking, loopback-only fan-out for tick-driven latest quotes."""

    def __init__(self, host: str, port: int, batch_ms: float) -> None:
        self.host = host
        self.port = port
        self.batch_seconds = max(batch_ms / 1000, 0.02)
        self.pending: dict[str, dict] = {}
        self.condition = threading.Condition()
        self.running = True
        self.ready = threading.Event()
        self.error: OSError | None = None
        self.thread = threading.Thread(target=self._run, name="ctp-quote-publisher", daemon=True)

    def start(self) -> None:
        self.thread.start()
        if not self.ready.wait(timeout=5):
            raise RuntimeError("CTP Tick 行情发布端口启动超时")
        if self.error is not None:
            raise RuntimeError(f"CTP Tick 行情发布端口启动失败: {self.error}")

    def publish(self, quote: dict | None) -> None:
        if quote is None:
            return
        with self.condition:
            self.pending[str(quote["symbol"])] = dict(quote)
            self.condition.notify()

    def close(self) -> None:
        self.running = False
        with self.condition:
            self.condition.notify_all()
        self.thread.join(timeout=2)

    def _run(self) -> None:
        clients: list[socket.socket] = []
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            try:
                listener.bind((self.host, self.port))
            except OSError as error:
                self.error = error
                return
            listener.listen(4)
            listener.setblocking(False)
            self.ready.set()
            next_emit = time.monotonic()
            while self.running:
                try:
                    while True:
                        client, _ = listener.accept()
                        client.settimeout(0.05)
                        clients.append(client)
                except BlockingIOError:
                    pass
                with self.condition:
                    if not self.pending and self.running:
                        self.condition.wait(timeout=self.batch_seconds)
                    if not self.running:
                        break
                    while self.pending and self.running:
                        remaining = next_emit - time.monotonic()
                        if remaining <= 0:
                            break
                        self.condition.wait(timeout=remaining)
                    quotes = list(self.pending.values())
                    self.pending.clear()
                if not quotes:
                    continue
                payload = (
                    json.dumps(
                        {"type": "quotes", "quotes": quotes},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode()
                next_emit = time.monotonic() + self.batch_seconds
                alive: list[socket.socket] = []
                for client in clients:
                    try:
                        client.sendall(payload)
                        alive.append(client)
                    except OSError:
                        client.close()
                clients = alive
        finally:
            self.ready.set()
            listener.close()
            for client in clients:
                client.close()


def record(args) -> None:
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    store = MinuteStore(args.db)
    aggregator = MinuteAggregator()
    backoff = 1
    last_flush = 0.0
    last_purge = 0.0
    disconnected_reported = False
    publisher = QuotePublisher(args.quote_host, args.quote_port, args.quote_batch_ms)
    publisher.start()
    try:
        while RUNNING:
            try:
                with socket.create_connection((args.host, args.port), timeout=10) as stream:
                    stream.settimeout(1)
                    resume = int(store.state("last_tick_seq"))
                    stream.sendall(f"RESUME {resume}\n".encode())
                    print(f"connected to CTP stream at {args.host}:{args.port}; resume={resume}", flush=True)
                    disconnected_reported = False
                    backoff = 1
                    buffer = b""
                    while RUNNING:
                        try:
                            chunk = stream.recv(65536)
                            if not chunk:
                                raise ConnectionError("stream closed")
                            buffer += chunk
                        except socket.timeout:
                            chunk = b""
                        while b"\n" in buffer:
                            raw, buffer = buffer.split(b"\n", 1)
                            if not raw:
                                continue
                            event = json.loads(raw)
                            sequence = int(event.get("seq") or 0)
                            if event.get("type") == "tick":
                                store.upsert(aggregator.process(event), publish=True)
                                publisher.publish(aggregator.last_quote)
                            elif event.get("type") == "status":
                                print(f"gateway state={event.get('state')} reason={event.get('reason')}", flush=True)
                            if sequence:
                                store.set_state("last_tick_seq", str(sequence))
                        now = time.monotonic()
                        if now - last_flush >= args.flush_seconds:
                            store.upsert(
                                aggregator.finalize_stale(
                                    datetime.now(TZ), args.complete_grace_seconds
                                ),
                                publish=True,
                            )
                            store.upsert(aggregator.snapshots(), publish=False)
                            store.upsert_quotes(aggregator.quotes())
                            store.db.commit()
                            last_flush = now
                        if now - last_purge >= 86400:
                            cutoff = (datetime.now(TZ) - timedelta(days=args.retention_days)).isoformat()
                            removed = store.purge_before(cutoff)
                            if removed:
                                print(f"purged {removed} server spool bars", flush=True)
                            last_purge = now
            except (ConnectionError, OSError, ValueError, json.JSONDecodeError) as error:
                if not disconnected_reported:
                    print(f"CTP stream unavailable: {error}; retrying", file=sys.stderr, flush=True)
                    disconnected_reported = True
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
    finally:
        publisher.close()
        store.upsert(aggregator.snapshots(), publish=False)
        store.upsert_quotes(aggregator.quotes())
        store.db.commit()
        store.close()


def follow(args) -> None:
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    store = MinuteStore(args.db)
    cursor = args.after
    def emit(completed: list[dict], quotes: list[dict]) -> bool:
        nonlocal cursor
        if completed:
            cursor = max(int(row["sync_seq"]) for row in completed)
        payload = dict(
            type="bundle",
            completed=completed,
            quotes=quotes,
            generated_at=datetime.now(TZ).isoformat(),
        )
        try:
            print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)
            return True
        except BrokenPipeError:
            return False

    quote_stream: socket.socket | None = None
    buffer = b""
    last_database_emit = 0.0
    last_connect_attempt = 0.0
    try:
        if not emit(store.export(cursor, args.limit), store.quotes()):
            return
        if args.once:
            return
        while RUNNING:
            current = time.monotonic()
            if quote_stream is None and current - last_connect_attempt >= 1:
                last_connect_attempt = current
                try:
                    quote_stream = socket.create_connection(
                        (args.quote_host, args.quote_port), timeout=1
                    )
                    quote_stream.settimeout(0.25)
                    buffer = b""
                except OSError:
                    quote_stream = None
            if quote_stream is not None:
                try:
                    chunk = quote_stream.recv(65536)
                    if not chunk:
                        raise ConnectionError("tick quote stream closed")
                    buffer += chunk
                    while b"\n" in buffer:
                        raw, buffer = buffer.split(b"\n", 1)
                        if not raw:
                            continue
                        payload = json.loads(raw)
                        if not emit([], list(payload.get("quotes") or [])):
                            return
                except socket.timeout:
                    pass
                except (ConnectionError, OSError, ValueError, json.JSONDecodeError):
                    quote_stream.close()
                    quote_stream = None
            current = time.monotonic()
            if current - last_database_emit >= max(args.interval, 0.5):
                completed = store.export(cursor, args.limit)
                fallback_quotes = store.quotes() if quote_stream is None else []
                if completed or fallback_quotes:
                    if not emit(completed, fallback_quotes):
                        return
                last_database_emit = current
    finally:
        if quote_stream is not None:
            quote_stream.close()
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    recording = sub.add_parser("record")
    recording.add_argument("--db", required=True)
    recording.add_argument("--host", default="127.0.0.1")
    recording.add_argument("--port", type=int, default=19001)
    recording.add_argument("--flush-seconds", type=float, default=5)
    recording.add_argument("--quote-host", default="127.0.0.1")
    recording.add_argument("--quote-port", type=int, default=19002)
    recording.add_argument("--quote-batch-ms", type=float, default=100)
    recording.add_argument("--complete-grace-seconds", type=float, default=15)
    recording.add_argument("--retention-days", type=int, default=30)
    exporting = sub.add_parser("export")
    exporting.add_argument("--db", required=True)
    exporting.add_argument("--after", type=int, default=0)
    exporting.add_argument("--limit", type=int, default=5000)
    status = sub.add_parser("status")
    status.add_argument("--db", required=True)
    daily = sub.add_parser("daily")
    daily.add_argument("--db", required=True)
    daily.add_argument("--trading-day")
    following = sub.add_parser("follow")
    following.add_argument("--db", required=True)
    following.add_argument("--after", type=int, default=0)
    following.add_argument("--limit", type=int, default=5000)
    following.add_argument("--interval", type=float, default=2)
    following.add_argument("--quote-host", default="127.0.0.1")
    following.add_argument("--quote-port", type=int, default=19002)
    following.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.command == "record":
        record(args)
        return
    if args.command == "follow":
        follow(args)
        return
    store = MinuteStore(args.db)
    try:
        if args.command == "export":
            for row in store.export(args.after, args.limit):
                print(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        elif args.command == "status":
            print(json.dumps(store.status(), ensure_ascii=False))
        else:
            for row in store.daily_summary(args.trading_day):
                print(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
    finally:
        store.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Incrementally replicate completed CTP minute bars from ECS to the Mac."""

from __future__ import annotations

import argparse
import json
import shlex
import signal
import subprocess
import sys
import time
import urllib.request

try:
    from .minute_store import MinuteStore
except ImportError:
    from minute_store import MinuteStore


RUNNING = True
NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def stop(*_) -> None:
    global RUNNING
    RUNNING = False


def fetch(host: str, remote_db: str, after: int, limit: int) -> list[dict]:
    command = (
        "/usr/bin/python3 /opt/ctp-md/bin/record_stream.py export "
        f"--db {shlex.quote(remote_db)} --after {after} --limit {limit}"
    )
    result = subprocess.run(
        ["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, command],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def ctp_instruments(contracts: list[dict]) -> list[str]:
    """Convert the public contract list to the broker CTP subscription format."""
    exchanges = {"RB": "SHFE", "JM": "DCE", "SM": "CZCE", "J": "DCE", "I": "DCE"}
    instruments: list[str] = []
    for row in contracts:
        product = str(row.get("product") or "").upper()
        symbol = str(row.get("symbol") or "").upper()
        exchange = str(row.get("exchange") or "").upper()
        if product not in exchanges or exchange != exchanges[product] or not symbol.startswith(product):
            continue
        digits = symbol[len(product):]
        if len(digits) != 4 or not digits.isdigit():
            continue
        instrument = f"{product}{digits}"
        if exchange == "CZCE":
            instrument = f"{product}{digits[1:]}"
        elif exchange in {"SHFE", "DCE"}:
            instrument = instrument.lower()
        instruments.append(instrument)
    return sorted(set(instruments), key=lambda value: (value.rstrip("0123456789").lower(), value))


def refresh_instruments(host: str, contracts_url: str) -> bool:
    with NO_PROXY_OPENER.open(contracts_url, timeout=15) as response:
        contracts = json.load(response)
    instruments = ctp_instruments(contracts)
    if not instruments:
        raise ValueError("本地合约接口没有返回五个黑色品种的有效合约")
    content = "# Auto-refreshed from the local TqSdk contract catalogue.\n" + "\n".join(instruments) + "\n"
    existing = subprocess.run(
        ["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host,
         "/bin/cat /etc/ctp-md/instruments.txt"],
        check=True, capture_output=True, text=True, timeout=30,
    ).stdout
    if existing == content:
        return False
    remote_command = (
        "/bin/cat > /etc/ctp-md/instruments.txt.new && "
        "/bin/chown root:root /etc/ctp-md/instruments.txt.new && "
        "/bin/chmod 0644 /etc/ctp-md/instruments.txt.new && "
        "/bin/mv /etc/ctp-md/instruments.txt.new /etc/ctp-md/instruments.txt && "
        "/usr/bin/systemctl try-restart ctp-md-gateway.service"
    )
    subprocess.run(
        ["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host,
         remote_command],
        input=content, check=True, text=True, timeout=30,
    )
    return True


def sync_once(store: MinuteStore, host: str, remote_db: str, limit: int) -> int:
    cursor = int(store.state("remote_sync_seq"))
    total = 0
    while True:
        rows = fetch(host, remote_db, cursor, limit)
        if not rows:
            break
        first_sequence = int(rows[0]["sync_seq"])
        if cursor and first_sequence > cursor + 1:
            raise ValueError(
                f"ECS 缓冲已缺失 sync_seq {cursor + 1}..{first_sequence - 1}；"
                "Mac 离线时间可能超过服务器保留期"
            )
        store.upsert(rows, publish=False)
        cursor = max(int(row["sync_seq"]) for row in rows)
        store.set_state("remote_sync_seq", str(cursor))
        store.db.commit()
        total += len(rows)
        if len(rows) < limit:
            break
    return total


def apply_bundle(store: MinuteStore, payload: dict) -> int:
    rows = list(payload.get("completed") or [])
    quotes = list(payload.get("quotes") or [])
    cursor = int(store.state("remote_sync_seq"))
    if rows:
        first_sequence = int(rows[0]["sync_seq"])
        if cursor and first_sequence > cursor + 1:
            raise ValueError(
                f"ECS 缓冲已缺失 sync_seq {cursor + 1}..{first_sequence - 1}；"
                "Mac 离线时间可能超过服务器保留期"
            )
        store.upsert(rows, publish=False)
        cursor = max(int(row["sync_seq"]) for row in rows)
        store.set_state("remote_sync_seq", str(cursor))
    store.upsert_quotes(quotes)
    store.db.commit()
    return len(rows)


def follow_command(remote_db: str, after: int, limit: int, interval: float, once: bool) -> str:
    command = (
        "/usr/bin/python3 /opt/ctp-md/bin/record_stream.py follow "
        f"--db {shlex.quote(remote_db)} --after {after} --limit {limit} "
        f"--interval {max(interval, 0.5):g}"
    )
    return command + (" --once" if once else "")


def follow_once(store: MinuteStore, host: str, remote_db: str, limit: int) -> int:
    cursor = int(store.state("remote_sync_seq"))
    result = subprocess.run(
        [
            "/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
            host, follow_command(remote_db, cursor, limit, 1, True),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("ECS 实时行情同步未返回唯一数据包")
    return apply_bundle(store, json.loads(lines[0]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--host", default="aliyun-ecs")
    parser.add_argument("--remote-db", default="/var/lib/ctp-md/minute_bars.sqlite3")
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--contracts-url", default="http://127.0.0.1:5174/api/contracts")
    parser.add_argument("--refresh-hours", type=float, default=6)
    parser.add_argument("--skip-contract-refresh", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    store = MinuteStore(args.db)
    backoff = max(args.interval, 1)
    failed = False
    refresh_failed = False
    last_refresh = 0.0
    try:
        while RUNNING:
            current = time.monotonic()
            if (
                not args.skip_contract_refresh
                and current - last_refresh >= max(args.refresh_hours * 3600, 60)
            ):
                try:
                    changed = refresh_instruments(args.host, args.contracts_url)
                    if changed:
                        print("refreshed ECS CTP instrument subscriptions", flush=True)
                    refresh_failed = False
                except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as error:
                    if not refresh_failed:
                        print(f"CTP instrument refresh unavailable: {error}", file=sys.stderr, flush=True)
                        refresh_failed = True
                last_refresh = current
            if args.once:
                count = follow_once(store, args.host, args.remote_db, args.limit)
                if count:
                    print(f"synced {count} completed CTP minute bars", flush=True)
                break
            cursor = int(store.state("remote_sync_seq"))
            command = follow_command(
                args.remote_db, cursor, args.limit, args.interval, False
            )
            process = None
            try:
                process = subprocess.Popen(
                    [
                        "/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                        "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                        args.host, command,
                    ],
                    stdout=subprocess.PIPE,
                    text=True,
                )
                assert process.stdout is not None
                for line in process.stdout:
                    if not RUNNING:
                        break
                    payload = json.loads(line)
                    count = apply_bundle(store, payload)
                    if count:
                        print(f"synced {count} completed CTP minute bars", flush=True)
                    failed = False
                    backoff = max(args.interval, 1)
                    current = time.monotonic()
                    if (
                        not args.skip_contract_refresh
                        and current - last_refresh >= max(args.refresh_hours * 3600, 60)
                    ):
                        changed = refresh_instruments(args.host, args.contracts_url)
                        if changed:
                            print("refreshed ECS CTP instrument subscriptions", flush=True)
                        last_refresh = current
                if RUNNING:
                    raise subprocess.SubprocessError(
                        f"ECS 实时行情流已断开，exit={process.wait(timeout=5)}"
                    )
            except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as error:
                if not failed:
                    print(f"ECS CTP sync unavailable: {error}", file=sys.stderr, flush=True)
                    failed = True
                backoff = min(max(backoff * 2, 5), 120)
                if RUNNING:
                    time.sleep(backoff)
            finally:
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
    finally:
        store.close()


if __name__ == "__main__":
    main()

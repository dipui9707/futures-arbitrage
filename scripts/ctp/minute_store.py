#!/usr/bin/env python3
"""CTP tick normalization, minute aggregation, and SQLite persistence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import math
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Asia/Shanghai")


SCHEMA = """
CREATE TABLE IF NOT EXISTS minute_bars (
    symbol TEXT NOT NULL,
    trading_day TEXT NOT NULL,
    minute TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL,
    turnover REAL NOT NULL,
    open_interest REAL,
    tick_count INTEGER NOT NULL,
    complete INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'ctp',
    sync_seq INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, minute)
);
CREATE INDEX IF NOT EXISTS idx_minute_bars_day ON minute_bars(trading_day, symbol);
CREATE INDEX IF NOT EXISTS idx_minute_bars_sync ON minute_bars(sync_seq);
CREATE TABLE IF NOT EXISTS collector_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS current_quotes (
    symbol TEXT PRIMARY KEY,
    trading_day TEXT NOT NULL,
    price REAL NOT NULL,
    observed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


@dataclass
class MinuteBar:
    symbol: str
    trading_day: str
    minute: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    turnover: float
    open_interest: float | None
    tick_count: int
    complete: int = 0
    source: str = "ctp"
    sync_seq: int = 0
    updated_at: str = ""

    def payload(self) -> dict:
        result = asdict(self)
        result["updated_at"] = datetime.now(TZ).isoformat()
        return result


def finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and abs(number) < 1e100 else None


def normalize_symbol(exchange: str, instrument: str, trading_day: str) -> str:
    exchange = exchange.strip().upper()
    instrument = instrument.strip().upper()
    letters = "".join(ch for ch in instrument if ch.isalpha())
    digits = "".join(ch for ch in instrument if ch.isdigit())
    if exchange == "CZCE" and len(digits) == 3 and len(trading_day) >= 4:
        trading_year = int(trading_day[:4])
        year = (trading_year // 10) * 10 + int(digits[0])
        if year < trading_year - 1:
            year += 10
        elif year > trading_year + 8:
            year -= 10
        digits = f"{year:04d}{digits[1:]}"[-4:]
    return f"{exchange}.{letters}{digits}"


def infer_exchange(instrument: str) -> str:
    product = "".join(ch for ch in instrument if ch.isalpha()).upper()
    return {"RB": "SHFE", "JM": "DCE", "J": "DCE", "I": "DCE", "SM": "CZCE"}.get(
        product, ""
    )


def tick_datetime(tick: dict) -> datetime:
    action_day = str(tick.get("action_day") or "")
    update_time = str(tick.get("update_time") or "")
    millisec = int(tick.get("update_millisec") or 0)
    if len(action_day) == 8 and len(update_time) == 8:
        return datetime.strptime(
            f"{action_day} {update_time}.{millisec:03d}", "%Y%m%d %H:%M:%S.%f"
        ).replace(tzinfo=TZ)
    now = datetime.now(TZ)
    if len(update_time) == 8:
        parsed = datetime.strptime(update_time, "%H:%M:%S").time()
        return datetime.combine(now.date(), parsed, TZ).replace(microsecond=millisec * 1000)
    return now


class MinuteAggregator:
    def __init__(self) -> None:
        self.current: dict[str, MinuteBar] = {}
        self.cumulative: dict[str, tuple[str, int, float]] = {}
        self.latest_quotes: dict[str, dict] = {}
        self.last_quote: dict | None = None

    def process(self, tick: dict) -> list[MinuteBar]:
        self.last_quote = None
        price = finite(tick.get("last_price"))
        instrument = str(tick.get("instrument") or "")
        exchange = str(tick.get("exchange") or "") or infer_exchange(instrument)
        trading_day = str(tick.get("trading_day") or "")
        if price is None or not instrument or not exchange or len(trading_day) != 8:
            return []
        symbol = normalize_symbol(exchange, instrument, trading_day)
        observed = tick_datetime(tick)
        minute = observed.replace(second=0, microsecond=0).isoformat()
        self.last_quote = dict(
            symbol=symbol,
            trading_day=trading_day,
            price=price,
            observed_at=observed.isoformat(),
        )
        self.latest_quotes[symbol] = self.last_quote
        cumulative_volume = max(0, int(tick.get("volume") or 0))
        cumulative_turnover = finite(tick.get("turnover")) or 0.0
        previous = self.cumulative.get(symbol)
        volume_delta = 0
        turnover_delta = 0.0
        if previous and previous[0] == trading_day:
            if cumulative_volume >= previous[1]:
                volume_delta = cumulative_volume - previous[1]
            if cumulative_turnover >= previous[2]:
                turnover_delta = cumulative_turnover - previous[2]
        self.cumulative[symbol] = (trading_day, cumulative_volume, cumulative_turnover)

        finalized: list[MinuteBar] = []
        bar = self.current.get(symbol)
        if bar is not None and minute < bar.minute:
            return finalized
        if bar is None or minute > bar.minute:
            if bar is not None:
                bar.complete = 1
                finalized.append(bar)
            bar = MinuteBar(
                symbol=symbol,
                trading_day=trading_day,
                minute=minute,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=0,
                turnover=0.0,
                open_interest=finite(tick.get("open_interest")),
                tick_count=0,
            )
            self.current[symbol] = bar
        bar.high = max(bar.high, price)
        bar.low = min(bar.low, price)
        bar.close = price
        bar.volume += volume_delta
        bar.turnover += turnover_delta
        bar.open_interest = finite(tick.get("open_interest"))
        bar.tick_count += 1
        return finalized

    def snapshots(self) -> list[MinuteBar]:
        return list(self.current.values())

    def quotes(self) -> list[dict]:
        return list(self.latest_quotes.values())

    def finalize_stale(self, now: datetime, grace_seconds: float = 15) -> list[MinuteBar]:
        cutoff = now - timedelta(seconds=max(grace_seconds, 0))
        completed: list[MinuteBar] = []
        for symbol, bar in list(self.current.items()):
            if datetime.fromisoformat(bar.minute) + timedelta(minutes=1) <= cutoff:
                bar.complete = 1
                completed.append(bar)
                del self.current[symbol]
        return completed

    def finalize_all(self) -> list[MinuteBar]:
        bars = list(self.current.values())
        for bar in bars:
            bar.complete = 1
        self.current.clear()
        return bars


class MinuteStore:
    COLUMNS = (
        "symbol", "trading_day", "minute", "open", "high", "low", "close",
        "volume", "turnover", "open_interest", "tick_count", "complete", "source",
        "sync_seq", "updated_at",
    )

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.executescript(SCHEMA)
        self.db.execute(
            "INSERT OR IGNORE INTO current_quotes(symbol,trading_day,price,observed_at,updated_at) "
            "SELECT bars.symbol,bars.trading_day,bars.close,bars.minute,bars.updated_at "
            "FROM minute_bars bars JOIN ("
            "SELECT symbol,MAX(minute) minute FROM minute_bars GROUP BY symbol"
            ") latest ON latest.symbol=bars.symbol AND latest.minute=bars.minute"
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def state(self, key: str, default: str = "0") -> str:
        row = self.db.execute("SELECT value FROM collector_state WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_state(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO collector_state(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value)
        )

    def upsert(self, bars: list[MinuteBar | dict], publish: bool = False) -> int:
        if not bars:
            return 0
        next_seq = int(self.state("next_sync_seq"))
        placeholders = ",".join("?" for _ in self.COLUMNS)
        updates = ",".join(
            f"{column}=excluded.{column}" for column in self.COLUMNS if column not in ("symbol", "minute")
        )
        sql = (
            f"INSERT INTO minute_bars({','.join(self.COLUMNS)}) VALUES({placeholders}) "
            f"ON CONFLICT(symbol,minute) DO UPDATE SET {updates}"
        )
        with self.db:
            for source in bars:
                data = source.payload() if isinstance(source, MinuteBar) else dict(source)
                if publish and int(data.get("complete", 0)):
                    next_seq += 1
                    data["sync_seq"] = next_seq
                data.setdefault("sync_seq", 0)
                data.setdefault("source", "ctp")
                data["updated_at"] = datetime.now(TZ).isoformat()
                self.db.execute(sql, tuple(data.get(column) for column in self.COLUMNS))
            if publish:
                self.set_state("next_sync_seq", str(next_seq))
        return next_seq

    def export(self, after: int, limit: int) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM minute_bars WHERE complete=1 AND sync_seq>? "
            "ORDER BY sync_seq LIMIT ?", (after, limit)
        ).fetchall()
        return [dict(row) for row in rows]

    def upsert_quotes(self, quotes: list[dict]) -> int:
        if not quotes:
            return 0
        now = datetime.now(TZ).isoformat()
        with self.db:
            self.db.executemany(
                "INSERT INTO current_quotes(symbol,trading_day,price,observed_at,updated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET "
                "trading_day=excluded.trading_day,price=excluded.price,"
                "observed_at=excluded.observed_at,updated_at=excluded.updated_at",
                [
                    (
                        quote["symbol"], quote["trading_day"], quote["price"],
                        quote["observed_at"], now,
                    )
                    for quote in quotes
                ],
            )
        return len(quotes)

    def quotes(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT symbol,trading_day,price,observed_at,updated_at "
            "FROM current_quotes ORDER BY symbol"
        ).fetchall()
        return [dict(row) for row in rows]

    def purge_before(self, minute: str) -> int:
        with self.db:
            cursor = self.db.execute(
                "DELETE FROM minute_bars WHERE complete=1 AND minute<?", (minute,)
            )
        return cursor.rowcount

    def status(self) -> dict:
        row = self.db.execute(
            "SELECT COUNT(*) count, SUM(complete) completed, MIN(minute) oldest, "
            "MAX(minute) latest, MAX(sync_seq) max_sync_seq FROM minute_bars"
        ).fetchone()
        result = dict(row)
        quote = self.db.execute(
            "SELECT COUNT(*) count, MAX(observed_at) latest FROM current_quotes"
        ).fetchone()
        result.update(quote_count=quote["count"], latest_quote=quote["latest"])
        return result

    def daily_summary(self, trading_day: str | None = None) -> list[dict]:
        where = "WHERE complete=1"
        parameters: tuple[str, ...] = ()
        if trading_day:
            where += " AND trading_day=?"
            parameters = (trading_day,)
        rows = self.db.execute(
            f"SELECT * FROM minute_bars {where} ORDER BY symbol,trading_day,minute",
            parameters,
        ).fetchall()
        summaries: list[dict] = []
        for row in rows:
            data = dict(row)
            key = (data["symbol"], data["trading_day"])
            if not summaries or (summaries[-1]["symbol"], summaries[-1]["trading_day"]) != key:
                summaries.append(
                    dict(
                        symbol=data["symbol"],
                        trading_day=data["trading_day"],
                        open=data["open"],
                        high=data["high"],
                        low=data["low"],
                        close=data["close"],
                        volume=data["volume"],
                        turnover=data["turnover"],
                        open_interest=data["open_interest"],
                        minute_count=1,
                    )
                )
                continue
            summary = summaries[-1]
            summary["high"] = max(summary["high"], data["high"])
            summary["low"] = min(summary["low"], data["low"])
            summary["close"] = data["close"]
            summary["volume"] += data["volume"]
            summary["turnover"] += data["turnover"]
            summary["open_interest"] = data["open_interest"]
            summary["minute_count"] += 1
        return summaries

"""Hybrid read-only adapter: SimNow CTP quotes plus TqSdk history/catalogue."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Callable

from .base import MarketDataUnavailable
from .tqsdk import PRODUCTS, TZ, TqSdkAdapter, _market_is_open


def _ctp_symbol(public_symbol: str) -> str:
    product = "".join(character for character in public_symbol if character.isalpha()).upper()
    spec = PRODUCTS.get(product)
    if spec is None:
        raise ValueError(f"SimNow CTP 不支持合约 {public_symbol}")
    return f"{spec['exchange']}.{public_symbol.upper()}"


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)


class SimNowTqSdkAdapter:
    source = "simnow_tqsdk"
    realtime_source = "simnow"
    history_source = "tqsdk"

    def __init__(
        self,
        *,
        historical: Any | None = None,
        database: str | Path | None = None,
        feed_max_age_seconds: float | None = None,
        connection_factory: Callable[[], sqlite3.Connection] | None = None,
    ) -> None:
        self._historical = historical or TqSdkAdapter()
        self._database = Path(
            database or os.getenv("CTP_MINUTE_DATABASE", "data/ctp_minute_bars.sqlite3")
        )
        self._feed_max_age = float(
            feed_max_age_seconds or os.getenv("CTP_MAX_FEED_AGE_SECONDS", "30")
        )
        self._connection_factory = connection_factory
        self._observed_at: datetime | None = None
        self._lock = threading.RLock()

    def _connect(self) -> sqlite3.Connection:
        if self._connection_factory is not None:
            connection = self._connection_factory()
        else:
            if not self._database.is_file():
                raise MarketDataUnavailable("本机 SimNow CTP 行情库尚未就绪")
            connection = sqlite3.connect(
                f"file:{self._database.resolve()}?mode=ro", uri=True, timeout=5
            )
        connection.row_factory = sqlite3.Row
        return connection

    def contracts(self) -> list[dict]:
        return self._historical.contracts()

    def snapshot(self, symbols: list[str], timestamp: float) -> dict:
        requested = {_ctp_symbol(symbol): symbol for symbol in symbols}
        try:
            with closing(self._connect()) as database:
                placeholders = ",".join("?" for _ in requested)
                rows = database.execute(
                    "SELECT symbol,price,observed_at FROM current_quotes "
                    f"WHERE symbol IN ({placeholders})",
                    tuple(requested),
                ).fetchall()
                feed = database.execute(
                    "SELECT MAX(observed_at) FROM current_quotes"
                ).fetchone()[0]
        except sqlite3.Error as exc:
            raise MarketDataUnavailable("本机 SimNow CTP 最新价读取失败") from exc
        by_symbol = {row["symbol"]: row for row in rows}
        missing = [public for ctp, public in requested.items() if ctp not in by_symbol]
        if missing:
            raise MarketDataUnavailable(
                f"SimNow CTP 尚未收到 {', '.join(missing)} 的最新价"
            )
        requested_at = datetime.fromtimestamp(timestamp, TZ)
        feed_at = _parse_time(feed)
        if feed_at is None:
            raise MarketDataUnavailable("SimNow CTP 行情时间无效")
        age = (requested_at - feed_at).total_seconds()
        if _market_is_open(requested_at) and (age > self._feed_max_age or age < -30):
            raise MarketDataUnavailable("SimNow CTP 实时行情已滞后")
        observed = [_parse_time(row["observed_at"]) for row in rows]
        valid_observed = [value for value in observed if value is not None]
        if valid_observed:
            with self._lock:
                newest = max(valid_observed)
                self._observed_at = max(
                    filter(None, (self._observed_at, newest)), default=newest
                )
        quotes = {
            public: {
                "price": float(by_symbol[ctp]["price"]),
                "observed_at": str(by_symbol[ctp]["observed_at"]),
            }
            for ctp, public in requested.items()
        }
        newest = max(valid_observed) if valid_observed else feed_at
        return {"timestamp": newest.isoformat(), "quotes": quotes}

    def prices(self, symbols: list[str], timestamp: float) -> dict[str, float]:
        snapshot = self.snapshot(symbols, timestamp)
        return {
            symbol: float(quote["price"])
            for symbol, quote in snapshot["quotes"].items()
        }

    def quote_history(self, combination, count: int, timestamp: float) -> dict:
        result = self._historical.quote_history(combination, count, timestamp)
        result["current_prices"] = self.prices(
            [combination.leg_a, combination.leg_b], timestamp
        )
        return result

    def bars(self, combination, period: str, count: int, timestamp: float) -> list[dict]:
        return self._historical.bars(combination, period, count, timestamp)

    def seasonality(self, combination, timestamp: float) -> dict:
        return self._historical.seasonality(combination, timestamp)

    def observed_at(self) -> str | None:
        with self._lock:
            return self._observed_at.isoformat() if self._observed_at else None

    def close(self) -> None:
        self._historical.close()

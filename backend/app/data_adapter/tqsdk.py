"""Read-only TqSdk market-data adapter.

One owner thread creates and drives TqApi.  The application exposes no account,
order, position or trade methods; this adapter only requests quotes and K-lines.
"""
from __future__ import annotations

import math
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as clock_time
from pathlib import Path
import threading
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .base import MarketDataUnavailable, spread_value

TZ = ZoneInfo('Asia/Shanghai')
PERIODS = {
    '1m': 60,
    '5m': 300,
    '15m': 900,
    '30m': 1800,
    '60m': 3600,
    '2h': 7200,
    '4h': 14400,
    '1d': 86400,
}
BASE_PERIODS = {
    '1m': 15,
    '5m': 60,
    '15m': 300,
    '30m': 300,
    '60m': 900,
    '2h': 1800,
    '4h': 3600,
    '1d': 3600,
}
PRODUCTS = {
    'RB': {'exchange': 'SHFE', 'tq_product': 'rb', 'name': '螺纹钢'},
    'JM': {'exchange': 'DCE', 'tq_product': 'jm', 'name': '焦煤'},
    'SM': {'exchange': 'CZCE', 'tq_product': 'SM', 'name': '硅锰'},
    'J': {'exchange': 'DCE', 'tq_product': 'j', 'name': '焦炭'},
    'I': {'exchange': 'DCE', 'tq_product': 'i', 'name': '铁矿石'},
}


def _configured_auth() -> tuple[str, str]:
    auth_text = os.getenv('TQSDK_AUTH', '').strip()
    user = os.getenv('TQSDK_USER', '').strip()
    password = os.getenv('TQSDK_PASSWORD', '').strip()
    auth_file = os.getenv('TQSDK_AUTH_FILE', '').strip()
    if not auth_text and not (user and password) and auth_file:
        path = Path(auth_file).expanduser()
        if not path.is_file():
            raise RuntimeError('TQSDK_AUTH_FILE 指向的凭据文件不存在')
        values: dict[str, str] = {}
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            if key.strip() in {'TQSDK_AUTH', 'TQSDK_USER', 'TQSDK_PASSWORD'}:
                values[key.strip()] = value.strip().strip('"').strip("'")
        auth_text = values.get('TQSDK_AUTH', '').strip()
        user = values.get('TQSDK_USER', '').strip()
        password = values.get('TQSDK_PASSWORD', '').strip()
    if auth_text:
        if ',' not in auth_text:
            raise RuntimeError('TQSDK_AUTH 必须采用 user,password 格式')
        user, password = (part.strip() for part in auth_text.split(',', 1))
    if not user or not password:
        raise RuntimeError(
            '缺少 TqSdk 授权；请配置 TQSDK_AUTH、TQSDK_USER/TQSDK_PASSWORD '
            '或 TQSDK_AUTH_FILE'
        )
    return user, password


def create_api():
    from tqsdk import TqApi, TqAuth

    user, password = _configured_auth()
    return TqApi(auth=TqAuth(user, password), disable_print=True)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _quote_value(quote: Any, field: str) -> Any:
    return quote.get(field) if isinstance(quote, dict) else getattr(quote, field, None)


def _quote_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)


def _public_contract(product: str, full_symbol: str, current_year: int) -> str | None:
    spec = PRODUCTS[product]
    instrument = str(full_symbol).split('.', 1)[-1]
    prefix = str(spec['tq_product'])
    if not instrument.lower().startswith(prefix.lower()):
        return None
    digits = instrument[len(prefix):]
    if not digits.isdigit():
        return None
    if spec['exchange'] == 'CZCE' and len(digits) == 3:
        year_digit = int(digits[0])
        decade = current_year // 10 * 10
        year = min(
            (decade - 10 + year_digit, decade + year_digit, decade + 10 + year_digit),
            key=lambda candidate: abs(candidate - current_year),
        )
        digits = f'{year % 100:02d}{int(digits[-2:]):02d}'
    if len(digits) != 4:
        return None
    return f'{product}{digits}'


def _market_is_open(now: datetime) -> bool:
    local = now.astimezone(TZ)
    current = local.time()
    if current >= clock_time(21):
        return local.weekday() in {0, 1, 2, 3, 6}
    if local.weekday() >= 5:
        return False
    return (
        clock_time(9) <= current < clock_time(10, 15)
        or clock_time(10, 30) <= current < clock_time(11, 30)
        or clock_time(13, 30) <= current < clock_time(15)
    )


class TqSdkAdapter:
    source = 'tqsdk'

    def __init__(
        self,
        *,
        api_factory: Callable[[], Any] | None = None,
        timeout_seconds: float | None = None,
        contract_cache_seconds: float = 300,
        quote_max_age_seconds: float | None = None,
    ) -> None:
        self._api_factory = api_factory or create_api
        self._timeout = float(timeout_seconds or os.getenv('TQSDK_TIMEOUT_SECONDS', '8'))
        self._contract_cache_seconds = float(contract_cache_seconds)
        self._quote_max_age = float(
            quote_max_age_seconds or os.getenv('TQSDK_MAX_QUOTE_AGE_SECONDS', '120')
        )
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='tqsdk-owner')
        self._lock = threading.RLock()
        self._closed = False
        self._api = None
        self._contracts_cache: tuple[float, list[dict]] | None = None
        self._public_to_tq: dict[str, str] = {}
        self._quotes: dict[str, Any] = {}
        self._serials: dict[tuple[tuple[str, ...], int, int], Any] = {}
        self._observed_at: datetime | None = None
        try:
            self._api = self._executor.submit(self._api_factory).result()
        except Exception:
            self._closed = True
            self._executor.shutdown(wait=False, cancel_futures=True)
            raise

    def _reset_on_owner(self) -> None:
        close = getattr(self._api, 'close', None)
        if callable(close):
            close()
        self._api = self._api_factory()
        self._contracts_cache = None
        self._public_to_tq.clear()
        self._quotes.clear()
        self._serials.clear()
        self._observed_at = None

    def _invoke(self, method_name: str, *args):
        with self._lock:
            if self._closed:
                raise MarketDataUnavailable('TqSdk 行情连接已关闭')

            def run():
                method = getattr(self, method_name)
                try:
                    return method(*args)
                except ValueError:
                    raise
                except Exception:
                    self._reset_on_owner()
                    return method(*args)

            try:
                return self._executor.submit(run).result()
            except ValueError:
                raise
            except Exception as exc:
                raise MarketDataUnavailable('TqSdk 行情连接失败或数据未就绪') from exc

    def _contracts_on_owner(self) -> list[dict]:
        now = time.monotonic()
        if self._contracts_cache and now - self._contracts_cache[0] < self._contract_cache_seconds:
            return [dict(row) for row in self._contracts_cache[1]]
        current_year = datetime.now(TZ).year
        rows: list[dict] = []
        mappings: dict[str, str] = {}
        for product, spec in PRODUCTS.items():
            quote_ids = self._api.query_quotes(
                ins_class='FUTURE',
                exchange_id=spec['exchange'],
                product_id=spec['tq_product'],
                expired=False,
            )
            for full_symbol in list(quote_ids):
                public = _public_contract(product, str(full_symbol), current_year)
                if not public:
                    continue
                mappings[public] = str(full_symbol)
                rows.append(
                    dict(
                        symbol=public,
                        name=spec['name'],
                        exchange=spec['exchange'],
                        product=product,
                    )
                )
        rows.sort(key=lambda row: (list(PRODUCTS).index(row['product']), int(row['symbol'][len(row['product']):])))
        if not rows:
            raise MarketDataUnavailable('TqSdk 未返回可用的未到期期货合约')
        self._public_to_tq = mappings
        self._contracts_cache = (now, rows)
        return [dict(row) for row in rows]

    def contracts(self) -> list[dict]:
        return self._invoke('_contracts_on_owner')

    def _resolve_on_owner(self, symbols: list[str]) -> list[str]:
        self._contracts_on_owner()
        missing = [symbol for symbol in symbols if symbol not in self._public_to_tq]
        if missing:
            raise ValueError(f'当前 TqSdk 合约库不支持 {", ".join(missing)}')
        return [self._public_to_tq[symbol] for symbol in symbols]

    def _wait_quotes_on_owner(self, public_symbols: list[str], timestamp: float) -> list[Any]:
        tq_symbols = self._resolve_on_owner(public_symbols)
        quotes = []
        for symbol in tq_symbols:
            if symbol not in self._quotes:
                self._quotes[symbol] = self._api.get_quote(symbol)
            quotes.append(self._quotes[symbol])
        deadline = time.time() + self._timeout
        if all(_finite(_quote_value(quote, 'last_price')) is not None for quote in quotes):
            # TqSdk quote objects update only while wait_update is driven by
            # their owner thread. Process one bounded update on every refresh.
            self._api.wait_update(deadline=min(time.time() + .5, deadline))
        while time.time() < deadline:
            if all(_finite(_quote_value(quote, 'last_price')) is not None for quote in quotes):
                break
            self._api.wait_update(deadline=min(time.time() + 1, deadline))
        if any(_finite(_quote_value(quote, 'last_price')) is None for quote in quotes):
            raise MarketDataUnavailable('TqSdk 最新价尚未就绪')
        requested = datetime.fromtimestamp(timestamp, TZ)
        observed = [_quote_datetime(_quote_value(quote, 'datetime')) for quote in quotes]
        valid_observed = [value for value in observed if value is not None]
        if valid_observed:
            newest = max(valid_observed)
            self._observed_at = max(filter(None, (self._observed_at, newest)), default=newest)
        if _market_is_open(requested):
            for quote_time in valid_observed:
                age = (requested - quote_time).total_seconds()
                if age > self._quote_max_age or age < -30:
                    raise MarketDataUnavailable('TqSdk 实时行情时间已滞后')
        return quotes

    def _prices_on_owner(self, symbols: list[str], timestamp: float) -> dict[str, float]:
        quotes = self._wait_quotes_on_owner(symbols, timestamp)
        return {
            symbol: float(_quote_value(quote, 'last_price'))
            for symbol, quote in zip(symbols, quotes)
        }

    def prices(self, symbols: list[str], timestamp: float) -> dict[str, float]:
        return self._invoke('_prices_on_owner', symbols, timestamp)

    def _serial_on_owner(self, tq_symbols: list[str], duration: int, length: int):
        key = (tuple(tq_symbols), duration, length)
        if key not in self._serials:
            self._serials[key] = self._api.get_kline_serial(
                tq_symbols,
                duration,
                data_length=length,
            )
        return self._serials[key]

    def _wait_serials_on_owner(self, serials: list[Any]) -> None:
        deadline = time.time() + self._timeout
        while not all(self._api.is_serial_ready(serial) for serial in serials):
            if time.time() >= deadline:
                raise MarketDataUnavailable('TqSdk K 线加载超时')
            self._api.wait_update(deadline=min(time.time() + 1, deadline))

    @staticmethod
    def _valid_records(serial: Any, fields: tuple[str, ...]) -> list[dict]:
        records = []
        for row in serial.to_dict('records'):
            if _finite(row.get('datetime')) is None:
                continue
            if any(_finite(row.get(field)) is None for field in fields):
                continue
            records.append(row)
        return records

    @staticmethod
    def _daily_incomplete(start_ns: int, timestamp: float) -> bool:
        bar_date = datetime.fromtimestamp(start_ns / 1_000_000_000, TZ).date()
        requested = datetime.fromtimestamp(timestamp, TZ)
        if bar_date != requested.date():
            return bar_date > requested.date()
        return requested.time() < clock_time(15)

    def _quote_history_on_owner(self, combination, count: int, timestamp: float) -> dict:
        public = [combination.leg_a, combination.leg_b]
        tq_symbols = self._resolve_on_owner(public)
        quotes = self._wait_quotes_on_owner(public, timestamp)
        serial = self._serial_on_owner(tq_symbols, 86400, count)
        self._wait_serials_on_owner([serial])
        rows = self._valid_records(serial, ('close', 'close1'))
        closes = []
        for row in rows:
            start_ns = int(row['datetime'])
            if not self._daily_incomplete(start_ns, timestamp):
                closes.append(spread_value(combination, float(row['close']), float(row['close1'])))
        if not closes:
            raise MarketDataUnavailable('当前组合没有可用的完整日线')
        current_prices = {
            symbol: float(_quote_value(quote, 'last_price'))
            for symbol, quote in zip(public, quotes)
        }
        previous_prices: dict[str, float] = {}
        for index, (symbol, quote) in enumerate(zip(public, quotes)):
            value = _finite(_quote_value(quote, 'pre_close'))
            if value is None and len(rows) >= 2:
                value = _finite(rows[-2]['close' if index == 0 else 'close1'])
            if value is None:
                raise MarketDataUnavailable('TqSdk 前收盘价尚未就绪')
            previous_prices[symbol] = value
        return dict(
            current_prices=current_prices,
            previous_prices=previous_prices,
            closes=closes,
        )

    def quote_history(self, combination, count: int, timestamp: float) -> dict:
        return self._invoke('_quote_history_on_owner', combination, count, timestamp)

    def _bars_on_owner(self, combination, period: str, count: int, timestamp: float) -> list[dict]:
        target_duration = PERIODS[period]
        base_duration = BASE_PERIODS[period]
        public = [combination.leg_a, combination.leg_b]
        tq_symbols = self._resolve_on_owner(public)
        target = self._serial_on_owner(tq_symbols, target_duration, count)
        factor = 10 if period == '1d' else math.ceil(target_duration / base_duration)
        base_length = min(10000, count * factor + 32)
        base = self._serial_on_owner(tq_symbols, base_duration, base_length)
        self._wait_serials_on_owner([target, base])
        targets = self._valid_records(
            target,
            ('open', 'high', 'low', 'close', 'open1', 'high1', 'low1', 'close1'),
        )[-count:]
        bases = self._valid_records(base, ('close', 'close1'))
        if not targets:
            raise MarketDataUnavailable('当前组合没有可用的同步 K 线')
        base_points = [
            (
                int(row['datetime']),
                spread_value(combination, float(row['close']), float(row['close1'])),
            )
            for row in bases
        ]
        result = []
        now_ns = int(timestamp * 1_000_000_000)
        for index, row in enumerate(targets):
            start_ns = int(row['datetime'])
            duration_ns = int(_finite(row.get('duration')) or target_duration * 1_000_000_000)
            end_ns = (
                int(targets[index + 1]['datetime'])
                if index + 1 < len(targets)
                else start_ns + duration_ns
            )
            open_value = spread_value(combination, float(row['open']), float(row['open1']))
            close_value = spread_value(combination, float(row['close']), float(row['close1']))
            samples = [open_value]
            samples.extend(value for point, value in base_points if start_ns <= point < end_ns)
            samples.append(close_value)
            incomplete = (
                self._daily_incomplete(start_ns, timestamp)
                if period == '1d'
                else start_ns + duration_ns > now_ns
            )
            result.append(
                dict(
                    time=datetime.fromtimestamp(start_ns / 1_000_000_000, TZ).isoformat(),
                    timestamp=start_ns / 1_000_000_000,
                    incomplete=incomplete,
                    open=open_value,
                    high=max(samples),
                    low=min(samples),
                    close=close_value,
                )
            )
        return result

    def bars(self, combination, period: str, count: int, timestamp: float) -> list[dict]:
        return self._invoke('_bars_on_owner', combination, period, count, timestamp)

    def seasonality(self, combination, timestamp: float) -> dict:
        return dict(
            available=False,
            dates=[],
            years=[],
            average=[],
            average_label='',
            note='TqSdk 实盘模式不把固定到期合约拼成五年季节性；待接入同月连续合约映射后再启用。',
        )

    def observed_at(self) -> str | None:
        with self._lock:
            return self._observed_at.isoformat() if self._observed_at else None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True

            def close_on_owner():
                close = getattr(self._api, 'close', None)
                if callable(close):
                    close()

            try:
                self._executor.submit(close_on_owner).result()
            finally:
                self._executor.shutdown(wait=False, cancel_futures=True)

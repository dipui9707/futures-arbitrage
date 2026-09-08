from typing import Protocol
from ..models import Combination

class MarketDataAdapter(Protocol):
    source: str
    def contracts(self) -> list[dict]: ...
    def prices(self, symbols: list[str], timestamp: float) -> dict[str, float]: ...
    def quote_history(self, combination: Combination, count: int, timestamp: float) -> dict: ...
    def bars(self, combination: Combination, period: str, count: int, timestamp: float) -> list[dict]: ...
    def seasonality(self, combination: Combination, timestamp: float) -> dict: ...
    def observed_at(self) -> str | None: ...
    def close(self) -> None: ...

class MarketDataUnavailable(RuntimeError):
    """The configured market-data source cannot provide a trustworthy result."""

def spread_value(c: Combination, a: float, b: float) -> float:
    if c.mode == 'ratio':
        if abs(b) < 1e-12:
            raise ValueError('比价分母为零，无法计算')
        return a / b
    return c.coefficient_a * a - c.coefficient_b * b

def aggregate_samples(values: list[float]) -> dict:
    if not values:
        raise ValueError('同步采样点不能为空')
    return dict(open=values[0], high=max(values), low=min(values), close=values[-1])

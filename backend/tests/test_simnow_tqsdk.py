import tempfile
from datetime import datetime, timedelta
from pathlib import Path
import sys
import unittest
from zoneinfo import ZoneInfo

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "backend"))

from app.data_adapter.base import MarketDataUnavailable
from app.data_adapter.simnow_tqsdk import SimNowTqSdkAdapter
from app.models import Combination
from scripts.ctp.minute_store import MinuteStore


TZ = ZoneInfo("Asia/Shanghai")


class FakeHistorical:
    def __init__(self):
        self.closed = False

    def contracts(self):
        return [
            {"symbol": "RB2610", "exchange": "SHFE", "product": "RB", "name": "螺纹钢"},
            {"symbol": "RB2611", "exchange": "SHFE", "product": "RB", "name": "螺纹钢"},
        ]

    def quote_history(self, combination, count, timestamp):
        return {
            "current_prices": {combination.leg_a: 1, combination.leg_b: 1},
            "previous_prices": {combination.leg_a: 3090, combination.leg_b: 3110},
            "closes": [-15, -18, -20],
        }

    def bars(self, combination, period, count, timestamp):
        return [{"time": "history", "close": -20}]

    def seasonality(self, combination, timestamp):
        return {"available": False}

    def close(self):
        self.closed = True


class SimNowTqSdkAdapterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "ctp.sqlite3"
        self.store = MinuteStore(self.path)
        self.now = datetime(2026, 9, 8, 10, 1, tzinfo=TZ)
        self.store.upsert_quotes([
            {
                "symbol": "SHFE.RB2610",
                "trading_day": "20260908",
                "price": 3102,
                "observed_at": (self.now - timedelta(seconds=2)).isoformat(),
            },
            {
                "symbol": "SHFE.RB2611",
                "trading_day": "20260908",
                "price": 3118,
                "observed_at": (self.now - timedelta(seconds=3)).isoformat(),
            },
        ])
        self.history = FakeHistorical()
        self.adapter = SimNowTqSdkAdapter(
            historical=self.history,
            database=self.path,
            feed_max_age_seconds=30,
        )

    def tearDown(self):
        self.adapter.close()
        self.store.close()
        self.directory.cleanup()

    def test_simnow_prices_and_tqsdk_history(self):
        timestamp = self.now.timestamp()
        self.assertEqual(
            self.adapter.prices(["RB2610", "RB2611"], timestamp),
            {"RB2610": 3102.0, "RB2611": 3118.0},
        )
        snapshot = self.adapter.snapshot(["RB2610", "RB2611"], timestamp)
        self.assertEqual(snapshot["quotes"]["RB2610"]["price"], 3102.0)
        self.assertEqual(
            snapshot["timestamp"], (self.now - timedelta(seconds=2)).isoformat()
        )
        combo = Combination(name="test", leg_a="RB2610", leg_b="RB2611")
        result = self.adapter.quote_history(combo, 250, timestamp)
        self.assertEqual(result["current_prices"], {"RB2610": 3102.0, "RB2611": 3118.0})
        self.assertEqual(result["previous_prices"], {"RB2610": 3090, "RB2611": 3110})
        self.assertEqual(result["closes"], [-15, -18, -20])
        self.assertEqual(self.adapter.bars(combo, "1d", 150, timestamp)[0]["time"], "history")
        self.assertEqual(self.adapter.source, "simnow_tqsdk")
        self.assertTrue(self.adapter.observed_at().startswith("2026-09-08T10:00:58"))

    def test_stale_feed_is_rejected_during_market(self):
        with self.assertRaises(MarketDataUnavailable):
            self.adapter.prices(
                ["RB2610"],
                (self.now + timedelta(minutes=2)).timestamp(),
            )


if __name__ == "__main__":
    unittest.main()

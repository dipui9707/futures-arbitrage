import tempfile
from datetime import datetime
from pathlib import Path
import unittest

from scripts.ctp.minute_store import MinuteAggregator, MinuteStore, infer_exchange, normalize_symbol
from scripts.ctp.sync_from_ecs import apply_bundle, ctp_instruments


class CtpCollectionTests(unittest.TestCase):
    def test_symbol_normalization(self):
        self.assertEqual(normalize_symbol("SHFE", "rb2610", "20260907"), "SHFE.RB2610")
        self.assertEqual(normalize_symbol("DCE", "jm2701", "20260907"), "DCE.JM2701")
        self.assertEqual(normalize_symbol("CZCE", "SM609", "20260907"), "CZCE.SM2609")
        self.assertEqual(normalize_symbol("CZCE", "SM701", "20260907"), "CZCE.SM2701")
        self.assertEqual(infer_exchange("jm2701"), "DCE")
        self.assertEqual(infer_exchange("SM701"), "CZCE")

    def test_ctp_subscription_format(self):
        contracts = [
            {"symbol": "RB2610", "exchange": "SHFE", "product": "RB"},
            {"symbol": "JM2701", "exchange": "DCE", "product": "JM"},
            {"symbol": "SM2609", "exchange": "CZCE", "product": "SM"},
            {"symbol": "AU2612", "exchange": "SHFE", "product": "AU"},
        ]
        self.assertEqual(ctp_instruments(contracts), ["jm2701", "rb2610", "SM609"])

    def test_minute_aggregation_and_incremental_export(self):
        ticks = [
            dict(instrument="rb2610", exchange="SHFE", trading_day="20260908",
                 action_day="20260907", update_time="21:00:01", update_millisec=0,
                 last_price=3200, volume=100, turnover=1_000_000, open_interest=500),
            dict(instrument="rb2610", exchange="SHFE", trading_day="20260908",
                 action_day="20260907", update_time="21:00:30", update_millisec=0,
                 last_price=3202, volume=103, turnover=1_096_060, open_interest=502),
            dict(instrument="rb2610", exchange="SHFE", trading_day="20260908",
                 action_day="20260907", update_time="21:01:01", update_millisec=0,
                 last_price=3199, volume=105, turnover=1_160_040, open_interest=504),
        ]
        aggregator = MinuteAggregator()
        self.assertEqual(aggregator.process(ticks[0]), [])
        self.assertEqual(aggregator.process(ticks[1]), [])
        completed = aggregator.process(ticks[2])
        self.assertEqual(len(completed), 1)
        bar = completed[0]
        self.assertEqual((bar.open, bar.high, bar.low, bar.close), (3200, 3202, 3200, 3202))
        self.assertEqual(bar.volume, 3)
        self.assertAlmostEqual(bar.turnover, 96060)
        self.assertEqual(bar.trading_day, "20260908")
        self.assertTrue(bar.minute.startswith("2026-09-07T21:00:00"))
        quotes = aggregator.quotes()
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0]["symbol"], "SHFE.RB2610")
        self.assertEqual(quotes[0]["price"], 3199)
        self.assertTrue(quotes[0]["observed_at"].endswith("21:01:01+08:00"))

        stale = aggregator.finalize_stale(
            datetime.fromisoformat("2026-09-07T21:02:20+08:00"), grace_seconds=15
        )
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0].minute, "2026-09-07T21:01:00+08:00")
        self.assertEqual(stale[0].complete, 1)

        with tempfile.TemporaryDirectory() as directory:
            store = MinuteStore(Path(directory) / "bars.sqlite3")
            try:
                self.assertEqual(store.upsert(completed, publish=True), 1)
                self.assertEqual(store.upsert_quotes(quotes), 1)
                self.assertEqual(store.quotes()[0]["price"], 3199)
                self.assertEqual(store.status()["quote_count"], 1)
                rows = store.export(0, 100)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["sync_seq"], 1)
                self.assertEqual(store.export(1, 100), [])
                store.upsert(stale, publish=True)
                summary = store.daily_summary("20260908")
                self.assertEqual(len(summary), 1)
                self.assertEqual(summary[0]["open"], 3200)
                self.assertEqual(summary[0]["close"], 3199)
                self.assertEqual(summary[0]["volume"], 5)
                self.assertEqual(summary[0]["minute_count"], 2)

                replica = MinuteStore(Path(directory) / "replica.sqlite3")
                try:
                    copied = apply_bundle(
                        replica,
                        {"completed": rows, "quotes": store.quotes()},
                    )
                    self.assertEqual(copied, 1)
                    self.assertEqual(replica.state("remote_sync_seq"), "1")
                    self.assertEqual(replica.quotes()[0]["price"], 3199)
                finally:
                    replica.close()
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()

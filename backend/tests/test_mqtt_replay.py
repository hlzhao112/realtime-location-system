import unittest
from datetime import datetime, timezone

from app.engine.mqtt_replay import ReplayGuard, timestamp_key


def guard() -> ReplayGuard:
    return ReplayGuard(burst=5, hold_sec=1.0, min_sec=5.0, quiet_sec=2.0, max_sec=30.0)


class ReplayGuardTests(unittest.TestCase):
    def test_timestamp_key_groups_same_second(self):
        arrived = datetime(2026, 11, 5, 2, 0, tzinfo=timezone.utc)
        self.assertEqual(
            timestamp_key("2026-11-05T10:00:00.100+08:00", arrived),
            timestamp_key("2026-11-05T10:00:00.900+08:00", arrived),
        )
        self.assertNotEqual(
            timestamp_key("2026-11-05T10:00:01+08:00", arrived),
            timestamp_key("2026-11-05T10:00:00+08:00", arrived),
        )

    def test_retained_messages_pass_through_a_burst(self):
        g = guard()
        g.arm(0)
        kept = []
        for i in range(8):
            decision, _n = g.offer("2026-11-05T10:00:00Z", "dev/coo/HN", f"r{i}", retain=True, now=0.2)
            self.assertEqual(decision, "accept")
            kept.append(i)
        self.assertEqual(kept, list(range(8)))
        released, _notice = g.poll(1.0)
        self.assertEqual(released, [])

    def test_identical_timestamp_burst_is_discarded(self):
        g = guard()
        g.arm(0)
        decisions = [
            g.offer("2026-11-05T10:00:00Z", "dev/coo/HN", f"b{i}", now=0.2)[0]
            for i in range(8)
        ]
        self.assertEqual(decisions[:4], ["hold"] * 4)
        self.assertEqual(decisions[4:], ["drop"] * 4)
        released, notice = g.poll(6.0)
        self.assertEqual(released, [])
        self.assertIn("8", notice or "")
        self.assertFalse(g.active(6.0))

    def test_small_group_is_released_after_hold(self):
        g = guard()
        g.arm(0)
        for i in range(3):
            decision, _n = g.offer("2026-11-05T10:00:01Z", "dev/coo/HN", f"live{i}", now=0.2)
            self.assertEqual(decision, "hold")
        released, notice = g.poll(1.3)
        self.assertEqual([item[1] for item in released], ["live0", "live1", "live2"])
        self.assertIsNone(notice)
        self.assertTrue(g.active(1.3))

    def test_after_window_same_timestamp_burst_is_accepted(self):
        g = guard()
        g.arm(0)
        for i in range(6):
            g.offer("2026-11-05T09:00:00Z", "dev/coo/HN", f"old{i}", now=0.2)
        g.poll(6.0)
        self.assertFalse(g.active(6.0))
        decisions = [g.offer("2026-11-05T09:00:00Z", "dev/coo/HN", f"again{i}", now=6.5)[0] for i in range(8)]
        self.assertEqual(decisions, ["accept"] * 8)

    def test_live_timestamp_survives_beside_a_burst(self):
        g = guard()
        g.arm(0)
        for i in range(6):
            g.offer("frozen", "dev/coo/HN", f"old{i}", now=0.2)
        for i in range(2):
            g.offer("2026-11-05T11:00:05Z", "dev/coo/HN", f"now{i}", now=0.4)
        released, _notice = g.poll(1.5)
        self.assertEqual([item[1] for item in released], ["now0", "now1"])

    def test_rearm_filters_the_next_reconnect(self):
        g = guard()
        g.arm(0)
        for i in range(6):
            g.offer("frozen", "dev/coo/HN", f"old{i}", now=0.2)
        g.poll(6.0)
        g.arm(10)
        decisions = [g.offer("frozen", "dev/coo/HN", f"next{i}", now=10.2)[0] for i in range(6)]
        self.assertEqual(decisions[-1], "drop")
        released, _notice = g.poll(16.0)
        self.assertEqual(released, [])


if __name__ == "__main__":
    unittest.main()

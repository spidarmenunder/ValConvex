import unittest

from valtracker.util import PerformanceMonitor


class PerformanceMonitorTests(unittest.TestCase):
    def test_samples_are_bounded_and_report_aggregates(self):
        monitor = PerformanceMonitor(report_interval_s=3600, sample_limit=2)
        for duration in (0.01, 0.02, 0.03):
            monitor.record("poll.idle", duration)
        self.assertEqual(len(monitor._samples["poll.idle"]), 2)
        monitor.report_interval_s = 0
        with self.assertLogs("valtracker", level="INFO") as captured:
            monitor.record("ui.navigation", 0.004)
        line = captured.output[0]
        self.assertIn("process CPU", line)
        self.assertIn("ui.navigation:n=1", line)
        self.assertIn("poll.idle:n=2", line)
        self.assertIn("p95=", line)


if __name__ == "__main__":
    unittest.main()

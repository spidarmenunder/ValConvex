import threading
import unittest

from valtracker.services.tasks import BoundedTaskScheduler


class BoundedTaskSchedulerTests(unittest.TestCase):
    def test_queue_bound_deduplication_and_replacement(self):
        scheduler = BoundedTaskScheduler(max_workers=1, max_queued=1)
        started = threading.Event()
        release = threading.Event()
        ran = []

        def blocked():
            started.set()
            release.wait(2)
            ran.append("running")

        try:
            first = scheduler.submit("running", blocked)
            self.assertIsNotNone(first)
            self.assertTrue(started.wait(1))
            queued = scheduler.submit("replace-me", lambda: ran.append("old"))
            self.assertIsNotNone(queued)
            self.assertIs(scheduler.submit("replace-me", lambda: ran.append("duplicate")), queued)
            replaced = scheduler.submit("replace-me", lambda: ran.append("new"), replace=True)
            self.assertIsNotNone(replaced)
            self.assertNotEqual(replaced, queued)
            self.assertIsNone(scheduler.submit("overflow", lambda: None))
            release.set()
            scheduler.shutdown(wait=True, cancel_futures=False)
            self.assertEqual(ran, ["running", "new"])
            self.assertEqual(scheduler.pending_count, 0)
        finally:
            release.set()
            scheduler.shutdown(wait=True)

    def test_shutdown_cancels_queued_work_and_waits_for_active_work(self):
        scheduler = BoundedTaskScheduler(max_workers=1, max_queued=2)
        started = threading.Event()
        release = threading.Event()
        ran = []
        scheduler.submit("active", lambda: (started.set(), release.wait(2)))
        self.assertTrue(started.wait(1))
        queued = scheduler.submit("queued", lambda: ran.append("queued"))
        self.assertIsNotNone(queued)
        scheduler.shutdown(wait=False, cancel_futures=True)
        self.assertTrue(queued.cancelled())
        release.set()
        scheduler.shutdown(wait=True, cancel_futures=True)
        self.assertEqual(ran, [])
        self.assertEqual(scheduler.pending_count, 0)


if __name__ == "__main__":
    unittest.main()

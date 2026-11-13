import queue
import tempfile
import unittest
from unittest.mock import patch

import run
from valtracker.static import StaticData


class OfflineHttp:
    def get(self, *args, **kwargs):
        raise OSError("offline")


class StartupTests(unittest.TestCase):
    def test_offline_startup_reaches_ui_after_early_instance_lock(self):
        events = []
        real_fetch = StaticData.fetch

        class Instance:
            def __init__(self, port):
                events.append("instance-created")

            def acquire(self, callback):
                events.append("lock-acquired")
                return True

            def release(self):
                events.append("lock-released")

        class Service:
            def shutdown(self, wait=True):
                events.append("service-shutdown")

        class App:
            def __init__(self, cfg, db, static, factory, demo=False):
                events.append("app-created")
                self.q = queue.Queue()
                self.service = Service()

            def mainloop(self):
                events.append("mainloop")

        def offline_fetch(cache_dir):
            events.append("static-started")
            data = real_fetch(cache_dir, http=OfflineHttp())
            self.assertFalse(data.has_maps)
            events.append("static-fallback")
            return data

        with tempfile.TemporaryDirectory() as home, \
                patch("valtracker.single_instance.SingleInstance", Instance), \
                patch.object(StaticData, "fetch", side_effect=offline_fetch), \
                patch("valtracker.ui.app.App", App), \
                patch("run.setup_logging"):
            self.assertEqual(run.main(["--home", home]), 0)

        self.assertLess(events.index("lock-acquired"), events.index("static-started"))
        self.assertLess(events.index("static-fallback"), events.index("app-created"))
        self.assertIn("mainloop", events)
        self.assertIn("service-shutdown", events)
        self.assertEqual(events[-1], "lock-released")


if __name__ == "__main__":
    unittest.main()

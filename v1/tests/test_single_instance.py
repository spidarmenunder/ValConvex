import socket
import time
import unittest

from valtracker.single_instance import SingleInstance


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class SingleInstanceTests(unittest.TestCase):
    def test_second_instance_pokes_first(self):
        port = free_port()
        shown = []
        first, second = SingleInstance(port), SingleInstance(port)
        try:
            self.assertTrue(first.acquire(lambda: shown.append(1)))
            self.assertFalse(second.acquire(lambda: shown.append(2)))        # told the first to show itself
            time.sleep(0.2)
            self.assertEqual(shown, [1])
        finally:
            first.release()
            second.release()

    def test_after_release_a_new_instance_can_start(self):
        port = free_port()
        a = SingleInstance(port)
        self.assertTrue(a.acquire(lambda: None))
        a.release()
        time.sleep(0.1)
        b = SingleInstance(port)
        try:
            self.assertTrue(b.acquire(lambda: None))
        finally:
            b.release()

    def test_port_used_by_something_else_does_not_block_startup(self):
        port = free_port()
        other = socket.socket()
        other.bind(("127.0.0.1", port))
        other.listen(1)
        try:
            self.assertTrue(SingleInstance(port).acquire(lambda: None))      # no valid reply -> carry on
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()

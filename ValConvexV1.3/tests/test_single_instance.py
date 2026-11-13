import socket
import threading
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

    def test_simultaneous_launches_have_exactly_one_owner(self):
        port = free_port()
        gate = threading.Barrier(8)
        instances = [SingleInstance(port) for _ in range(8)]
        results = [None] * len(instances)
        callbacks = []

        def acquire(i):
            gate.wait()
            results[i] = instances[i].acquire(lambda: callbacks.append(i))

        threads = [threading.Thread(target=acquire, args=(i,)) for i in range(len(instances))]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=3)
            self.assertEqual(sum(bool(result) for result in results), 1)
            time.sleep(0.2)
            self.assertEqual(len(callbacks), len(instances) - 1)
        finally:
            for instance in instances:
                instance.release()

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

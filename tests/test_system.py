import os
import subprocess
import sys
import time
import unittest

from client.client import Client
from sdn.path_planner import plan_paths
from worker.tasks import OPERATIONS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 5099


def start(*args):
    return subprocess.Popen(
        [sys.executable, "-u", "-m", *args],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def connect():
    for _ in range(50):
        try:
            return Client("127.0.0.1", PORT)
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("server did not start")


class TaskFunctionTests(unittest.TestCase):
    def test_operations(self):
        self.assertEqual(OPERATIONS["factorial"](10), 3628800)
        self.assertTrue(OPERATIONS["prime_check"](99991))
        self.assertFalse(OPERATIONS["prime_check"](100))
        self.assertEqual(OPERATIONS["sum_range"](100), 5050)
        self.assertEqual(OPERATIONS["sort"]([5, 2, 9, 1]), [1, 2, 5, 9])
        self.assertEqual(OPERATIONS["matrix_multiply"]({"a": [[1, 2], [3, 4]], "b": [[5, 6], [7, 8]]}), [[19, 22], [43, 50]])

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            OPERATIONS["factorial"](-1)


class PathPlannerTests(unittest.TestCase):
    def test_heavy_worker_gets_its_own_path(self):
        plan, _ = plan_paths({"w1": 85, "w2": 15, "w3": 60})
        self.assertEqual(plan["w1"], "s2")
        self.assertEqual(plan["w2"], "s3")
        self.assertEqual(plan["w3"], "s3")

    def test_everyone_moves_when_a_path_is_down(self):
        plan, _ = plan_paths({"w1": 85, "w2": 15, "w3": 60}, ["s3"])
        self.assertEqual(set(plan.values()), {"s3"})


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.procs = [start("server.task_server", "--port", str(PORT))]
        self.client = connect()

    def tearDown(self):
        self.client.close()
        for proc in self.procs:
            proc.kill()
            proc.wait()

    def add_worker(self, name, load, delay="0.2"):
        proc = start("worker.worker", "--id", name, "--port", str(PORT), "--load", str(load), "--delay", delay)
        self.procs.append(proc)
        return proc

    def wait_for_workers(self, count):
        for _ in range(50):
            if len(self.client.ask({"type": "STATUS"})["workers"]) == count:
                return
            time.sleep(0.1)
        self.fail("workers did not register")

    def test_least_loaded_worker_gets_the_task(self):
        self.add_worker("busy", 90)
        self.add_worker("idle", 5)
        self.wait_for_workers(2)
        task_id = self.client.submit("factorial", 10)
        finished, _ = self.client.wait_for([task_id], timeout=10)
        self.assertEqual(finished[task_id]["result"], 3628800)
        self.assertEqual(finished[task_id]["worker"], "idle")

    def test_all_tasks_finish(self):
        for i in range(3):
            self.add_worker(f"w{i}", 20 * i)
        self.wait_for_workers(3)
        ids = [self.client.submit("sum_range", n) for n in range(10, 20)]
        finished, pending = self.client.wait_for(ids, timeout=20)
        self.assertFalse(pending)
        self.assertTrue(all(t["status"] == "done" for t in finished.values()))
        self.assertGreater(len({t["worker"] for t in finished.values()}), 1)

    def test_task_is_requeued_when_worker_dies(self):
        doomed = self.add_worker("doomed", 0, delay="5")
        self.wait_for_workers(1)
        task_id = self.client.submit("factorial", 5)
        for _ in range(50):
            if self.client.get_task(task_id)["status"] == "running":
                break
            time.sleep(0.1)
        doomed.kill()
        self.add_worker("backup", 50, delay="0.2")
        finished, _ = self.client.wait_for([task_id], timeout=15)
        self.assertEqual(finished[task_id]["result"], 120)
        self.assertEqual(finished[task_id]["worker"], "backup")
        self.assertEqual(finished[task_id]["attempts"], 2)

    def test_round_robin_takes_turns(self):
        self.tearDown()
        self.procs = [start("server.task_server", "--port", str(PORT), "--policy", "round_robin")]
        self.client = connect()
        self.add_worker("a", 90)
        self.add_worker("b", 0)
        self.wait_for_workers(2)
        ids = [self.client.submit("factorial", 3) for _ in range(4)]
        finished, _ = self.client.wait_for(ids, timeout=20)
        workers = [finished[i]["worker"] for i in ids]
        self.assertEqual(workers.count("a"), 2)
        self.assertEqual(workers.count("b"), 2)

    def test_unknown_operation_is_rejected(self):
        with self.assertRaises(ValueError):
            self.client.submit("fly_to_moon", 1)


if __name__ == "__main__":
    unittest.main()

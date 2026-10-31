import argparse
import json
import random
import socket
import sys
import time

from common.config import DEFAULT_HOST, DEFAULT_PORT
from common.protocol import read_messages, send_msg, short

DEMO_TASKS = [
    ("factorial", 10),
    ("prime_check", 99991),
    ("sort", [5, 2, 9, 1, 7]),
    ("sum_range", 1000000),
    ("matrix_multiply", {"a": [[1, 2], [3, 4]], "b": [[5, 6], [7, 8]]}),
    ("factorial", 20),
    ("prime_check", 1000003),
    ("sort", 100000),
    ("matrix_multiply", 60),
    ("sum_range", 5000000),
]


class Client:
    def __init__(self, host, port):
        self.sock = socket.create_connection((host, port), timeout=10)
        self.messages = read_messages(self.sock)

    def close(self):
        self.sock.close()

    def ask(self, msg):
        send_msg(self.sock, msg)
        reply = next(self.messages, None)
        if reply is None:
            raise ConnectionError("server closed the connection")
        return reply

    def submit(self, operation, data):
        reply = self.ask({"type": "SUBMIT", "operation": operation, "input": data})
        if reply.get("type") != "ACK":
            raise ValueError(reply.get("message"))
        return reply["task_id"]

    def get_task(self, task_id):
        reply = self.ask({"type": "GET_RESULT", "task_id": task_id})
        if reply.get("type") != "TASK_INFO":
            raise ValueError(reply.get("message"))
        return reply["task"]

    def wait_for(self, task_ids, timeout):
        pending = set(task_ids)
        finished = {}
        deadline = time.time() + timeout
        while pending and time.time() < deadline:
            for task_id in sorted(pending):
                task = self.get_task(task_id)
                if task["status"] in ("done", "failed"):
                    finished[task_id] = task
            pending -= finished.keys()
            if pending:
                time.sleep(0.5)
        return finished, pending


def bench_tasks(kind, count):
    if kind == "heavy":
        numbers = [random.randint(0, 1_000_000) for _ in range(100_000)]
        return [("sort", numbers)] * count
    return [DEMO_TASKS[i % len(DEMO_TASKS)] for i in range(count)]


def run_bench(client, kind, count):
    started = time.time()
    ids = [client.submit(operation, data) for operation, data in bench_tasks(kind, count)]
    finished, pending = client.wait_for(ids, timeout=600)
    total = time.time() - started
    tasks = list(finished.values())
    per_worker = {}
    for task in tasks:
        per_worker[task["worker"]] = per_worker.get(task["worker"], 0) + 1
    times = [task["finished"] - task["submitted"] for task in tasks]
    summary = {
        "done": len(tasks),
        "count": count,
        "total_seconds": round(total, 2),
        "avg_task_seconds": round(sum(times) / len(times), 2) if times else None,
        "per_worker": dict(sorted(per_worker.items())),
    }
    print(f"{len(tasks)}/{count} tasks done in {total:.2f}s, average {summary['avg_task_seconds']}s per task")
    print(f"tasks per worker: {summary['per_worker']}")
    print("RESULT " + json.dumps(summary))


def parse_input(text):
    try:
        return json.loads(text)
    except ValueError:
        return text


def print_tasks(tasks):
    print(f"{'id':>4}  {'operation':<16} {'worker':<12} {'status':<8} result")
    for task in tasks:
        outcome = task["error"] if task["error"] else short(task["result"], 50)
        print(f"{task['task_id']:>4}  {task['operation']:<16} {str(task['worker']):<12} {task['status']:<8} {outcome}")


def print_status(reply):
    counts = reply["tasks"]
    print(f"tasks: {counts['pending']} pending, {counts['running']} running, {counts['done']} done, {counts['failed']} failed")
    if not reply["workers"]:
        print("no workers connected")
        return
    for w in reply["workers"]:
        bar = "#" * int(w["load"] // 10)
        print(f"  {w['worker_id']:<10} {w['ip']:<15} load {w['load']:5.1f}% [{bar:<10}]  running {w['active']}/{w['capacity']}")


def main():
    parser = argparse.ArgumentParser(description="send tasks to the task server")
    parser.add_argument("--server", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    sub = parser.add_subparsers(dest="command", required=True)

    submit = sub.add_parser("submit", help="send one task")
    submit.add_argument("operation")
    submit.add_argument("input", help="a number or JSON, e.g. 10 or '[3,1,2]'")
    submit.add_argument("--wait", action="store_true", help="wait and print the result")

    demo = sub.add_parser("demo", help="send a batch of sample tasks and wait for all results")
    demo.add_argument("--repeat", type=int, default=1)

    result = sub.add_parser("result", help="look up one task")
    result.add_argument("task_id", type=int)

    sub.add_parser("status", help="show workers and their load")

    bench = sub.add_parser("bench", help="send a batch, time it and print the numbers")
    bench.add_argument("--kind", choices=["normal", "heavy"], default="normal")
    bench.add_argument("--count", type=int, default=30)

    args = parser.parse_args()

    try:
        client = Client(args.server, args.port)
        if args.command == "submit":
            task_id = client.submit(args.operation, parse_input(args.input))
            print(f"submitted task {task_id}")
            if args.wait:
                finished, _ = client.wait_for([task_id], timeout=120)
                if finished:
                    print_tasks(finished.values())
                else:
                    print("still not finished after 120 seconds")
        elif args.command == "demo":
            ids = []
            for _ in range(max(1, args.repeat)):
                for operation, data in DEMO_TASKS:
                    task_id = client.submit(operation, data)
                    ids.append(task_id)
                    print(f"submitted task {task_id}: {operation}({short(data, 40)})")
            print("waiting for results...\n")
            started = time.time()
            finished, pending = client.wait_for(ids, timeout=180)
            print_tasks(finished[i] for i in sorted(finished))
            print(f"\n{len(finished)}/{len(ids)} tasks finished in {time.time() - started:.1f}s")
            if pending:
                print(f"not finished: {sorted(pending)}")
        elif args.command == "result":
            print_tasks([client.get_task(args.task_id)])
        elif args.command == "bench":
            run_bench(client, args.kind, max(1, args.count))
        elif args.command == "status":
            print_status(client.ask({"type": "STATUS"}))
    except ConnectionRefusedError:
        sys.exit(f"can't reach the task server at {args.server}:{args.port}, is it running?")
    except (OSError, ValueError) as error:
        sys.exit(f"error: {error}")


if __name__ == "__main__":
    main()

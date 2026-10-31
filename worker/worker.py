import argparse
import random
import socket
import sys
import threading
import time

from common.config import DEFAULT_HOST, DEFAULT_PORT, HEARTBEAT_INTERVAL
from common.protocol import read_messages, send_msg, short
from worker.tasks import OPERATIONS

LOG_LOCK = threading.Lock()


class Worker:
    def __init__(self, worker_id, host, port, base_load, capacity, delay, vary):
        self.worker_id = worker_id
        self.host = host
        self.port = port
        self.base_load = base_load
        self.capacity = capacity
        self.delay = delay
        self.vary = vary
        self.active = 0
        self.lock = threading.Lock()
        self.send_lock = threading.Lock()

    def log(self, text):
        with LOG_LOCK:
            sys.stdout.write(f"{time.strftime('%H:%M:%S')} [{self.worker_id}] {text}\n")
            sys.stdout.flush()

    def current_load(self):
        with self.lock:
            active = self.active
        busy = active * (100 - self.base_load) / self.capacity
        jitter = random.uniform(-3, 3)
        return round(max(0.0, min(100.0, self.base_load + busy + jitter)), 1)

    def status_message(self):
        with self.lock:
            active = self.active
        return {
            "type": "STATUS",
            "worker_id": self.worker_id,
            "load": self.current_load(),
            "active": active,
            "status": "available" if active < self.capacity else "busy",
        }

    def send(self, sock, msg):
        try:
            with self.send_lock:
                send_msg(sock, msg)
            return True
        except OSError:
            return False

    def run(self):
        if self.vary:
            threading.Thread(target=self.vary_load, daemon=True).start()
        while True:
            try:
                self.session()
                self.log("lost connection to the server")
            except ConnectionRefusedError:
                self.log(f"server at {self.host}:{self.port} is not reachable")
            except (OSError, ValueError) as error:
                self.log(f"connection error: {error}")
            self.log("trying again in 3 seconds")
            time.sleep(3)

    def session(self):
        sock = socket.create_connection((self.host, self.port), timeout=5)
        sock.settimeout(None)
        stop = threading.Event()
        try:
            self.send(sock, {
                "type": "REGISTER",
                "worker_id": self.worker_id,
                "capacity": self.capacity,
                "load": self.current_load(),
            })
            messages = read_messages(sock)
            reply = next(messages, None)
            if reply is None:
                return
            if reply.get("type") != "REGISTERED":
                raise SystemExit(f"[{self.worker_id}] server refused registration: {reply.get('message')}")
            self.log(f"registered with server {self.host}:{self.port} (base load {self.base_load}%, capacity {self.capacity})")
            threading.Thread(target=self.heartbeat, args=(sock, stop), daemon=True).start()
            for msg in messages:
                if msg.get("type") == "TASK":
                    threading.Thread(target=self.run_task, args=(sock, msg), daemon=True).start()
        finally:
            stop.set()
            sock.close()

    def heartbeat(self, sock, stop):
        while not stop.wait(HEARTBEAT_INTERVAL):
            if not self.send(sock, self.status_message()):
                return

    def run_task(self, sock, msg):
        task_id = msg.get("task_id")
        operation = msg.get("operation")
        data = msg.get("input")
        with self.lock:
            self.active += 1
        self.log(f"got task {task_id}: {operation}({short(data, 40)})")
        self.send(sock, self.status_message())
        started = time.time()
        time.sleep(self.delay * (1 + self.base_load / 25))
        try:
            result = OPERATIONS[operation](data)
            error = None
        except Exception as exc:
            result = None
            error = f"{type(exc).__name__}: {exc}"
        with self.lock:
            self.active -= 1
        reply = {
            "type": "RESULT",
            "task_id": task_id,
            "worker_id": self.worker_id,
            "result": result,
            "error": error,
            "seconds": round(time.time() - started, 3),
        }
        if self.send(sock, reply):
            self.log(f"sent result for task {task_id}: {short(error or result)}")
        else:
            self.log(f"could not send result for task {task_id}, the server will give it to someone else")
        self.send(sock, self.status_message())

    def vary_load(self):
        while True:
            time.sleep(15)
            self.base_load = random.choice(range(0, 95, 5))
            self.log(f"background load changed to {self.base_load}%")


def main():
    parser = argparse.ArgumentParser(description="task worker")
    parser.add_argument("--id", default=f"worker-{random.randint(100, 999)}")
    parser.add_argument("--server", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--load", type=int, default=20, help="pretend background load in percent")
    parser.add_argument("--capacity", type=int, default=2, help="how many tasks it can run at once")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds per task, longer when the worker is busy")
    parser.add_argument("--vary", action="store_true", help="change the background load every 15 seconds")
    args = parser.parse_args()
    worker = Worker(args.id, args.server, args.port, max(0, min(100, args.load)), max(1, args.capacity), args.delay, args.vary)
    try:
        worker.run()
    except KeyboardInterrupt:
        worker.log("stopped")


if __name__ == "__main__":
    main()

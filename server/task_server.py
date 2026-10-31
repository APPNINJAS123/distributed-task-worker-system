import argparse
import itertools
import json
import os
import socket
import sys
import threading
import time
from collections import deque

from common.config import DEFAULT_PORT, LOADS_FILE, SUPPORTED_OPERATIONS, WORKER_TIMEOUT
from common.protocol import read_messages, send_msg, short


LOG_LOCK = threading.Lock()


def log(text):
    with LOG_LOCK:
        sys.stdout.write(f"{time.strftime('%H:%M:%S')} [server] {text}\n")
        sys.stdout.flush()


def close_quietly(conn):
    try:
        conn.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


class TaskServer:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.cond = threading.Condition()
        self.queue = deque()
        self.tasks = {}
        self.workers = {}
        self.next_id = itertools.count(1)

    def serve_forever(self):
        threading.Thread(target=self.dispatch_loop, daemon=True).start()
        threading.Thread(target=self.monitor_loop, daemon=True).start()
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen()
        log(f"listening on {self.host}:{self.port}")
        while True:
            conn, addr = listener.accept()
            threading.Thread(target=self.handle_connection, args=(conn, addr), daemon=True).start()

    def handle_connection(self, conn, addr):
        messages = read_messages(conn)
        try:
            first = next(messages, None)
            if first is None:
                return
            if first.get("type") == "REGISTER":
                self.handle_worker(conn, addr, first, messages)
            else:
                self.handle_client(conn, first, messages)
        except (OSError, ValueError):
            pass
        finally:
            conn.close()

    def handle_client(self, conn, first, messages):
        send_msg(conn, self.answer(first))
        for msg in messages:
            send_msg(conn, self.answer(msg))

    def answer(self, msg):
        kind = msg.get("type")
        if kind == "SUBMIT":
            return self.submit(msg.get("operation"), msg.get("input"))
        if kind == "GET_RESULT":
            with self.cond:
                task = self.tasks.get(msg.get("task_id"))
                if task is None:
                    return {"type": "ERROR", "message": "unknown task id"}
                return {"type": "TASK_INFO", "task": dict(task)}
        if kind == "STATUS":
            return self.status()
        return {"type": "ERROR", "message": f"unknown message type {kind}"}

    def submit(self, operation, data):
        if operation not in SUPPORTED_OPERATIONS:
            return {"type": "ERROR", "message": f"unsupported operation {operation}"}
        with self.cond:
            task_id = next(self.next_id)
            self.tasks[task_id] = {
                "task_id": task_id,
                "operation": operation,
                "input": data,
                "status": "pending",
                "worker": None,
                "result": None,
                "error": None,
                "attempts": 0,
            }
            self.queue.append(task_id)
            log(f"task {task_id} queued: {operation}({short(data, 40)})")
            self.cond.notify_all()
        return {"type": "ACK", "task_id": task_id}

    def status(self):
        with self.cond:
            workers = [
                {
                    "worker_id": wid,
                    "ip": w["ip"],
                    "load": w["load"],
                    "active": len(w["active"]),
                    "capacity": w["capacity"],
                }
                for wid, w in sorted(self.workers.items())
            ]
            counts = {"pending": 0, "running": 0, "done": 0, "failed": 0}
            for task in self.tasks.values():
                counts[task["status"]] += 1
        return {"type": "STATUS", "workers": workers, "tasks": counts}

    def handle_worker(self, conn, addr, first, messages):
        worker_id = str(first.get("worker_id"))
        with self.cond:
            if worker_id in self.workers:
                taken = True
            else:
                taken = False
                self.workers[worker_id] = {
                    "conn": conn,
                    "ip": addr[0],
                    "load": float(first.get("load", 0)),
                    "capacity": max(1, int(first.get("capacity", 2))),
                    "active": set(),
                    "last_seen": time.time(),
                    "ready": False,
                }
        if taken:
            send_msg(conn, {"type": "ERROR", "message": f"worker id {worker_id} is already connected"})
            return
        try:
            send_msg(conn, {"type": "REGISTERED"})
            with self.cond:
                self.workers[worker_id]["ready"] = True
                self.cond.notify_all()
            log(f"worker {worker_id} joined from {addr[0]}")
            for msg in messages:
                self.on_worker_message(worker_id, msg)
        finally:
            self.remove_worker(worker_id)

    def on_worker_message(self, worker_id, msg):
        with self.cond:
            worker = self.workers.get(worker_id)
            if worker is None:
                return
            worker["last_seen"] = time.time()
            kind = msg.get("type")
            if kind == "STATUS":
                worker["load"] = float(msg.get("load", worker["load"]))
                self.cond.notify_all()
                return
            if kind != "RESULT":
                return
            task_id = msg.get("task_id")
            worker["active"].discard(task_id)
            self.cond.notify_all()
            task = self.tasks.get(task_id)
            if task is None or task["status"] != "running" or task["worker"] != worker_id:
                return
            task["result"] = msg.get("result")
            task["error"] = msg.get("error")
            task["status"] = "failed" if task["error"] else "done"
        if task["error"]:
            log(f"task {task_id} failed on {worker_id}: {task['error']}")
        else:
            log(f"task {task_id} done by {worker_id} -> {short(task['result'])}")

    def remove_worker(self, worker_id):
        with self.cond:
            worker = self.workers.pop(worker_id, None)
            if worker is None:
                return
            lost = sorted(worker["active"])
            for task_id in reversed(lost):
                task = self.tasks[task_id]
                task["status"] = "pending"
                task["worker"] = None
                self.queue.appendleft(task_id)
            self.cond.notify_all()
        log(f"worker {worker_id} disconnected")
        if lost:
            log(f"put tasks {lost} back in the queue")

    def pick_worker(self):
        free = [
            (w["load"], len(w["active"]), wid)
            for wid, w in self.workers.items()
            if w["ready"] and len(w["active"]) < w["capacity"]
        ]
        if not free:
            return None
        return min(free)[2]

    def dispatch_loop(self):
        while True:
            with self.cond:
                while True:
                    worker_id = self.pick_worker() if self.queue else None
                    if worker_id is not None:
                        break
                    self.cond.wait(timeout=1)
                task_id = self.queue.popleft()
                task = self.tasks[task_id]
                worker = self.workers[worker_id]
                loads = " ".join(f"{wid}={w['load']:.0f}%" for wid, w in sorted(self.workers.items()))
                try:
                    send_msg(worker["conn"], {
                        "type": "TASK",
                        "task_id": task_id,
                        "operation": task["operation"],
                        "input": task["input"],
                    })
                except OSError:
                    self.queue.appendleft(task_id)
                    worker["ready"] = False
                    close_quietly(worker["conn"])
                    continue
                task["status"] = "running"
                task["worker"] = worker_id
                task["attempts"] += 1
                worker["active"].add(task_id)
                worker["load"] = min(100.0, worker["load"] + 100.0 / worker["capacity"])
            log(f"task {task_id} ({task['operation']}) -> {worker_id}   loads: {loads}")

    def monitor_loop(self):
        while True:
            time.sleep(2)
            now = time.time()
            with self.cond:
                for wid, w in self.workers.items():
                    if w["ready"] and now - w["last_seen"] > WORKER_TIMEOUT:
                        w["ready"] = False
                        log(f"worker {wid} stopped sending heartbeats")
                        close_quietly(w["conn"])
                snapshot = {
                    wid: {"ip": w["ip"], "load": w["load"], "active": len(w["active"]), "capacity": w["capacity"]}
                    for wid, w in self.workers.items()
                }
            self.write_loads(snapshot)

    def write_loads(self, snapshot):
        tmp = LOADS_FILE + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump({"updated": time.time(), "workers": snapshot}, f)
            os.replace(tmp, LOADS_FILE)
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser(description="central task server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    try:
        TaskServer(args.host, args.port).serve_forever()
    except KeyboardInterrupt:
        log("shutting down")


if __name__ == "__main__":
    main()

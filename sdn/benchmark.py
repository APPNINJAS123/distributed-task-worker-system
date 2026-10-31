import json
import os
import subprocess
import time

from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController

from sdn.check_sdn import client, packets_through, wait_for
from sdn.network import WORKERS
from sdn.topology import PROJECT_DIR, TaskTopo, start_apps, stop_apps

RESULTS_FILE = os.path.join(PROJECT_DIR, "results", "benchmark_results.md")

SCHEDULING = [
    ("uneven loads 70/10/40", (70, 10, 40)),
    ("equal loads 30/30/30", (30, 30, 30)),
]
ROUTING = [6, 12]


def clean_up():
    subprocess.call(["mn", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for name in ("server.task_server", "worker.worker", "ryu-manager"):
        subprocess.call(["pkill", "-f", name])


def run_once(policy, routing, loads, delay, kind, count):
    clean_up()
    env = dict(os.environ, DTN_ROUTING=routing)
    with open("/tmp/dtn_ryu.log", "w") as ryu_log:
        ryu = subprocess.Popen(
            ["ryu-manager", os.path.join(PROJECT_DIR, "sdn", "load_balancer.py")],
            stdout=ryu_log, stderr=subprocess.STDOUT, env=env,
        )
    time.sleep(3)
    net = Mininet(topo=TaskTopo(), switch=OVSSwitch, link=TCLink, controller=None, autoSetMacs=False)
    net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=6653)
    net.start()
    try:
        wait_for(lambda: all(s.connected() for s in net.switches), 20)
        net.pingAll(timeout=2)
        start_apps(net, policy=policy, loads=loads, delay=delay)
        wait_for(lambda: client(net, "status").count("load") == len(WORKERS), 20)
        time.sleep(4)
        s2_before, s3_before = packets_through(net, "s2"), packets_through(net, "s3")
        output = client(net, f"bench --kind {kind} --count {count}")
        line = next(l for l in output.splitlines() if l.startswith("RESULT "))
        result = json.loads(line[len("RESULT "):])
        time.sleep(2)
        result["s2_packets"] = packets_through(net, "s2") - s2_before
        result["s3_packets"] = packets_through(net, "s3") - s3_before
        return result
    finally:
        stop_apps(net)
        net.stop()
        ryu.terminate()
        ryu.wait()


def show(rows, lines):
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
        print("  ".join(f"{str(cell):<22}" for cell in row), flush=True)


def main():
    setLogLevel("warning")
    lines = []

    print("\nexperiment 1: scheduling, least loaded vs round robin (30 normal tasks)\n", flush=True)
    lines += ["## Experiment 1: scheduling (30 normal tasks)", ""]
    lines += ["| condition | scheduler | total time (s) | avg time per task (s) | tasks per worker |", "|---|---|---|---|---|"]
    rows = []
    for name, loads in SCHEDULING:
        for policy in ("round_robin", "least_loaded"):
            r = run_once(policy, "dynamic", loads, 0.5, "normal", 30)
            per_worker = ", ".join(f"{w}={n}" for w, n in r["per_worker"].items())
            rows.append((name, policy, r["total_seconds"], r["avg_task_seconds"], per_worker))
    show(rows, lines)

    print("\nexperiment 2: routing, dynamic SDN vs static (big sort tasks, 10 Mbit/s links)\n", flush=True)
    lines += ["", "## Experiment 2: routing (big sort tasks, 10 Mbit/s core links)", ""]
    lines += ["| tasks | routing | total time (s) | avg time per task (s) | packets via s2 | packets via s3 |", "|---|---|---|---|---|---|"]
    rows = []
    for count in ROUTING:
        for routing in ("static", "dynamic"):
            r = run_once("least_loaded", routing, (30, 30, 30), 0, "heavy", count)
            rows.append((count, routing, r["total_seconds"], r["avg_task_seconds"], r["s2_packets"], r["s3_packets"]))
    show(rows, lines)

    clean_up()
    os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
    with open(RESULTS_FILE, "w") as f:
        f.write("# Benchmark results\n\nMade by running python3 -m sdn.benchmark\n\n" + "\n".join(lines) + "\n")
    print(f"\nsaved to {RESULTS_FILE}", flush=True)


if __name__ == "__main__":
    main()

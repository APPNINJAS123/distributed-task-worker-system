import os
import re
import subprocess
import sys
import time

from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController

from sdn.network import S1_UPLINK, SERVER, WORKERS
from sdn.topology import PROJECT_DIR, TaskTopo, start_apps, start_worker, stop_apps, stop_worker

RYU_LOG = "/tmp/dtn_ryu.log"
PORT_TO_PATH = {port: path for path, port in S1_UPLINK.items()}
results = []


def say(text):
    print(text, flush=True)


def check(name, ok, detail=""):
    results.append(ok)
    say(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"   ({detail})" if detail else ""))
    return ok


def wait_for(condition, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(1)
    return condition()


def current_paths(net):
    flows = net.get("s1").cmd("ovs-ofctl -O OpenFlow13 --no-names dump-flows s1")
    paths = {}
    for w in WORKERS:
        match = re.search(rf"nw_dst={re.escape(w['ip'])} .*actions=output:(\d+)", flows)
        if match:
            paths[w["name"]] = PORT_TO_PATH.get(int(match.group(1)))
    return paths


def packets_through(net, switch):
    flows = net.get(switch).cmd(f"ovs-ofctl -O OpenFlow13 --no-names dump-flows {switch}")
    return sum(int(n) for n in re.findall(r"n_packets=(\d+).*nw_dst=10\.0\.0\.1\d", flows))


def client(net, args):
    return net.get(SERVER["name"]).cmd(f"cd {PROJECT_DIR} && python3 -m client.client {args}")


def run_checks(net):
    check("all 4 switches connected to the Ryu controller",
          wait_for(lambda: all(s.connected() for s in net.switches), 20))

    loss = net.pingAll(timeout=2)
    check("pingall between server and all workers", loss == 0, f"{loss:.0f}% packet loss")

    start_apps(net)
    status = wait_for(lambda: (lambda out: out if out.count("load") == len(WORKERS) else "")(client(net, "status")), 20)
    check("task server running on srv and 3 workers registered over TCP", bool(status))
    say(status)

    expected = {"w1": "s2", "w2": "s3", "w3": "s3"}
    paths = wait_for(lambda: current_paths(net) if current_paths(net) == expected else None, 20)
    check("controller split workers by load (w1=70% alone on s2, w2 + w3 on s3)",
          paths == expected, f"flows on s1 say {current_paths(net)}")

    s2_before, s3_before = packets_through(net, "s2"), packets_through(net, "s3")
    demo = client(net, "demo --repeat 2")
    say(demo)
    check("20 demo tasks sent through the SDN network and all finished", "20/20 tasks finished" in demo)
    s2_used = packets_through(net, "s2") - s2_before
    s3_used = packets_through(net, "s3") - s3_before
    check("task traffic actually used both paths", s2_used > 0 and s3_used > 0,
          f"{s2_used} packets via s2, {s3_used} packets via s3")

    say("\nchanging loads: w1 drops to 5%, w2 jumps to 95% ...")
    for name, load in (("w1", 5), ("w2", 95)):
        stop_worker(net, name)
    time.sleep(1)
    for name, load in (("w1", 5), ("w2", 95)):
        start_worker(net, name, load)
    expected = {"w1": "s3", "w2": "s2", "w3": "s3"}
    paths = wait_for(lambda: current_paths(net) if current_paths(net) == expected else None, 30)
    check("controller rerouted after loads changed (w2 now alone on s2)",
          paths == expected, f"flows on s1 say {current_paths(net)}")
    with open(RYU_LOG) as f:
        reroutes = [line.strip() for line in f if "reroute" in line]
    check("controller logged the reroute decisions", bool(reroutes), "; ".join(reroutes[-2:]))

    wait_for(lambda: client(net, "status").count("load") == len(WORKERS), 20)
    single = client(net, "submit factorial 10 --wait")
    check("a new task still works after the reroute", "3628800" in single)
    srv = net.get(SERVER["name"])
    pings_through_paths(net, srv)

    say("\ntaking the s1-s2 link down ...")
    net.configLinkStatus("s1", "s2", "down")
    everyone_on_s3 = {"w1": "s3", "w2": "s3", "w3": "s3"}
    paths = wait_for(lambda: current_paths(net) if current_paths(net) == everyone_on_s3 else None, 15)
    check("controller moved every worker to s3 when the s2 link broke",
          paths == everyone_on_s3, f"flows on s1 say {current_paths(net)}")
    single = client(net, "submit factorial 12 --wait")
    check("tasks still work while the s2 link is down", "479001600" in single)

    net.configLinkStatus("s1", "s2", "up")
    paths = wait_for(lambda: "s2" in current_paths(net).values(), 15)
    check("traffic is split over both paths again once the link is back", bool(paths),
          f"flows on s1 say {current_paths(net)}")


def pings_through_paths(net, srv):
    for name, ip, path, other in (("w2", "10.0.0.12", "s2", "s3"), ("w1", "10.0.0.11", "s3", "s2")):
        on_path, off_path = packets_through(net, path), packets_through(net, other)
        srv.cmd(f"ping -c 20 -i 0.05 {ip}")
        time.sleep(3)
        gained = packets_through(net, path) - on_path
        check(f"pings to {name} travel through {path}", gained >= 20,
              f"{path} +{gained} packets, {other} +{packets_through(net, other) - off_path}")


def main():
    setLogLevel("warning")
    subprocess.call(["mn", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for name in ("server.task_server", "worker.worker", "ryu-manager"):
        subprocess.call(["pkill", "-f", name])
    for log in [RYU_LOG] + [f"/tmp/dtn_{w['name']}.log" for w in WORKERS]:
        if os.path.exists(log):
            os.remove(log)
    with open(RYU_LOG, "w") as ryu_log:
        ryu = subprocess.Popen(
            ["ryu-manager", os.path.join(PROJECT_DIR, "sdn", "load_balancer.py")],
            stdout=ryu_log, stderr=subprocess.STDOUT,
        )
    time.sleep(3)
    net = Mininet(topo=TaskTopo(), switch=OVSSwitch, link=TCLink, controller=None, autoSetMacs=False)
    net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=6653)
    net.start()
    try:
        run_checks(net)
    finally:
        stop_apps(net)
        net.stop()
        ryu.terminate()
    passed = sum(results)
    say(f"\n{passed}/{len(results)} checks passed. controller log: {RYU_LOG}, server log: /tmp/dtn_server.log")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()

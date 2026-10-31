import argparse
import os
import time

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import info, setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.topo import Topo

from sdn.network import CORE_TO_S1_PORT, CORE_TO_S4_PORT, S1_SERVER_PORT, S1_UPLINK, S4_UPLINK, SERVER, WORKERS

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TaskTopo(Topo):
    def build(self):
        s1 = self.addSwitch("s1", dpid="0000000000000001", protocols="OpenFlow13")
        s2 = self.addSwitch("s2", dpid="0000000000000002", protocols="OpenFlow13")
        s3 = self.addSwitch("s3", dpid="0000000000000003", protocols="OpenFlow13")
        s4 = self.addSwitch("s4", dpid="0000000000000004", protocols="OpenFlow13")

        srv = self.addHost(SERVER["name"], ip=SERVER["ip"] + "/24", mac=SERVER["mac"])
        self.addLink(srv, s1, port2=S1_SERVER_PORT)

        self.addLink(s1, s2, port1=S1_UPLINK["s2"], port2=CORE_TO_S1_PORT, bw=10)
        self.addLink(s1, s3, port1=S1_UPLINK["s3"], port2=CORE_TO_S1_PORT, bw=10)
        self.addLink(s2, s4, port1=CORE_TO_S4_PORT, port2=S4_UPLINK["s2"], bw=10)
        self.addLink(s3, s4, port1=CORE_TO_S4_PORT, port2=S4_UPLINK["s3"], bw=10)

        for w in WORKERS:
            host = self.addHost(w["name"], ip=w["ip"] + "/24", mac=w["mac"])
            self.addLink(host, s4, port2=w["s4_port"])


def start_worker(net, name, load, delay=1.0):
    net.get(name).cmd(
        f"cd {PROJECT_DIR} && python3 -u -m worker.worker --id {name} "
        f"--server {SERVER['ip']} --load {load} --delay {delay} >> /tmp/dtn_{name}.log 2>&1 &"
    )


def stop_worker(net, name):
    net.get(name).cmd(f"pkill -f -- '--id {name} '")


def start_apps(net, policy="least_loaded", loads=None, delay=1.0):
    srv = net.get(SERVER["name"])
    srv.cmd(f"cd {PROJECT_DIR} && python3 -u -m server.task_server --policy {policy} > /tmp/dtn_server.log 2>&1 &")
    time.sleep(1)
    for i, w in enumerate(WORKERS):
        start_worker(net, w["name"], loads[i] if loads else w["load"], delay)
    info("*** task server running on srv, workers running on " + ", ".join(w["name"] for w in WORKERS) + "\n")
    info("*** logs: /tmp/dtn_server.log and /tmp/dtn_w1.log etc.\n")


def stop_apps(net):
    net.get(SERVER["name"]).cmd("pkill -f server.task_server; pkill -f worker.worker")


def main():
    parser = argparse.ArgumentParser(description="mininet network for the task system")
    parser.add_argument("--controller-ip", default="127.0.0.1")
    parser.add_argument("--controller-port", type=int, default=6653)
    parser.add_argument("--no-apps", action="store_true", help="only build the network, start server/workers yourself")
    args = parser.parse_args()

    setLogLevel("info")
    net = Mininet(topo=TaskTopo(), switch=OVSSwitch, link=TCLink, controller=None, autoSetMacs=False)
    net.addController("c0", controller=RemoteController, ip=args.controller_ip, port=args.controller_port)
    net.start()
    try:
        if not args.no_apps:
            start_apps(net)
        CLI(net)
    finally:
        stop_apps(net)
        net.stop()


if __name__ == "__main__":
    main()

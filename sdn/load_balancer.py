import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, DEAD_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.lib.packet import arp, ether_types, ethernet, packet
from ryu.ofproto import ofproto_v1_3

from sdn.network import (
    CORE_TO_S1_PORT,
    CORE_TO_S4_PORT,
    HOSTS,
    S1,
    S1_SERVER_PORT,
    S1_UPLINK,
    S2,
    S3,
    S4,
    S4_UPLINK,
    SERVER,
    WORKERS,
)
from sdn.path_planner import PATHS, plan_paths, read_worker_loads

CHECK_EVERY = 3
MODE = os.environ.get("DTN_ROUTING", "dynamic")
S1_PORT_TO_PATH = {port: path for path, port in S1_UPLINK.items()}
S4_PORT_TO_PATH = {port: path for path, port in S4_UPLINK.items()}


class LoadAwareController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.datapaths = {}
        self.ip_to_mac = {h["ip"]: h["mac"] for h in HOSTS}
        self.ip_to_name = {h["ip"]: h["name"] for h in HOSTS}
        self.paths = {w["ip"]: "s2" for w in WORKERS}
        self.loads = {}
        self.last_bytes = {}
        self.alive = set(PATHS)
        self.logger.info("routing mode: %s", MODE)
        self.monitor_thread = hub.spawn(self.monitor)

    def add_flow(self, dp, priority, match, actions):
        parser = dp.ofproto_parser
        inst = [parser.OFPInstructionActions(dp.ofproto.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=priority, match=match, instructions=inst))

    def forward(self, dp, out_port, **fields):
        parser = dp.ofproto_parser
        match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, **fields)
        self.add_flow(dp, 10, match, [parser.OFPActionOutput(out_port)])

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def on_switch_connect(self, ev):
        dp = ev.msg.datapath
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        self.datapaths[dp.id] = dp
        self.add_flow(dp, 0, parser.OFPMatch(), [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)])
        self.install_static_rules(dp)
        for ip in self.paths:
            self.install_path(dp, ip)
        self.logger.info("switch s%s connected", dp.id)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def on_state_change(self, ev):
        dp = ev.datapath
        if ev.state == DEAD_DISPATCHER and dp.id in self.datapaths:
            del self.datapaths[dp.id]
            self.logger.info("switch s%s disconnected", dp.id)

    def install_static_rules(self, dp):
        if dp.id == S1:
            self.forward(dp, S1_SERVER_PORT, ipv4_dst=SERVER["ip"])
        elif dp.id in (S2, S3):
            self.forward(dp, CORE_TO_S1_PORT, ipv4_dst=SERVER["ip"])
            for w in WORKERS:
                self.forward(dp, CORE_TO_S4_PORT, ipv4_dst=w["ip"])
        elif dp.id == S4:
            for w in WORKERS:
                self.forward(dp, w["s4_port"], ipv4_dst=w["ip"])

    def install_path(self, dp, worker_ip):
        path = self.paths[worker_ip]
        if dp.id == S1:
            self.forward(dp, S1_UPLINK[path], ipv4_dst=worker_ip)
        elif dp.id == S4:
            self.forward(dp, S4_UPLINK[path], ipv4_src=worker_ip, ipv4_dst=SERVER["ip"])

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def on_packet_in(self, ev):
        msg = ev.msg
        dp = msg.datapath
        pkt = packet.Packet(msg.data)
        request = pkt.get_protocol(arp.arp)
        if request is None or request.opcode != arp.ARP_REQUEST:
            return
        target_mac = self.ip_to_mac.get(request.dst_ip)
        if target_mac is None:
            return
        reply = packet.Packet()
        reply.add_protocol(ethernet.ethernet(dst=request.src_mac, src=target_mac, ethertype=ether_types.ETH_TYPE_ARP))
        reply.add_protocol(arp.arp(
            opcode=arp.ARP_REPLY,
            src_mac=target_mac,
            src_ip=request.dst_ip,
            dst_mac=request.src_mac,
            dst_ip=request.src_ip,
        ))
        reply.serialize()
        parser = dp.ofproto_parser
        dp.send_msg(parser.OFPPacketOut(
            datapath=dp,
            buffer_id=dp.ofproto.OFP_NO_BUFFER,
            in_port=dp.ofproto.OFPP_CONTROLLER,
            actions=[parser.OFPActionOutput(msg.match["in_port"])],
            data=reply.data,
        ))

    def monitor(self):
        while True:
            hub.sleep(CHECK_EVERY)
            self.update_paths()
            dp = self.datapaths.get(S1)
            if dp is not None:
                dp.send_msg(dp.ofproto_parser.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY))

    def update_paths(self):
        workers = read_worker_loads()
        self.loads = {
            info["ip"]: float(info["load"]) for info in workers.values() if info.get("ip") in self.paths
        }
        if MODE == "static" or not self.alive:
            return
        loads = {ip: self.loads.get(ip, 0.0) for ip in self.paths}
        plan, _ = plan_paths(loads, sorted(self.alive))
        for ip, path in plan.items():
            if self.paths[ip] == path:
                continue
            old = self.paths[ip]
            self.paths[ip] = path
            self.logger.info(
                "reroute %s (load %.0f%%): via %s -> via %s", self.ip_to_name[ip], loads[ip], old, path
            )
            for dp in self.datapaths.values():
                self.install_path(dp, ip)

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def on_port_status(self, ev):
        dp = ev.msg.datapath
        port = ev.msg.desc
        if dp.id == S1:
            path = S1_PORT_TO_PATH.get(port.port_no)
        elif dp.id == S4:
            path = S4_PORT_TO_PATH.get(port.port_no)
        else:
            path = None
        if path is None:
            return
        down = bool(port.state & dp.ofproto.OFPPS_LINK_DOWN) or bool(port.config & dp.ofproto.OFPPC_PORT_DOWN)
        if down and path in self.alive:
            self.alive.discard(path)
            self.logger.info("link on the %s path went down, moving its traffic", path)
            self.update_paths()
        elif not down and path not in self.alive:
            self.alive.add(path)
            self.logger.info("link on the %s path is back up", path)
            self.update_paths()

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def on_port_stats(self, ev):
        if ev.msg.datapath.id != S1:
            return
        rates = {}
        for stat in ev.msg.body:
            for path, port in S1_UPLINK.items():
                if stat.port_no == port:
                    total = stat.tx_bytes + stat.rx_bytes
                    previous = self.last_bytes.get(port, total)
                    self.last_bytes[port] = total
                    rates[path] = (total - previous) / CHECK_EVERY / 1024
        if not self.loads:
            return
        loads = " ".join(f"{self.ip_to_name[ip]}={load:.0f}%" for ip, load in sorted(self.loads.items()))
        parts = []
        for path in PATHS:
            members = ",".join(self.ip_to_name[ip] for ip, p in sorted(self.paths.items()) if p == path) or "-"
            parts.append(f"via {path} [{members}] {rates.get(path, 0):.1f} KB/s")
        self.logger.info("loads %s | %s", loads, " | ".join(parts))

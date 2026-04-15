# sdn_load_balancer.py
# Requires: pip install ryu networkx

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ipv4
import networkx as nx
import random, time, threading

# ──────────────────────────────────────────
# 1. TOPOLOGY  — 10 hosts, 20 switches
# ──────────────────────────────────────────
class SDNTopology:
    def __init__(self):
        self.G = nx.DiGraph()
        self._build()

    def _build(self):
        G = self.G
        # Hosts H1-H10
        for i in range(1, 11):
            G.add_node(f"H{i}", type="host")
        # Switches S1-S20
        for i in range(1, 21):
            G.add_node(f"S{i}", type="switch", load=0.0)
        G.add_node("DST", type="destination")

        def link(a, b, bw=1000, lat=5, loss=0.01):
            """bidirectional link with metrics"""
            G.add_edge(a, b, bw=bw, lat=lat, loss=loss, util=0.0)
            G.add_edge(b, a, bw=bw, lat=lat, loss=loss, util=0.0)

        # Hosts -> core switch S1
        for i in range(1, 11):
            link(f"H{i}", "S1", bw=1000, lat=2, loss=0.005)

        # Layer 0 -> Layer 1  (S1 -> S2,S3,S4)
        for s in ["S2","S3","S4"]:
            link("S1", s, bw=10000, lat=1, loss=0.001)

        # Layer 1 -> Layer 2  (aggregation)
        edges_l1_l2 = [
            ("S2","S5",500,5,0.02), ("S2","S6",800,4,0.01),
            ("S3","S5",600,6,0.03), ("S3","S6",700,4,0.01), ("S3","S7",800,3,0.01),
            ("S4","S7",600,5,0.02), ("S4","S8",900,3,0.005),
        ]
        for a,b,bw,lat,loss in edges_l1_l2: link(a,b,bw,lat,loss)

        # Layer 2 -> Layer 3
        edges_l2_l3 = [
            ("S5","S9",400,8,0.04),  ("S5","S10",600,6,0.02),
            ("S6","S9",700,5,0.02),  ("S6","S10",800,4,0.01), ("S6","S11",700,4,0.015),
            ("S7","S10",600,5,0.02), ("S7","S11",800,4,0.01), ("S7","S12",700,5,0.02),
            ("S8","S11",600,6,0.02), ("S8","S12",900,3,0.005),
        ]
        for a,b,bw,lat,loss in edges_l2_l3: link(a,b,bw,lat,loss)

        # Layer 3 -> Layer 4
        edges_l3_l4 = [
            ("S9","S13",500,7,0.03),  ("S9","S14",600,6,0.02),
            ("S10","S13",700,5,0.02), ("S10","S14",800,4,0.01), ("S10","S15",700,5,0.02),
            ("S11","S14",600,5,0.02), ("S11","S15",800,4,0.01), ("S11","S16",700,5,0.025),
            ("S12","S15",600,6,0.02), ("S12","S16",900,3,0.005),
        ]
        for a,b,bw,lat,loss in edges_l3_l4: link(a,b,bw,lat,loss)

        # Layer 4 -> Layer 5
        edges_l4_l5 = [
            ("S13","S17",600,5,0.02), ("S13","S18",700,4,0.015),
            ("S14","S17",800,4,0.01), ("S14","S18",800,3,0.01), ("S14","S19",700,5,0.02),
            ("S15","S17",600,5,0.02), ("S15","S18",700,4,0.015), ("S15","S19",800,3,0.01),
            ("S16","S18",600,5,0.02), ("S16","S19",900,3,0.005),
        ]
        for a,b,bw,lat,loss in edges_l4_l5: link(a,b,bw,lat,loss)

        # Layer 5 -> S20 (egress switch)
        for s in ["S17","S18","S19"]:
            link(s, "S20", bw=10000, lat=1, loss=0.001)

        # S20 -> DST
        link("S20", "DST", bw=10000, lat=1, loss=0.0005)


# ──────────────────────────────────────────
# 2. LOAD BALANCER — 3 metrics
# ──────────────────────────────────────────
class WeightedLoadBalancer:
    """
    Composite score = w_bw * (1 - norm_bw)
                    + w_lat * norm_lat
                    + w_loss * norm_loss
                    + w_util * util

    Lower score = better path segment.
    """
    W_BW   = 0.40   # bandwidth weight
    W_LAT  = 0.35   # latency weight
    W_LOSS = 0.25   # packet-loss weight

    BW_MAX  = 10000  # Mbps
    LAT_MAX = 20     # ms
    LOSS_MAX = 0.05  # 5%

    def __init__(self, topo: SDNTopology):
        self.topo = topo

    def edge_cost(self, u, v) -> float:
        d = self.topo.G[u][v]
        norm_bw   = 1 - min(d['bw'],   self.BW_MAX)  / self.BW_MAX
        norm_lat  =     min(d['lat'],   self.LAT_MAX) / self.LAT_MAX
        norm_loss =     min(d['loss'],  self.LOSS_MAX)/ self.LOSS_MAX
        util_pen  = d['util']  # 0-1 utilisation penalty

        return (self.W_BW   * norm_bw  +
                self.W_LAT  * norm_lat +
                self.W_LOSS * norm_loss +
                0.10 * util_pen)        # small real-time penalty

    def best_path(self, src: str, dst: str = "DST") -> list:
        """Dijkstra with composite edge cost."""
        try:
            path = nx.dijkstra_path(
                self.topo.G, src, dst,
                weight=lambda u, v, _: self.edge_cost(u, v)
            )
            return path
        except nx.NetworkXNoPath:
            return []

    def update_utilisation(self, path: list, pkt_size_mb: float = 0.001):
        """Increment utilisation on every link in the chosen path."""
        for i in range(len(path) - 1):
            u, v = path[i], path[i+1]
            if self.topo.G.has_edge(u, v):
                bw = self.topo.G[u][v]['bw']
                self.topo.G[u][v]['util'] = min(
                    1.0, self.topo.G[u][v]['util'] + pkt_size_mb / bw
                )

    def decay_utilisation(self, decay=0.05):
        """Call periodically to model traffic draining."""
        for u, v in self.topo.G.edges():
            self.topo.G[u][v]['util'] = max(
                0.0, self.topo.G[u][v]['util'] - decay
            )


# ──────────────────────────────────────────
# 3. RYU CONTROLLER APP
# ──────────────────────────────────────────
class SDNLoadBalancerApp(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.topo = SDNTopology()
        self.lb   = WeightedLoadBalancer(self.topo)
        self.mac_to_port = {}   # dpid -> {mac: port}
        self.stats = {"sent":0,"delivered":0,"dropped":0,"paths":[]}
        # Background decay thread
        t = threading.Thread(target=self._decay_loop, daemon=True)
        t.start()

    def _decay_loop(self):
        while True:
            time.sleep(1)
            self.lb.decay_utilisation(decay=0.02)

    # ── Handshake: install table-miss flow ──
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        self._add_flow(dp, 0, match, actions)

    def _add_flow(self, dp, priority, match, actions, idle=0, hard=0):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        mod  = parser.OFPFlowMod(
            datapath=dp, priority=priority,
            idle_timeout=idle, hard_timeout=hard,
            match=match, instructions=inst
        )
        dp.send_msg(mod)

    # ── Packet-in handler ──
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        dp  = msg.datapath
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None:
            return

        dpid  = dp.id
        src_mac = eth.src
        dst_mac = eth.dst
        in_port = msg.match['in_port']

        # Learn source
        self.mac_to_port.setdefault(dpid, {})[src_mac] = in_port

        # Map src_mac -> host id (simplified: H1..H10 by index)
        host_id = f"H{(hash(src_mac) % 10) + 1}"

        # Load-balance: pick best path
        path = self.lb.best_path(host_id, "DST")
        if not path:
            self.stats["dropped"] += 1
            self.logger.warning(f"[DROP] No path from {host_id}")
            return

        # Simulate packet-loss on path
        dropped = any(
            random.random() < self.topo.G[path[i]][path[i+1]]['loss']
            for i in range(len(path)-1)
            if self.topo.G.has_edge(path[i], path[i+1])
        )

        if dropped:
            self.stats["dropped"] += 1
            self.logger.warning(f"[DROP] {host_id}→DST — packet loss on path {path}")
            return

        # Update stats
        self.stats["sent"] += 1
        self.stats["delivered"] += 1
        self.stats["paths"].append(path)
        self.lb.update_utilisation(path)

        self.logger.info(
            f"[OK] {host_id}→DST | hops={len(path)-1} | "
            f"path={'→'.join(path)} | "
            f"delivered={self.stats['delivered']}"
        )

        # Install forwarding rules on each switch in path
        ofp, parser = dp.ofproto, dp.ofproto_parser
        out_port = ofp.OFPP_FLOOD   # simplified; real impl maps to actual ports
        actions  = [parser.OFPActionOutput(out_port)]
        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        out = parser.OFPPacketOut(
            datapath=dp, buffer_id=msg.buffer_id,
            in_port=in_port, actions=actions, data=data
        )
        dp.send_msg(out)


# ──────────────────────────────────────────
# 4. STANDALONE SIMULATION (no Ryu needed)
# ──────────────────────────────────────────
def run_simulation(n_packets=100):
    topo = SDNTopology()
    lb   = WeightedLoadBalancer(topo)

    sent = delivered = dropped = 0
    total_hops = total_cost = 0

    print(f"\n{'='*55}")
    print(f"  SDN Load Balancer Simulation — {n_packets} packets")
    print(f"  Weights: BW=40%  Latency=35%  Loss=25%")
    print(f"{'='*55}\n")

    for i in range(n_packets):
        src = f"H{random.randint(1,10)}"
        path = lb.best_path(src, "DST")
        sent += 1

        if not path:
            dropped += 1
            print(f"  [{i+1:3d}] DROP  {src} → no path")
            continue

        # Probabilistic loss
        loss_event = any(
            random.random() < topo.G[path[j]][path[j+1]]['loss']
            for j in range(len(path)-1)
            if topo.G.has_edge(path[j], path[j+1])
        )
        if loss_event:
            dropped += 1
            print(f"  [{i+1:3d}] LOSS  {src}→DST via {len(path)-1} hops")
            continue

        delivered += 1
        hops = len(path) - 1
        cost = sum(lb.edge_cost(path[j], path[j+1]) for j in range(hops))
        total_hops += hops
        total_cost += cost
        lb.update_utilisation(path)

        if i % 10 == 0:
            lb.decay_utilisation()

        print(f"  [{i+1:3d}] OK    {src}→DST | {hops} hops | "
              f"path: {'→'.join(path)} | cost={cost:.3f}")

    print(f"\n{'='*55}")
    print(f"  RESULTS")
    print(f"  Packets sent      : {sent}")
    print(f"  Delivered         : {delivered}  ({100*delivered/sent:.1f}%)")
    print(f"  Dropped           : {dropped}   ({100*dropped/sent:.1f}%)")
    if delivered:
        print(f"  Avg hop count     : {total_hops/delivered:.2f}")
        print(f"  Avg path cost     : {total_cost/delivered:.4f}")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    run_simulation(n_packets=50)

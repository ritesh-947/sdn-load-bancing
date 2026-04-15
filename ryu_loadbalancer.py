"""
SDN Load Balancer – Ryu Controller
====================================
Algorithm : Minimum Latency (based on switch port statistics)
Topology  : h1 (client) → s1 (OVSwitch) → h2/h3/h4 (servers)
Virtual IP: 10.0.0.100  Virtual MAC: 00:00:00:00:00:AA

The controller:
  1. Intercepts PacketIn events destined for the Virtual IP.
  2. Queries each server port's byte-count from the switch statistics.
  3. Derives a throughput/load estimate (proxy for latency).
  4. Routes to the server with the lowest estimated latency.
  5. Installs bidirectional OpenFlow rules (hard_timeout to allow re-election).
  6. Exposes a REST API at /sdn/stats for the web dashboard.

Run:
    ryu-manager ryu_loadbalancer.py --observe-links
"""

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (
    CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
)
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, arp, ipv4, tcp, udp
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from ryu.lib import hub

import json
import time
import collections

# ─── Virtual IP / MAC used by clients ─────────────────────────────────────
VIP_IP  = '10.0.0.100'
VIP_MAC = '00:00:00:00:00:AA'

# ─── Backend Servers ───────────────────────────────────────────────────────
SERVERS = [
    {'id': 'h2', 'ip': '10.0.0.2', 'mac': '00:00:00:00:00:02', 'port': 2, 'base_latency_ms': 5},
    {'id': 'h3', 'ip': '10.0.0.3', 'mac': '00:00:00:00:00:03', 'port': 3, 'base_latency_ms': 12},
    {'id': 'h4', 'ip': '10.0.0.4', 'mac': '00:00:00:00:00:04', 'port': 4, 'base_latency_ms': 2},
]

# OpenFlow hard timeout for load-balancer rules (seconds) – forces re-election
LB_HARD_TIMEOUT  = 10
STAT_POLL_PERIOD  = 2    # seconds between port stats requests

REST_APP_NAME = 'sdn_lb_rest'


class SDNLoadBalancer(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS    = {'wsgi': WSGIApplication}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.datapaths   = {}          # dpid → datapath
        self.port_stats  = {}          # dpid → {port_no: {tx_bytes, rx_bytes, ts}}
        self.server_stats = {          # id → running metrics
            s['id']: {
                'packets': 0, 'bytes': 0, 'load': 0.0,
                'latency': s['base_latency_ms'], 'active': True
            } for s in SERVERS
        }
        self.total_flows  = 0
        self.packet_in_count = 0
        self.packet_in_rate  = 0
        self._pi_last_ts     = time.time()
        self._pi_last_count  = 0
        self.best_server     = 'h4'    # lowest base latency

        wsgi = kwargs['wsgi']
        wsgi.register(SDNRestController,
                       {REST_APP_NAME: self})

        self.monitor_thread = hub.spawn(self._monitor)

    # ── Datapath registration ────────────────────────────────────────────
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp       = ev.msg.datapath
        ofproto  = dp.ofproto
        parser   = dp.ofproto_parser

        self.datapaths[dp.id] = dp
        self.logger.info('Switch connected: DPID=%016x', dp.id)

        # Table-miss rule → send to controller
        match  = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self._add_flow(dp, 0, match, actions)

    # ── PacketIn ────────────────────────────────────────────────────────
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg     = ev.msg
        dp      = msg.datapath
        ofproto = dp.ofproto
        parser  = dp.ofproto_parser
        in_port = msg.match['in_port']

        pkt  = packet.Packet(msg.data)
        eth  = pkt.get_protocol(ethernet.ethernet)
        if eth is None:
            return

        # ── ARP handling for VIP ──────────────────────────────────────
        arp_pkt = pkt.get_protocol(arp.arp)
        if arp_pkt and arp_pkt.dst_ip == VIP_IP:
            self._handle_arp(dp, in_port, eth, arp_pkt, msg)
            return

        # ── IP load-balancing ─────────────────────────────────────────
        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        if ip_pkt and ip_pkt.dst == VIP_IP:
            self._handle_ip_lb(dp, in_port, eth, ip_pkt, pkt, msg)
            return

        # ── L2 learning / forward other packets ──────────────────────
        self._l2_forward(dp, in_port, eth, msg)

        # Update packet-in rate
        self.packet_in_count += 1
        now = time.time()
        elapsed = now - self._pi_last_ts
        if elapsed >= 1.0:
            self.packet_in_rate = int((self.packet_in_count - self._pi_last_count) / elapsed)
            self._pi_last_count = self.packet_in_count
            self._pi_last_ts    = now

    def _handle_arp(self, dp, in_port, eth, arp_pkt, msg):
        """Reply to ARP requests for the Virtual IP with VIP_MAC."""
        if arp_pkt.opcode != arp.ARP_REQUEST:
            return

        pkt_reply = packet.Packet()
        pkt_reply.add_protocol(ethernet.ethernet(
            ethertype=ethernet.ethernet.ETH_TYPE_ARP,  # type: ignore
            dst=eth.src,
            src=VIP_MAC,
        ))
        pkt_reply.add_protocol(arp.arp(
            opcode=arp.ARP_REPLY,
            src_mac=VIP_MAC,
            src_ip=VIP_IP,
            dst_mac=arp_pkt.src_mac,
            dst_ip=arp_pkt.src_ip,
        ))
        pkt_reply.serialize()
        self._send_packet(dp, in_port, pkt_reply)
        self.logger.info('ARP reply: %s is now at %s', VIP_IP, VIP_MAC)

    def _handle_ip_lb(self, dp, in_port, eth, ip_pkt, pkt, msg):
        """Select server with minimum latency and install flow rules."""
        server = self._select_server()
        if server is None:
            self.logger.warning('No available servers!')
            return

        ofproto = dp.ofproto
        parser  = dp.ofproto_parser

        self.logger.info('LB decision: %s → %s (%s)', ip_pkt.src, server['id'], server['ip'])
        self.total_flows += 1
        self.server_stats[server['id']]['packets'] += 1
        self.server_stats[server['id']]['bytes']   += len(msg.data)
        self._update_best_server()

        # Forward: client → server (rewrite dst to server's real IP/MAC)
        match_fwd = parser.OFPMatch(
            in_port=in_port,
            eth_type=0x0800,
            ip_proto=ip_pkt.proto,
            ipv4_dst=VIP_IP,
        )
        actions_fwd = [
            parser.OFPActionSetField(eth_dst=server['mac']),
            parser.OFPActionSetField(ipv4_dst=server['ip']),
            parser.OFPActionOutput(server['port']),
        ]
        self._add_flow(dp, 10, match_fwd, actions_fwd,
                       hard_timeout=LB_HARD_TIMEOUT)

        # Reverse: server → client (rewrite src back to VIP)
        match_rev = parser.OFPMatch(
            in_port=server['port'],
            eth_type=0x0800,
            ip_proto=ip_pkt.proto,
            ipv4_src=server['ip'],
        )
        actions_rev = [
            parser.OFPActionSetField(eth_src=VIP_MAC),
            parser.OFPActionSetField(ipv4_src=VIP_IP),
            parser.OFPActionOutput(in_port),
        ]
        self._add_flow(dp, 10, match_rev, actions_rev,
                       hard_timeout=LB_HARD_TIMEOUT)

        # Send the current packet immediately (don't drop it)
        data = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
        out = parser.OFPPacketOut(
            datapath=dp,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions_fwd,
            data=data,
        )
        dp.send_msg(out)

    def _l2_forward(self, dp, in_port, eth, msg):
        """Simple L2 flood for non-LB traffic."""
        ofproto = dp.ofproto
        parser  = dp.ofproto_parser
        out = parser.OFPPacketOut(
            datapath=dp,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=[parser.OFPActionOutput(ofproto.OFPP_FLOOD)],
            data=msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None,
        )
        dp.send_msg(out)

    # ── Server Selection ─────────────────────────────────────────────────
    def _select_server(self):
        """Return server with minimum estimated latency."""
        active = [s for s in SERVERS if self.server_stats[s['id']]['active']]
        if not active:
            return None
        return min(active, key=lambda s: self.server_stats[s['id']]['latency'])

    def _update_best_server(self):
        stats = [
            (s['id'], self.server_stats[s['id']]['latency'])
            for s in SERVERS if self.server_stats[s['id']]['active']
        ]
        if stats:
            self.best_server = min(stats, key=lambda x: x[1])[0]

    # ── Port Stats Monitoring ─────────────────────────────────────────────
    def _monitor(self):
        """Periodically request port statistics from all datapaths."""
        while True:
            for dp in list(self.datapaths.values()):
                self._request_port_stats(dp)
            hub.sleep(STAT_POLL_PERIOD)

    def _request_port_stats(self, dp):
        ofproto = dp.ofproto
        parser  = dp.ofproto_parser
        req = parser.OFPPortStatsRequest(dp, 0, ofproto.OFPP_ANY)
        dp.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        dp   = ev.msg.datapath
        body = ev.msg.body
        dpid = dp.id

        if dpid not in self.port_stats:
            self.port_stats[dpid] = {}

        for stat in body:
            pno = stat.port_no
            now = time.time()
            prev = self.port_stats[dpid].get(pno)

            if prev:
                dt       = now - prev['ts']
                d_bytes  = stat.tx_bytes - prev['tx_bytes']
                bw_bps   = (d_bytes * 8 / dt) if dt > 0 else 0

                # Map port to server
                server_map = {2: 'h2', 3: 'h3', 4: 'h4'}
                sid = server_map.get(pno)
                if sid:
                    # Latency proxy: base_latency + 0.001 * bw_utilization
                    base = next(s['base_latency_ms'] for s in SERVERS if s['id'] == sid)
                    util_penalty = min(bw_bps / 1e6, 10.0)   # cap at 10ms penalty
                    self.server_stats[sid]['latency'] = round(base + util_penalty, 2)
                    self.server_stats[sid]['load']    = min(bw_bps / 1e5, 100.0)

            self.port_stats[dpid][pno] = {
                'tx_bytes': stat.tx_bytes,
                'rx_bytes': stat.rx_bytes,
                'ts': now,
            }

        self._update_best_server()

    # ── Flow Management ──────────────────────────────────────────────────
    def _add_flow(self, datapath, priority, match, actions,
                  hard_timeout=0, idle_timeout=0):
        ofproto = datapath.ofproto
        parser  = datapath.ofproto_parser
        inst    = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod     = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=inst,
            hard_timeout=hard_timeout,
            idle_timeout=idle_timeout,
        )
        datapath.send_msg(mod)

    def _send_packet(self, datapath, port, pkt):
        ofproto = datapath.ofproto
        parser  = datapath.ofproto_parser
        pkt.serialize()
        data    = pkt.data
        actions = [parser.OFPActionOutput(port=port)]
        out     = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=ofproto.OFP_NO_BUFFER,
            in_port=ofproto.OFPP_CONTROLLER,
            actions=actions,
            data=data,
        )
        datapath.send_msg(out)

    # ── REST API data getter ──────────────────────────────────────────────
    def get_stats(self):
        total_bytes = sum(s['bytes'] for s in self.server_stats.values())
        servers_out = []
        for srv in SERVERS:
            sid  = srv['id']
            ss   = self.server_stats[sid]
            load_pct = (ss['bytes'] / total_bytes * 100) if total_bytes > 0 else 0
            servers_out.append({
                'id':      sid,
                'ip':      srv['ip'],
                'port':    srv['port'],
                'latency': ss['latency'],
                'packets': ss['packets'],
                'bytes':   ss['bytes'],
                'load':    round(load_pct, 1),
                'active':  ss['active'],
            })
        return {
            'best_server':    self.best_server,
            'total_flows':    self.total_flows,
            'packet_in_rate': self.packet_in_rate,
            'vip':            VIP_IP,
            'servers':        servers_out,
        }


# ─── REST Controller ──────────────────────────────────────────────────────
class SDNRestController(ControllerBase):
    def __init__(self, req, link, data, **config):
        super().__init__(req, link, data, **config)
        self.lb_app = data[REST_APP_NAME]

    @route('sdnlb', '/sdn/stats', methods=['GET'])
    def get_stats(self, req, **kwargs):
        from webob import Response
        data = self.lb_app.get_stats()
        body = json.dumps(data, indent=2)
        return Response(
            content_type='application/json',
            body=body,
            headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type',
            }
        )

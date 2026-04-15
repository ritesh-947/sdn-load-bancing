from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel, info
import os

class LoadBalancerTopo(Topo):
    def build(self):
        # Add switch
        # OpenFlow 1.3 requires newer OVS
        s1 = self.addSwitch('s1', cls=OVSSwitch, protocols='OpenFlow13', dpid='0000000000000001')

        # Add client host
        h1 = self.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
        
        # Add backend server hosts
        h2 = self.addHost('h2', ip='10.0.0.2/24', mac='00:00:00:00:00:02')
        h3 = self.addHost('h3', ip='10.0.0.3/24', mac='00:00:00:00:00:03')
        h4 = self.addHost('h4', ip='10.0.0.4/24', mac='00:00:00:00:00:04')

        # Add links with varying latency to simulate real-world conditions
        # Client link
        self.addLink(h1, s1, bw=100, delay='1ms')
        
        # Server links with different latencies (Server 3 is the fastest)
        self.addLink(s1, h2, bw=100, delay='5ms')   # Server 1 - Moderate latency
        self.addLink(s1, h3, bw=100, delay='12ms')  # Server 2 - High latency
        self.addLink(s1, h4, bw=100, delay='2ms')   # Server 3 - Low latency

def run_network():
    topo = LoadBalancerTopo()
    # Use TCLink to support delay and bandwidth restrictions
    net = Mininet(topo=topo, controller=lambda name: RemoteController(name, ip='127.0.0.1', port=6653), switch=OVSSwitch, link=TCLink)
    
    info('*** Starting network\n')
    net.start()
    
    h1 = net.get('h1')
    h2 = net.get('h2')
    h3 = net.get('h3')
    h4 = net.get('h4')

    info('*** Starting test HTTP Servers on Backend Hosts\n')
    
    # We create a dummy response file for each server to identify them
    for i, h in enumerate([h2, h3, h4], start=2):
        h.cmd(f'echo "Response from Server h{i}" > index.html')
        h.cmd('python3 -m http.server 80 &')

    # Provide client (h1) with static ARP for the Virtual IP (VIP = 10.0.0.100)
    # The load balancer controller will handle packets directed to this MAC
    info('*** Setting static ARP on client for Virtual IP (10.0.0.100)\n')
    h1.cmd('arp -s 10.0.0.100 00:00:00:00:00:AA')
    
    info('*** Preparing to run ping to Virtual IP...\n')
    info('    (In a separate terminal, test this by running: `mininet> h1 curl http://10.0.0.100`)\n')

    info('*** Running CLI\n')
    CLI(net)
    
    info('*** Stopping network\n')
    net.stop()
    os.system('mn -c')

if __name__ == '__main__':
    setLogLevel('info')
    # Cleanup previous instances just in case
    os.system('mn -c >/dev/null 2>&1')
    run_network()

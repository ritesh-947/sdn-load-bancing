/* =============================================================
   SDN Load Balancer Dashboard – app.js
   Handles:
     · Demo / Live mode toggle
     · Simulated controller state (matches Ryu REST API schema)
     · Topology SVG rendering & animated packet flows
     · Chart.js latency + traffic charts
     · Server card live updates
     · Controller event log
   =============================================================*/

'use strict';

// ─── Config ────────────────────────────────────────────────────────────────
const CONFIG = {
  RYU_BASE_URL:    'http://127.0.0.1:8080',
  POLL_INTERVAL_MS: 2000,
  LOG_MAX_ENTRIES:  80,
  LATENCY_HISTORY:  30,         // data-points to keep in latency chart
};

// ─── Server Definitions ────────────────────────────────────────────────────
const SERVERS = [
  { id: 'h2', name: 'Server h2', ip: '10.0.0.2', port: 2, baseLat: 5,  color: '#a78bfa', colorSoft: 'rgba(167,139,250,0.15)', cardColor: 'purple' },
  { id: 'h3', name: 'Server h3', ip: '10.0.0.3', port: 3, baseLat: 12, color: '#38bdf8', colorSoft: 'rgba(56,189,248,0.15)',  cardColor: 'cyan'   },
  { id: 'h4', name: 'Server h4', ip: '10.0.0.4', port: 4, baseLat: 2,  color: '#34d399', colorSoft: 'rgba(52,211,153,0.15)',  cardColor: 'green'  },
];

// ─── Application State ─────────────────────────────────────────────────────
const state = {
  isLive:        false,
  startTime:     Date.now(),
  totalFlows:    0,
  packetInRate:  0,
  lastPacketIn:  0,
  bestServer:    'h4',           // lowest base latency
  servers: {
    h2: { packets: 0, bytes: 0, load: 0, latency: 5  },
    h3: { packets: 0, bytes: 0, load: 0, latency: 12 },
    h4: { packets: 0, bytes: 0, load: 0, latency: 2  },
  },
  latencyHistory: {
    labels: [],
    h2: [], h3: [], h4: [],
  },
};

// ─── Utility ───────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

function fmtBytes(b) {
  if (b < 1024)       return b + ' B';
  if (b < 1048576)    return (b / 1024).toFixed(1) + ' KB';
  return (b / 1048576).toFixed(2) + ' MB';
}

function fmtTime(ms) {
  const s  = Math.floor(ms / 1000);
  const h  = Math.floor(s / 3600);
  const m  = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  return [h, m, ss].map(v => String(v).padStart(2, '0')).join(':');
}

function now() {
  const d = new Date();
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map(v => String(v).padStart(2, '0')).join(':');
}

function flashValue(el) {
  el.classList.remove('value-update');
  void el.offsetWidth;
  el.classList.add('value-update');
}

// ─── Event Log ─────────────────────────────────────────────────────────────
const logContainer  = $('log-container');
const logEmptyState = $('log-empty');
let  logCount = 0;

function addLog(type, msg) {
  if (logEmptyState) logEmptyState.remove();

  const entry = document.createElement('div');
  entry.className = 'log-entry';
  entry.innerHTML = `
    <span class="log-time">${now()}</span>
    <span class="log-tag ${type}">${type}</span>
    <span class="log-msg">${msg}</span>
  `;
  logContainer.prepend(entry);

  logCount++;
  const entries = logContainer.querySelectorAll('.log-entry');
  if (entries.length > CONFIG.LOG_MAX_ENTRIES) {
    entries[entries.length - 1].remove();
  }
}

$('clear-log-btn').addEventListener('click', () => {
  logContainer.innerHTML = '<div class="log-empty-state" id="log-empty">Log cleared.</div>';
  logCount = 0;
});

// ─── Topology SVG ──────────────────────────────────────────────────────────
const svg = $('topology-svg');

// Node positions [cx, cy]
const NODES = {
  client: { cx: 80,  cy: 200, label: 'Client h1', sublabel: '10.0.0.1', type: 'client' },
  switch: { cx: 300, cy: 200, label: 'Switch s1',  sublabel: 'OVS · OF1.3', type: 'switch' },
  h2:     { cx: 520, cy: 90,  label: 'Server h2',  sublabel: '10.0.0.2', type: 'server', sid: 'h2' },
  h3:     { cx: 520, cy: 200, label: 'Server h3',  sublabel: '10.0.0.3', type: 'server', sid: 'h3' },
  h4:     { cx: 520, cy: 310, label: 'Server h4',  sublabel: '10.0.0.4', type: 'server', sid: 'h4' },
};

// Edges
const EDGES = [
  { from: 'client', to: 'switch', id: 'link-cs' },
  { from: 'switch', to: 'h2',    id: 'link-h2' },
  { from: 'switch', to: 'h3',    id: 'link-h3' },
  { from: 'switch', to: 'h4',    id: 'link-h4' },
];

function buildTopology() {
  svg.innerHTML = '';

  // Defs
  const defs = document.createElementNS('http://www.w3.org/2000/svg','defs');
  defs.innerHTML = `
    <linearGradient id="linkGradActive" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#7c6fff"/>
      <stop offset="100%" stop-color="#06b6d4"/>
    </linearGradient>
    <filter id="glow-purple">
      <feGaussianBlur in="SourceGraphic" stdDeviation="3" result="blur"/>
      <feColorMatrix in="blur" type="matrix" values="0 0 0 0 0.49  0 0 0 0 0.44  0 0 0 0 1  0 0 0 1 0" result="colored"/>
      <feMerge><feMergeNode in="colored"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <filter id="glow-green">
      <feGaussianBlur in="SourceGraphic" stdDeviation="4" result="blur"/>
      <feColorMatrix in="blur" type="matrix" values="0 0 0 0 0.06  0 0 0 0 0.73  0 0 0 0 0.51  0 0 0 1 0" result="colored"/>
      <feMerge><feMergeNode in="colored"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <radialGradient id="nodeGrad-client" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#a78bfa"/>
      <stop offset="100%" stop-color="#6d28d9"/>
    </radialGradient>
    <radialGradient id="nodeGrad-switch" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#38bdf8"/>
      <stop offset="100%" stop-color="#0369a1"/>
    </radialGradient>
    <radialGradient id="nodeGrad-h2" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#a78bfa"/>
      <stop offset="100%" stop-color="#5b21b6"/>
    </radialGradient>
    <radialGradient id="nodeGrad-h3" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#38bdf8"/>
      <stop offset="100%" stop-color="#0c4a6e"/>
    </radialGradient>
    <radialGradient id="nodeGrad-h4" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#34d399"/>
      <stop offset="100%" stop-color="#065f46"/>
    </radialGradient>
    <marker id="arrowhead" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
      <path d="M0,0 L0,6 L6,3 z" fill="rgba(124,111,255,0.5)"/>
    </marker>
  `;
  svg.appendChild(defs);

  // Draw edges first
  EDGES.forEach(edge => {
    const from = NODES[edge.from];
    const to   = NODES[edge.to];
    const line = document.createElementNS('http://www.w3.org/2000/svg','line');
    line.setAttribute('x1', from.cx);
    line.setAttribute('y1', from.cy);
    line.setAttribute('x2', to.cx);
    line.setAttribute('y2', to.cy);
    line.setAttribute('id', edge.id);
    line.classList.add('topo-link', 'idle');
    svg.appendChild(line);
  });

  // Draw nodes
  Object.entries(NODES).forEach(([key, node]) => {
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.setAttribute('id', `node-${key}`);
    g.style.cursor = 'pointer';

    const isSwitch = node.type === 'switch';
    const r = isSwitch ? 22 : 18;

    // Outer glow ring (only show best server)
    const ring = document.createElementNS('http://www.w3.org/2000/svg','circle');
    ring.setAttribute('cx', node.cx);
    ring.setAttribute('cy', node.cy);
    ring.setAttribute('r', r + 7);
    ring.setAttribute('fill', 'none');
    ring.setAttribute('stroke', node.sid === state.bestServer ? '#34d399' : '#7c6fff');
    ring.setAttribute('stroke-width', '1.5');
    ring.setAttribute('opacity', '0');
    ring.setAttribute('id', `ring-${key}`);
    g.appendChild(ring);

    // Main circle
    const circle = document.createElementNS('http://www.w3.org/2000/svg','circle');
    circle.setAttribute('cx', node.cx);
    circle.setAttribute('cy', node.cy);
    circle.setAttribute('r',  r);
    circle.setAttribute('fill',   `url(#nodeGrad-${key})`);
    circle.setAttribute('stroke', 'rgba(255,255,255,0.08)');
    circle.setAttribute('stroke-width', '1.5');
    circle.classList.add('topo-node-circle');
    if (isSwitch) circle.setAttribute('filter', 'url(#glow-purple)');
    if (node.sid === state.bestServer) circle.setAttribute('filter', 'url(#glow-green)');
    g.appendChild(circle);

    // Icon text inside circle
    const icon = document.createElementNS('http://www.w3.org/2000/svg','text');
    icon.setAttribute('x', node.cx);
    icon.setAttribute('y', node.cy + 1);
    icon.setAttribute('text-anchor', 'middle');
    icon.setAttribute('dominant-baseline', 'middle');
    icon.setAttribute('fill', 'rgba(255,255,255,0.9)');
    icon.setAttribute('font-size', isSwitch ? '11' : '9');
    icon.setAttribute('font-family', 'Inter, sans-serif');
    icon.setAttribute('font-weight', '700');
    icon.textContent = isSwitch ? 'S1' : (node.type === 'client' ? 'H1' : node.label.slice(-2));
    g.appendChild(icon);

    // Label below
    const label = document.createElementNS('http://www.w3.org/2000/svg','text');
    label.setAttribute('x', node.cx);
    label.setAttribute('y', node.cy + r + 16);
    label.setAttribute('text-anchor', 'middle');
    label.setAttribute('fill', '#94a3b8');
    label.setAttribute('font-size', '10');
    label.setAttribute('font-family', 'Inter, sans-serif');
    label.setAttribute('font-weight', '600');
    label.textContent = node.label;
    g.appendChild(label);

    // Sub-label (IP)
    const sublabel = document.createElementNS('http://www.w3.org/2000/svg','text');
    sublabel.setAttribute('x', node.cx);
    sublabel.setAttribute('y', node.cy + r + 28);
    sublabel.setAttribute('text-anchor', 'middle');
    sublabel.setAttribute('fill', '#475569');
    sublabel.setAttribute('font-size', '9');
    sublabel.setAttribute('font-family', "'JetBrains Mono', monospace");
    sublabel.textContent = node.sublabel;
    g.appendChild(sublabel);

    svg.appendChild(g);
  });
}

// Animate packet flowing along an edge
function animatePacket(edgeId, color = '#ffffff') {
  const link = svg.querySelector(`#${edgeId}`);
  if (!link) return;

  const x1 = +link.getAttribute('x1');
  const y1 = +link.getAttribute('y1');
  const x2 = +link.getAttribute('x2');
  const y2 = +link.getAttribute('y2');

  const pkt = document.createElementNS('http://www.w3.org/2000/svg','circle');
  pkt.setAttribute('r', '4');
  pkt.setAttribute('fill', color);
  pkt.setAttribute('style', `filter: drop-shadow(0 0 6px ${color}80)`);
  svg.appendChild(pkt);

  const duration = 700;
  const start = performance.now();

  function step(ts) {
    const t = Math.min((ts - start) / duration, 1);
    pkt.setAttribute('cx', x1 + (x2 - x1) * t);
    pkt.setAttribute('cy', y1 + (y2 - y1) * t);
    if (t < 1) {
      requestAnimationFrame(step);
    } else {
      pkt.remove();
    }
  }
  requestAnimationFrame(step);
}

function setActiveLink(serverId) {
  ['h2', 'h3', 'h4'].forEach(sid => {
    const link = svg.querySelector(`#link-${sid}`);
    if (!link) return;
    if (sid === serverId) {
      link.classList.remove('idle');
      link.classList.add('active');
    } else {
      link.classList.remove('active');
      link.classList.add('idle');
    }
  });

  // Also highlight best server node ring
  SERVERS.forEach(s => {
    const ring = svg.querySelector(`#ring-${s.id}`);
    if (!ring) return;
    if (s.id === serverId) {
      ring.setAttribute('opacity', '0.7');
      ring.setAttribute('stroke', '#34d399');
    } else {
      ring.setAttribute('opacity', '0');
    }
  });
}

// ─── Charts ────────────────────────────────────────────────────────────────
let latencyChart, trafficChart;

const CHART_DEFAULTS = {
  responsive: true,
  maintainAspectRatio: false,
  animation: { duration: 400 },
  plugins: { legend: { display: false }, tooltip: {
    backgroundColor: 'rgba(15,23,42,0.95)',
    titleColor: '#94a3b8',
    bodyColor: '#f1f5f9',
    borderColor: 'rgba(255,255,255,0.07)',
    borderWidth: 1,
    padding: 10,
    cornerRadius: 8,
  }},
  scales: {
    x: {
      grid: { color: 'rgba(255,255,255,0.04)', drawTicks: false },
      ticks: { color: '#475569', font: { size: 10, family: 'JetBrains Mono' }, maxTicksLimit: 6 },
    },
    y: {
      grid: { color: 'rgba(255,255,255,0.04)', drawTicks: false },
      ticks: { color: '#475569', font: { size: 10, family: 'JetBrains Mono' } },
    }
  }
};

function buildCharts() {
  // Latency Chart
  const lctx = $('latency-chart').getContext('2d');
  latencyChart = new Chart(lctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        {
          label: 'Server h2',
          data: [],
          borderColor: '#a78bfa',
          backgroundColor: 'rgba(167,139,250,0.08)',
          borderWidth: 2,
          pointRadius: 3,
          pointHoverRadius: 5,
          tension: 0.4,
          fill: true,
        },
        {
          label: 'Server h3',
          data: [],
          borderColor: '#38bdf8',
          backgroundColor: 'rgba(56,189,248,0.06)',
          borderWidth: 2,
          pointRadius: 3,
          pointHoverRadius: 5,
          tension: 0.4,
          fill: true,
        },
        {
          label: 'Server h4',
          data: [],
          borderColor: '#34d399',
          backgroundColor: 'rgba(52,211,153,0.06)',
          borderWidth: 2,
          pointRadius: 3,
          pointHoverRadius: 5,
          tension: 0.4,
          fill: true,
        },
      ],
    },
    options: {
      ...CHART_DEFAULTS,
      scales: {
        ...CHART_DEFAULTS.scales,
        y: { ...CHART_DEFAULTS.scales.y, title: { display: true, text: 'ms', color: '#475569', font: { size: 10 } } },
      }
    }
  });

  // Traffic Chart
  const tctx = $('traffic-chart').getContext('2d');
  trafficChart = new Chart(tctx, {
    type: 'bar',
    data: {
      labels: ['Server h2', 'Server h3', 'Server h4'],
      datasets: [{
        label: 'Packets routed',
        data: [0, 0, 0],
        backgroundColor: ['rgba(167,139,250,0.6)', 'rgba(56,189,248,0.6)', 'rgba(52,211,153,0.6)'],
        borderColor:      ['#a78bfa', '#38bdf8', '#34d399'],
        borderWidth: 1.5,
        borderRadius: 6,
        borderSkipped: false,
      }]
    },
    options: {
      ...CHART_DEFAULTS,
      plugins: { ...CHART_DEFAULTS.plugins, legend: { display: false } },
      scales: {
        x: { ...CHART_DEFAULTS.scales.x },
        y: { ...CHART_DEFAULTS.scales.y, beginAtZero: true },
      }
    }
  });
}

function pushLatencyHistory(h2lat, h3lat, h4lat) {
  const label = now();
  const hist  = state.latencyHistory;

  hist.labels.push(label);
  hist.h2.push(h2lat);
  hist.h3.push(h3lat);
  hist.h4.push(h4lat);

  if (hist.labels.length > CONFIG.LATENCY_HISTORY) {
    hist.labels.shift();
    hist.h2.shift();
    hist.h3.shift();
    hist.h4.shift();
  }

  latencyChart.data.labels          = [...hist.labels];
  latencyChart.data.datasets[0].data = [...hist.h2];
  latencyChart.data.datasets[1].data = [...hist.h3];
  latencyChart.data.datasets[2].data = [...hist.h4];
  latencyChart.update('none');
}

function updateTrafficChart() {
  const s = state.servers;
  trafficChart.data.datasets[0].data = [s.h2.packets, s.h3.packets, s.h4.packets];
  trafficChart.update('none');
}

// ─── UI Updates ────────────────────────────────────────────────────────────
function updateStatCards() {
  const best = state.bestServer;
  const srv  = SERVERS.find(s => s.id === best);

  const bestVal = $('best-server-val');
  bestVal.textContent = srv ? srv.name : '—';
  flashValue(bestVal);

  $('best-server-sub').textContent = srv ? `${state.servers[best].latency}ms · Lowest latency` : 'Lowest latency';

  const lats = SERVERS.map(s => state.servers[s.id].latency);
  const avg  = (lats.reduce((a,b) => a+b, 0) / lats.length).toFixed(1);
  const avgEl = $('avg-latency-val');
  avgEl.textContent = avg + ' ms';
  flashValue(avgEl);

  const flowEl = $('total-flows-val');
  flowEl.textContent = state.totalFlows.toLocaleString();
  flashValue(flowEl);

  const pkEl = $('packet-in-val');
  pkEl.innerHTML = `${state.packetInRate} <small>/s</small>`;
}

function updateServerCard(sid) {
  const s    = state.servers[sid];
  const isBest = (sid === state.bestServer);

  const card = $(`server-card-${sid}`);
  if (!card) return;

  // Best server highlight
  if (isBest) {
    card.classList.add('best-server');
  } else {
    card.classList.remove('best-server');
  }

  // Latency badge
  const badge = $(`lat-badge-${sid}`);
  badge.textContent = s.latency + 'ms';
  if (isBest) badge.classList.add('best');
  else        badge.classList.remove('best');

  // Status indicator
  const ind = $(`ind-${sid}`);
  if (s.load > 75) ind.className = 'server-indicator busy';
  else             ind.className = 'server-indicator online';

  // Load bar
  const load = Math.min(s.load, 100);
  $(`load-bar-${sid}`).style.width = load + '%';
  $(`load-val-${sid}`).textContent = load.toFixed(0) + '%';

  // Stats
  $(`pkt-${sid}`).textContent   = s.packets.toLocaleString();
  $(`bytes-${sid}`).textContent = fmtBytes(s.bytes);
  $(`latency-${sid}`).textContent = s.latency + ' ms';
}

function updateAll() {
  updateStatCards();
  SERVERS.forEach(s => updateServerCard(s.id));
  updateTrafficChart();
  setActiveLink(state.bestServer);
}

// ─── Demo / Simulation Engine ──────────────────────────────────────────────
const BASE_LATENCIES = { h2: 5, h3: 12, h4: 2 };

function simulateTick() {
  // Add jitter to latencies
  SERVERS.forEach(srv => {
    const base    = BASE_LATENCIES[srv.id];
    const jitter  = (Math.random() - 0.5) * base * 0.6;
    state.servers[srv.id].latency = Math.max(1, +(base + jitter).toFixed(1));
  });

  // Determine best server by minimum latency
  let minLat = Infinity;
  SERVERS.forEach(srv => {
    if (state.servers[srv.id].latency < minLat) {
      minLat = state.servers[srv.id].latency;
      state.bestServer = srv.id;
    }
  });

  // Route a packet to best server
  const best     = state.bestServer;
  const pktSize  = Math.floor(Math.random() * 1400) + 64;
  const pktCount = Math.floor(Math.random() * 3) + 1;

  SERVERS.forEach(srv => {
    state.servers[srv.id].load    = Math.max(0, state.servers[srv.id].load * 0.92 + (Math.random() * 2));
  });
  state.servers[best].packets += pktCount;
  state.servers[best].bytes   += pktSize * pktCount;
  state.servers[best].load     = Math.min(95, state.servers[best].load + Math.random() * 8);
  state.totalFlows             += pktCount;

  // Packet-in rate
  state.packetInRate = Math.floor(Math.random() * 8) + 2;

  // Animate packets on SVG
  const srv = SERVERS.find(s => s.id === best);
  // Client → Switch
  animatePacket('link-cs', '#7c6fff');
  setTimeout(() => animatePacket(`link-${best}`, srv.color), 400);

  // Latency history
  pushLatencyHistory(
    state.servers.h2.latency,
    state.servers.h3.latency,
    state.servers.h4.latency,
  );

  // Event log entry
  const logTypes  = ['route', 'info', 'route', 'route', 'warn'];
  const logType   = logTypes[Math.floor(Math.random() * logTypes.length)];
  const messages  = {
    route: `Routed ${pktCount} pkt(s) → ${best} (10.0.0.${SERVERS.findIndex(s=>s.id===best)+2}) · ${state.servers[best].latency}ms`,
    info:  `PacketIn event · src=10.0.0.1 dst=10.0.0.100 · size=${pktSize}B`,
    warn:  `Load on ${best} rising: ${state.servers[best].load.toFixed(0)}%`,
  };
  addLog(logType, messages[logType] || messages.info);

  updateAll();
}

// ─── Live API Mode ─────────────────────────────────────────────────────────
async function fetchLiveData() {
  try {
    // Ryu REST API: /stats/flow/<dpid> & /stats/port/<dpid>
    const DPID = 1;
    const [portRes] = await Promise.all([
      fetch(`${CONFIG.RYU_BASE_URL}/stats/port/${DPID}`),
    ]);

    if (!portRes.ok) throw new Error(`HTTP ${portRes.status}`);

    const portData = await portRes.json();
    const ports = portData[DPID] || [];

    // Port 1 = client, Port 2 = h2, Port 3 = h3, Port 4 = h4
    const serverMap = { 2: 'h2', 3: 'h3', 4: 'h4' };

    ports.forEach(p => {
      const sid = serverMap[p.port_no];
      if (!sid) return;
      state.servers[sid].packets = p.rx_packets || 0;
      state.servers[sid].bytes   = p.rx_bytes   || 0;
    });

    state.totalFlows = Object.values(state.servers).reduce((a, s) => a + s.packets, 0);

    // For latency estimation we still apply jitter simulation
    SERVERS.forEach(srv => {
      const base   = BASE_LATENCIES[srv.id];
      const jitter = (Math.random() - 0.5) * 2;
      state.servers[srv.id].latency = Math.max(1, +(base + jitter).toFixed(1));
      const total = Math.max(...SERVERS.map(s => state.servers[s.id].packets), 1);
      state.servers[srv.id].load = (state.servers[srv.id].packets / total) * 100;
    });

    // Best server
    let minLat = Infinity;
    SERVERS.forEach(srv => {
      if (state.servers[srv.id].latency < minLat) {
        minLat = state.servers[srv.id].latency;
        state.bestServer = srv.id;
      }
    });

    pushLatencyHistory(state.servers.h2.latency, state.servers.h3.latency, state.servers.h4.latency);
    addLog('info', `Live poll OK · ${ports.length} ports · best=${state.bestServer}`);
    setStatus('connected', `Connected · ${CONFIG.RYU_BASE_URL}`);
    updateAll();
  } catch (err) {
    addLog('error', `API error: ${err.message}. Falling back to demo.`);
    setStatus('demo', 'Demo Mode (API Offline)');
    state.isLive = false;
    startDemo();
  }
}

// ─── Status ───────────────────────────────────────────────────────────────
function setStatus(type, text) {
  const pill = $('connection-status');
  pill.className = `status-pill ${type}`;
  $('status-text').textContent = text;
}

// ─── Uptime Timer ─────────────────────────────────────────────────────────
function tickUptime() {
  $('uptime-display').textContent = fmtTime(Date.now() - state.startTime);
}

// ─── Polling ──────────────────────────────────────────────────────────────
let demoInterval, liveInterval;

function startDemo() {
  clearInterval(liveInterval);
  clearInterval(demoInterval);
  setStatus('demo', 'Demo Mode (Simulated Data)');
  simulateTick();
  demoInterval = setInterval(simulateTick, CONFIG.POLL_INTERVAL_MS);
}

function startLive() {
  clearInterval(demoInterval);
  clearInterval(liveInterval);
  setStatus('connecting', 'Connecting…');
  fetchLiveData();
  liveInterval = setInterval(fetchLiveData, CONFIG.POLL_INTERVAL_MS);
}

// ─── Mode Toggle ─────────────────────────────────────────────────────────
$('toggle-mode-btn').addEventListener('click', () => {
  state.isLive = !state.isLive;
  $('mode-label').textContent = state.isLive ? 'Live Mode' : 'Demo Mode';
  if (state.isLive) {
    addLog('info', 'Switched to Live Mode. Polling Ryu API…');
    startLive();
  } else {
    addLog('warn', 'Switched to Demo Mode. Simulating data.');
    startDemo();
  }
});

// ─── Bootstrap ────────────────────────────────────────────────────────────
function init() {
  buildTopology();
  buildCharts();

  // Initial log messages
  addLog('info', 'Dashboard initialized. Running in Demo Mode.');
  addLog('info', 'Topology: h1 → s1 → [h2 | h3 | h4]');
  addLog('route', 'Load balancer algorithm: Minimum Latency');

  // Start demo simulation
  startDemo();

  // Uptime tick
  setInterval(tickUptime, 1000);
}

// Wait for DOM
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

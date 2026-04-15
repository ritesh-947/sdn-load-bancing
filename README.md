# 🚀 SDN Load Balancer using Weighted Algorithm

An advanced **Software Defined Networking (SDN) Load Balancer** project that uses a **Weighted Dijkstra Algorithm** based on:

- Bandwidth
- Latency
- Packet Loss
- Link Utilization

Includes a **real-time interactive dashboard** + **Python-based controller logic**.

---

## 📌 Features

### 🔹 Intelligent Load Balancing

- Weighted Dijkstra Algorithm
- Dynamic path selection
- Congestion-aware routing

### 🔹 Multiple Algorithms

- Weighted Dijkstra (default)
- Minimum Latency
- Round Robin
- Least Connections

### 🔹 Real-Time Dashboard

- Interactive network topology visualization
- Live packet simulation
- Path switching visualization
- Congestion injection (manual testing)

### 🔹 Metrics & Monitoring

- Packet sent / delivered / dropped
- Latency tracking
- Link utilization
- Traffic distribution across branches

### 🔹 Advanced Controls

- Adjust weights:
  - Bandwidth
  - Latency
  - Packet Loss
  - Utilization
- Control animation speed
- Auto packet generation

---

## 🏗️ Project Structure

sdn-load-balancing/
│
├── index.html # Main dashboard UI
├── styles.css # UI styling (dark theme)
├── app.js # Frontend logic & simulation
│
├── sdn_load_balancer.py # Core load balancing logic
├── ryu_loadbalancer.py # Ryu controller implementation
│
└── README.md

---

## ⚙️ Technologies Used

### 🌐 Frontend

- HTML, CSS, JavaScript
- SVG for topology visualization
- Chart.js for graphs

### 🧠 Backend / Controller

- Python
- Ryu SDN Controller framework

### 🌍 Networking Concepts

- SDN (Software Defined Networking)
- OpenFlow Protocol
- Dijkstra Algorithm
- Load Balancing Techniques

---

## 🧠 Algorithm Explanation

The system uses a **Weighted Cost Function**:

Cost = (W_BW × Bandwidth)

- (W_LAT × Latency)
- (W_LOSS × Packet Loss)
- (W_UTIL × Utilization)

* Lower cost path is selected
* Weights are adjustable in UI
* Helps simulate real-world network behavior

---

## ▶️ How to Run

### 🔹 1. Clone Repository

```bash
git clone https://github.com/ritesh-947/sdn-load-bancing.git
cd sdn-load-bancing


⸻

🔹 2. Run Frontend

Simply open:

index.html

in browser

⸻

🔹 3. Run Ryu Controller (Optional)

Make sure you have Ryu installed:

pip install ryu

Run:

ryu-manager ryu_loadbalancer.py


⸻

🎮 How to Use
	•	Click Send Packet → simulate traffic
	•	Use Auto Mode → continuous packets
	•	Adjust weights → observe path changes
	•	Use Spike buttons → simulate congestion
	•	Switch algorithms → compare behavior

⸻

📊 Output
	•	Real-time routing path
	•	Packet distribution across servers
	•	Latency charts
	•	Traffic graphs

⸻

🎯 Use Cases
	•	Network simulation learning
	•	SDN research projects
	•	Load balancing algorithm comparison
	•	Academic demonstrations

⸻

⭐ Future Improvements
	•	Real network integration (Mininet)
	•	AI-based routing optimization
	•	Cloud deployment
	•	Mobile dashboard

⸻

📜 License

This project is for educational purposes.
```

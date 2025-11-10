# GPU Resource Manager & Proxy for Ollama  
*A smart GPU-aware proxy for Proxmox VE that dynamically manages GPU resources between Ollama and GPU-intensive background processes.*

---

## ✅ Overview

The **GPU Resource Manager & Proxy for Ollama** is a lightweight Python service that sits between your applications and the Ollama server. It intelligently supervises NVIDIA GPU usage on **Proxmox VE** hosts and ensures that GPU-intensive background tasks (e.g. mining) are temporarily suspended whenever Ollama requires GPU power.

This enables smooth coexistence of AI workloads and other GPU idle tasks on the same host.

---

## ✨ Features

### 🔍 Intelligent GPU Monitoring  
- Detects GPU usage patterns and active processes via `nvidia-smi`.
- Differentiates essential system processes from idle GPU workloads.

### ⚙️ Dynamic Resource Allocation  
- Automatically pauses idle/non-critical GPU processes when Ollama becomes active.  
- Automatically resumes them after configurable inactivity timeouts.

### 🗓️ Scheduled Blackout Window  
- By Default Automatically stops idle GPU processes between **2:15 AM – 3:30 AM** for maintenance.

### 🖥️ Proxmox LXC Integration  
- Direct container control using Proxmox's `pct` command.  
- Ideal for GPU-passthrough LXC containers (miners, renderers, etc.).

### ⚡ Real-Time Process Detection  
- Inspects NVIDIA GPU processes continuously.  
- Supports customizable allow-lists and idle-process lists.

---

## 📦 Requirements

- NVIDIA GPU + drivers  
- `nvidia-smi`  
- Python **3.x**  
- `python3-requests`  
- Proxmox VE host  
- At least one GPU-passthrough LXC container  
- Optional: Ollama server running inside LXC

Install required packages:

```bash
sudo apt update
sudo apt install python3 python3-requests
```

---

## ⚙️ Configuration

These are the primary configuration variables inside the script:

```python
# Basic Configuration
OLLAMA_HOST = "localhost"      # Ollama container IP
OLLAMA_PORT = 11434            # Ollama API port
PROXY_PORT = 11435             # Proxy server port
GPU_CHECK_INTERVAL = 10        # Seconds between GPU checks

# GPU Process Management
IDLE_NvGPU_PROCESSES = ['t-rex', 'trex', 'miner', 'xmrig', 'lolminer', 'nbminer']
KNOWN_NvidiaGPU_PROCESSES = ['Xorg']  
IDLE_CONTAINER_ID = "120"      # LXC container ID of idle GPU workload
Blackout_schedule_Start = 2, 15 #when to start stopping the idle NvGPU container. Hour, Minute.
Blackout_schedule_End = 3, 30 #when to allow starting the idle NvGPU container again. Hour, Minute.

```

---

## 🛠️ Installation (systemd service)

Create the service file:

`/etc/systemd/system/gpu-proxy.service`

```ini
[Unit]
Description=GPU Resource Manager and Proxy for Ollama
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/python3 /usr/local/bin/gpu-proxy.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable gpu-proxy
sudo systemctl start gpu-proxy
```

---

## 🚀 Usage

Forward requests to the **proxy port** instead of directly to Ollama.

Example:

```bash
curl http://proxmox-host:11435/api/generate -d '{
  "model": "llama2",
  "prompt": "Why is the sky blue?",
  "stream": false
}'
```

### API Endpoints Automatically Managed
- `/api/generate`
- `/api/chat`
- `/api/embeddings`
- `/api/load`
- `/api/pull`

Anything GPU-intensive triggers resource management logic.

---

## 🔧 How It Works

### 🔄 Resource Management Flow

1. **Request received** by proxy  
2. Proxy detects GPU-intensive Ollama endpoint  
3. Proxy checks for non-idle GPU processes / idle container  
4. If necessary → **stops idle GPU container**  
5. Forwards request to Ollama
6. Waits for Ollama to Finish (default 120s timeout)
7. Watches GPU activity for non-idle processes
8. Once GPU is idle → **starts idle container**

---

## 📜 Logging

Logs stored in:

```
/var/log/gpu_proxy.log
```

Examples:

```
2025-11-10 15:36:41,882 - INFO - Starting GPU Resource Manager And Proxy for ollama.
2025-11-10 15:36:42,695 - INFO - pct command available (this is the host)
2025-11-10 15:36:42,717 - INFO - Current GPU processes: [{'pid': '2381690', 'name': '/var/lib/cudo-miner/registry/aaf375fd4c7b39548121985bce1e7b64/t-rex', 'memory': '5478 MiB'}]
2025-11-10 15:36:43,524 - INFO - Idle NvGPU container 120 running: True
2025-11-10 15:36:43,525 - INFO - GPU monitoring thread started
2025-11-10 15:36:43,525 - INFO - Proxy server running on port 11435
2025-11-10 15:36:43,525 - INFO - Forwarding to Ollama at localhost:11434
2025-11-10 15:36:43,525 - INFO - Managing idle NvGPU container: 120
2025-11-10 15:36:43,525 - INFO - Monitoring GPU usage and scheduled maintenance windows
2025-11-10 15:36:43,525 - INFO - Idle NvGPU process patterns: ['t-rex', 'trex', 'miner', 'xmrig', 'lolminer', 'nbminer']
2025-11-10 15:36:55,310 - INFO - Localhost - "GET /api/tags HTTP/1.1" 200 -
2025-11-10 15:36:55,332 - INFO - Localhost - "GET /api/ps HTTP/1.1" 200 -
2025-11-10 15:37:01,223 - INFO - GPU-intensive operation detected: /api/chat
2025-11-10 15:37:02,040 - INFO - Force stopping idle NvGPU container for Ollama GPU operation
2025-11-10 15:37:02,859 - INFO - Stopping container 120
2025-11-10 15:37:06,391 - INFO - Container 120 stopped successfully
2025-11-10 15:37:29,546 - INFO - Localhost - "POST /api/chat HTTP/1.1" 200 -
2025-11-10 15:37:29,551 - INFO - Ollama request completed, activity timestamp updated
2025-11-10 15:37:29,664 - INFO - GPU-intensive operation detected: /api/chat
2025-11-10 15:37:39,896 - INFO - Localhost - "POST /api/chat HTTP/1.1" 200 -
2025-11-10 15:37:39,896 - INFO - Ollama request completed, activity timestamp updated
2025-11-10 15:37:39,903 - INFO - GPU-intensive operation detected: /api/chat
2025-11-10 15:38:17,616 - INFO - Localhost - "POST /api/chat HTTP/1.1" 200 -
2025-11-10 15:38:17,616 - INFO - Ollama request completed, activity timestamp updated
2025-11-10 15:38:17,631 - INFO - GPU-intensive operation detected: /api/chat
2025-11-10 15:38:53,068 - INFO - Localhost - "POST /api/chat HTTP/1.1" 200 -
2025-11-10 15:38:53,069 - INFO - Ollama request completed, activity timestamp updated
2025-11-10 15:39:59,855 - INFO - Ollama activity timeout reached
2025-11-10 15:43:58,186 - INFO - GPU idle, starting idle NvGPU container
2025-11-10 15:43:59,001 - INFO - Starting container 120
2025-11-10 15:44:02,739 - INFO - Container 120 started successfully
```

Enable debug mode:

```python
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/gpu_proxy.log'),
        logging.StreamHandler()
    ]
)
```

---

## 🧪 Monitoring & Testing

Check service:

```bash
systemctl status gpu-proxy
```

Tail logs:

```bash
tail -f /var/log/gpu_proxy.log
```

Test GPU processes:

```bash
nvidia-smi --query-compute-apps=pid,process_name,used_memory   --format=csv,noheader,nounits
```

---

## ❗ Troubleshooting

### `pct` command not found  
→ Script must run **on the Proxmox host**, not inside an LXC.

### GPU processes not detected  
- Verify NVIDIA drivers  
- Run `nvidia-smi` manually  
- Ensure GPU passthrough is configured

### Idle container not managed  
- Check the LXC exists  
- Run `pct list`  
- Ensure root permissions

### Proxy connection refused  
- Ensure Ollama is running  
- Check firewall rules  
- Check via curl inside Proxmox:

```bash
curl http://<OLLAMA_HOST>:11434/api/version
```

---

## 🤝 Contributing

Pull requests and issues are welcome!  
If you’d like to contribute, please open an issue first to discuss your idea.

---

## 📄 License

This project is licensed under the **MIT License**. See the `LICENSE` file for details.

---

#!/usr/bin/env python3
import http.server
import socketserver
import requests
import json
import time
import threading
import subprocess
from datetime import datetime, time as dt_time
from urllib.parse import urlparse, parse_qs
import logging
import socket

# Configuration
OLLAMA_HOST = ""  # Your Ollama LXC IP
OLLAMA_PORT = 11434  # Your Ollama LXC Port
PROXY_PORT = 11435  # Port Of This Proxy
OLLAMA_BASE_URL = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}"  # Fixed formatting

# GPU monitoring
GPU_CHECK_INTERVAL = 10  # seconds it waits to check for other process apart from known/compute processes

# process process patterns (from your nvidia-smi output)
IDLE_NvGPU_PROCESSES = ['t-rex', 'trex', 'miner', 'xmrig', 'lolminer', 'nbminer']
KNOWN_NvGPU_PROCESSES = ['Xorg']  # Processes that are allowed when "idle" compute process is running
IDLE_CONTAINER_ID = ""  # running Idle GPU Container ID, example: COMPUTE_CONTAINER_ID ="120"
Blackout_schedule_Start = dt_time(2, 15)  # 2:15 AM
Blackout_schedule_End = dt_time(3, 30)    # 3:30 AM

# ----------------------------------------------------------active-code--------------------------------------------------------------#
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/gpu_proxy.log'),
        logging.StreamHandler()
    ]
)

class GPUResourceManager:
    def __init__(self):
        self.idle_compute_running = False
        self.ollama_active = False
        self.last_ollama_activity = 0
        self.ollama_activity_timeout = 120  # seconds of no activity before considering Ollama done
        self.last_gpu_check = 0
        self.gpu_processes = []
        self.lock = threading.Lock()
        self.operation_in_progress = False

    def run_command(self, cmd):
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            logging.error(f"Command failed: {cmd}, error: {e.stderr}")
            return None

    def is_container_running(self, container_id):
        output = self.run_command(f"pct list | grep \"^{container_id}\"")
        if output and "running" in output:
            return True
        return False

    def stop_container(self, container_id):
        if self.is_container_running(container_id):
            logging.info(f"Stopping container {container_id}")
            result = self.run_command(f"pct stop {container_id}")
            if result is not None:
                max_wait = 15
                waited = 0
                while self.is_container_running(container_id) and waited < max_wait:
                    time.sleep(1)
                    waited += 1

                if not self.is_container_running(container_id):
                    logging.info(f"Container {container_id} stopped successfully")
                    return True
                else:
                    logging.warning(f"Container {container_id} still running after {max_wait} seconds")
                    return False
            else:
                logging.error(f"Failed to stop container {container_id}")
        else:
            logging.debug(f"Container {container_id} already stopped, no action needed")
            return True
        return False

    def start_container(self, container_id):
        if not self.is_container_running(container_id):
            logging.info(f"Starting container {container_id}")
            result = self.run_command(f"pct start {container_id}")
            if result is not None:
                max_wait = 15
                waited = 0
                while not self.is_container_running(container_id) and waited < max_wait:
                    time.sleep(1)
                    waited += 1

                if self.is_container_running(container_id):
                    logging.info(f"Container {container_id} started successfully")
                    return True
                else:
                    logging.error(f"Container {container_id} failed to start within {max_wait} seconds")
            else:
                logging.error(f"Failed to start container {container_id}")
        else:
            logging.debug(f"Container {container_id} already running, no action needed")
            return True
        return False

    def get_gpu_processes(self):
        try:
            output = self.run_command(
                "nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits"
            )
            processes = []
            if output:
                for line in output.split('\n'):
                    if line.strip():
                        parts = line.split(', ')
                        if len(parts) >= 3:
                            processes.append({
                                'pid': parts[0],
                                'name': parts[1].strip(),
                                'memory': parts[2] + ' MiB'
                            })
            return processes
        except Exception as e:
            logging.error(f"Error getting GPU processes: {e}")
            return []

    def is_compute_process(self, process_name):
        process_lower = process_name.lower()
        for pattern in IDLE_NvGPU_PROCESSES:
            if pattern in process_lower:
                return True
        return False

    def is_known_system_process(self, process_name):
        return any(sys_proc in process_name for sys_proc in KNOWN_NvGPU_PROCESSES)

    def is_gpu_idle(self):
        processes = self.get_gpu_processes()

        non_mining_processes = []
        for process in processes:
            if not self.is_compute_process(process['name']) and not self.is_known_system_process(process['name']):
                memory_usage = int(process['memory'].split()[0])
                if memory_usage > 100: #MB 
                    non_mining_processes.append(process)

        is_idle = len(non_mining_processes) == 0

        if is_idle:
            mining_count = len([p for p in processes if self.is_compute_process(p['name'])])
            if mining_count > 0:
                logging.debug("GPU is running idle NvGPU task (acceptable idle state)")
            else:
                logging.debug("GPU is truly idle (no significant processes)")
        else:
            logging.debug(f"GPU is active with non-mining processes: {[p['name'] for p in non_mining_processes]}")

        return is_idle

    def is_Idle_NvGPU_process_active(self):
        processes = self.get_gpu_processes()
        mining_processes = [p for p in processes if self.is_compute_process(p['name'])]
        return len(mining_processes) > 0

    def is_ollama_still_active(self):
        if not self.ollama_active:
            return False
        time_since_last_activity = time.time() - self.last_ollama_activity
        return time_since_last_activity < self.ollama_activity_timeout

    def should_stop_for_schedule(self):
        now = datetime.now().time()
        stop_start = Blackout_schedule_Start 
        stop_end = Blackout_schedule_End    
        in_window = stop_start <= now <= stop_end
        if in_window:
            logging.debug("Within scheduled maintenance window (2:15am-3:30am)")
        return in_window

    def manage_idle_NvGPU_process(self):
        with self.lock:
            if self.operation_in_progress:
                return

            self.operation_in_progress = True
            try:
                current_idle_NvGPU_process_state = self.is_container_running(IDLE_CONTAINER_ID)
                mining_active_on_gpu = self.is_Idle_NvGPU_process_active()
                self.idle_compute_running = current_idle_NvGPU_process_state
                if self.should_stop_for_schedule():
                    if current_idle_NvGPU_process_state:
                        logging.info("Stopping idle NvGPU container due to scheduled maintenance window")
                        self.stop_container(IDLE_CONTAINER_ID)
                        self.idle_compute_running = False
                    return

                ollama_still_active = self.is_ollama_still_active()
                if ollama_still_active:
                    if current_idle_NvGPU_process_state or mining_active_on_gpu:
                        logging.info("Ollama still active, keeping idle NvGPU container stopped")
                        if current_idle_NvGPU_process_state:
                            self.stop_container(IDLE_CONTAINER_ID)
                        self.idle_compute_running = False
                    return
                else:
                    if self.ollama_active:
                        self.ollama_active = False
                        logging.info("Ollama activity timeout reached")

                if self.is_gpu_idle():
                    if not current_idle_NvGPU_process_state and not mining_active_on_gpu:
                        logging.info("GPU idle, starting idle NvGPU container")
                        if self.start_container(IDLE_CONTAINER_ID):
                            self.idle_compute_running = True
                    elif mining_active_on_gpu and not current_idle_NvGPU_process_state:
                        self.idle_compute_running = True
                        logging.debug("Mining active on GPU, updating state")
                else:
                    if current_idle_NvGPU_process_state:
                        logging.info("GPU in use by other process, stopping idle NvGPU container")
                        self.stop_container(IDLE_CONTAINER_ID)
                        self.idle_compute_running = False
            finally:
                self.operation_in_progress = False

    def force_stop_idle_NvGPU_process_for_ollama(self):
        with self.lock:
            self.ollama_active = True
            self.last_ollama_activity = time.time()
            current_idle_NvGPU_process_state = self.is_container_running(IDLE_CONTAINER_ID)
            mining_active_on_gpu = self.is_Idle_NvGPU_process_active()
            if current_idle_NvGPU_process_state or mining_active_on_gpu:
                logging.info("Force stopping idle NvGPU container for Ollama GPU operation")
                if current_idle_NvGPU_process_state:
                    self.stop_container(IDLE_CONTAINER_ID)
                if mining_active_on_gpu:
                    max_wait = 10
                    waited = 0
                    while self.is_Idle_NvGPU_process_active() and waited < max_wait:
                        time.sleep(1)
                        waited += 1
                    if self.is_Idle_NvGPU_process_active():
                        logging.warning("Mining processes still active after container stop")
            else:
                logging.debug("idle NvGPU container already stopped, no action needed")

            self.idle_compute_running = False

    def start_monitoring(self):
        def monitor_loop():
            while True:
                try:
                    self.manage_idle_NvGPU_process()
                except Exception as e:
                    logging.error(f"Error in monitor loop: {e}")
                time.sleep(GPU_CHECK_INTERVAL)

        monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        monitor_thread.start()
        logging.info("GPU monitoring thread started")


class OllamaProxyHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        self.gpu_manager = kwargs.pop('gpu_manager')
        super().__init__(*args, **kwargs)

    def do_GET(self):
        if self._is_gpu_intensive_operation(self.path, {}):
            self.gpu_manager.force_stop_idle_NvGPU_process_for_ollama()
            time.sleep(2)
        self._forward_request('GET')

    def do_HEAD(self):
        if self._is_gpu_intensive_operation(self.path, {}):
            self.gpu_manager.force_stop_idle_NvGPU_process_for_ollama()
            time.sleep(2)
        self._forward_request('HEAD')

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b''

        try:
            request_data = {}
            try:
                request_data = json.loads(post_data.decode('utf-8')) if post_data else {}
            except Exception:
                request_data = {}

            is_gpu_intensive = self._is_gpu_intensive_operation(self.path, request_data)

            if is_gpu_intensive:
                logging.info(f"GPU-intensive operation detected: {self.path}")
                self.gpu_manager.force_stop_idle_NvGPU_process_for_ollama()
                time.sleep(3.5)

            self._forward_request('POST', post_data)

            if is_gpu_intensive:
                with self.gpu_manager.lock:
                    self.gpu_manager.last_ollama_activity = time.time()
                logging.info("Ollama request completed, activity timestamp updated")

        except Exception as e:
            logging.error(f"Error processing request: {e}")
            self.send_error(500, f"Internal server error: {e}")

    def _is_gpu_intensive_operation(self, path, request_data):
        if path == '/api/generate' and request_data.get('keep_alive') == 0:
            logging.debug("Detected model unload request via /api/generate")
            return False
        if path == '/api/chat' and request_data.get('keep_alive') == 0 and request_data.get('messages') == []:
            logging.debug("Detected model unload request via /api/chat")
            return False
        if path in ['/api/generate', '/api/chat', '/api/embeddings']:
            return True
        if path == '/api/load':
            return True
        if path == '/api/pull':
            return request_data.get('stream', True)
        return False

    def _forward_request(self, method, data=None):
        url = f"{OLLAMA_BASE_URL}{self.path}"
        headers = {key: value for key, value in self.headers.items()}

        hop_headers = {
            'connection', 'keep-alive', 'proxy-authenticate',
            'proxy-authorization', 'te', 'trailers', 'upgrade',
            'transfer-encoding'
        }
        for header in list(headers.keys()):
            if header.lower() in hop_headers:
                headers.pop(header, None)

        headers.pop('Host', None)

        timeout = (10, None)

        try:
            if method.upper() in ('GET', 'HEAD'):
                resp = requests.request(method, url, headers=headers, stream=True, timeout=timeout)
            else:
                resp = requests.request(method, url, headers=headers, data=data, stream=True, timeout=timeout)

            self.send_response(resp.status_code)

            for key, value in resp.headers.items():
                k_lower = key.lower()
                if k_lower in hop_headers:
                    continue
                try:
                    self.send_header(key, value)
                except Exception:
                    logging.debug(f"Skipping header {key} due to send_header error")
            self.end_headers()

            try:
                for chunk in resp.iter_content(chunk_size=4096):
                    if not chunk:
                        continue
                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, socket.error) as e:
                        logging.info(f"Client connection closed during streaming: {e}")
                        break
            finally:
                resp.close()

        except requests.exceptions.RequestException as e:
            logging.error(f"Error forwarding to Ollama: {e}")
            try:
                if hasattr(self, '_headers_buffer') and getattr(self, 'wfile', None):
                    try:
                        err_msg = f"\n\n[proxy error] upstream request failed: {e}\n"
                        self.wfile.write(err_msg.encode('utf-8'))
                        self.wfile.flush()
                    except Exception:
                        pass
                else:
                    self.send_error(502, f"Bad gateway: {e}")
            except Exception:
                pass

    def log_message(self, format, *args):
        logging.info(f"{self.address_string()} - {format % args}")


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    logging.info("Starting GPU Resource Manager Proxy on Proxmox Host")

    manager = GPUResourceManager()
    test_output = manager.run_command("pct list > /dev/null && echo 'pct available'")
    if test_output:
        logging.info("pct command available")
    else:
        logging.error("pct command not available! Running on wrong system?")

    gpu_processes = manager.get_gpu_processes()
    logging.info(f"Current GPU processes: {gpu_processes}")

    idle_NvGPU_process_status = manager.is_container_running(IDLE_CONTAINER_ID)
    logging.info(f"idle NvGPU {IDLE_CONTAINER_ID} running: {idle_NvGPU_process_status}")

    gpu_manager = GPUResourceManager()
    gpu_manager.start_monitoring()

    handler = lambda *args, **kwargs: OllamaProxyHandler(*args, gpu_manager=gpu_manager, **kwargs)

    with ThreadedTCPServer(("", PROXY_PORT), handler) as httpd:
        logging.info(f"Proxy server running on port {PROXY_PORT}")
        logging.info(f"Forwarding to Ollama at {OLLAMA_HOST}:{OLLAMA_PORT}")
        logging.info(f"Managing idle NvGPU process: {IDLE_CONTAINER_ID}")
        logging.info("Monitoring GPU usage and scheduled maintenance windows")
        logging.info(f"Mining process patterns: {IDLE_NvGPU_PROCESSES}")

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            logging.info("Shutting down proxy server")
        except Exception as e:
            logging.error(f"Server error: {e}")

if __name__ == "__main__":
    main()
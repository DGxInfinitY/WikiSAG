import os
import sys
import socket
import threading
import signal
import time
import re
import configparser
import subprocess
import shutil
import logging
from logging.handlers import RotatingFileHandler
from libzim.reader import Archive
from libzim.search import Query, Searcher
from markdownify import markdownify
from openai import OpenAI

# --- Local Appliance Paths ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, 'wikisag.ini')
LOG_FILE = os.path.join(BASE_DIR, 'wikisag.log')
SERVICE_NAME = 'wikisag.service'
SERVICE_PATH = f'/etc/systemd/system/{SERVICE_NAME}'

# --- Dedicated File Logger with Rotation ---
log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=2)
file_handler.setFormatter(log_formatter)
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(log_formatter)

logging.basicConfig(level=logging.INFO, handlers=[file_handler, stream_handler])

shutdown_event = threading.Event()
active_threads = []

# --- Graceful Shutdown Handler ---
def signal_handler(sig, frame):
    logging.info("\n[!] Termination signal detected. Initiating graceful shutdown...")
    shutdown_event.set()

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# --- Service Management (The Timeout Fix) ---
def enforce_service_state(desired_state, start_immediately=True):
    """Installs or removes the service. Separates installation from starting."""
    if not sys.stdout.isatty():
        return
    is_installed = os.path.exists(SERVICE_PATH)

    if desired_state and not is_installed:
        logging.info("[*] Hooking into OS and creating systemd service...")
        script_path = os.path.abspath(__file__)
        python_exe = sys.executable 
        current_user = os.getenv('USER') or os.getenv('SUDO_USER') or 'root'
        
        service_content = f"""[Unit]
Description=WikiSAG Offline Wikipedia AI Bridge
After=network.target ollama.service

[Service]
Type=simple
User={current_user}
WorkingDirectory={BASE_DIR}
ExecStart={python_exe} {script_path}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
        try:
            temp_path = os.path.join(BASE_DIR, 'temp_wikisag.service')
            with open(temp_path, 'w') as f:
                f.write(service_content)
            subprocess.run(['sudo', 'mv', temp_path, SERVICE_PATH], check=True)
            subprocess.run(['sudo', 'systemctl', 'daemon-reload'], check=True)
            subprocess.run(['sudo', 'systemctl', 'enable', SERVICE_NAME], check=True)
            
            if start_immediately:
                subprocess.run(['sudo', 'systemctl', 'start', SERVICE_NAME], check=True)
                logging.info("[+] Service started in background. Exiting terminal.")
                sys.exit(0)
            else:
                logging.info("[+] Service configured. It will start after the download finishes.")
        except Exception as e:
            logging.error(f"[-] Failed to install service: {e}")

    elif not desired_state and is_installed:
        logging.info("[*] Removing systemd service...")
        try:
            subprocess.run(['sudo', 'systemctl', 'stop', SERVICE_NAME], stderr=subprocess.DEVNULL)
            subprocess.run(['sudo', 'systemctl', 'disable', SERVICE_NAME], stderr=subprocess.DEVNULL)
            subprocess.run(['sudo', 'rm', '-f', SERVICE_PATH], check=True)
            subprocess.run(['sudo', 'systemctl', 'daemon-reload'], check=True)
            logging.info("[+] Service removed successfully.")
        except Exception as e:
            logging.error(f"[-] Failed to remove service: {e}")

# --- Auto-Download Logic ---
def check_disk_space(required_gb, path="."):
    total, used, free = shutil.disk_usage(path)
    free_gb = free / (1024**3)
    return free_gb >= required_gb, free_gb

def download_zim_file():
    url = "https://dumps.wikimedia.org/kiwix/zim/wikipedia/wikipedia_en_all_nopic_2026-03.zim"
    filename = "wikipedia_en_all_nopic_2026-03.zim"
    required_space_gb = 55.0 
    
    logging.info("\n[*] Checking available disk space...")
    has_space, free_gb = check_disk_space(required_space_gb, BASE_DIR)
    
    if not has_space:
        logging.error(f"[-] CRITICAL WARNING: Insufficient disk space!")
        logging.error(f"    WikiSAG requires ~{required_space_gb}GB of free space, but only {free_gb:.1f}GB is available.")
        sys.exit(1)

    logging.info(f"[+] Disk space check passed ({free_gb:.1f}GB available).")
    logging.info(f"[*] Preparing to download {filename} (~52GB).")
    
    try:
        subprocess.run(['wget', '-c', url], check=True, cwd=BASE_DIR)
        logging.info("\n[+] Download completed successfully!")
    except FileNotFoundError:
        logging.error("\n[-] ERROR: 'wget' is not installed on this system.")
        sys.exit(1)
    except subprocess.CalledProcessError:
        logging.error("\n[-] ERROR: Download was interrupted or failed. Run script again to resume.")
        sys.exit(1)

# --- Interactive Setup Wizard ---
def ask(prompt_text, default_val):
    answer = input(f"{prompt_text} [{default_val}]: ").strip()
    return answer if answer else default_val

def run_interactive_setup():
    print("\n" + "="*50)
    print(" WikiSAG Configuration Wizard")
    print("="*50 + "\n")
    print("Press Enter to accept the [default] values.\n")
    
    c_host = ask("Listen IP Address", "127.0.0.1")
    c_port = ask("Listen Port", "8000")
    
    print("\n--- Wikipedia Database ---")
    dl_ans = ask("Automatically download the recommended ~52GB 'nopic' database? (yes/no)", "yes").lower()
    
    needs_download = False
    if dl_ans in ['y', 'yes', 'true']:
        c_zim = os.path.join(BASE_DIR, "wikipedia_en_all_nopic_2026-03.zim")
        needs_download = True
    else:
        c_zim = ask("ZIM File Path", "wikipedia_en_all_nopic_2026-03.zim")
        
    print("\n--- AI Model Configuration ---")
    c_model = ask("Ollama Model Name", "gemma4:e2b-it-q8_0")
    c_url = ask("Ollama API URL", "http://localhost:11434/v1")
    c_chars = ask("Max Context Characters", "15000")
    
    print("\n--- System Service ---")
    svc_ans = ask("Run as background systemd service? (yes/no)", "yes").lower()
    c_svc = 'yes' if svc_ans in ['y', 'yes', 'true'] else 'no'

    # Lock in config
    config = configparser.ConfigParser()
    config['System'] = {'run_as_service': c_svc}
    config['Network'] = {'host': c_host, 'port': c_port}
    config['Ollama'] = {'base_url': c_url, 'model': c_model, 'max_context_chars': c_chars}
    config['Data'] = {'zim_file': c_zim}
    
    with open(CONFIG_FILE, 'w') as configfile:
        config.write(configfile)
    print(f"\n[+] Configuration saved to {CONFIG_FILE}!")

    # Step 1: Hook the service immediately while the user is still at the keyboard
    enforce_service_state(c_svc == 'yes', start_immediately=False)

    # Step 2: Run the massive download
    if needs_download:
        download_zim_file()

    # Step 3: Attempt to start the background daemon
    if c_svc == 'yes':
        logging.info("\n[*] Starting background service...")
        try:
            # Stop it first just in case it was already running during a reconfig
            subprocess.run(['sudo', 'systemctl', 'stop', SERVICE_NAME], stderr=subprocess.DEVNULL)
            subprocess.run(['sudo', 'systemctl', 'start', SERVICE_NAME], check=True)
            logging.info("[+] Service started successfully. Node is live! Exiting terminal.")
            sys.exit(0)
        except subprocess.CalledProcessError:
            # The graceful timeout catcher
            logging.warning("\n[-] Sudo prompt timed out while waiting for the download to finish.")
            logging.info(f"    Your node is fully configured. To start it, run: sudo systemctl start {SERVICE_NAME}")
            sys.exit(0)

def validate_config():
    if not os.path.exists(CONFIG_FILE):
        return False
    config = configparser.ConfigParser()
    try:
        config.read(CONFIG_FILE)
        return all(s in config for s in ['System', 'Network', 'Ollama', 'Data'])
    except configparser.Error:
        return False

# --- Boot Logic & Flag Handling ---
force_config = False
if len(sys.argv) > 1 and sys.argv[1] in ['-c', '--config']:
    force_config = True
    logging.info("\n[*] Configuration flag detected. Launching wizard...")

if not validate_config() or force_config:
    if sys.stdout.isatty():
        run_interactive_setup()
    else:
        logging.error(f"CRITICAL ERROR: Invalid or missing {CONFIG_FILE}.")
        sys.exit(1)

# Load config for execution
config = configparser.ConfigParser()
config.read(CONFIG_FILE)

RUN_AS_SERVICE = config.getboolean('System', 'run_as_service', fallback=False)
HOST = config.get('Network', 'host', fallback='127.0.0.1')
PORT = config.getint('Network', 'port', fallback=8000)
OLLAMA_URL = config.get('Ollama', 'base_url', fallback='http://localhost:11434/v1')
MODEL = config.get('Ollama', 'model', fallback='gemma4:e2b-it-q8_0')
MAX_CHARS = config.getint('Ollama', 'max_context_chars', fallback=15000)
ZIM_FILE_PATH = config.get('Data', 'zim_file', fallback='wikipedia_en_all_nopic_2026-03.zim')

if not os.path.isabs(ZIM_FILE_PATH):
    ZIM_FILE_PATH = os.path.join(BASE_DIR, ZIM_FILE_PATH)

# Ensure service state aligns with the INI file if skipped the wizard
enforce_service_state(RUN_AS_SERVICE, start_immediately=True)

client = OpenAI(base_url=OLLAMA_URL, api_key='ollama')

try:
    logging.info(f"Loading ZIM archive: {ZIM_FILE_PATH}...")
    zim = Archive(ZIM_FILE_PATH)
    searcher = Searcher(zim)
    logging.info("ZIM archive loaded successfully.")
except Exception as e:
    logging.error(f"CRITICAL ERROR: Could not load ZIM file. Check {CONFIG_FILE}. Details: {e}")
    sys.exit(1)

# --- Core RAG Logic ---
def search_offline_wikipedia(user_query, top_k=1):
    query = Query().set_query(user_query)
    search_results = searcher.search(query)
    
    context = ""
    for result in list(search_results.getResults(0, top_k)):
        path = result if isinstance(result, str) else result.path
        entry = zim.get_entry_by_path(path)
        html_content = bytes(entry.get_item().content).decode("UTF-8")
        markdown_text = markdownify(html_content, strip=['a', 'img', 'script', 'style'])
        clean_text = re.sub(r'\n\s*\n', '\n\n', markdown_text).strip()
        context += f"Title: {entry.title}\n{clean_text}\n\n---\n\n"
        
    return context if context else "No relevant offline Wikipedia articles found."

def query_ai(user_question):
    context = search_offline_wikipedia(user_question, top_k=1)
    
    if len(context) > MAX_CHARS:
        context = context[:MAX_CHARS] + "... [Article Truncated]"
        
    prompt = f"""You are a helpful offline survival assistant. 
Use the following Wikipedia article text to answer the user's question. 
Be concise, direct, and factual. Limit your response to 2 short paragraphs or bullet points.
If the answer is not in the text, say you don't know based on the provided context.

Context:
{context}

Question: {user_question}
"""

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
    )
    return response.choices[0].message.content

# --- Packet Network Server ---
def handle_client(conn, addr):
    logging.info(f"[{addr[0]}:{addr[1]}] Connection established.")
    try:
        conn.sendall(b"*** Offline Wiki Assistant ***\r\nType your question:\r\n> ")
        data = conn.recv(1024).decode('utf-8').strip()
        
        if data and not shutdown_event.is_set():
            logging.info(f"[{addr[0]}:{addr[1]}] User asked: {data}")
            conn.sendall(b"Searching index and querying AI... Please wait.\r\n")
            
            answer = query_ai(data)
            
            if not shutdown_event.is_set():
                formatted_answer = answer.replace('\n', '\r\n') + "\r\n"
                conn.sendall(formatted_answer.encode('utf-8'))
            else:
                conn.sendall(b"\r\n[Server is shutting down. Transmission aborted.]\r\n")
                
    except Exception as e:
        logging.error(f"[{addr[0]}:{addr[1]}] Error handling connection: {e}", exc_info=True)
    finally:
        conn.close()
        logging.info(f"[{addr[0]}:{addr[1]}] Connection closed.")

def start_packet_server():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) 
    server_socket.bind((HOST, PORT))
    server_socket.settimeout(1.0)
    server_socket.listen(5)
    
    logging.info(f"WikiSAG Server listening on {HOST}:{PORT}...")
    
    while not shutdown_event.is_set():
        try:
            conn, addr = server_socket.accept()
            client_thread = threading.Thread(target=handle_client, args=(conn, addr))
            client_thread.start()
            active_threads.append(client_thread)
            active_threads[:] = [t for t in active_threads if t.is_alive()]
            
        except socket.timeout:
            continue
        except Exception as e:
            if not shutdown_event.is_set():
                logging.error(f"Socket error: {e}")
    
    logging.info("\n[*] Stop signal received. Closing main server socket...")
    server_socket.close()
    
    if active_threads:
        logging.info(f"[*] Waiting for {len(active_threads)} active query thread(s) to finish...")
        for t in active_threads:
            t.join(timeout=5.0)
            
    logging.info("[+] Shutdown complete. 73!")
    sys.exit(0)

if __name__ == "__main__":
    start_packet_server()

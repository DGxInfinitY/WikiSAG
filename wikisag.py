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
from libzim.reader import Archive
from libzim.search import Query, Searcher
from markdownify import markdownify
from openai import OpenAI

# --- Local Appliance Paths ---
CONFIG_FILE = 'wikisag.ini'
DATA_DIR = '.'
SERVICE_NAME = 'wikisag.service'
SERVICE_PATH = f'/etc/systemd/system/{SERVICE_NAME}'

# Global shutdown flag
shutdown_event = threading.Event()
active_threads = []

# --- Graceful Shutdown Handler ---
def signal_handler(sig, frame):
    print("\n\n[!] Termination signal detected (Ctrl-C). Initiating graceful shutdown...")
    shutdown_event.set()

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# --- Auto-Download Logic ---
def check_disk_space(required_gb, path="."):
    total, used, free = shutil.disk_usage(path)
    free_gb = free / (1024**3)
    return free_gb >= required_gb, free_gb

def download_zim_file():
    """Uses wget to safely download the ZIM file at the very end of setup."""
    url = "https://dumps.wikimedia.org/kiwix/zim/wikipedia/wikipedia_en_all_nopic_2026-03.zim"
    filename = "wikipedia_en_all_nopic_2026-03.zim"
    required_space_gb = 55.0 
    
    print(f"\n[*] Checking available disk space in current directory...")
    has_space, free_gb = check_disk_space(required_space_gb)
    
    if not has_space:
        print(f"\n[-] CRITICAL WARNING: Insufficient disk space!")
        print(f"    WikiSAG requires ~{required_space_gb}GB of free space, but only {free_gb:.1f}GB is available.")
        print(f"    The automatic download has been ABORTED to protect your operating system.")
        print(f"\n    CAVEAT: Your config is saved, but WikiSAG will fail to start until you")
        print(f"    manually free up space and place the correct .zim file in this directory.")
        sys.exit(1)

    print(f"[+] Disk space check passed ({free_gb:.1f}GB available).")
    print(f"[*] Preparing to download {filename} (~52GB).")
    print("    This will take a significant amount of time depending on your network speed.")
    print("    NOTE: If the download fails or you hit Ctrl-C, just run 'wikisag' again.")
    print("    It will automatically resume exactly where it left off!\n")
    
    try:
        subprocess.run(['wget', '-c', url], check=True)
        print("\n[+] Download completed successfully!")
    except FileNotFoundError:
        print("\n[-] ERROR: 'wget' is not installed on this system.")
        sys.exit(1)
    except subprocess.CalledProcessError:
        print("\n[-] ERROR: Download was interrupted or failed.")
        print("    Please run the script again to resume.")
        sys.exit(1)

# --- Interactive Setup Wizard ---
def ask(prompt_text, default_val):
    answer = input(f"{prompt_text} [{default_val}]: ").strip()
    return answer if answer else default_val

def run_interactive_setup():
    print("\n" + "="*50)
    print(" Welcome to WikiSAG First-Run Setup")
    print("="*50 + "\n")
    print("Press Enter to accept the [default] values.\n")
    
    c_host = ask("Listen IP Address", "127.0.0.1")
    c_port = ask("Listen Port", "8000")
    
    print("\n--- Wikipedia Database ---")
    print("WikiSAG needs a .zim file to function.")
    dl_ans = ask("Do you want to automatically download the recommended ~52GB 'nopic' database? (yes/no)", "yes").lower()
    
    needs_download = False
    if dl_ans in ['y', 'yes', 'true']:
        c_zim = os.path.join(DATA_DIR, "wikipedia_en_all_nopic_2026-03.zim")
        needs_download = True
    else:
        c_zim = ask("ZIM File Path", "wikipedia_en_all_nopic_2026-03.zim")
        
    print("\n--- AI Model Configuration ---")
    c_model = ask("Ollama Model Name", "gemma4:e2b-it-q8_0")
    c_url = ask("Ollama API URL", "http://localhost:11434/v1")
    c_chars = ask("Max Context Characters", "15000")
    
    print("\n--- System Service ---")
    print("Do you want WikiSAG to run silently in the background as a systemd service?")
    svc_ans = ask("Run as system service? (yes/no)", "yes").lower()
    c_svc = 'yes' if svc_ans in ['y', 'yes', 'true'] else 'no'

    # 1. Lock in the configuration FIRST
    config = configparser.ConfigParser()
    config['System'] = {'run_as_service': c_svc}
    config['Network'] = {'host': c_host, 'port': c_port}
    config['Ollama'] = {'base_url': c_url, 'model': c_model, 'max_context_chars': c_chars}
    config['Data'] = {'zim_file': c_zim}
    
    with open(CONFIG_FILE, 'w') as configfile:
        config.write(configfile)
    print(f"\n[+] Configuration saved to {CONFIG_FILE}!")

    # 2. Run the massive download LAST
    if needs_download:
        print("\n--- Starting Wikipedia Database Download ---")
        download_zim_file()

# --- Configuration Validation ---
def validate_config():
    if not os.path.exists(CONFIG_FILE):
        return False
    config = configparser.ConfigParser()
    try:
        config.read(CONFIG_FILE)
        required_sections = ['System', 'Network', 'Ollama', 'Data']
        for section in required_sections:
            if section not in config:
                return False
        return True
    except configparser.Error:
        return False

# --- Service Management (Desired State Sync) ---
def sync_service_state(desired_state):
    if not sys.stdout.isatty():
        return

    is_installed = os.path.exists(SERVICE_PATH)

    if desired_state and not is_installed:
        print("\n[*] Installing systemd service...")
        script_path = os.path.abspath(__file__)
        working_dir = os.path.dirname(script_path)
        python_exe = sys.executable 
        current_user = os.getenv('USER') or os.getenv('SUDO_USER') or 'root'

        service_content = f"""[Unit]
Description=WikiSAG Offline Wikipedia AI Bridge
After=network.target ollama.service

[Service]
Type=simple
User={current_user}
WorkingDirectory={working_dir}
ExecStart={python_exe} {script_path}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
        try:
            temp_path = os.path.join(working_dir, 'temp_wikisag.service')
            with open(temp_path, 'w') as f:
                f.write(service_content)
            
            subprocess.run(['sudo', 'mv', temp_path, SERVICE_PATH], check=True)
            subprocess.run(['sudo', 'systemctl', 'daemon-reload'], check=True)
            subprocess.run(['sudo', 'systemctl', 'enable', SERVICE_NAME], check=True)
            subprocess.run(['sudo', 'systemctl', 'start', SERVICE_NAME], check=True)
            print("[+] Service installed. Running in background. Exiting terminal.")
            sys.exit(0)
        except Exception as e:
            print(f"[-] Failed to install service: {e}")

    elif not desired_state and is_installed:
        print("\n[*] Removing systemd service...")
        try:
            subprocess.run(['sudo', 'systemctl', 'stop', SERVICE_NAME], check=True)
            subprocess.run(['sudo', 'systemctl', 'disable', SERVICE_NAME], check=True)
            subprocess.run(['sudo', 'rm', SERVICE_PATH], check=True)
            subprocess.run(['sudo', 'systemctl', 'daemon-reload'], check=True)
            print("[+] Service removed successfully.")
        except Exception as e:
            print(f"[-] Failed to remove service: {e}")

# --- Boot Logic ---
if not validate_config():
    if sys.stdout.isatty():
        run_interactive_setup()
    else:
        print(f"CRITICAL ERROR: Invalid or missing {CONFIG_FILE}.")
        sys.exit(1)

config = configparser.ConfigParser()
config.read(CONFIG_FILE)

RUN_AS_SERVICE = config.getboolean('System', 'run_as_service', fallback=False)
HOST = config.get('Network', 'host', fallback='127.0.0.1')
PORT = config.getint('Network', 'port', fallback=8000)
OLLAMA_URL = config.get('Ollama', 'base_url', fallback='http://localhost:11434/v1')
MODEL = config.get('Ollama', 'model', fallback='gemma4:e2b-it-q8_0')
MAX_CHARS = config.getint('Ollama', 'max_context_chars', fallback=15000)
ZIM_FILE_PATH = config.get('Data', 'zim_file', fallback='wikipedia_en_all_nopic_2026-03.zim')

sync_service_state(RUN_AS_SERVICE)

# --- Initialize Global Clients ---
client = OpenAI(base_url=OLLAMA_URL, api_key='ollama')

try:
    print(f"Loading ZIM archive: {ZIM_FILE_PATH}...")
    zim = Archive(ZIM_FILE_PATH)
    searcher = Searcher(zim)
    print("ZIM archive loaded successfully.")
except Exception as e:
    print(f"CRITICAL ERROR: Could not load ZIM file. Check {CONFIG_FILE}. Details: {e}")
    sys.exit(1)

# --- Core RAG Logic ---
def search_offline_wikipedia(user_query, top_k=1):
    query = Query().set_query(user_query)
    search_results = searcher.search(query)
    
    context = ""
    for result in list(search_results.getResults(0, top_k)):
        entry = zim.get_entry_by_path(result.path)
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
    print(f"[{addr}] Connection established.")
    try:
        conn.sendall(b"*** WikiSAG Offline Assistant ***\r\nType your question:\r\n> ")
        data = conn.recv(1024).decode('utf-8').strip()
        
        if data and not shutdown_event.is_set():
            print(f"[{addr}] User asked: {data}")
            conn.sendall(b"Searching index and querying AI... Please wait.\r\n")
            
            answer = query_ai(data)
            
            if not shutdown_event.is_set():
                formatted_answer = answer.replace('\n', '\r\n') + "\r\n"
                conn.sendall(formatted_answer.encode('utf-8'))
            else:
                conn.sendall(b"\r\n[Server is shutting down. Transmission aborted.]\r\n")
                
    except Exception as e:
        print(f"[{addr}] Error handling connection: {e}")
    finally:
        conn.close()
        print(f"[{addr}] Connection closed.")

def start_packet_server():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) 
    server_socket.bind((HOST, PORT))
    
    server_socket.settimeout(1.0)
    server_socket.listen(5)
    
    print(f"WikiSAG Server listening on {HOST}:{PORT}...")
    
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
                print(f"Socket error: {e}")
    
    # --- Shutdown Sequence ---
    print("\n[*] Stop signal received. Closing main server socket...")
    server_socket.close()
    
    if active_threads:
        print(f"[*] Waiting for {len(active_threads)} active query thread(s) to finish dropping connections...")
        for t in active_threads:
            t.join(timeout=5.0)
            
    print("[+] Shutdown complete. 73!")
    sys.exit(0)

if __name__ == "__main__":
    start_packet_server()

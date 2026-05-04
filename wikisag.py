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
import urllib.request
import json
from urllib.parse import urlparse
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

# --- Global Default Prompts (Formatted for INI multi-line support) ---
DEFAULT_ROUTER_PROMPT = (
    "You are a Wikipedia Title Extractor.\n"
    " Convert the user's question into 1 or 2 BROAD NOUNS that would match the exact title of a Wikipedia article.\n"
    " DO NOT use verbs, questions, or highly specific long phrases. Think of the broad encyclopedia category.\n"
    " Example 1:\n"
    " User: What torque specs are needed for a jeep wrangler jl's wheels?\n"
    " Output: Jeep Wrangler (JL)\n"
    " Example 2:\n"
    " User: When do you typically plant corn in Utah?\n"
    " Output: Agriculture in Utah, Maize\n"
    " Example 3:\n"
    " User: How to treat a burn?\n"
    " Output: Burn, First aid"
)

# UPDATED: Hardened the gag order to force internal memory usage when the wiki fails
DEFAULT_PRIMARY_PROMPT = (
    "You are a specialized SHTF Survival Assistant operating over a low-bandwidth Emergency Packet Radio link.\n"
    " GOAL: Provide the exact, correct answer immediately, resembling a highly accurate search engine \"Featured Snippet\".\n"
    " CONSTRAINTS:\n"
    " - SYNTHESIZE AND ENHANCE: Read the provided Wikipedia Context. If it contains the answer, use it. If the text DOES NOT contain the specific answer, you MUST use your own internal expert knowledge to answer the user's question directly.\n"
    " - CRITICAL GAG ORDER: DO NOT mention the provided context. NEVER say \"The provided text does not contain...\" or \"According to the text\". If the text is useless, just give the answer from your own memory without apologizing.\n"
    " - ACTIONABLE: If the user asks \"How to...\" or asks for specs, provide specific, actionable data.\n"
    " - BLUF (Bottom Line Up Front): Start IMMEDIATELY with the answer. No apologies, no conversational filler.\n"
    " - Be EXTREMELY concise. Use short bullet points."
)

# --- Graceful Shutdown Handler ---
def signal_handler(sig, frame):
    logging.info("\n[!] Termination signal detected. Initiating graceful shutdown...")
    shutdown_event.set()

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# --- Service Management ---
def enforce_service_state(desired_state, start_immediately=True):
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

# --- Ollama API Discovery ---
def fetch_ollama_models(api_url):
    try:
        parsed = urlparse(api_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        tags_url = f"{base_url}/api/tags"
        
        req = urllib.request.Request(tags_url, method="GET")
        with urllib.request.urlopen(req, timeout=2.0) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                return [m['name'] for m in data.get('models', [])]
    except Exception:
        return []

# --- Interactive Setup Wizard ---
def ask(prompt_text, default_val):
    answer = input(f"{prompt_text} [{default_val}]: ").strip()
    return answer if answer else default_val

def select_model_from_list(models, prompt_text, default_model):
    if not models:
        return ask(f"Enter {prompt_text} manually", default_model)
        
    print(f"\nSelect {prompt_text}:")
    for i, m in enumerate(models, 1):
        print(f"  {i}) {m}")
    print(f"  0) Enter custom model name manually")
    
    while True:
        choice = input(f"\nSelect a model (0-{len(models)}) [1]: ").strip()
        if not choice:
            return models[0]
        elif choice.isdigit():
            idx = int(choice)
            if idx == 0:
                return ask(f"Enter custom {prompt_text}", default_model)
            elif 1 <= idx <= len(models):
                return models[idx-1]
        print("Invalid selection. Please enter a valid number.")

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
        
    c_top_k = ask("Max Search Evaluations (Max articles to dig through before timing out)", "8")
        
    print("\n--- AI Model Configuration ---")
    c_url = ask("Ollama API URL", "http://localhost:11434/v1")
    
    print("[*] Detecting installed Ollama models...")
    models = fetch_ollama_models(c_url)
    
    c_primary_model = select_model_from_list(models, "Primary Model (The Heavy Lifter)", "gemma4:e2b-it-q8_0")
    c_router_model = select_model_from_list(models, "Router Model (The Speedy Extractor)", "qwen2.5:1.5b")
        
    c_chars = ask("Max Context Characters", "15000")
    
    print("\n--- System Service ---")
    svc_ans = ask("Run as background systemd service? (yes/no)", "yes").lower()
    c_svc = 'yes' if svc_ans in ['y', 'yes', 'true'] else 'no'

    config = configparser.ConfigParser()
    config['System'] = {'run_as_service': c_svc}
    config['Network'] = {'host': c_host, 'port': c_port}
    config['Data'] = {'zim_file': c_zim, 'top_k': c_top_k}
    config['Ollama'] = {
        'base_url': c_url, 
        'primary_model': c_primary_model, 
        'router_model': c_router_model,
        'max_context_chars': c_chars
    }
    config['Prompts'] = {
        'router_system_prompt': DEFAULT_ROUTER_PROMPT,
        'primary_system_prompt': DEFAULT_PRIMARY_PROMPT
    }
    
    with open(CONFIG_FILE, 'w') as configfile:
        config.write(configfile)
        
    print(f"\n[+] Configuration saved to {CONFIG_FILE}!")
    print(f"[*] Note: Advanced AI behavior prompts have been loaded with SHTF defaults.")
    print(f"    You can fine-tune the AI's personality anytime by editing {CONFIG_FILE}")

    enforce_service_state(c_svc == 'yes', start_immediately=False)

    if needs_download:
        download_zim_file()

    if c_svc == 'yes':
        logging.info("\n[*] Starting background service...")
        try:
            subprocess.run(['sudo', 'systemctl', 'stop', SERVICE_NAME], stderr=subprocess.DEVNULL)
            subprocess.run(['sudo', 'systemctl', 'start', SERVICE_NAME], check=True)
            logging.info("[+] Service started successfully. Node is live! Exiting terminal.")
            sys.exit(0)
        except subprocess.CalledProcessError:
            logging.warning("\n[-] Sudo prompt timed out while waiting for download.")
            logging.info(f"    Run: sudo systemctl start {SERVICE_NAME}")
            sys.exit(0)

def validate_config():
    if not os.path.exists(CONFIG_FILE):
        return False
    config = configparser.ConfigParser()
    try:
        config.read(CONFIG_FILE)
        return all(s in config for s in ['System', 'Network', 'Ollama', 'Data', 'Prompts'])
    except configparser.Error:
        return False

# --- Boot Logic & Flag Handling ---
force_config = False
if len(sys.argv) > 1 and sys.argv[1] in ['-c', '--config']:
    force_config = True

if not validate_config() or force_config:
    if sys.stdout.isatty():
        run_interactive_setup()
    else:
        sys.exit(1)

# Load global configurations
config = configparser.ConfigParser()
config.read(CONFIG_FILE)

RUN_AS_SERVICE = config.getboolean('System', 'run_as_service', fallback=False)
HOST = config.get('Network', 'host', fallback='127.0.0.1')
PORT = config.getint('Network', 'port', fallback=8000)

ZIM_FILE_PATH = config.get('Data', 'zim_file', fallback='wikipedia_en_all_nopic_2026-03.zim')
TOP_K = config.getint('Data', 'top_k', fallback=8)
if not os.path.isabs(ZIM_FILE_PATH):
    ZIM_FILE_PATH = os.path.join(BASE_DIR, ZIM_FILE_PATH)

OLLAMA_URL = config.get('Ollama', 'base_url', fallback='http://localhost:11434/v1')
PRIMARY_MODEL = config.get('Ollama', 'primary_model', fallback='gemma4:e2b-it-q8_0')
ROUTER_MODEL = config.get('Ollama', 'router_model', fallback='qwen2.5:1.5b')
MAX_CHARS = config.getint('Ollama', 'max_context_chars', fallback=15000)

ROUTER_PROMPT = config.get('Prompts', 'router_system_prompt', fallback=DEFAULT_ROUTER_PROMPT)
PRIMARY_PROMPT = config.get('Prompts', 'primary_system_prompt', fallback=DEFAULT_PRIMARY_PROMPT)

enforce_service_state(RUN_AS_SERVICE, start_immediately=True)
client = OpenAI(base_url=OLLAMA_URL, api_key='ollama')

try:
    logging.info(f"Loading ZIM archive: {ZIM_FILE_PATH}...")
    zim = Archive(ZIM_FILE_PATH)
    searcher = Searcher(zim)
    logging.info("ZIM archive loaded successfully.")
except Exception as e:
    logging.error(f"CRITICAL ERROR: Could not load ZIM file: {e}")
    sys.exit(1)

# --- Agentic RAG Logic ---
def generate_ai_search_terms(user_question):
    prompt = f"{ROUTER_PROMPT}\n\nQuestion: {user_question}"
    try:
        response = client.chat.completions.create(
            model=ROUTER_MODEL, 
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0, 
            max_tokens=15 
        )
        keywords = response.choices[0].message.content.strip()
        
        if not keywords:
            logging.warning(f"[*] Micro-Model choked. Falling back to raw query: '{user_question}'")
            return user_question
            
        logging.info(f"[*] Micro-Model translated '{user_question}' -> '{keywords}'")
        return keywords
    except Exception as e:
        logging.error(f"AI Keyword Error: {e}")
        return user_question

def grade_article_relevance(user_question, ai_keywords, article_title, article_text):
    # FAST PATH: If the extracted keywords match the article title, bypass the AI to save 45 seconds!
    title_clean = article_title.lower().strip()
    keywords_clean = ai_keywords.lower().strip()
    
    if title_clean in keywords_clean or keywords_clean in title_clean:
        logging.info(f"[*] FAST PATH: Title match detected! Auto-accepting '{article_title}' instantly.")
        return True

    # Reduced from 8000 back to 3000 to prevent the micro-model from hanging on huge context
    snippet = article_text[:4000] 
    
    prompt = f"""You are a forgiving relevance judge. 
Look at the Article Title and the Snippet. Does this article cover the CORE SUBJECT of the user's question?
We just need the broad background article so the primary AI can read it later. 
Answer ONLY with the word "YES" or "NO". Do not explain.

User Question: {user_question}
Article Title: {article_title}
Snippet: {snippet}"""

    try:
        response = client.chat.completions.create(
            model=ROUTER_MODEL, 
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0, 
            max_tokens=2 
        )
        judgment = response.choices[0].message.content.strip().upper()
        return "YES" in judgment
    except Exception as e:
        logging.warning(f"[*] Relevance Judge skipped due to error: {e}")
        return True

def search_offline_wikipedia(ai_keywords, user_question, target_accepted=2, max_evaluations=8):
    query = Query().set_query(ai_keywords)
    search_results = searcher.search(query)
    
    context_parts = []
    eval_count = 0
    results = list(search_results.getResults(0, max_evaluations))
    
    for result in results:
        if len(context_parts) >= target_accepted:
            logging.info(f"[+] Target of {target_accepted} relevant articles met. Stopping search early.")
            break
            
        eval_count += 1
        path = result if isinstance(result, str) else result.path
        try:
            entry = zim.get_entry_by_path(path)
            html_content = bytes(entry.get_item().content).decode("UTF-8")
            markdown_text = markdownify(html_content, strip=['a', 'img', 'script', 'style'])
            clean_text = re.sub(r'\n\s*\n', '\n\n', markdown_text).strip()
            
            logging.info(f"[*] Judging relevance of article {eval_count}/{max_evaluations}: '{entry.title}'...")
            
            # Pass ai_keywords to the judge to enable the Fast-Path
            is_relevant = grade_article_relevance(user_question, ai_keywords, entry.title, clean_text)
            
            if is_relevant:
                logging.info(f"[+] Article '{entry.title}' ACCEPTED.")
                context_parts.append(f"ARTICLE TITLE: {entry.title}\n{clean_text}")
            else:
                logging.info(f"[-] Article '{entry.title}' REJECTED.")
                
        except Exception as e:
            logging.error(f"Error reading article: {e}")
            continue
            
    return "\n\n---\n\n".join(context_parts) if context_parts else ""

def query_ai(user_question):
    optimized_keywords = generate_ai_search_terms(user_question)
    logging.info(f"[*] Searching ZIM archive for: '{optimized_keywords}'")
    context = search_offline_wikipedia(optimized_keywords, user_question, target_accepted=2, max_evaluations=TOP_K)
    
    if not context:
        logging.info("[*] All retrieved articles were rejected. Forcing Primary Model to use internal memory.")
    elif len(context) > MAX_CHARS:
        context = context[:MAX_CHARS] + "... [Context Truncated]"
        
    prompt = f"{PRIMARY_PROMPT}\n\nCONTEXT FROM OFFLINE WIKIPEDIA:\n{context}\n\nUSER QUESTION: {user_question}"
    
    logging.info(f"[*] Compiling data and querying Primary Model ({PRIMARY_MODEL})...")
    try:
        response = client.chat.completions.create(
            model=PRIMARY_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        logging.info("[+] Primary Model successfully generated an answer.")
        return response.choices[0].message.content
    except Exception as e:
        logging.error(f"AI Query Error: {e}")
        return "Error: Could not reach the local AI model."

# --- Packet Network Server ---
def handle_client(conn, addr):
    logging.info(f"[{addr[0]}:{addr[1]}] Connection established.")
    try:
        greeting = (
            "\r\n"
            "*** Offline Wiki Assistant ***\r\n"
            "Query the 52GB Wikipedia archive via local AI.\r\n"
            "----------------------------------------------\r\n"
            "INSTRUCTIONS:\r\n"
            " - Type a specific question (e.g., 'How to treat a burn?')\r\n"
            " - Please wait; processing takes 10-30 seconds.\r\n"
            " - To return to the node, type: EXIT, QUIT, or BYE.\r\n"
            "----------------------------------------------\r\n"
            "Type your question:\r\n> "
        )
        conn.sendall(greeting.encode('utf-8'))
        
        while not shutdown_event.is_set():
            raw_bytes = conn.recv(1024)
            
            if not raw_bytes:
                break
                
            raw_data = raw_bytes.decode('utf-8').strip()
            
            if not raw_data:
                conn.sendall(b"> ")
                continue
            
            if raw_data.lower() in ['exit', 'quit', 'bye', 'disconnect'] or raw_data.startswith('***'):
                logging.info(f"[{addr[0]}:{addr[1]}] User or node initiated disconnect.")
                try:
                    conn.sendall(b"73! Returning to node...\r\n")
                except OSError:
                    pass 
                break
                
            logging.info(f"[{addr[0]}:{addr[1]}] User asked: {raw_data}")
            
            try:
                conn.sendall(b"Searching index and querying AI... Please wait.\r\n")
            except OSError:
                logging.info(f"[{addr[0]}:{addr[1]}] Client dropped off the air. Aborting query.")
                break
            
            # --- Heartbeat Keepalive Thread ---
            ai_done = threading.Event()
            def send_keepalive():
                while not ai_done.wait(3.0):
                    try:
                        conn.sendall(b".")
                    except Exception:
                        break

            keepalive_thread = threading.Thread(target=send_keepalive)
            keepalive_thread.start()
            
            try:
                answer = query_ai(raw_data)
            finally:
                ai_done.set()
                keepalive_thread.join()
            
            if not shutdown_event.is_set():
                formatted_answer = "\r\n\r\n" + answer.replace('\n', '\r\n') + "\r\n\r\n> "
                try:
                    conn.sendall(formatted_answer.encode('utf-8'))
                except OSError as e:
                    logging.info(f"[{addr[0]}:{addr[1]}] Socket closed before answer could be sent ({e}).")
                    break
            else:
                try:
                    conn.sendall(b"\r\n[Server is shutting down. Transmission aborted.]\r\n")
                except OSError:
                    pass
                break
                
    except ConnectionResetError:
        logging.info(f"[{addr[0]}:{addr[1]}] Client disconnected unexpectedly (Connection reset by peer).")
    except BrokenPipeError:
        logging.info(f"[{addr[0]}:{addr[1]}] Client disconnected unexpectedly (Broken pipe).")
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

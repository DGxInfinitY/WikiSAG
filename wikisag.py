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
    total, used, free = shutil.

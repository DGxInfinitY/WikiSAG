# 📡 WikiSAG 

**Search-Augmented Generation Bridge for Offline Wikipedia & Packet Radio**

WikiSAG is a lightweight, fully offline Artificial Intelligence bridge designed for SHTF (Shit Hits The Fan) scenarios and amateur radio networks. It allows node operators to provide users with an interactive, AI-powered Wikipedia assistant over low-bandwidth RF links (like 1200 baud VHF or HF packet radio) using tools like **linBPQ**.

By utilizing a highly compressed `.zim` archive and a local LLM via Ollama, WikiSAG acts as a local TCP socket server, accepting questions, retrieving relevant encyclopedia articles, and summarizing the answers concisely for transmission over the airwaves—all with zero internet connection.

---

## ✨ Key Features

* **100% Offline Capable:** Runs entirely on your local hardware. No API keys, no cloud servers, no internet required after the initial setup.
* **Packet Radio Optimized:** Built specifically to integrate with BBS systems and node software like **linBPQ**. It outputs brutally concise, text-only answers suited for slow RF links.
* **Smart RAG Architecture:** Does not waste time or space vectorizing data. It queries the lightning-fast internal index of a Kiwix `.zim` file, strips the HTML, and feeds the raw text to a local AI model for summarization.
* **Multi-User Threading:** Handles multiple simultaneous packet users dynamically.
* **Appliance-Style Deployment:** Installs neatly into your home directory, avoiding system-wide clutter, making node backups as simple as zipping a folder.

---

## 🏗️ Architecture & Flow

WikiSAG operates as a background daemon (systemd) that listens on a local TCP port. 

1. **The User:** Connects to your linBPQ node over RF and types an application command (e.g., `WIKI`).
2. **The Node:** linBPQ routes the connection to WikiSAG's local TCP socket (default: `127.0.0.1:8000`).
3. **The Search:** The user asks a question. WikiSAG searches the ~52GB offline Wikipedia `.zim` archive and extracts the most relevant article.
4. **The Brain:** The article text and the user's question are sent to a local LLM (like `gemma4:e2b-it-q8_0`) via Ollama.
5. **The Transmission:** The AI generates a concise answer, which WikiSAG formats and spoons back over the TCP socket for linBPQ to broadcast over the radio.

---

## ⚙️ Prerequisites

Before installing WikiSAG, your server must have:
* **Linux OS:** Ubuntu, Debian, or Armbian recommended.
* **Ollama:** Installed and running locally. 
* **An AI Model:** Pull a fast, capable model into Ollama (e.g., `ollama run gemma4:e2b-it-q8_0` or `llama3`).
* **Storage Space:** At least **55GB of free disk space** for the `nopic` Wikipedia archive.

---

## 🚀 Installation

WikiSAG is designed to be installed as a "Node Appliance" right next to your existing packet radio software.

Run this single command to download the installer, build the isolated Python environment, and set up the global command:

```bash
curl -sSL [https://raw.githubusercontent.com/DGxInfinitY/WikiSAG/master/install.sh](https://raw.githubusercontent.com/DGxInfinitY/WikiSAG/master/install.sh) | bash
```

*(Note: The installer will ask for your `sudo` password strictly to install core OS dependencies like `python3-venv` and `wget`.)*

### First-Run Setup
Once installed, run the interactive setup wizard from anywhere in your terminal:

```bash
wikisag
```

The wizard will:
1. Allow you to customize your network ports and Ollama connection.
2. Hook into the OS to create a background `systemd` service.
3. Automatically download the massive ~52GB Wikipedia database directly from Kiwix (with resume support).
4. Seamlessly start the background daemon once the download finishes.

---

## 📂 Directory Structure

WikiSAG adheres to a localized structure to make node backups painless. By default, everything lives in:

```text
~/wikisag/
├── wikisag.py                  # Core application logic
├── wikisag.ini                 # User configurations (auto-generated)
├── wikisag.log                 # Rolling log file (capped at 5MB)
├── wikipedia_en_nopic.zim      # The 52GB offline database
├── venv/                       # Isolated Python environment
```

The installer also places a wrapper script in `~/.local/bin/wikisag` so you can launch the program globally.

---

## 📻 Integrating with linBPQ

To make WikiSAG available to your RF users, add an `APPLICATION` block to your `bpq32.cfg` file. 

Map the application directly to the TCP port you defined during the WikiSAG setup (default is `8000`). Replace `C 1` with whichever BPQ port number your Telnet driver is running on:

```ini
; TELNET tells BPQ to bridge the raw TCP socket without AX.25 formatting
APPLICATION 1,WIKI,C 1 TELNET 127.0.0.1 8000 S
```

Alternatively, you can test the system locally without a radio by simply telnetting into the port:
```bash
telnet 127.0.0.1 8000
```

---

## 🛠️ Configuration & Management

If you ever need to change your AI model, update the listening port, or toggle the background service, you don't need to hunt down config files. Just run the configuration flag:

```bash
wikisag -c
```
This will instantly launch the setup wizard, allow you to update your parameters, automatically adjust your `systemd` hooks, and restart the node.

### Advanced/Manual Configuration
If you prefer to edit configurations manually, you can modify the `wikisag.ini` file located in your installation directory:

```ini
[Network]
host = 127.0.0.1
port = 8000

[Ollama]
base_url = http://localhost:11434/v1
model = gemma4:e2b-it-q8_0
max_context_chars = 15000

[Data]
zim_file = /home/username/wikisag/wikipedia_en_all_nopic_2026-03.zim

[System]
run_as_service = yes
```
*(If you modify `run_as_service` manually, simply run `wikisag` once in the terminal to allow the script to automatically install or remove the OS background service based on your changes).*

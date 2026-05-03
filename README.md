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

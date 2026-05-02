#!/bin/bash
# WikiSAG Home Directory Installer
set -e

echo "=================================================="
echo " Installing WikiSAG (Search-Augmented Generation) "
echo "=================================================="
echo ""
echo "**************************************************"
echo " You will be prompted to select an installation   "
echo " directory. By default, the installer will install"
echo " to your home directory.                          "
echo "**************************************************"
echo ""

# --- Interactive Directory Prompt (Fixed for curl | bash) ---
echo -n "Enter installation directory [$HOME/wikisag]: "
read USER_INPUT < /dev/tty

if [ -z "$USER_INPUT" ]; then
    INSTALL_DIR="$HOME/wikisag"
else
    INSTALL_DIR="${USER_INPUT/#\~/$HOME}"
fi

echo ""
echo "[*] Target directory set to: $INSTALL_DIR"
echo ""

# 1. Install System Dependencies (Requires sudo)
echo "[*] Installing OS dependencies (you may be prompted for your password)..."
sudo apt-get update -yqq
sudo apt-get install -yqq python3 python3-venv python3-pip wget

# 2. Define the Target Directory
echo "[*] Building directory structure at $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# 3. Download the Python Application and Uninstaller
echo "[*] Downloading WikiSAG core and utilities..."
wget -O wikisag.py "https://raw.githubusercontent.com/DGxInfinitY/WikiSAG/master/wikisag.py"
wget -O uninstall.sh "https://raw.githubusercontent.com/DGxInfinitY/WikiSAG/master/uninstall.sh"
chmod +x uninstall.sh

# 4. Build the Isolated Python Environment
echo "[*] Creating Python Virtual Environment..."
python3 -m venv venv
./venv/bin/pip install --upgrade pip -q
./venv/bin/pip install libzim markdownify openai -q

# 5. Create the Local Wrapper Command
echo "[*] Creating user-local 'wikisag' command..."
mkdir -p "$HOME/.local/bin"

cat << EOF > "$HOME/.local/bin/wikisag"
#!/bin/bash
# Global wrapper for WikiSAG
cd "$INSTALL_DIR"
./venv/bin/python3 wikisag.py "\$@"
EOF

chmod +x "$HOME/.local/bin/wikisag"

# 6. Ensure .local/bin is in the PATH
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo "[!] Adding ~/.local/bin to your PATH..."
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
    export PATH="$HOME/.local/bin:$PATH"
fi

echo ""
echo "========================================"
echo "[+] WikiSAG has been successfully installed in $INSTALL_DIR!"
echo "========================================"
echo ""
echo "To begin the interactive setup wizard and download the Wikipedia database, run:"
echo "  wikisag"
echo ""
echo "(Note: If the command isn't found immediately, run 'source ~/.bashrc' or close and reopen your terminal)."
echo ""

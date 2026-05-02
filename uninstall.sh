#!/bin/bash
# WikiSAG Uninstaller

echo "========================================"
echo " Uninstalling WikiSAG "
echo "========================================"
echo ""

# Ask for confirmation
echo -n "Are you sure you want to completely remove WikiSAG and all its data? (y/N): "
read CONFIRM < /dev/tty

if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo "Uninstallation aborted."
    exit 0
fi

# 1. Stop and remove the systemd service
SERVICE_NAME="wikisag.service"
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"

if [ -f "$SERVICE_PATH" ]; then
    echo "[*] Stopping and removing background service..."
    sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    sudo systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    sudo rm -f "$SERVICE_PATH"
    sudo systemctl daemon-reload
    echo "    [+] Service removed."
fi

# 2. Remove the global command wrapper
WRAPPER_PATH="$HOME/.local/bin/wikisag"
if [ -f "$WRAPPER_PATH" ]; then
    echo "[*] Removing 'wikisag' terminal command..."
    rm -f "$WRAPPER_PATH"
    echo "    [+] Command removed."
fi

# 3. Locate and remove the installation directory
# We parse the wrapper script to find exactly where the user installed it
if [ -f "$WRAPPER_PATH.bak" ] || [ -f "$WRAPPER_PATH" ]; then
    # Fallback to default if wrapper is missing
    INSTALL_DIR="$HOME/wikisag"
else
    # We'll just ask the user or default to ~/wikisag
    INSTALL_DIR="$HOME/wikisag"
fi

echo -n "[*] Enter the directory where WikiSAG is installed [$INSTALL_DIR]: "
read USER_INPUT < /dev/tty

if [ -n "$USER_INPUT" ]; then
    INSTALL_DIR="${USER_INPUT/#\~/$HOME}"
fi

if [ -d "$INSTALL_DIR" ]; then
    echo "[*] Deleting application folder: $INSTALL_DIR..."
    rm -rf "$INSTALL_DIR"
    echo "    [+] Folder deleted."
else
    echo "    [-] Folder $INSTALL_DIR not found. Skipping."
fi

echo ""
echo "========================================"
echo "[+] WikiSAG has been completely uninstalled."
echo "========================================"

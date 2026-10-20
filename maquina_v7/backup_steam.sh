#!/bin/bash
# Maquina V7 - Backup Steam to Google Drive
# Run this AFTER installing your games to save them for next session.
# Next time you deploy, the backup will be restored automatically.
#
# This replaces V5's manual tar commands with a one-command backup
# that saves your entire Steam install to Google Drive.

DRIVE_MOUNT="/content/drive"
BACKUP_FILE="$DRIVE_MOUNT/MyDrive/maquina_v7_backup.tar.gz"
STEAM_INSTALL_DIR="$HOME/.steam/steam"

if [ ! -d "$STEAM_INSTALL_DIR" ]; then
    echo "[V7] ERROR: Steam install directory not found at $STEAM_INSTALL_DIR"
    echo "[V7] Install Steam first, then run this backup."
    exit 1
fi

if [ ! -d "$DRIVE_MOUNT/MyDrive" ]; then
    echo "[V7] ERROR: Google Drive not mounted at $DRIVE_MOUNT"
    echo "[V7] Run: from google.colab import drive; drive.mount('/content/drive')"
    exit 1
fi

echo "[V7] Creating Steam backup..."
echo "[V7] Source:      $STEAM_INSTALL_DIR"
echo "[V7] Destination: $BACKUP_FILE"
echo "[V7] This may take several minutes depending on game sizes..."

tar -czf "$BACKUP_FILE" -C "$STEAM_INSTALL_DIR" . 2>/dev/null

if [ $? -eq 0 ]; then
    SIZE=$(du -sh "$BACKUP_FILE" 2>/dev/null | cut -f1)
    echo "[V7] Backup complete! Size: $SIZE"
    echo "[V7] Next session will restore this automatically."
else
    echo "[V7] WARNING: Backup had errors. Check Drive space and try again."
fi

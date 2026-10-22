#!/bin/zsh
# Automator Folder Action — "Run Shell Script" (Shell: /bin/zsh, Pass input: as arguments)
# Watched folder: ~/Projects/Meeting Automation/MacWhisper-Exports
#
# Reference copy: Automator does not read this file. After changing it,
# paste the contents into the Folder Action again.
#
# Every file is passed on; meeting-processor decides whether the format is
# supported (.json, .vtt, .srt, .txt) and logs "Skipping unsupported file type" otherwise.

# ── Paths ────────────────────────────────────────────────
CMD="$HOME/Projects/Meeting Automation/meeting_processor/.venv/bin/meeting-processor"
LOG_DIR="$HOME/Projects/Meeting Automation/meeting_processor/logs"
LOG_FILE="$LOG_DIR/automator.log"

# ── Ensure log directory exists ──────────────────────────
mkdir -p "$LOG_DIR"

# ── Process each file passed by Automator ────────────────
for f in "$@"; do
  # Ignore hidden files (.DS_Store etc.) and folders
  if [[ "${f:t}" == .* || ! -f "$f" ]]; then
    continue
  fi
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Triggered: $f" >> "$LOG_FILE"
  "$CMD" "$f" >> "$LOG_FILE" 2>&1
  rc=$?
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Finished (exit $rc): $f" >> "$LOG_FILE"
done

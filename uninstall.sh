#!/usr/bin/env bash
# session.txt — uninstaller
#   curl -fsSL https://raw.githubusercontent.com/chuk-development/session.txt/main/uninstall.sh | bash
# Removes hooks, wrapper and the resume command. Existing session.txt files are kept.
set -euo pipefail

HOOKS_DIR="$HOME/.claude/hooks"
BIN_DIR="$HOME/.local/bin"
SETTINGS="$HOME/.claude/settings.json"
ICON_DIR="$HOME/.local/share/icons"
APPS_DIR="$HOME/.local/share/applications"
CMD='"$HOME/.claude/hooks/session-log.sh"'

log() { printf '\033[1;32m›\033[0m %s\n' "$*"; }

rm -f "$HOOKS_DIR/session-log.sh" "$HOOKS_DIR/agent-wrappers.sh" "$HOOKS_DIR/codex-session-log.sh" \
      "$BIN_DIR/resume" "$BIN_DIR/ccs" "$BIN_DIR/cc-session-gui" "$BIN_DIR/ccsessions.py" \
      "$ICON_DIR/cc-session-gui.svg" "$APPS_DIR/cc-session-gui.desktop"
rm -rf "$BIN_DIR/__pycache__"
command -v update-desktop-database >/dev/null 2>&1 \
  && update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
log "Scripts, GUI launcher and icon removed"

if [ -f "$SETTINGS" ] && command -v jq >/dev/null 2>&1; then
  tmp="$(mktemp)"
  jq --arg cmd "$CMD" '
    def strip($e):
      .hooks[$e] = ((.hooks[$e] // [])
        | map(select( ((.hooks // []) | map(.command) | index($cmd)) | not )));
    if .hooks then strip("SessionStart") | strip("Stop") | strip("SessionEnd") else . end
  ' "$SETTINGS" > "$tmp" && mv "$tmp" "$SETTINGS"
  log "Hooks removed from settings.json"
fi

for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
  [ -f "$rc" ] || continue
  sed -i '/# >>> session.txt codex hook >>>/,/# <<< session.txt codex hook <<</d' "$rc"
  sed -i '/# >>> session.txt agent wrappers >>>/,/# <<< session.txt agent wrappers <<</d' "$rc"
  log "wrappers removed from $(basename "$rc")"
done

log "Uninstalled. Your session.txt files were left untouched."

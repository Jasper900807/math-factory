#!/usr/bin/env bash
# sandbox_watcher.sh — Polls /sandbox/trigger.txt in the OpenShell sandbox.
# When a topic appears, triggers factory_v4.py and clears the file.

set -euo pipefail

SANDBOX="my-assistant"
REMOTE_TRIGGER="/sandbox/trigger.txt"
LOCAL_TMP="/tmp/mf_trigger_dl"
FACTORY="/home/ubuntu/math-factory/factory_v4.py"
VENV_PYTHON="/home/ubuntu/.venv/bin/python"
LOG="/home/ubuntu/math-factory/logs/watcher.log"
POLL_SEC=5

export NVM_DIR="$HOME/.nvm"
# shellcheck disable=SC1090
source "$NVM_DIR/nvm.sh" 2>/dev/null || true

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

log "Sandbox watcher started (polling every ${POLL_SEC}s)"

while true; do
    mkdir -p "$LOCAL_TMP"
    rm -rf "${LOCAL_TMP:?}"/*

    if openshell sandbox download "$SANDBOX" "$REMOTE_TRIGGER" "$LOCAL_TMP" 2>/dev/null; then
        # Downloaded filename may differ; find it
        TOPIC_FILE=$(find "$LOCAL_TMP" -type f | head -1)
        if [[ -n "$TOPIC_FILE" ]]; then
            TOPIC=$(cat "$TOPIC_FILE" | tr -d '\n\r' | head -c 200)
            if [[ -n "$TOPIC" ]]; then
                log "Trigger detected: topic='$TOPIC'"

                # Clear trigger file in sandbox immediately (use SSH to avoid upload creating a directory)
                ssh openshell-my-assistant "rm -f $REMOTE_TRIGGER" 2>/dev/null || true

                # Run factory
                log "Starting factory_v4.py ..."
                TOPIC="$TOPIC" "$VENV_PYTHON" "$FACTORY" >> "$LOG" 2>&1 && \
                    log "factory_v4.py done" || \
                    log "factory_v4.py FAILED (exit $?)"
            fi
        fi
    fi

    sleep "$POLL_SEC"
done

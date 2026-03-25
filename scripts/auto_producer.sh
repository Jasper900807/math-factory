#!/usr/bin/env bash
# auto_producer.sh — 持續生產數學 YouTube Shorts 並上傳 Google Drive
# 在 Brev 開啟期間持續運行，每支影片完成後自動接續下一支

set -euo pipefail

SANDBOX="my-assistant"
REMOTE_TRIGGER="/sandbox/trigger.txt"
WORK_DIR="/home/ubuntu/math-factory"
VENV_PYTHON="/home/ubuntu/.venv/bin/python"
LOG="$WORK_DIR/logs/auto_producer.log"
DONE_FILE="$WORK_DIR/data/topics_done.txt"
WATCHER_LOG="$WORK_DIR/logs/watcher.log"
TIMEOUT_MIN=35  # 每支影片最長等待時間（分鐘）

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

log "====== Auto Producer 啟動 ======"

while true; do
    # 1. 生成主題
    log "[1/4] 生成主題..."
    TOPIC=$("$VENV_PYTHON" "$WORK_DIR/generate_topic.py" 2>>"$LOG") || {
        log "ERROR: 生成主題失敗，60秒後重試"
        sleep 60
        continue
    }
    log "主題：$TOPIC"

    # 2. 觸發生產（寫入 sandbox trigger）
    log "[2/4] 觸發生產..."
    echo "$TOPIC" > /tmp/ap_topic.txt
    touch /tmp/mf_last_trigger  # 時間戳記基準，用於之後找新影片
    ssh openshell-my-assistant "rm -f $REMOTE_TRIGGER" 2>/dev/null || true
    openshell sandbox upload "$SANDBOX" /tmp/ap_topic.txt "$REMOTE_TRIGGER" 2>/dev/null || {
        log "ERROR: 無法上傳 trigger，60秒後重試"
        sleep 60
        continue
    }

    # 3. 等待 factory 完成
    log "[3/4] 等待 factory_v4.py 完成（最長 ${TIMEOUT_MIN} 分鐘）..."
    # 記錄觸發前 watcher.log 的行數，只看新增的行
    BASELINE=$(wc -l < "$WATCHER_LOG" 2>/dev/null || echo 0)
    DEADLINE=$(( $(date +%s) + TIMEOUT_MIN * 60 ))
    STATUS=""

    while [[ $(date +%s) -lt $DEADLINE ]]; do
        RESULT=$(tail -n +"$BASELINE" "$WATCHER_LOG" 2>/dev/null | grep -m1 "factory_v4\.py \(done\|FAILED\)" || true)
        if [[ -n "$RESULT" ]]; then
            if echo "$RESULT" | grep -q "done"; then
                STATUS="done"
            else
                STATUS="failed"
            fi
            break
        fi
        sleep 10
    done

    if [[ "$STATUS" == "done" ]]; then
        log "  ✅ 影片生產完成"
    elif [[ "$STATUS" == "failed" ]]; then
        log "  ⚠️ factory_v4.py 失敗，跳過上傳，繼續下一個主題"
        echo "$TOPIC" >> "$DONE_FILE"
        sleep 5
        continue
    else
        log "  ⚠️ 逾時（${TIMEOUT_MIN} 分鐘），跳過，繼續下一個主題"
        echo "$TOPIC" >> "$DONE_FILE"
        sleep 5
        continue
    fi

    # 4. 上傳 Google Drive（找觸發後產生的 output_*.mp4）
    VIDEO_PATH=$(find "$WORK_DIR/output" -maxdepth 1 -name "output_*.mp4" \
        -newer /tmp/mf_last_trigger \
        ! -name "output_test-topic.mp4" 2>/dev/null | sort | tail -1)

    if [[ -n "$VIDEO_PATH" && -f "$VIDEO_PATH" ]]; then
        FNAME=$(basename "$VIDEO_PATH")
        log "[4/4] 上傳 Google Drive：$FNAME"
        "$VENV_PYTHON" "$WORK_DIR/upload_gdrive.py" "$VIDEO_PATH" "$FNAME" && \
            log "  ✅ 上傳完成" || \
            log "  ⚠️ 上傳失敗（影片仍保留在本機）"
    else
        log "  ⚠️ 找不到影片檔"
    fi

    # 記錄已完成主題
    echo "$TOPIC" >> "$DONE_FILE"

    log "====== 完成一支，立刻開始下一支 ======"
    sleep 3
done

#!/usr/bin/env bash
# auto_producer.sh — 持續生產數學 YouTube Shorts 並上傳 Google Drive
# 在 gcloud VM 上持續運行，每支影片完成後自動接續下一支

set -euo pipefail

WORK_DIR="/home/ubuntu/math-factory"
VENV_PYTHON="/home/ubuntu/.venv/bin/python"
LOG="$WORK_DIR/logs/auto_producer.log"
DONE_FILE="$WORK_DIR/data/topics_done.txt"
TIMEOUT_MIN=35  # 每支影片最長等待時間（分鐘）

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

log "====== Auto Producer 啟動 ======"

while true; do
    # 1. 生成主題
    log "[1/3] 生成主題..."
    TOPIC=$("$VENV_PYTHON" "$WORK_DIR/generate_topic.py" 2>>"$LOG") || {
        log "ERROR: 生成主題失敗，60秒後重試"
        sleep 60
        continue
    }
    log "主題：$TOPIC"

    # 2. 直接執行 factory_v4.py 生產影片
    log "[2/3] 開始生產..."
    touch /tmp/mf_last_trigger  # 時間戳記基準，用於之後找新影片

    QUALITY="failed"
    if timeout "${TIMEOUT_MIN}m" "$VENV_PYTHON" "$WORK_DIR/factory_v4.py" --topic "$TOPIC" 2>&1; then
        QUALITY=$(grep -o '\[QUALITY\] [a-z]*' "$LOG" | tail -1 | awk '{print $2}' 2>/dev/null || echo "ok")
        QUALITY="${QUALITY:-ok}"
        log "  ✅ 影片生產完成（品質：$QUALITY）"
    else
        EXIT_CODE=$?
        if [[ $EXIT_CODE -eq 124 ]]; then
            log "  ⚠️ 逾時（${TIMEOUT_MIN} 分鐘），跳過，繼續下一個主題"
            echo "$TOPIC|timeout|$(date +%F)" >> "$DONE_FILE"
        else
            log "  ⚠️ factory_v4.py 失敗（exit $EXIT_CODE），跳過上傳，繼續下一個主題"
            echo "$TOPIC|failed|$(date +%F)" >> "$DONE_FILE"
        fi
        sleep 5
        continue
    fi

    # 3. 上傳 Google Drive（找觸發後產生的 output_*.mp4）
    VIDEO_PATH=$(find "$WORK_DIR/output" -maxdepth 1 -name "output_*.mp4" \
        -newer /tmp/mf_last_trigger \
        ! -name "output_test-topic.mp4" 2>/dev/null | sort | tail -1)

    if [[ -n "$VIDEO_PATH" && -f "$VIDEO_PATH" ]]; then
        FNAME=$(basename "$VIDEO_PATH")
        log "[3/3] 上傳 Google Drive：$FNAME"
        "$VENV_PYTHON" "$WORK_DIR/upload_gdrive.py" "$VIDEO_PATH" "$FNAME" && \
            log "  ✅ 上傳完成" || \
            log "  ⚠️ 上傳失敗（影片仍保留在本機）"
    else
        log "  ⚠️ 找不到影片檔"
    fi

    # 記錄已完成主題（含品質與日期）
    echo "$TOPIC|$QUALITY|$(date +%F)" >> "$DONE_FILE"

    log "====== 完成一支，立刻開始下一支 ======"
    sleep 3
done

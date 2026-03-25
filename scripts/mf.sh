#!/usr/bin/env bash
# mf.sh — Math Factory 控制腳本
# 用法：mf.sh start | stop | status | log

WORK_DIR="/home/ubuntu/math-factory"
WATCHER="$WORK_DIR/scripts/sandbox_watcher.sh"
PRODUCER="$WORK_DIR/scripts/auto_producer.sh"
BOT="$WORK_DIR/bot/discord_bot.py"
WATCHER_LOG="$WORK_DIR/logs/watcher.log"
PRODUCER_LOG="$WORK_DIR/logs/auto_producer.log"
BOT_LOG="$WORK_DIR/logs/discord_bot.log"
VENV_PYTHON="/home/ubuntu/.venv/bin/python"

source ~/.bashrc 2>/dev/null || true

case "$1" in
  start)
    if pgrep -f "bash.*auto_producer.sh" > /dev/null; then
      echo "已經在運行中"
      exit 0
    fi
    nohup bash "$WATCHER" >> "$WATCHER_LOG" 2>&1 &
    nohup bash "$PRODUCER" >> "$PRODUCER_LOG" 2>&1 &
    echo "✅ 已啟動（watcher + producer）"
    ;;
  stop)
    pkill -f "bash.*auto_producer.sh" 2>/dev/null && echo "✅ auto_producer 已停止" || echo "auto_producer 未在運行"
    pkill -f "bash.*sandbox_watcher.sh" 2>/dev/null && echo "✅ sandbox_watcher 已停止" || echo "sandbox_watcher 未在運行"
    ;;
  status)
    echo "=== 運行狀態 ==="
    pgrep -af "bash.*(auto_producer|sandbox_watcher).sh" | grep -v grep || echo "（未在運行）"
    pgrep -af "python.*discord_bot" | grep -v grep || true
    echo ""
    echo "=== 最新進度 ==="
    tail -5 "$PRODUCER_LOG"
    ;;
  log)
    tail -f "$PRODUCER_LOG"
    ;;
  bot)
    if pgrep -f "python.*discord_bot" > /dev/null; then
      echo "Bot 已在運行中"
      exit 0
    fi
    source ~/.bashrc 2>/dev/null || true
    nohup "$VENV_PYTHON" "$BOT" >> "$BOT_LOG" 2>&1 &
    echo "✅ Discord Bot 已啟動（PID $!，log: $BOT_LOG）"
    ;;
  bot-stop)
    pkill -f "python.*discord_bot" 2>/dev/null && echo "✅ Discord Bot 已停止" || echo "Bot 未在運行"
    ;;
  bot-log)
    tail -f "$BOT_LOG"
    ;;
  clear-topics)
    COUNT=$(wc -l < "$WORK_DIR/data/topics_done.txt" 2>/dev/null || echo 0)
    > "$WORK_DIR/data/topics_done.txt"
    echo "✅ 已清除 $COUNT 筆主題紀錄"
    ;;
  remove-topic)
    if [[ -z "${2:-}" ]]; then
      echo "用法：$0 remove-topic <主題名稱>"
      exit 1
    fi
    TOPIC="$2"
    FILE="$WORK_DIR/data/topics_done.txt"
    BEFORE=$(wc -l < "$FILE" 2>/dev/null || echo 0)
    grep -v "^${TOPIC}|" "$FILE" | grep -v "^${TOPIC}$" > "${FILE}.tmp" && mv "${FILE}.tmp" "$FILE"
    AFTER=$(wc -l < "$FILE")
    REMOVED=$(( BEFORE - AFTER ))
    if [[ "$REMOVED" -gt 0 ]]; then
      echo "✅ 已移除：$TOPIC（$REMOVED 筆）"
    else
      echo "⚠️ 找不到：$TOPIC"
    fi
    ;;
  *)
    echo "用法：$0 start | stop | status | log | bot | bot-stop | bot-log | clear-topics | remove-topic <主題>"
    exit 1
    ;;
esac

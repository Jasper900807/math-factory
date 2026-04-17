# 數學自媒體工廠

全自動生產數學知識 YouTube Shorts（60秒直式影片）的 AI 流水線。

---

## 系統概覽

```
auto_producer.sh（無限迴圈）
    ├── generate_topic.py → Gemini 自動選題（熱門 / 深度 交替）
    └── factory_v4.py（直接執行）
            ├── Gemini 2.5 Flash → 旁白腳本（6段結構）
            ├── F5-TTS → 分段配音
            ├── Gemini 2.5 Flash → Manim 動畫程式碼（AST 驗證，最多重試3次）
            ├── Manim → 數學動畫渲染（1080×1920），失敗自動降級備用模板
            └── FFmpeg → 合併影片 + 字幕燒入
            ↓ 輸出品質標記（ok / fallback）
auto_producer.sh
    ├── rclone → 上傳 Google Drive
    └── 記錄 data/topics_done.txt（含品質與日期）
```

---

## 目錄結構

```
math-factory/
├── factory_v4.py         # 核心生產程式
├── generate_topic.py     # Gemini 自動選題（含分類邏輯）
├── upload_gdrive.py      # Google Drive 上傳（rclone）
├── scripts/
│   ├── mf.sh             # 控制腳本（start/stop/status/bot）
│   └── auto_producer.sh  # 無限生產迴圈
├── bot/
│   └── discord_bot.py    # Discord 遠端控制 Bot
├── logs/                 # 執行日誌
├── output/               # 成品影片 output_*.mp4
├── data/
│   ├── topics_done.txt   # 已生產主題紀錄（格式：主題|品質|日期）
│   └── ref_voices/       # TTS 參考音訊
└── tmp/                  # factory 執行暫存（自動生成）
    ├── math_short.py     # AI 生成的 Manim 程式碼
    ├── segments/         # 分段 TTS 音訊
    └── media/            # Manim 渲染中間檔
```

---

## 快速操作

### 環境需求

```bash
export GEMINI_API_KEY="..."      # 寫入 ~/.bashrc
export DISCORD_BOT_TOKEN="..."   # 寫入 ~/.bashrc
```

### 啟動 / 停止生產線

```bash
mf start          # 啟動 auto_producer
mf stop           # 停止生產線
mf status         # 查看運行狀態
mf log            # 即時查看 log（Ctrl+C 離開）
mf clear-topics           # 清除所有已完成主題紀錄
mf remove-topic <主題>    # 移除指定主題（讓它可以重新被生產）
```

### 啟動 Discord Bot

```bash
mf bot        # 啟動 Discord 遠端控制 Bot
mf bot-stop   # 停止 Bot
mf bot-log    # 查看 Bot log
```

### 手動觸發單支影片

```bash
# 直接執行
TOPIC="黎曼假設" /home/ubuntu/.venv/bin/python factory_v4.py
# 或使用 --topic 參數
/home/ubuntu/.venv/bin/python factory_v4.py --topic "黎曼假設"
```

---

## Discord Bot 指令

Bot 上線後在任意頻道使用：

| 指令 | 說明 |
|------|------|
| `!status` | 查看生產線運行狀態與最新進度 |
| `!start` | 啟動生產線 |
| `!stop` | 停止生產線 |
| `!log [行數]` | 查看最新 log（預設 15 行） |
| `!topic 主題名稱` | 觸發指定主題生產（完成後自動推播通知） |
| `!queue` | 查看已完成主題列表（含品質標記） |
| `!remove <主題名稱>` | 從已完成清單移除指定主題 |
| `!help` | 顯示指令列表 |

---

## 生產流程細節

### 1. 選題（generate_topic.py）
- 呼叫 Gemini API，依已完成主題數量**交替生成兩種類別**：
  - 偶數輪 → **熱門**：反直覺、大眾化（生日悖論、蒙提霍爾問題等）
  - 奇數輪 → **深度**：有故事性的進階主題（哥德爾不完備定理、黎曼假設等）
- 自動讀取 `data/topics_done.txt`，避免重複（最近 50 筆）

### 2. 腳本生成（factory_v4.py）
- 6 段結構：Hook → 直覺挑戰 → 核心概念 → 驚人數字 → 生活連結 → CTA
- 每段約 10 秒，總長 60 秒

### 3. 配音（F5-TTS）
- 按句拆分，依序生成所有片段
- 合併為完整 `voiceover.mp3`，同時建立時間軸

### 4. 動畫（Manim）
- Gemini 針對每個時間段生成對應的 Manim snippet
- AST 語法驗證，失敗自動重試最多 3 次
- **全部重試失敗時自動降級為備用模板**（脈衝動畫），不中斷流程
- 渲染規格：1080×1920（垂直），黑底白字

### 5. 合成（FFmpeg）
- 合併動畫 + 配音
- 字幕燒入（drawtext，底部，半透明背景）
- 輸出：`output/output_{主題}.mp4`，8Mbps

### 6. 上傳與品質記錄（auto_producer.sh）
- 自動上傳至 Google Drive `影片/` 資料夾
- 記錄生產結果至 `data/topics_done.txt`，格式：

  ```
  主題名稱|品質|日期
  哥德爾不完備定理|ok|2026-03-25
  某主題|fallback|2026-03-25
  ```

  | 品質標記 | 說明 |
  |----------|------|
  | `ok` | AI Manim 動畫生成成功 |
  | `fallback` | AI 失敗，使用備用模板 |
  | `failed` | factory 整體失敗 |
  | `timeout` | 超過 35 分鐘逾時 |

---

## 環境規格

| 項目 | 內容 |
|------|------|
| 機器 | gcloud VM |
| GPU | NVIDIA T4 16GB（或其他 GPU） |
| OS | Ubuntu 22.04 |
| Python | 3.12（`/home/ubuntu/.venv`） |
| 主要 API | Gemini 2.5 Flash |
| TTS | F5-TTS |
| 雲端儲存 | Google Drive（rclone OAuth2） |

---

## 未來計畫

- 配音升級：ElevenLabs 或 Azure Neural TTS
- 自動上傳 YouTube（YouTube Data API）

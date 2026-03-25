# 自媒體工廠 — 專案背景

## 目標
全自動生產數學知識 YouTube Shorts（60秒直式影片）

## 流程
```
Gemini 2.5 Flash API → 旁白腳本（6段結構：Hook → 直覺挑戰 → 核心概念 → 驚人數字 → 生活連結 → CTA）
  ↓ 按句拆分
edge-tts → 每句分段音訊（asyncio 並行）→ 時間軸
  ↓
Gemini 2.5 Flash API → Manim snippet（每段獨立生成，AI 只填視覺內容）
  ↓ AST 語法驗證 → 自動重試3次，失敗用備用模板
Manim → 純數學動畫渲染（1080x1920，無字幕）
  ↓
FFmpeg → 合併影片 + 配音 + 字幕燒入（drawtext）→ final_output.mp4
```

## 環境
- 機器：Brev `youtube-factory`，NVIDIA T4 16GB，Ubuntu 22.04，Python 3.12
- venv：`/home/ubuntu/.venv`
- 主程式：`~/math-factory/factory_v4.py`
- Claude Code CLI：已安裝（`claude --version` = 2.1.81）

## 主要檔案
| 檔案 | 說明 |
|------|------|
| `factory_v4.py` | 主程式 |
| `math_short.py` | Manim 動畫範本（每次執行自動覆蓋） |
| `segments/` | 分段 TTS 音訊 |
| `voiceover.mp3` | 完整配音 |
| `final_output.mp4` | 最終成品 |

## 關鍵設計決策

### 字幕
- **不在 Manim 裡做字幕**，改用 FFmpeg `drawtext` 燒入
- 原因：Manim 座標系（frame_width=9 units）跟像素換算複雜，容易超出畫面
- 字幕樣式：fontsize=44，每行最多16字，底部留260px，半透明黑色背景（`box=1:boxcolor=black@0.45:boxborderw=14`）

### Manim 生成策略（Segment Snippet）
- AI 針對每個時間段獨立輸出 snippet，Python 組裝成完整檔案
- 用 `=== SEGMENT N ===` 分隔符解析 AI 回應
- `_parse_segment_snippets()`：用 `.splitlines()` + 逐行 pop 去除首尾空行，**不用 `.strip()`**（會破壞內部縮排）
- `_assemble_manim_code()`：計算 `min_indent`，將相對縮排對齊到 8 空格
- 渲染前做 `ast.parse()` 語法驗證，有錯直接重生成（省渲染時間）
- `fix_manim_code()` 後處理：修正常見錯誤（Wait()語法、MathTex中文、未定義變數）
- `_estimate_snippet_duration()`：估算 snippet 時長，補 padding wait 對齊配音

### 畫面邊界保護（Frame Guard）
- **不用 `play()`/`add()` override**（會在 mobject 未初始化時崩潰，且無法攔截 `always_redraw`）
- 改用 **updater-based guard**：在 `construct()` 最前面加一個 dummy `Mobject()`，`add_updater()` 每幀對所有 `self.mobjects` 呼叫 `constrain()`
- `constrain()` 函數：縮放過大物件、shift 超出安全區的物件
- 安全區：X ±3.8（SAFE_W=7.8），Y -4.5 ~ +7.0
- `_is_bg=True` 旗標保護背景不被縮放

### 音訊
- 用 `asyncio.gather()` 並行生成所有 TTS 片段
- concat list 用絕對路徑（避免相對路徑問題）
- edge-tts 503 重試：最多5次，間隔1.5秒

### 輸出路徑
- Manim 輸出路徑從 stderr 動態抓（`"File ready at '...'"` 這行）
- 不硬寫 quality map，因為 `config.pixel_height=1920` 會覆蓋 `-ql` 的解析度

### FFmpeg 輸出品質
- `-b:v 8M -maxrate 10M -bufsize 16M -b:a 192k -preset slow`
- `-t {audio_duration}` 精確截切（避免動畫比配音短導致 loop artifact）
- **不用** `-stream_loop -1`

### 片頭 / 片尾
- 片頭：黃點 + 放射圓環（0.6秒），`INTRO_DURATION = 0.6`（字幕時間偏移用）
- 片尾：`數學小知識 / 每週更新` 文字，FadeIn 0.6秒 + hold 1.5秒 + FadeOut 0.4秒
- 末尾補 `self.wait(3.0)` end buffer（防止渲染比配音短）

## 目前狀態
- ✅ 字幕同步且不超出畫面
- ✅ Gemini 2.5 Flash 生成腳本正常
- ✅ AST 語法驗證 + 自動重試正常
- ✅ FFmpeg 輸出品質提升（8Mbps）
- ✅ 片頭 / 片尾品牌動畫
- ✅ Frame guard 解決動畫超出畫面問題
- 🔄 Manim 動畫品質仍有時落到備用模板（AI 生成不穩定）
- 📋 待做：自動選題、配音升級（ElevenLabs / Azure Neural TTS）、自動上傳 YouTube

## 已知問題與解法
| 問題 | 解法 |
|------|------|
| Manim 輸出路徑錯誤 | 從 stderr 動態抓路徑，不用硬寫 quality map |
| 字幕超出畫面 | 改用 FFmpeg drawtext，完全離開 Manim 座標系 |
| AI 生成 Wait 語法錯誤 | `fix_manim_code()` 正則修正 |
| edge-tts 503 | asyncio 重試5次，間隔1.5秒 |
| AST "unexpected indent" | `_parse_segment_snippets` 只去首尾空行，不 `.strip()`；`_assemble_manim_code` 保留相對縮排 |
| 動畫超出畫面 | Frame guard updater（每幀 constrain 所有 mobjects），不用 play/add override |
| play/add override 崩潰 | 已移除；改用 frame guard，避免操作未初始化的 mobject |
| 影片末尾 loop artifact | 移除 `-stream_loop -1`，加 `self.wait(3.0)` end buffer，用 `-t` 精確截切 |
| 執行順序顯示錯誤（5/6 先於 4/6）| 重新標號：generate+render = [4/6]，merge = [5/6] |
| `corner_radius` TypeError（Rectangle/Polygon 不支援）| `fix_manim_code()` 自動轉換 `Rectangle(..., corner_radius=N)` → `RoundedRectangle(...)`，其他物件移除該參數（placeholder 技術保護 RoundedRectangle） |
| AI 重試時放棄 segment 格式、改寫完整 class | 錯誤訊息只傳最後一行關鍵錯誤（不傳完整 traceback）；prompt 頂端加「絕對格式規定」block；error section 明確重申保持格式 |
| 多行 `Text(...)` 沒有觸發 fit_width 注入（大→跳縮 artifact）| `_inject_fit_width` 改用 bracket-depth 計數追蹤多行建構子，與 `_inject_max_width` 同樣邏輯 |
| 分數標籤與門框重疊（buff 太小）| prompt 規則及 SEGMENT 7 範例統一改為 `buff=0.5` |

## 環境變數
```bash
export GEMINI_API_KEY="AIza..."   # 已寫入 ~/.bashrc
```

## 未來計畫
- 配音升級：ElevenLabs 或 Azure Neural TTS（取代 edge-tts）
- 自動選題（目前 `TOPIC` 硬寫在主程式頂部）
- NemoClaw（NVIDIA + OpenClaw）整合：agent 自動觸發影片生產，走雲端推論不佔 T4 VRAM
- 自動上傳 YouTube

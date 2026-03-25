"""
自媒體工廠 v4 — Gemini API + 結構化生成 + AST 驗證版
改進重點：
  1. Manim 生成：結構化 Prompt（固定骨架，AI 只填 segment 內容）
  2. 渲染前 AST syntax check，語法錯就直接重生成，不浪費渲染時間
  3. 後處理更全面的正則修正（涵蓋 v3 發現的所有錯誤模式）
  4. 字幕換行改用寬度計算（中文字每字約 36px @font_size=36）
  5. asyncio 統一用 event loop，避免 asyncio.run() 巢狀問題
  6. concat list 改用絕對路徑
"""

import os
import re
import ast
import asyncio
import argparse
import subprocess
import requests

# ── 專案根目錄 ──
WORK_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Gemini API 設定 ──
# 從環境變數讀取，啟動前執行：export GEMINI_API_KEY="你的key"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODEL_SCRIPT = "gemini-2.5-flash"   # 腳本生成
GEMINI_MODEL_MANIM  = "gemini-2.5-flash"   # Manim 程式碼生成

MAX_RETRIES = 3

# Manim 座標系：frame_width=9, pixel_width=1080 → 1 unit = 120px
# Text().set_max_width(8.2) 讓 Manim 自己換行，對應約 984px（留各 48px 邊距）
FONT_SIZE = 40
TEXT_MAX_WIDTH = 8.2   # Manim units
INTRO_DURATION = 0.6   # 片頭動畫長度（秒），字幕時間軸要跟著偏移


# ══════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════
def gemini(model: str, prompt: str, timeout: int = 120) -> str:
    """呼叫 Gemini API，回傳純文字回應"""
    if not GEMINI_API_KEY:
        raise Exception("GEMINI_API_KEY 未設定，請執行：export GEMINI_API_KEY='你的key'")
    url = GEMINI_URL.format(model=model) + f"?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 16384},
    }
    response = requests.post(url, json=payload, timeout=timeout)
    if response.status_code != 200:
        raise Exception(f"Gemini API 錯誤 {response.status_code}：{response.text[:300]}")
    data = response.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError) as e:
        raise Exception(f"Gemini 回應格式異常：{data}") from e




def fix_manim_code(code: str) -> str:
    """全面修正 AI 常見 Manim 語法錯誤"""
    # 去除 markdown 包裹
    code = re.sub(r"```python\s*", "", code)
    code = re.sub(r"```\s*", "", code)
    code = code.strip()

    # MathTex 中的中文字元
    def _strip_chinese_from_mathtex(match):
        inner = match.group(1)
        cleaned = re.sub(r"[\u4e00-\u9fff\u3400-\u4dbf]+", "", inner)
        return f"MathTex({cleaned}"
    code = re.sub(r"MathTex\(([^)]+)", _strip_chinese_from_mathtex, code)

    # Wait(run_time=...) → self.wait(N)
    code = re.sub(r"\bWait\s*\(\s*run_time\s*=\s*([^)]+)\)", r"self.wait(\1)", code)
    # Wait(...) 獨立呼叫（非 self.）
    code = re.sub(r"(?<!self\.)(?<!\w)Wait\s*\(([^)]*)\)", r"self.wait(3.0)", code)
    # self.play(self.wait(...)) → self.wait(...)
    code = re.sub(r"self\.play\(\s*self\.wait\(([^)]*)\)\s*\)", r"self.wait(\1)", code)
    # self.self.wait
    code = code.replace("self.self.wait", "self.wait")
    # 未定義的 duration 變數
    for var in ("duration", "wait_time", "remaining_time", "seg_duration"):
        code = re.sub(rf"self\.wait\(\s*{var}\s*\)", "self.wait(3.0)", code)
    # run_time=duration 在 self.play 裡
    code = re.sub(r"run_time\s*=\s*duration\b", "run_time=1.5", code)

    # Tex() 裡有中文 → 改 Text()
    def _tex_to_text_if_chinese(match):
        inner = match.group(1)
        if re.search(r"[\u4e00-\u9fff]", inner):
            # 取出第一個引號內的字串
            m = re.search(r'["\']([^"\']+)["\']', inner)
            if m:
                return f'Text("{m.group(1)}", font="Noto Sans CJK TC"'
        return f"Tex({inner}"
    code = re.sub(r"Tex\(([^)]+)", _tex_to_text_if_chinese, code)

    # FadeIn(obj, scale=...) → FadeIn(obj, scale=...) 正確，但 FadeIn(run_time=...) 要移除 run_time
    # 某些版本 FadeIn 不接受 run_time，改用 self.play(FadeIn(obj), run_time=...)
    code = re.sub(
        r"self\.play\(FadeIn\(([^,)]+),\s*run_time=([^)]+)\)\)",
        r"self.play(FadeIn(\1), run_time=\2)",
        code,
    )
    code = re.sub(
        r"self\.play\(FadeOut\(([^,)]+),\s*run_time=([^)]+)\)\)",
        r"self.play(FadeOut(\1), run_time=\2)",
        code,
    )

    # scale() 後接 .to_edge() 是合法的，但 .shift(UP * N) 有時缺 * 運算子
    # 修正 UP N → UP * N（後面接數字沒有 *）
    for direction in ("UP", "DOWN", "LEFT", "RIGHT"):
        code = re.sub(rf"\b{direction}\s+(\d)", rf"{direction} * \1", code)

    # 限制 font_size 上限（> 60 在 9-unit 寬的直式畫面很容易超出）
    def _cap_font_size(m):
        return f"font_size={min(int(m.group(1)), 60)}"
    code = re.sub(r"font_size\s*=\s*(\d+)", _cap_font_size, code)

    # 限制 .scale(N) 上限為 2.0（超過很容易超出畫面）
    def _cap_scale(m):
        return f".scale({min(float(m.group(1)), 2.0):.1f})"
    code = re.sub(r"\.scale\((\d+\.?\d*)\)", _cap_scale, code)

    # MathTex(r"\text{純文字}") → Text("純文字")
    # Write 在純 \text{} 上效果差且容易超寬，改成 Text + FadeIn 更穩定
    def _mathtex_text_to_text(m):
        content = m.group(1)
        suffix  = m.group(2)   # 可能有 .scale(...) .set_color(...) 等
        # 只轉換「整個 MathTex 內容就是 \text{...}」的情況
        return f'Text("{content}", font="Noto Sans CJK TC"{suffix})'
    code = re.sub(
        r'MathTex\(r"\\text\{([^{}\\]+)\}"([^)]*)\)',
        _mathtex_text_to_text,
        code,
    )
    code = re.sub(
        r"MathTex\(r'\\text\{([^{}\\]+)\}'([^)]*)\)",
        _mathtex_text_to_text,
        code,
    )

    # Text 物件強制加 .scale_to_fit_width(6.5)，比 set_max_width 更可靠
    # 支援多行 Text 賦值（追蹤括號深度）
    def _inject_fit_width(code_str):
        lines = code_str.splitlines()
        out = []
        pending = None  # (indent, varname) waiting for assignment to close
        depth = 0
        for line in lines:
            out.append(line)
            stripped = line.strip()
            if pending is None:
                m = re.match(r'^(\s+)(\w+)\s*=\s*Text\(', line)
                if m:
                    pending = (m.group(1), m.group(2))
                    depth = line.count('(') - line.count(')')
                    if depth <= 0:
                        # Single-line: close immediately
                        indent, var = pending
                        out.append(f'{indent}if {var}.width > 6.5: {var}.scale_to_fit_width(6.5)')
                        pending = None
            else:
                depth += line.count('(') - line.count(')')
                if depth <= 0:
                    indent, var = pending
                    out.append(f'{indent}if {var}.width > 6.5: {var}.scale_to_fit_width(6.5)')
                    pending = None
        return '\n'.join(out)
    code = _inject_fit_width(code)

    # corner_radius 只有 RoundedRectangle 支援，不是 Rectangle/Polygon/VMobject
    # 把 Rectangle(..., corner_radius=N, ...) → RoundedRectangle(..., corner_radius=N, ...)
    code = re.sub(r'\bRectangle\s*\(([^)]*\bcorner_radius\b[^)]*)\)', r'RoundedRectangle(\1)', code)
    # 其他非 RoundedRectangle 的物件（Polygon/Square/VMobject 等）直接移除 corner_radius 參數
    # 先把 RoundedRectangle 內的 corner_radius 值暫時用 placeholder 保護
    _cr_map = {}
    def _protect_cr(m):
        key = f"__CR{len(_cr_map)}__"
        _cr_map[key] = m.group(1)
        return f"RoundedRectangle({m.group(1).replace('corner_radius', key)})"
    # 暫時 placeholder RoundedRectangle 裡的 corner_radius
    code = re.sub(r'RoundedRectangle\(([^)]*\bcorner_radius\b[^)]*)\)',
                  lambda m: "RoundedRectangle(" + m.group(1).replace("corner_radius", "\x00CR\x00") + ")",
                  code)
    code = re.sub(r',\s*corner_radius\s*=\s*[\d.]+', '', code)  # 移除其他所有的
    code = code.replace("\x00CR\x00", "corner_radius")  # 還原 RoundedRectangle 的

    # 截斷的 method chain：行末孤立的 `.` → 移除（AI 回應被截斷時出現）
    code = re.sub(r"\)\.\s*\n(\s*self\.wait)", r")\n\1", code)
    code = re.sub(r"\)\.\s*\n(\s*self\.play)", r")\n\1", code)
    code = re.sub(r"\)\.\s*\n(\s*self\.remove)", r")\n\1", code)

    # ParametricFunction: t_range 必須是 list/tuple，不能是 np.array
    code = re.sub(
        r"t_range\s*=\s*np\.array\(\[([^\]]+)\]\)",
        r"t_range=[\1]",
        code,
    )
    # ParametricFunction lambda 回傳必須是 np.array([x, y, 0])，不能是 (x, y, 0)
    code = re.sub(
        r"lambda t:\s*\(([^,]+),\s*([^,]+),\s*0\)",
        r"lambda t: np.array([\1, \2, 0])",
        code,
    )

    # 修正 always_redraw lambda 裡的 f-string 使用 int() 可能遇到的問題（保持原樣即可）
    # 修正 TransformMatchingTex 要求兩個物件都在場景中
    # → 如果有 TransformMatchingTex(a, b) 但 a 沒有 self.add/self.play 過，容易出錯
    # 這個較難自動修正，先略過

    # 修正 Axes → 確保有正確的 x_range / y_range
    # 若 Axes() 沒有參數，補上預設值
    code = re.sub(r"Axes\(\s*\)", "Axes(x_range=[-5,5,1], y_range=[-4,4,1])", code)


    # always_redraw lambda 外部的 .next_to() 無效，移除
    code = re.sub(
        r"(=\s*always_redraw\([^\)]+\))\s*\n(\s+)\w+\.next_to\([^\)]+\)\n",
        r"\1\n",
        code,
    )

    # 強制為所有 Text 物件加上 set_max_width（防止超出畫面）
    def _inject_max_width(code_str):
        out = []
        i = 0
        import re as _re
        src = code_str.splitlines()
        while i < len(src):
            line = src[i]
            out.append(line)
            m = _re.match(r'(\s+)(\w+)\s*=\s*Text\(', line)
            if m:
                indent, varname = m.group(1), m.group(2)
                depth = line.count('(') - line.count(')')
                while depth > 0 and i + 1 < len(src):
                    i += 1
                    out.append(src[i])
                    depth += src[i].count('(') - src[i].count(')')
                peek = src[i + 1].strip() if i + 1 < len(src) else ''
                if 'set_max_width' not in peek:
                    out.append(f"{indent}{varname}.set_max_width(8.0)")
            i += 1
        return '\n'.join(out)
    code = _inject_max_width(code)

    # MathTex / VGroup 加上 set_max_width 保護（透過 scale_to_fit_width 更安全）
    # 對 .scale(N) 超過 1.9 的單一 MathTex，縮到 1.8
    code = re.sub(
        r'(MathTex\([^)]+\))\.scale\(([2-9][\d.]*)\)',
        lambda m: m.group(1) + '.scale(1.8)',
        code,
    )

    return code


def ast_check(code: str) -> tuple[bool, str]:
    """回傳 (ok, error_message)"""
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, f"SyntaxError at line {e.lineno}: {e.msg}"


def get_audio_duration(audio_path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        audio_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except Exception:
        return 3.0


# ══════════════════════════════════════════════
# Step 1：生成腳本
# ══════════════════════════════════════════════
def generate_script(topic: str) -> str:
    print(f"\n[1/6] 生成旁白腳本：{topic}")

    prompt = f"""你是專業的 YouTube Shorts 數學科普腳本寫手。
請用繁體中文為主題「{topic}」寫一段 60 秒旁白腳本。

【結構要求（照順序）】
① Hook（1句）：開場反直覺的衝擊性陳述，讓人想繼續看。例如：「你知道只需要 23 個人，就有超過一半機率兩人同天生日嗎？」
② 直覺挑戰（1句）：說出大多數人的錯誤直覺。
③ 核心概念（2～3句）：用最簡單的語言解釋數學原理，搭配具體數字或公式描述。
④ 驚人數字（1～2句）：給出讓人震驚的具體數值或結論。
⑤ 生活連結（1句）：把數學概念連結到日常生活場景。
⑥ CTA（1句）：固定結尾「追蹤頻道，觀看更多顛覆直覺的數學小知識。」

【格式規則】
- 只輸出旁白文字，每句單獨一行
- 不加序號、標題、括號說明或時間標記
- 每句長度控制在 20～35 字，TTS 讀起來節奏感強
- 總句數 7～9 句"""

    script = gemini(GEMINI_MODEL_SCRIPT, prompt)
    print("  ✅ 腳本生成完成")
    print(f"\n--- 腳本內容 ---\n{script}\n----------------")
    return script


# ══════════════════════════════════════════════
# Step 2：拆分句子
# ══════════════════════════════════════════════
def split_sentences(script: str) -> list:
    lines = [l.strip() for l in script.split("\n") if l.strip()]
    if len(lines) <= 2:
        lines = re.split(r"(?<=[。！？])", script)
        lines = [l.strip() for l in lines if l.strip()]
    print(f"  ✅ 拆分成 {len(lines)} 個句子")
    return lines


# ══════════════════════════════════════════════
# Step 3：每句生成音訊 + 建立時間軸
# ══════════════════════════════════════════════
F5_REF_AUDIO = "/home/ubuntu/math-factory/data/ref_voices/雲健_ref.wav"
F5_REF_TEXT  = "歡迎來到數學小知識，今天我們要聊一個超有趣的數學問題。"

def _num_to_chinese(text: str) -> str:
    """將文字中的阿拉伯數字轉成中文讀法，供 TTS 使用"""
    _DIGITS = "零一二三四五六七八九"
    _UNITS = {1: "十", 2: "百", 3: "千", 4: "萬", 8: "億"}

    def _int_to_cn(n: int) -> str:
        if n < 0:
            return "負" + _int_to_cn(-n)
        if n == 0:
            return "零"
        s = str(n)
        length = len(s)
        parts = []
        zero_flag = False
        for i, ch in enumerate(s):
            d = int(ch)
            pos = length - 1 - i  # 個=0, 十=1, 百=2...
            if d == 0:
                zero_flag = True
            else:
                if zero_flag and parts:
                    parts.append("零")
                    zero_flag = False
                parts.append(_DIGITS[d])
                if pos >= 1:
                    # 處理萬、億
                    if pos >= 8:
                        parts.append("億")
                    elif pos >= 4:
                        parts.append("萬")
                    elif pos in _UNITS:
                        parts.append(_UNITS[pos])
        result = "".join(parts)
        # 一十 → 十
        if result.startswith("一十"):
            result = result[1:]
        return result

    def _replace_match(m):
        s = m.group(0)
        is_pct = s.endswith("%")
        if is_pct:
            s = s[:-1]
        # 小數點
        if "." in s:
            integer, decimal = s.split(".", 1)
            cn_int = _int_to_cn(int(integer)) if integer else "零"
            cn_dec = "".join(_DIGITS[int(c)] for c in decimal)
            result = cn_int + "點" + cn_dec
        else:
            result = _int_to_cn(int(s))
        if is_pct:
            return "百分之" + result
        return result

    return re.sub(r"\d+\.?\d*%?", _replace_match, text)


def _tts_all_f5(sentences: list, seg_dir: str) -> list[str]:
    """用 F5-TTS 依序生成所有 TTS 片段，輸出 MP3"""
    import contextlib, io
    from f5_tts.api import F5TTS
    import soundfile as sf

    import shutil
    if os.path.exists(seg_dir):
        shutil.rmtree(seg_dir)
    os.makedirs(seg_dir)
    os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        tts = F5TTS()
    paths = []
    for i, text in enumerate(sentences):
        wav_path = os.path.join(seg_dir, f"seg_{i:02d}.wav")
        mp3_path = os.path.join(seg_dir, f"seg_{i:02d}.mp3")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            wav, sr, _ = tts.infer(
                ref_file=F5_REF_AUDIO,
                ref_text=F5_REF_TEXT,
                gen_text=_num_to_chinese(text),
                nfe_step=32,
                speed=1.0,
                seed=42,
            )
        sf.write(wav_path, wav, sr)
        subprocess.run(
            ["ffmpeg", "-y", "-i", wav_path, "-q:a", "2", mp3_path],
            capture_output=True, check=True,
        )
        os.remove(wav_path)
        paths.append(mp3_path)
        print(f"  [{i+1}/{len(sentences)}] 生成完成")
    return paths


def generate_timeline(sentences: list, seg_dir: str = "segments") -> list:
    print(f"\n[2/6] 生成 TTS 音訊 + 時間軸（F5-TTS）...")

    paths = _tts_all_f5(sentences, seg_dir)

    timeline = []
    current_time = 0.0
    for i, (sentence, path) in enumerate(zip(sentences, paths)):
        duration = get_audio_duration(path)
        timeline.append({
            "index": i,
            "text": sentence,
            "audio": path,
            "start": current_time + INTRO_DURATION,   # 字幕往後位移片頭時間
            "duration": duration,
            "end": current_time + INTRO_DURATION + duration,
        })
        print(f"  [{i+1}/{len(sentences)}] {sentence[:20]}... → {duration:.1f}s")
        current_time += duration

    print(f"  ✅ 時間軸完成，總長：{current_time:.1f}s")
    return timeline


# ══════════════════════════════════════════════
# Step 4：合併音訊
# ══════════════════════════════════════════════
def merge_audio_segments(timeline: list, output_path: str = "voiceover.mp3") -> str:
    print(f"\n[3/6] 合併配音音訊...")

    list_file = os.path.join(os.path.dirname(timeline[0]["audio"]), "concat_list.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for seg in timeline:
            # 使用絕對路徑避免相對路徑問題
            abs_path = os.path.abspath(seg["audio"])
            f.write(f"file '{abs_path}'\n")

    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", list_file, "-c", "copy", output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ❌ 音訊合併失敗：{result.stderr[-500:]}")
        return None

    print(f"  ✅ 配音儲存：{output_path}")
    return output_path


# ══════════════════════════════════════════════
# Step 5：生成 Manim（結構化 Prompt）
# ══════════════════════════════════════════════
SEGMENT_TEMPLATE = """
        # ══ Segment {idx}: {start:.1f}s–{end:.1f}s (共 {duration:.1f}s) ══
        # 旁白：「{text}」
        # ▼▼▼ 請在此處填入視覺動畫，完全取代下方 self.wait() ▼▼▼
        self.wait({duration:.1f})
        # ▲▲▲ END Segment {idx} ▲▲▲
"""


# 每個 segment 的動畫時間分配（供 AI 參考）
def _seg_timing(seg):
    fadein  = min(1.2, seg["duration"] * 0.3)
    fadeout = 0.4
    hold    = max(0.2, seg["duration"] - fadein - fadeout)
    return fadein, hold, fadeout


_SEGMENT_EXAMPLES = r"""
=== SEGMENT 0（範例，動畫時間 6.5s — 兩行公式，VGroup 居中）===
        grp = VGroup(
            MathTex(r"n = 23").scale(1.8).set_color(YELLOW),
            MathTex(r"P \geq 50.7\%").scale(1.8).set_color(BLUE),
        ).arrange(DOWN, buff=0.7).move_to(UP * 1)
        self.play(LaggedStart(*[Write(m) for m in grp], lag_ratio=0.5), run_time=1.5)
        self.wait(4.6)
        self.play(FadeOut(grp), run_time=0.4)

=== SEGMENT 1（範例，動畫時間 3.5s — 列表，VGroup 置中上方）===
        items = VGroup(
            MathTex(r"n = 10 \Rightarrow P = 11.7\%"),
            MathTex(r"n = 23 \Rightarrow P = 50.7\%"),
            MathTex(r"n = 57 \Rightarrow P = 99\%"),
        ).arrange(DOWN, buff=0.45).scale(1.3).move_to(UP * 0.5)
        self.play(LaggedStart(*[FadeIn(m, shift=LEFT*0.3) for m in items], lag_ratio=0.4), run_time=1.5)
        self.wait(1.6)
        self.play(FadeOut(items), run_time=0.4)

=== SEGMENT 2（範例，動畫時間 5.0s — 動態數字，lambda 內定位）===
        tracker = ValueTracker(0)
        label = always_redraw(
            lambda: MathTex(r"P = " + f"{tracker.get_value():.1f}" + r"\%")
            .scale(2.0).set_color(BLUE).move_to(ORIGIN)
        )
        self.add(label)
        self.play(tracker.animate.set_value(99.9), run_time=2.5)
        self.wait(2.1)
        self.play(FadeOut(label), run_time=0.4)

=== SEGMENT 3（範例，動畫時間 5.0s — 長條圖，先建 VGroup 再定位）===
        ps = [0.117, 0.507, 0.891, 0.990, 0.999]
        bars = VGroup(*[
            Rectangle(width=0.55, height=max(0.1, p * 4.5),
                      color=interpolate_color(BLUE, RED, p),
                      fill_opacity=0.85, stroke_width=0)
            for p in ps
        ]).arrange(RIGHT, buff=0.2).move_to(DOWN * 0.5)
        for b in bars:
            b.align_to(bars.get_bottom(), DOWN)
        self.play(LaggedStart(*[GrowFromEdge(b, DOWN) for b in bars], lag_ratio=0.15), run_time=1.5)
        self.wait(3.1)
        self.play(FadeOut(bars), run_time=0.4)

=== SEGMENT 4（範例，動畫時間 4.0s — 公式 + 高亮框）===
        formula = MathTex(r"\binom{n}{2} = \frac{n(n-1)}{2}").scale(1.5).move_to(ORIGIN)
        box = SurroundingRectangle(formula, color=YELLOW, buff=0.2)
        self.play(Write(formula), run_time=1.0)
        self.play(Create(box), run_time=0.6)
        self.wait(2.0)
        self.play(FadeOut(formula, box), run_time=0.4)

=== SEGMENT 5（範例，動畫時間 5.0s — 參數曲線，ParametricFunction 正確寫法）===
        spiral = ParametricFunction(
            lambda t: np.array([t * np.cos(t) * 0.18, t * np.sin(t) * 0.18, 0]),
            t_range=[0, 4 * PI],
            color=YELLOW, stroke_width=3,
        ).move_to(ORIGIN)
        self.play(Create(spiral), run_time=2.5)
        self.wait(2.1)
        self.play(FadeOut(spiral), run_time=0.4)

=== SEGMENT 6（範例，動畫時間 4.5s — 數線 / 座標軸，NumberLine 正確寫法）===
        nl = NumberLine(x_range=[0, 10, 1], length=6, include_numbers=True).move_to(ORIGIN)
        dot = Dot(color=RED).move_to(nl.n2p(0))
        self.play(Create(nl), run_time=1.0)
        self.play(dot.animate.move_to(nl.n2p(7)), run_time=2.0)
        self.wait(1.1)
        self.play(FadeOut(nl, dot), run_time=0.4)

=== SEGMENT 7（範例，動畫時間 5.5s — 建立跨段持續物件；機率數字放門的正上方）===
        # 建立三扇門，下一段繼續使用
        doors = VGroup(*[
            Rectangle(width=1.6, height=2.8, color=BLUE_D, fill_opacity=0.6)
            for _ in range(3)
        ]).arrange(RIGHT, buff=0.5).move_to(ORIGIN)
        # 標籤用 move_to(get_center())，font_size ≤ 36 避免撐滿整個門
        door_labels = VGroup(*[
            Text(f"門{i+1}", font="Noto Sans CJK TC", font_size=32).move_to(doors[i].get_center())
            for i in range(3)
        ])
        # 機率分數：放門的正上方（UP），不放下方
        prob_1_3 = MathTex(r"\frac{1}{3}", color=YELLOW).scale(1.2).next_to(doors[0], UP, buff=0.5)
        self.play(FadeIn(doors), run_time=0.5)
        self.play(FadeIn(door_labels), run_time=0.5)
        self.play(FadeIn(prob_1_3), run_time=0.5)
        self.wait(3.6)
        # 注意：不 FadeOut，doors / door_labels / prob_1_3 持續到下一段

=== SEGMENT 8（範例，動畫時間 4.5s — 顯示全螢幕比較：先清場再顯示）===
        # 本段要顯示比較，先 FadeOut 所有前段持續物件（doors, labels, prob_1_3）
        self.play(FadeOut(doors, door_labels, prob_1_3), run_time=0.4)
        # 清場後，在乾淨畫面上顯示比較
        keep_grp = VGroup(
            Text("不換：", font="Noto Sans CJK TC", font_size=44, color=BLUE),
            MathTex(r"\frac{1}{3}").scale(1.4).set_color(BLUE),
        ).arrange(RIGHT, buff=0.3)
        switch_grp = VGroup(
            Text("換門：", font="Noto Sans CJK TC", font_size=44, color=GREEN_B),
            MathTex(r"\frac{2}{3}").scale(1.4).set_color(GREEN_B),
        ).arrange(RIGHT, buff=0.3)
        summary = VGroup(keep_grp, switch_grp).arrange(DOWN, buff=0.8).move_to(ORIGIN)
        self.play(FadeIn(summary, shift=UP*0.4), run_time=0.8)
        self.wait(2.9)
        self.play(FadeOut(summary), run_time=0.4)
"""


def _prompt_for_segments(timeline: list, topic: str, error_msg: str = None) -> str:
    """
    讓 AI 只輸出每個 segment 的動畫片段，Python 自行組裝。
    重點：告訴 AI ANIM_TIME（你可以用的動畫秒數），Python 之後會補對齊用的 wait。
    """
    segs_desc = ""
    for seg in timeline:
        fadein, hold, fadeout = _seg_timing(seg)
        # anim_budget = 留給 AI 的動畫時間（扣掉結尾 FadeOut 0.4s）
        anim_budget = max(1.0, seg["duration"] - 0.4)
        segs_desc += (
            f"=== SEGMENT {seg['index']} ===\n"
            f"旁白：「{seg['text']}」\n"
            f"動畫時間：最多 {anim_budget:.1f}s（建議 FadeIn/Write {fadein:.1f}s + hold {hold:.1f}s + FadeOut 0.4s）\n\n"
        )

    error_section = ""
    if error_msg:
        error_section = (
            f"⚠️ 上次渲染錯誤（請修正這個 bug，但格式不變）：\n{error_msg}\n\n"
            f"【重要】請繼續使用 === SEGMENT N === 格式輸出每個片段，"
            f"不要輸出 import / class / def，不要寫完整程式，只輸出片段程式碼。\n\n"
        )

    prompt = f"""你是 Manim CE 動畫專家。請為數學短影片的每個 Segment 生成視覺動畫程式碼片段。

【輸出格式規定（絕對不可違反）】
- 每個 Segment 必須以 === SEGMENT N === 開頭（N 從 0 開始）
- 程式碼用 8 個空格縮排（construct() 方法的內部縮排）
- 嚴禁輸出 import / class / def / from manim import
- 嚴禁輸出完整 Python 模組或完整 Scene 類別
- 只輸出能直接貼進 construct() 內部的程式碼片段

主題：{topic}
{error_section}
以下是每個 Segment 的旁白和可用動畫時間：

{segs_desc}
---

【畫面邊界規則（最重要）】
畫面是 9 × 16 units（直式），座標原點在中心，字幕佔底部 3 units：
- 安全區 X：-3.8 ~ +3.8
- 安全區 Y：-4.5 ~ +7.0（底部留給字幕）
- 單一元素請用 .move_to(ORIGIN) 或 .shift(UP*N) 明確定位
- 多元素必須先 VGroup(...).arrange(DOWN, buff=0.4).move_to(UP*1)
- 嚴禁連鎖 .next_to()（如 A → next_to → B → next_to → C），會累加偏移超出畫面
- .arrange(RIGHT) 容易超出寬度，改用 .arrange(DOWN)
- always_redraw 物件不能用 .next_to()，要在 lambda 內用 .move_to() 指定位置
- 形狀（Rectangle/Square）內的文字標籤：用 .move_to(shape.get_center())，標籤 font_size 不超過 36
- 形狀旁的數字標籤（如機率分數）：一律放在形狀「正上方」用 .next_to(shape, UP, buff=0.5)，絕對禁止放在下方（DOWN 方向距字幕太近）
- 如果多個形狀橫向排列，機率標籤應用 VGroup arrange 或各自用 .next_to(shape, UP) 明確定位，不可疊加 .next_to() chain
- 三個以上物件水平排列時，每個寬度不超過 1.8，buff 不低於 0.5

【時間控制規則】
- run_time 總和不得超過「動畫時間」上限
- 物件必須持續到 segment 結束，FadeOut 放在最後 0.4 秒，中間用 self.wait() 撐滿時長
- 最後一行必須是 self.play(FadeOut(...), run_time=0.4) 清場
- FadeOut 之後不要加 self.wait()
- 嚴禁畫面空白：每個 segment 從頭到尾必須有物件在畫面上

【跨段物件重用規則（重要）】
- 所有 Segment 都在同一個 construct() 函數裡執行，前面 Segment 定義的變數在後面 Segment 都直接可用
- 若某個視覺元素（如一組門、一條數線）需要在多個連續 Segment 中出現，在第一個 Segment 建立後不要 FadeOut，後面的 Segment 直接操作同一個變數
- 嚴禁在後面 Segment 重新建立前面 Segment 已建立的物件（會造成畫面物件重疊和閃爍）
- 只有在物件真的不再需要時才 FadeOut（通常是最後一個使用它的 Segment 結尾）
- 請參考範例 SEGMENT 7–8 的跨段寫法

【畫面清場規則（防止文字堆疊）】
- 若當前 Segment 要顯示「全螢幕比較/總結」（如 A vs B、換門=2/3 不換=1/3），必須先在本段開頭 FadeOut 所有前段的持續物件，再顯示新內容
- 每個 Segment 結束時，該段「自己新增」的元素必須 FadeOut（self. 跨段物件除外）
- 嚴禁：在還有大量舊物件的畫面上，再疊加新的文字或圖形（會造成多層文字重疊看不清）
- 正確做法示範：`self.play(FadeOut(self.doors, self.door_labels, *all_prev_elements), run_time=0.4)` 先清場，然後再 FadeIn 新的比較元素

【其他規則】
- 輸出格式嚴格遵守：每段必須以 === SEGMENT N === 開頭（N 從 0 開始），後接 8 空格縮排的程式碼
- 所有 {len(timeline)} 個 Segment 都必須輸出，不可省略或合併
- MathTex() 只放純 LaTeX 數學式，不放中文字，也不用 \\text{{...}} 顯示純文字
- 要顯示純文字（包含英文專有名詞）一律用 Text("...", font="Noto Sans CJK TC")，不用 MathTex
- Text 物件不用 Write()，改用 FadeIn() 或 LaggedStart，避免筆畫抖動
- 公式 .scale(1.4) ~ .scale(1.8)，Text 不超過 .scale(1.5)
- 只用 self.wait()，不用 Wait()
- 不要輸出 import / class / def

【參考範例 — 依動畫時間選合適的模式】
{_SEGMENT_EXAMPLES}

---
現在請生成所有 {len(timeline)} 個 Segment 的程式碼：
"""
    return prompt


def _parse_segment_snippets(raw: str, timeline: list) -> tuple[str, dict[int, str]]:
    """
    從 AI 回應中解析各 segment 的程式碼片段。
    回傳 (preamble_code, {segment_index: code_snippet})
    preamble_code：第一個 === SEGMENT === 之前的程式碼（AI 有時把初始化放這裡）
    """
    # 清理 markdown
    raw = re.sub(r"```python\s*", "", raw)
    raw = re.sub(r"```\s*", "", raw)

    snippets = {}
    # 用 === SEGMENT N === 分割
    parts = re.split(r"===\s*SEGMENT\s+(\d+)[^=]*===", raw)
    # parts[0] 是前導區（AI 可能把 self.xxx = VGroup() 等初始化放這裡）
    # 過濾掉 import/class/def/from 等不合法行，保留 self.xxx 初始化
    preamble_raw = parts[0] if parts else ""
    preamble_lines = []
    for line in preamble_raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("import ", "from ", "class ", "def ")):
            continue
        if stripped.startswith("#"):
            continue
        preamble_lines.append(line)
    preamble = "\n".join(preamble_lines)

    i = 1
    while i + 1 < len(parts):
        idx = int(parts[i])
        # 移除頭尾空行，但保留第一行的縮排（不用 .strip()，否則第一行縮排被吃掉
        # 導致 min_indent=0 使後續行的相對縮排計算錯誤）
        lines = parts[i + 1].splitlines()
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        code = '\n'.join(lines)
        if idx < len(timeline):
            snippets[idx] = code
        i += 2

    return preamble, snippets


def _estimate_snippet_duration(snippet: str) -> float:
    """
    估算 snippet 的動畫總時長（把所有 run_time 和 self.wait 加總）。
    用來計算需要補多少 padding wait。
    """
    total = 0.0
    for m in re.finditer(r"run_time\s*=\s*([\d.]+)", snippet):
        total += float(m.group(1))
    for m in re.finditer(r"self\.wait\s*\(\s*([\d.]+)\s*\)", snippet):
        total += float(m.group(1))
    return total


def _assemble_manim_code(timeline: list, snippets: dict[int, str], preamble: str = "") -> str:
    """
    將 AI 生成的 snippets 插入骨架，組裝成完整的 Manim 程式碼。
    每段自動補上 padding wait，確保動畫時長與配音對齊。
    """
    header = (
        "from manim import *\n\n"
        "config.pixel_width = 1080\n"
        "config.pixel_height = 1920\n"
        "config.frame_width = 9\n"
        "config.frame_height = 16\n"
        "config.frame_rate = 30\n\n"
        "# 安全區：X ±3.8，Y -4.5（底部留字幕）~ +7.0\n"
        "SAFE_W = 7.8\n"
        "SAFE_TOP = 7.0\n"
        "SAFE_BOT = -4.5\n\n"
        "def constrain(mob):\n"
        "    if getattr(mob, '_is_bg', False):\n"
        "        return mob\n"
        "    try:\n"
        "        w, h = mob.width, mob.height\n"
        "        if w == 0 or h == 0:\n"
        "            return mob\n"
        "        if w > SAFE_W:\n"
        "            mob.scale_to_fit_width(SAFE_W)\n"
        "        if mob.height > (SAFE_TOP - SAFE_BOT - 0.5):\n"
        "            mob.scale_to_fit_height(SAFE_TOP - SAFE_BOT - 0.5)\n"
        "        top = mob.get_top()[1]\n"
        "        bot = mob.get_bottom()[1]\n"
        "        right = mob.get_right()[0]\n"
        "        left = mob.get_left()[0]\n"
        "        if top > SAFE_TOP:\n"
        "            mob.shift(DOWN * (top - SAFE_TOP))\n"
        "        if bot < SAFE_BOT:\n"
        "            mob.shift(UP * (SAFE_BOT - bot))\n"
        "        if right > SAFE_W / 2:\n"
        "            mob.shift(LEFT * (right - SAFE_W / 2))\n"
        "        if left < -SAFE_W / 2:\n"
        "            mob.shift(RIGHT * (-SAFE_W / 2 - left))\n"
        "    except Exception:\n"
        "        pass\n"
        "    return mob\n\n"
        "class MathShort(Scene):\n"
        "    def _constrain_all(self):\n"
        "        # 跳過有 updater 的物件（always_redraw）：\n"
        "        # constrain 會在 play() 後縮小它，但下一幀 lambda 重建回原大小 → 突然放大\n"
        "        for m in list(self.mobjects):\n"
        "            if not getattr(m, '_is_bg', False) and not m.get_updaters():\n"
        "                constrain(m)\n"
        "    def play(self, *args, **kwargs):\n"
        "        super().play(*args, **kwargs)\n"
        "        self._constrain_all()\n\n"
        "    def construct(self):\n"
        "        self.camera.background_color = \"#0d1b2a\"\n"
        "        bg = Rectangle(width=9, height=16).set_fill(\n"
        "            color=[\"#0d1b2a\", \"#1b2a4a\"], opacity=1).set_stroke(width=0)\n"
        "        bg._is_bg = True\n"
        "        super().add(bg)\n"
        "        # ── 片頭 ──\n"
        "        dot = Dot(radius=0.18, color=YELLOW).move_to(ORIGIN)\n"
        "        ring = Circle(radius=0.18, color=YELLOW, stroke_width=3)\n"
        "        self.add(dot)\n"
        "        self.play(ring.animate.scale(18).set_opacity(0), run_time=0.6, rate_func=rush_from)\n"
        "        self.remove(dot, ring)\n"
    )

    # 插入前導區（AI 在 SEGMENT 0 之前寫的初始化程式碼）
    body = ""
    if preamble.strip():
        lines = preamble.splitlines()
        code_lines = [l for l in lines if l.strip() and not l.lstrip().startswith("#")]
        non_empty = code_lines if code_lines else [l for l in lines if l.strip()]
        min_indent = min((len(l) - len(l.lstrip()) for l in non_empty), default=0)
        normalized = []
        for line in lines:
            if line.strip():
                extra = len(line) - len(line.lstrip()) - min_indent
                normalized.append(" " * (8 + max(0, extra)) + line.lstrip())
            else:
                normalized.append("")
        body += "        # ── AI 前導初始化 ──\n"
        body += "\n".join(normalized) + "\n\n"

    for seg in timeline:
        idx = seg["index"]
        body += f"\n        # ── Segment {idx}: {seg['text'][:30]} ──\n"
        if idx in snippets and snippets[idx].strip():
            snippet = snippets[idx]

            # 計算 AI 動畫時長，補上 padding 讓此段對齊配音時長
            used = _estimate_snippet_duration(snippet)
            padding = max(0.0, seg["duration"] - used)

            # 保留相對縮排，只把最小縮排對齊到 8 空格
            # 只用「非空、非純注釋」的行計算 min_indent，避免 AI 加的 # Note: 把最小縮排拉到 0
            lines = snippet.splitlines()
            code_lines = [l for l in lines if l.strip() and not l.lstrip().startswith("#")]
            non_empty = code_lines if code_lines else [l for l in lines if l.strip()]
            min_indent = min((len(l) - len(l.lstrip()) for l in non_empty), default=0)
            target_indent = 8
            normalized = []
            for line in lines:
                if line.strip() == "":
                    normalized.append("")
                else:
                    current = len(line) - len(line.lstrip())
                    extra = current - min_indent      # 相對縮排量
                    normalized.append(" " * (target_indent + extra) + line.lstrip())
            body += "\n".join(normalized) + "\n"

            if padding > 0.05:
                body += f"        self.wait({padding:.2f})  # timing padding\n"
        else:
            body += f"        self.wait({seg['duration']:.1f})\n"

    # ── 片尾 ──
    body += (
        "\n        # ── 片尾 ──\n"
        "        outro = VGroup(\n"
        "            Text('數學小知識', font='Noto Sans CJK TC', font_size=52).set_color(YELLOW),\n"
        "            Text('每週更新', font='Noto Sans CJK TC', font_size=36).set_color(WHITE),\n"
        "        ).arrange(DOWN, buff=0.3).move_to(UP * 1)\n"
        "        self.play(FadeIn(outro, shift=UP*0.4), run_time=0.6)\n"
        "        self.wait(1.5)\n"
        "        self.play(FadeOut(outro), run_time=0.4)\n"
        "        self.wait(3.0)  # end buffer\n"
    )

    return header + body


def generate_manim_code(timeline: list, topic: str, error_msg: str = None) -> str:
    print(f"  [生成中] Manim 動畫程式碼（片段策略）...")

    prompt = _prompt_for_segments(timeline, topic, error_msg)
    raw = gemini(GEMINI_MODEL_MANIM, prompt, timeout=180)

    # debug：印出 AI 回應前 300 字
    preview = raw[:300]
    print(f"  AI 回應預覽：{preview}...")

    preamble, snippets = _parse_segment_snippets(raw, timeline)
    print(f"  解析到 {len(snippets)}/{len(timeline)} 個 Segment 片段")
    if preamble.strip():
        print(f"  前導區程式碼：{len(preamble.splitlines())} 行（已納入組裝）")

    if len(snippets) == 0:
        print("  ⚠️  無法解析任何片段，跳至備用")
        return None

    code = _assemble_manim_code(timeline, snippets, preamble)
    code = fix_manim_code(code)

    # 統計有幾個 segment 有真實動畫
    play_count = len(re.findall(r"self\.play\s*\(", code))
    print(f"  self.play() 共 {play_count} 次，覆蓋 {len(snippets)} 個 Segment")

    return code


# ══════════════════════════════════════════════
# Step 5b：渲染（含 AST 驗證 + 自動重試）
# ══════════════════════════════════════════════
MANIM_FILE = "math_short.py"
CLASS_NAME = "MathShort"


def _find_output_mp4(combined_output: str) -> str | None:
    """從 Manim 的 stdout+stderr 動態抓輸出路徑，不依賴硬寫的 quality map"""
    # 優先從 "File ready at '...'" 抓
    m = re.search(r"File ready at\s+'([^']+\.mp4)'", combined_output)
    if m:
        path = m.group(1).strip()
        if os.path.exists(path):
            return path

    # 備用：glob 找最新的 mp4
    import glob
    candidates = glob.glob("media/videos/math_short/**/*.mp4", recursive=True)
    if candidates:
        return max(candidates, key=os.path.getmtime)

    return None


def render_manim_with_retry(timeline: list, topic: str, quality: str = "h") -> str:
    print(f"\n[4/6] Manim 程式碼生成 + 渲染（最多 {MAX_RETRIES} 次）...")

    # 清除 media/Tex/ 內的子目錄（如 .ipynb_checkpoints）
    # Manim 的 delete_nonsvg_files() 對目錄呼叫 f.unlink() 會 IsADirectoryError
    import shutil
    tex_dir = "media/Tex"
    if os.path.isdir(tex_dir):
        for item in os.scandir(tex_dir):
            if item.is_dir():
                shutil.rmtree(item.path, ignore_errors=True)

    error_msg = None

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n  嘗試 {attempt}/{MAX_RETRIES}...")

        code = generate_manim_code(timeline, topic, error_msg)
        if code is None:
            if attempt < MAX_RETRIES:
                print("  → 重新生成...")
                continue
            print("  ❌ 全部重試失敗（Gemini 無法解析），停止執行")
            raise SystemExit(1)

        # ── AST 語法檢查（省掉渲染時間）──
        ok, syntax_err = ast_check(code)
        if not ok:
            print(f"  ❌ AST 語法錯誤：{syntax_err}")
            # 印出錯誤行附近的程式碼，方便排查
            m_line = re.search(r"line (\d+)", syntax_err)
            if m_line:
                err_ln = int(m_line.group(1))
                lines = code.splitlines()
                for i in range(max(0, err_ln - 4), min(len(lines), err_ln + 2)):
                    marker = ">>>" if i + 1 == err_ln else "   "
                    print(f"  {marker} {i+1:3d}: {lines[i]}")
            error_msg = f"Python SyntaxError: {syntax_err}"
            continue

        # 儲存此次嘗試的程式碼（供失敗時診斷）
        debug_path = f"math_short_attempt{attempt}.py"
        with open(MANIM_FILE, "w", encoding="utf-8") as f:
            f.write(code)
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(code)

        import time as _t
        _render_start = _t.time()
        print("  [渲染中]", flush=True)

        _manim_env = {k: v for k, v in os.environ.items() if k != "PYTHONHASHSEED"}
        proc = subprocess.Popen(
            ["manim", f"-pq{quality}", MANIM_FILE, CLASS_NAME],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=_manim_env,
        )
        output_lines = []
        _last_print = _render_start
        for raw_line in proc.stdout:
            output_lines.append(raw_line)
            now = _t.time()
            if now - _last_print >= 30:
                print(f"  [{now - _render_start:.0f}s] 渲染中...", flush=True)
                _last_print = now
        proc.wait()

        _render_elapsed = _t.time() - _render_start
        print(f"  [渲染完成] 耗時 {_render_elapsed:.1f}s", flush=True)
        combined = "".join(output_lines)

        if proc.returncode == 0:
            mp4 = _find_output_mp4(combined)
            if mp4:
                print(f"  ✅ 渲染成功：{mp4}")
                return mp4

        # 完整錯誤（去 ANSI，取最後 2500 字）
        raw_err = re.sub(r"\x1b\[[0-9;]*m", "", combined)
        # 找出 Traceback 起始位置
        tb_pos = raw_err.rfind("Traceback")
        if tb_pos == -1:
            tb_pos = max(0, len(raw_err) - 2500)
        error_snippet = raw_err[tb_pos:tb_pos + 2500]
        print(f"  ❌ 渲染失敗（存於 {debug_path}）：\n{error_snippet[:600]}")

        # 只抽出關鍵錯誤行（最後一個 XxxError: ... 行），避免完整 traceback 讓 AI 重寫整個結構
        key_err = error_snippet.strip().splitlines()[-1]  # 通常是 "SomeError: message"
        # 再往前找有沒有 "File ... line N" 提供位置
        loc_line = ""
        for ln in reversed(error_snippet.splitlines()):
            if "File" in ln and "line" in ln:
                loc_line = ln.strip()
                break
        error_msg = f"{loc_line}\n{key_err}".strip() if loc_line else key_err

    print("\n  ❌ 全部重試失敗，停止執行")
    print("  診斷檔：math_short_attempt1.py / attempt2.py / attempt3.py")
    raise SystemExit(1)


# ══════════════════════════════════════════════
# 備用：固定模板（逐句顯示，自動換行）
# ══════════════════════════════════════════════
def render_fallback(timeline: list, quality: str = "h") -> str:
    print("\n  使用備用模板渲染（基礎動畫版）...")

    # 備用模板：每段顯示一個問號或省略號的脈衝動畫，至少有視覺動態
    scenes = ""
    for seg in timeline:
        fadein, hold, fadeout = _seg_timing(seg)
        # 用段落索引產生不同的幾何形狀，讓畫面不要全空白
        shape_idx = seg["index"] % 4
        if shape_idx == 0:
            shape_code = "        shape = Circle(radius=1.5, color=BLUE, fill_opacity=0.2)"
        elif shape_idx == 1:
            shape_code = "        shape = Square(side_length=3.0, color=GREEN, fill_opacity=0.2)"
        elif shape_idx == 2:
            shape_code = "        shape = RegularPolygon(n=6, color=PURPLE, fill_opacity=0.2).scale(1.8)"
        else:
            shape_code = "        shape = Triangle(color=ORANGE, fill_opacity=0.2).scale(2.0)"

        scenes += f"""
        # Segment {seg["index"]}: {seg["text"][:20]}
{shape_code}
        self.play(FadeIn(shape, scale=0.8), run_time={fadein:.1f})
        self.wait({hold:.1f})
        self.play(FadeOut(shape), run_time={fadeout:.1f})
"""

    fallback_code = f"""from manim import *

config.pixel_width = 1080
config.pixel_height = 1920
config.frame_width = 9
config.frame_height = 16
config.frame_rate = 30

SAFE_W = 7.8
SAFE_TOP = 7.0
SAFE_BOT = -4.5

def constrain(mob):
    if getattr(mob, '_is_bg', False):
        return mob
    try:
        w, h = mob.width, mob.height
        if w == 0 or h == 0:
            return mob
        if w > SAFE_W:
            mob.scale_to_fit_width(SAFE_W)
        if mob.height > (SAFE_TOP - SAFE_BOT - 0.5):
            mob.scale_to_fit_height(SAFE_TOP - SAFE_BOT - 0.5)
        top = mob.get_top()[1]
        bot = mob.get_bottom()[1]
        right = mob.get_right()[0]
        left = mob.get_left()[0]
        if top > SAFE_TOP:
            mob.shift(DOWN * (top - SAFE_TOP))
        if bot < SAFE_BOT:
            mob.shift(UP * (SAFE_BOT - bot))
        if right > SAFE_W / 2:
            mob.shift(LEFT * (right - SAFE_W / 2))
        if left < -SAFE_W / 2:
            mob.shift(RIGHT * (-SAFE_W / 2 - left))
    except Exception:
        pass
    return mob

class MathShort(Scene):
    def _constrain_all(self):
        for m in list(self.mobjects):
            if not getattr(m, '_is_bg', False) and not m.get_updaters():
                constrain(m)
    def play(self, *args, **kwargs):
        super().play(*args, **kwargs)
        self._constrain_all()

    def construct(self):
        self.camera.background_color = "#0d1b2a"
        bg = Rectangle(width=9, height=16).set_fill(
            color=["#0d1b2a", "#1b2a4a"], opacity=1).set_stroke(width=0)
        bg._is_bg = True
        super().add(bg)
{scenes}
"""

    with open(MANIM_FILE, "w", encoding="utf-8") as f:
        f.write(fallback_code)

    _manim_env = {k: v for k, v in os.environ.items() if k != "PYTHONHASHSEED"}
    result = subprocess.run(
        ["manim", f"-pq{quality}", MANIM_FILE, CLASS_NAME],
        capture_output=True, text=True, env=_manim_env,
    )

    combined = result.stdout + result.stderr
    mp4 = _find_output_mp4(combined)
    if mp4:
        print(f"  ✅ 備用模板完成：{mp4}")
        return mp4

    print(f"  ❌ 備用模板也失敗：{result.stderr[-500:]}")
    return None


# ══════════════════════════════════════════════
# Step 6：FFmpeg 合併影片 + 配音 + 字幕燒入
# ══════════════════════════════════════════════
def _build_subtitle_filter(timeline: list) -> str:
    """
    用 FFmpeg drawtext 把字幕燒入影片。
    字幕樣式：白字 + 半透明黑底框，提升可讀性。
    """
    W, H = 1080, 1920
    FONT_SIZE = 46
    MAX_CHARS_PER_LINE = 16
    MARGIN_BOTTOM = 220
    LINE_SPACING = 62
    FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    BOX_PADDING = 14          # 背景框內距（px）

    def wrap(text, n=MAX_CHARS_PER_LINE):
        return [text[i:i+n] for i in range(0, len(text), n)]

    filters = []
    for seg in timeline:
        lines = wrap(seg["text"])
        num_lines = len(lines)
        block_top = H - MARGIN_BOTTOM - (num_lines - 1) * LINE_SPACING
        enable = f"between(t,{seg['start']:.3f},{seg['end']:.3f})"

        for li, line in enumerate(lines):
            y = block_top + li * LINE_SPACING
            safe = (line
                    .replace("'", "\u2019")
                    .replace(":", "\\:")
                    .replace("\\", "\\\\"))
            filters.append(
                f"drawtext=fontfile='{FONT_PATH}'"
                f":text='{safe}'"
                f":fontsize={FONT_SIZE}"
                f":fontcolor=white"
                f":borderw=2:bordercolor=black@0.6"
                f":box=1:boxcolor=black@0.45:boxborderw={BOX_PADDING}"
                f":x=(w-text_w)/2"
                f":y={y}"
                f":enable='{enable}'"
            )

    return ",".join(filters)


def merge_video_audio(
    video_path: str,
    audio_path: str,
    timeline: list,
    output_path: str = "final_output.mp4",
) -> str:
    print("\n[5/6] 合併影片 + 配音 + 字幕燒入...")

    audio_duration = get_audio_duration(audio_path)
    subtitle_filter = _build_subtitle_filter(timeline)

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-map", "0:v:0", "-map", "1:a:0",
        "-vf", subtitle_filter,
        "-c:v", "libx264", "-preset", "slow", "-b:v", "8M", "-maxrate", "10M", "-bufsize", "16M",
        "-c:a", "aac", "-b:a", "192k",
        "-t", f"{audio_duration:.3f}",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ❌ FFmpeg 錯誤：{result.stderr[-800:]}")
        return None

    print(f"  ✅ 最終影片（含字幕）：{output_path}")
    return output_path


# ══════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════
if __name__ == "__main__":
    import time as _time

    def _step(label):
        _step.t = _time.time()
        print(f"\n{label}")

    def _done():
        elapsed = _time.time() - _step.t
        print(f"  ✓ {elapsed:.1f}s")
        _step.t = _time.time()

    _step.t = _time.time()
    _total_start = _time.time()

    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", default=os.environ.get("TOPIC", "巴塞爾問題"))
    args = parser.parse_args()
    TOPIC = args.topic
    # 切換到 tmp/ 工作目錄（segments/、media/、math_short.py 都放在這裡）
    os.makedirs(os.path.join(WORK_DIR, "tmp"), exist_ok=True)
    os.chdir(os.path.join(WORK_DIR, "tmp"))

    os.makedirs(os.path.join(WORK_DIR, "output"), exist_ok=True)
    OUTPUT_NAME = os.path.join(WORK_DIR, "output", f"output_{TOPIC.replace(' ', '_')}.mp4")

    print("══════════════════════════════════════")
    print(" 自媒體工廠 v4 — 結構化生成 + AST 驗證")
    print(f" 主題：{TOPIC}")
    print("══════════════════════════════════════")

    script   = generate_script(TOPIC); _done()
    sentences = split_sentences(script)
    timeline  = generate_timeline(sentences); _done()
    audio     = merge_audio_segments(timeline); _done()
    video     = render_manim_with_retry(timeline, TOPIC); _done()

    if video and audio:
        final = merge_video_audio(video, audio, timeline, OUTPUT_NAME); _done()
        print(f"\n完成！最終檔案：{final}")
    elif video:
        print(f"\n動畫完成（無配音）：{video}")
    else:
        print("\n❌ 流程中斷")

    total = _time.time() - _total_start
    print(f"\n總耗時：{total//60:.0f}m {total%60:.0f}s")
    print("\n══════════════════════════════════════")
    print(f" 下載 {OUTPUT_NAME} 後上傳 YouTube")
    print("══════════════════════════════════════")
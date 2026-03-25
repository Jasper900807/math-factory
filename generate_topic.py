#!/usr/bin/env python3
"""
使用 Gemini 自動生成數學主題，避免重複已生產的主題。
輸出：單行主題字串到 stdout

topics_done.txt 格式（每行二選一）：
  主題名稱                    ← 舊格式，向下相容
  主題名稱|ok|2026-03-25      ← 新格式，包含品質與日期
"""
import os
import sys

from google import genai

DONE_FILE = "/home/ubuntu/math-factory/data/topics_done.txt"

CATEGORY_PROMPTS = {
    "熱門": (
        "主題要令人驚訝、有強烈反直覺性，適合大眾觀看，不需數學背景。"
        "例如：生日悖論、蒙提霍爾問題、布雷斯悖論、無限猴子定理、碎形海岸線。"
    ),
    "深度": (
        "主題可以稍深，但仍要有趣、有故事性，適合對數學有興趣的觀眾。"
        "例如：黎曼假設、哥德爾不完備定理、康托爾的無限、P vs NP、四色定理。"
    ),
}


def load_done_topics() -> list[str]:
    """讀取已完成主題清單，相容新舊格式。"""
    if not os.path.exists(DONE_FILE):
        return []
    topics = []
    with open(DONE_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                topics.append(line.split("|")[0])  # 新格式取第一欄
    return topics


def get_next_category(done_count: int) -> str:
    """依已完成數量交替選擇主題類別：偶數→熱門，奇數→深度。"""
    return "熱門" if done_count % 2 == 0 else "深度"


def generate_topic() -> str:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: 找不到 GEMINI_API_KEY", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    done = load_done_topics()
    done_str = "、".join(done[-50:]) if done else "（無）"
    category = get_next_category(len(done))
    category_hint = CATEGORY_PROMPTS[category]

    prompt = f"""你是數學知識 YouTube Shorts 的選題專員。
請生成一個適合製作成 60 秒影片的數學主題。

本次類別：【{category}】
{category_hint}

其他要求：
- 主題名稱用繁體中文，5–15 字以內
- 只輸出主題名稱，不要任何解釋或標點符號

已生產過的主題（請勿重複）：{done_str}

請輸出一個新的【{category}】類主題："""

    response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
    topic = response.text.strip().strip("「」『』【】。，、")
    return topic


if __name__ == "__main__":
    topic = generate_topic()
    print(topic)

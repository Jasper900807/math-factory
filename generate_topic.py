#!/usr/bin/env python3
"""
使用 Gemini 自動生成數學主題，避免重複已生產的主題。
輸出：單行主題字串到 stdout
"""
import os
import sys
import json

from google import genai

DONE_FILE = "/home/ubuntu/math-factory/topics_done.txt"

def load_done_topics():
    if not os.path.exists(DONE_FILE):
        return []
    with open(DONE_FILE, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def generate_topic():
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: 找不到 GEMINI_API_KEY", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    done = load_done_topics()
    done_str = "、".join(done[-50:]) if done else "（無）"

    prompt = f"""你是數學知識 YouTube Shorts 的選題專員。
請生成一個適合製作成 60 秒影片的數學主題。

要求：
- 主題要有趣、有反直覺性或令人驚訝的特性
- 適合大眾觀看，不需要太深的數學背景
- 主題名稱用繁體中文，5-15 字以內
- 只輸出主題名稱，不要任何解釋

已生產過的主題（請勿重複）：{done_str}

請輸出一個新主題："""

    response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
    topic = response.text.strip().strip("「」『』【】")
    return topic

if __name__ == "__main__":
    topic = generate_topic()
    print(topic)

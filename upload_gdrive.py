#!/usr/bin/env python3
"""
上傳影片到 Google Drive 指定資料夾（使用 rclone）。
用法：python upload_gdrive.py <video_path> [display_name]
"""
import sys
import os
import subprocess

GDRIVE_FOLDER = "gdrive:影片"  # rclone remote:資料夾名稱


def upload(video_path: str, display_name: str = None):
    if not os.path.exists(video_path):
        print(f"ERROR: 找不到檔案 {video_path}", file=sys.stderr)
        sys.exit(1)

    size_mb = os.path.getsize(video_path) // 1024 // 1024
    name = display_name or os.path.basename(video_path)
    print(f"  上傳中：{name} ({size_mb} MB) → {GDRIVE_FOLDER}")

    # 用 rclone copyto 上傳並指定目標檔名
    dest = f"{GDRIVE_FOLDER}/{name}"
    result = subprocess.run(
        ["rclone", "copyto", video_path, dest, "--progress"],
        capture_output=False,
        text=True,
    )

    if result.returncode == 0:
        print(f"  ✅ 上傳完成：{dest}")
    else:
        print(f"  ERROR: 上傳失敗（exit {result.returncode}）", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python upload_gdrive.py <video_path> [display_name]")
        sys.exit(1)
    path = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else None
    upload(path, name)

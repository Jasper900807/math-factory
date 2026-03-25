#!/usr/bin/env python3
"""
Math Factory Discord Bot
指令：!status, !start, !stop, !log, !topic <主題>, !queue
"""
import asyncio
import os
import subprocess
import discord
from discord.ext import commands

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN 環境變數未設定，請先 export DISCORD_BOT_TOKEN=...")
WORK_DIR = "/home/ubuntu/math-factory"
VENV_PYTHON = "/home/ubuntu/.venv/bin/python"
WATCHER_SCRIPT = f"{WORK_DIR}/scripts/sandbox_watcher.sh"
PRODUCER_SCRIPT = f"{WORK_DIR}/scripts/auto_producer.sh"
PRODUCER_LOG = f"{WORK_DIR}/logs/auto_producer.log"
WATCHER_LOG = f"{WORK_DIR}/logs/watcher.log"
DONE_FILE = f"{WORK_DIR}/data/topics_done.txt"
TOPIC_TIMEOUT_MIN = 40

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


def run_cmd(cmd: str) -> str:
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
    return (result.stdout + result.stderr).strip()


def is_running(name: str) -> bool:
    result = subprocess.run(
        ["pgrep", "-f", f"bash.*{name}"], capture_output=True
    )
    return result.returncode == 0


@bot.event
async def on_ready():
    print(f"[Bot] 已上線：{bot.user}")


@bot.command(name="status")
async def cmd_status(ctx):
    watcher = "🟢 運行中" if is_running("sandbox_watcher.sh") else "🔴 停止"
    producer = "🟢 運行中" if is_running("auto_producer.sh") else "🔴 停止"

    # 最新 log 3 行
    log_tail = run_cmd(f"tail -3 {PRODUCER_LOG} 2>/dev/null || echo '（無 log）'")

    # 最新影片
    latest = run_cmd(f"ls -t {WORK_DIR}/output_*.mp4 2>/dev/null | head -1")
    latest_name = os.path.basename(latest) if latest else "（無）"

    embed = discord.Embed(title="📊 Math Factory 狀態", color=0x00bfff)
    embed.add_field(name="Watcher", value=watcher, inline=True)
    embed.add_field(name="Producer", value=producer, inline=True)
    embed.add_field(name="最新影片", value=latest_name, inline=False)
    embed.add_field(name="最新 Log", value=f"```{log_tail}```", inline=False)
    await ctx.send(embed=embed)


@bot.command(name="start")
async def cmd_start(ctx):
    watcher_running = is_running("sandbox_watcher.sh")
    producer_running = is_running("auto_producer.sh")

    if watcher_running and producer_running:
        await ctx.send("⚠️ 已經在運行中")
        return

    if not watcher_running:
        subprocess.Popen(
            f"nohup bash {WATCHER_SCRIPT} >> {WORK_DIR}/watcher.log 2>&1 &",
            shell=True
        )
    if not producer_running:
        subprocess.Popen(
            f"nohup bash {PRODUCER_SCRIPT} >> {PRODUCER_LOG} 2>&1 &",
            shell=True
        )

    await ctx.send("✅ 已啟動生產線！使用 `!status` 確認狀態")


@bot.command(name="stop")
async def cmd_stop(ctx):
    run_cmd("pkill -f 'bash.*auto_producer.sh'")
    run_cmd("pkill -f 'bash.*sandbox_watcher.sh'")
    await ctx.send("🛑 已停止生產線")


@bot.command(name="log")
async def cmd_log(ctx, lines: int = 15):
    lines = min(lines, 30)
    log = run_cmd(f"tail -{lines} {PRODUCER_LOG} 2>/dev/null || echo '（無 log）'")
    if len(log) > 1900:
        log = log[-1900:]
    await ctx.send(f"```\n{log}\n```")


async def _watch_production(channel: discord.TextChannel, topic: str):
    """背景任務：輪詢 watcher.log，生產完成時推播通知。"""
    # 以觸發前的行數為基準，只看新增的行
    try:
        with open(WATCHER_LOG, encoding="utf-8") as f:
            baseline = sum(1 for _ in f)
    except FileNotFoundError:
        baseline = 0

    deadline = asyncio.get_event_loop().time() + TOPIC_TIMEOUT_MIN * 60

    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(15)
        try:
            with open(WATCHER_LOG, encoding="utf-8") as f:
                new_lines = f.readlines()[baseline:]
        except FileNotFoundError:
            continue

        new_text = "".join(new_lines)

        if "factory_v4.py done" in new_text:
            quality_match = next(
                (l for l in new_lines if "[QUALITY]" in l), ""
            )
            quality = quality_match.split("[QUALITY]")[-1].strip() if quality_match else "ok"
            icon = QUALITY_ICON.get(quality, "✅")
            embed = discord.Embed(
                title=f"{icon} 影片生產完成",
                description=f"**{topic}**",
                color=0x00ff99 if quality == "ok" else 0xffa500,
            )
            embed.set_footer(text=f"品質：{quality}")
            await channel.send(embed=embed)
            return

        if "factory_v4.py FAILED" in new_text:
            await channel.send(f"❌ **{topic}** 生產失敗，請用 `!log` 查看原因")
            return

    await channel.send(f"⏱️ **{topic}** 等待逾時（{TOPIC_TIMEOUT_MIN} 分鐘），請用 `!log` 確認狀態")


@bot.command(name="topic")
async def cmd_topic(ctx, *, topic: str):
    if not topic:
        await ctx.send("用法：`!topic 黎曼假設`")
        return

    await ctx.send(f"⏳ 正在觸發主題：**{topic}**")

    # 寫入 trigger
    tmp = "/tmp/discord_trigger.txt"
    with open(tmp, "w") as f:
        f.write(topic)

    # 上傳到 sandbox
    result = run_cmd(
        f"ssh openshell-my-assistant 'rm -f /sandbox/trigger.txt' 2>/dev/null; "
        f"openshell sandbox upload my-assistant {tmp} /sandbox/trigger.txt"
    )
    await ctx.send(f"✅ 已觸發！生產完成後會自動通知\n```{result[:200]}```")

    # 啟動背景監控
    asyncio.create_task(_watch_production(ctx.channel, topic))


QUALITY_ICON = {"ok": "✅", "fallback": "⚠️", "failed": "❌", "timeout": "⏱️"}

@bot.command(name="queue")
async def cmd_queue(ctx):
    if not os.path.exists(DONE_FILE):
        await ctx.send("📋 尚無已完成主題")
        return
    with open(DONE_FILE, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    recent = lines[-20:]

    rows = []
    ok_count = fallback_count = failed_count = 0
    for i, line in enumerate(recent):
        parts = line.split("|")
        topic = parts[0]
        quality = parts[1] if len(parts) > 1 else "ok"
        icon = QUALITY_ICON.get(quality, "✅")
        rows.append(f"{icon} {i+1}. {topic}")
        if quality == "ok": ok_count += 1
        elif quality == "fallback": fallback_count += 1
        else: failed_count += 1

    text = "\n".join(rows)
    stats = f"✅ {ok_count}  ⚠️ {fallback_count}  ❌ {failed_count}"
    embed = discord.Embed(
        title=f"📋 已完成主題（最近 {len(recent)} 個）",
        description=text,
        color=0x00ff99
    )
    embed.set_footer(text=stats)
    await ctx.send(embed=embed)


@bot.command(name="remove")
async def cmd_remove(ctx, *, topic: str = ""):
    if not topic:
        await ctx.send("用法：`!remove 主題名稱`")
        return

    if not os.path.exists(DONE_FILE):
        await ctx.send("⚠️ 清單檔案不存在")
        return

    with open(DONE_FILE, encoding="utf-8") as f:
        lines = f.readlines()

    kept = [l for l in lines if not (
        l.strip() == topic or l.strip().startswith(topic + "|")
    )]
    removed = len(lines) - len(kept)

    if removed == 0:
        await ctx.send(f"⚠️ 找不到：**{topic}**")
        return

    with open(DONE_FILE, "w", encoding="utf-8") as f:
        f.writelines(kept)

    await ctx.send(f"✅ 已從清單移除：**{topic}**")


@bot.command(name="help")
async def cmd_help(ctx):
    embed = discord.Embed(title="🤖 Math Factory Bot 指令", color=0xffd700)
    embed.add_field(name="!status", value="查看生產線狀態", inline=False)
    embed.add_field(name="!start", value="啟動生產線", inline=False)
    embed.add_field(name="!stop", value="停止生產線", inline=False)
    embed.add_field(name="!log [行數]", value="查看最新 log（預設 15 行）", inline=False)
    embed.add_field(name="!topic <主題>", value="觸發特定主題生產", inline=False)
    embed.add_field(name="!queue", value="查看已完成主題列表（含品質標記）", inline=False)
    embed.add_field(name="!remove <主題>", value="從已完成清單移除指定主題", inline=False)
    await ctx.send(embed=embed)


if __name__ == "__main__":
    bot.run(TOKEN)

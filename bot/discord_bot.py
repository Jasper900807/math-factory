#!/usr/bin/env python3
"""
Math Factory Discord Bot
指令：!status, !start, !stop, !log, !topic <主題>, !queue
"""
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
DONE_FILE = f"{WORK_DIR}/data/topics_done.txt"

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
    await ctx.send(f"✅ 已觸發！等待生產完成...\n```{result[:200]}```")


@bot.command(name="queue")
async def cmd_queue(ctx):
    if not os.path.exists(DONE_FILE):
        await ctx.send("📋 尚無已完成主題")
        return
    with open(DONE_FILE) as f:
        topics = [l.strip() for l in f if l.strip()]
    recent = topics[-20:]
    text = "\n".join(f"{i+1}. {t}" for i, t in enumerate(recent))
    embed = discord.Embed(title=f"📋 已完成主題（最近 {len(recent)} 個）", description=text, color=0x00ff99)
    await ctx.send(embed=embed)


@bot.command(name="help")
async def cmd_help(ctx):
    embed = discord.Embed(title="🤖 Math Factory Bot 指令", color=0xffd700)
    embed.add_field(name="!status", value="查看生產線狀態", inline=False)
    embed.add_field(name="!start", value="啟動生產線", inline=False)
    embed.add_field(name="!stop", value="停止生產線", inline=False)
    embed.add_field(name="!log [行數]", value="查看最新 log（預設 15 行）", inline=False)
    embed.add_field(name="!topic <主題>", value="觸發特定主題生產", inline=False)
    embed.add_field(name="!queue", value="查看已完成主題列表", inline=False)
    await ctx.send(embed=embed)


if __name__ == "__main__":
    bot.run(TOKEN)

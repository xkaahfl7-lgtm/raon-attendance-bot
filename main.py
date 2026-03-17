import discord
from discord.ext import commands, tasks
from discord.ui import View, Button
import json
import os
from datetime import datetime
import asyncio

TOKEN = os.getenv("TOKEN")

GUILD_ID = 1462457099039674498

BUTTON_CHANNEL_ID = 1481808025030492180
RECORD_CHANNEL_ID = 1479035911726563419
STATUS_CHANNEL_ID = 1479036025820156035
LOG_CHANNEL_ID = 1479382504204013568

DATA_FILE = "attendance_data.json"

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

lock = asyncio.Lock()


def load_data():
    if not os.path.exists(DATA_FILE):
        return {"users": {}, "status_message_id": None}

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


attendance = load_data()


def now():
    return datetime.now()


def format_time(sec):
    h = sec // 3600
    m = (sec % 3600) // 60
    return f"{h}시간 {m:02d}분"


def is_staff(name):
    return name.startswith("STㆍ")


def normalize(name):
    return name.replace("⭐", "").replace(" ", "").lower()


def merge_users():
    merged = {}

    for uid, user in attendance["users"].items():

        key = normalize(user["display_name"])

        if key not in merged:
            merged[key] = user
        else:
            merged[key]["total_time"] += user["total_time"]

    attendance["users"] = {u["display_name"]: u for u in merged.values()}


def build_board():

    merge_users()

    rows = []

    for u in attendance["users"].values():

        total = u["total_time"]

        if u["is_working"]:
            start = datetime.fromisoformat(u["last_clock_in"])
            total += int((now() - start).total_seconds())

        rows.append((u["display_name"], total))

    rows.sort(key=lambda x: x[1], reverse=True)

    text = "📊 현재 근무중\n\n"

    for name, t in rows:

        if attendance["users"][name]["is_working"]:
            text += f"{name} - {format_time(t)}\n"

    text += "\n🏆 근무 랭킹\n\n"

    for i, (name, t) in enumerate(rows[:10], 1):
        text += f"{i}위 {name} - {format_time(t)}\n"

    return text


async def update_board():

    async with lock:

        channel = bot.get_channel(STATUS_CHANNEL_ID)

        text = build_board()

        msg_id = attendance["status_message_id"]

        if msg_id:
            try:
                msg = await channel.fetch_message(msg_id)
                await msg.edit(content=text, view=StatusView())
                return
            except:
                pass

        msg = await channel.send(text, view=StatusView())
        attendance["status_message_id"] = msg.id
        save_data(attendance)


class StatusView(View):

    @discord.ui.button(label="새로고침", style=discord.ButtonStyle.primary)
    async def refresh(self, interaction: discord.Interaction, button: Button):

        await update_board()
        await interaction.response.send_message("현황 새로고침 완료", ephemeral=True)

    @discord.ui.button(label="현황판 복구", style=discord.ButtonStyle.secondary)
    async def rebuild(self, interaction: discord.Interaction, button: Button):

        attendance["status_message_id"] = None
        save_data(attendance)

        await update_board()

        await interaction.response.send_message("현황판 복구 완료", ephemeral=True)


class AttendanceView(View):

    @discord.ui.button(label="출근", style=discord.ButtonStyle.green)
    async def clockin(self, interaction: discord.Interaction, button: Button):

        uid = str(interaction.user.id)
        name = interaction.user.display_name

        if uid not in attendance["users"]:
            attendance["users"][uid] = {
                "display_name": name,
                "total_time": 0,
                "is_working": False,
                "last_clock_in": None
            }

        user = attendance["users"][uid]

        if user["is_working"]:
            await interaction.response.send_message("이미 출근 상태입니다.", ephemeral=True)
            return

        user["is_working"] = True
        user["last_clock_in"] = now().isoformat()

        save_data(attendance)

        await update_board()

        await interaction.response.send_message("출근 완료", ephemeral=True)

    @discord.ui.button(label="퇴근", style=discord.ButtonStyle.red)
    async def clockout(self, interaction: discord.Interaction, button: Button):

        uid = str(interaction.user.id)

        if uid not in attendance["users"]:
            return

        user = attendance["users"][uid]

        if not user["is_working"]:
            await interaction.response.send_message("출근 기록 없음", ephemeral=True)
            return

        start = datetime.fromisoformat(user["last_clock_in"])

        sec = int((now() - start).total_seconds())

        user["total_time"] += sec
        user["is_working"] = False
        user["last_clock_in"] = None

        save_data(attendance)

        await update_board()

        await interaction.response.send_message("퇴근 완료", ephemeral=True)


@bot.tree.command(name="강제퇴근")
async def force(interaction: discord.Interaction, 대상: str):

    manager = interaction.user.display_name

    if is_staff(manager):
        await interaction.response.send_message("스태프는 사용 불가", ephemeral=True)
        return

    for u in attendance["users"].values():

        if 대상 in u["display_name"]:

            u["is_working"] = False
            u["last_clock_in"] = None

    save_data(attendance)

    await update_board()

    await interaction.response.send_message("강제퇴근 완료", ephemeral=True)


@bot.event
async def on_ready():

    bot.add_view(AttendanceView())
    bot.add_view(StatusView())

    await update_board()

    print("RAON 출퇴근봇 실행됨")


bot.run(TOKEN)

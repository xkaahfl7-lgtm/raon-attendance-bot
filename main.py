# 🔥 기존 import 동일
import discord
from discord.ext import commands, tasks
from discord.ui import View, Button
from datetime import datetime, timezone, timedelta
import json, os, shutil, asyncio, re, unicodedata

TOKEN = os.getenv("TOKEN")

GUILD_ID = 1462457099039674498

BUTTON_CHANNEL_ID = 1481808025030492180
RECORD_CHANNEL_ID = 1479035911726563419
STATUS_CHANNEL_ID = 1479036025820156035
LOG_CHANNEL_ID = 1479382504204013568

DATA_FILE = "attendance_data.json"
BACKUP_FILE = "attendance_data_backup.json"

KST = timezone(timedelta(hours=9))

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

status_lock = asyncio.Lock()

# ==============================
# 🔥 공통 함수
# ==============================

def now_kst():
    return datetime.now(KST)

def now_str():
    return now_kst().strftime("%Y-%m-%d %H:%M:%S")

def safe_int(v):
    try:
        return int(v)
    except:
        return 0

def format_time(sec):
    sec = max(0, int(sec))
    return f"{sec//3600}시간 {(sec%3600)//60:02d}분"

def parse_dt(dt):
    try:
        return datetime.fromisoformat(dt)
    except:
        return None

# ==============================
# 🔥 데이터
# ==============================

def load_data():
    if not os.path.exists(DATA_FILE):
        return {"users": {}, "status_message_id": None, "button_message_id": None}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    if os.path.exists(DATA_FILE):
        shutil.copyfile(DATA_FILE, BACKUP_FILE)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

attendance = load_data()

# ==============================
# 🔥 사용자 처리 (핵심 수정)
# ==============================

def ensure_user(member):
    uid = str(member.id)

    if uid not in attendance["users"]:
        attendance["users"][uid] = {
            "user_id": uid,
            "display_name": member.display_name,
            "total_time": 0,
            "is_working": False,
            "last_clock_in": None,
        }

    return attendance["users"][uid]

def get_current_work_seconds(user):
    if not user["is_working"]:
        return 0
    dt = parse_dt(user["last_clock_in"])
    if not dt:
        return 0
    return int((now_kst() - dt).total_seconds())

# ==============================
# 🔥 근무중 / 랭킹
# ==============================

def get_working_users():
    result = []
    for uid, user in attendance["users"].items():
        if user["is_working"]:
            sec = get_current_work_seconds(user)
            result.append((user["display_name"], sec))
    result.sort(key=lambda x: -x[1])
    return result

def get_ranking_users():
    result = []
    for uid, user in attendance["users"].items():
        total = safe_int(user["total_time"]) + get_current_work_seconds(user)
        if total > 0:
            result.append((user["display_name"], total))
    result.sort(key=lambda x: -x[1])
    return result[:10]

# ==============================
# 🔥 현황판
# ==============================

def build_status_text():
    working = get_working_users()
    ranking = get_ranking_users()

    text = "📊 현재 근무중\n\n"

    if not working:
        text += "없음\n"
    else:
        for i, (name, sec) in enumerate(working, 1):
            text += f"{i}. {name} - {format_time(sec)}\n"

    text += "\n🏆 근무 랭킹\n\n"

    if not ranking:
        text += "없음"
    else:
        for i, (name, sec) in enumerate(ranking, 1):
            text += f"{i}위 {name} - {format_time(sec)}\n"

    return text

async def update_status():
    channel = bot.get_channel(STATUS_CHANNEL_ID)
    if not channel:
        return

    text = build_status_text()

    msg_id = attendance.get("status_message_id")

    if msg_id:
        try:
            msg = await channel.fetch_message(msg_id)
            await msg.edit(content=text)
            return
        except:
            pass

    msg = await channel.send(text)
    attendance["status_message_id"] = msg.id
    save_data(attendance)

# ==============================
# 🔥 버튼
# ==============================

class AttendanceView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="출근", style=discord.ButtonStyle.success)
    async def clock_in(self, interaction: discord.Interaction, button: Button):
        user = ensure_user(interaction.user)

        if user["is_working"]:
            await interaction.response.send_message("이미 출근중", ephemeral=True)
            return

        user["is_working"] = True
        user["last_clock_in"] = now_kst().isoformat()
        save_data(attendance)

        await update_status()
        await interaction.response.send_message("출근 완료", ephemeral=True)

    @discord.ui.button(label="퇴근", style=discord.ButtonStyle.danger)
    async def clock_out(self, interaction: discord.Interaction, button: Button):
        user = ensure_user(interaction.user)

        if not user["is_working"]:
            await interaction.response.send_message("출근 상태 아님", ephemeral=True)
            return

        sec = get_current_work_seconds(user)

        user["total_time"] += sec
        user["is_working"] = False
        user["last_clock_in"] = None
        save_data(attendance)

        await update_status()
        await interaction.response.send_message("퇴근 완료", ephemeral=True)

# ==============================
# 🔥 강제퇴근 (완전 수정 핵심)
# ==============================

@bot.tree.command(name="강제퇴근")
async def force_out(interaction: discord.Interaction, 대상: discord.Member):

    user_id = str(대상.id)

    if user_id not in attendance["users"]:
        await interaction.response.send_message("데이터 없음", ephemeral=True)
        return

    user = attendance["users"][user_id]

    if not user["is_working"]:
        await interaction.response.send_message("근무중 아님", ephemeral=True)
        return

    # 🔥 완전 종료
    user["is_working"] = False
    user["last_clock_in"] = None
    save_data(attendance)

    await update_status()

    await interaction.response.send_message(f"{대상.display_name} 강제퇴근 완료", ephemeral=True)

# ==============================
# 🔥 시작
# ==============================

@bot.event
async def on_ready():
    bot.add_view(AttendanceView())

    channel = bot.get_channel(BUTTON_CHANNEL_ID)
    msg = await channel.send("출퇴근 버튼", view=AttendanceView())

    attendance["button_message_id"] = msg.id
    save_data(attendance)

    await update_status()

    print("봇 실행 완료")

bot.run(TOKEN)

import discord
from discord.ext import commands, tasks
from discord.ui import View, Button
from datetime import datetime, timezone, timedelta
import json
import os
import shutil
import asyncio
import re
import unicodedata

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
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
status_lock = asyncio.Lock()


def now_kst():
    return datetime.now(KST)


def now_str():
    return now_kst().strftime("%Y-%m-%d %H:%M:%S")


def safe_int(v):
    try:
        return int(v)
    except Exception:
        return 0


def format_time(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}시간 {m:02d}분"


def normalize_person_key(name: str) -> str:
    if not name:
        return ""

    text = unicodedata.normalize("NFKC", str(name)).strip()
    text = re.sub(r"[\u200b-\u200d\ufeff]", "", text)
    text = text.replace("(", "").replace(")", "")
    text = text.replace("⭐", "")
    text = text.lower()

    text = re.sub(
        r"^(gm|dgm|am|im|ig|st|staff|dev|admin|mod)[\s\-_ㆍ·|/\\]*",
        "",
        text,
        flags=re.IGNORECASE
    )
    text = re.sub(r"^@+", "", text)
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[^0-9a-z가-힣]", "", text)

    alias_map = {
        "쏘야": "쏘야",
        "soya": "쏘야",

        "볶음": "볶음",
        "bokkeum": "볶음",

        "봉식": "봉식",
        "bongsik": "봉식",

        "우진": "우진",
        "woojin": "우진",
        "ujin": "우진",

        "챠니": "챠니",
        "chani": "챠니",

        "호랭": "호랭",
        "horang": "호랭",

        "혁이": "혁이",
        "hyuki": "혁이",
        "hyuk": "혁이",

        "백구": "백구",
        "baekgu": "백구",
    }

    return alias_map.get(text, text)


def get_fixed_display_name(name: str) -> str:
    key = normalize_person_key(name)

    mapping = {
        "쏘야": "GMㆍ쏘야",
        "볶음": "DEVㆍ볶음",
        "봉식": "IGㆍ봉식",
        "우진": "AMㆍ우진",
        "챠니": "STㆍ챠니",
        "호랭": "STㆍ호랭",
        "혁이": "STㆍ혁이",
        "백구": "STㆍ백구",
    }

    return mapping.get(key, str(name).strip())


def load_data():
    if not os.path.exists(DATA_FILE):
        data = {"users": {}, "status_message_id": None, "button_message_id": None}
        save_data(data)
        return data

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if "users" not in data:
            data["users"] = {}
        if "status_message_id" not in data:
            data["status_message_id"] = None
        if "button_message_id" not in data:
            data["button_message_id"] = None

        return data

    except Exception:
        if os.path.exists(BACKUP_FILE):
            try:
                with open(BACKUP_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if "users" not in data:
                    data["users"] = {}
                if "status_message_id" not in data:
                    data["status_message_id"] = None
                if "button_message_id" not in data:
                    data["button_message_id"] = None

                save_data(data)
                return data
            except Exception:
                pass

        return {"users": {}, "status_message_id": None, "button_message_id": None}


def save_data(data):
    temp_file = DATA_FILE + ".tmp"

    if os.path.exists(DATA_FILE):
        shutil.copyfile(DATA_FILE, BACKUP_FILE)

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    os.replace(temp_file, DATA_FILE)


attendance = load_data()


def ensure_user(user_id: int, display_name: str):
    uid = str(user_id)

    if uid not in attendance["users"]:
        attendance["users"][uid] = {
            "user_id": uid,
            "display_name": get_fixed_display_name(display_name),
            "raw_display_name": display_name,
            "total_time": 0,
            "is_working": False,
            "last_clock_in": None
        }
    else:
        attendance["users"][uid]["display_name"] = get_fixed_display_name(display_name)
        attendance["users"][uid]["raw_display_name"] = display_name
        attendance["users"][uid].setdefault("total_time", 0)
        attendance["users"][uid].setdefault("is_working", False)
        attendance["users"][uid].setdefault("last_clock_in", None)

    return attendance["users"][uid]


def parse_dt(dt_str):
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str)
    except Exception:
        return None


def get_current_work_seconds(user_data):
    if not user_data.get("is_working"):
        return 0

    start_dt = parse_dt(user_data.get("last_clock_in"))
    if not start_dt:
        return 0

    diff = now_kst() - start_dt
    return max(0, int(diff.total_seconds()))


def get_working_users():
    rows = []

    for uid, user in attendance["users"].items():
        if user.get("is_working"):
            rows.append({
                "user_id": uid,
                "display_name": user.get("display_name", uid),
                "current_seconds": get_current_work_seconds(user)
            })

    rows.sort(key=lambda x: (-x["current_seconds"], x["display_name"]))
    return rows


def get_ranking_users(limit=10):
    rows = []

    for uid, user in attendance["users"].items():
        total = safe_int(user.get("total_time", 0))
        if user.get("is_working"):
            total += get_current_work_seconds(user)

        if total > 0:
            rows.append({
                "user_id": uid,
                "display_name": user.get("display_name", uid),
                "total_seconds": total
            })

    rows.sort(key=lambda x: (-x["total_seconds"], x["display_name"]))
    return rows[:limit]


async def send_log(text: str):
    print(text)
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        try:
            await channel.send(text)
        except Exception:
            pass


def build_status_text():
    working = get_working_users()
    ranking = get_ranking_users()

    lines = []
    lines.append("📊 현재 근무중")
    lines.append("")

    if not working:
        lines.append("현재 근무중인 관리자가 없습니다.")
    else:
        for idx, row in enumerate(working, start=1):
            lines.append(f"{idx}. {row['display_name']} - {format_time(row['current_seconds'])}")

    lines.append("")
    lines.append("🏆 근무 랭킹")
    lines.append("")

    if not ranking:
        lines.append("근무 데이터가 없습니다.")
    else:
        for idx, row in enumerate(ranking, start=1):
            lines.append(f"{idx}위 {row['display_name']} - {format_time(row['total_seconds'])}")

    return "\n".join(lines)


async def update_status_message():
    async with status_lock:
        channel = bot.get_channel(STATUS_CHANNEL_ID)
        if not channel:
            await send_log("오류 | 관리자 근무확인 채널 없음")
            return

        text = build_status_text()
        msg_id = attendance.get("status_message_id")

        if msg_id:
            try:
                msg = await channel.fetch_message(int(msg_id))
                await msg.edit(content=text)
                return
            except Exception:
                pass

        try:
            msg = await channel.send(text)
            attendance["status_message_id"] = msg.id
            save_data(attendance)
        except Exception as e:
            await send_log(f"오류 | 근무현황 메시지 생성 실패 | {e}")


class AttendanceView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="출근", style=discord.ButtonStyle.success, custom_id="raon_clock_in")
    async def clock_in_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)

        try:
            user = ensure_user(interaction.user.id, interaction.user.display_name)

            if user["is_working"]:
                await interaction.followup.send("이미 출근 상태입니다.", ephemeral=True)
                return

            user["is_working"] = True
            user["last_clock_in"] = now_kst().isoformat()
            save_data(attendance)

            log_name = get_fixed_display_name(interaction.user.display_name)

            embed = discord.Embed(title="🟢 출근 기록", color=0x2ecc71)
            embed.add_field(name="👤 관리자", value=log_name, inline=False)
            embed.add_field(name="🕒 출근 시간", value=now_str(), inline=False)

            record_channel = bot.get_channel(RECORD_CHANNEL_ID)
            if record_channel:
                await record_channel.send(embed=embed)

            await send_log(f"출근 완료 | {log_name}")
            await update_status_message()

            await interaction.followup.send("출근 처리 완료", ephemeral=True)

        except Exception as e:
            await send_log(f"오류 | 출근 처리 실패 | {interaction.user.display_name} | {e}")
            await interaction.followup.send("출근 처리 중 오류가 발생했습니다.", ephemeral=True)

    @discord.ui.button(label="퇴근", style=discord.ButtonStyle.danger, custom_id="raon_clock_out")
    async def clock_out_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)

        try:
            user = ensure_user(interaction.user.id, interaction.user.display_name)

            if not user["is_working"]:
                await interaction.followup.send("출근 기록이 없습니다.", ephemeral=True)
                return

            worked = get_current_work_seconds(user)
            user["total_time"] = safe_int(user.get("total_time", 0)) + worked
            user["is_working"] = False
            user["last_clock_in"] = None
            save_data(attendance)

            log_name = get_fixed_display_name(interaction.user.display_name)

            embed = discord.Embed(title="🔴 퇴근 기록", color=0xe74c3c)
            embed.add_field(name="👤 관리자", value=log_name, inline=False)
            embed.add_field(name="🕒 퇴근 시간", value=now_str(), inline=False)

            record_channel = bot.get_channel(RECORD_CHANNEL_ID)
            if record_channel:
                await record_channel.send(embed=embed)

            await send_log(f"퇴근 완료 | {log_name}")
            await update_status_message()

            await interaction.followup.send("퇴근 처리 완료", ephemeral=True)

        except Exception as e:
            await send_log(f"오류 | 퇴근 처리 실패 | {interaction.user.display_name} | {e}")
            await interaction.followup.send("퇴근 처리 중 오류가 발생했습니다.", ephemeral=True)

    @discord.ui.button(label="근무현황", style=discord.ButtonStyle.primary, custom_id="raon_status_refresh")
    async def refresh_status_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)

        try:
            await update_status_message()
            await interaction.followup.send("근무현황을 갱신했습니다.", ephemeral=True)
        except Exception as e:
            await send_log(f"오류 | 근무현황 갱신 실패 | {interaction.user.display_name} | {e}")
            await interaction.followup.send("근무현황 갱신 중 오류가 발생했습니다.", ephemeral=True)


async def ensure_button_message():
    channel = bot.get_channel(BUTTON_CHANNEL_ID)
    if not channel:
        await send_log("오류 | 출퇴근 버튼 채널 없음")
        return

    msg_id = attendance.get("button_message_id")
    view = AttendanceView()
    content = "📌 출퇴근 버튼\n\n아래 버튼을 눌러 출근 / 퇴근 / 근무현황을 이용해주세요."

    if msg_id:
        try:
            msg = await channel.fetch_message(int(msg_id))
            await msg.edit(content=content, view=view)
            return
        except Exception:
            pass

    try:
        msg = await channel.send(content, view=view)
        attendance["button_message_id"] = msg.id
        save_data(attendance)
    except Exception as e:
        await send_log(f"오류 | 버튼 메시지 생성 실패 | {e}")


@tasks.loop(seconds=60)
async def auto_refresh_status():
    await update_status_message()


@bot.event
async def on_ready():
    global attendance
    attendance = load_data()

    for uid, user in attendance["users"].items():
        fixed_name = get_fixed_display_name(user.get("raw_display_name") or user.get("display_name", uid))
        user["display_name"] = fixed_name

    save_data(attendance)

    bot.add_view(AttendanceView())

    await send_log("🤖 RAON 출퇴근 봇이 정상적으로 실행되었습니다.")
    await ensure_button_message()
    await update_status_message()

    if not auto_refresh_status.is_running():
        auto_refresh_status.start()

    print(f"로그인 완료: {bot.user}")


if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("환경변수 TOKEN 이 비어 있습니다.")

    bot.run(TOKEN)

import discord
from discord.ext import commands, tasks
from discord.ui import View, Button
from datetime import datetime, timezone, timedelta
import json
import os
import shutil
import asyncio

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


def safe_int(value):
    try:
        return int(value)
    except Exception:
        return 0


def format_time(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}시간 {m:02d}분"


def parse_dt(dt_str):
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str)
    except Exception:
        return None


def is_real_user_key(uid: str) -> bool:
    return str(uid).isdigit()


def load_data():
    default_data = {
        "users": {},
        "status_message_id": None,
        "button_message_id": None,
    }

    if not os.path.exists(DATA_FILE):
        save_data(default_data)
        return default_data

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return default_data

        if "users" not in data or not isinstance(data["users"], dict):
            data["users"] = {}

        data.setdefault("status_message_id", None)
        data.setdefault("button_message_id", None)

        # 기존 데이터 보정
        for uid, user in list(data["users"].items()):
            if not isinstance(user, dict):
                data["users"][uid] = {
                    "user_id": str(uid),
                    "display_name": str(uid),
                    "total_time": 0,
                    "is_working": False,
                    "last_clock_in": None,
                }
                continue

            user.setdefault("user_id", str(uid))
            user.setdefault("display_name", str(uid))
            user.setdefault("total_time", 0)
            user.setdefault("is_working", False)
            user.setdefault("last_clock_in", None)

        return data

    except Exception:
        if os.path.exists(BACKUP_FILE):
            try:
                with open(BACKUP_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if not isinstance(data, dict):
                    return default_data

                if "users" not in data or not isinstance(data["users"], dict):
                    data["users"] = {}

                data.setdefault("status_message_id", None)
                data.setdefault("button_message_id", None)

                for uid, user in list(data["users"].items()):
                    if not isinstance(user, dict):
                        data["users"][uid] = {
                            "user_id": str(uid),
                            "display_name": str(uid),
                            "total_time": 0,
                            "is_working": False,
                            "last_clock_in": None,
                        }
                        continue

                    user.setdefault("user_id", str(uid))
                    user.setdefault("display_name", str(uid))
                    user.setdefault("total_time", 0)
                    user.setdefault("is_working", False)
                    user.setdefault("last_clock_in", None)

                save_data(data)
                return data
            except Exception:
                pass

        return default_data


def save_data(data):
    temp_file = DATA_FILE + ".tmp"

    if os.path.exists(DATA_FILE):
        try:
            shutil.copyfile(DATA_FILE, BACKUP_FILE)
        except Exception:
            pass

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    os.replace(temp_file, DATA_FILE)


attendance = load_data()


def cleanup_users():
    """
    진짜 디스코드 유저 ID(숫자 키)만 실사용 대상으로 유지
    숫자 아닌 임시 키는 랭킹/현황에서 무시
    """
    changed = False

    for uid, user in attendance.get("users", {}).items():
        if not isinstance(user, dict):
            continue

        user["user_id"] = str(uid)
        user["display_name"] = str(user.get("display_name", uid))
        user["total_time"] = safe_int(user.get("total_time", 0))
        user["is_working"] = bool(user.get("is_working", False))

        if user.get("is_working"):
            dt = parse_dt(user.get("last_clock_in"))
            if not dt:
                user["is_working"] = False
                user["last_clock_in"] = None
                changed = True
        else:
            user["last_clock_in"] = user.get("last_clock_in")

    if changed:
        save_data(attendance)


def ensure_user(member: discord.Member):
    uid = str(member.id)

    if uid not in attendance["users"] or not isinstance(attendance["users"][uid], dict):
        attendance["users"][uid] = {
            "user_id": uid,
            "display_name": member.display_name,
            "total_time": 0,
            "is_working": False,
            "last_clock_in": None,
        }
    else:
        attendance["users"][uid]["user_id"] = uid
        attendance["users"][uid]["display_name"] = member.display_name
        attendance["users"][uid]["total_time"] = safe_int(attendance["users"][uid].get("total_time", 0))
        attendance["users"][uid]["is_working"] = bool(attendance["users"][uid].get("is_working", False))
        attendance["users"][uid].setdefault("last_clock_in", None)

    save_data(attendance)
    return attendance["users"][uid]


def get_current_work_seconds(user_data):
    if not user_data.get("is_working", False):
        return 0

    start_dt = parse_dt(user_data.get("last_clock_in"))
    if not start_dt:
        return 0

    diff = now_kst() - start_dt
    return max(0, int(diff.total_seconds()))


def get_working_users():
    rows = []

    for uid, user in attendance.get("users", {}).items():
        if not is_real_user_key(uid):
            continue

        if user.get("is_working", False):
            current_seconds = get_current_work_seconds(user)
            if current_seconds > 0:
                rows.append({
                    "display_name": user.get("display_name", uid),
                    "current_seconds": current_seconds,
                })

    rows.sort(key=lambda x: (-x["current_seconds"], x["display_name"]))
    return rows


def get_ranking_users(limit=10):
    rows = []

    for uid, user in attendance.get("users", {}).items():
        if not is_real_user_key(uid):
            continue

        total_seconds = safe_int(user.get("total_time", 0))
        if user.get("is_working", False):
            total_seconds += get_current_work_seconds(user)

        if total_seconds > 0:
            rows.append({
                "display_name": user.get("display_name", uid),
                "total_seconds": total_seconds,
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

    lines = ["📊 현재 근무중", ""]

    if not working:
        lines.append("현재 근무중인 관리자가 없습니다.")
    else:
        for idx, row in enumerate(working, start=1):
            lines.append(f"{idx}. {row['display_name']} - {format_time(row['current_seconds'])}")

    lines.extend(["", "🏆 근무 랭킹", ""])

    if not ranking:
        lines.append("근무 데이터가 없습니다.")
    else:
        for idx, row in enumerate(ranking, start=1):
            lines.append(f"{idx}위 {row['display_name']} - {format_time(row['total_seconds'])}")

    return "\n".join(lines)


class StatusView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="새로고침", style=discord.ButtonStyle.primary, custom_id="raon_status_refresh")
    async def refresh_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)

        try:
            await update_status_message()
            await interaction.followup.send("근무현황 새로고침 완료", ephemeral=True)
        except Exception as e:
            await send_log(f"오류 | 근무현황 새로고침 실패 | {interaction.user.display_name} | {e}")
            await interaction.followup.send("근무현황 새로고침 중 오류가 발생했습니다.", ephemeral=True)

    @discord.ui.button(label="현황판 복구", style=discord.ButtonStyle.secondary, custom_id="raon_status_rebuild")
    async def rebuild_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)

        try:
            channel = bot.get_channel(STATUS_CHANNEL_ID)
            old_id = attendance.get("status_message_id")

            if old_id and channel:
                try:
                    old_msg = await channel.fetch_message(int(old_id))
                    await old_msg.delete()
                except Exception:
                    pass

            attendance["status_message_id"] = None
            save_data(attendance)
            await ensure_status_message()

            await interaction.followup.send("현황판 복구 완료", ephemeral=True)
        except Exception as e:
            await send_log(f"오류 | 현황판 복구 실패 | {interaction.user.display_name} | {e}")
            await interaction.followup.send("현황판 복구 중 오류가 발생했습니다.", ephemeral=True)


async def ensure_status_message():
    async with status_lock:
        channel = bot.get_channel(STATUS_CHANNEL_ID)
        if not channel:
            await send_log("오류 | 관리자 근무확인 채널 없음")
            return

        cleanup_users()

        text = build_status_text()
        view = StatusView()
        msg_id = attendance.get("status_message_id")

        if msg_id:
            try:
                msg = await channel.fetch_message(int(msg_id))
                await msg.edit(content=text, view=view)
                return
            except Exception:
                pass

        try:
            msg = await channel.send(text, view=view)
            attendance["status_message_id"] = msg.id
            save_data(attendance)
        except Exception as e:
            await send_log(f"오류 | 근무현황 메시지 생성 실패 | {e}")


async def update_status_message():
    cleanup_users()
    save_data(attendance)
    await ensure_status_message()


class AttendanceView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="출근", style=discord.ButtonStyle.success, custom_id="raon_clock_in")
    async def clock_in_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)

        try:
            user = ensure_user(interaction.user)

            if user.get("is_working", False):
                await interaction.followup.send("이미 출근 상태입니다.", ephemeral=True)
                return

            user["is_working"] = True
            user["last_clock_in"] = now_kst().isoformat()
            save_data(attendance)

            embed = discord.Embed(title="🟢 출근 기록", color=0x2ECC71)
            embed.add_field(name="👤 사용자", value=interaction.user.display_name, inline=False)
            embed.add_field(name="🕒 출근 시간", value=now_str(), inline=False)

            record_channel = bot.get_channel(RECORD_CHANNEL_ID)
            if record_channel:
                await record_channel.send(embed=embed)

            await send_log(f"출근 완료 | {interaction.user.display_name}")
            await update_status_message()
            await interaction.followup.send("출근 처리 완료", ephemeral=True)

        except Exception as e:
            await send_log(f"오류 | 출근 처리 실패 | {interaction.user.display_name} | {e}")
            await interaction.followup.send("출근 처리 중 오류가 발생했습니다.", ephemeral=True)

    @discord.ui.button(label="퇴근", style=discord.ButtonStyle.danger, custom_id="raon_clock_out")
    async def clock_out_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)

        try:
            user = ensure_user(interaction.user)

            if not user.get("is_working", False):
                await interaction.followup.send("출근 기록이 없습니다.", ephemeral=True)
                return

            worked = get_current_work_seconds(user)
            user["total_time"] = safe_int(user.get("total_time", 0)) + worked
            user["is_working"] = False
            user["last_clock_in"] = None
            save_data(attendance)

            embed = discord.Embed(title="🔴 퇴근 기록", color=0xE74C3C)
            embed.add_field(name="👤 사용자", value=interaction.user.display_name, inline=False)
            embed.add_field(name="🕒 퇴근 시간", value=now_str(), inline=False)
            embed.add_field(name="⏱ 이번 근무", value=format_time(worked), inline=False)

            record_channel = bot.get_channel(RECORD_CHANNEL_ID)
            if record_channel:
                await record_channel.send(embed=embed)

            await send_log(f"퇴근 완료 | {interaction.user.display_name} | {format_time(worked)}")
            await update_status_message()
            await interaction.followup.send("퇴근 처리 완료", ephemeral=True)

        except Exception as e:
            await send_log(f"오류 | 퇴근 처리 실패 | {interaction.user.display_name} | {e}")
            await interaction.followup.send("퇴근 처리 중 오류가 발생했습니다.", ephemeral=True)


@bot.tree.command(name="강제퇴근", description="근무중인 유저를 강제로 퇴근 처리합니다. (근무시간 미반영)")
async def force_clock_out(interaction: discord.Interaction, 대상: discord.Member):
    await interaction.response.defer(ephemeral=True)

    try:
        manager_id = str(interaction.user.id)
        target_id = str(대상.id)

        # 스태프 차단 로직이 꼭 필요하면 역할 ID 기준으로 바꾸는 게 맞지만,
        # 지금은 일단 기존 이름 꼬임 없애는 게 우선이라 권한 제한은 생략.
        if target_id not in attendance.get("users", {}):
            await interaction.followup.send(f"{대상.display_name} 님은 근무 데이터가 없습니다.", ephemeral=True)
            return

        user = attendance["users"][target_id]

        if not user.get("is_working", False):
            await interaction.followup.send(f"{대상.display_name} 님은 현재 근무중이 아닙니다.", ephemeral=True)
            return

        user["is_working"] = False
        user["last_clock_in"] = None
        save_data(attendance)

        record_channel = bot.get_channel(RECORD_CHANNEL_ID)
        if record_channel:
            embed = discord.Embed(title="⚫ 강제 퇴근 기록", color=0x555555)
            embed.add_field(name="👤 대상자", value=대상.display_name, inline=False)
            embed.add_field(name="🛠 처리자", value=interaction.user.display_name, inline=False)
            embed.add_field(name="🕒 처리 시간", value=now_str(), inline=False)
            embed.add_field(name="🗑 처리 방식", value="이번 근무시간 미반영", inline=False)
            await record_channel.send(embed=embed)

        await send_log(f"강제퇴근 완료 | 대상 {대상.display_name} | 처리자 {interaction.user.display_name}")
        await update_status_message()
        await interaction.followup.send(f"{대상.display_name} 강제퇴근 처리 완료", ephemeral=True)

    except Exception as e:
        await send_log(f"오류 | 강제퇴근 실패 | {interaction.user.display_name} | {e}")
        await interaction.followup.send("강제퇴근 처리 중 오류가 발생했습니다.", ephemeral=True)


async def ensure_button_message():
    channel = bot.get_channel(BUTTON_CHANNEL_ID)
    if not channel:
        await send_log("오류 | 출퇴근 버튼 채널 없음")
        return

    msg_id = attendance.get("button_message_id")
    view = AttendanceView()
    content = "📌 출퇴근 버튼\n\n아래 버튼을 눌러 출근 / 퇴근을 이용해주세요."

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

    cleanup_users()
    save_data(attendance)

    bot.add_view(AttendanceView())
    bot.add_view(StatusView())

    await send_log("🤖 RAON 출퇴근 봇이 정상적으로 실행되었습니다.")
    await ensure_button_message()
    await ensure_status_message()

    if not auto_refresh_status.is_running():
        auto_refresh_status.start()

    try:
        guild_obj = discord.Object(id=GUILD_ID)
        synced = await bot.tree.sync(guild=guild_obj)
        if not synced:
            synced = await bot.tree.sync()
        print(f"슬래시 명령어 동기화 완료: {len(synced)}개")
        await send_log(f"슬래시 명령어 동기화 완료: {len(synced)}개")
    except Exception as e:
        await send_log(f"오류 | 슬래시 명령어 동기화 실패 | {e}")

    print(f"로그인 완료: {bot.user}")


if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("환경변수 TOKEN 이 비어 있습니다.")
    bot.run(TOKEN)

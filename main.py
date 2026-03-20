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

# 고정 명단
ADMIN_KEYS = {"쏘야", "볶음", "우진", "봉식"}
STAFF_KEYS = {"호랭", "백구", "혁이", "알루"}

CANONICAL_DISPLAY = {
    "쏘야": "GMㆍ쏘야",
    "볶음": "DEVㆍ볶음",
    "봉식": "IGㆍ봉식",
    "우진": "AMㆍ우진",
    "호랭": "STㆍ⭐호랭",
    "백구": "STㆍ⭐백구",
    "혁이": "STㆍ혁이",
    "알루": "STㆍ알루",
}


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


def normalize_person_key(name: str) -> str:
    if not name:
        return ""

    text = unicodedata.normalize("NFKC", str(name)).strip()
    text = re.sub(r"[\u200b-\u200d\ufeff]", "", text)

    # 문제 원인 제거
    text = text.replace("(수정됨)", "")
    text = text.replace("수정됨", "")

    text = text.replace("(", "").replace(")", "")
    text = text.replace("⭐", "")
    text = text.lower()

    # 앞 직급 제거
    text = re.sub(
        r"^(gm|dgm|am|im|ig|st|staff|dev|admin|mod)[\s\-_ㆍ·|/\\]*",
        "",
        text,
        flags=re.IGNORECASE,
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
        "호랭": "호랭",
        "horang": "호랭",
        "백구": "백구",
        "baekgu": "백구",
        "혁이": "혁이",
        "hyuk": "혁이",
        "hyuki": "혁이",
        "알루": "알루",
        "allu": "알루",
        "alu": "알루",
    }
    return alias_map.get(text, text)


def get_fixed_display_name(name: str) -> str:
    key = normalize_person_key(name)
    return CANONICAL_DISPLAY.get(key, str(name).strip())


def get_role_label_from_name(name: str) -> str:
    key = normalize_person_key(name)
    if key in ADMIN_KEYS:
        return "관리자"
    if key in STAFF_KEYS:
        return "스태프"
    return "사용자"


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

        for uid, user in list(data["users"].items()):
            if not isinstance(user, dict):
                data["users"][uid] = {
                    "user_id": str(uid),
                    "display_name": str(uid),
                    "raw_display_name": str(uid),
                    "total_time": 0,
                    "is_working": False,
                    "last_clock_in": None,
                }
                continue

            user.setdefault("user_id", str(uid))
            user.setdefault("display_name", str(uid))
            user.setdefault("raw_display_name", user.get("display_name", str(uid)))
            user.setdefault("total_time", 0)
            user.setdefault("is_working", False)
            user.setdefault("last_clock_in", None)

        return data

    except Exception:
        if os.path.exists(BACKUP_FILE):
            try:
                with open(BACKUP_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if "users" not in data or not isinstance(data["users"], dict):
                    data["users"] = {}
                data.setdefault("status_message_id", None)
                data.setdefault("button_message_id", None)
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


def choose_keeper_uid(uids):
    numeric = [str(uid) for uid in uids if str(uid).isdigit()]
    if numeric:
        # 실제 디스코드 ID처럼 긴 숫자 우선
        numeric.sort(key=lambda x: (-len(x), x))
        return numeric[0]
    return str(uids[0])


def cleanup_and_merge_users():
    users = attendance.get("users", {})
    if not users:
        return

    groups = {}
    for uid, user in list(users.items()):
        if not isinstance(user, dict):
            continue

        raw_name = user.get("raw_display_name") or user.get("display_name", uid)
        key = normalize_person_key(raw_name)
        fixed_name = get_fixed_display_name(raw_name)

        groups.setdefault(key, []).append({
            "uid": str(uid),
            "raw_display_name": raw_name,
            "display_name": fixed_name,
            "total_time": safe_int(user.get("total_time", 0)),
            "is_working": bool(user.get("is_working", False)),
            "last_clock_in": user.get("last_clock_in"),
        })

    new_users = {}

    for key, items in groups.items():
        keeper_uid = choose_keeper_uid([item["uid"] for item in items])

        total_sum = 0
        earliest_clock_in = None
        is_working = False

        for item in items:
            total_sum += safe_int(item["total_time"])
            if item["is_working"]:
                dt = parse_dt(item.get("last_clock_in"))
                if dt:
                    is_working = True
                    if earliest_clock_in is None or dt < earliest_clock_in[0]:
                        earliest_clock_in = (dt, item["last_clock_in"])

        raw_name = items[0]["raw_display_name"]
        fixed_name = get_fixed_display_name(raw_name)

        new_users[keeper_uid] = {
            "user_id": keeper_uid,
            "display_name": fixed_name,
            "raw_display_name": raw_name,
            "total_time": total_sum,
            "is_working": is_working,
            "last_clock_in": earliest_clock_in[1] if earliest_clock_in else None,
        }

    attendance["users"] = new_users


def ensure_user(member: discord.Member):
    uid = str(member.id)
    fixed_name = get_fixed_display_name(member.display_name)
    target_key = normalize_person_key(member.display_name)

    # 같은 사람의 기존 데이터가 다른 uid에 있으면 실제 디스코드 uid로 이전
    found_uid = None
    for old_uid, user in attendance.get("users", {}).items():
        raw_name = user.get("raw_display_name") or user.get("display_name", old_uid)
        if normalize_person_key(raw_name) == target_key:
            found_uid = str(old_uid)
            break

    if found_uid and found_uid != uid:
        old_user = attendance["users"].pop(found_uid)
        attendance["users"][uid] = {
            "user_id": uid,
            "display_name": fixed_name,
            "raw_display_name": member.display_name,
            "total_time": safe_int(old_user.get("total_time", 0)),
            "is_working": bool(old_user.get("is_working", False)),
            "last_clock_in": old_user.get("last_clock_in"),
        }

    if uid not in attendance["users"]:
        attendance["users"][uid] = {
            "user_id": uid,
            "display_name": fixed_name,
            "raw_display_name": member.display_name,
            "total_time": 0,
            "is_working": False,
            "last_clock_in": None,
        }
    else:
        attendance["users"][uid]["user_id"] = uid
        attendance["users"][uid]["display_name"] = fixed_name
        attendance["users"][uid]["raw_display_name"] = member.display_name
        attendance["users"][uid]["total_time"] = safe_int(attendance["users"][uid].get("total_time", 0))
        attendance["users"][uid]["is_working"] = bool(attendance["users"][uid].get("is_working", False))
        attendance["users"][uid].setdefault("last_clock_in", None)

    cleanup_and_merge_users()
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


def build_grouped_users():
    grouped = {}

    for uid, user in attendance.get("users", {}).items():
        if not isinstance(user, dict):
            continue

        raw_name = user.get("raw_display_name") or user.get("display_name", uid)
        group_key = normalize_person_key(raw_name)
        fixed_name = get_fixed_display_name(raw_name)

        if group_key not in grouped:
            grouped[group_key] = {
                "display_name": fixed_name,
                "total_time": 0,
                "current_seconds": 0,
            }

        grouped[group_key]["total_time"] += safe_int(user.get("total_time", 0))

        current_seconds = get_current_work_seconds(user)
        if current_seconds > 0:
            grouped[group_key]["current_seconds"] = max(
                grouped[group_key]["current_seconds"],
                current_seconds
            )

    return grouped


def get_working_users():
    grouped = build_grouped_users()
    rows = []

    for _, user in grouped.items():
        if user["current_seconds"] > 0:
            rows.append({
                "display_name": user["display_name"],
                "current_seconds": user["current_seconds"],
            })

    rows.sort(key=lambda x: (-x["current_seconds"], x["display_name"]))
    return rows


def get_ranking_users(limit=10):
    grouped = build_grouped_users()
    rows = []

    for _, user in grouped.items():
        total_seconds = user["total_time"] + user["current_seconds"]
        if total_seconds > 0:
            rows.append({
                "display_name": user["display_name"],
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
            cleanup_and_merge_users()

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

        cleanup_and_merge_users()

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
    cleanup_and_merge_users()
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

            fixed_name = get_fixed_display_name(interaction.user.display_name)
            role_label = get_role_label_from_name(interaction.user.display_name)

            embed = discord.Embed(title="🟢 출근 기록", color=0x2ECC71)
            embed.add_field(name=f"👤 {role_label}", value=fixed_name, inline=False)
            embed.add_field(name="🕒 출근 시간", value=now_str(), inline=False)

            record_channel = bot.get_channel(RECORD_CHANNEL_ID)
            if record_channel:
                await record_channel.send(embed=embed)

            await send_log(f"출근 완료 | {role_label} | {fixed_name}")
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

            fixed_name = get_fixed_display_name(interaction.user.display_name)
            role_label = get_role_label_from_name(interaction.user.display_name)

            embed = discord.Embed(title="🔴 퇴근 기록", color=0xE74C3C)
            embed.add_field(name=f"👤 {role_label}", value=fixed_name, inline=False)
            embed.add_field(name="🕒 퇴근 시간", value=now_str(), inline=False)
            embed.add_field(name="⏱ 이번 근무", value=format_time(worked), inline=False)

            record_channel = bot.get_channel(RECORD_CHANNEL_ID)
            if record_channel:
                await record_channel.send(embed=embed)

            await send_log(f"퇴근 완료 | {role_label} | {fixed_name}")
            await update_status_message()
            await interaction.followup.send("퇴근 처리 완료", ephemeral=True)

        except Exception as e:
            await send_log(f"오류 | 퇴근 처리 실패 | {interaction.user.display_name} | {e}")
            await interaction.followup.send("퇴근 처리 중 오류가 발생했습니다.", ephemeral=True)


@bot.tree.command(name="강제퇴근", description="근무중인 유저를 강제로 퇴근 처리합니다. (근무시간 미반영)")
async def force_clock_out(interaction: discord.Interaction, 대상: discord.Member):
    await interaction.response.defer(ephemeral=True)

    try:
        uid = str(대상.id)

        if uid not in attendance["users"] or not attendance["users"][uid].get("is_working", False):
            await interaction.followup.send(f"{대상.display_name} 님은 현재 근무중이 아닙니다.", ephemeral=True)
            return

        user = attendance["users"][uid]
        user["is_working"] = False
        user["last_clock_in"] = None
        save_data(attendance)

        target_name = get_fixed_display_name(대상.display_name)
        target_role = get_role_label_from_name(대상.display_name)
        manager_name = get_fixed_display_name(interaction.user.display_name)

        record_channel = bot.get_channel(RECORD_CHANNEL_ID)
        if record_channel:
            embed = discord.Embed(title="⚫ 강제 퇴근 기록", color=0x555555)
            embed.add_field(name=f"👤 {target_role}", value=target_name, inline=False)
            embed.add_field(name="🛠 처리자", value=manager_name, inline=False)
            embed.add_field(name="🕒 퇴근 시간", value=now_str(), inline=False)
            embed.add_field(name="🗑 처리 방식", value="이번 근무시간 미반영", inline=False)
            await record_channel.send(embed=embed)

        await send_log(f"강제퇴근 완료 | {target_role} | {target_name} | 처리자 {manager_name}")
        await update_status_message()
        await interaction.followup.send(f"{target_name} 강제퇴근 처리 완료", ephemeral=True)

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

    cleanup_and_merge_users()
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

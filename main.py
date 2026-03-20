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
        "챠니": "챠니",
        "chani": "챠니",
        "호랭": "호랭",
        "horang": "호랭",
        "혁이": "혁이",
        "hyuk": "혁이",
        "hyuki": "혁이",
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
        "호랭": "STㆍ⭐호랭",
        "혁이": "STㆍ혁이",
        "백구": "STㆍ⭐백구",
    }

    return mapping.get(key, str(name).strip())


def get_role_label(display_name: str) -> str:
    fixed_name = get_fixed_display_name(display_name)
    if fixed_name.startswith("STㆍ"):
        return "스태프"
    return "관리자"


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


def load_data():
    if not os.path.exists(DATA_FILE):
        data = {"users": {}, "status_message_id": None, "button_message_id": None}
        save_data(data)
        return data

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if "users" not in data or not isinstance(data["users"], dict):
            data["users"] = {}
        if "status_message_id" not in data:
            data["status_message_id"] = None
        if "button_message_id" not in data:
            data["button_message_id"] = None

        for uid, user in data["users"].items():
            if isinstance(user, dict):
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
                if "status_message_id" not in data:
                    data["status_message_id"] = None
                if "button_message_id" not in data:
                    data["button_message_id"] = None

                for uid, user in data["users"].items():
                    if isinstance(user, dict):
                        user.setdefault("user_id", str(uid))
                        user.setdefault("display_name", str(uid))
                        user.setdefault("raw_display_name", user.get("display_name", str(uid)))
                        user.setdefault("total_time", 0)
                        user.setdefault("is_working", False)
                        user.setdefault("last_clock_in", None)

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


def choose_keeper_uid(uids):
    numeric = [uid for uid in uids if str(uid).isdigit()]
    if numeric:
        return numeric[0]
    return str(uids[0])


def compact_duplicate_users():
    users = attendance.get("users", {})
    if not users:
        return

    groups = {}

    for uid, user in list(users.items()):
        base_name = user.get("raw_display_name") or user.get("display_name", uid)
        fixed_name = get_fixed_display_name(base_name)
        key = normalize_person_key(fixed_name)

        groups.setdefault(key, []).append({
            "uid": str(uid),
            "display_name": fixed_name,
            "raw_display_name": base_name,
            "total_time": safe_int(user.get("total_time", 0)),
            "is_working": bool(user.get("is_working", False)),
            "last_clock_in": user.get("last_clock_in"),
        })

    new_users = {}

    for _, items in groups.items():
        keeper_uid = choose_keeper_uid([item["uid"] for item in items])

        total_sum = sum(item["total_time"] for item in items)

        working_items = []
        for item in items:
            if item["is_working"] and item["last_clock_in"]:
                dt = parse_dt(item["last_clock_in"])
                if dt:
                    working_items.append((dt, item["last_clock_in"]))

        is_working = False
        last_clock_in = None

        if working_items:
            working_items.sort(key=lambda x: x[0])
            is_working = True
            last_clock_in = working_items[0][1]

        fixed_name = items[0]["display_name"]
        raw_name = items[0]["raw_display_name"]

        new_users[str(keeper_uid)] = {
            "user_id": str(keeper_uid),
            "display_name": fixed_name,
            "raw_display_name": raw_name,
            "total_time": total_sum,
            "is_working": is_working,
            "last_clock_in": last_clock_in,
        }

    attendance["users"] = new_users


def cleanup_invalid_working_states():
    changed = False

    for uid, user in attendance.get("users", {}).items():
        if user.get("is_working"):
            dt = parse_dt(user.get("last_clock_in"))
            if not dt:
                user["is_working"] = False
                user["last_clock_in"] = None
                changed = True

    if changed:
        save_data(attendance)


def find_existing_uid_by_name(display_name: str):
    target = normalize_person_key(display_name)

    for uid, user in attendance["users"].items():
        fixed_name = get_fixed_display_name(user.get("raw_display_name") or user.get("display_name", uid))
        if normalize_person_key(fixed_name) == target:
            return str(uid)

    return None


def ensure_user(user_id: int, display_name: str):
    uid = str(user_id)
    fixed_name = get_fixed_display_name(display_name)

    existing_uid = find_existing_uid_by_name(fixed_name)

    if existing_uid and existing_uid != uid and uid not in attendance["users"]:
        old_user = attendance["users"].pop(existing_uid)
        attendance["users"][uid] = {
            "user_id": uid,
            "display_name": fixed_name,
            "raw_display_name": display_name,
            "total_time": safe_int(old_user.get("total_time", 0)),
            "is_working": bool(old_user.get("is_working", False)),
            "last_clock_in": old_user.get("last_clock_in"),
        }

    if uid not in attendance["users"]:
        attendance["users"][uid] = {
            "user_id": uid,
            "display_name": fixed_name,
            "raw_display_name": display_name,
            "total_time": 0,
            "is_working": False,
            "last_clock_in": None,
        }
    else:
        attendance["users"][uid]["user_id"] = uid
        attendance["users"][uid]["display_name"] = fixed_name
        attendance["users"][uid]["raw_display_name"] = display_name
        attendance["users"][uid].setdefault("total_time", 0)
        attendance["users"][uid].setdefault("is_working", False)
        attendance["users"][uid].setdefault("last_clock_in", None)

    compact_duplicate_users()
    cleanup_invalid_working_states()
    save_data(attendance)
    return attendance["users"][uid]


def aggregate_users():
    grouped = {}

    for uid, user in attendance["users"].items():
        fixed_name = get_fixed_display_name(user.get("raw_display_name") or user.get("display_name", uid))
        key = normalize_person_key(fixed_name)

        if key not in grouped:
            grouped[key] = {
                "display_name": fixed_name,
                "base_total": 0,
                "current_seconds": 0,
            }

        base_total = safe_int(user.get("total_time", 0))
        current_sec = get_current_work_seconds(user) if user.get("is_working") else 0

        grouped[key]["base_total"] += base_total
        grouped[key]["current_seconds"] = max(grouped[key]["current_seconds"], current_sec)
        grouped[key]["display_name"] = fixed_name

    result = {}
    for key, value in grouped.items():
        result[key] = {
            "display_name": value["display_name"],
            "current_seconds": value["current_seconds"],
            "total_seconds": value["base_total"] + value["current_seconds"],
        }

    return result


def get_working_users():
    grouped = aggregate_users()
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
    grouped = aggregate_users()
    rows = []

    for _, user in grouped.items():
        if user["total_seconds"] > 0:
            rows.append({
                "display_name": user["display_name"],
                "total_seconds": user["total_seconds"],
            })

    rows.sort(key=lambda x: (-x["total_seconds"], x["display_name"]))
    return rows[:limit]


def find_user_by_query(query: str):
    query = str(query).strip()
    if not query:
        return None, None

    compact_duplicate_users()
    cleanup_invalid_working_states()

    users = attendance.get("users", {})

    if query in users:
        return query, users[query]

    normalized_query = normalize_person_key(query)
    exact_matches = []

    for uid, user in users.items():
        fixed_name = user.get("display_name", "")
        raw_name = user.get("raw_display_name", "")

        if (
            normalize_person_key(fixed_name) == normalized_query
            or normalize_person_key(raw_name) == normalized_query
        ):
            exact_matches.append((uid, user))

    if not exact_matches:
        return None, None

    working_matches = [(uid, user) for uid, user in exact_matches if user.get("is_working", False)]
    if working_matches:
        return working_matches[0]

    return exact_matches[0]


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
            compact_duplicate_users()
            cleanup_invalid_working_states()

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

        compact_duplicate_users()
        cleanup_invalid_working_states()

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
    compact_duplicate_users()
    cleanup_invalid_working_states()
    save_data(attendance)
    await ensure_status_message()


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
            role_label = get_role_label(interaction.user.display_name)

            embed = discord.Embed(title="🟢 출근 기록", color=0x2ECC71)
            embed.add_field(name=f"👤 {role_label}", value=log_name, inline=False)
            embed.add_field(name="🕒 출근 시간", value=now_str(), inline=False)

            record_channel = bot.get_channel(RECORD_CHANNEL_ID)
            if record_channel:
                await record_channel.send(embed=embed)

            await send_log(f"출근 완료 | {role_label} | {log_name}")
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
            role_label = get_role_label(interaction.user.display_name)

            embed = discord.Embed(title="🔴 퇴근 기록", color=0xE74C3C)
            embed.add_field(name=f"👤 {role_label}", value=log_name, inline=False)
            embed.add_field(name="🕒 퇴근 시간", value=now_str(), inline=False)

            record_channel = bot.get_channel(RECORD_CHANNEL_ID)
            if record_channel:
                await record_channel.send(embed=embed)

            await send_log(f"퇴근 완료 | {role_label} | {log_name}")
            await update_status_message()
            await interaction.followup.send("퇴근 처리 완료", ephemeral=True)

        except Exception as e:
            await send_log(f"오류 | 퇴근 처리 실패 | {interaction.user.display_name} | {e}")
            await interaction.followup.send("퇴근 처리 중 오류가 발생했습니다.", ephemeral=True)


@bot.tree.command(name="강제퇴근", description="근무중인 유저를 강제로 퇴근 처리합니다. (근무시간 미반영)")
async def force_clock_out(interaction: discord.Interaction, 대상: str):
    await interaction.response.defer(ephemeral=True)

    try:
        compact_duplicate_users()
        cleanup_invalid_working_states()
        save_data(attendance)

        manager_name = get_fixed_display_name(interaction.user.display_name)

        if manager_name.startswith("STㆍ"):
            await interaction.followup.send("스태프는 강제퇴근 명령어를 사용할 수 없습니다.", ephemeral=True)
            return

        uid, user = find_user_by_query(대상)

        if not user:
            await interaction.followup.send("대상을 찾을 수 없습니다. 예: 우진 / 백구 / 봉식 / 혁이", ephemeral=True)
            return

        target_name = user.get("display_name", uid)
        role_label = "스태프" if target_name.startswith("STㆍ") else "관리자"

        if not user.get("is_working"):
            normalized_target = normalize_person_key(target_name)

            for check_uid, check_user in attendance.get("users", {}).items():
                check_name = check_user.get("display_name", "")
                check_raw = check_user.get("raw_display_name", "")

                if (
                    normalize_person_key(check_name) == normalized_target
                    or normalize_person_key(check_raw) == normalized_target
                ) and check_user.get("is_working", False):
                    uid = check_uid
                    user = check_user
                    target_name = user.get("display_name", check_uid)
                    role_label = "스태프" if target_name.startswith("STㆍ") else "관리자"
                    break

        if not user.get("is_working"):
            await interaction.followup.send(f"{target_name} 님은 현재 근무중이 아닙니다.", ephemeral=True)
            return

        user["is_working"] = False
        user["last_clock_in"] = None
        save_data(attendance)

        record_channel = bot.get_channel(RECORD_CHANNEL_ID)
        if record_channel:
            embed = discord.Embed(title="⚫ 강제 퇴근 기록", color=0x555555)
            embed.add_field(name=f"👤 {role_label}", value=target_name, inline=False)
            embed.add_field(name="🛠 처리자", value=manager_name, inline=False)
            embed.add_field(name="🕒 퇴근 시간", value=now_str(), inline=False)
            embed.add_field(name="🗑 처리 방식", value="이번 근무시간 미반영", inline=False)
            await record_channel.send(embed=embed)

        await send_log(f"강제퇴근 완료 | {role_label} | {target_name} | 처리자 {manager_name} | 근무시간 미반영")
        await update_status_message()
        await interaction.followup.send(f"{target_name} 강제퇴근 처리 완료 (근무시간 미반영)", ephemeral=True)

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

    compact_duplicate_users()
    cleanup_invalid_working_states()

    for uid, user in attendance["users"].items():
        fixed_name = get_fixed_display_name(user.get("raw_display_name") or user.get("display_name", uid))
        user["display_name"] = fixed_name
        user["raw_display_name"] = user.get("raw_display_name") or fixed_name
        user["user_id"] = str(uid)

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

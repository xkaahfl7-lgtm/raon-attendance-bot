import os
import json
import time
import shutil
import asyncio
import traceback
from typing import Dict, Any, Optional, List, Tuple

import discord
from discord.ext import commands, tasks
from discord import app_commands


# =========================
# 설정
# =========================
TOKEN = os.getenv("TOKEN")

GUILD_ID = 1462457099039674498

BUTTON_CHANNEL_ID = 1481808025030492180
RECORD_CHANNEL_ID = 1479035911726563419
STATUS_CHANNEL_ID = 1479036025820156035
LOG_CHANNEL_ID = 1479382504204013568

DATA_FILE = "attendance_data.json"
DATA_BACKUP_FILE = "attendance_data.backup.json"

STATUS_UPDATE_INTERVAL = 120

EMBED_COLOR_CLOCK_IN = 0x2ECC71
EMBED_COLOR_CLOCK_OUT = 0xE74C3C
EMBED_COLOR_STATUS = 0x3498DB
EMBED_COLOR_BUTTON = 0x5865F2

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
data_lock = asyncio.Lock()

attendance_data: Dict[str, Any] = {
    "users": {},
    "status_message_id": None,
    "button_message_id": None
}


# =========================
# 기본 함수
# =========================
def now_ts() -> int:
    return int(time.time())


def ensure_data_shape(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}

    raw.setdefault("users", {})
    raw.setdefault("status_message_id", None)
    raw.setdefault("button_message_id", None)

    if not isinstance(raw["users"], dict):
        raw["users"] = {}

    fixed_users = {}
    for uid, user in raw["users"].items():
        uid = str(uid)
        if not isinstance(user, dict):
            continue

        total_time = user.get("total_time", 0)
        is_working = bool(user.get("is_working", False))
        last_clock_in = user.get("last_clock_in", None)

        try:
            total_time = int(total_time)
        except Exception:
            total_time = 0

        if last_clock_in is not None:
            try:
                last_clock_in = int(last_clock_in)
            except Exception:
                last_clock_in = None
                is_working = False

        fixed_users[uid] = {
            "user_id": uid,
            "display_name": str(user.get("display_name", f"USER-{uid}")),
            "total_time": total_time,
            "is_working": is_working,
            "last_clock_in": last_clock_in
        }

    raw["users"] = fixed_users
    return raw


def save_data(data: Dict[str, Any]) -> None:
    temp_file = f"{DATA_FILE}.tmp"

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    if os.path.exists(DATA_FILE):
        shutil.copyfile(DATA_FILE, DATA_BACKUP_FILE)

    os.replace(temp_file, DATA_FILE)


def load_data() -> Dict[str, Any]:
    if not os.path.exists(DATA_FILE):
        fresh = {
            "users": {},
            "status_message_id": None,
            "button_message_id": None
        }
        save_data(fresh)
        return fresh

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return ensure_data_shape(raw)
    except Exception:
        if os.path.exists(DATA_BACKUP_FILE):
            try:
                with open(DATA_BACKUP_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                fixed = ensure_data_shape(raw)
                save_data(fixed)
                return fixed
            except Exception:
                pass

        fresh = {
            "users": {},
            "status_message_id": None,
            "button_message_id": None
        }
        save_data(fresh)
        return fresh


def format_seconds(seconds: int) -> str:
    if seconds < 0:
        seconds = 0

    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60

    if h > 0:
        return f"{h}시간 {m:02d}분"
    if m > 0:
        return f"{m}분 {s:02d}초"
    return f"{s}초"


def is_admin(member: discord.Member) -> bool:
    return member.guild_permissions.administrator


def normalize_name(name: str) -> str:
    if not name:
        return ""
    return (
        name.replace("ㆍ", "ᆞ")
        .replace("·", "ᆞ")
        .replace("•", "ᆞ")
        .replace(" ", "")
        .strip()
        .lower()
    )


def member_log_name(member: discord.Member) -> str:
    return f"{member.display_name} ({member.id})"


def safe_member_from_uid(guild: discord.Guild, uid: str) -> Optional[discord.Member]:
    try:
        if str(uid).isdigit():
            return guild.get_member(int(uid))
    except Exception:
        return None
    return None


def get_user_record(member: discord.Member) -> Dict[str, Any]:
    uid = str(member.id)

    if uid not in attendance_data["users"]:
        attendance_data["users"][uid] = {
            "user_id": uid,
            "display_name": member.display_name,
            "total_time": 0,
            "is_working": False,
            "last_clock_in": None
        }
    else:
        attendance_data["users"][uid]["display_name"] = member.display_name

    return attendance_data["users"][uid]


def parse_time_to_seconds(text: str) -> Optional[int]:
    text = str(text).strip().lower()

    if text.endswith("시간"):
        num = text[:-2].strip()
        if num.isdigit():
            return int(num) * 3600

    if text.endswith("분"):
        num = text[:-1].strip()
        if num.isdigit():
            return int(num) * 60

    if text.endswith("초"):
        num = text[:-1].strip()
        if num.isdigit():
            return int(num)

    return None


def find_user_by_display_name(name: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    target = normalize_name(name)

    # 완전일치 우선
    for uid, user in attendance_data["users"].items():
        display_name = str(user.get("display_name", ""))
        if normalize_name(display_name) == target:
            return uid, user

    # 접두사 제거 후 비교
    for uid, user in attendance_data["users"].items():
        display_name = str(user.get("display_name", ""))
        compare_name = display_name.split("ᆞ")[-1]
        if normalize_name(compare_name) == target:
            return uid, user

    # 부분일치 허용
    for uid, user in attendance_data["users"].items():
        display_name = str(user.get("display_name", ""))
        if target and target in normalize_name(display_name):
            return uid, user

    return None


def normalize_role_label(role_text: str) -> Optional[str]:
    text = normalize_name(role_text)

    mapping = {
        "스태프": "STAFF",
        "staff": "STAFF",
        "st": "STAFF",
        "am": "AM",
        "ig": "IG",
        "gm": "GM",
        "dgm": "DGM",
        "dev": "DEV",
        "뉴비도우미": "뉴비도우미",
        "뉴비": "뉴비도우미",
        "도우미": "뉴비도우미",
        "helper": "뉴비도우미",
    }

    return mapping.get(text)


def make_manual_user_key(nickname: str) -> str:
    return f"manual_{normalize_name(nickname)}"


async def send_log(message: str) -> None:
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        print(f"[LOG CHANNEL ERROR] {message}")
        return

    try:
        if len(message) <= 1900:
            await channel.send(message)
        else:
            for i in range(0, len(message), 1900):
                await channel.send(message[i:i + 1900])
    except Exception as e:
        print(f"[SEND LOG ERROR] {type(e).__name__}: {e}")
        print(message)


# =========================
# 임베드
# =========================
def build_clock_embed(is_clock_in: bool, member: discord.Member, ts: int, elapsed: Optional[int] = None) -> discord.Embed:
    title = "🟢 출근" if is_clock_in else "🔴 퇴근"
    color = EMBED_COLOR_CLOCK_IN if is_clock_in else EMBED_COLOR_CLOCK_OUT
    time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))

    desc = (
        f"## {title}\n\n"
        f"**관리자**\n{member.mention}\n\n"
        f"**시간**\n{time_str}"
    )

    if elapsed is not None:
        desc += f"\n\n**근무시간**\n{format_seconds(elapsed)}"

    embed = discord.Embed(description=desc, color=color)
    return embed


def get_current_workers(guild: discord.Guild) -> List[Tuple[str, int]]:
    result = []

    for uid, user in attendance_data["users"].items():
        if user.get("is_working") and user.get("last_clock_in") is not None:
            elapsed = max(0, now_ts() - int(user["last_clock_in"]))
            member = safe_member_from_uid(guild, uid)
            display_name = member.display_name if member else user.get("display_name", uid)
            result.append((display_name, elapsed))

    result.sort(key=lambda x: x[1], reverse=True)
    return result


def get_ranking(guild: discord.Guild) -> List[Tuple[str, int]]:
    result = []

    for uid, user in attendance_data["users"].items():
        total_time = int(user.get("total_time", 0) or 0)
        member = safe_member_from_uid(guild, uid)
        display_name = member.display_name if member else user.get("display_name", uid)
        result.append((display_name, total_time))

    result.sort(key=lambda x: x[1], reverse=True)
    return result


def build_status_embed(guild: discord.Guild) -> discord.Embed:
    current_workers = get_current_workers(guild)
    ranking = get_ranking(guild)

    if current_workers:
        current_text = "\n".join(
            f"{name} - {format_seconds(elapsed)}"
            for name, elapsed in current_workers
        )
    else:
        current_text = "없음"

    if ranking:
        ranking_text = "\n".join(
            f"{idx}위 {name} - {format_seconds(total)}"
            for idx, (name, total) in enumerate(ranking[:10], start=1)
        )
    else:
        ranking_text = "데이터 없음"

    embed = discord.Embed(
        description=(
            f"## 📊 관리자 근무확인\n\n"
            f"### 🟢 현재 근무중\n"
            f"{current_text}\n\n"
            f"### 🏆 근무랭킹\n"
            f"{ranking_text}"
        ),
        color=EMBED_COLOR_STATUS
    )
    return embed


# =========================
# 메시지 관리
# =========================
async def get_or_create_button_message(channel: discord.TextChannel) -> discord.Message:
    msg_id = attendance_data.get("button_message_id")

    if msg_id:
        try:
            return await channel.fetch_message(int(msg_id))
        except Exception:
            pass

    embed = discord.Embed(
        description="## 🕒 RAON 관리자 출퇴근\n\n아래 버튼으로 출근 / 퇴근을 진행하세요.",
        color=EMBED_COLOR_BUTTON
    )
    msg = await channel.send(embed=embed, view=AttendanceView())
    attendance_data["button_message_id"] = msg.id
    save_data(attendance_data)
    return msg


async def get_or_create_status_message(channel: discord.TextChannel, guild: discord.Guild) -> discord.Message:
    msg_id = attendance_data.get("status_message_id")

    if msg_id:
        try:
            return await channel.fetch_message(int(msg_id))
        except Exception:
            pass

    embed = build_status_embed(guild)
    msg = await channel.send(embed=embed, view=StatusView())
    attendance_data["status_message_id"] = msg.id
    save_data(attendance_data)
    return msg


async def refresh_status_message(guild: discord.Guild) -> None:
    channel = bot.get_channel(STATUS_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        await send_log("❌ 상태메시지 갱신 실패: STATUS_CHANNEL_ID 채널을 찾지 못했습니다.")
        return

    try:
        msg = await get_or_create_status_message(channel, guild)
        await msg.edit(embed=build_status_embed(guild), view=StatusView())
    except Exception as e:
        error_text = traceback.format_exc()
        await send_log(
            f"❌ 상태메시지 갱신 오류: {type(e).__name__} / {e}\n```py\n{error_text[:1500]}\n```"
        )


async def rebuild_messages(guild: discord.Guild) -> None:
    button_channel = bot.get_channel(BUTTON_CHANNEL_ID)
    status_channel = bot.get_channel(STATUS_CHANNEL_ID)

    if isinstance(button_channel, discord.TextChannel):
        attendance_data["button_message_id"] = None
        save_data(attendance_data)
        await get_or_create_button_message(button_channel)

    if isinstance(status_channel, discord.TextChannel):
        attendance_data["status_message_id"] = None
        save_data(attendance_data)
        await get_or_create_status_message(status_channel, guild)


# =========================
# 데이터 복구 / 정리
# =========================
def cleanup_invalid_working_states() -> int:
    fixed = 0

    for user in attendance_data["users"].values():
        if user.get("is_working") and user.get("last_clock_in") is None:
            user["is_working"] = False
            fixed += 1

        if not user.get("is_working") and user.get("last_clock_in") is not None:
            user["last_clock_in"] = None
            fixed += 1

    return fixed


def merge_duplicate_names() -> int:
    users = attendance_data["users"]
    grouped: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}

    for uid, user in list(users.items()):
        key = normalize_name(user.get("display_name", ""))
        grouped.setdefault(key, []).append((uid, user))

    changed = 0

    for _, entries in grouped.items():
        if len(entries) <= 1:
            continue

        keep_uid, keep_user = entries[0]

        for uid, user in entries[1:]:
            keep_user["total_time"] = int(keep_user.get("total_time", 0)) + int(user.get("total_time", 0))

            keep_working = bool(keep_user.get("is_working"))
            user_working = bool(user.get("is_working"))

            if not keep_working and user_working:
                keep_user["is_working"] = True
                keep_user["last_clock_in"] = user.get("last_clock_in")
                keep_user["display_name"] = user.get("display_name", keep_user.get("display_name"))

            elif keep_working and user_working:
                keep_clock = keep_user.get("last_clock_in")
                user_clock = user.get("last_clock_in")

                if keep_clock is None and user_clock is not None:
                    keep_user["last_clock_in"] = user_clock
                elif keep_clock is not None and user_clock is not None:
                    keep_user["last_clock_in"] = min(int(keep_clock), int(user_clock))

            users.pop(uid, None)
            changed += 1

    return changed


def force_clock_out_user(member: discord.Member) -> Tuple[bool, str]:
    uid = str(member.id)
    user = attendance_data["users"].get(uid)

    if not user:
        return False, "해당 유저 데이터가 없습니다."

    if not user.get("is_working"):
        return False, "현재 근무중이 아닙니다."

    clock_in = user.get("last_clock_in")
    if clock_in is None:
        user["is_working"] = False
        user["last_clock_in"] = None
        return True, "근무 상태만 해제했습니다."

    elapsed = max(0, now_ts() - int(clock_in))
    user["total_time"] = int(user.get("total_time", 0)) + elapsed
    user["is_working"] = False
    user["last_clock_in"] = None

    return True, f"강제퇴근 완료 (+{format_seconds(elapsed)})"


def add_or_update_staff_by_name(nickname: str, role_label: str) -> Tuple[str, Dict[str, Any], bool]:
    found = find_user_by_display_name(nickname)
    display_name = f"{role_label}ᆞ{nickname}"

    if found:
        uid, user = found
        user["display_name"] = display_name
        return uid, user, False

    uid = make_manual_user_key(nickname)
    attendance_data["users"][uid] = {
        "user_id": uid,
        "display_name": display_name,
        "total_time": 0,
        "is_working": False,
        "last_clock_in": None
    }
    return uid, attendance_data["users"][uid], True


# =========================
# 기록 전송
# =========================
async def send_record_log(is_clock_in: bool, member: discord.Member, ts: int, elapsed: Optional[int] = None) -> None:
    channel = bot.get_channel(RECORD_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        await send_log("❌ 기록 전송 실패: RECORD_CHANNEL_ID 채널을 찾지 못했습니다.")
        return

    try:
        embed = build_clock_embed(is_clock_in, member, ts, elapsed)
        await channel.send(embed=embed)
    except Exception as e:
        error_text = traceback.format_exc()
        await send_log(
            f"❌ 기록 전송 오류: {type(e).__name__} / {e}\n```py\n{error_text[:1500]}\n```"
        )


# =========================
# 버튼
# =========================
class AttendanceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="출근", style=discord.ButtonStyle.success, custom_id="raon_clock_in")
    async def clock_in_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("서버 안에서만 사용할 수 있습니다.", ephemeral=True)
            return

        member = interaction.user

        async with data_lock:
            user = get_user_record(member)

            if user.get("is_working"):
                await interaction.response.send_message("이미 출근 중입니다.", ephemeral=True)
                return

            ts = now_ts()
            user["display_name"] = member.display_name
            user["is_working"] = True
            user["last_clock_in"] = ts
            save_data(attendance_data)

        await send_record_log(True, member, ts)
        await refresh_status_message(interaction.guild)
        await send_log(f"✅ 출근 완료: {member_log_name(member)}")
        await interaction.response.send_message("출근 처리되었습니다.", ephemeral=True)

    @discord.ui.button(label="퇴근", style=discord.ButtonStyle.danger, custom_id="raon_clock_out")
    async def clock_out_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("서버 안에서만 사용할 수 있습니다.", ephemeral=True)
            return

        member = interaction.user

        async with data_lock:
            user = get_user_record(member)

            if not user.get("is_working"):
                await interaction.response.send_message("현재 출근 중이 아닙니다.", ephemeral=True)
                return

            clock_in = user.get("last_clock_in")
            if clock_in is None:
                user["is_working"] = False
                user["last_clock_in"] = None
                save_data(attendance_data)
                await interaction.response.send_message("데이터가 꼬여 근무상태만 해제했습니다.", ephemeral=True)
                await refresh_status_message(interaction.guild)
                await send_log(f"⚠️ 퇴근 오류 복구: {member_log_name(member)}")
                return

            ts = now_ts()
            elapsed = max(0, ts - int(clock_in))

            user["total_time"] = int(user.get("total_time", 0)) + elapsed
            user["display_name"] = member.display_name
            user["is_working"] = False
            user["last_clock_in"] = None
            save_data(attendance_data)

        await send_record_log(False, member, ts, elapsed)
        await refresh_status_message(interaction.guild)
        await send_log(f"✅ 퇴근 완료: {member_log_name(member)} / +{format_seconds(elapsed)}")
        await interaction.response.send_message(
            f"퇴근 처리되었습니다. 이번 근무시간: {format_seconds(elapsed)}",
            ephemeral=True
        )


class StatusView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="현황갱신", style=discord.ButtonStyle.primary, custom_id="raon_status_refresh")
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("서버 안에서만 사용할 수 있습니다.", ephemeral=True)
            return

        if not is_admin(interaction.user):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return

        await refresh_status_message(interaction.guild)
        await interaction.response.send_message("근무 현황을 갱신했습니다.", ephemeral=True)

    @discord.ui.button(label="복구", style=discord.ButtonStyle.secondary, custom_id="raon_status_rebuild")
    async def rebuild_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("서버 안에서만 사용할 수 있습니다.", ephemeral=True)
            return

        if not is_admin(interaction.user):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return

        async with data_lock:
            fixed = cleanup_invalid_working_states()
            attendance_data["button_message_id"] = None
            attendance_data["status_message_id"] = None
            save_data(attendance_data)

        await rebuild_messages(interaction.guild)
        await refresh_status_message(interaction.guild)
        await send_log(f"🛠️ 복구 실행: {member_log_name(interaction.user)} / 상태수정 {fixed}건")
        await interaction.response.send_message("복구 완료되었습니다.", ephemeral=True)

    @discord.ui.button(label="중복삭제", style=discord.ButtonStyle.secondary, custom_id="raon_status_cleanup")
    async def cleanup_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("서버 안에서만 사용할 수 있습니다.", ephemeral=True)
            return

        if not is_admin(interaction.user):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return

        async with data_lock:
            merged = merge_duplicate_names()
            fixed = cleanup_invalid_working_states()
            save_data(attendance_data)

        await refresh_status_message(interaction.guild)
        await send_log(
            f"🧹 중복삭제 실행: {member_log_name(interaction.user)} / 중복병합 {merged}건 / 상태수정 {fixed}건"
        )
        await interaction.response.send_message(
            f"중복삭제 완료: 병합 {merged}건 / 상태수정 {fixed}건",
            ephemeral=True
        )


# =========================
# 슬래시 명령어
# =========================
@bot.tree.command(name="추가", description="관리자/스태프를 수동 추가하거나 직급을 변경합니다")
@app_commands.describe(nickname="닉네임", role="직급 예: STAFF, AM, 뉴비도우미, GM, DGM, IG, DEV")
async def slash_add_staff(interaction: discord.Interaction, nickname: str, role: str):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("서버 안에서만 사용할 수 있습니다.", ephemeral=True)
        return

    if not is_admin(interaction.user):
        await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
        return

    role_label = normalize_role_label(role)
    if not role_label:
        await interaction.response.send_message(
            "직급이 올바르지 않습니다. 사용 가능: STAFF, AM, 뉴비도우미, GM, DGM, IG, DEV",
            ephemeral=True
        )
        return

    async with data_lock:
        uid, user, created = add_or_update_staff_by_name(nickname, role_label)
        save_data(attendance_data)

    await refresh_status_message(interaction.guild)
    await send_log(
        f"👤 인원추가/수정: {member_log_name(interaction.user)} / "
        f"{user.get('display_name', uid)} / {'신규추가' if created else '직급수정'}"
    )
    await interaction.response.send_message(
        f"{user.get('display_name', uid)} 등록 완료",
        ephemeral=True
    )


@bot.tree.command(name="시간추가", description="닉네임 기준으로 시간을 추가합니다")
@app_commands.describe(nickname="닉네임", amount="예: 1시간, 30분, 45초")
async def slash_add_time(interaction: discord.Interaction, nickname: str, amount: str):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("서버 안에서만 사용할 수 있습니다.", ephemeral=True)
        return

    if not is_admin(interaction.user):
        await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
        return

    seconds = parse_time_to_seconds(amount)
    if seconds is None:
        await interaction.response.send_message("시간 형식이 올바르지 않습니다. 예: 1시간, 30분, 45초", ephemeral=True)
        return

    async with data_lock:
        found = find_user_by_display_name(nickname)
        if not found:
            await interaction.response.send_message(f"`{nickname}` 닉네임을 찾지 못했습니다.", ephemeral=True)
            return

        uid, user = found
        before = int(user.get("total_time", 0))
        user["total_time"] = before + seconds
        after = int(user["total_time"])
        save_data(attendance_data)

    await refresh_status_message(interaction.guild)
    await send_log(
        f"➕ 시간추가: {member_log_name(interaction.user)} / "
        f"{user.get('display_name', uid)} / 추가 {format_seconds(seconds)} / "
        f"변경 {format_seconds(before)} -> {format_seconds(after)}"
    )
    await interaction.response.send_message(
        f"{user.get('display_name', uid)} 시간 추가 완료\n"
        f"- 추가: {format_seconds(seconds)}\n"
        f"- 변경 후: {format_seconds(after)}",
        ephemeral=True
    )


@bot.tree.command(name="시간삭제", description="닉네임 기준으로 시간을 차감합니다")
@app_commands.describe(nickname="닉네임", amount="예: 1시간, 30분, 45초")
async def slash_delete_time(interaction: discord.Interaction, nickname: str, amount: str):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("서버 안에서만 사용할 수 있습니다.", ephemeral=True)
        return

    if not is_admin(interaction.user):
        await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
        return

    seconds = parse_time_to_seconds(amount)
    if seconds is None:
        await interaction.response.send_message("시간 형식이 올바르지 않습니다. 예: 1시간, 30분, 45초", ephemeral=True)
        return

    async with data_lock:
        found = find_user_by_display_name(nickname)
        if not found:
            await interaction.response.send_message(f"`{nickname}` 닉네임을 찾지 못했습니다.", ephemeral=True)
            return

        uid, user = found
        before = int(user.get("total_time", 0))
        user["total_time"] = max(0, before - seconds)
        after = int(user["total_time"])
        save_data(attendance_data)

    await refresh_status_message(interaction.guild)
    await send_log(
        f"➖ 시간차감: {member_log_name(interaction.user)} / "
        f"{user.get('display_name', uid)} / 차감 {format_seconds(seconds)} / "
        f"변경 {format_seconds(before)} -> {format_seconds(after)}"
    )
    await interaction.response.send_message(
        f"{user.get('display_name', uid)} 시간 차감 완료\n"
        f"- 차감: {format_seconds(seconds)}\n"
        f"- 변경 후: {format_seconds(after)}",
        ephemeral=True
    )


@bot.tree.command(name="강제퇴근", description="선택한 유저를 강제퇴근 처리합니다")
@app_commands.describe(member="강제퇴근할 유저")
async def slash_force_clock_out(interaction: discord.Interaction, member: discord.Member):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("서버 안에서만 사용할 수 있습니다.", ephemeral=True)
        return

    if not is_admin(interaction.user):
        await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
        return

    async with data_lock:
        ok, msg = force_clock_out_user(member)
        save_data(attendance_data)

    await refresh_status_message(interaction.guild)
    await send_log(f"🛑 강제퇴근: {member_log_name(interaction.user)} -> {member_log_name(member)} / {msg}")
    await interaction.response.send_message(f"{member.display_name} / {msg}", ephemeral=True)


@bot.tree.command(name="현황갱신", description="근무 현황판을 갱신합니다")
async def slash_refresh_status(interaction: discord.Interaction):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("서버 안에서만 사용할 수 있습니다.", ephemeral=True)
        return

    if not is_admin(interaction.user):
        await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
        return

    await refresh_status_message(interaction.guild)
    await send_log(f"🔄 현황갱신: {member_log_name(interaction.user)}")
    await interaction.response.send_message("근무 현황을 갱신했습니다.", ephemeral=True)


@bot.tree.command(name="근무초기화", description="현재 근무중 상태를 전부 해제합니다")
async def slash_reset_working(interaction: discord.Interaction):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("서버 안에서만 사용할 수 있습니다.", ephemeral=True)
        return

    if not is_admin(interaction.user):
        await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
        return

    count = 0

    async with data_lock:
        for user in attendance_data["users"].values():
            if user.get("is_working"):
                user["is_working"] = False
                user["last_clock_in"] = None
                count += 1
        save_data(attendance_data)

    await refresh_status_message(interaction.guild)
    await send_log(f"🚨 근무초기화 실행: {member_log_name(interaction.user)} / {count}명 해제")
    await interaction.response.send_message(f"현재 근무중 상태 {count}명을 해제했습니다.", ephemeral=True)


# =========================
# 이벤트
# =========================
@bot.event
async def setup_hook():
    bot.add_view(AttendanceView())
    bot.add_view(StatusView())

    guild_obj = discord.Object(id=GUILD_ID)
    try:
        synced = await bot.tree.sync(guild=guild_obj)
        print(f"슬래시 명령어 동기화 완료: {len(synced)}개")
    except Exception as e:
        print(f"슬래시 명령어 동기화 실패: {e}")


@bot.event
async def on_ready():
    global attendance_data
    attendance_data = load_data()

    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        print("지정한 GUILD_ID의 서버를 찾지 못했습니다.")
        return

    async with data_lock:
        cleanup_invalid_working_states()
        save_data(attendance_data)

    await rebuild_messages(guild)
    await refresh_status_message(guild)

    guild_obj = discord.Object(id=GUILD_ID)
    try:
        synced = await bot.tree.sync(guild=guild_obj)
        await send_log(f"✅ 슬래시 명령어 동기화 완료: {len(synced)}개")
    except Exception as e:
        await send_log(f"❌ 슬래시 명령어 동기화 실패: {type(e).__name__} / {e}")

    if not auto_status_updater.is_running():
        auto_status_updater.start()

    print(f"Logged in as {bot.user} ({bot.user.id})")
    await send_log("🤖 RAON 출퇴근 봇이 정상적으로 실행되었습니다.")


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    if isinstance(error, commands.CommandNotFound):
        return

    error_text = traceback.format_exc()
    await send_log(
        f"❌ 일반 명령어 오류: {type(error).__name__} / {error}\n```py\n{error_text[:1500]}\n```"
    )
    try:
        await ctx.reply(f"오류가 발생했습니다: {type(error).__name__} / {error}")
    except Exception:
        pass


@bot.event
async def on_error(event, *args, **kwargs):
    error_text = traceback.format_exc()
    await send_log(
        f"❌ 이벤트 오류: {event}\n```py\n{error_text[:1500]}\n```"
    )


# =========================
# 자동 갱신
# =========================
@tasks.loop(seconds=STATUS_UPDATE_INTERVAL)
async def auto_status_updater():
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return

    try:
        await refresh_status_message(guild)
    except Exception as e:
        error_text = traceback.format_exc()
        await send_log(
            f"❌ 자동 현황갱신 오류: {type(e).__name__} / {e}\n```py\n{error_text[:1500]}\n```"
        )


@auto_status_updater.before_loop
async def before_auto_status_updater():
    await bot.wait_until_ready()


# =========================
# 실행
# =========================
if __name__ == "__main__":
    if not TOKEN:
        print("TOKEN을 입력하거나 환경변수로 설정해주세요.")
    else:
        bot.run(TOKEN)

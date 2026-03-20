import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime
import json
import os
import asyncio
import re

import os
TOKEN = os.getenv("TOKEN")
GUILD_ID = 1462457099039674498

BUTTON_CHANNEL_ID = 1481808025030492180   # 출퇴근 버튼 채널
RECORD_CHANNEL_ID = 1479035911726563419   # 출퇴근 기록 채널
STATUS_CHANNEL_ID = 1479036025820156035   # 관리자 근무확인 채널
LOG_CHANNEL_ID = 1479382504204013568      # 봇로그 채널

DATA_FILE = "attendance_data.json"
KST = discord.utils.utcnow

intents = discord.Intents.default()
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


# =========================
# 공통 함수
# =========================
def now_ts():
    return int(datetime.utcnow().timestamp())


def format_seconds(sec: int) -> str:
    h = sec // 3600
    m = (sec % 3600) // 60
    return f"{h}시간 {m:02d}분"


def safe_send(channel, content=None, embed=None, view=None):
    return channel.send(content=content, embed=embed, view=view)


def clean_display_name(name: str) -> str:
    """표시용 이름 정리: 별 제거, 공백 정리"""
    if not name:
        return "알수없음"
    name = name.replace("⭐", "").replace("★", "").strip()
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def normalize_core_name(name: str) -> str:
    """
    중복 병합용 이름 정리:
    DEVㆍ볶음 / AMㆍ우진 / STㆍ호랭 / STAFFㆍ호랭 / ⭐호랭
    -> 볶음 / 우진 / 호랭
    """
    if not name:
        return "알수없음"

    name = clean_display_name(name)

    # 앞 직급 제거
    prefixes = [
        "DEVㆍ", "DGMㆍ", "GMㆍ", "AMㆍ", "IMㆍ", "IGㆍ",
        "STㆍ", "STAFFㆍ", "STAFF ", "ST ", "DEV ", "AM ", "IG "
    ]
    changed = True
    while changed:
        changed = False
        for p in prefixes:
            if name.upper().startswith(p.upper()):
                name = name[len(p):].strip()
                changed = True

    # 특수문자 제거
    name = name.replace("ㆍ", "").strip()
    name = re.sub(r"^[\-\|\•·\s]+", "", name).strip()
    return name if name else "알수없음"


def default_user_entry(user_id: str, display_name: str = "알수없음"):
    return {
        "user_id": str(user_id),
        "display_name": clean_display_name(display_name),
        "core_name": normalize_core_name(display_name),
        "total_seconds": 0,
        "work_count": 0,
        "is_working": False,
        "clock_in_ts": None,
        "today_seconds": 0
    }


def load_data():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({"users": {}, "panel_message_id": None, "status_message_id": None}, f, ensure_ascii=False, indent=4)

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except:
        data = {"users": {}, "panel_message_id": None, "status_message_id": None}

    if "users" not in data:
        data = {"users": {}, "panel_message_id": None, "status_message_id": None}

    return migrate_legacy_data(data)


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def merge_entries(base, extra):
    """중복 병합"""
    base["total_seconds"] += int(extra.get("total_seconds", 0))
    base["today_seconds"] += int(extra.get("today_seconds", 0))
    base["work_count"] += int(extra.get("work_count", 0))

    # 근무중인 항목이 하나라도 있으면 유지
    if extra.get("is_working"):
        if not base.get("is_working"):
            base["is_working"] = True
            base["clock_in_ts"] = extra.get("clock_in_ts")
        else:
            # 둘 다 근무중이면 더 이른 출근시간 유지
            a = base.get("clock_in_ts")
            b = extra.get("clock_in_ts")
            if a and b:
                base["clock_in_ts"] = min(a, b)
            elif b:
                base["clock_in_ts"] = b

    # display_name은 더 긴 쪽, 또는 기존 유지
    if len(extra.get("display_name", "")) > len(base.get("display_name", "")):
        base["display_name"] = clean_display_name(extra.get("display_name", base["display_name"]))

    return base


def migrate_legacy_data(data):
    """
    기존 꼬인 데이터 자동 정리
    - 이름 기준 저장 -> user_id 기준 구조로 변환
    - 별 제거
    - ST/STAFF/AM 같은 접두 중복 합치기
    - 0시간 데이터 제거
    """
    users = data.get("users", {})
    new_users = {}

    # 이미 user_id 기반인 경우도 다시 한 번 정리
    temp_merge_by_key = {}

    for key, value in users.items():
        if not isinstance(value, dict):
            continue

        # 다양한 구버전 키 대응
        display_name = (
            value.get("display_name")
            or value.get("nickname")
            or value.get("name")
            or str(key)
        )

        user_id = str(
            value.get("user_id")
            or value.get("id")
            or key
        )

        entry = default_user_entry(user_id, display_name)

        # 구버전 시간 키 대응
        total_seconds = int(
            value.get("total_seconds", value.get("total_time", 0))
        )
        today_seconds = int(value.get("today_seconds", 0))
        work_count = int(value.get("work_count", value.get("count", 0)))
        is_working = bool(value.get("is_working", value.get("working", False)))
        clock_in_ts = value.get("clock_in_ts", value.get("start_time"))

        entry["total_seconds"] = total_seconds
        entry["today_seconds"] = today_seconds
        entry["work_count"] = work_count
        entry["is_working"] = is_working
        entry["clock_in_ts"] = clock_in_ts
        entry["display_name"] = clean_display_name(display_name)
        entry["core_name"] = normalize_core_name(display_name)

        # user_id가 숫자형이면 그걸 우선 기준으로
        # 예전 데이터처럼 key가 이름이면 core_name으로 임시 병합
        if user_id.isdigit():
            merge_key = f"id:{user_id}"
        else:
            merge_key = f"name:{entry['core_name']}"

        if merge_key not in temp_merge_by_key:
            temp_merge_by_key[merge_key] = entry
        else:
            temp_merge_by_key[merge_key] = merge_entries(temp_merge_by_key[merge_key], entry)

    # 2차 병합:
    # 숫자 user_id가 없는 legacy 이름 데이터가 숫자 user_id 데이터와 core_name이 같으면 합치기
    id_entries = {}
    name_entries = []

    for mk, entry in temp_merge_by_key.items():
        if mk.startswith("id:"):
            id_entries[entry["user_id"]] = entry
        else:
            name_entries.append(entry)

    # core_name 기준으로 숫자 ID 데이터 찾아 병합
    core_to_id = {}
    for uid, entry in id_entries.items():
        core_to_id.setdefault(entry["core_name"], uid)

    for entry in name_entries:
        core = entry["core_name"]
        if core in core_to_id:
            uid = core_to_id[core]
            id_entries[uid] = merge_entries(id_entries[uid], entry)
        else:
            # 숫자 ID 없으면 이름 기반 가짜키 유지
            fake_uid = f"legacy_{core}"
            if fake_uid not in id_entries:
                entry["user_id"] = fake_uid
                id_entries[fake_uid] = entry
            else:
                id_entries[fake_uid] = merge_entries(id_entries[fake_uid], entry)

    # 0시간 & 근무중 아님 데이터 제거
    cleaned_users = {}
    for uid, entry in id_entries.items():
        total_sec = int(entry.get("total_seconds", 0))
        is_working = bool(entry.get("is_working", False))

        if total_sec <= 0 and not is_working:
            continue

        entry["display_name"] = clean_display_name(entry.get("display_name", "알수없음"))
        entry["core_name"] = normalize_core_name(entry["display_name"])
        cleaned_users[uid] = entry

    data["users"] = cleaned_users
    return data


data = load_data()
save_data(data)


# =========================
# 로그 / 채널 함수
# =========================
async def send_log(message: str):
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        try:
            await channel.send(message)
        except:
            pass


async def send_record(message: str):
    channel = bot.get_channel(RECORD_CHANNEL_ID)
    if channel:
        try:
            await channel.send(message)
        except:
            pass


# =========================
# 상태판 생성 / 갱신
# =========================
async def build_status_embed(guild: discord.Guild):
    users = data["users"]

    # 서버 멤버 기준으로 display_name 최신화
    for uid, info in users.items():
        if str(uid).isdigit():
            member = guild.get_member(int(uid))
            if member:
                info["display_name"] = clean_display_name(member.display_name)
                info["core_name"] = normalize_core_name(member.display_name)

    working_lines = []
    ranking_rows = []

    # 현재 근무중
    for uid, info in users.items():
        if info.get("is_working"):
            start_ts = info.get("clock_in_ts")
            current_sec = 0
            if start_ts:
                current_sec = max(0, now_ts() - int(start_ts))
            total_live = int(info.get("today_seconds", 0)) + current_sec
            working_lines.append(f"{info['display_name']} - {format_seconds(total_live)}")

    # 누적 랭킹
    rank_source = []
    for uid, info in users.items():
        total_sec = int(info.get("total_seconds", 0))

        # 근무중이면 현재 시간도 실시간으로 합산해서 보여줌
        if info.get("is_working") and info.get("clock_in_ts"):
            total_sec += max(0, now_ts() - int(info["clock_in_ts"]))

        if total_sec > 0:
            rank_source.append((info["display_name"], total_sec))

    rank_source.sort(key=lambda x: x[1], reverse=True)

    for idx, (name, sec) in enumerate(rank_source[:10], start=1):
        ranking_rows.append(f"{idx}위 {name} - {format_seconds(sec)}")

    embed = discord.Embed(
        title="📊 관리자 근무 현황",
        description="실시간 근무 현황판입니다.",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="🟢 현재 근무중",
        value="\n".join(working_lines) if working_lines else "현재 근무중인 관리자가 없습니다.",
        inline=False
    )

    embed.add_field(
        name="🏆 근무 랭킹",
        value="\n".join(ranking_rows) if ranking_rows else "랭킹 데이터가 없습니다.",
        inline=False
    )

    embed.set_footer(text="중복 자동정리 / 별표 제거 / 0시간 삭제 적용됨")
    return embed


async def update_status_message():
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return

    channel = bot.get_channel(STATUS_CHANNEL_ID)
    if channel is None:
        return

    embed = await build_status_embed(guild)

    message_id = data.get("status_message_id")
    if message_id:
        try:
            msg = await channel.fetch_message(message_id)
            await msg.edit(embed=embed)
            save_data(data)
            return
        except:
            pass

    msg = await channel.send(embed=embed)
    data["status_message_id"] = msg.id
    save_data(data)


# =========================
# 버튼 UI
# =========================
class AttendanceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="출근", style=discord.ButtonStyle.success, custom_id="attendance_clock_in")
    async def clock_in_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_clock_in(interaction)

    @discord.ui.button(label="퇴근", style=discord.ButtonStyle.danger, custom_id="attendance_clock_out")
    async def clock_out_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_clock_out(interaction)

    @discord.ui.button(label="근무현황", style=discord.ButtonStyle.primary, custom_id="attendance_status")
    async def status_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await update_status_message()
        await interaction.response.send_message("근무현황을 갱신했습니다.", ephemeral=True)


async def ensure_panel():
    channel = bot.get_channel(BUTTON_CHANNEL_ID)
    if channel is None:
        return

    embed = discord.Embed(
        title="🕒 RAON 출퇴근 봇",
        description="아래 버튼으로 출근 / 퇴근 / 근무현황 확인이 가능합니다.",
        color=discord.Color.green()
    )

    view = AttendanceView()
    panel_message_id = data.get("panel_message_id")

    if panel_message_id:
        try:
            msg = await channel.fetch_message(panel_message_id)
            await msg.edit(embed=embed, view=view)
            return
        except:
            pass

    msg = await channel.send(embed=embed, view=view)
    data["panel_message_id"] = msg.id
    save_data(data)


# =========================
# 출근 / 퇴근 처리
# =========================
def get_or_create_user(member: discord.Member):
    uid = str(member.id)
    if uid not in data["users"]:
        data["users"][uid] = default_user_entry(uid, member.display_name)
    else:
        data["users"][uid]["display_name"] = clean_display_name(member.display_name)
        data["users"][uid]["core_name"] = normalize_core_name(member.display_name)
    return data["users"][uid]


async def handle_clock_in(interaction: discord.Interaction):
    member = interaction.user
    user = get_or_create_user(member)

    if user.get("is_working"):
        await interaction.response.send_message("이미 출근 상태입니다.", ephemeral=True)
        return

    user["is_working"] = True
    user["clock_in_ts"] = now_ts()
    user["display_name"] = clean_display_name(member.display_name)
    user["core_name"] = normalize_core_name(member.display_name)

    save_data(data)
    await update_status_message()

    msg = f"✅ {user['display_name']} 님이 출근했습니다."
    await send_record(msg)
    await send_log(f"🟢 출근 처리 완료 - {user['display_name']}")
    await interaction.response.send_message("출근 처리 완료되었습니다.", ephemeral=True)


async def handle_clock_out(interaction: discord.Interaction):
    member = interaction.user
    uid = str(member.id)

    if uid not in data["users"]:
        await interaction.response.send_message("출근 기록이 없습니다.", ephemeral=True)
        return

    user = data["users"][uid]

    if not user.get("is_working") or not user.get("clock_in_ts"):
        await interaction.response.send_message("현재 출근 상태가 아닙니다.", ephemeral=True)
        return

    worked = max(0, now_ts() - int(user["clock_in_ts"]))
    user["total_seconds"] += worked
    user["today_seconds"] += worked
    user["work_count"] += 1
    user["is_working"] = False
    user["clock_in_ts"] = None
    user["display_name"] = clean_display_name(member.display_name)
    user["core_name"] = normalize_core_name(member.display_name)

    save_data(data)
    await update_status_message()

    msg = f"🔴 {user['display_name']} 님이 퇴근했습니다. ({format_seconds(worked)})"
    await send_record(msg)
    await send_log(f"🔴 퇴근 처리 완료 - {user['display_name']} / {format_seconds(worked)}")
    await interaction.response.send_message(f"퇴근 처리 완료: {format_seconds(worked)}", ephemeral=True)


# =========================
# 관리자용 명령어
# =========================
def is_admin(interaction: discord.Interaction):
    return interaction.user.guild_permissions.administrator


@tree.command(name="중복확인", description="중복 이름 데이터 확인")
async def duplicate_check(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
        return

    groups = {}
    for uid, info in data["users"].items():
        core = normalize_core_name(info.get("display_name", ""))
        groups.setdefault(core, []).append((uid, info))

    duplicated = {k: v for k, v in groups.items() if len(v) >= 2}

    if not duplicated:
        await interaction.response.send_message("중복 데이터가 없습니다.", ephemeral=True)
        return

    lines = []
    for core, items in duplicated.items():
        lines.append(f"**{core}**")
        for uid, info in items:
            lines.append(f"- {info.get('display_name')} / {format_seconds(int(info.get('total_seconds', 0)))} / ID:{uid}")
        lines.append("")

    text = "\n".join(lines)
    if len(text) > 1900:
        text = text[:1900] + "\n...(생략)"
    await interaction.response.send_message(text, ephemeral=True)


@tree.command(name="중복정리", description="중복 데이터 자동정리")
async def duplicate_cleanup(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
        return

    before_count = len(data["users"])
    migrated = migrate_legacy_data(data)
    data["users"] = migrated["users"]
    save_data(data)
    await update_status_message()

    after_count = len(data["users"])
    removed = before_count - after_count

    await send_log(f"🧹 중복 자동정리 완료 - 정리 전 {before_count}개 / 정리 후 {after_count}개")
    await interaction.response.send_message(
        f"중복 자동정리 완료\n정리 전: {before_count}개\n정리 후: {after_count}개\n삭제/병합: {removed}개",
        ephemeral=True
    )


@tree.command(name="강제퇴근", description="관리자 강제 퇴근")
@app_commands.describe(대상="강제퇴근할 멤버")
async def force_clock_out(interaction: discord.Interaction, 대상: discord.Member):
    if not is_admin(interaction):
        await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
        return

    uid = str(대상.id)
    if uid not in data["users"]:
        await interaction.response.send_message("해당 유저 데이터가 없습니다.", ephemeral=True)
        return

    user = data["users"][uid]

    if not user.get("is_working") or not user.get("clock_in_ts"):
        await interaction.response.send_message("해당 유저는 현재 근무중이 아닙니다.", ephemeral=True)
        return

    worked = max(0, now_ts() - int(user["clock_in_ts"]))
    user["total_seconds"] += worked
    user["today_seconds"] += worked
    user["work_count"] += 1
    user["is_working"] = False
    user["clock_in_ts"] = None
    user["display_name"] = clean_display_name(대상.display_name)
    user["core_name"] = normalize_core_name(대상.display_name)

    save_data(data)
    await update_status_message()

    await send_record(f"⛔ {user['display_name']} 님이 관리자에 의해 강제퇴근 처리되었습니다. ({format_seconds(worked)})")
    await send_log(f"⛔ 강제퇴근 완료 - {user['display_name']} / {format_seconds(worked)} / 처리자: {interaction.user.display_name}")
    await interaction.response.send_message(f"강제퇴근 완료: {user['display_name']} / {format_seconds(worked)}", ephemeral=True)


@tree.command(name="현황갱신", description="근무현황 수동 갱신")
async def refresh_status(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
        return

    await update_status_message()
    await interaction.response.send_message("근무현황을 갱신했습니다.", ephemeral=True)


# =========================
# 자동 갱신
# =========================
@tasks.loop(minutes=1)
async def auto_update_status():
    try:
        await update_status_message()
    except Exception as e:
        await send_log(f"❌ 상태판 갱신 오류: {e}")


# =========================
# 이벤트
# =========================
@bot.event
async def on_ready():
    try:
        bot.add_view(AttendanceView())
        guild = discord.Object(id=GUILD_ID)
        await tree.sync(guild=guild)
    except Exception as e:
        print("슬래시 명령어 동기화 오류:", e)

    await ensure_panel()
    await update_status_message()

    if not auto_update_status.is_running():
        auto_update_status.start()

    await send_log("🤖 RAON 출퇴근 봇이 정상적으로 실행되었습니다.")
    print(f"로그인 완료: {bot.user}")


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    """
    닉네임 바뀌어도 user_id 기준이라 데이터 안날아감
    """
    uid = str(after.id)
    if uid in data["users"]:
        data["users"][uid]["display_name"] = clean_display_name(after.display_name)
        data["users"][uid]["core_name"] = normalize_core_name(after.display_name)
        save_data(data)


# =========================
# 실행
# =========================
bot.run(TOKEN)

import discord
from discord.ext import commands, tasks
from datetime import datetime
import json
import os
import re

TOKEN = os.getenv("TOKEN")

GUILD_ID = 1462457099039674498

BUTTON_CHANNEL_ID = 1481808025030492180   # 출퇴근 버튼 채널
RECORD_CHANNEL_ID = 1479035911726563419   # 출퇴근 기록 채널
STATUS_CHANNEL_ID = 1479036025820156035   # 관리자 근무확인 채널
LOG_CHANNEL_ID = 1479382504204013568      # 봇로그 채널

DATA_FILE = "attendance_data.json"

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.message_content = False

bot = commands.Bot(command_prefix="!", intents=intents)


# =========================
# 공통 함수
# =========================
def now_ts():
    return int(datetime.utcnow().timestamp())


def format_seconds(sec: int) -> str:
    h = sec // 3600
    m = (sec % 3600) // 60
    return f"{h}시간 {m:02d}분"


def clean_display_name(name: str) -> str:
    if not name:
        return "알수없음"
    name = name.replace("⭐", "").replace("★", "").strip()
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def normalize_core_name(name: str) -> str:
    if not name:
        return "알수없음"

    name = clean_display_name(name)

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
            json.dump(
                {
                    "users": {},
                    "panel_message_id": None,
                    "status_message_id": None
                },
                f,
                ensure_ascii=False,
                indent=4
            )

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except:
        data = {
            "users": {},
            "panel_message_id": None,
            "status_message_id": None
        }

    if "users" not in data:
        data["users"] = {}
    if "panel_message_id" not in data:
        data["panel_message_id"] = None
    if "status_message_id" not in data:
        data["status_message_id"] = None

    return migrate_legacy_data(data)


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def merge_entries(base, extra):
    base["total_seconds"] += int(extra.get("total_seconds", 0))
    base["today_seconds"] += int(extra.get("today_seconds", 0))
    base["work_count"] += int(extra.get("work_count", 0))

    if extra.get("is_working"):
        if not base.get("is_working"):
            base["is_working"] = True
            base["clock_in_ts"] = extra.get("clock_in_ts")
        else:
            a = base.get("clock_in_ts")
            b = extra.get("clock_in_ts")
            if a and b:
                base["clock_in_ts"] = min(a, b)
            elif b:
                base["clock_in_ts"] = b

    if len(extra.get("display_name", "")) > len(base.get("display_name", "")):
        base["display_name"] = clean_display_name(extra.get("display_name", base["display_name"]))

    base["core_name"] = normalize_core_name(base["display_name"])
    return base


def migrate_legacy_data(data):
    users = data.get("users", {})
    temp_merge_by_key = {}

    for key, value in users.items():
        if not isinstance(value, dict):
            continue

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

        entry["total_seconds"] = int(value.get("total_seconds", value.get("total_time", 0)) or 0)
        entry["today_seconds"] = int(value.get("today_seconds", 0) or 0)
        entry["work_count"] = int(value.get("work_count", value.get("count", 0)) or 0)
        entry["is_working"] = bool(value.get("is_working", value.get("working", False)))
        entry["clock_in_ts"] = value.get("clock_in_ts", value.get("start_time"))
        entry["display_name"] = clean_display_name(display_name)
        entry["core_name"] = normalize_core_name(display_name)

        if user_id.isdigit():
            merge_key = f"id:{user_id}"
        else:
            merge_key = f"name:{entry['core_name']}"

        if merge_key not in temp_merge_by_key:
            temp_merge_by_key[merge_key] = entry
        else:
            temp_merge_by_key[merge_key] = merge_entries(temp_merge_by_key[merge_key], entry)

    id_entries = {}
    name_entries = []

    for mk, entry in temp_merge_by_key.items():
        if mk.startswith("id:"):
            id_entries[entry["user_id"]] = entry
        else:
            name_entries.append(entry)

    core_to_id = {}
    for uid, entry in id_entries.items():
        core_to_id.setdefault(entry["core_name"], uid)

    for entry in name_entries:
        core = entry["core_name"]
        if core in core_to_id:
            uid = core_to_id[core]
            id_entries[uid] = merge_entries(id_entries[uid], entry)
        else:
            fake_uid = f"legacy_{core}"
            if fake_uid not in id_entries:
                entry["user_id"] = fake_uid
                id_entries[fake_uid] = entry
            else:
                id_entries[fake_uid] = merge_entries(id_entries[fake_uid], entry)

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
# 로그 / 기록
# =========================
async def send_log(message: str):
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        try:
            await channel.send(message)
        except Exception as e:
            print(f"로그 전송 오류: {e}")


async def send_record_embed(title: str, name: str, in_time: str = None, out_time: str = None, color=discord.Color.green()):
    channel = bot.get_channel(RECORD_CHANNEL_ID)
    if channel:
        try:
            lines = [f"관리자: **{name}**"]
            if in_time:
                lines.append(f"출근시간: **{in_time}**")
            if out_time:
                lines.append(f"퇴근시간: **{out_time}**")

            embed = discord.Embed(
                title=title,
                description="\n".join(lines),
                color=color
            )
            embed.timestamp = datetime.utcnow()
            await channel.send(embed=embed)
        except Exception as e:
            print(f"출퇴근 기록 전송 오류: {e}")


# =========================
# 상태판 생성
# =========================
async def build_status_embed(guild: discord.Guild):
    users = data["users"]

    for uid, info in users.items():
        if str(uid).isdigit():
            member = guild.get_member(int(uid))
            if member:
                info["display_name"] = clean_display_name(member.display_name)
                info["core_name"] = normalize_core_name(member.display_name)

    working_lines = []

    for uid, info in users.items():
        if info.get("is_working"):
            start_ts = info.get("clock_in_ts")
            current_sec = 0
            if start_ts:
                current_sec = max(0, now_ts() - int(start_ts))
            working_lines.append(f"{info['display_name']} - {format_seconds(current_sec)}")

    merged_ranking = {}

    for uid, info in users.items():
        total_sec = int(info.get("total_seconds", 0))

        if info.get("is_working") and info.get("clock_in_ts"):
            total_sec += max(0, now_ts() - int(info["clock_in_ts"]))

        if total_sec <= 0:
            continue

        core_name = normalize_core_name(info.get("display_name", "알수없음"))
        display_name = clean_display_name(info.get("display_name", core_name))

        if core_name not in merged_ranking:
            merged_ranking[core_name] = {
                "display_name": display_name,
                "seconds": total_sec
            }
        else:
            merged_ranking[core_name]["seconds"] += total_sec

            current_name = merged_ranking[core_name]["display_name"]
            if len(display_name) > len(current_name):
                merged_ranking[core_name]["display_name"] = display_name

    rank_source = sorted(
        [(v["display_name"], v["seconds"]) for v in merged_ranking.values()],
        key=lambda x: x[1],
        reverse=True
    )

    ranking_rows = []
    for idx, (name, sec) in enumerate(rank_source[:10], start=1):
        ranking_rows.append(f"{idx}위 {name} - {format_seconds(sec)}")

    embed = discord.Embed(
        title="📊 관리자 근무확인",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="🟢 현재 근무중",
        value="\n".join(working_lines) if working_lines else "현재 근무중인 관리자가 없습니다.",
        inline=False
    )

    embed.add_field(
        name="🏆 근무랭킹",
        value="\n".join(ranking_rows) if ranking_rows else "랭킹 데이터가 없습니다.",
        inline=False
    )

    return embed


async def update_status_message():
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return

    channel = bot.get_channel(STATUS_CHANNEL_ID)
    if channel is None:
        return

    embed = await build_status_embed(guild)
    view = StatusControlView()

    message_id = data.get("status_message_id")
    if message_id:
        try:
            msg = await channel.fetch_message(message_id)
            await msg.edit(embed=embed, view=view)
            save_data(data)
            return
        except:
            pass

    msg = await channel.send(embed=embed, view=view)
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


class StatusControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="복구", style=discord.ButtonStyle.primary, custom_id="status_restore")
    async def restore_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await update_status_message()
        await interaction.followup.send("관리자 근무확인 현황판을 복구했습니다.", ephemeral=True)

    @discord.ui.button(label="중복 삭제", style=discord.ButtonStyle.secondary, custom_id="status_cleanup_duplicates")
    async def cleanup_duplicates_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        before_count = len(data["users"])
        migrated = migrate_legacy_data(data)
        data["users"] = migrated["users"]
        save_data(data)
        await update_status_message()

        after_count = len(data["users"])
        removed = before_count - after_count

        await send_log(f"중복 삭제 실행됨 / 정리 전 {before_count}개 / 정리 후 {after_count}개")
        await interaction.followup.send(
            f"중복 삭제 완료\n정리 전: {before_count}개\n정리 후: {after_count}개\n삭제/병합: {removed}개",
            ephemeral=True
        )


# =========================
# 패널 생성
# =========================
async def ensure_panel():
    channel = bot.get_channel(BUTTON_CHANNEL_ID)
    if channel is None:
        return

    embed = discord.Embed(
        title="🕒 RAON 출퇴근",
        description="아래 버튼으로 출근 / 퇴근을 진행하세요.",
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
    await interaction.response.defer(ephemeral=True)

    member = interaction.user
    user = get_or_create_user(member)

    if user.get("is_working"):
        await interaction.followup.send("이미 출근 상태입니다.", ephemeral=True)
        return

    user["is_working"] = True
    user["clock_in_ts"] = now_ts()
    user["display_name"] = clean_display_name(member.display_name)
    user["core_name"] = normalize_core_name(member.display_name)

    save_data(data)
    await update_status_message()

    in_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    await send_record_embed(
        title="✅ 출근기록",
        name=user["display_name"],
        in_time=in_time,
        color=discord.Color.green()
    )

    await send_log(f"{user['display_name']} 출근했습니다")
    await interaction.followup.send("출근 처리 완료되었습니다.", ephemeral=True)


async def handle_clock_out(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    member = interaction.user
    uid = str(member.id)

    if uid not in data["users"]:
        await interaction.followup.send("출근 기록이 없습니다.", ephemeral=True)
        return

    user = data["users"][uid]

    if not user.get("is_working") or not user.get("clock_in_ts"):
        await interaction.followup.send("현재 출근 상태가 아닙니다.", ephemeral=True)
        return

    worked = max(0, now_ts() - int(user["clock_in_ts"]))
    in_time_text = datetime.fromtimestamp(int(user["clock_in_ts"])).strftime("%Y-%m-%d %H:%M:%S")
    out_time_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    user["total_seconds"] += worked
    user["today_seconds"] += worked
    user["work_count"] += 1
    user["is_working"] = False
    user["clock_in_ts"] = None
    user["display_name"] = clean_display_name(member.display_name)
    user["core_name"] = normalize_core_name(member.display_name)

    save_data(data)
    await update_status_message()

    await send_record_embed(
        title="🔴 퇴근기록",
        name=user["display_name"],
        in_time=in_time_text,
        out_time=out_time_text,
        color=discord.Color.red()
    )

    await send_log(f"{user['display_name']} 퇴근했습니다")
    await interaction.followup.send(f"퇴근 처리 완료: {format_seconds(worked)}", ephemeral=True)


# =========================
# 슬래시 명령어
# =========================
def is_admin(interaction: discord.Interaction):
    return interaction.user.guild_permissions.administrator


@bot.tree.command(name="강제퇴근", description="관리자 강제 퇴근")
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
    in_time_text = datetime.fromtimestamp(int(user["clock_in_ts"])).strftime("%Y-%m-%d %H:%M:%S")
    out_time_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    user["total_seconds"] += worked
    user["today_seconds"] += worked
    user["work_count"] += 1
    user["is_working"] = False
    user["clock_in_ts"] = None
    user["display_name"] = clean_display_name(대상.display_name)
    user["core_name"] = normalize_core_name(대상.display_name)

    save_data(data)
    await update_status_message()

    await send_record_embed(
        title="⛔ 강제퇴근기록",
        name=user["display_name"],
        in_time=in_time_text,
        out_time=out_time_text,
        color=discord.Color.orange()
    )

    await send_log(f"{user['display_name']} 퇴근했습니다")
    await interaction.response.send_message(
        f"강제퇴근 완료: {user['display_name']} / {format_seconds(worked)}",
        ephemeral=True
    )


@bot.tree.command(name="현황갱신", description="근무현황 수동 갱신")
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
        await send_log(f"오류: 상태판 갱신 실패 / {e}")


# =========================
# 이벤트
# =========================
@bot.event
async def on_ready():
    try:
        bot.add_view(AttendanceView())
        bot.add_view(StatusControlView())
        synced = await bot.tree.sync()
        print(f"슬래시 명령어 동기화 완료: {len(synced)}개")
    except Exception as e:
        print("슬래시 명령어 동기화 오류:", e)

    await ensure_panel()
    await update_status_message()

    if not auto_update_status.is_running():
        auto_update_status.start()

    await send_log("봇 실행되었습니다")
    print(f"로그인 완료: {bot.user}")


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    uid = str(after.id)
    if uid in data["users"]:
        data["users"][uid]["display_name"] = clean_display_name(after.display_name)
        data["users"][uid]["core_name"] = normalize_core_name(after.display_name)
        save_data(data)


# =========================
# 실행
# =========================
bot.run(TOKEN)

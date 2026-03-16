import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# =========================
# 기본 설정
# =========================
TOKEN = os.getenv("TOKEN")

if not TOKEN:
    raise ValueError("환경변수 TOKEN이 비어 있습니다. Render > Environment에 TOKEN을 추가하세요.")

GUILD_ID = 1462457099039674498

# 버튼 패널 올릴 채널
BUTTON_CHANNEL_ID = 1481808025030492180

# 출퇴근 기록 채널
RECORD_CHANNEL_ID = 1479035911726563419

# 관리자 근무현황 채널
STATUS_CHANNEL_ID = 1479036025820156035

# 봇 로그 채널
LOG_CHANNEL_ID = 1479382504204013568

DATA_FILE = "attendance_data.json"
KST = ZoneInfo("Asia/Seoul")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


# =========================
# 데이터 로드/저장
# =========================
def load_data():
    if not os.path.exists(DATA_FILE):
        return {"users": {}, "status_message_id": None, "panel_message_id": None}

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"users": {}, "status_message_id": None, "panel_message_id": None}

    if not isinstance(data, dict):
        return {"users": {}, "status_message_id": None, "panel_message_id": None}

    users = data.get("users", {})
    if not isinstance(users, dict):
        users = {}

    cleaned_users = {}
    for user_id, info in users.items():
        if not str(user_id).isdigit():
            continue
        if not isinstance(info, dict):
            continue

        cleaned_users[str(user_id)] = {
            "name": str(info.get("name", "알 수 없음")),
            "total_seconds": int(info.get("total_seconds", 0)),
            "is_working": bool(info.get("is_working", False)),
            "start_time": info.get("start_time", None),
        }

    return {
        "users": cleaned_users,
        "status_message_id": data.get("status_message_id"),
        "panel_message_id": data.get("panel_message_id"),
    }


db = load_data()


def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=4)


# =========================
# 시간 / 포맷 함수
# =========================
def now_kst():
    return datetime.now(KST)


def now_utc():
    return datetime.now(timezone.utc)


def format_seconds(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}시간 {m}분"


def parse_iso_datetime(value: str):
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


# =========================
# 유저 데이터
# =========================
def get_user_record(member: discord.Member):
    user_id = str(member.id)

    if user_id not in db["users"]:
        db["users"][user_id] = {
            "name": member.display_name,
            "total_seconds": 0,
            "is_working": False,
            "start_time": None,
        }
    else:
        db["users"][user_id]["name"] = member.display_name
        db["users"][user_id].setdefault("total_seconds", 0)
        db["users"][user_id].setdefault("is_working", False)
        db["users"][user_id].setdefault("start_time", None)

    return db["users"][user_id]


def get_live_total_seconds(user_info: dict) -> int:
    total = int(user_info.get("total_seconds", 0))

    if user_info.get("is_working") and user_info.get("start_time"):
        started = parse_iso_datetime(user_info["start_time"])
        if started:
            elapsed = int((now_utc() - started).total_seconds())
            if elapsed > 0:
                total += elapsed

    return total


# =========================
# 채널 전송
# =========================
async def send_log(message: str):
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        try:
            await channel.send(message)
        except Exception:
            pass


async def send_record(message: str):
    channel = bot.get_channel(RECORD_CHANNEL_ID)
    if channel:
        try:
            await channel.send(message)
        except Exception:
            pass


# =========================
# 현황판 생성
# =========================
def build_status_embed(guild: discord.Guild):
    current_workers = []
    ranking_data = []

    for user_id, info in db["users"].items():
        total_live = get_live_total_seconds(info)
        ranking_data.append((str(user_id), info["name"], total_live, info.get("is_working", False)))

        if info.get("is_working"):
            current_workers.append(info["name"])

    ranking_data.sort(key=lambda x: x[2], reverse=True)

    embed = discord.Embed(
        title="📊 관리자 근무현황",
        color=discord.Color.light_grey()
    )

    # 현재 근무중
    if current_workers:
        current_text = "\n".join([f"• {name}" for name in current_workers])
    else:
        current_text = "현재 근무중인 관리자가 없습니다."
    embed.add_field(name="🟢 현재 근무중", value=current_text, inline=False)

    # 근무 랭킹
    if ranking_data:
        rank_lines = []
        for idx, (_, name, total_sec, _) in enumerate(ranking_data[:10], start=1):
            icon = "🏆 " if idx == 1 else ""
            rank_lines.append(f"{idx}위 {icon}{name} - {format_seconds(total_sec)}")
        rank_text = "\n".join(rank_lines)
    else:
        rank_text = "기록 없음"
    embed.add_field(name="🏆 근무 랭킹", value=rank_text, inline=False)

    # 누적근무시간
    if ranking_data:
        total_lines = []
        for _, name, total_sec, _ in ranking_data[:10]:
            total_lines.append(f"{name} - {format_seconds(total_sec)}")
        total_text = "\n".join(total_lines)
    else:
        total_text = "기록 없음"
    embed.add_field(name="⏰ 누적근무시간", value=total_text, inline=False)

    embed.set_footer(text=f"업데이트 시간: {now_kst().strftime('%Y-%m-%d %H:%M:%S')}")
    return embed


async def update_status_message():
    channel = bot.get_channel(STATUS_CHANNEL_ID)
    guild = bot.get_guild(GUILD_ID)

    if channel is None or guild is None:
        return

    embed = build_status_embed(guild)
    message_id = db.get("status_message_id")

    if message_id:
        try:
            msg = await channel.fetch_message(message_id)
            await msg.edit(embed=embed)
            return
        except Exception:
            db["status_message_id"] = None
            save_data()

    try:
        msg = await channel.send(embed=embed)
        db["status_message_id"] = msg.id
        save_data()
    except Exception as e:
        await send_log(f"❌ 현황판 생성 실패: {e}")


# =========================
# 출근 / 퇴근
# =========================
async def do_clock_in(member: discord.Member):
    user = get_user_record(member)

    if user["is_working"]:
        return False, "이미 출근 상태입니다."

    user["name"] = member.display_name
    user["is_working"] = True
    user["start_time"] = now_utc().isoformat()

    save_data()
    await update_status_message()

    await send_record(
        f"🟢 출근 | {member.mention} ({member.display_name}) | {now_kst().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await send_log(f"✅ 출근 처리 완료: {member.display_name}")

    return True, "출근 처리되었습니다."


async def do_clock_out(member: discord.Member):
    user = get_user_record(member)

    if not user["is_working"]:
        return False, "출근한 기록이 없습니다."

    added_seconds = 0
    if user["start_time"]:
        started = parse_iso_datetime(user["start_time"])
        if started:
            added_seconds = int((now_utc() - started).total_seconds())
            if added_seconds < 0:
                added_seconds = 0

    user["total_seconds"] += added_seconds
    user["is_working"] = False
    user["start_time"] = None
    user["name"] = member.display_name

    save_data()
    await update_status_message()

    await send_record(
        f"🔴 퇴근 | {member.mention} ({member.display_name}) | 이번 근무: {format_seconds(added_seconds)} | 누적: {format_seconds(user['total_seconds'])} | {now_kst().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await send_log(
        f"✅ 퇴근 처리 완료: {member.display_name} / 이번근무 {format_seconds(added_seconds)} / 누적 {format_seconds(user['total_seconds'])}"
    )

    return True, f"퇴근 처리되었습니다.\n이번 근무: {format_seconds(added_seconds)}\n누적: {format_seconds(user['total_seconds'])}"


# =========================
# 버튼 뷰
# =========================
class AttendanceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="출근", style=discord.ButtonStyle.success, custom_id="attendance_clock_in")
    async def clock_in_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        success, msg = await do_clock_in(interaction.user)
        if success:
            await interaction.response.send_message(f"✅ {msg}", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ {msg}", ephemeral=True)

    @discord.ui.button(label="퇴근", style=discord.ButtonStyle.danger, custom_id="attendance_clock_out")
    async def clock_out_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        success, msg = await do_clock_out(interaction.user)
        if success:
            await interaction.response.send_message(f"✅ {msg}", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ {msg}", ephemeral=True)

    @discord.ui.button(label="현황갱신", style=discord.ButtonStyle.primary, custom_id="attendance_refresh")
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await update_status_message()
        await interaction.response.send_message("🔄 근무현황을 갱신했습니다.", ephemeral=True)


# =========================
# 패널 생성/복구
# =========================
async def ensure_panel_message():
    channel = bot.get_channel(BUTTON_CHANNEL_ID)
    if channel is None:
        return

    panel_message_id = db.get("panel_message_id")

    if panel_message_id:
        try:
            msg = await channel.fetch_message(panel_message_id)
            await msg.edit(view=AttendanceView())
            return
        except Exception:
            db["panel_message_id"] = None
            save_data()

    embed = discord.Embed(
        title="RAON 출퇴근 봇",
        description="아래 버튼으로 출근 / 퇴근 / 현황갱신을 사용할 수 있습니다.",
        color=discord.Color.blue()
    )

    try:
        msg = await channel.send(embed=embed, view=AttendanceView())
        db["panel_message_id"] = msg.id
        save_data()
    except Exception as e:
        await send_log(f"❌ 버튼 패널 생성 실패: {e}")


# =========================
# 슬래시 명령어
# =========================
@tree.command(name="출근", description="출근 처리")
async def slash_clock_in(interaction: discord.Interaction):
    success, msg = await do_clock_in(interaction.user)
    if success:
        await interaction.response.send_message(f"✅ {msg}", ephemeral=True)
    else:
        await interaction.response.send_message(f"⚠️ {msg}", ephemeral=True)


@tree.command(name="퇴근", description="퇴근 처리")
async def slash_clock_out(interaction: discord.Interaction):
    success, msg = await do_clock_out(interaction.user)
    if success:
        await interaction.response.send_message(f"✅ {msg}", ephemeral=True)
    else:
        await interaction.response.send_message(f"⚠️ {msg}", ephemeral=True)


@tree.command(name="현황갱신", description="관리자 근무현황 갱신")
async def slash_refresh(interaction: discord.Interaction):
    await update_status_message()
    await interaction.response.send_message("🔄 근무현황을 갱신했습니다.", ephemeral=True)


@tree.command(name="근무패널", description="출퇴근 버튼 패널 다시 생성")
@app_commands.checks.has_permissions(administrator=True)
async def slash_panel(interaction: discord.Interaction):
    channel = bot.get_channel(BUTTON_CHANNEL_ID)
    if channel is None:
        await interaction.response.send_message("버튼 채널을 찾을 수 없습니다.", ephemeral=True)
        return

    embed = discord.Embed(
        title="RAON 출퇴근 봇",
        description="아래 버튼으로 출근 / 퇴근 / 현황갱신을 사용할 수 있습니다.",
        color=discord.Color.blue()
    )

    msg = await channel.send(embed=embed, view=AttendanceView())
    db["panel_message_id"] = msg.id
    save_data()

    await interaction.response.send_message("✅ 근무 패널을 다시 생성했습니다.", ephemeral=True)


@tree.command(name="근무초기화", description="특정 유저 근무 데이터 삭제")
@app_commands.describe(유저="삭제할 유저")
@app_commands.checks.has_permissions(administrator=True)
async def slash_reset_user(interaction: discord.Interaction, 유저: discord.Member):
    user_id = str(유저.id)
    if user_id in db["users"]:
        del db["users"][user_id]
        save_data()
        await update_status_message()
        await interaction.response.send_message(f"🗑️ {유저.display_name} 데이터를 삭제했습니다.", ephemeral=True)
        await send_log(f"🗑️ 근무 데이터 삭제: {유저.display_name}")
    else:
        await interaction.response.send_message("해당 유저 데이터가 없습니다.", ephemeral=True)


@tree.command(name="근무정리", description="서버에 없는 유저 데이터 정리")
@app_commands.checks.has_permissions(administrator=True)
async def slash_cleanup(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("서버 안에서만 사용할 수 있습니다.", ephemeral=True)
        return

    remove_ids = []
    for user_id in list(db["users"].keys()):
        member = guild.get_member(int(user_id))
        if member is None:
            remove_ids.append(user_id)

    for user_id in remove_ids:
        del db["users"][user_id]

    save_data()
    await update_status_message()

    await interaction.response.send_message(
        f"🧹 서버에 없는 유저 데이터 {len(remove_ids)}개를 정리했습니다.",
        ephemeral=True
    )
    await send_log(f"🧹 근무 데이터 정리 완료: {len(remove_ids)}개 삭제")


# =========================
# 에러 처리
# =========================
@slash_panel.error
@slash_reset_user.error
@slash_cleanup.error
async def admin_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        if not interaction.response.is_done():
            await interaction.response.send_message("이 명령어는 관리자만 사용할 수 있습니다.", ephemeral=True)
    else:
        if not interaction.response.is_done():
            await interaction.response.send_message("명령어 처리 중 오류가 발생했습니다.", ephemeral=True)
        await send_log(f"❌ 명령어 오류: {error}")


# =========================
# 자동 갱신
# =========================
@tasks.loop(minutes=1)
async def auto_update_status():
    await update_status_message()


# =========================
# 이벤트
# =========================
@bot.event
async def on_ready():
    try:
        guild_obj = discord.Object(id=GUILD_ID)
        synced = await tree.sync(guild=guild_obj)
        print(f"슬래시 명령어 동기화 완료: {len(synced)}개")
    except Exception as e:
        print(f"슬래시 명령어 동기화 실패: {e}")

    bot.add_view(AttendanceView())

    if not auto_update_status.is_running():
        auto_update_status.start()

    await ensure_panel_message()
    await update_status_message()
    await send_log("🤖 RAON 출퇴근 봇이 정상적으로 실행되었습니다.")
    print(f"Logged in as {bot.user}")


@bot.event
async def on_member_remove(member: discord.Member):
    user_id = str(member.id)
    if user_id in db["users"]:
        del db["users"][user_id]
        save_data()
        await update_status_message()
        await send_log(f"🧹 서버 퇴장 유저 데이터 자동 삭제: {member.display_name}")


bot.run(TOKEN)

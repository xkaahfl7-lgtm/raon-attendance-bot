import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime
import json
import os
import copy

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

bot = commands.Bot(command_prefix="!", intents=intents)


# =========================
# 기본 함수
# =========================
def now_ts() -> int:
    return int(datetime.utcnow().timestamp())


def format_seconds(sec: int) -> str:
    if sec < 0:
        sec = 0
    h = sec // 3600
    m = (sec % 3600) // 60
    return f"{h}시간 {m:02d}분"


def load_data():
    default_data = {
        "users": {},
        "status_message_id": None,
        "button_message_id": None
    }

    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(default_data, f, ensure_ascii=False, indent=4)
        return default_data

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = default_data

    if "users" not in data:
        data["users"] = {}
    if "status_message_id" not in data:
        data["status_message_id"] = None
    if "button_message_id" not in data:
        data["button_message_id"] = None

    return data


def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def get_display_name(member: discord.Member) -> str:
    return member.display_name.strip()


def get_or_create_user(member: discord.Member):
    uid = str(member.id)

    if uid not in data["users"]:
        data["users"][uid] = {
            "user_id": uid,
            "display_name": get_display_name(member),
            "total_time": 0,
            "is_working": False,
            "last_clock_in": None
        }
    else:
        data["users"][uid]["display_name"] = get_display_name(member)

    return data["users"][uid]


def cleanup_duplicate_users():
    """
    user_id 기준으로만 정리
    같은 user_id가 여러 번 꼬여있으면 하나로 합침
    이름은 가장 최근 표시명 유지
    """
    users = data.get("users", {})
    cleaned = {}

    for key, user in users.items():
        if not isinstance(user, dict):
            continue

        uid = str(user.get("user_id", key))
        display_name = user.get("display_name", str(uid))
        total_time = int(user.get("total_time", 0) or 0)
        is_working = bool(user.get("is_working", False))
        last_clock_in = user.get("last_clock_in", None)

        if uid not in cleaned:
            cleaned[uid] = {
                "user_id": uid,
                "display_name": display_name,
                "total_time": total_time,
                "is_working": is_working,
                "last_clock_in": last_clock_in
            }
        else:
            cleaned[uid]["total_time"] += total_time

            # 이름은 더 긴 쪽 우선
            if len(display_name) >= len(cleaned[uid]["display_name"]):
                cleaned[uid]["display_name"] = display_name

            # 근무중 상태 합치기
            if is_working:
                if not cleaned[uid]["is_working"]:
                    cleaned[uid]["is_working"] = True
                    cleaned[uid]["last_clock_in"] = last_clock_in
                else:
                    a = cleaned[uid]["last_clock_in"]
                    b = last_clock_in
                    if a is None:
                        cleaned[uid]["last_clock_in"] = b
                    elif b is not None:
                        cleaned[uid]["last_clock_in"] = min(int(a), int(b))

    data["users"] = cleaned
    save_data()


def calc_current_work_time(user: dict) -> int:
    if not user.get("is_working"):
        return 0

    clock_in = user.get("last_clock_in")
    if clock_in is None:
        return 0

    return max(0, now_ts() - int(clock_in))


async def send_log(msg: str):
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if not channel:
        return
    try:
        await channel.send(msg)
    except Exception as e:
        print(f"로그 전송 오류: {e}")


async def send_record_embed(title: str, name: str, in_time=None, out_time=None, color=discord.Color.green()):
    channel = bot.get_channel(RECORD_CHANNEL_ID)
    if not channel:
        return

    desc = [f"관리자: **{name}**"]
    if in_time:
        desc.append(f"출근시간: **{in_time}**")
    if out_time:
        desc.append(f"퇴근시간: **{out_time}**")

    embed = discord.Embed(
        title=title,
        description="\n".join(desc),
        color=color
    )
    embed.timestamp = datetime.utcnow()

    try:
        await channel.send(embed=embed)
    except Exception as e:
        print(f"출퇴근 기록 전송 오류: {e}")


# =========================
# 관리자 근무확인
# =========================
async def build_status_embed():
    users = data.get("users", {})

    current_rows = []
    ranking_rows = []

    for uid, user in users.items():
        if user.get("is_working"):
            current_time = calc_current_work_time(user)
            current_rows.append((user.get("display_name", uid), current_time))

    current_rows.sort(key=lambda x: x[1], reverse=True)

    ranking_source = []
    for uid, user in users.items():
        total_time = int(user.get("total_time", 0) or 0)
        if user.get("is_working"):
            total_time += calc_current_work_time(user)

        ranking_source.append((user.get("display_name", uid), total_time))

    ranking_source.sort(key=lambda x: x[1], reverse=True)

    for name, sec in current_rows:
        ranking_dummy = f"{name} - {format_seconds(sec)}"
        current_rows[current_rows.index((name, sec))] = ranking_dummy

    for idx, (name, sec) in enumerate(ranking_source[:10], start=1):
        ranking_rows.append(f"{idx}위 {name} - {format_seconds(sec)}")

    embed = discord.Embed(
        title="📊 관리자 근무확인",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="🟢 현재 근무중",
        value="\n".join(current_rows) if current_rows else "현재 근무중인 관리자가 없습니다.",
        inline=False
    )

    embed.add_field(
        name="🏆 근무랭킹",
        value="\n".join(ranking_rows) if ranking_rows else "랭킹 데이터가 없습니다.",
        inline=False
    )

    return embed


async def update_status_message(force_new=False):
    channel = bot.get_channel(STATUS_CHANNEL_ID)
    if not channel:
        return

    embed = await build_status_embed()
    view = StatusView()

    if force_new:
        data["status_message_id"] = None
        save_data()

    status_message_id = data.get("status_message_id")

    if status_message_id:
        try:
            msg = await channel.fetch_message(status_message_id)
            await msg.edit(embed=embed, view=view)
            return
        except Exception:
            pass

    new_msg = await channel.send(embed=embed, view=view)
    data["status_message_id"] = new_msg.id
    save_data()


# =========================
# 출퇴근 버튼
# =========================
class AttendanceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="출근", style=discord.ButtonStyle.success, custom_id="clock_in_btn")
    async def clock_in_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = get_or_create_user(interaction.user)

        if user.get("is_working"):
            await interaction.response.send_message("이미 출근 상태입니다.", ephemeral=True)
            return

        user["display_name"] = get_display_name(interaction.user)
        user["is_working"] = True
        user["last_clock_in"] = now_ts()

        save_data()
        await update_status_message()

        in_time_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        await send_record_embed(
            "✅ 출근기록",
            user["display_name"],
            in_time=in_time_text,
            color=discord.Color.green()
        )
        await send_log(f"✅ {user['display_name']} 출근")

        await interaction.response.send_message("출근 처리 완료", ephemeral=True)

    @discord.ui.button(label="퇴근", style=discord.ButtonStyle.danger, custom_id="clock_out_btn")
    async def clock_out_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = str(interaction.user.id)

        if uid not in data["users"]:
            await interaction.response.send_message("출근 기록이 없습니다.", ephemeral=True)
            return

        user = data["users"][uid]

        if not user.get("is_working") or user.get("last_clock_in") is None:
            await interaction.response.send_message("현재 출근 상태가 아닙니다.", ephemeral=True)
            return

        worked = calc_current_work_time(user)
        in_time_text = datetime.fromtimestamp(int(user["last_clock_in"])).strftime("%Y-%m-%d %H:%M:%S")
        out_time_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        user["display_name"] = get_display_name(interaction.user)
        user["total_time"] = int(user.get("total_time", 0) or 0) + worked
        user["is_working"] = False
        user["last_clock_in"] = None

        save_data()
        await update_status_message()

        await send_record_embed(
            "🔴 퇴근기록",
            user["display_name"],
            in_time=in_time_text,
            out_time=out_time_text,
            color=discord.Color.red()
        )
        await send_log(f"🔴 {user['display_name']} 퇴근 / {format_seconds(worked)}")

        await interaction.response.send_message(f"퇴근 처리 완료 / {format_seconds(worked)}", ephemeral=True)


class StatusView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="복구", style=discord.ButtonStyle.primary, custom_id="restore_status_btn")
    async def restore_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        await update_status_message(force_new=True)
        await send_log("🛠️ 관리자 근무확인 복구 실행")

        await interaction.followup.send("관리자 근무확인 복구 완료", ephemeral=True)

    @discord.ui.button(label="중복삭제", style=discord.ButtonStyle.secondary, custom_id="cleanup_duplicate_btn")
    async def cleanup_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        before_users = copy.deepcopy(data["users"])
        before_count = len(before_users)

        cleanup_duplicate_users()

        after_count = len(data["users"])
        merged_count = before_count - after_count

        await update_status_message()
        await send_log(f"🧹 중복삭제 실행 / 정리 전: {before_count} / 정리 후: {after_count} / 병합: {merged_count}")

        await interaction.followup.send(
            f"중복삭제 완료\n정리 전: {before_count}개\n정리 후: {after_count}개\n병합: {merged_count}개",
            ephemeral=True
        )


async def ensure_button_message():
    channel = bot.get_channel(BUTTON_CHANNEL_ID)
    if not channel:
        return

    embed = discord.Embed(
        title="🕒 RAON 출퇴근",
        description="아래 버튼으로 출근 / 퇴근을 진행하세요.",
        color=discord.Color.green()
    )

    view = AttendanceView()

    button_message_id = data.get("button_message_id")

    if button_message_id:
        try:
            msg = await channel.fetch_message(button_message_id)
            await msg.edit(embed=embed, view=view)
            return
        except Exception:
            pass

    new_msg = await channel.send(embed=embed, view=view)
    data["button_message_id"] = new_msg.id
    save_data()


# =========================
# 슬래시 명령어
# =========================
def is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.administrator


@bot.tree.command(name="강제퇴근", description="관리자를 강제로 퇴근 처리합니다")
@app_commands.describe(대상="강제퇴근할 관리자")
async def force_clock_out(interaction: discord.Interaction, 대상: discord.Member):
    if not is_admin(interaction):
        await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
        return

    uid = str(대상.id)

    if uid not in data["users"]:
        await interaction.response.send_message("해당 관리자 데이터가 없습니다.", ephemeral=True)
        return

    user = data["users"][uid]

    if not user.get("is_working") or user.get("last_clock_in") is None:
        await interaction.response.send_message("해당 관리자는 현재 근무중이 아닙니다.", ephemeral=True)
        return

    worked = calc_current_work_time(user)
    in_time_text = datetime.fromtimestamp(int(user["last_clock_in"])).strftime("%Y-%m-%d %H:%M:%S")
    out_time_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    user["display_name"] = get_display_name(대상)
    user["total_time"] = int(user.get("total_time", 0) or 0) + worked
    user["is_working"] = False
    user["last_clock_in"] = None

    save_data()
    await update_status_message()

    await send_record_embed(
        "⛔ 강제퇴근기록",
        user["display_name"],
        in_time=in_time_text,
        out_time=out_time_text,
        color=discord.Color.orange()
    )
    await send_log(f"⛔ {user['display_name']} 강제퇴근 / {format_seconds(worked)}")

    await interaction.response.send_message(
        f"강제퇴근 완료 / {user['display_name']} / {format_seconds(worked)}",
        ephemeral=True
    )


@bot.tree.command(name="현황갱신", description="관리자 근무확인 메시지를 갱신합니다")
async def refresh_status(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
        return

    cleanup_duplicate_users()
    await update_status_message()
    await interaction.response.send_message("관리자 근무확인 갱신 완료", ephemeral=True)


# =========================
# 자동 갱신
# =========================
@tasks.loop(minutes=1)
async def auto_status_update():
    try:
        await update_status_message()
    except Exception as e:
        await send_log(f"❌ 상태판 자동갱신 오류: {e}")


# =========================
# 이벤트
# =========================
@bot.event
async def on_ready():
    print(f"로그인 완료: {bot.user}")

    try:
        bot.add_view(AttendanceView())
        bot.add_view(StatusView())
        synced = await bot.tree.sync()
        print(f"슬래시 명령어 동기화 완료: {len(synced)}개")
    except Exception as e:
        print(f"슬래시 동기화 오류: {e}")

    cleanup_duplicate_users()
    await ensure_button_message()
    await update_status_message()

    if not auto_status_update.is_running():
        auto_status_update.start()

    await send_log("🤖 RAON 출퇴근 봇이 정상적으로 실행되었습니다.")


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    uid = str(after.id)
    if uid in data["users"]:
        data["users"][uid]["display_name"] = get_display_name(after)
        save_data()


# =========================
# 실행
# =========================
data = load_data()
bot.run(TOKEN)

import os
import json
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks
from discord import app_commands

# =========================
# 기본 설정
# =========================
TOKEN = os.getenv("TOKEN")

if not TOKEN:
    raise ValueError("TOKEN 변수가 비어 있습니다. Railway Variables 확인하세요.")

GUILD_ID = 1462457099039674498

# 출근/퇴근 버튼 채널
BUTTON_CHANNEL_ID = 1481808025030492180

# 출퇴근 기록 채널
RECORD_CHANNEL_ID = 1479035911726563419

# 관리자 근무확인 채널
STATUS_CHANNEL_ID = 1479036025820156035

# 봇 로그 채널
LOG_CHANNEL_ID = 1479382504204013568

KST = ZoneInfo("Asia/Seoul")

# Railway 볼륨 있으면 유지 저장
DB_PATH = "/data/attendance.db" if os.path.isdir("/data") else "attendance.db"
LEGACY_JSON_FILE = "attendance_data.json"

# 기존 누적값 복구용
RECOVERY_TOTALS = {
    "DEVㆍ볶음": 56 * 3600,
    "IGㆍ봉식": 26 * 3600 + 14 * 60,
    "AMㆍ우진": 25 * 3600 + 12 * 60,
    "STAFFㆍ⭐호랭": 14 * 3600,
    "STAFFㆍ⭐백구": 12 * 3600,
}

intents = discord.Intents.default()
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


# =========================
# 공용 함수
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


def parse_iso(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


# =========================
# DB
# =========================
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS workers (
            user_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            total_seconds INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS active_sessions (
            user_id TEXT PRIMARY KEY,
            start_time TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    conn.commit()
    conn.close()


def get_meta(key: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT value FROM meta WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else None


def set_meta(key: str, value: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO meta (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (key, str(value)))
    conn.commit()
    conn.close()


def delete_meta(key: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM meta WHERE key = ?", (key,))
    conn.commit()
    conn.close()


def upsert_worker(user_id: str, display_name: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM workers WHERE user_id = ?", (user_id,))
    row = cur.fetchone()

    if row:
        cur.execute("""
            UPDATE workers
            SET display_name = ?, updated_at = ?
            WHERE user_id = ?
        """, (display_name, now_utc().isoformat(), user_id))
    else:
        cur.execute("""
            INSERT INTO workers (user_id, display_name, total_seconds, updated_at)
            VALUES (?, ?, 0, ?)
        """, (user_id, display_name, now_utc().isoformat()))

    conn.commit()
    conn.close()


def get_worker(user_id: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM workers WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row


def is_working(user_id: str) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM active_sessions WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row is not None


def get_start_time(user_id: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT start_time FROM active_sessions WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row["start_time"] if row else None


def start_work(user_id: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO active_sessions (user_id, start_time)
        VALUES (?, ?)
    """, (user_id, now_utc().isoformat()))
    conn.commit()
    conn.close()


def stop_work(user_id: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM active_sessions WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def add_total_seconds(user_id: str, seconds: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE workers
        SET total_seconds = total_seconds + ?, updated_at = ?
        WHERE user_id = ?
    """, (max(0, int(seconds)), now_utc().isoformat(), user_id))
    conn.commit()
    conn.close()


def set_total_seconds_if_higher(user_id: str, seconds: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT total_seconds FROM workers WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if row is None:
        conn.close()
        return
    if int(seconds) > int(row["total_seconds"]):
        cur.execute("""
            UPDATE workers
            SET total_seconds = ?, updated_at = ?
            WHERE user_id = ?
        """, (int(seconds), now_utc().isoformat(), user_id))
        conn.commit()
    conn.close()


def get_all_workers_with_live_total():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT w.user_id, w.display_name, w.total_seconds, a.start_time
        FROM workers w
        LEFT JOIN active_sessions a ON w.user_id = a.user_id
    """)
    rows = cur.fetchall()
    conn.close()

    data = []
    for row in rows:
        live_total = int(row["total_seconds"])
        if row["start_time"]:
            started = parse_iso(row["start_time"])
            if started:
                elapsed = int((now_utc() - started).total_seconds())
                if elapsed > 0:
                    live_total += elapsed

        data.append({
            "user_id": row["user_id"],
            "display_name": row["display_name"],
            "total_seconds": int(row["total_seconds"]),
            "live_total_seconds": live_total,
            "is_working": row["start_time"] is not None,
            "start_time": row["start_time"],
        })
    return data


def delete_worker(user_id: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM active_sessions WHERE user_id = ?", (user_id,))
    cur.execute("DELETE FROM workers WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


# =========================
# 기존 JSON 마이그레이션
# =========================
def migrate_legacy_json_once():
    if not os.path.exists(LEGACY_JSON_FILE):
        return

    if get_meta("legacy_json_migrated") == "1":
        return

    try:
        with open(LEGACY_JSON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        set_meta("legacy_json_migrated", "1")
        return

    users = data.get("users", {})
    if not isinstance(users, dict):
        set_meta("legacy_json_migrated", "1")
        return

    conn = get_conn()
    cur = conn.cursor()

    for user_id, info in users.items():
        if not str(user_id).isdigit():
            continue
        if not isinstance(info, dict):
            continue

        display_name = str(info.get("name", "알 수 없음"))
        total_seconds = int(info.get("total_seconds", 0))
        is_working_now = bool(info.get("is_working", False))
        start_time = info.get("start_time")

        cur.execute("""
            INSERT OR IGNORE INTO workers (user_id, display_name, total_seconds, updated_at)
            VALUES (?, ?, ?, ?)
        """, (str(user_id), display_name, total_seconds, now_utc().isoformat()))

        cur.execute("""
            UPDATE workers
            SET display_name = ?, total_seconds = CASE
                WHEN total_seconds < ? THEN ?
                ELSE total_seconds
            END,
            updated_at = ?
            WHERE user_id = ?
        """, (display_name, total_seconds, total_seconds, now_utc().isoformat(), str(user_id)))

        if is_working_now and start_time:
            cur.execute("""
                INSERT OR REPLACE INTO active_sessions (user_id, start_time)
                VALUES (?, ?)
            """, (str(user_id), start_time))

    conn.commit()
    conn.close()
    set_meta("legacy_json_migrated", "1")


# =========================
# 중복 이름 자동 정리
# =========================
def cleanup_duplicate_display_names():
    rows = get_all_workers_with_live_total()
    grouped = {}

    for row in rows:
        name = row["display_name"].strip()
        grouped.setdefault(name, []).append(row)

    for display_name, items in grouped.items():
        if len(items) <= 1:
            continue

        items.sort(key=lambda x: (x["total_seconds"], x["live_total_seconds"]), reverse=True)
        keep = items[0]
        keep_id = keep["user_id"]

        active_start_times = []
        best_total = keep["total_seconds"]

        for item in items:
            if item["total_seconds"] > best_total:
                best_total = item["total_seconds"]
            if item["start_time"]:
                active_start_times.append(item["start_time"])

        set_total_seconds_if_higher(keep_id, best_total)

        if active_start_times:
            valid_times = [t for t in active_start_times if parse_iso(t)]
            if valid_times:
                earliest = min(valid_times, key=lambda x: parse_iso(x))
                conn = get_conn()
                cur = conn.cursor()
                cur.execute("""
                    INSERT OR REPLACE INTO active_sessions (user_id, start_time)
                    VALUES (?, ?)
                """, (keep_id, earliest))
                conn.commit()
                conn.close()

        for item in items[1:]:
            delete_worker(item["user_id"])


# =========================
# 기존 누적 복구
# =========================
async def apply_recovery_totals(guild: discord.Guild):
    if get_meta("recovery_totals_applied") == "1":
        return

    applied_count = 0

    for member in guild.members:
        name = member.display_name
        if name in RECOVERY_TOTALS:
            user_id = str(member.id)
            upsert_worker(user_id, name)
            set_total_seconds_if_higher(user_id, RECOVERY_TOTALS[name])
            applied_count += 1

    cleanup_duplicate_display_names()
    set_meta("recovery_totals_applied", "1")

    if applied_count > 0:
        await send_log(f"✅ 기존 누적 근무시간 복구 완료: {applied_count}명")


# =========================
# 임베드
# =========================
def build_clock_in_embed(member: discord.Member):
    embed = discord.Embed(
        title="🟢 출근 기록",
        color=discord.Color.green(),
        timestamp=now_kst()
    )
    embed.add_field(name="관리자", value=f"{member.mention}\n{member.display_name}", inline=True)
    embed.add_field(name="처리", value="출근 완료", inline=True)
    embed.add_field(name="시간", value=now_kst().strftime("%Y-%m-%d %H:%M:%S"), inline=False)
    return embed


def build_clock_out_embed(member: discord.Member, added_seconds: int, total_seconds: int):
    embed = discord.Embed(
        title="🔴 퇴근 기록",
        color=discord.Color.red(),
        timestamp=now_kst()
    )
    embed.add_field(name="관리자", value=f"{member.mention}\n{member.display_name}", inline=True)
    embed.add_field(name="이번 근무", value=format_seconds(added_seconds), inline=True)
    embed.add_field(name="누적 근무", value=format_seconds(total_seconds), inline=True)
    embed.add_field(name="시간", value=now_kst().strftime("%Y-%m-%d %H:%M:%S"), inline=False)
    return embed


def build_status_embed():
    rows = get_all_workers_with_live_total()

    display_map = {}
    for row in rows:
        name = row["display_name"].strip()
        current = display_map.get(name)

        if current is None:
            display_map[name] = row
        else:
            keep = current if current["live_total_seconds"] >= row["live_total_seconds"] else row
            keep["is_working"] = current["is_working"] or row["is_working"]
            if current["start_time"] and row["start_time"]:
                keep["start_time"] = min(current["start_time"], row["start_time"])
            elif current["start_time"] or row["start_time"]:
                keep["start_time"] = current["start_time"] or row["start_time"]
            display_map[name] = keep

    merged_rows = list(display_map.values())
    merged_rows.sort(key=lambda x: x["live_total_seconds"], reverse=True)

    current_workers = [r for r in merged_rows if r["is_working"]]
    current_workers.sort(key=lambda x: x["live_total_seconds"], reverse=True)

    embed = discord.Embed(
        title="📊 관리자 근무현황",
        color=discord.Color.light_grey()
    )

    if current_workers:
        current_text = "\n".join([f"• {r['display_name']}" for r in current_workers])
    else:
        current_text = "현재 근무중인 관리자가 없습니다."
    embed.add_field(name="🟢 현재 근무중", value=current_text, inline=False)

    if merged_rows:
        rank_lines = []
        for idx, row in enumerate(merged_rows[:10], start=1):
            icon = "🏆 " if idx == 1 else ""
            rank_lines.append(f"{idx}위 {icon}{row['display_name']} - {format_seconds(row['live_total_seconds'])}")
        rank_text = "\n".join(rank_lines)
    else:
        rank_text = "기록 없음"
    embed.add_field(name="🏆 근무 랭킹", value=rank_text, inline=False)

    if merged_rows:
        total_lines = []
        for row in merged_rows[:10]:
            total_lines.append(f"{row['display_name']} - {format_seconds(row['live_total_seconds'])}")
        total_text = "\n".join(total_lines)
    else:
        total_text = "기록 없음"
    embed.add_field(name="⏰ 누적 근무시간", value=total_text, inline=False)

    embed.set_footer(text=f"업데이트 시간: {now_kst().strftime('%Y-%m-%d %H:%M:%S')}")
    return embed


# =========================
# 로그 / 기록
# =========================
async def send_log(message: str):
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        try:
            await channel.send(message)
        except Exception:
            pass


async def log_error(where: str, error: Exception):
    await send_log(f"❌ 오류 | {where} | {str(error)}")


async def send_clock_in_record(member: discord.Member):
    channel = bot.get_channel(RECORD_CHANNEL_ID)
    if channel:
        await channel.send(embed=build_clock_in_embed(member))


async def send_clock_out_record(member: discord.Member, added_seconds: int, total_seconds: int):
    channel = bot.get_channel(RECORD_CHANNEL_ID)
    if channel:
        await channel.send(embed=build_clock_out_embed(member, added_seconds, total_seconds))


# =========================
# 상태 메시지 / 버튼 메시지
# =========================
async def update_status_message():
    channel = bot.get_channel(STATUS_CHANNEL_ID)
    if channel is None:
        return

    embed = build_status_embed()
    message_id = get_meta("status_message_id")

    if message_id:
        try:
            msg = await channel.fetch_message(int(message_id))
            await msg.edit(embed=embed, view=StatusRefreshView())
            return
        except Exception:
            delete_meta("status_message_id")

    msg = await channel.send(embed=embed, view=StatusRefreshView())
    set_meta("status_message_id", str(msg.id))


async def ensure_attendance_panel():
    channel = bot.get_channel(BUTTON_CHANNEL_ID)
    if channel is None:
        return

    message_id = get_meta("attendance_panel_message_id")

    if message_id:
        try:
            msg = await channel.fetch_message(int(message_id))
            await msg.edit(view=AttendanceView())
            return
        except Exception:
            delete_meta("attendance_panel_message_id")

    embed = discord.Embed(
        title="RAON 출퇴근 봇",
        description="아래 버튼으로 출근 / 퇴근을 진행할 수 있습니다.",
        color=discord.Color.blue()
    )
    msg = await channel.send(embed=embed, view=AttendanceView())
    set_meta("attendance_panel_message_id", str(msg.id))


# =========================
# 출근 / 퇴근
# =========================
async def do_clock_in(member: discord.Member):
    user_id = str(member.id)
    display_name = member.display_name

    upsert_worker(user_id, display_name)

    if is_working(user_id):
        return False, "이미 출근 상태입니다."

    start_work(user_id)
    cleanup_duplicate_display_names()
    await update_status_message()
    await send_clock_in_record(member)
    await send_log(f"✅ 출근 완료 | {display_name}")

    return True, "출근 처리되었습니다."


async def do_clock_out(member: discord.Member):
    user_id = str(member.id)
    display_name = member.display_name

    upsert_worker(user_id, display_name)

    if not is_working(user_id):
        return False, "출근한 기록이 없습니다."

    started = parse_iso(get_start_time(user_id))
    added_seconds = 0

    if started:
        added_seconds = int((now_utc() - started).total_seconds())
        if added_seconds < 0:
            added_seconds = 0

    add_total_seconds(user_id, added_seconds)
    stop_work(user_id)
    cleanup_duplicate_display_names()

    worker = get_worker(user_id)
    total_seconds = int(worker["total_seconds"]) if worker else added_seconds

    await update_status_message()
    await send_clock_out_record(member, added_seconds, total_seconds)
    await send_log(f"✅ 퇴근 완료 | {display_name}")

    return True, f"퇴근 처리되었습니다.\n이번 근무: {format_seconds(added_seconds)}\n누적: {format_seconds(total_seconds)}"


# =========================
# 버튼 뷰
# =========================
class AttendanceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="출근", style=discord.ButtonStyle.success, custom_id="raon_clock_in")
    async def clock_in_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            success, msg = await do_clock_in(interaction.user)
            if success:
                await interaction.response.send_message(f"✅ {msg}", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ {msg}", ephemeral=True)
        except Exception as e:
            await log_error("출근 버튼", e)
            if not interaction.response.is_done():
                await interaction.response.send_message("오류가 발생했습니다.", ephemeral=True)

    @discord.ui.button(label="퇴근", style=discord.ButtonStyle.danger, custom_id="raon_clock_out")
    async def clock_out_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            success, msg = await do_clock_out(interaction.user)
            if success:
                await interaction.response.send_message(f"✅ {msg}", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ {msg}", ephemeral=True)
        except Exception as e:
            await log_error("퇴근 버튼", e)
            if not interaction.response.is_done():
                await interaction.response.send_message("오류가 발생했습니다.", ephemeral=True)


class StatusRefreshView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="근무현황", style=discord.ButtonStyle.primary, custom_id="raon_status_refresh")
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await update_status_message()
            await interaction.response.send_message("🔄 근무현황을 갱신했습니다.", ephemeral=True)
        except Exception as e:
            await log_error("근무현황 버튼", e)
            if not interaction.response.is_done():
                await interaction.response.send_message("오류가 발생했습니다.", ephemeral=True)


# =========================
# 슬래시 명령어
# =========================
@tree.command(name="출근", description="출근 처리")
async def slash_clock_in(interaction: discord.Interaction):
    try:
        success, msg = await do_clock_in(interaction.user)
        if success:
            await interaction.response.send_message(f"✅ {msg}", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ {msg}", ephemeral=True)
    except Exception as e:
        await log_error("/출근", e)
        await interaction.response.send_message("오류가 발생했습니다.", ephemeral=True)


@tree.command(name="퇴근", description="퇴근 처리")
async def slash_clock_out(interaction: discord.Interaction):
    try:
        success, msg = await do_clock_out(interaction.user)
        if success:
            await interaction.response.send_message(f"✅ {msg}", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ {msg}", ephemeral=True)
    except Exception as e:
        await log_error("/퇴근", e)
        await interaction.response.send_message("오류가 발생했습니다.", ephemeral=True)


@tree.command(name="현황갱신", description="관리자 근무현황 갱신")
async def slash_refresh(interaction: discord.Interaction):
    try:
        await update_status_message()
        await interaction.response.send_message("🔄 근무현황을 갱신했습니다.", ephemeral=True)
    except Exception as e:
        await log_error("/현황갱신", e)
        await interaction.response.send_message("오류가 발생했습니다.", ephemeral=True)


@tree.command(name="근무정리", description="서버에 없는 유저 데이터 정리")
@app_commands.checks.has_permissions(administrator=True)
async def slash_cleanup(interaction: discord.Interaction):
    try:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("서버 안에서만 사용할 수 있습니다.", ephemeral=True)
            return

        current_ids = {str(member.id) for member in guild.members}

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM workers")
        rows = cur.fetchall()
        conn.close()

        removed = 0
        for row in rows:
            user_id = str(row["user_id"])
            if user_id not in current_ids:
                delete_worker(user_id)
                removed += 1

        cleanup_duplicate_display_names()
        await update_status_message()
        await send_log(f"✅ 데이터 정리 완료 | 삭제 {removed}명")
        await interaction.response.send_message(f"🧹 데이터 {removed}개를 정리했습니다.", ephemeral=True)
    except Exception as e:
        await log_error("/근무정리", e)
        await interaction.response.send_message("오류가 발생했습니다.", ephemeral=True)


# =========================
# 자동 갱신
# =========================
@tasks.loop(minutes=1)
async def auto_update_status():
    try:
        cleanup_duplicate_display_names()
        await update_status_message()
    except Exception as e:
        await log_error("자동 갱신", e)


# =========================
# 이벤트
# =========================
@bot.event
async def on_ready():
    try:
        guild_obj = discord.Object(id=GUILD_ID)
        await tree.sync(guild=guild_obj)
    except Exception as e:
        await log_error("슬래시 동기화", e)

    bot.add_view(AttendanceView())
    bot.add_view(StatusRefreshView())

    try:
        init_db()
        migrate_legacy_json_once()

        guild = bot.get_guild(GUILD_ID)
        if guild:
            await apply_recovery_totals(guild)

        cleanup_duplicate_display_names()

        if not auto_update_status.is_running():
            auto_update_status.start()

        await ensure_attendance_panel()
        await update_status_message()
        await send_log("🤖 RAON 출퇴근 봇이 정상적으로 실행되었습니다.")
        print(f"Logged in as {bot.user}")
    except Exception as e:
        await log_error("on_ready", e)
        raise


@bot.event
async def on_member_remove(member: discord.Member):
    try:
        delete_worker(str(member.id))
        cleanup_duplicate_display_names()
        await update_status_message()
        await send_log(f"✅ 퇴장 데이터 삭제 완료 | {member.display_name}")
    except Exception as e:
        await log_error("on_member_remove", e)


bot.run(TOKEN)

import os
import json
import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime

import os
TOKEN = os.environ["DISCORD_BOT_TOKEN"]

if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN 시크릿이 설정되지 않았습니다.")

GUILD_ID = 1462457099039674498

BUTTON_CHANNEL_ID = 1481808025030492180  # 출퇴근버튼
RECORD_CHANNEL_ID = 1479035911726563419  # 출퇴근ㅣ기록
STATUS_CHANNEL_ID = 1479036025820156035  # 관리자 근무확인
LOG_CHANNEL_ID = 1479382504204013568  # 봇로그 (지금 우진님이 보낸 값 그대로 넣음)

DATA_FILE = "attendance.json"

intents = discord.Intents.default()
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


def load_data():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f, ensure_ascii=False, indent=4)

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except:
            return {}


def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(attendance, f, ensure_ascii=False, indent=4)


attendance = load_data()


def format_time(seconds: int):
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}시간 {m}분"


async def send_log(message: str):
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        try:
            await channel.send(message)
        except Exception as e:
            print(f"로그 전송 실패: {e}")


async def update_status_message():
    channel = bot.get_channel(STATUS_CHANNEL_ID)
    if not channel:
        return

    working_list = []
    ranking_list = []

    now = datetime.now()

    for uid, data in attendance.items():
        nickname = data.get("name", f"알수없음({uid})")
        total = int(data.get("total", 0))

        if data.get("clock_in"):
            try:
                start = datetime.fromisoformat(data["clock_in"])
                running = int((now - start).total_seconds())
                total += running
                working_list.append(nickname)
            except:
                pass

        ranking_list.append((nickname, total))

    ranking_list.sort(key=lambda x: x[1], reverse=True)

    working_text = (
        "\n".join([f"• {name}" for name in working_list])
        if working_list
        else "현재 근무중인 관리자 없음"
    )

    ranking_text = ""
    for i, (name, sec) in enumerate(ranking_list[:10], start=1):
        ranking_text += f"{i}위 {name} - {format_time(sec)}\n"
    if not ranking_text:
        ranking_text = "기록 없음"

    total_text = ""
    for name, sec in ranking_list:
        total_text += f"{name} - {format_time(sec)}\n"
    if not total_text:
        total_text = "기록 없음"

    embed = discord.Embed(title="📊 관리자 근무현황", color=0x2F3136)
    embed.add_field(name="🟢 현재 근무중", value=working_text, inline=False)
    embed.add_field(name="🏆 근무 랭킹", value=ranking_text, inline=False)
    embed.add_field(name="⏰ 누적 근무시간", value=total_text, inline=False)
    embed.set_footer(text=f"업데이트 시간: {now.strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        async for msg in channel.history(limit=20):
            if msg.author == bot.user:
                try:
                    await msg.delete()
                except:
                    pass
        await channel.send(embed=embed)
    except Exception as e:
        print(f"현황판 업데이트 실패: {e}")
        await send_log(f"오류 | 현황판 업데이트 실패 | {e}")


class AttendanceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="출근",
        style=discord.ButtonStyle.green,
        emoji="🟢",
        custom_id="attendance_clock_in",
    )
    async def clock_in_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        uid = str(interaction.user.id)
        nickname = interaction.user.display_name

        if uid not in attendance:
            attendance[uid] = {"name": nickname, "total": 0, "clock_in": None}

        attendance[uid]["name"] = nickname

        if attendance[uid]["clock_in"] is not None:
            await interaction.response.send_message(
                "이미 출근 상태입니다.", ephemeral=True
            )
            return

        now = datetime.now()
        attendance[uid]["clock_in"] = now.isoformat()
        save_data()

        record_channel = bot.get_channel(RECORD_CHANNEL_ID)
        if record_channel:
            embed = discord.Embed(color=0x57F287)
            embed.title = "🟢 출근"
            embed.add_field(name="관리자", value=interaction.user.mention, inline=False)
            embed.add_field(
                name="시간", value=now.strftime("%Y-%m-%d %H:%M:%S"), inline=False
            )
            await record_channel.send(embed=embed)

        await send_log(f"출근완료 | {nickname}")
        await update_status_message()
        await interaction.response.send_message("출근 완료", ephemeral=True)

    @discord.ui.button(
        label="퇴근",
        style=discord.ButtonStyle.red,
        emoji="🔴",
        custom_id="attendance_clock_out",
    )
    async def clock_out_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        uid = str(interaction.user.id)
        nickname = interaction.user.display_name

        if uid not in attendance or attendance[uid].get("clock_in") is None:
            await interaction.response.send_message(
                "출근 기록이 없습니다.", ephemeral=True
            )
            return

        now = datetime.now()

        try:
            start = datetime.fromisoformat(attendance[uid]["clock_in"])
            diff = int((now - start).total_seconds())
        except Exception as e:
            await send_log(f"오류 | 퇴근 처리 실패 | {nickname} | {e}")
            await interaction.response.send_message(
                "퇴근 처리 중 오류가 발생했습니다.", ephemeral=True
            )
            return

        attendance[uid]["name"] = nickname
        attendance[uid]["total"] = int(attendance[uid].get("total", 0)) + diff
        attendance[uid]["clock_in"] = None
        save_data()

        record_channel = bot.get_channel(RECORD_CHANNEL_ID)
        if record_channel:
            embed = discord.Embed(color=0xED4245)
            embed.title = "🔴 퇴근"
            embed.add_field(name="관리자", value=interaction.user.mention, inline=False)
            embed.add_field(
                name="시간", value=now.strftime("%Y-%m-%d %H:%M:%S"), inline=False
            )
            embed.add_field(name="근무시간", value=format_time(diff), inline=False)
            await record_channel.send(embed=embed)

        await send_log(f"퇴근완료 | {nickname}")
        await update_status_message()
        await interaction.response.send_message("퇴근 완료", ephemeral=True)


@tree.command(name="강제퇴근", description="관리자가 유저를 강제로 퇴근 처리합니다")
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(member="강제 퇴근 처리할 관리자")
async def force_clock_out(interaction: discord.Interaction, member: discord.Member):
    uid = str(member.id)
    nickname = member.display_name

    if uid not in attendance or attendance[uid].get("clock_in") is None:
        await interaction.response.send_message(
            "해당 유저는 현재 출근 상태가 아닙니다.", ephemeral=True
        )
        return

    now = datetime.now()

    try:
        start = datetime.fromisoformat(attendance[uid]["clock_in"])
        diff = int((now - start).total_seconds())
    except Exception as e:
        await send_log(f"오류 | 강제퇴근 실패 | {nickname} | {e}")
        await interaction.response.send_message(
            "강제퇴근 처리 중 오류가 발생했습니다.", ephemeral=True
        )
        return

    attendance[uid]["name"] = nickname
    attendance[uid]["total"] = int(attendance[uid].get("total", 0)) + diff
    attendance[uid]["clock_in"] = None
    save_data()

    record_channel = bot.get_channel(RECORD_CHANNEL_ID)
    if record_channel:
        embed = discord.Embed(color=0xFAA61A)
        embed.title = "⚠️ 강제 퇴근"
        embed.add_field(name="관리자", value=member.mention, inline=False)
        embed.add_field(name="처리자", value=interaction.user.mention, inline=False)
        embed.add_field(
            name="시간", value=now.strftime("%Y-%m-%d %H:%M:%S"), inline=False
        )
        embed.add_field(name="추가된 근무시간", value=format_time(diff), inline=False)
        await record_channel.send(embed=embed)

    await send_log(
        f"강제퇴근 | 대상:{nickname} | 처리자:{interaction.user.display_name}"
    )
    await update_status_message()
    await interaction.response.send_message(
        f"{member.mention} 강제 퇴근 처리 완료", ephemeral=True
    )


@tree.command(name="근무시간추가", description="관리자가 유저 근무시간을 추가합니다")
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(member="시간을 추가할 관리자", hours="추가할 시간")
async def add_work_time(
    interaction: discord.Interaction, member: discord.Member, hours: int
):
    if hours <= 0:
        await interaction.response.send_message(
            "1 이상의 숫자를 입력해주세요.", ephemeral=True
        )
        return

    uid = str(member.id)
    nickname = member.display_name

    if uid not in attendance:
        attendance[uid] = {"name": nickname, "total": 0, "clock_in": None}

    attendance[uid]["name"] = nickname
    attendance[uid]["total"] = int(attendance[uid].get("total", 0)) + (hours * 3600)
    save_data()

    await send_log(
        f"근무시간추가 | {nickname} | {hours}시간 | 처리자:{interaction.user.display_name}"
    )
    await update_status_message()
    await interaction.response.send_message(
        f"{member.mention} 근무시간 {hours}시간 추가 완료", ephemeral=True
    )


@tree.command(name="근무시간차감", description="관리자가 유저 근무시간을 차감합니다")
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(member="시간을 차감할 관리자", hours="차감할 시간")
async def subtract_work_time(
    interaction: discord.Interaction, member: discord.Member, hours: int
):
    if hours <= 0:
        await interaction.response.send_message(
            "1 이상의 숫자를 입력해주세요.", ephemeral=True
        )
        return

    uid = str(member.id)
    nickname = member.display_name

    if uid not in attendance:
        await interaction.response.send_message(
            "해당 유저의 근무 기록이 없습니다.", ephemeral=True
        )
        return

    current_total = int(attendance[uid].get("total", 0))
    new_total = current_total - (hours * 3600)
    if new_total < 0:
        new_total = 0

    attendance[uid]["name"] = nickname
    attendance[uid]["total"] = new_total
    save_data()

    await send_log(
        f"근무시간차감 | {nickname} | {hours}시간 | 처리자:{interaction.user.display_name}"
    )
    await update_status_message()
    await interaction.response.send_message(
        f"{member.mention} 근무시간 {hours}시간 차감 완료", ephemeral=True
    )


@tasks.loop(minutes=1)
async def auto_status_update():
    await update_status_message()


@bot.event
async def on_ready():
    print(f"로그인 완료 | {bot.user}")

    bot.add_view(AttendanceView())

    try:
        guild = discord.Object(id=GUILD_ID)
        synced = await tree.sync(guild=guild)
        print(f"슬래시 명령어 동기화 완료: {len(synced)}개")
    except Exception as e:
        print(f"슬래시 명령어 동기화 실패: {e}")
        await send_log(f"오류 | 슬래시 명령어 동기화 실패 | {e}")

    button_channel = bot.get_channel(BUTTON_CHANNEL_ID)
    if button_channel:
        try:
            async for msg in button_channel.history(limit=20):
                if msg.author == bot.user:
                    try:
                        await msg.delete()
                    except:
                        pass
        except Exception as e:
            await send_log(f"오류 | 버튼채널 정리 실패 | {e}")

        embed = discord.Embed(
            title="관리자 출퇴근",
            description="버튼을 눌러 출근 / 퇴근을 기록하세요.",
            color=0x5865F2,
        )
        try:
            await button_channel.send(embed=embed, view=AttendanceView())
        except Exception as e:
            await send_log(f"오류 | 버튼 생성 실패 | {e}")

    await update_status_message()

    if not auto_status_update.is_running():
        auto_status_update.start()

    await send_log("봇활성화")


@force_clock_out.error
@add_work_time.error
@subtract_work_time.error
async def admin_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "이 명령어는 관리자만 사용할 수 있습니다.", ephemeral=True
            )
    else:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                f"오류가 발생했습니다: {error}", ephemeral=True
            )
        await send_log(f"오류 | 관리자 명령어 실패 | {error}")


bot.run(TOKEN)

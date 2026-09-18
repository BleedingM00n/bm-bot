import os
import json
import sqlite3
import urllib.parse
import urllib.request
import gzip
import zlib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv


# ============================================================
# CONFIGURATION
# ============================================================

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

# Discord channel used for Minecraft commands
MINE_BOT_CHANNEL_ID = 1546212814501322902

# Plan API - UPDATED ENDERCLOUD ADDRESS
PLAN_API = "http://agni.ender.co.in:45801"

# Plan server UUID
PLAN_SERVER_UUID = "b8bc0c5d-3735-4e95-8b7e-672fd1947580"

# Databases
DATABASE_FILE = "minecraft_links.db"
ACTIVITY_DATABASE_FILE = "activity.db"

LEADERBOARD_SIZE = 15

# Text activity:
# Each message can extend the active session by up to 10 minutes.
TEXT_ACTIVITY_TIMEOUT_SECONDS = 10 * 60

# Use India time for week/month boundaries.
TIMEZONE = ZoneInfo("Asia/Kolkata")

# Text levels are lifetime levels.
TEXT_LEVELS = [
    (1, 1),       # 1 hour
    (2, 3),       # 3 hours
    (3, 6),       # 6 hours
    (4, 10),      # 10 hours
    (5, 15),      # 15 hours
    (6, 20),      # 20 hours
    (7, 30),      # 30 hours
    (8, 40),      # 40 hours
    (9, 50),      # 50 hours
    (10, 75),     # 75 hours
    (11, 100),    # 100 hours
    (12, 150),    # 150 hours
    (13, 200),    # 200 hours
    (14, 300),    # 300 hours
    (15, 500),    # 500 hours
]


# ============================================================
# BOT SETUP
# ============================================================

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# ============================================================
# GENERAL DATABASE HELPERS
# ============================================================

def get_db(path):
    connection = sqlite3.connect(path, timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


# ============================================================
# MINECRAFT DATABASE
# ============================================================

def initialize_minecraft_database():
    connection = get_db(DATABASE_FILE)
    cursor = connection.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS minecraft_links (
            discord_user_id INTEGER PRIMARY KEY,
            minecraft_uuid TEXT NOT NULL,
            minecraft_name TEXT NOT NULL
        )
        """
    )

    connection.commit()
    connection.close()


def get_minecraft_link(discord_user_id):
    connection = get_db(DATABASE_FILE)
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT minecraft_uuid, minecraft_name
        FROM minecraft_links
        WHERE discord_user_id = ?
        """,
        (discord_user_id,)
    )

    result = cursor.fetchone()
    connection.close()

    return result


def save_minecraft_link(discord_user_id, minecraft_uuid, minecraft_name):
    connection = get_db(DATABASE_FILE)
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT OR REPLACE INTO minecraft_links
        (
            discord_user_id,
            minecraft_uuid,
            minecraft_name
        )
        VALUES (?, ?, ?)
        """,
        (
            discord_user_id,
            minecraft_uuid,
            minecraft_name
        )
    )

    connection.commit()
    connection.close()


def delete_minecraft_link(discord_user_id):
    connection = get_db(DATABASE_FILE)
    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM minecraft_links
        WHERE discord_user_id = ?
        """,
        (discord_user_id,)
    )

    deleted = cursor.rowcount > 0

    connection.commit()
    connection.close()

    return deleted


# ============================================================
# ACTIVITY DATABASE
# ============================================================

def initialize_activity_database():
    connection = get_db(ACTIVITY_DATABASE_FILE)
    cursor = connection.cursor()

    # One row for every message that contributed text activity.
    # credited_seconds is capped at 10 minutes.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS text_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_user_id INTEGER NOT NULL,
            timestamp REAL NOT NULL,
            credited_seconds INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_text_activity_user_time
        ON text_activity(discord_user_id, timestamp)
        """
    )

    # Last message timestamp is used to calculate the next text session.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS text_sessions (
            discord_user_id INTEGER PRIMARY KEY,
            last_message_timestamp REAL NOT NULL
        )
        """
    )

    # Voice sessions. end_timestamp is NULL while the user is currently
    # in a voice channel.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS voice_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_user_id INTEGER NOT NULL,
            start_timestamp REAL NOT NULL,
            end_timestamp REAL
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_voice_sessions_user_time
        ON voice_sessions(discord_user_id, start_timestamp)
        """
    )

    connection.commit()
    connection.close()


# ============================================================
# TIME HELPERS
# ============================================================

def now_utc_timestamp():
    return datetime.now(timezone.utc).timestamp()


def local_now():
    return datetime.now(TIMEZONE)


def get_week_start_timestamp():
    now = local_now()

    # Monday = 0
    start = datetime(
        now.year,
        now.month,
        now.day,
        tzinfo=TIMEZONE
    )

    start = start.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    start = start.fromtimestamp(
        start.timestamp() - start.weekday() * 86400,
        tz=TIMEZONE
    )

    return start.timestamp()


def get_month_start_timestamp():
    now = local_now()

    start = datetime(
        now.year,
        now.month,
        1,
        tzinfo=TIMEZONE
    )

    return start.timestamp()


def get_month_name():
    return local_now().strftime("%B %Y")


def get_week_name():
    return local_now().strftime("Week of %d %B %Y")


# ============================================================
# TEXT ACTIVITY
# ============================================================

def record_text_activity(discord_user_id, timestamp=None):
    """
    Option C:
    - A message starts/continues a text activity session.
    - Between consecutive messages, at most 10 minutes are credited.
    - A gap longer than 10 minutes starts a new session.
    - Activity is stored permanently, so lifetime data never resets.
    """
    if timestamp is None:
        timestamp = now_utc_timestamp()

    connection = get_db(ACTIVITY_DATABASE_FILE)
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT last_message_timestamp
        FROM text_sessions
        WHERE discord_user_id = ?
        """,
        (discord_user_id,)
    )

    row = cursor.fetchone()

    credited_seconds = 0

    if row is not None:
        last_timestamp = float(row[0])
        delta = timestamp - last_timestamp

        if delta > 0:
            credited_seconds = min(
                int(delta),
                TEXT_ACTIVITY_TIMEOUT_SECONDS
            )

    cursor.execute(
        """
        INSERT INTO text_activity
        (
            discord_user_id,
            timestamp,
            credited_seconds
        )
        VALUES (?, ?, ?)
        """,
        (
            discord_user_id,
            timestamp,
            credited_seconds
        )
    )

    cursor.execute(
        """
        INSERT OR REPLACE INTO text_sessions
        (
            discord_user_id,
            last_message_timestamp
        )
        VALUES (?, ?)
        """,
        (
            discord_user_id,
            timestamp
        )
    )

    connection.commit()
    connection.close()

    return credited_seconds


def get_text_activity(
    discord_user_id,
    start_timestamp=None,
    end_timestamp=None
):
    connection = get_db(ACTIVITY_DATABASE_FILE)
    cursor = connection.cursor()

    query = """
        SELECT COALESCE(SUM(credited_seconds), 0)
        FROM text_activity
        WHERE discord_user_id = ?
    """

    params = [discord_user_id]

    if start_timestamp is not None:
        query += " AND timestamp >= ?"
        params.append(start_timestamp)

    if end_timestamp is not None:
        query += " AND timestamp < ?"
        params.append(end_timestamp)

    cursor.execute(query, params)

    result = cursor.fetchone()
    connection.close()

    return int(result[0] or 0)


def get_all_text_users():
    connection = get_db(ACTIVITY_DATABASE_FILE)
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT DISTINCT discord_user_id
        FROM text_activity
        """
    )

    users = [int(row[0]) for row in cursor.fetchall()]
    connection.close()

    return users


# ============================================================
# TEXT LEVELS
# ============================================================

def get_text_level(total_seconds):
    total_hours = total_seconds / 3600

    current_level = 0

    for level, required_hours in TEXT_LEVELS:
        if total_hours >= required_hours:
            current_level = level
        else:
            break

    return current_level


def get_next_text_level(current_level):
    for level, required_hours in TEXT_LEVELS:
        if level > current_level:
            return level, required_hours

    return None


def format_seconds(seconds):
    seconds = max(0, int(seconds))

    days = seconds // 86400
    seconds %= 86400

    hours = seconds // 3600
    seconds %= 3600

    minutes = seconds // 60

    parts = []

    if days:
        parts.append(f"{days}d")

    if hours:
        parts.append(f"{hours}h")

    if minutes:
        parts.append(f"{minutes}m")

    if not parts:
        parts.append("0m")

    return " ".join(parts)


# ============================================================
# TEXT LEVEL ROLES
# ============================================================

def text_level_role_name(level):
    return f"Text Level {level}"


async def get_or_create_text_level_role(guild, level):
    if level <= 0:
        return None

    role_name = text_level_role_name(level)

    role = discord.utils.get(
        guild.roles,
        name=role_name
    )

    if role:
        return role

    try:
        role = await guild.create_role(
            name=role_name,
            reason="Bleeding Moon text activity level"
        )
        print(f"✅ Created Discord role: {role_name}")
        return role

    except discord.Forbidden:
        print(
            f"❌ Cannot create {role_name}. "
            "Give the bot Manage Roles permission."
        )
        return None

    except Exception as error:
        print(
            f"❌ Error creating {role_name}: {error}"
        )
        return None


async def update_text_level_role(member):
    """
    Text levels are lifetime-based.
    The bot keeps only the player's current Text Level role.
    """
    if member.bot:
        return

    total_seconds = get_text_activity(member.id)
    level = get_text_level(total_seconds)

    if level <= 0:
        return

    role = await get_or_create_text_level_role(
        member.guild,
        level
    )

    if role is None:
        return

    # Remove other Text Level roles.
    roles_to_remove = []

    for existing_role in member.roles:
        if (
            existing_role.name.startswith("Text Level ")
            and existing_role != role
        ):
            roles_to_remove.append(existing_role)

    try:
        if roles_to_remove:
            await member.remove_roles(
                *roles_to_remove,
                reason="Bleeding Moon text level update"
            )

        if role not in member.roles:
            await member.add_roles(
                role,
                reason="Bleeding Moon text level update"
            )

    except discord.Forbidden:
        print(
            f"❌ Cannot update Text Level role for {member}"
        )

    except Exception as error:
        print(
            f"❌ Error updating Text Level role for {member}: {error}"
        )


# ============================================================
# VOICE ACTIVITY
# ============================================================

def start_voice_session(discord_user_id):
    timestamp = now_utc_timestamp()

    connection = get_db(ACTIVITY_DATABASE_FILE)
    cursor = connection.cursor()

    # Avoid duplicate open sessions.
    cursor.execute(
        """
        SELECT id
        FROM voice_sessions
        WHERE discord_user_id = ?
        AND end_timestamp IS NULL
        ORDER BY start_timestamp DESC
        LIMIT 1
        """,
        (discord_user_id,)
    )

    if cursor.fetchone() is None:
        cursor.execute(
            """
            INSERT INTO voice_sessions
            (
                discord_user_id,
                start_timestamp,
                end_timestamp
            )
            VALUES (?, ?, NULL)
            """,
            (
                discord_user_id,
                timestamp
            )
        )

    connection.commit()
    connection.close()


def end_voice_session(discord_user_id):
    timestamp = now_utc_timestamp()

    connection = get_db(ACTIVITY_DATABASE_FILE)
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT id
        FROM voice_sessions
        WHERE discord_user_id = ?
        AND end_timestamp IS NULL
        ORDER BY start_timestamp DESC
        LIMIT 1
        """,
        (discord_user_id,)
    )

    row = cursor.fetchone()

    if row is not None:
        cursor.execute(
            """
            UPDATE voice_sessions
            SET end_timestamp = ?
            WHERE id = ?
            """,
            (
                timestamp,
                row[0]
            )
        )

    connection.commit()
    connection.close()


def calculate_voice_overlap(
    start_timestamp,
    end_timestamp,
    period_start,
    period_end
):
    actual_end = (
        end_timestamp
        if end_timestamp is not None
        else now_utc_timestamp()
    )

    overlap_start = max(
        start_timestamp,
        period_start
    )

    overlap_end = min(
        actual_end,
        period_end
    )

    if overlap_end <= overlap_start:
        return 0

    return int(overlap_end - overlap_start)


def get_voice_activity(
    discord_user_id,
    period_start=None,
    period_end=None
):
    if period_start is None:
        period_start = 0

    if period_end is None:
        period_end = now_utc_timestamp()

    connection = get_db(ACTIVITY_DATABASE_FILE)
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT start_timestamp, end_timestamp
        FROM voice_sessions
        WHERE discord_user_id = ?
        AND start_timestamp < ?
        AND (
            end_timestamp IS NULL
            OR end_timestamp > ?
        )
        """,
        (
            discord_user_id,
            period_end,
            period_start
        )
    )

    total = 0

    for start_timestamp, end_timestamp in cursor.fetchall():
        total += calculate_voice_overlap(
            float(start_timestamp),
            (
                float(end_timestamp)
                if end_timestamp is not None
                else None
            ),
            period_start,
            period_end
        )

    connection.close()

    return total


def get_all_voice_users():
    connection = get_db(ACTIVITY_DATABASE_FILE)
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT DISTINCT discord_user_id
        FROM voice_sessions
        """
    )

    users = [int(row[0]) for row in cursor.fetchall()]
    connection.close()

    return users


# ============================================================
# DISCORD VOICE STATE
# ============================================================

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    was_in_voice = before.channel is not None
    is_in_voice = after.channel is not None

    # Joined voice.
    if not was_in_voice and is_in_voice:
        start_voice_session(member.id)
        print(
            f"🎙️ Voice started: {member} -> {after.channel}"
        )

    # Left voice completely.
    elif was_in_voice and not is_in_voice:
        end_voice_session(member.id)
        print(
            f"🎙️ Voice ended: {member}"
        )


# ============================================================
# DISCORD MESSAGE ACTIVITY
# ============================================================

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.guild is None:
        return

    try:
        credited = record_text_activity(
            message.author.id
        )

        # Only check/update the role when new activity was credited.
        # This avoids unnecessary Discord API calls.
        if credited > 0:
            await update_text_level_role(
                message.author
            )

    except Exception as error:
        print(
            f"❌ Text activity error for "
            f"{message.author}: {error}"
        )

    # Keep normal command processing available.
    await bot.process_commands(message)


# ============================================================
# PLAN API
# ============================================================

def plan_get(path):
    url = f"{PLAN_API}{path}"

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "BleedingMoon/1.0",
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate, identity"
        }
    )

    with urllib.request.urlopen(
        request,
        timeout=15
    ) as response:

        data = response.read()

        content_encoding = response.headers.get(
            "Content-Encoding",
            ""
        ).lower()

        if content_encoding == "gzip":
            data = gzip.decompress(data)

        elif content_encoding == "deflate":
            data = zlib.decompress(data)

        elif (
            len(data) >= 2
            and data[:2] == b"\x1f\x8b"
        ):
            data = gzip.decompress(data)

    return json.loads(
        data.decode("utf-8")
    )


# ============================================================
# GET ALL PLAYERS FROM PLAN
# ============================================================

def get_plan_players():
    query = urllib.parse.urlencode(
        {
            "server": PLAN_SERVER_UUID
        }
    )

    return plan_get(
        f"/v1/playersTable?{query}"
    )


# ============================================================
# EXTRACT PLAYERS
# ============================================================

def extract_players(data):
    players = []

    if isinstance(data, dict):

        possible_players = data.get(
            "players"
        )

        if isinstance(
            possible_players,
            list
        ):
            for player in possible_players:

                if not isinstance(
                    player,
                    dict
                ):
                    continue

                if (
                    "playerUUID" in player
                    and "playerName" in player
                ):
                    players.append(
                        player
                    )

            if players:
                return players

        for value in data.values():

            if isinstance(
                value,
                (dict, list)
            ):
                found = extract_players(
                    value
                )

                if found:
                    players.extend(
                        found
                    )

    elif isinstance(data, list):

        for item in data:

            if not isinstance(
                item,
                dict
            ):
                continue

            if (
                "playerUUID" in item
                and "playerName" in item
            ):
                players.append(
                    item
                )

            else:
                found = extract_players(
                    item
                )

                if found:
                    players.extend(
                        found
                    )

    return players


# ============================================================
# CLEAN FLOODGATE / PLAN PLAYER NAME
# ============================================================

def clean_minecraft_name(name):
    name = str(name).strip()

    if name.startswith("."):
        name = name[1:]

    return name


# ============================================================
# FIND PLAYER IN PLAN
# ============================================================

def find_plan_player(minecraft_name):
    data = get_plan_players()

    players = extract_players(
        data
    )

    wanted_name = (
        minecraft_name
        .strip()
        .lower()
    )

    for player in players:

        player_name = clean_minecraft_name(
            player.get(
                "playerName",
                ""
            )
        )

        nickname = clean_minecraft_name(
            player.get(
                "nicknames",
                ""
            )
        )

        if player_name.lower() == wanted_name:
            return player

        if nickname.lower() == wanted_name:
            return player

    return None


# ============================================================
# MINECRAFT MONTHLY PLAYTIME
# ============================================================

def get_month_start_millis():
    month_start = datetime.fromtimestamp(
        get_month_start_timestamp(),
        tz=timezone.utc
    )

    return int(
        month_start.timestamp() * 1000
    )


def get_monthly_playtime(player_uuid):
    month_start = get_month_start_millis()

    query = urllib.parse.urlencode(
        {
            "type": "PLAYTIME",
            "player": player_uuid,
            "after": month_start
        }
    )

    data = plan_get(
        f"/v1/datapoint?{query}"
    )

    return int(
        data.get(
            "value",
            0
        )
    )


# ============================================================
# FORMAT MINECRAFT PLAYTIME
# ============================================================

def format_playtime(milliseconds):
    total_seconds = milliseconds // 1000

    days = total_seconds // 86400
    total_seconds %= 86400

    hours = total_seconds // 3600
    total_seconds %= 3600

    minutes = total_seconds // 60
    seconds = total_seconds % 60

    parts = []

    if days:
        parts.append(f"{days}d")

    if hours:
        parts.append(f"{hours}h")

    if minutes:
        parts.append(f"{minutes}m")

    if seconds or not parts:
        parts.append(f"{seconds}s")

    return " ".join(parts)


# ============================================================
# CHECK MINECRAFT CHANNEL
# ============================================================

async def check_mine_bot_channel(interaction):
    if interaction.channel_id != MINE_BOT_CHANNEL_ID:

        await interaction.response.send_message(
            "❌ Minecraft commands can only be used in "
            "<#1546212814501322902>.",
            ephemeral=True
        )

        return False

    return True


# ============================================================
# CHECK ADMIN / MODERATOR
# ============================================================

def is_moderator(member):
    permissions = member.guild_permissions

    return (
        permissions.administrator
        or permissions.manage_roles
        or permissions.manage_guild
    )


# ============================================================
# BOT READY
# ============================================================

@bot.event
async def on_ready():
    print(
        f"🌙 Bleeding Moon is online as {bot.user}"
    )

    print(
        f"⛏️ Plan API: {PLAN_API}"
    )

    print(
        f"🌏 Activity timezone: {TIMEZONE.key}"
    )

    try:
        synced = await bot.tree.sync()

        print(
            f"✅ Synced {len(synced)} slash command(s)"
        )

    except Exception as error:
        print(
            f"❌ Failed to sync commands: {error}"
        )


# ============================================================
# LINK MINECRAFT PLAYER
# ============================================================

@bot.tree.command(
    name="link-player",
    description="Link a Discord member to a Minecraft account."
)
@app_commands.describe(
    member="The Discord member",
    minecraft_name="The Minecraft username"
)
async def link_player(
    interaction: discord.Interaction,
    member: discord.Member,
    minecraft_name: str
):

    if not await check_mine_bot_channel(
        interaction
    ):
        return

    if not is_moderator(
        interaction.user
    ):

        await interaction.response.send_message(
            "❌ Only administrators or moderators can link Minecraft accounts.",
            ephemeral=True
        )

        return

    await interaction.response.defer(
        ephemeral=True
    )

    try:
        player = await bot.loop.run_in_executor(
            None,
            find_plan_player,
            minecraft_name
        )

    except Exception as error:
        print(
            f"❌ Plan API error while linking player: {error}"
        )

        await interaction.followup.send(
            "❌ I couldn't contact the Minecraft statistics system.",
            ephemeral=True
        )

        return

    if player is None:

        await interaction.followup.send(
            f"❌ I couldn't find **{minecraft_name}** "
            "in the Minecraft statistics database.\n\n"
            "Make sure the player has joined the server at least once.",
            ephemeral=True
        )

        return

    minecraft_uuid = player.get(
        "playerUUID"
    )

    stored_name = clean_minecraft_name(
        player.get(
            "playerName",
            minecraft_name
        )
    )

    if not minecraft_uuid:

        await interaction.followup.send(
            "❌ Plan did not return a Minecraft UUID for that player.",
            ephemeral=True
        )

        return

    save_minecraft_link(
        member.id,
        minecraft_uuid,
        stored_name
    )

    await interaction.followup.send(
        "🌙 **MINECRAFT ACCOUNT LINKED**\n\n"
        f"**Discord:** {member.mention}\n"
        f"**Minecraft:** `{stored_name}`\n"
        f"**UUID:** `{minecraft_uuid}`",
        ephemeral=True
    )


# ============================================================
# UNLINK MINECRAFT PLAYER
# ============================================================

@bot.tree.command(
    name="unlink-player",
    description="Unlink a Discord member from Minecraft."
)
@app_commands.describe(
    member="The Discord member"
)
async def unlink_player(
    interaction: discord.Interaction,
    member: discord.Member
):

    if not await check_mine_bot_channel(
        interaction
    ):
        return

    if not is_moderator(
        interaction.user
    ):

        await interaction.response.send_message(
            "❌ Only administrators or moderators can unlink Minecraft accounts.",
            ephemeral=True
        )

        return

    existing = get_minecraft_link(
        member.id
    )

    if existing is None:

        await interaction.response.send_message(
            f"ℹ️ {member.mention} does not have a linked Minecraft account.",
            ephemeral=True
        )

        return

    minecraft_name = existing[1]

    delete_minecraft_link(
        member.id
    )

    await interaction.response.send_message(
        "🌙 **MINECRAFT ACCOUNT UNLINKED**\n\n"
        f"**Discord:** {member.mention}\n"
        f"**Minecraft:** `{minecraft_name}`",
        ephemeral=True
    )


# ============================================================
# MINECRAFT MONTHLY PLAYTIME
# ============================================================

@bot.tree.command(
    name="playtime",
    description="Check a linked Minecraft player's playtime this month."
)
@app_commands.describe(
    member="Optional Discord member"
)
async def playtime(
    interaction: discord.Interaction,
    member: discord.Member | None = None
):

    if not await check_mine_bot_channel(
        interaction
    ):
        return

    target = member or interaction.user

    link = get_minecraft_link(
        target.id
    )

    if link is None:

        await interaction.response.send_message(
            f"❌ {target.mention} does not have a linked Minecraft account.",
            ephemeral=True
        )

        return

    minecraft_uuid = link[0]
    minecraft_name = link[1]

    await interaction.response.defer()

    try:
        milliseconds = await bot.loop.run_in_executor(
            None,
            get_monthly_playtime,
            minecraft_uuid
        )

    except Exception as error:
        print(
            f"❌ Plan API error while getting monthly playtime: {error}"
        )

        await interaction.followup.send(
            "❌ I couldn't retrieve the monthly Minecraft playtime right now."
        )

        return

    month_name = get_month_name()

    embed = discord.Embed(
        title=f"🌙  {month_name.upper()}",
        description="## ✦  MINECRAFT MONTHLY PLAYTIME",
        color=discord.Color.from_rgb(
            88,
            52,
            120
        )
    )

    embed.add_field(
        name="Discord",
        value=target.mention,
        inline=True
    )

    embed.add_field(
        name="Minecraft",
        value=f"`{minecraft_name}`",
        inline=True
    )

    embed.add_field(
        name="Monthly Playtime",
        value=f"**{format_playtime(milliseconds)}**",
        inline=False
    )

    embed.set_footer(
        text=f"Bleeding Moon  •  {month_name}"
    )

    await interaction.followup.send(
        embed=embed
    )


# ============================================================
# MINECRAFT MONTHLY TOP 15
# ============================================================

@bot.tree.command(
    name="playtime-top",
    description="Show the top 15 Minecraft players this month."
)
async def playtime_top(
    interaction: discord.Interaction
):

    if not await check_mine_bot_channel(
        interaction
    ):
        return

    await interaction.response.defer()

    try:
        data = await bot.loop.run_in_executor(
            None,
            get_plan_players
        )

        players = extract_players(
            data
        )

    except Exception as error:
        print(
            f"❌ Plan API error while getting players: {error}"
        )

        await interaction.followup.send(
            "❌ I couldn't retrieve the Minecraft player list right now."
        )

        return

    unique_players = {}

    for player in players:

        player_uuid = player.get(
            "playerUUID"
        )

        if player_uuid:
            unique_players[
                player_uuid
            ] = player

    players = list(
        unique_players.values()
    )

    monthly_players = []

    for player in players:

        player_uuid = player.get(
            "playerUUID"
        )

        try:
            monthly_playtime = (
                await bot.loop.run_in_executor(
                    None,
                    get_monthly_playtime,
                    player_uuid
                )
            )

        except Exception as error:
            print(
                f"❌ Failed to get monthly playtime for "
                f"{player.get('playerName', 'Unknown')}: {error}"
            )
            continue

        if monthly_playtime > 0:

            monthly_players.append(
                {
                    "player": player,
                    "playtime": monthly_playtime
                }
            )

    monthly_players.sort(
        key=lambda item: item["playtime"],
        reverse=True
    )

    top_players = monthly_players[
        :LEADERBOARD_SIZE
    ]

    if not top_players:

        await interaction.followup.send(
            f"❌ No Minecraft playtime has been recorded "
            f"for **{get_month_name()}** yet."
        )

        return

    lines = []

    medals = {
        1: "🥇",
        2: "🥈",
        3: "🥉"
    }

    for position, item in enumerate(
        top_players,
        start=1
    ):

        player = item["player"]
        playtime = item["playtime"]

        minecraft_name = clean_minecraft_name(
            player.get(
                "playerName",
                "Unknown"
            )
        )

        prefix = medals.get(
            position,
            f"`{position:02d}`"
        )

        lines.append(
            f"{prefix} **{minecraft_name}** — "
            f"`{format_playtime(playtime)}`"
        )

    month_name = get_month_name()

    embed = discord.Embed(
        title=f"🌙  {month_name.upper()}",
        description=(
            "## ✦  MINECRAFT PLAYTIME LEADERBOARD\n\n"
            + "\n".join(lines)
        ),
        color=discord.Color.from_rgb(
            88,
            52,
            120
        )
    )

    embed.set_footer(
        text=f"Top 15  •  {month_name}  •  Powered by Plan"
    )

    await interaction.followup.send(
        embed=embed
    )


# ============================================================
# TEXT LEVEL
# ============================================================

@bot.tree.command(
    name="text-level",
    description="Show a member's lifetime text level."
)
@app_commands.describe(
    member="Optional Discord member"
)
async def text_level(
    interaction: discord.Interaction,
    member: discord.Member | None = None
):

    target = member or interaction.user

    total_seconds = get_text_activity(
        target.id
    )

    level = get_text_level(
        total_seconds
    )

    embed = discord.Embed(
        title="🌙  BLEEDING MOON",
        description="## ✦  TEXT LEVEL",
        color=discord.Color.from_rgb(
            88,
            52,
            120
        )
    )

    if level <= 0:
        embed.add_field(
            name="Current Level",
            value="**Level 0**",
            inline=False
        )

        first_level, first_hours = TEXT_LEVELS[0]

        embed.add_field(
            name="Next Level",
            value=f"Level {first_level} at {first_hours}h",
            inline=False
        )

    else:
        embed.add_field(
            name="Current Level",
            value=f"**Level {level}**",
            inline=True
        )

        embed.add_field(
            name="Lifetime Activity",
            value=f"**{format_seconds(total_seconds)}**",
            inline=True
        )

        next_level = get_next_text_level(level)

        if next_level:
            next_level_number, required_hours = next_level

            remaining_seconds = max(
                0,
                int(required_hours * 3600) - total_seconds
            )

            embed.add_field(
                name=f"Next Level — {next_level_number}",
                value=(
                    f"**{format_seconds(remaining_seconds)}** remaining"
                ),
                inline=False
            )
        else:
            embed.add_field(
                name="Status",
                value="🏆 **Maximum Level Reached**",
                inline=False
            )

    embed.set_footer(
        text="Text level is lifetime-based and never resets."
    )

    await interaction.response.send_message(
        embed=embed
    )


# ============================================================
# TEXT LEADERBOARD
# ============================================================

def get_text_period(period):
    now = local_now()
    current_timestamp = now.timestamp()

    if period == "week":
        return (
            get_week_start_timestamp(),
            current_timestamp,
            get_week_name()
        )

    if period == "month":
        return (
            get_month_start_timestamp(),
            current_timestamp,
            get_month_name()
        )

    return (
        0,
        current_timestamp,
        "LIFETIME"
    )


@bot.tree.command(
    name="text-top",
    description="Show the text activity leaderboard."
)
@app_commands.describe(
    period="Leaderboard period"
)
@app_commands.choices(
    period=[
        app_commands.Choice(
            name="Week",
            value="week"
        ),
        app_commands.Choice(
            name="Month",
            value="month"
        ),
        app_commands.Choice(
            name="Lifetime",
            value="lifetime"
        )
    ]
)
async def text_top(
    interaction: discord.Interaction,
    period: app_commands.Choice[str]
):

    await interaction.response.defer()

    start_timestamp, end_timestamp, period_name = get_text_period(
        period.value
    )

    user_ids = get_all_text_users()

    leaderboard = []

    for user_id in user_ids:

        seconds = get_text_activity(
            user_id,
            start_timestamp,
            end_timestamp
        )

        if seconds > 0:
            leaderboard.append(
                (
                    user_id,
                    seconds
                )
            )

    leaderboard.sort(
        key=lambda item: item[1],
        reverse=True
    )

    leaderboard = leaderboard[
        :LEADERBOARD_SIZE
    ]

    if not leaderboard:
        await interaction.followup.send(
            f"❌ No text activity has been recorded for **{period_name}** yet."
        )
        return

    lines = []

    medals = {
        1: "🥇",
        2: "🥈",
        3: "🥉"
    }

    for position, (user_id, seconds) in enumerate(
        leaderboard,
        start=1
    ):

        member = interaction.guild.get_member(
            user_id
        )

        if member:
            name = member.display_name
            mention = member.mention
        else:
            name = f"User {user_id}"
            mention = name

        prefix = medals.get(
            position,
            f"`{position:02d}`"
        )

        lines.append(
            f"{prefix} {mention} — **{format_seconds(seconds)}**"
        )

    embed = discord.Embed(
        title=f"💬  {period_name.upper()}",
        description=(
            "## ✦  TEXT ACTIVITY LEADERBOARD\n\n"
            + "\n".join(lines)
        ),
        color=discord.Color.from_rgb(
            88,
            52,
            120
        )
    )

    embed.set_footer(
        text="Top 15  •  Text activity"
    )

    await interaction.followup.send(
        embed=embed
    )


# ============================================================
# VOICE MONTHLY LEADERBOARD
# ============================================================

@bot.tree.command(
    name="voice-top",
    description="Show the top 15 voice users for this month."
)
async def voice_top(
    interaction: discord.Interaction
):

    await interaction.response.defer()

    month_start = get_month_start_timestamp()
    current_timestamp = now_utc_timestamp()

    user_ids = get_all_voice_users()

    leaderboard = []

    for user_id in user_ids:

        seconds = get_voice_activity(
            user_id,
            month_start,
            current_timestamp
        )

        if seconds > 0:
            leaderboard.append(
                (
                    user_id,
                    seconds
                )
            )

    leaderboard.sort(
        key=lambda item: item[1],
        reverse=True
    )

    leaderboard = leaderboard[
        :LEADERBOARD_SIZE
    ]

    month_name = get_month_name()

    if not leaderboard:
        await interaction.followup.send(
            f"❌ No voice activity has been recorded for **{month_name}** yet."
        )
        return

    lines = []

    medals = {
        1: "🥇",
        2: "🥈",
        3: "🥉"
    }

    for position, (user_id, seconds) in enumerate(
        leaderboard,
        start=1
    ):

        member = interaction.guild.get_member(
            user_id
        )

        if member:
            mention = member.mention
        else:
            mention = f"User {user_id}"

        prefix = medals.get(
            position,
            f"`{position:02d}`"
        )

        lines.append(
            f"{prefix} {mention} — **{format_seconds(seconds)}**"
        )

    embed = discord.Embed(
        title=f"🎙️  {month_name.upper()}",
        description=(
            "## ✦  VOICE ACTIVITY LEADERBOARD\n\n"
            + "\n".join(lines)
        ),
        color=discord.Color.from_rgb(
            88,
            52,
            120
        )
    )

    embed.set_footer(
        text=f"Top 15  •  {month_name}  •  Voice activity"
    )

    await interaction.followup.send(
        embed=embed
    )


# ============================================================
# INITIALIZE DATABASES
# ============================================================

initialize_minecraft_database()
initialize_activity_database()


# ============================================================
# START BOT
# ============================================================

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN was not found in .env"
    )

bot.run(TOKEN)

import os
import json
import sqlite3
import urllib.parse
import urllib.request
import gzip
import zlib
from datetime import datetime

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

# Plan API
PLAN_API = "http://thala.ender.co.in:45801"

# Plan server UUID
PLAN_SERVER_UUID = "b8bc0c5d-3735-4e95-8b7e-672fd1947580"

# Local database
DATABASE_FILE = "minecraft_links.db"

# Leaderboard size
LEADERBOARD_SIZE = 15


# ============================================================
# BOT SETUP
# ============================================================

intents = discord.Intents.default()

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# ============================================================
# DATABASE
# ============================================================

def initialize_database():

    connection = sqlite3.connect(
        DATABASE_FILE
    )

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


def get_minecraft_link(
    discord_user_id
):

    connection = sqlite3.connect(
        DATABASE_FILE
    )

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


def save_minecraft_link(
    discord_user_id,
    minecraft_uuid,
    minecraft_name
):

    connection = sqlite3.connect(
        DATABASE_FILE
    )

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


def delete_minecraft_link(
    discord_user_id
):

    connection = sqlite3.connect(
        DATABASE_FILE
    )

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

        # Handle gzip
        if content_encoding == "gzip":

            data = gzip.decompress(
                data
            )

        # Handle deflate
        elif content_encoding == "deflate":

            data = zlib.decompress(
                data
            )

        # Some servers return gzip data
        # without setting Content-Encoding.
        elif (
            len(data) >= 2
            and data[:2] == b"\x1f\x8b"
        ):

            data = gzip.decompress(
                data
            )

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

def clean_minecraft_name(
    name
):

    name = str(
        name
    ).strip()

    # Plan stores Floodgate players with
    # a leading dot.
    if name.startswith("."):

        name = name[1:]

    return name


# ============================================================
# FIND PLAYER IN PLAN
# ============================================================

def find_plan_player(
    minecraft_name
):

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
# CURRENT MONTH
# ============================================================

def get_current_month():

    now = datetime.now()

    return now.year, now.month


def get_month_name():

    now = datetime.now()

    return now.strftime(
        "%B %Y"
    )


def get_month_start_millis():

    now = datetime.now()

    month_start = datetime(
        now.year,
        now.month,
        1
    )

    return int(
        month_start.timestamp() * 1000
    )


# ============================================================
# GET MONTHLY PLAYTIME
# ============================================================

def get_monthly_playtime(
    player_uuid
):

    month_start = (
        get_month_start_millis()
    )

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
# FORMAT PLAYTIME
# ============================================================

def format_playtime(
    milliseconds
):

    total_seconds = (
        milliseconds // 1000
    )

    days = (
        total_seconds // 86400
    )

    total_seconds %= 86400

    hours = (
        total_seconds // 3600
    )

    total_seconds %= 3600

    minutes = (
        total_seconds // 60
    )

    seconds = (
        total_seconds % 60
    )

    parts = []

    if days:

        parts.append(
            f"{days}d"
        )

    if hours:

        parts.append(
            f"{hours}h"
        )

    if minutes:

        parts.append(
            f"{minutes}m"
        )

    if seconds or not parts:

        parts.append(
            f"{seconds}s"
        )

    return " ".join(
        parts
    )


# ============================================================
# CHECK MINECRAFT CHANNEL
# ============================================================

async def check_mine_bot_channel(
    interaction
):

    if (
        interaction.channel_id
        != MINE_BOT_CHANNEL_ID
    ):

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

def is_moderator(
    member
):

    permissions = (
        member.guild_permissions
    )

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
# MONTHLY PLAYTIME
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

    target = (
        member
        or interaction.user
    )

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
# MONTHLY TOP 15 LEADERBOARD
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

    # Remove duplicate UUIDs
    unique_players = {}

    for player in players:

        player_uuid = player.get(
            "playerUUID"
        )

        if not player_uuid:

            continue

        unique_players[
            player_uuid
        ] = player

    players = list(
        unique_players.values()
    )

    # ========================================================
    # GET MONTHLY PLAYTIME FOR EVERY PLAYER
    # ========================================================

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

        # Only include players who have played this month
        if monthly_playtime > 0:

            monthly_players.append(
                {
                    "player": player,
                    "playtime": monthly_playtime
                }
            )

    # ========================================================
    # SORT BY MONTHLY PLAYTIME
    # ========================================================

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

    # ========================================================
    # BUILD LEADERBOARD
    # ========================================================

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

    # ========================================================
    # EMBED
    # ========================================================

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
# START BOT
# ============================================================

initialize_database()


if not TOKEN:

    raise RuntimeError(
        "DISCORD_TOKEN was not found in .env"
    )


bot.run(TOKEN)
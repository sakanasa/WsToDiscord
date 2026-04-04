"""
Emoji statistics slash command.

Scans server message history (text content + reactions) and ranks
emojis by total usage count.

Requires the `message_content` privileged intent to read emoji inside
message text.  Reaction counts are always collected regardless of intent.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Optional

import discord
from discord import app_commands

# Matches custom Discord emojis: <:name:id> or <a:name:id> (animated)
_CUSTOM_EMOJI_RE = re.compile(r"<(a?):([^:>\s]+):(\d+)>")

# Matches the most common Unicode emoji ranges
_UNICODE_EMOJI_RE = re.compile(
    "(?:"
    "[\U0001F600-\U0001F64F]|"  # Emoticons
    "[\U0001F300-\U0001F5FF]|"  # Symbols & Pictographs
    "[\U0001F680-\U0001F6FF]|"  # Transport & Map
    "[\U0001F700-\U0001F77F]|"  # Alchemical Symbols
    "[\U0001F780-\U0001F7FF]|"  # Geometric Shapes Extended
    "[\U0001F800-\U0001F8FF]|"  # Supplemental Arrows-C
    "[\U0001F900-\U0001F9FF]|"  # Supplemental Symbols and Pictographs
    "[\U0001FA00-\U0001FA6F]|"  # Chess Symbols
    "[\U0001FA70-\U0001FAFF]|"  # Symbols and Pictographs Extended-A
    "[\U00002702-\U000027B0]|"  # Dingbats
    "[\U000024C2-\U0001F251]"   # Enclosed characters
    ")",
    flags=re.UNICODE,
)


def _count_text_emojis(content: str, counter: Counter) -> None:
    """Count all emojis in a message string and update *counter* in-place."""
    for match in _CUSTOM_EMOJI_RE.finditer(content):
        animated, name, emoji_id = match.group(1), match.group(2), match.group(3)
        key = f"<{'a' if animated else ''}:{name}:{emoji_id}>"
        counter[key] += 1

    # Strip custom emoji markup before scanning for unicode emojis
    stripped = _CUSTOM_EMOJI_RE.sub("", content)
    for match in _UNICODE_EMOJI_RE.finditer(stripped):
        counter[match.group(0)] += 1


def _reaction_key(emoji: discord.PartialEmoji | discord.Emoji | str) -> str | None:
    """Return a stable string key for a reaction emoji."""
    if isinstance(emoji, str):
        return emoji
    if isinstance(emoji, (discord.Emoji, discord.PartialEmoji)):
        if emoji.id:
            return f"<{'a' if emoji.animated else ''}:{emoji.name}:{emoji.id}>"
        return emoji.name or str(emoji)
    return None


async def _scan_channel(
    channel: discord.TextChannel,
    limit: int,
    counter: Counter,
) -> int:
    """Scan *channel* history and update *counter*.  Returns message count."""
    count = 0
    async for message in channel.history(limit=limit, oldest_first=False):
        # Message text (requires message_content privileged intent)
        if message.content:
            _count_text_emojis(message.content, counter)

        # Reactions (always available, no privileged intent needed)
        for reaction in message.reactions:
            key = _reaction_key(reaction.emoji)
            if key:
                counter[key] += reaction.count

        count += 1
    return count


def _medal(rank: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"`{rank}.`")


@app_commands.command(name="emoji-stats", description="顯示伺服器最常用的表情符號排行榜")
@app_commands.describe(
    top="顯示前幾名（預設 10，最大 25）",
    scan_limit="每個頻道掃描的訊息數量（預設 500，最大 2000）",
)
async def emoji_stats_command(
    interaction: discord.Interaction,
    top: Optional[int] = 10,
    scan_limit: Optional[int] = 500,
) -> None:
    top = max(1, min(top or 10, 25))
    scan_limit = max(1, min(scan_limit or 500, 2000))

    await interaction.response.defer(thinking=True)

    guild = interaction.guild
    if guild is None:
        await interaction.followup.send("此指令只能在伺服器內使用。", ephemeral=True)
        return

    counter: Counter = Counter()
    total_messages = 0
    scanned_channels = 0

    for channel in guild.text_channels:
        perms = channel.permissions_for(guild.me)
        if not (perms.read_messages and perms.read_message_history):
            continue
        try:
            n = await _scan_channel(channel, scan_limit, counter)
            total_messages += n
            scanned_channels += 1
        except discord.Forbidden:
            continue
        except Exception:
            continue

    if not counter:
        await interaction.followup.send("在可讀取的頻道中沒有找到任何表情符號。")
        return

    top_emojis = counter.most_common(top)
    lines = [
        f"{_medal(i + 1)} {key} — **{count:,}** 次"
        for i, (key, count) in enumerate(top_emojis)
    ]

    embed = discord.Embed(
        title=f"📊 表情符號排行榜 Top {top}",
        description="\n".join(lines),
        color=0x5865F2,  # Discord Blurple
    )
    embed.set_footer(
        text=(
            f"掃描了 {scanned_channels} 個頻道、{total_messages:,} 則訊息"
            f"（每頻道最多 {scan_limit} 則）"
        )
    )
    await interaction.followup.send(embed=embed)

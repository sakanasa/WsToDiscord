"""
Discord notification sender for WsToDiscord.
Sends rich embed messages for product change events.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import List
from urllib.parse import quote, urlparse, urlunparse

import discord
import requests as http_requests

from scrapers import ChangeEvent

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

# Embed colors
COLOR_NEW = 0x00C851        # Green
COLOR_PRICE_CHANGE = 0xFFBB33  # Amber
COLOR_SOLD_OUT = 0xFF4444   # Red

SITE_NAMES = {
    "hobbystation": "ホビーステーション",
    "fukufuku": "ふくふくとれか",
}

EVENT_TITLES = {
    "new": "🆕 新着商品",
    "price_change": "💴 価格変更",
    "sold_out": "❌ 売り切れ",
}

EVENT_COLORS = {
    "new": COLOR_NEW,
    "price_change": COLOR_PRICE_CHANGE,
    "sold_out": COLOR_SOLD_OUT,
}


def _encode_url(url: str) -> str:
    """Percent-encode non-ASCII characters in a URL, preserving structure."""
    try:
        parsed = urlparse(url)
        encoded_path = quote(parsed.path, safe='/:@!$&\'()*+,;=%')
        encoded_query = quote(parsed.query, safe='=&+%')
        return urlunparse(parsed._replace(path=encoded_path, query=encoded_query))
    except Exception:
        return url


_DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def _download_image(url: str, referer: str) -> bytes | None:
    """Download an image with proper Referer header.

    Returns image bytes, or None on failure.
    """
    try:
        headers = {**_DOWNLOAD_HEADERS, "Referer": referer}
        resp = http_requests.get(_encode_url(url), headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        logger.warning("discord: failed to download image %s: %s", url, e)
        return None


def _build_embed(event: ChangeEvent) -> tuple[discord.Embed, list[discord.File]]:
    """Build embed and attached image files for a change event."""
    snap = event.snapshot
    site_name = SITE_NAMES.get(snap.site, snap.site)
    title = EVENT_TITLES.get(event.event_type, event.event_type)
    color = EVENT_COLORS.get(event.event_type, 0x888888)

    embed = discord.Embed(
        title=title,
        url=_encode_url(snap.product_url),
        color=color,
        timestamp=datetime.now(JST),
    )

    embed.add_field(name="商品名", value=snap.name, inline=False)

    if event.event_type == "new":
        price_display = f"¥{snap.price_raw}" if snap.price_raw else "價格不明❓"
        embed.add_field(name="価格", value=price_display, inline=True)
        embed.add_field(name="在庫", value="有存貨😄" if snap.in_stock else "已售完😫", inline=True)

    elif event.event_type == "price_change":
        old_price_display = f"¥{event.old_price_raw}" if event.old_price_raw else "不明"
        new_price_display = f"¥{snap.price_raw}" if snap.price_raw else "不明"

        if event.old_price_int and snap.price_int:
            diff = snap.price_int - event.old_price_int
            diff_str = f"({'+' if diff >= 0 else ''}{diff:,}円)"
        else:
            diff_str = ""

        embed.add_field(
            name="價格變更⚠️",
            value=f"{old_price_display} → {new_price_display} {diff_str}",
            inline=False,
        )

    elif event.event_type == "sold_out":
        price_display = f"¥{snap.price_raw}" if snap.price_raw else "價格不明❓"
        embed.add_field(name="価格", value=price_display, inline=True)

    embed.add_field(name="ショップ", value=f"[{site_name}]({snap.product_url})", inline=True)

    # Download images and attach as files (server blocks hotlinking)
    files: list[discord.File] = []
    referer = snap.product_url

    if snap.image_url:
        ext = os.path.splitext(urlparse(snap.image_url).path)[1] or ".png"
        fname_main = f"main_{snap.product_id}{ext}"
        data = _download_image(snap.image_url, referer)
        if data:
            files.append(discord.File(io.BytesIO(data), filename=fname_main))
            embed.set_image(url=f"attachment://{fname_main}")

    if snap.image_url_2:
        ext = os.path.splitext(urlparse(snap.image_url_2).path)[1] or ".png"
        fname_thumb = f"thumb_{snap.product_id}{ext}"
        data = _download_image(snap.image_url_2, referer)
        if data:
            files.append(discord.File(io.BytesIO(data), filename=fname_thumb))
            embed.set_thumbnail(url=f"attachment://{fname_thumb}")

    embed.set_footer(text=site_name)

    return embed, files


async def send_notifications(
    events: List[ChangeEvent],
    bot_token: str,
    channel_id: int,
) -> None:
    """Send Discord embed notifications for all change events."""
    if not events:
        logger.info("discord: no events to send")
        return

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        logger.info("discord: logged in as %s", client.user)
        try:
            channel = client.get_channel(channel_id)
            if channel is None:
                logger.info("discord: fetching channel/thread id=%d", channel_id)
                channel = await client.fetch_channel(channel_id)
            logger.info("discord: channel resolved: %s (type=%s)", channel, type(channel).__name__)

            for event in events:
                try:
                    embed, files = _build_embed(event)
                    await channel.send(embed=embed, files=files)
                    logger.info(
                        "discord: sent %s for %s",
                        event.event_type, event.snapshot.state_key
                    )
                    await asyncio.sleep(1)  # Respect rate limits
                except Exception as e:
                    logger.error(
                        "discord: URL repr: %r", event.snapshot.product_url
                    )
                    logger.error(
                        "discord: failed to send %s for %s: %s",
                        event.event_type, event.snapshot.state_key, e
                    )
        except Exception as e:
            logger.error("discord: channel fetch failed: %s", e)
        finally:
            await client.close()

    await client.start(bot_token)


def build_product_embed(product: dict) -> tuple[discord.Embed, list[discord.File]]:
    """Build a stock-list embed with image for a single product from state dict."""
    site_name = SITE_NAMES.get(product["site"], product["site"])
    product_id = product["product_id"]
    encoded_url = _encode_url(product["product_url"])

    embed = discord.Embed(
        title=product["name"],
        url=encoded_url,
        color=COLOR_NEW,
        timestamp=datetime.now(JST),
    )
    embed.add_field(name="価格", value=f"¥{product['price_raw']}", inline=True)
    embed.add_field(name="ショップ", value=f"[{site_name}]({encoded_url})", inline=True)
    embed.set_footer(text=site_name)

    files: list[discord.File] = []
    referer = product["product_url"]

    image_url = product.get("image_url", "")
    if image_url:
        ext = os.path.splitext(urlparse(image_url).path)[1] or ".png"
        fname = f"main_{product_id}{ext}"
        data = _download_image(image_url, referer)
        if data:
            files.append(discord.File(io.BytesIO(data), filename=fname))
            embed.set_image(url=f"attachment://{fname}")

    image_url_2 = product.get("image_url_2", "")
    if image_url_2:
        ext = os.path.splitext(urlparse(image_url_2).path)[1] or ".png"
        fname2 = f"thumb_{product_id}{ext}"
        data = _download_image(image_url_2, referer)
        if data:
            files.append(discord.File(io.BytesIO(data), filename=fname2))
            embed.set_thumbnail(url=f"attachment://{fname2}")

    return embed, files


async def send_to_channel(
    channel: discord.abc.Messageable,
    events: List[ChangeEvent],
) -> None:
    """Send embed notifications to an existing channel object."""
    for event in events:
        try:
            embed, files = _build_embed(event)
            await channel.send(embed=embed, files=files)
            logger.info("discord: sent %s for %s", event.event_type, event.snapshot.state_key)
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(
                "discord: failed to send %s for %s: %s",
                event.event_type, event.snapshot.state_key, e,
            )

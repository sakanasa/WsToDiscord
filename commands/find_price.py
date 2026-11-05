"""
/findprice — look up price and stock for one or more card numbers across shops.

Shops queried (link only: ドラゴンスター, カードショップイセイ): 遊々亭, ふくふくとれか, ホビーステーション, まんぞく屋, マスターズスクウェア, カードラボ, BIGWEB, グランドパンダキャニオン, 露天（牌塔）.
ドラゴンスター sits behind Cloudflare, so only a search link is provided.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from urllib.parse import quote

import discord
import requests
from discord import app_commands

import discord_notifier
from card_search import (
    MAX_CARDS,
    CardListing,
    bigweb,
    clabo,
    fx,
    gurapan,
    fukufuku,
    hobbystation,
    manzokuya,
    mastersquare,
    official_image_url,
    parse_card_list,
    ruten,
    yuyutei,
)
from scrapers.hobbystation import HEADERS

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

# (display name, module) — order is the order of fields in the embed
SITES = [
    ("遊々亭", yuyutei),
    ("ふくふくとれか", fukufuku),
    ("ホビーステーション", hobbystation),
    ("まんぞく屋", manzokuya),
    ("マスターズスクウェア", mastersquare),
    ("カードラボ", clabo),
    ("BIGWEB", bigweb),
    ("グランドパンダ", gurapan),
    ("露天 牌塔", ruten),
]


def _dorasuta_link(card_no: str) -> str:
    return f"https://www.google.com/search?q={quote(f'site:dorasuta.jp {card_no}')}"


def _isei_link(card_no: str) -> str:
    return f"https://cardshopisei.com/search?q={quote(card_no, safe='')}&type=product"


def _image_exists(url: str) -> bool:
    try:
        return requests.head(url, headers=HEADERS, timeout=15, allow_redirects=True).status_code == 200
    except requests.RequestException:
        return False


def _format_line(l: CardListing) -> str:
    label = f"`{l.rarity or '—'}`"
    if not l.in_stock:
        return f"{label} 品切れ"
    price = f"{l.currency}{l.price_raw}" if l.price_raw else "價格不明"
    return f"{label} [{price}]({l.url}) 在庫 {l.stock_text}"


def _to_jpy(l: CardListing, twd_rate: float) -> float:
    return l.price_int * twd_rate if l.currency == "NT$" else float(l.price_int)


def _lowest_price_lines(found: List[CardListing], twd_rate: float) -> List[str]:
    """Cheapest in-stock listing per card number, comparing NT$ prices by their ¥ equivalent."""
    site_names = {mod.SITE: name for name, mod in SITES}
    best: dict[str, CardListing] = {}
    for l in found:
        if not (l.in_stock and l.price_int > 0):
            continue
        if l.card_no not in best or _to_jpy(l, twd_rate) < _to_jpy(best[l.card_no], twd_rate):
            best[l.card_no] = l

    lines = []
    for no, l in best.items():
        price = f"{l.currency}{l.price_raw}"
        if l.currency == "NT$":
            price += f" ≈ ¥{round(_to_jpy(l, twd_rate)):,}"
        lines.append(f"`{l.rarity or no}` [{price}]({l.url}) {site_names.get(l.site, l.site)}")
    return lines


async def _search_card(card_no: str) -> discord.Embed:
    tasks = [asyncio.to_thread(mod.search, card_no) for _, mod in SITES]
    image_task = asyncio.to_thread(_image_exists, official_image_url(card_no))
    fx_task = asyncio.to_thread(fx.twd_to_jpy)
    *site_results, has_official_image, fx_result = await asyncio.gather(
        *tasks, image_task, fx_task, return_exceptions=True
    )
    twd_rate, rate_is_live = fx_result if not isinstance(fx_result, Exception) else (fx.FALLBACK_RATE, False)

    found: List[CardListing] = []
    embed = discord.Embed(color=0x5865F2, timestamp=datetime.now(JST))
    for (display_name, mod), result in zip(SITES, site_results):
        if isinstance(result, Exception):
            logger.error("findprice: %s search failed for %s: %s", mod.SITE, card_no, result)
            embed.add_field(name=display_name, value="⚠️ 查詢失敗", inline=False)
            continue
        if not result:
            embed.add_field(name=display_name, value=getattr(mod, "EMPTY_TEXT", "查無此卡"), inline=False)
            continue
        found.extend(result)
        discord_notifier.add_chunked_fields(embed, display_name, [_format_line(l) for l in result])

    # Shops that block automated access (Cloudflare / 429): link only, no scraping
    embed.add_field(
        name="🔗 只提供搜尋連結",
        value=(
            f"[ドラゴンスター]({_dorasuta_link(card_no)}) ／ "
            f"[カードショップイセイ]({_isei_link(card_no)})"
        ),
        inline=False,
    )

    # Divider, then the cheapest in-stock option(s) on the last line
    lowest = _lowest_price_lines(found, twd_rate)
    lines = lowest if lowest else ["目前各店皆無庫存"]
    if any("NT$" in line for line in lowest):
        lines.append(f"*匯率 1 NT$ ≈ ¥{twd_rate:.2f}{'' if rate_is_live else '（預設值，即時匯率取得失敗）'}*")
    embed.add_field(name="──────────────", value="💰 **最低價**\n" + "\n".join(lines), inline=False)

    # Prefer the base card's name; other rarities may carry notes like "(サイン入り)"
    base = next((l for l in found if l.card_no == card_no and l.name), None) \
        or next((l for l in found if l.name), None)
    name = re.sub(r"[(（][^()（）]*[)）]\s*$", "", base.name).strip() if base else ""
    embed.title = f"🔎 {card_no} {name}".strip()

    if has_official_image is True:
        embed.set_thumbnail(url=official_image_url(card_no))
    else:
        fallback = next((l.image_url for l in found if l.image_url and l.site == "yuyutei"), "") \
            or next((l.image_url for l in found if l.image_url), "")
        if fallback:
            embed.set_thumbnail(url=fallback)

    embed.set_footer(text=f"查詢時間: {datetime.now(JST).strftime('%Y-%m-%d %H:%M')} JST")
    return embed


@app_commands.command(
    name="findprice",
    description="輸入卡號查詢各店價格與庫存（可用 Tab 切換到 card_2、card_3… 填入多張）",
)
@app_commands.describe(
    card_1="卡號，例如 NIK/S135-001（也可在這欄用空白分隔多張）",
    card_2="第 2 張卡號（選填）",
    card_3="第 3 張卡號（選填）",
    card_4="第 4 張卡號（選填）",
    card_5="第 5 張卡號（選填）",
)
async def find_price_command(
    interaction: discord.Interaction,
    card_1: str,
    card_2: Optional[str] = None,
    card_3: Optional[str] = None,
    card_4: Optional[str] = None,
    card_5: Optional[str] = None,
) -> None:
    valid, invalid = parse_card_list(" ".join(c for c in (card_1, card_2, card_3, card_4, card_5) if c))

    if not valid:
        await interaction.response.send_message(
            "請輸入有效的卡號，例如 `NIK/S135-001`（多張可用空白分隔，或用 Tab 切到 card_2、card_3 填入）。"
            + (f"\n無法辨識：{'、'.join(invalid)}" if invalid else ""),
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    notes = []
    if len(valid) > MAX_CARDS:
        notes.append(f"一次最多查 {MAX_CARDS} 張，已略過：{'、'.join(valid[MAX_CARDS:])}")
        valid = valid[:MAX_CARDS]
    if invalid:
        notes.append(f"無法辨識：{'、'.join(invalid)}")

    embeds = await asyncio.gather(*(_search_card(no) for no in valid))
    await interaction.followup.send(content="\n".join(notes) or None, embeds=list(embeds))

"""
Persistent Discord bot for WsToDiscord.

Usage:
    python bot.py

Features:
    - Scheduled scrape every 30 minutes (Hobbystation, Fukufuku)
    - Scheduled scrape every 10 minutes (Mercari TW)
    - /stock    — list currently in-stock products from state.json
    - /stockimg — list in-stock products with images
    - /update   — force an immediate scrape + notify cycle (all sites)
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

import discord_notifier
import storage
import scrapers.hobbystation as hs
import scrapers.fukufuku as ff
import scrapers.mercari as mc
from commands.emoji_stats import emoji_stats_command

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])
GUILD_ID = int(os.environ["DISCORD_GUILD_ID"])
LOCAL_STATE_PATH = os.environ.get("LOCAL_STATE_PATH", "state.json")

JST = timezone(timedelta(hours=9))

SITE_NAMES = {
    "hobbystation": "ホビーステーション",
    "fukufuku": "ふくふくとれか",
    "mercari": "Mercari 台灣",
}


class WsBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # Privileged — enable in Discord Dev Portal
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self._lock = asyncio.Lock()

    async def setup_hook(self) -> None:
        self.tree.add_command(emoji_stats_command)
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        logger.info("bot: slash commands synced (guild=%d)", GUILD_ID)
        self.scheduled_scrape.start()
        self.scheduled_mercari_scrape.start()

    @tasks.loop(minutes=30)
    async def scheduled_scrape(self) -> None:
        logger.info("bot: scheduled scrape starting (hobbystation + fukufuku)")
        channel = await self.fetch_channel(CHANNEL_ID)
        n = await self._do_scrape_cycle(channel, force_notify=False)
        logger.info("bot: scheduled scrape done, %d events", n)

    @scheduled_scrape.before_loop
    async def _before_scheduled_scrape(self) -> None:
        await self.wait_until_ready()

    @tasks.loop(minutes=10)
    async def scheduled_mercari_scrape(self) -> None:
        logger.info("bot: Mercari scrape starting")
        channel = await self.fetch_channel(CHANNEL_ID)
        n = await self._do_mercari_scrape_cycle(channel, force_notify=False)
        logger.info("bot: Mercari scrape done, %d events", n)

    @scheduled_mercari_scrape.before_loop
    async def _before_scheduled_mercari_scrape(self) -> None:
        await self.wait_until_ready()

    async def _do_scrape_cycle(
        self,
        channel: discord.abc.Messageable,
        *,
        force_notify: bool = False,
    ) -> int:
        """Scrape Hobbystation + Fukufuku → diff → save → notify."""
        async with self._lock:
            hs_products = await asyncio.to_thread(hs.scrape)
            ff_products = await asyncio.to_thread(ff.scrape)
            all_products = hs_products + ff_products

            stored = storage.load_local(LOCAL_STATE_PATH)
            is_first_run = len(stored) == 0 and not force_notify
            events = storage.compute_changes(all_products, stored, is_first_run)
            new_state = storage.update_state(all_products, stored)
            storage.save_local(LOCAL_STATE_PATH, new_state)

            if events:
                await discord_notifier.send_to_channel(channel, events)

            return len(events)

    async def _do_mercari_scrape_cycle(
        self,
        channel: discord.abc.Messageable,
        *,
        force_notify: bool = False,
    ) -> int:
        """Scrape Mercari TW → diff → save → notify. Uses site-scoped state functions."""
        async with self._lock:
            mc_products = await asyncio.to_thread(mc.scrape)

            stored = storage.load_local(LOCAL_STATE_PATH)
            mercari_stored = {k: v for k, v in stored.items() if k.startswith("mercari:")}
            is_first_run = len(mercari_stored) == 0 and not force_notify

            events = storage.compute_changes_for_site(mc_products, stored, "mercari", is_first_run)
            new_state = storage.update_state_for_site(mc_products, stored, "mercari")
            storage.save_local(LOCAL_STATE_PATH, new_state)

            if events:
                await discord_notifier.send_to_channel(channel, events)

            return len(events)


bot = WsBot()


@bot.tree.command(name="stock", description="目前有哪些商品有存貨？")
async def stock_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.defer()

    stored = storage.load_local(LOCAL_STATE_PATH)
    in_stock = {k: v for k, v in stored.items() if v.get("in_stock")}

    embed = discord.Embed(
        title="📦 可購買的商品",
        color=0x00C851,
        timestamp=datetime.now(JST),
    )

    for site, display_name in SITE_NAMES.items():
        items = [v for v in in_stock.values() if v.get("site") == site]
        if not items:
            continue
        currency = "NT$" if site == "mercari" else "¥"
        lines = [
            f"[{v['name']}]({v['product_url']}) **{currency}{v['price_raw']}**"
            for v in items
        ]
        # Discord embed field value limit is 1024 chars; split into chunks if needed
        chunk, chunks = [], []
        for line in lines:
            if sum(len(l) + 1 for l in chunk) + len(line) > 1020:
                chunks.append("\n".join(chunk))
                chunk = []
            chunk.append(line)
        if chunk:
            chunks.append("\n".join(chunk))
        for i, value in enumerate(chunks):
            field_name = display_name if i == 0 else f"{display_name} (續)"
            embed.add_field(name=field_name, value=value, inline=False)

    if not in_stock:
        embed.description = "目前沒有可以購買的商品。"

    embed.set_footer(text=f"最終更新: {datetime.now(JST).strftime('%Y-%m-%d %H:%M')} JST")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="stockimg", description="目前有存貨的商品（含圖片）")
async def stockimg_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.defer()

    stored = storage.load_local(LOCAL_STATE_PATH)
    in_stock = [v for v in stored.values() if v.get("in_stock")]

    if not in_stock:
        embed = discord.Embed(
            title="📦 可購買的商品",
            description="目前沒有可以購買的商品。",
            color=0x00C851,
            timestamp=datetime.now(JST),
        )
        await interaction.followup.send(embed=embed)
        return

    await interaction.followup.send(
        f"📦 目前共有 **{len(in_stock)}** 件可購買的商品："
    )
    for product in in_stock:
        embed, files = await asyncio.to_thread(
            discord_notifier.build_product_embed, product
        )
        await interaction.followup.send(embed=embed, files=files)
        await asyncio.sleep(0.5)


@bot.tree.command(name="update", description="今天的產品情報已經更新了！")
async def update_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    channel = interaction.channel
    n = await bot._do_scrape_cycle(channel, force_notify=True)
    n += await bot._do_mercari_scrape_cycle(channel, force_notify=True)
    await interaction.followup.send(f"✅ 更新完成。總共{n}件產品情報有變化。")


if __name__ == "__main__":
    bot.run(BOT_TOKEN)

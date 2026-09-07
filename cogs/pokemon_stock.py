from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from services.retailer_stock import fetch_product_status

log = logging.getLogger("daddys-belt.pokemon-stock")


class PokemonStockCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.stock_loop.start()

    def cog_unload(self) -> None:
        self.stock_loop.cancel()

    async def _send_alert(self, channel, config: dict, embed: discord.Embed) -> None:
        role_id = config.get("ping_role_id")
        role = channel.guild.get_role(int(role_id)) if role_id else None
        content = role.mention if role else None
        allowed = discord.AllowedMentions(roles=[role] if role else False)
        await channel.send(content=content, embed=embed, allowed_mentions=allowed)

    @tasks.loop(minutes=10)
    async def stock_loop(self) -> None:
        configs = await self.bot.database.get_enabled_stock_configs()
        for config in configs:
            guild_id = int(config["guild_id"])
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            stock_config = config.get("pokemon_stock", {})
            channel_id = stock_config.get("channel_id")
            if not channel_id:
                continue
            channel = await self.bot.get_text_channel(guild, int(channel_id))
            if channel is None:
                continue

            alerts = stock_config.get("alerts", {})
            in_stock_alerts = alerts.get("in_stock", True)
            out_alerts = alerts.get("out_of_stock", False)
            price_alerts = alerts.get("price_change", False)

            for product in stock_config.get("products", [])[:30]:
                url = product.get("url")
                if not url:
                    continue
                try:
                    status = await fetch_product_status(url)
                except Exception as exc:
                    log.warning("Stock check failed for %s: %s", url, exc)
                    await self.bot.database.update_stock_product_status(
                        guild_id, str(product["_id"]), error=str(exc)
                    )
                    await asyncio.sleep(2)
                    continue

                previous_available = product.get("available")
                previous_price = product.get("price")
                await self.bot.database.update_stock_product_status(
                    guild_id,
                    str(product["_id"]),
                    available=status.available,
                    store=status.store,
                    name=status.name,
                    price=status.price,
                    image_url=status.image_url,
                    detail=status.detail,
                    checked_at=datetime.now(timezone.utc),
                    error=None,
                )

                embed = None
                if previous_available is False and status.available is True and in_stock_alerts:
                    embed = discord.Embed(
                        title="🟢 Pokémon Restock",
                        description=f"[{status.name}]({status.url})",
                    )
                    embed.add_field(name="Store", value=status.store)
                    embed.add_field(name="Status", value="✅ Now in stock")
                elif previous_available is True and status.available is False and out_alerts:
                    embed = discord.Embed(
                        title="🔴 Pokémon Sold Out",
                        description=f"[{status.name}]({status.url})",
                    )
                    embed.add_field(name="Store", value=status.store)
                    embed.add_field(name="Status", value="❌ Now unavailable")
                elif (
                    price_alerts
                    and previous_price
                    and status.price
                    and previous_price != status.price
                ):
                    embed = discord.Embed(
                        title="💲 Pokémon Price Change",
                        description=f"[{status.name}]({status.url})",
                    )
                    embed.add_field(name="Store", value=status.store)
                    embed.add_field(name="Old price", value=str(previous_price))
                    embed.add_field(name="New price", value=str(status.price))

                if embed:
                    if status.price and embed.title != "💲 Pokémon Price Change":
                        embed.add_field(name="Price", value=status.price)
                    if status.sku:
                        embed.add_field(name="Product ID", value=status.sku)
                    if status.image_url:
                        embed.set_thumbnail(url=status.image_url)
                    embed.set_footer(text=f"Confirm availability on {status.store} before purchasing.")
                    try:
                        await self._send_alert(channel, stock_config, embed)
                    except (discord.Forbidden, discord.HTTPException):
                        log.exception("Could not post stock alert in guild %s", guild_id)
                await asyncio.sleep(2)

    @stock_loop.before_loop
    async def before_stock_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot) -> None:
    await bot.add_cog(PokemonStockCog(bot))

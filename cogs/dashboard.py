from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from core.bot import DaddysBeltBot
from services.permissions import (
    deny_access,
    evaluate_access,
)
from views.dashboard import (
    DashboardView,
    dashboard_embed,
)


class DashboardCog(commands.Cog):
    def __init__(
        self,
        bot: DaddysBeltBot,
    ) -> None:
        self.bot = bot

    @app_commands.command(
        name="dashboard",
        description=(
            "Open the Daddy's Belt server dashboard."
        ),
    )
    @app_commands.guild_only()
    async def dashboard(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "The dashboard must be opened inside a server.",
                ephemeral=True,
            )
            return

        decision = await evaluate_access(interaction, self.bot.database, minimum="moderator")
        if not decision.allowed:
            await deny_access(interaction, decision.reason)
            return

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        await self.bot.database.ensure_guild_profile(
            interaction.guild
        )
        await self.bot.database.increment_stat(
            interaction.guild.id,
            "commands_used",
        )

        await interaction.followup.send(
            embed=dashboard_embed(
                interaction.guild,
                database_connected=(
                    self.bot.database.connected
                ),
            ),
            view=DashboardView(
                bot=self.bot,
                guild_id=interaction.guild.id,
                requester_id=interaction.user.id,
                database_connected=(
                    self.bot.database.connected
                ),
            ),
            ephemeral=True,
        )


async def setup(
    bot: DaddysBeltBot,
) -> None:
    await bot.add_cog(
        DashboardCog(bot)
    )

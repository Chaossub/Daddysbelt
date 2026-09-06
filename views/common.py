from __future__ import annotations

import discord

from services.permissions import deny_access, evaluate_access


class AuthorizedView(discord.ui.View):
    def __init__(
        self,
        *,
        guild_id: int,
        requester_id: int,
        timeout: float | None = 21_600,
        minimum_access: str = "manager",
    ) -> None:
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.requester_id = requester_id
        self.minimum_access = minimum_access

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or interaction.guild_id != self.guild_id:
            await deny_access(interaction)
            return False

        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "This control panel belongs to someone else. Open your own with `/dashboard`.",
                ephemeral=True,
            )
            return False

        bot = getattr(self, "bot", None)
        database = getattr(bot, "database", None)
        if database is None:
            await deny_access(interaction)
            return False

        decision = await evaluate_access(
            interaction,
            database,
            minimum=self.minimum_access,  # type: ignore[arg-type]
        )
        if not decision.allowed:
            await deny_access(interaction, decision.reason)
            return False
        return True

from __future__ import annotations

import discord


class MommyDashboardView(discord.ui.View):
    """Button-first Mommy.exe dashboard. Persistent navigation IDs are intentional."""

    def __init__(self, bot, *, page: str = "home") -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.page = page

    async def _edit(self, interaction: discord.Interaction, *, title: str, body: str, page: str) -> None:
        embed = discord.Embed(title=title, description=body)
        await interaction.response.edit_message(embed=embed, view=MommyDashboardView(self.bot, page=page))

    @discord.ui.button(label="Status", emoji="📊", style=discord.ButtonStyle.secondary, custom_id="mommy:dash:status", row=0)
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        db = "connected" if self.bot.database.connected else "disconnected"
        ai = "ready" if self.bot.ai_available else "missing OPENAI_API_KEY"
        body = (
            f"**Discord:** {'ready' if self.bot.is_ready() else 'starting'}\n"
            f"**MongoDB:** {db}\n"
            f"**AI:** {ai}\n"
            f"**Guilds:** {len(self.bot.guilds)}\n\n"
            "Daddy's Belt owns the text/admin side. Mommy.exe owns the migrated VC side in this build."
        )
        await self._edit(interaction, title="Mommy.exe • Status", body=body, page="status")

    @discord.ui.button(label="AI", emoji="🧠", style=discord.ButtonStyle.primary, custom_id="mommy:dash:ai", row=0)
    async def ai(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._edit(
            interaction,
            title="Mommy.exe • AI",
            body=(
                "Mention **Mommy.exe** in text and she can answer in-character.\n\n"
                "Long-term memory is intentionally limited to conversations involving her. "
                "The deeper memory controls will be added with the VC migration."
            ),
            page="ai",
        )

    @discord.ui.button(label="Voice", emoji="🎙️", style=discord.ButtonStyle.secondary, custom_id="mommy:dash:voice", row=1)
    async def voice(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._edit(
            interaction,
            title="Mommy.exe • Voice",
            body="Mommy.exe now owns VC recording, live captions, voice AI, 1–2 person conversation behavior, 3-minute clips, and followed-member auto-join. Use the record controls while the button controls are being wired in.",
            page="voice",
        )

    @discord.ui.button(label="VC Automod", emoji="🛡️", style=discord.ButtonStyle.secondary, custom_id="mommy:dash:automod", row=1)
    async def automod(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._edit(
            interaction,
            title="Mommy.exe • VC Automod",
            body="Planned: 10-minute detection window, 7-day infraction history, exception roles, and temporary removal of **House Trained**.",
            page="automod",
        )

    @discord.ui.button(label="Verification", emoji="✅", style=discord.ButtonStyle.secondary, custom_id="mommy:dash:verification", row=1)
    async def verification(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._edit(
            interaction,
            title="Mommy.exe • Verification",
            body="Planned: react to the configured rules post → automatically receive **House Trained**.",
            page="verification",
        )

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.success, custom_id="mommy:dash:home", row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed = build_home_embed()
        await interaction.response.edit_message(embed=embed, view=MommyDashboardView(self.bot, page="home"))


def build_home_embed() -> discord.Embed:
    return discord.Embed(
        title="Mommy.exe Control Panel",
        description=(
            "Behave. Mommy has buttons now.\n\n"
            "Daddy handles text. Mommy handles voice. VC recording, captions, 1–2 person AI, 3-minute clips, and followed-member auto-join are live in this migration build. Automod/House Trained/verification are next."
        ),
    )

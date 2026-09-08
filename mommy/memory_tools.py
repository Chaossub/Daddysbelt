from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


class ClearMemoryConfirmView(discord.ui.View):
    def __init__(self, bot, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=120)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("That memory panel belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Yes, clear my memory", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        conversations, facts = await self.bot.clear_user_memory(self.guild_id, self.user_id)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"Cleared **{conversations}** remembered conversation(s) and **{facts}** saved fact(s).",
            embed=None,
            view=self,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Memory clear cancelled.", embed=None, view=self)


class MemoryPanelView(discord.ui.View):
    def __init__(self, bot, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("That memory panel belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="View Memory", emoji="🧠", style=discord.ButtonStyle.primary)
    async def view_memory(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        enabled = await self.bot.memory_enabled(self.guild_id, self.user_id)
        facts = await self.bot._load_facts(self.guild_id, self.user_id, limit=20) if enabled else []  # noqa: SLF001
        history = await self.bot._load_memory(self.guild_id, self.user_id, limit=6) if enabled else []  # noqa: SLF001
        embed = discord.Embed(title="Mommy.exe • Your Memory", color=discord.Color.purple())
        embed.description = "Memory is **ON**." if enabled else "Memory is **OFF**. Mommy will still reply when addressed, but won't load or save personal memory."
        if facts:
            embed.add_field(name="Things Mommy remembers", value="\n".join(f"• {x}" for x in facts)[:1000], inline=False)
        if history:
            recent = []
            for item in history[-4:]:
                human = str(item.get("human_text") or "")[:150]
                recent.append(f"• You: {human}")
            embed.add_field(name="Recent addressed conversations", value="\n".join(recent)[:1000], inline=False)
        if not facts and not history and enabled:
            embed.add_field(name="Nothing saved yet", value="Mommy only remembers conversations where you directly address her, plus useful preferences/facts from those conversations.", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Memory On", emoji="✅", style=discord.ButtonStyle.success)
    async def enable(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot.set_memory_enabled(self.guild_id, self.user_id, True)
        await interaction.response.send_message("🧠 Mommy memory is now **ON** for you in this server.", ephemeral=True)

    @discord.ui.button(label="Memory Off", emoji="⛔", style=discord.ButtonStyle.secondary)
    async def disable(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot.set_memory_enabled(self.guild_id, self.user_id, False)
        await interaction.response.send_message("Mommy memory is now **OFF** for you in this server. Existing memories stay saved until you clear them.", ephemeral=True)

    @discord.ui.button(label="Clear My Memory", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def clear(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        embed = discord.Embed(
            title="Clear Mommy memory?",
            description="This deletes your saved Mommy conversation history and remembered facts for **this server**. It does not delete moderation records or VC transcripts.",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, view=ClearMemoryConfirmView(self.bot, self.guild_id, self.user_id), ephemeral=True)


class MommyMemoryCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    @app_commands.command(name="mommy-memory", description="View or control what Mommy.exe remembers about your conversations with her.")
    @app_commands.guild_only()
    async def mommy_memory(self, interaction: discord.Interaction) -> None:
        enabled = await self.bot.memory_enabled(interaction.guild.id, interaction.user.id)
        embed = discord.Embed(
            title="Mommy.exe • Memory",
            description=(
                "Mommy only saves personal conversation memory when **you directly address her** by @mention or wake name. "
                "Normal server chatter and full VC transcripts are not added to this memory.\n\n"
                f"Your memory is currently **{'ON' if enabled else 'OFF'}**."
            ),
            color=discord.Color.purple(),
        )
        await interaction.response.send_message(embed=embed, view=MemoryPanelView(self.bot, interaction.guild.id, interaction.user.id), ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(MommyMemoryCog(bot))

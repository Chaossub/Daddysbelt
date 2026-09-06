from __future__ import annotations

import logging
from typing import Literal

import discord

from services.permissions import evaluate_access

log = logging.getLogger("mommy-exe.dashboard")


def _home_embed() -> discord.Embed:
    return discord.Embed(
        title="Mommy.exe Control Panel",
        description=(
            "Behave. Mommy has buttons now.\n\n"
            "**Voice** controls recording, captions, auto-join and the live VC session.\n"
            "**Clips** saves the previous three minutes.\n"
            "**Follow Members** controls who Mommy automatically follows into VC.\n"
            "**AI** explains her conversation/memory behavior.\n\n"
            "Daddy's Belt handles the text/admin side. Mommy.exe handles voice."
        ),
    )


def build_home_embed() -> discord.Embed:
    return _home_embed()


async def _authorized(interaction: discord.Interaction, bot, *, minimum: str = "moderator") -> bool:
    decision = await evaluate_access(interaction, bot.database, minimum=minimum, enforce_channel=False)
    if decision.allowed:
        return True
    text = decision.reason or "You do not have permission to use Mommy's controls."
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)
    return False


def _voice_cog(bot):
    return bot.get_cog("VoiceRecordingCog")


class MommyDashboardView(discord.ui.View):
    """Home dashboard. All deeper screens include Back/Home navigation."""

    def __init__(self, bot) -> None:
        super().__init__(timeout=600)
        self.bot = bot

    @discord.ui.button(label="Voice", emoji="🎙️", style=discord.ButtonStyle.primary, row=0)
    async def voice(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot):
            return
        await show_voice_page(interaction, self.bot)

    @discord.ui.button(label="Clips", emoji="✂️", style=discord.ButtonStyle.primary, row=0)
    async def clips(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot):
            return
        await show_clips_page(interaction, self.bot)

    @discord.ui.button(label="Follow Members", emoji="👣", style=discord.ButtonStyle.secondary, row=0)
    async def follow(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot):
            return
        await show_follow_page(interaction, self.bot)

    @discord.ui.button(label="AI", emoji="🧠", style=discord.ButtonStyle.secondary, row=1)
    async def ai(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="moderator"):
            return
        embed = discord.Embed(
            title="Mommy.exe • AI",
            description=(
                "**1 human:** she can participate naturally after the speaker finishes.\n"
                "**2 humans:** she stays out of ordinary chatter unless addressed, already in the conversation, or someone talks shit about her.\n"
                "**Long-term memory:** only exchanges actually involving Mommy are stored. Random VC conversation is not kept as AI memory.\n"
                "**Style:** varied wording, dry/sassy dommy-mommy energy, occasional adult jokes, and no canned pet-name spam."
            ),
        )
        await interaction.response.edit_message(embed=embed, view=SimpleNavView(self.bot, back="home"))

    @discord.ui.button(label="Status", emoji="📊", style=discord.ButtonStyle.secondary, row=1)
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot):
            return
        await show_status_page(interaction, self.bot)

    @discord.ui.button(label="Automod", emoji="🛡️", style=discord.ButtonStyle.secondary, row=1)
    async def automod(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot):
            return
        embed = discord.Embed(
            title="Mommy.exe • VC Automod",
            description="Coming in the **final roles/moderation deploy**: House Trained, exception roles, 10-minute detection windows, 7-day infraction history, quote/false-positive handling, punishment sounds, and automatic restoration.",
        )
        await interaction.response.edit_message(embed=embed, view=SimpleNavView(self.bot, back="home"))

    @discord.ui.button(label="Verification", emoji="✅", style=discord.ButtonStyle.secondary, row=2)
    async def verification(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot):
            return
        embed = discord.Embed(
            title="Mommy.exe • Verification",
            description="Coming in the **final roles/moderation deploy**: react to the configured rules post to receive **House Trained** and unlock the server.",
        )
        await interaction.response.edit_message(embed=embed, view=SimpleNavView(self.bot, back="home"))


class SimpleNavView(discord.ui.View):
    def __init__(self, bot, *, back: Literal["home", "voice", "clips", "follow"] = "home") -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.back_page = back

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, self.back_page)

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.success)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "home")


class VoiceView(discord.ui.View):
    def __init__(self, bot) -> None:
        super().__init__(timeout=600)
        self.bot = bot

    @discord.ui.button(label="Start / Join", emoji="▶️", style=discord.ButtonStyle.success, row=0)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot):
            return
        await interaction.response.edit_message(
            embed=discord.Embed(title="Choose a VC", description="Pick the voice channel Mommy should join and start recording/listening in."),
            view=VoiceChannelActionView(self.bot, action="start", back="voice"),
        )

    @discord.ui.button(label="Stop / Leave", emoji="⏹️", style=discord.ButtonStyle.danger, row=0)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot):
            return
        cog = _voice_cog(self.bot)
        if cog is None:
            await interaction.response.send_message("Voice controls are not loaded.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        text = await cog.dashboard_stop(interaction.guild, interaction.user) if interaction.guild else "Server only."
        await interaction.followup.send(text, ephemeral=True)

    @discord.ui.button(label="Clip Last 3 Minutes", emoji="✂️", style=discord.ButtonStyle.primary, row=0)
    async def clip(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot):
            return
        cog = _voice_cog(self.bot)
        if cog is None:
            await interaction.response.send_message("Voice controls are not loaded.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        text = await cog.dashboard_clip(interaction.guild, interaction.user) if interaction.guild else "Server only."
        await interaction.followup.send(text, ephemeral=True)

    @discord.ui.button(label="Recording Setup", emoji="⚙️", style=discord.ButtonStyle.secondary, row=1)
    async def setup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"):
            return
        await interaction.response.edit_message(
            embed=discord.Embed(title="Recording Setup", description="First choose the private/control text channel. Then you'll choose the archive/output channel."),
            view=SetupControlChannelView(self.bot),
        )

    @discord.ui.button(label="Auto Join Room", emoji="🔁", style=discord.ButtonStyle.secondary, row=1)
    async def auto(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"):
            return
        await interaction.response.edit_message(
            embed=discord.Embed(title="Auto Join", description="Choose the fixed VC Mommy should automatically join when a human enters."),
            view=VoiceChannelActionView(self.bot, action="auto", back="voice"),
        )

    @discord.ui.button(label="Captions", emoji="💬", style=discord.ButtonStyle.secondary, row=1)
    async def captions(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"):
            return
        await interaction.response.edit_message(
            embed=discord.Embed(title="Caption Routing", description="Choose the source voice channel first, then the text channel where its live captions should go."),
            view=CaptionVoiceSelectView(self.bot),
        )

    @discord.ui.button(label="Follow Members", emoji="👣", style=discord.ButtonStyle.secondary, row=2)
    async def follow(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await show_follow_page(interaction, self.bot)

    @discord.ui.button(label="Disable Auto Join", emoji="🚫", style=discord.ButtonStyle.secondary, row=2)
    async def autooff(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"):
            return
        if interaction.guild:
            await self.bot.database.set_voice_recording_auto_channel(interaction.guild.id, None)
        await interaction.response.send_message("Fixed-room auto join is disabled. Followed-member behavior is unchanged.", ephemeral=True)

    @discord.ui.button(label="Status", emoji="📊", style=discord.ButtonStyle.secondary, row=2)
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await show_status_page(interaction, self.bot)

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.success, row=3)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "home")


class ClipsView(discord.ui.View):
    def __init__(self, bot) -> None:
        super().__init__(timeout=600)
        self.bot = bot

    @discord.ui.button(label="Clip Last 3 Minutes", emoji="✂️", style=discord.ButtonStyle.primary)
    async def clip(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot):
            return
        cog = _voice_cog(self.bot)
        await interaction.response.defer(ephemeral=True, thinking=True)
        text = await cog.dashboard_clip(interaction.guild, interaction.user) if cog and interaction.guild else "No active voice session."
        await interaction.followup.send(text, ephemeral=True)

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "home")

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.success)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "home")


class FollowView(discord.ui.View):
    def __init__(self, bot) -> None:
        super().__init__(timeout=600)
        self.bot = bot

    @discord.ui.button(label="Add Member", emoji="➕", style=discord.ButtonStyle.success, row=0)
    async def add(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"):
            return
        await interaction.response.edit_message(
            embed=discord.Embed(title="Follow Members • Add", description="Choose a member Mommy should automatically follow into VC."),
            view=FollowMemberSelectView(self.bot, action="add"),
        )

    @discord.ui.button(label="Remove Member", emoji="➖", style=discord.ButtonStyle.danger, row=0)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"):
            return
        await interaction.response.edit_message(
            embed=discord.Embed(title="Follow Members • Remove", description="Choose a member to remove from Mommy's followed list."),
            view=FollowMemberSelectView(self.bot, action="remove"),
        )

    @discord.ui.button(label="Refresh List", emoji="🔄", style=discord.ButtonStyle.secondary, row=0)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await show_follow_page(interaction, self.bot)

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "voice")

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.success, row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "home")


class VoiceChannelActionSelect(discord.ui.ChannelSelect):
    def __init__(self, parent_view: "VoiceChannelActionView", *, action: Literal["start", "auto"]) -> None:
        super().__init__(placeholder="Choose a voice channel…", channel_types=[discord.ChannelType.voice], min_values=1, max_values=1)
        self.parent_view = parent_view
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0]
        channel = interaction.guild.get_channel(int(selected.id)) if interaction.guild else None
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message("Pick a normal voice channel.", ephemeral=True)
            return
        if self.action == "auto":
            if not await _authorized(interaction, self.parent_view.bot, minimum="admin"):
                return
            config = await self.parent_view.bot.database.get_guild_profile(interaction.guild.id)
            if not (config or {}).get("voice_recording", {}).get("output_channel_id"):
                await interaction.response.send_message("Set **Recording Setup** first so Mommy knows where clips/recordings go.", ephemeral=True)
                return
            await self.parent_view.bot.database.set_voice_recording_auto_channel(interaction.guild.id, channel.id)
            await interaction.response.send_message(f"Auto join is now set to {channel.mention}.", ephemeral=True)
            return

        if not await _authorized(interaction, self.parent_view.bot):
            return
        cog = _voice_cog(self.parent_view.bot)
        await interaction.response.defer(ephemeral=True, thinking=True)
        text = await cog.dashboard_start(interaction.guild, interaction.user, channel) if cog and interaction.guild else "Voice controls unavailable."
        await interaction.followup.send(text, ephemeral=True)


class VoiceChannelActionView(discord.ui.View):
    def __init__(self, bot, *, action: Literal["start", "auto"], back: str) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.back_page = back
        self.add_item(VoiceChannelActionSelect(self, action=action))

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, self.back_page)

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.success, row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "home")


class SetupChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent_view, *, stage: Literal["control", "output"]) -> None:
        label = "Choose control channel…" if stage == "control" else "Choose archive/output channel…"
        super().__init__(placeholder=label, channel_types=[discord.ChannelType.text], min_values=1, max_values=1)
        self.parent_view = parent_view
        self.stage = stage

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0]
        channel = interaction.guild.get_channel(int(selected.id)) if interaction.guild else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Choose a text channel.", ephemeral=True)
            return
        if not await _authorized(interaction, self.parent_view.bot, minimum="admin"):
            return
        if self.stage == "control":
            await interaction.response.edit_message(
                embed=discord.Embed(title="Recording Setup", description=f"Control channel: {channel.mention}\nNow choose the archive/output channel."),
                view=SetupOutputChannelView(self.parent_view.bot, control_channel_id=channel.id),
            )
            return
        await interaction.response.send_message("Unexpected setup state.", ephemeral=True)


class SetupControlChannelView(discord.ui.View):
    def __init__(self, bot) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.add_item(SetupChannelSelect(self, stage="control"))

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "voice")

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.success, row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "home")


class SetupOutputSelect(discord.ui.ChannelSelect):
    def __init__(self, parent_view) -> None:
        super().__init__(placeholder="Choose archive/output channel…", channel_types=[discord.ChannelType.text], min_values=1, max_values=1)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0]
        channel = interaction.guild.get_channel(int(selected.id)) if interaction.guild else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Choose a text channel.", ephemeral=True)
            return
        if not await _authorized(interaction, self.parent_view.bot, minimum="admin"):
            return
        await self.parent_view.bot.database.set_voice_recording_config(
            interaction.guild.id,
            control_channel_id=self.parent_view.control_channel_id,
            output_channel_id=channel.id,
        )
        await interaction.response.edit_message(
            embed=discord.Embed(title="Recording Setup Saved", description=f"Control: <#{self.parent_view.control_channel_id}>\nArchive/output: {channel.mention}"),
            view=SimpleNavView(self.parent_view.bot, back="voice"),
        )


class SetupOutputChannelView(discord.ui.View):
    def __init__(self, bot, *, control_channel_id: int) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.control_channel_id = control_channel_id
        self.add_item(SetupOutputSelect(self))

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "voice")

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.success, row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "home")


class CaptionVoiceSelect(discord.ui.ChannelSelect):
    def __init__(self, parent_view) -> None:
        super().__init__(placeholder="Choose source voice channel…", channel_types=[discord.ChannelType.voice], min_values=1, max_values=1)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0]
        channel = interaction.guild.get_channel(int(selected.id)) if interaction.guild else None
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message("Choose a voice channel.", ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=discord.Embed(title="Caption Routing", description=f"Source: {channel.mention}\nNow choose the text channel for live captions."),
            view=CaptionTextSelectView(self.parent_view.bot, voice_channel_id=channel.id),
        )


class CaptionVoiceSelectView(discord.ui.View):
    def __init__(self, bot) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.add_item(CaptionVoiceSelect(self))

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "voice")

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.success, row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "home")


class CaptionTextSelect(discord.ui.ChannelSelect):
    def __init__(self, parent_view) -> None:
        super().__init__(placeholder="Choose caption text channel…", channel_types=[discord.ChannelType.text], min_values=1, max_values=1)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0]
        channel = interaction.guild.get_channel(int(selected.id)) if interaction.guild else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Choose a text channel.", ephemeral=True)
            return
        if not await _authorized(interaction, self.parent_view.bot, minimum="admin"):
            return
        await self.parent_view.bot.database.set_voice_recording_caption_route(
            interaction.guild.id, self.parent_view.voice_channel_id, channel.id
        )
        await interaction.response.edit_message(
            embed=discord.Embed(title="Caption Route Saved", description=f"<#{self.parent_view.voice_channel_id}> → {channel.mention}"),
            view=SimpleNavView(self.parent_view.bot, back="voice"),
        )


class CaptionTextSelectView(discord.ui.View):
    def __init__(self, bot, *, voice_channel_id: int) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.voice_channel_id = voice_channel_id
        self.add_item(CaptionTextSelect(self))

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "voice")

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.success, row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "home")


class FollowMemberSelect(discord.ui.UserSelect):
    def __init__(self, parent_view, *, action: Literal["add", "remove"]) -> None:
        super().__init__(placeholder="Choose a member…", min_values=1, max_values=1)
        self.parent_view = parent_view
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _authorized(interaction, self.parent_view.bot, minimum="admin"):
            return
        selected = self.values[0]
        user_id = int(selected.id)
        profile = await self.parent_view.bot.database.get_guild_profile(interaction.guild.id)
        config = (profile or {}).get("voice_recording", {})
        ids = [int(v) for v in (config.get("follow_user_ids") or [])]
        if self.action == "add":
            if user_id not in ids:
                ids.append(user_id)
            message = f"I'll automatically follow <@{user_id}> into VC and start listening."
        else:
            ids = [value for value in ids if value != user_id]
            message = f"I won't automatically follow <@{user_id}> anymore."
        await self.parent_view.bot.database.set_voice_recording_follow_users(interaction.guild.id, ids)
        await interaction.response.edit_message(
            embed=discord.Embed(title="Follow Members", description=message),
            view=FollowView(self.parent_view.bot),
        )


class FollowMemberSelectView(discord.ui.View):
    def __init__(self, bot, *, action: Literal["add", "remove"]) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.add_item(FollowMemberSelect(self, action=action))

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "follow")

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.success, row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "home")


async def show_voice_page(interaction: discord.Interaction, bot) -> None:
    profile = await bot.database.get_guild_profile(interaction.guild.id) if interaction.guild else None
    cfg = (profile or {}).get("voice_recording", {})
    auto = cfg.get("auto_voice_channel_id")
    output = cfg.get("output_channel_id")
    control = cfg.get("control_channel_id")
    caption_routes = cfg.get("caption_routes") or {}
    embed = discord.Embed(
        title="Mommy.exe • Voice",
        description=(
            f"**Control:** {f'<#{control}>' if control else 'not set'}\n"
            f"**Archive/output:** {f'<#{output}>' if output else 'not set'}\n"
            f"**Fixed auto-join VC:** {f'<#{auto}>' if auto else 'off'}\n"
            f"**Caption routes:** {len(caption_routes)}\n\n"
            "Use the buttons below. Slash `/record` commands remain only as fallback controls."
        ),
    )
    await interaction.response.edit_message(embed=embed, view=VoiceView(bot))


async def show_clips_page(interaction: discord.Interaction, bot) -> None:
    cog = _voice_cog(bot)
    active = bool(cog and interaction.guild and interaction.guild.id in cog.sessions)
    embed = discord.Embed(
        title="Mommy.exe • Clips",
        description=(
            "Mommy keeps the active session audio aligned so she can save the **previous 3 minutes**.\n\n"
            f"**Active buffer:** {'yes' if active else 'no'}"
        ),
    )
    await interaction.response.edit_message(embed=embed, view=ClipsView(bot))


async def show_follow_page(interaction: discord.Interaction, bot) -> None:
    profile = await bot.database.get_guild_profile(interaction.guild.id) if interaction.guild else None
    ids = [int(v) for v in ((profile or {}).get("voice_recording", {}).get("follow_user_ids") or [])]
    lines = []
    if interaction.guild:
        for user_id in ids:
            member = interaction.guild.get_member(user_id)
            lines.append(f"• {member.mention if member else f'<@{user_id}>'}")
    description = "\n".join(lines) if lines else "Nobody is on the followed-member list yet."
    embed = discord.Embed(title="Mommy.exe • Follow Members", description=description)
    await interaction.response.edit_message(embed=embed, view=FollowView(bot))


async def show_status_page(interaction: discord.Interaction, bot) -> None:
    cog = _voice_cog(bot)
    session = cog.sessions.get(interaction.guild.id) if cog and interaction.guild else None
    profile = await bot.database.get_guild_profile(interaction.guild.id) if interaction.guild else None
    cfg = (profile or {}).get("voice_recording", {})
    if session:
        active = f"Recording/listening in <#{session.voice_channel_id}> • {session.duration:.0f}s • {len(session.tracks)} speaker track(s)"
    else:
        active = "No active VC session"
    embed = discord.Embed(
        title="Mommy.exe • Status",
        description=(
            f"**Discord:** {'ready' if bot.is_ready() else 'starting'}\n"
            f"**MongoDB:** {'connected' if bot.database.connected else 'disconnected'}\n"
            f"**AI:** {'ready' if bot.ai_available else 'missing key'}\n"
            f"**Voice:** {active}\n"
            f"**Followed members:** {len(cfg.get('follow_user_ids') or [])}"
        ),
    )
    await interaction.response.edit_message(embed=embed, view=SimpleNavView(bot, back="home"))


async def navigate(interaction: discord.Interaction, bot, page: str) -> None:
    if page == "voice":
        await show_voice_page(interaction, bot)
    elif page == "clips":
        await show_clips_page(interaction, bot)
    elif page == "follow":
        await show_follow_page(interaction, bot)
    else:
        await interaction.response.edit_message(embed=_home_embed(), view=MommyDashboardView(bot))

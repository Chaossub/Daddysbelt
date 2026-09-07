from __future__ import annotations

import logging
import os
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


async def _authorized(interaction: discord.Interaction, bot, *, minimum: str = "admin") -> bool:
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
        if not await _authorized(interaction, self.bot, minimum="admin"):
            return
        await show_ai_page(interaction, self.bot)

    @discord.ui.button(label="Status", emoji="📊", style=discord.ButtonStyle.secondary, row=1)
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot):
            return
        await show_status_page(interaction, self.bot)

    @discord.ui.button(label="Automod", emoji="🛡️", style=discord.ButtonStyle.secondary, row=1)
    async def automod(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot):
            return
        await show_automod_page(interaction, self.bot)

    @discord.ui.button(label="Verification", emoji="✅", style=discord.ButtonStyle.secondary, row=2)
    async def verification(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot):
            return
        await show_verification_page(interaction, self.bot)


class SimpleNavView(discord.ui.View):
    def __init__(self, bot, *, back: Literal["home", "voice", "clips", "follow", "automod", "verification", "ai", "voice_settings"] = "home") -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.back_page = back

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, self.back_page)

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.success)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "home")


MOMMY_TTS_VOICES = [
    "alloy", "ash", "ballad", "coral", "echo", "fable", "onyx",
    "nova", "sage", "shimmer", "verse", "marin", "cedar",
]


class AIView(discord.ui.View):
    def __init__(self, bot) -> None:
        super().__init__(timeout=600)
        self.bot = bot

    @discord.ui.button(label="Voice Settings", emoji="🔊", style=discord.ButtonStyle.primary, row=0)
    async def voice_settings(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"):
            return
        await show_voice_settings_page(interaction, self.bot)

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "home")

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.success, row=1)
    async def home(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "home")


class MommyVoiceSelect(discord.ui.Select):
    def __init__(self, parent_view: "MommyVoiceSettingsView", *, current: str) -> None:
        options = [
            discord.SelectOption(label=name.title(), value=name, default=(name == current))
            for name in MOMMY_TTS_VOICES
        ]
        super().__init__(placeholder="Choose a Mommy.exe voice…", options=options, min_values=1, max_values=1)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _authorized(interaction, self.parent_view.bot, minimum="admin"):
            return
        self.parent_view.selected_voice = self.values[0]
        await interaction.response.edit_message(
            embed=self.parent_view.embed_for(self.parent_view.selected_voice),
            view=self.parent_view,
        )


class MommyVoiceSettingsView(discord.ui.View):
    def __init__(self, bot, *, current_voice: str) -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.current_voice = current_voice
        self.selected_voice = current_voice
        self.piper = bool(os.getenv("PIPER_TTS_URL", "").strip())
        if self.piper:
            # Piper is configured from Render. Keep this panel alive so the admin
            # can repeatedly test the active provider without reopening !mommy.
            for child in list(self.children):
                if isinstance(child, discord.ui.Button) and child.label == "Preview Selected":
                    child.label = "Test Piper Voice"
                elif isinstance(child, discord.ui.Button) and child.label in {"Set Selected", "Reset Default"}:
                    self.remove_item(child)
        else:
            self.add_item(MommyVoiceSelect(self, current=current_voice))

    def embed_for(self, selected: str | None = None) -> discord.Embed:
        selected = selected or self.selected_voice
        if self.piper:
            return discord.Embed(
                title="Mommy.exe • Voice Settings",
                description=(
                    "**Active provider:** `Piper`\n"
                    "**Voice:** `en_US-libritts_r-medium` • speaker `3922 (0)` on your Piper service\n\n"
                    "Press **Test Piper Voice** as many times as you want. "
                    "This panel stays usable, so you do not need to type `!mommy` between tests.\n\n"
                    "If Piper is unavailable, Mommy automatically falls back to OpenAI."
                ),
            )
        return discord.Embed(
            title="Mommy.exe • Voice Settings",
            description=(
                f"**Current saved voice:** `{self.current_voice}`\n"
                f"**Selected for preview:** `{selected}`\n\n"
                "**Preview Selected** plays a temporary sample in VC. It does not change Mommy's voice.\n"
                "**Set Selected** saves it immediately for future TTS and survives Render restarts.\n"
                "You can switch voices whenever you want."
            ),
        )

    @discord.ui.button(label="Preview Selected", emoji="▶️", style=discord.ButtonStyle.primary, row=1)
    async def preview(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"):
            return
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Server only.", ephemeral=True)
            return
        cog = _voice_cog(self.bot)
        if cog is None or not hasattr(cog, "preview_tts_voice"):
            await interaction.response.send_message("Voice preview isn't loaded.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        text = await cog.preview_tts_voice(interaction.guild, interaction.user, self.selected_voice)
        await interaction.followup.send(text, ephemeral=True)

    @discord.ui.button(label="Set Selected", emoji="💾", style=discord.ButtonStyle.success, row=1)
    async def save(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"):
            return
        if not interaction.guild:
            await interaction.response.send_message("Server only.", ephemeral=True)
            return
        await self.bot.database.set_voice_recording_tts_voice(interaction.guild.id, self.selected_voice)
        self.current_voice = self.selected_voice
        await interaction.response.edit_message(embed=self.embed_for(), view=self)

    @discord.ui.button(label="Reset Default", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def reset(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"):
            return
        if not interaction.guild:
            await interaction.response.send_message("Server only.", ephemeral=True)
            return
        await self.bot.database.set_voice_recording_tts_voice(interaction.guild.id, None)
        import os
        self.current_voice = os.getenv("MOMMY_TTS_VOICE", "shimmer").strip().lower()
        self.selected_voice = self.current_voice
        await interaction.response.edit_message(
            embed=self.embed_for(),
            view=MommyVoiceSettingsView(self.bot, current_voice=self.current_voice),
        )

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=4)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "ai")

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.success, row=4)
    async def home(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
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

        if self.action == "add":
            member = interaction.guild.get_member(user_id)
            voice_cog = self.parent_view.bot.get_cog("VoiceRecordingCog")
            if member is not None and voice_cog is not None and hasattr(voice_cog, "follow_member_now"):
                try:
                    message = await voice_cog.follow_member_now(interaction.guild, member)
                except Exception as exc:
                    log.exception("Immediate followed-member join failed for %s", user_id)
                    message = f"Saved <@{user_id}> to Follow Members, but the immediate VC join failed: {type(exc).__name__}."

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


async def show_ai_page(interaction: discord.Interaction, bot) -> None:
    profile = await bot.database.get_guild_profile(interaction.guild.id) if interaction.guild else None
    configured = ((profile or {}).get("voice_recording", {}) or {}).get("tts_voice")
    current_voice = str(configured or os.getenv("MOMMY_TTS_VOICE", "shimmer")).strip().lower()
    piper = bool(os.getenv("PIPER_TTS_URL", "").strip())
    provider_text = "Piper / en_US-libritts_r-medium / speaker 3922 (0)" if piper else f"OpenAI / {current_voice}"
    embed = discord.Embed(
        title="Mommy.exe • AI",
        description=(
            "**1 human:** she can participate naturally after the speaker finishes.\n"
            "**2 humans:** she stays out of ordinary chatter unless addressed, already in the conversation, or someone talks shit about her.\n"
            "**Long-term memory:** only exchanges actually involving Mommy are stored. Random VC conversation is not kept as AI memory.\n"
            "**Style:** varied wording, dry/sassy dommy-mommy energy, occasional adult jokes, and no canned pet-name spam.\n\n"
            f"**Current TTS:** `{provider_text}`"
        ),
    )
    await interaction.response.edit_message(embed=embed, view=AIView(bot))


async def show_voice_settings_page(interaction: discord.Interaction, bot) -> None:
    profile = await bot.database.get_guild_profile(interaction.guild.id) if interaction.guild else None
    configured = ((profile or {}).get("voice_recording", {}) or {}).get("tts_voice")
    current_voice = str(configured or os.getenv("MOMMY_TTS_VOICE", "shimmer")).strip().lower()
    piper = bool(os.getenv("PIPER_TTS_URL", "").strip())
    if not piper and current_voice not in MOMMY_TTS_VOICES:
        current_voice = "shimmer"
    view = MommyVoiceSettingsView(bot, current_voice=current_voice)
    await interaction.response.edit_message(embed=view.embed_for(), view=view)


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
    elif page == "automod":
        await show_automod_page(interaction, bot)
    elif page == "verification":
        await show_verification_page(interaction, bot)
    elif page == "ai":
        await show_ai_page(interaction, bot)
    elif page == "voice_settings":
        await show_voice_settings_page(interaction, bot)
    else:
        await interaction.response.edit_message(embed=_home_embed(), view=MommyDashboardView(bot))

# ---------------- Final moderation / verification controls ----------------

def _vc_automod_cog(bot):
    return bot.get_cog("VCAutomodCog")


class AutomodView(discord.ui.View):
    def __init__(self, bot) -> None:
        super().__init__(timeout=600)
        self.bot = bot

    @discord.ui.button(label="Enable / Disable", emoji="🛡️", style=discord.ButtonStyle.primary, row=0)
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"): return
        profile = await self.bot.database.get_guild_profile(interaction.guild.id) or {}
        current = bool((profile.get("vc_automod") or {}).get("enabled", True))
        await self.bot.database.set_config_path(interaction.guild.id, "vc_automod.enabled", not current)
        await show_automod_page(interaction, self.bot)

    @discord.ui.button(label="Triggers", emoji="🚨", style=discord.ButtonStyle.primary, row=0)
    async def triggers(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"): return
        await show_vc_triggers_page(interaction, self.bot)

    @discord.ui.button(label="Exception Roles", emoji="🛡️", style=discord.ButtonStyle.secondary, row=0)
    async def exceptions(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"): return
        await show_exception_roles_page(interaction, self.bot)

    @discord.ui.button(label="Member History", emoji="📋", style=discord.ButtonStyle.secondary, row=1)
    async def history(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot): return
        await interaction.response.edit_message(
            embed=discord.Embed(title="VC Automod • Member History", description="Choose a member to review recent detections (kept for 7 days)."),
            view=AutomodMemberActionView(self.bot, action="history"),
        )

    @discord.ui.button(label="Forgive Member", emoji="🧽", style=discord.ButtonStyle.secondary, row=1)
    async def forgive(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"): return
        await interaction.response.edit_message(
            embed=discord.Embed(title="VC Automod • Forgive", description="Choose a member. Their stored VC detections will be cleared."),
            view=AutomodMemberActionView(self.bot, action="forgive"),
        )

    @discord.ui.button(label="Restore Access", emoji="🔓", style=discord.ButtonStyle.success, row=1)
    async def restore(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"): return
        await interaction.response.edit_message(
            embed=discord.Embed(title="VC Automod • Restore", description="Choose a member to manually restore House Trained."),
            view=AutomodMemberActionView(self.bot, action="restore"),
        )

    @discord.ui.button(label="Punishment Sound", emoji="🔊", style=discord.ButtonStyle.secondary, row=2)
    async def sound(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"): return
        await show_punishment_sound_page(interaction, self.bot)

    @discord.ui.button(label="Punishment Ladder", emoji="📶", style=discord.ButtonStyle.primary, row=2)
    async def ladder(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"): return
        await show_punishment_ladder_page(interaction, self.bot)

    @discord.ui.button(label="House Trained Role", emoji="🏠", style=discord.ButtonStyle.secondary, row=2)
    async def house_role(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"): return
        await interaction.response.edit_message(
            embed=discord.Embed(title="VC Automod • House Trained Role", description="Choose the role Mommy removes during punishment and restores afterward."),
            view=HouseRoleView(self.bot, back_page="automod"),
        )

    @discord.ui.button(label="Grant / Remove Access", emoji="🎟️", style=discord.ButtonStyle.secondary, row=3)
    async def access(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"): return
        await interaction.response.edit_message(
            embed=discord.Embed(title="VC Automod • House Trained Access", description="Manually give or remove the configured House Trained role."),
            view=HouseAccessView(self.bot),
        )

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=3)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None: await navigate(interaction, self.bot, "home")
    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.success, row=3)
    async def home(self, interaction: discord.Interaction, _: discord.ui.Button) -> None: await navigate(interaction, self.bot, "home")


class VCTriggerModal(discord.ui.Modal, title="Add VC Automod Trigger"):
    label = discord.ui.TextInput(label="Name", placeholder="Example: banned phrase", max_length=60)
    phrase = discord.ui.TextInput(label="Word or phrase", placeholder="What should Mommy listen for?", max_length=180)
    threshold = discord.ui.TextInput(label="Hits before punishment", default="3", max_length=2)
    window = discord.ui.TextInput(label="Window in minutes", default="10", max_length=4)
    points = discord.ui.TextInput(label="Infraction points", default="1", max_length=3)

    def __init__(self, bot) -> None:
        super().__init__(); self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        import uuid
        try:
            threshold=max(1,int(str(self.threshold))); window=max(1,int(str(self.window))); points=max(1,int(str(self.points)))
        except ValueError:
            await interaction.response.send_message("Threshold/window/points must be numbers.", ephemeral=True); return
        profile=await self.bot.database.get_guild_profile(interaction.guild.id) or {}
        history_days=max(1,int((profile.get("vc_automod") or {}).get("retention_days",7) or 7))
        trigger={"id":uuid.uuid4().hex[:12],"label":str(self.label).strip(),"phrase":str(self.phrase).strip(),"enabled":True,"threshold":threshold,"window_minutes":window,"points":points,"history_days":history_days,"quote_lenient":True}
        await self.bot.database.push_config_path(interaction.guild.id,"vc_automod.triggers",trigger)
        await interaction.response.send_message(f"Added VC automod trigger **{trigger['label']}**.",ephemeral=True)


class VCTriggerView(discord.ui.View):
    def __init__(self, bot) -> None:
        super().__init__(timeout=600); self.bot=bot
    @discord.ui.button(label="Add Trigger", emoji="➕", style=discord.ButtonStyle.success)
    async def add(self, interaction, _):
        if not await _authorized(interaction,self.bot,minimum="admin"): return
        await interaction.response.send_modal(VCTriggerModal(self.bot))
    @discord.ui.button(label="Edit Trigger", emoji="✏️", style=discord.ButtonStyle.primary)
    async def edit(self, interaction, _):
        if not await _authorized(interaction,self.bot,minimum="admin"): return
        cog=_vc_automod_cog(self.bot); cfg=await cog.ensure_defaults(interaction.guild) if cog else ((await self.bot.database.get_guild_profile(interaction.guild.id) or {}).get("vc_automod") or {})
        await interaction.response.edit_message(embed=discord.Embed(title="Edit VC Trigger",description="Choose a trigger. Built-in trigger thresholds can be edited too."),view=VCTriggerEditPickView(self.bot,cfg.get("triggers") or []))

    @discord.ui.button(label="Remove Trigger", emoji="➖", style=discord.ButtonStyle.danger)
    async def remove(self, interaction, _):
        if not await _authorized(interaction,self.bot,minimum="admin"): return
        profile = await self.bot.database.get_guild_profile(interaction.guild.id) or {}
        triggers = (profile.get("vc_automod") or {}).get("triggers") or []
        await interaction.response.edit_message(embed=discord.Embed(title="Remove VC Trigger",description="Choose a custom trigger to remove. The built-in N-word rule stays available."),view=VCTriggerRemoveView(self.bot, triggers))
    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, _): await navigate(interaction,self.bot,"automod")
    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.success)
    async def home(self, interaction, _): await navigate(interaction,self.bot,"home")


class VCTriggerRemoveSelect(discord.ui.Select):
    def __init__(self, bot, triggers):
        self.bot=bot
        options=[discord.SelectOption(label=str(t.get("label") or "Trigger")[:100],value=str(t.get("id"))) for t in triggers if not t.get("builtin")][:25]
        super().__init__(placeholder="Choose trigger…",options=options or [discord.SelectOption(label="No custom triggers",value="none")])
    async def callback(self, interaction):
        if self.values[0]=="none": await interaction.response.send_message("There are no custom triggers to remove.",ephemeral=True); return
        await self.bot.database.pull_config_path(interaction.guild.id,"vc_automod.triggers",{"id":self.values[0]})
        await interaction.response.send_message("Removed that VC automod trigger.",ephemeral=True)


class VCTriggerRemoveView(discord.ui.View):
    def __init__(self, bot, triggers):
        super().__init__(timeout=300); self.bot=bot; self.add_item(VCTriggerRemoveSelect(self.bot, triggers))
    @discord.ui.button(label="Back",emoji="⬅️",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,interaction,_): await show_vc_triggers_page(interaction,self.bot)
    @discord.ui.button(label="Home",emoji="🏠",style=discord.ButtonStyle.success,row=1)
    async def home(self,interaction,_): await navigate(interaction,self.bot,"home")


class VCTriggerEditModal(discord.ui.Modal, title="Edit VC Automod Trigger"):
    hits = discord.ui.TextInput(label="Hits required", max_length=3)
    window = discord.ui.TextInput(label="Window in minutes", max_length=5)
    points = discord.ui.TextInput(label="Infraction points", max_length=4)
    history = discord.ui.TextInput(label="Points/history lifetime in days", max_length=4)
    quote_lenient = discord.ui.TextInput(label="Quote leniency (yes/no)", max_length=5)
    def __init__(self, bot, trigger):
        super().__init__(); self.bot=bot; self.trigger=trigger
        self.hits.default=str(trigger.get("threshold",3)); self.window.default=str(trigger.get("window_minutes",10))
        self.points.default=str(trigger.get("points",1)); self.history.default=str(trigger.get("history_days",7))
        self.quote_lenient.default="yes" if trigger.get("quote_lenient",True) else "no"
    async def on_submit(self, interaction):
        try:
            hits=max(1,int(str(self.hits))); window=max(1,int(str(self.window))); points=max(1,int(str(self.points))); history=max(1,int(str(self.history)))
        except ValueError:
            await interaction.response.send_message("Hits, window, points, and history must be numbers.",ephemeral=True); return
        q=str(self.quote_lenient).strip().lower()
        if q not in {"yes","y","true","on","1","no","n","false","off","0"}:
            await interaction.response.send_message("Quote leniency must be yes or no.",ephemeral=True); return
        profile=await self.bot.database.get_guild_profile(interaction.guild.id) or {}; triggers=list((profile.get("vc_automod") or {}).get("triggers") or [])
        for t in triggers:
            if str(t.get("id"))==str(self.trigger.get("id")):
                t.update({"threshold":hits,"window_minutes":window,"points":points,"history_days":history,"quote_lenient":q in {"yes","y","true","on","1"}})
        await self.bot.database.set_config_path(interaction.guild.id,"vc_automod.triggers",triggers)
        await interaction.response.send_message(f"Updated **{self.trigger.get('label','trigger')}**.",ephemeral=True)

class VCTriggerEditSelect(discord.ui.Select):
    def __init__(self,bot,triggers):
        self.bot=bot; self.trigger_map={str(t.get("id")):t for t in triggers}
        options=[discord.SelectOption(label=str(t.get("label") or "Trigger")[:100],value=str(t.get("id"))) for t in triggers][:25]
        super().__init__(placeholder="Choose trigger to edit…",options=options or [discord.SelectOption(label="No triggers",value="none")])
    async def callback(self,interaction):
        value=self.values[0]
        if value=="none": await interaction.response.send_message("No triggers are configured.",ephemeral=True); return
        await interaction.response.send_modal(VCTriggerEditModal(self.bot,self.trigger_map[value]))

class VCTriggerEditPickView(discord.ui.View):
    def __init__(self,bot,triggers): super().__init__(timeout=300); self.bot=bot; self.add_item(VCTriggerEditSelect(bot,triggers))
    @discord.ui.button(label="Back",emoji="⬅️",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,interaction,_): await show_vc_triggers_page(interaction,self.bot)

PUNISHMENT_ACTION_LABELS = {
    "warning":"Warning only",
    "remove_role":"Remove House Trained",
    "timeout":"Discord timeout",
    "remove_timeout":"Remove House Trained + timeout",
    "kick":"Kick",
    "ban":"Ban",
}

def _parse_yes_no(value: str) -> bool | None:
    value=value.strip().lower()
    if value in {"yes","y","true","on","1"}: return True
    if value in {"no","n","false","off","0"}: return False
    return None

def _normalize_action(value: str) -> str | None:
    v=value.strip().lower().replace("+","_").replace(" ","_").replace("-","_")
    aliases={"remove":"remove_role","house_trained":"remove_role","role":"remove_role","remove_house_trained":"remove_role","remove_and_timeout":"remove_timeout","role_timeout":"remove_timeout","remove_role_timeout":"remove_timeout"}
    v=aliases.get(v,v)
    return v if v in PUNISHMENT_ACTION_LABELS else None

class PunishmentLevelModal(discord.ui.Modal, title="Punishment Ladder Level"):
    threshold=discord.ui.TextInput(label="Score threshold",max_length=4)
    action=discord.ui.TextInput(label="Action",placeholder="warning / remove_role / timeout / remove_timeout / kick / ban",max_length=30)
    duration=discord.ui.TextInput(label="Duration in minutes (0 for warning/kick/ban)",max_length=7)
    sound=discord.ui.TextInput(label="Play punishment sound? (yes/no)",max_length=5)
    reset=discord.ui.TextInput(label="Reset score after this level? (yes/no)",max_length=5)
    def __init__(self,bot,index=None,row=None):
        super().__init__(); self.bot=bot; self.index=index; row=row or {"threshold":1,"action":"remove_timeout","duration_minutes":5,"play_sound":True,"reset_score":False}
        self.threshold.default=str(row.get("threshold",1)); self.action.default=str(row.get("action","remove_timeout")); self.duration.default=str(row.get("duration_minutes",5)); self.sound.default="yes" if row.get("play_sound",True) else "no"; self.reset.default="yes" if row.get("reset_score",False) else "no"
    async def on_submit(self,interaction):
        try: threshold=max(1,int(str(self.threshold))); duration=max(0,int(str(self.duration)))
        except ValueError: await interaction.response.send_message("Threshold and duration must be numbers.",ephemeral=True); return
        action=_normalize_action(str(self.action)); sound=_parse_yes_no(str(self.sound)); reset=_parse_yes_no(str(self.reset))
        if action is None: await interaction.response.send_message("Action must be warning, remove_role, timeout, remove_timeout, kick, or ban.",ephemeral=True); return
        if sound is None or reset is None: await interaction.response.send_message("Sound/reset must be yes or no.",ephemeral=True); return
        profile=await self.bot.database.get_guild_profile(interaction.guild.id) or {}; ladder=list((profile.get("vc_automod") or {}).get("punishment_ladder") or [])
        row={"threshold":threshold,"action":action,"duration_minutes":duration,"play_sound":sound,"reset_score":reset}
        if self.index is None: ladder.append(row)
        elif 0 <= self.index < len(ladder): ladder[self.index]=row
        else: await interaction.response.send_message("That ladder level no longer exists.",ephemeral=True); return
        ladder.sort(key=lambda r:max(1,int(r.get("threshold",1))))
        await self.bot.database.set_config_path(interaction.guild.id,"vc_automod.punishment_ladder",ladder)
        await interaction.response.send_message("Punishment ladder saved.",ephemeral=True)

class PunishmentLevelSelect(discord.ui.Select):
    def __init__(self,bot,ladder,mode):
        self.bot=bot; self.ladder=ladder; self.mode=mode
        opts=[]
        for i,row in enumerate(ladder[:25]):
            action=PUNISHMENT_ACTION_LABELS.get(str(row.get("action")),str(row.get("action")))
            opts.append(discord.SelectOption(label=f"{row.get('threshold',1)} pts • {action}"[:100],value=str(i),description=f"{row.get('duration_minutes',0)} min"))
        super().__init__(placeholder=f"Choose level to {mode}…",options=opts or [discord.SelectOption(label="No levels",value="none")])
    async def callback(self,interaction):
        if self.values[0]=="none": await interaction.response.send_message("No punishment levels exist.",ephemeral=True); return
        idx=int(self.values[0])
        if self.mode=="edit": await interaction.response.send_modal(PunishmentLevelModal(self.bot,idx,self.ladder[idx])); return
        ladder=list(self.ladder); ladder.pop(idx); await self.bot.database.set_config_path(interaction.guild.id,"vc_automod.punishment_ladder",ladder); await interaction.response.send_message("Removed that punishment level.",ephemeral=True)

class PunishmentLevelPickView(discord.ui.View):
    def __init__(self,bot,ladder,mode): super().__init__(timeout=300); self.bot=bot; self.add_item(PunishmentLevelSelect(bot,ladder,mode))
    @discord.ui.button(label="Back",emoji="⬅️",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,interaction,_): await show_punishment_ladder_page(interaction,self.bot)

class PunishmentLadderView(discord.ui.View):
    def __init__(self,bot,ladder): super().__init__(timeout=600); self.bot=bot; self.ladder=ladder
    @discord.ui.button(label="Add Level",emoji="➕",style=discord.ButtonStyle.success)
    async def add(self,interaction,_):
        if not await _authorized(interaction,self.bot,minimum="admin"): return
        await interaction.response.send_modal(PunishmentLevelModal(self.bot))
    @discord.ui.button(label="Edit Level",emoji="✏️",style=discord.ButtonStyle.primary)
    async def edit(self,interaction,_):
        if not await _authorized(interaction,self.bot,minimum="admin"): return
        await interaction.response.edit_message(embed=discord.Embed(title="Edit Punishment Level"),view=PunishmentLevelPickView(self.bot,self.ladder,"edit"))
    @discord.ui.button(label="Remove Level",emoji="➖",style=discord.ButtonStyle.danger)
    async def remove(self,interaction,_):
        if not await _authorized(interaction,self.bot,minimum="admin"): return
        await interaction.response.edit_message(embed=discord.Embed(title="Remove Punishment Level"),view=PunishmentLevelPickView(self.bot,self.ladder,"remove"))
    @discord.ui.button(label="Back",emoji="⬅️",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,interaction,_): await navigate(interaction,self.bot,"automod")
    @discord.ui.button(label="Home",emoji="🏠",style=discord.ButtonStyle.success,row=1)
    async def home(self,interaction,_): await navigate(interaction,self.bot,"home")


class ExceptionRoleSelect(discord.ui.RoleSelect):
    def __init__(self,parent,action): super().__init__(placeholder="Choose a role…",min_values=1,max_values=1); self.owner_view=parent; self.action=action
    async def callback(self,interaction):
        role=self.values[0]; profile=await self.owner_view.bot.database.get_guild_profile(interaction.guild.id) or {}; ids=[int(v) for v in (profile.get("vc_automod") or {}).get("exception_role_ids") or []]
        if self.action=="add" and role.id not in ids: ids.append(role.id)
        if self.action=="remove": ids=[v for v in ids if v!=role.id]
        await self.owner_view.bot.database.set_config_path(interaction.guild.id,"vc_automod.exception_role_ids",ids)
        await interaction.response.send_message(f"Updated exception roles for {role.mention}.",ephemeral=True)

class ExceptionRolePickView(discord.ui.View):
    def __init__(self,bot,action): super().__init__(timeout=300); self.bot=bot; self.add_item(ExceptionRoleSelect(self,action))
    @discord.ui.button(label="Back",emoji="⬅️",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,interaction,_): await show_exception_roles_page(interaction,self.bot)
    @discord.ui.button(label="Home",emoji="🏠",style=discord.ButtonStyle.success,row=1)
    async def home(self,interaction,_): await navigate(interaction,self.bot,"home")

class ExceptionRolesView(discord.ui.View):
    def __init__(self,bot): super().__init__(timeout=600); self.bot=bot
    @discord.ui.button(label="Add Role",emoji="➕",style=discord.ButtonStyle.success)
    async def add(self,interaction,_): await interaction.response.edit_message(embed=discord.Embed(title="Add Exception Role"),view=ExceptionRolePickView(self.bot,"add"))
    @discord.ui.button(label="Remove Role",emoji="➖",style=discord.ButtonStyle.danger)
    async def remove(self,interaction,_): await interaction.response.edit_message(embed=discord.Embed(title="Remove Exception Role"),view=ExceptionRolePickView(self.bot,"remove"))
    @discord.ui.button(label="Back",emoji="⬅️",style=discord.ButtonStyle.secondary)
    async def back(self,interaction,_): await navigate(interaction,self.bot,"automod")
    @discord.ui.button(label="Home",emoji="🏠",style=discord.ButtonStyle.success)
    async def home(self,interaction,_): await navigate(interaction,self.bot,"home")


class AutomodMemberSelect(discord.ui.UserSelect):
    def __init__(self,parent,action): super().__init__(placeholder="Choose member…",min_values=1,max_values=1); self.owner_view=parent; self.action=action
    async def callback(self,interaction):
        member=interaction.guild.get_member(int(self.values[0].id)); cog=_vc_automod_cog(self.owner_view.bot)
        if member is None or cog is None: await interaction.response.send_message("Member/automod unavailable.",ephemeral=True); return
        if self.action=="history":
            rows=await cog.history_for(interaction.guild.id,member.id); infractions=await cog.infraction_history(interaction.guild.id,member.id); score=await cog.active_score(interaction.guild.id,member.id)
            detections="\n".join(f"• {r.get('trigger_label')} • {'possible quote' if r.get('possible_quote') else 'credible'} • {r.get('created_at').strftime('%m/%d %H:%M')}" for r in rows[:8]) or "No stored detections."
            points="\n".join(f"• +{r.get('points',1)} {r.get('trigger_label')} • {r.get('created_at').strftime('%m/%d %H:%M')}" for r in infractions[:6]) or "No active/stored infractions."
            desc=f"**Active score: {score}**\n\n**Detections**\n{detections}\n\n**Point infractions**\n{points}"
            await interaction.response.edit_message(embed=discord.Embed(title=f"VC History • {member.display_name}",description=desc[:4000]),view=MemberHistoryView(self.owner_view.bot,member.id)); return
        if self.action=="forgive": await cog.forgive(interaction.guild.id,member.id); msg=f"Cleared VC detections for {member.mention}."
        else: msg=await cog.restore_member(member)
        await interaction.response.send_message(msg,ephemeral=True)


class MemberHistoryView(discord.ui.View):
    def __init__(self,bot,user_id): super().__init__(timeout=600); self.bot=bot; self.user_id=user_id
    async def _member(self,interaction): return interaction.guild.get_member(int(self.user_id))
    @discord.ui.button(label="Forgive Latest Point",emoji="↩️",style=discord.ButtonStyle.secondary,row=0)
    async def latest(self,interaction,_):
        if not await _authorized(interaction,self.bot,minimum="admin"): return
        cog=_vc_automod_cog(self.bot); ok=await cog.clear_latest_infraction(interaction.guild.id,self.user_id); await interaction.response.send_message("Removed the latest point infraction." if ok else "No point infractions to remove.",ephemeral=True)
    @discord.ui.button(label="Clear Score",emoji="🧽",style=discord.ButtonStyle.danger,row=0)
    async def score(self,interaction,_):
        if not await _authorized(interaction,self.bot,minimum="admin"): return
        cog=_vc_automod_cog(self.bot); await cog.clear_score(interaction.guild.id,self.user_id); await interaction.response.send_message("Cleared that member's active infraction score.",ephemeral=True)
    @discord.ui.button(label="Clear Detections + Score",emoji="🗑️",style=discord.ButtonStyle.danger,row=0)
    async def all(self,interaction,_):
        if not await _authorized(interaction,self.bot,minimum="admin"): return
        cog=_vc_automod_cog(self.bot); await cog.forgive(interaction.guild.id,self.user_id); await interaction.response.send_message("Cleared detections and point infractions.",ephemeral=True)
    @discord.ui.button(label="Restore / Clear Punishment",emoji="🔓",style=discord.ButtonStyle.success,row=1)
    async def restore(self,interaction,_):
        if not await _authorized(interaction,self.bot,minimum="admin"): return
        member=await self._member(interaction); cog=_vc_automod_cog(self.bot)
        if member is None: await interaction.response.send_message("Member is no longer in the server.",ephemeral=True); return
        try:
            await member.timeout(None,reason="Manual Mommy.exe punishment clear")
        except (discord.Forbidden,discord.HTTPException): pass
        msg=await cog.restore_member(member); await interaction.response.send_message(msg+" Discord timeout was also cleared where permitted.",ephemeral=True)
    @discord.ui.button(label="Back",emoji="⬅️",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,interaction,_): await navigate(interaction,self.bot,"automod")

class AutomodMemberActionView(discord.ui.View):
    def __init__(self,bot,action): super().__init__(timeout=300); self.bot=bot; self.add_item(AutomodMemberSelect(self,action))
    @discord.ui.button(label="Back",emoji="⬅️",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,interaction,_): await navigate(interaction,self.bot,"automod")
    @discord.ui.button(label="Home",emoji="🏠",style=discord.ButtonStyle.success,row=1)
    async def home(self,interaction,_): await navigate(interaction,self.bot,"home")


class HouseRoleSelect(discord.ui.RoleSelect):
    def __init__(self,parent): super().__init__(placeholder="Choose House Trained role…",min_values=1,max_values=1); self.owner_view=parent
    async def callback(self,interaction):
        if not await _authorized(interaction, self.owner_view.bot, minimum="admin"): return
        role=self.values[0]
        await self.owner_view.bot.database.set_config_path(interaction.guild.id,"verification.house_trained_role_id",role.id)
        await interaction.response.send_message(f"House Trained role set to {role.mention}. Make sure Mommy's role is above it.",ephemeral=True)
class HouseRoleView(discord.ui.View):
    def __init__(self,bot,back_page="verification"):
        super().__init__(timeout=300); self.bot=bot; self.back_page=back_page; self.add_item(HouseRoleSelect(self))
    @discord.ui.button(label="Back",emoji="⬅️",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,interaction,_): await navigate(interaction,self.bot,self.back_page)
    @discord.ui.button(label="Home",emoji="🏠",style=discord.ButtonStyle.success,row=1)
    async def home(self,interaction,_): await navigate(interaction,self.bot,"home")


class HouseAccessMemberSelect(discord.ui.UserSelect):
    def __init__(self,parent,action):
        super().__init__(placeholder="Choose member…",min_values=1,max_values=1); self.owner_view=parent; self.action=action
    async def callback(self,interaction):
        if not await _authorized(interaction, self.owner_view.bot, minimum="admin"): return
        member=interaction.guild.get_member(int(self.values[0].id))
        profile=await self.owner_view.bot.database.get_guild_profile(interaction.guild.id) or {}
        role_id=(profile.get("verification") or {}).get("house_trained_role_id")
        role=interaction.guild.get_role(int(role_id)) if role_id else None
        if member is None or role is None:
            await interaction.response.send_message("Choose a member and configure House Trained first.",ephemeral=True); return
        if self.action=="grant":
            if role not in member.roles: await member.add_roles(role,reason="Manual Mommy.exe House Trained grant")
            msg=f"Granted {role.mention} to {member.mention}."
        else:
            if role in member.roles: await member.remove_roles(role,reason="Manual Mommy.exe House Trained removal")
            msg=f"Removed {role.mention} from {member.mention}."
        await interaction.response.send_message(msg,ephemeral=True)

class HouseAccessPickView(discord.ui.View):
    def __init__(self,bot,action):
        super().__init__(timeout=300); self.bot=bot; self.add_item(HouseAccessMemberSelect(self,action))
    @discord.ui.button(label="Back",emoji="⬅️",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,interaction,_): await navigate(interaction,self.bot,"automod")
    @discord.ui.button(label="Home",emoji="🏠",style=discord.ButtonStyle.success,row=1)
    async def home(self,interaction,_): await navigate(interaction,self.bot,"home")

class HouseAccessView(discord.ui.View):
    def __init__(self,bot): super().__init__(timeout=600); self.bot=bot
    @discord.ui.button(label="Grant House Trained",emoji="✅",style=discord.ButtonStyle.success)
    async def grant(self,interaction,_):
        if not await _authorized(interaction,self.bot,minimum="admin"): return
        await interaction.response.edit_message(embed=discord.Embed(title="Grant House Trained",description="Choose a member."),view=HouseAccessPickView(self.bot,"grant"))
    @discord.ui.button(label="Remove House Trained",emoji="🚫",style=discord.ButtonStyle.danger)
    async def remove(self,interaction,_):
        if not await _authorized(interaction,self.bot,minimum="admin"): return
        await interaction.response.edit_message(embed=discord.Embed(title="Remove House Trained",description="Choose a member."),view=HouseAccessPickView(self.bot,"remove"))
    @discord.ui.button(label="Back",emoji="⬅️",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,interaction,_): await navigate(interaction,self.bot,"automod")
    @discord.ui.button(label="Home",emoji="🏠",style=discord.ButtonStyle.success,row=1)
    async def home(self,interaction,_): await navigate(interaction,self.bot,"home")


class PunishmentSoundURLModal(discord.ui.Modal,title="Set Punishment Sound URL"):
    url=discord.ui.TextInput(label="Direct audio URL",placeholder="https://.../sound.mp3",max_length=500)
    def __init__(self,bot): super().__init__(); self.bot=bot
    async def on_submit(self,interaction):
        if not await _authorized(interaction,self.bot,minimum="admin"): return
        value=str(self.url).strip()
        if not value.startswith(("https://","http://")):
            await interaction.response.send_message("Use a direct http/https audio URL.",ephemeral=True); return
        await self.bot.database.set_config_path(interaction.guild.id,"vc_automod.punishment_sound_url",value)
        await self.bot.database.set_config_path(interaction.guild.id,"vc_automod.punishment_sound_mode","url")
        await self.bot.database.set_config_path(interaction.guild.id,"vc_automod.punishment_sound_enabled",True)
        await interaction.response.send_message("Custom punishment sound saved. Use **Test Sound** to try it.",ephemeral=True)

class PunishmentTTSModal(discord.ui.Modal,title="Set Punishment TTS Line"):
    text=discord.ui.TextInput(label="What should Mommy say?",default="You're banned.",max_length=180)
    def __init__(self,bot): super().__init__(); self.bot=bot
    async def on_submit(self,interaction):
        if not await _authorized(interaction,self.bot,minimum="admin"): return
        await self.bot.database.set_config_path(interaction.guild.id,"vc_automod.punishment_tts_text",str(self.text).strip() or "You're banned.")
        await self.bot.database.set_config_path(interaction.guild.id,"vc_automod.punishment_sound_mode","tts")
        await self.bot.database.set_config_path(interaction.guild.id,"vc_automod.punishment_sound_enabled",True)
        await interaction.response.send_message("Punishment TTS line saved. Use **Test Sound** to try it.",ephemeral=True)

class PunishmentVolumeSelect(discord.ui.Select):
    def __init__(self, bot):
        self.bot = bot
        options = [
            discord.SelectOption(label="100%", value="1.0", description="Normal volume"),
            discord.SelectOption(label="150%", value="1.5"),
            discord.SelectOption(label="200%", value="2.0"),
            discord.SelectOption(label="300%", value="3.0", description="Very loud"),
            discord.SelectOption(label="400%", value="4.0", description="Maximum boost"),
        ]
        super().__init__(placeholder="Punishment sound volume…", min_values=1, max_values=1, options=options, row=2)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"):
            return
        value = float(self.values[0])
        await self.bot.database.set_config_path(interaction.guild.id, "vc_automod.punishment_sound_volume", value)
        await interaction.response.send_message(f"Punishment sound volume set to **{int(value * 100)}%**.", ephemeral=True)


class PunishmentSoundboardSelect(discord.ui.Select):
    def __init__(self, bot, sounds):
        self.bot = bot
        options = []
        for sound in sounds[:25]:
            label = str(getattr(sound, "name", "Sound"))[:100]
            sid = str(getattr(sound, "id", getattr(sound, "sound_id", "")))
            emoji = getattr(sound, "emoji", None)
            kwargs = {"label": label, "value": sid, "description": f"Server soundboard • ID {sid}"[:100]}
            if emoji:
                kwargs["emoji"] = emoji
            options.append(discord.SelectOption(**kwargs))
        super().__init__(placeholder="Choose a server Soundboard sound…", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"):
            return
        sound_id = int(self.values[0])
        sound = interaction.guild.get_soundboard_sound(sound_id)
        name = getattr(sound, "name", f"Sound {sound_id}") if sound else f"Sound {sound_id}"
        await self.bot.database.set_config_path(interaction.guild.id, "vc_automod.punishment_soundboard_id", sound_id)
        await self.bot.database.set_config_path(interaction.guild.id, "vc_automod.punishment_soundboard_name", str(name))
        await self.bot.database.set_config_path(interaction.guild.id, "vc_automod.punishment_sound_mode", "soundboard")
        await self.bot.database.set_config_path(interaction.guild.id, "vc_automod.punishment_sound_enabled", True)
        await interaction.response.send_message(f"Soundboard punishment sound set to **{name}**.", ephemeral=True)


class PunishmentSoundboardPickView(discord.ui.View):
    def __init__(self, bot, sounds):
        super().__init__(timeout=300)
        self.bot = bot
        self.add_item(PunishmentSoundboardSelect(bot, sounds))

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, _):
        await show_punishment_sound_page(interaction, self.bot)


class PunishmentSoundView(discord.ui.View):
    def __init__(self,bot):
        super().__init__(timeout=600)
        self.bot=bot
        self.add_item(PunishmentVolumeSelect(bot))
    @discord.ui.button(label="Server Soundboard",emoji="🔊",style=discord.ButtonStyle.primary,row=0)
    async def soundboard(self, interaction, _):
        if not await _authorized(interaction, self.bot, minimum="admin"):
            return
        try:
            sounds = list(interaction.guild.soundboard_sounds)
            if not sounds:
                sounds = await interaction.guild.fetch_soundboard_sounds()
        except Exception as exc:
            await interaction.response.send_message(f"I couldn't load this server's Soundboard: `{type(exc).__name__}` — {str(exc)[:500]}", ephemeral=True)
            return
        sounds = [x for x in sounds if getattr(x, "available", True)]
        if not sounds:
            await interaction.response.send_message("This server doesn't have any available Soundboard sounds.", ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=discord.Embed(title="Choose Server Soundboard Sound", description="Pick the sound Mommy should play for the punishment."),
            view=PunishmentSoundboardPickView(self.bot, sounds),
        )

    @discord.ui.button(label="Custom Audio URL",emoji="🔗",style=discord.ButtonStyle.secondary,row=1)
    async def url(self,interaction,_):
        if not await _authorized(interaction,self.bot,minimum="admin"): return
        await interaction.response.send_modal(PunishmentSoundURLModal(self.bot))
    @discord.ui.button(label="TTS Line",emoji="🗣️",style=discord.ButtonStyle.secondary,row=1)
    async def tts(self,interaction,_):
        if not await _authorized(interaction,self.bot,minimum="admin"): return
        await interaction.response.send_modal(PunishmentTTSModal(self.bot))
    @discord.ui.button(label="Turn Off",emoji="🔇",style=discord.ButtonStyle.danger,row=1)
    async def off(self,interaction,_):
        if not await _authorized(interaction,self.bot,minimum="admin"): return
        await self.bot.database.set_config_path(interaction.guild.id,"vc_automod.punishment_sound_mode","off")
        await self.bot.database.set_config_path(interaction.guild.id,"vc_automod.punishment_sound_enabled",False)
        await show_punishment_sound_page(interaction,self.bot)
    @discord.ui.button(label="Test Sound",emoji="▶️",style=discord.ButtonStyle.success,row=3)
    async def test(self,interaction,_):
        if not await _authorized(interaction,self.bot,minimum="admin"): return
        cog=_voice_cog(self.bot)
        if cog is None or not interaction.guild.voice_client:
            await interaction.response.send_message("Mommy needs to be connected to a VC before testing the sound.",ephemeral=True); return
        await interaction.response.defer(ephemeral=True,thinking=True)
        try:
            await cog.play_punishment_sound(interaction.guild)
            await interaction.followup.send("Played the configured punishment sound.",ephemeral=True)
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            await interaction.followup.send(
                f"Sound test failed: `{type(exc).__name__}` — {detail[:900]}",
                ephemeral=True,
            )
    @discord.ui.button(label="Back",emoji="⬅️",style=discord.ButtonStyle.secondary,row=3)
    async def back(self,interaction,_): await navigate(interaction,self.bot,"automod")
    @discord.ui.button(label="Home",emoji="🏠",style=discord.ButtonStyle.success,row=3)
    async def home(self,interaction,_): await navigate(interaction,self.bot,"home")

class RulesChannelSelect(discord.ui.ChannelSelect):
    def __init__(self,parent): super().__init__(placeholder="Choose rules channel…",channel_types=[discord.ChannelType.text],min_values=1,max_values=1); self.owner_view=parent
    async def callback(self,interaction):
        ch=self.values[0]; await self.owner_view.bot.database.set_config_path(interaction.guild.id,"verification.rules_channel_id",ch.id); await interaction.response.send_message(f"Rules channel set to {ch.mention}.",ephemeral=True)
class RulesChannelView(discord.ui.View):
    def __init__(self,bot): super().__init__(timeout=300); self.bot=bot; self.add_item(RulesChannelSelect(self))
    @discord.ui.button(label="Back",emoji="⬅️",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,interaction,_): await navigate(interaction,self.bot,"verification")

class RulesPostModal(discord.ui.Modal,title="Set Rules Post"):
    message_id=discord.ui.TextInput(label="Rules message ID or message link",placeholder="Paste the message ID or full Discord message link",max_length=200)
    emoji=discord.ui.TextInput(label="Reaction emoji",default="✅",max_length=80)
    def __init__(self,bot): super().__init__(); self.bot=bot
    async def on_submit(self,interaction):
        import re
        nums=re.findall(r"\d{15,22}",str(self.message_id));
        if not nums: await interaction.response.send_message("I couldn't find a Discord message ID in that.",ephemeral=True); return
        await self.bot.database.set_config_path(interaction.guild.id,"verification.rules_message_id",int(nums[-1])); await self.bot.database.set_config_path(interaction.guild.id,"verification.rules_emoji",str(self.emoji).strip() or "✅")
        await interaction.response.send_message("Rules reaction post saved.",ephemeral=True)

class VerificationView(discord.ui.View):
    def __init__(self,bot): super().__init__(timeout=600); self.bot=bot
    @discord.ui.button(label="Enable / Disable",emoji="✅",style=discord.ButtonStyle.primary,row=0)
    async def toggle(self,interaction,_):
        profile=await self.bot.database.get_guild_profile(interaction.guild.id) or {}; cur=bool((profile.get("verification") or {}).get("enabled",False)); await self.bot.database.set_config_path(interaction.guild.id,"verification.enabled",not cur); await show_verification_page(interaction,self.bot)
    @discord.ui.button(label="House Trained Role",emoji="🏠",style=discord.ButtonStyle.secondary,row=0)
    async def role(self,interaction,_): await interaction.response.edit_message(embed=discord.Embed(title="Choose House Trained Role"),view=HouseRoleView(self.bot))
    @discord.ui.button(label="Rules Channel",emoji="#️⃣",style=discord.ButtonStyle.secondary,row=0)
    async def channel(self,interaction,_): await interaction.response.edit_message(embed=discord.Embed(title="Choose Rules Channel"),view=RulesChannelView(self.bot))
    @discord.ui.button(label="Rules Post + Emoji",emoji="📝",style=discord.ButtonStyle.secondary,row=1)
    async def post(self,interaction,_): await interaction.response.send_modal(RulesPostModal(self.bot))
    @discord.ui.button(label="Back",emoji="⬅️",style=discord.ButtonStyle.secondary,row=3)
    async def back(self,interaction,_): await navigate(interaction,self.bot,"home")
    @discord.ui.button(label="Home",emoji="🏠",style=discord.ButtonStyle.success,row=3)
    async def home(self,interaction,_): await navigate(interaction,self.bot,"home")


async def show_automod_page(interaction: discord.Interaction, bot) -> None:
    cog=_vc_automod_cog(bot); cfg=await cog.ensure_defaults(interaction.guild) if cog else ((await bot.database.get_guild_profile(interaction.guild.id) or {}).get("vc_automod") or {})
    profile=await bot.database.get_guild_profile(interaction.guild.id) or {}; role_id=(profile.get("verification") or {}).get("house_trained_role_id")
    triggers=cfg.get("triggers") or []
    ladder=cfg.get("punishment_ladder") or []
    ladder_text=" → ".join(f"{r.get('threshold',1)}pt:{PUNISHMENT_ACTION_LABELS.get(str(r.get('action')),str(r.get('action')))}" for r in sorted(ladder,key=lambda x:int(x.get('threshold',1)))) or "not configured"
    desc=(f"**Enabled:** {'yes' if cfg.get('enabled',True) else 'no'}\n**House Trained:** {f'<@&{role_id}>' if role_id else 'not set'}\n**Triggers:** {len(triggers)}\n**Exception roles:** {len(cfg.get('exception_role_ids') or [])}\n**Punishment ladder:** {ladder_text[:700]}\n**Punishment sound:** {str(cfg.get('punishment_sound_mode') or 'tts') if cfg.get('punishment_sound_enabled',True) else 'off'}\n\nEach trigger controls its own hit window, points, and history lifetime. Possible quotations are logged but do not count toward automatic punishment.")
    await interaction.response.edit_message(embed=discord.Embed(title="Mommy.exe • VC Automod",description=desc),view=AutomodView(bot))


async def show_punishment_ladder_page(interaction: discord.Interaction, bot) -> None:
    cog=_vc_automod_cog(bot); cfg=await cog.ensure_defaults(interaction.guild) if cog else ((await bot.database.get_guild_profile(interaction.guild.id) or {}).get("vc_automod") or {})
    ladder=list(cfg.get("punishment_ladder") or [])
    lines=[]
    for row in sorted(ladder,key=lambda r:int(r.get("threshold",1))):
        action=PUNISHMENT_ACTION_LABELS.get(str(row.get("action")),str(row.get("action")))
        duration=int(row.get("duration_minutes",0) or 0)
        detail=f" • {duration} min" if duration else ""
        if row.get("play_sound",True): detail+=" • sound"
        if row.get("reset_score",False): detail+=" • resets score"
        lines.append(f"• **{row.get('threshold',1)} pts** → {action}{detail}")
    desc="\n".join(lines) or "No punishment levels. Add one to make score thresholds perform actions."
    desc += "\n\nActions: `warning`, `remove_role`, `timeout`, `remove_timeout`, `kick`, `ban`.\n`remove_timeout` attempts **both** actions independently, so a missing/failed House Trained role does not prevent the timeout."
    await interaction.response.edit_message(embed=discord.Embed(title="VC Automod • Punishment Ladder",description=desc[:4000]),view=PunishmentLadderView(bot,ladder))

async def show_punishment_sound_page(interaction: discord.Interaction, bot) -> None:
    profile=await bot.database.get_guild_profile(interaction.guild.id) or {}
    cfg=profile.get("vc_automod") or {}
    mode=str(cfg.get("punishment_sound_mode") or "tts").lower()
    enabled=bool(cfg.get("punishment_sound_enabled",True)) and mode!="off"
    if mode=="soundboard":
        sb_name = str(cfg.get("punishment_soundboard_name") or "Selected server sound")
        detail = f"Server Soundboard: **{sb_name[:80]}**"
    elif mode=="url":
        detail="Custom audio URL" if cfg.get("punishment_sound_url") else "Custom URL (not set)"
    elif mode=="off": detail="Off"
    else: detail=f"TTS: `{str(cfg.get('punishment_tts_text') or "You're banned.")[:80]}`"
    try:
        volume_pct = int(float(cfg.get("punishment_sound_volume", 3.0)) * 100)
    except (TypeError, ValueError):
        volume_pct = 300
    desc=(
        f"**Enabled:** {'yes' if enabled else 'no'}\n"
        f"**Selected sound:** {detail}\n"
        f"**Volume:** {volume_pct}%\n\n"
        "Pick a sound already uploaded to this server’s **Soundboard**, use a direct audio URL, "
        "or let Mommy say a short TTS line. **Test Sound** plays the current choice in her VC.\n\n"
        "Soundboard playback uses Discord’s own Soundboard volume; the boost selector applies to URL/TTS playback."
    )
    await interaction.response.edit_message(embed=discord.Embed(title="VC Automod • Punishment Sound",description=desc),view=PunishmentSoundView(bot))

async def show_vc_triggers_page(interaction: discord.Interaction, bot) -> None:
    cog=_vc_automod_cog(bot); cfg=await cog.ensure_defaults(interaction.guild) if cog else ((await bot.database.get_guild_profile(interaction.guild.id) or {}).get("vc_automod") or {})
    lines=[f"• **{t.get('label','Trigger')}** — {t.get('threshold',3)} hit(s) / {t.get('window_minutes',10)} min → **{t.get('points',1)} pt** • lasts {t.get('history_days',7)}d{' • built-in' if t.get('builtin') else ''}" for t in cfg.get('triggers') or []]
    await interaction.response.edit_message(embed=discord.Embed(title="VC Automod • Triggers",description="\n".join(lines) or "No triggers."),view=VCTriggerView(bot))

async def show_exception_roles_page(interaction: discord.Interaction, bot) -> None:
    profile=await bot.database.get_guild_profile(interaction.guild.id) or {}; ids=(profile.get("vc_automod") or {}).get("exception_role_ids") or []; lines=[f"• <@&{int(v)}>" for v in ids]
    await interaction.response.edit_message(embed=discord.Embed(title="VC Automod • Exception Roles",description="\n".join(lines) or "No exception roles. Server owner and bots are always exempt."),view=ExceptionRolesView(bot))

async def show_verification_page(interaction: discord.Interaction, bot) -> None:
    profile=await bot.database.get_guild_profile(interaction.guild.id) or {}; cfg=profile.get("verification") or {}; rid=cfg.get("house_trained_role_id"); cid=cfg.get("rules_channel_id"); mid=cfg.get("rules_message_id")
    desc=(f"**Enabled:** {'yes' if cfg.get('enabled') else 'no'}\n**House Trained:** {f'<@&{rid}>' if rid else 'not set'}\n**Rules channel:** {f'<#{cid}>' if cid else 'not set'}\n**Rules message:** `{mid}`\n**Reaction:** {cfg.get('rules_emoji','✅')}\n\nNew members stay without House Trained until they react to the configured rules post. Removing the reaction later does not remove access.")
    await interaction.response.edit_message(embed=discord.Embed(title="Mommy.exe • Verification",description=desc),view=VerificationView(bot))

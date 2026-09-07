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
            "**Voice** controls recording, captions and the live VC session.\n"
            "**Auto Join** controls followed members, multiple auto-join VCs and per-target behavior.\n"
            "**Clips** saves the previous three minutes when enabled.\n"
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

    @discord.ui.button(label="Auto Join", emoji="🔁", style=discord.ButtonStyle.secondary, row=0)
    async def follow(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot):
            return
        await show_autojoin_page(interaction, self.bot)

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

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=3)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "ai")

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.success, row=3)
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

    @discord.ui.button(label="Auto Join", emoji="🔁", style=discord.ButtonStyle.secondary, row=1)
    async def auto(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"):
            return
        await show_autojoin_page(interaction, self.bot)

    @discord.ui.button(label="Captions", emoji="💬", style=discord.ButtonStyle.secondary, row=1)
    async def captions(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"):
            return
        await interaction.response.edit_message(
            embed=discord.Embed(title="Caption Routing", description="Choose the source voice channel first, then the text channel where its live captions should go."),
            view=CaptionVoiceSelectView(self.bot),
        )

    @discord.ui.button(label="Current Session", emoji="🎛️", style=discord.ButtonStyle.secondary, row=2)
    async def follow(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await show_active_session_page(interaction, self.bot)

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


class AutoJoinView(discord.ui.View):
    def __init__(self, bot) -> None:
        super().__init__(timeout=600)
        self.bot = bot

    @discord.ui.button(label="Follow Members", emoji="👣", style=discord.ButtonStyle.primary, row=0)
    async def follow(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await show_follow_page(interaction, self.bot)

    @discord.ui.button(label="Auto-Join Channels", emoji="🔊", style=discord.ButtonStyle.primary, row=0)
    async def channels(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await show_auto_channels_page(interaction, self.bot)

    @discord.ui.button(label="Default Behavior", emoji="⚙️", style=discord.ButtonStyle.secondary, row=0)
    async def defaults(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"):
            return
        await show_default_behavior_page(interaction, self.bot)

    @discord.ui.button(label="Current Session", emoji="🎛️", style=discord.ButtonStyle.secondary, row=1)
    async def active(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await show_active_session_page(interaction, self.bot)

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "voice")

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.success, row=2)
    async def home(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "home")


class FollowView(discord.ui.View):
    def __init__(self, bot) -> None:
        super().__init__(timeout=600)
        self.bot = bot

    @discord.ui.button(label="Add / Edit Member", emoji="➕", style=discord.ButtonStyle.success, row=0)
    async def add(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"):
            return
        await interaction.response.edit_message(
            embed=discord.Embed(title="Follow Members", description="Choose a member, then choose exactly what Mommy should do when following them."),
            view=FollowTargetSelectView(self.bot, action="edit"),
        )

    @discord.ui.button(label="Remove Member", emoji="➖", style=discord.ButtonStyle.danger, row=0)
    async def remove(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"):
            return
        await interaction.response.edit_message(
            embed=discord.Embed(title="Follow Members • Remove", description="Choose a followed member to remove."),
            view=FollowTargetSelectView(self.bot, action="remove"),
        )

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await show_autojoin_page(interaction, self.bot)

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.success, row=1)
    async def home(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "home")


class FollowTargetSelect(discord.ui.UserSelect):
    def __init__(self, parent_view, *, action: str) -> None:
        super().__init__(placeholder="Choose a member…", min_values=1, max_values=1)
        self.owner_view = parent_view
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _authorized(interaction, self.owner_view.bot, minimum="admin"):
            return
        user_id = int(self.values[0].id)
        profile = await self.owner_view.bot.database.get_guild_profile(interaction.guild.id)
        cfg = (profile or {}).get("voice_recording", {})
        targets = list(cfg.get("follow_targets") or [])
        if not targets:
            default = cfg.get("default_join_behavior") or _default_behavior()
            targets = [{"user_id": int(uid), "behavior": default} for uid in (cfg.get("follow_user_ids") or [])]
        if self.action == "remove":
            targets = [item for item in targets if int(item.get("user_id", 0)) != user_id]
            await self.owner_view.bot.database.set_voice_recording_follow_targets(interaction.guild.id, targets)
            cog = _voice_cog(self.owner_view.bot)
            if cog:
                await cog._reconcile_auto_join(interaction.guild)
            await interaction.response.edit_message(embed=discord.Embed(title="Follow Members", description=f"Removed <@{user_id}> from Follow Members."), view=FollowView(self.owner_view.bot))
            return
        await interaction.response.edit_message(
            embed=discord.Embed(title="Follow Member Behavior", description=f"Choose what Mommy should do when she follows <@{user_id}>."),
            view=BehaviorPresetView(self.owner_view.bot, target_kind="member", target_id=user_id, back_page="follow"),
        )


class FollowTargetSelectView(discord.ui.View):
    def __init__(self, bot, *, action: str) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.add_item(FollowTargetSelect(self, action=action))

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await show_follow_page(interaction, self.bot)


class AutoChannelsView(discord.ui.View):
    def __init__(self, bot) -> None:
        super().__init__(timeout=600)
        self.bot = bot

    @discord.ui.button(label="Add / Edit Channel", emoji="➕", style=discord.ButtonStyle.success, row=0)
    async def add(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"):
            return
        await interaction.response.edit_message(embed=discord.Embed(title="Auto-Join Channels", description="Choose a VC, then choose its behavior."), view=AutoChannelSelectView(self.bot, action="edit"))

    @discord.ui.button(label="Remove Channel", emoji="➖", style=discord.ButtonStyle.danger, row=0)
    async def remove(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"):
            return
        await interaction.response.edit_message(embed=discord.Embed(title="Auto-Join Channels • Remove", description="Choose a configured VC to remove."), view=AutoChannelSelectView(self.bot, action="remove"))

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await show_autojoin_page(interaction, self.bot)

    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.success, row=1)
    async def home(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await navigate(interaction, self.bot, "home")


class AutoChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, owner_view, *, action: str) -> None:
        super().__init__(placeholder="Choose a voice channel…", channel_types=[discord.ChannelType.voice], min_values=1, max_values=1)
        self.owner_view = owner_view
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _authorized(interaction, self.owner_view.bot, minimum="admin"):
            return
        channel_id = int(self.values[0].id)
        profile = await self.owner_view.bot.database.get_guild_profile(interaction.guild.id)
        cfg = (profile or {}).get("voice_recording", {})
        targets = list(cfg.get("auto_join_channels") or [])
        if self.action == "remove":
            targets = [item for item in targets if int(item.get("channel_id", 0)) != channel_id]
            await self.owner_view.bot.database.set_voice_recording_auto_join_channels(interaction.guild.id, targets)
            cog = _voice_cog(self.owner_view.bot)
            if cog:
                await cog._reconcile_auto_join(interaction.guild)
            await interaction.response.edit_message(embed=discord.Embed(title="Auto-Join Channels", description=f"Removed <#{channel_id}>."), view=AutoChannelsView(self.owner_view.bot))
            return
        await interaction.response.edit_message(
            embed=discord.Embed(title="Auto-Join Channel Behavior", description=f"Choose what Mommy should do when <#{channel_id}> becomes active."),
            view=BehaviorPresetView(self.owner_view.bot, target_kind="channel", target_id=channel_id, back_page="channels"),
        )


class AutoChannelSelectView(discord.ui.View):
    def __init__(self, bot, *, action: str) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.add_item(AutoChannelSelect(self, action=action))

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await show_auto_channels_page(interaction, self.bot)


def _default_behavior() -> dict[str, bool]:
    return {"automod": True, "ai": True, "record": False, "clips": True, "captions": True}


def _preset_behavior(name: str) -> dict[str, bool]:
    if name == "automod_record":
        return {"automod": True, "ai": True, "record": True, "clips": True, "captions": True}
    if name == "record_only":
        return {"automod": False, "ai": False, "record": True, "clips": False, "captions": False}
    return _default_behavior()


def _behavior_label(behavior: dict | None) -> str:
    b = _default_behavior() | (behavior or {})
    if b == _preset_behavior("automod_record"):
        return "Automod + Record"
    if b == _preset_behavior("record_only"):
        return "Record Only"
    if b == _default_behavior():
        return "Automod Only"
    enabled = [name for key, name in [("automod","Automod"),("ai","AI"),("record","Record"),("clips","Clips"),("captions","Captions")] if b.get(key)]
    return "Custom: " + (", ".join(enabled) if enabled else "listen only")


async def _save_target_behavior(bot, guild: discord.Guild, target_kind: str, target_id: int | None, behavior: dict) -> None:
    if target_kind == "default":
        await bot.database.set_voice_recording_default_join_behavior(guild.id, behavior)
        return
    profile = await bot.database.get_guild_profile(guild.id)
    cfg = (profile or {}).get("voice_recording", {})
    if target_kind == "member":
        targets = list(cfg.get("follow_targets") or [])
        if not targets:
            default = cfg.get("default_join_behavior") or _default_behavior()
            targets = [{"user_id": int(uid), "behavior": default} for uid in (cfg.get("follow_user_ids") or [])]
        targets = [item for item in targets if int(item.get("user_id", 0)) != int(target_id)]
        targets.append({"user_id": int(target_id), "behavior": behavior})
        await bot.database.set_voice_recording_follow_targets(guild.id, targets)
    else:
        targets = list(cfg.get("auto_join_channels") or [])
        targets = [item for item in targets if int(item.get("channel_id", 0)) != int(target_id)]
        targets.append({"channel_id": int(target_id), "behavior": behavior})
        await bot.database.set_voice_recording_auto_join_channels(guild.id, targets)
    cog = _voice_cog(bot)
    if cog:
        await cog._reconcile_auto_join(guild)


class BehaviorPresetView(discord.ui.View):
    def __init__(self, bot, *, target_kind: str, target_id: int | None, back_page: str) -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.target_kind = target_kind
        self.target_id = target_id
        self.back_page = back_page

    async def _save(self, interaction: discord.Interaction, behavior: dict) -> None:
        await _save_target_behavior(self.bot, interaction.guild, self.target_kind, self.target_id, behavior)
        await interaction.response.edit_message(
            embed=discord.Embed(title="Auto-Join Behavior Saved", description=f"Saved **{_behavior_label(behavior)}**."),
            view=AutoJoinView(self.bot),
        )

    @discord.ui.button(label="Automod Only", emoji="🛡️", style=discord.ButtonStyle.primary, row=0)
    async def automod(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._save(interaction, _preset_behavior("automod"))

    @discord.ui.button(label="Automod + Record", emoji="🔴", style=discord.ButtonStyle.danger, row=0)
    async def record(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._save(interaction, _preset_behavior("automod_record"))

    @discord.ui.button(label="Record Only", emoji="🎙️", style=discord.ButtonStyle.secondary, row=0)
    async def record_only(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._save(interaction, _preset_behavior("record_only"))

    @discord.ui.button(label="Custom", emoji="⚙️", style=discord.ButtonStyle.secondary, row=1)
    async def custom(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=discord.Embed(title="Custom Join Behavior", description="Toggle each feature, then press **Save**."),
            view=CustomBehaviorView(self.bot, target_kind=self.target_kind, target_id=self.target_id, back_page=self.back_page),
        )


class CustomBehaviorView(discord.ui.View):
    def __init__(self, bot, *, target_kind: str, target_id: int | None, back_page: str, behavior: dict | None = None) -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.target_kind = target_kind
        self.target_id = target_id
        self.back_page = back_page
        self.behavior = _default_behavior() | (behavior or {})
        self._sync_labels()

    def _sync_labels(self) -> None:
        for item in self.children:
            key = getattr(item, "behavior_key", None)
            if key:
                item.label = f"{getattr(item, 'base_label', key)}: {'ON' if self.behavior[key] else 'OFF'}"
                item.style = discord.ButtonStyle.success if self.behavior[key] else discord.ButtonStyle.secondary

    async def _toggle(self, interaction: discord.Interaction, key: str) -> None:
        self.behavior[key] = not self.behavior[key]
        self._sync_labels()
        await interaction.response.edit_message(embed=discord.Embed(title="Custom Join Behavior", description=f"Current: **{_behavior_label(self.behavior)}**\nToggle features, then press **Save**."), view=self)

    @discord.ui.button(label="Automod", row=0)
    async def automod(self, interaction, button):
        button.behavior_key="automod"; button.base_label="Automod"; await self._toggle(interaction,"automod")
    @discord.ui.button(label="AI Replies", row=0)
    async def ai(self, interaction, button):
        button.behavior_key="ai"; button.base_label="AI Replies"; await self._toggle(interaction,"ai")
    @discord.ui.button(label="Full Recording", row=1)
    async def record(self, interaction, button):
        button.behavior_key="record"; button.base_label="Full Recording"; await self._toggle(interaction,"record")
    @discord.ui.button(label="Rolling Clips", row=1)
    async def clips(self, interaction, button):
        button.behavior_key="clips"; button.base_label="Rolling Clips"; await self._toggle(interaction,"clips")
    @discord.ui.button(label="Live Captions", row=1)
    async def captions(self, interaction, button):
        button.behavior_key="captions"; button.base_label="Live Captions"; await self._toggle(interaction,"captions")

    @discord.ui.button(label="Save", emoji="💾", style=discord.ButtonStyle.success, row=2)
    async def save(self, interaction, _):
        await _save_target_behavior(self.bot, interaction.guild, self.target_kind, self.target_id, self.behavior)
        await interaction.response.edit_message(embed=discord.Embed(title="Auto-Join Behavior Saved", description=f"Saved **{_behavior_label(self.behavior)}**."), view=AutoJoinView(self.bot))


# Assign behavior metadata after discord.py creates the button objects.
_orig_custom_init = CustomBehaviorView.__init__
def _custom_init(self, *args, **kwargs):
    _orig_custom_init(self, *args, **kwargs)
    meta = [("automod","Automod"),("ai","AI Replies"),("record","Full Recording"),("clips","Rolling Clips"),("captions","Live Captions")]
    for child, (key, label) in zip([c for c in self.children if isinstance(c, discord.ui.Button)][:5], meta):
        child.behavior_key = key
        child.base_label = label
    self._sync_labels()
CustomBehaviorView.__init__ = _custom_init


class ActiveSessionView(discord.ui.View):
    def __init__(self, bot) -> None:
        super().__init__(timeout=600)
        self.bot = bot

    async def _toggle(self, interaction, flag: str):
        cog = _voice_cog(self.bot)
        if not cog or not interaction.guild or interaction.guild.id not in cog.sessions:
            await interaction.response.send_message("Mommy isn't in a VC session right now.", ephemeral=True)
            return
        session = cog.sessions[interaction.guild.id]
        attr = {"automod":"automod_enabled","record":"record_enabled"}[flag]
        enabled = not getattr(session, attr)
        await cog.dashboard_set_active_flag(interaction.guild, flag, enabled)
        await show_active_session_page(interaction, self.bot)

    @discord.ui.button(label="Toggle Automod", emoji="🛡️", style=discord.ButtonStyle.primary, row=0)
    async def automod(self, interaction, _): await self._toggle(interaction,"automod")
    @discord.ui.button(label="Toggle Full Recording", emoji="🔴", style=discord.ButtonStyle.danger, row=0)
    async def record(self, interaction, _): await self._toggle(interaction,"record")
    @discord.ui.button(label="Leave VC", emoji="🚪", style=discord.ButtonStyle.secondary, row=1)
    async def leave(self, interaction, _):
        cog = _voice_cog(self.bot)
        await interaction.response.defer(ephemeral=True, thinking=True)
        text = await cog.dashboard_stop(interaction.guild, interaction.user) if cog and interaction.guild else "No active session."
        await interaction.followup.send(text, ephemeral=True)
    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction, _): await show_autojoin_page(interaction,self.bot)
    @discord.ui.button(label="Home", emoji="🏠", style=discord.ButtonStyle.success, row=2)
    async def home(self, interaction, _): await navigate(interaction,self.bot,"home")


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
            profile = await self.parent_view.bot.database.get_guild_profile(interaction.guild.id)
            cfg = (profile or {}).get("voice_recording", {})
            targets = list(cfg.get("auto_join_channels") or [])
            targets = [item for item in targets if int(item.get("channel_id", 0)) != channel.id]
            targets.append({"channel_id": channel.id, "behavior": cfg.get("default_join_behavior") or _default_behavior()})
            await self.parent_view.bot.database.set_voice_recording_auto_join_channels(interaction.guild.id, targets)
            await interaction.response.send_message(f"Added {channel.mention} to Auto-Join Channels.", ephemeral=True)
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
    auto_targets = cfg.get("auto_join_channels") or []
    output = cfg.get("output_channel_id")
    control = cfg.get("control_channel_id")
    caption_routes = cfg.get("caption_routes") or {}
    embed = discord.Embed(
        title="Mommy.exe • Voice",
        description=(
            f"**Control:** {f'<#{control}>' if control else 'not set'}\n"
            f"**Archive/output:** {f'<#{output}>' if output else 'not set'}\n"
            f"**Auto-join VCs:** {len(auto_targets)}\n"
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


async def show_autojoin_page(interaction: discord.Interaction, bot) -> None:
    profile = await bot.database.get_guild_profile(interaction.guild.id) if interaction.guild else None
    cfg = (profile or {}).get("voice_recording", {})
    follows = cfg.get("follow_targets") or []
    if not follows:
        follows = [{"user_id": uid, "behavior": cfg.get("default_join_behavior") or _default_behavior()} for uid in (cfg.get("follow_user_ids") or [])]
    channels = cfg.get("auto_join_channels") or []
    default = cfg.get("default_join_behavior") or _default_behavior()
    cog = _voice_cog(bot)
    active = cog.active_session_status(interaction.guild) if cog and interaction.guild else "No active session."
    embed = discord.Embed(
        title="Mommy.exe • Auto Join",
        description=(
            f"**Followed members:** {len(follows)}\n"
            f"**Auto-join channels:** {len(channels)}\n"
            f"**Default:** {_behavior_label(default)}\n\n"
            "Priority: **Follow Member → Auto-Join Channel**. Within the same type, a target with full recording enabled wins.\n"
            "Mommy can monitor many configured VCs, but can only physically sit in **one VC per server at a time**.\n\n"
            f"{active}"
        ),
    )
    await interaction.response.edit_message(embed=embed, view=AutoJoinView(bot))


async def show_follow_page(interaction: discord.Interaction, bot) -> None:
    profile = await bot.database.get_guild_profile(interaction.guild.id) if interaction.guild else None
    cfg = (profile or {}).get("voice_recording", {})
    targets = cfg.get("follow_targets") or []
    if not targets:
        default = cfg.get("default_join_behavior") or _default_behavior()
        targets = [{"user_id": int(uid), "behavior": default} for uid in (cfg.get("follow_user_ids") or [])]
    lines = []
    for item in targets:
        uid = int(item.get("user_id", 0))
        if uid:
            lines.append(f"• <@{uid}> — **{_behavior_label(item.get('behavior'))}**")
    embed = discord.Embed(title="Mommy.exe • Follow Members", description="\n".join(lines) if lines else "Nobody is followed yet.")
    await interaction.response.edit_message(embed=embed, view=FollowView(bot))


async def show_auto_channels_page(interaction: discord.Interaction, bot) -> None:
    profile = await bot.database.get_guild_profile(interaction.guild.id) if interaction.guild else None
    cfg = (profile or {}).get("voice_recording", {})
    targets = cfg.get("auto_join_channels") or []
    lines = [f"• <#{int(item.get('channel_id'))}> — **{_behavior_label(item.get('behavior'))}**" for item in targets if item.get("channel_id")]
    embed = discord.Embed(title="Mommy.exe • Auto-Join Channels", description="\n".join(lines) if lines else "No auto-join voice channels configured yet.")
    await interaction.response.edit_message(embed=embed, view=AutoChannelsView(bot))


async def show_default_behavior_page(interaction: discord.Interaction, bot) -> None:
    profile = await bot.database.get_guild_profile(interaction.guild.id) if interaction.guild else None
    behavior = ((profile or {}).get("voice_recording", {}) or {}).get("default_join_behavior") or _default_behavior()
    embed = discord.Embed(title="Mommy.exe • Default Join Behavior", description=f"Current default: **{_behavior_label(behavior)}**\n\nThis is used for new/legacy targets unless you choose a specific behavior for that member or channel.")
    await interaction.response.edit_message(embed=embed, view=BehaviorPresetView(bot, target_kind="default", target_id=None, back_page="autojoin"))


async def show_active_session_page(interaction: discord.Interaction, bot) -> None:
    cog = _voice_cog(bot)
    text = cog.active_session_status(interaction.guild) if cog and interaction.guild else "Voice controls unavailable."
    embed = discord.Embed(title="Mommy.exe • Current VC Session", description=text + "\n\nYou can toggle automod or whether the full session is archived when it ends.")
    await interaction.response.edit_message(embed=embed, view=ActiveSessionView(bot))



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
    if page == "home":
        await interaction.response.edit_message(embed=_home_embed(), view=MommyDashboardView(bot))
    elif page == "voice":
        await show_voice_page(interaction, bot)
    elif page == "clips":
        await show_clips_page(interaction, bot)
    elif page == "follow":
        await show_follow_page(interaction, bot)
    elif page == "autojoin":
        await show_autojoin_page(interaction, bot)
    elif page == "channels":
        await show_auto_channels_page(interaction, bot)
    elif page == "active":
        await show_active_session_page(interaction, bot)
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

    @discord.ui.button(label="House Trained Role", emoji="🏠", style=discord.ButtonStyle.secondary, row=2)
    async def house_role(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _authorized(interaction, self.bot, minimum="admin"): return
        await interaction.response.edit_message(
            embed=discord.Embed(title="VC Automod • House Trained Role", description="Choose the role Mommy removes during punishment and restores afterward."),
            view=HouseRoleView(self.bot, back_page="automod"),
        )

    @discord.ui.button(label="Grant / Remove Access", emoji="🎟️", style=discord.ButtonStyle.secondary, row=2)
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
    window = discord.ui.TextInput(label="Window in minutes", default="10", max_length=3)

    def __init__(self, bot) -> None:
        super().__init__(); self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        import uuid
        try: threshold=max(1,int(str(self.threshold))); window=max(1,int(str(self.window)))
        except ValueError:
            await interaction.response.send_message("Threshold/window must be numbers.", ephemeral=True); return
        trigger={"id":uuid.uuid4().hex[:12],"label":str(self.label).strip(),"phrase":str(self.phrase).strip(),"enabled":True,"threshold":threshold,"window_minutes":window,"quote_lenient":True}
        await self.bot.database.push_config_path(interaction.guild.id,"vc_automod.triggers",trigger)
        await interaction.response.send_message(f"Added VC automod trigger **{trigger['label']}**.",ephemeral=True)


class VCTriggerView(discord.ui.View):
    def __init__(self, bot) -> None:
        super().__init__(timeout=600); self.bot=bot
    @discord.ui.button(label="Add Trigger", emoji="➕", style=discord.ButtonStyle.success)
    async def add(self, interaction, _):
        if not await _authorized(interaction,self.bot,minimum="admin"): return
        await interaction.response.send_modal(VCTriggerModal(self.bot))
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
            rows=await cog.history_for(interaction.guild.id,member.id); desc="\n".join(f"• {r.get('trigger_label')} • {'possible quote' if r.get('possible_quote') else 'credible'} • {r.get('created_at').strftime('%m/%d %H:%M')}" for r in rows) or "No stored detections."
            await interaction.response.edit_message(embed=discord.Embed(title=f"VC History • {member.display_name}",description=desc),view=SimpleNavView(self.owner_view.bot,back="automod")); return
        if self.action=="forgive": await cog.forgive(interaction.guild.id,member.id); msg=f"Cleared VC detections for {member.mention}."
        else: msg=await cog.restore_member(member)
        await interaction.response.send_message(msg,ephemeral=True)

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
    desc=(f"**Enabled:** {'yes' if cfg.get('enabled',True) else 'no'}\n**House Trained:** {f'<@&{role_id}>' if role_id else 'not set'}\n**Triggers:** {len(triggers)}\n**Exception roles:** {len(cfg.get('exception_role_ids') or [])}\n**Window:** default 10 minutes • **History:** 7 days\n**Punishment ladder:** 5m → 15m → 1h → 24h\n**Punishment sound:** {str(cfg.get('punishment_sound_mode') or 'tts') if cfg.get('punishment_sound_enabled',True) else 'off'}\n\nPossible quotations are logged but do not count toward automatic punishment.")
    await interaction.response.edit_message(embed=discord.Embed(title="Mommy.exe • VC Automod",description=desc),view=AutomodView(bot))

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
    lines=[f"• **{t.get('label','Trigger')}** — {t.get('threshold',3)} hit(s) / {t.get('window_minutes',10)} min{' • built-in' if t.get('builtin') else ''}" for t in cfg.get('triggers') or []]
    await interaction.response.edit_message(embed=discord.Embed(title="VC Automod • Triggers",description="\n".join(lines) or "No triggers."),view=VCTriggerView(bot))

async def show_exception_roles_page(interaction: discord.Interaction, bot) -> None:
    profile=await bot.database.get_guild_profile(interaction.guild.id) or {}; ids=(profile.get("vc_automod") or {}).get("exception_role_ids") or []; lines=[f"• <@&{int(v)}>" for v in ids]
    await interaction.response.edit_message(embed=discord.Embed(title="VC Automod • Exception Roles",description="\n".join(lines) or "No exception roles. Server owner and bots are always exempt."),view=ExceptionRolesView(bot))

async def show_verification_page(interaction: discord.Interaction, bot) -> None:
    profile=await bot.database.get_guild_profile(interaction.guild.id) or {}; cfg=profile.get("verification") or {}; rid=cfg.get("house_trained_role_id"); cid=cfg.get("rules_channel_id"); mid=cfg.get("rules_message_id")
    desc=(f"**Enabled:** {'yes' if cfg.get('enabled') else 'no'}\n**House Trained:** {f'<@&{rid}>' if rid else 'not set'}\n**Rules channel:** {f'<#{cid}>' if cid else 'not set'}\n**Rules message:** `{mid}`\n**Reaction:** {cfg.get('rules_emoji','✅')}\n\nNew members stay without House Trained until they react to the configured rules post. Removing the reaction later does not remove access.")
    await interaction.response.edit_message(embed=discord.Embed(title="Mommy.exe • Verification",description=desc),view=VerificationView(bot))

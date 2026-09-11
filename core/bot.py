from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord.ext import commands

from core.config import Settings
from database.mongo import MongoDatabase
from services.member_event_renderer import render_member_event_message
from services.daddy_ai import DaddyAI

log = logging.getLogger("daddys-belt.bot")

class DaddysBeltBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents=discord.Intents.default(); intents.guilds=True; intents.members=True; intents.message_content=True; intents.moderation=True; intents.voice_states=True
        super().__init__(command_prefix="!",intents=intents,allowed_mentions=discord.AllowedMentions(everyone=False,roles=False,users=True,replied_user=False))
        self.settings=settings; self.database=MongoDatabase(uri=settings.mongodb_uri,database_name=settings.mongodb_database)
        self._ban_events: dict[tuple[int,int], datetime]={}
        self._global_command_cleanup_done = False
        self.daddy_ai = DaddyAI(self)

    async def setup_hook(self):
        await self.database.connect()
        for extension in ("cogs.dashboard","cogs.scheduled_worker","cogs.triggers","cogs.text_automod"):
            await self.load_extension(extension)

        # Do NOT sync globally here. We mirror the command tree directly into
        # each guild in on_ready/on_guild_join instead. That keeps updates fast
        # without showing duplicate global + guild slash commands in Discord.

    async def on_ready(self):
        if not self.user:return
        log.info("Logged in as %s (%s).",self.user,self.user.id)
        await self.change_presence(activity=discord.CustomActivity(name="reviewing questionable decisions"))
        # Clear Daddy's old GLOBAL slash-command registrations once. Older builds
        # globally registered /record, so Discord can keep showing ghost voice
        # commands even after the cog is removed. We preserve the local command
        # objects, sync an empty global tree to delete the ghosts, then use those
        # commands only for fast guild-scoped syncing.
        if not self._global_command_cleanup_done:
            try:
                local_global_commands = list(self.tree.get_commands(guild=None))
                self.tree.clear_commands(guild=None)
                removed = await self.tree.sync()
                log.info("Daddy global cleanup synced %s command(s); stale global commands removed.", len(removed))
                for command in local_global_commands:
                    try:
                        self.tree.add_command(command)
                    except Exception:
                        log.debug("Daddy command %s was already restored locally.", getattr(command, "name", "?"))
                self._global_command_cleanup_done = True
            except Exception:
                log.exception("Daddy stale global command cleanup failed.")

        for guild in self.guilds:
            await self.database.ensure_guild_profile(guild)
            try:
                self.tree.clear_commands(guild=guild)
                self.tree.copy_global_to(guild=guild)
                guild_synced = await self.tree.sync(guild=guild)
                log.info("Ready-sync: synced %s command(s) to guild %s (%s).", len(guild_synced), guild.name, guild.id)
            except Exception:
                log.exception("Ready-sync failed for guild %s.", guild.id)

    async def on_guild_join(self, guild):
        await self.database.ensure_guild_profile(guild)
        try:
            self.tree.clear_commands(guild=guild)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %s command(s) to newly joined guild %s (%s).", len(synced), guild.name, guild.id)
        except Exception:
            log.exception("Could not sync application commands to newly joined guild %s.", guild.id)
    async def on_guild_remove(self,guild): await self.database.mark_guild_inactive(guild.id)

    async def get_text_channel(self,guild,channel_id):
        cached=guild.get_channel(channel_id)
        if isinstance(cached,(discord.TextChannel,discord.Thread)):return cached
        try:fetched=await self.fetch_channel(channel_id)
        except (discord.NotFound,discord.Forbidden,discord.HTTPException):return None
        return fetched if isinstance(fetched,(discord.TextChannel,discord.Thread)) else None

    @staticmethod
    def choose_event_message(config: dict[str,Any]):
        messages=[m for m in config.get('messages',[]) if m.get('enabled',True) and str(m.get('content','')).strip()]
        if not messages:return None
        mode=config.get('selection_mode','weighted')
        if mode=='fixed':return messages[0]
        if mode=='equal':return random.choice(messages)
        weights=[max(1,int(m.get('weight',1) or 1)) for m in messages]
        return random.choices(messages,weights=weights,k=1)[0]

    async def send_member_event(self,*,event_type,guild,member,moderator=None,reason=None,test=False):
        profile=await self.database.get_guild_profile(guild.id)
        if not profile:return False
        config=profile.get('member_events',{}).get(event_type,{})
        if not config.get('enabled',False) and not test:return False
        channel_id=config.get('channel_id')
        if not channel_id:return False
        message=self.choose_event_message(config)
        if not message:return False
        channel=await self.get_text_channel(guild,int(channel_id))
        if channel is None:return False
        template=str(message.get('content',''))
        rendered=render_member_event_message(template,member=member,guild=guild,moderator=moderator,reason=reason,event_type=event_type)

        # Leave, kick, and ban announcements should always identify the user.
        # If the custom template does not contain a user placeholder, add the
        # member's display name automatically above the configured message.
        identity_placeholders=(
            '{mention}',
            '{username}',
            '{display_name}',
        )
        if event_type in {'goodbye','kick','ban'} and not any(
            placeholder in template for placeholder in identity_placeholders
        ):
            rendered=f"**{getattr(member, 'display_name', member.name)}**\n{rendered}"

        image=str(message.get('image_url') or '').strip()
        content=rendered
        if test:
            content="**Member event test:**\n"+content

        ping_enabled=(
            event_type=='welcome'
            and config.get('ping_member',True)
        )
        template_has_mention=(
            '{mention}' in str(message.get('content',''))
        )

        # Mentions inside embed descriptions are only visual and do not
        # notify the member. Image/GIF welcomes therefore send the actual
        # ping as normal message content outside the embed.
        try:
            if image:
                embed=discord.Embed(description=content)
                embed.set_image(url=image)
                await channel.send(
                    content=member.mention if ping_enabled else None,
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(
                        users=True,
                        roles=False,
                        everyone=False,
                    ),
                )
            else:
                if ping_enabled and not template_has_mention:
                    content=f"{member.mention}\n{content}"
                await channel.send(
                    content,
                    allowed_mentions=discord.AllowedMentions(
                        users=True,
                        roles=False,
                        everyone=False,
                    ),
                )
            return True
        except (discord.Forbidden,discord.HTTPException):
            log.exception("Could not send %s event in guild %s",event_type,guild.id);return False

    async def on_member_join(self,member):
        if await self.send_member_event(event_type='welcome',guild=member.guild,member=member):
            await self.database.increment_stat(member.guild.id,'members_welcomed')

    async def _recent_audit_entry(self,guild,action,user_id):
        try:
            async for entry in guild.audit_logs(limit=8,action=action):
                if getattr(entry.target,'id',None)==user_id and (discord.utils.utcnow()-entry.created_at).total_seconds()<12:return entry
        except (discord.Forbidden,discord.HTTPException):pass
        return None

    async def on_member_ban(self,guild,user):
        self._ban_events[(guild.id,user.id)]=discord.utils.utcnow()
        entry=await self._recent_audit_entry(guild,discord.AuditLogAction.ban,user.id)
        await self.send_member_event(event_type='ban',guild=guild,member=user,moderator=entry.user if entry else None,reason=entry.reason if entry else None)

    async def on_member_remove(self,member):
        await asyncio.sleep(1.5)
        stamp=self._ban_events.get((member.guild.id,member.id))
        if stamp and (discord.utils.utcnow()-stamp).total_seconds()<15:return
        kick=await self._recent_audit_entry(member.guild,discord.AuditLogAction.kick,member.id)
        if kick:
            await self.send_member_event(event_type='kick',guild=member.guild,member=member,moderator=kick.user,reason=kick.reason)
        else:
            await self.send_member_event(event_type='goodbye',guild=member.guild,member=member)


    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if not self.user:
            return

        if message.guild is None and message.content.strip().casefold() == "!daddy":
            from mommy.child_vc import maybe_send_child_appeal_menu
            if await maybe_send_child_appeal_menu(self, message):
                return

        addressed = self.daddy_ai.addressed_text(message, self.user)
        if addressed is not None:
            async with message.channel.typing():
                reply = await self.daddy_ai.reply(message, addressed)
            await message.reply(reply, mention_author=False)
            return

        await self.process_commands(message)

    async def on_command_error(self, ctx, error):
        # Mommy.exe owns !mommy. Daddy sees the same message because both bots
        # share the server, so quietly ignore commands that do not belong to him.
        if isinstance(error, commands.CommandNotFound):
            return
        await super().on_command_error(ctx, error)

    async def close(self): await self.database.close();await super().close()

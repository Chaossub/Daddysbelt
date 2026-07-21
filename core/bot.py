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

log = logging.getLogger("daddys-belt.bot")

class DaddysBeltBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents=discord.Intents.default(); intents.guilds=True; intents.members=True; intents.message_content=True; intents.moderation=True
        super().__init__(command_prefix="!",intents=intents,allowed_mentions=discord.AllowedMentions(everyone=False,roles=False,users=True,replied_user=False))
        self.settings=settings; self.database=MongoDatabase(uri=settings.mongodb_uri,database_name=settings.mongodb_database)
        self._ban_events: dict[tuple[int,int], datetime]={}

    async def setup_hook(self):
        await self.database.connect()
        for extension in ("cogs.dashboard","cogs.scheduled_worker","cogs.triggers","cogs.pokemon_stock"): await self.load_extension(extension)
        if self.settings.development_guild_id:
            guild=discord.Object(id=self.settings.development_guild_id);self.tree.copy_global_to(guild=guild);synced=await self.tree.sync(guild=guild);log.info("Synced %s command(s) to development server %s.",len(synced),guild.id)
        else:
            synced=await self.tree.sync();log.info("Synced %s global command(s).",len(synced))

    async def on_ready(self):
        if not self.user:return
        log.info("Logged in as %s (%s).",self.user,self.user.id)
        await self.change_presence(activity=discord.CustomActivity(name="reviewing questionable decisions"))
        for guild in self.guilds: await self.database.ensure_guild_profile(guild)

    async def on_guild_join(self,guild): await self.database.ensure_guild_profile(guild)
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

    async def close(self): await self.database.close();await super().close()

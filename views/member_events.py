from __future__ import annotations

from typing import Any
import discord

from views.common import AuthorizedView
from views.dashboard import DashboardView, dashboard_embed, BOT_VERSION

EVENT_INFO = {
    "welcome": ("Welcome", "👋"),
    "goodbye": ("Goodbye", "🚪"),
    "kick": ("Kick", "👢"),
    "ban": ("Ban", "🔨"),
}

PLACEHOLDERS = "`{mention}` `{username}` `{display_name}` `{server}` `{member_count}` `{moderator}` `{reason}` `{created_at}` `{joined_at}`"


def event_settings(profile: dict[str, Any], event_type: str) -> dict[str, Any]:
    return profile.get("member_events", {}).get(event_type, {})


def member_events_embed(guild: discord.Guild, profile: dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(title="👥 Member Events", description=f"**Managing:** {discord.utils.escape_markdown(guild.name)}\n\nCreate separate custom randomized messages for joins and departures.")
    for key, (name, emoji) in EVENT_INFO.items():
        cfg = event_settings(profile, key)
        channel = guild.get_channel(cfg.get("channel_id")) if cfg.get("channel_id") else None
        embed.add_field(name=f"{emoji} {name}", value=f"{'🟢 Enabled' if cfg.get('enabled') else '🔴 Disabled'}\nChannel: {channel.mention if channel else 'Not set'}\nMessages: {len(cfg.get('messages', []))}", inline=True)
    embed.set_footer(text=f"Daddy's Belt v{BOT_VERSION}")
    return embed


def event_embed(guild: discord.Guild, profile: dict[str, Any], event_type: str) -> discord.Embed:
    name, emoji = EVENT_INFO[event_type]
    cfg = event_settings(profile, event_type)
    channel = guild.get_channel(cfg.get("channel_id")) if cfg.get("channel_id") else None
    mode_names = {"fixed": "Fixed first message", "equal": "Equal random", "weighted": "Weighted random"}
    embed = discord.Embed(title=f"{emoji} {name} Messages", description=f"{'🟢 Enabled' if cfg.get('enabled') else '🔴 Disabled'}\n**Channel:** {channel.mention if channel else 'Not set'}\n**Selection:** {mode_names.get(cfg.get('selection_mode', 'weighted'), 'Weighted random')}\n**Messages:** {len(cfg.get('messages', []))}")
    if event_type == "welcome": embed.add_field(name="Ping new member", value="Yes" if cfg.get("ping_member", True) else "No")
    embed.add_field(name="Placeholders", value=PLACEHOLDERS, inline=False)
    embed.set_footer(text="Weights are used only in Weighted Random mode.")
    return embed

async def show_home(interaction, view):
    profile = await view.bot.database.ensure_guild_profile(interaction.guild)
    await interaction.response.edit_message(embed=member_events_embed(interaction.guild, profile), view=MemberEventsView(bot=view.bot, guild_id=view.guild_id, requester_id=view.requester_id, database_connected=view.database_connected))

async def show_event(interaction, view, event_type):
    profile = await view.bot.database.ensure_guild_profile(interaction.guild)
    await interaction.response.edit_message(embed=event_embed(interaction.guild, profile, event_type), view=EventConfigView(bot=view.bot, guild_id=view.guild_id, requester_id=view.requester_id, database_connected=view.database_connected, event_type=event_type))

class MemberEventsView(AuthorizedView):
    def __init__(self, *, bot, guild_id, requester_id, database_connected):
        super().__init__(guild_id=guild_id, requester_id=requester_id); self.bot=bot; self.database_connected=database_connected
    @discord.ui.button(label="Welcome", emoji="👋", style=discord.ButtonStyle.success, row=0)
    async def welcome(self,i,b): await show_event(i,self,"welcome")
    @discord.ui.button(label="Goodbye", emoji="🚪", style=discord.ButtonStyle.primary, row=0)
    async def goodbye(self,i,b): await show_event(i,self,"goodbye")
    @discord.ui.button(label="Kick", emoji="👢", style=discord.ButtonStyle.danger, row=1)
    async def kick(self,i,b): await show_event(i,self,"kick")
    @discord.ui.button(label="Ban", emoji="🔨", style=discord.ButtonStyle.danger, row=1)
    async def ban(self,i,b): await show_event(i,self,"ban")
    @discord.ui.button(label="Dashboard", emoji="🏠", style=discord.ButtonStyle.secondary, row=2)
    async def dash(self,i,b): await i.response.edit_message(embed=dashboard_embed(i.guild,database_connected=self.database_connected),view=DashboardView(bot=self.bot,guild_id=self.guild_id,requester_id=self.requester_id,database_connected=self.database_connected))

class EventChannelSelect(discord.ui.ChannelSelect):
    def __init__(self,parent):
        super().__init__(placeholder="Choose the channel", channel_types=[discord.ChannelType.text,discord.ChannelType.news], min_values=1,max_values=1); self.parent_ref=parent
    async def callback(self,i):
        await self.parent_ref.bot.database.set_member_event_channel(self.parent_ref.guild_id,self.parent_ref.event_type,self.values[0].id); await show_event(i,self.parent_ref,self.parent_ref.event_type)

class ChannelPickerView(AuthorizedView):
    def __init__(self,*,bot,guild_id,requester_id,database_connected,event_type):
        super().__init__(guild_id=guild_id,requester_id=requester_id); self.bot=bot; self.database_connected=database_connected; self.event_type=event_type; self.add_item(EventChannelSelect(self))
    @discord.ui.button(label="Back",emoji="◀️",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,b): await show_event(i,self,self.event_type)

class MessageModal(discord.ui.Modal):
    def __init__(self,*,parent,message=None):
        super().__init__(title="Edit Message" if message else "Add Message"); self.parent_ref=parent; self.message_id=str(message['_id']) if message else None
        self.content=discord.ui.TextInput(label="Message",style=discord.TextStyle.paragraph,default=str(message.get('content','')) if message else None,placeholder="Welcome {mention} to {server}!",max_length=1900)
        self.image=discord.ui.TextInput(label="Optional image or GIF URL",required=False,default=str(message.get('image_url') or '') if message else None,max_length=500)
        self.weight=discord.ui.TextInput(label="Random weight (1-100)",default=str(message.get('weight',1)) if message else "1",max_length=3)
        self.add_item(self.content); self.add_item(self.image); self.add_item(self.weight)
    async def on_submit(self,i):
        try: weight=int(str(self.weight.value))
        except ValueError: return await i.response.send_message("Weight must be a whole number from 1 to 100.",ephemeral=True)
        if not 1<=weight<=100: return await i.response.send_message("Weight must be from 1 to 100.",ephemeral=True)
        kwargs=dict(content=str(self.content.value),image_url=str(self.image.value).strip() or None,weight=weight)
        if self.message_id: await self.parent_ref.bot.database.update_member_event_message(self.parent_ref.guild_id,self.parent_ref.event_type,self.message_id,updated_by=i.user.id,**kwargs)
        else: await self.parent_ref.bot.database.add_member_event_message(self.parent_ref.guild_id,self.parent_ref.event_type,created_by=i.user.id,**kwargs)
        await show_event(i,self.parent_ref,self.parent_ref.event_type)

class ModeSelect(discord.ui.Select):
    def __init__(self,parent):
        super().__init__(placeholder="Choose selection style",options=[discord.SelectOption(label="Weighted Random",value="weighted",emoji="🎲",description="Higher weights appear more often"),discord.SelectOption(label="Equal Random",value="equal",emoji="🔀",description="Every message has the same chance"),discord.SelectOption(label="Fixed First Message",value="fixed",emoji="📌",description="Always use the first enabled message")]); self.parent_ref=parent
    async def callback(self,i): await self.parent_ref.bot.database.set_member_event_mode(self.parent_ref.guild_id,self.parent_ref.event_type,self.values[0]); await show_event(i,self.parent_ref,self.parent_ref.event_type)

class ModePickerView(AuthorizedView):
    def __init__(self,**kw):
        event_type=kw.pop('event_type'); super().__init__(guild_id=kw['guild_id'],requester_id=kw['requester_id']); self.bot=kw['bot'];self.database_connected=kw['database_connected'];self.event_type=event_type;self.add_item(ModeSelect(self))
    @discord.ui.button(label="Back",emoji="◀️",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,b): await show_event(i,self,self.event_type)

class MessageSelect(discord.ui.Select):
    def __init__(self,parent,messages):
        opts=[]
        for n,m in enumerate(messages[:25],1):
            text=str(m.get('content','')).replace('\n',' ')[:70] or '(empty)'; opts.append(discord.SelectOption(label=f"{n}. {text}"[:100],value=str(m['_id']),description=f"Weight: {m.get('weight',1)}"))
        super().__init__(placeholder="Select a message",options=opts);self.parent_ref=parent
    async def callback(self,i): self.parent_ref.selected=self.values[0]; await i.response.send_message("Selected.",ephemeral=True)

class MessageManagerView(AuthorizedView):
    def __init__(self,*,bot,guild_id,requester_id,database_connected,event_type,messages):
        super().__init__(guild_id=guild_id,requester_id=requester_id);self.bot=bot;self.database_connected=database_connected;self.event_type=event_type;self.messages=messages;self.selected=None;self.add_item(MessageSelect(self,messages))
    def chosen(self): return next((m for m in self.messages if str(m['_id'])==self.selected),None)
    @discord.ui.button(label="Edit Selected",emoji="✏️",style=discord.ButtonStyle.primary,row=1)
    async def edit(self,i,b):
        m=self.chosen()
        if not m:return await i.response.send_message("Select a message first.",ephemeral=True)
        await i.response.send_modal(MessageModal(parent=self,message=m))
    @discord.ui.button(label="Delete Selected",emoji="🗑️",style=discord.ButtonStyle.danger,row=1)
    async def delete(self,i,b):
        if not self.selected:return await i.response.send_message("Select a message first.",ephemeral=True)
        await self.bot.database.delete_member_event_message(self.guild_id,self.event_type,self.selected); await show_event(i,self,self.event_type)
    @discord.ui.button(label="Back",emoji="◀️",style=discord.ButtonStyle.secondary,row=2)
    async def back(self,i,b): await show_event(i,self,self.event_type)

class EventConfigView(AuthorizedView):
    def __init__(self,*,bot,guild_id,requester_id,database_connected,event_type):
        super().__init__(guild_id=guild_id,requester_id=requester_id);self.bot=bot;self.database_connected=database_connected;self.event_type=event_type
        if event_type!='welcome': self.remove_item(self.ping)
    @discord.ui.button(label="Enable/Disable",emoji="🔌",style=discord.ButtonStyle.success,row=0)
    async def toggle(self,i,b):
        p=await self.bot.database.ensure_guild_profile(i.guild);cfg=event_settings(p,self.event_type);await self.bot.database.set_member_event_enabled(self.guild_id,self.event_type,not cfg.get('enabled',False));await show_event(i,self,self.event_type)
    @discord.ui.button(label="Channel",emoji="📢",style=discord.ButtonStyle.primary,row=0)
    async def channel(self,i,b): await i.response.edit_message(embed=discord.Embed(title="Choose Channel",description="Select where these messages should be sent."),view=ChannelPickerView(bot=self.bot,guild_id=self.guild_id,requester_id=self.requester_id,database_connected=self.database_connected,event_type=self.event_type))
    @discord.ui.button(label="Add Message",emoji="➕",style=discord.ButtonStyle.primary,row=0)
    async def add(self,i,b): await i.response.send_modal(MessageModal(parent=self))
    @discord.ui.button(label="Manage Messages",emoji="📝",style=discord.ButtonStyle.secondary,row=1)
    async def manage(self,i,b):
        p=await self.bot.database.ensure_guild_profile(i.guild);msgs=event_settings(p,self.event_type).get('messages',[])
        if not msgs:return await i.response.send_message("Add a message first.",ephemeral=True)
        await i.response.edit_message(embed=discord.Embed(title="Manage Messages",description="Select a message, then edit or delete it."),view=MessageManagerView(bot=self.bot,guild_id=self.guild_id,requester_id=self.requester_id,database_connected=self.database_connected,event_type=self.event_type,messages=msgs))
    @discord.ui.button(label="Random Mode",emoji="🎲",style=discord.ButtonStyle.secondary,row=1)
    async def mode(self,i,b): await i.response.edit_message(embed=discord.Embed(title="Message Selection",description="Choose how the bot picks a message."),view=ModePickerView(bot=self.bot,guild_id=self.guild_id,requester_id=self.requester_id,database_connected=self.database_connected,event_type=self.event_type))
    @discord.ui.button(label="Ping New User",emoji="🔔",style=discord.ButtonStyle.secondary,row=1)
    async def ping(self,i,b):
        p=await self.bot.database.ensure_guild_profile(i.guild);enabled=event_settings(p,'welcome').get('ping_member',True);await self.bot.database.set_welcome_ping(self.guild_id,not enabled);await show_event(i,self,'welcome')
    @discord.ui.button(label="Test",emoji="🧪",style=discord.ButtonStyle.primary,row=2)
    async def test(self,i,b):
        await i.response.defer(ephemeral=True)
        ok=await self.bot.send_member_event(event_type=self.event_type,guild=i.guild,member=i.user,moderator=i.user,reason="Test event",test=True)
        await i.followup.send("Test sent." if ok else "Set a channel, enable the event, and add at least one message first.",ephemeral=True)
    @discord.ui.button(label="Back",emoji="◀️",style=discord.ButtonStyle.secondary,row=2)
    async def back(self,i,b): await show_home(i,self)

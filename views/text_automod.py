from __future__ import annotations

import uuid

import discord

from views.common import AuthorizedView


def text_automod_embed(guild: discord.Guild, profile: dict) -> discord.Embed:
    cfg = profile.get("text_automod") or {}
    triggers = cfg.get("triggers") or []
    lines = [
        f"• **{t.get('label') or t.get('phrase','Trigger')}** — {t.get('threshold',1)} hit(s) / {t.get('window_minutes',10)} min → {t.get('timeout_minutes',5)}m timeout"
        for t in triggers
    ]
    embed = discord.Embed(
        title="🛡️ Daddy's Belt • Text Automod",
        description=(
            f"**Enabled:** {'yes' if cfg.get('enabled') else 'no'}\n"
            f"**Exception roles:** {len(cfg.get('exception_role_ids') or [])}\n\n"
            + ("\n".join(lines) if lines else "No custom text automod triggers yet.")
        ),
    )
    embed.set_footer(text="Daddy handles text. Mommy.exe handles VC.")
    return embed


class AddTextTriggerModal(discord.ui.Modal, title="Add Text Automod Trigger"):
    label = discord.ui.TextInput(label="Name", placeholder="Example: scam phrase", max_length=60)
    phrase = discord.ui.TextInput(label="Word or phrase", max_length=180)
    threshold = discord.ui.TextInput(label="Hits before timeout", default="1", max_length=2)
    window = discord.ui.TextInput(label="Window in minutes", default="10", max_length=3)
    timeout = discord.ui.TextInput(label="Timeout in minutes", default="5", max_length=4)

    def __init__(self, view) -> None:
        super().__init__(); self.parent_view=view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            threshold=max(1,int(str(self.threshold))); window=max(1,int(str(self.window))); timeout=max(1,int(str(self.timeout)))
        except ValueError:
            await interaction.response.send_message("Threshold, window, and timeout must be numbers.", ephemeral=True); return
        trigger={"id":uuid.uuid4().hex[:12],"label":str(self.label).strip(),"phrase":str(self.phrase).strip(),"threshold":threshold,"window_minutes":window,"timeout_minutes":timeout,"enabled":True}
        await self.parent_view.bot.database.push_config_path(interaction.guild.id,"text_automod.triggers",trigger)
        await interaction.response.send_message(f"Added text automod trigger **{trigger['label']}**.",ephemeral=True)


class RemoveTextTriggerSelect(discord.ui.Select):
    def __init__(self, parent, triggers) -> None:
        self.parent=parent
        options=[discord.SelectOption(label=str(t.get("label") or t.get("phrase") or "Trigger")[:100],value=str(t.get("id"))) for t in triggers][:25]
        super().__init__(placeholder="Choose trigger…",options=options or [discord.SelectOption(label="No triggers",value="none")])
    async def callback(self,interaction):
        if self.values[0]=="none": await interaction.response.send_message("There are no triggers to remove.",ephemeral=True); return
        await self.parent.bot.database.pull_config_path(interaction.guild.id,"text_automod.triggers",{"id":self.values[0]})
        await interaction.response.send_message("Removed that text automod trigger.",ephemeral=True)

class RemoveTextTriggerView(AuthorizedView):
    def __init__(self,*,bot,guild_id,requester_id,database_connected,triggers):
        super().__init__(guild_id=guild_id,requester_id=requester_id,minimum_access="admin"); self.bot=bot; self.database_connected=database_connected; self.add_item(RemoveTextTriggerSelect(self,triggers))
    @discord.ui.button(label="Back",emoji="⬅️",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,interaction,_): await _show(interaction,self)

class TextExceptionRoleSelect(discord.ui.RoleSelect):
    def __init__(self,parent,action): super().__init__(placeholder="Choose role…",min_values=1,max_values=1); self.parent=parent; self.action=action
    async def callback(self,interaction):
        role=self.values[0]; profile=await self.parent.bot.database.get_guild_profile(interaction.guild.id) or {}; ids=[int(v) for v in (profile.get("text_automod") or {}).get("exception_role_ids") or []]
        if self.action=="add" and role.id not in ids: ids.append(role.id)
        if self.action=="remove": ids=[v for v in ids if v!=role.id]
        await self.parent.bot.database.set_config_path(interaction.guild.id,"text_automod.exception_role_ids",ids)
        await interaction.response.send_message(f"Updated text automod exception for {role.mention}.",ephemeral=True)

class TextExceptionPickView(AuthorizedView):
    def __init__(self,*,bot,guild_id,requester_id,database_connected,action):
        super().__init__(guild_id=guild_id,requester_id=requester_id,minimum_access="admin"); self.bot=bot; self.database_connected=database_connected; self.add_item(TextExceptionRoleSelect(self,action))
    @discord.ui.button(label="Back",emoji="⬅️",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,interaction,_): await _show(interaction,self)

async def _show(interaction, view):
    profile=await view.bot.database.get_guild_profile(interaction.guild.id) or {}
    await interaction.response.edit_message(embed=text_automod_embed(interaction.guild,profile),view=TextAutomodView(bot=view.bot,guild_id=view.guild_id,requester_id=view.requester_id,database_connected=view.database_connected))

class TextAutomodView(AuthorizedView):
    def __init__(self,*,bot,guild_id,requester_id,database_connected):
        super().__init__(guild_id=guild_id,requester_id=requester_id,minimum_access="moderator"); self.bot=bot; self.database_connected=database_connected

    @discord.ui.button(label="Enable / Disable",emoji="🛡️",style=discord.ButtonStyle.primary,row=0)
    async def toggle(self,interaction,_):
        profile=await self.bot.database.get_guild_profile(interaction.guild.id) or {}; cur=bool((profile.get("text_automod") or {}).get("enabled",False)); await self.bot.database.set_config_path(interaction.guild.id,"text_automod.enabled",not cur); await _show(interaction,self)
    @discord.ui.button(label="Add Trigger",emoji="➕",style=discord.ButtonStyle.success,row=0)
    async def add(self,interaction,_): await interaction.response.send_modal(AddTextTriggerModal(self))
    @discord.ui.button(label="Remove Trigger",emoji="➖",style=discord.ButtonStyle.danger,row=0)
    async def remove(self,interaction,_):
        profile=await self.bot.database.get_guild_profile(interaction.guild.id) or {}; await interaction.response.edit_message(embed=discord.Embed(title="Remove Text Automod Trigger"),view=RemoveTextTriggerView(bot=self.bot,guild_id=self.guild_id,requester_id=self.requester_id,database_connected=self.database_connected,triggers=(profile.get("text_automod") or {}).get("triggers") or []))
    @discord.ui.button(label="Add Exception Role",emoji="➕",style=discord.ButtonStyle.secondary,row=1)
    async def add_exception(self,interaction,_): await interaction.response.edit_message(embed=discord.Embed(title="Add Text Automod Exception"),view=TextExceptionPickView(bot=self.bot,guild_id=self.guild_id,requester_id=self.requester_id,database_connected=self.database_connected,action="add"))
    @discord.ui.button(label="Remove Exception Role",emoji="➖",style=discord.ButtonStyle.secondary,row=1)
    async def rem_exception(self,interaction,_): await interaction.response.edit_message(embed=discord.Embed(title="Remove Text Automod Exception"),view=TextExceptionPickView(bot=self.bot,guild_id=self.guild_id,requester_id=self.requester_id,database_connected=self.database_connected,action="remove"))
    @discord.ui.button(label="Back",emoji="⬅️",style=discord.ButtonStyle.secondary,row=2)
    async def back(self,interaction,_):
        from views.dashboard import DashboardView,dashboard_embed
        await interaction.response.edit_message(embed=dashboard_embed(interaction.guild,database_connected=self.database_connected),view=DashboardView(bot=self.bot,guild_id=self.guild_id,requester_id=self.requester_id,database_connected=self.database_connected))
    @discord.ui.button(label="Home",emoji="🏠",style=discord.ButtonStyle.primary,row=2)
    async def home(self,interaction,_): await self.back(interaction,_)

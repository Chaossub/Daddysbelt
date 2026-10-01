from __future__ import annotations

import random
import time
from datetime import timedelta

import discord
from discord.ext import commands


SHUT_UP_TIMEOUT_MINUTES = 5
SHUT_UP_SECONDS = SHUT_UP_TIMEOUT_MINUTES * 60


class TriggerListener(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.cooldowns: dict[tuple[int, str], float] = {}

        # (guild_id, person_who_said_shut_up) -> (timed_out_member_id, expires_at)
        # This lets the same person say "sorry" for a 50% chance to undo it.
        self.shut_up_targets: dict[tuple[int, int], tuple[int, float]] = {}

    @staticmethod
    def matches(
        content: str,
        phrase: str,
        match_type: str,
        *,
        case_sensitive: bool,
    ) -> bool:
        if not case_sensitive:
            content = content.lower()
            phrase = phrase.lower()

        if match_type == "exact":
            return content.strip() == phrase.strip()
        if match_type == "starts":
            return content.startswith(phrase)
        if match_type == "ends":
            return content.endswith(phrase)

        return phrase in content

    @staticmethod
    def is_admin_or_owner(member: discord.Member) -> bool:
        return (
            member.id == member.guild.owner_id
            or member.guild_permissions.administrator
            or member.guild_permissions.moderate_members
        )

    async def handle_shut_up(self, message: discord.Message) -> bool:
        """Handle the public `shut up @member` timeout command."""
        content = message.content.strip().lower()
        if not content.startswith("shut up"):
            return False

        # A real user mention is required. Plain text names do nothing.
        targets = [member for member in message.mentions if not member.bot]
        if not targets:
            return False

        target = targets[0]
        actor = message.author
        assert isinstance(actor, discord.Member)

        if target.id == actor.id:
            await message.reply(
                "You cannot tell yourself to shut up. Nice try though 💀",
                mention_author=False,
            )
            return True

        if target.id == message.guild.owner_id:
            await message.reply(
                "I can't timeout the server owner. They own the belt factory.",
                mention_author=False,
            )
            return True

        bot_member = message.guild.me
        if bot_member is None:
            return True

        if target.top_role >= bot_member.top_role:
            await message.reply(
                f"I can't timeout {target.mention} because their role is above mine.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True

        try:
            await target.timeout(
                timedelta(minutes=SHUT_UP_TIMEOUT_MINUTES),
                reason=f"Shut up command used by {actor} ({actor.id})",
            )
        except discord.Forbidden:
            await message.reply(
                "I don't have permission to timeout that person. Make sure my role has **Moderate Members** and is above their role.",
                mention_author=False,
            )
            return True
        except discord.HTTPException:
            await message.reply(
                "Discord wouldn't let me apply that timeout. Try again in a moment.",
                mention_author=False,
            )
            return True

        self.shut_up_targets[(message.guild.id, actor.id)] = (
            target.id,
            time.monotonic() + SHUT_UP_SECONDS,
        )

        await message.channel.send(
            f"🔇 {target.mention} has been told to shut up for **5 minutes** by {actor.mention}.\n"
            f"{actor.mention} can say **sorry** for a 50% chance to free them early.",
            allowed_mentions=discord.AllowedMentions(
                users=True,
                roles=False,
                everyone=False,
            ),
        )
        return True

    async def handle_sorry(self, message: discord.Message) -> bool:
        """Give the original caller a 50% chance to undo their timeout."""
        if message.content.strip().lower() not in {"sorry", "i'm sorry", "im sorry"}:
            return False

        actor = message.author
        assert isinstance(actor, discord.Member)
        key = (message.guild.id, actor.id)
        saved = self.shut_up_targets.get(key)
        if saved is None:
            return False

        target_id, expires_at = saved
        if time.monotonic() > expires_at:
            self.shut_up_targets.pop(key, None)
            return False

        target = message.guild.get_member(target_id)
        if target is None:
            self.shut_up_targets.pop(key, None)
            return True

        # One apology attempt per shut-up command.
        self.shut_up_targets.pop(key, None)

        if random.random() >= 0.5:
            await message.reply(
                f"❌ Apology rejected. {target.mention} stays muted. The belt has spoken.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True

        try:
            await target.timeout(
                None,
                reason=f"Successful apology roll by {actor} ({actor.id})",
            )
        except (discord.Forbidden, discord.HTTPException):
            await message.reply(
                "The apology worked, but Discord wouldn't let me remove the timeout.",
                mention_author=False,
            )
            return True

        await message.channel.send(
            f"✅ The apology worked! {target.mention} has been released early.",
            allowed_mentions=discord.AllowedMentions(
                users=True,
                roles=False,
                everyone=False,
            ),
        )
        return True

    async def handle_admin_unmute(self, message: discord.Message) -> bool:
        """Allow the owner/admins/moderators to always remove a timeout."""
        content = message.content.strip().lower()
        if not (
            content.startswith("unmute")
            or content.startswith("untimeout")
        ):
            return False

        actor = message.author
        assert isinstance(actor, discord.Member)
        if not self.is_admin_or_owner(actor):
            return False

        targets = [member for member in message.mentions if not member.bot]
        if not targets:
            return False

        target = targets[0]
        try:
            await target.timeout(
                None,
                reason=f"Timeout removed by {actor} ({actor.id})",
            )
        except discord.Forbidden:
            await message.reply(
                "I don't have permission to remove that timeout.",
                mention_author=False,
            )
            return True
        except discord.HTTPException:
            await message.reply(
                "Discord wouldn't let me remove that timeout right now.",
                mention_author=False,
            )
            return True

        # Remove any saved apology records pointing to this member.
        stale_keys = [
            key
            for key, (member_id, _) in self.shut_up_targets.items()
            if key[0] == message.guild.id and member_id == target.id
        ]
        for key in stale_keys:
            self.shut_up_targets.pop(key, None)

        await message.channel.send(
            f"🔊 {target.mention} has been completely unmuted by {actor.mention}.",
            allowed_mentions=discord.AllowedMentions(
                users=True,
                roles=False,
                everyone=False,
            ),
        )
        return True

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message,
    ) -> None:
        if (
            message.guild is None
            or message.author.bot
            or not message.content
            or not isinstance(message.author, discord.Member)
        ):
            return

        # Built-in unhinged moderation commands work for everyone and do not
        # depend on the configurable trigger system being enabled.
        if await self.handle_admin_unmute(message):
            return
        if await self.handle_sorry(message):
            return
        if await self.handle_shut_up(message):
            return

        profile = await self.bot.database.get_guild_profile(
            message.guild.id
        )
        if not profile:
            return

        now = time.monotonic()

        for trigger in profile.get("triggers", []):
            if not trigger.get("enabled", True):
                continue

            saved_channel_id = trigger.get("channel_id")
            if (
                saved_channel_id is not None
                and int(saved_channel_id) != message.channel.id
            ):
                continue

            phrase = str(trigger.get("phrase", "")).strip()
            if not phrase:
                continue

            if not self.matches(
                message.content,
                phrase,
                str(trigger.get("match_type", "contains")),
                case_sensitive=bool(
                    trigger.get("case_sensitive", False)
                ),
            ):
                continue

            chance_percent = max(
                1,
                min(
                    100,
                    int(trigger.get("chance_percent", 100)),
                ),
            )

            if random.randint(1, 100) > chance_percent:
                continue

            trigger_id = str(trigger["_id"])
            cooldown_key = (message.guild.id, trigger_id)
            cooldown = max(
                0,
                int(trigger.get("cooldown_seconds", 0)),
            )

            last_used = self.cooldowns.get(cooldown_key, 0.0)
            if cooldown and now - last_used < cooldown:
                continue

            responses = [
                str(response).strip()
                for response in trigger.get("responses", [])
                if str(response).strip()
            ]
            if not responses:
                continue

            response = random.choice(responses)
            response = (
                response
                .replace("{mention}", message.author.mention)
                .replace("{username}", message.author.name)
                .replace(
                    "{display_name}",
                    message.author.display_name,
                )
                .replace("{server}", message.guild.name)
                .replace("{channel}", message.channel.mention)
            )

            if trigger.get("ping_author", False):
                response = f"{message.author.mention}\n{response}"

            try:
                await message.channel.send(
                    response[:2000],
                    allowed_mentions=discord.AllowedMentions(
                        users=True,
                        roles=False,
                        everyone=False,
                    ),
                )
            except (
                discord.Forbidden,
                discord.HTTPException,
            ):
                continue

            self.cooldowns[cooldown_key] = now
            await self.bot.database.record_trigger_fired(
                message.guild.id,
                trigger_id,
            )

            # Fire only one trigger per message.
            break


async def setup(bot) -> None:
    await bot.add_cog(TriggerListener(bot))

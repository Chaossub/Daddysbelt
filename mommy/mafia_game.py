from __future__ import annotations

import asyncio
import logging
import random
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

import discord

log = logging.getLogger("mommy-exe.mafia")

Role = Literal["mafia", "doctor", "detective", "vigilante", "villager"]
Phase = Literal["lobby", "night", "day", "vote", "finished"]

ROLE_NAMES: dict[Role, str] = {
    "mafia": "🔪 Mafia",
    "doctor": "🩺 Doctor",
    "detective": "🔍 Detective",
    "vigilante": "🔫 Vigilante",
    "villager": "👤 Villager",
}

ROLE_DESCRIPTIONS: dict[Role, str] = {
    "mafia": "Choose someone to eliminate each night. You win when Mafia controls the town.",
    "doctor": "Protect one living player each night. If Mafia targets them, they survive.",
    "detective": "Investigate one living player each night and learn whether they are Mafia.",
    "vigilante": "You have 2 shots. Shoot at night or skip. If you kill Town, guilt kills you the next morning.",
    "villager": "No night action. Talk, investigate socially, and vote out the Mafia.",
}


@dataclass(slots=True)
class MafiaRules:
    discussion_seconds: int = 90
    night_seconds: int = 60
    vote_seconds: int = 60
    reveal_roles: bool = True
    anonymous_votes: bool = False
    vigilante_mode: Literal["auto", "on", "off"] = "auto"
    bot_fill_mode: Literal["off", "minimum", "custom"] = "minimum"
    bot_fill_count: int = 2


@dataclass(slots=True)
class MafiaPlayer:
    user_id: int
    name: str
    role: Role = "villager"
    alive: bool = True
    vigilante_shots: int = 2
    guilt_pending: bool = False
    is_bot: bool = False
    bot_personality: str = "balanced"
    known_mafia: set[int] = field(default_factory=set)
    suspicion: dict[int, float] = field(default_factory=dict)


class MafiaSession:
    def __init__(self, bot, guild: discord.Guild, channel: discord.TextChannel, host: discord.Member):
        self.bot = bot
        self.guild = guild
        self.channel = channel
        self.host_id = host.id
        self.players: dict[int, MafiaPlayer] = {host.id: MafiaPlayer(host.id, host.display_name)}
        self.rules = MafiaRules()
        self.phase: Phase = "lobby"
        self.day_number = 0
        self.night_number = 0
        self.message: discord.Message | None = None
        self.phase_token = ""
        self.phase_task: asyncio.Task | None = None
        self.night_actions: dict[str, dict[int, int | None]] = {
            "mafia": {}, "doctor": {}, "detective": {}, "vigilante": {}
        }
        self.day_votes: dict[int, int] = {}
        self.eliminated_this_phase: list[int] = []
        self.bot_serial = 0
        self.transition_lock = asyncio.Lock()

    def living(self) -> list[MafiaPlayer]:
        return [p for p in self.players.values() if p.alive]

    def living_ids(self) -> list[int]:
        return [p.user_id for p in self.living()]

    def display(self, user_id: int) -> str:
        p = self.players.get(user_id)
        if p and p.is_bot:
            return f"🤖 **{p.name}**"
        return f"<@{user_id}>"

    def bot_players(self) -> list[MafiaPlayer]:
        return [p for p in self.players.values() if p.is_bot]

    def human_players(self) -> list[MafiaPlayer]:
        return [p for p in self.players.values() if not p.is_bot]

    def remove_filler_bots(self) -> None:
        for uid in [p.user_id for p in self.bot_players()]:
            self.players.pop(uid, None)

    def desired_bot_count(self) -> int:
        humans = len(self.human_players())
        mode = self.rules.bot_fill_mode
        if mode == "off":
            return 0
        if mode == "minimum":
            return max(0, 4 - humans)
        return max(0, min(8, int(self.rules.bot_fill_count)))

    def apply_bot_fill(self) -> None:
        self.remove_filler_bots()
        names = [
            ("Mommy.exe", "chaotic"),
            ("Daddy's Belt", "aggressive"),
            ("Suspicious Steve", "paranoid"),
            ("Mildred", "cautious"),
            ("Chairman Meow", "chaotic"),
            ("Definitely Human", "balanced"),
            ("The Intern", "cautious"),
            ("Crime Goblin", "aggressive"),
        ]
        for i in range(self.desired_bot_count()):
            self.bot_serial += 1
            uid = -100000 - self.bot_serial
            name, personality = names[i % len(names)]
            self.players[uid] = MafiaPlayer(uid, name, is_bot=True, bot_personality=personality)

    def bot_visible_ids(self, bot_player: MafiaPlayer) -> list[int]:
        """Only public/already-known players. Never exposes hidden role state."""
        return [p.user_id for p in self.living() if p.user_id != bot_player.user_id]

    def bot_pick_target(self, bot_player: MafiaPlayer, *, exclude: set[int] | None = None) -> int | None:
        exclude = set(exclude or set())
        candidates = [uid for uid in self.bot_visible_ids(bot_player) if uid not in exclude]
        if not candidates:
            return None
        # Bots reason from their own suspicion map only. Unknown players start neutral.
        weights = []
        for uid in candidates:
            score = bot_player.suspicion.get(uid, 0.0)
            if bot_player.bot_personality == "paranoid":
                score += random.uniform(-0.2, 0.8)
            elif bot_player.bot_personality == "aggressive":
                score += random.uniform(0.0, 0.5)
            elif bot_player.bot_personality == "chaotic":
                score += random.uniform(-0.8, 0.8)
            else:
                score += random.uniform(-0.25, 0.25)
            weights.append(max(0.05, 1.0 + score))
        return random.choices(candidates, weights=weights, k=1)[0]

    def bot_view(self, bot_player: MafiaPlayer) -> dict:
        """Sanitized role view used by bot decisions. Hidden roles never leave here."""
        view = {
            "self_id": bot_player.user_id,
            "role": bot_player.role,
            "alive": self.living_ids(),
            "dead": [p.user_id for p in self.players.values() if not p.alive],
            "known_mafia": sorted(bot_player.known_mafia),
            "suspicion": dict(bot_player.suspicion),
            "day": self.day_number,
            "night": self.night_number,
        }
        return view

    def mafia_alive(self) -> list[MafiaPlayer]:
        return [p for p in self.living() if p.role == "mafia"]

    def town_alive(self) -> list[MafiaPlayer]:
        return [p for p in self.living() if p.role != "mafia"]

    def role_reveal(self, player: MafiaPlayer) -> str:
        if self.rules.reveal_roles:
            return f" They were **{ROLE_NAMES[player.role]}**."
        return " Their role remains a mystery."

    def role_has_night_action(self, player: MafiaPlayer) -> bool:
        if player.role in {"mafia", "doctor", "detective"}:
            return True
        return player.role == "vigilante" and player.vigilante_shots > 0

    def role_card_embed(self, player: MafiaPlayer) -> discord.Embed:
        has_action = self.role_has_night_action(player)
        embed = discord.Embed(
            title=f"🎭 Your Role • {ROLE_NAMES[player.role]}",
            description=ROLE_DESCRIPTIONS[player.role],
        )
        embed.add_field(
            name="Night action",
            value="✅ **Yes** — use **🌙 Night Action** when night begins." if has_action
            else "❌ **None** — you do not need to press Night Action.",
            inline=False,
        )
        if player.role == "vigilante":
            embed.add_field(name="Shots", value=f"{player.vigilante_shots} remaining", inline=True)
        if player.role == "mafia" and player.known_mafia:
            teammates = [self.display(uid) for uid in sorted(player.known_mafia)]
            embed.add_field(name="Your Mafia team", value=" • ".join(teammates), inline=False)
        embed.set_footer(text="Only you can see your role.")
        return embed

    def vigilance_enabled_for(self, count: int) -> bool:
        if count < 6:
            return False
        if self.rules.vigilante_mode == "on":
            return True
        if self.rules.vigilante_mode == "off":
            return False
        return count >= 8

    def role_plan(self, count: int | None = None) -> list[Role]:
        n = count if count is not None else len(self.players)
        if n < 4:
            return []
        mafia_count = 1 if n <= 6 else 2 if n <= 10 else 3
        roles: list[Role] = ["mafia"] * mafia_count + ["doctor", "detective"]
        if self.vigilance_enabled_for(n):
            roles.append("vigilante")
        while len(roles) < n:
            roles.append("villager")
        return roles[:n]

    def role_preview(self) -> str:
        projected = len(self.human_players()) + self.desired_bot_count()
        roles = self.role_plan(projected)
        if not roles:
            return "Need at least **4 players**."
        counts = Counter(roles)
        order: list[Role] = ["mafia", "doctor", "detective", "vigilante", "villager"]
        return " • ".join(f"{ROLE_NAMES[r]} ×{counts[r]}" for r in order if counts[r])

    def cancel_timer(self) -> None:
        task = self.phase_task
        self.phase_task = None
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()

    def new_phase_token(self) -> str:
        self.cancel_timer()
        self.phase_token = uuid.uuid4().hex
        return self.phase_token

    async def send_or_edit(self, *, embed: discord.Embed, view: discord.ui.View | None = None) -> None:
        if self.message:
            try:
                await self.message.edit(embed=embed, view=view)
                return
            except (discord.NotFound, discord.HTTPException):
                self.message = None
        self.message = await self.channel.send(embed=embed, view=view)

    async def post_fresh(self, *, embed: discord.Embed, view: discord.ui.View | None = None) -> None:
        self.message = await self.channel.send(embed=embed, view=view)

    async def start(self) -> None:
        self.apply_bot_fill()
        if len(self.players) < 4:
            raise ValueError("Mafia needs at least 4 players after bot fill.")
        ids = list(self.players)
        roles = self.role_plan(len(ids))
        random.shuffle(roles)
        random.shuffle(ids)
        for uid, role in zip(ids, roles):
            p = self.players[uid]
            p.role = role
            p.alive = True
            p.vigilante_shots = 2
            p.guilt_pending = False
            p.known_mafia.clear()
            p.suspicion = {other: 0.0 for other in ids if other != uid}
        mafia_ids = {p.user_id for p in self.players.values() if p.role == "mafia"}
        for p in self.players.values():
            if p.role == "mafia":
                p.known_mafia = set(mafia_ids - {p.user_id})
        self.phase = "night"
        await self.start_night()

    def night_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"🌙 Night {self.night_number}",
            description=(
                "The town goes quiet. Tap **🎭 View My Role** to privately see your role.\n"
                "If your role has a night action, use **🌙 Night Action**."
            ),
        )
        embed.add_field(name="Alive", value=" • ".join(self.display(p.user_id) for p in self.living()), inline=False)
        embed.set_footer(text=f"Night actions close in {self.rules.night_seconds}s")
        return embed

    async def start_night(self) -> None:
        if await self.check_win():
            return
        self.phase = "night"
        self.night_number += 1
        self.night_actions = {"mafia": {}, "doctor": {}, "detective": {}, "vigilante": {}}
        token = self.new_phase_token()
        await self.post_fresh(embed=self.night_embed(), view=NightPublicView(self))
        # Start the fallback timer before bot work so the night always has a live
        # progression task, even if a bot action raises unexpectedly.
        self.phase_task = asyncio.create_task(self._night_timer(token))
        try:
            await self.run_bot_night_actions()
        except Exception:
            log.exception("Mafia bot night actions failed")
        await self.maybe_finish_night()

    async def run_bot_night_actions(self) -> None:
        """Resolve bot choices using only each bot's sanitized player knowledge."""
        for botp in [p for p in self.living() if p.is_bot]:
            _ = self.bot_view(botp)  # Explicit boundary: decisions use this player's knowledge only.
            if botp.role == "mafia":
                target = self.bot_pick_target(botp, exclude={botp.user_id, *botp.known_mafia})
                if target is not None:
                    self.night_actions["mafia"][botp.user_id] = target
            elif botp.role == "doctor":
                # Doctor does not know roles. Bias slightly toward self-protection but otherwise random.
                candidates = self.living_ids()
                target = botp.user_id if random.random() < 0.25 else random.choice(candidates)
                self.night_actions["doctor"][botp.user_id] = target
            elif botp.role == "detective":
                target = self.bot_pick_target(botp)
                if target is not None:
                    self.night_actions["detective"][botp.user_id] = target
                    # This is the only place a Town bot learns Mafia alignment.
                    if self.players[target].role == "mafia":
                        botp.known_mafia.add(target)
                        botp.suspicion[target] = 5.0
                    else:
                        botp.suspicion[target] = -2.0
            elif botp.role == "vigilante" and botp.vigilante_shots > 0:
                # Conservative bots usually hold fire. Known Mafia gets priority.
                known_targets = [uid for uid in botp.known_mafia if uid in self.living_ids()]
                shoot_chance = 0.85 if known_targets else 0.18
                if random.random() < shoot_chance:
                    target = random.choice(known_targets) if known_targets else self.bot_pick_target(botp)
                    self.night_actions["vigilante"][botp.user_id] = target
                else:
                    self.night_actions["vigilante"][botp.user_id] = None

    async def bot_day_comment(self, botp: MafiaPlayer) -> str:
        alive = [uid for uid in self.living_ids() if uid != botp.user_id]
        if not alive:
            return "I have nobody left to accuse, which feels like a personal failure."
        known = [uid for uid in botp.known_mafia if uid in alive]
        if known and botp.role != "mafia":
            target = known[0]
            return f"I am extremely suspicious of {self.display(target)}. Like, put-them-under-a-lamp suspicious."
        target = max(alive, key=lambda uid: botp.suspicion.get(uid, 0.0) + random.random() * 0.5)
        lines = {
            "aggressive": f"I'm voting {self.display(target)} unless somebody gives me a reason not to. Their vibes have warrants.",
            "paranoid": f"Maybe it's {self.display(target)}. Maybe it's all of you. I trust nobody here.",
            "chaotic": f"My evidence against {self.display(target)} is legally inadmissible but spiritually overwhelming.",
            "cautious": f"I don't have proof, but {self.display(target)} is where my suspicion is sitting right now.",
            "balanced": f"I'm leaning toward {self.display(target)} based on what we've seen so far.",
        }
        return lines.get(botp.bot_personality, lines["balanced"])

    async def run_bot_day_comments(self) -> None:
        bots = [p for p in self.living() if p.is_bot]
        random.shuffle(bots)
        for botp in bots[: min(3, len(bots))]:
            try:
                await self.channel.send(f"**{botp.name}:** {await self.bot_day_comment(botp)}")
            except discord.HTTPException:
                pass

    async def run_bot_votes(self) -> None:
        for botp in [p for p in self.living() if p.is_bot]:
            known = [uid for uid in botp.known_mafia if uid in self.living_ids()] if botp.role != "mafia" else []
            if known:
                target = random.choice(known)
            else:
                exclude = {botp.user_id}
                if botp.role == "mafia":
                    exclude |= botp.known_mafia
                target = self.bot_pick_target(botp, exclude=exclude)
            if target is not None:
                self.day_votes[botp.user_id] = target

    def required_night_actor_ids(self) -> set[int]:
        """Living players whose roles require an explicit night choice."""
        required: set[int] = set()
        for player in self.living():
            if player.role in {"mafia", "doctor", "detective"}:
                required.add(player.user_id)
            elif player.role == "vigilante" and player.vigilante_shots > 0:
                required.add(player.user_id)
        return required

    def completed_night_actor_ids(self) -> set[int]:
        completed: set[int] = set()
        for role_actions in self.night_actions.values():
            completed.update(role_actions.keys())
        return completed

    def night_actions_complete(self) -> bool:
        required = self.required_night_actor_ids()
        return bool(required) and required.issubset(self.completed_night_actor_ids())

    async def maybe_finish_night(self) -> None:
        """Advance immediately once every required night role has acted/skipped."""
        if self.phase != "night" or not self.night_actions_complete():
            return
        await self.resolve_night()

    async def _night_timer(self, token: str) -> None:
        try:
            await asyncio.sleep(self.rules.night_seconds)
            if self.phase == "night" and self.phase_token == token:
                await self.resolve_night()
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Mafia night timer failed")

    async def resolve_night(self) -> None:
        async with self.transition_lock:
            if self.phase != "night":
                return
            # Flip out of night immediately so a late timer/final action cannot
            # enter resolution a second time while this transition is running.
            self.phase = "day"
            self.cancel_timer()
            deaths: list[tuple[MafiaPlayer, str]] = []

            # Doctor: last submitted protection is used. Multiple doctors are not in core set,
            # but this remains safe if roles expand later.
            protected = {target for target in self.night_actions["doctor"].values() if target is not None}

            mafia_targets = [t for t in self.night_actions["mafia"].values() if t is not None and t in self.players]
            mafia_target: int | None = None
            if mafia_targets:
                counts = Counter(mafia_targets)
                top = max(counts.values())
                mafia_target = random.choice([uid for uid, c in counts.items() if c == top])
            if mafia_target is not None and mafia_target not in protected:
                victim = self.players.get(mafia_target)
                if victim and victim.alive:
                    victim.alive = False
                    deaths.append((victim, "Mafia struck during the night."))

            # Vigilante shots resolve separately. A Town hit schedules guilt death the next morning.
            for shooter_id, target_id in list(self.night_actions["vigilante"].items()):
                shooter = self.players.get(shooter_id)
                target = self.players.get(target_id) if target_id is not None else None
                if not shooter or not shooter.alive or shooter.role != "vigilante" or shooter.vigilante_shots <= 0 or not target or not target.alive:
                    continue
                shooter.vigilante_shots -= 1
                target.alive = False
                deaths.append((target, f"{self.display(shooter_id)} fired a Vigilante shot."))
                if target.role != "mafia":
                    shooter.guilt_pending = True

            # Morning guilt deaths happen after the night's kills.
            guilt_deaths: list[MafiaPlayer] = []
            for p in self.players.values():
                if p.alive and p.role == "vigilante" and p.guilt_pending:
                    p.alive = False
                    p.guilt_pending = False
                    guilt_deaths.append(p)

            embed = discord.Embed(title="☀️ Morning", description="The town wakes up and counts heads.")
            if deaths:
                for victim, reason in deaths:
                    embed.add_field(
                        name=f"💀 {victim.name}",
                        value=f"{reason}{self.role_reveal(victim)}",
                        inline=False,
                    )
            else:
                embed.add_field(name="Nobody died", value="Either the Doctor came through or Mafia failed to agree.", inline=False)
            for p in guilt_deaths:
                embed.add_field(
                    name=f"💀 {p.name}",
                    value=f"The Vigilante couldn't live with shooting Town.{self.role_reveal(p)}",
                    inline=False,
                )
            await self.post_fresh(embed=embed)
            if await self.check_win():
                return
            await self.start_day()

    async def start_day(self) -> None:
        self.phase = "day"
        self.day_number += 1
        token = self.new_phase_token()
        embed = discord.Embed(
            title=f"☀️ Day {self.day_number}",
            description="Talk it out. Accuse your friends. Lie with confidence. Voting opens when the discussion timer ends.",
        )
        embed.add_field(name="Alive", value=" • ".join(self.display(p.user_id) for p in self.living()), inline=False)
        embed.set_footer(text=f"Discussion: {self.rules.discussion_seconds}s")
        await self.post_fresh(embed=embed, view=DayHostView(self))
        await self.run_bot_day_comments()
        self.phase_task = asyncio.create_task(self._day_timer(token))

    async def _day_timer(self, token: str) -> None:
        try:
            await asyncio.sleep(self.rules.discussion_seconds)
            if self.phase == "day" and self.phase_token == token:
                await self.start_vote()
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Mafia day timer failed")

    async def start_vote(self) -> None:
        if self.phase not in {"day", "vote"}:
            return
        self.phase = "vote"
        self.day_votes = {}
        token = self.new_phase_token()
        embed = discord.Embed(
            title="🗳️ Vote",
            description="Who are we throwing to the wolves? Every living player gets one vote.",
        )
        embed.add_field(name="Alive", value=" • ".join(self.display(p.user_id) for p in self.living()), inline=False)
        embed.set_footer(text=f"Voting closes in {self.rules.vote_seconds}s")
        await self.post_fresh(embed=embed, view=VotePublicView(self))
        await self.run_bot_votes()
        human_living = [p for p in self.living() if not p.is_bot]
        if len(self.day_votes) >= len(self.living_ids()) or (not human_living and self.day_votes):
            await self.resolve_vote()
            return
        self.phase_task = asyncio.create_task(self._vote_timer(token))

    async def _vote_timer(self, token: str) -> None:
        try:
            await asyncio.sleep(self.rules.vote_seconds)
            if self.phase == "vote" and self.phase_token == token:
                await self.resolve_vote()
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Mafia vote timer failed")

    async def resolve_vote(self) -> None:
        if self.phase != "vote":
            return
        self.cancel_timer()
        valid = [target for voter, target in self.day_votes.items() if voter in self.living_ids() and target in self.living_ids()]
        embed = discord.Embed(title="⚖️ Vote Result")
        if not valid:
            embed.description = "Nobody could agree on anything. Extremely productive town meeting."
            await self.post_fresh(embed=embed)
            await self.start_night()
            return
        counts = Counter(valid)
        top = max(counts.values())
        leaders = [uid for uid, c in counts.items() if c == top]
        if len(leaders) > 1:
            embed.description = "The vote tied. Nobody is eliminated."
            if not self.rules.anonymous_votes:
                embed.add_field(name="Vote count", value="\n".join(f"{self.display(uid)} — {counts[uid]}" for uid in leaders), inline=False)
            await self.post_fresh(embed=embed)
            await self.start_night()
            return
        target = self.players[leaders[0]]
        target.alive = False
        embed.description = f"{self.display(target.user_id)} was eliminated.{self.role_reveal(target)}"
        if not self.rules.anonymous_votes:
            vote_lines = []
            for voter, chosen in self.day_votes.items():
                if voter in self.players:
                    vote_lines.append(f"{self.display(voter)} → {self.display(chosen)}")
            if vote_lines:
                embed.add_field(name="Votes", value="\n".join(vote_lines), inline=False)
        await self.post_fresh(embed=embed)
        if await self.check_win():
            return
        await self.start_night()

    async def check_win(self) -> bool:
        mafia = len(self.mafia_alive())
        town = len(self.town_alive())
        winner: str | None = None
        if mafia == 0 and self.phase != "lobby":
            winner = "town"
        elif mafia > 0 and mafia >= town and self.phase != "lobby":
            winner = "mafia"
        if winner is None:
            return False
        self.phase = "finished"
        self.cancel_timer()
        embed = discord.Embed(
            title="🎭 Game Over",
            description="🏘️ **Town wins!** The Mafia has been wiped out." if winner == "town" else "🔪 **Mafia wins!** They control what remains of the town.",
        )
        role_lines = [f"{self.display(p.user_id)} — {ROLE_NAMES[p.role]} {'❤️' if p.alive else '💀'}" for p in self.players.values()]
        embed.add_field(name="Final roles", value="\n".join(role_lines), inline=False)
        await self.post_fresh(embed=embed, view=MafiaGameOverView(self))
        return True

    async def end(self) -> None:
        self.phase = "finished"
        self.cancel_timer()
        sessions = getattr(self.bot, "_mommy_mafia_sessions", {})
        if sessions.get(self.channel.id) is self:
            sessions.pop(self.channel.id, None)


class MafiaSetupView(discord.ui.View):
    def __init__(self, session: MafiaSession, owner_id: int):
        super().__init__(timeout=600)
        self.session = session
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the host can change this setup.", ephemeral=True)
            return False
        return True

    def embed(self) -> discord.Embed:
        r = self.session.rules
        embed = discord.Embed(title="🎭 Mafia", description="Set the rules, then open the lobby.")
        embed.add_field(name="Discussion", value=f"{r.discussion_seconds}s", inline=True)
        embed.add_field(name="Night", value=f"{r.night_seconds}s", inline=True)
        embed.add_field(name="Voting", value=f"{r.vote_seconds}s", inline=True)
        embed.add_field(name="Role reveal", value="✅ On" if r.reveal_roles else "❌ Off", inline=True)
        embed.add_field(name="Votes", value="🕵️ Anonymous" if r.anonymous_votes else "👀 Visible after voting", inline=True)
        vig = {"auto": "Auto • ON at 8+", "on": "ON at 6+", "off": "Off"}[r.vigilante_mode]
        embed.add_field(name="Vigilante", value=vig, inline=True)
        mode_label = {"off": "Off", "minimum": "Fill to 4", "custom": f"Add {r.bot_fill_count}"}[r.bot_fill_mode]
        embed.add_field(name="Bot fill", value=mode_label, inline=True)
        embed.add_field(name="Role scaling", value="Roles adjust automatically to the final lobby size, including filler bots.", inline=False)
        return embed

    async def refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Discussion", emoji="☀️", style=discord.ButtonStyle.secondary, row=0)
    async def discussion(self, interaction: discord.Interaction, _):
        vals = [60, 90, 120, 180]
        cur = self.session.rules.discussion_seconds
        self.session.rules.discussion_seconds = vals[(vals.index(cur) + 1) % len(vals)] if cur in vals else 90
        await self.refresh(interaction)

    @discord.ui.button(label="Night", emoji="🌙", style=discord.ButtonStyle.secondary, row=0)
    async def night(self, interaction: discord.Interaction, _):
        vals = [30, 45, 60, 90]
        cur = self.session.rules.night_seconds
        self.session.rules.night_seconds = vals[(vals.index(cur) + 1) % len(vals)] if cur in vals else 60
        await self.refresh(interaction)

    @discord.ui.button(label="Voting", emoji="🗳️", style=discord.ButtonStyle.secondary, row=0)
    async def voting(self, interaction: discord.Interaction, _):
        vals = [30, 45, 60, 90]
        cur = self.session.rules.vote_seconds
        self.session.rules.vote_seconds = vals[(vals.index(cur) + 1) % len(vals)] if cur in vals else 60
        await self.refresh(interaction)

    @discord.ui.button(label="Role Reveal", emoji="💀", style=discord.ButtonStyle.secondary, row=1)
    async def reveal(self, interaction: discord.Interaction, _):
        self.session.rules.reveal_roles = not self.session.rules.reveal_roles
        await self.refresh(interaction)

    @discord.ui.button(label="Anonymous Votes", emoji="🕵️", style=discord.ButtonStyle.secondary, row=1)
    async def anonymous(self, interaction: discord.Interaction, _):
        self.session.rules.anonymous_votes = not self.session.rules.anonymous_votes
        await self.refresh(interaction)

    @discord.ui.button(label="Vigilante", emoji="🔫", style=discord.ButtonStyle.secondary, row=1)
    async def vigilante(self, interaction: discord.Interaction, _):
        modes = ["auto", "on", "off"]
        cur = self.session.rules.vigilante_mode
        self.session.rules.vigilante_mode = modes[(modes.index(cur) + 1) % len(modes)]
        await self.refresh(interaction)

    @discord.ui.button(label="Bot Fill", emoji="🤖", style=discord.ButtonStyle.secondary, row=2)
    async def bot_fill(self, interaction: discord.Interaction, _):
        modes = ["off", "minimum", "custom"]
        cur = self.session.rules.bot_fill_mode
        self.session.rules.bot_fill_mode = modes[(modes.index(cur) + 1) % len(modes)]
        await self.refresh(interaction)

    @discord.ui.button(label="Bot Count", emoji="➕", style=discord.ButtonStyle.secondary, row=2)
    async def bot_count(self, interaction: discord.Interaction, _):
        vals = [1, 2, 3, 4, 5, 6, 7, 8]
        cur = self.session.rules.bot_fill_count
        self.session.rules.bot_fill_count = vals[(vals.index(cur) + 1) % len(vals)] if cur in vals else 2
        self.session.rules.bot_fill_mode = "custom"
        await self.refresh(interaction)

    @discord.ui.button(label="Open Lobby", emoji="🚪", style=discord.ButtonStyle.success, row=3)
    async def open_lobby(self, interaction: discord.Interaction, _):
        await interaction.response.defer(ephemeral=True)
        embed = lobby_embed(self.session)
        self.session.message = await self.session.channel.send(embed=embed, view=MafiaLobbyView(self.session))
        try:
            await interaction.edit_original_response(content="Lobby opened.", embed=None, view=None)
        except discord.HTTPException:
            pass


class MafiaLobbyView(discord.ui.View):
    def __init__(self, session: MafiaSession):
        super().__init__(timeout=None)
        self.session = session

    @discord.ui.button(label="Join", emoji="➕", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, _):
        if self.session.phase != "lobby":
            await interaction.response.send_message("This game already started.", ephemeral=True); return
        if interaction.user.id in self.session.players:
            await interaction.response.send_message("You're already in the lobby.", ephemeral=True); return
        self.session.players[interaction.user.id] = MafiaPlayer(interaction.user.id, interaction.user.display_name)
        await interaction.response.edit_message(embed=lobby_embed(self.session), view=self)

    @discord.ui.button(label="Leave", emoji="➖", style=discord.ButtonStyle.secondary)
    async def leave(self, interaction: discord.Interaction, _):
        if self.session.phase != "lobby":
            await interaction.response.send_message("You can't leave after the game starts.", ephemeral=True); return
        if interaction.user.id == self.session.host_id:
            await interaction.response.send_message("The host can't leave. Cancel the lobby instead.", ephemeral=True); return
        if interaction.user.id not in self.session.players:
            await interaction.response.send_message("You're not in this lobby.", ephemeral=True); return
        self.session.players.pop(interaction.user.id, None)
        await interaction.response.edit_message(embed=lobby_embed(self.session), view=self)

    @discord.ui.button(label="Start", emoji="▶️", style=discord.ButtonStyle.primary)
    async def start(self, interaction: discord.Interaction, _):
        if interaction.user.id != self.session.host_id:
            await interaction.response.send_message("Only the host can start the game.", ephemeral=True); return
        projected = len(self.session.human_players()) + self.session.desired_bot_count()
        if projected < 4:
            await interaction.response.send_message("Mafia needs at least **4 total players**. Turn on bot fill or invite more people.", ephemeral=True); return
        await interaction.response.defer()
        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass
        await self.session.start()

    @discord.ui.button(label="Cancel", emoji="🛑", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, _):
        if interaction.user.id != self.session.host_id:
            await interaction.response.send_message("Only the host can cancel the lobby.", ephemeral=True); return
        await interaction.response.edit_message(embed=discord.Embed(title="🎭 Mafia", description="Lobby cancelled."), view=None)
        await self.session.end()


def lobby_embed(session: MafiaSession) -> discord.Embed:
    embed = discord.Embed(title="🎭 Mafia Lobby", description="Join the town. Host starts when everyone is ready.")
    human_lines = [session.display(p.user_id) for p in session.human_players()]
    bot_count = session.desired_bot_count()
    player_text = "\n".join(human_lines) or "Nobody yet."
    if bot_count:
        player_text += f"\n🤖 **+{bot_count} filler bot{'s' if bot_count != 1 else ''} at start**"
    projected = len(session.human_players()) + bot_count
    embed.add_field(name=f"Players • {projected} projected", value=player_text, inline=False)
    embed.add_field(name="Roles at this size", value=session.role_preview(), inline=False)
    vig = {"auto": "Auto (8+)", "on": "On (6+)", "off": "Off"}[session.rules.vigilante_mode]
    bot_label = {"off": "Bots off", "minimum": "Bots fill to 4", "custom": f"+{session.rules.bot_fill_count} bots"}[session.rules.bot_fill_mode]
    embed.set_footer(text=f"Host: {session.display(session.host_id)} • Vigilante: {vig} • {bot_label}")
    return embed


class ViewMyRoleButton(discord.ui.Button):
    def __init__(self, session: MafiaSession, *, row: int = 0):
        super().__init__(
            label="View My Role",
            emoji="🎭",
            style=discord.ButtonStyle.secondary,
            row=row,
        )
        self.session = session

    async def callback(self, interaction: discord.Interaction) -> None:
        player = self.session.players.get(interaction.user.id)
        if not player:
            await interaction.response.send_message(
                "You're not a player in this Mafia game.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=self.session.role_card_embed(player),
            ephemeral=True,
        )


class NightPublicView(discord.ui.View):
    def __init__(self, session: MafiaSession):
        super().__init__(timeout=None)
        self.session = session
        self.add_item(ViewMyRoleButton(session, row=0))

    @discord.ui.button(label="Night Action", emoji="🌙", style=discord.ButtonStyle.primary, row=0)
    async def action(self, interaction: discord.Interaction, _):
        p = self.session.players.get(interaction.user.id)
        if not p or not p.alive:
            await interaction.response.send_message("You're not alive in this game.", ephemeral=True); return
        if self.session.phase != "night":
            await interaction.response.send_message("Night actions are closed.", ephemeral=True); return
        if p.role == "villager":
            await interaction.response.send_message("You're a Villager. Sleep tight and try not to get murdered.", ephemeral=True); return
        if p.role == "vigilante" and p.vigilante_shots <= 0:
            await interaction.response.send_message("You're out of Vigilante shots.", ephemeral=True); return
        await interaction.response.send_message(embed=night_action_embed(self.session, p), view=NightActionView(self.session, p), ephemeral=True)


def night_action_embed(session: MafiaSession, player: MafiaPlayer) -> discord.Embed:
    desc = ROLE_DESCRIPTIONS[player.role]
    if player.role == "vigilante":
        desc += f"\n\n**Shots left:** {player.vigilante_shots}"
    return discord.Embed(title=f"{ROLE_NAMES[player.role]} • Night Action", description=desc)


class TargetSelect(discord.ui.Select):
    def __init__(self, session: MafiaSession, player: MafiaPlayer):
        self.session = session
        self.player = player
        candidates = []
        for p in session.living():
            if p.user_id == player.user_id and player.role in {"mafia", "detective", "vigilante"}:
                continue
            if player.role == "mafia" and p.role == "mafia":
                continue
            candidates.append(p)
        options = [discord.SelectOption(label=p.name[:100], value=str(p.user_id)) for p in candidates[:25]]
        super().__init__(placeholder="Choose a player…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        p = self.session.players.get(interaction.user.id)
        if not p or p.user_id != self.player.user_id or not p.alive or self.session.phase != "night":
            await interaction.response.send_message("That night action is no longer valid.", ephemeral=True); return
        target_id = int(self.values[0])
        if target_id not in self.session.living_ids():
            await interaction.response.send_message("That player is no longer available.", ephemeral=True); return
        if p.role == "detective":
            self.session.night_actions["detective"][p.user_id] = target_id
            target = self.session.players[target_id]
            result = "🔪 **Mafia**" if target.role == "mafia" else "🏘️ **Not Mafia**"
            await interaction.response.edit_message(embed=discord.Embed(title="🔍 Investigation", description=f"{self.session.display(target_id)} is {result}."), view=None)
            await self.session.maybe_finish_night()
            return
        self.session.night_actions[p.role][p.user_id] = target_id
        labels = {"mafia": "Target locked.", "doctor": "Protection locked.", "vigilante": "Shot locked. You can change it before night ends."}
        await interaction.response.edit_message(embed=discord.Embed(title="✅ Action saved", description=f"{labels.get(p.role, 'Saved')}\n\nTarget: {self.session.display(target_id)}"), view=None)
        await self.session.maybe_finish_night()


class NightActionView(discord.ui.View):
    def __init__(self, session: MafiaSession, player: MafiaPlayer):
        super().__init__(timeout=None)
        self.session = session
        self.player = player
        self.add_item(TargetSelect(session, player))
        if player.role == "vigilante":
            self.add_item(VigilanteSkipButton(session, player))


class VigilanteSkipButton(discord.ui.Button):
    def __init__(self, session: MafiaSession, player: MafiaPlayer):
        super().__init__(label="Skip Tonight", emoji="😴", style=discord.ButtonStyle.secondary)
        self.session = session
        self.player = player

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.player.user_id or self.session.phase != "night":
            await interaction.response.send_message("That action is no longer valid.", ephemeral=True); return
        self.session.night_actions["vigilante"][self.player.user_id] = None
        await interaction.response.edit_message(embed=discord.Embed(title="😴 Shot saved", description="You're keeping the gun holstered tonight."), view=None)
        await self.session.maybe_finish_night()


class DayHostView(discord.ui.View):
    def __init__(self, session: MafiaSession):
        super().__init__(timeout=None)
        self.session = session
        self.add_item(ViewMyRoleButton(session, row=0))

    @discord.ui.button(label="Open Vote", emoji="🗳️", style=discord.ButtonStyle.primary)
    async def vote(self, interaction: discord.Interaction, _):
        if interaction.user.id != self.session.host_id:
            await interaction.response.send_message("Only the host can open voting early.", ephemeral=True); return
        if self.session.phase != "day":
            await interaction.response.send_message("Voting already moved on.", ephemeral=True); return
        await interaction.response.defer()
        await self.session.start_vote()


class VotePublicView(discord.ui.View):
    def __init__(self, session: MafiaSession):
        super().__init__(timeout=None)
        self.session = session
        self.add_item(ViewMyRoleButton(session, row=0))

    @discord.ui.button(label="Vote", emoji="🗳️", style=discord.ButtonStyle.primary)
    async def vote(self, interaction: discord.Interaction, _):
        p = self.session.players.get(interaction.user.id)
        if not p or not p.alive:
            await interaction.response.send_message("Only living players can vote.", ephemeral=True); return
        if self.session.phase != "vote":
            await interaction.response.send_message("Voting is closed.", ephemeral=True); return
        await interaction.response.send_message(embed=discord.Embed(title="🗳️ Cast Your Vote", description="Pick one living player."), view=VoteChoiceView(self.session, p), ephemeral=True)


class VoteSelect(discord.ui.Select):
    def __init__(self, session: MafiaSession, voter: MafiaPlayer):
        self.session = session
        self.voter = voter
        options = [discord.SelectOption(label=p.name[:100], value=str(p.user_id)) for p in session.living() if p.user_id != voter.user_id]
        super().__init__(placeholder="Choose who to eliminate…", min_values=1, max_values=1, options=options[:25])

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.session.phase != "vote" or interaction.user.id != self.voter.user_id or not self.voter.alive:
            await interaction.response.send_message("That vote is no longer valid.", ephemeral=True); return
        target_id = int(self.values[0])
        if target_id not in self.session.living_ids():
            await interaction.response.send_message("That player is no longer available.", ephemeral=True); return
        self.session.day_votes[self.voter.user_id] = target_id
        await interaction.response.edit_message(embed=discord.Embed(title="✅ Vote saved", description=f"You voted for {self.session.display(target_id)}. You can reopen Vote to change it before time runs out."), view=None)
        if len(self.session.day_votes) >= len(self.session.living_ids()):
            await self.session.resolve_vote()


class VoteChoiceView(discord.ui.View):
    def __init__(self, session: MafiaSession, voter: MafiaPlayer):
        super().__init__(timeout=None)
        self.add_item(VoteSelect(session, voter))


class MafiaGameOverView(discord.ui.View):
    def __init__(self, session: MafiaSession):
        super().__init__(timeout=600)
        self.session = session

    @discord.ui.button(label="Clear Game", emoji="🧹", style=discord.ButtonStyle.secondary)
    async def clear(self, interaction: discord.Interaction, _):
        if interaction.user.id != self.session.host_id:
            await interaction.response.send_message("Only the host can clear the game.", ephemeral=True); return
        await interaction.response.edit_message(view=None)
        await self.session.end()


class MafiaHomeView(discord.ui.View):
    def __init__(self, bot, user_id: int):
        super().__init__(timeout=600)
        self.bot = bot
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Open your own game menu.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="New Game", emoji="🎮", style=discord.ButtonStyle.success)
    async def new_game(self, interaction: discord.Interaction, _):
        if interaction.guild is None or interaction.channel is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Mafia can only be started inside a server channel.", ephemeral=True); return
        sessions: dict[int, MafiaSession] = getattr(self.bot, "_mommy_mafia_sessions", {})
        setattr(self.bot, "_mommy_mafia_sessions", sessions)
        existing = sessions.get(interaction.channel.id)
        if existing and existing.phase != "finished":
            await interaction.response.send_message("There is already a Mafia game running in this channel.", ephemeral=True); return
        session = MafiaSession(self.bot, interaction.guild, interaction.channel, interaction.user)
        sessions[interaction.channel.id] = session
        view = MafiaSetupView(session, interaction.user.id)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)

    @discord.ui.button(label="How to Play", emoji="❓", style=discord.ButtonStyle.secondary)
    async def help(self, interaction: discord.Interaction, _):
        embed = discord.Embed(title="🎭 How Mafia Works", description="Town tries to find the Mafia before Mafia takes control. Night roles act privately; during the day everyone argues and votes.")
        embed.add_field(name="Town", value="👤 Villager • 🩺 Doctor • 🔍 Detective • 🔫 Vigilante when the lobby is large enough", inline=False)
        embed.add_field(name="Mafia", value="🔪 Mafia chooses a target each night and lies through their teeth during the day.", inline=False)
        embed.add_field(name="Vigilante", value="2 shots. Available at 6+ players; Auto mode adds it at 8+. Shooting Town causes a guilt death the next morning.", inline=False)
        embed.add_field(name="Filler bots", value="Fill a small lobby automatically or add a custom number. Town bots only know public information and anything their own role legitimately discovers; Mafia bots only know their Mafia teammates.", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def open_mafia_home(interaction: discord.Interaction, bot) -> None:
    embed = discord.Embed(title="🎭 Mafia", description="Lie, investigate, accuse your friends, and try not to die.")
    await interaction.response.send_message(embed=embed, view=MafiaHomeView(bot, interaction.user.id), ephemeral=True)

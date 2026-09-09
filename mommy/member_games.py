from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import discord
from discord.ext import commands
from openai import AsyncOpenAI

log = logging.getLogger("mommy-exe.member-games")

MIN_BLACK = 10
MIN_WHITE = 30
BLANK_TOKEN = "__WRITE_IN_BLANK__"
MOMMY_PLAYER_ID = -1

# Original starter content so the game works immediately without copying CAH card text.
BUILTIN_PACKS: dict[str, dict[str, Any]] = {
    "builtin:chaos": {
        "name": "House Chaos",
        "description": "General-purpose nonsense for a chaotic server night.",
        "black_cards": [
            {"text": "The group chat went silent after ____.", "pick": 1},
            {"text": "My villain origin story started with ____.", "pick": 1},
            {"text": "Tonight's terrible idea is ____ followed immediately by ____.", "pick": 2},
            {"text": "The real reason we got banned from the function was ____.", "pick": 1},
            {"text": "Nothing ruins a perfectly good Tuesday like ____.", "pick": 1},
            {"text": "I opened the fridge and found ____ staring back at me.", "pick": 1},
            {"text": "The server's new mascot is officially ____.", "pick": 1},
            {"text": "Emergency meeting: who left ____ in the voice channel?", "pick": 1},
            {"text": "The apology would have worked if they had not mentioned ____.", "pick": 1},
            {"text": "Step one: ____. Step two: ____. Step three: pretend this was intentional.", "pick": 2},
        ],
        "white_cards": [
            "a suspiciously warm hot dog", "three raccoons with a plan", "weaponized incompetence",
            "a Costco-sized tub of bad decisions", "an emotional-support traffic cone", "the forbidden Tupperware",
            "an aggressively moist handshake", "a group project nobody agreed to", "the world's loudest flip-flop",
            "a chair that has seen too much", "a deeply personal beef with a goose", "feral confidence",
            "one incredibly judgmental toddler", "a cursed Facebook Marketplace couch", "the family-size consequences",
            "an unnecessary amount of ranch", "a haunted air fryer", "the audacity on clearance",
            "a 3 a.m. voice note", "a legally distinct goblin", "the last clean spoon",
            "a suspicious hole in the drywall", "six feet of extension cord and no supervision", "an apology written in Comic Sans",
            "a bag of mystery cables", "a gas-station sushi prophecy", "the consequences of free will",
            "an unpaid emotional invoice", "an industrial quantity of glitter", "the world's least convincing disguise",
            "a Roomba with unresolved anger", "a wet sock in a dry room", "a tactical Capri Sun",
            "the smell of burnt popcorn and fear", "one final bad idea for the road",
        ],
    },
    "builtin:gaming": {
        "name": "Terminally Online",
        "description": "Gaming, Discord, and internet-brain nonsense.",
        "black_cards": [
            {"text": "The patch notes forgot to mention ____.", "pick": 1},
            {"text": "Our raid wiped because of ____.", "pick": 1},
            {"text": "Discord added a new feature: ____.", "pick": 1},
            {"text": "The speedrun category nobody asked for is ____%.", "pick": 1},
            {"text": "My setup is powered entirely by ____.", "pick": 1},
            {"text": "The final boss was just ____ wearing ____.", "pick": 2},
            {"text": "I got kicked from the lobby for ____.", "pick": 1},
            {"text": "The modpack broke after I installed ____.", "pick": 1},
            {"text": "Nothing says competitive integrity like ____.", "pick": 1},
            {"text": "My new gamer tag is basically ____ plus ____.", "pick": 2},
        ],
        "white_cards": [
            "a microphone peaking into another dimension", "forty-seven browser tabs", "a controller with stick drift",
            "the loading screen tip nobody reads", "a Discord mod on their lunch break", "an RGB-powered personality",
            "a keyboard full of crumbs", "the teammate who says 'trust me'", "an update that somehow made it worse",
            "a 900-page mod compatibility spreadsheet", "one suspiciously specific ban reason", "a headset held together by tape",
            "a ping spike at the worst possible moment", "a loot box full of disappointment", "an NPC with better social skills",
            "a save file named FINAL_final_REAL", "the world's angriest tutorial", "a server restart with no warning",
            "a five-hour character creator", "a patch downloaded over hotel Wi-Fi", "an emote used as legal evidence",
            "a keyboard shortcut nobody remembers", "a username from 2012", "a boss fight against the settings menu",
            "an accidental hot mic confession", "a deeply cursed custom skin", "the one person still using push-to-talk correctly",
            "a frame rate measured in vibes", "a mod that adds seventeen kinds of cheese", "an inventory full of junk I might need later",
            "the admin typing...", "a rage quit with excellent comedic timing", "a tutorial skipped with confidence",
            "a graphics card begging for mercy", "the sacred mute button",
        ],
    },
    "builtin:foul": {
        "name": "Absolutely Foul",
        "description": "After-dark filth, bodily disasters, cursed hookups, and criminal group-chat energy.",
        "black_cards": [
            {"text": "The bathroom was permanently condemned after ____.", "pick": 1},
            {"text": "I knew the hookup was over when they pulled out ____.", "pick": 1},
            {"text": "Nothing says romance like ____ and ____ on a fitted sheet.", "pick": 2},
            {"text": "The health inspector took one look at ____ and simply went home.", "pick": 1},
            {"text": "My search history can be explained by ____ but absolutely not ____.", "pick": 2},
            {"text": "The porta-potty started rocking because of ____.", "pick": 1},
            {"text": "Grandma did not survive Thanksgiving after learning about ____.", "pick": 1},
            {"text": "The smell wasn't sewage. It was actually ____.", "pick": 1},
            {"text": "I have been legally advised to stop calling it ____.", "pick": 1},
            {"text": "The worst thing to hear during sex is: 'Don't worry about ____.'", "pick": 1},
            {"text": "This year's county fair proudly introduces ____ on a stick.", "pick": 1},
            {"text": "The group chat unanimously agreed never to discuss ____ again.", "pick": 1},
            {"text": "My body rejected ____ but unfortunately kept ____.", "pick": 2},
            {"text": "The stain on the mattress turned out to be ____.", "pick": 1},
            {"text": "The wedding was beautiful until someone unleashed ____.", "pick": 1},
            {"text": "Doctors hate this one weird trick involving ____.", "pick": 1},
            {"text": "My last shred of dignity was found underneath ____.", "pick": 1},
            {"text": "I would have gotten away with it too, if not for ____ and that goddamn smell.", "pick": 1},
        ],
        "white_cards": [
            "a warm ham pocket",
            "porta-potty backsplash",
            "a medically concerning amount of mayonnaise",
            "the forbidden meat drawer",
            "a fart with paperwork",
            "an aggressively sweaty ass crack",
            "a suspiciously damp mattress",
            "gas-station bathroom intimacy",
            "a crusty little mystery towel",
            "the smell of hot pennies and regret",
            "a condom full of soup",
            "an emotional-support butt plug",
            "a gallon Ziploc bag of loose spaghetti",
            "the downstairs deli counter",
            "a moist rotisserie-chicken handshake",
            "a booty call with a court date",
            "an unlicensed Brazilian wax in a kitchen chair",
            "a used thong stuck to the ceiling fan",
            "the wettest cough you've ever heard",
            "a suspiciously crunchy bedsheet",
            "a deep-fried yeast infection joke",
            "a fart so violent it changed the thermostat",
            "a freezer bag labeled 'DO NOT EAT'",
            "a motel hot tub with visible history",
            "the last clean pair of underwear in the county",
            "a sloppy handful of meat juice",
            "an ass-print on a leather couch",
            "a toe with the confidence of a thumb",
            "a lukewarm jar of hot-dog water",
            "the sound of cheeks clapping in a quiet house",
            "a cursed amount of discharge",
            "a pubic hair with its own zip code",
            "a gas-station egg salad incident",
            "a belly-button smell that files taxes",
            "an entire rotisserie chicken eaten over the sink",
            "a bra full of emergency snacks",
            "a mystery fluid under blacklight",
            "a yeast infection with main-character energy",
            "the forbidden bathroom hand towel",
            "a meatball rolling out of somebody's purse",
            "a sweaty folding chair at a family reunion",
            "the unmistakable squelch of poor decisions",
            "a dirty sock used as medical equipment",
            "an unwashed hoodie with emotional significance",
            "a toenail clipping in the butter dish",
            "a hot burp directly into another person's mouth",
            "a sandwich bag full of teeth",
            "an absolutely disrespectful amount of back sweat",
            "a wet fart during a moment of silence",
            "a communal jar of Vaseline",
            "the kind of underwear you bury instead of wash",
            "a suspicious bump that definitely wasn't there Tuesday",
            "the neighborhood sex couch",
            "a damp handful of shredded cheese",
            "three days of swamp ass",
            "a used pregnancy test in the silverware drawer",
            "a shower drain hairball with legal custody rights",
            "a sneeze that launched something solid",
            "a ham candle burning in the bedroom",
            "a rogue nipple piercing caught in upholstery",
            "the world's angriest hemorrhoid",
            "a suspiciously intimate jar of peanut butter",
            "a butt dial during something deeply private",
            "a wet wipe doing the work of God",
            "an adult diaper full of ambition",
            "a Taco Bell bathroom exorcism",
            "a sweaty stranger whispering 'trust the process'",
            "a fistful of room-temperature shrimp",
            "an air fryer coated in ancient grease",
            "a fart trapped under a weighted blanket",
            "a mysterious rash shaped like Florida",
            "a mattress that knows too much",
        ],
    },
    "builtin:foul2": {
        "name": "Unfit for Company",
        "description": "A second after-dark pack full of cursed hookups, bathroom crimes, mystery fluids, and absolutely no dignity.",
        "black_cards": [
            {"text": "The Airbnb host charged us $900 because of ____.", "pick": 1},
            {"text": "I thought it was lube until I realized it was ____.", "pick": 1},
            {"text": "The date was going perfectly until ____ crawled out from under ____.", "pick": 2},
            {"text": "There are some smells Febreze cannot defeat, especially ____.", "pick": 1},
            {"text": "The emergency room nurse took one look at ____ and whispered, 'Again?'", "pick": 1},
            {"text": "The hotel security footage will forever be known as 'the ____ incident.'", "pick": 1},
            {"text": "My safe word is apparently ____.", "pick": 1},
            {"text": "The worst possible thing to find floating in the hot tub is ____.", "pick": 1},
            {"text": "Dinner was ruined when somebody put ____ next to ____.", "pick": 2},
            {"text": "Nothing bonds a family faster than pretending not to notice ____.", "pick": 1},
            {"text": "The neighbors called the police after hearing ____ through the wall.", "pick": 1},
            {"text": "The bathroom fan was not designed to handle ____.", "pick": 1},
            {"text": "The obituary left out the part about ____.", "pick": 1},
            {"text": "My therapist says I need to stop romanticizing ____.", "pick": 1},
            {"text": "The wedding registry included ____ and, for some reason, ____.", "pick": 2},
            {"text": "The group vacation ended early because somebody packed ____.", "pick": 1},
            {"text": "HR specifically asked me to stop bringing up ____ at lunch.", "pick": 1},
            {"text": "Nobody warned me that adulthood would involve this much ____.", "pick": 1},
        ],
        "white_cards": [
            "a bath towel with a suspicious stiff corner",
            "the forbidden bedside Gatorade",
            "a mystery stain that survived three landlords",
            "a fart that arrived with a sequel",
            "an upsettingly warm toilet seat",
            "a crusty sock with seniority",
            "the emotional aftermath of gas-station nachos",
            "a motel comforter with generational trauma",
            "a used washcloth in a Ziploc bag",
            "an unreasonably loud suction noise",
            "a mouthful of somebody else's hair",
            "an expired bottle of massage oil from 2009",
            "a shower curtain that touched too much",
            "the suspicious damp spot nobody claimed",
            "a belly button full of lint and secrets",
            "a clogged toilet making eye contact",
            "a thong being used as emergency equipment",
            "a bedroom fan spreading consequences evenly",
            "a single pube floating in the sink",
            "a wet couch cushion with no explanation",
            "a sock under the bed that has become sentient",
            "an industrial-strength booty sweat situation",
            "a hot pocket cooling on somebody's bare stomach",
            "a gas-station condom and blind optimism",
            "a shower drain that growls when fed",
            "a suspiciously slippery bathroom floor",
            "a forgotten chicken wing in the bedsheets",
            "the sound of a plunger losing hope",
            "a motel ice bucket being used incorrectly",
            "a body pillow with a criminal amount of history",
            "an ass crack full of beach sand",
            "a bathroom rug that squishes when stepped on",
            "a mouthwash bottle filled with something else",
            "a baggie of unidentified toenails",
            "a one-night stand who brought Tupperware",
            "a forehead kiss immediately after Taco Bell",
            "a backseat covered in mystery crumbs and shame",
            "a shower loofah old enough to vote",
            "a suspiciously warm jar of pickles",
            "an accidental nipple piercing tug-of-war",
            "a toilet brush being promoted beyond its job description",
            "a deeply personal relationship with baby powder",
            "a bedsheet with a crunchy side and a soft side",
            "a handful of damp cereal",
            "an unsolicited close-up of a rash",
            "a couch that makes a wet noise when you sit down",
            "the smell of old fries and poor judgment",
            "a half-melted ice cube found somewhere alarming",
            "a bathroom trash can full of unanswered questions",
            "a condom wrapper stuck to a Croc",
            "a humid room with no visible source of humidity",
            "an ankle monitor tangled in satin sheets",
            "a wet wipe folded with military precision",
            "a bottle of lotion that has seen active duty",
            "a suspiciously sticky remote control",
            "a toilet seat warmer powered by regret",
            "a sweaty wig on the passenger seat",
            "a pair of underwear abandoned like a crime scene",
            "a mystery smell trapped in a hoodie",
            "a bathroom stall with excellent acoustics",
            "the unmistakable texture of day-old body glitter",
            "a used Band-Aid in the chip bowl",
            "a hot dog rolling loose in a purse",
            "a shower cap full of shredded cheese",
            "an aggressively intimate amount of ranch",
            "a fitted sheet that should be submitted as evidence",
            "a warm can of energy drink from under the bed",
            "a bedroom trash bag doing heroic work",
            "a damp neck pillow from a bus station",
            "a single flip-flop beside an unexplained puddle",
            "the kind of fart that ends negotiations",
            "a public restroom hand dryer aimed somewhere personal",
            "a hotel Bible hiding something sticky",
            "a gallon jug labeled 'DO NOT SHAKE'",
        ],
    },

    "builtin:digital_circus": {
        "name": "The Amazing Digital Circus",
        "description": "Digital existential dread, Caine-approved nonsense, and absolutely normal circus activities.",
        "black_cards": [
            {"text": "Caine's newest totally safe adventure involves ____.", "pick": 1},
            {"text": "Pomni finally snapped after discovering ____ behind the exit door.", "pick": 1},
            {"text": "Jax got banned from today's adventure for ____.", "pick": 1},
            {"text": "Kinger became unexpectedly lucid and warned us about ____.", "pick": 1},
            {"text": "Ragatha is smiling through the emotional damage caused by ____.", "pick": 1},
            {"text": "Gangle's comedy mask shattered immediately after ____.", "pick": 1},
            {"text": "Zooble refused to participate until Caine removed ____.", "pick": 1},
            {"text": "Bubble has been specifically asked to stop licking ____.", "pick": 1},
            {"text": "The Amazing Digital Circus proudly presents: ____ versus ____.", "pick": 2},
            {"text": "The next abstracted character was last seen screaming about ____.", "pick": 1},
            {"text": "The exit door opened, but unfortunately it only led to ____.", "pick": 1},
            {"text": "Today's adventure has one rule: absolutely no ____.", "pick": 1},
            {"text": "Caine insists that ____ is 'great for morale.'", "pick": 1},
            {"text": "Nothing says digital immortality like ____ and ____.", "pick": 2},
            {"text": "The circus gift shop is now selling ____ for 30,000 digital tokens.", "pick": 1},
        ],
        "white_cards": [
            "Pomni's thousand-yard stare",
            "Caine inventing a new OSHA violation",
            "Jax being helpful for suspicious reasons",
            "Kinger hiding in his pillow fort",
            "Ragatha holding the group together with emotional duct tape",
            "Gangle's last surviving comedy mask",
            "Zooble removing a body part out of pure annoyance",
            "Bubble eating something that was definitely not food",
            "an exit door with commitment issues",
            "a hallway that absolutely was not there five minutes ago",
            "a digital nervous breakdown",
            "an adventure nobody consented to",
            "Caine's legally questionable game design",
            "Jax committing a misdemeanor for enrichment",
            "Pomni asking one perfectly reasonable question",
            "Kinger suddenly becoming the smartest person in the room",
            "Ragatha saying 'it's fine' while nothing is fine",
            "Gangle's emotional support ribbon",
            "Zooble's detachable patience",
            "Bubble with unrestricted access to the kitchen",
            "the consequences of digital immortality",
            "an NPC developing suspiciously real feelings",
            "a horrifying amount of confetti",
            "the void staring back",
            "a door labeled EXIT in Comic Sans",
            "a circus tent powered by psychological damage",
            "Caine's emergency pair of novelty teeth",
            "Jax's completely punchable confidence",
            "Pomni speedrunning all five stages of grief",
            "Kinger explaining something terrifyingly profound",
            "Ragatha's customer-service smile",
            "Gangle crying in 4K",
            "Zooble opting out of the plot",
            "Bubble saying something deeply concerning",
            "a digital feast nobody can actually digest",
            "a side quest with permanent emotional consequences",
            "being trapped forever with the world's most enthusiastic ringmaster",
            "a fake exit placed there for character development",
            "an abstracted coworker in the basement",
            "three seconds of genuine peace before Caine appears",
            "a giant novelty hammer labeled THERAPY",
            "the world's least comforting carnival music",
            "a suspiciously sentient mannequin",
            "an existential crisis with ray tracing",
            "a character model held together by panic",
            "a perfectly normal amount of screaming",
            "Caine turning trauma into a team-building exercise",
            "Jax pressing the button marked DO NOT PRESS",
            "an NPC funeral with catering",
            "the horrifying realization that respawning is mandatory",
        ],
    },
    "builtin:youtube_gremlins": {
        "name": "YouTube Gremlin Energy",
        "description": "CaseOh streams meet the Brandon Rogers multiverse. Loud, chaotic, and legally concerning.",
        "black_cards": [
            {"text": "CaseOh banned someone from chat for mentioning ____.", "pick": 1},
            {"text": "The horror game stopped being scary once CaseOh encountered ____.", "pick": 1},
            {"text": "Chat's newest nickname for CaseOh is somehow ____.", "pick": 1},
            {"text": "CaseOh's DoorDash driver arrived carrying ____ and ____.", "pick": 2},
            {"text": "The stream ended immediately after ____ appeared on screen.", "pick": 1},
            {"text": "In the Brandon Rogers cinematic universe, today's emergency is ____.", "pick": 1},
            {"text": "Helen Brownstein has been permanently banned from ____.", "pick": 1},
            {"text": "Bryce Tankthrust's newest business venture is ____.", "pick": 1},
            {"text": "The PTA meeting was derailed by ____ and a suspicious amount of ____.", "pick": 2},
            {"text": "Nobody in this Brandon Rogers sketch was prepared for ____.", "pick": 1},
            {"text": "The YouTube algorithm recommended three hours of ____.", "pick": 1},
            {"text": "This stream is sponsored by ____ for reasons nobody understands.", "pick": 1},
            {"text": "The comment section has unanimously decided that ____ is canon.", "pick": 1},
            {"text": "CaseOh versus the Brandon Rogers multiverse: the final challenge is ____.", "pick": 1},
            {"text": "The collab was going fine until somebody brought ____.", "pick": 1},
        ],
        "white_cards": [
            "CaseOh reading one chat message and immediately regretting it",
            "a chat message getting banned before the sentence is finished",
            "CaseOh arguing with a fictional food item",
            "a horror-game monster getting roasted instead of feared",
            "the world's most aggressive DoorDash notification",
            "chat typing the same joke 400 times",
            "a donation designed exclusively to start an argument",
            "CaseOh yelling at a completely innocent NPC",
            "one food take powerful enough to divide the entire stream",
            "a tier list that becomes a federal incident",
            "an accidental five-minute rant about ranch",
            "a stream title that ages terribly within six minutes",
            "the chat moving too fast for human comprehension",
            "a jumpscare followed by immediate disrespect",
            "CaseOh threatening to ban the entire internet",
            "a suspiciously personal feud with a menu screen",
            "Helen Brownstein escalating a minor inconvenience into a felony",
            "Bryce Tankthrust monetizing a human rights violation",
            "a Brandon Rogers character entering at maximum volume",
            "a suburban meltdown with excellent lighting",
            "a minivan full of unresolved character arcs",
            "a PTA meeting that requires police backup",
            "a customer-service interaction from hell",
            "a wig with more personality than most people",
            "an HR complaint written in all caps",
            "a suburban mom with the energy of a natural disaster",
            "a fake accent that somehow gets stronger under pressure",
            "a family dinner becoming a season finale",
            "a sketch character making the worst possible entrance",
            "a completely unnecessary costume change",
            "an argument that starts at ten and somehow gets louder",
            "a backyard containing at least three crimes",
            "a motivational speech delivered by the least qualified person alive",
            "an aggressively specific insult",
            "a YouTube thumbnail with seventeen facial expressions",
            "a demonetization email arriving mid-sentence",
            "the algorithm rewarding the worst behavior imaginable",
            "a comment pinned purely out of spite",
            "a sponsorship nobody remembers agreeing to",
            "a ring light witnessing things it can never unsee",
            "a creator apology filmed next to an unplugged ukulele",
            "a collab held together by caffeine and bad judgment",
            "a livestream chat functioning as a hostile jury",
            "a microphone peaking from pure emotional damage",
            "an editor deciding this is tomorrow's problem",
            "a camera battery dying at the funniest possible moment",
            "a merch drop that looks vaguely threatening",
            "a reaction face powerful enough to alter the timeline",
            "YouTube subtitles giving up completely",
            "the exact moment the bit goes way too far",
        ],
    },
    "builtin:hazbin": {
        "name": "Hazbin Hotel",
        "description": "Hellish hotel management, redemption disasters, radio-demon nonsense, and infernal workplace drama.",
        "black_cards": [
            {"text": "Charlie's newest redemption exercise is ____.", "pick": 1},
            {"text": "Vaggie knew the hotel meeting was doomed when ____ walked in.", "pick": 1},
            {"text": "Alastor's radio show has been interrupted by ____.", "pick": 1},
            {"text": "Angel Dust has been asked, once again, to stop bringing ____ into the lobby.", "pick": 1},
            {"text": "Husk will pour you a drink if you promise never to mention ____.", "pick": 1},
            {"text": "Niffty found ____ and has decided it belongs to her now.", "pick": 1},
            {"text": "Lucifer's latest attempt to bond with Charlie involves ____.", "pick": 1},
            {"text": "Vox interrupted regular programming to complain about ____.", "pick": 1},
            {"text": "The Hazbin Hotel's newest amenity is ____ next to ____.", "pick": 2},
            {"text": "Today's mandatory trust exercise somehow ended with ____.", "pick": 1},
            {"text": "The hotel received a one-star review because of ____.", "pick": 1},
            {"text": "Hell's newest turf war is officially about ____.", "pick": 1},
            {"text": "Charlie insists even ____ deserves a second chance.", "pick": 1},
            {"text": "The lobby went completely silent when ____ challenged ____.", "pick": 2},
            {"text": "The hotel's employee handbook now has an entire section about ____.", "pick": 1},
        ],
        "white_cards": [
            "Charlie's unstoppable optimism",
            "Vaggie's last remaining nerve",
            "Alastor smiling during something deeply alarming",
            "Angel Dust turning therapy into crowd work",
            "Husk drinking through another staff meeting",
            "Niffty discovering a brand-new stain",
            "Lucifer arriving with unnecessary theatrical flair",
            "Vox buffering during a villain monologue",
            "Valentino making the room instantly worse",
            "Velvette live-posting the apocalypse",
            "Sir Pentious unveiling another doomed invention",
            "Cherri Bomb treating structural damage as a hobby",
            "a redemption worksheet nobody completed",
            "an infernal group-therapy circle",
            "a hotel lobby with negative insurance coverage",
            "Alastor's radio-static entrance music",
            "a contract with extremely suspicious fine print",
            "Charlie's emergency friendship song",
            "Vaggie physically preventing another terrible idea",
            "Angel Dust's deeply unhelpful coping mechanism",
            "Husk's thousand-yard bartender stare",
            "Niffty cleaning something that should remain untouched",
            "Lucifer's collection of increasingly specific ducks",
            "Vox losing an argument to outdated technology",
            "a turf war with branding guidelines",
            "an overlord meeting that should have been an email",
            "a demon with a LinkedIn profile",
            "a suspicious amount of red interior decorating",
            "a musical number during an active emergency",
            "the hotel's nonexistent security deposit",
            "a redemption plan written on a cocktail napkin",
            "an extermination-day scheduling conflict",
            "a demon trying mindfulness for eleven seconds",
            "a bar tab older than several civilizations",
            "Hell's most passive-aggressive elevator ride",
            "an extremely cursed staff retreat",
            "a microphone that definitely belongs to Alastor",
            "a TV screen displaying Vox's ego in 8K",
            "an emotional-support rubber duck",
            "a chandelier destroyed for narrative emphasis",
            "a hotel guest with absolutely no intention of improving",
            "a sinister jazz interlude",
            "a heartfelt speech immediately ruined by violence",
            "an infernal influencer sponsorship",
            "a suspiciously cheerful welcome basket",
            "the worst roommate arrangement in Hell",
            "a group hug with at least one concealed weapon",
            "a redemption milestone nobody can verify",
            "a bartender who has heard every possible confession",
            "an HR department that literally does not exist",
        ],
    },
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _short(text: str, limit: int = 80) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _is_staff(member: discord.Member) -> bool:
    p = member.guild_permissions
    return member.id == member.guild.owner_id or p.administrator or p.manage_guild


@dataclass
class GameRules:
    czar_mode: str = "ai_if_needed"  # human | ai_if_needed | ai_always
    ai_style: str = "funniest"
    hand_size: int = 8
    score_to_win: int = 5
    blank_per_hand: int = 1
    mommy_player: bool = False


@dataclass
class PlayerState:
    user_id: int
    name: str
    hand: list[str] = field(default_factory=list)
    score: int = 0
    is_bot: bool = False


@dataclass
class Submission:
    user_id: int
    cards: list[str]


class PackStore:
    def __init__(self, bot) -> None:
        self.bot = bot
        self._indexed = False

    async def collection(self):
        db = self.bot.database._database  # noqa: SLF001
        if db is None:
            raise RuntimeError("MongoDB is not connected")
        col = db["cah_packs"]
        if not self._indexed:
            await asyncio.to_thread(col.create_index, [("guild_id", 1), ("scope", 1), ("owner_id", 1)])
            self._indexed = True
        return col

    async def list_for_host(self, guild_id: int, owner_id: int) -> list[dict[str, Any]]:
        col = await self.collection()
        docs = await asyncio.to_thread(
            lambda: list(col.find({"guild_id": guild_id, "$or": [{"scope": "server"}, {"owner_id": owner_id}]}).sort("name", 1))
        )
        return docs

    async def list_personal(self, guild_id: int, owner_id: int) -> list[dict[str, Any]]:
        col = await self.collection()
        return await asyncio.to_thread(lambda: list(col.find({"guild_id": guild_id, "owner_id": owner_id}).sort("name", 1)))

    async def create(self, *, guild_id: int, owner_id: int, name: str, scope: str) -> str:
        col = await self.collection()
        doc = {
            "guild_id": guild_id,
            "owner_id": owner_id,
            "name": name[:80].strip(),
            "scope": scope,
            "black_cards": [],
            "white_cards": [],
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        result = await asyncio.to_thread(col.insert_one, doc)
        return str(result.inserted_id)

    async def get(self, pack_id: str) -> dict[str, Any] | None:
        from bson import ObjectId
        try:
            oid = ObjectId(pack_id)
        except Exception:
            return None
        col = await self.collection()
        return await asyncio.to_thread(col.find_one, {"_id": oid})

    async def add_black(self, pack_id: str, text: str, pick: int) -> None:
        from bson import ObjectId
        col = await self.collection()
        await asyncio.to_thread(col.update_one, {"_id": ObjectId(pack_id)}, {"$push": {"black_cards": {"text": text, "pick": pick}}, "$set": {"updated_at": utcnow()}})

    async def add_white(self, pack_id: str, text: str) -> None:
        from bson import ObjectId
        col = await self.collection()
        await asyncio.to_thread(col.update_one, {"_id": ObjectId(pack_id)}, {"$push": {"white_cards": text}, "$set": {"updated_at": utcnow()}})

    async def delete(self, pack_id: str, owner_id: int, allow_staff: bool) -> bool:
        from bson import ObjectId
        col = await self.collection()
        query: dict[str, Any] = {"_id": ObjectId(pack_id)}
        if not allow_staff:
            query["owner_id"] = owner_id
        result = await asyncio.to_thread(col.delete_one, query)
        return bool(result.deleted_count)


class CAHSession:
    def __init__(self, cog: "MemberGamesCog", guild: discord.Guild, channel: discord.abc.Messageable, host: discord.Member) -> None:
        self.cog = cog
        self.guild = guild
        self.channel = channel
        self.host_id = host.id
        self.players: dict[int, PlayerState] = {host.id: PlayerState(host.id, host.display_name)}
        self.selected_pack_ids: list[str] = ["builtin:chaos"]
        self.rules = GameRules()
        self.status = "setup"
        self.message: discord.Message | None = None
        self.black_pool: list[dict[str, Any]] = []
        self.white_pool: list[str] = []
        # Played regular white cards are discarded here. They only become
        # drawable again after the live draw pile is exhausted and the discard
        # pile is reshuffled. Cards still sitting in players' hands are never
        # returned to the draw pile.
        self.white_discard: list[str] = []
        self.current_black: dict[str, Any] | None = None
        self.current_czar_id: int | None = None
        self.czar_rotation: list[int] = []
        self.czar_index = -1
        self.submissions: dict[int, Submission] = {}
        self.round_number = 0
        self.last_ai_reason = ""
        self.lock = asyncio.Lock()

    def host(self) -> discord.Member | None:
        return self.guild.get_member(self.host_id)

    def participant_ids(self) -> list[int]:
        ids = list(self.players)
        if self.rules.mommy_player and MOMMY_PLAYER_ID not in ids:
            ids.append(MOMMY_PLAYER_ID)
        return ids

    def display_player(self, user_id: int) -> str:
        return "🤖 Mommy.exe" if user_id == MOMMY_PLAYER_ID else f"<@{user_id}>"

    def sync_mommy_player(self) -> None:
        if self.rules.mommy_player:
            self.players.setdefault(MOMMY_PLAYER_ID, PlayerState(MOMMY_PLAYER_ID, "Mommy.exe", is_bot=True))
        else:
            self.players.pop(MOMMY_PLAYER_ID, None)
            self.czar_rotation = [uid for uid in self.czar_rotation if uid != MOMMY_PLAYER_ID]

    def eligible_submitters(self) -> list[int]:
        if self.current_czar_id is None:
            return list(self.players)
        return [uid for uid in self.players if uid != self.current_czar_id]

    def is_ai_czar(self) -> bool:
        return self.current_czar_id in (None, MOMMY_PLAYER_ID)

    async def load_pools(self) -> tuple[bool, str]:
        black: list[dict[str, Any]] = []
        white: list[str] = []
        for pack_id in self.selected_pack_ids:
            if pack_id.startswith("builtin:"):
                pack = BUILTIN_PACKS.get(pack_id)
            else:
                pack = await self.cog.store.get(pack_id)
            if not pack:
                continue
            b = list(pack.get("black_cards", []))
            w = list(pack.get("white_cards", []))
            if len(b) < MIN_BLACK or len(w) < MIN_WHITE:
                continue
            black.extend({"text": str(x.get("text", "")), "pick": max(1, min(2, int(x.get("pick", 1))))} for x in b if str(x.get("text", "")).strip())
            white.extend(str(x).strip() for x in w if str(x).strip())
        if len(black) < MIN_BLACK or len(white) < MIN_WHITE:
            return False, f"The selected ready packs do not provide enough cards ({len(black)} black / {len(white)} white)."
        random.shuffle(black)
        random.shuffle(white)
        self.black_pool = black
        self.white_pool = white
        self.white_discard.clear()
        return True, ""

    def draw_white(self) -> str:
        # Draw without replacement. Once the live pile is empty, recycle only
        # cards that have actually been played/discarded; cards still held by
        # players remain out of circulation.
        if not self.white_pool and self.white_discard:
            random.shuffle(self.white_discard)
            self.white_pool = self.white_discard
            self.white_discard = []
        if not self.white_pool:
            return "the horrifying realization that the deck is empty"
        return self.white_pool.pop()

    def discard_white(self, card: str) -> None:
        if card and card != BLANK_TOKEN:
            self.white_discard.append(card)

    def fill_hand(self, player: PlayerState, *, initial: bool = False) -> None:
        # Keep unused cards between rounds. Only played cards are replaced.
        # The host-selected write-in blank count stays exact.
        blank_target = max(0, min(self.rules.blank_per_hand, self.rules.hand_size))
        regular_target = max(0, self.rules.hand_size - blank_target)

        regular_cards = [x for x in player.hand if x != BLANK_TOKEN][:regular_target]
        while len(regular_cards) < regular_target:
            regular_cards.append(self.draw_white())

        player.hand = regular_cards + ([BLANK_TOKEN] * blank_target)
        if initial:
            random.shuffle(player.hand)

    def decide_czar(self) -> None:
        self.sync_mommy_player()
        ids = self.participant_ids()
        human_ids = [uid for uid in ids if uid != MOMMY_PLAYER_ID]
        mode = self.rules.czar_mode

        # AI Always keeps Mommy in the judge seat. In the rotating modes, enabling
        # Mommy as a player places her in the same Czar queue as everyone else.
        if mode == "ai_always":
            self.current_czar_id = MOMMY_PLAYER_ID
            return
        if mode == "ai_if_needed" and not self.rules.mommy_player and len(human_ids) < 3:
            self.current_czar_id = MOMMY_PLAYER_ID
            return

        if not self.czar_rotation or set(self.czar_rotation) != set(ids):
            self.czar_rotation = ids[:]
            random.shuffle(self.czar_rotation)
            self.czar_index = -1
        self.czar_index = (self.czar_index + 1) % len(self.czar_rotation)
        self.current_czar_id = self.czar_rotation[self.czar_index]

    async def begin(self) -> tuple[bool, str]:
        self.sync_mommy_player()
        human_count = len([uid for uid in self.players if uid != MOMMY_PLAYER_ID])
        total_count = len(self.participant_ids())
        if total_count < 2:
            return False, "You need at least 2 players total. Add Mommy as a player or wait for someone else to join."
        if self.rules.czar_mode == "human" and total_count < 3:
            return False, "Rotating Czar mode needs at least 3 seats. Add Mommy as a player or use AI Czar."
        ok, reason = await self.load_pools()
        if not ok:
            return False, reason
        for player in self.players.values():
            self.fill_hand(player, initial=True)
        self.status = "playing"
        await self.start_round()
        return True, ""

    async def start_round(self) -> None:
        self.round_number += 1
        self.submissions.clear()
        self.last_ai_reason = ""
        if not self.black_pool:
            await self.load_pools()
        self.current_black = self.black_pool.pop() if self.black_pool else {"text": "____", "pick": 1}
        self.decide_czar()
        await self.render_round(new_message=True)
        if self.rules.mommy_player and MOMMY_PLAYER_ID in self.eligible_submitters():
            await self.cog.mommy_submit(self)

    def round_embed(self) -> discord.Embed:
        black = self.current_black or {"text": "Waiting…", "pick": 1}
        czar = "🤖 Mommy.exe (AI Czar)" if self.current_czar_id in (None, MOMMY_PLAYER_ID) else f"👑 <@{self.current_czar_id}>"
        embed = discord.Embed(title=f"🃏 Card Game — Round {self.round_number}", description=f"### {black['text']}")
        embed.add_field(name="Czar", value=czar, inline=True)
        embed.add_field(name="Pick", value=str(black.get("pick", 1)), inline=True)
        embed.add_field(name="Submitted", value=f"{len(self.submissions)}/{len(self.eligible_submitters())}", inline=True)
        scores = sorted(self.players.values(), key=lambda p: (-p.score, p.name.casefold()))
        embed.add_field(name="Scoreboard", value="\n".join(f"**{p.score}** — {self.display_player(p.user_id)}" for p in scores) or "No scores yet.", inline=False)
        embed.set_footer(text=f"AI style: {self.rules.ai_style.replace('_', ' ').title()} • First to {self.rules.score_to_win}")
        return embed

    async def render_round(self, *, new_message: bool = False) -> None:
        view = RoundView(self)
        if new_message or self.message is None:
            self.message = await self.channel.send(embed=self.round_embed(), view=view)
        else:
            await self.message.edit(embed=self.round_embed(), view=view)

    async def submit(self, user_id: int, cards: list[str], hand_indexes: list[int]) -> tuple[bool, str]:
        async with self.lock:
            if self.status != "playing":
                return False, "That round is no longer accepting cards."
            if user_id not in self.eligible_submitters():
                return False, "The Czar does not submit a card this round."
            if user_id in self.submissions:
                return False, "You already submitted this round."
            player = self.players.get(user_id)
            if not player:
                return False, "You are not in this game."
            # Played regular white cards leave the hand and go to the
            # discard pile. Write-in blanks are virtual slots, so they are not
            # added to the regular white-card deck.
            for idx in sorted(set(hand_indexes), reverse=True):
                if 0 <= idx < len(player.hand):
                    used_card = player.hand.pop(idx)
                    self.discard_white(used_card)
            # Refill only the slots that were played. Replacement regular cards
            # are random draws from the remaining shuffled pool.
            self.fill_hand(player)
            self.submissions[user_id] = Submission(user_id=user_id, cards=cards)
            await self.render_round()
            if len(self.submissions) >= len(self.eligible_submitters()):
                await self.begin_judging()
            return True, "Card submitted."

    async def begin_judging(self) -> None:
        if self.status != "playing" or not self.submissions:
            return
        self.status = "judging"
        ordered = list(self.submissions.values())
        random.shuffle(ordered)
        if self.is_ai_czar():
            winner_id, reason = await self.cog.ai_choose(self.current_black or {}, ordered, self.rules.ai_style)
            await self.finish_round(winner_id, reason)
            return
        # Freeze the round card where it is, then post judging as a fresh
        # message at the bottom so the Czar never has to scroll back up.
        if self.message is not None:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass
        self.message = await self.channel.send(
            embed=self.judging_embed(ordered),
            view=JudgingView(self, ordered),
        )

    def judging_embed(self, ordered: list[Submission]) -> discord.Embed:
        embed = discord.Embed(title="👑 Czar, choose the winner", description=f"### {(self.current_black or {}).get('text', '')}")
        for i, submission in enumerate(ordered):
            embed.add_field(name=f"Option {chr(65+i)}", value=" / ".join(submission.cards), inline=False)
        embed.set_footer(text="Submissions are anonymous until the winner is chosen.")
        return embed

    async def finish_round(self, winner_id: int, reason: str = "") -> None:
        if winner_id not in self.players:
            winner_id = next(iter(self.submissions))
        self.players[winner_id].score += 1
        self.last_ai_reason = reason
        winner_submission = self.submissions.get(winner_id)
        embed = discord.Embed(title="🏆 Round winner", description=f"{self.display_player(winner_id)} wins the round!")
        if winner_submission:
            embed.add_field(name="Winning answer", value=" / ".join(winner_submission.cards), inline=False)
        if reason:
            embed.add_field(name="AI Czar's take", value=_short(reason, 500), inline=False)
        embed.add_field(name="Score", value=f"**{self.players[winner_id].score}** / {self.rules.score_to_win}", inline=False)
        # Freeze the judging/round card where it is so the result does not
        # overwrite an older message higher in the channel. Every result gets
        # a fresh message at the bottom, and that new message becomes the
        # active game UI for the next action.
        if self.message is not None:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass

        if self.players[winner_id].score >= self.rules.score_to_win:
            self.status = "finished"
            embed.title = "🎉 Game over"
            embed.description = f"{self.display_player(winner_id)} wins the game!"
            if winner_id != MOMMY_PLAYER_ID:
                await self.cog.record_win(self.guild.id, winner_id)
            self.message = await self.channel.send(embed=embed, view=GameOverView(self))
        else:
            self.status = "between_rounds"
            self.message = await self.channel.send(embed=embed, view=BetweenRoundsView(self))

    async def end(self) -> None:
        self.status = "finished"
        self.cog.sessions.pop(self.channel.id, None)
        if self.message:
            embed = discord.Embed(title="🃏 Game ended", description="The table has been cleared.")
            await self.message.edit(embed=embed, view=None)


class CreatePackModal(discord.ui.Modal, title="Create a card pack"):
    name = discord.ui.TextInput(label="Pack name", max_length=80, placeholder="Server Brainrot")

    def __init__(self, cog: "MemberGamesCog", scope: str):
        super().__init__()
        self.cog = cog
        self.scope = scope

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This only works in a server.", ephemeral=True)
            return
        if self.scope == "server" and not _is_staff(interaction.user):
            await interaction.response.send_message("Only server staff can create shared server packs.", ephemeral=True)
            return
        pack_id = await self.cog.store.create(guild_id=interaction.guild.id, owner_id=interaction.user.id, name=str(self.name), scope=self.scope)
        pack = await self.cog.store.get(pack_id)
        await interaction.response.send_message(embed=pack_embed(pack), view=PackEditorView(self.cog, pack_id, interaction.user.id), ephemeral=True)


class AddBlackModal(discord.ui.Modal, title="Add a black prompt"):
    prompt = discord.ui.TextInput(label="Prompt", style=discord.TextStyle.paragraph, max_length=300, placeholder="I got banned from Walmart for ____.")
    pick = discord.ui.TextInput(label="Number of answers (1 or 2)", default="1", max_length=1)

    def __init__(self, cog: "MemberGamesCog", pack_id: str, owner_id: int):
        super().__init__(); self.cog = cog; self.pack_id = pack_id; self.owner_id = owner_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try: pick = int(str(self.pick))
        except ValueError: pick = 1
        pick = max(1, min(2, pick))
        text = str(self.prompt).strip()
        if "____" not in text:
            text = text.rstrip(" .") + " ____."
        await self.cog.store.add_black(self.pack_id, text, pick)
        pack = await self.cog.store.get(self.pack_id)
        await interaction.response.edit_message(embed=pack_embed(pack), view=PackEditorView(self.cog, self.pack_id, self.owner_id))


class AddWhiteModal(discord.ui.Modal, title="Add a white card"):
    answer = discord.ui.TextInput(label="Answer", style=discord.TextStyle.paragraph, max_length=220, placeholder="a suspiciously specific alibi")

    def __init__(self, cog: "MemberGamesCog", pack_id: str, owner_id: int):
        super().__init__(); self.cog = cog; self.pack_id = pack_id; self.owner_id = owner_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = str(self.answer).strip()
        if not text:
            await interaction.response.send_message("The card cannot be empty.", ephemeral=True); return
        await self.cog.store.add_white(self.pack_id, text)
        pack = await self.cog.store.get(self.pack_id)
        await interaction.response.edit_message(embed=pack_embed(pack), view=PackEditorView(self.cog, self.pack_id, self.owner_id))


class WriteInModal(discord.ui.Modal, title="Write your own answer"):
    answer = discord.ui.TextInput(label="Blank white card", style=discord.TextStyle.paragraph, max_length=220)

    def __init__(self, card_view: "HandView", hand_index: int):
        super().__init__(); self.card_view = card_view; self.hand_index = hand_index

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.card_view.accept_choice(interaction, self.hand_index, str(self.answer).strip() or "[blank]")


def pack_embed(pack: dict[str, Any] | None) -> discord.Embed:
    if not pack:
        return discord.Embed(title="Pack not found")
    black = len(pack.get("black_cards", [])); white = len(pack.get("white_cards", []))
    ready = black >= MIN_BLACK and white >= MIN_WHITE
    embed = discord.Embed(title=f"🗃️ {pack.get('name', 'Untitled Pack')}", description=("✅ Ready to play" if ready else "📝 Draft — keep adding cards"))
    embed.add_field(name="Black prompts", value=f"{black}/{MIN_BLACK} minimum", inline=True)
    embed.add_field(name="White cards", value=f"{white}/{MIN_WHITE} minimum", inline=True)
    embed.add_field(name="Visibility", value=str(pack.get("scope", "personal")).title(), inline=True)
    if not ready:
        embed.add_field(name="Still needed", value=f"{max(0, MIN_BLACK-black)} black • {max(0, MIN_WHITE-white)} white", inline=False)
    embed.set_footer(text="Drafts save automatically. Only ready packs can be selected in a game.")
    return embed


class PackEditorView(discord.ui.View):
    def __init__(self, cog: "MemberGamesCog", pack_id: str, owner_id: int):
        super().__init__(timeout=900); self.cog = cog; self.pack_id = pack_id; self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id and not (isinstance(interaction.user, discord.Member) and _is_staff(interaction.user)):
            await interaction.response.send_message("That pack editor belongs to someone else.", ephemeral=True); return False
        return True

    @discord.ui.button(label="Add Black Card", emoji="⬛", style=discord.ButtonStyle.primary)
    async def add_black(self, interaction, _): await interaction.response.send_modal(AddBlackModal(self.cog, self.pack_id, self.owner_id))

    @discord.ui.button(label="Add White Card", emoji="⬜", style=discord.ButtonStyle.primary)
    async def add_white(self, interaction, _): await interaction.response.send_modal(AddWhiteModal(self.cog, self.pack_id, self.owner_id))

    @discord.ui.button(label="Preview Counts", emoji="🔎", style=discord.ButtonStyle.secondary)
    async def preview(self, interaction, _):
        pack = await self.cog.store.get(self.pack_id); await interaction.response.edit_message(embed=pack_embed(pack), view=self)

    @discord.ui.button(label="View Cards", emoji="📄", style=discord.ButtonStyle.secondary)
    async def view_cards(self, interaction, _):
        pack = await self.cog.store.get(self.pack_id)
        if not pack:
            await interaction.response.send_message("Pack not found.", ephemeral=True); return
        black = pack.get("black_cards", [])[-10:]
        white = pack.get("white_cards", [])[-15:]
        embed = discord.Embed(title=f"📄 {pack.get('name', 'Pack')} — recent cards")
        embed.add_field(name="Black prompts", value="\n".join(f"• {_short(str(c.get('text','')), 170)}" for c in black) or "None yet.", inline=False)
        embed.add_field(name="White cards", value="\n".join(f"• {_short(str(c), 120)}" for c in white) or "None yet.", inline=False)
        embed.set_footer(text="Showing the most recently added cards.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Delete Pack", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def delete(self, interaction, _):
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        ok = await self.cog.store.delete(self.pack_id, interaction.user.id, bool(member and _is_staff(member)))
        await interaction.response.edit_message(content="Pack deleted." if ok else "I couldn't delete that pack.", embed=None, view=None)


class PackListView(discord.ui.View):
    def __init__(self, cog: "MemberGamesCog", packs: list[dict[str, Any]], user_id: int):
        super().__init__(timeout=600); self.cog = cog; self.user_id = user_id
        for pack in packs[:20]:
            button = discord.ui.Button(label=_short(str(pack.get("name", "Pack")), 70), style=discord.ButtonStyle.secondary)
            pid = str(pack["_id"])
            async def callback(interaction: discord.Interaction, pack_id=pid):
                p = await self.cog.store.get(pack_id)
                await interaction.response.edit_message(embed=pack_embed(p), view=PackEditorView(self.cog, pack_id, self.user_id))
            button.callback = callback; self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Open your own pack manager from the member panel.", ephemeral=True); return False
        return True


class ManagePacksView(discord.ui.View):
    def __init__(self, cog: "MemberGamesCog", user_id: int): super().__init__(timeout=600); self.cog = cog; self.user_id = user_id
    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Open your own pack manager.", ephemeral=True); return False
        return True

    @discord.ui.button(label="Create Personal Pack", emoji="🧠", style=discord.ButtonStyle.primary)
    async def personal(self, interaction, _): await interaction.response.send_modal(CreatePackModal(self.cog, "personal"))

    @discord.ui.button(label="Create Server Pack", emoji="🏠", style=discord.ButtonStyle.primary)
    async def server(self, interaction, _): await interaction.response.send_modal(CreatePackModal(self.cog, "server"))

    @discord.ui.button(label="My Packs", emoji="🗃️", style=discord.ButtonStyle.secondary)
    async def mypacks(self, interaction, _):
        packs = await self.cog.store.list_personal(interaction.guild.id, interaction.user.id)
        if not packs:
            await interaction.response.send_message("You do not have any custom packs yet.", ephemeral=True); return
        embed = discord.Embed(title="🗃️ Your card packs", description="Choose one to edit it.")
        await interaction.response.send_message(embed=embed, view=PackListView(self.cog, packs, interaction.user.id), ephemeral=True)


class PackToggleButton(discord.ui.Button):
    def __init__(self, selector: "PackSelectView", pack_id: str, name: str, row: int):
        self.selector = selector; self.pack_id = pack_id
        selected = pack_id in selector.setup.session.selected_pack_ids
        super().__init__(label=_short(name, 65), style=discord.ButtonStyle.success if selected else discord.ButtonStyle.secondary, row=row)

    async def callback(self, interaction: discord.Interaction):
        selected = self.selector.setup.session.selected_pack_ids
        if self.pack_id in selected: selected.remove(self.pack_id)
        else: selected.append(self.pack_id)
        await interaction.response.edit_message(
            embed=discord.Embed(title="Choose packs", description=f"**{len(selected)} selected.** Tap packs to toggle them. You can combine multiple packs."),
            view=PackSelectView(self.selector.setup, self.selector.packs),
        )


class PackSelectView(discord.ui.View):
    def __init__(self, setup: "GameSetupView", packs: list[tuple[str, str]]):
        super().__init__(timeout=600); self.setup = setup; self.packs = packs
        for i, (pid, name) in enumerate(packs[:15]): self.add_item(PackToggleButton(self, pid, name, i // 5))
        done = discord.ui.Button(label="Done", emoji="✅", style=discord.ButtonStyle.primary, row=3)
        async def done_cb(interaction): await interaction.response.edit_message(embed=self.setup.embed(), view=self.setup)
        done.callback = done_cb; self.add_item(done)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.setup.session.host_id:
            await interaction.response.send_message("Only the host can change game setup.", ephemeral=True); return False
        return True


class BlankCountModal(discord.ui.Modal, title="Write-in blank cards"):
    count = discord.ui.TextInput(
        label="Blank cards per hand",
        placeholder="0",
        required=True,
        max_length=2,
    )

    def __init__(self, setup_view):
        super().__init__()
        self.setup_view = setup_view
        self.count.default = str(setup_view.session.rules.blank_per_hand)

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.count.value).strip()
        try:
            value = int(raw)
        except ValueError:
            await interaction.response.send_message("Enter a whole number of blank cards.", ephemeral=True)
            return

        hand_size = self.setup_view.session.rules.hand_size
        if value < 0 or value > hand_size:
            await interaction.response.send_message(
                f"Choose between 0 and {hand_size} blank cards for the current {hand_size}-card hand.",
                ephemeral=True,
            )
            return

        self.setup_view.session.rules.blank_per_hand = value
        await interaction.response.edit_message(embed=self.setup_view.embed(), view=self.setup_view)


class GameSetupView(discord.ui.View):
    CZAR = [("human", "Rotating Czar"), ("ai_if_needed", "AI If Needed"), ("ai_always", "AI Always")]
    STYLES = ["funniest", "most_unhinged", "most_absurd", "darkest", "best_fit"]
    def __init__(self, session: CAHSession): super().__init__(timeout=900); self.session = session

    async def interaction_check(self, interaction):
        if interaction.user.id != self.session.host_id:
            await interaction.response.send_message("Only the host can change these settings.", ephemeral=True); return False
        return True

    def embed(self):
        r = self.session.rules
        czar = dict(self.CZAR).get(r.czar_mode, r.czar_mode)
        embed = discord.Embed(title="🃏 New Card Game", description="Set the rules, choose one or more packs, then open the lobby.")
        embed.add_field(name="Packs", value=f"{len(self.session.selected_pack_ids)} selected", inline=True)
        embed.add_field(name="Czar", value=czar, inline=True)
        embed.add_field(name="AI style", value=r.ai_style.replace('_',' ').title(), inline=True)
        embed.add_field(name="Hand", value=f"{r.hand_size} cards • {r.blank_per_hand} write-in blank(s)", inline=True)
        embed.add_field(name="Win", value=f"First to {r.score_to_win}", inline=True)
        embed.add_field(name="Mommy Player", value="✅ At the table" if r.mommy_player else "❌ Off", inline=True)
        embed.set_footer(text=f"Custom packs need at least {MIN_BLACK} black + {MIN_WHITE} white cards to be playable.")
        return embed

    async def refresh(self, interaction): await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Choose Packs", emoji="🗃️", style=discord.ButtonStyle.primary)
    async def packs(self, interaction, _):
        docs = await self.session.cog.store.list_for_host(self.session.guild.id, self.session.host_id)
        options = [(pid, p["name"]) for pid, p in BUILTIN_PACKS.items()]
        for d in docs:
            if len(d.get("black_cards", [])) >= MIN_BLACK and len(d.get("white_cards", [])) >= MIN_WHITE:
                options.append((str(d["_id"]), str(d.get("name", "Custom Pack"))))
        await interaction.response.edit_message(embed=discord.Embed(title="Choose packs", description="Tap packs to toggle them. You can combine multiple packs."), view=PackSelectView(self, options))

    @discord.ui.button(label="Czar Mode", emoji="👑", style=discord.ButtonStyle.secondary)
    async def czar(self, interaction, _):
        modes = [x[0] for x in self.CZAR]; i = modes.index(self.session.rules.czar_mode); self.session.rules.czar_mode = modes[(i+1)%len(modes)]; await self.refresh(interaction)

    @discord.ui.button(label="AI Humor", emoji="🤖", style=discord.ButtonStyle.secondary)
    async def humor(self, interaction, _):
        s=self.session.rules; i=self.STYLES.index(s.ai_style); s.ai_style=self.STYLES[(i+1)%len(self.STYLES)]; await self.refresh(interaction)

    @discord.ui.button(label="Hand Size", emoji="🖐️", style=discord.ButtonStyle.secondary)
    async def hand(self, interaction, _):
        s = self.session.rules
        s.hand_size = 7 if s.hand_size >= 10 else s.hand_size + 1
        s.blank_per_hand = min(s.blank_per_hand, s.hand_size)
        await self.refresh(interaction)

    @discord.ui.button(label="Write-in Blanks", emoji="✍️", style=discord.ButtonStyle.secondary)
    async def blanks(self, interaction, _):
        await interaction.response.send_modal(BlankCountModal(self))

    @discord.ui.button(label="Score to Win", emoji="🏆", style=discord.ButtonStyle.secondary)
    async def score(self, interaction, _):
        vals=[3,5,7,10]; s=self.session.rules; s.score_to_win=vals[(vals.index(s.score_to_win)+1)%len(vals)] if s.score_to_win in vals else 5; await self.refresh(interaction)

    @discord.ui.button(label="Add/Remove Mommy", emoji="🤖", style=discord.ButtonStyle.secondary)
    async def mommy_player(self, interaction, _):
        self.session.rules.mommy_player = not self.session.rules.mommy_player
        self.session.sync_mommy_player()
        await self.refresh(interaction)

    @discord.ui.button(label="Open Lobby", emoji="🚪", style=discord.ButtonStyle.success)
    async def lobby(self, interaction, _):
        if not self.session.selected_pack_ids:
            await interaction.response.send_message("Choose at least one pack first.", ephemeral=True); return
        self.session.status="lobby"
        await interaction.response.send_message("Lobby opened in this channel.", ephemeral=True)
        self.session.message = await interaction.channel.send(embed=lobby_embed(self.session), view=LobbyView(self.session))



def lobby_embed(session: CAHSession) -> discord.Embed:
    embed=discord.Embed(title="🃏 Card Game Lobby", description="Join the table, then the host can start.")
    embed.add_field(name="Host", value=f"<@{session.host_id}>", inline=True)
    session.sync_mommy_player()
    embed.add_field(name="Players", value=str(len(session.participant_ids())), inline=True)
    embed.add_field(name="Packs", value=str(len(session.selected_pack_ids)), inline=True)
    embed.add_field(name="At the table", value="\n".join(f"• {session.display_player(u)}" for u in session.participant_ids()) or "Nobody yet", inline=False)
    mode=dict(GameSetupView.CZAR).get(session.rules.czar_mode,session.rules.czar_mode)
    embed.set_footer(text=f"{mode} • {session.rules.blank_per_hand} write-in blank(s) per hand • first to {session.rules.score_to_win}")
    return embed


class LobbyView(discord.ui.View):
    def __init__(self, session): super().__init__(timeout=3600); self.session=session

    @discord.ui.button(label="Join", emoji="➕", style=discord.ButtonStyle.success)
    async def join(self, interaction, _):
        if interaction.user.id not in self.session.players:
            self.session.players[interaction.user.id]=PlayerState(interaction.user.id,getattr(interaction.user,"display_name",interaction.user.name))
        await interaction.response.edit_message(embed=lobby_embed(self.session), view=self)

    @discord.ui.button(label="Leave", emoji="➖", style=discord.ButtonStyle.secondary)
    async def leave(self, interaction, _):
        if interaction.user.id==self.session.host_id:
            await interaction.response.send_message("The host cannot leave; use Cancel Game.", ephemeral=True); return
        self.session.players.pop(interaction.user.id,None); await interaction.response.edit_message(embed=lobby_embed(self.session), view=self)

    @discord.ui.button(label="Start", emoji="▶️", style=discord.ButtonStyle.primary)
    async def start(self, interaction, _):
        if interaction.user.id!=self.session.host_id:
            await interaction.response.send_message("Only the host can start the game.", ephemeral=True); return
        await interaction.response.defer()
        ok,reason=await self.session.begin()
        if not ok: await interaction.followup.send(reason, ephemeral=True)

    @discord.ui.button(label="Cancel Game", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction, _):
        if interaction.user.id!=self.session.host_id:
            await interaction.response.send_message("Only the host can cancel the game.", ephemeral=True); return
        await interaction.response.defer(); await self.session.end()


class HandCardButton(discord.ui.Button):
    def __init__(self, hand_view: "HandView", index: int, card: str):
        self.hand_view = hand_view
        self.hand_index = index
        self.card = card
        # Put the actual white-card text on the button, like the original UI.
        # Discord caps button labels, so very long cards are shortened only as needed.
        label = "✍️ Write-in blank" if card == BLANK_TOKEN else _short(card, 75)
        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary if card == BLANK_TOKEN else discord.ButtonStyle.secondary,
            row=index // 5,
        )

    async def callback(self, interaction):
        if self.card == BLANK_TOKEN:
            await interaction.response.send_modal(WriteInModal(self.hand_view, self.hand_index))
        else:
            await self.hand_view.accept_choice(interaction, self.hand_index, self.card)


class HandView(discord.ui.View):
    def __init__(self, session: CAHSession, user_id: int):
        super().__init__(timeout=600); self.session=session; self.user_id=user_id; self.selected_cards:list[str]=[]; self.selected_indexes:list[int]=[]
        player=session.players[user_id]
        for i,card in enumerate(player.hand[:20]): self.add_item(HandCardButton(self,i,card))

    async def interaction_check(self, interaction):
        if interaction.user.id!=self.user_id:
            await interaction.response.send_message("That hand is not yours.", ephemeral=True); return False
        return True

    async def accept_choice(self, interaction, index:int, text:str):
        if index in self.selected_indexes:
            if not interaction.response.is_done(): await interaction.response.send_message("You already picked that slot.", ephemeral=True)
            return
        self.selected_indexes.append(index); self.selected_cards.append(text)
        need=max(1,int((self.session.current_black or {}).get("pick",1)))
        if len(self.selected_cards)>=need:
            ok,msg=await self.session.submit(self.user_id,self.selected_cards[:need],self.selected_indexes[:need])
            if not interaction.response.is_done(): await interaction.response.send_message("✅ "+msg if ok else msg, ephemeral=True)
        else:
            if not interaction.response.is_done(): await interaction.response.send_message(f"Selected **{_short(text,100)}**. Pick {need-len(self.selected_cards)} more.", ephemeral=True)


class RoundView(discord.ui.View):
    def __init__(self, session): super().__init__(timeout=1800); self.session=session

    @discord.ui.button(label="Play Card", emoji="🃏", style=discord.ButtonStyle.primary)
    async def play(self, interaction, _):
        uid=interaction.user.id
        if uid not in self.session.players:
            await interaction.response.send_message("You are not in this game.", ephemeral=True); return
        if uid not in self.session.eligible_submitters():
            await interaction.response.send_message("You're the Czar this round — judge, don't submit.", ephemeral=True); return
        if uid in self.session.submissions:
            await interaction.response.send_message("You already submitted.", ephemeral=True); return
        player = self.session.players[uid]
        black = self.session.current_black or {}
        need = max(1, int(black.get("pick", 1)))
        prompt = str(black.get("text") or "")

        # Keep the black prompt visually separate from the player's answers.
        # Two embeds prevent the question from blending into a long hand on Discord.
        prompt_embed = discord.Embed(
            title="⬛ BLACK CARD",
            description=f"> **{prompt}**",
        )
        prompt_embed.set_footer(text=f"Pick {need} card{'s' if need != 1 else ''} to answer this prompt.")

        hand_embed = discord.Embed(
            title=f"⬜ YOUR HAND — PICK {need}",
            description="Tap the white card you want to play below.",
        )
        hand_embed.set_footer(text="Only you can see this hand.")
        await interaction.response.send_message(
            embeds=[prompt_embed, hand_embed],
            view=HandView(self.session, uid),
            ephemeral=True,
        )

    @discord.ui.button(label="Force Judge", emoji="⚖️", style=discord.ButtonStyle.secondary)
    async def force(self, interaction, _):
        if interaction.user.id!=self.session.host_id:
            await interaction.response.send_message("Only the host can force judging.", ephemeral=True); return
        if not self.session.submissions:
            await interaction.response.send_message("Nobody has submitted yet.", ephemeral=True); return
        await interaction.response.defer(); await self.session.begin_judging()

    @discord.ui.button(label="End Game", emoji="⏹️", style=discord.ButtonStyle.danger)
    async def end(self, interaction, _):
        if interaction.user.id!=self.session.host_id:
            await interaction.response.send_message("Only the host can end the game.", ephemeral=True); return
        await interaction.response.defer(); await self.session.end()


class JudgeButton(discord.ui.Button):
    def __init__(self, view:"JudgingView", idx:int, sub:Submission):
        self.jview=view; self.sub=sub
        super().__init__(label=f"Option {chr(65+idx)}",style=discord.ButtonStyle.primary,row=idx//5)
    async def callback(self,interaction):
        if interaction.user.id!=self.jview.session.current_czar_id:
            await interaction.response.send_message("Only this round's Czar can choose the winner.",ephemeral=True); return
        await interaction.response.defer(); await self.jview.session.finish_round(self.sub.user_id)


class JudgingView(discord.ui.View):
    def __init__(self,session,ordered):
        super().__init__(timeout=900); self.session=session
        for i,sub in enumerate(ordered[:20]): self.add_item(JudgeButton(self,i,sub))


class BetweenRoundsView(discord.ui.View):
    def __init__(self,session): super().__init__(timeout=900); self.session=session
    @discord.ui.button(label="Next Round",emoji="➡️",style=discord.ButtonStyle.success)
    async def next(self,interaction,_):
        if interaction.user.id!=self.session.host_id:
            await interaction.response.send_message("Only the host can start the next round.",ephemeral=True); return
        await interaction.response.defer(); self.session.status="playing"; await self.session.start_round()
    @discord.ui.button(label="End Game",emoji="⏹️",style=discord.ButtonStyle.danger)
    async def end(self,interaction,_):
        if interaction.user.id!=self.session.host_id:
            await interaction.response.send_message("Only the host can end the game.",ephemeral=True); return
        await interaction.response.defer(); await self.session.end()


class GameOverView(discord.ui.View):
    def __init__(self,session): super().__init__(timeout=600); self.session=session
    @discord.ui.button(label="Clear Table",emoji="🧹",style=discord.ButtonStyle.secondary)
    async def clear(self,interaction,_):
        if interaction.user.id!=self.session.host_id:
            await interaction.response.send_message("Only the host can clear the table.",ephemeral=True); return
        await interaction.response.defer(); await self.session.end()


class CAHHomeView(discord.ui.View):
    def __init__(self,cog:"MemberGamesCog",user_id:int): super().__init__(timeout=600); self.cog=cog; self.user_id=user_id
    async def interaction_check(self,interaction):
        if interaction.user.id!=self.user_id:
            await interaction.response.send_message("Open your own game menu from the public member panel.",ephemeral=True); return False
        return True

    @discord.ui.button(label="Create Game",emoji="🎮",style=discord.ButtonStyle.success)
    async def create(self,interaction,_):
        if interaction.channel.id in self.cog.sessions and self.cog.sessions[interaction.channel.id].status!="finished":
            await interaction.response.send_message("There is already a card game running in this channel.",ephemeral=True); return
        if not isinstance(interaction.user,discord.Member): return
        session=CAHSession(self.cog,interaction.guild,interaction.channel,interaction.user); self.cog.sessions[interaction.channel.id]=session
        view=GameSetupView(session); await interaction.response.send_message(embed=view.embed(),view=view,ephemeral=True)

    @discord.ui.button(label="Manage Packs",emoji="🗃️",style=discord.ButtonStyle.primary)
    async def packs(self,interaction,_):
        embed=discord.Embed(title="🗃️ Card Packs",description=f"Create your own decks. A pack becomes playable at **{MIN_BLACK} black prompts + {MIN_WHITE} white cards**. You can combine multiple ready packs in one game.")
        await interaction.response.send_message(embed=embed,view=ManagePacksView(self.cog,interaction.user.id),ephemeral=True)

    @discord.ui.button(label="How It Works",emoji="❓",style=discord.ButtonStyle.secondary)
    async def help(self,interaction,_):
        embed=discord.Embed(title="How the card game works",description="Join a lobby, play the funniest answer to each prompt, and let a human or AI Czar pick the winner. Write-in blanks let you type a custom answer for that round.")
        embed.add_field(name="Czar modes",value="Rotating Czar • AI if the lobby is too small • AI always. Add Mommy as a player and she joins the rotation too.",inline=False)
        embed.add_field(name="Packs",value="Use built-in packs, your own personal packs, shared server packs, or several at once.",inline=False)
        await interaction.response.send_message(embed=embed,ephemeral=True)


class MemberHubView(discord.ui.View):
    """Persistent public panel. Members use buttons; no member slash-command menu required."""
    def __init__(self,bot):
        super().__init__(timeout=None); self.bot=bot

    @discord.ui.button(label="Cards Against Humanity-ish",emoji="🃏",style=discord.ButtonStyle.primary,custom_id="mommy:member:cah")
    async def cah(self,interaction,_):
        cog=self.bot.get_cog("MemberGamesCog")
        if cog is None:
            await interaction.response.send_message("The game table is unavailable right now.",ephemeral=True); return
        embed=discord.Embed(title="🃏 Card Game",description="Create a lobby, manage your decks, use write-in blanks, and mix multiple packs.")
        await interaction.response.send_message(embed=embed,view=CAHHomeView(cog,interaction.user.id),ephemeral=True)


def member_hub_embed(guild: discord.Guild) -> discord.Embed:
    embed=discord.Embed(title="🎮 Mommy.exe — Member Games",description="Tap a button to play. Mommy keeps her staff/voice tools separate from the public member side.")
    embed.add_field(name="🃏 Card Game",value="Custom decks • multiple packs • write-in blanks • human or AI Czar",inline=False)
    embed.set_footer(text=f"{guild.name} • button-only member panel")
    return embed


class MemberGamesCog(commands.Cog):
    def __init__(self,bot):
        self.bot=bot; self.store=PackStore(bot); self.sessions:dict[int,CAHSession]={}
        api_key=os.getenv("OPENAI_API_KEY","").strip(); self.ai=AsyncOpenAI(api_key=api_key) if api_key else None
        self.ai_model=os.getenv("MOMMY_AI_MODEL","gpt-5.6-luna").strip()

    async def cog_load(self): self.bot.add_view(MemberHubView(self.bot))

    async def _configured_staff(self, member: discord.Member) -> bool:
        if _is_staff(member):
            return True
        try:
            profile = await self.bot.database.get_guild_profile(member.guild.id)
            if profile is None:
                profile = await self.bot.database.ensure_guild_profile(member.guild)
            config = (profile or {}).get("access_control", {})
            moderator_role_ids = set()
            for value in config.get("moderator_role_ids", []) or []:
                try:
                    moderator_role_ids.add(int(value))
                except (TypeError, ValueError):
                    pass
            return bool({role.id for role in member.roles} & moderator_role_ids)
        except Exception:
            log.exception("Could not resolve Mommy staff access for %s", member.id)
            return False

    @commands.command(name="mommy")
    async def mommy_entry(self, ctx: commands.Context, *, section: str = "") -> None:
        """Public member games entry; bare !mommy stays staff-aware."""
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        section = (section or "").strip().lower()
        wants_games = section in {"game", "games"}
        wants_staff = section in {"dashboard", "panel", "controls", "settings"}
        is_staff = await self._configured_staff(ctx.author)

        if wants_staff:
            if not is_staff:
                return
            from mommy.dashboard import MommyDashboardView, build_home_embed
            await ctx.send(embed=build_home_embed(), view=MommyDashboardView(self.bot))
            return

        if wants_games or not is_staff:
            await ctx.send(embed=member_hub_embed(ctx.guild), view=MemberHubView(self.bot))
            return

        from mommy.dashboard import MommyDashboardView, build_home_embed
        await ctx.send(embed=build_home_embed(), view=MommyDashboardView(self.bot))

    async def record_win(self,guild_id:int,user_id:int):
        db=self.bot.database._database  # noqa: SLF001
        if db is None:return
        await asyncio.to_thread(db["cah_stats"].update_one,{"guild_id":guild_id,"user_id":user_id},{"$inc":{"wins":1,"rounds_won":1},"$set":{"updated_at":utcnow()}},True)

    async def mommy_submit(self, session: CAHSession) -> None:
        player = session.players.get(MOMMY_PLAYER_ID)
        if not player or MOMMY_PLAYER_ID not in session.eligible_submitters() or MOMMY_PLAYER_ID in session.submissions:
            return
        need = max(1, int((session.current_black or {}).get("pick", 1)))
        available = list(enumerate(player.hand))
        if not available:
            session.fill_hand(player)
            available = list(enumerate(player.hand))
        indexes = await self.ai_play_indexes(session.current_black or {}, player.hand, need, session.rules.ai_style)
        indexes = [i for i in indexes if 0 <= i < len(player.hand)][:need]
        if len(indexes) < need:
            extras = [i for i, _ in available if i not in indexes]
            random.shuffle(extras)
            indexes.extend(extras[: need-len(indexes)])
        cards: list[str] = []
        for idx in indexes:
            card = player.hand[idx]
            if card == BLANK_TOKEN:
                card = await self.ai_write_in(session.current_black or {}, session.rules.ai_style)
            cards.append(card)
        await session.submit(MOMMY_PLAYER_ID, cards, indexes)

    async def ai_play_indexes(self, black: dict[str, Any], hand: list[str], need: int, style: str) -> list[int]:
        regular = [(i, c) for i, c in enumerate(hand) if c != BLANK_TOKEN]
        if self.ai is None:
            pool = [i for i, _ in regular] or list(range(len(hand)))
            random.shuffle(pool)
            return pool[:need]
        lines = [f"{i}: {'[WRITE-IN BLANK]' if c == BLANK_TOKEN else c}" for i, c in enumerate(hand)]
        prompt = f"Prompt: {black.get('text','')}\nPick {need} card slot(s) from this hand to make the funniest answer.\n" + "\n".join(lines) + f"\nHumor style: {style}. Return JSON exactly like {{\"indexes\":[1]}} or {{\"indexes\":[1,4]}}."
        try:
            response = await self.ai.responses.create(model=self.ai_model, instructions="You are Mommy.exe playing a party card game. Choose the funniest card combination from your hand. Return only the requested JSON.", input=prompt, max_output_tokens=80)
            raw=(response.output_text or "").strip(); match=re.search(r"\{.*\}",raw,re.S); data=json.loads(match.group(0) if match else raw)
            vals=[int(x) for x in data.get("indexes",[]) if isinstance(x,(int,float,str))]
            return vals[:need]
        except Exception:
            log.exception("Mommy player card selection failed")
            pool=[i for i,_ in regular] or list(range(len(hand))); random.shuffle(pool); return pool[:need]

    async def ai_write_in(self, black: dict[str, Any], style: str) -> str:
        if self.ai is None:
            return "an aggressively confident bad decision"
        try:
            response = await self.ai.responses.create(model=self.ai_model, instructions="Write one short funny Cards-Against-Humanity-style answer for a blank white card. Do not explain it.", input=f"Prompt: {black.get('text','')}\nHumor style: {style}.", max_output_tokens=50)
            text=(response.output_text or "").strip().strip('\"')
            return _short(text or "an aggressively confident bad decision", 220)
        except Exception:
            return "an aggressively confident bad decision"

    async def ai_choose(self,black:dict[str,Any],submissions:list[Submission],style:str)->tuple[int,str]:
        if not submissions: raise RuntimeError("No submissions")
        if self.ai is None: return random.choice(submissions).user_id,"AI judging is unavailable, so I had to improvise."
        options=[]
        for i,s in enumerate(submissions,1): options.append(f"{i}. " + " / ".join(s.cards))
        style_instruction={
            "funniest":"Choose the answer with the strongest comedic payoff.",
            "most_unhinged":"Choose the funniest answer through chaotic or unhinged absurdity.",
            "most_absurd":"Choose the most absurd answer that still lands as a joke.",
            "darkest":"Choose the darkest humorous answer without rewarding hateful targeting.",
            "best_fit":"Choose the answer that fits the prompt best while still being funny.",
        }.get(style,"Choose the funniest answer.")
        prompt=f"Prompt: {black.get('text','')}\n\nAnonymous answers:\n"+"\n".join(options)+f"\n\n{style_instruction}\nJudge only the joke itself. Do not infer identities. Return JSON exactly like {{\"winner\": 2, \"reason\": \"short funny explanation\"}}."
        try:
            response=await self.ai.responses.create(model=self.ai_model,instructions="You are the anonymous Czar for a party card game. Pick one winning submission fairly. Keep the explanation under 25 words.",input=prompt,max_output_tokens=100)
            raw=(response.output_text or "").strip(); match=re.search(r"\{.*\}",raw,re.S); data=json.loads(match.group(0) if match else raw)
            idx=max(1,min(len(submissions),int(data.get("winner",1))))-1; reason=str(data.get("reason","")).strip()
            return submissions[idx].user_id,reason
        except Exception:
            log.exception("AI Czar failed")
            return random.choice(submissions).user_id,"My judging brain glitched, so this round was decided by chaos."


async def setup(bot): await bot.add_cog(MemberGamesCog(bot))

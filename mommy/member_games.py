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

MIN_BLACK = 20
MIN_WHITE = 30
BLANK_TOKEN = "__WRITE_IN_BLANK__"
MAX_WRITE_IN_BLANKS_PER_HAND = 3
MOMMY_PLAYER_ID = -1
DADDY_PLAYER_ID = -2
AI_PLAYER_IDS = {MOMMY_PLAYER_ID, DADDY_PLAYER_ID}

# Original starter content so the game works immediately without copying CAH card text.
BUILTIN_PACKS: dict[str, dict[str, Any]] = {'builtin:chaos': {'name': 'House Chaos',
                   'description': 'General-purpose nonsense for a chaotic server night.',
                   'black_cards': [{'text': 'The group chat went silent after ____.', 'pick': 1},
                                   {'text': 'My villain origin story started with ____.', 'pick': 1},
                                   {'text': "Tonight's terrible idea is ____ followed immediately by ____.", 'pick': 2},
                                   {'text': 'The real reason we got banned from the function was ____.', 'pick': 1},
                                   {'text': 'Nothing ruins a perfectly good Tuesday like ____.', 'pick': 1},
                                   {'text': 'I opened the fridge and found ____ staring back at me.', 'pick': 1},
                                   {'text': "The server's new mascot is officially ____.", 'pick': 1},
                                   {'text': 'Emergency meeting: who left ____ in the voice channel?', 'pick': 1},
                                   {'text': 'The apology would have worked if they had not mentioned ____.', 'pick': 1},
                                   {'text': 'Step one: ____. Step two: ____. Step three: pretend this was intentional.',
                                    'pick': 2},
                                   {'text': 'Nobody expected the emergency contact to be ____.', 'pick': 1},
                                   {'text': 'The landlord specifically asked us to stop storing ____ in the hallway.',
                                    'pick': 1},
                                   {'text': 'The neighborhood app is currently arguing about ____.', 'pick': 1},
                                   {'text': 'The potluck was canceled after someone brought ____.', 'pick': 1},
                                   {'text': 'Our security deposit disappeared because of ____.', 'pick': 1},
                                   {'text': 'The family group chat now has a rule against ____.', 'pick': 1},
                                   {'text': 'I knew the night was over when somebody yelled about ____.', 'pick': 1},
                                   {'text': 'The smoke alarm is not supposed to react to ____.', 'pick': 1},
                                   {'text': 'We could explain ____ or ____, but absolutely not both.', 'pick': 2},
                                   {'text': 'The house meeting ended with a unanimous ban on ____.', 'pick': 1}],
                   'white_cards': ['a suspiciously warm hot dog',
                                   'three raccoons with a plan',
                                   'weaponized incompetence',
                                   'a Costco-sized tub of bad decisions',
                                   'an emotional-support traffic cone',
                                   'the forbidden Tupperware',
                                   'an aggressively moist handshake',
                                   'a group project nobody agreed to',
                                   "the world's loudest flip-flop",
                                   'a chair that has seen too much',
                                   'a deeply personal beef with a goose',
                                   'feral confidence',
                                   'one incredibly judgmental toddler',
                                   'a cursed Facebook Marketplace couch',
                                   'the family-size consequences',
                                   'an unnecessary amount of ranch',
                                   'a haunted air fryer',
                                   'the audacity on clearance',
                                   'a 3 a.m. voice note',
                                   'a legally distinct goblin',
                                   'the last clean spoon',
                                   'a suspicious hole in the drywall',
                                   'six feet of extension cord and no supervision',
                                   'an apology written in Comic Sans',
                                   'a bag of mystery cables',
                                   'a gas-station sushi prophecy',
                                   'the consequences of free will',
                                   'an unpaid emotional invoice',
                                   'an industrial quantity of glitter',
                                   "the world's least convincing disguise",
                                   'a Roomba with unresolved anger',
                                   'a wet sock in a dry room',
                                   'a tactical Capri Sun',
                                   'the smell of burnt popcorn and fear',
                                   'one final bad idea for the road',
                                   'Mold.',
                                   'Crumbs.',
                                   'Grease.',
                                   'Laundry.',
                                   'Rent.']},
 'builtin:gaming': {'name': 'Terminally Online',
                    'description': 'Gaming, Discord, and internet-brain nonsense.',
                    'black_cards': [{'text': 'The patch notes forgot to mention ____.', 'pick': 1},
                                    {'text': 'Our raid wiped because of ____.', 'pick': 1},
                                    {'text': 'Discord added a new feature: ____.', 'pick': 1},
                                    {'text': 'The speedrun category nobody asked for is ____%.', 'pick': 1},
                                    {'text': 'My setup is powered entirely by ____.', 'pick': 1},
                                    {'text': 'The final boss was just ____ wearing ____.', 'pick': 2},
                                    {'text': 'I got kicked from the lobby for ____.', 'pick': 1},
                                    {'text': 'The modpack broke after I installed ____.', 'pick': 1},
                                    {'text': 'Nothing says competitive integrity like ____.', 'pick': 1},
                                    {'text': 'My new gamer tag is basically ____ plus ____.', 'pick': 2},
                                    {'text': 'The voice chat peaked when someone accidentally streamed ____.',
                                     'pick': 1},
                                    {'text': 'My inventory is 90% ____ and 10% regret.', 'pick': 1},
                                    {'text': 'The new meta is apparently ____.', 'pick': 1},
                                    {'text': 'The server owner rage-quit over ____.', 'pick': 1},
                                    {'text': 'The anti-cheat flagged me for ____.', 'pick': 1},
                                    {'text': 'The tutorial somehow requires ____ and ____.', 'pick': 2},
                                    {'text': 'My entire build falls apart without ____.', 'pick': 1},
                                    {'text': 'The patch fixed one bug and introduced ____.', 'pick': 1},
                                    {'text': 'The most cursed Steam achievement is awarded for ____.', 'pick': 1},
                                    {'text': 'I knew the lobby was doomed when someone equipped ____.', 'pick': 1}],
                    'white_cards': ['a microphone peaking into another dimension',
                                    'forty-seven browser tabs',
                                    'a controller with stick drift',
                                    'the loading screen tip nobody reads',
                                    'a Discord mod on their lunch break',
                                    'an RGB-powered personality',
                                    'a keyboard full of crumbs',
                                    "the teammate who says 'trust me'",
                                    'an update that somehow made it worse',
                                    'a 900-page mod compatibility spreadsheet',
                                    'one suspiciously specific ban reason',
                                    'a headset held together by tape',
                                    'a ping spike at the worst possible moment',
                                    'a loot box full of disappointment',
                                    'an NPC with better social skills',
                                    'a save file named FINAL_final_REAL',
                                    "the world's angriest tutorial",
                                    'a server restart with no warning',
                                    'a five-hour character creator',
                                    'a patch downloaded over hotel Wi-Fi',
                                    'an emote used as legal evidence',
                                    'a keyboard shortcut nobody remembers',
                                    'a username from 2012',
                                    'a boss fight against the settings menu',
                                    'an accidental hot mic confession',
                                    'a deeply cursed custom skin',
                                    'the one person still using push-to-talk correctly',
                                    'a frame rate measured in vibes',
                                    'a mod that adds seventeen kinds of cheese',
                                    'an inventory full of junk I might need later',
                                    'the admin typing...',
                                    'a rage quit with excellent comedic timing',
                                    'a tutorial skipped with confidence',
                                    'a graphics card begging for mercy',
                                    'the sacred mute button',
                                    'Lag.',
                                    'Discord.',
                                    'Sweat.',
                                    'Respawn.',
                                    'Nerf.']},
 'builtin:foul': {'name': 'Absolutely Foul',
                  'description': 'After-dark filth, bodily disasters, cursed hookups, and criminal group-chat energy.',
                  'black_cards': [{'text': 'The bathroom was permanently condemned after ____.', 'pick': 1},
                                  {'text': 'I knew the hookup was over when they pulled out ____.', 'pick': 1},
                                  {'text': 'Nothing says romance like ____ and ____ on a fitted sheet.', 'pick': 2},
                                  {'text': 'The health inspector took one look at ____ and simply went home.',
                                   'pick': 1},
                                  {'text': 'My search history can be explained by ____ but absolutely not ____.',
                                   'pick': 2},
                                  {'text': 'The porta-potty started rocking because of ____.', 'pick': 1},
                                  {'text': 'Grandma did not survive Thanksgiving after learning about ____.',
                                   'pick': 1},
                                  {'text': "The smell wasn't sewage. It was actually ____.", 'pick': 1},
                                  {'text': 'I have been legally advised to stop calling it ____.', 'pick': 1},
                                  {'text': "The worst thing to hear during sex is: 'Don't worry about ____.'",
                                   'pick': 1},
                                  {'text': "This year's county fair proudly introduces ____ on a stick.", 'pick': 1},
                                  {'text': 'The group chat unanimously agreed never to discuss ____ again.', 'pick': 1},
                                  {'text': 'My body rejected ____ but unfortunately kept ____.', 'pick': 2},
                                  {'text': 'The stain on the mattress turned out to be ____.', 'pick': 1},
                                  {'text': 'The wedding was beautiful until someone unleashed ____.', 'pick': 1},
                                  {'text': 'Doctors hate this one weird trick involving ____.', 'pick': 1},
                                  {'text': 'My last shred of dignity was found underneath ____.', 'pick': 1},
                                  {'text': 'I would have gotten away with it too, if not for ____ and that goddamn '
                                           'smell.',
                                   'pick': 1},
                                  {'text': 'The forensic swab came back positive for ____.', 'pick': 1},
                                  {'text': 'The motel charged an additional cleaning fee for ____.', 'pick': 1}],
                  'white_cards': ['a warm ham pocket',
                                  'porta-potty backsplash',
                                  'a medically concerning amount of mayonnaise',
                                  'the forbidden meat drawer',
                                  'a fart with paperwork',
                                  'an aggressively sweaty ass crack',
                                  'a suspiciously damp mattress',
                                  'gas-station bathroom intimacy',
                                  'a crusty little mystery towel',
                                  'the smell of hot pennies and regret',
                                  'a condom full of soup',
                                  'an emotional-support butt plug',
                                  'a gallon Ziploc bag of loose spaghetti',
                                  'the downstairs deli counter',
                                  'a moist rotisserie-chicken handshake',
                                  'a booty call with a court date',
                                  'an unlicensed Brazilian wax in a kitchen chair',
                                  'a used thong stuck to the ceiling fan',
                                  "the wettest cough you've ever heard",
                                  'a suspiciously crunchy bedsheet',
                                  'a deep-fried yeast infection joke',
                                  'a fart so violent it changed the thermostat',
                                  "a freezer bag labeled 'DO NOT EAT'",
                                  'a motel hot tub with visible history',
                                  'the last clean pair of underwear in the county',
                                  'a sloppy handful of meat juice',
                                  'an ass-print on a leather couch',
                                  'a toe with the confidence of a thumb',
                                  'a lukewarm jar of hot-dog water',
                                  'the sound of cheeks clapping in a quiet house',
                                  'a cursed amount of discharge',
                                  'a pubic hair with its own zip code',
                                  'a gas-station egg salad incident',
                                  'a belly-button smell that files taxes',
                                  'an entire rotisserie chicken eaten over the sink',
                                  'a bra full of emergency snacks',
                                  'a mystery fluid under blacklight',
                                  'a yeast infection with main-character energy',
                                  'the forbidden bathroom hand towel',
                                  "a meatball rolling out of somebody's purse",
                                  'a sweaty folding chair at a family reunion',
                                  'the unmistakable squelch of poor decisions',
                                  'a dirty sock used as medical equipment',
                                  'an unwashed hoodie with emotional significance',
                                  'a toenail clipping in the butter dish',
                                  "a hot burp directly into another person's mouth",
                                  'a sandwich bag full of teeth',
                                  'an absolutely disrespectful amount of back sweat',
                                  'a wet fart during a moment of silence',
                                  'a communal jar of Vaseline',
                                  'the kind of underwear you bury instead of wash',
                                  "a suspicious bump that definitely wasn't there Tuesday",
                                  'the neighborhood sex couch',
                                  'a damp handful of shredded cheese',
                                  'three days of swamp ass',
                                  'a used pregnancy test in the silverware drawer',
                                  'a shower drain hairball with legal custody rights',
                                  'a sneeze that launched something solid',
                                  'a ham candle burning in the bedroom',
                                  'a rogue nipple piercing caught in upholstery',
                                  "the world's angriest hemorrhoid",
                                  'a suspiciously intimate jar of peanut butter',
                                  'a butt dial during something deeply private',
                                  'a wet wipe doing the work of God',
                                  'an adult diaper full of ambition',
                                  'a Taco Bell bathroom exorcism',
                                  "a sweaty stranger whispering 'trust the process'",
                                  'a fistful of room-temperature shrimp',
                                  'an air fryer coated in ancient grease',
                                  'a fart trapped under a weighted blanket',
                                  'a mysterious rash shaped like Florida',
                                  'a mattress that knows too much',
                                  'Moist.',
                                  'Foreskin.',
                                  'Yeast.',
                                  'Crust.',
                                  'Discharge.']},
 'builtin:foul2': {'name': 'Unfit for Company',
                   'description': 'A second after-dark pack full of cursed hookups, bathroom crimes, mystery fluids, '
                                  'and absolutely no dignity.',
                   'black_cards': [{'text': 'The Airbnb host charged us $900 because of ____.', 'pick': 1},
                                   {'text': 'I thought it was lube until I realized it was ____.', 'pick': 1},
                                   {'text': 'The date was going perfectly until ____ crawled out from under ____.',
                                    'pick': 2},
                                   {'text': 'There are some smells Febreze cannot defeat, especially ____.', 'pick': 1},
                                   {'text': "The emergency room nurse took one look at ____ and whispered, 'Again?'",
                                    'pick': 1},
                                   {'text': "The hotel security footage will forever be known as 'the ____ incident.'",
                                    'pick': 1},
                                   {'text': 'My safe word is apparently ____.', 'pick': 1},
                                   {'text': 'The worst possible thing to find floating in the hot tub is ____.',
                                    'pick': 1},
                                   {'text': 'Dinner was ruined when somebody put ____ next to ____.', 'pick': 2},
                                   {'text': 'Nothing bonds a family faster than pretending not to notice ____.',
                                    'pick': 1},
                                   {'text': 'The neighbors called the police after hearing ____ through the wall.',
                                    'pick': 1},
                                   {'text': 'The bathroom fan was not designed to handle ____.', 'pick': 1},
                                   {'text': 'The obituary left out the part about ____.', 'pick': 1},
                                   {'text': 'My therapist says I need to stop romanticizing ____.', 'pick': 1},
                                   {'text': 'The wedding registry included ____ and, for some reason, ____.',
                                    'pick': 2},
                                   {'text': 'The group vacation ended early because somebody packed ____.', 'pick': 1},
                                   {'text': 'HR specifically asked me to stop bringing up ____ at lunch.', 'pick': 1},
                                   {'text': 'Nobody warned me that adulthood would involve this much ____.', 'pick': 1},
                                   {'text': 'The group chat needed a content warning after ____.', 'pick': 1},
                                   {'text': 'The washing machine gave up halfway through cleaning ____.', 'pick': 1}],
                   'white_cards': ['a bath towel with a suspicious stiff corner',
                                   'the forbidden bedside Gatorade',
                                   'a mystery stain that survived three landlords',
                                   'a fart that arrived with a sequel',
                                   'an upsettingly warm toilet seat',
                                   'a crusty sock with seniority',
                                   'the emotional aftermath of gas-station nachos',
                                   'a motel comforter with generational trauma',
                                   'a used washcloth in a Ziploc bag',
                                   'an unreasonably loud suction noise',
                                   "a mouthful of somebody else's hair",
                                   'an expired bottle of massage oil from 2009',
                                   'a shower curtain that touched too much',
                                   'the suspicious damp spot nobody claimed',
                                   'a belly button full of lint and secrets',
                                   'a clogged toilet making eye contact',
                                   'a thong being used as emergency equipment',
                                   'a bedroom fan spreading consequences evenly',
                                   'a single pube floating in the sink',
                                   'a wet couch cushion with no explanation',
                                   'a sock under the bed that has become sentient',
                                   'an industrial-strength booty sweat situation',
                                   "a hot pocket cooling on somebody's bare stomach",
                                   'a gas-station condom and blind optimism',
                                   'a shower drain that growls when fed',
                                   'a suspiciously slippery bathroom floor',
                                   'a forgotten chicken wing in the bedsheets',
                                   'the sound of a plunger losing hope',
                                   'a motel ice bucket being used incorrectly',
                                   'a body pillow with a criminal amount of history',
                                   'an ass crack full of beach sand',
                                   'a bathroom rug that squishes when stepped on',
                                   'a mouthwash bottle filled with something else',
                                   'a baggie of unidentified toenails',
                                   'a one-night stand who brought Tupperware',
                                   'a forehead kiss immediately after Taco Bell',
                                   'a backseat covered in mystery crumbs and shame',
                                   'a shower loofah old enough to vote',
                                   'a suspiciously warm jar of pickles',
                                   'an accidental nipple piercing tug-of-war',
                                   'a toilet brush being promoted beyond its job description',
                                   'a deeply personal relationship with baby powder',
                                   'a bedsheet with a crunchy side and a soft side',
                                   'a handful of damp cereal',
                                   'an unsolicited close-up of a rash',
                                   'a couch that makes a wet noise when you sit down',
                                   'the smell of old fries and poor judgment',
                                   'a half-melted ice cube found somewhere alarming',
                                   'a bathroom trash can full of unanswered questions',
                                   'a condom wrapper stuck to a Croc',
                                   'a humid room with no visible source of humidity',
                                   'an ankle monitor tangled in satin sheets',
                                   'a wet wipe folded with military precision',
                                   'a bottle of lotion that has seen active duty',
                                   'a suspiciously sticky remote control',
                                   'a toilet seat warmer powered by regret',
                                   'a sweaty wig on the passenger seat',
                                   'a pair of underwear abandoned like a crime scene',
                                   'a mystery smell trapped in a hoodie',
                                   'a bathroom stall with excellent acoustics',
                                   'the unmistakable texture of day-old body glitter',
                                   'a used Band-Aid in the chip bowl',
                                   'a hot dog rolling loose in a purse',
                                   'a shower cap full of shredded cheese',
                                   'an aggressively intimate amount of ranch',
                                   'a fitted sheet that should be submitted as evidence',
                                   'a warm can of energy drink from under the bed',
                                   'a bedroom trash bag doing heroic work',
                                   'a damp neck pillow from a bus station',
                                   'a single flip-flop beside an unexplained puddle',
                                   'the kind of fart that ends negotiations',
                                   'a public restroom hand dryer aimed somewhere personal',
                                   'a hotel Bible hiding something sticky',
                                   "a gallon jug labeled 'DO NOT SHAKE'",
                                   'Sweat.',
                                   'Gooch.',
                                   'Grease.',
                                   'Mucus.',
                                   'Stench.']},
 'builtin:digital_circus': {'name': 'The Amazing Digital Circus',
                            'description': 'Digital existential dread, Caine-approved nonsense, and absolutely normal '
                                           'circus activities.',
                            'black_cards': [{'text': "Caine's newest totally safe adventure involves ____.", 'pick': 1},
                                            {'text': 'Pomni finally snapped after discovering ____ behind the exit '
                                                     'door.',
                                             'pick': 1},
                                            {'text': "Jax got banned from today's adventure for ____.", 'pick': 1},
                                            {'text': 'Kinger became unexpectedly lucid and warned us about ____.',
                                             'pick': 1},
                                            {'text': 'Ragatha is smiling through the emotional damage caused by ____.',
                                             'pick': 1},
                                            {'text': "Gangle's comedy mask shattered immediately after ____.",
                                             'pick': 1},
                                            {'text': 'Zooble refused to participate until Caine removed ____.',
                                             'pick': 1},
                                            {'text': 'Bubble has been specifically asked to stop licking ____.',
                                             'pick': 1},
                                            {'text': 'The Amazing Digital Circus proudly presents: ____ versus ____.',
                                             'pick': 2},
                                            {'text': 'The next abstracted character was last seen screaming about '
                                                     '____.',
                                             'pick': 1},
                                            {'text': 'The exit door opened, but unfortunately it only led to ____.',
                                             'pick': 1},
                                            {'text': "Today's adventure has one rule: absolutely no ____.", 'pick': 1},
                                            {'text': "Caine insists that ____ is 'great for morale.'", 'pick': 1},
                                            {'text': 'Nothing says digital immortality like ____ and ____.', 'pick': 2},
                                            {'text': 'The circus gift shop is now selling ____ for 30,000 digital '
                                                     'tokens.',
                                             'pick': 1},
                                            {'text': 'Caine promises this adventure is only mildly traumatizing: ____.',
                                             'pick': 1},
                                            {'text': "Jax's newest hobby is replacing everyone's belongings with ____.",
                                             'pick': 1},
                                            {'text': 'Pomni found a hidden room containing nothing but ____.',
                                             'pick': 1},
                                            {'text': 'Kinger says the secret to surviving the circus is ____.',
                                             'pick': 1},
                                            {'text': 'Ragatha finally stopped being nice after ____.', 'pick': 1},
                                            {'text': "Gangle's new mask represents the emotion of ____.", 'pick': 1},
                                            {'text': "Zooble's missing piece was last seen attached to ____.",
                                             'pick': 1},
                                            {'text': 'Bubble has been banned from the kitchen after ____.', 'pick': 1},
                                            {'text': 'Caine accidentally generated an NPC obsessed with ____.',
                                             'pick': 1},
                                            {'text': 'The circus talent show was cancelled because of ____.',
                                             'pick': 1},
                                            {'text': "Pomni's escape plan requires ____ and an unreasonable amount of "
                                                     '____.',
                                             'pick': 2},
                                            {'text': "The next adventure is called 'Please Stop Screaming About ____.'",
                                             'pick': 1},
                                            {'text': "Jax insists ____ was 'just a prank.'", 'pick': 1},
                                            {'text': 'The void is now accepting applications for ____.', 'pick': 1},
                                            {'text': 'The circus finally found an exit, but Caine filled it with ____.',
                                             'pick': 1}],
                            'white_cards': ["Pomni's thousand-yard stare",
                                            'Caine inventing a new OSHA violation',
                                            'Jax being helpful for suspicious reasons',
                                            'Kinger hiding in his pillow fort',
                                            'Ragatha holding the group together with emotional duct tape',
                                            "Gangle's last surviving comedy mask",
                                            'Zooble removing a body part out of pure annoyance',
                                            'Bubble eating something that was definitely not food',
                                            'an exit door with commitment issues',
                                            'a hallway that absolutely was not there five minutes ago',
                                            'a digital nervous breakdown',
                                            'an adventure nobody consented to',
                                            "Caine's legally questionable game design",
                                            'Jax committing a misdemeanor for enrichment',
                                            'Pomni asking one perfectly reasonable question',
                                            'Kinger suddenly becoming the smartest person in the room',
                                            "Ragatha saying 'it's fine' while nothing is fine",
                                            "Gangle's emotional support ribbon",
                                            "Zooble's detachable patience",
                                            'Bubble with unrestricted access to the kitchen',
                                            'the consequences of digital immortality',
                                            'an NPC developing suspiciously real feelings',
                                            'a horrifying amount of confetti',
                                            'the void staring back',
                                            'a door labeled EXIT in Comic Sans',
                                            'a circus tent powered by psychological damage',
                                            "Caine's emergency pair of novelty teeth",
                                            "Jax's completely punchable confidence",
                                            'Pomni speedrunning all five stages of grief',
                                            'Kinger explaining something terrifyingly profound',
                                            "Ragatha's customer-service smile",
                                            'Gangle crying in 4K',
                                            'Zooble opting out of the plot',
                                            'Bubble saying something deeply concerning',
                                            'a digital feast nobody can actually digest',
                                            'a side quest with permanent emotional consequences',
                                            "being trapped forever with the world's most enthusiastic ringmaster",
                                            'a fake exit placed there for character development',
                                            'an abstracted coworker in the basement',
                                            'three seconds of genuine peace before Caine appears',
                                            'a giant novelty hammer labeled THERAPY',
                                            "the world's least comforting carnival music",
                                            'a suspiciously sentient mannequin',
                                            'an existential crisis with ray tracing',
                                            'a character model held together by panic',
                                            'a perfectly normal amount of screaming',
                                            'Caine turning trauma into a team-building exercise',
                                            'Jax pressing the button marked DO NOT PRESS',
                                            'an NPC funeral with catering',
                                            'the horrifying realization that respawning is mandatory',
                                            'Pomni.',
                                            'Caine.',
                                            'Ragatha.',
                                            'Zooble.',
                                            'Gangle.']},
 'builtin:youtube_gremlins': {'name': 'YouTube Gremlin Energy',
                              'description': 'CaseOh streams meet the Brandon Rogers multiverse. Loud, chaotic, and '
                                             'legally concerning.',
                              'black_cards': [{'text': 'CaseOh banned someone from chat for mentioning ____.',
                                               'pick': 1},
                                              {'text': 'The horror game stopped being scary once CaseOh encountered '
                                                       '____.',
                                               'pick': 1},
                                              {'text': "Chat's newest nickname for CaseOh is somehow ____.", 'pick': 1},
                                              {'text': "CaseOh's DoorDash driver arrived carrying ____ and ____.",
                                               'pick': 2},
                                              {'text': 'The stream ended immediately after ____ appeared on screen.',
                                               'pick': 1},
                                              {'text': "In the Brandon Rogers cinematic universe, today's emergency is "
                                                       '____.',
                                               'pick': 1},
                                              {'text': 'Helen Brownstein has been permanently banned from ____.',
                                               'pick': 1},
                                              {'text': "Bryce Tankthrust's newest business venture is ____.",
                                               'pick': 1},
                                              {'text': 'The PTA meeting was derailed by ____ and a suspicious amount '
                                                       'of ____.',
                                               'pick': 2},
                                              {'text': 'Nobody in this Brandon Rogers sketch was prepared for ____.',
                                               'pick': 1},
                                              {'text': 'The YouTube algorithm recommended three hours of ____.',
                                               'pick': 1},
                                              {'text': 'This stream is sponsored by ____ for reasons nobody '
                                                       'understands.',
                                               'pick': 1},
                                              {'text': 'The comment section has unanimously decided that ____ is '
                                                       'canon.',
                                               'pick': 1},
                                              {'text': 'CaseOh versus the Brandon Rogers multiverse: the final '
                                                       'challenge is ____.',
                                               'pick': 1},
                                              {'text': 'The collab was going fine until somebody brought ____.',
                                               'pick': 1},
                                              {'text': "CaseOh's chat immediately regretted bringing up ____.",
                                               'pick': 1},
                                              {'text': 'The stream became unwatchable after chat spammed ____.',
                                               'pick': 1},
                                              {'text': "CaseOh's newest enemy is apparently ____.", 'pick': 1},
                                              {'text': 'The next ban-worthy word in chat is ____.', 'pick': 1},
                                              {'text': 'CaseOh opened the fridge and discovered ____ beside ____.',
                                               'pick': 2},
                                              {'text': "Brandon Rogers' newest character is somehow a ____ with ____.",
                                               'pick': 2},
                                              {'text': 'Helen turned a normal grocery trip into an incident involving '
                                                       '____.',
                                               'pick': 1},
                                              {'text': 'Bryce Tankthrust just acquired a controlling interest in ____.',
                                               'pick': 1},
                                              {'text': 'The neighborhood watch meeting ended after someone admitted to '
                                                       '____.',
                                               'pick': 1},
                                              {'text': 'The sketch escalated from zero to ____ in under ten seconds.',
                                               'pick': 1},
                                              {'text': 'YouTube demonetized the video because of ____.', 'pick': 1},
                                              {'text': 'The thumbnail needed three arrows pointing directly at ____.',
                                               'pick': 1},
                                              {'text': "CaseOh's next rage compilation will be sponsored by ____.",
                                               'pick': 1},
                                              {'text': "Brandon Rogers presents: 'A Completely Normal Family Dealing "
                                                       "With ____.'",
                                               'pick': 1},
                                              {'text': 'The collab ended with ____ getting banned and ____ getting '
                                                       'monetized.',
                                               'pick': 2},
                                              {'text': 'Jenna Marbles has decided to spend the next three hours '
                                                       'turning herself into ____.',
                                               'pick': 1},
                                              {'text': 'Kermit is crying because someone put ____ within six feet of '
                                                       'him.',
                                               'pick': 1},
                                              {'text': 'Peach has once again been forced to supervise ____.',
                                               'pick': 1},
                                              {'text': 'Marbles was last seen being carried inside ____.', 'pick': 1},
                                              {'text': 'Bunny discovered ____ on the kitchen counter and now nobody is '
                                                       'safe.',
                                               'pick': 1},
                                              {'text': 'Julien turned a normal cooking video into ____.', 'pick': 1},
                                              {'text': 'The next dog birthday theme is ____ and ____.', 'pick': 2},
                                              {'text': "Jenna's latest unnecessary DIY requires ____.", 'pick': 1},
                                              {'text': 'Kermit has been diagnosed with a severe case of ____.',
                                               'pick': 1},
                                              {'text': 'The household group chat has one new rule: never let Julien '
                                                       'near ____.',
                                               'pick': 1}],
                              'white_cards': ['CaseOh reading one chat message and immediately regretting it',
                                              'a chat message getting banned before the sentence is finished',
                                              'CaseOh arguing with a fictional food item',
                                              'a horror-game monster getting roasted instead of feared',
                                              "the world's most aggressive DoorDash notification",
                                              'chat typing the same joke 400 times',
                                              'a donation designed exclusively to start an argument',
                                              'CaseOh yelling at a completely innocent NPC',
                                              'one food take powerful enough to divide the entire stream',
                                              'a tier list that becomes a federal incident',
                                              'an accidental five-minute rant about ranch',
                                              'a stream title that ages terribly within six minutes',
                                              'the chat moving too fast for human comprehension',
                                              'a jumpscare followed by immediate disrespect',
                                              'CaseOh threatening to ban the entire internet',
                                              'a suspiciously personal feud with a menu screen',
                                              'Helen Brownstein escalating a minor inconvenience into a felony',
                                              'Bryce Tankthrust monetizing a human rights violation',
                                              'a Brandon Rogers character entering at maximum volume',
                                              'a suburban meltdown with excellent lighting',
                                              'a minivan full of unresolved character arcs',
                                              'a PTA meeting that requires police backup',
                                              'a customer-service interaction from hell',
                                              'a wig with more personality than most people',
                                              'an HR complaint written in all caps',
                                              'a suburban mom with the energy of a natural disaster',
                                              'a fake accent that somehow gets stronger under pressure',
                                              'a family dinner becoming a season finale',
                                              'a sketch character making the worst possible entrance',
                                              'a completely unnecessary costume change',
                                              'an argument that starts at ten and somehow gets louder',
                                              'a backyard containing at least three crimes',
                                              'a motivational speech delivered by the least qualified person alive',
                                              'an aggressively specific insult',
                                              'a YouTube thumbnail with seventeen facial expressions',
                                              'a demonetization email arriving mid-sentence',
                                              'the algorithm rewarding the worst behavior imaginable',
                                              'a comment pinned purely out of spite',
                                              'a sponsorship nobody remembers agreeing to',
                                              'a ring light witnessing things it can never unsee',
                                              'a creator apology filmed next to an unplugged ukulele',
                                              'a collab held together by caffeine and bad judgment',
                                              'a livestream chat functioning as a hostile jury',
                                              'a microphone peaking from pure emotional damage',
                                              "an editor deciding this is tomorrow's problem",
                                              'a camera battery dying at the funniest possible moment',
                                              'a merch drop that looks vaguely threatening',
                                              'a reaction face powerful enough to alter the timeline',
                                              'YouTube subtitles giving up completely',
                                              'the exact moment the bit goes way too far',
                                              'Jenna Marbles turning herself into a toothbrush',
                                              'Jenna making something she absolutely did not need to make',
                                              'Kermit the Italian Greyhound screaming at the concept of dinner',
                                              'Kermit crying because somebody looked at him',
                                              'Peach being suspiciously competent compared with Kermit',
                                              'Marbles existing at the approximate size of a dinner roll',
                                              'Bunny standing six feet tall and still being a baby',
                                              'a dog birthday party more organized than most weddings',
                                              'Kermit having another nasty-boy incident',
                                              'Peach quietly judging the entire household',
                                              'Marbles getting carried around like a Victorian coin purse',
                                              'Bunny discovering she is physically capable of reaching the counter',
                                              "Jenna saying 'what are this' to something deeply avoidable",
                                              'Julien turning a simple task into Aries Kitchen',
                                              'an Aries man being given unsupervised access to a kitchen',
                                              'Jenna attempting a beauty hack nobody should attempt',
                                              'a craft project becoming structurally unsound',
                                              'a leisure suit for a dog who never asked for one',
                                              'Kermit vibrating with unnecessary anxiety',
                                              'a tiny Chihuahua surviving entirely through spite',
                                              'four dogs causing one extremely specific household emergency',
                                              "Jenna's green screen doing emotional labor",
                                              'a dog costume that immediately becomes a lifestyle',
                                              "Julien yelling 'eh bep bep bep' at the situation",
                                              'a YouTube video that starts as a craft and ends as a cry for help',
                                              'CaseOh.',
                                              'Jenna.',
                                              'Julien.',
                                              'Kermit.',
                                              'Peach.']},
 'builtin:hazbin': {'name': 'Hazbin Hotel',
                    'description': 'Hellish hotel management, redemption disasters, radio-demon nonsense, and infernal '
                                   'workplace drama.',
                    'black_cards': [{'text': "Charlie's newest redemption exercise is ____.", 'pick': 1},
                                    {'text': 'Vaggie knew the hotel meeting was doomed when ____ walked in.',
                                     'pick': 1},
                                    {'text': "Alastor's radio show has been interrupted by ____.", 'pick': 1},
                                    {'text': 'Angel Dust has been asked, once again, to stop bringing ____ into the '
                                             'lobby.',
                                     'pick': 1},
                                    {'text': 'Husk will pour you a drink if you promise never to mention ____.',
                                     'pick': 1},
                                    {'text': 'Niffty found ____ and has decided it belongs to her now.', 'pick': 1},
                                    {'text': "Lucifer's latest attempt to bond with Charlie involves ____.", 'pick': 1},
                                    {'text': 'Vox interrupted regular programming to complain about ____.', 'pick': 1},
                                    {'text': "The Hazbin Hotel's newest amenity is ____ next to ____.", 'pick': 2},
                                    {'text': "Today's mandatory trust exercise somehow ended with ____.", 'pick': 1},
                                    {'text': 'The hotel received a one-star review because of ____.', 'pick': 1},
                                    {'text': "Hell's newest turf war is officially about ____.", 'pick': 1},
                                    {'text': 'Charlie insists even ____ deserves a second chance.', 'pick': 1},
                                    {'text': 'The lobby went completely silent when ____ challenged ____.', 'pick': 2},
                                    {'text': "The hotel's employee handbook now has an entire section about ____.",
                                     'pick': 1},
                                    {'text': "Charlie added ____ to the hotel's official redemption curriculum.",
                                     'pick': 1},
                                    {'text': 'Vaggie threatened to cancel group therapy after ____.', 'pick': 1},
                                    {'text': 'Alastor offered to help, which somehow made ____ much worse.', 'pick': 1},
                                    {'text': "Angel Dust's newest excuse for missing therapy is ____.", 'pick': 1},
                                    {'text': 'Husk has officially stopped serving anyone who asks for ____.',
                                     'pick': 1},
                                    {'text': 'Niffty emerged from a vent holding ____.', 'pick': 1},
                                    {'text': 'Lucifer tried to impress Charlie by creating ____.', 'pick': 1},
                                    {'text': "Vox's latest broadcast is just twelve straight hours of ____.",
                                     'pick': 1},
                                    {'text': 'The Vees launched a new product called ____.', 'pick': 1},
                                    {'text': "Sir Pentious' latest invention runs entirely on ____.", 'pick': 1},
                                    {'text': 'Cherri Bomb solved the problem with ____ and several pounds of ____.',
                                     'pick': 2},
                                    {'text': 'The hotel talent show ended abruptly when ____ appeared.', 'pick': 1},
                                    {'text': "Charlie's latest song is an emotional ballad about ____.", 'pick': 1},
                                    {'text': "Hell's newest scandal involves ____ secretly dating ____.", 'pick': 2},
                                    {'text': "The hotel's new house rule is: under no circumstances bring ____ into "
                                             'the lobby.',
                                     'pick': 1}],
                    'white_cards': ["Charlie's unstoppable optimism",
                                    "Vaggie's last remaining nerve",
                                    'Alastor smiling during something deeply alarming',
                                    'Angel Dust turning therapy into crowd work',
                                    'Husk drinking through another staff meeting',
                                    'Niffty discovering a brand-new stain',
                                    'Lucifer arriving with unnecessary theatrical flair',
                                    'Vox buffering during a villain monologue',
                                    'Valentino making the room instantly worse',
                                    'Velvette live-posting the apocalypse',
                                    'Sir Pentious unveiling another doomed invention',
                                    'Cherri Bomb treating structural damage as a hobby',
                                    'a redemption worksheet nobody completed',
                                    'an infernal group-therapy circle',
                                    'a hotel lobby with negative insurance coverage',
                                    "Alastor's radio-static entrance music",
                                    'a contract with extremely suspicious fine print',
                                    "Charlie's emergency friendship song",
                                    'Vaggie physically preventing another terrible idea',
                                    "Angel Dust's deeply unhelpful coping mechanism",
                                    "Husk's thousand-yard bartender stare",
                                    'Niffty cleaning something that should remain untouched',
                                    "Lucifer's collection of increasingly specific ducks",
                                    'Vox losing an argument to outdated technology',
                                    'a turf war with branding guidelines',
                                    'an overlord meeting that should have been an email',
                                    'a demon with a LinkedIn profile',
                                    'a suspicious amount of red interior decorating',
                                    'a musical number during an active emergency',
                                    "the hotel's nonexistent security deposit",
                                    'a redemption plan written on a cocktail napkin',
                                    'an extermination-day scheduling conflict',
                                    'a demon trying mindfulness for eleven seconds',
                                    'a bar tab older than several civilizations',
                                    "Hell's most passive-aggressive elevator ride",
                                    'an extremely cursed staff retreat',
                                    'a microphone that definitely belongs to Alastor',
                                    "a TV screen displaying Vox's ego in 8K",
                                    'an emotional-support rubber duck',
                                    'a chandelier destroyed for narrative emphasis',
                                    'a hotel guest with absolutely no intention of improving',
                                    'a sinister jazz interlude',
                                    'a heartfelt speech immediately ruined by violence',
                                    'an infernal influencer sponsorship',
                                    'a suspiciously cheerful welcome basket',
                                    'the worst roommate arrangement in Hell',
                                    'a group hug with at least one concealed weapon',
                                    'a redemption milestone nobody can verify',
                                    'a bartender who has heard every possible confession',
                                    'an HR department that literally does not exist',
                                    'Alastor.',
                                    'Angel.',
                                    'Lucifer.',
                                    'Vaggie.',
                                    'Niffty.']},
 'builtin:pokemon': {'name': 'Pokémon',
                     'description': 'Pokémon battles, questionable trainers, cursed Pokédex entries, and Team '
                                    'Rocket-level decision making.',
                     'black_cards': [{'text': 'Professor Oak has decided that ____ is a perfectly acceptable starter '
                                              'Pokémon.',
                                      'pick': 1},
                                     {'text': "Team Rocket's newest scheme involves ____ and an alarming amount of "
                                              '____.',
                                      'pick': 2},
                                     {'text': 'The Pokédex entry nobody should have approved describes ____.',
                                      'pick': 1},
                                     {'text': 'Ash would have won the league sooner if he had just used ____.',
                                      'pick': 1},
                                     {'text': 'Pikachu refused to get in the Poké Ball because of ____.', 'pick': 1},
                                     {'text': 'Nurse Joy has officially asked trainers to stop bringing in ____.',
                                      'pick': 1},
                                     {'text': 'Officer Jenny is investigating a string of crimes involving ____.',
                                      'pick': 1},
                                     {'text': 'The newest gym challenge is literally just ____.', 'pick': 1},
                                     {'text': 'The Elite Four were not prepared for ____.', 'pick': 1},
                                     {'text': 'My Pokémon evolved after being exposed to ____.', 'pick': 1},
                                     {'text': 'The daycare gave my Pokémon back holding ____.', 'pick': 1},
                                     {'text': "The move 'Splash' has finally been buffed to summon ____.", 'pick': 1},
                                     {'text': 'The Pokémon Center lost its five-star rating because of ____.',
                                      'pick': 1},
                                     {'text': 'The newest regional form is just ____ wearing ____.', 'pick': 2},
                                     {'text': 'A wild ____ appeared! Unfortunately, so did ____.', 'pick': 2},
                                     {'text': 'The real reason MissingNo. exists is ____.', 'pick': 1},
                                     {'text': 'The battle was going fine until somebody used ____.', 'pick': 1},
                                     {'text': 'The newest Poké Ball is designed specifically for catching ____.',
                                      'pick': 1},
                                     {'text': 'Brock fell in love with ____ immediately.', 'pick': 1},
                                     {'text': 'Misty has once again threatened violence over ____.', 'pick': 1},
                                     {'text': 'The Safari Zone now charges extra for ____.', 'pick': 1},
                                     {'text': "The champion's secret strategy was actually just ____.", 'pick': 1},
                                     {'text': 'Ditto transformed into ____ and instantly regretted it.', 'pick': 1},
                                     {'text': 'Meowth learned to talk solely so he could complain about ____.',
                                      'pick': 1},
                                     {'text': "The new Pokémon contest category is 'Best Use of ____.'", 'pick': 1},
                                     {'text': "This Pokémon's hidden ability is somehow ____.", 'pick': 1},
                                     {'text': 'The legendary Pokémon awakened because somebody touched ____.',
                                      'pick': 1},
                                     {'text': 'The newest evil team wants to reshape the world using ____.', 'pick': 1},
                                     {'text': 'I traded my shiny Pokémon and received ____ in return.', 'pick': 1},
                                     {'text': 'The next Pokémon game will finally let players romance ____.',
                                      'pick': 1}],
                     'white_cards': ['Pikachu choosing violence',
                                     'a Magikarp with unrealistic confidence',
                                     'Team Rocket blasting off for the 900th time',
                                     'Professor Oak forgetting your name again',
                                     'Brock falling in love before learning her name',
                                     'Misty threatening someone with a bicycle',
                                     'Meowth paying taxes',
                                     'Ditto having an identity crisis',
                                     'a Snorlax blocking the only road in town',
                                     'a Psyduck headache powerful enough to level a building',
                                     'a Pokédex entry written by a sleep-deprived intern',
                                     'a suspiciously buff Machoke',
                                     'a Gengar living rent-free in the walls',
                                     'an Eevee refusing to commit to an evolution',
                                     'a Jigglypuff with permanent marker',
                                     'a Charizard ignoring direct orders out of spite',
                                     'a Zubat every four goddamn steps',
                                     'a shiny Pokémon appearing when you have no Poké Balls',
                                     'a Master Ball used on a Caterpie',
                                     'an HM move nobody wants to teach',
                                     'a Nurse Joy family reunion',
                                     'three Officer Jennys arguing over jurisdiction',
                                     'a Poké Ball thrown with absolutely no aim',
                                     'an Exp. Share doing all the parenting',
                                     'a legendary Pokémon hiding behind a ten-year-old',
                                     'a gym leader whose entire personality is one type',
                                     'a rival showing up at the worst possible moment',
                                     'a Pokémon egg that has taken 40,000 steps',
                                     'a daycare bill larger than rent',
                                     'a Rotom possessing the refrigerator',
                                     'a Slowpoke realizing the joke six rounds later',
                                     'a Cubone making everybody uncomfortable at family dinner',
                                     'a Wobbuffet contributing nothing but enthusiasm',
                                     'a Metapod hardening with terrifying determination',
                                     'a Poké Flute solo nobody asked for',
                                     'a Tentacool ruining the beach vacation',
                                     'an Abra teleporting out of responsibility',
                                     'a Haunter pulling the same prank for 25 years',
                                     'a Chansey carrying the entire healthcare system',
                                     "a Farfetch'd showing up with its own garnish",
                                     'a Sudowoodo lying badly about being a tree',
                                     'a Mimikyu wanting one normal friendship',
                                     'a Yamask carrying around its deeply upsetting lore',
                                     'a Drifloon standing suspiciously close to a playground',
                                     'a Gardevoir creating a black hole over mild inconvenience',
                                     'a Lopunny conversation getting weird immediately',
                                     'a Vaporeon discussion everyone agreed not to continue',
                                     'a Pokémon battle interrupted by friendship',
                                     'a trainer with six identical Bidoof',
                                     'a level 100 Pokémon that still knows Tackle',
                                     'a critical hit at the exact worst moment',
                                     'a one-percent encounter rate',
                                     'a Poké Mart employee watching you buy 99 Repels',
                                     'a berry that somehow fixes paralysis',
                                     'a Revive being treated like basic healthcare',
                                     'a Team Rocket disguise consisting of one fake mustache',
                                     'a gym badge obtained through emotional damage',
                                     'a Pokémon named something regrettable in 2009',
                                     'an NPC who refuses to move until you solve their personal problem',
                                     'the terrifying realization that children run the entire economy',
                                     'Pikachu.',
                                     'Magikarp.',
                                     'Ditto.',
                                     'Snorlax.',
                                     'Gengar.']},
 'builtin:unhinged_internet': {'name': 'Unhinged Internet',
                               'description': 'Brainrot, cursed posts, Discord drama, and terminally online behavior.',
                               'black_cards': [{'text': 'The algorithm ruined my life by recommending ____.',
                                                'pick': 1},
                                               {'text': 'The group chat got locked after somebody posted ____.',
                                                'pick': 1},
                                               {'text': 'My For You Page is now 90% ____ and 10% ____.', 'pick': 2},
                                               {'text': 'The apology video would have worked without ____.', 'pick': 1},
                                               {'text': 'I knew the discourse was cooked when people started defending '
                                                        '____.',
                                                'pick': 1},
                                               {'text': 'Nothing good has ever followed the phrase “hear me out” '
                                                        'except ____.',
                                                'pick': 1},
                                               {'text': 'The server gained 200 members overnight because of ____.',
                                                'pick': 1},
                                               {'text': 'My digital footprint is mostly ____ and regrettably ____.',
                                                'pick': 2},
                                               {'text': 'The comment section achieved peace by collectively bullying '
                                                        '____.',
                                                'pick': 1},
                                               {'text': 'The internet should have been shut down the moment ____ went '
                                                        'viral.',
                                                'pick': 1},
                                               {'text': 'The comment section achieved sentience after ____.',
                                                'pick': 1},
                                               {'text': 'My screen time report blamed everything on ____.', 'pick': 1},
                                               {'text': 'The algorithm decided my entire personality is ____.',
                                                'pick': 1},
                                               {'text': 'The thread was normal until somebody posted ____.', 'pick': 1},
                                               {'text': 'I lost three hours doomscrolling about ____.', 'pick': 1},
                                               {'text': 'The influencer apology somehow included ____ and ____.',
                                                'pick': 2},
                                               {'text': 'The internet collectively decided to romanticize ____.',
                                                'pick': 1},
                                               {'text': 'The new brainrot phrase is just ____ with extra steps.',
                                                'pick': 1},
                                               {'text': 'My mutuals staged an intervention over ____.', 'pick': 1},
                                               {'text': 'The deleted post was preserved forever because of ____.',
                                                'pick': 1}],
                               'white_cards': ['a 47-part TikTok storytime',
                                               'a Discord argument with footnotes',
                                               'an apology typed in Notes app',
                                               'a cursed reaction image from 2014',
                                               'three hours of doomscrolling',
                                               'a mutual who knows too much',
                                               'the phrase “source?” at 2 a.m.',
                                               'a suspiciously specific subtweet',
                                               'a burner account with zero shame',
                                               'the world’s worst ratio',
                                               'a screenshot with twelve red circles',
                                               'a fandom civil war',
                                               'a podcast clip taken violently out of context',
                                               'a profile picture that explains everything',
                                               'a reply guy with premium',
                                               'an influencer apology hoodie',
                                               'an accidental close-friends post',
                                               'a 900-comment Facebook fight',
                                               'the phrase “unalive” used during dinner',
                                               'a thirst trap with municipal lighting',
                                               'a server owner having a public breakdown',
                                               'a meme compressed beyond recognition',
                                               'a deleted tweet preserved forever',
                                               'a Notes-app manifesto',
                                               'a parasocial relationship in its final form',
                                               'a comment beginning with “as a mother”',
                                               'an unsolicited voice note',
                                               'a TikTok psychic diagnosing the situation',
                                               'a Discord mod typing “interesting”',
                                               'a group chat poll that ended friendships',
                                               'Brainrot.',
                                               'Ratio.',
                                               'Doomscrolling.',
                                               'Cringe.',
                                               'Parasocial.']},
 'builtin:family_hell': {'name': 'Family Functions From Hell',
                         'description': 'Holiday dinners, family drama, and reasons to fake a migraine before dessert.',
                         'black_cards': [{'text': 'Thanksgiving ended early after Grandma brought up ____.', 'pick': 1},
                                         {'text': 'The family photo had to be retaken because of ____.', 'pick': 1},
                                         {'text': 'My aunt cornered me near the mashed potatoes to ask about ____.',
                                          'pick': 1},
                                         {'text': 'The Christmas gift exchange was permanently canceled after ____.',
                                          'pick': 1},
                                         {'text': 'The wedding registry included ____ and, for some reason, ____.',
                                          'pick': 2},
                                         {'text': 'Grandpa’s Facebook post revealed the family secret about ____.',
                                          'pick': 1},
                                         {'text': 'The reunion became a crime scene when someone found ____.',
                                          'pick': 1},
                                         {'text': 'Mom said “don’t start” immediately before ____ started.', 'pick': 1},
                                         {'text': 'The only thing holding this family together is ____ and ____.',
                                          'pick': 2},
                                         {'text': 'Dessert was delayed because the cousins were fighting over ____.',
                                          'pick': 1},
                                         {'text': 'Thanksgiving dinner stopped when Grandma asked about ____.',
                                          'pick': 1},
                                         {'text': 'The family reunion T-shirt accidentally featured ____.', 'pick': 1},
                                         {'text': 'My aunt brought up ____ before the appetizers arrived.', 'pick': 1},
                                         {'text': 'The cousins were banned from the basement because of ____.',
                                          'pick': 1},
                                         {'text': 'Grandpa confused ____ with ____ and nobody corrected him.',
                                          'pick': 2},
                                         {'text': 'The Christmas card conveniently left out ____.', 'pick': 1},
                                         {'text': 'The inheritance dispute somehow centered on ____.', 'pick': 1},
                                         {'text': 'The family photo was ruined by ____.', 'pick': 1},
                                         {'text': 'My mother whispered “do not mention ____” five minutes too late.',
                                          'pick': 1},
                                         {'text': 'The reunion ended early after the incident involving ____.',
                                          'pick': 1}],
                         'white_cards': ['a casserole nobody can identify',
                                         'an uncle three beers past appropriate',
                                         'a cousin selling essential oils',
                                         'grandma oversharing at full volume',
                                         'an inheritance worth exactly $14',
                                         'a folding chair with emotional damage',
                                         'a turkey dryer than the family group chat',
                                         'the aunt who asks when you are having kids',
                                         'a suspicious family recipe',
                                         'a divorce announcement before dessert',
                                         'a passive-aggressive serving spoon',
                                         'a child screaming under the table',
                                         'a Facebook conspiracy printed on paper',
                                         'a secret second family',
                                         'a pie used as leverage',
                                         'an ancient grudge about a crockpot',
                                         'a wedding plus-one nobody recognizes',
                                         'the good Tupperware going missing',
                                         'a family member live-streaming the argument',
                                         'a prayer that becomes a lecture',
                                         'a cousin arriving with six uninvited people',
                                         'a card table collapsing mid-fight',
                                         'the phrase “we don’t talk about that”',
                                         'a birthday cake with the wrong name',
                                         'a relative who brought politics anyway',
                                         'a suspicious amount of boxed wine',
                                         'a family photo cropped for legal reasons',
                                         'an heirloom nobody actually wants',
                                         'a DNA test ruining brunch',
                                         'the annual fight over who hosts Christmas',
                                         'Grandma.',
                                         'Cousins.',
                                         'Inheritance.',
                                         'Casserole.',
                                         'Thanksgiving.']},
 'builtin:bad_parenting': {'name': 'Bad Parenting Decisions',
                           'description': 'Feral household energy, questionable shortcuts, and parenting choices made '
                                          'under pressure.',
                           'black_cards': [{'text': 'The parenting book definitely did not recommend ____.', 'pick': 1},
                                           {'text': 'I knew pickup was going badly when my kid handed the teacher '
                                                    '____.',
                                            'pick': 1},
                                           {'text': 'The babysitter quit immediately after seeing ____.', 'pick': 1},
                                           {'text': 'Today’s educational activity is apparently ____.', 'pick': 1},
                                           {'text': 'The reward chart now includes a sticker for not ____.', 'pick': 1},
                                           {'text': 'Dinner became “whatever survives” after ____.', 'pick': 1},
                                           {'text': 'The family calendar has an emergency appointment for ____.',
                                            'pick': 1},
                                           {'text': 'My child learned ____ from the internet before learning ____.',
                                            'pick': 2},
                                           {'text': 'The school email began with “we need to discuss” and ended with '
                                                    '____.',
                                            'pick': 1},
                                           {'text': 'The parenting group banned me after I suggested ____.', 'pick': 1},
                                           {'text': 'The school called home because my kid brought ____ for '
                                                    'show-and-tell.',
                                            'pick': 1},
                                           {'text': 'The babysitter quit immediately after discovering ____.',
                                            'pick': 1},
                                           {'text': 'The parenting group banned me for suggesting ____.', 'pick': 1},
                                           {'text': 'Dinner tonight is ____ because I have given up.', 'pick': 1},
                                           {'text': 'The tablet was confiscated after my child searched for ____.',
                                            'pick': 1},
                                           {'text': 'My kid learned ____ from YouTube before learning their address.',
                                            'pick': 1},
                                           {'text': 'The daycare incident report simply says “____.”', 'pick': 1},
                                           {'text': 'I bribed my child with ____ to survive Target.', 'pick': 1},
                                           {'text': 'The chore chart now includes “stop putting ____ in ____.”',
                                            'pick': 2},
                                           {'text': 'The pediatrician asked one question about ____ and I immediately '
                                                    'lied.',
                                            'pick': 1}],
                           'white_cards': ['screen time used as international diplomacy',
                                           'a juice box opened with teeth',
                                           'a backpack full of mysterious crumbs',
                                           'a sticker chart nobody understands',
                                           'dinner served directly from the air fryer basket',
                                           'the emergency tablet charger',
                                           'a school pickup line meltdown',
                                           'a toy making noise at 3 a.m.',
                                           'a snack cup full of floor Cheerios',
                                           'a permission slip signed in the parking lot',
                                           'an entire outfit chosen by a five-year-old',
                                           'a car seat containing seventeen crackers',
                                           'a bedtime routine with six encores',
                                           'a child negotiating like a union rep',
                                           'a lunchbox returning untouched again',
                                           'a marker stain shaped like a confession',
                                           'a parent hiding in the pantry for two minutes',
                                           'an educational video becoming four hours of YouTube',
                                           'a lost shoe during a time-sensitive emergency',
                                           'a sticker permanently attached to the dog',
                                           'a Happy Meal toy functioning as currency',
                                           'a tiny person refusing pants',
                                           'a daycare note written in concerningly polite language',
                                           'the phrase “because I said so” losing all power',
                                           'a grocery cart hostage situation',
                                           'a tablet at 2% battery',
                                           'a suspicious silence from the next room',
                                           'a half-eaten granola bar in the couch',
                                           'a bedtime story shortened for legal reasons',
                                           'parenting entirely by vibes',
                                           'iPad.',
                                           'Grounded.',
                                           'Daycare.',
                                           'Cocomelon.',
                                           'Consequences.']},
 'builtin:convention': {'name': 'Convention Pack',
                        'description': 'Cosplay disasters, hotel chaos, artist alley debt, and con-floor survival.',
                        'black_cards': [{'text': 'The cosplay contest was delayed because of ____.', 'pick': 1},
                                        {'text': 'The hotel room somehow contained ____ and ____.', 'pick': 2},
                                        {'text': 'Artist Alley took my last $40 in exchange for ____.', 'pick': 1},
                                        {'text': 'The convention security announcement specifically mentioned ____.',
                                         'pick': 1},
                                        {'text': 'My badge name is now legally ____.', 'pick': 1},
                                        {'text': 'The afterparty died instantly when someone brought ____.', 'pick': 1},
                                        {'text': 'I spent six months on this cosplay just to destroy it with ____.',
                                         'pick': 1},
                                        {'text': 'The panel became adults-only after a question about ____.',
                                         'pick': 1},
                                        {'text': 'Con funk is just ____ mixed with ____.', 'pick': 2},
                                        {'text': 'The line for the celebrity photo op was caused by ____.', 'pick': 1},
                                        {'text': 'The cosplay contest was delayed because of ____.', 'pick': 1},
                                        {'text': 'The hotel elevator smelled strongly of ____ all weekend.', 'pick': 1},
                                        {'text': 'Artist Alley sold out of everything except ____.', 'pick': 1},
                                        {'text': 'The con badge should have included a warning about ____.', 'pick': 1},
                                        {'text': 'Security removed someone for combining ____ with ____.', 'pick': 2},
                                        {'text': 'The afterparty became legendary because of ____.', 'pick': 1},
                                        {'text': 'My cosplay survived three hours before ____ happened.', 'pick': 1},
                                        {'text': 'The panel Q&A immediately went off the rails when someone asked '
                                                 'about ____.',
                                         'pick': 1},
                                        {'text': 'The convention survival kit should always include ____.', 'pick': 1},
                                        {'text': 'Nobody warned first-time attendees about ____.', 'pick': 1}],
                        'white_cards': ['a foam sword held together by hot glue',
                                        'con funk with its own zip code',
                                        'an emergency cosplay repair kit',
                                        'a hotel room with eleven occupants',
                                        'a badge covered in cursed ribbons',
                                        'a $17 convention hot dog',
                                        'an artist alley impulse purchase',
                                        'a wig cap fighting for its life',
                                        'a prop weapon inspection lasting forty minutes',
                                        'a cosplay contact lens emergency',
                                        'a panel question nobody should have asked',
                                        'a body pillow with priority boarding',
                                        'a tote bag full of prints',
                                        'an escalator stopped by a giant costume',
                                        'a hotel elevator packed with armor',
                                        'an afterparty hosted in a room for two',
                                        'a lanyard carrying seventeen pounds of merch',
                                        'a convention center carpet pattern burned into memory',
                                        'a cosplayer dehydrated but committed',
                                        'a five-hour line for twenty seconds of interaction',
                                        'a stranger yelling the character name across the lobby',
                                        'a badge forgotten in the hotel room',
                                        'a cursed meet-and-greet photo',
                                        'a costume tail caught in a door',
                                        'a vendor selling suspicious mystery bags',
                                        'an emergency sewing kit from Walgreens',
                                        'a group cosplay missing one critical person',
                                        'a foam prop melting in the car',
                                        'a hotel breakfast consumed in full costume',
                                        'the financial consequences of artist alley',
                                        'Concrud.',
                                        'Cosplay.',
                                        'Fursuit.',
                                        'Lanyard.',
                                        'Badge.']},
 'builtin:florida_man': {'name': 'Florida Man',
                         'description': 'Alligators, gas stations, hurricanes, and deeply questionable local '
                                        'decisions.',
                         'black_cards': [{'text': 'Florida Man was arrested Tuesday after attempting ____.', 'pick': 1},
                                         {'text': 'The hurricane party was going fine until ____.', 'pick': 1},
                                         {'text': 'The gas station clerk refused service because of ____.', 'pick': 1},
                                         {'text': 'Authorities found ____ inside the stolen golf cart.', 'pick': 1},
                                         {'text': 'The alligator was not the problem. The problem was ____.',
                                          'pick': 1},
                                         {'text': 'The HOA sent a final warning about ____ and ____.', 'pick': 2},
                                         {'text': 'Local news described the incident as “involving” ____.', 'pick': 1},
                                         {'text': 'The backyard became a protected habitat for ____.', 'pick': 1},
                                         {'text': 'Nothing says hurricane preparedness like ____.', 'pick': 1},
                                         {'text': 'The mugshot made sense after police explained ____.', 'pick': 1},
                                         {'text': 'Florida Man was arrested after attempting to weaponize ____.',
                                          'pick': 1},
                                         {'text': 'The hurricane party was going fine until someone brought ____.',
                                          'pick': 1},
                                         {'text': 'Police discovered ____ riding shotgun in the stolen golf cart.',
                                          'pick': 1},
                                         {'text': 'The gas station clerk refused service because of ____.', 'pick': 1},
                                         {'text': 'The alligator was innocent; ____ started it.', 'pick': 1},
                                         {'text': 'The local news described the incident as “____ meets ____.”',
                                          'pick': 2},
                                         {'text': 'Florida Man claimed ____ was protected by maritime law.', 'pick': 1},
                                         {'text': 'The HOA sent a cease-and-desist over ____.', 'pick': 1},
                                         {'text': 'The mugshot makes more sense once you know about ____.', 'pick': 1},
                                         {'text': 'Only in Florida would ____ become a neighborhood attraction.',
                                          'pick': 1}],
                         'white_cards': ['an alligator in a kiddie pool',
                                         'a lawn chair tied to a pickup truck',
                                         'a gas-station sword',
                                         'a hurricane party with no batteries',
                                         'a stolen golf cart',
                                         'a jet ski on residential streets',
                                         'a cooler full of boiled peanuts',
                                         'a shirtless man arguing with weather radar',
                                         'a python where a python should not be',
                                         'a Publix sub used as negotiation leverage',
                                         'a backyard swamp project',
                                         'a sunburn shaped like poor judgment',
                                         'a mobility scooter in a police chase',
                                         'a decorative flamingo used as evidence',
                                         'a boat parked in the living room',
                                         'a mysterious cooler on the interstate',
                                         'a raccoon eating convenience-store nachos',
                                         'a hurricane named like an ex',
                                         'a tarp installed with pure confidence',
                                         'an inflatable pool on an apartment balcony',
                                         'a meth-adjacent craft project',
                                         'a mailbox destroyed by recreational vehicles',
                                         'a fake parking permit laminated at home',
                                         'a fishing pole involved in a felony',
                                         'an airboat with questionable registration',
                                         'a generator powering only the margarita machine',
                                         'a pet lizard wearing a tiny chain',
                                         'a parking-lot wedding reception',
                                         'a weather alert ignored on principle',
                                         'the phrase “hold my beer” entering evidence',
                                         'Alligator.',
                                         'Meth.',
                                         'Hurricane.',
                                         'Probation.',
                                         'Walmart.']},
 'builtin:marcus_worm': {'name': 'ROFLGators’ Marcus the Worm',
                         'description': 'ROFLGators server lore starring Marcus the Worm and whatever crime he '
                                        'committed this time.',
                         'black_cards': [{'text': 'ROFLGators’ Marcus the Worm was banned from the kitchen for ____.',
                                          'pick': 1},
                                         {'text': 'The official ROFLGators Marcus lore now includes ____.', 'pick': 1},
                                         {'text': 'Nobody expected Marcus to emerge from ____ carrying ____.',
                                          'pick': 2},
                                         {'text': 'ROFLGators voted unanimously to blame Marcus for ____.', 'pick': 1},
                                         {'text': 'Marcus evolved after consuming ____.', 'pick': 1},
                                         {'text': 'The prophecy clearly warned us about Marcus and ____.', 'pick': 1},
                                         {'text': 'Marcus only answers to one thing: ____.', 'pick': 1},
                                         {'text': 'The FBI file labeled “MARCUS” contains ____.', 'pick': 1},
                                         {'text': 'Marcus the Worm’s final form is powered by ____.', 'pick': 1},
                                         {'text': 'We lost Marcus for six hours and found him inside ____.', 'pick': 1},
                                         {'text': 'Marcus the Worm has been accused of ____.', 'pick': 1},
                                         {'text': 'Nobody knows why Marcus keeps carrying ____.', 'pick': 1},
                                         {'text': 'Marcus emerged from the dirt demanding ____.', 'pick': 1},
                                         {'text': 'The prophecy specifically warned us about Marcus and ____.',
                                          'pick': 1},
                                         {'text': 'Marcus traded ____ for ____ and somehow profited.', 'pick': 2},
                                         {'text': 'The server banned discussion of Marcus after ____.', 'pick': 1},
                                         {'text': 'Marcus insists ____ is part of his natural habitat.', 'pick': 1},
                                         {'text': 'The worm council sentenced Marcus to ____.', 'pick': 1},
                                         {'text': 'Marcus would like everyone to forget the incident with ____.',
                                          'pick': 1},
                                         {'text': 'The final form of Marcus the Worm is just ____.', 'pick': 1}],
                         'white_cards': ['Marcus in a tiny cowboy hat',
                                         'Marcus committing tax fraud',
                                         'Marcus entering his villain era',
                                         'Marcus under the refrigerator',
                                         'Marcus with suspiciously good credit',
                                         'Marcus operating heavy machinery',
                                         'Marcus demanding union representation',
                                         'Marcus in witness protection',
                                         'Marcus piloting a forklift',
                                         'Marcus wearing one Croc',
                                         'Marcus with a Bluetooth speaker',
                                         'Marcus becoming management',
                                         'Marcus haunting the snack drawer',
                                         'Marcus refusing to elaborate',
                                         'Marcus appearing in the security footage',
                                         'Marcus holding a tiny clipboard',
                                         'Marcus with diplomatic immunity',
                                         'Marcus behind the Wendy’s dumpster',
                                         'Marcus winning employee of the month',
                                         'Marcus requesting legal counsel',
                                         'Marcus in a trench coat',
                                         'Marcus downloading Discord Nitro',
                                         'Marcus challenging God to a duel',
                                         'Marcus at the DMV',
                                         'Marcus running an underground casino',
                                         'Marcus entering through the air vent',
                                         'Marcus becoming sentient during lunch',
                                         'Marcus with a suspicious briefcase',
                                         'Marcus starting a podcast',
                                         'Marcus the Worm, unfortunately',
                                         'Marcus.',
                                         'Worm.',
                                         'ROFLGators.',
                                         'Dirt.',
                                         'Lore.']},
 'builtin:red_flag': {'name': 'Red Flag',
                      'description': 'Dating disasters, toxic behavior, and “they’re perfect except...” energy.',
                      'black_cards': [{'text': 'They are a 10, but they ____.', 'pick': 1},
                                      {'text': 'The first date ended after they admitted to ____.', 'pick': 1},
                                      {'text': 'My biggest red flag is apparently ____.', 'pick': 1},
                                      {'text': 'Their dating profile says “fluent in” ____.', 'pick': 1},
                                      {'text': 'I should have left when I noticed ____ next to ____.', 'pick': 2},
                                      {'text': 'The relationship survived everything except ____.', 'pick': 1},
                                      {'text': 'Their ex warned me specifically about ____.', 'pick': 1},
                                      {'text': 'The couples therapist quietly wrote down ____.', 'pick': 1},
                                      {'text': 'Nothing says “emotionally available” like ____.', 'pick': 1},
                                      {'text': 'The breakup text was just a photo of ____.', 'pick': 1},
                                      {'text': 'They were a perfect 10 until they admitted to ____.', 'pick': 1},
                                      {'text': 'The dating profile said “no drama” directly above ____.', 'pick': 1},
                                      {'text': 'My therapist called ____ a “learning opportunity.”', 'pick': 1},
                                      {'text': 'The first red flag was ____. The second was ____.', 'pick': 2},
                                      {'text': 'They said their ex was crazy, then immediately showed me ____.',
                                       'pick': 1},
                                      {'text': 'I should have left when they introduced me to ____.', 'pick': 1},
                                      {'text': 'Nothing says “emotionally available” like ____.', 'pick': 1},
                                      {'text': 'Their love language is apparently ____.', 'pick': 1},
                                      {'text': 'The relationship survived cheating but not ____.', 'pick': 1},
                                      {'text': 'I ignored every warning sign until ____ happened.', 'pick': 1}],
                      'white_cards': ['following 4,000 thirst traps',
                                      'calling every ex “crazy”',
                                      'a mattress directly on the floor',
                                      'sharing one towel with three roommates',
                                      'a Snapchat score in the millions',
                                      'a podcast microphone on the nightstand',
                                      'asking for your location on date two',
                                      'an emotional-support situationship',
                                      'a phone always face-down',
                                      'a mysterious “work wife”',
                                      'a dating profile full of fish photos',
                                      'a mother who still books their dentist',
                                      'three active group chats with exes',
                                      'saying “I’m an empath” before doing damage',
                                      'a bathroom with no hand soap',
                                      'a bedroom illuminated only by LEDs',
                                      'a car full of fast-food receipts',
                                      'a notes app list ranking exes',
                                      'an allergy to accountability',
                                      'the phrase “I don’t do labels”',
                                      'a suspicious second phone',
                                      'one pillow and no fitted sheet',
                                      'a gaming chair at the dining table',
                                      'crypto advice during foreplay',
                                      'an Instagram following list from hell',
                                      'a tattoo dedicated to an ex',
                                      'a roommate who is definitely not “just a roommate”',
                                      'a mattress protector that has seen war',
                                      'sharing motivational quotes after arguments',
                                      'a ring light permanently aimed at the bed',
                                      'Gaslighting.',
                                      'Lovebombing.',
                                      'Jealousy.',
                                      'Situationship.',
                                      'Audacity.']},
 'builtin:group_chat': {'name': 'Group Chat Evidence',
                        'description': 'Screenshots, deleted messages, voice notes, and evidence that should never '
                                       'leave the chat.',
                        'black_cards': [{'text': 'The group chat is legally inadmissible because of ____.', 'pick': 1},
                                        {'text': 'Someone deleted 47 messages after posting ____.', 'pick': 1},
                                        {'text': 'The 3 a.m. voice note was mostly about ____.', 'pick': 1},
                                        {'text': '“Do not screenshot this” was followed immediately by ____.',
                                         'pick': 1},
                                        {'text': 'The pinned message is just ____ next to ____.', 'pick': 2},
                                        {'text': 'The friendship ended over a poll asking about ____.', 'pick': 1},
                                        {'text': 'The chat changed its name to “Evidence Locker” after ____.',
                                         'pick': 1},
                                        {'text': 'The typing indicator lasted six minutes before sending ____.',
                                         'pick': 1},
                                        {'text': 'Someone’s mom got added by accident during ____.', 'pick': 1},
                                        {'text': 'The group chat lawyer advised us to stop discussing ____.',
                                         'pick': 1},
                                        {'text': 'The message was deleted, but somebody already screenshotted ____.',
                                         'pick': 1},
                                        {'text': 'The group chat renamed itself after ____.', 'pick': 1},
                                        {'text': '“Do not tell anyone” was followed immediately by ____.', 'pick': 1},
                                        {'text': 'The 3 a.m. voice note was mostly about ____.', 'pick': 1},
                                        {'text': 'Someone accidentally sent ____ to the family chat.', 'pick': 1},
                                        {'text': 'The pinned message is just a warning about ____.', 'pick': 1},
                                        {'text': 'The group chat evidence folder contains ____ and ____.', 'pick': 2},
                                        {'text': 'We all agreed to lie about ____.', 'pick': 1},
                                        {'text': 'The friendship nearly ended over ____.', 'pick': 1},
                                        {'text': 'The typing bubble lasted five minutes before they finally sent ____.',
                                         'pick': 1}],
                        'white_cards': ['a screenshot taken before the delete',
                                        'a fourteen-minute voice note',
                                        'the phrase “wrong chat”',
                                        'a poll with one morally correct answer',
                                        'someone typing and deleting for ten minutes',
                                        'a reaction emoji doing all the legal work',
                                        'a message edited six times',
                                        'the friend who saves every receipt',
                                        'a blurry screenshot of another screenshot',
                                        'an accidental live location share',
                                        'a meme used during a serious argument',
                                        'a nickname that cannot appear in court',
                                        'someone’s mom reacting with a heart',
                                        'a private story screenshot',
                                        'a message beginning “hypothetically”',
                                        'a 2 a.m. “you awake?”',
                                        'a deleted image everyone still remembers',
                                        'a pinned apology nobody accepted',
                                        'a group chat rename after an incident',
                                        'a voice message recorded while driving',
                                        'a suspicious “message unavailable”',
                                        'the one friend with zero context',
                                        'an inside joke requiring a flowchart',
                                        'a screen recording with notifications visible',
                                        'a reply consisting only of “girl...”',
                                        'an accidental @everyone',
                                        'the phrase “take this to your grave”',
                                        'a seven-year-old screenshot resurfacing',
                                        'someone leaving and rejoining dramatically',
                                        'the admin changing permissions mid-argument',
                                        'Screenshots.',
                                        'Receipts.',
                                        'Discord.',
                                        'Deleted.',
                                        'Context.']},
 'builtin:bathroom_crime': {'name': 'Bathroom Crime Scene',
                            'description': 'A deeply unnecessary collection of bathroom-specific horrors.',
                            'black_cards': [{'text': 'The gas station bathroom was closed indefinitely because of '
                                                     '____.',
                                             'pick': 1},
                                            {'text': 'Housekeeping entered the bathroom and immediately saw ____.',
                                             'pick': 1},
                                            {'text': 'The toilet survived ____, but not ____.', 'pick': 2},
                                            {'text': 'There is no plumbing code for ____.', 'pick': 1},
                                            {'text': 'The bathroom fan gave up after ____.', 'pick': 1},
                                            {'text': 'The sign now says “DO NOT” followed by a picture of ____.',
                                             'pick': 1},
                                            {'text': 'Someone left ____ balanced on the sink.', 'pick': 1},
                                            {'text': 'The porta-potty tipped over during ____.', 'pick': 1},
                                            {'text': 'The smell could only be described as ____ plus ____.', 'pick': 2},
                                            {'text': 'Maintenance charged an emergency fee for ____.', 'pick': 1},
                                            {'text': 'The bathroom became a crime scene after ____.', 'pick': 1},
                                            {'text': 'The plunger was never the same after ____.', 'pick': 1},
                                            {'text': 'Someone wrote “sorry” on the mirror using ____.', 'pick': 1},
                                            {'text': 'The stall door was locked from the inside by ____.', 'pick': 1},
                                            {'text': 'The janitor found ____ floating next to ____.', 'pick': 2},
                                            {'text': 'The hand dryer somehow made ____ worse.', 'pick': 1},
                                            {'text': 'There is no OSHA guideline for dealing with ____.', 'pick': 1},
                                            {'text': 'The bathroom attendant deserves hazard pay for ____.', 'pick': 1},
                                            {'text': 'The smell can only be described as ____.', 'pick': 1},
                                            {'text': 'The toilet survived ____ but finally lost to ____.', 'pick': 2}],
                            'white_cards': ['a toilet making eye contact',
                                            'a mystery puddle with no known source',
                                            'one square of toilet paper',
                                            'a clogged toilet with ambition',
                                            'a sink full of ramen noodles',
                                            'a plunger promoted to management',
                                            'a bath mat soaked for unknown reasons',
                                            'a public restroom hand dryer screaming',
                                            'a porta-potty in direct sunlight',
                                            'a shower drain growing its own ecosystem',
                                            'a roll of toilet paper installed backwards',
                                            'a toothbrush on the floor',
                                            'a suspicious wet sock',
                                            'a toilet seat held together by hope',
                                            'a candle fighting for its life',
                                            'a bathroom stall with no lock',
                                            'a soap dispenser containing only air',
                                            'a hand towel nobody trusts',
                                            'a hairball with legal standing',
                                            'a toilet brush used beyond its job description',
                                            'a shower curtain sticking to your soul',
                                            'a drain snake that learned too much',
                                            'a ceiling leak directly above the toilet',
                                            'a bathroom trash can overflowing with secrets',
                                            'a motel towel shaped like surrender',
                                            'a bidet installed with YouTube confidence',
                                            'a stall gap wide enough for witnesses',
                                            'a mirror covered in motivational stickers',
                                            'a sink that only has boiling water',
                                            'the unmistakable sound of plumbing regret',
                                            'Diarrhea.',
                                            'Plunger.',
                                            'Bidet.',
                                            'Skidmarks.',
                                            'Clogged.']},
 'builtin:divorce_court': {'name': 'Divorce Court',
                           'description': 'Petty exes, custody of the air fryer, and evidence entered as Exhibit A.',
                           'black_cards': [{'text': 'The judge visibly sighed after hearing about ____.', 'pick': 1},
                                           {'text': 'Exhibit A was a screenshot of ____.', 'pick': 1},
                                           {'text': 'The divorce became final after they fought over ____.', 'pick': 1},
                                           {'text': 'The custody agreement somehow includes ____.', 'pick': 1},
                                           {'text': 'Their lawyer asked the court to disregard ____ and ____.',
                                            'pick': 2},
                                           {'text': 'The settlement awarded me the house and them ____.', 'pick': 1},
                                           {'text': 'The final straw was discovering ____.', 'pick': 1},
                                           {'text': 'Mediation lasted six hours because nobody would surrender ____.',
                                            'pick': 1},
                                           {'text': 'The Facebook relationship status changed during ____.', 'pick': 1},
                                           {'text': 'The judge called a recess after hearing the phrase ____.',
                                            'pick': 1},
                                           {'text': 'The divorce became final after the judge heard about ____.',
                                            'pick': 1},
                                           {'text': 'We are currently fighting for custody of ____.', 'pick': 1},
                                           {'text': 'The prenup somehow included a clause about ____.', 'pick': 1},
                                           {'text': 'My ex demanded ____ in the settlement.', 'pick': 1},
                                           {'text': 'Exhibit A was ____. Exhibit B was ____.', 'pick': 2},
                                           {'text': 'The mediator gave up after hearing about ____.', 'pick': 1},
                                           {'text': 'The wedding photos were divided based on who caused ____.',
                                            'pick': 1},
                                           {'text': 'The judge asked us to stop mentioning ____.', 'pick': 1},
                                           {'text': 'Our relationship counselor predicted the divorce when ____ '
                                                    'happened.',
                                            'pick': 1},
                                           {'text': 'The final argument was not about money. It was about ____.',
                                            'pick': 1}],
                           'white_cards': ['custody of the air fryer',
                                           'a shared Netflix password',
                                           'a wedding album used as evidence',
                                           'a passive-aggressive Venmo request',
                                           'the good couch',
                                           'a Facebook relationship status',
                                           'a lawyer with visible regret',
                                           'a Ring camera compilation',
                                           'a jointly owned Costco membership',
                                           'the family dog choosing sides',
                                           'an engagement ring receipt',
                                           'a 47-page text-message exhibit',
                                           'a restraining order against the group chat',
                                           'a prenup found in Google Docs',
                                           'the vacation points nobody remembered',
                                           'a suspicious Cash App history',
                                           'a wedding registry blender',
                                           'a mattress neither person wants',
                                           'a shared Spotify playlist',
                                           'a custody calendar color-coded in rage',
                                           'the phrase “per my last email”',
                                           'a mediator eating vending-machine crackers',
                                           'a stack of screenshots printed at CVS',
                                           'an ex arriving with a new partner',
                                           'a houseplant included in negotiations',
                                           'a courtroom whisper audible to everyone',
                                           'an apology sent through an attorney',
                                           'the last remaining matching towel',
                                           'a divorce announcement posted before court ended',
                                           'a judge who has absolutely heard enough',
                                           'Alimony.',
                                           'Custody.',
                                           'Lawyer.',
                                           'Prenup.',
                                           'Divorce.']},
 'builtin:blackout_drunk': {'name': 'Blackout Drunk',
                            'description': 'Missing shoes, mystery rides, questionable texts, and the morning-after '
                                           'investigation.',
                            'black_cards': [{'text': 'I woke up with no shoes and a receipt for ____.', 'pick': 1},
                                            {'text': 'The bartender cut me off after I tried to order ____.',
                                             'pick': 1},
                                            {'text': 'My camera roll says the night involved ____ and ____.',
                                             'pick': 2},
                                            {'text': 'The Uber driver still talks about ____.', 'pick': 1},
                                            {'text': 'The hangover became medically disrespectful after ____.',
                                             'pick': 1},
                                            {'text': 'Apparently I spent forty minutes arguing with ____.', 'pick': 1},
                                            {'text': 'The 2 a.m. purchase I cannot explain is ____.', 'pick': 1},
                                            {'text': 'The group chat reconstructed the night using ____.', 'pick': 1},
                                            {'text': 'I knew I had a problem when I found ____ in my purse.',
                                             'pick': 1},
                                            {'text': 'The bar permanently renamed the drink after ____.', 'pick': 1},
                                            {'text': 'I woke up with no shoes and a receipt for ____.', 'pick': 1},
                                            {'text': 'The Uber driver still remembers me because of ____.', 'pick': 1},
                                            {'text': 'Nobody can explain how ____ ended up in my bed.', 'pick': 1},
                                            {'text': 'My camera roll says the night ended with ____.', 'pick': 1},
                                            {'text': 'The bartender cut me off after I requested ____.', 'pick': 1},
                                            {'text': 'I apparently spent $84 on ____ and ____.', 'pick': 2},
                                            {'text': 'The morning-after group chat opened with a photo of ____.',
                                             'pick': 1},
                                            {'text': 'I woke up emotionally attached to ____.', 'pick': 1},
                                            {'text': 'The missing three hours were eventually explained by ____.',
                                             'pick': 1},
                                            {'text': 'My hangover came with a complimentary ____.', 'pick': 1}],
                            'white_cards': ['one shoe and no explanation',
                                            'a $63 Taco Bell order',
                                            'an Uber ride to the wrong city',
                                            'a traffic cone brought home as a friend',
                                            'a karaoke performance nobody requested',
                                            'a receipt longer than the night',
                                            'a 2 a.m. tattoo idea',
                                            'a missing phone found in the freezer',
                                            'a drunk text to the landlord',
                                            'a bathroom selfie with strangers',
                                            'a bar tab requiring financing',
                                            'a shot named after a bad decision',
                                            'a hotel key from an unknown hotel',
                                            'a plastic cup carried like a trophy',
                                            'a voicemail to an ex',
                                            'a dance floor injury discovered tomorrow',
                                            'a random slice of pizza in a pocket',
                                            'a bartender saying “absolutely not”',
                                            'a ride-share rating in free fall',
                                            'a photo holding someone else’s dog',
                                            'a hangover with surround sound',
                                            'a breakfast burrito used as medicine',
                                            'a nightclub stamp that will not wash off',
                                            'a missing jacket adopted by someone else',
                                            'a group photo with no recognizable faces',
                                            'a 4 a.m. online purchase',
                                            'a mysterious wristband',
                                            'a terrible idea shouted confidently',
                                            'an entire pitcher ordered personally',
                                            'the morning-after forensic investigation',
                                            'Tequila.',
                                            'Hangover.',
                                            'Uber.',
                                            'Regret.',
                                            'Vodka.']},
 'builtin:std_speedrun': {'name': 'STD Speed Run',
                          'description': 'Adult hookup chaos, clinic jokes, and decisions that require follow-up '
                                         'testing.',
                          'black_cards': [{'text': 'The clinic receptionist recognized me because of ____.', 'pick': 1},
                                          {'text': 'Nothing ruins a hookup faster than ____.', 'pick': 1},
                                          {'text': 'My dating bio now includes a warning about ____.', 'pick': 1},
                                          {'text': 'The “just this once” decision resulted in ____.', 'pick': 1},
                                          {'text': 'The pharmacy tech made eye contact after seeing ____ and ____.',
                                           'pick': 2},
                                          {'text': 'The health department called specifically about ____.', 'pick': 1},
                                          {'text': 'The morning-after conversation began with ____.', 'pick': 1},
                                          {'text': 'My safest dating strategy is avoiding anyone with ____.',
                                           'pick': 1},
                                          {'text': 'The situationship ended after a shared calendar invite for ____.',
                                           'pick': 1},
                                          {'text': 'The speed run category nobody wanted is ____%.', 'pick': 1},
                                          {'text': 'The clinic receptionist recognized me because of ____.', 'pick': 1},
                                          {'text': 'The hookup came with a warning label about ____.', 'pick': 1},
                                          {'text': 'My test results somehow included ____ and ____.', 'pick': 2},
                                          {'text': 'The health-class slideshow was updated after ____.', 'pick': 1},
                                          {'text': 'The waiting room went silent when I mentioned ____.', 'pick': 1},
                                          {'text': 'Nothing kills the mood faster than ____.', 'pick': 1},
                                          {'text': 'My doctor asked me to stop calling it ____.', 'pick': 1},
                                          {'text': 'The dating app should really have a filter for ____.', 'pick': 1},
                                          {'text': 'The pharmacy tech raised an eyebrow at ____.', 'pick': 1},
                                          {'text': 'The safest word in my vocabulary is now ____.', 'pick': 1}],
                          'white_cards': ['a clinic punch card that should not exist',
                                          'a dating app bio that says “clean”',
                                          'a condom found after it was useful',
                                          'an awkward pharmacy pickup',
                                          'a hookup with a follow-up appointment',
                                          'a suspicious “you should get tested” text',
                                          'a partner who says “trust me”',
                                          'a waiting room full of consequences',
                                          'an STI panel scheduled like brunch',
                                          'a notification from the county health department',
                                          'a pharmacy bag carried like evidence',
                                          'a very educational Tuesday',
                                          'a situationship with medical paperwork',
                                          'a dating profile full of warning signs',
                                          'a latex allergy discovered at the worst time',
                                          'a group chat discussing incubation periods',
                                          'a clinic receptionist who knows your birthday',
                                          'a condom brand selected by panic',
                                          'a hookup spreadsheet with color coding',
                                          'a vague burning question',
                                          'a “no symptoms though” defense',
                                          'a second opinion from Reddit',
                                          'a morning-after pharmacy run',
                                          'a doctor asking “how many partners?”',
                                          'a test result opened in the parking lot',
                                          'a partner suddenly becoming unavailable',
                                          'an incredibly specific Google search',
                                          'a public-health brochure used as a bookmark',
                                          'a text saying “so funny story”',
                                          'responsible testing after irresponsible choices',
                                          'Herpes.',
                                          'Chlamydia.',
                                          'Syphilis.',
                                          'Gonorrhea.',
                                          'Antibiotics.']},
 'builtin:femboy_hooters': {'name': 'Femboy Hooters',
                            'description': 'Server lore, questionable management, uniform violations, and VC chaos '
                                           'from Femboy Hooters.',
                            'black_cards': [{'text': 'Femboy Hooters lost another health inspection because of ____.',
                                             'pick': 1},
                                            {'text': 'Tonight’s employee special is ____ with a side of ____.',
                                             'pick': 2},
                                            {'text': 'Management has issued a formal warning about ____.', 'pick': 1},
                                            {'text': 'The uniform policy was rewritten after ____.', 'pick': 1},
                                            {'text': 'The VC went silent when someone admitted ____.', 'pick': 1},
                                            {'text': 'The Femboy Hooters mascot has been replaced with ____.',
                                             'pick': 1},
                                            {'text': 'Employee orientation now includes a section on ____.', 'pick': 1},
                                            {'text': 'The secret menu contains ____ and legally cannot contain ____.',
                                             'pick': 2},
                                            {'text': 'The staff meeting was canceled due to ____.', 'pick': 1},
                                            {'text': 'The server owner logged on and immediately found ____.',
                                             'pick': 1},
                                            {'text': 'The Femboy Hooters employee handbook specifically bans ____.',
                                             'pick': 1},
                                            {'text': 'Tonight’s special comes with fries, ranch, and ____.', 'pick': 1},
                                            {'text': 'Management had to schedule an emergency meeting about ____.',
                                             'pick': 1},
                                            {'text': 'The uniform policy was rewritten after ____.', 'pick': 1},
                                            {'text': 'Table seven tipped entirely in ____.', 'pick': 1},
                                            {'text': 'The health inspector found ____ behind the bar.', 'pick': 1},
                                            {'text': 'The employee of the month award went to ____ for surviving ____.',
                                             'pick': 2},
                                            {'text': 'The server lore officially begins with ____.', 'pick': 1},
                                            {'text': 'The kitchen ticket simply read “NO ____.”', 'pick': 1},
                                            {'text': 'Femboy Hooters lost its liquor-adjacent privileges because of '
                                                     '____.',
                                             'pick': 1}],
                            'white_cards': ['a crop top violating seven labor laws',
                                            'thigh highs with management authority',
                                            'an employee meal nobody can identify',
                                            'a suspiciously sticky menu',
                                            'a Discord VC used as a break room',
                                            'a manager typing “who did this”',
                                            'a customer asking for the secret menu',
                                            'an HR department consisting of one meme',
                                            'a uniform inspection getting weird',
                                            'a chicken wing with emotional damage',
                                            'a tip jar labeled “legal defense”',
                                            'a server nickname that became canon',
                                            'a staff meeting held at 3 a.m.',
                                            'a health inspector joining the Discord',
                                            'a fryer basket full of poor decisions',
                                            'a customer banned from the premises and VC',
                                            'a neon sign flickering ominously',
                                            'a name tag reading “probably staff”',
                                            'a break room with RGB lighting',
                                            'a shift lead wearing cat ears',
                                            'a receipt containing a Discord invite',
                                            'a suspicious amount of ranch',
                                            'a manager using reaction roles as scheduling',
                                            'a customer complaint turned copypasta',
                                            'a table reserved for server lore',
                                            'an employee handbook written in lowercase',
                                            'a hostess stand doubling as moderation HQ',
                                            'a uniform accessory from Spencer’s',
                                            'a shift change announced with an @everyone',
                                            'Femboy Hooters corporate refusing to comment',
                                            'Thighhighs.',
                                            'Femboys.',
                                            'Hooters.',
                                            'Fishnets.',
                                            'Management.']},
 'builtin:invader_zim': {'name': 'Invader Zim',
                         'description': 'Irken schemes, GIR chaos, Dib paranoia, and doomed Earth-conquering energy.',
                         'black_cards': [{'text': 'Zim’s latest plan to conquer Earth depends entirely on ____.',
                                          'pick': 1},
                                         {'text': 'GIR was left alone for five minutes and somehow created ____.',
                                          'pick': 1},
                                         {'text': 'Dib finally found proof of aliens, but unfortunately it was just '
                                                  '____.',
                                          'pick': 1},
                                         {'text': 'The Tallest rejected Zim’s mission report after seeing ____.',
                                          'pick': 1},
                                         {'text': 'The Skool has officially banned ____ from the cafeteria.',
                                          'pick': 1},
                                         {'text': 'Gaz paused her game exactly once to deal with ____.', 'pick': 1},
                                         {'text': 'The Irken invasion failed because of ____ and an alarming amount of '
                                                  '____.',
                                          'pick': 2},
                                         {'text': 'Zim disguised ____ as a completely normal human activity.',
                                          'pick': 1},
                                         {'text': 'Professor Membrane insists science can explain ____ but not ____.',
                                          'pick': 2},
                                         {'text': 'Earth was almost destroyed by something embarrassingly simple: '
                                                  '____.',
                                          'pick': 1},
                                         {'text': 'Zim’s latest plan to conquer Earth depends entirely on ____.',
                                          'pick': 1},
                                         {'text': 'GIR abandoned the mission after discovering ____.', 'pick': 1},
                                         {'text': 'Dib finally found undeniable proof of ____.', 'pick': 1},
                                         {'text': 'The Tallest rejected Zim’s report because it contained ____.',
                                          'pick': 1},
                                         {'text': 'The school cafeteria is secretly powered by ____.', 'pick': 1},
                                         {'text': 'Zim disguised ____ as ____ and somehow fooled everyone.', 'pick': 2},
                                         {'text': 'Gaz paused her game only because of ____.', 'pick': 1},
                                         {'text': 'The newest Irken technology is suspiciously similar to ____.',
                                          'pick': 1},
                                         {'text': 'The Earth invasion failed again because of ____.', 'pick': 1},
                                         {'text': 'GIR put ____ in the disguise and called it perfect.', 'pick': 1}],
                         'white_cards': ['GIR wearing something he absolutely should not be wearing',
                                         'an Irken plan held together by duct tape',
                                         'Dib presenting a 400-slide conspiracy deck',
                                         'Gaz choosing violence without looking up from her game',
                                         'a robot dog causing a municipal emergency',
                                         'Zim yelling at a household appliance',
                                         'an alien disguise nobody questions for some reason',
                                         'a mission report written entirely in panic',
                                         'a cafeteria lunch with extraterrestrial consequences',
                                         'the Tallest ignoring another incoming call',
                                         'a wildly unnecessary underground laboratory',
                                         'a dramatic cape used for absolutely no tactical reason',
                                         'an evil scheme ruined by basic household maintenance',
                                         'a suspicious amount of waffles',
                                         'a human child with too much evidence',
                                         'a computer screaming about system failure',
                                         'an invasion fleet with terrible customer service',
                                         'a tiny robot with catastrophic confidence',
                                         'an experiment escaping into the neighborhood',
                                         'a science fair project with military applications',
                                         'an alien snack with unknowable ingredients',
                                         'a disguise made worse by adding more disguise',
                                         'a government agent who is somehow less prepared than Dib',
                                         'an Irken device pointed at the wrong planet',
                                         'a screaming lawn gnome',
                                         'a school presentation nobody believes',
                                         'a doomsday machine powered by snack food',
                                         'a secret base hidden with zero subtlety',
                                         'a malfunctioning robot insisting everything is fine',
                                         'a perfectly avoidable planetary incident',
                                         'Zim.',
                                         'GIR.',
                                         'Dib.',
                                         'Gaz.',
                                         'Irken.']},
 'builtin:helluva_boss': {'name': 'Helluva Boss',
                          'description': 'I.M.P. jobs, infernal workplace disasters, relationship chaos, and very bad '
                                         'decisions.',
                          'black_cards': [{'text': 'The latest I.M.P. contract went off the rails because of ____.',
                                           'pick': 1},
                                          {'text': 'Blitzø ruined the meeting by bringing up ____ again.', 'pick': 1},
                                          {'text': 'Moxxie prepared a careful plan, which was immediately replaced by '
                                                   '____.',
                                           'pick': 1},
                                          {'text': 'Millie solved the problem with ____ and absolutely no paperwork.',
                                           'pick': 1},
                                          {'text': 'Loona looked up from her phone only because of ____.', 'pick': 1},
                                          {'text': 'Stolas opened the grimoire and accidentally summoned ____.',
                                           'pick': 1},
                                          {'text': 'The mission required stealth, so naturally they brought ____ and '
                                                   '____.',
                                           'pick': 2},
                                          {'text': 'Hell’s least professional workplace award goes to ____.',
                                           'pick': 1},
                                          {'text': 'The client wanted revenge. They did not ask for ____.', 'pick': 1},
                                          {'text': 'The office group chat was permanently archived after ____.',
                                           'pick': 1},
                                          {'text': 'I.M.P. accepted a contract involving ____.', 'pick': 1},
                                          {'text': 'Blitzø ruined the mission by bringing ____.', 'pick': 1},
                                          {'text': 'Moxxie tried to make a reasonable plan until ____ happened.',
                                           'pick': 1},
                                          {'text': 'Millie solved the problem with ____.', 'pick': 1},
                                          {'text': 'Loona refused to answer the phone because of ____.', 'pick': 1},
                                          {'text': 'Stolas opened the grimoire and accidentally summoned ____.',
                                           'pick': 1},
                                          {'text': 'The mission briefing included ____ but somehow forgot ____.',
                                           'pick': 2},
                                          {'text': 'Hell’s newest workplace violation involves ____.', 'pick': 1},
                                          {'text': 'The client wanted revenge for ____.', 'pick': 1},
                                          {'text': 'The office meeting ended when somebody mentioned ____.',
                                           'pick': 1}],
                          'white_cards': ['an I.M.P. job with no usable briefing',
                                          'Blitzø making the situation deeply personal',
                                          'Moxxie trying to follow the actual plan',
                                          'Millie arriving with immediate solutions',
                                          'Loona answering the phone with visible contempt',
                                          'Stolas making everything more emotionally complicated',
                                          'a grimoire being used with questionable supervision',
                                          'an assassination target with excellent timing',
                                          'a workplace romance nobody handles normally',
                                          'a hellhound with zero patience left',
                                          'a musical number occurring during a crisis',
                                          'a mission vehicle returning with several new dents',
                                          'a client explaining way too much backstory',
                                          'an office weapon stored next to lunch',
                                          'a portal opening somewhere profoundly inconvenient',
                                          'an infernal HR complaint nobody intends to read',
                                          'a dramatic phone call in the middle of a job',
                                          'a family issue becoming everyone’s problem',
                                          'a contract that should have included hazard pay',
                                          'a very expensive mistake in the human world',
                                          'a demon pretending this is all completely professional',
                                          'an emergency exit through the wrong portal',
                                          'a weapon selected mostly for style',
                                          'a revenge plan becoming a relationship argument',
                                          'a receptionist refusing to be impressed',
                                          'a target who somehow has worse impulse control than I.M.P.',
                                          'a job briefing interrupted by personal drama',
                                          'a completely avoidable infernal scandal',
                                          'a workplace meeting ending in property damage',
                                          'an extremely messy trip to the living world',
                                          'Blitzø.',
                                          'Stolas.',
                                          'Loona.',
                                          'Moxxie.',
                                          'Millie.']}
,
 'builtin:jujutsu_kaisen': {'name': 'Jujutsu Kaisen',
                            'description': 'Curses, domain expansions, terrible mentorship, and Gojo being an absolute public nuisance.',
                            'black_cards': [
                                {'text': 'Gojo got banned from another establishment after ____.', 'pick': 1},
                                {'text': 'The real reason Gojo wears a blindfold is to hide ____.', 'pick': 1},
                                {'text': 'Gojo’s latest lesson plan consists entirely of ____ and ____.', 'pick': 2},
                                {'text': 'Yuji swallowed another cursed object because apparently ____ was not enough.', 'pick': 1},
                                {'text': 'Megumi summoned Mahoraga over something as minor as ____.', 'pick': 1},
                                {'text': 'Nobara solved the entire mission using ____ and pure spite.', 'pick': 1},
                                {'text': 'Sukuna took over Yuji’s body just to experience ____ firsthand.', 'pick': 1},
                                {'text': 'Nanami clocked out immediately after seeing ____.', 'pick': 1},
                                {'text': 'Gojo claims Infinity can protect him from everything except ____.', 'pick': 1},
                                {'text': 'Geto’s villain origin story could have been prevented by ____.', 'pick': 1},
                                {'text': 'Toji accepted the contract the second somebody offered him ____.', 'pick': 1},
                                {'text': 'Jujutsu High’s newest safety policy specifically bans ____.', 'pick': 1},
                                {'text': 'The cursed spirit was born from humanity’s collective fear of ____.', 'pick': 1},
                                {'text': 'Gojo entered the room carrying ____ like it was completely normal.', 'pick': 1},
                                {'text': 'The Domain Expansion opened and revealed nothing but ____.', 'pick': 1},
                                {'text': 'Shoko refuses to explain why the infirmary smells like ____.', 'pick': 1},
                                {'text': 'The mission went perfectly until Gojo said, “Trust me,” and produced ____.', 'pick': 1},
                                {'text': 'Yuji’s greatest weakness is not Sukuna. It is ____.', 'pick': 1},
                                {'text': 'Megumi looked tired before the mission even started because of ____.', 'pick': 1},
                                {'text': 'Nobara’s newest cursed technique is basically ____ with extra violence.', 'pick': 1},
                                {'text': 'Gojo’s emergency plan is just ____ at maximum output.', 'pick': 1},
                                {'text': 'Sukuna looked genuinely offended when confronted with ____.', 'pick': 1},
                                {'text': 'Nanami considers ____ a violation of overtime policy.', 'pick': 1},
                                {'text': 'The higher-ups are furious that Gojo taught the students about ____.', 'pick': 1},
                                {'text': 'Toji arrived shirtless, armed, and somehow carrying ____.', 'pick': 1},
                                {'text': 'Geto opened his robe and an alarming amount of ____ fell out.', 'pick': 1},
                                {'text': 'The strongest sorcerer alive was defeated by ____ and poor judgment.', 'pick': 2},
                                {'text': 'Gojo’s search history contains a suspicious amount of ____.', 'pick': 1},
                                {'text': 'Jujutsu society finally collapsed because of ____.', 'pick': 1},
                                {'text': 'The next special-grade curse will definitely be born from ____.', 'pick': 1},
                            ],
                            'white_cards': [
                                'Gojo using Infinity to avoid paying the bill',
                                'Gojo taking a thirst trap during an active emergency',
                                'Gojo saying “I got this” immediately before making it worse',
                                'Gojo flirting with his own reflection',
                                'Gojo weaponizing pretty privilege',
                                'Gojo removing the blindfold for absolutely no tactical reason',
                                'Gojo arriving three hours late with snacks',
                                'Gojo treating a special-grade curse like a minor inconvenience',
                                'Gojo sending the students into danger and calling it character development',
                                'Gojo buying something expensive with somebody else’s money',
                                'Gojo explaining nothing and looking gorgeous while doing it',
                                'Gojo making direct eye contact and ruining someone’s week',
                                'Gojo using Unlimited Void on a customer service representative',
                                'Gojo pretending he cannot hear criticism through Infinity',
                                'Gojo starting drama and teleporting away',
                                'Gojo turning a funeral into a networking opportunity',
                                'Gojo.',
                                'Infinity.',
                                'Blue.',
                                'Red.',
                                'Purple.',
                                'Six Eyes.',
                                'Sukuna committing atrocities out of boredom',
                                'Sukuna being insulted by the concept of sharing',
                                'Sukuna treating Yuji like a timeshare',
                                'Sukuna demanding more fingers',
                                'Sukuna.',
                                'Malevolent Shrine.',
                                'Yuji eating something that should not be eaten',
                                'Yuji apologizing while actively making things worse',
                                'Yuji trying his best and being punished by the narrative',
                                'Yuji.',
                                'Megumi reaching for Mahoraga over a parking dispute',
                                'Megumi looking exhausted beyond his years',
                                'Megumi summoning something wildly disproportionate',
                                'Megumi.',
                                'Mahoraga.',
                                'Nobara solving interpersonal conflict with a hammer',
                                'Nobara refusing to lower her standards for anyone',
                                'Nobara.',
                                'Nanami checking the clock during a supernatural disaster',
                                'Nanami.',
                                'Overtime.',
                                'Toji showing up for money and leaving with trauma',
                                'Toji’s gambling debt',
                                'Toji.',
                                'Geto.',
                                'a cursed spirit with the energy of a Discord moderator',
                                'a cursed womb in the break room fridge',
                                'a Domain Expansion with terrible interior design',
                                'an emotional support cursed tool',
                                'a cursed object labeled “do not lick”',
                                'a special-grade problem nobody budgeted for',
                                'a binding vow made while extremely sleep deprived',
                                'a black flash triggered by pure annoyance',
                                'a cursed technique powered by bad decisions',
                                'a school field trip with a casualty rate',
                                'a mission report that just says “Gojo happened”',
                                'a completely avoidable exorcism incident',
                                'trauma.',
                            ]}
}


PACK_CATEGORIES: dict[str, tuple[str, str, list[str]]] = {
    "core": ("🌪️ Core Chaos", "Internet-brain and general chaos.", [
        "builtin:chaos", "builtin:gaming", "builtin:foul", "builtin:foul2",
        "builtin:unhinged_internet", "builtin:group_chat", "builtin:red_flag",
    ]),
    "disaster": ("💀 Life Is a Disaster", "Family, dating, bathroom, legal, and terrible-decision packs.", [
        "builtin:family_hell", "builtin:bad_parenting", "builtin:bathroom_crime",
        "builtin:divorce_court", "builtin:blackout_drunk", "builtin:std_speedrun", "builtin:florida_man",
    ]),
    "fandom": ("🎭 Fandom / Events", "Convention and fandom chaos.", [
        "builtin:convention", "builtin:pokemon", "builtin:digital_circus",
        "builtin:hazbin", "builtin:helluva_boss", "builtin:invader_zim",
        "builtin:jujutsu_kaisen", "builtin:youtube_gremlins",
    ]),
    "server": ("🍑 Server Lore", "Packs made for the server and its ongoing crimes.", [
        "builtin:femboy_hooters", "builtin:marcus_worm",
    ]),
}

GAME_NAME = "Femboy Hooters Against Humanity"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _short(text: str, limit: int = 80) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _display_prompt(text: str, pick: int = 1) -> str:
    """Render CAH blanks clearly in Discord without underscore markdown bleed."""
    raw = str(text or "")
    total = max(1, int(pick or 1))
    slot = 0

    def repl(_match: re.Match[str]) -> str:
        nonlocal slot
        slot += 1
        if total <= 1:
            return "**[ CARD ]**"
        return f"**[ CARD {slot} ]**"

    rendered = re.sub(r"_{2,}", repl, raw)
    return rendered


def _is_staff(member: discord.Member) -> bool:
    p = member.guild_permissions
    return member.id == member.guild.owner_id or p.administrator or p.manage_guild


@dataclass
class GameRules:
    czar_mode: str = "ai_if_needed"  # human | ai_if_needed | ai_always
    ai_style: str = "funniest"
    hand_size: int = 8
    score_to_win: int = 5
    blank_chance_percent: int = 15
    starting_blank_count: int = 0
    mommy_player: bool = False
    daddy_player: bool = False
    ai_wildcard_chance: float = 0.20


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
        # Played regular white cards leave the player's hand immediately, then
        # re-enter the shared draw pool at a random position. This means a used
        # card can show up again later by chance, while cards still being held
        # by players remain out of circulation.
        self.white_discard: list[str] = []
        self.current_black: dict[str, Any] | None = None
        self.current_czar_id: int | None = None
        self.czar_rotation: list[int] = []
        self.czar_index = -1
        self.submissions: dict[int, Submission] = {}
        self.round_number = 0
        self.last_ai_reason = ""
        self.lock = asyncio.Lock()
        self.afk_misses: dict[int, int] = {}
        self._submission_timeout_task: asyncio.Task | None = None
        self._czar_timeout_task: asyncio.Task | None = None
        # Monotonic generation for round timers. Old timeout tasks can never
        # mutate a newer round even if Discord/API work delays cancellation.
        self._round_token = 0

    def host(self) -> discord.Member | None:
        return self.guild.get_member(self.host_id)

    def participant_ids(self) -> list[int]:
        ids = list(self.players)
        if self.rules.mommy_player and MOMMY_PLAYER_ID not in ids:
            ids.append(MOMMY_PLAYER_ID)
        if self.rules.daddy_player and DADDY_PLAYER_ID not in ids:
            ids.append(DADDY_PLAYER_ID)
        return ids

    def display_player(self, user_id: int) -> str:
        if user_id == MOMMY_PLAYER_ID:
            return "🤖 Mommy.exe"
        if user_id == DADDY_PLAYER_ID:
            return "🥋 Daddy’s Belt"
        return f"<@{user_id}>"

    def sync_ai_players(self) -> None:
        if self.rules.mommy_player:
            self.players.setdefault(MOMMY_PLAYER_ID, PlayerState(MOMMY_PLAYER_ID, "Mommy.exe", is_bot=True))
        else:
            self.players.pop(MOMMY_PLAYER_ID, None)
            self.czar_rotation = [uid for uid in self.czar_rotation if uid != MOMMY_PLAYER_ID]

        if self.rules.daddy_player:
            self.players.setdefault(DADDY_PLAYER_ID, PlayerState(DADDY_PLAYER_ID, "Daddy’s Belt", is_bot=True))
        else:
            self.players.pop(DADDY_PLAYER_ID, None)
            self.czar_rotation = [uid for uid in self.czar_rotation if uid != DADDY_PLAYER_ID]

    # Compatibility for older call sites.
    def sync_mommy_player(self) -> None:
        self.sync_ai_players()

    def eligible_submitters(self) -> list[int]:
        if self.current_czar_id is None:
            return list(self.players)
        return [uid for uid in self.players if uid != self.current_czar_id]

    def is_ai_czar(self) -> bool:
        return self.current_czar_id is None or self.current_czar_id in AI_PLAYER_IDS

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

    def draw_white(self, *, exclude: set[str] | None = None) -> str:
        if not self.white_pool:
            return "the horrifying realization that the deck is empty"

        blocked = exclude or set()
        # Draw randomly from cards that are not the exact text(s) this player
        # just used. This also protects against duplicate text across mixed packs.
        eligible = [i for i, card in enumerate(self.white_pool) if card not in blocked]
        if eligible:
            return self.white_pool.pop(random.choice(eligible))
        return self.white_pool.pop(random.randrange(len(self.white_pool)))

    def discard_white(self, card: str) -> None:
        if not card or card == BLANK_TOKEN:
            return
        # A played white card leaves circulation first. It only has a 25%
        # chance to return to the shared draw pool for a later draw; otherwise
        # it stays out for the rest of this game.
        if random.random() < 0.25:
            insert_at = random.randint(0, len(self.white_pool))
            self.white_pool.insert(insert_at, card)
        else:
            self.white_discard.append(card)

    def fill_hand(self, player: PlayerState, *, initial: bool = False, avoid: set[str] | None = None) -> None:
        # Unused cards stay in place. Only empty hand slots are refilled.
        player.hand = list(player.hand[: self.rules.hand_size])
        blocked = set(avoid or set())
        blocked.update(card for card in player.hand if card != BLANK_TOKEN)

        if initial:
            # The host can choose exactly how many write-in blanks everyone
            # starts with. Zero means starting blanks are disabled.
            wanted_blanks = max(
                0,
                min(
                    int(self.rules.starting_blank_count),
                    self.rules.hand_size,
                    MAX_WRITE_IN_BLANKS_PER_HAND,
                ),
            )
            current_blanks = sum(1 for card in player.hand if card == BLANK_TOKEN)
            while current_blanks < wanted_blanks and len(player.hand) < self.rules.hand_size:
                player.hand.append(BLANK_TOKEN)
                current_blanks += 1
            blank_chance = 0.0
        else:
            # After play begins, a replacement slot can independently become a
            # write-in blank according to the host's configured draw chance.
            blank_chance = max(0, min(50, int(self.rules.blank_chance_percent))) / 100.0

        while len(player.hand) < self.rules.hand_size:
            current_blanks = sum(1 for card in player.hand if card == BLANK_TOKEN)
            if (
                current_blanks < MAX_WRITE_IN_BLANKS_PER_HAND
                and blank_chance > 0
                and random.random() < blank_chance
            ):
                player.hand.append(BLANK_TOKEN)
                continue
            card = self.draw_white(exclude=blocked)
            player.hand.append(card)
            if card != BLANK_TOKEN:
                blocked.add(card)

        if initial:
            random.shuffle(player.hand)

    def decide_czar(self) -> None:
        self.sync_mommy_player()
        ids = self.participant_ids()
        human_ids = [uid for uid in ids if uid not in AI_PLAYER_IDS]
        mode = self.rules.czar_mode

        # AI Always keeps Mommy in the judge seat. In the rotating modes, enabling
        # Mommy as a player places her in the same Czar queue as everyone else.
        if mode == "ai_always":
            enabled_ai = [uid for uid in (MOMMY_PLAYER_ID, DADDY_PLAYER_ID) if uid in ids]
            self.current_czar_id = random.choice(enabled_ai) if enabled_ai else MOMMY_PLAYER_ID
            return
        if mode == "ai_if_needed" and not any(uid in ids for uid in AI_PLAYER_IDS) and len(human_ids) < 3:
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
        human_count = len([uid for uid in self.players if uid not in AI_PLAYER_IDS])
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

    def _cancel_submission_timeout(self) -> None:
        task = self._submission_timeout_task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
        self._submission_timeout_task = None

    def _cancel_czar_timeout(self) -> None:
        task = self._czar_timeout_task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
        self._czar_timeout_task = None

    def _remove_afk_player(self, user_id: int) -> None:
        if user_id in AI_PLAYER_IDS:
            return
        self.players.pop(user_id, None)
        self.afk_misses.pop(user_id, None)
        self.czar_rotation = [uid for uid in self.czar_rotation if uid != user_id]

    async def _submission_timeout(self, round_number: int, round_token: int) -> None:
        try:
            await asyncio.sleep(90)
            # Token + round number prevents a delayed/stale task from touching
            # any later round.
            if (
                self.status != "playing"
                or self.round_number != round_number
                or self._round_token != round_token
            ):
                return

            missing = [
                uid for uid in self.eligible_submitters()
                if uid not in AI_PLAYER_IDS and uid not in self.submissions
            ]
            removed: list[int] = []
            for uid in missing:
                self.afk_misses[uid] = self.afk_misses.get(uid, 0) + 1
                if self.afk_misses[uid] >= 2:
                    removed.append(uid)

            for uid in removed:
                self._remove_afk_player(uid)

            if missing:
                skipped = len(missing)
                removed_count = len(removed)
                note = f"⏰ {skipped} player{'s' if skipped != 1 else ''} timed out and were skipped this round."
                if removed_count:
                    note += f" {removed_count} removed for missing 2 rounds in a row."
                try:
                    await self.channel.send(note)
                except discord.HTTPException:
                    pass

            # Re-check after sends/removals in case the round changed while we
            # were awaiting Discord.
            if (
                self.status != "playing"
                or self.round_number != round_number
                or self._round_token != round_token
            ):
                return

            if self.submissions:
                await self.begin_judging()
            else:
                if len(self.participant_ids()) < 2:
                    await self.end()
                    return
                await self.start_round()
        except asyncio.CancelledError:
            pass
        finally:
            if self._submission_timeout_task is asyncio.current_task():
                self._submission_timeout_task = None

    async def _czar_timeout(self, round_number: int, round_token: int) -> None:
        try:
            await asyncio.sleep(60)
            if (
                self.status != "judging"
                or self.round_number != round_number
                or self._round_token != round_token
                or not self.submissions
            ):
                return

            # Human Czars also accrue AFK misses. Two missed judging turns removes
            # them from the table, matching player submission AFK behavior.
            czar_id = self.current_czar_id
            if czar_id is not None and czar_id not in AI_PLAYER_IDS:
                self.afk_misses[czar_id] = self.afk_misses.get(czar_id, 0) + 1
                if self.afk_misses[czar_id] >= 2:
                    self._remove_afk_player(czar_id)

            ordered = list(self.submissions.values())
            winner_id, reason = await self.cog.ai_choose(
                self.current_black or {}, ordered, self.rules.ai_style
            )
            if (
                self.status != "judging"
                or self.round_number != round_number
                or self._round_token != round_token
            ):
                return
            reason = ("Czar timed out — Mommy picked automatically. " + reason).strip()
            await self.finish_round(winner_id, reason)
        except asyncio.CancelledError:
            pass
        finally:
            if self._czar_timeout_task is asyncio.current_task():
                self._czar_timeout_task = None

    async def start_round(self) -> None:
        self._cancel_submission_timeout()
        self._cancel_czar_timeout()
        self._round_token += 1
        round_token = self._round_token
        self.round_number += 1
        self.submissions.clear()
        self.last_ai_reason = ""
        if not self.black_pool:
            await self.load_pools()
        self.current_black = self.black_pool.pop() if self.black_pool else {"text": "____", "pick": 1}
        self.decide_czar()
        await self.render_round(new_message=True)
        self._submission_timeout_task = asyncio.create_task(
            self._submission_timeout(self.round_number, round_token)
        )
        if self.rules.mommy_player and MOMMY_PLAYER_ID in self.eligible_submitters():
            await self.cog.ai_player_submit(self, MOMMY_PLAYER_ID)
        if self.rules.daddy_player and DADDY_PLAYER_ID in self.eligible_submitters():
            await self.cog.ai_player_submit(self, DADDY_PLAYER_ID)

    def round_embed(self) -> discord.Embed:
        black = self.current_black or {"text": "Waiting…", "pick": 1}
        if self.current_czar_id is None:
            czar = "🤖 Mommy.exe (AI Czar)"
        elif self.current_czar_id == MOMMY_PLAYER_ID:
            czar = "🤖 Mommy.exe (Czar)"
        elif self.current_czar_id == DADDY_PLAYER_ID:
            czar = "🥋 Daddy’s Belt (Czar)"
        else:
            czar = f"👑 <@{self.current_czar_id}>"
        prompt_text = _display_prompt(black.get("text", ""), int(black.get("pick", 1)))
        embed = discord.Embed(title=f"🃏 {GAME_NAME} • Round {self.round_number}", description=f"### {prompt_text}")
        embed.add_field(name="Czar", value=czar, inline=True)
        embed.add_field(name="Pick", value=str(black.get("pick", 1)), inline=True)
        embed.add_field(name="Submitted", value=f"{len(self.submissions)}/{len(self.eligible_submitters())}", inline=True)
        scores = sorted(self.players.values(), key=lambda p: (-p.score, p.name.casefold()))
        embed.add_field(name="Scoreboard", value="\n".join(f"**{p.score}** — {self.display_player(p.user_id)}" for p in scores) or "No scores yet.", inline=False)
        embed.set_footer(text=f"AI style: {self.rules.ai_style.replace('_', ' ').title()} • First to {self.rules.score_to_win} • 90s to submit")
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
            # Played cards leave the hand immediately. Hold normal white cards
            # aside until AFTER replacement cards are drawn so a card can never
            # become its own immediate replacement. Once the hand is refilled,
            # used cards go back into random positions in the shared pool and can
            # return naturally on a later draw.
            unique_indexes = sorted(set(hand_indexes), reverse=True)
            need = max(1, int((self.current_black or {}).get("pick", 1)))
            if len(unique_indexes) != need or any(idx < 0 or idx >= len(player.hand) for idx in unique_indexes):
                return False, "Your hand changed before that submission could be locked in. Open your hand again."

            used_cards: list[str] = []
            for idx in unique_indexes:
                used_card = player.hand.pop(idx)
                if used_card != BLANK_TOKEN:
                    used_cards.append(used_card)

            # Refill the exact missing slots while excluding the text just used.
            # This guarantees that a played card is actually gone from the next
            # hand, even if mixed packs contain duplicate copies of that text.
            self.fill_hand(player, avoid=set(used_cards))

            # Only after the replacement draw do played cards re-enter circulation
            # at randomized positions, so they can return on a later draw by chance.
            for used_card in used_cards:
                self.discard_white(used_card)

            self.submissions[user_id] = Submission(user_id=user_id, cards=cards)
            if user_id not in AI_PLAYER_IDS:
                self.afk_misses[user_id] = 0
            await self.render_round()
            if len(self.submissions) >= len(self.eligible_submitters()):
                await self.begin_judging()
            return True, "Card submitted."

    async def begin_judging(self) -> None:
        if self.status != "playing" or not self.submissions:
            return
        self._cancel_submission_timeout()
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
        self._czar_timeout_task = asyncio.create_task(
            self._czar_timeout(self.round_number, self._round_token)
        )

    def judging_embed(self, ordered: list[Submission]) -> discord.Embed:
        black = self.current_black or {}
        prompt_text = _display_prompt(black.get("text", ""), int(black.get("pick", 1)))
        embed = discord.Embed(title="👑 Czar, choose the winner", description=f"### {prompt_text}")
        for i, submission in enumerate(ordered):
            embed.add_field(name=f"Option {chr(65+i)}", value=" / ".join(submission.cards), inline=False)
        embed.set_footer(text="Submissions are anonymous until the winner is chosen. • Czar has 60s")
        return embed

    async def finish_round(self, winner_id: int, reason: str = "") -> None:
        self._cancel_czar_timeout()
        self._cancel_submission_timeout()
        if winner_id not in self.players:
            winner_id = next(iter(self.submissions))
        self.players[winner_id].score += 1
        self.last_ai_reason = reason
        winner_submission = self.submissions.get(winner_id)
        embed = discord.Embed(title="🏆 Round winner", description=f"{self.display_player(winner_id)} wins the round!")
        if winner_submission:
            embed.add_field(name="🥇 Winning answer", value=" / ".join(winner_submission.cards), inline=False)

        # Reveal every submitted answer after judging so the winner screen also
        # serves as a clean recap of what the Czar chose from.
        recap_lines: list[str] = []
        for uid, submission in self.submissions.items():
            marker = "🏆" if uid == winner_id else "•"
            answer = " / ".join(submission.cards)
            recap_lines.append(f"{marker} {self.display_player(uid)} — {answer}")
        if recap_lines:
            recap = "\n".join(recap_lines)
            # Discord embed field values cap at 1024 characters.
            embed.add_field(name="All answers this round", value=_short(recap, 1024), inline=False)

        if reason:
            embed.add_field(name="AI Czar's take", value=_short(reason, 500), inline=False)
        embed.add_field(name="Score", value=f"**{self.players[winner_id].score}** / {self.rules.score_to_win}", inline=False)
        # Remove the stale active judging/round card so the current game UI
        # cannot remain stranded above the winner. The winner card itself stays
        # in chat as round history.
        if self.message is not None:
            try:
                await self.message.delete()
            except discord.HTTPException:
                try:
                    await self.message.edit(view=None)
                except discord.HTTPException:
                    pass

        if self.players[winner_id].score >= self.rules.score_to_win:
            self.status = "finished"
            embed.title = "🎉 Game over"
            embed.description = f"{self.display_player(winner_id)} wins the game!"
            if winner_id not in AI_PLAYER_IDS:
                await self.cog.record_win(self.guild.id, winner_id)
            self.message = await self.channel.send(embed=embed, view=GameOverView(self))
        else:
            self.status = "between_rounds"
            self.message = await self.channel.send(embed=embed, view=BetweenRoundsView(self))

    async def end(self) -> None:
        self._cancel_submission_timeout()
        self._cancel_czar_timeout()
        self._round_token += 1
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

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, _):
        embed = discord.Embed(
            title=f"🃏 {GAME_NAME}",
            description="Create a lobby, manage your decks, use random write-in blanks, and mix multiple packs.",
        )
        await interaction.response.edit_message(
            embed=embed,
            view=CAHHomeView(self.cog, self.user_id),
        )


def _pack_summary(selected_ids: list[str], catalog: dict[str, tuple[str, str]]) -> str:
    names = [catalog[pid][0] for pid in selected_ids if pid in catalog]
    if not names:
        return "Nothing selected yet."
    shown = names[:6]
    suffix = f" +{len(names) - 6} more" if len(names) > 6 else ""
    return " • ".join(shown) + suffix


def pack_picker_embed(setup: "GameSetupView", catalog: dict[str, tuple[str, str]]) -> discord.Embed:
    selected = setup.session.selected_pack_ids
    embed = discord.Embed(
        title="🗃️ Choose Packs",
        description="Pick a category, then choose any individual packs you want. Mix as many categories and packs as you like.",
    )
    embed.add_field(name=f"Selected • {len(selected)}", value=_pack_summary(selected, catalog), inline=False)
    return embed


def category_embed(setup: "GameSetupView", category_key: str, catalog: dict[str, tuple[str, str]], category_ids: list[str]) -> discord.Embed:
    title, description, _ = PACK_CATEGORIES.get(category_key, ("🗃️ Custom Packs", "Your ready personal and server packs.", []))
    selected = [pid for pid in category_ids if pid in setup.session.selected_pack_ids]
    embed = discord.Embed(title=title, description=description)
    embed.add_field(name=f"Selected here • {len(selected)}/{len(category_ids)}", value=_pack_summary(selected, catalog), inline=False)
    embed.set_footer(text="Choose any combination. Your selections stay active when you switch categories.")
    return embed


class PackCategoryButton(discord.ui.Button):
    def __init__(self, parent: "PackCategoryHubView", category_key: str, label: str, emoji: str, row: int):
        super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.secondary, row=row)
        self.parent_view = parent
        self.category_key = category_key

    async def callback(self, interaction: discord.Interaction):
        ids = self.parent_view.category_ids(self.category_key)
        if not ids:
            await interaction.response.send_message("There are no ready packs in that category yet.", ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=category_embed(self.parent_view.setup, self.category_key, self.parent_view.catalog, ids),
            view=PackCategoryView(self.parent_view, self.category_key, ids),
        )


class PackMultiSelect(discord.ui.Select):
    def __init__(self, category_view: "PackCategoryView"):
        self.category_view = category_view
        selected = set(category_view.parent_view.setup.session.selected_pack_ids)
        options = []
        for pid in category_view.pack_ids[:25]:
            name, description = category_view.parent_view.catalog[pid]
            options.append(discord.SelectOption(
                label=_short(name, 95),
                value=pid,
                description=_short(description, 95) if description else None,
                default=pid in selected,
            ))
        super().__init__(
            placeholder="Choose specific packs…",
            min_values=0,
            max_values=max(1, len(options)),
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        selected = self.category_view.parent_view.setup.session.selected_pack_ids
        category_set = set(self.category_view.pack_ids)
        kept = [pid for pid in selected if pid not in category_set]
        chosen = list(self.values)
        self.category_view.parent_view.setup.session.selected_pack_ids = kept + chosen
        await interaction.response.edit_message(
            embed=category_embed(
                self.category_view.parent_view.setup,
                self.category_view.category_key,
                self.category_view.parent_view.catalog,
                self.category_view.pack_ids,
            ),
            view=PackCategoryView(self.category_view.parent_view, self.category_view.category_key, self.category_view.pack_ids),
        )


class PackCategoryView(discord.ui.View):
    def __init__(self, parent_view: "PackCategoryHubView", category_key: str, pack_ids: list[str]):
        super().__init__(timeout=600)
        self.parent_view = parent_view
        self.category_key = category_key
        self.pack_ids = pack_ids
        self.add_item(PackMultiSelect(self))

    async def interaction_check(self, interaction):
        if interaction.user.id != self.parent_view.setup.session.host_id:
            await interaction.response.send_message("Only the host can change game setup.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Select All Here", emoji="✅", style=discord.ButtonStyle.success, row=1)
    async def select_all(self, interaction, _):
        selected = self.parent_view.setup.session.selected_pack_ids
        for pid in self.pack_ids:
            if pid not in selected:
                selected.append(pid)
        await interaction.response.edit_message(
            embed=category_embed(self.parent_view.setup, self.category_key, self.parent_view.catalog, self.pack_ids),
            view=PackCategoryView(self.parent_view, self.category_key, self.pack_ids),
        )

    @discord.ui.button(label="Clear Category", emoji="❌", style=discord.ButtonStyle.secondary, row=1)
    async def clear(self, interaction, _):
        blocked = set(self.pack_ids)
        self.parent_view.setup.session.selected_pack_ids = [pid for pid in self.parent_view.setup.session.selected_pack_ids if pid not in blocked]
        await interaction.response.edit_message(
            embed=category_embed(self.parent_view.setup, self.category_key, self.parent_view.catalog, self.pack_ids),
            view=PackCategoryView(self.parent_view, self.category_key, self.pack_ids),
        )

    @discord.ui.button(label="Categories", emoji="⬅️", style=discord.ButtonStyle.primary, row=1)
    async def back(self, interaction, _):
        await interaction.response.edit_message(
            embed=pack_picker_embed(self.parent_view.setup, self.parent_view.catalog),
            view=PackCategoryHubView(self.parent_view.setup, self.parent_view.catalog, self.parent_view.custom_ids),
        )


class PackCategoryHubView(discord.ui.View):
    def __init__(self, setup: "GameSetupView", catalog: dict[str, tuple[str, str]], custom_ids: list[str]):
        super().__init__(timeout=600)
        self.setup = setup
        self.catalog = catalog
        self.custom_ids = custom_ids
        buttons = [
            ("core", "Core Chaos", "🌪️"),
            ("disaster", "Life Is a Disaster", "💀"),
            ("fandom", "Fandom / Events", "🎭"),
            ("server", "Server Lore", "🍑"),
        ]
        for idx, (key, label, emoji) in enumerate(buttons):
            self.add_item(PackCategoryButton(self, key, label, emoji, 0 if idx < 4 else 1))
        if custom_ids:
            self.add_item(PackCategoryButton(self, "custom", "Custom Packs", "🧠", 1))

    def category_ids(self, key: str) -> list[str]:
        if key == "custom":
            return [pid for pid in self.custom_ids if pid in self.catalog]
        raw = PACK_CATEGORIES.get(key, ("", "", []))[2]
        return [pid for pid in raw if pid in self.catalog]

    async def interaction_check(self, interaction):
        if interaction.user.id != self.setup.session.host_id:
            await interaction.response.send_message("Only the host can change game setup.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Select All Built-In", emoji="✅", style=discord.ButtonStyle.success, row=2)
    async def all_builtin(self, interaction, _):
        # Acknowledge immediately so Discord never reports a timeout while the
        # picker is being rebuilt.
        await interaction.response.defer()
        selected = self.setup.session.selected_pack_ids
        for pid in BUILTIN_PACKS:
            if pid not in selected:
                selected.append(pid)
        await interaction.edit_original_response(
            embed=pack_picker_embed(self.setup, self.catalog),
            view=PackCategoryHubView(self.setup, self.catalog, self.custom_ids),
        )

    @discord.ui.button(label="Clear All", emoji="❌", style=discord.ButtonStyle.secondary, row=2)
    async def clear_all(self, interaction, _):
        # Clear in-place and acknowledge before rebuilding the view. This avoids
        # the intermittent "didn't respond in time" failure on ephemeral UI.
        await interaction.response.defer()
        self.setup.session.selected_pack_ids.clear()
        await interaction.edit_original_response(
            embed=pack_picker_embed(self.setup, self.catalog),
            view=PackCategoryHubView(self.setup, self.catalog, self.custom_ids),
        )

    @discord.ui.button(label="Done", emoji="✅", style=discord.ButtonStyle.primary, row=2)
    async def done(self, interaction, _):
        await interaction.response.edit_message(embed=self.setup.embed(), view=self.setup)


class BlankChanceModal(discord.ui.Modal, title="Write-in settings"):
    starting = discord.ui.TextInput(
        label="Starting write-ins per hand (0 = off)",
        placeholder="0",
        required=True,
        max_length=2,
    )
    chance = discord.ui.TextInput(
        label="Replacement write-in chance (%)",
        placeholder="15",
        required=True,
        max_length=2,
    )

    def __init__(self, setup_view):
        super().__init__()
        self.setup_view = setup_view
        self.starting.default = str(setup_view.session.rules.starting_blank_count)
        self.chance.default = str(setup_view.session.rules.blank_chance_percent)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            starting = int(str(self.starting.value).strip())
            chance = int(str(self.chance.value).strip())
        except ValueError:
            await interaction.response.send_message("Use whole numbers for both write-in settings.", ephemeral=True)
            return

        hand_size = int(self.setup_view.session.rules.hand_size)
        max_starting = min(hand_size, MAX_WRITE_IN_BLANKS_PER_HAND)
        if starting < 0 or starting > max_starting:
            await interaction.response.send_message(
                f"Starting write-ins must be between 0 and {max_starting}.",
                ephemeral=True,
            )
            return
        if chance < 0 or chance > 50:
            await interaction.response.send_message(
                "Choose a replacement write-in chance between 0% and 50%.",
                ephemeral=True,
            )
            return

        self.setup_view.session.rules.starting_blank_count = starting
        self.setup_view.session.rules.blank_chance_percent = chance
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
        embed = discord.Embed(title=f"🃏 {GAME_NAME}", description="Set up the table, choose your packs, then open the lobby.")
        embed.add_field(name="Packs", value=f"{len(self.session.selected_pack_ids)} selected", inline=True)
        embed.add_field(name="Czar", value=czar, inline=True)
        embed.add_field(name="AI style", value=r.ai_style.replace('_',' ').title(), inline=True)
        starting_blanks = "Off" if r.starting_blank_count <= 0 else str(r.starting_blank_count)
        embed.add_field(
            name="Hand",
            value=f"{r.hand_size} cards • Start blanks: {starting_blanks} • {r.blank_chance_percent}% replacement chance",
            inline=False,
        )
        embed.add_field(name="Win", value=f"First to {r.score_to_win}", inline=True)
        embed.add_field(name="Mommy", value="✅ At the table" if r.mommy_player else "❌ Off", inline=True)
        embed.add_field(name="Daddy", value="✅ At the table" if r.daddy_player else "❌ Off", inline=True)
        embed.set_footer(text=f"Custom packs need at least {MIN_BLACK} black + {MIN_WHITE} white cards to be playable.")
        return embed

    async def refresh(self, interaction): await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Packs", emoji="🗃️", style=discord.ButtonStyle.primary)
    async def packs(self, interaction, _):
        docs = await self.session.cog.store.list_for_host(self.session.guild.id, self.session.host_id)
        catalog: dict[str, tuple[str, str]] = {
            pid: (str(pack.get("name", "Pack")), str(pack.get("description", "")))
            for pid, pack in BUILTIN_PACKS.items()
        }
        custom_ids: list[str] = []
        for d in docs:
            if len(d.get("black_cards", [])) >= MIN_BLACK and len(d.get("white_cards", [])) >= MIN_WHITE:
                pid = str(d["_id"])
                custom_ids.append(pid)
                catalog[pid] = (str(d.get("name", "Custom Pack")), "Custom pack")
        await interaction.response.edit_message(
            embed=pack_picker_embed(self, catalog),
            view=PackCategoryHubView(self, catalog, custom_ids),
        )

    @discord.ui.button(label="Czar", emoji="👑", style=discord.ButtonStyle.secondary)
    async def czar(self, interaction, _):
        modes = [x[0] for x in self.CZAR]; i = modes.index(self.session.rules.czar_mode); self.session.rules.czar_mode = modes[(i+1)%len(modes)]; await self.refresh(interaction)

    @discord.ui.button(label="AI Style", emoji="🤖", style=discord.ButtonStyle.secondary)
    async def humor(self, interaction, _):
        s=self.session.rules; i=self.STYLES.index(s.ai_style); s.ai_style=self.STYLES[(i+1)%len(self.STYLES)]; await self.refresh(interaction)

    @discord.ui.button(label="Hand", emoji="🖐️", style=discord.ButtonStyle.secondary)
    async def hand(self, interaction, _):
        s = self.session.rules
        s.hand_size = 7 if s.hand_size >= 10 else s.hand_size + 1
        await self.refresh(interaction)

    @discord.ui.button(label="Write-ins", emoji="✍️", style=discord.ButtonStyle.secondary)
    async def blanks(self, interaction, _):
        await interaction.response.send_modal(BlankChanceModal(self))

    @discord.ui.button(label="Score", emoji="🏆", style=discord.ButtonStyle.secondary)
    async def score(self, interaction, _):
        vals=[3,5,7,10]; s=self.session.rules; s.score_to_win=vals[(vals.index(s.score_to_win)+1)%len(vals)] if s.score_to_win in vals else 5; await self.refresh(interaction)

    @discord.ui.button(label="Mommy", emoji="🤖", style=discord.ButtonStyle.secondary)
    async def mommy_player(self, interaction, _):
        self.session.rules.mommy_player = not self.session.rules.mommy_player
        self.session.sync_ai_players()
        await self.refresh(interaction)

    @discord.ui.button(label="Daddy", emoji="🥋", style=discord.ButtonStyle.secondary)
    async def daddy_player(self, interaction, _):
        self.session.rules.daddy_player = not self.session.rules.daddy_player
        self.session.sync_ai_players()
        await self.refresh(interaction)

    @discord.ui.button(label="Open Lobby", emoji="🚪", style=discord.ButtonStyle.success)
    async def lobby(self, interaction, _):
        if not self.session.selected_pack_ids:
            await interaction.response.send_message("Choose at least one pack first.", ephemeral=True); return
        self.session.status="lobby"
        await interaction.response.send_message("Lobby opened in this channel.", ephemeral=True)
        self.session.message = await interaction.channel.send(embed=lobby_embed(self.session), view=LobbyView(self.session))



def lobby_embed(session: CAHSession) -> discord.Embed:
    embed=discord.Embed(title=f"🃏 {GAME_NAME} • Lobby", description="Grab a seat. The host starts when everyone is ready.")
    embed.add_field(name="Host", value=f"<@{session.host_id}>", inline=True)
    session.sync_ai_players()
    embed.add_field(name="Players", value=str(len(session.participant_ids())), inline=True)
    embed.add_field(name="Packs", value=str(len(session.selected_pack_ids)), inline=True)
    embed.add_field(name="At the table", value="\n".join(f"• {session.display_player(u)}" for u in session.participant_ids()) or "Nobody yet", inline=False)
    mode=dict(GameSetupView.CZAR).get(session.rules.czar_mode,session.rules.czar_mode)
    start_blanks = "off" if session.rules.starting_blank_count <= 0 else str(session.rules.starting_blank_count)
    embed.set_footer(text=f"{mode} • start blanks {start_blanks} • {session.rules.blank_chance_percent}% replacement blank chance • used cards 25% recycle • first to {session.rules.score_to_win}")
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
            if not interaction.response.is_done():
                await interaction.response.send_message("You already picked that card.", ephemeral=True)
            return

        self.selected_indexes.append(index)
        self.selected_cards.append(text)
        need=max(1,int((self.session.current_black or {}).get("pick",1)))

        # Make a picked white card visibly disappear from the selectable hand.
        # We disable/remove its button in this ephemeral view immediately rather
        # than leaving a stale copy on screen after the underlying hand changes.
        for child in list(self.children):
            if isinstance(child, HandCardButton) and child.hand_index == index:
                self.remove_item(child)
                break

        if len(self.selected_cards)>=need:
            ok,msg=await self.session.submit(self.user_id,self.selected_cards[:need],self.selected_indexes[:need])
            if interaction.response.is_done():
                return
            if ok:
                await interaction.response.edit_message(
                    embeds=[discord.Embed(title="✅ Card submitted", description="Your played card has left your hand. Your replacement will be waiting next round.")],
                    view=None,
                )
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        else:
            remaining=need-len(self.selected_cards)
            if not interaction.response.is_done():
                await interaction.response.edit_message(
                    view=self,
                )
            try:
                await interaction.followup.send(
                    f"Selected **{_short(text,100)}**. Pick {remaining} more.",
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass


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
        prompt = _display_prompt(str(black.get("text") or ""), need)

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
        embed=discord.Embed(title="🗃️ Packs",description="Create and edit your own packs.")
        await interaction.response.send_message(embed=embed,view=ManagePacksView(self.cog,interaction.user.id),ephemeral=True)

    @discord.ui.button(label="How It Works",emoji="❓",style=discord.ButtonStyle.secondary)
    async def help(self,interaction,_):
        embed=discord.Embed(title=f"How {GAME_NAME} Works",description="Join a lobby, play the funniest answer, and let the Czar pick the winner. Write-ins let you make your own answer for the round.")
        embed.add_field(name="Czar modes",value="Rotate between players, use AI for small lobbies, or let AI handle judging.",inline=False)
        embed.add_field(name="Packs",value="Use built-in packs, personal packs, server packs, or mix several together.",inline=False)
        await interaction.response.send_message(embed=embed,ephemeral=True)


class MemberHubView(discord.ui.View):
    """Persistent public panel. Members use buttons; no member slash-command menu required."""
    def __init__(self,bot):
        super().__init__(timeout=None); self.bot=bot

    @discord.ui.button(label="Femboy Hooters Against Humanity",emoji="🃏",style=discord.ButtonStyle.primary,custom_id="mommy:member:cah",row=0)
    async def cah(self,interaction,_):
        cog=self.bot.get_cog("MemberGamesCog")
        if cog is None:
            await interaction.response.send_message("The game table is unavailable right now.",ephemeral=True); return
        embed=discord.Embed(title=f"🃏 {GAME_NAME}",description="Start a lobby, mix your packs, and let the bad decisions begin.")
        await interaction.response.send_message(embed=embed,view=CAHHomeView(cog,interaction.user.id),ephemeral=True)

    @discord.ui.button(label="Mafia",emoji="🎭",style=discord.ButtonStyle.secondary,custom_id="mommy:member:mafia",row=0)
    async def mafia(self,interaction,_):
        from mommy.mafia_game import open_mafia_home
        await open_mafia_home(interaction,self.bot)


def member_hub_embed(guild: discord.Guild) -> discord.Embed:
    embed=discord.Embed(title="🎮 Games",description="Pick a game to start.")
    embed.add_field(
        name=f"🃏 {GAME_NAME}",
        value="Mix packs • write-ins • Mommy & Daddy • human or AI Czar",
        inline=True,
    )
    embed.add_field(
        name="🎭 Mafia",
        value="Hidden roles • night actions • voting • optional filler bots",
        inline=True,
    )
    embed.set_footer(text=guild.name)
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

    async def ai_player_submit(self, session: CAHSession, player_id: int) -> None:
        player = session.players.get(player_id)
        if not player or player_id not in session.eligible_submitters() or player_id in session.submissions:
            return

        need = max(1, int((session.current_black or {}).get("pick", 1)))
        if not player.hand:
            session.fill_hand(player)

        persona = "Mommy.exe" if player_id == MOMMY_PLAYER_ID else "Daddy’s Belt"
        indexes = await self.ai_play_indexes(
            session.current_black or {},
            player.hand,
            need,
            session.rules.ai_style,
            persona=persona,
        )
        indexes = [i for i in indexes if 0 <= i < len(player.hand)][:need]
        if len(indexes) < need:
            extras = [i for i in range(len(player.hand)) if i not in indexes]
            random.shuffle(extras)
            indexes.extend(extras[: need - len(indexes)])

        cards: list[str] = []
        wildcard_used = False
        wildcard_chance = max(0.0, min(1.0, float(session.rules.ai_wildcard_chance)))
        wildcard_slot = random.randrange(need) if need and random.random() < wildcard_chance else -1

        for slot, idx in enumerate(indexes):
            card = player.hand[idx]
            if slot == wildcard_slot:
                card = await self.ai_write_in(
                    session.current_black or {},
                    session.rules.ai_style,
                    persona=persona,
                )
                wildcard_used = True
            elif card == BLANK_TOKEN:
                card = await self.ai_write_in(
                    session.current_black or {},
                    session.rules.ai_style,
                    persona=persona,
                )
            cards.append(card)

        await session.submit(player_id, cards, indexes)

        if wildcard_used:
            log.info("%s used a one-off AI wild white card in guild %s", persona, session.guild.id)

    async def mommy_submit(self, session: CAHSession) -> None:
        # Compatibility wrapper for older call sites.
        await self.ai_player_submit(session, MOMMY_PLAYER_ID)

    async def ai_play_indexes(self, black: dict[str, Any], hand: list[str], need: int, style: str, *, persona: str = "Mommy.exe") -> list[int]:
        regular = [(i, c) for i, c in enumerate(hand) if c != BLANK_TOKEN]
        if self.ai is None:
            pool = [i for i, _ in regular] or list(range(len(hand)))
            random.shuffle(pool)
            return pool[:need]
        lines = [f"{i}: {'[WRITE-IN BLANK]' if c == BLANK_TOKEN else c}" for i, c in enumerate(hand)]
        prompt = f"Prompt: {black.get('text','')}\nPick {need} card slot(s) from this hand to make the funniest answer.\n" + "\n".join(lines) + f"\nHumor style: {style}. Return JSON exactly like {{\"indexes\":[1]}} or {{\"indexes\":[1,4]}}."
        try:
            response = await self.ai.responses.create(model=self.ai_model, instructions=f"You are {persona} playing a party card game. Choose the funniest card combination from your hand. Return only the requested JSON.", input=prompt, max_output_tokens=80)
            raw=(response.output_text or "").strip(); match=re.search(r"\{.*\}",raw,re.S); data=json.loads(match.group(0) if match else raw)
            vals=[int(x) for x in data.get("indexes",[]) if isinstance(x,(int,float,str))]
            return vals[:need]
        except Exception:
            log.exception("Mommy player card selection failed")
            pool=[i for i,_ in regular] or list(range(len(hand))); random.shuffle(pool); return pool[:need]

    async def ai_write_in(self, black: dict[str, Any], style: str, *, persona: str = "Mommy.exe") -> str:
        if self.ai is None:
            return "an aggressively confident bad decision"
        try:
            response = await self.ai.responses.create(model=self.ai_model, instructions=f"You are {persona}. Write one short funny Cards-Against-Humanity-style answer for a blank white card. Do not explain it.", input=f"Prompt: {black.get('text','')}\nHumor style: {style}.", max_output_tokens=50)
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

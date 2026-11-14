"""Keep generated scripts from all sounding the same.

Every script gets a randomly picked "angle": how it opens, how it is built and
how it ends. On top of that, an optional channel voice (CHANNEL_VOICE in .env)
describes how one particular channel sounds, so two people making videos on the
same topic still end up with different scripts.
"""

from __future__ import annotations

import os
import random
import re
from dataclasses import dataclass
from typing import Optional

HOOKS = [
    "Cold open in the middle of a moment: drop the viewer into a specific scene, then explain.",
    "Start with the strangest true detail about the topic, stated plainly.",
    "Name a belief most viewers hold about the topic, then say why it is wrong.",
    "Ask the viewer one sharp, specific question they cannot answer yet.",
    "Open with a concrete number and immediately make it feel big or small with a comparison.",
    "Start with a tiny story about one real person, place or object connected to the topic.",
    "Open with a confession in first person, as if admitting something slightly embarrassing.",
    "Start with a prediction or a 'by the end of this you will...' promise that the script keeps.",
    "Open by comparing two things nobody would normally compare.",
    "Start with a blunt, one-line opinion, then back it up.",
    "Open with what the viewer would see, hear or feel if they were there.",
    "Start with the answer, then rewind to how we got there.",
]

STRUCTURES = [
    "A countdown of three to five points, saving the most surprising one for last.",
    "Myth, then truth, then why the myth exists.",
    "Problem, twist, payoff: set up a puzzle, complicate it, then resolve it.",
    "Before and after: how things were, what changed, how they are now.",
    "One continuous story arc with a clear beginning, middle and end.",
    "Go three layers deeper: an obvious explanation, a better one, and the real one.",
    "A chain of questions, where each answer raises the next question.",
    "Side-by-side comparison that ends with a verdict.",
]

# Casual, thinking-out-loud openings for the 'creative' style.
CREATIVE_HOOKS = [
    "State the odd premise as a flat fact, then immediately question it.",
    "Start from an everyday object or moment that made you think of the idea.",
    "Open with the most absurd consequence of the idea, then work backwards.",
    "Start with the one concrete detail that makes the idea feel real, as a full sentence.",
    "Open with a tiny, specific scene from daily life that sets up the question.",
    "Start with a one-word or two-word sentence, then explain why it matters.",
]

ENDINGS = [
    "Call back to the opening line, so the last sentence reframes it.",
    "End on one surprising final fact that changes how the viewer sees the topic.",
    "End with a single-sentence takeaway the viewer could repeat to a friend.",
    "End with an open question that makes the viewer want to comment.",
    "End with a practical thing the viewer can try or notice today.",
    "End on a quiet, understated line instead of a big finish.",
]

# Phrases that make AI-written narration instantly recognisable.
BANNED_PHRASES = [
    "okay but like", "okay so", "did you know", "in today's video", "let's dive in", "dive into", "buckle up",
    "mind-blowing", "mind blowing", "game-changer", "game changer", "stay tuned",
    "imagine a world", "ever wondered", "have you ever wondered", "here's the thing",
    "you won't believe", "let's explore", "the fascinating world", "embark on",
    "journey", "unlock the secrets", "delve", "tapestry", "in conclusion",
    "smash that like", "like and subscribe", "without further ado",
]

# Openers that must never start a script (checked after generation).
BANNED_OPENERS = re.compile(
    r"^\W*(okay,? (but|so)|ok,? (but|so)|so,? like|did you know|have you ever|ever wondered|in today'?s video|imagine a world|"
    r"let'?s (dive|explore|talk)|welcome (back|to)|hey (guys|everyone))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Angle:
    hook: Optional[str]
    structure: str
    ending: str

    def describe(self) -> str:
        return f"hook={self.hook!r}, structure={self.structure!r}, ending={self.ending!r}"


def pick_angle(style: str = "viral", rng: Optional[random.Random] = None) -> Angle:
    """Pick a random hook/structure/ending combination for one script.

    Every style gets a random opening, shape and ending; the style itself only
    sets voice and tone.
    """
    rng = rng or random.SystemRandom()
    # Creative gets its casual openings mixed with the general pool, so it
    # keeps its tone without a recognisable fixed start.
    pool = (CREATIVE_HOOKS + HOOKS) if style == "creative" else HOOKS
    hook = rng.choice(pool)
    return Angle(hook=hook, structure=rng.choice(STRUCTURES), ending=rng.choice(ENDINGS))


def channel_voice() -> str:
    return (os.getenv("CHANNEL_VOICE") or "").strip()


def variety_block(angle: Angle, voice: Optional[str] = None) -> str:
    """Prompt section that makes this script's shape different from the last one."""
    voice = channel_voice() if voice is None else voice.strip()
    lines = ["THIS VIDEO'S ANGLE (decides how this script opens, flows and ends):"]
    if angle.hook:
        lines.append(f"- Opening: {angle.hook}")
    lines.append(f"- Shape: {angle.structure}")
    lines.append(f"- Ending: {angle.ending}")
    lines.append(
        "- Never use these phrases or anything like them: "
        + ", ".join(f'"{p}"' for p in BANNED_PHRASES[:16]) + "."
    )
    lines.append("- No calls to like, subscribe or comment unless the ending above asks for a question.")
    lines.append("- The first sentence is complete and stands alone: no leading dots, \"and\", \"so\" or filler word.")
    if voice:
        lines.append("")
        lines.append("CHANNEL VOICE (keep this consistent; it is what makes this channel recognisable):")
        lines.append(voice)
    return "\n".join(lines)


LEADING_FILLER = re.compile(r"^\s*(\.\.\.|…|and\b|so\b|but\b|like\b)", re.IGNORECASE)


def has_banned_opener(text: str) -> bool:
    return bool(BANNED_OPENERS.match(text or "") or LEADING_FILLER.match(text or ""))


def count_banned_phrases(text: str) -> int:
    lower = (text or "").lower()
    return sum(lower.count(p) for p in BANNED_PHRASES)

"""Scene planning using LLM (Ollama or OpenRouter) to generate structured video scenes."""

import json
import re
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator
import ollama


class Scene(BaseModel):
    """Represents a single scene in the video with multiple clip support."""

    narration: str = Field(..., min_length=8, max_length=500)  # Allow shorter narrations for fast pacing
    keywords: List[str] = Field(..., min_length=2, max_length=5)
    target_seconds: float = Field(..., ge=2.0, lt=15.0)  # Allow 2-15s scenes (fast pacing)
    visual_description: str = Field(..., min_length=10, max_length=300)

    # NEW: Multiple clips per scene support
    num_clips: int = Field(default=1, ge=1, le=4, description="Number of video clips for this scene (1-4)")
    clip_keywords: List[List[str]] = Field(
        default_factory=list,
        description="Keywords for each clip within the scene (optional)"
    )

    @field_validator('keywords')
    @classmethod
    def validate_keywords(cls, v):
        """Ensure keywords are lowercase and non-empty."""
        if not all(isinstance(k, str) and len(k) > 0 for k in v):
            raise ValueError("All keywords must be non-empty strings")
        return [k.lower().strip() for k in v]

    @field_validator('clip_keywords')
    @classmethod
    def validate_clip_keywords(cls, v, info):
        """Ensure clip_keywords matches num_clips if provided."""
        # If clip_keywords is empty, it will be auto-generated later
        if not v:
            return v

        # Get num_clips from the model data
        num_clips = info.data.get('num_clips', 1)

        if len(v) != num_clips:
            raise ValueError(f"clip_keywords length {len(v)} != num_clips {num_clips}")

        # Validate each clip's keywords
        for clip_kw in v:
            if not all(isinstance(k, str) and len(k) > 0 for k in clip_kw):
                raise ValueError("All clip keywords must be non-empty strings")

        return [[k.lower().strip() for k in clip_kw] for clip_kw in v]

    @field_validator('narration', 'visual_description')
    @classmethod
    def validate_text(cls, v):
        """Ensure text fields are properly stripped."""
        return v.strip()


class ScenePlan(BaseModel):
    """Collection of scenes forming a complete video plan."""

    scenes: List[Scene] = Field(..., min_length=3, max_length=40)  # Allow up to 40 scenes for fast pacing
    total_duration: float = Field(..., gt=0)

    @field_validator('scenes')
    @classmethod
    def validate_scene_count(cls, v, info):
        """Validate scene count is appropriate for duration."""
        if len(v) < 3:
            raise ValueError("Must have at least 3 scenes")
        if len(v) > 40:  # Allow up to 40 scenes for long videos
            raise ValueError("Must have at most 40 scenes")
        return v


class VisualClip(BaseModel):
    """A single visual segment (~2-3 seconds) for continuous narration mode."""

    keywords: List[str] = Field(..., min_length=2, max_length=5)
    visual_description: str = Field(..., min_length=5, max_length=200)

    @field_validator('keywords')
    @classmethod
    def validate_keywords(cls, v):
        if not all(isinstance(k, str) and len(k) > 0 for k in v):
            raise ValueError("All keywords must be non-empty strings")
        return [k.lower().strip() for k in v]


class VideoPlan(BaseModel):
    """Complete video plan with decoupled narration and visuals."""

    narration: str = Field(..., min_length=20)
    clips: List[VisualClip] = Field(..., min_length=3, max_length=80)


class ScenePlanner:
    """Generates structured scene plans using LLM."""

    # Style-specific tone/voice instructions injected into the system prompt
    NARRATION_STYLES = {
        'viral': {
            'voice': (
                'You are an elite YouTube shorts scriptwriter. '
                'You write hook-driven micro-stories that feel true, specific, and addictive.'
            ),
            'tone': (
                'Like telling a fascinating story to a friend: confident, playful, punchy. '
                'No lecturing. No “in this video” fluff.'
            ),
            'rules': (
                '- OPEN HARD (first 1–2 lines): a shocking fact, bold claim, or weird question. No warm-up.\n'
                '- STRUCTURE: Hook → Setup (context fast) → Twist (unexpected) → Reveal (the “aha”) → Payoff (clean final line).\n'
                '- PACING: Every 1–2 sentences must add NEW information. No stalling, no rephrasing.\n'
                '- RHYTHM: Mix ultra-short punches (3–6 words) with longer sentences. Vary sentence starts.\n'
                '- CURIOSITY BRIDGES (use sparingly, 1–3 max): “But here’s the weird part…”, “Now it gets wilder…”, “Here’s the catch…”\n'
                '- CONVERSATIONAL: Use contractions. Speak to “you”. Sound like a human, not a narrator.\n'
                '- SPECIFICITY > HYPE: Prefer concrete details, comparisons, and examples over vague words like “crazy/insane.”\n'
                '- VISUAL WRITING: Describe what you’d *see* in simple, vivid terms (actions, contrasts, tiny details).\n'
                '- KEEP IT TIGHT: Remove filler phrases (“imagine this”, “let’s talk about”). No disclaimers.\n'
                '- END STRONG: A satisfying callback, perspective shift, or crisp thought-provoking line. No generic “follow for more.”'
            ),
        },

        'informative': {
            'voice': 'an expert educator who explains complex topics clearly and engagingly',
            'tone': 'a knowledgeable teacher making a topic accessible — clear, structured, and insightful',
            'rules': (
                '- Open with a clear, intriguing statement of what the viewer will learn\n'
                '- Present facts and explanations in a logical order, building understanding step by step\n'
                '- Use analogies and relatable comparisons to make complex ideas simple\n'
                '- Include specific numbers, dates, or data points to add credibility\n'
                '- End with a key takeaway or surprising implication the viewer can remember'
            ),
        },
        'funny': {
            'voice': 'a stand-up comedian who makes every topic hilarious with sharp wit and timing',
            'tone': 'a funny friend roasting the topic — sarcastic, exaggerated, absurd, but still accurate',
            'rules': (
                '- Open with a joke, absurd observation, or exaggerated take on the topic\n'
                '- Use comedic timing: set up expectations, then subvert them with a punchline\n'
                '- Exaggerate for effect but keep the core facts accurate\n'
                '- Include self-aware humor, rhetorical questions, and sarcastic commentary\n'
                '- End with a big punchline or a funny callback to the opening joke'
            ),
        },
        'storytelling': {
            'voice': 'a master storyteller who turns any topic into a captivating narrative',
            'tone': 'someone telling a campfire story — vivid, emotional, with characters and drama',
            'rules': (
                '- Open by setting a scene or introducing a character the viewer can relate to\n'
                '- Build narrative tension — present a problem, conflict, or mystery\n'
                '- Use vivid sensory details: what things look, sound, feel like\n'
                '- Include emotional beats — moments of wonder, surprise, or reflection\n'
                '- End with a resolution that ties the story together meaningfully'
            ),
        },
        'motivational': {
            'voice': 'an inspiring speaker who makes viewers feel empowered and driven to act',
            'tone': 'a coach giving a pep talk — energetic, direct, and uplifting',
            'rules': (
                '- Open with a bold, empowering statement or challenge to the viewer\n'
                '- Acknowledge the struggle or doubt the viewer might feel\n'
                '- Use "you" frequently — make it personal and direct\n'
                '- Build momentum with increasingly powerful statements\n'
                '- End with a clear call to action or an unforgettable closing line'
            ),
        },
        'creative': {
            'voice': (
                'a chill, curious overthinker who goes down rabbit holes and somehow makes them make sense. '
                'You talk like someone having a late-night epiphany — not a scientist, not a debater.'
            ),
            'tone': (
                'Like a friend on the couch at 2 AM who just had a wild thought and is NOW explaining it to you. '
                'Relaxed, genuine, mind-wandering but always landing on something that clicks. '
                'Think shower thoughts that accidentally become philosophy.'
            ),
            'rules': (
                '- OPEN WITH THE QUESTION: State the premise casually so the viewer knows what we\'re talking about.\n'
                '  Example: "Okay but like… is a duck seafood? Think about it for a second."\n'
                '- NEVER correct, debunk, or "well actually" the premise. You genuinely believe it. Run with it.\n'
                '- THINK OUT LOUD: Let the narration feel like a train of thought, not a prepared speech.\n'
                '  Use natural transitions like "and like…", "okay but then…", "which means…", "right?"\n'
                '- USE SIMPLE, EVERYDAY COMPARISONS — not scientific terminology.\n'
                '  Good: "A duck literally lives in water. A shrimp lives in water. We eat both. What\'s the difference?"\n'
                '  Bad: "Taxonomically speaking, the Anatidae family shares aquatic habitats with crustaceans."\n'
                '- ESCALATE THE RABBIT HOLE: Start with the obvious thought, then keep pulling the thread further.\n'
                '  Each point should feel like "wait… and ALSO…" — building momentum.\n'
                '- KEEP IT REAL: Every argument must be based on true, checkable facts. Don\'t make stuff up.\n'
                '  But present facts casually, not like a textbook.\n'
                '- NO jargon, no academic language, no "furthermore" or "moreover" or "it\'s worth noting".\n'
                '  Write how people actually talk.\n'
                '- END with the thought that hits hardest — the one that makes the viewer go "…huh. okay yeah maybe."\n'
                '  Or leave it as an open question that sticks in their head.'
            ),
        },
    }

    @classmethod
    def _build_continuous_prompt(cls, style: str = 'viral') -> str:
        """Build the system prompt for continuous narration generation.

        Args:
            style: Narration style key from NARRATION_STYLES

        Returns:
            Full system prompt string
        """
        s = cls.NARRATION_STYLES.get(style, cls.NARRATION_STYLES['viral'])

        # Creative style needs a different relationship to the user's prompt:
        # treat it as a thesis to defend, not a question to answer.
        if style == 'creative':
            prompt_rule = (
                "MOST IMPORTANT RULE — RUN WITH THE USER'S IDEA:\n"
                "- The user's prompt is your shower thought. You're vibing with it, not fact-checking it.\n"
                "- Do NOT answer, correct, or debunk the premise. You genuinely think it might be true.\n"
                "- If the user asks 'Is X a Y?' — your reaction is 'honestly… kinda yeah?' and then you explain why.\n"
                "- Build the case using everyday logic, common sense comparisons, and real-world examples.\n"
                "- Stay on the user's specific topic — don't wander off to unrelated stuff.\n"
                "- If the user mentions specific angles or arguments, riff on those and go deeper."
            )
        else:
            prompt_rule = (
                "MOST IMPORTANT RULE — FOLLOW THE USER'S PROMPT:\n"
                "- The user's prompt is your BLUEPRINT. Read it carefully.\n"
                "- If the user asks a specific question, your narration MUST answer that exact question.\n"
                "- If the user lists specific angles, points, or sub-topics, you MUST cover ALL of them in order.\n"
                "- Do NOT go off on tangents or cover generic background that the user didn't ask for.\n"
                "- Every sentence must serve the user's stated goal — if a sentence doesn't relate to what they asked, DELETE IT.\n"
                "- If the user provides a specific argument or thesis, BUILD on it — don't just restate the obvious."
            )

        return f"""You are {s['voice']}. Generate a CONTINUOUS narration script with a separate visual clip list.

The narration should sound like {s['tone']}.

Output ONLY valid JSON in this exact format:
{{
  "narration": "Your full continuous narration script here.",
  "clips": [
    {{"keywords": ["keyword1", "keyword2", "keyword3"], "visual_description": "What the viewer sees during this 3-second segment"}},
    {{"keywords": ["keyword1", "keyword2", "keyword3"], "visual_description": "Next visual segment"}}
  ]
}}

{prompt_rule}

NARRATION RULES:
{s['rules']}
- Target ~3 words per second (60-second video = ~180 words)
- Do NOT use filler phrases or overly formal language
- NEVER repeat a sentence or idea you already stated — every sentence must add NEW information or a NEW angle
- Vary your sentence structure — do NOT start multiple sentences the same way
- Every sentence must be grammatically complete and make logical sense
- Do NOT write sentence fragments or trailing thoughts that go nowhere

CLIPS RULES:
- Each clip represents ~3 seconds of video footage
- Clips are PURELY VISUAL - they show relevant imagery while narration plays
- The number of clips should match the video duration (e.g., 60s video = ~20 clips)
- Clip keywords must be CONCRETE, PHYSICAL NOUNS that you can film
- Include the main topic noun in most clips
- Clip visual variety: alternate between wide shots, close-ups, different angles
- Clips do NOT need to match narration word-for-word, just stay on topic

KEYWORD REQUIREMENTS (CRITICAL):
1. Keywords MUST be concrete, physical NOUNS
2. Test each keyword: "Can I film a video of just this keyword?" If no, it's BAD
3. NEVER use abstract concepts: "nutrients", "process", "energy", "system"
4. NEVER use scientific terms: "enzymes", "photosynthesis", "molecules"
5. Use words a photographer would use to label footage

EXAMPLE for "How bees make honey" (30 seconds):

{{
  "narration": "Did you know a single bee only makes one twelfth of a teaspoon of honey in its entire life? One twelfth. That tiny drop takes visiting thousands of flowers, one by one. They suck up the nectar, store it in a special honey stomach, and fly it all the way back to the hive. But here's where it gets wild. They actually pass the nectar mouth to mouth to other bees, who chew it up and spread it across the honeycomb. Then they just wait. The water slowly evaporates, and what's left is that thick golden honey you put on your toast. All that work, from thousands of bees, just so you can have one jar.",
  "clips": [
    {{"keywords": ["bee", "flower", "garden", "flying"], "visual_description": "Bee flying toward colorful flowers in a garden"}},
    {{"keywords": ["bee", "flower", "pollen", "closeup"], "visual_description": "Close-up of bee landing on a flower"}},
    {{"keywords": ["bee", "nectar", "tongue", "flower"], "visual_description": "Bee collecting nectar from flower petals"}},
    {{"keywords": ["bee", "flying", "sky", "wings"], "visual_description": "Bee in flight carrying pollen on its legs"}},
    {{"keywords": ["beehive", "bees", "entrance", "wooden"], "visual_description": "Bees entering and exiting a wooden beehive"}},
    {{"keywords": ["honeycomb", "bees", "wax", "hive"], "visual_description": "Bees working on honeycomb cells inside hive"}},
    {{"keywords": ["honeycomb", "honey", "golden", "dripping"], "visual_description": "Golden honey dripping from honeycomb"}},
    {{"keywords": ["honey", "jar", "spoon", "golden"], "visual_description": "Close-up of golden honey in a glass jar"}},
    {{"keywords": ["bee", "flower", "field", "meadow"], "visual_description": "Single bee among wildflowers in a meadow"}},
    {{"keywords": ["honey", "toast", "breakfast", "drizzle"], "visual_description": "Honey being drizzled on food"}}
  ]
}}

Do NOT include markdown code blocks, explanations, or any text outside the JSON."""

    SYSTEM_PROMPT = """You are a video script writer. Generate a structured scene plan for a fast-paced, engaging video with FULL NARRATION SCRIPTS.

CRITICAL: The "narration" field must contain the COMPLETE SCRIPT that will be spoken aloud, NOT just a topic description.

Output ONLY valid JSON in this exact format:
{
  "scenes": [
    {
      "narration": "Complete sentences that will be spoken aloud. For a 3-second scene, write 8-10 words of concise, punchy narration.",
      "keywords": ["keyword1", "keyword2", "keyword3"],
      "target_seconds": 3.0,
      "visual_description": "Detailed description of visuals needed (10-300 chars)"
    }
  ]
}

Requirements:
- Create 10-40 scenes depending on total duration (~3 seconds per scene for fast, TikTok-style pacing)
- Each scene narration: FULL SCRIPT with 8-25 words (concise and punchy!)
- Narration should be clear, direct, and match the target duration
- Target word count: ~3 words per second (3s scene = 9 words, 5s scene = 15 words)
- Each scene keywords: 3-5 CONCRETE, VISUAL NOUNS related to main topic
- Each scene duration: 3-6 seconds (fast pacing keeps viewers engaged)
- Visual descriptions should be concrete and specific

KEYWORD REQUIREMENTS (CRITICAL - FAILURE TO FOLLOW = BAD VIDEO QUALITY):
1. Keywords MUST be concrete, physical NOUNS that exist in real life
2. MANDATORY: Include the main topic noun in EVERY scene (e.g., "honey", "bee", "ocean", "plant")
3. Test each keyword: "Can I film a video of just this keyword?" If no, it's BAD
4. NEVER use abstract concepts, processes, or scientific terms as keywords
5. NEVER use adjectives, verbs, or adverbs as keywords

FORBIDDEN KEYWORDS (NEVER USE THESE):
- Abstract: "nutrients", "process", "energy", "health", "diet", "system", "cycle"
- Scientific: "enzymes", "photosynthesis", "molecules", "chemicals", "reactions"
- Verbs: "regurgitate", "condensing", "evaporating", "forming"
- Adjectives: "important", "essential", "vital", "natural"

REQUIRED KEYWORD PATTERN:
Every scene MUST follow this pattern:
- Keyword 1: Main topic noun (e.g., "bee", "honey", "ocean")
- Keyword 2-4: Related physical objects you can see/film
- Example: ["bee", "flower", "pollen", "wings"] ✓
- Example: ["ocean", "waves", "beach", "sunset"] ✓

NARRATION EXAMPLES (3-second scenes):

Bad narration (too short): "Introduction to photosynthesis."
Good narration (3s, ~9 words): "Plants convert sunlight into energy through photosynthesis using chlorophyll."

Good narration (4s, ~12 words): "Bees collect nectar from flowers and carry it back to their hive."

Good narration (5s, ~15 words): "The honey is stored in hexagonal wax cells that the bees build inside the hive."

KEYWORD EXAMPLES - Learn from these:

Topic: "How bees make honey"
WRONG: ["nutrients", "antioxidants", "diet"] → Gets walnut/vitamin videos!
CORRECT: ["honey", "jar", "honeycomb", "golden"] → Gets honey videos ✓

WRONG: ["regurgitate", "enzymes", "process"] → Gets medical/lab videos!
CORRECT: ["bee", "flower", "nectar", "pollen"] → Gets bee foraging ✓

WRONG: ["storage", "cells", "structure"] → Gets random storage videos!
CORRECT: ["honeycomb", "beeswax", "hive", "cells"] → Gets bee hive ✓

Topic: "Water cycle"
WRONG: ["evaporation", "condensation", "precipitation"] → Gets science diagrams!
CORRECT: ["ocean", "waves", "sun", "water"] → Gets ocean footage ✓

WRONG: ["atmosphere", "vapor", "cloud formation"] → Gets sky timelapses!
CORRECT: ["clouds", "sky", "storm", "rain"] → Gets weather footage ✓

Topic: "Photosynthesis"
WRONG: ["chlorophyll", "glucose", "carbon dioxide"] → Gets lab/science videos!
CORRECT: ["plant", "leaves", "sunlight", "green"] → Gets plant footage ✓

REMEMBER: Use words that a photographer would use to label their video!

MULTIPLE CLIPS PER SCENE (OPTIONAL - for scenes >8 seconds):
- For longer scenes (>8 seconds), you can specify num_clips and clip_keywords
- num_clips: Number of different clips to show (1-4)
- clip_keywords: Array of keyword arrays, one for each clip
- Each clip should show a different angle, moment, or aspect
- Example for 12-second scene about "bees collecting nectar":
  {
    "narration": "Bees fly from flower to flower, collecting nectar with their long tongues and storing it in special honey stomach pouches.",
    "keywords": ["bee", "flower", "nectar"],
    "target_seconds": 12.0,
    "visual_description": "Bees approaching, landing on, and flying away from colorful flowers",
    "num_clips": 3,
    "clip_keywords": [
      ["bee", "flying", "approach", "flower"],
      ["bee", "flower", "pollen", "closeup"],
      ["bee", "flying", "wings", "departure"]
    ]
  }

- If num_clips is omitted or 1, only use keywords field (single clip per scene)
- Clip progression should tell a mini-story or show variety:
  - Wide shot → Medium shot → Close-up
  - Action start → Action middle → Action end
  - Angle A → Angle B → Angle C

- Set num_clips based on scene duration:
  - 2-8 seconds: 1 clip (default)
  - 8-12 seconds: 2 clips
  - 12-20 seconds: 3 clips
  - 20+ seconds: 4 clips

Do NOT include markdown code blocks, explanations, or any text outside the JSON."""

    def __init__(self,
                 provider: Literal["ollama", "openrouter"] = "ollama",
                 model: str = "qwen2.5:3b",
                 base_url: str = "http://localhost:11434",
                 api_key: Optional[str] = None,
                 narration_style: str = "viral"):
        """Initialize scene planner.

        Args:
            provider: LLM provider to use ('ollama' or 'openrouter')
            model: Model name (Ollama model or OpenRouter model ID)
            base_url: API base URL (Ollama URL or OpenRouter URL)
            api_key: API key (required for OpenRouter, not needed for Ollama)
            narration_style: Script style ('viral', 'informative', 'funny', 'storytelling', 'motivational', 'creative')
        """
        self.provider = provider
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.narration_style = narration_style

        if provider == "openrouter" and not api_key:
            raise ValueError("OpenRouter requires an API key (OPENROUTER_API_KEY)")

    def _call_ollama(self, messages: list, temperature: float = 0.7) -> str:
        """Call Ollama API and return the response content.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature

        Returns:
            Response content string
        """
        response = ollama.chat(
            model=self.model,
            messages=messages,
            options={
                "temperature": temperature,
                "num_predict": 4000,
            }
        )
        return response['message']['content']

    def _call_openrouter(self, messages: list, temperature: float = 0.7) -> str:
        """Call OpenRouter API and return the response content.

        OpenRouter uses an OpenAI-compatible API format.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature

        Returns:
            Response content string
        """
        from openai import OpenAI

        client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
        )

        response = client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=4000,
            frequency_penalty=0.3,
            presence_penalty=0.2,
        )

        return response.choices[0].message.content

    def _call_llm(self, messages: list, temperature: float = 0.7) -> str:
        """Call the configured LLM provider.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature

        Returns:
            Response content string
        """
        if self.provider == "openrouter":
            return self._call_openrouter(messages, temperature)
        else:
            return self._call_ollama(messages, temperature)

    def generate(self, prompt: str, duration_minutes: float, max_retries: int = 3) -> List[Scene]:
        """Generate scene plan from prompt.

        Args:
            prompt: User's video topic/description
            duration_minutes: Target video duration in minutes
            max_retries: Maximum retry attempts for invalid responses

        Returns:
            List of Scene objects

        Raises:
            ValueError: If unable to generate valid plan after retries
        """
        total_seconds = duration_minutes * 60
        scene_count = self._estimate_scene_count(total_seconds)

        user_prompt = f"""Create a {duration_minutes}-minute video about: {prompt}

Target: {scene_count} scenes, {total_seconds:.0f} seconds total
Make sure scene durations sum to approximately {total_seconds:.0f} seconds.

Output ONLY the JSON, no other text."""

        print(f"  Using LLM: {self.provider} / {self.model}")

        for attempt in range(max_retries):
            try:
                messages = [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ]

                content = self._call_llm(messages)

                # Debug: Check if response is empty
                if not content or len(content.strip()) == 0:
                    print(f"\n  WARNING: Empty response from {self.provider} (attempt {attempt + 1}/{max_retries})")
                    if attempt < max_retries - 1:
                        continue
                    else:
                        raise ValueError(f"{self.provider} returned empty response after all retries")

                # Parse and validate
                scenes = self._parse_and_validate(content, total_seconds)

                # Success!
                return scenes

            except (json.JSONDecodeError, ValueError) as e:
                # Debug: Show what the LLM returned
                print(f"\n  ERROR parsing {self.provider} response (attempt {attempt + 1}/{max_retries}): {str(e)}")
                if 'content' in locals() and content:
                    print(f"  First 500 chars of response: {content[:500]}")

                if attempt < max_retries - 1:
                    # Retry with repair prompt
                    user_prompt = f"""Previous attempt failed: {str(e)}

Create a {duration_minutes}-minute video about: {prompt}

IMPORTANT: Output ONLY valid JSON with no markdown, no explanations.
Target: {scene_count} scenes, {total_seconds:.0f} seconds total.
Each scene must have: narration, keywords (2-5 items), target_seconds (3-60), visual_description."""
                    continue
                else:
                    raise ValueError(f"Failed to generate valid scene plan after {max_retries} attempts: {str(e)}")

        raise ValueError(f"Failed to generate valid scene plan after {max_retries} attempts")

    def _estimate_scene_count(self, total_seconds: float) -> int:
        """Estimate scene count for fast, engaging pacing.

        Target: ~3 seconds per scene for TikTok/Instagram Reel-style pacing.
        Longer videos use slightly longer scenes to avoid overwhelming the LLM.
        """
        if total_seconds < 30:
            # Very short: 3-4s per scene
            return max(3, int(total_seconds / 3.5))
        elif total_seconds < 120:
            # Short: 3s per scene (fast pacing)
            return int(total_seconds / 3)
        elif total_seconds < 180:
            # Medium: 4s per scene
            return int(total_seconds / 4)
        else:
            # Long: 5-6s per scene, capped at 40 scenes
            return min(40, int(total_seconds / 5))

    def _parse_and_validate(self, content: str, target_duration: float) -> List[Scene]:
        """Parse LLM response and validate scenes.

        Args:
            content: Raw LLM response
            target_duration: Target total duration in seconds

        Returns:
            List of validated Scene objects

        Raises:
            json.JSONDecodeError: If JSON parsing fails
            ValueError: If validation fails
        """
        # Strip markdown code blocks if present
        content = content.strip()
        if content.startswith('```'):
            # Extract content between code fences
            match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
            if match:
                content = match.group(1)

        # Parse JSON
        data = json.loads(content)

        # Validate with Pydantic
        plan = ScenePlan(
            scenes=[Scene(**scene) for scene in data['scenes']],
            total_duration=target_duration
        )

        # Check total duration is reasonable (within 20% of target)
        actual_duration = sum(scene.target_seconds for scene in plan.scenes)
        duration_diff = abs(actual_duration - target_duration) / target_duration

        if duration_diff > 0.2:
            raise ValueError(
                f"Scene durations sum to {actual_duration:.1f}s, "
                f"but target is {target_duration:.1f}s (difference: {duration_diff*100:.1f}%)"
            )

        return plan.scenes

    def preview_plan(self, scenes: List[Scene]) -> str:
        """Generate human-readable preview of scene plan.

        Args:
            scenes: List of Scene objects

        Returns:
            Formatted string preview
        """
        total_duration = sum(s.target_seconds for s in scenes)

        lines = [
            "=" * 70,
            f"SCENE PLAN ({len(scenes)} scenes, {total_duration:.1f}s total)",
            "=" * 70,
            ""
        ]

        for i, scene in enumerate(scenes, 1):
            lines.append(f"Scene {i} ({scene.target_seconds:.1f}s):")
            lines.append(f"  Narration: {scene.narration}")
            lines.append(f"  Keywords: {', '.join(scene.keywords)}")
            lines.append(f"  Visuals: {scene.visual_description}")
            lines.append("")

        return "\n".join(lines)

    def generate_continuous(self, prompt: str, duration_minutes: float,
                            words_per_second: float = 3.0,
                            max_retries: int = 3,
                            loop_mode: bool = False) -> VideoPlan:
        """Generate continuous narration + visual clip list.

        Unlike generate(), this creates a single flowing narration with
        independent visual clips that change every ~3 seconds.

        Args:
            prompt: User's video topic/description
            duration_minutes: Target video duration in minutes
            words_per_second: Target speaking rate (adjust for TTS speed)
            max_retries: Maximum retry attempts
            loop_mode: If True, narration ends with a bridge word that loops
                       back to the start, and the last clip matches the first

        Returns:
            VideoPlan with continuous narration and clip list
        """
        total_seconds = duration_minutes * 60
        num_clips = max(3, int(total_seconds / 3))
        word_count = int(total_seconds * words_per_second)

        loop_instruction = ""
        if loop_mode:
            loop_instruction = (
                "\n\nLOOP MODE (CRITICAL):"
                "\n- The video will play on repeat. The ending MUST flow seamlessly back into the beginning."
                "\n- End the narration with a SHORT bridge phrase that leads back to the opening words."
                "\n  Examples: end with 'So…' or 'Which brings us back to…' or 'And that's why…' — "
                "something that makes the viewer feel the video is about to restart naturally."
                "\n- Do NOT end with a conclusion or summary. End mid-thought so it loops."
                "\n- The LAST visual clip MUST use the same keywords as the FIRST clip so the visuals also loop."
            )

        user_prompt = f"""VIDEO TOPIC & DIRECTION (follow this closely):
{prompt}

Write a {duration_minutes}-minute narration (~{word_count} words at 3 words/sec) that directly addresses the above topic.
Stay focused on what the user is asking — cover their specific points, questions, and angles.
Include {num_clips} visual clips (~3 seconds each).{loop_instruction}

Output ONLY the JSON, no other text."""

        system_prompt = self._build_continuous_prompt(self.narration_style)

        print(f"  Using LLM: {self.provider} / {self.model}")
        print(f"  Style: {self.narration_style}")
        print(f"  Target: {word_count} words ({words_per_second:.1f} words/sec), {num_clips} clips")

        for attempt in range(max_retries):
            try:
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]

                content = self._call_llm(messages, temperature=0.6)

                if not content or len(content.strip()) == 0:
                    print(f"\n  WARNING: Empty response from {self.provider} (attempt {attempt + 1}/{max_retries})")
                    if attempt < max_retries - 1:
                        continue
                    else:
                        raise ValueError(f"{self.provider} returned empty response after all retries")

                # Strip <think>...</think> blocks from reasoning models (e.g. DeepSeek-R1)
                content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()

                # Parse and validate
                plan = self._parse_continuous_plan(content, total_seconds, words_per_second)
                return plan

            except (json.JSONDecodeError, ValueError) as e:
                print(f"\n  ERROR parsing {self.provider} response (attempt {attempt + 1}/{max_retries}): {str(e)}")
                if 'content' in locals() and content:
                    print(f"  First 500 chars of response: {content[:500]}")

                if attempt < max_retries - 1:
                    user_prompt = f"""Previous attempt failed: {str(e)}

VIDEO TOPIC & DIRECTION (follow this closely):
{prompt}

Write a {duration_minutes}-minute narration (~{word_count} words) that directly addresses the above topic.
IMPORTANT: Output ONLY valid JSON with "narration" (string) and "clips" (array of objects with "keywords" and "visual_description").
Include {num_clips} clips."""
                    continue
                else:
                    raise ValueError(f"Failed to generate valid plan after {max_retries} attempts: {str(e)}")

        raise ValueError(f"Failed to generate valid plan after {max_retries} attempts")

    def _parse_continuous_plan(self, content: str, target_duration: float,
                              words_per_second: float = 3.0) -> VideoPlan:
        """Parse and validate continuous plan response.

        If narration is too long, truncates to target word count at a sentence
        boundary to keep the video duration accurate.

        Args:
            content: Raw LLM response
            target_duration: Target total duration in seconds
            words_per_second: Target speaking rate

        Returns:
            Validated VideoPlan
        """
        content = content.strip()
        if content.startswith('```'):
            match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
            if match:
                content = match.group(1)

        data = json.loads(content)

        narration = data['narration']
        expected_words = int(target_duration * words_per_second)

        # Truncate narration if too long (at sentence boundary)
        words = narration.split()
        if len(words) > expected_words * 1.15:  # Allow 15% over, truncate beyond that
            max_words = int(expected_words * 1.1)  # Truncate to 10% over target
            truncated = ' '.join(words[:max_words])

            # Try to cut at last sentence boundary
            last_period = max(
                truncated.rfind('.'),
                truncated.rfind('!'),
                truncated.rfind('?')
            )
            if last_period > len(truncated) * 0.7:  # Only if we keep at least 70%
                truncated = truncated[:last_period + 1]

            print(f"  Narration truncated: {len(words)} -> {len(truncated.split())} words "
                  f"(target: {expected_words})")
            narration = truncated

        plan = VideoPlan(
            narration=narration,
            clips=[VisualClip(**clip) for clip in data['clips']]
        )

        # Validate narration has reasonable word count
        word_count = len(plan.narration.split())
        if word_count < expected_words * 0.5:
            raise ValueError(
                f"Narration too short: {word_count} words, expected ~{expected_words} "
                f"for {target_duration:.0f}s video"
            )

        # Validate clip count is reasonable
        expected_clips = target_duration / 3
        if len(plan.clips) < expected_clips * 0.5:
            raise ValueError(
                f"Too few clips: {len(plan.clips)}, expected ~{expected_clips:.0f} "
                f"for {target_duration:.0f}s video"
            )

        return plan

    def preview_continuous_plan(self, plan: VideoPlan) -> str:
        """Generate human-readable preview of continuous plan.

        Args:
            plan: VideoPlan object

        Returns:
            Formatted string preview
        """
        word_count = len(plan.narration.split())
        lines = [
            "=" * 70,
            f"VIDEO PLAN ({len(plan.clips)} clips, ~{word_count} words)",
            "=" * 70,
            "",
            "NARRATION:",
            plan.narration[:300] + ("..." if len(plan.narration) > 300 else ""),
            "",
            "VISUAL CLIPS:",
        ]

        for i, clip in enumerate(plan.clips, 1):
            lines.append(f"  Clip {i}: [{', '.join(clip.keywords)}] - {clip.visual_description}")

        return "\n".join(lines)

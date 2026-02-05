"""Scene planning using LLM (Ollama or OpenRouter) to generate structured video scenes."""

import json
import re
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator
import ollama

from token_counter import TokenCounter


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

    # Style-specific tone/voice instructions - kept concise to avoid LLM confusion
    NARRATION_STYLES = {
        'viral': {
            'voice': 'a YouTube shorts scriptwriter who hooks viewers with specific, fascinating facts',
            'tone': 'telling a story to a friend — confident, punchy, no fluff',
            'rules': (
                '- Hook first: open with a shocking fact or bold claim, no warm-up\n'
                '- Every sentence = new info (never repeat or rephrase)\n'
                '- Mix short punches with longer sentences, vary your rhythm\n'
                '- End with a memorable line that sticks'
            ),
        },

        'informative': {
            'voice': 'an expert teacher who makes complex topics simple',
            'tone': 'clear, structured, insightful — like a great explainer video',
            'rules': (
                '- Open with what the viewer will learn\n'
                '- Build understanding step by step in logical order\n'
                '- Use analogies to make complex ideas click\n'
                '- End with a key takeaway they can remember'
            ),
        },

        'funny': {
            'voice': 'a comedian who makes every topic hilarious',
            'tone': 'a funny friend roasting the topic — sarcastic but accurate',
            'rules': (
                '- Open with a joke or absurd observation\n'
                '- Set up expectations, then subvert with a punchline\n'
                '- Exaggerate for effect, keep facts accurate\n'
                '- End with a big punchline or callback'
            ),
        },

        'storytelling': {
            'voice': 'a master storyteller who turns any topic into a narrative',
            'tone': 'a campfire story — vivid, emotional, with drama',
            'rules': (
                '- Open by setting a scene or introducing a character\n'
                '- Build tension with a problem or mystery\n'
                '- Use vivid sensory details — what things look, sound, feel like\n'
                '- End with a satisfying resolution'
            ),
        },

        'motivational': {
            'voice': 'an inspiring coach who empowers viewers to act',
            'tone': 'a pep talk — energetic, direct, uplifting',
            'rules': (
                '- Open with a bold statement or challenge\n'
                '- Acknowledge the struggle, then build momentum\n'
                '- Speak directly to "you" — make it personal\n'
                '- End with a clear call to action'
            ),
        },

        'creative': {
            'voice': 'a curious overthinker having a late-night epiphany',
            'tone': 'a friend at 2 AM who just had a wild thought — relaxed, genuine, mind-wandering',
            'rules': (
                '- Open with the premise casually ("Okay but like... is X really Y?")\n'
                '- Run with the idea — don\'t debunk it, explore it\n'
                '- Think out loud: "and like...", "okay but then...", "which means..."\n'
                '- Use everyday comparisons, not jargon\n'
                '- End with the thought that hits hardest'
            ),
        },
    }

    # Concise keyword rules - used in multiple prompts
    KEYWORD_RULES = """KEYWORDS: Use only filmable nouns (things you can point a camera at).
Good: bee, flower, jar, ocean, clock. Bad: process, energy, concept, nutrients, enzymes."""

    @classmethod
    def _build_continuous_prompt(cls, style: str = 'viral') -> str:
        """Build the system prompt for continuous narration generation.

        Args:
            style: Narration style key from NARRATION_STYLES

        Returns:
            Full system prompt string
        """
        s = cls.NARRATION_STYLES.get(style, cls.NARRATION_STYLES['viral'])

        # Creative style: run with the idea. Other styles: answer the question.
        if style == 'creative':
            prompt_rule = (
                "FOLLOW THE PROMPT: Run with the user's idea. Don't debunk or correct it — "
                "explore it with everyday logic. Stay on their specific topic."
            )
        else:
            prompt_rule = (
                "FOLLOW THE PROMPT: Answer exactly what the user asks. "
                "Cover their specific points in order. Don't go off on tangents."
            )

        return f"""You are {s['voice']}. Your narration sounds like {s['tone']}.

PRIORITY RULES (most important):
1. Stay on topic — every sentence must relate to the user's prompt
2. No repetition — each sentence adds NEW information
3. Natural speech — write like talking to a friend, not a textbook

{prompt_rule}

STYLE:
{s['rules']}

OUTPUT FORMAT (JSON only):
{{"narration": "Full script here.", "clips": [{{"keywords": ["noun1", "noun2"], "visual_description": "What viewer sees"}}]}}

NARRATION: ~3 words/second. No filler phrases. Vary sentence structure.

CLIPS: One per ~3 seconds. Alternate wide/close-up/detail shots.
{cls.KEYWORD_RULES}

Output ONLY valid JSON, no markdown or explanation."""

    SYSTEM_PROMPT = """You write video narration scripts.

PRIORITY RULES (most important):
1. Stay on topic — every sentence must relate to the user's prompt
2. No repetition — each sentence adds NEW information
3. Natural speech — write like talking to a friend

OUTPUT FORMAT (JSON only):
{"scenes": [{"narration": "Full script spoken aloud (8-25 words)", "keywords": ["noun1", "noun2", "noun3"], "target_seconds": 3.0, "visual_description": "What viewer sees"}]}

SCENES: 10-40 scenes, 3-6 seconds each. ~3 words per second.

KEYWORDS: Use only filmable nouns — things you can point a camera at.
- Good: bee, flower, jar, ocean, clock, hands, sunset
- Bad: process, energy, nutrients, enzymes, concept
- Include main topic noun in most scenes (e.g., "bee" for bee videos)

Output ONLY valid JSON, no markdown or explanation."""

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
        self.token_counter = TokenCounter()

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

    def _call_openrouter(self, messages: list, temperature: float = 0.7,
                          stage: str = "unknown") -> str:
        """Call OpenRouter API and return the response content.

        OpenRouter uses an OpenAI-compatible API format.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature
            stage: Description of the call stage for token tracking

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

        # Track token usage if available
        if response.usage:
            self.token_counter.track_usage(
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                model=self.model,
                stage=stage
            )

        return response.choices[0].message.content

    def _call_llm(self, messages: list, temperature: float = 0.7,
                   stage: str = "unknown") -> str:
        """Call the configured LLM provider.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature
            stage: Description of the call stage for token tracking

        Returns:
            Response content string
        """
        if self.provider == "openrouter":
            return self._call_openrouter(messages, temperature, stage)
        else:
            return self._call_ollama(messages, temperature)

    def get_token_report(self) -> str:
        """Get formatted token usage report.

        Returns:
            Formatted string with token counts and cost estimate
        """
        return self.token_counter.format_report(self.model)

    def reset_token_counter(self) -> None:
        """Reset token counter for a new video generation."""
        self.token_counter.reset()

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

                content = self._call_llm(messages, stage="scenes")

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

    def _generate_narration_only(self, prompt: str, duration_minutes: float,
                                  words_per_second: float, loop_mode: bool,
                                  max_retries: int = 3) -> str:
        """Stage 1: Generate narration text only (no clips).

        Smaller output = more reliable for long videos.

        Args:
            prompt: User's video topic/description
            duration_minutes: Target duration in minutes
            words_per_second: Target speaking rate
            loop_mode: If True, narration loops back to start
            max_retries: Maximum retry attempts

        Returns:
            Narration text string
        """
        word_count = int(duration_minutes * 60 * words_per_second)
        s = self.NARRATION_STYLES.get(self.narration_style, self.NARRATION_STYLES['viral'])

        loop_instruction = ""
        if loop_mode:
            loop_instruction = (
                "\n\nLOOP MODE: End with a short bridge phrase that leads back to the opening "
                "(e.g., 'So…', 'Which brings us back to…'). Do NOT conclude — end mid-thought."
            )

        system_prompt = f"""You are {s['voice']}.
Write a narration script that sounds like {s['tone']}.

RULES:
{s['rules']}
- Target ~{word_count} words (~3 words per second)
- Do NOT use filler phrases or overly formal language
- NEVER repeat ideas — every sentence must add NEW information
- Every sentence must be grammatically complete

Output ONLY the narration text. No JSON, no formatting, no quotes around it."""

        user_prompt = f"""Write a {duration_minutes}-minute narration (~{word_count} words) about:
{prompt}{loop_instruction}

Output ONLY the narration text."""

        print(f"  [Stage 1] Generating narration ({word_count} words)...")

        for attempt in range(max_retries):
            try:
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]

                content = self._call_llm(messages, temperature=0.6, stage="narration")

                if not content or len(content.strip()) == 0:
                    print(f"    WARNING: Empty response (attempt {attempt + 1}/{max_retries})")
                    continue

                # Strip thinking tags and clean up
                content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()

                # Remove any accidental JSON wrapping or quotes
                content = content.strip('"\'')
                if content.startswith('{') and '"narration"' in content:
                    # LLM accidentally output JSON, extract narration
                    try:
                        data = json.loads(content)
                        content = data.get('narration', content)
                    except:
                        pass

                words = content.split()
                print(f"    Generated {len(words)} words")

                if len(words) < word_count * 0.5:
                    print(f"    WARNING: Too short, retrying...")
                    continue

                return content

            except Exception as e:
                print(f"    ERROR (attempt {attempt + 1}/{max_retries}): {e}")
                continue

        raise ValueError(f"Failed to generate narration after {max_retries} attempts")

    def _generate_clips_for_narration(self, prompt: str, narration: str,
                                       num_clips: int, max_retries: int = 3) -> List[dict]:
        """Stage 2: Generate visual clips that match the narration.

        Tracks used keywords and passes context between chunks to ensure
        consistency, flow, and avoid video repetition.

        Args:
            prompt: Original user topic (for context)
            narration: Full narration text from Stage 1
            num_clips: Number of clips to generate
            max_retries: Maximum retry attempts

        Returns:
            List of clip dicts with 'keywords' and 'visual_description'
        """
        # For very long narrations, split into chunks
        words = narration.split()
        chunk_size = 200  # ~60 seconds of narration
        clips_per_chunk = max(5, num_clips // max(1, len(words) // chunk_size))

        all_clips = []
        used_keywords = set()  # Track keywords to avoid repetition
        chunks = []

        # Split narration into manageable chunks
        if len(words) > chunk_size * 1.5:
            # Split at sentence boundaries
            sentences = re.split(r'(?<=[.!?])\s+', narration)
            current_chunk = []
            current_words = 0

            for sent in sentences:
                sent_words = len(sent.split())
                if current_words + sent_words > chunk_size and current_chunk:
                    chunks.append(' '.join(current_chunk))
                    current_chunk = [sent]
                    current_words = sent_words
                else:
                    current_chunk.append(sent)
                    current_words += sent_words

            if current_chunk:
                chunks.append(' '.join(current_chunk))
        else:
            chunks = [narration]

        print(f"  [Stage 2] Generating clips for {len(chunks)} chunk(s)...")

        system_prompt = """Generate visual clips for a video narration.

OUTPUT FORMAT (JSON array only):
[
  {"keywords": ["keyword1", "keyword2", "keyword3"], "visual_description": "What viewer sees"},
  {"keywords": ["keyword1", "keyword2", "keyword3"], "visual_description": "Next visual"}
]

RULES:
- Each clip = 4-10 seconds of video footage
- Keywords must be CONCRETE, FILMABLE nouns (not abstract concepts)
- Use words a photographer would use to label footage
- Match clips to the narration content — show what's being discussed
- VARY SHOTS: alternate wide → close-up → detail → action → wide
- Do NOT repeat the same visual concept twice in a row
- Each clip should feel like a natural visual progression

WRONG keywords: ["process", "concept", "idea", "thing"]
RIGHT keywords: ["brain", "neurons", "clock", "sunset"]

Output ONLY the JSON array, nothing else."""

        for chunk_idx, chunk in enumerate(chunks):
            chunk_clips = clips_per_chunk if chunk_idx < len(chunks) - 1 else (num_clips - len(all_clips))
            chunk_clips = max(3, chunk_clips)

            # Build context from previous clips
            context_info = ""
            if all_clips:
                # Show last 2 clips for continuity
                last_clips = all_clips[-2:]
                prev_visuals = [c['visual_description'] for c in last_clips]
                context_info = f"\n\nPREVIOUS CLIPS (for continuity):\n"
                for i, vis in enumerate(prev_visuals):
                    context_info += f"  - {vis}\n"
                context_info += "Continue the visual flow from these.\n"

            # List keywords to avoid (limit to most recent to avoid huge lists)
            avoid_info = ""
            if used_keywords:
                recent_keywords = list(used_keywords)[-30:]  # Last 30 keywords
                avoid_info = f"\n\nAVOID THESE ALREADY-USED KEYWORDS (to prevent video repetition):\n{', '.join(recent_keywords)}\nUse DIFFERENT angles, subjects, or descriptors.\n"

            user_prompt = f"""Topic: {prompt}

Narration segment (chunk {chunk_idx + 1} of {len(chunks)}):
"{chunk}"{context_info}{avoid_info}
Generate {chunk_clips} visual clips that match this narration.
Output ONLY the JSON array."""

            for attempt in range(max_retries):
                try:
                    messages = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ]

                    content = self._call_llm(messages, temperature=0.5, stage="clips")

                    if not content:
                        continue

                    # Clean up response
                    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
                    content = content.strip()

                    # Extract JSON array from markdown code blocks
                    if content.startswith('```'):
                        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
                        if match:
                            content = match.group(1)

                    # Try to extract just the array portion (ignore garbage around it)
                    array_match = re.search(r'\[[\s\S]*\]', content)
                    if array_match:
                        content = array_match.group(0)

                    # JSON repair fixes
                    content = re.sub(r',\s*([\]}])', r'\1', content)  # trailing commas
                    content = re.sub(r'\.\.\.', '', content)  # ellipsis used by LLM
                    content = re.sub(r'"\s*\n\s*"', '", "', content)  # broken strings across lines
                    content = re.sub(r',\s*,', ',', content)  # double commas
                    content = re.sub(r'\[\s*,', '[', content)  # leading comma in array
                    content = re.sub(r',\s*\]', ']', content)  # trailing comma before ]

                    try:
                        clips_data = json.loads(content)
                    except json.JSONDecodeError:
                        # Fallback: extract clip objects with regex
                        clips_data = []
                        pattern = r'\{\s*"keywords"\s*:\s*\[(.*?)\]\s*,\s*"visual_description"\s*:\s*"([^"]+)"\s*\}'
                        for clip_match in re.finditer(pattern, content):
                            keywords = [k.strip(' "\'') for k in clip_match.group(1).split(',') if k.strip(' "\'')]
                            clips_data.append({
                                'keywords': keywords,
                                'visual_description': clip_match.group(2)
                            })
                        if not clips_data:
                            raise ValueError(f"Could not extract any clips from response")

                    # Handle both array and object with clips key
                    if isinstance(clips_data, dict) and 'clips' in clips_data:
                        clips_data = clips_data['clips']

                    if not isinstance(clips_data, list):
                        raise ValueError("Expected array of clips")

                    # Validate and add clips, track keywords
                    for clip in clips_data:
                        if 'keywords' in clip and 'visual_description' in clip:
                            all_clips.append(clip)
                            # Track keywords to avoid repetition
                            for kw in clip['keywords']:
                                used_keywords.add(kw.lower())

                    print(f"    Chunk {chunk_idx + 1}: {len(clips_data)} clips")
                    break

                except Exception as e:
                    print(f"    Chunk {chunk_idx + 1} ERROR (attempt {attempt + 1}): {e}")
                    if 'content' in locals() and content:
                        # Show a snippet of what we tried to parse
                        snippet = content[:200].replace('\n', ' ')
                        print(f"    Response snippet: {snippet}...")
                    if attempt == max_retries - 1:
                        # Generate fallback clips with varied keywords
                        topic_words = [w.lower() for w in prompt.split()[:3] if len(w) > 3]
                        angles = ['wide', 'closeup', 'detail', 'action', 'aerial']
                        for i in range(chunk_clips):
                            angle = angles[i % len(angles)]
                            all_clips.append({
                                'keywords': topic_words + [angle],
                                'visual_description': f'{angle.title()} shot for segment {len(all_clips) + 1}'
                            })

        return all_clips[:num_clips]

    def generate_continuous(self, prompt: str, duration_minutes: float,
                            words_per_second: float = 3.0,
                            max_retries: int = 3,
                            loop_mode: bool = False) -> VideoPlan:
        """Generate continuous narration + visual clip list.

        Unlike generate(), this creates a single flowing narration with
        independent visual clips that change every 3-6 seconds (longer for videos > 1 min).

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
        # Longer videos use slower clip changes (4-10 seconds per clip)
        clip_duration = 6 if duration_minutes > 1.0 else 3
        num_clips = max(3, int(total_seconds / clip_duration))
        word_count = int(total_seconds * words_per_second)

        print(f"  Using LLM: {self.provider} / {self.model}")
        print(f"  Style: {self.narration_style}")
        print(f"  Target: {word_count} words ({words_per_second:.1f} words/sec), {num_clips} clips")

        # Use two-stage generation for videos > 1 minute (more reliable for long outputs)
        if duration_minutes > 1.0:
            print(f"  Using two-stage generation for long video...")
            try:
                # Stage 1: Generate narration only
                narration = self._generate_narration_only(
                    prompt, duration_minutes, words_per_second, loop_mode, max_retries
                )

                # Stage 2: Generate clips for the narration
                clips_data = self._generate_clips_for_narration(
                    prompt, narration, num_clips, max_retries
                )

                # Build VideoPlan
                plan = VideoPlan(
                    narration=narration,
                    clips=[VisualClip(**clip) for clip in clips_data]
                )

                # Log summary
                actual_words = len(narration.split())
                print(f"\n  PLAN SUMMARY (two-stage):")
                print(f"  Narration: {actual_words} words")
                print(f"  Clips: {len(plan.clips)}")

                return plan

            except Exception as e:
                print(f"  Two-stage failed: {e}")
                print(f"  Falling back to single-stage generation...")

        # Single-stage generation (for short videos or as fallback)
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

        for attempt in range(max_retries):
            try:
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]

                content = self._call_llm(messages, temperature=0.6, stage="narration+clips")

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

"""Scene planning: an LLM (Ollama, OpenRouter or Claude) writes the narration and shot plan."""

import json
import os
import re
from difflib import SequenceMatcher
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator

from token_counter import TokenCounter
from script_variety import BANNED_PHRASES, has_banned_opener, pick_angle, variety_block


def extract_json_payload(content: str) -> str:
    """Extract the JSON object/array from a raw LLM response.

    Handles <think> blocks, ```json fences, and leading/trailing prose by
    slicing from the first '{' or '[' to the matching last '}' or ']'.
    Returns the (possibly unchanged) string; json.loads decides validity.
    """
    if content is None:
        return ''
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()

    fence = re.search(r'```(?:json|JSON)?\s*\n?(.*?)\n?```', content, re.DOTALL)
    if fence:
        content = fence.group(1).strip()

    starts = [i for i in (content.find('{'), content.find('[')) if i != -1]
    if not starts:
        return content
    start = min(starts)
    closer = '}' if content[start] == '{' else ']'
    end = content.rfind(closer)
    if end > start:
        return content[start:end + 1]
    return content[start:]


def parse_llm_json(content: str):
    """Parse a raw LLM response into Python data (raises json.JSONDecodeError)."""
    return json.loads(extract_json_payload(content))


def extract_clips_list(data) -> list:
    """Return the clips list from parsed LLM JSON.

    Accepts either {"clips": [...]} or a bare top-level list of clips.
    Raises ValueError (retryable) for any other shape.
    """
    if isinstance(data, list):
        clips = data
    elif isinstance(data, dict) and isinstance(data.get('clips'), list):
        clips = data['clips']
    else:
        raise ValueError("LLM response has no 'clips' array")
    clips = [c for c in clips if isinstance(c, dict)]
    if not clips:
        raise ValueError("LLM response contains no clip objects")
    return clips


# Function words that must never be promoted to stock-footage keywords
def seconds_per_clip(total_seconds: float) -> float:
    """How long each shot lasts, which sets how many stock clips a video needs.

    SECONDS_PER_CLIP (default 5) controls pacing; fewer, longer shots mean
    fewer downloads. Videos over a minute use at least 6 s per shot.
    """
    try:
        base = float(os.getenv("SECONDS_PER_CLIP", "5"))
    except ValueError:
        base = 5.0
    base = min(max(base, 2.0), 15.0)
    return max(base, 6.0) if total_seconds > 60 else base


def clip_count_for(total_seconds: float) -> int:
    return max(2, int(round(total_seconds / seconds_per_clip(total_seconds))))


KEYWORD_STOPWORDS = frozenset({
    'a', 'an', 'the', 'and', 'or', 'but', 'nor', 'so', 'yet', 'for', 'of', 'to',
    'in', 'on', 'at', 'by', 'up', 'as', 'is', 'it', 'its', 'be', 'are', 'was',
    'were', 'been', 'being', 'am', 'has', 'have', 'had', 'having', 'do', 'does',
    'did', 'doing', 'can', 'could', 'will', 'would', 'shall', 'should', 'may',
    'might', 'must', 'not', 'no', 'yes', 'all', 'any', 'each', 'every', 'few',
    'more', 'most', 'other', 'some', 'such', 'only', 'own', 'same', 'than',
    'too', 'very', 'just', 'also', 'then', 'there', 'here', 'when', 'where',
    'why', 'how', 'what', 'which', 'who', 'whom', 'whose', 'this', 'that',
    'these', 'those', 'with', 'without', 'within', 'from', 'into', 'onto',
    'over', 'under', 'above', 'below', 'between', 'through', 'during', 'before',
    'after', 'while', 'about', 'against', 'along', 'around', 'across', 'behind',
    'beside', 'near', 'off', 'out', 'down', 'again', 'once', 'until', 'upon',
    'toward', 'towards', 'via', 'per', 'his', 'her', 'hers', 'him', 'she', 'he',
    'they', 'them', 'their', 'theirs', 'our', 'ours', 'we', 'us', 'you', 'your',
    'yours', 'my', 'mine', 'me', 'one', 'two', 'three', 'itself',
    'herself', 'himself', 'themselves', 'someone', 'something', 'another',
    # Shot-description filler
    'close', 'closeup', 'wide', 'shot', 'shots', 'showing', 'shows', 'view',
    'scene', 'camera', 'angle', 'visual', 'slowly', 'quickly', 'moving',
    'reveals', 'revealing', 'footage', 'video', 'clip', 'frame', 'background',
    'foreground', 'slow', 'motion', 'aerial', 'pan', 'zoom', 'detail',
})


def _normalize_keyword_list(keywords) -> list:
    """Coerce LLM keyword output into a list of non-empty strings."""
    if keywords is None:
        return []
    if isinstance(keywords, str):
        keywords = keywords.split(',')
    elif not isinstance(keywords, (list, tuple)):
        return []
    result = []
    for k in keywords:
        if isinstance(k, str):
            k = k.strip().strip('"\'').strip()
            if k:
                result.append(k)
    return result


class Scene(BaseModel):
    """Represents a single scene in the video with multiple clip support."""

    narration: str = Field(..., min_length=8, max_length=500)  # Allow shorter narrations for fast pacing
    keywords: List[str] = Field(..., min_length=2, max_length=5)
    target_seconds: float = Field(..., ge=2.0, lt=15.0)  # Allow 2-15s scenes (fast pacing)
    visual_description: str = Field(..., min_length=10, max_length=300)

    # Multiple clips per scene support
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
        # A style sets the voice, tone and devices only. How each video opens,
        # how it is structured and how it ends come from a per-video angle
        # (script_variety.pick_angle), so two videos in one style never share
        # the same opening.
        'viral': {
            'voice': 'a short-form scriptwriter who hooks viewers with specific, surprising facts',
            'tone': 'telling a story to a friend — confident, punchy, no fluff',
            'rules': (
                '- Hook in the first sentence, no warm-up\n'
                '- Every sentence = new info (never repeat or rephrase)\n'
                '- Mix short punches with longer sentences, vary your rhythm\n'
                '- Prefer concrete numbers, names and places over vague claims'
            ),
        },

        'informative': {
            'voice': 'an expert teacher who makes complex topics simple',
            'tone': 'clear, structured, insightful — like a great explainer video',
            'rules': (
                '- Build understanding in a logical order\n'
                '- Use one strong analogy to make the hardest idea click\n'
                '- Define any term the first time you use it, in plain words\n'
                '- Precise facts over generalities'
            ),
        },

        'funny': {
            'voice': 'a comedian who makes every topic hilarious',
            'tone': 'a funny friend roasting the topic — sarcastic but accurate',
            'rules': (
                '- Set up expectations, then subvert them with a punchline\n'
                '- Exaggerate for effect, keep the facts accurate\n'
                '- Use specific, visual comparisons rather than generic jokes\n'
                '- Land at least one callback to an earlier joke'
            ),
        },

        'storytelling': {
            'voice': 'a master storyteller who turns any topic into a narrative',
            'tone': 'a campfire story — vivid, emotional, with drama',
            'rules': (
                '- Give the story a person, place or object to follow\n'
                '- Build tension with a problem or mystery\n'
                '- Use vivid sensory details — what things look, sound, feel like\n'
                '- Show, don\'t summarise'
            ),
        },

        'motivational': {
            'voice': 'an inspiring coach who empowers viewers to act',
            'tone': 'a pep talk — energetic, direct, uplifting',
            'rules': (
                '- Acknowledge the struggle honestly, then build momentum\n'
                '- Speak directly to "you" — make it personal\n'
                '- Use one concrete example instead of abstract advice\n'
                '- Avoid motivational clichés; be specific'
            ),
        },

        'creative': {
            'voice': 'a curious overthinker having a late-night epiphany',
            'tone': 'a friend at 2 AM who just had a wild thought — relaxed, genuine, mind-wandering',
            'rules': (
                '- Run with the idea — don\'t debunk it, explore it\n'
                '- Think out loud in your own words; vary how each sentence starts\n'
                '- Use everyday comparisons, not jargon\n'
                '- Follow the logic one step further than feels comfortable'
            ),
        },
    }

    # Concise keyword rules - used in multiple prompts
    KEYWORD_RULES = """KEYWORDS (CRITICAL - follow these rules exactly):
1. Use ONLY concrete, filmable nouns — things a camera can physically record
2. Test: "Can I find this on a stock video site?" If no, replace it
3. NEVER use: process, energy, concept, system, importance, nutrients, enzymes, transformation, method, approach, technique, factor, aspect, element, principle
4. ALWAYS prefer: the physical object, location, animal, tool, body part
5. Use "noun + modifier" pairs: "coffee shop" not "coffee", "woman typing laptop" not "laptop"
6. Examples:
   - Topic "photosynthesis" -> keywords: ["leaf", "sunlight", "tree canopy"]
   - Topic "economics" -> keywords: ["coins", "stock market screen", "factory"]
   - Topic "sleep science" -> keywords: ["bed", "pillow", "brain scan", "alarm clock"]"""

    # Abstract words that should never appear as stock footage keywords
    ABSTRACT_BLOCKLIST = {
        'process', 'energy', 'concept', 'system', 'importance', 'nutrients',
        'enzymes', 'transformation', 'method', 'approach', 'technique',
        'factor', 'aspect', 'element', 'principle', 'mechanism', 'idea',
        'thing', 'stuff', 'way', 'impact', 'effect', 'result', 'outcome',
        'benefit', 'advantage', 'challenge', 'issue', 'problem', 'solution',
        'strategy', 'practice', 'experience', 'information', 'knowledge',
        'ability', 'capacity', 'potential', 'quality', 'level', 'type',
    }

    @staticmethod
    def _refine_keywords(clips: list) -> list:
        """Post-process clip keywords: remove abstract words, supplement from visual_description.

        - Keywords given as a comma-separated string are split into a list.
        - Words promoted from the description skip stopwords/shot filler.
        - If refinement would leave fewer than 2 keywords (VisualClip minimum),
          the original keywords are kept so valid LLM output isn't rejected.
        """
        for clip in clips:
            if isinstance(clip, dict):
                raw_keywords = clip.get('keywords', [])
                desc = clip.get('visual_description', '')
            else:
                raw_keywords = clip.keywords
                desc = clip.visual_description
            if not isinstance(desc, str):
                desc = ''

            original = _normalize_keyword_list(raw_keywords)

            # Filter out abstract words
            filtered = [k for k in original
                        if k.lower().strip() not in ScenePlanner.ABSTRACT_BLOCKLIST]

            # If too many were removed, extract nouns from visual_description
            if len(filtered) < 2 and desc:
                existing = {k.lower() for k in filtered}
                desc_words = re.findall(r'\b[a-z]{3,}\b', desc.lower())
                seen = set()
                unique = []
                for w in desc_words:
                    if (w in ScenePlanner.ABSTRACT_BLOCKLIST or w in KEYWORD_STOPWORDS
                            or w in existing or w in seen):
                        continue
                    seen.add(w)
                    unique.append(w)
                filtered.extend(unique[:max(0, 3 - len(filtered))])

            # Never drop below the VisualClip minimum; fall back to the original
            if len(filtered) < 2 and len(original) >= 2:
                filtered = original

            if isinstance(clip, dict):
                clip['keywords'] = filtered[:5]
            else:
                clip.keywords = filtered[:5]

        return clips

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

CLIPS: Use exactly the number of clips the user message asks for. Alternate wide/close-up/detail shots.
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
                 provider: Literal["ollama", "openrouter", "anthropic", "claude-code"] = "ollama",
                 model: str = "qwen2.5:3b",
                 base_url: str = "http://localhost:11434",
                 api_key: Optional[str] = None,
                 narration_style: str = "viral"):
        """Initialize scene planner.

        Args:
            provider: LLM provider: 'ollama', 'openrouter', 'anthropic' (Anthropic API)
                or 'claude-code' (local Claude Code CLI, uses its own login)
            model: Model name (Ollama model, OpenRouter model ID, or Claude model;
                for claude-code an empty value uses the CLI's default)
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

        self._claude = None
        if provider == "anthropic":
            from claude_llm import AnthropicChat
            self._claude = AnthropicChat(api_key=api_key, model=model or None)
            self.model = self._claude.model
        elif provider == "claude-code":
            from claude_llm import ClaudeCodeChat
            self._claude = ClaudeCodeChat(executable=base_url or None, model=model or None)
            self.model = model or "claude-code"

        # Ollama client bound to the configured host (e.g. host.docker.internal
        # in Docker). Module-level ollama.chat() would ignore base_url.
        self._ollama_client = None
        if provider == "ollama":
            import ollama
            self._ollama_client = ollama.Client(host=base_url)

    def _call_ollama(self, messages: list, temperature: float = 0.7) -> str:
        """Call Ollama API and return the response content.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature

        Returns:
            Response content string
        """
        if self._ollama_client is None:
            import ollama
            self._ollama_client = ollama.Client(host=self.base_url)
        response = self._ollama_client.chat(
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
            timeout=60.0,
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
        if self._claude is not None:
            # Claude models choose their own sampling; temperature is not sent.
            return self._claude.chat(messages)
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

            except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
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
        """Scene count for the given length, following SECONDS_PER_CLIP pacing."""
        return min(40, clip_count_for(total_seconds))

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
        # Strip fences/prose and parse JSON
        data = parse_llm_json(content)

        if isinstance(data, list):
            scenes_data = data
        elif isinstance(data, dict) and isinstance(data.get('scenes'), list):
            scenes_data = data['scenes']
        else:
            raise ValueError("LLM response has no 'scenes' array")

        # Validate with Pydantic
        plan = ScenePlan(
            scenes=[Scene(**scene) for scene in scenes_data if isinstance(scene, dict)],
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

    def generate_narration(self, prompt: str, duration_minutes: float,
                           words_per_second: float, loop_mode: bool = False,
                           max_retries: int = 3) -> str:
        """Generate narration text only (no clips).

        Use this when you only need the script and will generate clips
        separately later via generate_clips_from_script().

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
        angle = pick_angle(self.narration_style)
        print(f"  Script angle: {angle.describe()}")

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

{variety_block(angle)}

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

                content = self._call_llm(messages, temperature=0.8, stage="narration")

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

                if has_banned_opener(content) and attempt < max_retries - 1:
                    print(f"    WARNING: Cliche opening, retrying...")
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
- Match clips to the narration content — show what's being discussed
- VARY SHOTS: alternate wide → close-up → detail → action → wide
- Do NOT repeat the same visual concept twice in a row
- Each clip should feel like a natural visual progression

KEYWORD REQUIREMENTS (CRITICAL - follow exactly):
1. Use ONLY concrete, filmable nouns — things a camera can physically record
2. Test: "Can I find this on a stock video site?" If no, replace it
3. NEVER use: process, energy, concept, system, importance, nutrients, enzymes, transformation, method, approach, technique, factor, aspect, element, principle
4. ALWAYS prefer: the physical object, location, animal, tool, body part
5. Use "noun + modifier" pairs: "coffee shop" not "coffee", "woman typing laptop" not "laptop"
6. Examples:
   - Topic "photosynthesis" -> ["leaf", "sunlight", "tree canopy"]
   - Topic "economics" -> ["coins", "stock market screen", "factory"]
   - WRONG: ["process", "concept", "idea", "thing", "importance"]
   - RIGHT: ["brain", "neurons", "clock tower", "sunset beach"]

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

        all_clips = self._refine_keywords(all_clips[:num_clips])
        return all_clips

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
        clip_duration = seconds_per_clip(total_seconds)
        num_clips = clip_count_for(total_seconds)
        word_count = int(total_seconds * words_per_second)

        print(f"  Using LLM: {self.provider} / {self.model}")
        print(f"  Style: {self.narration_style}")
        print(f"  Target: {word_count} words ({words_per_second:.1f} words/sec), {num_clips} clips")

        # Use two-stage generation for videos > 1 minute (more reliable for long outputs)
        if duration_minutes > 1.0:
            print(f"  Using two-stage generation for long video...")
            try:
                # Stage 1: Generate narration only
                narration = self.generate_narration(
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
Include {num_clips} visual clips (~{clip_duration:.0f} seconds each).{loop_instruction}

Output ONLY the JSON, no other text."""

        angle = pick_angle(self.narration_style)
        print(f"  Script angle: {angle.describe()}")
        system_prompt = self._build_continuous_prompt(self.narration_style) + "\n\n" + variety_block(angle)

        for attempt in range(max_retries):
            try:
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]

                content = self._call_llm(messages, temperature=0.8, stage="narration+clips")

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

            except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
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
        data = parse_llm_json(content)
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object with 'narration' and 'clips'")

        narration = data.get('narration')
        if not isinstance(narration, str) or not narration.strip():
            raise ValueError("LLM response has no 'narration' string")
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

        refined_clips = self._refine_keywords(extract_clips_list(data))
        plan = VideoPlan(
            narration=narration,
            clips=[VisualClip(**clip) for clip in refined_clips]
        )

        # Validate narration has reasonable word count
        word_count = len(plan.narration.split())
        if word_count < expected_words * 0.5:
            raise ValueError(
                f"Narration too short: {word_count} words, expected ~{expected_words} "
                f"for {target_duration:.0f}s video"
            )

        # Validate clip count is reasonable
        expected_clips = target_duration / seconds_per_clip(target_duration)
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

    @classmethod
    def _build_clips_only_prompt(cls) -> str:
        """Build system prompt for clips-only generation (no narration modification)."""
        return """You are a visual director. Given a narration script, generate a list of video clips that visually illustrate the content.

Output ONLY valid JSON in this exact format:
{
  "clips": [
    {"keywords": ["keyword1", "keyword2", "keyword3"], "visual_description": "What the viewer sees during this 3-second segment"},
    {"keywords": ["keyword1", "keyword2", "keyword3"], "visual_description": "Next visual segment"}
  ]
}

CLIPS RULES:
- Each clip represents one shot of stock footage (the user message gives the length)
- Clips should visually illustrate the key topics, objects, and scenes mentioned in the narration
- Spread clips evenly across the script content
- Vary the visuals: alternate between wide shots, close-ups, different angles

KEYWORD REQUIREMENTS (CRITICAL - follow exactly):
1. Keywords MUST be concrete, physical NOUNS that can be filmed
2. Test each keyword: "Can I find this on a stock video site?" If no, replace it
3. NEVER use abstract concepts: "process", "energy", "system", "importance", "concept", "method", "approach", "technique", "factor", "aspect", "element", "principle", "transformation"
4. NEVER use scientific terms as keywords: "enzymes", "photosynthesis", "molecules"
5. Use words a photographer would use to label footage
6. Use "noun + modifier" pairs: "coffee shop" not "coffee", "woman typing laptop" not "laptop"

EXAMPLES:
- Good: ["ocean waves", "sandy beach", "sunset sky"]
- Good: ["kitchen counter", "chef cooking", "frying pan"]
- Good: ["brain scan", "alarm clock", "pillow bed"]
- Bad: ["importance", "health", "nutrients"] (too abstract)
- Bad: ["process", "transformation", "system"] (not filmable)

Do NOT include markdown code blocks, explanations, or any text outside the JSON."""

    def generate_clips_from_script(self, script: str, duration_minutes: float,
                                   max_retries: int = 3) -> VideoPlan:
        """Generate visual clips for a user-provided script.

        Unlike generate_continuous(), this method does NOT modify the narration.
        The script is used AS-IS; only visual clips are generated by the LLM.

        Args:
            script: User-provided narration script (used as-is)
            duration_minutes: Estimated video duration in minutes
            max_retries: Maximum retry attempts

        Returns:
            VideoPlan with the original script and LLM-generated clips
        """
        total_seconds = duration_minutes * 60
        num_clips = clip_count_for(total_seconds)
        shot_seconds = seconds_per_clip(total_seconds)

        system_prompt = self._build_clips_only_prompt()

        user_prompt = f"""Generate {num_clips} visual clips (~{shot_seconds:.0f} seconds each) for this narration script.

SCRIPT (do NOT modify this - generate visuals that match it):
---
{script}
---

Output {num_clips} clips that visually illustrate the key moments and topics in this script.
Each clip should be ~{shot_seconds:.0f} seconds of video footage.

Output ONLY the JSON, no other text."""

        print(f"  Using LLM: {self.provider} / {self.model}")
        print(f"  Generating {num_clips} visual clips for user-provided script...")

        for attempt in range(max_retries):
            try:
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]

                content = self._call_llm(messages, temperature=0.6)

                if not content or len(content.strip()) == 0:
                    print(f"\n  WARNING: Empty response (attempt {attempt + 1}/{max_retries})")
                    if attempt < max_retries - 1:
                        continue
                    else:
                        raise ValueError("LLM returned empty response after all retries")

                # Strip <think>...</think> blocks if present (reasoning models)
                content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()

                # Parse clips from response
                clips = self._parse_clips_only(content, num_clips)

                # Return VideoPlan with original script + generated clips
                return VideoPlan(narration=script, clips=clips)

            except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
                print(f"\n  ERROR parsing response (attempt {attempt + 1}/{max_retries}): {str(e)}")
                if attempt < max_retries - 1:
                    user_prompt = f"""Previous attempt failed: {str(e)}

Generate {num_clips} visual clips for this script:
---
{script}
---

IMPORTANT: Output ONLY valid JSON with "clips" array. Each clip needs "keywords" (array) and "visual_description" (string)."""
                    continue
                else:
                    raise ValueError(f"Failed to generate clips after {max_retries} attempts: {str(e)}")

        raise ValueError(f"Failed to generate clips after {max_retries} attempts")

    def generate_publish_metadata(self, narration: str, topic: Optional[str] = None) -> dict:
        """Write a YouTube title, description and tags for a finished video.

        Returns {"title": str (<=100 chars), "description": str, "tags": [str] (<=15)}.
        Never raises: falls back to a title from the topic and the first
        sentences of the narration if the LLM fails or returns bad JSON.
        """
        fallback = fallback_publish_metadata(narration, topic)
        topic_line = f"Topic: {topic}\n\n" if topic else ""
        messages = [
            {
                "role": "system",
                "content": (
                    "You write YouTube metadata for short videos. Respond with ONLY a JSON object: "
                    '{"title": "...", "description": "...", "tags": ["...", "..."]}. '
                    "Title: catchy, accurate, under 90 characters, no hashtags, no quotes. "
                    "Description: 2-4 short sentences that hook the viewer and summarize the video, "
                    "no links, no hashtags. Tags: 8-15 relevant search keywords or short phrases."
                ),
            },
            {
                "role": "user",
                "content": f"{topic_line}Narration:\n{(narration or '')[:6000]}\n\nReturn the JSON now.",
            },
        ]
        try:
            content = self._call_llm(messages, temperature=0.5, stage="publish_metadata")
            data = parse_llm_json(content)
            if not isinstance(data, dict):
                raise ValueError("metadata is not a JSON object")
            title = _truncate_title(data.get("title") or "")
            description = _clean_youtube_text(data.get("description") or "")
            tags = normalize_tags(data.get("tags") or [])
            return {
                "title": title or fallback["title"],
                "description": description or fallback["description"],
                "tags": tags or fallback["tags"],
            }
        except Exception as e:
            print(f"    WARNING: publish metadata generation failed, using fallback: {e}")
            return fallback

    def suggest_topics(self, niche: str, count: int, avoid: Optional[List[str]] = None,
                       style: Optional[str] = None) -> List[str]:
        """Brainstorm ``count`` specific, filmable Short topics for a niche.

        Topics are questions or claims of at most 90 characters, without
        numbering or clickbait phrases, deduplicated case-insensitively against
        each other and against ``avoid`` (titles/topics the user already has;
        near-duplicates with a similarity ratio above 0.85 are dropped). If
        fewer than ``count`` survive, the LLM is asked once more for the rest.
        Raises if the first LLM call fails or nothing usable comes back.
        """
        count = max(1, int(count))
        avoid = [a for a in (avoid or []) if isinstance(a, str) and a.strip()]
        accepted: List[str] = []
        for attempt in range(2):
            need = count - len(accepted)
            if need <= 0:
                break
            ask = need + max(2, need // 4)
            messages = _topic_messages(niche, ask, avoid + accepted, style)
            try:
                content = self._call_llm(messages, temperature=0.9, stage="suggest_topics")
                raw = parse_topics_response(content)
            except Exception:
                if attempt == 0:
                    raise
                break  # keep what the first call gave
            accepted = dedupe_topics(raw, avoid, limit=count, accepted=accepted)
        if not accepted:
            raise ValueError("The AI did not return any usable topics")
        return accepted[:count]

    def _parse_clips_only(self, content: str, expected_clips: int) -> List[VisualClip]:
        """Parse LLM response for clips-only generation.

        Args:
            content: Raw LLM response
            expected_clips: Expected number of clips

        Returns:
            List of VisualClip objects
        """
        data = parse_llm_json(content)
        refined = self._refine_keywords(extract_clips_list(data))
        clips = [VisualClip(**clip) for clip in refined]

        # Validate reasonable clip count
        if len(clips) < expected_clips * 0.5:
            raise ValueError(f"Too few clips: {len(clips)}, expected ~{expected_clips}")

        return clips


# ── Topic brainstorming (batch autopilot) ─────────────────────

TOPIC_MAX_CHARS = 90
TOPIC_MIN_CHARS = 8
TOPIC_SIMILARITY = 0.85
TOPIC_AVOID_IN_PROMPT = 80
_TOPIC_NUMBERING = re.compile(r'^\s*(?:[-*•]+|#?\d{1,3}\s*[.):\]-]|\(\d{1,3}\))\s*')


def normalize_topic(text: str) -> str:
    """Lowercase, punctuation-free, single-spaced form used to compare topics."""
    text = (text or '').lower().replace("'", '').replace('’', '')
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', text)).strip()


_BANNED_TOPIC_PHRASES = tuple(normalize_topic(p) for p in BANNED_PHRASES)


def clean_topic(text) -> Optional[str]:
    """One usable topic line, or None (too long/short, numbered junk removed, banned phrase)."""
    if not isinstance(text, str):
        return None
    t = re.sub(r'\s+', ' ', text).strip()
    t = _TOPIC_NUMBERING.sub('', t).strip()
    t = t.strip('"\'“”‘’` ').strip()
    t = re.sub(r'\s#\w+', '', t).strip()  # stray hashtags
    if len(t) < TOPIC_MIN_CHARS or len(t) > TOPIC_MAX_CHARS:
        return None
    norm = f' {normalize_topic(t)} '
    if any(p and f' {p} ' in norm for p in _BANNED_TOPIC_PHRASES):
        return None
    return t


def _too_similar(a: str, b: str) -> bool:
    if a == b:
        return True
    matcher = SequenceMatcher(None, a, b)
    return matcher.real_quick_ratio() > TOPIC_SIMILARITY and matcher.quick_ratio() > TOPIC_SIMILARITY \
        and matcher.ratio() > TOPIC_SIMILARITY


def dedupe_topics(candidates, avoid=(), limit: Optional[int] = None,
                  accepted: Optional[List[str]] = None) -> List[str]:
    """Append cleaned candidates to ``accepted`` unless they (nearly) repeat it or ``avoid``."""
    result = list(accepted or [])
    taken = [normalize_topic(a[:200]) for a in avoid or [] if isinstance(a, str)]
    taken += [normalize_topic(t) for t in result]
    taken = [t for t in taken if t]
    for raw in candidates or []:
        if limit is not None and len(result) >= limit:
            break
        topic = clean_topic(raw)
        if topic is None:
            continue
        norm = normalize_topic(topic)
        if not norm or any(_too_similar(norm, t) for t in taken):
            continue
        result.append(topic)
        taken.append(norm)
    return result


def parse_topics_response(content: str) -> List[str]:
    """Topic strings from {"topics": [...]} (or a bare list; items may be {"topic": ...})."""
    data = parse_llm_json(content)
    if isinstance(data, dict):
        data = data.get('topics') or data.get('ideas') or data.get('titles')
    if not isinstance(data, list):
        raise ValueError("LLM response has no 'topics' list")
    topics = []
    for entry in data:
        if isinstance(entry, dict):
            entry = entry.get('topic') or entry.get('title') or entry.get('text')
        if isinstance(entry, str) and entry.strip():
            topics.append(entry.strip())
    return topics


def _topic_messages(niche: str, count: int, avoid: List[str], style: Optional[str]) -> list:
    banned = ', '.join(f'"{p}"' for p in BANNED_PHRASES)
    style_line = f"- Match a {style} channel's tone.\n" if style else ''
    system = (
        "You brainstorm topics for short narrated videos (YouTube Shorts) built from stock footage. "
        'Respond with ONLY a JSON object: {"topics": ["...", "..."]}.\n'
        "Rules for every topic:\n"
        "- Specific and curiosity-driven: a sharp question or a surprising claim, not a broad subject.\n"
        f"- At most {TOPIC_MAX_CHARS} characters, one line, no numbering, quotes, emojis or hashtags.\n"
        "- Filmable with stock footage: about real people, places, animals, objects, food, nature or "
        "everyday situations a camera can show.\n"
        "- Factually defensible; no invented statistics.\n"
        f"- No clickbait cliches or these phrases: {banned}.\n"
        "- Every topic clearly different from the others and from the already-covered list.\n"
        f"{style_line}"
    )
    recent = [a.strip()[:100] for a in avoid[-TOPIC_AVOID_IN_PROMPT:] if a.strip()]
    covered = ("\n\nAlready covered (do not repeat or rephrase these):\n"
               + "\n".join(f"- {a}" for a in recent)) if recent else ''
    user = f"Niche: {niche}\nWrite {count} topics.{covered}\n\nReturn the JSON now."
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


YOUTUBE_TITLE_MAX = 100
YOUTUBE_MAX_TAGS = 15
YOUTUBE_TAGS_TOTAL_MAX = 450  # YouTube rejects tag lists over ~500 chars in total


def _clean_youtube_text(text) -> str:
    """YouTube rejects '<' and '>' in titles and descriptions."""
    return re.sub(r"[<>]", "", str(text or "")).strip()


def _truncate_title(title: str, limit: int = YOUTUBE_TITLE_MAX) -> str:
    title = re.sub(r"\s+", " ", _clean_youtube_text(title)).strip().strip('"\'')
    if len(title) <= limit:
        return title
    cut = title[:limit - 1]
    if " " in cut[limit // 2:]:
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" ,;:-") + "\u2026"


def normalize_tags(tags, limit: int = YOUTUBE_MAX_TAGS,
                   total_max: int = YOUTUBE_TAGS_TOTAL_MAX) -> list:
    """Clean, de-duplicate (case-insensitive) and cap a tag list for YouTube."""
    if isinstance(tags, str):
        tags = tags.split(",")
    out, seen, total = [], set(), 0
    for tag in tags or []:
        if not isinstance(tag, str):
            continue
        tag = re.sub(r"\s+", " ", _clean_youtube_text(tag).lstrip("#")).strip(" ,")
        if not tag or len(tag) > 100 or tag.casefold() in seen:
            continue
        if total + len(tag) > total_max:
            break
        out.append(tag)
        seen.add(tag.casefold())
        total += len(tag)
        if len(out) >= limit:
            break
    return out


def _first_sentences(text: str, count: int = 2, max_chars: int = 400) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text or "").strip())
    result = " ".join(s for s in sentences[:count] if s).strip()
    if len(result) > max_chars:
        result = result[:max_chars - 1].rstrip() + "\u2026"
    return result


def fallback_publish_metadata(narration: str, topic: Optional[str] = None) -> dict:
    """Metadata used when the LLM is unavailable or returns unusable output."""
    words = (narration or "").split()
    title = topic or (" ".join(words[:10]) if words else "") or "New video"
    keywords = [
        w for w in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", topic or "")
        if w.lower() not in KEYWORD_STOPWORDS
    ]
    return {
        "title": _truncate_title(title) or "New video",
        "description": _clean_youtube_text(_first_sentences(narration)),
        "tags": normalize_tags(keywords),
    }


def create_planner(narration_style: str = "viral",
                   provider: Optional[str] = None) -> "ScenePlanner":
    """Build a ScenePlanner for the LLM provider configured in the environment.

    LLM_PROVIDER selects the backend: ollama (default), openrouter, anthropic,
    or claude-code. Each backend reads its own settings (see .env.example).
    """
    provider = (provider or os.getenv("LLM_PROVIDER") or "ollama").strip().lower()

    if provider == "openrouter":
        return ScenePlanner(
            provider="openrouter",
            model=os.getenv("OPENROUTER_MODEL") or "google/gemini-2.0-flash-001",
            base_url=os.getenv("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
            narration_style=narration_style,
        )
    if provider == "anthropic":
        return ScenePlanner(
            provider="anthropic",
            model=os.getenv("ANTHROPIC_MODEL") or "claude-opus-5-5",
            base_url="",
            api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            narration_style=narration_style,
        )
    if provider == "claude-code":
        return ScenePlanner(
            provider="claude-code",
            model=os.getenv("CLAUDE_CODE_MODEL") or "",
            base_url=os.getenv("CLAUDE_CODE_PATH") or "claude",
            narration_style=narration_style,
        )
    if provider != "ollama":
        raise ValueError(
            f"Unknown LLM_PROVIDER '{provider}'. Use ollama, openrouter, anthropic or claude-code."
        )
    return ScenePlanner(
        provider="ollama",
        model=os.getenv("OLLAMA_MODEL") or "qwen2.5:3b",
        base_url=os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434",
        narration_style=narration_style,
    )

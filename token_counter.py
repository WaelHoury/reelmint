"""Token usage tracking and cost estimation for LLM API calls."""

from typing import Dict, List, Optional


class TokenCounter:
    """Tracks token usage across LLM API calls and estimates costs.

    Designed for OpenRouter API which returns actual token counts.
    Pricing data from OpenRouter (per 1M tokens, USD).
    """

    # OpenRouter model pricing (per 1M tokens)
    # Updated: 2024 - check https://openrouter.ai/docs#models for latest
    PRICING = {
        # Google models
        "google/gemini-2.0-flash-001": {"input": 0.10, "output": 0.40},
        "google/gemini-2.0-flash-exp:free": {"input": 0.0, "output": 0.0},
        "google/gemini-flash-1.5": {"input": 0.075, "output": 0.30},
        "google/gemini-pro-1.5": {"input": 1.25, "output": 5.00},

        # Anthropic models
        "anthropic/claude-3.5-sonnet": {"input": 3.00, "output": 15.00},
        "anthropic/claude-3-haiku": {"input": 0.25, "output": 1.25},
        "anthropic/claude-3-opus": {"input": 15.00, "output": 75.00},

        # OpenAI models
        "openai/gpt-4o": {"input": 2.50, "output": 10.00},
        "openai/gpt-4o-mini": {"input": 0.15, "output": 0.60},
        "openai/gpt-4-turbo": {"input": 10.00, "output": 30.00},

        # Meta models
        "meta-llama/llama-3.1-8b-instruct": {"input": 0.055, "output": 0.055},
        "meta-llama/llama-3.1-70b-instruct": {"input": 0.35, "output": 0.40},
        "meta-llama/llama-3.1-405b-instruct": {"input": 2.70, "output": 2.70},

        # Mistral models
        "mistralai/mistral-7b-instruct": {"input": 0.055, "output": 0.055},
        "mistralai/mixtral-8x7b-instruct": {"input": 0.24, "output": 0.24},

        # DeepSeek models
        "deepseek/deepseek-chat": {"input": 0.14, "output": 0.28},
        "deepseek/deepseek-r1": {"input": 0.55, "output": 2.19},

        # Qwen models
        "qwen/qwen-2.5-72b-instruct": {"input": 0.35, "output": 0.40},
    }

    # Default pricing for unknown models (conservative estimate)
    DEFAULT_PRICING = {"input": 1.00, "output": 2.00}

    def __init__(self):
        """Initialize the token counter."""
        self._calls: List[Dict] = []
        self._total_input = 0
        self._total_output = 0

    def track_usage(self, prompt_tokens: int, completion_tokens: int,
                    model: str, stage: str = "unknown") -> None:
        """Record token usage from an API call.

        Args:
            prompt_tokens: Number of input/prompt tokens
            completion_tokens: Number of output/completion tokens
            model: Model identifier (e.g., "google/gemini-2.0-flash-001")
            stage: Description of the call stage (e.g., "narration", "clips")
        """
        self._calls.append({
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "model": model,
            "stage": stage,
        })
        self._total_input += prompt_tokens
        self._total_output += completion_tokens

    def get_total_tokens(self) -> Dict[str, int]:
        """Get total token counts.

        Returns:
            Dict with 'input', 'output', and 'total' keys
        """
        return {
            "input": self._total_input,
            "output": self._total_output,
            "total": self._total_input + self._total_output,
        }

    def get_cost_estimate(self, model: str) -> float:
        """Calculate estimated cost in USD.

        Args:
            model: Model identifier for pricing lookup

        Returns:
            Estimated cost in USD
        """
        pricing = self.PRICING.get(model, self.DEFAULT_PRICING)

        # Pricing is per 1M tokens
        input_cost = (self._total_input / 1_000_000) * pricing["input"]
        output_cost = (self._total_output / 1_000_000) * pricing["output"]

        return input_cost + output_cost

    def get_calls_by_stage(self) -> Dict[str, Dict[str, int]]:
        """Get token breakdown by stage.

        Returns:
            Dict mapping stage names to token counts
        """
        stages = {}
        for call in self._calls:
            stage = call["stage"]
            if stage not in stages:
                stages[stage] = {"input": 0, "output": 0}
            stages[stage]["input"] += call["prompt_tokens"]
            stages[stage]["output"] += call["completion_tokens"]
        return stages

    def format_report(self, model: str) -> str:
        """Generate a formatted token usage report.

        Args:
            model: Model identifier for pricing lookup

        Returns:
            Formatted string report
        """
        if not self._calls:
            return "=== TOKEN USAGE ===\nNo LLM calls recorded.\n==================="

        lines = ["", "=== LLM TOKEN USAGE ==="]

        # Per-stage breakdown
        stages = self.get_calls_by_stage()
        for stage, tokens in stages.items():
            stage_total = tokens["input"] + tokens["output"]
            lines.append(f"  {stage.capitalize():12} {tokens['input']:,} input / {tokens['output']:,} output ({stage_total:,})")

        # Totals
        totals = self.get_total_tokens()
        lines.append(f"  {'Total':12} {totals['input']:,} input / {totals['output']:,} output ({totals['total']:,})")

        # Cost estimate
        cost = self.get_cost_estimate(model)
        model_short = model.split("/")[-1] if "/" in model else model
        lines.append(f"  Cost:        ${cost:.4f} ({model_short})")

        lines.append("=======================")
        lines.append("")

        return "\n".join(lines)

    def reset(self) -> None:
        """Reset all tracked usage."""
        self._calls = []
        self._total_input = 0
        self._total_output = 0

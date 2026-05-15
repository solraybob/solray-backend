"""
ai/pricing.py — model pricing table used by the usage logger.

USD per MILLION tokens. cache_write and cache_read columns are Anthropic-
specific (prompt caching). For OpenAI models cache_* are 0 because OpenAI
does not bill for cache reads separately at this price tier.

Pricing version bumps when we update rates. The version is recorded on
each ApiUsage row so historical cost math stays stable when prices
change in the future.
"""

PRICING_VERSION = "2026-05-14"

# All values in USD per 1,000,000 tokens.
PRICING = {
    "claude-sonnet-4-6": {
        "input":           3.00,
        "output":         15.00,
        "cache_write":     3.75,  # 1.25x input for cache write
        "cache_read":      0.30,  # 10% of input for cache read
    },
    "claude-haiku-4-5-20251001": {
        "input":           0.80,
        "output":          4.00,
        "cache_write":     1.00,
        "cache_read":      0.08,
    },
    # GPT-4o family
    "gpt-4o": {
        "input":           2.50,
        "output":         10.00,
        "cache_write":     0.0,
        "cache_read":      0.0,
    },
    "gpt-4o-2024-08-06": {
        "input":           2.50,
        "output":         10.00,
        "cache_write":     0.0,
        "cache_read":      0.0,
    },
    "gpt-4o-mini": {
        "input":           0.15,
        "output":          0.60,
        "cache_write":     0.0,
        "cache_read":      0.0,
    },
    # Whisper bills per minute, not tokens. usage_logger treats Whisper
    # specially: input_tokens carries minutes * 1000 (scaled), output_tokens 0.
    "whisper-1": {
        "input":           6.00,   # $0.006 per minute = $6 per 1,000 minute-units
        "output":          0.0,
        "cache_write":     0.0,
        "cache_read":      0.0,
    },
}

# Fallback for unknown models. Conservative: assume mid-range pricing so
# we over-estimate rather than under-estimate cost.
DEFAULT_PRICING = {"input": 5.00, "output": 15.00, "cache_write": 6.25, "cache_read": 0.50}


def compute_cost_usd_micros(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> int:
    """Return cost in USD * 1_000_000 (micros), integer for precision.

    Uses provider-native token counts. Anthropic returns input_tokens that
    already EXCLUDES cached prefix; cache_read_tokens and cache_creation_tokens
    are billed separately. OpenAI returns input_tokens as the full prompt.
    """
    rates = PRICING.get(model, DEFAULT_PRICING)
    cost = (
        (input_tokens or 0) * rates["input"]
        + (output_tokens or 0) * rates["output"]
        + (cache_creation_tokens or 0) * rates["cache_write"]
        + (cache_read_tokens or 0) * rates["cache_read"]
    )
    # cost is now USD per 1,000,000 tokens worth, so cost / 1_000_000 = USD
    # Multiply by 1_000_000 to convert to micros for integer storage:
    # cost_in_usd = cost / 1_000_000
    # cost_in_micros = cost_in_usd * 1_000_000 = cost (same number)
    return int(round(cost))

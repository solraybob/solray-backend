"""
Memory synthesis eval fixture.

This is a real LLM call against the production synthesis prompt, run
against a fixed sample conversation. It exists to catch regressions in
the synthesis quality, not just the schema. Specifically: after this
conversation, the synthesizer should return useful, specific,
non-creepy memories including a `communication_style` entry.

Marked `slow` so it does not run on every `pytest` invocation. Run
with `pytest -m slow` and ensure ANTHROPIC_API_KEY is set in env.
Skipped automatically when the key is missing so CI without secrets
does not fail spuriously.
"""

import os

import pytest

from ai.chat import synthesize_memories


SAMPLE_BLUEPRINT = {
    "summary": {
        "sun_sign": "Virgo",
        "moon_sign": "Pisces",
        "ascendant": "Cancer",
        "hd_type": "Generator",
        "hd_authority": "Sacral",
        "hd_strategy": "Wait to Respond",
        "hd_profile": "2/4",
    },
    "human_design": {
        "type": "Generator",
        "authority": "Sacral",
        "profile": "2/4",
        "defined_centres": {"G": True, "Sacral": True, "Throat": True, "Heart": False},
        "defined_channels": [],
    },
    "astrology": {"natal": {"planets": {"Sun": {"sign": "Virgo"}, "Moon": {"sign": "Pisces"}}}},
    "gene_keys": {},
    "meta": {"name": "Sam"},
}


SAMPLE_CONVERSATION = [
    {
        "role": "user",
        "content": "I'm starting therapy next week. Honestly, I'm scared. My family never did this kind of thing growing up and I keep wondering if I'm being self-indulgent.",
    },
    {
        "role": "assistant",
        "content": "## Your Pisces Moon\n\nThe water you carry is deep, and the Cancer rising you wear is a tide that learns to hold. Therapy is not self-indulgent for someone built like you. It is the difference between letting the water move with care or holding the dam alone.\n\n*What does the part of you that calls it self-indulgent want you to do instead?*",
    },
    {
        "role": "user",
        "content": "Probably just keep working through it on my own. That's been my pattern. Read a book, journal about it, figure it out.",
    },
    {
        "role": "assistant",
        "content": "## The 2/4 design\n\nYou were built to spend time alone integrating before you bring something into a relational field. That impulse is real. And the Generator strategy is to wait to respond, which means: when therapy was offered or pulled toward you, did your body say yes, or did your mind argue itself into it?\n\n*Which one?*",
    },
    {
        "role": "user",
        "content": "Body said yes immediately. Mind has been arguing ever since.",
    },
]


def _content_lower(memories: list) -> str:
    """Concatenate all memory content lowercased for substring asserts."""
    return " ".join(m.get("content", "").lower() for m in memories if isinstance(m, dict))


@pytest.mark.slow
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set in env, skipping live eval",
)
def test_synthesize_returns_useful_specific_memories():
    """Feed the sample conversation to synthesize_memories and assert
    on shape + substance.

    Asserts intentionally focus on what we WOULD lose if synthesis
    quietly degraded: structure, presence of a communication_style
    entry, specific topical content from the conversation. The exact
    wording of each memory will vary across runs; we don't pin it.
    """
    result = synthesize_memories(SAMPLE_BLUEPRINT, SAMPLE_CONVERSATION, [])

    # Shape checks
    assert isinstance(result, list), f"expected list, got {type(result).__name__}"
    assert 1 <= len(result) <= 6, f"expected 1-6 memories, got {len(result)}"

    for m in result:
        assert isinstance(m, dict), f"memory entry should be a dict, got {type(m).__name__}"
        assert "category" in m and "content" in m, f"memory missing keys: {m}"
        assert isinstance(m["content"], str) and len(m["content"]) > 0
        # surface_next is optional but if present must be a bool
        if "surface_next" in m:
            assert isinstance(m["surface_next"], bool)

    # At least one communication_style entry. The synthesis prompt
    # explicitly instructs the model to include or update one after
    # every session. If this fails, the prompt has drifted.
    categories = {m.get("category") for m in result}
    assert "communication_style" in categories, \
        f"expected a communication_style memory, got categories={categories}"

    # Specific topical content from the conversation should appear
    # somewhere. We check a few low-precision substrings; any one is
    # enough to pass. If none appear, the synthesizer is being too
    # generic.
    blob = _content_lower(result)
    topical_hits = [
        "therapy" in blob,
        "self-indulgen" in blob,  # lemma
        "alone" in blob or "integrat" in blob,
        "body" in blob or "sacral" in blob,
        "generator" in blob or "2/4" in blob,
    ]
    assert any(topical_hits), \
        f"synthesis was too generic, no topical reference appeared in: {result}"

    # Non-creepy guardrail: synthesis must not invent personal facts
    # the user did not share. We don't test this exhaustively, but
    # check for common confabulation patterns.
    for m in result:
        content = m.get("content", "").lower()
        # User mentioned family but said nothing about specific names,
        # ages, traumas, or diagnoses. The synthesizer must not invent.
        assert "abuse" not in content, "synthesizer invented an abuse claim"
        assert "diagnosed" not in content, "synthesizer invented a diagnosis"

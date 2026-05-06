"""Static allowlist of model name patterns known to support OpenAI-style
function-calling reliably.

This is informational only: lillycoder does not refuse to talk to a model
that does not match. It just warns the user, since tool calls on a
non-trained model frequently hallucinate or fail to emit JSON.

Add a new pattern when you have verified end-to-end that the model
actually returns tool_calls in the streaming response.
"""
from __future__ import annotations

import re


# Substring patterns matched case-insensitively against the model name.
TOOL_CAPABLE_PATTERNS = [
    # Qwen 2.5 / 3.x families: native function-call training across all sizes
    r"qwen2\.5",
    r"qwen3",
    # Gemma 3 / 4 instruction-tuned
    r"gemma-?3",
    r"gemma-?4",
    # Llama 3.1+ instruct (3.1, 3.2, 3.3)
    r"llama-?3\.[123]",
    # Mistral small 3 (the 3.x family) and Mixtral with tool training
    r"mistral-small-3",
    r"mistral-nemo",
    # Cognitivecomputations Dolphin 3 R1 (R1 reasoning + tool emission OK)
    r"dolphin.*3\.0",
    r"dolphin.*r1",
    # Anthropic-shape tool-calling fine-tunes you may add
    r"hermes-3",
    r"firefunction",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in TOOL_CAPABLE_PATTERNS]


def is_tool_capable(model_name: str) -> bool:
    """Return True iff the model name matches a known tool-capable pattern."""
    return any(p.search(model_name) for p in _COMPILED)

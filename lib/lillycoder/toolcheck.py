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


CODING_TOOLS = [
    "read_file", "list_dir", "grep", "find", "write_file", "edit_file",
    "bash", "mkdir", "mv", "rm",
]
PERSONA_TOOLS = [
    "set_persona", "list_personas", "add_persona", "clone_persona",
    "set_active_persona", "set_evolve",
]


def _is_repository_persona_lookup(prompt: str) -> bool:
    """Distinguish source-code concepts from Lilly's runtime personas.

    Words such as "persona" and "personality" commonly name classes, config,
    fixtures, or character definitions inside the current project. A request
    to find definitions "here" should therefore stay on the coding tools,
    while ordinary requests to list, create, or switch Lilly's own personas
    can expose the persona-management tools.
    """
    lookup = re.search(
        r"\b(find|search|locate|grep|scan|inspect|defined|definition|"
        r"definitions|declared|implemented)\b",
        prompt,
    )
    project_scope = re.search(
        r"\b(here|repo|repository|project|codebase|workspace|directory|"
        r"folder|files?|source)\b",
        prompt,
    )
    return bool(lookup and project_scope)


def tools_for_prompt(prompt: str) -> list[str]:
    """Keep weak-model tool menus focused without removing capabilities.

    File and shell tools are always available. Package and persona tools are
    added only when the user's request mentions those domains. This cuts the
    normal coding menu from 17 schemas to 10 while preserving an escape path
    through direct slash commands.
    """
    lower = prompt.lower()
    chosen = list(CODING_TOOLS)
    if re.search(
        r"\b(install|package|dependency|dependencies|pip|npm|apt|yarn|pnpm)\b",
        lower,
    ):
        chosen.append("pkg_install")
    persona_mentioned = re.search(
        r"\b(persona|personality|personalities|evolve|tsundere|yandere)\b",
        lower,
    )
    if persona_mentioned and not _is_repository_persona_lookup(lower):
        chosen.extend(PERSONA_TOOLS)
    return chosen

"""Tool registry: schema (for the model) + dispatch (for execution).

We use the OpenAI-compatible function-calling shape because llama.cpp's
/v1/chat/completions speaks it for models that have native tool training
(qwen2.5+, qwen3, gemma3+). For models without native tool training we
fall back to a JSON-protocol prompt — see agent.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict           # JSON schema for the args
    handler: Callable[..., dict]  # called with **kwargs → returns result dict
    mutating: bool = False     # True ⇒ permission gate runs first
    safe_subset: bool = False  # for bash: only the allowlist subset
    danger: str = "low"        # low | medium | high — drives prompt urgency

    def schema(self) -> dict:
        """OpenAI function-calling shape."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    if tool.name in _REGISTRY:
        raise ValueError(f"tool {tool.name} already registered")
    _REGISTRY[tool.name] = tool
    return tool


def all_tools() -> list[Tool]:
    return list(_REGISTRY.values())


def by_name(name: str) -> Optional[Tool]:
    return _REGISTRY.get(name)


def schemas_for_model() -> list[dict]:
    return [t.schema() for t in all_tools()]


# --- import side effect: register every built-in tool ----------------------

from . import read, ls, grep, find, write, edit, bash, mkdir, mv, rm, pkg  # noqa

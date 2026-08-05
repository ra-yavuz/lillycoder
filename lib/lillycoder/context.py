"""Bounded context and durable conversation memory.

The full session stays on disk. The model receives a smaller working set:

* the active persona plus a structured memory checkpoint;
* the most recent conversation turns verbatim;
* bounded tool traces, with bulky mechanical payloads cleared after use.

This is deliberately closer to virtual memory than an ever-growing chat log.
Compaction can use the active model for a factual summary, but always has a
deterministic local fallback so a full or unavailable endpoint cannot prevent
recovery.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

import httpx


MEMORY_START = "\n\n<lilly_memory>\n"
MEMORY_END = "\n</lilly_memory>"
DEFAULT_WORKING_WINDOW = 32768


def safe_working_window(reported: Optional[int]) -> int:
    """Bound the live working set even when a server advertises 128K or 256K.

    Very large trained contexts are capabilities, not evidence that the local
    machine can afford a matching KV cache during a long tool session.
    """
    if not isinstance(reported, int) or reported <= 0:
        return 8192
    return min(reported, DEFAULT_WORKING_WINDOW)


def split_system_memory(content: str) -> tuple[str, str]:
    """Return the persona portion and embedded memory portion."""
    if MEMORY_START not in content:
        return content, ""
    base, rest = content.split(MEMORY_START, 1)
    memory = rest.split(MEMORY_END, 1)[0]
    return base.rstrip(), memory.strip()


def system_with_memory(system_prompt: str, memory: str) -> str:
    """Build one system message.

    Some local chat templates reject a second system-role message, so memory
    is embedded into the first message instead of being inserted as another
    system message later in the list.
    """
    base, _old = split_system_memory(system_prompt)
    if not memory.strip():
        return base
    clean = memory.replace(MEMORY_START.strip(), "").replace(
        MEMORY_END.strip(), ""
    ).strip()
    return f"{base}{MEMORY_START}{clean}{MEMORY_END}"


def memory_from_messages(messages: list[dict]) -> str:
    if not messages or messages[0].get("role") != "system":
        return ""
    _base, memory = split_system_memory(str(messages[0].get("content") or ""))
    return memory


def _serialized_chars(value) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    except (TypeError, ValueError):
        return len(str(value))


def estimate_messages(messages: list[dict]) -> float:
    """Conservative token estimate for OpenAI-style messages."""
    chars = 0
    for message in messages:
        chars += _serialized_chars(message.get("content"))
        chars += 48  # role, separators, and chat-template framing
        for call in message.get("tool_calls", []) or []:
            chars += _serialized_chars(call)
    return chars / 4.0


def estimate_tools(tools: Optional[list[dict]]) -> float:
    return _serialized_chars(tools or []) / 4.0


def bound_json_value(value, limit: int):
    """Recursively bound a JSON value while keeping useful identifiers."""
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        kept = max(80, limit - 80)
        return value[:kept] + f"\n...[{len(value) - kept} chars cleared from context]"
    if isinstance(value, list):
        kept_items = [bound_json_value(item, max(120, limit // 12)) for item in value[:20]]
        if len(value) > 20:
            kept_items.append(f"...[{len(value) - 20} items cleared from context]")
        return kept_items
    if isinstance(value, dict):
        out = {}
        per_value = max(160, limit // max(1, min(10, len(value))))
        for key, item in value.items():
            out[key] = bound_json_value(item, per_value)
        return out
    return value


def trim_tool_payloads(messages: list[dict], recent_results: int = 2) -> bool:
    """Clear bulky tool mechanics while preserving protocol structure.

    Tool calls and tool result messages remain paired by id. Only their large
    arguments and outputs are shortened. The latest two results retain more
    detail because the model is most likely to act on them next.
    """
    changed = False
    tool_indexes = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    recent = set(tool_indexes[-recent_results:])

    for index, message in enumerate(messages):
        if message.get("role") == "assistant" and message.get("tool_calls"):
            for call in message.get("tool_calls") or []:
                fn = call.get("function") or {}
                raw_args = fn.get("arguments")
                if not isinstance(raw_args, str) or len(raw_args) <= 2400:
                    continue
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {"arguments_preview": raw_args}
                bounded = bound_json_value(args, 1800)
                if isinstance(bounded, dict):
                    bounded["_context_note"] = "large tool arguments cleared after execution"
                fn["arguments"] = json.dumps(bounded, ensure_ascii=False)
                changed = True

        if message.get("role") != "tool":
            continue
        content = message.get("content") or ""
        limit = 6000 if index in recent else 1400
        if _serialized_chars(content) <= limit:
            continue
        try:
            parsed = json.loads(content) if isinstance(content, str) else content
        except json.JSONDecodeError:
            parsed = {"output": content}
        bounded = bound_json_value(parsed, limit - 160)
        if isinstance(bounded, dict):
            bounded["_context_truncated"] = True
            bounded["_context_note"] = (
                "full tool output was cleared after use; rerun a narrow read or search if needed"
            )
        message["content"] = json.dumps(bounded, ensure_ascii=False)
        changed = True
    return changed


def _text_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(str(part.get("text") or ""))
            elif isinstance(part, dict):
                texts.append(f"[{part.get('type', 'attachment')} omitted]")
        return "\n".join(texts)
    return str(content or "")


def _render_history(messages: list[dict], max_chars: int) -> str:
    lines = []
    for message in messages:
        role = message.get("role", "?")
        content = _text_content(message.get("content"))
        if role == "tool":
            label = "tool result"
            limit = 600
        elif role == "assistant" and message.get("tool_calls"):
            names = [
                call.get("function", {}).get("name", "?")
                for call in message.get("tool_calls") or []
            ]
            label = f"assistant called {', '.join(names)}"
            limit = 800
        else:
            label = role
            limit = 1400
        if len(content) > limit:
            content = content[:limit] + "...[trimmed]"
        lines.append(f"[{label}] {content}")
    rendered = "\n".join(lines)
    if len(rendered) <= max_chars:
        return rendered
    # Keep both the beginning (initial requirements) and the end (latest
    # decisions) when the summarizer input itself needs a hard bound.
    head = max_chars // 3
    tail = max_chars - head
    return rendered[:head] + "\n...[middle omitted]...\n" + rendered[-tail:]


def _fallback_summary(previous_memory: str, middle: list[dict]) -> str:
    rendered = _render_history(middle, max_chars=7000)
    parts = []
    if previous_memory:
        parts.append("Previous durable memory:\n" + previous_memory[:5000])
    if rendered:
        parts.append("Archived conversation checkpoint:\n" + rendered)
    parts.append(
        "The lossless original conversation remains in the session archive. "
        "Ask for a narrow file read or search instead of guessing missing details."
    )
    return "\n\n".join(parts)


@dataclass
class ContextTracker:
    model_window: int = 8192
    estimated: float = 0.0
    compactions: int = 0
    last_compaction_used_fallback: bool = False
    last_compaction_kind: str = "none"

    @property
    def window(self) -> int:
        return self.model_window

    def estimate(self, messages: list[dict]) -> float:
        return estimate_messages(messages)

    def request_estimate(self, messages: list[dict],
                         tools: Optional[list[dict]] = None) -> float:
        return estimate_messages(messages) + estimate_tools(tools)

    def refresh(self, messages: list[dict]) -> None:
        self.estimated = self.estimate(messages)

    def percent(self) -> float:
        return min(100.0, 100.0 * self.estimated / max(1, self.window))

    def request_percent(self, messages: list[dict],
                        tools: Optional[list[dict]] = None) -> float:
        return min(
            100.0,
            100.0 * self.request_estimate(messages, tools) / max(1, self.window),
        )

    def needs_compaction(self, messages: list[dict],
                         tools: Optional[list[dict]] = None,
                         reserve_tokens: int = 2048) -> bool:
        """Leave room for tokenization error, output, and another tool cycle."""
        prompt = self.request_estimate(messages, tools)
        return prompt >= self.window * 0.72 or (
            prompt + reserve_tokens >= self.window * 0.88
        )

    def replace_system_prompt(self, messages: list[dict],
                              system_prompt: str) -> None:
        """Change the persona without discarding compacted memory."""
        memory = memory_from_messages(messages)
        content = system_with_memory(system_prompt, memory)
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = content
        else:
            messages.insert(0, {"role": "system", "content": content})
        self.refresh(messages)

    def compact(self, messages: list[dict], system_prompt: str,
                client: Optional[httpx.Client], model,
                keep_last_turns: int = 2,
                use_model: bool = True) -> bool:
        """Compact older turns and bound tool payloads in place.

        Returns True when the working context changed. Model summarization is
        best-effort. Any HTTP, decoding, or empty-response failure falls back
        to a deterministic local checkpoint.
        """
        changed = trim_tool_payloads(messages)
        if not messages:
            return changed
        current_system = str(messages[0].get("content") or system_prompt)
        _base, previous_memory = split_system_memory(current_system)
        history = messages[1:]
        user_positions = [
            i for i, message in enumerate(history)
            if message.get("role") == "user"
        ]
        if len(user_positions) <= 1:
            if changed:
                self.compactions += 1
                self.last_compaction_kind = "bounded tool trace"
                self.refresh(messages)
            return changed

        # Always leave at least one complete older turn available to summarize.
        keep = min(max(1, keep_last_turns), len(user_positions) - 1)
        tail_start = user_positions[-keep]
        middle = history[:tail_start]
        tail = history[tail_start:]
        if not middle:
            if changed:
                self.compactions += 1
                self.last_compaction_kind = "bounded tool trace"
                self.refresh(messages)
            return changed

        max_log_chars = min(32000, max(6000, self.window * 2))
        log_text = _render_history(middle, max_chars=max_log_chars)
        if previous_memory:
            log_text = (
                "[previous durable memory]\n" + previous_memory +
                "\n[end previous memory]\n" + log_text
            )

        summary = ""
        used_fallback = True
        if use_model and client is not None:
            prompt = (
                "Create a compact, factual memory checkpoint for a coding "
                "assistant. Preserve user goals and preferences, decisions and "
                "their reasons, file paths and symbols touched, commands and "
                "test results, unresolved work, and important conversational "
                "facts. Do not invent details. Drop greetings, repetition, raw "
                "file bodies, and mechanical tool output. Use concise sections "
                "and stay under 900 words.\n\n" + log_text
            )
            payload = {
                "model": model.alias,
                "messages": [
                    {"role": "system", "content": "Write factual memory checkpoints only."},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "temperature": 0.1,
                "max_tokens": 1200,
            }
            try:
                response = client.post("/chat/completions", json=payload, timeout=120)
                response.raise_for_status()
                summary = str(
                    response.json()["choices"][0]["message"]["content"] or ""
                ).strip()
                used_fallback = not bool(summary)
            except Exception:
                summary = ""
        if not summary:
            summary = _fallback_summary(previous_memory, middle)

        new_messages = [{
            "role": "system",
            "content": system_with_memory(system_prompt, summary),
        }, *tail]
        messages.clear()
        messages.extend(new_messages)
        self.compactions += 1
        self.last_compaction_used_fallback = used_fallback
        self.last_compaction_kind = (
            "local fallback" if used_fallback else "model summary"
        )
        self.refresh(messages)
        return True

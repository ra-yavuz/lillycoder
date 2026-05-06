"""Context window tracker + auto-compact.

Token estimation is rough (chars/4 heuristic) — good enough to display a
percentage indicator and trigger compaction near the limit. We don't have
a real tokeniser available without pulling in tiktoken/transformers, both
of which inflate install size.

Compaction strategy:
  - Keep the system message verbatim.
  - Keep the most recent N (default 6) user/assistant pairs verbatim.
  - Summarise everything older into a single system note via the model.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

import httpx


@dataclass
class ContextTracker:
    model_window: int = 8192     # conservative default; bumped per model below
    estimated: float = 0.0       # most recent estimate

    @property
    def window(self) -> int:
        return self.model_window

    def estimate(self, messages: list[dict]) -> float:
        total = 0
        for m in messages:
            content = m.get("content") or ""
            if isinstance(content, str):
                total += len(content)
            for tc in m.get("tool_calls", []) or []:
                fn = tc.get("function") or {}
                total += len(fn.get("name", "") or "")
                total += len(fn.get("arguments", "") or "")
            if m.get("role") == "tool":
                total += len(m.get("content", "") or "")
        return total / 4.0  # rough char→token

    def refresh(self, messages: list[dict]) -> None:
        self.estimated = self.estimate(messages)

    def percent(self) -> float:
        return min(100.0, 100.0 * self.estimated / max(1, self.window))

    def compact(self, messages: list[dict], system_prompt: str,
                client: httpx.Client, model, keep_last_pairs: int = 6) -> None:
        """Mutate `messages` in place: replace the older middle with a summary."""
        # Identify the slice to summarise: everything between system[0]
        # and the last 2*keep_last_pairs role-bearing turns.
        if len(messages) <= 1 + 2 * keep_last_pairs:
            return  # not enough to compact
        head = messages[0:1]      # system
        tail = messages[-(2 * keep_last_pairs):]
        middle = messages[1:-(2 * keep_last_pairs)]
        if not middle:
            return

        # Render middle as a simple text log for summarisation.
        log_lines = []
        for m in middle:
            role = m.get("role", "?")
            content = m.get("content", "") or ""
            if role == "tool":
                log_lines.append(f"[tool result] {content[:300]}")
            elif role == "assistant" and m.get("tool_calls"):
                names = [tc.get("function", {}).get("name", "?")
                         for tc in m["tool_calls"]]
                log_lines.append(f"[lilly called: {', '.join(names)}] {content or ''}"[:400])
            else:
                log_lines.append(f"[{role}] {content[:400]}")
        log_text = "\n".join(log_lines)

        prompt = (
            "Summarise the conversation log below into a compact note for "
            "future reference. Preserve: file paths touched, commands run, "
            "decisions made, unresolved threads. Drop niceties and "
            "repetition. Keep under 600 words.\n\n"
            "=== conversation log ===\n" + log_text
        )
        payload = {
            "model": model.alias,
            "messages": [
                {"role": "system", "content": "You write tight, factual summaries."},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "temperature": 0.2,
        }
        r = client.post("/chat/completions", json=payload, timeout=120)
        r.raise_for_status()
        summary = r.json()["choices"][0]["message"]["content"]

        new_msgs = head + [{
            "role": "system",
            "content": (
                "[earlier conversation summary]\n" + summary +
                "\n[end summary — recent turns follow]"
            ),
        }] + tail
        messages.clear()
        messages.extend(new_msgs)
        self.refresh(messages)

"""Minimal, explicit context construction for a worker turn.

Keeps the two responsibilities that belong together: assembling the
provider message list (system + user + bounded tool-result history) and
measuring what the turn actually consumed (selected tool count, schema
bytes, an explicit character estimate of prompt size against the profile's
context budget). The estimate is chars, clearly labeled — it is never
presented as a real token count, which only a tokenizer could produce.

Tool-result history stays coherent: a tool turn is appended as the
assistant ``tool_calls`` message followed by one ``tool`` message per call,
and the window is trimmed in whole turns (never mid-turn) so the provider
never sees a tool result without its corresponding assistant tool call.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from meizex_mrw.profiles.schema import ExecutionProfile
from meizex_mrw.providers.base import ChatMessage, ToolSpec


class ContextTelemetry(BaseModel):
    selected_tool_count: int = Field(ge=0)
    tool_schema_bytes: int = Field(ge=0)
    prompt_size_estimate_chars: int = Field(ge=0)
    context_budget: int = Field(ge=0)
    budget_respected: bool


class BuiltContext(BaseModel):
    messages: list[ChatMessage]
    telemetry: ContextTelemetry


class ContextBuilder:
    def __init__(
        self,
        *,
        system_instruction: str,
        chars_per_token_estimate: int = 4,
        tool_result_max_chars: int = 2000,
        max_tool_result_turns: int = 4,
    ) -> None:
        self.system_instruction = system_instruction
        self.chars_per_token_estimate = max(1, chars_per_token_estimate)
        self.tool_result_max_chars = max(100, tool_result_max_chars)
        self.max_tool_result_turns = max(1, max_tool_result_turns)

    def build(
        self,
        *,
        user_request: str,
        profile: ExecutionProfile,
        tools: list[ToolSpec],
    ) -> BuiltContext:
        messages = [
            ChatMessage(role="system", content=self.system_instruction),
            ChatMessage(role="user", content=user_request),
        ]
        schema_bytes = sum(
            len(json.dumps(spec.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")))
            for spec in tools
        )
        estimate_chars = sum(len(msg.content or "") for msg in messages) + schema_bytes
        budget_chars = profile.context_budget * self.chars_per_token_estimate
        return BuiltContext(
            messages=messages,
            telemetry=ContextTelemetry(
                selected_tool_count=len(tools),
                tool_schema_bytes=schema_bytes,
                prompt_size_estimate_chars=estimate_chars,
                context_budget=budget_chars,
                budget_respected=estimate_chars <= budget_chars,
            ),
        )

    def append_tool_turn(
        self,
        messages: list[ChatMessage],
        calls_results: list[tuple[Any, str]],
    ) -> tuple[list[ChatMessage], dict[str, int]]:
        """Append one assistant tool-call message plus its tool results.

        ``calls_results`` is a list of ``(ToolCall, result_text)`` pairs in
        provider order. Returns (new_messages, stats) where stats counts how
        many results were truncated and how many whole tool turns were
        dropped to respect the bounded window.
        """
        new_messages = list(messages)
        new_messages.append(
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments},
                    }
                    for call, _ in calls_results
                ],
            )
        )
        truncated = 0
        for call, result in calls_results:
            text = result or ""
            if len(text) > self.tool_result_max_chars:
                text = text[: self.tool_result_max_chars] + "\n...[truncated]"
                truncated += 1
            new_messages.append(ChatMessage(role="tool", content=text, tool_call_id=call.id))
        trimmed, dropped = self._trim_tool_history(new_messages, self.max_tool_result_turns)
        return trimmed, {"truncated_results": truncated, "dropped_tool_turns": dropped}

    def append_user_note(self, messages: list[ChatMessage], note: str) -> list[ChatMessage]:
        """Append an advisory user message (used for verification retries)."""
        return list(messages) + [ChatMessage(role="user", content=note)]

    @staticmethod
    def _trim_tool_history(
        messages: list[ChatMessage], max_turns: int
    ) -> tuple[list[ChatMessage], int]:
        if len(messages) <= 2:
            return messages, 0
        prefix = list(messages[:2])
        groups: list[list[ChatMessage]] = []
        rest = list(messages[2:])
        index = 0
        while index < len(rest):
            msg = rest[index]
            if msg.role == "assistant" and msg.tool_calls:
                group = [msg]
                index += 1
                while index < len(rest) and rest[index].role == "tool":
                    group.append(rest[index])
                    index += 1
                groups.append(group)
            else:
                groups.append([msg])
                index += 1
        turn_indices = [
            idx
            for idx, group in enumerate(groups)
            if group and group[0].role == "assistant" and group[0].tool_calls
        ]
        dropped = 0
        if len(turn_indices) > max_turns:
            drop = set(turn_indices[: len(turn_indices) - max_turns])
            groups = [group for idx, group in enumerate(groups) if idx not in drop]
            dropped = len(drop)
        out = prefix
        for group in groups:
            out.extend(group)
        return out, dropped

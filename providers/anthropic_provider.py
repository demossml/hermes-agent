"""
Native Anthropic SDK provider for Hermes Agent.

Replaces OpenRouter proxy with direct anthropic.AsyncAnthropic() calls.
Supports tool_use, streaming, and extended thinking.

Usage:
    from providers.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(model="claude-sonnet-4-20250514")
    response = await provider.complete(
        messages=[{"role": "user", "content": "Hello"}],
        system="You are helpful.",
        tools=[...],
        stream=False,
    )
"""

import os
import json
import logging
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

# Anthropic models that support extended thinking
_THINKING_MODELS = {
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
}

# Models that require streaming for tool_use (Claude 4+)
_STREAMING_TOOL_MODELS = {
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
}


class AnthropicProvider:
    """Native Anthropic API provider with tool_use and streaming support."""

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        max_tokens: int = 8096,
        thinking_budget: int | None = None,
    ):
        api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY not set. Export it or pass api_key=..."
            )

        self.model = model
        self.max_tokens = max_tokens
        self.thinking_budget = thinking_budget

        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self.client = anthropic.AsyncAnthropic(**client_kwargs)

    def _needs_streaming_for_tools(self, tools: list | None) -> bool:
        """Claude 4+ models require streaming for tool_use."""
        return bool(tools) and self.model in _STREAMING_TOOL_MODELS

    def _build_kwargs(
        self,
        messages: list[dict],
        system: str = "",
        tools: list | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        """Build kwargs for the Anthropic API call."""
        # Convert OpenAI-format messages to Anthropic format
        anthropic_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                # System messages go into the system parameter, not messages
                if system:
                    system = f"{system}\n\n{content}"
                else:
                    system = content
                continue

            if role == "assistant":
                anthropic_messages.append({"role": "assistant", "content": content})
            elif role == "tool":
                anthropic_messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id", ""),
                            "content": content,
                        }
                    ],
                })
            else:
                anthropic_messages.append({"role": "user", "content": content})

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": anthropic_messages,
            "temperature": temperature,
        }

        if system:
            kwargs["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]

        if tools:
            anthropic_tools = []
            for tool in tools:
                if isinstance(tool, dict):
                    func = tool.get("function", tool)
                    anthropic_tools.append({
                        "name": func.get("name", ""),
                        "description": func.get("description", ""),
                        "input_schema": func.get("parameters", {}),
                    })
            if anthropic_tools:
                kwargs["tools"] = anthropic_tools

        # Extended thinking for supported models
        if self.model in _THINKING_MODELS and self.thinking_budget:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.thinking_budget,
            }

        return kwargs

    async def complete(
        self,
        messages: list[dict],
        system: str = "",
        tools: list | None = None,
        stream: bool = False,
        temperature: float = 0.7,
    ) -> "AnthropicResponse":
        """Send a completion request to Anthropic.

        Returns AnthropicResponse with .content, .tool_calls, .stop_reason.
        """
        kwargs = self._build_kwargs(messages, system, tools, temperature)

        if stream or self._needs_streaming_for_tools(tools):
            return await self._stream_complete(kwargs)

        try:
            response = await self.client.messages.create(**kwargs)
            return AnthropicResponse.from_raw(response)
        except anthropic.APIError as e:
            logger.error(f"Anthropic API error: {e}")
            raise

    async def _stream_complete(self, kwargs: dict) -> "AnthropicResponse":
        """Handle streaming completion, collecting tool_use blocks."""
        collected_content: list[str] = []
        tool_use_blocks: list[dict] = []
        stop_reason = "end_turn"
        current_tool: dict | None = None

        async with self.client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if event.type == "content_block_start":
                    if event.content_block.type == "tool_use":
                        current_tool = {
                            "id": event.content_block.id,
                            "name": event.content_block.name,
                            "input": "",
                        }
                elif event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        collected_content.append(event.delta.text)
                    elif event.delta.type == "input_json_delta" and current_tool is not None:
                        current_tool["input"] += event.delta.partial_json
                elif event.type == "content_block_stop":
                    if current_tool is not None:
                        try:
                            current_tool["input"] = json.loads(current_tool["input"])
                        except json.JSONDecodeError:
                            pass
                        tool_use_blocks.append(current_tool)
                        current_tool = None
                elif event.type == "message_delta":
                    stop_reason = event.delta.stop_reason or stop_reason

            final_message = await stream.get_final_message()
            if final_message:
                stop_reason = final_message.stop_reason or stop_reason

        return AnthropicResponse(
            content="".join(collected_content),
            tool_calls=tool_use_blocks,
            stop_reason=stop_reason,
        )


class AnthropicResponse:
    """Normalized response from Anthropic provider."""

    def __init__(self, content: str, tool_calls: list[dict] | None = None, stop_reason: str = "end_turn"):
        self.content = content
        self.tool_calls = tool_calls or []
        self.stop_reason = stop_reason

    @classmethod
    def from_raw(cls, response) -> "AnthropicResponse":
        """Parse raw anthropic.types.Message into AnthropicResponse."""
        content_parts: list[str] = []
        tool_calls: list[dict] = []

        for block in response.content:
            if block.type == "text":
                content_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        return cls(
            content="\n".join(content_parts),
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "end_turn",
        )

    @property
    def text(self) -> str:
        return self.content

    def __repr__(self) -> str:
        return f"AnthropicResponse(content='{self.content[:50]}...', tool_calls={len(self.tool_calls)})"

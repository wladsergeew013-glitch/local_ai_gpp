"""Text and function-call subset of the OpenAI chat-completions wire format."""

from __future__ import annotations
import json
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator
from backend.app.tool_calls import validate_transcript


class FunctionCall(BaseModel):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$")
    arguments: str

    @field_validator("arguments")
    @classmethod
    def object_arguments(cls, value):
        if not isinstance(json.loads(value), dict):
            raise ValueError("Tool call arguments must encode a JSON object.")
        return value


class ToolCall(BaseModel):
    id: str = Field(min_length=1, max_length=256)
    type: Literal["function"] = "function"
    function: FunctionCall


class OpenAIMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    reasoning_content: str | None = None

    @field_validator("content", mode="before")
    @classmethod
    def text_blocks(cls, value):
        if isinstance(value, list):
            if not all(
                isinstance(item, dict)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
                for item in value
            ):
                raise ValueError(
                    "This runtime supports text inputs only; image/audio blocks are unsupported."
                )
            return "\n".join(item["text"] for item in value)
        return value

    @model_validator(mode="after")
    def coherent(self):
        if self.content is None and not (self.role == "assistant" and self.tool_calls):
            raise ValueError(
                "content is required except for assistant tool-call messages."
            )
        if self.role == "tool" and (not self.tool_call_id or self.tool_calls):
            raise ValueError(
                "A tool result requires tool_call_id and may not contain tool_calls."
            )
        if self.role != "tool" and self.tool_call_id:
            raise ValueError("tool_call_id is only valid on tool results.")
        if self.role != "assistant" and self.tool_calls:
            raise ValueError("Only assistant messages may contain tool_calls.")
        return self


class ToolFunction(BaseModel):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$")
    description: str = ""
    parameters: dict = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )


class ToolDefinition(BaseModel):
    type: Literal["function"] = "function"
    function: ToolFunction


class OpenAIChatRequest(BaseModel):
    model: str = Field(min_length=1)
    messages: list[OpenAIMessage] = Field(min_length=1, max_length=8192)
    tools: list[ToolDefinition] = Field(default_factory=list, max_length=128)
    tool_choice: Literal["auto", "none", "required"] | dict = "auto"
    parallel_tool_calls: bool = True
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=256, ge=1, le=32768)
    stream: bool = False
    memory: bool = True
    stream_options: dict = Field(default_factory=dict)
    chat_template_kwargs: dict[str, bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def transcript(self):
        messages = [message.model_dump(exclude_none=True) for message in self.messages]
        tools = [tool.model_dump() for tool in self.tools]
        validate_transcript(messages, tools)
        if isinstance(self.tool_choice, dict):
            if (
                set(self.tool_choice) != {"type", "function"}
                or self.tool_choice.get("type") != "function"
            ):
                raise ValueError("tool_choice must name a function.")
            function = self.tool_choice.get("function")
            if (
                not isinstance(function, dict)
                or set(function) != {"name"}
                or function["name"] not in {t.function.name for t in self.tools}
            ):
                raise ValueError("tool_choice must name one offered tool.")
        if not self.tools and self.tool_choice not in ("none", "auto"):
            raise ValueError("A required tool_choice needs tools.")
        if not self.memory and (
            self.tools or any(m.role == "tool" or m.tool_calls for m in self.messages)
        ):
            raise ValueError(
                "Tool turns require memory=true to preserve call/result linkage."
            )
        return self

"""Stable Agent event DTOs for TradeCat (tui-service).

The adapter layer owns the mapping from upstream gateway/provider payloads into these
DTOs. TUI, logs, and session replay should depend only on the normalized fields
defined here, never on upstream payload structure.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, TypeAlias

JsonPrimitive: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

EventFamily: TypeAlias = Literal["session", "assistant", "tool", "system"]
AgentEventType: TypeAlias = Literal[
    "session.start",
    "session.user_message",
    "session.end",
    "assistant.delta",
    "assistant.message",
    "tool.start",
    "tool.update",
    "tool.end",
    "tool.error",
    "system.transport",
    "system.fallback",
    "system.log",
]

EVENT_FAMILIES: tuple[EventFamily, ...] = ("session", "assistant", "tool", "system")
EVENT_TYPES: tuple[AgentEventType, ...] = (
    "session.start",
    "session.user_message",
    "session.end",
    "assistant.delta",
    "assistant.message",
    "tool.start",
    "tool.update",
    "tool.end",
    "tool.error",
    "system.transport",
    "system.fallback",
    "system.log",
)


def _strip_none(value: Any) -> Any:
    """Recursively remove keys whose value is None from nested dict payloads."""

    if isinstance(value, dict):
        return {key: _strip_none(val) for key, val in value.items() if val is not None}
    if isinstance(value, list):
        return [_strip_none(item) for item in value]
    return value


@dataclass(frozen=True, kw_only=True)
class _ToDictMixin:
    """Serialize dataclass DTOs into JSON-safe dict payloads."""

    def to_dict(self) -> JsonObject:
        data = asdict(self)
        return _strip_none(data)


@dataclass(frozen=True, kw_only=True)
class AgentError(_ToDictMixin):
    """Normalized error structure for ``tool.error`` and ``system.transport``."""

    message: str
    code: str | None = None
    type: str | None = None
    retryable: bool | None = None
    detail: JsonObject | None = None


@dataclass(frozen=True, kw_only=True)
class SessionStartEvent(_ToDictMixin):
    ts: float
    event_id: str
    session_id: str
    turn_id: str | None = None
    source: str | None = None
    raw: JsonObject | None = None
    meta: JsonObject | None = None
    config: JsonObject | None = None

    v: Literal[1] = field(default=1, init=False)
    type: Literal["session.start"] = field(default="session.start", init=False)


@dataclass(frozen=True, kw_only=True)
class SessionUserMessageEvent(_ToDictMixin):
    ts: float
    event_id: str
    session_id: str
    message_id: str
    content: str
    turn_id: str | None = None
    source: str | None = None
    raw: JsonObject | None = None
    meta: JsonObject | None = None

    v: Literal[1] = field(default=1, init=False)
    type: Literal["session.user_message"] = field(default="session.user_message", init=False)


@dataclass(frozen=True, kw_only=True)
class SessionEndEvent(_ToDictMixin):
    ts: float
    event_id: str
    session_id: str
    turn_id: str | None = None
    source: str | None = None
    raw: JsonObject | None = None
    meta: JsonObject | None = None
    reason: str | None = None

    v: Literal[1] = field(default=1, init=False)
    type: Literal["session.end"] = field(default="session.end", init=False)


@dataclass(frozen=True, kw_only=True)
class AssistantDeltaEvent(_ToDictMixin):
    ts: float
    event_id: str
    session_id: str
    message_id: str
    delta: str
    turn_id: str | None = None
    source: str | None = None
    raw: JsonObject | None = None
    meta: JsonObject | None = None

    v: Literal[1] = field(default=1, init=False)
    type: Literal["assistant.delta"] = field(default="assistant.delta", init=False)


@dataclass(frozen=True, kw_only=True)
class AssistantMessageEvent(_ToDictMixin):
    ts: float
    event_id: str
    session_id: str
    message_id: str
    content: str
    turn_id: str | None = None
    source: str | None = None
    raw: JsonObject | None = None
    meta: JsonObject | None = None
    finish_reason: str | None = None
    usage: JsonObject | None = None

    v: Literal[1] = field(default=1, init=False)
    type: Literal["assistant.message"] = field(default="assistant.message", init=False)


@dataclass(frozen=True, kw_only=True)
class ToolStartEvent(_ToDictMixin):
    ts: float
    event_id: str
    session_id: str
    tool_call_id: str
    tool_name: str
    turn_id: str | None = None
    source: str | None = None
    raw: JsonObject | None = None
    meta: JsonObject | None = None
    args: JsonObject | None = None
    timeout_s: float | None = None

    v: Literal[1] = field(default=1, init=False)
    type: Literal["tool.start"] = field(default="tool.start", init=False)


@dataclass(frozen=True, kw_only=True)
class ToolUpdateEvent(_ToDictMixin):
    ts: float
    event_id: str
    session_id: str
    tool_call_id: str
    tool_name: str
    turn_id: str | None = None
    source: str | None = None
    raw: JsonObject | None = None
    meta: JsonObject | None = None
    message: str | None = None
    progress: float | None = None
    partial_output: JsonObject | None = None

    v: Literal[1] = field(default=1, init=False)
    type: Literal["tool.update"] = field(default="tool.update", init=False)


@dataclass(frozen=True, kw_only=True)
class ToolEndEvent(_ToDictMixin):
    ts: float
    event_id: str
    session_id: str
    tool_call_id: str
    tool_name: str
    turn_id: str | None = None
    source: str | None = None
    raw: JsonObject | None = None
    meta: JsonObject | None = None
    output: JsonObject | None = None
    elapsed_s: float | None = None

    v: Literal[1] = field(default=1, init=False)
    type: Literal["tool.end"] = field(default="tool.end", init=False)


@dataclass(frozen=True, kw_only=True)
class ToolErrorEvent(_ToDictMixin):
    ts: float
    event_id: str
    session_id: str
    tool_call_id: str
    tool_name: str
    error: AgentError
    turn_id: str | None = None
    source: str | None = None
    raw: JsonObject | None = None
    meta: JsonObject | None = None
    elapsed_s: float | None = None

    v: Literal[1] = field(default=1, init=False)
    type: Literal["tool.error"] = field(default="tool.error", init=False)


@dataclass(frozen=True, kw_only=True)
class SystemTransportEvent(_ToDictMixin):
    ts: float
    event_id: str
    session_id: str
    component: str
    operation: str
    state: str
    turn_id: str | None = None
    source: str | None = None
    raw: JsonObject | None = None
    meta: JsonObject | None = None
    attempt: int | None = None
    timeout_s: float | None = None
    elapsed_s: float | None = None
    error: AgentError | None = None

    v: Literal[1] = field(default=1, init=False)
    type: Literal["system.transport"] = field(default="system.transport", init=False)


@dataclass(frozen=True, kw_only=True)
class SystemFallbackEvent(_ToDictMixin):
    ts: float
    event_id: str
    session_id: str
    strategy: str
    reason: str
    turn_id: str | None = None
    source: str | None = None
    raw: JsonObject | None = None
    meta: JsonObject | None = None
    from_: JsonObject | None = None
    to: JsonObject | None = None
    result: str | None = None

    v: Literal[1] = field(default=1, init=False)
    type: Literal["system.fallback"] = field(default="system.fallback", init=False)

    def to_dict(self) -> JsonObject:
        data = super().to_dict()
        from_value = data.pop("from_", None)
        if from_value is not None:
            data["from"] = from_value
        return data


@dataclass(frozen=True, kw_only=True)
class SystemLogEvent(_ToDictMixin):
    ts: float
    event_id: str
    session_id: str
    level: str
    message: str
    turn_id: str | None = None
    source: str | None = None
    raw: JsonObject | None = None
    meta: JsonObject | None = None
    data: JsonObject | None = None

    v: Literal[1] = field(default=1, init=False)
    type: Literal["system.log"] = field(default="system.log", init=False)


AgentEvent: TypeAlias = (
    SessionStartEvent
    | SessionUserMessageEvent
    | SessionEndEvent
    | AssistantDeltaEvent
    | AssistantMessageEvent
    | ToolStartEvent
    | ToolUpdateEvent
    | ToolEndEvent
    | ToolErrorEvent
    | SystemTransportEvent
    | SystemFallbackEvent
    | SystemLogEvent
)

__all__ = [
    "EVENT_FAMILIES",
    "EVENT_TYPES",
    "AgentError",
    "AgentEvent",
    "AgentEventType",
    "AssistantDeltaEvent",
    "AssistantMessageEvent",
    "EventFamily",
    "SessionEndEvent",
    "SessionStartEvent",
    "SessionUserMessageEvent",
    "SystemFallbackEvent",
    "SystemLogEvent",
    "SystemTransportEvent",
    "ToolEndEvent",
    "ToolErrorEvent",
    "ToolStartEvent",
    "ToolUpdateEvent",
]

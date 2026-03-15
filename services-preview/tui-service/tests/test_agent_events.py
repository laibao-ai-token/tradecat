from __future__ import annotations

import unittest

from src.agent_events import (
    EVENT_FAMILIES,
    EVENT_TYPES,
    AgentError,
    SessionUserMessageEvent,
    SystemFallbackEvent,
    SystemTransportEvent,
    ToolErrorEvent,
    ToolStartEvent,
)


class TestAgentEvents(unittest.TestCase):
    def test_event_type_catalog_stays_stable(self) -> None:
        self.assertEqual(EVENT_FAMILIES, ("session", "assistant", "tool", "system"))
        self.assertEqual(
            EVENT_TYPES,
            (
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
            ),
        )

    def test_session_user_message_serializes_required_fields(self) -> None:
        event = SessionUserMessageEvent(
            ts=1.5,
            event_id="evt-user-1",
            session_id="sess-1",
            turn_id="turn-1",
            source="tradecat",
            raw=None,
            meta=None,
            message_id="msg-user-1",
            content="查一下 BTCUSDT 当前价格",
        )

        payload = event.to_dict()
        self.assertEqual(payload["v"], 1)
        self.assertEqual(payload["type"], "session.user_message")
        self.assertEqual(payload["message_id"], "msg-user-1")
        self.assertEqual(payload["content"], "查一下 BTCUSDT 当前价格")
        self.assertNotIn("raw", payload)
        self.assertNotIn("meta", payload)

    def test_tool_start_keeps_args_and_raw_payload(self) -> None:
        event = ToolStartEvent(
            ts=2.0,
            event_id="evt-tool-start-1",
            session_id="sess-1",
            turn_id="turn-1",
            source="adapter",
            raw={"upstream_type": "tool_call", "arguments": {"symbol": "BTCUSDT"}},
            meta={"adapter": "openclaw"},
            tool_call_id="call-1",
            tool_name="get_crypto_price",
            args={"symbol": "BTCUSDT"},
            timeout_s=5.0,
        )

        payload = event.to_dict()
        self.assertEqual(payload["type"], "tool.start")
        self.assertEqual(payload["args"], {"symbol": "BTCUSDT"})
        self.assertEqual(payload["raw"]["arguments"]["symbol"], "BTCUSDT")
        self.assertEqual(payload["meta"]["adapter"], "openclaw")
        self.assertEqual(payload["timeout_s"], 5.0)

    def test_tool_error_to_dict_strips_none_recursively(self) -> None:
        error = AgentError(
            message="boom",
            code=None,
            retryable=True,
            detail={"http_status": 504, "provider": None},
        )
        event = ToolErrorEvent(
            ts=3.0,
            event_id="evt-tool-error-1",
            session_id="sess-1",
            turn_id=None,
            source=None,
            raw=None,
            meta=None,
            tool_call_id="call-1",
            tool_name="get_crypto_price",
            elapsed_s=None,
            error=error,
        )

        payload = event.to_dict()
        self.assertEqual(payload["v"], 1)
        self.assertEqual(payload["type"], "tool.error")
        self.assertEqual(payload["tool_call_id"], "call-1")
        self.assertEqual(payload["tool_name"], "get_crypto_price")
        self.assertEqual(payload["error"]["message"], "boom")
        self.assertEqual(payload["error"]["retryable"], True)
        self.assertNotIn("code", payload["error"])
        self.assertEqual(payload["error"]["detail"], {"http_status": 504})
        self.assertNotIn("raw", payload)
        self.assertNotIn("meta", payload)
        self.assertNotIn("elapsed_s", payload)
        self.assertNotIn("turn_id", payload)

    def test_system_transport_serializes_nested_error(self) -> None:
        event = SystemTransportEvent(
            ts=4.0,
            event_id="evt-transport-1",
            session_id="sess-1",
            turn_id="turn-2",
            source="adapter",
            raw=None,
            meta={"reason": "stream_timeout"},
            component="gateway",
            operation="chat.stream",
            state="timeout",
            attempt=1,
            timeout_s=20.0,
            elapsed_s=20.1,
            error=AgentError(
                message="stream idle timeout",
                code="timeout",
                retryable=True,
                detail={"mode": "stream"},
            ),
        )

        payload = event.to_dict()
        self.assertEqual(payload["type"], "system.transport")
        self.assertEqual(payload["component"], "gateway")
        self.assertEqual(payload["operation"], "chat.stream")
        self.assertEqual(payload["state"], "timeout")
        self.assertEqual(payload["error"]["code"], "timeout")
        self.assertEqual(payload["error"]["detail"], {"mode": "stream"})

    def test_system_fallback_serializes_from_key(self) -> None:
        event = SystemFallbackEvent(
            ts=5.0,
            event_id="evt-fallback-1",
            session_id="sess-1",
            turn_id="turn-2",
            source="adapter",
            raw={"upstream": "gateway"},
            meta=None,
            strategy="retry",
            reason="stream timeout",
            from_={"mode": "stream"},
            to={"mode": "non_stream"},
            result="applied",
        )

        payload = event.to_dict()
        self.assertEqual(payload["type"], "system.fallback")
        self.assertIn("from", payload)
        self.assertNotIn("from_", payload)
        self.assertEqual(payload["from"], {"mode": "stream"})
        self.assertEqual(payload["to"], {"mode": "non_stream"})
        self.assertEqual(payload["result"], "applied")


if __name__ == "__main__":
    unittest.main()

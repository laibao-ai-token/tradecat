import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.tui import (
    AgentShellState,
    _seed_agent_shell_state,
    _submit_agent_shell_input,
    _tail_truncate,
    _workspace_panel_widths,
    _workspace_shell_enabled,
)


class TestAgentShellHelpers(unittest.TestCase):
    def test_workspace_shell_disabled_by_default(self) -> None:
        self.assertFalse(_workspace_shell_enabled("market_micro"))
        self.assertFalse(_workspace_shell_enabled("market_news"))
        self.assertFalse(_workspace_shell_enabled("market_backtest"))
        self.assertFalse(_workspace_shell_enabled("signals"))

    def test_workspace_shell_can_be_enabled_explicitly(self) -> None:
        with patch.dict("os.environ", {"TUI_ENABLE_AGENT_PLACEHOLDER": "1"}, clear=False):
            self.assertTrue(_workspace_shell_enabled("market_micro"))
            self.assertTrue(_workspace_shell_enabled("market_news"))
            self.assertTrue(_workspace_shell_enabled("market_backtest"))
            self.assertFalse(_workspace_shell_enabled("signals"))

    def test_workspace_panel_widths_cover_screen(self) -> None:
        left_w, right_w = _workspace_panel_widths(140)
        self.assertEqual(left_w + right_w + 1, 140)
        self.assertGreaterEqual(left_w, 48)
        self.assertGreaterEqual(right_w, 36)

    def test_tail_truncate_keeps_suffix(self) -> None:
        self.assertEqual(_tail_truncate("abcdef", 4), "<def")
        self.assertEqual(_tail_truncate("abc", 8), "abc")

    def test_submit_plain_input_appends_demo_reply(self) -> None:
        state = _seed_agent_shell_state()
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "agent_events.jsonl"
            state.events_log_path = str(log_path)
            initial_message_count = len(state.messages)
            initial_tool_count = len(state.tool_events)

            state.input_buffer = "帮我看看BTC和ETH"
            handled = _submit_agent_shell_input(state)

            self.assertTrue(handled)
            self.assertEqual(state.input_buffer, "")
            self.assertEqual(len(state.messages), initial_message_count + 2)
            self.assertEqual(state.messages[-2].role, "user")
            self.assertEqual(state.messages[-2].text, "帮我看看BTC和ETH")
            self.assertEqual(state.messages[-1].role, "assistant")
            self.assertEqual(len(state.tool_events), initial_tool_count + 1)
            self.assertEqual(state.tool_events[-1].tool_name, "local_echo")
            rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(
                [row["type"] for row in rows],
                ["session.start", "session.user_message", "tool.start", "tool.end", "assistant.message"],
            )
            self.assertTrue(all(row["session_id"] == state.session_id for row in rows))
            turn_ids = {row.get("turn_id") for row in rows if row["type"] != "session.start"}
            self.assertEqual(turn_ids, {state.current_turn_id})

    def test_submit_new_command_updates_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "agent_events.jsonl"
            state = AgentShellState(
                session_name="tradecat-main",
                session_id="sess_test_old",
                events_log_enabled=True,
                events_log_path=str(log_path),
            )
            state.input_buffer = "/new"

            handled = _submit_agent_shell_input(state)

            self.assertTrue(handled)
            self.assertEqual(state.input_buffer, "")
            self.assertNotEqual(state.session_name, "tradecat-main")
            self.assertTrue(state.session_name.startswith("tradecat-"))
            self.assertEqual(state.messages[-1].role, "system")
            self.assertNotEqual(state.session_id, "sess_test_old")
            rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(
                [row["type"] for row in rows],
                ["session.start", "session.user_message", "session.end", "session.start"],
            )
            self.assertEqual(rows[1]["session_id"], "sess_test_old")
            self.assertEqual(rows[2]["session_id"], "sess_test_old")
            self.assertEqual(rows[3]["session_id"], state.session_id)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = PROJECT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))
sys.path.insert(0, str(PROJECT_DIR / "tools"))
sys.path.insert(0, str(REPO_DIR))
sys.path.append(str(REPO_DIR / "w3-mux-tt" / "src"))

from background import BackgroundCoordinator, MoshBackgroundCollector
from constants import PAYLOAD_BYTES, PAYLOAD_LINES, PAYLOAD_SHA256
from generate_payload import build_payload
from run_w4 import build_schedule
from stream_mux import RawStream
from trial import (
    _reconstruct_final_output, editor_command, mosh_spec, roles_for,
    save_editor, wait_final_output,
)
from terminal_screen import TerminalScreen


class W4Tests(unittest.TestCase):
    def test_payload_is_fixed_one_mib(self):
        payload = build_payload()
        self.assertEqual(len(payload), PAYLOAD_BYTES)
        self.assertEqual(payload.count(b"\n"), PAYLOAD_LINES)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), PAYLOAD_SHA256)

    def test_scenario_roles(self):
        self.assertEqual(roles_for("W4-CMD"), ["interactive_0", "command_0"])
        self.assertEqual(roles_for("W4-OUTPUT"), ["interactive_0", "output_0"])
        self.assertEqual(roles_for("W4-MIX"), ["interactive_0", "command_0", "output_0"])

    def test_complete_block_schedule(self):
        rows = build_schedule(
            ["ssh", "ssh3", "mosh"], ["vim", "nano"],
            ["W4-CMD", "W4-OUTPUT", "W4-MIX"], 2, 7, "run",
        )
        self.assertEqual(len(rows), 36)
        self.assertEqual({row["block_id"] for row in rows}, {1, 2})
        self.assertEqual(len({row["trial_id"] for row in rows}), 36)

    def test_direct_framing_measures_real_output(self):
        holder = {}
        def sender(data):
            token = re.search(rb"__W4BG_START__:([0-9a-f]{24})", data).group(1)
            raw.put_data(b"__W4BG_START__:" + token + b"\nhello\n")
            raw.put_data(b"__W4BG_DONE__:" + token + b":0\n")
        raw = RawStream("command_0", sender)
        holder["raw"] = raw
        coordinator = BackgroundCoordinator(raw)
        result = coordinator.execute("trial:command:1", "printf hello", 1)
        coordinator.close()
        self.assertEqual(result["stdout"], b"hello\n")
        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(result["completion_marker_received"])

    def test_dynamic_command_expected_hash_matches_same_invocation(self):
        def sender(data):
            process = subprocess.run(["bash"], input=data, stdout=subprocess.PIPE, check=True)
            raw.put_data(process.stdout)
        raw = RawStream("command_0", sender)
        coordinator = BackgroundCoordinator(raw)
        result = coordinator.execute(
            "trial:command:dynamic", "printf 'dynamic-output\\n'", 2,
            capture_expected=True,
        )
        coordinator.close()
        self.assertEqual(result["stdout"], b"dynamic-output\n")
        self.assertEqual(result["expected_bytes"], len(result["stdout"]))
        self.assertEqual(
            result["expected_sha256"], hashlib.sha256(result["stdout"]).hexdigest()
        )

    def test_mosh_collector_does_not_claim_lossless_output(self):
        collector = MoshBackgroundCollector()
        collector.feed(b"\x1b[2J__W4BG_START__:output_0:1:1\n", 10, 100)
        collector.feed(b"visible fragment\n__W4BG_DONE__:output_0:1:1:0\n", 20, 200)
        trial = {
            "run_id": "r", "block_id": 1, "trial_order": 1,
            "trial_id": "t", "trial_tag": "o1_t", "protocol": "mosh",
            "editor": "vim", "scenario": "W4-OUTPUT", "logical_workload_count": 2,
        }
        stream = type("S", (), {"stream_id": "", "conversation_id": ""})()
        rows = collector.rows(trial, stream)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "completed")
        self.assertEqual(rows[0]["output_complete"], 0)
        self.assertIn("screen state", rows[0]["note"])

    def test_final_output_is_reconstructed_from_terminal_chunks(self):
        payload = b"0123456789abcdef" * 2 + b"end"
        lines = []
        for index in range(0, len(payload), 16):
            lines.append(
                f"__W4FINAL__:{index // 16 + 1:06d}:{payload[index:index + 16].hex()}"
            )
        lines.append(f"__W4FINAL_END__:{len(payload)}")
        endpoint = type("E", (), {})()
        endpoint.screen = TerminalScreen(12, 100)
        endpoint.terminal_error = ""
        endpoint.screen.feed("\r\n".join(lines), 1)
        self.assertEqual(wait_final_output(endpoint, .1), payload)

    def test_final_output_is_reconstructed_from_recent_ansi_output(self):
        payload = b"final-output-from-same-stream"
        marker = (
            "\x1b[32m__W4FINAL__:000001:"
            + payload[:16].hex()
            + "\x1b[0m\r\n\x1b[32m__W4FINAL__:000002:"
            + payload[16:].hex()
            + "\x1b[0m\r\n"
            + f"__W4FINAL_END__:{len(payload)}\r\n"
        )
        endpoint = type("E", (), {})()
        endpoint.screen = TerminalScreen(4, 40)
        endpoint.terminal_error = ""
        endpoint.recent_text = lambda: marker
        self.assertEqual(wait_final_output(endpoint, .1), payload)

    def test_final_output_uses_complete_recent_chunk_over_partial_screen_marker(self):
        payload = b"0123456789abcdef" + b"tail"
        recent = (
            "__W4FINAL__:000001:" + payload[:16].hex() + "\n"
            "__W4FINAL__:000002:" + payload[16:].hex() + "\n"
            f"__W4FINAL_END__:{len(payload)}\n"
        )
        screen = (
            "__W4FINAL__:000001:01\n"
            "__W4FINAL__:000002:" + payload[16:].hex() + "\n"
            f"__W4FINAL_END__:{len(payload)}"
        )
        self.assertEqual(_reconstruct_final_output((recent, screen)), payload)

    def test_final_output_rejects_missing_chunk_index(self):
        text = (
            "__W4FINAL__:000001:" + (b"a" * 16).hex() + "\n"
            "__W4FINAL__:000003:" + b"b".hex() + "\n"
            "__W4FINAL_END__:17\n"
        )
        self.assertIsNone(_reconstruct_final_output((text,)))

    def test_editor_command_clears_viewport_before_final_markers(self):
        command = editor_command({}, "vim", "/tmp/probe.c")
        clear = "printf '\\033[2J\\033[H'"
        self.assertIn(clear, command)
        self.assertLess(command.index(clear), command.index("od -An"))

    def test_save_editor_resets_capture_before_sending_exit_keys(self):
        class Endpoint:
            def __init__(self):
                self.events = []

            def clear_recent(self):
                self.events.append("clear")

            def send(self, data):
                self.events.append(data)

        endpoint = Endpoint()
        save_editor("vim", endpoint)
        self.assertEqual(endpoint.events, ["clear", b"\x1b", b":wq\r"])

    def test_mosh_invalid_command_marker_is_partial_not_exception(self):
        collector = MoshBackgroundCollector()
        collector.feed(b"__W4BG_START__:command_0:1:99\n", 10, 100)
        collector.feed(b"__W4BG_DONE__:command_0:1:99:0\n", 20, 200)
        trial = {
            "run_id": "r", "block_id": 1, "trial_order": 1,
            "trial_id": "t", "trial_tag": "o1_t", "protocol": "mosh",
            "editor": "vim", "scenario": "W4-CMD", "logical_workload_count": 2,
        }
        stream = type("S", (), {"stream_id": "", "conversation_id": ""})()
        rows = collector.rows(trial, stream)
        self.assertEqual(rows[0]["status"], "partial")
        self.assertIn("invalid command marker", rows[0]["note"])

    def test_mosh_scenarios_use_isolated_identical_three_pane_layout(self):
        cfg = {"TMUX_BIN": "tmux", "TERMINAL_COLUMNS": "180"}
        commands = {}
        for scenario in ("W4-CMD", "W4-OUTPUT", "W4-MIX"):
            trial = {
                "trial_tag": f"trial-{scenario}", "editor": "nano",
                "scenario": scenario,
            }
            specs, _session, _socket, _start, _stop = mosh_spec(
                cfg, trial, roles_for(scenario), "/tmp/probe.c"
            )
            commands[scenario] = specs[0].remote_command
        for command in commands.values():
            self.assertIn("-f /dev/null", command)
            self.assertIn("main-pane-width 90", command)
            self.assertEqual(command.count(" split-window "), 2)
        self.assertIn("cat ", commands["W4-OUTPUT"])
        self.assertIn("case ", commands["W4-CMD"])
        self.assertIn("cat ", commands["W4-MIX"])
        self.assertIn("case ", commands["W4-MIX"])


if __name__ == "__main__":
    unittest.main()

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from protocol_runner import ProtocolRunner


class FakeChild:
    # Luu cac lenh ma handshake gui vao terminal Mosh.
    def __init__(self):
        self.commands = []

    # Mo phong pexpect.sendline.
    def sendline(self, command):
        self.commands.append(command)


class MoshReadinessTests(unittest.TestCase):
    # Lan kiem tra dau thieu READY phai duoc retry thay vi loai ca trial.
    @patch("protocol_runner.time.sleep", return_value=None)
    def test_interactive_handshake_retries(self, _sleep):
        runner = object.__new__(ProtocolRunner)
        runner.trial_tag = "retry_test"
        runner.log_dir = Path("/private/tmp")
        runner.tracker = None
        checked = subprocess.CompletedProcess([], 0, stdout="interactive=MISSING\n")
        runner.mosh_control_command = Mock(return_value=checked)
        runner.drain_mosh_startup = Mock()
        runner.mosh_ready_status = Mock(side_effect=[
            (set(), checked),
            ({"interactive"}, subprocess.CompletedProcess([], 0, stdout="interactive=READY\n")),
        ])
        child = FakeChild()

        self.assertTrue(runner.wait_mosh_interactive_ready(child, timeout=2.0))
        self.assertEqual(len(child.commands), 2)
        self.assertEqual(runner.mosh_ready_status.call_count, 2)


if __name__ == "__main__":
    unittest.main()

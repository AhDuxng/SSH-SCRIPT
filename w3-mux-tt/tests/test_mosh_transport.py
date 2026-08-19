import errno
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from stream_mux.connection.base import RawStream, StreamSpec
from stream_mux.connection.mosh import MoshConnection


class ClosingChild:
    exitstatus = None

    def __init__(self, connection):
        self.connection = connection

    def read_nonblocking(self, size, timeout):
        self.connection._closing.set()
        raise OSError(errno.EBADF, "Bad file descriptor")


class MoshTransportTests(unittest.TestCase):
    def test_reader_treats_ebadf_during_close_as_clean_exit(self):
        connection = MoshConnection(
            {}, [StreamSpec("terminal", "true")], "trial",
        )
        connection.child = ClosingChild(connection)
        connection.streams["terminal"] = RawStream("terminal", lambda _data: None)
        fake_pexpect = SimpleNamespace(
            TIMEOUT=type("Timeout", (Exception,), {}),
            EOF=type("EOF", (Exception,), {}),
        )

        with patch.dict(sys.modules, {"pexpect": fake_pexpect}):
            connection._read()

        event = connection.streams["terminal"].receive(timeout=0.1)
        self.assertEqual(event.kind, "exit")


if __name__ == "__main__":
    unittest.main()

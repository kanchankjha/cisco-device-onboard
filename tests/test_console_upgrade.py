import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cisco_device_onboard.console_upgrade import upgrade_row
from cisco_device_onboard.csv_input import DeviceRow


class FakeProcess:
    def __init__(self, output, return_code=0):
        self.stdout = io.StringIO(output)
        self.returncode = return_code
        self.wait_called = False

    def wait(self):
        self.wait_called = True
        return self.returncode


class ConsoleUpgradeOutputTests(unittest.TestCase):
    def test_upgrade_row_streams_engine_output(self):
        process = FakeProcess("first log line\nsecond log line\n")
        row = DeviceRow(
            network_name="",
            serial="AAAA-BBBB-CCCC",
            console_ip="192.0.2.10",
            console_port=8235,
            extra={},
            row_number=2,
        )
        config = {
            "device": {"connection": {}},
            "image": {"source": {"server": {}}},
        }
        args = SimpleNamespace()

        with tempfile.TemporaryDirectory() as temp_dir:
            output = io.StringIO()
            with mock.patch("cisco_device_onboard.console_upgrade.subprocess.Popen", return_value=process) as popen:
                with redirect_stdout(output):
                    result = upgrade_row(row, config, Path(temp_dir), args)

        log_output = output.getvalue()
        self.assertIn("device=AAAA-BBBB-CCCC", log_output)
        self.assertIn("network=-", log_output)
        self.assertIn("first log line", log_output)
        self.assertIn("second log line", log_output)
        self.assertIn("END result=PASSED", log_output)
        self.assertTrue(process.wait_called)
        self.assertEqual(result["result"], "PASSED")
        popen_args, popen_kwargs = popen.call_args
        command = popen_args[0]
        self.assertEqual(command[1], "-u")
        self.assertEqual(popen_kwargs["stderr"], subprocess.STDOUT)


if __name__ == "__main__":
    unittest.main()

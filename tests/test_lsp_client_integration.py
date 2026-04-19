from __future__ import annotations

import stat
import sys
import tempfile
import unittest
from pathlib import Path

from mlua_lint.lsp_client import LspClient


def _make_fake_server_executable(tmp_path: Path) -> Path:
    helper = Path(__file__).parent / "helpers" / "fake_lsp_server.py"
    launcher = tmp_path / "fake-lsp"
    launcher.write_text(f"#!{sys.executable}\nimport runpy\nrunpy.run_path({helper.as_posix()!r}, run_name='__main__')\n")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    return launcher


class LspClientIntegrationTests(unittest.TestCase):
    def test_lsp_client_with_fake_server(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            launcher = _make_fake_server_executable(tmp_path)
            sample = tmp_path / "sample.mlua"
            sample.write_text("@Logic\nscript Test extends Logic\nend\n", encoding="utf-8")

            client = LspClient(str(launcher))
            try:
                client.initialize(str(tmp_path), [])
                client.ensure_document(str(sample), sample.read_text(encoding="utf-8"))

                diagnostics = client.diagnostics([str(sample)], initial_wait_seconds=0.1)
                self.assertEqual(diagnostics["warningCount"], 1)
                self.assertEqual(diagnostics["errorCount"], 1)
                self.assertEqual(diagnostics["infoCount"], 1)

                warning_only = client.diagnostics([str(sample)], initial_wait_seconds=0.1, severities={"warning"})
                self.assertEqual(warning_only["warningCount"], 1)
                self.assertEqual(warning_only["errorCount"], 0)
                self.assertEqual(warning_only["infoCount"], 0)
                first_report = warning_only["files"][0]
                self.assertEqual(len(first_report["diagnostics"]), 1)
                self.assertEqual(first_report["diagnostics"][0]["severity"], "warning")
            finally:
                client.close()


if __name__ == "__main__":
    unittest.main()

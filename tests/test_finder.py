import os
import tempfile
import unittest
from pathlib import Path

from mlua_lint.finder import find_language_server


class FinderTests(unittest.TestCase):
    def test_find_language_server_prefers_latest_semver(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ext_root = Path(temp_dir) / "extensions"
            old_dir = ext_root / "msw.mlua-1.0.0" / "server" / "bin"
            new_dir = ext_root / "msw.mlua-1.2.0" / "server" / "bin"
            old_dir.mkdir(parents=True)
            new_dir.mkdir(parents=True)
            (old_dir / "msw-mlua-lsp").write_text("", encoding="utf-8")
            (new_dir / "msw-mlua-lsp").write_text("", encoding="utf-8")

            old_env = os.environ.get("MLUA_VSCODE_EXTENSIONS_DIR")
            os.environ["MLUA_VSCODE_EXTENSIONS_DIR"] = str(ext_root)
            try:
                found = find_language_server(None)
            finally:
                if old_env is None:
                    del os.environ["MLUA_VSCODE_EXTENSIONS_DIR"]
                else:
                    os.environ["MLUA_VSCODE_EXTENSIONS_DIR"] = old_env
            self.assertIn("1.2.0", found)

    def test_find_language_server_custom_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            binary = Path(temp_dir) / "custom-lsp"
            binary.write_text("", encoding="utf-8")
            self.assertEqual(find_language_server(str(binary)), str(binary))


if __name__ == "__main__":
    unittest.main()

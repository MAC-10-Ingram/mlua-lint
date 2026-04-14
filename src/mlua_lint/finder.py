from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Iterable


def _extensions_roots() -> list[Path]:
    custom = os.getenv("MLUA_VSCODE_EXTENSIONS_DIR")
    if custom:
        return [Path(custom).expanduser()]

    home = Path.home()
    return [
        home / ".vscode" / "extensions",
        home / ".vscode-insiders" / "extensions",
        home / ".cursor" / "extensions",
        home / ".vscode-oss" / "extensions",
    ]


def _parse_semver_from_name(name: str) -> tuple[int, int, int, str]:
    base = name
    marker = "msw.mlua-"
    if marker in base:
        base = base.split(marker, 1)[1]
    parts = base.split(".")
    nums: list[int] = []
    for part in parts:
        if part.isdigit():
            nums.append(int(part))
        else:
            digits = "".join(ch for ch in part if ch.isdigit())
            if digits:
                nums.append(int(digits))
            else:
                break
        if len(nums) == 3:
            break
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2], name


def _candidate_paths(ext_dir: Path) -> Iterable[Path]:
    is_windows = platform.system().lower() == "windows"
    binary = ext_dir / "server" / "bin" / "msw-mlua-lsp"
    if is_windows:
        binary = binary.with_suffix(".exe")
    yield binary
    yield ext_dir / "scripts" / "server" / "out" / "languageServer.js"
    yield ext_dir / "server" / "main.js"


def find_language_server(custom_path: str | None) -> str:
    if custom_path:
        path = Path(custom_path).expanduser()
        if path.exists():
            return str(path)
        raise FileNotFoundError(f"custom language server path not found: {path}")

    candidates: list[Path] = []
    for root in _extensions_roots():
        if not root.exists():
            continue
        for entry in root.iterdir():
            if not entry.is_dir() or not entry.name.startswith("msw.mlua"):
                continue
            for cand in _candidate_paths(entry):
                if cand.exists():
                    candidates.append(cand)
                    break

    if not candidates:
        searched = ", ".join(str(root) for root in _extensions_roots())
        raise FileNotFoundError(f"could not find msw.mlua extension in: {searched}")

    def _ext_name(path: Path) -> str:
        for parent in path.parents:
            if parent.name.startswith("msw.mlua"):
                return parent.name
        return path.name

    candidates.sort(key=lambda path: _parse_semver_from_name(_ext_name(path)), reverse=True)
    return str(candidates[0])

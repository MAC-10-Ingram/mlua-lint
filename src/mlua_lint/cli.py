from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from mlua_lint.errors import JsonRpcError, ValidationError
from mlua_lint.finder import find_language_server
from mlua_lint.lsp_client import LspClient


def validate_format(fmt: str) -> str:
    normalized = fmt.strip().lower()
    if normalized in {"", "json", "text"}:
        return normalized or "json"
    raise ValidationError(f"unsupported format {fmt!r}")


def parse_diagnostic_severities(values: list[str] | None) -> set[str] | None:
    if not values:
        return None
    allowed = {"error", "warning", "information"}
    parsed: set[str] = set()
    for raw in values:
        for part in str(raw).split(","):
            severity = part.strip().lower()
            if not severity:
                continue
            if severity not in allowed:
                raise ValidationError(
                    f"invalid severity {severity!r}; allowed values: warning, error, information"
                )
            parsed.add(severity)
    return parsed or None


def resolve_root(root: str | None) -> str:
    if not root or not root.strip():
        return str(Path.cwd())
    return str(Path(root).expanduser().resolve())


def resolve_file(project_root: str, file_path: str) -> str:
    p = Path(file_path).expanduser()
    if not p.is_absolute():
        p = Path(project_root) / p
    return str(p.resolve())


def collect_document_items(project_root: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in Path(project_root).rglob("*.d.mlua"):
        text = path.read_text(encoding="utf-8")
        items.append(
            {
                "uri": path.resolve().as_uri(),
                "languageId": "mlua",
                "version": 1,
                "text": text,
            }
        )
    return items


def prepare_client(opts: argparse.Namespace, files: list[str]) -> tuple[LspClient, list[str]]:
    project_root = resolve_root(opts.root)
    ls_path = find_language_server(opts.ls_path or os.getenv("MLUA_LS_PATH"))
    client = LspClient(ls_path)
    try:
        client.initialize(project_root, collect_document_items(project_root))
        resolved_files: list[str] = []
        for file_path in files:
            resolved = resolve_file(project_root, file_path)
            content = Path(resolved).read_text(encoding="utf-8")
            client.ensure_document(resolved, content)
            resolved_files.append(resolved)
        return client, resolved_files
    except Exception:
        client.close()
        raise


def print_envelope(fmt: str, envelope: dict[str, Any]) -> None:
    if fmt == "json":
        print(json.dumps(envelope, ensure_ascii=False, indent=2))
        return
    if fmt == "text":
        print_text_envelope(envelope)
        return
    raise ValidationError(f"unsupported format {fmt!r}")


def print_text_envelope(envelope: dict[str, Any]) -> None:
    if envelope.get("command") == "diagnostic" and envelope.get("error") is None:
        result = envelope.get("result", {})
        files = result.get("files", []) if isinstance(result, dict) else []
        for file_item in files:
            if not isinstance(file_item, dict):
                continue
            uri = str(file_item.get("uri", ""))
            filename = uri_to_filename(uri)
            diagnostics = file_item.get("diagnostics", [])
            if not isinstance(diagnostics, list):
                continue
            for diagnostic in diagnostics:
                if not isinstance(diagnostic, dict):
                    continue
                rng = diagnostic.get("range", {})
                start = rng.get("start", {}) if isinstance(rng, dict) else {}
                line = int(start.get("line", 0)) + 1
                char = int(start.get("character", 0)) + 1
                severity = str(diagnostic.get("severity", "")).upper()
                message = str(diagnostic.get("message", ""))
                print(f"[{severity}] {filename}:{line}:{char} - {message}")
        return
    print(json.dumps(envelope, ensure_ascii=False, indent=2))


def uri_to_filename(uri: str) -> str:
    if uri.startswith("file://"):
        from urllib.parse import unquote, urlparse

        parsed = urlparse(uri)
        return unquote(parsed.path)
    return uri


def normalize_runtime_error(err: Exception, capability: dict[str, Any]) -> dict[str, str]:
    supported = bool(capability.get("supported", False))
    detail = str(capability.get("detail", ""))
    if isinstance(err, JsonRpcError):
        if err.code == -32601 or not supported:
            message = err.message
            if detail:
                message = f"{message} ({detail})"
            return {"code": "unsupported", "message": message}
        return {"code": f"jsonrpc_{err.code}", "message": err.message}
    if not supported:
        message = str(err)
        if detail:
            message = f"{message} ({detail})"
        return {"code": "unsupported", "message": message}
    return {"code": "runtime_error", "message": str(err)}


def emit_validation_failure(err: Exception, fmt: str) -> int:
    print_envelope(
        fmt,
        {
            "ok": False,
            "command": "diagnostic",
            "serverCapability": {"method": "textDocument/diagnostic"},
            "error": {"code": "invalid_argument", "message": str(err)},
        },
    )
    return 1


def emit_runtime_failure(capability: dict[str, Any], err: Exception, fmt: str) -> int:
    print_envelope(
        fmt,
        {
            "ok": False,
            "command": "diagnostic",
            "serverCapability": capability,
            "error": normalize_runtime_error(err, capability),
        },
    )
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mlua-linter", description="mLua diagnostic CLI.")
    parser.add_argument("files", nargs="+", help="Target .mlua files")
    parser.add_argument("--ls-path", default="", dest="ls_path", help="Path to the msw.mlua language server")
    parser.add_argument("--root", default="", help="Workspace root (defaults to current directory)")
    parser.add_argument("--format", default="json", help="Output format (json|text)")
    parser.add_argument("--timeout", type=int, default=5, help="Timeout in seconds to wait for analysis")
    parser.add_argument(
        "--severity",
        action="append",
        default=[],
        help="Filter diagnostics by severity (warning|error|information). Repeatable or comma-separated.",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    try:
        fmt = validate_format(args.format)
        if not args.files:
            raise ValidationError("at least one file is required")
        if args.files and args.files[0] == "diagnostic":
            raise ValidationError("diagnostic subcommand has been removed; pass files directly")
        severities = parse_diagnostic_severities(args.severity)
    except Exception as err:
        return emit_validation_failure(err, args.format or "json")

    try:
        client, files = prepare_client(args, list(args.files))
    except Exception as err:
        return emit_runtime_failure({"supported": True, "method": "textDocument/diagnostic"}, err, fmt)

    try:
        capability = client.capability("diagnostic")
        result = client.diagnostics(files, float(args.timeout), severities)
        print_envelope(
            fmt,
            {
                "ok": True,
                "command": "diagnostic",
                "result": result,
                "serverCapability": capability,
            },
        )
        return 1 if int(result.get("errorCount", 0)) > 0 else 0
    except Exception as err:
        return emit_runtime_failure(capability, err, fmt)
    finally:
        client.close()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except BrokenPipeError:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

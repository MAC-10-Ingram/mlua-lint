from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Callable

from mlua_lint.errors import JsonRpcError, ValidationError
from mlua_lint.finder import find_language_server
from mlua_lint.lsp_client import LspClient


def parse_position(value: str) -> dict[str, int]:
    raw = value.strip()
    if not raw:
        raise ValidationError("--position is required")
    parts = raw.split(":")
    if len(parts) != 2:
        raise ValidationError("position must be in 0-based line:character form")
    try:
        line = int(parts[0].strip())
        char = int(parts[1].strip())
    except ValueError as exc:
        raise ValidationError(f"invalid position value: {value}") from exc
    if line < 0 or char < 0:
        raise ValidationError("position values must be >= 0")
    return {"line": line, "character": char}


def validate_format(fmt: str) -> str:
    normalized = fmt.strip().lower()
    if normalized in {"", "json", "text"}:
        return normalized or "json"
    raise ValidationError(f"unsupported format {fmt!r}")


def parse_bool_arg(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ValidationError(f"invalid boolean value {value!r}")


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


def prepare_client(opts: argparse.Namespace, files: list[str]) -> tuple[LspClient, list[str], dict[str, str]]:
    project_root = resolve_root(opts.root)
    ls_path = find_language_server(opts.ls_path or os.getenv("MLUA_LS_PATH"))
    client = LspClient(ls_path)
    try:
        client.initialize(project_root, collect_document_items(project_root))
        resolved_files: list[str] = []
        contents: dict[str, str] = {}
        for file_path in files:
            resolved = resolve_file(project_root, file_path)
            content = Path(resolved).read_text(encoding="utf-8")
            client.ensure_document(resolved, content)
            resolved_files.append(resolved)
            contents[resolved] = content
        return client, resolved_files, contents
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


def emit_validation_failure(command: str, file_path: str, pos: dict[str, int] | None, err: Exception, fmt: str) -> int:
    print_envelope(
        fmt,
        {
            "ok": False,
            "command": command,
            "file": file_path,
            "position": pos,
            "serverCapability": {"method": command},
            "error": {"code": "invalid_argument", "message": str(err)},
        },
    )
    return 1


def emit_runtime_failure(
    command: str,
    file_path: str,
    pos: dict[str, int] | None,
    capability: dict[str, Any],
    err: Exception,
    fmt: str,
) -> int:
    print_envelope(
        fmt,
        {
            "ok": False,
            "command": command,
            "file": file_path,
            "position": pos,
            "serverCapability": capability,
            "error": normalize_runtime_error(err, capability),
        },
    )
    return 1


def run_file_command(
    command_name: str,
    args: argparse.Namespace,
    invoke: Callable[[LspClient, str, str], Any],
) -> int:
    try:
        fmt = validate_format(args.format)
        if not args.file or not str(args.file).strip():
            raise ValidationError("--file is required")
    except Exception as err:
        return emit_validation_failure(command_name, str(args.file or ""), None, err, args.format or "json")

    try:
        client, files, contents = prepare_client(args, [args.file])
    except Exception as err:
        return emit_runtime_failure(command_name, str(args.file or ""), None, {"method": command_name}, err, fmt)

    try:
        file_path = files[0]
        capability = client.capability(command_name)
        result = invoke(client, file_path, contents[file_path])
        print_envelope(
            fmt,
            {
                "ok": True,
                "command": command_name,
                "file": file_path,
                "result": result,
                "serverCapability": capability,
            },
        )
        return 0
    except Exception as err:
        return emit_runtime_failure(command_name, files[0], None, capability, err, fmt)
    finally:
        client.close()


def run_position_command(
    command_name: str,
    feature: str,
    args: argparse.Namespace,
    invoke: Callable[[LspClient, str, str, dict[str, int]], Any],
) -> int:
    try:
        fmt = validate_format(args.format)
        if not args.file or not str(args.file).strip():
            raise ValidationError("--file is required")
        pos = parse_position(args.position or "")
    except Exception as err:
        return emit_validation_failure(command_name, str(args.file or ""), None, err, args.format or "json")

    try:
        client, files, contents = prepare_client(args, [args.file])
    except Exception as err:
        return emit_runtime_failure(command_name, str(args.file or ""), pos, {"method": feature}, err, fmt)

    try:
        file_path = files[0]
        capability = client.capability(feature)
        result = invoke(client, file_path, contents[file_path], pos)
        print_envelope(
            fmt,
            {
                "ok": True,
                "command": command_name,
                "file": file_path,
                "position": pos,
                "result": result,
                "serverCapability": capability,
            },
        )
        return 0
    except Exception as err:
        return emit_runtime_failure(command_name, files[0], pos, capability, err, fmt)
    finally:
        client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mlua-linter", description="AI-agent-oriented mLua language tooling CLI.")
    subparsers = parser.add_subparsers(dest="command")

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--ls-path", default="", dest="ls_path", help="Path to the msw.mlua language server")
        p.add_argument("--root", default="", help="Workspace root (defaults to current directory)")
        p.add_argument("--format", default="json", help="Output format (json|text)")
        p.add_argument("--timeout", type=int, default=5, help="Timeout in seconds to wait for analysis")

    def add_file(p: argparse.ArgumentParser) -> None:
        add_common(p)
        p.add_argument("--file", default="", help="Target file path")

    def add_position(p: argparse.ArgumentParser) -> None:
        add_file(p)
        p.add_argument("--position", default="", help="0-based line:character position")

    diagnostic = subparsers.add_parser("diagnostic", help="Collect diagnostics for one or more files.")
    add_common(diagnostic)
    diagnostic.add_argument("files", nargs="+")
    diagnostic.add_argument(
        "--severity",
        action="append",
        default=[],
        help="Filter diagnostics by severity (warning|error|information). Repeatable or comma-separated.",
    )

    annotation = subparsers.add_parser("annotation", aliases=["annotations"], help="Return semantic annotation tokens.")
    add_file(annotation)

    highlight = subparsers.add_parser("highlight", aliases=["highlighting"], help="Return highlights at a position.")
    add_position(highlight)

    completion = subparsers.add_parser("completion", help="Return completion items at a position.")
    add_position(completion)

    hover = subparsers.add_parser("hover", help="Return hover information at a position.")
    add_position(hover)

    inlay = subparsers.add_parser("inlay-hint", help="Return inlay hints for a file.")
    add_file(inlay)

    definition = subparsers.add_parser("definition", help="Go to definition at a position.")
    add_position(definition)

    type_definition = subparsers.add_parser("type-definition", help="Go to type definition at a position.")
    add_position(type_definition)

    reference = subparsers.add_parser("reference", help="Return the first reference at a position.")
    add_position(reference)
    reference.add_argument("--include-declaration", nargs="?", const=True, default=True, type=parse_bool_arg)

    references = subparsers.add_parser("references", help="Return all references at a position.")
    add_position(references)
    references.add_argument("--include-declaration", nargs="?", const=True, default=True, type=parse_bool_arg)

    rename = subparsers.add_parser("rename", help="Preview workspace edits for rename.")
    add_position(rename)
    rename.add_argument("--new-name", "--name", dest="new_name", default="", help="Replacement symbol name")

    signature = subparsers.add_parser("signature-help", aliases=["signature-helper"], help="Return signature help.")
    add_position(signature)

    return parser


def run(args: argparse.Namespace) -> int:
    command = args.command
    if command is None:
        build_parser().print_help()
        return 0

    if command == "diagnostic":
        try:
            fmt = validate_format(args.format)
            if not args.files:
                raise ValidationError("at least one file is required")
            severities = parse_diagnostic_severities(args.severity)
        except Exception as err:
            return emit_validation_failure("diagnostic", "", None, err, args.format or "json")

        try:
            client, files, _ = prepare_client(args, list(args.files))
        except Exception as err:
            return emit_runtime_failure(
                "diagnostic",
                "",
                None,
                {"supported": True, "method": "textDocument/diagnostic"},
                err,
                fmt,
            )

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
            return emit_runtime_failure("diagnostic", "", None, capability, err, fmt)
        finally:
            client.close()

    if command in {"annotation", "annotations"}:
        return run_file_command("annotation", args, lambda c, f, t: c.annotation(f, t))
    if command in {"completion"}:
        return run_position_command("completion", "completion", args, lambda c, f, t, p: c.completion(f, p["line"], p["character"]))
    if command in {"definition"}:
        return run_position_command("definition", "definition", args, lambda c, f, t, p: c.definition(f, p["line"], p["character"]))
    if command in {"highlight", "highlighting"}:
        return run_position_command(
            "highlight",
            "highlight",
            args,
            lambda c, f, t, p: c.document_highlight(f, p["line"], p["character"]),
        )
    if command in {"hover"}:
        return run_position_command("hover", "hover", args, lambda c, f, t, p: c.hover(f, p["line"], p["character"]))
    if command in {"inlay-hint"}:
        return run_file_command("inlay-hint", args, lambda c, f, t: c.inlay_hint(f, t))
    if command in {"reference"}:
        return run_position_command(
            "reference",
            "reference",
            args,
            lambda c, f, t, p: {"location": (refs := c.references(f, p["line"], p["character"], args.include_declaration))[0] if refs else None},
        )
    if command in {"references"}:
        return run_position_command(
            "references",
            "references",
            args,
            lambda c, f, t, p: c.references(f, p["line"], p["character"], args.include_declaration),
        )
    if command in {"rename"}:
        if not str(args.new_name).strip():
            return emit_validation_failure("rename", str(args.file or ""), None, ValidationError("--new-name is required"), args.format)
        return run_position_command(
            "rename",
            "rename",
            args,
            lambda c, f, t, p: c.rename(f, p["line"], p["character"], args.new_name),
        )
    if command in {"signature-help", "signature-helper"}:
        return run_position_command(
            "signature-help",
            "signature-help",
            args,
            lambda c, f, t, p: c.signature_help(f, p["line"], p["character"]),
        )
    if command in {"type-definition"}:
        return run_position_command(
            "type-definition",
            "type-definition",
            args,
            lambda c, f, t, p: c.type_definition(f, p["line"], p["character"]),
        )

    raise ValidationError(f"unknown command {command!r}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except BrokenPipeError:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

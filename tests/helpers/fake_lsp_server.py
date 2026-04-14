from __future__ import annotations

import json
import sys
from typing import Any


def read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        raw = line.decode("ascii", errors="replace").strip()
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def write_message(payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    sys.stdout.buffer.write(header)
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def notify(method: str, params: Any) -> None:
    write_message({"jsonrpc": "2.0", "method": method, "params": params})


def main() -> int:
    while True:
        msg = read_message()
        if msg is None:
            return 0
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params", {})

        if msg_id is None:
            continue

        if method == "initialize":
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "capabilities": {
                            "hoverProvider": True,
                            "definitionProvider": True,
                            "referencesProvider": True,
                            "completionProvider": {},
                            "renameProvider": True,
                            "signatureHelpProvider": {},
                            "documentHighlightProvider": True,
                            "typeDefinitionProvider": True,
                            "semanticTokensProvider": {
                                "full": True,
                                "range": True,
                                "legend": {"tokenTypes": ["keyword"], "tokenModifiers": ["declaration"]},
                            },
                            "inlayHintProvider": True,
                        }
                    },
                }
            )
            continue

        if method == "textDocument/hover":
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "contents": [{"kind": "markdown", "value": "fake hover"}],
                        "range": {"start": {"line": 1, "character": 1}, "end": {"line": 1, "character": 5}},
                    },
                }
            )
            continue

        if method == "textDocument/definition":
            uri = params.get("textDocument", {}).get("uri", "file:///tmp/test.mlua")
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": [{"uri": uri, "range": {"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 4}}}],
                }
            )
            continue

        if method == "textDocument/references":
            uri = params.get("textDocument", {}).get("uri", "file:///tmp/test.mlua")
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": [{"uri": uri, "range": {"start": {"line": 2, "character": 0}, "end": {"line": 2, "character": 4}}}],
                }
            )
            continue

        if method == "textDocument/completion":
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"isIncomplete": False, "items": [{"label": "foo", "kind": 3}]},
                }
            )
            continue

        if method == "textDocument/documentHighlight":
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": [{"range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 2}}, "kind": 1}],
                }
            )
            continue

        if method == "textDocument/signatureHelp":
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"activeSignature": 0, "activeParameter": 0, "signatures": [{"label": "foo(a: string)"}]},
                }
            )
            continue

        if method == "textDocument/typeDefinition":
            uri = params.get("textDocument", {}).get("uri", "file:///tmp/test.mlua")
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": [{"uri": uri, "range": {"start": {"line": 3, "character": 0}, "end": {"line": 3, "character": 5}}}],
                }
            )
            continue

        if method == "textDocument/rename":
            uri = params.get("textDocument", {}).get("uri", "file:///tmp/test.mlua")
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "changes": {
                            uri: [
                                {
                                    "range": {
                                        "start": {"line": 1, "character": 0},
                                        "end": {"line": 1, "character": 3},
                                    },
                                    "newText": params.get("newName", "renamed"),
                                }
                            ]
                        }
                    },
                }
            )
            continue

        if method == "textDocument/diagnostic":
            uri = params.get("textDocument", {}).get("uri", "file:///tmp/test.mlua")
            notify("workspace/diagnostic/refresh", {})
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "kind": "full",
                        "items": [
                            {
                                "severity": 2,
                                "source": "fake",
                                "message": "sample warning",
                                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 4}},
                                "code": "W001",
                            }
                        ],
                        "uri": uri,
                    },
                }
            )
            continue

        if method == "textDocument/inlayHint":
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": [{"position": {"line": 0, "character": 1}, "label": "hint", "kind": 1}],
                }
            )
            continue

        if method == "textDocument/semanticTokens/full":
            write_message({"jsonrpc": "2.0", "id": msg_id, "result": {"data": [0, 0, 4, 0, 1]}})
            continue

        write_message({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": "method not found"}})


if __name__ == "__main__":
    raise SystemExit(main())

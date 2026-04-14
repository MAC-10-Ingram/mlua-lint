# mlua-lint-python

Python port of the `mlua-lint-go` CLI wrapper around the official `msw.mlua` language server.

## Install

```bash
python -m pip install -e .
```

## Usage

```bash
mlua-linter diagnostic path/to/script.mlua
mlua-linter hover --file path/to/script.mlua --position 10:4
```

Default output format is JSON envelope:

```json
{
  "ok": true,
  "command": "hover",
  "file": "/abs/path/script.mlua",
  "position": { "line": 10, "character": 4 },
  "result": {},
  "serverCapability": { "supported": true, "method": "textDocument/hover" }
}
```

# mlua-lint-python

Python port of the `mlua-lint-go` CLI wrapper around the official `msw.mlua` language server.

## Install

```bash
python -m pip install -e .
```

## Usage

```bash
mlua-linter path/to/script.mlua
mlua-linter path/to/a.mlua path/to/b.mlua --severity warning,error
```

Default output format is JSON envelope:

```json
{
  "ok": true,
  "command": "diagnostic",
  "result": {
    "files": [],
    "errorCount": 0,
    "warningCount": 0,
    "infoCount": 0
  },
  "serverCapability": { "supported": true, "method": "textDocument/diagnostic" }
}
```

from __future__ import annotations


class MluaLintError(Exception):
    pass


class JsonRpcError(MluaLintError):
    def __init__(self, code: int, message: str, data: object | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class ValidationError(MluaLintError):
    pass

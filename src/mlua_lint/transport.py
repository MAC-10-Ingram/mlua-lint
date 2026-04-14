from __future__ import annotations

import json
import queue
import threading
from subprocess import Popen
from typing import Any, Callable

from mlua_lint.errors import JsonRpcError

NotificationHandler = Callable[[str, Any], None]


class JsonRpcTransport:
    def __init__(self, proc: Popen[bytes], on_notification: NotificationHandler | None = None):
        self._proc = proc
        self._on_notification = on_notification
        self._next_id = 1
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except OSError:
            pass

    def notify(self, method: str, params: Any | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._write(payload)

    def call(self, method: str, params: Any | None = None, timeout: float | None = None) -> Any:
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[request_id] = response_queue

        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._write(payload)

        try:
            response = response_queue.get(timeout=timeout)
        except queue.Empty as exc:
            with self._lock:
                self._pending.pop(request_id, None)
            raise TimeoutError(f"jsonrpc timeout waiting for {method}") from exc

        if "error" in response and response["error"] is not None:
            err = response["error"]
            raise JsonRpcError(
                code=int(err.get("code", -32000)),
                message=str(err.get("message", "jsonrpc error")),
                data=err.get("data"),
            )
        return response.get("result")

    def _write(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        if not self._proc.stdin:
            raise BrokenPipeError("language server stdin is not available")
        self._proc.stdin.write(header)
        self._proc.stdin.write(body)
        self._proc.stdin.flush()

    def _read_loop(self) -> None:
        while not self._closed.is_set():
            message = self._read_message()
            if message is None:
                self._closed.set()
                self._fail_pending("language server closed unexpectedly")
                return
            if "id" in message:
                request_id = message["id"]
                if isinstance(request_id, int):
                    with self._lock:
                        pending = self._pending.pop(request_id, None)
                    if pending:
                        pending.put(message)
            elif "method" in message and self._on_notification:
                self._on_notification(str(message["method"]), message.get("params"))

    def _read_message(self) -> dict[str, Any] | None:
        if not self._proc.stdout:
            return None
        headers: dict[str, str] = {}
        while True:
            raw = self._proc.stdout.readline()
            if not raw:
                return None
            if raw in (b"\r\n", b"\n"):
                break
            line = raw.decode("ascii", errors="replace").strip()
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()

        content_length = int(headers.get("content-length", "0"))
        if content_length <= 0:
            return None
        body = self._proc.stdout.read(content_length)
        if not body:
            return None
        return json.loads(body.decode("utf-8"))

    def _fail_pending(self, message: str) -> None:
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for waiter in pending:
            waiter.put({"error": {"code": -32099, "message": message}})

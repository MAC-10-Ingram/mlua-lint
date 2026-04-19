from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from mlua_lint.normalize import normalize_diagnostic
from mlua_lint.transport import JsonRpcTransport


def resolve_server_command(ls_path: str) -> list[str]:
    node_path = os.getenv("MLUA_NODE_PATH", "node")
    ext = Path(ls_path).suffix.lower()
    if ext in {".js", ".mjs", ".cjs"}:
        return [node_path, ls_path, "--stdio"]
    return [ls_path, "--stdio"]


class LspClient:
    def __init__(self, ls_path: str):
        self._process = subprocess.Popen(
            resolve_server_command(ls_path),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._diag_lock = threading.Lock()
        self._diagnostics: dict[str, list[dict[str, Any]]] = {}
        self._refresh_signal = threading.Semaphore(0)
        self._doc_versions: dict[str, int] = {}
        self._transport = JsonRpcTransport(self._process, self._on_notification)
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def _drain_stderr(self) -> None:
        if not self._process.stderr:
            return
        while True:
            data = self._process.stderr.read1(2048)
            if not data:
                return
            os.write(2, data)

    def _on_notification(self, method: str, params: Any) -> None:
        if method == "workspace/diagnostic/refresh":
            self._refresh_signal.release()
            return
        if method != "textDocument/publishDiagnostics":
            return
        if not isinstance(params, dict):
            return
        uri = str(params.get("uri", ""))
        diagnostics = params.get("diagnostics", [])
        if not uri or not isinstance(diagnostics, list):
            return
        with self._diag_lock:
            self._diagnostics[uri] = diagnostics

    def initialize(self, root_path: str, document_items: list[dict[str, Any]]) -> None:
        init_options = {
            "documentItems": document_items,
            "entryItems": [],
            "modules": [],
            "globalVariables": [],
            "globalFunctions": [],
            "profileMode": False,
            "stopwatch": False,
            "capabilities": {"diagnosticCapability": _full_diagnostic_capability()},
        }
        params = {
            "clientInfo": {"name": "mlua-linter", "version": "0.2.0"},
            "rootUri": Path(root_path).resolve().as_uri(),
            "capabilities": _client_capabilities(),
            "initializationOptions": json.dumps(init_options),
        }
        self._transport.call("initialize", params, timeout=20.0)
        self._transport.notify("initialized", {})

    def close(self) -> None:
        self._transport.close()
        if self._process.poll() is None:
            self._process.kill()

    def open_document(self, file_path: str, content: str) -> None:
        uri = Path(file_path).resolve().as_uri()
        params = {
            "textDocument": {"uri": uri, "languageId": "mlua", "version": 1, "text": content},
        }
        self._transport.notify("textDocument/didOpen", params)
        self._doc_versions[uri] = 1
        self._transport.notify("msw.protocol.refreshDiagnostic", None)

    def change_document(self, file_path: str, content: str) -> None:
        uri = Path(file_path).resolve().as_uri()
        version = self._doc_versions.get(uri, 0) + 1
        self._doc_versions[uri] = version
        params = {
            "textDocument": {"uri": uri, "version": version},
            "contentChanges": [{"text": content}],
        }
        self._transport.notify("textDocument/didChange", params)

    def ensure_document(self, file_path: str, content: str) -> None:
        uri = Path(file_path).resolve().as_uri()
        if uri in self._doc_versions:
            self.change_document(file_path, content)
        else:
            self.open_document(file_path, content)

    def wait_for_refresh(self, timeout: float) -> bool:
        return self._refresh_signal.acquire(timeout=timeout)

    def call_raw(self, method: str, params: dict[str, Any]) -> Any:
        return self._transport.call(method, params, timeout=20.0)

    def diagnostics(
        self,
        file_paths: list[str],
        initial_wait_seconds: float,
        severities: set[str] | None = None,
    ) -> dict[str, Any]:
        self.wait_for_refresh(initial_wait_seconds)
        files: list[dict[str, Any]] = []
        error_count = 0
        warning_count = 0
        info_count = 0
        for path in file_paths:
            report = self._pull_diagnostics(Path(path).resolve().as_uri())
            if severities is not None:
                diagnostics = report.get("diagnostics", [])
                if isinstance(diagnostics, list):
                    report = {
                        "uri": report.get("uri", ""),
                        "diagnostics": [
                            item
                            for item in diagnostics
                            if isinstance(item, dict) and str(item.get("severity", "")) in severities
                        ],
                    }
            files.append(report)
            for diagnostic in report.get("diagnostics", []):
                severity = diagnostic.get("severity", "")
                if severity == "error":
                    error_count += 1
                elif severity == "warning":
                    warning_count += 1
                else:
                    info_count += 1
        return {
            "files": files,
            "errorCount": error_count,
            "warningCount": warning_count,
            "infoCount": info_count,
        }

    def capability(self, feature: str) -> dict[str, Any]:
        if feature == "diagnostic":
            return {"supported": True, "method": "textDocument/diagnostic"}
        return {"supported": False, "method": feature, "detail": "unknown feature"}

    def _pull_diagnostics(self, doc_uri: str) -> dict[str, Any]:
        params = {"textDocument": {"uri": doc_uri}}
        result = self.call_raw("textDocument/diagnostic", params) or {}
        deadline = time.time() + 2.0
        while time.time() < deadline:
            kind = result.get("kind")
            items = result.get("items", [])
            if kind == "full" and isinstance(items, list) and items:
                break

            # Some servers report "unchanged" before background analysis finishes.
            # Others emit an early empty "full" and then fill diagnostics shortly after.
            if kind != "full":
                if self.wait_for_refresh(0.4):
                    result = self.call_raw("textDocument/diagnostic", params) or {}
                    continue
                time.sleep(0.1)
            else:
                time.sleep(0.2)

            result = self.call_raw("textDocument/diagnostic", params) or {}
        items = result.get("items", [])
        if not isinstance(items, list):
            return {"uri": doc_uri, "diagnostics": []}
        diagnostics = [normalize_diagnostic(item) for item in items if isinstance(item, dict)]
        return {"uri": doc_uri, "diagnostics": diagnostics}


def _client_capabilities() -> dict[str, Any]:
    return {
        "textDocument": {
            "publishDiagnostics": {"relatedInformation": True},
        },
        "workspace": {},
    }


def _full_diagnostic_capability() -> dict[str, bool]:
    return {
        "needExtendsDiagnostic": True,
        "notEqualsNameDiagnostic": True,
        "duplicateLocalDiagnostic": True,
        "introduceGlobalVariableDiagnostic": True,
        "parseErrorDiagnostic": True,
        "annotationParseErrorDiagnostic": True,
        "unavailableAttributeDiagnostic": True,
        "unavailableTypeDiagnostic": True,
        "unresolvedMemberDiagnostic": True,
        "unresolvedSymbolDiagnostic": True,
        "assignTypeMismatchDiagnostic": True,
        "parameterTypeMismatchDiagnostic": True,
        "deprecatedDiagnostic": True,
        "overrideMemberMismatchDiagnostic": True,
        "unavailableOptionalParameterDiagnostic": True,
        "unavailableParameterNameDiagnostic": True,
        "invalidAttributeArgumentDiagnostic": True,
        "notAllowPropertyDefaultValueDiagnostic": True,
        "assignToReadonlyDiagnostic": True,
        "needPropertyDefaultValueDiagnostic": True,
        "notEnoughArgumentDiagnostic": True,
        "tooManyArgumentDiagnostic": True,
        "duplicateMemberDiagnostic": True,
        "cannotOverrideMemberDiagnostic": True,
        "tableKeyTypeMismatchDiagnostic": True,
        "duplicateAttributeDiagnostic": True,
        "invalidEventHandlerParameterDiagnostic": True,
        "unavailablePropertyNameDiagnostic": True,
        "annotationTypeNotFoundDiagnostic": True,
        "annotationParamNotFoundDiagnostic": True,
        "unbalancedAssignmentDiagnostic": True,
        "unexpectedReturnDiagnostic": True,
        "needReturnDiagnostic": True,
        "duplicateParamDiagnostic": True,
        "returnTypeMismatchDiagnostic": True,
        "expectedReturnValueDiagnostic": True,
    }

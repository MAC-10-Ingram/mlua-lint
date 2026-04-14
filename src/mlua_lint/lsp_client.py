from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from mlua_lint.errors import JsonRpcError
from mlua_lint.normalize import (
    extract_semantic_legend,
    normalize_annotation_result,
    normalize_completion_result,
    normalize_diagnostic,
    normalize_highlight_result,
    normalize_hover_result,
    normalize_inlay_hint_result,
    normalize_location_result,
    normalize_rename_result,
    normalize_signature_help_result,
)
from mlua_lint.transport import JsonRpcTransport


def resolve_server_command(ls_path: str) -> list[str]:
    node_path = os.getenv("MLUA_NODE_PATH", "node")
    ext = Path(ls_path).suffix.lower()
    if ext in {".js", ".mjs", ".cjs"}:
        return [node_path, ls_path, "--stdio"]
    return [ls_path, "--stdio"]


def provider_enabled(provider: Any) -> bool:
    if provider is None:
        return False
    if isinstance(provider, bool):
        return provider
    return True


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
        self._refresh_event = threading.Event()
        self._doc_versions: dict[str, int] = {}
        self._capabilities: dict[str, Any] = {}
        self._raw_capabilities: dict[str, Any] = {}
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
            self._refresh_event.set()
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
        result = self._transport.call("initialize", params, timeout=20.0) or {}
        capabilities = result.get("capabilities", {}) if isinstance(result, dict) else {}
        if isinstance(capabilities, dict):
            self._raw_capabilities = capabilities
            self._capabilities = capabilities
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
        return self._refresh_event.wait(timeout=timeout)

    def call_raw(self, method: str, params: dict[str, Any]) -> Any:
        return self._transport.call(method, params, timeout=20.0)

    def diagnostics(self, file_paths: list[str], initial_wait_seconds: float) -> dict[str, Any]:
        self.wait_for_refresh(initial_wait_seconds)
        files: list[dict[str, Any]] = []
        error_count = 0
        warning_count = 0
        info_count = 0
        for path in file_paths:
            report = self._pull_diagnostics(Path(path).resolve().as_uri())
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

    def hover(self, file_path: str, line: int, character: int) -> dict[str, Any]:
        return normalize_hover_result(self.call_raw("textDocument/hover", self._position_params(file_path, line, character)))

    def completion(self, file_path: str, line: int, character: int) -> dict[str, Any]:
        params = self._position_params(file_path, line, character)
        params["context"] = {"triggerKind": 1}
        return normalize_completion_result(self.call_raw("textDocument/completion", params))

    def document_highlight(self, file_path: str, line: int, character: int) -> list[dict[str, Any]]:
        return normalize_highlight_result(
            self.call_raw("textDocument/documentHighlight", self._position_params(file_path, line, character))
        )

    def definition(self, file_path: str, line: int, character: int) -> list[dict[str, Any]]:
        return normalize_location_result(self.call_raw("textDocument/definition", self._position_params(file_path, line, character)))

    def type_definition(self, file_path: str, line: int, character: int) -> list[dict[str, Any]]:
        return normalize_location_result(
            self.call_raw("textDocument/typeDefinition", self._position_params(file_path, line, character))
        )

    def references(self, file_path: str, line: int, character: int, include_declaration: bool) -> list[dict[str, Any]]:
        params = self._position_params(file_path, line, character)
        params["context"] = {"includeDeclaration": include_declaration}
        return normalize_location_result(self.call_raw("textDocument/references", params))

    def rename(self, file_path: str, line: int, character: int, new_name: str) -> dict[str, Any]:
        params = self._position_params(file_path, line, character)
        params["newName"] = new_name
        return normalize_rename_result(self.call_raw("textDocument/rename", params))

    def signature_help(self, file_path: str, line: int, character: int) -> dict[str, Any]:
        params = self._position_params(file_path, line, character)
        params["context"] = {"triggerKind": 1, "isRetrigger": False}
        return normalize_signature_help_result(self.call_raw("textDocument/signatureHelp", params))

    def inlay_hint(self, file_path: str, content: str) -> list[dict[str, Any]]:
        params = {"textDocument": {"uri": Path(file_path).resolve().as_uri()}, "range": _document_range(content)}
        return normalize_inlay_hint_result(self.call_raw("textDocument/inlayHint", params))

    def annotation(self, file_path: str, content: str) -> dict[str, Any]:
        params = {"textDocument": {"uri": Path(file_path).resolve().as_uri()}}
        try:
            raw = self.call_raw("textDocument/semanticTokens/full", params)
        except JsonRpcError as err:
            if err.code != -32601 or not _has_semantic_token_range_support(self._raw_capabilities):
                raise
            raw = self.call_raw(
                "textDocument/semanticTokens/range",
                {"textDocument": {"uri": Path(file_path).resolve().as_uri()}, "range": _document_range(content)},
            )
        legend = extract_semantic_legend(self._raw_capabilities)
        return normalize_annotation_result(raw, legend)

    def capability(self, feature: str) -> dict[str, Any]:
        if feature == "annotation":
            if _has_semantic_token_support(self._raw_capabilities):
                return {"supported": True, "method": "textDocument/semanticTokens/full"}
            return {
                "supported": False,
                "method": "textDocument/semanticTokens/full",
                "detail": "semanticTokensProvider missing",
            }
        if feature == "completion":
            return _provider_status(self._capabilities.get("completionProvider"), "textDocument/completion", "completionProvider missing")
        if feature == "definition":
            return _provider_status(self._capabilities.get("definitionProvider"), "textDocument/definition", "definitionProvider missing")
        if feature == "diagnostic":
            return {"supported": True, "method": "textDocument/diagnostic"}
        if feature == "highlight":
            return _provider_status(
                self._capabilities.get("documentHighlightProvider"),
                "textDocument/documentHighlight",
                "documentHighlightProvider missing",
            )
        if feature == "hover":
            return _provider_status(self._capabilities.get("hoverProvider"), "textDocument/hover", "hoverProvider missing")
        if feature == "inlay-hint":
            if provider_enabled(self._raw_capabilities.get("inlayHintProvider")):
                return {"supported": True, "method": "textDocument/inlayHint"}
            return {"supported": False, "method": "textDocument/inlayHint", "detail": "inlayHintProvider missing"}
        if feature in {"reference", "references"}:
            return _provider_status(self._capabilities.get("referencesProvider"), "textDocument/references", "referencesProvider missing")
        if feature == "rename":
            return _provider_status(self._capabilities.get("renameProvider"), "textDocument/rename", "renameProvider missing")
        if feature == "signature-help":
            return _provider_status(
                self._capabilities.get("signatureHelpProvider"),
                "textDocument/signatureHelp",
                "signatureHelpProvider missing",
            )
        if feature == "type-definition":
            return _provider_status(
                self._capabilities.get("typeDefinitionProvider"),
                "textDocument/typeDefinition",
                "typeDefinitionProvider missing",
            )
        return {"supported": False, "method": feature, "detail": "unknown feature"}

    def _position_params(self, file_path: str, line: int, character: int) -> dict[str, Any]:
        return {
            "textDocument": {"uri": Path(file_path).resolve().as_uri()},
            "position": {"line": line, "character": character},
        }

    def _pull_diagnostics(self, doc_uri: str) -> dict[str, Any]:
        params = {"textDocument": {"uri": doc_uri}}
        result = self.call_raw("textDocument/diagnostic", params) or {}
        kind = result.get("kind")
        if kind != "full":
            deadline = time.time() + 2.0
            while time.time() < deadline:
                if self.wait_for_refresh(0.5):
                    result = self.call_raw("textDocument/diagnostic", params) or {}
                    if result.get("kind") == "full":
                        break
                else:
                    time.sleep(0.1)
        items = result.get("items", [])
        if not isinstance(items, list):
            return {"uri": doc_uri, "diagnostics": []}
        diagnostics = [normalize_diagnostic(item) for item in items if isinstance(item, dict)]
        return {"uri": doc_uri, "diagnostics": diagnostics}


def _provider_status(provider: Any, method: str, missing_detail: str) -> dict[str, Any]:
    if provider_enabled(provider):
        return {"supported": True, "method": method}
    return {"supported": False, "method": method, "detail": missing_detail}


def _has_semantic_token_support(raw: dict[str, Any]) -> bool:
    return provider_enabled(raw.get("semanticTokensProvider"))


def _has_semantic_token_range_support(raw: dict[str, Any]) -> bool:
    provider = raw.get("semanticTokensProvider")
    if not isinstance(provider, dict):
        return False
    return provider_enabled(provider.get("range"))


def _document_range(content: str) -> dict[str, Any]:
    lines = content.split("\n")
    end_line = max(len(lines) - 1, 0)
    end_character = len(lines[end_line]) if lines else 0
    return {"start": {"line": 0, "character": 0}, "end": {"line": end_line, "character": end_character}}


def _client_capabilities() -> dict[str, Any]:
    return {
        "textDocument": {
            "publishDiagnostics": {"relatedInformation": True},
            "completion": {
                "contextSupport": True,
                "completionItem": {"documentationFormat": ["markdown", "plaintext"], "snippetSupport": True},
            },
            "hover": {"contentFormat": ["markdown", "plaintext"]},
            "signatureHelp": {
                "contextSupport": True,
                "signatureInformation": {
                    "documentationFormat": ["markdown", "plaintext"],
                    "parameterInformation": {"labelOffsetSupport": True},
                    "activeParameterSupport": True,
                },
            },
            "definition": {"linkSupport": True},
            "typeDefinition": {"linkSupport": True},
            "references": {},
            "documentHighlight": {},
            "rename": {"prepareSupport": True, "prepareSupportDefaultBehavior": 1},
            "semanticTokens": {
                "requests": {"range": True, "full": True},
                "tokenTypes": [
                    "namespace",
                    "type",
                    "class",
                    "enum",
                    "interface",
                    "struct",
                    "typeParameter",
                    "parameter",
                    "variable",
                    "property",
                    "enumMember",
                    "event",
                    "function",
                    "method",
                    "macro",
                    "keyword",
                    "modifier",
                    "comment",
                    "string",
                    "number",
                    "regexp",
                    "operator",
                ],
                "tokenModifiers": [
                    "declaration",
                    "definition",
                    "readonly",
                    "static",
                    "deprecated",
                    "abstract",
                    "async",
                    "modification",
                    "documentation",
                    "defaultLibrary",
                ],
                "formats": ["relative"],
                "overlappingTokenSupport": True,
                "multilineTokenSupport": True,
            },
        },
        "workspace": {"workspaceEdit": {"documentChanges": True}},
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

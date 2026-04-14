from __future__ import annotations

from typing import Any


def normalize_hover_result(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, Any] = {"contents": normalize_content_blocks(raw.get("contents"))}
    rng = normalize_range_from_any(raw.get("range"))
    if rng:
        result["range"] = rng
    return result


def normalize_completion_result(raw: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"isIncomplete": False, "items": []}
    if raw is None:
        return result
    if isinstance(raw, list):
        result["items"] = normalize_completion_items(raw)
        return result
    if isinstance(raw, dict):
        result["isIncomplete"] = bool(raw.get("isIncomplete", False))
        result["items"] = normalize_completion_items(raw.get("items", []))
    return result


def normalize_highlight_result(raw: Any) -> list[dict[str, Any]]:
    if raw is None or not isinstance(raw, list):
        return []
    results: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rng = normalize_range_from_any(item.get("range"))
        if not rng:
            continue
        out = {"range": rng}
        kind = normalize_highlight_kind(number_value(item.get("kind")))
        if kind:
            out["kind"] = kind
        results.append(out)
    return results


def normalize_location_result(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        loc = map_to_location(raw)
        return [loc] if loc else []
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        loc = map_to_location(item)
        if loc:
            result.append(loc)
    return result


def normalize_rename_result(raw: Any) -> dict[str, Any]:
    if raw is None or not isinstance(raw, dict):
        return {"changes": []}
    by_uri: dict[str, list[dict[str, Any]]] = {}
    changes = raw.get("changes", {})
    if isinstance(changes, dict):
        for uri, edits in changes.items():
            if not isinstance(uri, str) or not isinstance(edits, list):
                continue
            for edit in edits:
                normalized = normalize_text_edit_from_any(edit)
                if normalized:
                    by_uri.setdefault(uri, []).append(normalized)

    document_changes = raw.get("documentChanges", [])
    if isinstance(document_changes, list):
        for change in document_changes:
            if not isinstance(change, dict):
                continue
            text_document = change.get("textDocument", {})
            if not isinstance(text_document, dict):
                continue
            uri = string_value(text_document.get("uri"))
            if not uri:
                continue
            edits = change.get("edits", [])
            if not isinstance(edits, list):
                continue
            for edit in edits:
                normalized = normalize_text_edit_from_any(edit)
                if normalized:
                    by_uri.setdefault(uri, []).append(normalized)

    out = [{"uri": uri, "edits": by_uri[uri]} for uri in sorted(by_uri.keys())]
    return {"changes": out}


def normalize_signature_help_result(raw: Any) -> dict[str, Any]:
    if raw is None or not isinstance(raw, dict):
        return {}
    result: dict[str, Any] = {}
    if "activeSignature" in raw:
        result["activeSignature"] = int(number_value(raw.get("activeSignature")))
    if "activeParameter" in raw:
        result["activeParameter"] = int(number_value(raw.get("activeParameter")))
    signatures = raw.get("signatures", [])
    if isinstance(signatures, list):
        out_sigs: list[dict[str, Any]] = []
        for signature in signatures:
            if not isinstance(signature, dict):
                continue
            out_sig: dict[str, Any] = {
                "label": string_value(signature.get("label")),
                "documentation": normalize_content_blocks(signature.get("documentation")),
            }
            if signature.get("activeParameter") is not None:
                out_sig["activeParameter"] = int(number_value(signature.get("activeParameter")))
            params = signature.get("parameters", [])
            if isinstance(params, list):
                out_sig["parameters"] = [normalize_parameter(parameter) for parameter in params if isinstance(parameter, dict)]
            out_sigs.append(out_sig)
        result["signatures"] = out_sigs
    return result


def normalize_inlay_hint_result(raw: Any) -> list[dict[str, Any]]:
    if raw is None or not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for hint in raw:
        if not isinstance(hint, dict):
            continue
        position = hint.get("position", {})
        if not isinstance(position, dict):
            continue
        item: dict[str, Any] = {
            "position": {
                "line": int(number_value(position.get("line"))),
                "character": int(number_value(position.get("character"))),
            },
            "label": normalize_inlay_label(hint.get("label")),
            "paddingLeft": bool(hint.get("paddingLeft", False)),
            "paddingRight": bool(hint.get("paddingRight", False)),
            "tooltip": normalize_content_blocks(hint.get("tooltip")),
            "textEdits": [],
        }
        kind = normalize_inlay_kind(number_value(hint.get("kind")))
        if kind:
            item["kind"] = kind
        edits = hint.get("textEdits", [])
        if isinstance(edits, list):
            for edit in edits:
                normalized = normalize_text_edit_from_any(edit)
                if normalized:
                    item["textEdits"].append(normalized)
        result.append(item)
    return result


def normalize_annotation_result(raw: Any, legend: dict[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {"legend": legend, "tokens": []}
    if raw is None or not isinstance(raw, dict):
        return result
    data = raw.get("data", [])
    if not isinstance(data, list):
        return result
    line = 0
    start = 0
    token_types = legend.get("tokenTypes", []) if legend else []
    token_mods = legend.get("tokenModifiers", []) if legend else []
    idx = 0
    while idx + 4 < len(data):
        line_delta = int(number_value(data[idx]))
        start_delta = int(number_value(data[idx + 1]))
        length = int(number_value(data[idx + 2]))
        type_index = int(number_value(data[idx + 3]))
        modifier_bits = int(number_value(data[idx + 4]))
        line += line_delta
        start = start + start_delta if line_delta == 0 else start_delta
        token: dict[str, Any] = {
            "line": line,
            "startCharacter": start,
            "length": length,
            "tokenTypeIndex": type_index,
            "tokenModifierBits": modifier_bits,
        }
        if 0 <= type_index < len(token_types):
            token["tokenType"] = token_types[type_index]
        token_modifiers: list[str] = []
        for mod_idx, modifier in enumerate(token_mods):
            if modifier_bits & (1 << mod_idx):
                token_modifiers.append(modifier)
        if token_modifiers:
            token["tokenModifiers"] = token_modifiers
        result["tokens"].append(token)
        idx += 5
    return result


def normalize_diagnostic(diag: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "severity": normalize_diagnostic_severity(number_value(diag.get("severity"))),
        "code": normalize_diagnostic_code(diag.get("code")),
        "source": string_value(diag.get("source")),
        "message": string_value(diag.get("message")).strip(),
        "range": normalize_range_from_any(diag.get("range")) or {
            "start": {"line": 0, "character": 0},
            "end": {"line": 0, "character": 0},
        },
    }
    code_description = diag.get("codeDescription")
    if isinstance(code_description, dict):
        href = string_value(code_description.get("href"))
        if href:
            out["codeDescription"] = href
    related_info = diag.get("relatedInformation", [])
    if isinstance(related_info, list):
        out_info: list[dict[str, Any]] = []
        for info in related_info:
            if not isinstance(info, dict):
                continue
            location = info.get("location")
            if not isinstance(location, dict):
                continue
            loc = map_to_location(location)
            if not loc:
                continue
            out_info.append({"location": loc, "message": string_value(info.get("message"))})
        if out_info:
            out["relatedInformation"] = out_info
    return out


def normalize_parameter(parameter: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"documentation": normalize_content_blocks(parameter.get("documentation"))}
    label = parameter.get("label")
    if isinstance(label, str):
        result["label"] = label
    elif isinstance(label, list):
        offsets = [int(number_value(item)) for item in label]
        result["labelOffsets"] = offsets
    elif label is not None:
        result["label"] = str(label)
    return result


def normalize_completion_items(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out: dict[str, Any] = {
            "label": string_value(item.get("label")),
            "kind": normalize_completion_kind(number_value(item.get("kind"))),
            "detail": string_value(item.get("detail")),
            "documentation": normalize_content_blocks(item.get("documentation")),
            "insertText": string_value(item.get("insertText")),
            "filterText": string_value(item.get("filterText")),
            "sortText": string_value(item.get("sortText")),
            "deprecated": bool(item.get("deprecated", False)),
            "preselect": bool(item.get("preselect", False)),
            "commitCharacters": item.get("commitCharacters", []),
        }
        text_edit = normalize_text_edit_from_any(item.get("textEdit"))
        if text_edit:
            out["textEdit"] = text_edit
        additional = item.get("additionalTextEdits", [])
        if isinstance(additional, list):
            out["additionalTextEdits"] = [ed for ed in (normalize_text_edit_from_any(edit) for edit in additional) if ed]
        result.append(out)
    return result


def normalize_content_blocks(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, str):
        return [{"kind": "plaintext", "value": value}]
    if isinstance(value, dict):
        block = normalize_content_block_map(value)
        if block:
            return [block]
        return [{"value": str(value)}]
    if isinstance(value, list):
        result: list[dict[str, Any]] = []
        for item in value:
            result.extend(normalize_content_blocks(item))
        return result
    return [{"value": str(value)}]


def normalize_content_block_map(value: dict[str, Any]) -> dict[str, Any] | None:
    kind = value.get("kind")
    text = value.get("value")
    if isinstance(kind, str) and isinstance(text, str):
        return {"kind": kind, "value": text}
    if isinstance(text, str):
        language = value.get("language")
        if isinstance(language, str) and language:
            return {"kind": language, "value": text}
        return {"kind": "plaintext", "value": text}
    return None


def normalize_text_edit_from_any(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {"newText": string_value(value.get("newText"))}
    annotation = string_value(value.get("annotationId"))
    if annotation:
        result["annotationId"] = annotation
    rng = normalize_range_from_any(value.get("range"))
    if rng:
        result["range"] = rng
    ins = normalize_range_from_any(value.get("insert"))
    if ins:
        result["insertRange"] = ins
    rep = normalize_range_from_any(value.get("replace"))
    if rep:
        result["replaceRange"] = rep
    return result


def normalize_range_from_any(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    start = value.get("start")
    end = value.get("end")
    if not isinstance(start, dict) or not isinstance(end, dict):
        return None
    return {
        "start": {"line": int(number_value(start.get("line"))), "character": int(number_value(start.get("character")))},
        "end": {"line": int(number_value(end.get("line"))), "character": int(number_value(end.get("character")))},
    }


def map_to_location(item: dict[str, Any]) -> dict[str, Any] | None:
    raw_uri = item.get("uri")
    if isinstance(raw_uri, str):
        rng = normalize_range_from_any(item.get("range"))
        if not rng:
            return None
        result: dict[str, Any] = {"uri": raw_uri, "range": rng}
        selection = normalize_range_from_any(item.get("selectionRange"))
        if selection:
            result["selectionRange"] = selection
        origin = normalize_range_from_any(item.get("originSelectionRange"))
        if origin:
            result["originSelectionRange"] = origin
        return result

    target_uri = item.get("targetUri")
    if not isinstance(target_uri, str):
        return None
    target_range = normalize_range_from_any(item.get("targetRange"))
    if not target_range:
        return None
    result = {"uri": target_uri, "range": target_range}
    selection = normalize_range_from_any(item.get("targetSelectionRange"))
    if selection:
        result["selectionRange"] = selection
    origin = normalize_range_from_any(item.get("originSelectionRange"))
    if origin:
        result["originSelectionRange"] = origin
    return result


def normalize_inlay_label(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = string_value(item.get("value"))
                if text:
                    parts.append(text)
        return "".join(parts)
    return str(value)


def normalize_diagnostic_code(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(int(value))
    return str(value)


def normalize_diagnostic_severity(value: float) -> str:
    if int(value) == 1:
        return "error"
    if int(value) == 2:
        return "warning"
    if int(value) == 3:
        return "information"
    if int(value) == 4:
        return "hint"
    return "unknown"


def normalize_completion_kind(value: float) -> str:
    names = {
        1: "text",
        2: "method",
        3: "function",
        4: "constructor",
        5: "field",
        6: "variable",
        7: "class",
        8: "interface",
        9: "module",
        10: "property",
        11: "unit",
        12: "value",
        13: "enum",
        14: "keyword",
        15: "snippet",
        16: "color",
        17: "file",
        18: "reference",
        19: "folder",
        20: "enumMember",
        21: "constant",
        22: "struct",
        23: "event",
        24: "operator",
        25: "typeParameter",
    }
    return names.get(int(value), "")


def normalize_highlight_kind(value: float) -> str:
    return {1: "text", 2: "read", 3: "write"}.get(int(value), "")


def normalize_inlay_kind(value: float) -> str:
    return {1: "type", 2: "parameter"}.get(int(value), "")


def extract_semantic_legend(raw_capabilities: dict[str, Any]) -> dict[str, Any] | None:
    provider = raw_capabilities.get("semanticTokensProvider")
    if not isinstance(provider, dict):
        return None
    legend = provider.get("legend")
    if not isinstance(legend, dict):
        return None
    token_types = [item for item in legend.get("tokenTypes", []) if isinstance(item, str)]
    token_modifiers = [item for item in legend.get("tokenModifiers", []) if isinstance(item, str)]
    if not token_types and not token_modifiers:
        return None
    return {"tokenTypes": token_types, "tokenModifiers": token_modifiers}


def string_value(value: Any) -> str:
    return value if isinstance(value, str) else ""


def number_value(value: Any) -> float:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0

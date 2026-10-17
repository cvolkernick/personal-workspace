"""Load snapshot_v1, validate it, and hash the canonical body.

The JSON Schema file declares structure. Share-sum and the required class-id
set are `x-share-sum` and `x-class-ids` on that file because standard JSON
Schema cannot sum an array. Validation always applies both.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = PACKAGE_ROOT / "schemas" / "snapshot_v1.json"

_SCHEMA: dict[str, Any] | None = None


class SchemaError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def load_schema() -> dict[str, Any]:
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return _SCHEMA


def to_jsonable(value: Any) -> Any:
    """Convert Decimals to ints or floats that survive a JSON round trip."""
    if isinstance(value, Decimal):
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        if text in ("", "-"):
            text = "0"
        if "." not in text:
            return int(text)
        as_float = float(text)
        dumped = json.dumps(as_float)
        if dumped == text or json.loads(dumped) == as_float and dumped == json.dumps(json.loads(text)):
            return json.loads(text)
        return json.loads(text)
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [to_jsonable(v) for v in value]
    return value


def canonical_text(doc: dict[str, Any]) -> str:
    body = {k: v for k, v in doc.items() if k != "sha256"}
    return json.dumps(
        to_jsonable(body),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def body_sha256(doc: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_text(doc).encode("utf-8")).hexdigest()


def pretty_bytes(doc: dict[str, Any]) -> bytes:
    text = json.dumps(to_jsonable(doc), indent=2, sort_keys=True, ensure_ascii=False)
    return (text + "\n").encode("utf-8")


def seal(doc: dict[str, Any]) -> dict[str, Any]:
    """Return a jsonable snapshot with sha256 set. Raises SchemaError if invalid."""
    body = {k: v for k, v in doc.items() if k != "sha256"}
    sealed = to_jsonable(body)
    sealed["sha256"] = body_sha256(sealed)
    errors = validate(sealed)
    if errors:
        raise SchemaError(errors)
    return sealed


def validate(doc: dict[str, Any]) -> list[str]:
    schema = load_schema()
    errors: list[str] = []
    _validate_node(doc, schema, "$", schema, errors)
    _check_invariants(doc, schema, errors)
    if isinstance(doc, dict) and isinstance(doc.get("sha256"), str):
        expect = body_sha256(doc)
        if doc["sha256"] != expect:
            errors.append("sha256 mismatch")
    return errors


def _check_invariants(doc: Any, schema: dict[str, Any], errors: list[str]) -> None:
    if not isinstance(doc, dict):
        return
    share_rule = schema.get("x-share-sum")
    classes = doc.get("classes")
    if isinstance(share_rule, dict) and isinstance(classes, list):
        total = Decimal(0)
        ok = True
        for item in classes:
            if not isinstance(item, dict) or "share_pct" not in item:
                ok = False
                break
            try:
                total += Decimal(str(item["share_pct"]))
            except Exception:
                ok = False
                break
        if ok:
            target = Decimal(str(share_rule.get("total", 100)))
            tol = Decimal(str(share_rule.get("tolerance", "0.01")))
            if abs(total - target) > tol:
                errors.append(
                    f"shares sum {total} outside {target} ± {tol}"
                )
    expected_ids = schema.get("x-class-ids")
    if isinstance(expected_ids, list) and isinstance(classes, list):
        found = [c.get("id") for c in classes if isinstance(c, dict)]
        if found != expected_ids:
            errors.append(f"class ids {found} != {expected_ids}")


def _type_ok(value: Any, spec: Any) -> bool:
    names = spec if isinstance(spec, list) else [spec]
    for name in names:
        if name == "null" and value is None:
            return True
        if name == "string" and isinstance(value, str):
            return True
        if name == "boolean" and isinstance(value, bool):
            return True
        if name == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if name == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if name == "object" and isinstance(value, dict):
            return True
        if name == "array" and isinstance(value, list):
            return True
    return False


def _resolve(ref: str, root: dict[str, Any]) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise SchemaError([f"unsupported $ref {ref}"])
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _validate_node(value: Any, schema: dict[str, Any], path: str, root: dict[str, Any], errors: list[str]) -> None:
    if "$ref" in schema:
        _validate_node(value, _resolve(schema["$ref"], root), path, root, errors)
        return
    if "type" in schema and not _type_ok(value, schema["type"]):
        errors.append(f"{path}: expected {schema['type']}")
        return
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in enum")
    if isinstance(value, str):
        min_len = schema.get("minLength")
        if isinstance(min_len, int) and len(value) < min_len:
            errors.append(f"{path}: shorter than {min_len}")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.fullmatch(pattern, value) is None:
            errors.append(f"{path}: does not match {pattern}")
    if isinstance(value, int) and not isinstance(value, bool) and "minimum" in schema:
        if value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        required = schema.get("required") or []
        for key in required:
            if key not in value:
                errors.append(f"{path}: missing required {key}")
        additional = schema.get("additionalProperties", True)
        for key, child in value.items():
            if key in props:
                _validate_node(child, props[key], f"{path}.{key}", root, errors)
            elif additional is False:
                errors.append(f"{path}: unknown property {key}")
            elif isinstance(additional, dict):
                _validate_node(child, additional, f"{path}.{key}", root, errors)
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, child in enumerate(value):
                _validate_node(child, item_schema, f"{path}[{i}]", root, errors)
        needle = schema.get("x-contains")
        if isinstance(needle, str):
            if not any(isinstance(item, str) and needle in item for item in value):
                errors.append(f"{path}: missing caveat {needle!r}")

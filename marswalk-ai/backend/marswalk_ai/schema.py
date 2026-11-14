"""A small, strict JSON-Schema validator (the subset used by capability schemas).

Supported keywords: type (string or list), enum, properties, required,
additionalProperties (bool), items, minItems, maxItems, minimum, maximum,
minLength, maxLength, description (ignored).

Kept in-house so validation errors are predictable and there is no extra dependency.
Booleans are never accepted as numbers or integers.
"""
from __future__ import annotations

import math
from typing import Any

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v),
    "null": lambda v: v is None,
}


def validate(instance: Any, schema: dict, path: str = "$") -> list[str]:
    """Return a list of human-readable errors (empty when valid)."""
    errors: list[str] = []
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPES[x](instance) for x in types):
            return [f"{path}: expected {' or '.join(types)}, got {type(instance).__name__}"]
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} is not one of {schema['enum']}")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} is below the minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance} is above the maximum {schema['maximum']}")
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: string shorter than {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: string longer than {schema['maxLength']}")
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: needs at least {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: allows at most {schema['maxItems']} items")
        if "items" in schema:
            for i, item in enumerate(instance):
                errors.extend(validate(item, schema["items"], f"{path}[{i}]"))
    if isinstance(instance, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required property '{key}'")
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in props:
                    errors.append(f"{path}: unexpected property '{key}'")
        for key, sub in props.items():
            if key in instance:
                errors.extend(validate(instance[key], sub, f"{path}.{key}"))
    return errors


def apply_defaults(instance: dict, schema: dict) -> dict:
    """Fill top-level 'default' values for missing optional properties."""
    out = dict(instance)
    for key, sub in schema.get("properties", {}).items():
        if key not in out and "default" in sub:
            out[key] = sub["default"]
    return out


def obj(properties: dict, required: list[str] | None = None, description: str | None = None) -> dict:
    """Helper: a closed object schema."""
    s = {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}
    if description:
        s["description"] = description
    return s


# Reusable fragments
LAT = {"type": "number", "minimum": -90, "maximum": 90, "description": "Planetocentric latitude, degrees north"}
LON = {"type": "number", "minimum": -180, "maximum": 180, "description": "East-positive longitude, degrees (-180..180)"}

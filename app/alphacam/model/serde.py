"""Dataclass <-> JSON helpers for the .alcam format.

Points are stored as [x, z]; polymorphic objects (geometry, operations) carry a "type" key."""

from __future__ import annotations

from ..i18n import tr

import dataclasses
import types
import typing

from .geometry import Point


def to_json(obj):
    if isinstance(obj, Point):
        return obj.to_json()
    if dataclasses.is_dataclass(obj):
        out = {}
        if isinstance(getattr(type(obj), "type", None), str):
            out["type"] = type(obj).type
        hints = typing.get_type_hints(type(obj))
        for f in dataclasses.fields(obj):
            value = getattr(obj, f.name)
            if hints[f.name] is float and isinstance(value, int) and not isinstance(value, bool):
                value = float(value)  # stable text: 55 and 55.0 serialise the same
            out[f.name] = to_json(value)
        return out
    if isinstance(obj, (list, tuple)):
        return [to_json(v) for v in obj]
    if isinstance(obj, dict):
        return {k: to_json(v) for k, v in obj.items()}
    return obj


def from_json(cls, data, registry: dict[str, type] | None = None):
    """Build a dataclass instance of `cls` (or of registry[data["type"]]) from parsed JSON."""
    if registry is not None:
        try:
            cls = registry[data["type"]]
        except KeyError:
            raise ValueError(tr("unknown element or operation type {type}", type=repr(data.get("type")))) from None
    hints = typing.get_type_hints(cls)
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = set(data) - known - {"type"}
    if unknown:
        raise ValueError(tr("{what}: unknown keys {keys}", what=cls.__name__, keys=sorted(unknown)))
    kwargs = {name: _convert(hints[name], value) for name, value in data.items() if name in known}
    return cls(**kwargs)


def _convert(hint, value):
    if value is None:
        return None
    origin = typing.get_origin(hint)
    if hint is Point:
        return Point.from_json(value)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(hint) if a is not type(None)]
        if len(args) == 1:
            return _convert(args[0], value)
        return value
    if origin is list:
        (item,) = typing.get_args(hint)
        return [_convert(item, v) for v in value]
    if dataclasses.is_dataclass(hint):
        return from_json(hint, value)
    if hint is float and isinstance(value, int):
        return float(value)
    return value

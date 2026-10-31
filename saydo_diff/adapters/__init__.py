"""Receipt importers: turn other tools' audit logs into say-do-diff tool logs.

    from saydo_diff.adapters import import_log
    imported = import_log("session_events.log", "planlock")
    imported.calls      # say-do-diff tool-log entries (dicts)
    imported.plans      # planlock plans with step status (empty for other formats)
    imported.meta, imported.warnings

Formats: "native" (say-do-diff's own JSON/JSONL log), "planlock", "agt", or "auto".
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ._common import infer_action
from .agt import import_agt, looks_like as _is_agt
from .planlock import import_planlock, looks_like as _is_planlock

FORMATS = ("native", "planlock", "agt", "auto")


@dataclass
class Imported:
    format: str
    calls: list
    plans: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


def _peek(path: str) -> list:
    with open(path, encoding="utf-8") as fh:
        text = fh.read(1_000_000)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = []
        for line in text.splitlines()[:50]:
            try:
                data.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if isinstance(data, dict):
        data = data.get("entries") or data.get("calls") or [data]
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def detect(path: str) -> str:
    rows = _peek(path)
    if _is_planlock(rows):
        return "planlock"
    if _is_agt(rows):
        return "agt"
    return "native"


def load_mapping(path: str | None) -> dict | None:
    """A JSON object {tool name: action}. Use "read" for lookup tools that should not count."""
    if not path:
        return None
    with open(path, encoding="utf-8") as fh:
        m = json.load(fh)
    if not isinstance(m, dict):
        raise ValueError(f"{path}: the tool map must be a JSON object of tool name -> action")
    return m.get("tools", m) if isinstance(m.get("tools"), dict) else m


def import_log(path: str, fmt: str = "auto", mapping: dict | None = None, **opts) -> Imported:
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt!r}; use one of {', '.join(FORMATS)}")
    if fmt == "auto":
        fmt = detect(path)
    if fmt == "native":
        from ..core import load_log
        return Imported("native", [{k: v for k, v in c.items() if not k.startswith("_")} for c in load_log(path)],
                        meta={"format": "native"})
    fn = import_planlock if fmt == "planlock" else import_agt
    calls, plans, meta, warnings = fn(path, mapping=mapping, **opts)
    return Imported(fmt, calls, plans, meta, warnings)


__all__ = ["FORMATS", "Imported", "detect", "import_log", "import_agt", "import_planlock", "infer_action",
           "load_mapping"]

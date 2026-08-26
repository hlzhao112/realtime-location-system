from __future__ import annotations

import re

_SPACE = re.compile(r"[\s:\-]+")


def norm_epc(value: str | None) -> str:
    if not value:
        return ""
    return _SPACE.sub("", str(value)).upper()


def pretty_epc(value: str | None) -> str:
    n = norm_epc(value)
    if not n:
        return ""
    return " ".join(n[i : i + 2] for i in range(0, len(n), 2))


def cell_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()

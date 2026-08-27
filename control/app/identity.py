"""Canonical SIM line identity.

The durable key for a SIM line is its ICCID. Two sources disagree about spelling:

* PC/SC EF.ICCID nibble-swap decode emits lowercase hex and strips a trailing F pad.
* ModemManager's ``sim.properties.iccid`` often returns the same digits in uppercase,
  sometimes still carrying that pad.

Equality must therefore be case-insensitive and padding-tolerant. This module never
reads or stores Ki/OP/OPc; AKA stays on the physical card.
"""
from __future__ import annotations


def normalize_iccid(value) -> str:
    """Return the case-folded ICCID used for matching and durable tracking.

    Non-hex test fixtures (``card-a``) are only case-folded so existing tests keep a
    stable key. A hexadecimal identity of typical ICCID length also drops trailing F
    padding, which is an encoding artefact rather than part of the number.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    folded = raw.casefold()
    if len(folded) >= 15 and all(ch in "0123456789abcdef" for ch in folded):
        return folded.rstrip("f")
    return folded


def iccids_equal(left, right) -> bool:
    """True when both values name the same ICCID after normalisation."""
    a, b = normalize_iccid(left), normalize_iccid(right)
    return bool(a) and a == b

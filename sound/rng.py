"""`engine/rng.js`, in Python. Value for value.

The picture chooses a photograph with `hash(seed, cell, epoch, 401)`. The sound
chooses a grain the same way, from the same seed — which is only true if the two
hashes agree exactly, including across the sign boundary where JavaScript's
`Math.imul` and `>>> 0` do things Python's unbounded integers do not.

This is its own module, with NO imports, for one reason: `scripts/check-danse.py`
holds it against the JavaScript on every run, and an invariant that can only be
checked where numpy is installed is an invariant that gets skipped.

32-bit throughout, matching the note in rng.js: JavaScript has no native 64-bit
integer arithmetic, and a 32-bit avalanche has ample decorrelation for choosing
among 162 photographs or 265 grains.
"""

from __future__ import annotations

MASK = 0xFFFFFFFF


def _imul(a: int, b: int) -> int:
    """`Math.imul` — 32-bit multiply, wrapping."""
    return ((a & MASK) * (b & MASK)) & MASK


def _avalanche(h: int) -> int:
    h ^= h >> 16
    h = _imul(h, 0x85EBCA6B)
    h ^= h >> 13
    h = _imul(h, 0xC2B2AE35)
    h ^= h >> 16
    return h & MASK


def hash32(*words: int) -> int:
    """Hash any number of integers to a uint32. Order matters: hash(1,2) != hash(2,1)."""
    h = 0x9E3779B9
    for w in words:
        h = _imul(h ^ (int(w) & MASK), 0x5BD1E995)
        h = ((h << 15) | (h >> 17)) & MASK
    return _avalanche(h)


def rand(*words: int) -> float:
    """Uniform in [0, 1)."""
    return hash32(*words) / 4294967296.0


def pick(items: list, *words: int):
    """Pick one element deterministically."""
    return items[hash32(*words) % len(items)] if items else None

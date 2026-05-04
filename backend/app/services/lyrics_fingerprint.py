"""Non-reversible MinHash fingerprint of submitted lyrics.

Layer 2 of the Lyrical Charger paste-path guards (see
LC-LYRICS-GUARDS.md). Detects "two runs claim the same (title, artist) but
the lyrics are radically different" without ever storing raw lyric text.

  fp = compute_fingerprint(lyrics)        # 512 bytes or None
  jaccard(fp_a, fp_b)                     # 0.0-1.0 similarity
  max_jaccard(fp, [fp1, fp2, ...])        # best match across prior runs

The signature is a 128-function MinHash over word 5-shingles. Two
substantially identical lyric texts converge near 1.0; two unrelated
texts hover near 0.0. Cannot be inverted to recover lyrics.

Hard legal constraint: lyrics text is never stored. The fingerprint is the
only persisted artifact and is intentionally lossy.
"""

import hashlib
import random
import re
import struct

# --- Tunables ------------------------------------------------------------
NUM_FUNCS = 128         # signature length; 512 bytes per row
SHINGLE_K = 5           # word-level n-gram size
_PRIME = (1 << 61) - 1  # Mersenne prime for permutation arithmetic
_PERM_SEED = 0xC0FFEE   # fixed across processes — fingerprints must be comparable

# Default rejection threshold for the divergence guard. Below this, the
# new submission is treated as "different lyrics for the same identity"
# and rejected before calibration. Tune against real data after rollout.
DIVERGENCE_THRESHOLD = 0.25


def _gen_perms() -> list[tuple[int, int]]:
    rng = random.Random(_PERM_SEED)
    return [
        (rng.randint(1, _PRIME - 1), rng.randint(0, _PRIME - 1))
        for _ in range(NUM_FUNCS)
    ]


_PERMS = _gen_perms()


def _normalize_words(text: str) -> list[str]:
    """Lowercase + word-character split. Punctuation and section markers
    like [Chorus] become noise that's filtered out by the \\w+ class."""
    if not text:
        return []
    return re.findall(r"\w+", text.lower())


def _hash_shingle(s: str) -> int:
    """Deterministic 64-bit hash of a shingle. blake2b is used because
    Python's built-in hash() is process-randomized and would make
    fingerprints non-comparable across requests."""
    return int.from_bytes(
        hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(),
        "big",
    )


def _shingle_hashes(words: list[str], k: int = SHINGLE_K) -> set[int]:
    if not words:
        return set()
    if len(words) < k:
        # Short inputs: degrade to a single bag-of-words shingle. Better
        # than rejecting; the divergence guard mostly fires on long
        # submissions anyway.
        return {_hash_shingle(" ".join(words))}
    return {
        _hash_shingle(" ".join(words[i : i + k]))
        for i in range(len(words) - k + 1)
    }


def compute_fingerprint(lyrics: str | None) -> bytes | None:
    """Return a 512-byte MinHash signature, or None for empty input."""
    words = _normalize_words(lyrics or "")
    shingles = _shingle_hashes(words, SHINGLE_K)
    if not shingles:
        return None
    sig = []
    for a, b in _PERMS:
        sig.append(min((a * h + b) % _PRIME for h in shingles) & 0xFFFFFFFF)
    return struct.pack(f">{NUM_FUNCS}I", *sig)


def jaccard(a: bytes | None, b: bytes | None) -> float:
    """Estimate Jaccard similarity between two fingerprints (0.0-1.0).
    Returns 0.0 if either side is missing or malformed — a missing
    fingerprint contributes no signal, so it neither supports nor blocks.
    """
    expected_len = NUM_FUNCS * 4
    if not a or not b or len(a) != expected_len or len(b) != expected_len:
        return 0.0
    sig_a = struct.unpack(f">{NUM_FUNCS}I", a)
    sig_b = struct.unpack(f">{NUM_FUNCS}I", b)
    matches = sum(1 for x, y in zip(sig_a, sig_b) if x == y)
    return matches / NUM_FUNCS


def max_jaccard(fp: bytes | None, others: list[bytes | None]) -> float:
    """Best similarity score of `fp` against any non-null entry in `others`."""
    if not fp or not others:
        return 0.0
    best = 0.0
    for o in others:
        if not o:
            continue
        j = jaccard(fp, o)
        if j > best:
            best = j
    return best

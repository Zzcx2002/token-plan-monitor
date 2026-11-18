"""Pieces shared by the two request signers.

Only the transport-shaped bits live here: the signed-request value object,
the SHA-256 helper and the empty-body digest. The signing PROTOCOLS stay
deliberately separate (Volcengine V4 vs Alibaba ACS3 — canonical header
selection and key derivation differ); see each signer's docstring.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

#: SHA-256 of an empty body, the value both protocols sign for empty payloads.
EMPTY_BODY_SHA256 = hashlib.sha256(b"").hexdigest()


@dataclass(frozen=True)
class SignedRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()

"""Security helpers for AWAP."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet

DEFAULT_SECRET_SEED = "awap-local-dev-secret"


def generate_bearer_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def encrypt_secret_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return _get_fernet().encrypt(raw).decode("utf-8")


def decrypt_secret_payload(ciphertext: str) -> dict[str, Any]:
    raw = _get_fernet().decrypt(ciphertext.encode("utf-8"))
    return json.loads(raw.decode("utf-8"))


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    seed = os.getenv("AWAP_SECRET_KEY", DEFAULT_SECRET_SEED)
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)

"""Password hashing and user auth utilities."""
from __future__ import annotations

import hashlib
import secrets


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"sha256${salt}${h}"


def check_password(password: str, stored: str) -> bool:
    parts = stored.split("$", 2)
    if len(parts) != 3:
        return False
    algo, salt, h = parts
    if algo == "sha256":
        return hashlib.sha256((salt + password).encode()).hexdigest() == h
    return False

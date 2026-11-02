"""Strict Elasticsearch connection and bounded JSON validation."""

from __future__ import annotations
import json
import re
from urllib.parse import urlsplit


class Failure(Exception):
    def __init__(self, code, message, *, dispatched=False):
        super().__init__(message)
        self.code = code
        self.dispatched = dispatched


def require(condition, message, *, code="invalid_payload"):
    if not condition:
        raise Failure(code, message)


def text(value, name, maximum=65536, *, nonempty=False):
    require(isinstance(value, str), f"{name} must be text.")
    require(
        len(value.encode()) <= maximum and (not nonempty or bool(value.strip())), f"Invalid {name}."
    )
    return value


def integer(value, name, low, high):
    require(type(value) is int and low <= value <= high, f"Invalid {name} bound.")
    return value


def bounded_json(value, limit=8 * 1024 * 1024):
    try:
        require(
            len(json.dumps(value, allow_nan=False).encode()) <= limit, "JSON byte budget exceeded."
        )
        nodes = 0

        def walk(v, depth):
            nonlocal nodes
            nodes += 1
            require(nodes <= 10000 and depth <= 20, "JSON complexity budget exceeded.")
            if isinstance(v, dict):
                for k, c in v.items():
                    require(isinstance(k, str), "JSON keys must be text.")
                    walk(c, depth + 1)
            elif isinstance(v, list):
                for c in v:
                    walk(c, depth + 1)

        walk(value, 0)
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise Failure("invalid_payload", "Bounded finite JSON required.") from exc


def index_name(value):
    require(
        isinstance(value, str)
        and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,254}", value)
        and ".." not in value,
        "Use a concrete lowercase index name.",
    )
    return value


def field(value):
    require(
        isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,127}", value),
        "Use explicit top-level field names.",
    )
    return value


def connection_values(connection):
    try:
        c = connection.get("config") or {}
        s = connection.get("secrets") or {}
        require(
            isinstance(c, dict) and set(c) <= {"origin", "indices", "request_timeout_seconds"},
            "Invalid config.",
        )
        require(
            isinstance(s, dict) and set(s) <= {"api_key", "username", "password"},
            "Invalid secrets.",
        )
        origin = text(c.get("origin"), "origin", 2048, nonempty=True)
        u = urlsplit(origin)
        require(
            u.scheme in {"http", "https"}
            and u.hostname
            and u.username is None
            and u.password is None
            and not u.path
            and "?" not in origin
            and "#" not in origin
            and not any(ch.isspace() or ch in "\\" for ch in origin),
            "Invalid origin.",
        )
        port = u.port if u.port is not None else (443 if u.scheme == "https" else 80)
        integer(port, "port", 1, 65535)
        indices = c.get("indices")
        require(
            isinstance(indices, list)
            and 1 <= len(indices) <= 32
            and len(set(indices)) == len(indices),
            "Explicit indices required.",
        )
        if not isinstance(indices, list):
            raise Failure("invalid_connection", "Explicit indices required.")
        indices = [index_name(i) for i in indices]
        for k, v in s.items():
            text(v, k, 8192, nonempty=True)
            require(not any(ord(ch) < 32 or ord(ch) == 127 for ch in v), "Invalid credential.")
        require(
            (set(s) == {"api_key"}) or (set(s) == {"username", "password"}),
            "Select exactly one authentication mode.",
        )
        return {
            "origin": origin,
            "host": u.hostname,
            "port": port,
            "tls": u.scheme == "https",
            "indices": indices,
            "request_timeout_seconds": integer(
                c.get("request_timeout_seconds", 15), "timeout", 1, 30
            ),
        }, s
    except (Failure, ValueError, TypeError, AttributeError) as exc:
        raise Failure(
            "invalid_connection", "Invalid Elasticsearch connection or credentials."
        ) from exc

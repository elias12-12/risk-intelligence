"""Two demo users, one map, no user management.

The scope is a demo with one analyst and one admin. A `users` table would imply
user management, and nothing here manages users — so identity is a static
`token -> (actor, role)` map and everything downstream consumes an actor
*string*. That is the part which survives: `case_outcomes.analyst_id`,
`rule_definitions.created_by` and `rule_versions.published_by` are all TEXT, so
replacing this module with real identity later changes this file and no other.

**This is not authentication.** Bearer tokens compared against a hardcoded map,
over plain HTTP, with no expiry, rotation, or per-request nonce. It establishes
*which of two demo users is acting* so the audit columns have something true to
record and the console knows which surfaces to render. It would be malpractice
in front of real money, and the README says so rather than leaving a reader to
discover it.

Reads stay OPEN. Every GET in this API was public before Week 5 and still is:
the read surfaces publish decisions about synthetic subjects, the test suite and
`bootstrap.ps1` call them without ceremony, and gating them would buy nothing
but friction. Authentication guards the two things that leave a mark — writing a
disposition, and (in session 3) writing a rule.

Roles are ORDERED, not a set: `admin` can do everything `analyst` can. A demo
with two users and a set-valued permission model would be modelling a problem it
does not have.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

# analyst < admin. The index in this tuple IS the ordering.
ROLES: tuple[str, ...] = ("analyst", "admin")

DEFAULT_TOKENS = "analyst-token:jane.analyst:analyst,admin-token:joe.admin:admin"


@dataclass(frozen=True)
class Principal:
    actor: str
    role: str

    def can(self, required: str) -> bool:
        return ROLES.index(self.role) >= ROLES.index(required)


def _parse(raw: str) -> dict[str, Principal]:
    """`token:actor:role,token:actor:role` -> the map.

    Parsed on every call rather than cached at import, so a test that sets
    GLASSBOX_API_TOKENS inside its own environment is honoured without
    reloading the module. Two entries make the cost irrelevant.
    """
    table: dict[str, Principal] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) != 3:
            raise ValueError(
                f"GLASSBOX_API_TOKENS entry {entry!r} is not token:actor:role")
        token, actor, role = (p.strip() for p in parts)
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}; expected one of {ROLES}")
        table[token] = Principal(actor=actor, role=role)
    return table


def tokens() -> dict[str, Principal]:
    return _parse(os.environ.get("GLASSBOX_API_TOKENS", DEFAULT_TOKENS))


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def principal(request: Request) -> Principal:
    """401 for absent or unknown credentials. Never 403 — that is about role."""
    token = _bearer(request)
    if token is None:
        raise HTTPException(
            status_code=401,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    found = tokens().get(token)
    if found is None:
        raise HTTPException(
            status_code=401,
            detail="unknown token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return found


def require_role(required: str):
    """A dependency that admits `required` and everything above it.

    Returns the Principal, so a route never has to ask who is acting a second
    time — the actor that reaches `case_outcomes.analyst_id` is the same object
    the check was made against, not a re-read of the header.
    """
    if required not in ROLES:  # pragma: no cover - programmer error
        raise ValueError(f"unknown role {required!r}")

    def dependency(who: Principal = Depends(principal)) -> Principal:
        if not who.can(required):
            raise HTTPException(
                status_code=403,
                detail=f"role {who.role!r} cannot act as {required!r}",
            )
        return who

    return dependency

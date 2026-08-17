"""Client -> MCP inbound authentication (docs/MCP_DESIGN.md §4).

The other hop — this server authenticating itself *to* the REST API — is
`client.py`'s job and uses `settings.api_key`. This module is the one that was
missing: verifying that whoever is calling *this* server is allowed to.

Deliberately not importing `dxb_api.auth`, even though the logic below is a
near-duplicate of it: `mcp/`'s pyproject bans importing `dxb_api` at all
(MCP_DESIGN.md §2 — this service must stay a pure HTTP client of the REST API,
never grow a second copy of its internals). There is nothing proprietary in
"SHA-256 hex digest + constant-time compare" to protect by sharing code — it
is a generic technique. What *is* shared, deliberately, is the wire *format*
({"name", "key_hash", "scopes"}), so the one key-generation command documented
in the root README produces valid entries for either service's config.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    """Who is calling this server. Only used for audit logging today."""

    subject: str


def hash_api_key(raw_key: str) -> str:
    """SHA-256 hex digest — matches dxb_api.auth.hash_api_key exactly.

    Not memory-hard on purpose: an API key is a high-entropy random secret
    with no dictionary to attack, unlike a human password, so a fast digest is
    cryptographically sufficient and is what keeps this check cheap enough to
    run before every single tool call.
    """
    return hashlib.sha256(raw_key.encode()).hexdigest()


def verify_api_key(configured_keys: list[dict], raw_key: str) -> Principal | None:
    """`None` on any failure — no key, empty key, or no match.

    `hmac.compare_digest`, not `==`: a plain string comparison returns on the
    first differing byte, which leaks how much of the key was guessed
    correctly through response timing. Every candidate is compared in full,
    every time, regardless of how many keys are configured.
    """
    if not raw_key:
        return None
    digest = hash_api_key(raw_key)
    for entry in configured_keys:
        if hmac.compare_digest(digest, entry.get("key_hash", "")):
            return Principal(subject=entry.get("name", "api-key"))
    return None

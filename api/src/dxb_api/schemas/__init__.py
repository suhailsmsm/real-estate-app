"""Pydantic response models — the public API contract.

Deliberately separate from the SQLAlchemy tables (CQRS-lite): serializing ORM
rows directly would weld the contract to the physical schema, making any
internal column rename a breaking API change."""

"""dxb_readonly role — read-only enforcement layer 1 of 3

The API connects as this role, which holds SELECT and nothing else, so a code
bug cannot write even if the transaction-level and surface-level guards were
both bypassed (docs/API_DESIGN.md §4).

ALTER DEFAULT PRIVILEGES is the part that is easy to omit and expensive to
discover: without it, tables created by *future* migrations are invisible to
the API, and the failure looks like a mysterious permission error weeks later.

The password comes from DXB_API_DB_PASSWORD rather than being hardcoded, so a
credential never lands in version control. Re-running the migration rotates
the password to whatever is currently in the environment.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-24
"""

from __future__ import annotations

import os

from sqlalchemy import text

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

ROLE = "dxb_readonly"


def upgrade() -> None:
    password = os.environ.get("DXB_API_DB_PASSWORD", "").strip()
    if not password:
        raise RuntimeError(
            "DXB_API_DB_PASSWORD must be set to create the read-only API role. "
            "Add it to .env (see .env.example)."
        )

    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar()

    # LOGIN but explicitly NOINHERIT/no other attributes: this role exists to
    # read and nothing else.
    bind.execute(
        text(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
                    CREATE ROLE {ROLE} LOGIN;
                END IF;
            END
            $$;
            """
        )
    )
    # ALTER ROLE is a utility statement and cannot take a bind parameter, so
    # the password has to be inlined. Have Postgres produce the quoted literal
    # rather than escaping it here — quote_literal *can* be parameterized, so
    # the value never reaches the parser unescaped.
    quoted_pw = bind.execute(
        text("SELECT quote_literal(:pw)"), {"pw": password}
    ).scalar()
    bind.execute(text(f"ALTER ROLE {ROLE} WITH PASSWORD {quoted_pw}"))
    # Belt and braces with the engine's connect-time setting: even a session
    # that somehow missed the connect args starts read-only.
    bind.execute(text(f"ALTER ROLE {ROLE} SET default_transaction_read_only = on"))

    bind.execute(text(f'GRANT CONNECT ON DATABASE "{db}" TO {ROLE}'))
    bind.execute(text(f"GRANT USAGE ON SCHEMA public TO {ROLE}"))
    bind.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {ROLE}"))
    bind.execute(text(f"GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO {ROLE}"))
    # Applies to tables created later by whichever role runs migrations.
    bind.execute(
        text(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT SELECT ON TABLES TO {ROLE}"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    db = bind.execute(text("SELECT current_database()")).scalar()
    bind.execute(
        text(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"REVOKE SELECT ON TABLES FROM {ROLE}"
        )
    )
    bind.execute(text(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE}"))
    bind.execute(text(f"REVOKE ALL ON SCHEMA public FROM {ROLE}"))
    bind.execute(text(f'REVOKE ALL ON DATABASE "{db}" FROM {ROLE}'))
    bind.execute(text(f"DROP ROLE IF EXISTS {ROLE}"))

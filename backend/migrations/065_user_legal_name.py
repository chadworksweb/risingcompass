"""Public Participation -- real-name display in the Deliberation Chamber.

Tier 2 (id_verified) users may have their verified legal name shown
publicly on their Chamber posts -- the room where accountability is the
point. Two additive columns on users:

  legal_name                     -- "First Last" pulled from Stripe Identity
                                    verified_outputs on the verified webhook.
  legal_name_public_consent_at   -- when the user explicitly consented, at
                                    the verify-identity step, to public
                                    display of that name. Display is gated on
                                    BOTH columns being populated, so a name
                                    captured without consent is never shown.

PG-compatible (063+). Both nullable + additive -> safe on the live DB;
older code ignores the columns.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE users "
        "ADD COLUMN IF NOT EXISTS legal_name TEXT"
    ))
    conn.execute(text(
        "ALTER TABLE users "
        "ADD COLUMN IF NOT EXISTS legal_name_public_consent_at TIMESTAMP"
    ))

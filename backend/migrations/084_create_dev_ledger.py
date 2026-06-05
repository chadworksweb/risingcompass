"""Create dev_ledger_items + dev_ledger_votes -- the public "dev side, exposed".

One pipeline, one data model: feature requests + bug reports (community IN),
roadmap items + changelog entries (admin OUT), all stamped with a CalVer version
when shipped. Walled from Motion Desk / Misread Reports / Deliberation Chamber:
those govern the tenet/framework layer; this is the product/engineering layer.

item_type: 'feature' (feature request) | 'bug' (bug report) | 'change' (admin-
authored roadmap/changelog work item).

status:
  feature/bug: submitted -> triaging -> accepted -> in_progress -> shipped
                                      | -> declined | duplicate
  change:      planned -> in_progress -> shipped

stage (roadmap bucket, pre-ship): now | next | later
version: CalVer release id (YYYY.MM.DD[suffix]), set when shipped.
is_public: nothing a stranger submits is served publicly until admin flips this.

PG-compatible (063+). Base.metadata.create_all() picks the tables up on fresh
installs from the models; this migration is the idempotent path for prod.

See RISING-COMPASS-DEV-LEDGER-SCOPE.md.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS dev_ledger_items (
            id SERIAL PRIMARY KEY,
            item_type VARCHAR(20) NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'submitted',
            stage VARCHAR(20),
            version VARCHAR(20),
            severity VARCHAR(10),
            area VARCHAR(40),
            submitted_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            vote_count INTEGER NOT NULL DEFAULT 0,
            is_public BOOLEAN NOT NULL DEFAULT FALSE,
            admin_note TEXT,
            resolution TEXT,
            duplicate_of_id INTEGER REFERENCES dev_ledger_items(id) ON DELETE SET NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            published_at TIMESTAMP,
            shipped_at TIMESTAMP,
            resolved_by_admin_id INTEGER
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_dev_ledger_public_type_created "
        "ON dev_ledger_items(is_public, item_type, created_at DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_dev_ledger_status_stage "
        "ON dev_ledger_items(status, stage)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_dev_ledger_version "
        "ON dev_ledger_items(version)"
    ))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS dev_ledger_votes (
            id SERIAL PRIMARY KEY,
            item_id INTEGER NOT NULL REFERENCES dev_ledger_items(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_dev_ledger_vote_item_user "
        "ON dev_ledger_votes(item_id, user_id)"
    ))

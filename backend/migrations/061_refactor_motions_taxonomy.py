"""Public Participation Phase 3.2 -- taxonomy correction.

Original 060 wired motions to per-song recalibration challenges. That
was wrong: the Motion Desk deliberates the framework (tenets, rules,
modifiers, methodology). Per-song mistakes are Misread Reports.

SQLite can't ALTER away the FK on spawned_proposal_id even after
the column is logically unused, so this migration uses the standard
SQLite table-rebuild pattern: create a new table with the target
schema, copy non-challenge rows over, drop the old table, rename.

Final schema:
  motions
    id, motion_type, target_kind, target_ref, claim, reasoning,
    citations, filed_by_user_id, status, resolution_summary,
    resolved_at, resolved_by_admin_id, filed_at

target_kind/target_ref are polymorphic for tenet/rule/modifier targets;
both NULL for process motions and new_tenet/new_rule.
"""

from sqlalchemy import text


def up(conn):
    # 1. Build the new table.
    conn.execute(text("""
        CREATE TABLE motions_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            motion_type TEXT NOT NULL,
            target_kind TEXT,
            target_ref TEXT,
            claim TEXT NOT NULL,
            reasoning TEXT NOT NULL,
            citations TEXT,
            filed_by_user_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'filed',
            resolution_summary TEXT,
            resolved_at DATETIME,
            resolved_by_admin_id INTEGER,
            filed_at DATETIME NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (filed_by_user_id) REFERENCES users(id)
        )
    """))

    # 2. Copy rows over. Drop synthetic challenge rows (the type no
    #    longer exists) and remap target_tenet_ref into the new
    #    target_kind/target_ref pair.
    conn.execute(text("""
        INSERT INTO motions_new (
            id, motion_type, target_kind, target_ref,
            claim, reasoning, citations,
            filed_by_user_id, status,
            resolution_summary, resolved_at, resolved_by_admin_id,
            filed_at
        )
        SELECT
            id, motion_type,
            CASE WHEN target_tenet_ref IS NOT NULL THEN 'tenet' ELSE NULL END,
            target_tenet_ref,
            claim, reasoning, citations,
            filed_by_user_id, status,
            resolution_summary, resolved_at, resolved_by_admin_id,
            filed_at
        FROM motions
        WHERE motion_type != 'recalibration_challenge'
    """))

    # 3. Drop the old table (cascades indexes) and rename the new one.
    conn.execute(text("DROP TABLE motions"))
    conn.execute(text("ALTER TABLE motions_new RENAME TO motions"))

    # 4. Recreate indexes on the new table.
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_motions_status_filed_at "
        "ON motions(status, filed_at DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_motions_type_status "
        "ON motions(motion_type, status, filed_at DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_motions_target "
        "ON motions(target_kind, target_ref)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_motions_filed_by "
        "ON motions(filed_by_user_id, filed_at DESC)"
    ))

"""Public Participation Phase 3.2 -- motions table.

The Motion Desk is the public entry point for filing formal proposals.
Two motion types share one table:

  recalibration_challenge -- targets a specific song. claim/reasoning argue
    that the agent's calibration is wrong. On admin approval, spawns a
    SongRecalibrationProposal with pipeline='public_motion' and
    trigger_ref_id=motion.id. Proposal accept/reject drives motion status.

  amend_tenet | new_tenet | remove_tenet -- targets the tenets (the
    framework itself). target_tenet_ref is a text reference like
    'violet-01' (matches tenets/core.json ids). NULL for new_tenet.
    Resolved in the Chamber (Phase 4); admin transitions status manually
    for now via ratify/reject/covered actions.

Lifecycle: filed -> in_deliberation -> ratified | rejected | covered.
'covered' means "already covered by an existing tenet" (amendment-only).

Tier 2 (id_verified) is required to file. Tier 1 can read + (Phase 4)
upvote/comment. Anonymous users can read.

Citations is a JSON array of URLs (optional supporting evidence).
spawned_proposal_id links a recalibration_challenge to its downstream
SongRecalibrationProposal once admin spawns one.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS motions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            motion_type TEXT NOT NULL,
            target_song_source TEXT,
            target_song_id INTEGER,
            target_tenet_ref TEXT,
            claim TEXT NOT NULL,
            reasoning TEXT NOT NULL,
            citations TEXT,
            filed_by_user_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'filed',
            resolution_summary TEXT,
            resolved_at DATETIME,
            resolved_by_admin_id INTEGER,
            spawned_proposal_id INTEGER,
            filed_at DATETIME NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (filed_by_user_id) REFERENCES users(id),
            FOREIGN KEY (spawned_proposal_id) REFERENCES song_recalibration_proposals(id) ON DELETE SET NULL
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_motions_status_filed_at "
        "ON motions(status, filed_at DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_motions_type_status "
        "ON motions(motion_type, status, filed_at DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_motions_target_song "
        "ON motions(target_song_source, target_song_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_motions_filed_by "
        "ON motions(filed_by_user_id, filed_at DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_motions_spawned_proposal "
        "ON motions(spawned_proposal_id)"
    ))

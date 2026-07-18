"""Make instrumental a real draft disposition, and stop giving instrumentals a tier.

An instrumental is a PLACEHOLDER: no lyrics to read, so no charge and no color.
It renders grey. Until now `agent_draft_songs` had no `instrumental` column, so
the disposition had nowhere to live on the draft row and a green tier was written
as a stand-in -- the draft gates all key on `rubric_color IS NOT NULL`, and green
was the only way to satisfy them. That stand-in is the liability: a placeholder
sat in the corpus wearing a Decent tier it never earned.

This adds the column (the missing sibling to `preorder` and `lyrics_unavailable`)
so the gates can key on the disposition itself, and clears the stand-in tier from
every instrumental row, draft and Library alike.

- agent_draft_songs.instrumental -- the per-draft mark (gate exemption)
- songs.instrumental             -- already exists (the Library cache-hit state)

Data: nulls rubric_color + charge_value on every instrumental row. Paired with
the calibrator change that stops requiring a tier for an instrumental cache hit;
without that code, a color-less instrumental would re-list daily forever.

Idempotent; PG-compatible (063+). Base.metadata.create_all() builds the column on
fresh installs from the models.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE agent_draft_songs ADD COLUMN IF NOT EXISTS instrumental "
        "BOOLEAN NOT NULL DEFAULT FALSE"
    ))

    # Carry the Library disposition onto any draft row that still points at an
    # instrumental song, so in-flight drafts keep their exemption after the gates
    # switch off rubric_color.
    conn.execute(text(
        "UPDATE agent_draft_songs ds SET instrumental = TRUE "
        "FROM songs s WHERE ds.song_id = s.id AND s.instrumental IS TRUE "
        "AND ds.instrumental IS NOT TRUE"
    ))

    # Drop the stand-in tier. A placeholder carries no color and no charge.
    conn.execute(text(
        "UPDATE songs SET rubric_color = NULL, charge_value = NULL "
        "WHERE instrumental IS TRUE "
        "AND (rubric_color IS NOT NULL OR charge_value IS NOT NULL)"
    ))
    conn.execute(text(
        "UPDATE agent_draft_songs SET rubric_color = NULL, charge_value = NULL "
        "WHERE instrumental IS TRUE "
        "AND (rubric_color IS NOT NULL OR charge_value IS NOT NULL)"
    ))

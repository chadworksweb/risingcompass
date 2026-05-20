"""Split withdraw (user sovereignty) from delete (admin moderation).

Before: comments.deleted_at served double duty -- user "take back" actions
wrote here, and the admin queue had no delete (only hide). The states
weren't actually distinct in the data model.

After:
  withdrawn_at -- user retracted their own comment. Attribution preserved.
                  Frontend renders "[withdrawn by @handle]" with a press-
                  and-hold reveal of the original content.
  deleted_at   -- admin removed the comment. No attribution surfaced. No
                  reveal. The row stays for audit but is functionally gone
                  from public view.
  hidden_at    -- admin suppressed pending review. Reversible. Author
                  sees the reason (no shadow-ban).

Every existing deleted_at row in this schema was a user action (the
admin Delete didn't exist yet), so they all migrate to withdrawn_at.
"""

from sqlalchemy import text


def up(conn):
    # Add the new column first so we can populate it before clearing the
    # old one. SQLite allows ALTER TABLE ADD COLUMN with no constraints.
    conn.execute(text("ALTER TABLE comments ADD COLUMN withdrawn_at DATETIME"))

    # Move every existing soft-delete into the withdraw bucket. These were
    # all user "take back" actions -- admin Delete is brand new.
    conn.execute(text(
        "UPDATE comments SET withdrawn_at = deleted_at WHERE deleted_at IS NOT NULL"
    ))
    conn.execute(text(
        "UPDATE comments SET deleted_at = NULL WHERE deleted_at IS NOT NULL"
    ))

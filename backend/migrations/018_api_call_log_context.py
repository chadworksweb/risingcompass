"""Add context_json to api_call_log — captures which song/artist/dataset each call touched."""

from sqlalchemy import text


def up(conn):
    conn.execute(text("ALTER TABLE api_call_log ADD COLUMN context_json TEXT"))

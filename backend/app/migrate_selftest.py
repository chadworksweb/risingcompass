"""Self-test for the filename-identity migration runner (app/migrate.py).

Runs entirely against in-memory SQLite with throwaway migration files in a temp
dir. No real DB, no network. Run:  python -m app.migrate_selftest
"""

import os
import shutil
import tempfile

from sqlalchemy import create_engine, text

from app import migrate

_FAILS = []
_PASSES = 0


def _check(cond, label):
    global _PASSES
    if cond:
        _PASSES += 1
        print(f"  [PASS] {label}")
    else:
        _FAILS.append(label)
        print(f"  [FAIL] {label}")


def _write_migration(d, number, name, body):
    """Write migrations/NNN_name.py whose up() runs `body` (a SQL string) and
    records a marker row in a `ran` table so we can assert what actually executed."""
    fn = os.path.join(d, f"{number:03d}_{name}.py")
    with open(fn, "w", encoding="ascii") as f:
        f.write(
            "from sqlalchemy import text\n\n\n"
            "def up(conn):\n"
            f"    conn.execute(text(\"INSERT INTO ran (tag) VALUES ('{number:03d}_{name}')\"))\n"
        )
    return fn


def _ran_tags(engine):
    with engine.begin() as conn:
        return [r[0] for r in conn.execute(text("SELECT tag FROM ran ORDER BY tag")).fetchall()]


def _fresh_engine():
    eng = create_engine("sqlite://")  # in-memory, single connection pool
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE ran (tag TEXT)"))
    return eng


def _seed_legacy_versions(engine, versions):
    """Simulate the old runner / PG baseline having recorded these versions."""
    with engine.begin() as conn:
        migrate._ensure_tables(conn)
        for v in versions:
            conn.execute(text("INSERT INTO schema_version (version) VALUES (:v)"), {"v": v})


def test_fresh_install():
    print("test_fresh_install")
    d = tempfile.mkdtemp()
    try:
        eng = _fresh_engine()
        _write_migration(d, 1, "a", "")
        _write_migration(d, 2, "b", "")
        _write_migration(d, 3, "c", "")
        migrate.run_migrations(eng, migrations_dir=d)
        _check(_ran_tags(eng) == ["001_a", "002_b", "003_c"], "all 3 apply on fresh install")
        migrate.run_migrations(eng, migrations_dir=d)
        _check(_ran_tags(eng) == ["001_a", "002_b", "003_c"], "re-run is a no-op (idempotent)")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_legacy_bridge():
    print("test_legacy_bridge")
    d = tempfile.mkdtemp()
    try:
        eng = _fresh_engine()
        _seed_legacy_versions(eng, [1, 2])  # old runner already applied 1,2
        _write_migration(d, 1, "a", "")
        _write_migration(d, 2, "b", "")
        _write_migration(d, 3, "c", "")
        migrate.run_migrations(eng, migrations_dir=d)
        _check(_ran_tags(eng) == ["003_c"], "bridge marks 1,2 applied; only 3 runs")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_cross_track_collision():
    print("test_cross_track_collision (the bug this fixes)")
    d = tempfile.mkdtemp()
    try:
        eng = _fresh_engine()
        # Another track ran its own migrations 4 and 5 against the shared DB; no
        # files for them exist in THIS repo. Legacy max is therefore 5.
        _seed_legacy_versions(eng, [1, 2, 3, 4, 5])
        _write_migration(d, 1, "a", "")
        _write_migration(d, 2, "b", "")
        _write_migration(d, 3, "c", "")
        # Our genuinely-new migration. Old runner: 6 > 5 so this happened to work,
        # but had we (correctly) numbered it 4 or 5 it would have been shadowed.
        _write_migration(d, 6, "ours", "")
        migrate.run_migrations(eng, migrations_dir=d)
        _check(_ran_tags(eng) == ["006_ours"], "new file above baseline runs; 1-3 bridged")

        # Now the real shadow case: a genuinely-unapplied file numbered BELOW the
        # other track's max. Old runner would skip it forever. New runner runs it.
        _write_migration(d, 4, "late", "")
        migrate.run_migrations(eng, migrations_dir=d)
        _check("004_late" in _ran_tags(eng), "late-added lower number still applies (no shadow)")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_same_number_two_files():
    print("test_same_number_two_files")
    d = tempfile.mkdtemp()
    try:
        eng = _fresh_engine()
        _write_migration(d, 6, "alpha", "")
        _write_migration(d, 6, "beta", "")
        migrate.run_migrations(eng, migrations_dir=d)
        tags = _ran_tags(eng)
        _check("006_alpha" in tags and "006_beta" in tags, "two files sharing a number both apply")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_failure_rolls_back():
    print("test_failure_rolls_back")
    d = tempfile.mkdtemp()
    try:
        eng = _fresh_engine()
        _write_migration(d, 1, "ok", "")
        # A migration whose up() raises.
        fn = os.path.join(d, "002_boom.py")
        with open(fn, "w", encoding="ascii") as f:
            f.write("def up(conn):\n    raise RuntimeError('boom')\n")
        _write_migration(d, 3, "after", "")
        raised = False
        try:
            migrate.run_migrations(eng, migrations_dir=d)
        except RuntimeError:
            raised = True
        _check(raised, "a failing migration propagates (does not swallow)")
        with eng.begin() as conn:
            applied = migrate._applied_names(conn)
        _check("001_ok" in applied, "the migration before the failure is recorded")
        _check("002_boom" not in applied, "the failed migration is NOT recorded")
        _check("003_after" not in applied, "migrations after the failure do not run")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    for t in (
        test_fresh_install,
        test_legacy_bridge,
        test_cross_track_collision,
        test_same_number_two_files,
        test_failure_rolls_back,
    ):
        t()
    total = _PASSES + len(_FAILS)
    print(f"\nmigrate runner self-test: {_PASSES}/{total}")
    if _FAILS:
        print("FAILURES:")
        for f in _FAILS:
            print(f"  - {f}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()

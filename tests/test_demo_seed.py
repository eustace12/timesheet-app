"""Demo-account seeding tests."""

import db


def test_empty_database_is_not_seeded_without_explicit_demo_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    db.configure(tmp_path / "production-like.db")
    db.init_db()
    db.seed_if_empty()

    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0

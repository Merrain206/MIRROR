from io import StringIO
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_migrations_build_core_schema_and_society_fields(tmp_path: Path, monkeypatch) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    database_file = tmp_path / "mirror.sqlite3"
    url = f"sqlite+pysqlite:///{database_file.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))

    command.upgrade(config, "head")
    command.check(config)

    engine = create_engine(url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {
        "games",
        "actors",
        "claims",
        "knowledge",
        "knowledge_sources",
        "events",
        "event_causes",
        "privacy_grants",
        "action_proposals",
        "intentions",
        "score_snapshots",
        "assertions",
            "llm_calls",
    } <= tables
    character_columns = {row["name"]: row for row in inspector.get_columns("characters")}
    relationship_columns = {row["name"]: row for row in inspector.get_columns("relationships")}
    score_columns = {row["name"]: row for row in inspector.get_columns("score_snapshots")}
    assert character_columns["decision_profile"]["nullable"] is False
    assert relationship_columns["basis"]["nullable"] is False
    assert score_columns["details"]["nullable"] is False
    assert any(row["constrained_columns"] == ["game_id", "actor_id"]
               for row in inspector.get_foreign_keys("characters"))
    assert any(row["constrained_columns"] == ["game_id", "from_actor_id"]
               for row in inspector.get_foreign_keys("relationships"))
    assert any(row["name"] == "ck_relationship_trust"
               for row in inspector.get_check_constraints("relationships"))
    assert any(row["constrained_columns"] == ["game_id", "actor_id"]
               for row in inspector.get_foreign_keys("intentions"))
    assert any(row["name"] == "ck_intentions_status"
               for row in inspector.get_check_constraints("intentions"))
    assert any(row["constrained_columns"] == ["game_id", "claim_id"]
               for row in inspector.get_foreign_keys("assertions"))
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "b94f7a61d25c"
        )
    engine.dispose()


def test_society_migration_preserves_existing_phase2_rows(tmp_path: Path, monkeypatch) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    url = f"sqlite+pysqlite:///{(tmp_path / 'existing.sqlite3').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(config, "a601cf30cdbd")
    game_id, alice_id, bob_id, relationship_id = (uuid4().hex for _ in range(4))
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO games (id, day, phase, state_version) "
            "VALUES (:id, 1, 'active', 0)"
        ), {"id": game_id})
        connection.execute(text(
            "INSERT INTO actors (id, game_id, kind, display_name) "
            "VALUES (:id, :game_id, 'character', :name)"
        ), [
            {"id": alice_id, "game_id": game_id, "name": "Alice"},
            {"id": bob_id, "game_id": game_id, "name": "Bob"},
        ])
        connection.execute(text(
            "INSERT INTO characters (actor_id, game_id, age, occupation, background, "
            "traits, \"values\", fears, desires, communication_style, goals, emotional_state) "
            "VALUES (:actor_id, :game_id, 24, 'test', 'test', '[]', '[]', '[]', '[]', "
            "'short', '{}', '{}')"
        ), {"actor_id": alice_id, "game_id": game_id})
        connection.execute(text(
            "INSERT INTO relationships (id, game_id, from_actor_id, to_actor_id, "
            "trust, affection, intimacy, conflict) "
            "VALUES (:id, :game_id, :from_id, :to_id, 50, 20, 30, 0)"
        ), {"id": relationship_id, "game_id": game_id,
            "from_id": alice_id, "to_id": bob_id})
    engine.dispose()
    command.upgrade(config, "head")
    command.check(config)
    engine = create_engine(url)
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT decision_profile FROM characters WHERE actor_id = :id"
        ), {"id": alice_id}) == "{}"
        assert connection.scalar(text(
            "SELECT basis FROM relationships WHERE id = :id"
        ), {"id": relationship_id}) == ""
        assert connection.scalar(text(
            "SELECT scenario FROM games WHERE id = :id"
        ), {"id": game_id}) == "society"
    engine.dispose()


def test_mysql_offline_sql_contains_assertions_and_existing_guards(monkeypatch) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    monkeypatch.setenv(
        "DATABASE_URL", "mysql+pymysql://root@127.0.0.1:3306/mirror_dev?charset=utf8mb4"
    )
    output = StringIO()
    config = Config(str(backend_dir / "alembic.ini"), output_buffer=output)
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(config, "head", sql=True)
    statements = output.getvalue()
    assert "ALTER TABLE characters ADD COLUMN decision_profile JSON" in statements
    assert "ALTER TABLE relationships ADD COLUMN basis TEXT" in statements
    assert "ALTER TABLE games ADD COLUMN scenario VARCHAR(16)" in statements
    assert "CREATE TABLE intentions" in statements
    assert "CREATE TABLE assertions" in statements
    assert "fk_assertions_game_claim FOREIGN KEY(game_id, claim_id)" in statements
    assert "ck_assertions_confidence CHECK" in statements
    assert "ALTER TABLE score_snapshots ADD COLUMN details JSON" in statements
    assert "fk_intentions_game_claim FOREIGN KEY(game_id, claim_id)" in statements
    assert "ck_intentions_status CHECK" in statements
    assert "fk_knowledge_game_holder FOREIGN KEY(game_id, holder_actor_id)" in statements
    assert "ck_knowledge_confidence CHECK (confidence BETWEEN 0 AND 1)" in statements


def test_score_migration_preserves_existing_snapshot(tmp_path: Path, monkeypatch) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    url = f"sqlite+pysqlite:///{(tmp_path / 'score_existing.sqlite3').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(config, "c824e15a770d")
    game_id, event_id, score_id = (uuid4().hex for _ in range(3))
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO games (id, day, scenario, phase, state_version) "
            "VALUES (:id, 1, 'temporal', 'active', 1)"
        ), {"id": game_id})
        connection.execute(text(
            "INSERT INTO events (id, game_id, sequence, day, type, data, visibility) "
            "VALUES (:id, :game_id, 1, 1, 'DAY_BEGAN', '{}', 'private')"
        ), {"id": event_id, "game_id": game_id})
        connection.execute(text(
            "INSERT INTO score_snapshots (id, game_id, day, reason_event_id, "
            "formula_version, happiness, trust, connection, agency, stability, "
            "truth, dependence) VALUES (:id, :game_id, 1, :event_id, 'legacy', "
            "40, 50, 60, 70, 80, 90, 10)"
        ), {"id": score_id, "game_id": game_id, "event_id": event_id})
    engine.dispose()
    command.upgrade(config, "head")
    command.check(config)
    engine = create_engine(url)
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT formula_version, agency, details FROM score_snapshots WHERE id=:id"
        ), {"id": score_id}).one() == ("legacy", 70, "{}")
    engine.dispose()

"""SQLite connection factory + versioned migration runner."""
import pathlib
import sqlite3

from .config import settings

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"


def connect() -> sqlite3.Connection:
    path = settings.database_path
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def migrate(conn: sqlite3.Connection | None = None) -> None:
    """Apply pending migrations in filename order, tracking schema_migrations."""
    own = conn is None
    conn = conn or connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.stem not in applied:
                conn.executescript(path.read_text())
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) "
                    "VALUES (?, datetime('now'))",
                    (path.stem,),
                )
                conn.commit()
    finally:
        if own:
            conn.close()

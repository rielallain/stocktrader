"""
Database module for StockTracker Web.

Preserves the exact schema from the original PySide6 desktop app, with
two new tables added for alerting:
  - alert_rules: user-defined price/percent/RSI triggers
  - alert_log:   audit trail of fired alerts

On first run, if DATABASE_PATH doesn't exist but an initial seed file
(stocks_seed.db) is bundled with the app, the seed is copied into place.
This lets all 61 existing tickers migrate over with zero re-entry.
"""
import os
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path

# On Render, persistent disk is mounted at /var/data
# Locally, we use ./data/stocks.db
DATABASE_PATH = os.environ.get(
    "DATABASE_PATH",
    str(Path(__file__).parent.parent / "data" / "stocks.db")
)

SEED_PATH = str(Path(__file__).parent.parent / "data" / "stocks_seed.db")


def _ensure_parent_dir(path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def _seed_if_missing():
    """Copy the bundled seed db into place on first boot."""
    if os.path.exists(DATABASE_PATH):
        return
    _ensure_parent_dir(DATABASE_PATH)
    if os.path.exists(SEED_PATH):
        shutil.copy(SEED_PATH, DATABASE_PATH)
        print(f"[db] Seeded database from {SEED_PATH} -> {DATABASE_PATH}")
    else:
        print(f"[db] No seed found; starting with empty database at {DATABASE_PATH}")


@contextmanager
def get_conn():
    """Context-managed SQLite connection with row factory."""
    _seed_if_missing()
    conn = sqlite3.connect(DATABASE_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema():
    """Create all tables if they don't exist. Idempotent."""
    _seed_if_missing()
    with get_conn() as conn:
        cur = conn.cursor()

        # --- Existing tables (preserved from desktop app) ---
        cur.execute("""
            CREATE TABLE IF NOT EXISTS stocks (
                ticker            TEXT PRIMARY KEY,
                endorsement_price REAL NOT NULL DEFAULT 0.0,
                is_portfolio      INTEGER NOT NULL DEFAULT 0,
                is_watchlist      INTEGER NOT NULL DEFAULT 0,
                company_name      TEXT,
                last_price        REAL,
                last_fetched      TEXT,
                previous_close    REAL,
                volume            INTEGER,
                market_cap        REAL,
                high_52w          REAL,
                low_52w           REAL,
                rsi               REAL,
                sma_200_pct       REAL,
                target_price      REAL DEFAULT 0.0,
                endorsement_date  TEXT,
                allocation        REAL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS watchlists (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                name     TEXT NOT NULL UNIQUE,
                position INTEGER NOT NULL DEFAULT 0
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_members (
                watchlist_id      INTEGER NOT NULL,
                ticker            TEXT NOT NULL,
                endorsement_price REAL NOT NULL DEFAULT 0.0,
                target_price      REAL NOT NULL DEFAULT 0.0,
                endorsement_date  TEXT,
                allocation        REAL,
                PRIMARY KEY (watchlist_id, ticker),
                FOREIGN KEY (watchlist_id) REFERENCES watchlists(id) ON DELETE CASCADE,
                FOREIGN KEY (ticker) REFERENCES stocks(ticker) ON DELETE CASCADE
            )
        """)

        # Ensure at least one default watchlist exists
        cur.execute("SELECT COUNT(*) FROM watchlists")
        if cur.fetchone()[0] == 0:
            cur.execute(
                "INSERT INTO watchlists (name, position) VALUES (?, ?)",
                ("Watchlist", 0)
            )

        # --- New tables for alerting ---
        cur.execute("""
            CREATE TABLE IF NOT EXISTS alert_rules (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker            TEXT NOT NULL,
                rule_type         TEXT NOT NULL,  -- 'price_above', 'price_below',
                                                  -- 'pct_from_endorsement', 'rsi_above', 'rsi_below'
                threshold         REAL NOT NULL,
                active            INTEGER NOT NULL DEFAULT 1,
                one_shot          INTEGER NOT NULL DEFAULT 1,  -- auto-deactivate after firing
                note              TEXT,
                created_at        TEXT NOT NULL DEFAULT (datetime('now')),
                last_triggered_at TEXT,
                FOREIGN KEY (ticker) REFERENCES stocks(ticker) ON DELETE CASCADE
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS alert_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id     INTEGER,
                ticker      TEXT NOT NULL,
                rule_type   TEXT NOT NULL,
                threshold   REAL NOT NULL,
                actual      REAL NOT NULL,
                message     TEXT NOT NULL,
                sent_ok     INTEGER NOT NULL DEFAULT 0,
                error       TEXT,
                fired_at    TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_alert_rules_active
            ON alert_rules(active, ticker)
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_alert_log_fired
            ON alert_log(fired_at DESC)
        """)

        # --- Migrations: additive columns on existing `stocks` table ---
        # Each wrapped in try/except because SQLite lacks "ADD COLUMN IF NOT EXISTS".
        for col_sql in (
            "ALTER TABLE stocks ADD COLUMN extended_price REAL",
            "ALTER TABLE stocks ADD COLUMN extended_session TEXT",  # 'pre' | 'post' | NULL
        ):
            try:
                cur.execute(col_sql)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise

        print("[db] Schema initialized")


if __name__ == "__main__":
    init_schema()
    print(f"[db] Ready at {DATABASE_PATH}")

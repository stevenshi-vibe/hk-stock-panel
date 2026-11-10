"""
Database layer for HK Stock Panel — multi-stock support.
Each stock gets its own SQLite file under data/{stock_code}.db for clean isolation.
No data ships with the project; databases are created on-demand when a stock is first accessed.
"""
import sqlite3
import os
import json
from contextlib import contextmanager
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), '..', 'uploads')

# Global meta DB (tracks which stocks are being monitored, user-uploaded files, etc.)
META_DB_PATH = os.path.join(DATA_DIR, '_meta.db')

SCHEMA_VERSION = 3


def _migrate_stock_db(conn):
    """Auto-migrate existing stock DBs to add new tables/columns."""
    try:
        # Get existing tables
        existing_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        needed_tables = {
            'announcements': """
                CREATE TABLE IF NOT EXISTS announcements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    announce_date TEXT NOT NULL,
                    title TEXT NOT NULL,
                    category TEXT,
                    pdf_url TEXT,
                    summary TEXT,
                    is_material INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(announce_date, title)
                )""",
            'stock_info': """
                CREATE TABLE IF NOT EXISTS stock_info (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT DEFAULT (datetime('now'))
                )""",
            'schema_version': """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT DEFAULT (datetime('now'))
                )""",
        }
        for table, ddl in needed_tables.items():
            if table not in existing_tables:
                conn.execute(ddl)
                if table == 'announcements':
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_ann_date ON announcements(announce_date)")

        # Add newer attribution-rule columns for mixed-seat shareholder selloff tracking.
        if 'shareholder_attribution' in existing_tables:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(shareholder_attribution)").fetchall()}
            migrations = [
                ('sell_start_date', 'TEXT'),
                ('sell_end_date', 'TEXT'),
                ('rule', "TEXT DEFAULT 'all_flow'"),
                ('static_shares', 'INTEGER DEFAULT 0'),
                ('peer_shareholders', 'TEXT'),
                ('holding_pct', 'REAL'),
                ('manually_edited', 'INTEGER DEFAULT 0'),
                ('updated_at', 'TEXT'),
            ]
            for col, ddl in migrations:
                if col not in cols:
                    conn.execute(f"ALTER TABLE shareholder_attribution ADD COLUMN {col} {ddl}")

        if 'shareholder_selloff' in existing_tables:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(shareholder_selloff)").fetchall()}
            if 'notes' not in cols:
                conn.execute("ALTER TABLE shareholder_selloff ADD COLUMN notes TEXT")

        if 'schema_version' in existing_tables or 'schema_version' in needed_tables:
            conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    except sqlite3.OperationalError:
        pass


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)


def get_stock_db_path(stock_code: str) -> str:
    ensure_dirs()
    code = stock_code.zfill(5)
    return os.path.join(DATA_DIR, f'{code}.db')


@contextmanager
def get_meta_conn():
    ensure_dirs()
    conn = sqlite3.connect(META_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_stock_conn(stock_code: str):
    ensure_dirs()
    db_path = get_stock_db_path(stock_code)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        # Auto-migrate
        _migrate_stock_db(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_meta_db():
    """Initialize the meta database (tracks monitored stocks, uploads, settings)."""
    with get_meta_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS stocks (
                stock_code TEXT PRIMARY KEY,
                stock_name TEXT,
                first_added TEXT DEFAULT (datetime('now')),
                last_updated TEXT,
                is_ir_configured INTEGER DEFAULT 0,
                issued_shares INTEGER DEFAULT 0,
                meta_json TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS uploads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT NOT NULL,
                filename TEXT NOT NULL,
                file_type TEXT,
                upload_date TEXT DEFAULT (datetime('now')),
                parsed_status TEXT DEFAULT 'pending',
                parsed_data TEXT,
                notes TEXT,
                FOREIGN KEY (stock_code) REFERENCES stocks(stock_code)
            );

            CREATE TABLE IF NOT EXISTS broker_mapping (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                participant_id TEXT,
                participant_name TEXT UNIQUE,
                broker_label TEXT,
                seat_type TEXT,
                is_cross_connect INTEGER DEFAULT 0,
                is_retail INTEGER DEFAULT 0,
                notes TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            );
        """)


def init_stock_db(stock_code: str):
    """Initialize a per-stock database with all required tables."""
    code = stock_code.zfill(5)
    with get_stock_conn(code) as conn:
        conn.executescript(f"""
            -- 每日行情
            CREATE TABLE IF NOT EXISTS daily_trading (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL,
                volume INTEGER, turnover REAL, change_pct REAL,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(trade_date)
            );

            -- CCASS 总持仓
            CREATE TABLE IF NOT EXISTS ccass_total (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                total_shares INTEGER,
                pct_of_issued REAL,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(trade_date)
            );

            -- CCASS 参与者（席位）每日持仓
            CREATE TABLE IF NOT EXISTS ccass_participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                participant_name TEXT,
                shares INTEGER,
                pct_of_issued REAL,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(trade_date, participant_id)
            );

            -- CCASS 每日变化（计算好的变动）
            CREATE TABLE IF NOT EXISTS ccass_daily_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                participant_name TEXT,
                shares INTEGER,
                shares_change INTEGER,
                shares_change_3d INTEGER DEFAULT 0,
                shares_change_5d INTEGER DEFAULT 0,
                broker_label TEXT,
                seat_type TEXT,
                is_cross_connect INTEGER DEFAULT 0,
                attributed_shareholder TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(trade_date, participant_id)
            );

            -- 新入/退出席位
            CREATE TABLE IF NOT EXISTS ccass_new_participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                participant_name TEXT,
                shares INTEGER,
                pct_of_issued REAL,
                is_new INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(trade_date, participant_id)
            );

            -- Top10 席位快照
            CREATE TABLE IF NOT EXISTS ccass_top10 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                rank INTEGER NOT NULL,
                participant_id TEXT NOT NULL,
                participant_name TEXT,
                shares INTEGER,
                pct_of_issued REAL,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(trade_date, rank)
            );

            -- 港股通持股
            CREATE TABLE IF NOT EXISTS hk_connect (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                shares INTEGER,
                pct_of_issued REAL,
                shares_change INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(trade_date)
            );

            -- 股东席位归因（IR用户上传后填写）
            CREATE TABLE IF NOT EXISTS shareholder_attribution (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shareholder_name TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                participant_name TEXT,
                shares_baseline INTEGER DEFAULT 0,
                investor_type TEXT,
                cost_hkd REAL,
                unlock_date TEXT,
                attribution_type TEXT DEFAULT 'manual',
                confidence TEXT DEFAULT 'high',
                notes TEXT,
                sell_start_date TEXT,
                sell_end_date TEXT,
                rule TEXT DEFAULT 'all_flow',
                static_shares INTEGER DEFAULT 0,
                peer_shareholders TEXT,
                holding_pct REAL,
                manually_edited INTEGER DEFAULT 0,
                updated_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(shareholder_name, participant_id)
            );

            -- 减持追踪（按股东归因计算的每日净卖出）
            CREATE TABLE IF NOT EXISTS shareholder_selloff (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT NOT NULL,
                shareholder_name TEXT NOT NULL,
                shares_change INTEGER,
                shares_remaining INTEGER,
                estimated_proceeds REAL,
                notes TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(trade_date, shareholder_name)
            );

            -- 解禁日历
            CREATE TABLE IF NOT EXISTS unlock_calendar (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                unlock_date TEXT NOT NULL,
                shareholder_name TEXT NOT NULL,
                shares INTEGER,
                cost_hkd REAL,
                broker TEXT,
                investor_type TEXT,
                unlock_status TEXT DEFAULT 'upcoming',
                notes TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(unlock_date, shareholder_name)
            );

            -- 公司公告
            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                announce_date TEXT NOT NULL,
                title TEXT NOT NULL,
                category TEXT,
                pdf_url TEXT,
                summary TEXT,
                is_material INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(announce_date, title)
            );

            -- 基本面信息
            CREATE TABLE IF NOT EXISTS stock_info (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT (datetime('now'))
            );

            -- 索引
            CREATE INDEX IF NOT EXISTS idx_dt_date ON daily_trading(trade_date);
            CREATE INDEX IF NOT EXISTS idx_ct_date ON ccass_total(trade_date);
            CREATE INDEX IF NOT EXISTS idx_cp_date ON ccass_participants(trade_date);
            CREATE INDEX IF NOT EXISTS idx_cp_pid ON ccass_participants(participant_id);
            CREATE INDEX IF NOT EXISTS idx_cdc_date ON ccass_daily_changes(trade_date);
            CREATE INDEX IF NOT EXISTS idx_hk_date ON hk_connect(trade_date);
            CREATE INDEX IF NOT EXISTS idx_sa_name ON shareholder_attribution(shareholder_name);
            CREATE INDEX IF NOT EXISTS idx_uc_date ON unlock_calendar(unlock_date);
            CREATE INDEX IF NOT EXISTS idx_ann_date ON announcements(announce_date);

            -- Schema version for per-stock DB
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            );
        """)

        # Record schema version + run migrations
        _migrate_stock_db(conn)


def ensure_stock(stock_code: str, stock_name: str = None) -> str:
    """Ensure a stock exists in meta DB and its per-stock DB is initialized."""
    code = stock_code.zfill(5)
    init_meta_db()
    init_stock_db(code)
    with get_meta_conn() as conn:
        existing = conn.execute("SELECT stock_code FROM stocks WHERE stock_code = ?", (code,)).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO stocks (stock_code, stock_name) VALUES (?, ?)",
                (code, stock_name or '')
            )
        elif stock_name:
            conn.execute("UPDATE stocks SET stock_name = ? WHERE stock_code = ?", (stock_name, code))
    return code


def list_stocks() -> list[dict]:
    """List all monitored stocks."""
    init_meta_db()
    with get_meta_conn() as conn:
        rows = conn.execute("SELECT * FROM stocks ORDER BY first_added DESC").fetchall()
        stocks = []
        for r in rows:
            s = dict(r)
            db_path = get_stock_db_path(s['stock_code'])
            if os.path.exists(db_path):
                try:
                    with sqlite3.connect(db_path) as sconn:
                        sconn.row_factory = sqlite3.Row
                        lt = sconn.execute("SELECT MAX(trade_date) as d FROM daily_trading").fetchone()
                        pc = sconn.execute("SELECT COUNT(*) as c FROM ccass_participants WHERE trade_date = (SELECT MAX(trade_date) FROM ccass_participants)").fetchone()
                        s['last_trade_date'] = lt['d'] if lt else None
                        s['participant_count'] = pc['c'] if pc else 0
                except Exception:
                    s['last_trade_date'] = None
                    s['participant_count'] = 0
            else:
                s['last_trade_date'] = None
                s['participant_count'] = 0
            stocks.append(s)
        return stocks


def update_stock_last_updated(stock_code: str):
    code = stock_code.zfill(5)
    with get_meta_conn() as conn:
        conn.execute("UPDATE stocks SET last_updated = datetime('now') WHERE stock_code = ?", (code,))


def delete_stock(stock_code: str):
    """Delete all data for a stock (for user-initiated removal)."""
    code = stock_code.zfill(5)
    db_path = get_stock_db_path(code)
    if os.path.exists(db_path):
        os.remove(db_path)
    with get_meta_conn() as conn:
        conn.execute("DELETE FROM stocks WHERE stock_code = ?", (code,))
        conn.execute("DELETE FROM uploads WHERE stock_code = ?", (code,))
    # Delete upload files for this stock
    stock_upload_dir = os.path.join(UPLOAD_DIR, code)
    if os.path.isdir(stock_upload_dir):
        import shutil
        shutil.rmtree(stock_upload_dir, ignore_errors=True)


def get_stock_upload_dir(stock_code: str) -> str:
    code = stock_code.zfill(5)
    d = os.path.join(UPLOAD_DIR, code)
    os.makedirs(d, exist_ok=True)
    return d


if __name__ == '__main__':
    init_meta_db()
    print(f"Meta DB initialized at {META_DB_PATH}")
    print("Run the app to create per-stock databases on demand.")

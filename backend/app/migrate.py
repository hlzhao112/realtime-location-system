"""轻量列迁移：create_all 不会 ALTER 已有 SQLite / PostgreSQL 表。"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

EXTRA = {
    "projects": [
        ("test_on", "BOOLEAN DEFAULT 0"),
        ("test_fast", "BOOLEAN DEFAULT 1"),
        ("test_tick", "INTEGER DEFAULT 0"),
        ("test_made", "INTEGER DEFAULT 0"),
        ("test_next_adv", "TIMESTAMP"),
        ("test_next_keep", "TIMESTAMP"),
        ("keep_next", "TIMESTAMP"),
    ],
    "latest_locations": [
        ("kind", "VARCHAR(32) DEFAULT '实时上报'"),
        ("unassigned", "BOOLEAN DEFAULT 0"),
        ("reissue", "INTEGER DEFAULT 0"),
        ("test", "BOOLEAN DEFAULT 0"),
        ("skip_tick", "INTEGER DEFAULT -1"),
        ("retry_proc", "VARCHAR(64) DEFAULT ''"),
    ],
    "push_records": [
        ("kind", "VARCHAR(32) DEFAULT '实时上报'"),
        ("test", "BOOLEAN DEFAULT 0"),
        ("edited", "BOOLEAN DEFAULT 0"),
        ("area_name", "VARCHAR(128) DEFAULT ''"),
        ("ref_id", "INTEGER"),
    ],
    "raw_reports": [
        ("test", "BOOLEAN DEFAULT 0"),
    ],
}


def migrate(engine: Engine) -> None:
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, cols in EXTRA.items():
            if table not in tables:
                continue
            existing = {c["name"] for c in inspect(engine).get_columns(table)}
            for name, ddl in cols:
                if name in existing:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))

"""轻量列迁移：create_all 不会 ALTER 已有 SQLite / PostgreSQL 表。"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

EXTRA = {
    "projects": [
        ("test_on", "BOOLEAN DEFAULT 0"),
        ("test_fast", "BOOLEAN DEFAULT 1"),
        ("test_adv_mode", "VARCHAR(16) DEFAULT 'fast'"),
        ("test_tick", "INTEGER DEFAULT 0"),
        ("test_made", "INTEGER DEFAULT 0"),
        ("test_next_adv", "TIMESTAMP"),
        ("test_next_keep", "TIMESTAMP"),
        ("keep_next", "TIMESTAMP"),
        ("keep_sec", "INTEGER DEFAULT 600"),
    ],
    "latest_locations": [
        ("kind", "VARCHAR(32) DEFAULT '实时上报'"),
        ("unassigned", "BOOLEAN DEFAULT 0"),
        ("reissue", "INTEGER DEFAULT 0"),
        ("test", "BOOLEAN DEFAULT 0"),
        ("skip_tick", "INTEGER DEFAULT -1"),
        ("retry_proc", "VARCHAR(64) DEFAULT ''"),
        ("seq_guard", "BOOLEAN DEFAULT 0"),
        ("seq_visited", "JSON"),
        ("seq_reissue", "JSON"),
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
    "areas": [
        ("last_identify_time", "TIMESTAMP"),
        ("last_tz", "VARCHAR(64) DEFAULT ''"),
        ("reissue_count", "INTEGER DEFAULT 0"),
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
        _migrate_test_adv_mode(conn, insp)
        _migrate_proc_unique(conn, insp)
        _migrate_default_templates(conn)


def _migrate_test_adv_mode(conn, insp) -> None:
    """从旧布尔 test_fast 回填 test_adv_mode；保留已选的 hour。"""
    if "projects" not in insp.get_table_names():
        return
    try:
        conn.execute(
            text(
                "UPDATE projects SET test_adv_mode = CASE "
                "WHEN test_adv_mode = 'hour' THEN 'hour' "
                "WHEN COALESCE(test_fast, 1) IN (1, '1', 'true', 'True') THEN 'fast' "
                "ELSE 'real' END"
            )
        )
    except Exception:
        pass


def _migrate_proc_unique(conn, insp) -> None:
    """允许同一产线内不同工序使用相同工序编号（按 编码+名称 区分）。"""
    if "procedures" not in insp.get_table_names():
        return
    for uk in insp.get_unique_constraints("procedures"):
        cols = list(uk.get("column_names") or [])
        if cols == ["line_id", "code"]:
            break
    else:
        return
    conn.execute(text("PRAGMA foreign_keys=OFF"))
    conn.execute(
        text(
            """
            CREATE TABLE procedures_new (
                id INTEGER NOT NULL PRIMARY KEY,
                line_id INTEGER NOT NULL,
                code VARCHAR(64) NOT NULL,
                name VARCHAR(128) NOT NULL,
                sort_order INTEGER NOT NULL,
                CONSTRAINT uq_proc_line_code_name UNIQUE (line_id, code, name),
                FOREIGN KEY(line_id) REFERENCES lines (id) ON DELETE CASCADE
            )
            """
        )
    )
    conn.execute(
        text(
            "INSERT INTO procedures_new (id, line_id, code, name, sort_order) "
            "SELECT id, line_id, code, name, sort_order FROM procedures"
        )
    )
    conn.execute(text("DROP TABLE procedures"))
    conn.execute(text("ALTER TABLE procedures_new RENAME TO procedures"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_procedures_line_id ON procedures (line_id)"))
    conn.execute(text("PRAGMA foreign_keys=ON"))


def _migrate_default_templates(conn) -> None:
    """升级默认模板：对齐客户接口字段，去掉 deviceCode / projectCode。"""
    import json

    from .schemas import DEFAULT_FIELDS, DEFAULT_TPL

    drop_keys = ("deviceCode", "projectCode")
    old_six = {
        "areaCode": "${areaCode}",
        "beamAssetsCode": "${beamAssetsCode}",
        "beamLineCode": "${beamLineCode}",
        "identifyTime": "${identifyTime}",
        "procedureCode": "${procedureCode}",
        "sportState": "${sportState}",
    }
    try:
        rows = conn.execute(text("SELECT id, tpl_json, tpl_fields FROM projects")).mappings().all()
    except Exception:
        return
    for row in rows:
        raw = row["tpl_json"] or ""
        try:
            obj = json.loads(raw) if str(raw).strip() else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(obj, dict):
            continue

        changed = False
        if obj == old_six or obj == {}:
            obj = json.loads(DEFAULT_TPL)
            changed = True
        else:
            for key in drop_keys:
                if key in obj:
                    del obj[key]
                    changed = True
            if obj.get("projectId") == "":
                obj["projectId"] = "${projectId}"
                changed = True
            if obj.get("isAutoImported") is False:
                obj["isAutoImported"] = True
                changed = True

        fields_raw = row["tpl_fields"]
        if isinstance(fields_raw, str):
            try:
                fields = json.loads(fields_raw) if fields_raw.strip() else []
            except (TypeError, ValueError, json.JSONDecodeError):
                fields = []
        elif isinstance(fields_raw, list):
            fields = list(fields_raw)
        else:
            fields = []
        new_fields = [f for f in fields if f not in drop_keys]
        if not new_fields:
            new_fields = list(DEFAULT_FIELDS)
        fields_changed = new_fields != fields

        if not changed and not fields_changed:
            continue
        sets = []
        params = {"id": row["id"]}
        if changed:
            sets.append("tpl_json = :tpl")
            params["tpl"] = json.dumps(obj, ensure_ascii=False, indent=2)
        if fields_changed:
            sets.append("tpl_fields = :fields")
            params["fields"] = json.dumps(new_fields, ensure_ascii=False)
        conn.execute(text(f"UPDATE projects SET {', '.join(sets)} WHERE id = :id"), params)

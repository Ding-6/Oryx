"""审计表补列的迁移验证。

为什么要有这条：老库的 `audit` 表**没有 mutated 列**。
新代码的 `finish_audit` 会去写这一列 —— 列不存在时整条 UPDATE 报错，
审计补记就静默失败（表现为「界面有徽章但账本里没有」）。

判据要两向：
  1. 老库能被补上列（迁移生效）；
  2. 补完列之后 `finish_audit` 真能写进去（不报错、值对）。
只验第 1 条不够 —— 列补上了不代表 UPDATE 语法对。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from oryx.store import Store


def _make_legacy_db(path: Path) -> None:
    """造一个「没有 mutated 列」的老库。"""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS audit (
            id          TEXT PRIMARY KEY,
            ts          REAL NOT NULL,
            task_id     TEXT,
            step_id     TEXT,
            tool        TEXT,
            args        TEXT,
            risk        INTEGER,
            decision    TEXT,
            outcome     TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def test_legacy_audit_table_gets_the_mutated_column(tmp_path):
    """老库启动后要补上 mutated 列。"""
    db = tmp_path / "oryx.db"
    _make_legacy_db(db)

    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(audit)")}
    conn.close()
    assert "mutated" not in cols, "前置条件不成立：造出来的库不该已经有这列"

    Store(db)  # 启动即迁移

    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(audit)")}
    conn.close()
    assert "mutated" in cols, "老库的 audit 表没补上 mutated 列"


def test_finish_audit_writes_into_a_migrated_table(tmp_path):
    """补完列之后 finish_audit 要真能写进去。

    这一条是上一条的下游：列补上了但 UPDATE 写不进去，
    表现是「审计行还是 pending」—— 与没迁移一样。
    """
    db = tmp_path / "oryx.db"
    _make_legacy_db(db)
    store = Store(db)

    audit_id = store.audit("t1", "s1", "fs_read", {}, 0, "auto", "pending")
    store.finish_audit(audit_id, "done", False)

    rows = store.audit_for_task("t1")
    assert rows, "审计行不见了"
    assert rows[-1]["outcome"] == "done", f"outcome 还是 {rows[-1]['outcome']!r}"
    assert rows[-1]["mutated"] == 0, f"mutated 是 {rows[-1]['mutated']!r}"


def test_migration_is_idempotent(tmp_path):
    """补列要幂等 —— 第二次启动不能报错，也不能重复加。"""
    db = tmp_path / "oryx.db"
    _make_legacy_db(db)
    Store(db)
    Store(db)  # 再来一次
    Store(db)

    conn = sqlite3.connect(db)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(audit)")]
    conn.close()
    assert cols.count("mutated") == 1, f"mutated 列出现了 {cols.count('mutated')} 次"

"""三库分离：会话域 / 统计域 / 记忆域的物理隔离与向后兼容。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from oryx.store import Store


def tables_in(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}


def test_three_databases_are_created(tmp_path):
    store = Store(tmp_path / "oryx.db")
    assert store.conv_path.exists()
    assert store.metrics_path.exists()
    assert store.memory_path.exists()
    # 三库互不重叠
    assert store.conv_path != store.metrics_path != store.memory_path


def test_domains_hold_their_own_tables(tmp_path):
    store = Store(tmp_path / "oryx.db")

    conv = tables_in(store.conv_path)
    metrics = tables_in(store.metrics_path)
    memory = tables_in(store.memory_path)

    assert {"tasks", "steps", "audit", "messages", "todos"} <= conv
    assert "usage" in metrics
    assert "memory" in memory

    # 关键：usage / memory 不再出现在会话库里
    assert "usage" not in conv, "token 计量应独立成统计域，不能留在会话库"
    assert "memory" not in conv, "长期记忆应独立成记忆域"


def test_usage_writes_to_metrics_db(tmp_path):
    store = Store(tmp_path / "oryx.db")
    store.record_usage("t1", "anthropic", "claude", 100, 50)

    assert tables_in(store.conv_path).isdisjoint({"usage"})
    with sqlite3.connect(store.metrics_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens) FROM usage"
        ).fetchone()
    assert row == (1, 100, 50)

    summary = store.usage_summary()
    assert summary["total"]["calls"] == 1
    assert summary["total"]["input_tokens"] == 100


def test_memory_writes_to_memory_db(tmp_path):
    store = Store(tmp_path / "oryx.db")
    saved = store.save_memory("记住这句话", tags="测试")

    assert "memory" not in tables_in(store.conv_path)
    found = store.search_memories("记住")
    assert [m["id"] for m in found] == [saved["id"]]
    assert store.forget_memory(saved["id"]) is True
    assert store.search_memories("记住") == []


def test_conversation_data_stays_in_conversation_db(tmp_path):
    store = Store(tmp_path / "oryx.db")
    task_id = store.create_task("目标", "craft", "p")
    store.add_message(task_id, "user", "你好")
    store.add_todo(task_id, "要做的事")

    conv = tables_in(store.conv_path)
    assert {"tasks", "messages", "todos"} <= conv
    # 会话库不该被统计/记忆写入污染
    assert "usage" not in conv and "memory" not in conv
    assert store.messages_for_task(task_id)[0]["content"] == "你好"


def test_delete_session_clears_usage_across_databases(tmp_path):
    store = Store(tmp_path / "oryx.db")
    task_id = store.create_task("目标", "craft", "p")
    store.add_message(task_id, "user", "你好")
    store.record_usage(task_id, "anthropic", "claude", 10, 5)

    assert store.usage_for_task(task_id)["calls"] == 1
    assert store.delete_session(task_id) is True
    assert store.messages_for_task(task_id) == []
    assert store.usage_for_task(task_id)["calls"] == 0, "删会话要连统计域一起清"


def test_legacy_single_db_is_migrated(tmp_path):
    """旧版把 usage / memory 混在 oryx.db 里，升级后要自动搬走。"""
    legacy = tmp_path / "oryx.db"
    with sqlite3.connect(legacy) as conn:
        conn.executescript(
            """
            CREATE TABLE tasks (id TEXT PRIMARY KEY, goal TEXT, mode TEXT,
                provider TEXT, status TEXT, created_at REAL, updated_at REAL);
            CREATE TABLE usage (id TEXT PRIMARY KEY, ts REAL, task_id TEXT,
                provider TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER);
            CREATE TABLE memory (id TEXT PRIMARY KEY, text TEXT, tags TEXT,
                created_at REAL, updated_at REAL);
            INSERT INTO usage VALUES('u1', 100.0, 't1', 'p', 'm', 7, 3);
            INSERT INTO memory VALUES('m1', '旧记忆', 'tag', 100.0, 100.0);
            """
        )

    store = Store(legacy)

    assert "usage" not in tables_in(store.conv_path), "旧表应从会话库移除"
    assert "memory" not in tables_in(store.conv_path)
    with sqlite3.connect(store.metrics_path) as conn:
        assert conn.execute("SELECT input_tokens FROM usage WHERE id='u1'").fetchone()[0] == 7
    assert [m["text"] for m in store.search_memories("旧记忆")] == ["旧记忆"]


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "oryx.db"
    Store(path)
    Store(path)  # 第二次构造不应重复搬或报错
    store = Store(path)
    assert store.usage_summary()["total"]["calls"] == 0
    assert store.search_memories("") == []


def test_accepts_directory_path(tmp_path):
    store = Store(tmp_path)  # 传目录：三个库都落在里面
    assert store.conv_path.parent == tmp_path
    assert store.conv_path.name == "oryx.db"
    assert store.metrics_path.name == "metrics.db"
    assert store.memory_path.name == "memory.db"
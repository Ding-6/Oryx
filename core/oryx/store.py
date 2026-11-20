"""SQLite 持久化：按 Marvis 的三库分离模式组织。

    会话域 oryx.db      tasks / steps / audit / messages / todos
    统计域 metrics.db   usage（token 计量，高频写、与会话无关）
    记忆域 memory.db    memory（长期记忆）

为什么拆三库（对齐 Marvis 的 data/business/memory 三库设计）：
会话写入与 token 计量都是高频写。混在一个库里，统计聚合的扫描会跟
会话写入抢同一个 WAL 锁；记忆库则是纯文本召回，不需要参与会话事务。
分库后各域独立备份、独立降级 —— 统计库坏了不影响会话历史。

对外仍是 Store 一个门面：调用方（loop / server）不用关心落在哪个文件。
构造函数接受目录时会自动派生三个文件名，保证向后兼容。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .config import app_dir

# ---------- 会话域 ----------
CONVERSATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    goal        TEXT NOT NULL,
    mode        TEXT NOT NULL,
    provider    TEXT,
    status      TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS steps (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    agent       TEXT,
    tool        TEXT,
    args        TEXT,
    state       TEXT,
    risk        INTEGER,
    result      TEXT,
    error       TEXT,
    started_at  REAL,
    ended_at    REAL,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS audit (
    id          TEXT PRIMARY KEY,
    ts          REAL NOT NULL,
    task_id     TEXT,
    step_id     TEXT,
    tool        TEXT,
    args        TEXT,
    risk        INTEGER,
    decision    TEXT,
    outcome     TEXT,
    mutated     INTEGER
);

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT,
    ts          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS todos (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_steps_task ON steps(task_id, seq);
CREATE INDEX IF NOT EXISTS idx_audit_task ON audit(task_id, ts);
CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id, ts);
CREATE INDEX IF NOT EXISTS idx_todos_task ON todos(task_id, seq);

-- 定时任务：重启后仍然存在（对齐 WorkBuddy 的 durable 定时任务）
CREATE TABLE IF NOT EXISTS automations (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    cron          TEXT NOT NULL,
    goal          TEXT NOT NULL,
    enabled       INTEGER DEFAULT 1,
    recurring     INTEGER DEFAULT 1,
    jitter_ratio  REAL DEFAULT 0.1,
    next_run_at   REAL,
    last_run_at   REAL,
    created_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_automations_next ON automations(next_run_at);

-- 定时任务的执行记录：每次跑都留一条。
--
-- 以前 automations 表只记 last_run_at（最后一次什么时候跑的），
-- 「那次到底生成了什么、成没成」完全查不到 ——
-- 用户想知道「上周三那次定时任务产出了什么」只能靠猜。
-- 对齐 WorkBuddy 的 scheduled-tasks：独立的执行记录 + 失败可追溯。
CREATE TABLE IF NOT EXISTS automation_runs (
    id            TEXT PRIMARY KEY,
    automation_id TEXT NOT NULL,
    name          TEXT NOT NULL,
    goal          TEXT NOT NULL,
    status        TEXT NOT NULL,      -- done / failed
    output        TEXT,
    error         TEXT,
    task_id       TEXT,               -- 对应的会话 id（有的话）
    started_at    REAL NOT NULL,
    ended_at      REAL NOT NULL,
    duration_ms   INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_automation_runs ON automation_runs(automation_id, started_at);
CREATE INDEX IF NOT EXISTS idx_automation_runs_time ON automation_runs(started_at);

-- 团队：跨会话保留（成员、信箱、共享任务）
CREATE TABLE IF NOT EXISTS teams (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    goal        TEXT,
    members     TEXT,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS team_messages (
    id          TEXT PRIMARY KEY,
    team_id     TEXT NOT NULL,
    sender      TEXT NOT NULL,
    recipient   TEXT NOT NULL,
    content     TEXT NOT NULL,
    read        INTEGER DEFAULT 0,
    ts          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_messages ON team_messages(team_id, recipient, ts);

-- 检查点：每次用户提示打一个快照，用于回退。
-- 只记"这一轮改了什么"（文件前后内容 + 消息位置），不复制整个工作区。
CREATE TABLE IF NOT EXISTS checkpoints (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    label       TEXT,
    msg_count   INTEGER DEFAULT 0,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_task ON checkpoints(task_id, seq);

-- 检查点里的文件快照。before 为 NULL 表示"原本不存在这个文件"。
CREATE TABLE IF NOT EXISTS checkpoint_files (
    id           TEXT PRIMARY KEY,
    checkpoint_id TEXT NOT NULL,
    path         TEXT NOT NULL,
    before       TEXT,
    after        TEXT,
    existed      INTEGER DEFAULT 0,
    FOREIGN KEY (checkpoint_id) REFERENCES checkpoints(id)
);
CREATE INDEX IF NOT EXISTS idx_ckpt_files ON checkpoint_files(checkpoint_id);

-- 工作流 run：编排脚本的一次执行。中间结果不进对话上下文，落这里。
CREATE TABLE IF NOT EXISTS workflow_runs (
    id          TEXT PRIMARY KEY,
    name        TEXT,
    goal        TEXT,
    status      TEXT NOT NULL,
    args        TEXT,
    error       TEXT,
    created_at  REAL NOT NULL,
    ended_at    REAL
);

-- 工作流里的每一个 step（= 一次子 Agent 调用）。
-- output 存最终结论的完整文本；给模型看的摘要走 truncation，避免撑爆上下文。
CREATE TABLE IF NOT EXISTS workflow_steps (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL,
    stage       TEXT,
    name        TEXT,
    agent       TEXT,
    goal        TEXT,
    status      TEXT NOT NULL,
    output      TEXT,
    error       TEXT,
    duration_ms INTEGER DEFAULT 0,
    created_at  REAL NOT NULL,
    ended_at    REAL,
    FOREIGN KEY (run_id) REFERENCES workflow_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_wf_steps ON workflow_steps(run_id, created_at);

-- 工作流模板：用户手写的编排存档。
-- 与 workflow_runs 是两回事 —— 模板是「可复用的编排定义」，run 是「一次执行痕迹」。
-- 两张表天然隔离，前端也分两段渲染，用户不会把两者搞混。
--
-- aliases / read_only 两列是后补的（旧库没有）——
-- 这个仓没有 schema 版本号，所以用 _ensure_columns 幂等补列，
-- 不走 ALTER 迁移流程。见 Store._ensure_columns。
CREATE TABLE IF NOT EXISTS workflow_templates (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    plan        TEXT NOT NULL,
    args        TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    run_count   INTEGER DEFAULT 0,
    last_run_at REAL,
    aliases     TEXT,
    read_only   INTEGER DEFAULT 0
);

-- run 的 plan 本体（供"把这次运行存成模板"）。
-- 单独建表而不是给 workflow_runs 加列：加列要走 ALTER 迁移，
-- 而这个仓没有 schema 版本号 —— 新表只要 CREATE TABLE IF NOT EXISTS，
-- 旧库零迁移成本。
CREATE TABLE IF NOT EXISTS workflow_run_plans (
    run_id     TEXT PRIMARY KEY,
    plan       TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""

# ---------- 统计域 ----------
METRICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    id            TEXT PRIMARY KEY,
    ts            REAL NOT NULL,
    task_id       TEXT,
    provider      TEXT,
    model         TEXT,
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage(ts);
"""

# ---------- 记忆域 ----------
MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
    id          TEXT PRIMARY KEY,
    text        TEXT NOT NULL,
    tags        TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_ts ON memory(updated_at);
"""

SCHEMA = CONVERSATION_SCHEMA  # 向后兼容：外部只认这个常量

# Store() 不传路径时用哪个库。
#
# **测试环境下绝不用真实数据库。**
#
# 这不是洁癖 —— 是出过事的：`Store()` 不带参数 → 落到 app_dir()/oryx.db
# → 就是用户自己的库。测试里一句 `store.delete_session(...)`
# 把用户的历史会话全删了，而且 pytest 全绿、没有任何报错。
# （症状：tasks 表被清空、messages 表被清空，steps/audit 还留着 ——
#  正是「删会话」这个动作的痕迹。）
#
# 判据用「pytest 模块在 sys.modules 里」而不是 PYTEST_CURRENT_TEST 环境变量：
# 后者只在**单个测试执行期间**有值，而模块导入发生在**收集阶段** ——
# 那时环境变量还没设，判断会落空。
#
# 真正的隔离在 tests/conftest.py（那里把 ORYX_HOME 指到临时目录，
# 并在模块导入前重定向这些常量）。这里的判断是第二道防线。
if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
    import tempfile as _tempfile

    _TEST_HOME = Path(_tempfile.gettempdir()) / "oryx-test-store"
    _TEST_HOME.mkdir(parents=True, exist_ok=True)
    DEFAULT_STORE_PATH = _TEST_HOME / "oryx.db"
    DEFAULT_INDEX_PATH = _TEST_HOME / "index.db"
else:
    DEFAULT_STORE_PATH = app_dir() / "oryx.db"
    DEFAULT_INDEX_PATH = app_dir() / "index.db"


class Store:
    """三库门面。方法名与旧的单库 Store 完全一致，调用方无感。"""

    def __init__(self, path: Path | None = None):
        base = path or DEFAULT_STORE_PATH
        base.parent.mkdir(parents=True, exist_ok=True)
        # 传 .db 就按它派生同目录的兄弟库；传目录就三个都放进去
        if base.suffix:
            self.conv_path = base
            self.metrics_path = base.with_name(base.stem + "-metrics.db")
            self.memory_path = base.with_name(base.stem + "-memory.db")
        else:
            self.conv_path = base / "oryx.db"
            self.metrics_path = base / "metrics.db"
            self.memory_path = base / "memory.db"

        self._lock = threading.Lock()
        for p, schema in (
            (self.conv_path, CONVERSATION_SCHEMA),
            (self.metrics_path, METRICS_SCHEMA),
            (self.memory_path, MEMORY_SCHEMA),
        ):
            with self._connect(p) as conn:
                conn.executescript(schema)

        # 迁移：旧单库里可能残留 usage / memory 表，搬进对应域后删掉，避免两处都有
        self._migrate_legacy_tables()
        # 后补列：老库的 workflow_templates 没有 aliases / read_only，
        # audit 没有 mutated。同理不写 ALTER 迁移脚本，每次启动幂等补一次。
        self._ensure_columns(
            self.conv_path,
            "workflow_templates",
            {"aliases": "TEXT", "read_only": "INTEGER DEFAULT 0"},
        )
        self._ensure_columns(
            self.conv_path,
            "audit",
            {"mutated": "INTEGER"},
        )
        # 会话标题（用户改名后的名字）。没有它就退回 goal[:40]。
        # team = 这个会话挂着的团队（名字）。
        # 「每个对话可以有一个自己的团队」—— 团队是全局的，会话是私人的，
        # 靠这一列把两者挂起来。
        self._ensure_columns(self.conv_path, "tasks", {"title": "TEXT", "team": "TEXT"})
        # 检查点记录当时的步骤数 —— 回退对话时要一起裁掉那些执行记录。
        self._ensure_columns(self.conv_path, "checkpoints", {"step_count": "INTEGER DEFAULT 0"})

    @staticmethod
    def _ensure_columns(path: Path, table: str, columns: dict[str, str]) -> None:
        """给表补列。已存在就跳过。

        为什么不走 ALTER 迁移脚本：这个仓没有 schema 版本号，
        没法判断「这条 ALTER 跑过没有」。每次启动查一次列名，
        缺哪个补哪个 —— 幂等，且不需要版本号。
        """
        with sqlite3.connect(path, timeout=10) as conn:
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if exists is None:
                return
            present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in columns.items():
                if name in present:
                    continue
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    # ---------- 连接 ----------
    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _conn(self) -> sqlite3.Connection:
        """会话域连接（默认域，保持原有方法体不变）。"""
        return self._connect(self.conv_path)

    def _metrics_conn(self) -> sqlite3.Connection:
        return self._connect(self.metrics_path)

    def _memory_conn(self) -> sqlite3.Connection:
        return self._connect(self.memory_path)

    def _migrate_legacy_tables(self) -> None:
        """把旧单库里 usage / memory 迁到新库，再删旧表。幂等。"""
        legacy = [("usage", self.metrics_path, METRICS_SCHEMA),
                  ("memory", self.memory_path, MEMORY_SCHEMA)]
        with self._lock, self._conn() as conn:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            for table, target, schema in legacy:
                if table not in names:
                    continue
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()
                if rows:
                    dst = self._connect(target)
                    cols = [c[1] for c in dst.execute(f"PRAGMA table_info({table})")]
                    placeholders = ",".join("?" * len(cols))
                    with dst:
                        dst.executemany(
                            f"INSERT OR IGNORE INTO {table}({','.join(cols)}) VALUES({placeholders})",
                            [tuple(r[c] for c in cols) for r in rows],
                        )
                    dst.close()
                conn.execute(f"DROP TABLE {table}")

    # ---------- tasks ----------
    def create_task(self, goal: str, mode: str, provider: str) -> str:
        task_id = uuid.uuid4().hex[:12]
        now = time.time()
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO tasks(id, goal, mode, provider, status, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (task_id, goal, mode, provider, "running", now, now),
            )
        return task_id

    def set_task_status(self, task_id: str, status: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                (status, time.time(), task_id),
            )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def recent_tasks(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- 会话（= task 视角）----------
    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        """会话列表：首条用户消息当标题，附带消息数与步骤数。

        排序按 **updated_at DESC**（最近动过的排最前），不是 created_at。
        续接对话会更新 updated_at —— 用户刚聊完的那条应该浮到最上面，
        按创建时间排的话老会话会一直压着新会话。

        标题优先用用户改过的名字（tasks.title），没有就退回 goal 摘要。
        """
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT t.id, t.goal, t.title, t.status, t.mode, t.provider,
                       t.created_at, t.updated_at,
                       (SELECT COUNT(*) FROM messages m WHERE m.task_id = t.id) AS msg_count,
                       (SELECT COUNT(*) FROM steps s WHERE s.task_id = t.id) AS step_count,
                       (SELECT COUNT(*) FROM todos d WHERE d.task_id = t.id AND d.status != 'done') AS todo_open
                FROM tasks t
                ORDER BY t.updated_at DESC, t.created_at DESC, t.rowid DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            goal = (item.get("goal") or "").strip().replace("\n", " ")
            named = (item.get("title") or "").strip()
            item["title"] = named or (goal[:40] + ("…" if len(goal) > 40 else ""))
            item["renamed"] = bool(named)
            out.append(item)
        return out

    def session_stats(self) -> dict[str, int]:
        """会话计数：总数 / 进行中 / 今日。

        给大屏用 —— 「一共跑过多少轮、现在有几个在跑、今天动了多少」。

        这些数字**不能**让前端从列表里数：
        列表是限量的（limit=20），数出来的是「最近 20 个里有几个在跑」，
        跟「一共跑了多少」完全是两回事 —— 显示错了用户会以为数据丢了。
        """
        # 本地时间的今天零点。
        # tm_isdst=-1 = 让系统自己判断夏令时。
        # 注意 mktime 要的是 **9 元组**（年 月 日 时 分 秒 星期 年内第几天 夏令时），
        # 少给几个会直接抛 TypeError。
        now = time.localtime()
        start_of_day = time.mktime(
            (now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, now.tm_wday, now.tm_yday, -1)
        )
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total,"
                " COALESCE(SUM(CASE WHEN status='running' THEN 1 ELSE 0 END),0) AS active,"
                " COALESCE(SUM(CASE WHEN updated_at >= ? THEN 1 ELSE 0 END),0) AS today"
                " FROM tasks",
                (start_of_day,),
            ).fetchone()
        return {
            "total": int(row["total"]),
            "active": int(row["active"]),
            "today": int(row["today"]),
        }

    def set_session_team(self, task_id: str, team: str) -> bool:
        """给会话挂一个团队（传空串 = 摘掉）。

        团队是全局的（/api/teams），会话是私人的 —— 这一列把两者挂起来：
        打开这个会话就带着这个团队，agent 派活时也知道该派给谁。
        """
        name = (team or "").strip()
        if len(name) > 60:
            name = name[:60]
        with self._lock, self._conn() as conn:
            exists = conn.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not exists:
                return False
            conn.execute(
                "UPDATE tasks SET team=?, updated_at=? WHERE id=?",
                (name, time.time(), task_id),
            )
        return True

    def session_team(self, task_id: str) -> str:
        """这个会话挂着哪个团队（没有就是空串）。"""
        with self._conn() as conn:
            row = conn.execute("SELECT team FROM tasks WHERE id=?", (task_id,)).fetchone()
        return (row["team"] if row else "") or ""

    def rename_session(self, task_id: str, title: str) -> bool:
        """给会话改个名字。

        名字存在 tasks.title 列；清空（传空串）就退回用 goal 摘要当标题。
        会话 id 不变 —— 消息、步骤、检查点都按 id 关联，改名不影响它们。
        """
        name = (title or "").strip()
        if len(name) > 60:
            name = name[:60]
        with self._lock, self._conn() as conn:
            exists = conn.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not exists:
                return False
            conn.execute(
                "UPDATE tasks SET title=?, updated_at=? WHERE id=?",
                (name or None, time.time(), task_id),
            )
        return True

    def session_detail(self, task_id: str) -> dict[str, Any] | None:
        task = self.get_task(task_id)
        if task is None:
            return None
        named = (task.get("title") or "").strip()
        return {
            "task": task,
            "title": named,
            # 这个会话挂着的团队（每个对话可以有自己的团队）
            "team": (task.get("team") or "").strip(),
            "messages": self.messages_for_task(task_id),
            "steps": self.steps_for_task(task_id),
            "todos": self.list_todos(task_id),
            "usage": self.usage_for_task(task_id),
        }

    def delete_session(self, task_id: str) -> bool:
        with self._lock, self._conn() as conn:
            exists = conn.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not exists:
                return False
            # usage 在统计域，跨库删除，所以不跟上面这批表一起清
            for table in ("messages", "steps", "audit", "todos"):
                conn.execute(f"DELETE FROM {table} WHERE task_id=?", (task_id,))
            conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        with self._metrics_conn() as conn:
            conn.execute("DELETE FROM usage WHERE task_id=?", (task_id,))
        return True

    def usage_for_task(self, task_id: str) -> dict[str, int]:
        with self._metrics_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) calls, COALESCE(SUM(input_tokens),0) inp,"
                " COALESCE(SUM(output_tokens),0) outp FROM usage WHERE task_id=?",
                (task_id,),
            ).fetchone()
        return {"calls": row["calls"], "input_tokens": row["inp"], "output_tokens": row["outp"]}

    def chat_image_paths(self, limit: int = 300) -> list[str]:
        """对话里产出或引用过的图片路径。

        两条线索：
          1. 步骤参数里的 path（截图、生成的图表、导出的图片）
          2. 消息正文里出现的图片路径（用户贴的、模型提到的）

        图库的"对话图片"就靠这个 —— 用户关心的是自己聊过的图，
        不是磁盘上所有长着 .png 后缀的文件。
        """
        import re

        out: list[str] = []
        seen: set[str] = set()

        def _add(path: str) -> None:
            text = str(path or "").strip().strip('"')
            if not text:
                return
            key = text.lower()
            if key in seen:
                return
            seen.add(key)
            out.append(text)

        try:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT args FROM steps WHERE args IS NOT NULL"
                    " ORDER BY started_at DESC LIMIT ?",
                    (limit * 3,),
                ).fetchall()
            for row in rows:
                try:
                    args = json.loads(row["args"]) if isinstance(row["args"], str) else row["args"]
                except (TypeError, ValueError):
                    continue
                if not isinstance(args, dict):
                    continue
                for key in ("path", "file", "output", "image", "src"):
                    value = args.get(key)
                    if isinstance(value, str):
                        _add(value)
        except Exception:
            pass

        # 消息正文里的路径（Windows 与 POSIX 两种写法）
        pattern = re.compile(r"[A-Za-z]:[\\/][^\s\"'<>|]{3,240}\.(?:png|jpe?g|gif|webp|bmp|tiff?)", re.I)
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT content FROM messages WHERE content IS NOT NULL"
                    " ORDER BY ts DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            for row in rows:
                for match in pattern.findall(str(row["content"] or "")):
                    _add(match)
        except Exception:
            pass

        return out[:limit]

    # ---------- steps ----------
    def create_step(
        self, task_id: str, seq: int, tool: str, args: dict[str, Any], risk: int
    ) -> str:
        step_id = uuid.uuid4().hex[:12]
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO steps(id, task_id, seq, tool, args, state, risk, started_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (
                    step_id,
                    task_id,
                    seq,
                    tool,
                    json.dumps(args, ensure_ascii=False),
                    "running",
                    risk,
                    time.time(),
                ),
            )
        return step_id

    def finish_step(
        self, step_id: str, state: str, result: Any = None, error: str | None = None
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE steps SET state=?, result=?, error=?, ended_at=? WHERE id=?",
                (
                    state,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error,
                    time.time(),
                    step_id,
                ),
            )

    def steps_for_task(self, task_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM steps WHERE task_id=? ORDER BY seq", (task_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- audit ----------
    def audit(
        self,
        task_id: str,
        step_id: str,
        tool: str,
        args: dict[str, Any],
        risk: int,
        decision: str,
        outcome: str,
    ) -> str:
        """记一条审计。返回这条审计的 id。

        返回 id 是为了**事后补记结果**：审计行在动手前就写好了（记的是
        「打算做什么、谁批准的」），但「实际改了什么」要等工具跑完才知道。
        以前 outcome 永远是 'pending' —— 审计等于只记了半截，
        事后翻账本看不出这条操作到底动没动东西。
        """
        audit_id = uuid.uuid4().hex
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO audit(id, ts, task_id, step_id, tool, args, risk, decision, outcome)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    audit_id,
                    time.time(),
                    task_id,
                    step_id,
                    tool,
                    json.dumps(args, ensure_ascii=False),
                    risk,
                    decision,
                    outcome,
                ),
            )
        return audit_id

    def finish_audit(self, audit_id: str, outcome: str, mutated: bool | None = None) -> None:
        """补记这条审计的结果：跑成了没、有没有改动东西。

        只更新**刚写入的那一条**（按 id 定位），不按 step_id ——
        同一个步骤可能有多条审计（hook 改写过参数时会有两条），
        按 step_id 更新会一次性改掉多行。
        """
        with self._lock, self._conn() as conn:
            if mutated is None:
                conn.execute(
                    "UPDATE audit SET outcome=? WHERE id=?", (outcome, audit_id)
                )
            else:
                conn.execute(
                    "UPDATE audit SET outcome=?, mutated=? WHERE id=?",
                    (outcome, 1 if mutated else 0, audit_id),
                )

    def audit_for_task(self, task_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM audit WHERE task_id=? ORDER BY ts", (task_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- messages ----------
    def add_message(self, task_id: str, role: str, content: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO messages(id, task_id, role, content, ts) VALUES(?,?,?,?,?)",
                (uuid.uuid4().hex, task_id, role, content, time.time()),
            )

    def messages_for_task(self, task_id: str) -> list[dict[str, Any]]:
        """取一个会话的消息，按**写入顺序**排。

        排序用 rowid 不用 ts —— ts 是秒级浮点，同一秒写入的多条消息
        按 ts 排会错位（「最后一条提问」可能挑错）。
        trim_messages / prepare_retry 都是按 rowid 切的，
        这里要是还按 ts 排，界面显示的顺序就跟裁剪的顺序对不上。
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE task_id=? ORDER BY rowid", (task_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def copy_messages(self, from_task: str, to_task: str) -> int:
        """把一个会话的消息复制到另一个会话。返回复制了几条。

        用来做**对话续接**：新建一个 task 承载下一轮，但把上一轮的上下文
        带过去，模型才记得前面聊过什么。
        """
        rows = self.messages_for_task(from_task)
        if not rows:
            return 0
        now = time.time()
        with self._lock, self._conn() as conn:
            for row in rows:
                conn.execute(
                    "INSERT INTO messages(id, task_id, role, content, ts) VALUES(?,?,?,?,?)",
                    (
                        uuid.uuid4().hex,
                        to_task,
                        row.get("role", "user"),
                        row.get("content", ""),
                        float(row.get("ts") or now),
                    ),
                )
        return len(rows)

    # ---------- usage ----------
    def record_usage(
        self,
        task_id: str,
        provider: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        with self._lock, self._metrics_conn() as conn:
            conn.execute(
                "INSERT INTO usage(id, ts, task_id, provider, model, input_tokens, output_tokens)"
                " VALUES(?,?,?,?,?,?,?)",
                (
                    uuid.uuid4().hex,
                    time.time(),
                    task_id,
                    provider,
                    model,
                    int(input_tokens),
                    int(output_tokens),
                ),
            )

    def usage_summary(self, since_hours: int = 24 * 30) -> dict[str, Any]:
        since = time.time() - since_hours * 3600
        with self._metrics_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) calls, COALESCE(SUM(input_tokens),0) inp,"
                " COALESCE(SUM(output_tokens),0) outp FROM usage WHERE ts >= ?",
                (since,),
            ).fetchone()
            today = conn.execute(
                "SELECT COUNT(*) calls, COALESCE(SUM(input_tokens),0) inp,"
                " COALESCE(SUM(output_tokens),0) outp FROM usage WHERE ts >= ?",
                (time.time() - 86400,),
            ).fetchone()
            by_model = conn.execute(
                "SELECT provider, model, COUNT(*) calls,"
                " COALESCE(SUM(input_tokens),0) inp, COALESCE(SUM(output_tokens),0) outp"
                " FROM usage WHERE ts >= ? GROUP BY provider, model ORDER BY calls DESC",
                (since,),
            ).fetchall()
        return {
            "window_hours": since_hours,
            "total": {
                "calls": total["calls"],
                "input_tokens": total["inp"],
                "output_tokens": total["outp"],
            },
            "today": {
                "calls": today["calls"],
                "input_tokens": today["inp"],
                "output_tokens": today["outp"],
            },
            "by_model": [dict(r) for r in by_model],
        }

    # ---------- memory ----------
    def save_memory(self, text: str, tags: str = "") -> dict[str, Any]:
        now = time.time()
        memory_id = uuid.uuid4().hex[:10]
        with self._lock, self._memory_conn() as conn:
            # 完全相同的内容不重复存，更新时间戳即可
            existing = conn.execute(
                "SELECT id FROM memory WHERE text = ?", (text.strip(),)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE memory SET updated_at=?, tags=? WHERE id=?",
                    (now, tags, existing["id"]),
                )
                return {"id": existing["id"], "text": text, "deduped": True}
            conn.execute(
                "INSERT INTO memory(id, text, tags, created_at, updated_at) VALUES(?,?,?,?,?)",
                (memory_id, text.strip(), tags, now, now),
            )
        return {"id": memory_id, "text": text, "deduped": False}

    def search_memories(self, query: str = "", limit: int = 20) -> list[dict[str, Any]]:
        with self._memory_conn() as conn:
            if query:
                rows = conn.execute(
                    "SELECT id, text, tags, updated_at FROM memory"
                    " WHERE text LIKE ? OR tags LIKE ?"
                    " ORDER BY updated_at DESC LIMIT ?",
                    (f"%{query}%", f"%{query}%", limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, text, tags, updated_at FROM memory ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def forget_memory(self, memory_id: str) -> bool:
        with self._lock, self._memory_conn() as conn:
            cur = conn.execute("DELETE FROM memory WHERE id=?", (memory_id,))
        return cur.rowcount > 0

    # ---------- todos ----------
    def add_todo(self, task_id: str, title: str) -> dict[str, Any]:
        now = time.time()
        with self._lock, self._conn() as conn:
            seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM todos WHERE task_id=?", (task_id,)
            ).fetchone()[0]
            todo_id = uuid.uuid4().hex[:8]
            conn.execute(
                "INSERT INTO todos(id, task_id, seq, title, status, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (todo_id, task_id, seq, title, "pending", now, now),
            )
        return {"id": todo_id, "title": title, "status": "pending", "seq": seq}

    def update_todo(self, task_id: str, todo_id: str, status: str) -> dict[str, Any] | None:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM todos WHERE task_id=? AND id=?", (task_id, todo_id)
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE todos SET status=?, updated_at=? WHERE id=?",
                (status, time.time(), todo_id),
            )
        return next((t for t in self.list_todos(task_id) if t["id"] == todo_id), None)

    def list_todos(self, task_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, seq, title, status FROM todos WHERE task_id=? ORDER BY seq",
                (task_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- 定时任务 ----------
    def save_automation(self, item: dict[str, Any]) -> dict[str, Any]:
        """新增或更新一个定时任务。"""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO automations "
                "(id, name, cron, goal, enabled, recurring, jitter_ratio, next_run_at, last_run_at, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name, cron=excluded.cron, goal=excluded.goal, "
                "enabled=excluded.enabled, recurring=excluded.recurring, "
                "jitter_ratio=excluded.jitter_ratio, next_run_at=excluded.next_run_at",
                (
                    item["id"], item.get("name", ""), item["cron"], item.get("goal", ""),
                    1 if item.get("enabled", True) else 0,
                    1 if item.get("recurring", True) else 0,
                    float(item.get("jitter_ratio", 0.1)),
                    item.get("next_run_at"),
                    item.get("last_run_at"),
                    float(item.get("created_at", time.time())),
                ),
            )
        return item

    def list_automations(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM automations ORDER BY created_at"
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["enabled"] = bool(item.get("enabled"))
            item["recurring"] = bool(item.get("recurring"))
            items.append(item)
        return items

    def delete_automation(self, item_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM automations WHERE id=?", (item_id,))
        return cur.rowcount > 0

    def mark_automation_run(self, item_id: str, next_run_at: float | None) -> None:
        """记一次执行，并写上下次触发时间。"""
        with self._conn() as conn:
            conn.execute(
                "UPDATE automations SET last_run_at=?, next_run_at=? WHERE id=?",
                (time.time(), next_run_at, item_id),
            )

    # ---------- 定时任务的执行记录 ----------
    def start_automation_run(
        self, run_id: str, automation_id: str, name: str, goal: str, started_at: float
    ) -> None:
        """记一次定时任务的执行开始。

        先写一条 status='running' 的行 —— 这样**任务卡死或进程被杀**时，
        用户能看到「那次执行没跑完」，而不是它凭空消失。
        """
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO automation_runs"
                "(id, automation_id, name, goal, status, started_at, ended_at) "
                "VALUES(?,?,?,?,'running',?,?)",
                (run_id, automation_id, name, goal, started_at, started_at),
            )

    def finish_automation_run(
        self,
        run_id: str,
        status: str,
        output: str = "",
        error: str = "",
        task_id: str = "",
        ended_at: float | None = None,
    ) -> None:
        """补记一次执行的结局。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT started_at FROM automation_runs WHERE id=?", (run_id,)
            ).fetchone()
            started = float(row["started_at"]) if row else time.time()
            end = ended_at if ended_at is not None else time.time()
            conn.execute(
                "UPDATE automation_runs SET status=?, output=?, error=?, task_id=?, "
                "ended_at=?, duration_ms=? WHERE id=?",
                (
                    status,
                    output[:20000],
                    error[:4000],
                    task_id,
                    end,
                    int((end - started) * 1000),
                    run_id,
                ),
            )

    def automation_runs(self, automation_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        """执行记录列表。没给 automation_id 就按时间倒序给全部。"""
        with self._conn() as conn:
            if automation_id:
                rows = conn.execute(
                    "SELECT * FROM automation_runs WHERE automation_id=? "
                    "ORDER BY started_at DESC LIMIT ?",
                    (automation_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM automation_runs ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def automation_run_detail(self, run_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM automation_runs WHERE id=?", (run_id,)
            ).fetchone()
        return dict(row) if row else None

    # ---------- 团队 ----------
    def save_team(self, team: dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO teams (id, name, goal, members, created_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET goal=excluded.goal, members=excluded.members",
                (
                    team["id"], team["name"], team.get("goal", ""),
                    json.dumps(team.get("members") or {}, ensure_ascii=False),
                    float(team.get("created_at", time.time())),
                ),
            )

    def list_teams(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM teams ORDER BY created_at").fetchall()
        teams = []
        for row in rows:
            item = dict(row)
            try:
                item["members"] = json.loads(item.get("members") or "{}")
            except Exception:
                item["members"] = {}
            teams.append(item)
        return teams

    def get_team(self, name: str) -> dict[str, Any] | None:
        for team in self.list_teams():
            if team["name"] == name:
                return team
        return None

    def delete_team(self, name: str) -> bool:
        with self._conn() as conn:
            # 先把 id 查出来再删 —— 原来先删 teams 行再去子查询找 id，
            # 行已经没了，子查询查不到，信箱消息全成孤儿。
            row = conn.execute("SELECT id FROM teams WHERE name=?", (name,)).fetchone()
            if row is None:
                return False
            conn.execute("DELETE FROM team_messages WHERE team_id=?", (row["id"],))
            cur = conn.execute("DELETE FROM teams WHERE name=?", (name,))
        return cur.rowcount > 0

    def rename_team(self, old_name: str, new_name: str) -> bool:
        """改团队名。name 是唯一键，直接 UPDATE 会撞键 —— 先查重再改。

        信箱消息按 team_id 关联，团队 id 不变，所以改名不用动消息。
        """
        with self._conn() as conn:
            if conn.execute("SELECT 1 FROM teams WHERE name=?", (new_name,)).fetchone():
                return False
            cur = conn.execute("UPDATE teams SET name=? WHERE name=?", (new_name, old_name))
        return cur.rowcount > 0

    def save_team_message(self, message: dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO team_messages (id, team_id, sender, recipient, content, read, ts) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    message["id"], message["team_id"], message["sender"], message["recipient"],
                    message["content"], 1 if message.get("read") else 0, float(message["ts"]),
                ),
            )

    def team_messages(self, team_id: str, recipient: str = "", limit: int = 50) -> list[dict[str, Any]]:
        """取信箱消息，**最新的在前**。

        ts 相同时按 rowid 兜底 —— 同一秒写入的多条消息光靠 ts 排会错位
        （跟对话消息用 rowid 排序是同一个坑）。
        """
        with self._conn() as conn:
            if recipient:
                rows = conn.execute(
                    "SELECT * FROM team_messages WHERE team_id=? AND recipient=? "
                    "ORDER BY ts DESC, rowid DESC LIMIT ?",
                    (team_id, recipient, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM team_messages WHERE team_id=? "
                    "ORDER BY ts DESC, rowid DESC LIMIT ?",
                    (team_id, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def mark_team_messages_read(self, team_id: str, recipient: str) -> int:
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "UPDATE team_messages SET read=1 WHERE team_id=? AND recipient=? AND read=0",
                (team_id, recipient),
            )
        return cur.rowcount

    # ---------- 检查点 ----------
    def create_checkpoint(self, task_id: str, seq: int, label: str = "") -> str:
        """打一个检查点。返回 checkpoint_id。

        ``seq`` 传 0 表示"这个会话的第几个检查点"——由本方法自己数。
        调用方不该自己数：数错了（或者永远传 0）会让回退的范围整个错位。
        """
        cid = uuid.uuid4().hex[:12]
        msg_count = 0
        step_count = 0
        try:
            msg_count = len(self.messages_for_task(task_id))
        except Exception:
            pass
        try:
            step_count = len(self.steps_for_task(task_id))
        except Exception:
            pass
        with self._lock, self._conn() as conn:
            if not seq:
                # 自己数：这个会话已经打了几个点了
                row = conn.execute(
                    "SELECT COALESCE(MAX(seq), -1) AS m FROM checkpoints WHERE task_id=?",
                    (task_id,),
                ).fetchone()
                seq = int(row["m"]) + 1
            conn.execute(
                "INSERT INTO checkpoints(id, task_id, seq, label, msg_count, step_count, created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (cid, task_id, seq, label, msg_count, step_count, time.time()),
            )
        return cid

    def record_checkpoint_file(
        self,
        checkpoint_id: str,
        path: str,
        before: str | None,
        after: str | None,
        existed: bool,
    ) -> None:
        """记下一个文件在某个检查点上的前后内容。

        before=None 且 existed=False 表示"这个文件本来不存在"。
        """
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO checkpoint_files"
                "(id, checkpoint_id, path, before, after, existed) VALUES(?,?,?,?,?,?)",
                (uuid.uuid4().hex, checkpoint_id, path, before, after, 1 if existed else 0),
            )

    def list_checkpoints(self, task_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM checkpoints WHERE task_id=? ORDER BY seq", (task_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM checkpoints WHERE id=?", (checkpoint_id,)
            ).fetchone()
        return dict(row) if row else None

    def checkpoint_files(self, checkpoint_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT path, before, after, existed FROM checkpoint_files WHERE checkpoint_id=?",
                (checkpoint_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def files_after(self, checkpoint_id: str) -> list[dict[str, Any]]:
        """某个检查点**之后**所有改过的文件。

        回退要用：把 [cp, 末尾] 之间动过的所有文件都还原。
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT f.path, f.before, f.after, f.existed, c.seq, c.created_at "
                "FROM checkpoint_files f "
                "JOIN checkpoints c ON f.checkpoint_id = c.id "
                "WHERE c.task_id = (SELECT task_id FROM checkpoints WHERE id=?) "
                "  AND c.seq >= (SELECT seq FROM checkpoints WHERE id=?) "
                "ORDER BY c.seq, f.rowid",
                (checkpoint_id, checkpoint_id),
            ).fetchall()
        # 同一个文件取**最早**的那份 before（还原到检查点当时的状态）。
        # 排序必须按 seq —— 原来按 checkpoint_id 排，那是随机 hex，
        # 「最早」就变成了「任意一份」，回退出来的内容是随机的。
        earliest: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            path = item["path"]
            if path not in earliest:
                earliest[path] = item
        return list(earliest.values())

    def messages_after(self, checkpoint_id: str) -> list[dict[str, Any]]:
        """某个检查点之后追加的消息（回退对话用）。

        检查点创建时记了当时的 msg_count，直接按序号切，不依赖时间戳 ——
        同一秒内的多条消息按 ts 排序会错位。
        """
        cp = self.get_checkpoint(checkpoint_id)
        if not cp:
            return []
        keep = int(cp.get("msg_count") or 0)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE task_id=? ORDER BY rowid", (cp["task_id"],)
            ).fetchall()
        return [dict(r) for r in rows[max(0, keep):]]

    def trim_messages(self, task_id: str, keep: int) -> int:
        """只保留前 keep 条消息，返回删掉了几条。"""
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT rowid FROM messages WHERE task_id=? ORDER BY rowid", (task_id,)
            ).fetchall()
            victims = [r[0] for r in rows[max(0, keep):]]
            for rowid in victims:
                conn.execute("DELETE FROM messages WHERE rowid=?", (rowid,))
        return len(victims)

    def trim_steps(self, task_id: str, keep: int) -> int:
        """只保留前 keep 条执行记录，返回删掉了几条。

        回退对话时要把执行记录一起裁掉 —— 留着那些步骤，
    界面右栏会显示一堆「已经不存在」的工具调用，用户会以为回退没生效。
    """
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT rowid FROM steps WHERE task_id=? ORDER BY seq", (task_id,)
            ).fetchall()
            victims = [r[0] for r in rows[max(0, keep):]]
            for rowid in victims:
                conn.execute("DELETE FROM steps WHERE rowid=?", (rowid,))
        return len(victims)

    def prepare_retry(self, task_id: str) -> dict[str, Any]:
        """准备「重新生成」：删掉最后一轮助手回复，保留用户提问。

        返回 ``{"ok": bool, "goal": str, "removed": int, "reason": str}``。
        ``goal`` 是那条提问的原文，交给内核重跑。

        为什么保留用户提问：Marvis 的「重新生成」只重生成回答 ——
        用户说过的话一个字都不该动。

        排序统一用 rowid 而不是 ts —— 同一秒内写入的多条消息按 ts 排会错位，
        那样"最后一条提问"可能挑错。（messages_for_task 用的是 ts，
        这里刻意用 rowid，保持"写入顺序"这个语义。）

        注意：整个「读 + 删」在同一把锁、同一个事务里做完。
        不能先去读、再调 trim_messages —— 那会二次拿锁（Lock 不可重入，死锁）。
        """
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT rowid, role, content FROM messages WHERE task_id=? ORDER BY rowid",
                (task_id,),
            ).fetchall()
            if not rows:
                return {"ok": False, "goal": "", "removed": 0, "reason": "这个会话还没有消息"}

            # 找最后一条 user 提问
            goal = ""
            goal_idx = -1
            for i, row in enumerate(rows):
                if row["role"] == "user":
                    goal = str(row["content"] or "")
                    goal_idx = i
            if goal_idx < 0:
                return {"ok": False, "goal": "", "removed": 0, "reason": "这个会话没有提问"}
            if not goal.strip():
                return {"ok": False, "goal": "", "removed": 0, "reason": "那条提问是空的"}

            # 删掉这条提问之后的所有消息（助手回复、以及更晚的轮次）
            victims = [r["rowid"] for r in rows[goal_idx + 1:]]
            for rowid in victims:
                conn.execute("DELETE FROM messages WHERE rowid=?", (rowid,))
        return {"ok": True, "goal": goal, "removed": len(victims), "reason": ""}

    def prune_checkpoints(self, days: int = 30) -> int:
        """清掉过期检查点（对齐 WorkBuddy 的 30 天保留）。"""
        cutoff = time.time() - days * 86400
        with self._lock, self._conn() as conn:
            conn.execute(
                "DELETE FROM checkpoint_files WHERE checkpoint_id IN "
                "(SELECT id FROM checkpoints WHERE created_at < ?)",
                (cutoff,),
            )
            cur = conn.execute("DELETE FROM checkpoints WHERE created_at < ?", (cutoff,))
        return cur.rowcount

    # ---------- 工作流 ----------
    def create_workflow_run(self, run: dict[str, Any]) -> str:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO workflow_runs (id, name, goal, status, args, error, created_at, ended_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    run["id"], run.get("name", ""), run.get("goal", ""), run["status"],
                    json.dumps(run.get("args") or {}, ensure_ascii=False),
                    run.get("error", ""),
                    float(run["created_at"]), run.get("ended_at"),
                ),
            )
        return run["id"]

    def finish_workflow_run(self, run_id: str, status: str, error: str = "") -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE workflow_runs SET status=?, error=?, ended_at=? WHERE id=?",
                (status, error, time.time(), run_id),
            )

    def workflow_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """最近的 run 列表，带每个 run 的 step 状态统计。

        统计走一条 GROUP BY 而不是逐个查 —— 列表页要显示"N 个失败"，
        让前端从 run.error 文本里正则猜是脆弱的（error 可能为空，
        也可能写着别的原因）。**不含 output 正文**，避免响应体膨胀。
        """
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            ids = [r["id"] for r in rows]
            stats: dict[str, dict[str, int]] = {}
            with_plan: set[str] = set()
            if ids:
                marks = ",".join("?" * len(ids))
                for r in conn.execute(
                    f"SELECT run_id, status, COUNT(*) AS n FROM workflow_steps "
                    f"WHERE run_id IN ({marks}) GROUP BY run_id, status",
                    ids,
                ).fetchall():
                    stats.setdefault(r["run_id"], {})[r["status"]] = int(r["n"])
                # 有没有存下 plan —— 老记录是补不回来的，前端据此决定
                # 要不要显示「存为模板」按钮（显示了也只能报错）
                with_plan = {
                    r[0] for r in conn.execute(
                        f"SELECT run_id FROM workflow_run_plans WHERE run_id IN ({marks})",
                        ids,
                    ).fetchall()
                }

        out = []
        for row in rows:
            item = dict(row)
            try:
                item["args"] = json.loads(item.get("args") or "{}")
            except Exception:
                item["args"] = {}
            by_status = stats.get(item["id"], {})
            item["step_counts"] = by_status
            item["total_steps"] = sum(by_status.values())
            item["failed_steps"] = by_status.get("failed", 0)
            item["has_plan"] = item["id"] in with_plan
            out.append(item)
        return out

    def get_workflow_run(self, run_id: str) -> dict[str, Any] | None:
        for item in self.workflow_runs(limit=200):
            if item["id"] == run_id:
                return item
        return None

    def add_workflow_step(self, step: dict[str, Any]) -> str:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO workflow_steps "
                "(id, run_id, stage, name, agent, goal, status, output, error, duration_ms, created_at, ended_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    step["id"], step["run_id"], step.get("stage", ""), step.get("name", ""),
                    step.get("agent", ""), step.get("goal", ""), step["status"],
                    step.get("output", ""), step.get("error", ""),
                    int(step.get("duration_ms", 0)),
                    float(step["created_at"]), step.get("ended_at"),
                ),
            )
        return step["id"]

    def finish_workflow_step(
        self,
        step_id: str,
        status: str,
        output: str = "",
        error: str = "",
        duration_ms: int = 0,
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE workflow_steps SET status=?, output=?, error=?, duration_ms=?, ended_at=? WHERE id=?",
                (status, output, error, int(duration_ms), time.time(), step_id),
            )

    def workflow_steps(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_steps WHERE run_id=? ORDER BY created_at, rowid",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def prune_workflow_runs(self, keep: int = 50) -> int:
        """只保留最近 keep 个 run。"""
        with self._lock, self._conn() as conn:
            # DELETE 不支持 ORDER BY，得先用子查询挑出要删的 id
            stale = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM workflow_runs ORDER BY created_at DESC LIMIT -1 OFFSET ?",
                    (keep,),
                ).fetchall()
            ]
            if not stale:
                return 0
            marks = ",".join("?" * len(stale))
            conn.execute(f"DELETE FROM workflow_steps WHERE run_id IN ({marks})", stale)
            cur = conn.execute(f"DELETE FROM workflow_runs WHERE id IN ({marks})", stale)
        return cur.rowcount

    def delete_workflow_run(self, run_id: str) -> bool:
        """删掉一次 run 及其全部 step。

        编排记录会一直攒（一次 run 几十条 step），而 run 本身对用户
        只是一次执行痕迹 —— 没有清理入口的话，跑几十次之后列表就没法看了。
        """
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM workflow_steps WHERE run_id=?", (run_id,))
            cur = conn.execute("DELETE FROM workflow_runs WHERE id=?", (run_id,))
        return cur.rowcount > 0

    # 一条 step 都没有的 run 不算"全失败"（那是刚开始的）
    _ALL_FAILED_RUNS = """
        SELECT id FROM workflow_runs
        WHERE id IN (SELECT run_id FROM workflow_steps)
          AND id NOT IN (
              SELECT run_id FROM workflow_steps WHERE status != 'failed'
          )
    """

    def count_all_failed_workflow_runs(self) -> int:
        """有多少个 run 是"每个 step 都失败"的。

        配置没配好时一次失败就会留下一批这种记录，是列表噪声的主要来源。
        """
        with self._lock, self._conn() as conn:
            return int(conn.execute(
                f"SELECT COUNT(*) FROM ({self._ALL_FAILED_RUNS})").fetchone()[0])

    def prune_all_failed_workflow_runs(self) -> int:
        """删掉所有"每个 step 都失败"的 run，返回删掉的数量。

        为什么不在前端循环删：列表接口是分页的（limit=N），删掉一批之后
        更早的记录补位上来，前端看到的永远是那一页 —— 点了清理也清不干净。
        全失败判定与删除都必须在库这一侧做，与分页无关。
        """
        with self._lock, self._conn() as conn:
            stale = [r[0] for r in conn.execute(self._ALL_FAILED_RUNS).fetchall()]
            if not stale:
                return 0
            marks = ",".join("?" * len(stale))
            conn.execute(f"DELETE FROM workflow_steps WHERE run_id IN ({marks})", stale)
            conn.execute(f"DELETE FROM workflow_runs WHERE id IN ({marks})", stale)
        return len(stale)

    # ---------- 工作流模板 ----------
    def save_workflow_template(self, item: dict[str, Any]) -> dict[str, Any]:
        """新增或更新一个工作流模板。"""
        now = time.time()
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO workflow_templates "
                "(id, name, description, plan, args, created_at, updated_at, run_count, last_run_at,"
                " aliases, read_only) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name, description=excluded.description, plan=excluded.plan, "
                "args=excluded.args, updated_at=excluded.updated_at, "
                "aliases=excluded.aliases, read_only=excluded.read_only",
                (
                    item["id"], item.get("name", ""), item.get("description", ""),
                    json.dumps(item.get("plan") or {}, ensure_ascii=False),
                    json.dumps(item.get("args") or {}, ensure_ascii=False),
                    float(item.get("created_at", now)), now,
                    int(item.get("run_count", 0)), item.get("last_run_at"),
                    json.dumps(list(item.get("aliases") or []), ensure_ascii=False),
                    1 if item.get("read_only") else 0,
                ),
            )
        return item

    def list_workflow_templates(self) -> list[dict[str, Any]]:
        """模板列表。**不含 plan 正文** —— 列表页只要名字与规模。

        阶段数与步数从 plan 里解析出来给前端显示徽标，但正文只留详情接口给，
        否则模板一多，列表响应体就撑起来了。
        """
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_templates ORDER BY updated_at DESC"
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["args"] = json.loads(item.get("args") or "{}")
            except Exception:
                item["args"] = {}
            item["aliases"] = self._aliases_of(item)
            item["read_only"] = bool(item.get("read_only"))
            plan = self._plan_of(item)
            item["stage_count"] = len(plan.get("stages") or [])
            item["step_count"] = sum(len(s.get("steps") or []) for s in plan.get("stages") or [])
            item.pop("plan", None)
            out.append(item)
        return out

    def get_workflow_template(self, template_id: str) -> dict[str, Any] | None:
        """模板详情，含 plan 正文。

        别名也能当 id 用 —— `get_workflow_template("巡检")` 能取到
        名为「巡检」的模板。别名表存在这里，取模板时先查别名。
        """
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_templates WHERE id=?", (template_id,)
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT * FROM workflow_templates WHERE name=? ORDER BY updated_at DESC LIMIT 1",
                    (template_id,),
                ).fetchone()
            if row is None:
                row = self._find_by_alias(conn, template_id)
        if row is None:
            return None
        item = dict(row)
        try:
            item["args"] = json.loads(item.get("args") or "{}")
        except Exception:
            item["args"] = {}
        item["aliases"] = self._aliases_of(item)
        item["read_only"] = bool(item.get("read_only"))
        item["plan"] = self._plan_of(item)
        return item

    @staticmethod
    def _find_by_alias(conn: sqlite3.Connection, alias: str) -> sqlite3.Row | None:
        """按别名找模板。别名存在 aliases 列里（JSON 数组）。

        一行一行扫 —— 模板数量是用户级（几十条），不值得建索引。
        """
        needle = str(alias).strip()
        if not needle:
            return None
        rows = conn.execute("SELECT * FROM workflow_templates").fetchall()
        for row in rows:
            try:
                aliases = json.loads(row["aliases"] or "[]")
            except Exception:
                aliases = []
            if needle in aliases:
                return row
        return None

    @staticmethod
    def _aliases_of(item: dict[str, Any]) -> list[str]:
        """取别名。存的是 JSON 数组，读的时候容错。"""
        raw = item.get("aliases")
        if isinstance(raw, list):
            return [str(a) for a in raw]
        try:
            parsed = json.loads(raw or "[]")
        except Exception:
            return []
        if not isinstance(parsed, list):
            return []
        return [str(a) for a in parsed]

    def delete_workflow_template(self, template_id: str) -> bool:
        with self._lock, self._conn() as conn:
            cur = conn.execute("DELETE FROM workflow_templates WHERE id=?", (template_id,))
        return cur.rowcount > 0

    def mark_workflow_template_run(self, template_id: str) -> None:
        """记一次触发。用户要看"这份模板我用过几次、上次是什么时候"。"""
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE workflow_templates SET run_count=run_count+1, last_run_at=? WHERE id=?",
                (time.time(), template_id),
            )

    # ---------- run 的 plan ----------
    def save_workflow_run_plan(self, run_id: str, plan: dict[str, Any]) -> None:
        """存下这次 run 用的 plan —— "把这次运行存成模板"的数据来源。"""
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO workflow_run_plans (run_id, plan, created_at) VALUES (?,?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET plan=excluded.plan",
                (run_id, json.dumps(plan or {}, ensure_ascii=False), time.time()),
            )

    def get_workflow_run_plan(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT plan FROM workflow_run_plans WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["plan"])
        except Exception:
            return None

    @staticmethod
    def _plan_of(item: dict[str, Any]) -> dict[str, Any]:
        """把存着的 plan 文本解回对象。解不了就当没有 plan（不抛）。"""
        raw = item.get("plan")
        if isinstance(raw, dict):
            return raw
        try:
            parsed = json.loads(raw or "{}")
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

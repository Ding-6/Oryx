from __future__ import annotations

import threading
import time
from pathlib import Path

from oryx.automation import AutomationScheduler, BEIJING_TZ
from oryx.store import Store
from oryx.team import TeamRegistry


def test_team_definition_persists_but_messages_are_private(tmp_path: Path):
    store = Store(tmp_path / "oryx.db")
    teams = TeamRegistry(store)
    team, error = teams.create("研究小组", ["researcher", "reviewer"], "资料研究", owner_id="u1")
    assert team is not None and not error
    assert teams.add_member("研究小组", "writer", owner_id="u2")[0] is False
    assert teams.delete("研究小组", owner_id="u2") is False

    message, error = teams.post("研究小组", "main", "researcher", "仅供我看的任务", owner_id="u1")
    assert message is not None and not error
    assert len(teams.thread("研究小组", owner_id="u1")) == 1
    assert teams.thread("研究小组", owner_id="u2") == []

    restored = TeamRegistry(Store(tmp_path / "oryx.db"))
    assert restored.get("研究小组") is not None
    assert restored.list("u2")[0]["can_edit"] is False
    assert restored.thread("研究小组", owner_id="u1")[0]["content"] == "仅供我看的任务"
    assert restored.thread("研究小组", owner_id="u2") == []


def test_team_rename_persists_and_delete_keeps_other_private_mailboxes(tmp_path: Path):
    store = Store(tmp_path / "oryx.db")
    teams = TeamRegistry(store)
    team, _ = teams.create("团队甲", ["researcher", "reviewer"], owner_id="u1")
    assert team is not None
    teams.post("团队甲", "main", "researcher", "创建者消息", owner_id="u1")
    teams.post("团队甲", "main", "reviewer", "另一个账号消息", owner_id="u2")

    team.name = "团队乙"
    team.goal = "更新后的目标"
    teams._persist_team(team)
    teams._teams.pop("团队甲")
    teams._teams["团队乙"] = team
    row = Store(tmp_path / "oryx.db").get_team("团队乙")
    assert row is not None and row["goal"] == "更新后的目标"

    assert teams.delete("团队乙", owner_id="u1") is True
    retained = store.team_messages(team.id)
    assert len(retained) == 1 and retained[0]["owner_id"] == "u2"


def test_automation_records_are_private_and_task_state_persists(tmp_path: Path):
    store = Store(tmp_path / "oryx.db")
    scheduler = AutomationScheduler(lambda _item: {"ok": True}, store=store)
    item = scheduler.add("早报", "0 9 * * 1-5", "生成工作日简报", owner_id="u1")
    scheduler.add("另一个账号的任务", "0 10 * * *", "私有目标", owner_id="u2")
    assert len(scheduler.list("u1")) == 1
    assert scheduler.update(item.id, name="被越权修改", owner_id="u2") is None
    assert scheduler.toggle(item.id, False, owner_id="u2") is None
    assert scheduler.remove(item.id, owner_id="u2") is False

    item.retry_count = 2
    item.retrying = True
    item.next_run = time.time() + 300
    scheduler._persist(item)
    restored = AutomationScheduler(lambda _item: None, store=Store(tmp_path / "oryx.db"))
    recovered = restored.items[item.id]
    assert recovered.owner_id == "u1"
    assert recovered.retry_count == 2
    assert recovered.retrying is True
    assert restored.list("u2")[0]["name"] == "另一个账号的任务"


def test_automation_failure_uses_three_increasing_retries(monkeypatch):
    import oryx.automation as automation_module

    now = [1000.0]
    monkeypatch.setattr(automation_module, "time", type("Clock", (), {"time": staticmethod(lambda: now[0])}))
    attempts: list[int] = []

    def fail(item):
        attempts.append(item.attempt)
        return {"ok": False, "error": "暂时失败", "run_id": f"run-{item.attempt}"}

    scheduler = AutomationScheduler(fail)
    item = scheduler.add("任务", "* * * * *", "执行")
    scheduler._run_item(item)
    assert item.retry_count == 1 and item.retrying is True
    assert item.next_run == 1060.0
    now[0] = item.next_run
    scheduler._run_item(item, retry=True)
    assert item.retry_count == 2 and item.next_run == 1180.0
    now[0] = item.next_run
    scheduler._run_item(item, retry=True)
    assert item.retry_count == 3 and item.next_run == 1420.0
    now[0] = item.next_run
    scheduler._run_item(item, retry=True)
    assert attempts == [1, 2, 3, 4]
    assert item.retry_count == 3 and item.retrying is False and item.next_run is None


def test_manual_retry_requires_owner_and_links_history(tmp_path: Path):
    store = Store(tmp_path / "oryx.db")
    store.start_automation_run(
        "failed-1", "auto-1", "每日报告", "生成报告", time.time(), owner_id="u1", attempt=1
    )
    store.finish_automation_run("failed-1", "failed", error="上游超时")
    completed = threading.Event()

    def record_retry(item):
        store.start_automation_run(
            "manual-1", item.id, item.name, item.goal, time.time(),
            owner_id=item.owner_id, attempt=item.attempt,
            parent_run_id=item.retry_parent_run_id, manual_retry=item.manual_retry,
        )
        store.finish_automation_run("manual-1", "done", output="完成")
        completed.set()
        return {"ok": True, "run_id": "manual-1"}

    scheduler = AutomationScheduler(record_retry, store=store)
    assert scheduler.retry_failed_run("failed-1", owner_id="u2") is None
    assert scheduler.retry_failed_run("failed-1", owner_id="u1")["queued"] is True
    assert completed.wait(2)
    row = store.automation_run_detail("manual-1", owner_id="u1")
    assert row is not None
    assert row["manual_retry"] == 1
    assert row["parent_run_id"] == "failed-1"
    assert store.automation_run_detail("manual-1", owner_id="u2") is None


def test_automation_timezone_is_beijing():
    assert BEIJING_TZ.utcoffset(None).total_seconds() == 8 * 60 * 60

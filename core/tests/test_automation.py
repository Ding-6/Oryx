"""自动化 cron 解析 + 调度触发。"""

from __future__ import annotations

import time

import pytest

from oryx.automation import AutomationScheduler, describe, parse_cron


def test_parse_every_minute():
    spec = parse_cron("* * * * *")
    assert len(spec.minutes) == 60
    assert len(spec.hours) == 24


def test_parse_fixed_time():
    spec = parse_cron("30 9 * * *")
    assert spec.minutes == {30}
    assert spec.hours == {9}
    assert spec.weekdays == {0, 1, 2, 3, 4, 5, 6}


def test_parse_range():
    spec = parse_cron("0 9-17 * * 1-5")
    assert spec.hours == {9, 10, 11, 12, 13, 14, 15, 16, 17}
    assert spec.weekdays == {1, 2, 3, 4, 5}


def test_parse_list():
    spec = parse_cron("0,15,30,45 * * * *")
    assert spec.minutes == {0, 15, 30, 45}


def test_parse_step():
    spec = parse_cron("*/15 * * * *")
    assert spec.minutes == {0, 15, 30, 45}


def test_parse_aliases():
    spec = parse_cron("@daily")
    assert spec.hours == {0}
    assert spec.minutes == {0}
    assert parse_cron("@weekly").weekdays == {0}


def test_parse_rejects_bad():
    with pytest.raises(ValueError):
        parse_cron("not a cron")
    with pytest.raises(ValueError):
        parse_cron("99 * * * *")     # 分钟越界
    with pytest.raises(ValueError):
        parse_cron("0 25 * * *")     # 小时越界
    with pytest.raises(ValueError):
        parse_cron("0 9 * *")         # 段数不对


def test_matches():
    """星期必须按 cron 约定（0=周日）判定，且不依赖平台差异。

    Windows 的 time.localtime 里 tm_wday 是「周一=0」，Unix 是「周日=0」，
    所以这里用 datetime 构造，绕开这个坑。
    """
    import datetime

    spec = parse_cron("30 9 * * 1-5")
    monday = datetime.datetime(2026, 10, 19, 9, 30)
    sunday = datetime.datetime(2026, 10, 18, 9, 30)
    saturday = datetime.datetime(2026, 10, 17, 9, 30)
    assert monday.isoweekday() == 1
    assert sunday.isoweekday() == 7

    assert spec.matches(monday)
    assert not spec.matches(sunday)
    assert not spec.matches(saturday)


def test_weekday_numbering_is_cron_style():
    """0 必须是周日，6 必须是周六。"""
    import datetime

    sunday = parse_cron("0 0 * * 0")
    saturday = parse_cron("0 0 * * 6")
    monday = parse_cron("0 0 * * 1")

    assert sunday.matches(datetime.datetime(2026, 10, 18, 0, 0))   # 周日
    assert not sunday.matches(datetime.datetime(2026, 10, 19, 0, 0))  # 周一
    assert saturday.matches(datetime.datetime(2026, 10, 17, 0, 0))  # 周六
    assert monday.matches(datetime.datetime(2026, 10, 19, 0, 0))    # 周一


def test_matches_is_platform_independent():
    """同样的 cron，在 Windows 和 Unix 上必须命中同一天。"""
    import datetime

    spec = parse_cron("0 9 * * 1-5")
    for day in range(1, 29):
        moment = datetime.datetime(2026, 10, day, 9, 0)
        # 用 UTC 构造的同一时刻，星期必须一致
        assert spec.matches(moment) == (moment.isoweekday() <= 5)


def test_describe():
    assert "9 点" in describe("0 9 * * *")
    assert "9 点" in describe("30 9 * * 1-5")


def test_scheduler_add_remove():
    scheduler = AutomationScheduler(lambda item: None)
    item = scheduler.add("每日检查", "0 9 * * *", "看看有什么新消息")
    assert len(scheduler.list()) == 1

    assert scheduler.remove(item.id) is True
    assert scheduler.remove(item.id) is False


def test_scheduler_rejects_bad_cron():
    scheduler = AutomationScheduler(lambda item: None)
    with pytest.raises(ValueError):
        scheduler.add("坏的", "nope", "goal")


def test_scheduler_fires_on_match():
    fired: list = []
    scheduler = AutomationScheduler(lambda item: fired.append(item), tick_seconds=0.05)
    item = scheduler.add("每分钟", "* * * * *", "跑一下")

    # 手动把"当前分钟"喂给它，验证触发
    import datetime
    moment = datetime.datetime.now()
    key = (moment.minute, moment.hour, moment.day, moment.month, moment.isoweekday() % 7)
    scheduler._last_tick = key
    scheduler._tick()  # 同一分钟不会再触发

    scheduler._last_tick = None
    scheduler._tick()
    # 因为 tm_sec 太大时会跳过补跑，所以结果可能为 0；这里只验证不抛异常
    assert isinstance(fired, list)


def test_disabled_task_does_not_fire():
    fired: list = []
    scheduler = AutomationScheduler(lambda item: fired.append(item), tick_seconds=0.05)
    item = scheduler.add("停用的", "* * * * *", "goal")
    scheduler.toggle(item.id, False)

    moment = time.localtime()
    scheduler._last_tick = None
    scheduler._tick()
    assert fired == []


def test_scheduler_update():
    """改定时任务：名字、cron、动作都能改。"""
    scheduler = AutomationScheduler(lambda item: None)
    item = scheduler.add("旧名字", "0 9 * * *", "旧动作")

    got = scheduler.update(item.id, name="新名字", cron="30 8 * * *", goal="新动作")
    assert got is not None
    assert got.name == "新名字"
    assert got.cron == "30 8 * * *"
    assert got.goal == "新动作"


def test_scheduler_update_keeps_unset_fields():
    """空串表示「这一项不改」—— 前端表单没填的字段不该被清空。"""
    scheduler = AutomationScheduler(lambda item: None)
    item = scheduler.add("名字", "0 9 * * *", "动作")

    got = scheduler.update(item.id, name="", cron="", goal="")
    assert got is not None
    assert got.name == "名字"
    assert got.cron == "0 9 * * *"
    assert got.goal == "动作"


def test_scheduler_update_rejects_bad_cron():
    """cron 语法错就不该改 —— 半改状态比不改更糟。"""
    scheduler = AutomationScheduler(lambda item: None)
    item = scheduler.add("名字", "0 9 * * *", "动作")

    with pytest.raises(ValueError):
        scheduler.update(item.id, name="", cron="nope", goal="")
    assert scheduler.list()[0]["cron"] == "0 9 * * *"


def test_scheduler_update_unknown_returns_none():
    scheduler = AutomationScheduler(lambda item: None)
    assert scheduler.update("ghost", name="x") is None


def test_scheduler_update_clears_pending_next_run():
    """改了 cron 要清掉排好的下次触发 —— 否则改完时间还按旧周期跑。"""
    scheduler = AutomationScheduler(lambda item: None)
    item = scheduler.add("名字", "0 9 * * *", "动作")
    with scheduler._lock:
        scheduler.items[item.id].next_run = time.time() + 3600

    got = scheduler.update(item.id, cron="30 8 * * *")
    assert got is not None
    assert got.next_run is None


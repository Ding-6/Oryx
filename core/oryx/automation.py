"""自动化：定时任务。

最小可用但真的能用：
- 任务在后台线程里按 cron 表达式轮询触发；
- 触发后跑一次普通的 Agent 循环（和用户手动发消息一样）；
- 全部落库，可查历史。

时间用本地时区，cron 只支持 5 段（分 时 日 月 周）。
"""

from __future__ import annotations

import datetime
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

FIELD_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
FIELD_NAMES = ["分钟", "小时", "日", "月", "周"]
ALIASES = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}


@dataclass
class CronSpec:
    minutes: set[int] = field(default_factory=set)
    hours: set[int] = field(default_factory=set)
    days: set[int] = field(default_factory=set)
    months: set[int] = field(default_factory=set)
    weekdays: set[int] = field(default_factory=set)

    def matches(self, moment: "datetime.datetime") -> bool:
        """moment 是本地时间的 datetime。

        注意星期换算：cron 用 0=周日，而 datetime.date.isoweekday() 是 1=周一。
        Windows 上 time.localtime 的 tm_wday 又是「周一=0」，与 Unix 相反——
        所以这里一律走 isoweekday()，不碰 tm_wday，跨平台行为一致。
        """
        cron_wday = moment.isoweekday() % 7  # 周一→1 … 周日→0
        return (
            moment.minute in self.minutes
            and moment.hour in self.hours
            and moment.day in self.days
            and moment.month in self.months
            and cron_wday in self.weekdays
        )


def parse_cron(expression: str) -> CronSpec:
    """解析 5 段 cron。支持 *、a-b、a,b、*/n。"""
    text = (expression or "").strip().lower()
    text = ALIASES.get(text, text)
    parts = text.split()
    if len(parts) != 5:
        raise ValueError(f"cron 必须是 5 段（分 时 日 月 周），收到 {len(parts)} 段")

    values: list[set[int]] = []
    for index, part in enumerate(parts):
        low, high = FIELD_RANGES[index]
        collected: set[int] = set()
        for chunk in part.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            step = 1
            if "/" in chunk:
                chunk, _, step_text = chunk.partition("/")
                if not step_text.isdigit() or int(step_text) < 1:
                    raise ValueError(f"步长不合法：{part}")
                step = int(step_text)
            if chunk in ("*", ""):
                start, end = low, high
            elif "-" in chunk:
                start_text, _, end_text = chunk.partition("-")
                if not start_text.isdigit() or not end_text.isdigit():
                    raise ValueError(f"范围不合法：{part}")
                start, end = int(start_text), int(end_text)
            else:
                if not chunk.isdigit():
                    raise ValueError(f"不认识的写法：{part}")
                start = end = int(chunk)
            if start < low or end > high or start > end:
                raise ValueError(f"{FIELD_NAMES[index]}超出范围（{low}-{high}）：{part}")
            collected.update(range(start, end + 1, step))
        if not collected:
            raise ValueError(f"{FIELD_NAMES[index]}不能为空")
        values.append(collected)

    return CronSpec(*values)


def describe(expression: str) -> str:
    """把 cron 说成人话。"""
    spec = parse_cron(expression)
    def part(values: set[int], names: list[str]) -> str:
        if len(values) == 1:
            return names[min(values)]
        return "、".join(names[v] for v in sorted(values))

    minute_names = [f"{m} 分" for m in range(60)]
    hour_names = [f"{h} 点" for h in range(24)]
    return f"每{part(spec.minutes, minute_names)}的{part(spec.hours, hour_names)}"


@dataclass
class Automation:
    id: str
    name: str
    cron: str
    goal: str
    enabled: bool = True
    recurring: bool = True
    jitter_ratio: float = 0.1
    last_run: float | None = None
    next_run: float | None = None
    run_count: int = 0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "cron": self.cron,
            "goal": self.goal,
            "enabled": self.enabled,
            "recurring": self.recurring,
            "last_run": self.last_run,
            "next_run": self.next_run,
            "run_count": self.run_count,
            "created_at": self.created_at,
            "description": describe(self.cron),
        }


class AutomationScheduler:
    """定时任务调度器。

    三个关键设计（对齐 WorkBuddy 的 scheduled-tasks）：
    1. **持久化**：任务落 SQLite，重启后还在（CLI 侧是会话绑定不落盘，
       但 Oryx 是常驻桌面应用，落盘才符合预期）。
    2. **抖动**：循环任务最多延迟周期时长的 10%（上限 15 分钟），
       避免整点集体触发造成写锁争抢。
    3. **仅空闲触发**：主 Agent 正在忙的时候顺延，不抢占当前任务。
    """

    # 抖动上限
    MAX_JITTER_SECONDS = 15 * 60
    # 每会话最多任务数
    MAX_ITEMS = 50

    def __init__(
        self,
        on_fire: Callable[[Automation], None],
        tick_seconds: float = 20.0,
        store: Any = None,
        is_busy: Callable[[], bool] | None = None,
        on_error: Callable[[Automation, str], None] | None = None,
    ):
        self.on_fire = on_fire
        self.tick_seconds = tick_seconds
        self.store = store
        self.is_busy = is_busy or (lambda: False)
        # 触发失败时的回调。用于发通知（系统通知 / webhook）。
        # 不传就只记 last_error，不外发。
        self.on_error = on_error
        # 最近一次触发失败的原因。给人看，也便于排查。
        self.last_error = ""
        self.items: dict[str, Automation] = {}
        self._last_tick: tuple[int, int, int, int, int] | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._spec_cache: dict[str, CronSpec] = {}
        if store is not None:
            self._restore()

    # ---------------- 持久化 ----------------
    def _restore(self) -> None:
        """从存储里恢复任务。"""
        try:
            rows = self.store.list_automations()
        except Exception:
            return
        for row in rows:
            try:
                item = Automation(
                    id=str(row.get("id")),
                    name=str(row.get("name") or ""),
                    cron=str(row.get("cron") or ""),
                    goal=str(row.get("goal") or ""),
                    enabled=bool(row.get("enabled", True)),
                    recurring=bool(row.get("recurring", True)),
                    jitter_ratio=float(row.get("jitter_ratio") or 0.1),
                    last_run=row.get("last_run_at"),
                    next_run=row.get("next_run_at"),
                    created_at=float(row.get("created_at") or time.time()),
                )
                parse_cron(item.cron)  # 校验，坏的就跳过
            except Exception:
                continue
            self.items[item.id] = item

    def _persist(self, item: Automation) -> None:
        if self.store is None:
            return
        try:
            self.store.save_automation({
                "id": item.id,
                "name": item.name,
                "cron": item.cron,
                "goal": item.goal,
                "enabled": item.enabled,
                "recurring": item.recurring,
                "jitter_ratio": item.jitter_ratio,
                "next_run_at": item.next_run,
                "last_run_at": item.last_run,
                "created_at": item.created_at,
            })
        except Exception:
            pass

    # ---------------- 管理 ----------------
    def add(self, name: str, cron: str, goal: str) -> Automation:
        parse_cron(cron)  # 先校验
        with self._lock:
            if len(self.items) >= self.MAX_ITEMS:
                raise ValueError(f"定时任务最多 {self.MAX_ITEMS} 个")
        item = Automation(
            id=uuid.uuid4().hex[:8], name=name or cron, cron=cron, goal=goal
        )
        with self._lock:
            self.items[item.id] = item
        self._persist(item)
        return item

    def remove(self, item_id: str) -> bool:
        with self._lock:
            removed = self.items.pop(item_id, None) is not None
        if removed and self.store is not None:
            try:
                self.store.delete_automation(item_id)
            except Exception:
                pass
        return removed

    def update(
        self,
        item_id: str,
        name: str = "",
        cron: str = "",
        goal: str = "",
    ) -> Automation | None:
        """改一个定时任务的名字、cron 或动作。

        传空串表示「这一项不改」—— 前端表单里没填的字段不该被清空。
        cron 改动会把排好的下次触发时间清掉，否则改完时间还是按旧周期跑。
        """
        with self._lock:
            item = self.items.get(item_id)
            if item is None:
                return None
            if cron:
                parse_cron(cron)  # 语法错就别改了
                item.cron = cron
                item.next_run = None
            if name:
                item.name = name
            if goal:
                item.goal = goal
        self._persist(item)
        return item

    def toggle(self, item_id: str, enabled: bool) -> Automation | None:
        with self._lock:
            item = self.items.get(item_id)
            if item:
                item.enabled = enabled
        if item:
            self._persist(item)
        return item

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [i.to_dict() for i in self.items.values()]

    # ---------------- 运行 ----------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="oryx-automation", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                pass
            self._stop.wait(self.tick_seconds)

    def _spec(self, item: Automation) -> CronSpec:
        """解析结果缓存，避免每 20 秒重复解析同一批 cron。"""
        spec = self._spec_cache.get(item.cron)
        if spec is None:
            spec = parse_cron(item.cron)
            self._spec_cache[item.cron] = spec
        return spec

    def _jitter_seconds(self, item: Automation) -> float:
        """这次触发要延迟多少秒。

        循环任务：周期时长的 10%，上限 15 分钟。
        一次性任务：提前最多 90 秒（返回负数表示提前）。
        """
        import random

        if not item.recurring:
            return -random.uniform(0, 90)
        period = self._period_seconds(item)
        if period <= 0:
            return 0.0
        delay = period * item.jitter_ratio
        return min(delay, self.MAX_JITTER_SECONDS) * random.random()

    def _period_seconds(self, item: Automation) -> float:
        """粗略估算任务周期（秒）。解析不出就用 1 小时兜底。"""
        spec = self._spec(item)
        if spec.minutes and len(spec.minutes) == 1:
            return 60.0
        return 3600.0

    def _tick(self) -> None:
        now = datetime.datetime.now()
        key = (now.minute, now.hour, now.day, now.month, now.isoweekday() % 7)
        if self._last_tick == key:
            return
        self._last_tick = key
        if now.second > 5:
            return  # 刚启动时不要补跑

        # 主 Agent 正忙着就顺延 —— 定时任务不抢占当前任务
        try:
            if self.is_busy():
                return
        except Exception:
            pass

        with self._lock:
            items = list(self.items.values())

        for item in items:
            if not item.enabled:
                continue

            # 已经排过抖动窗口 —— 到点了就触发
            if item.next_run is not None:
                if time.time() < item.next_run:
                    continue
                item.next_run = None
            elif self._spec(item).matches(now):
                # 第一次命中：排一个抖动窗口，下一轮再真正触发
                delay = self._jitter_seconds(item)
                if delay > 0:
                    item.next_run = time.time() + delay
                    self._persist(item)
                    continue
                item.next_run = None

            self._run_item(item)

    def _run_item(self, item: Automation) -> None:
        """真正触发一个定时任务。

        单独抽出来是因为这段逻辑有两个关注点，混在 _tick 里看不清：
        1. 触发本身（on_fire）；
        2. 触发失败**不能被吞掉** —— 记进 last_error，交给 on_error 通知出去。

        以前这里是 `except Exception: pass` —— 定时任务失败了没人知道，
        用户不打开界面就完全错过。对齐 WorkBuddy 的 scheduled-tasks：
        失败必须可见（记进执行记录 + 走通知渠道）。

        但也不能让异常把调度线程带死 —— 记下来，交给上层处理。
        """
        item.last_run = time.time()
        item.run_count += 1
        try:
            self.on_fire(item)
        except Exception as exc:  # noqa: BLE001 — 记下来，不能让调度线程死
            self.last_error = f"{type(exc).__name__}: {exc}"
            if self.on_error is not None:
                try:
                    self.on_error(item, self.last_error)
                except Exception:
                    pass
        if self.store is not None:
            try:
                self.store.mark_automation_run(item.id, None)
            except Exception:
                pass

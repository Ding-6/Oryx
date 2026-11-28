"""会话域：会话列表、详情、改名、删除、待办、用量。

这是 v2 重写的**样板域** —— 第一个按新契约改的域。

    契约层      请求体用 pydantic 模型，不再是裸 dict 请求体
    统一响应    成功 `{code, data, message}`；错误 `{code, data, message, detail}`
    前端接入    老前端（core/web）同步改，新前端（apps/web）直接用

其余 21 个域照这个路子铺开。

路由：
    POST   /api/sessions                        新建空会话
    GET    /api/sessions                        列表（分页 + 服务端搜索）
    GET    /api/sessions/{task_id}              详情（消息 / 步骤 / 待办 / 用量）
    DELETE /api/sessions/{task_id}              删除
    PUT    /api/sessions/{task_id}              改名
    PUT    /api/sessions/{task_id}/todos/{tid}  改待办状态
    GET    /api/sessions/{task_id}/steps        执行步骤
    GET    /api/sessions/{task_id}/todos        待办
    GET    /api/sessions/{task_id}/usage        用量摘要

**data 的形状规则**（v2 全局规则，spec §1.1）
--------------------------------------------
`data` 恒为该接口的**领域对象**：

    · 领域对象本身就是一个列表 → `data` 是**数组**
      （`/api/sessions`、`/api/sessions/{id}/steps`、`/api/sessions/{id}/todos`）
    · 否则 → `data` 是**对象**
      （详情、用量摘要、大屏那三个聚合视图）

所以 `/api/sessions` 返回的是**数组**，不是 `{"sessions": [...]}` ——
多包一层没有任何信息量，前端却要为此多写一次 `.sessions`。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from ..api.response import ok
from ..ipc import IpcError
from ..store import Store

router = APIRouter(prefix="/api/sessions", tags=["会话"])

# 待办的三种状态。
TODO_STATUSES = ("pending", "in_progress", "done")


class RenameRequest(BaseModel):
    """改会话的请求。

    `title` 传空串 = 撤掉自定义名字，退回用首条提问当标题。
    传 null 也当空串 —— 前端表单清空时给的就是 null，
    以前 `str(None)` 会把字面量 "None" 存成会话名。

    `team` = 给这个会话挂一个团队（每个对话可以有自己的团队）。
    不传 = 不动它（`None` 与空串的区别：空串是「摘掉团队」）。
    """

    title: str | None = None
    team: str | None = None

    def given(self, name: str) -> bool:
        """这个字段在请求体里**出现过**吗？

        为什么不能只看「值是不是 None」
        ------------------------------
        三种语义要共存：
          · ``{title: "新名"}``  → 改名
          · ``{title: ""}``      → 清空自定义名字（前端清空时传的就是空串）
          · ``{team: "甲组"}``   → 只挂团队，**名字一个字都不许动**
          · ``{}``               → 老约定：清空名字

        「只挂团队」和「空 body」光看值分不开（都是 title=None）——
        所以要看字段**有没有出现**。
        """
        return name in self.model_fields_set

    @field_validator("title", mode="before")
    @classmethod
    def _null_to_empty(cls, value):
        return "" if value is None else value


class CreateSessionRequest(BaseModel):
    """新建一条空会话。

    `title` 可以不给 —— 空会话在列表里显示「新会话」，
    第一次发消息后由 goal 摘要接手；给了就当场命名。
    长度在**业务层**校验（400 + 中文），跟待办状态一个道理：
    Literal / max_length 抛的 422 只有英文机器话。
    """

    title: str = ""


class TodoCreateRequest(BaseModel):
    """新建一条待办。

    用户在对话里手动加一件事 —— 以前只有 agent 能建（task_create 工具），
    用户想自己列个清单都做不到。
    """

    title: str = ""
    status: str = "pending"


class TodoStatusRequest(BaseModel):
    """改待办状态。

    状态取值在**业务层**校验（400 + 中文提示），不在这里用 Literal ——
    Literal 不匹配会变成 422，错误体里只有字段路径和英文校验消息，
    用户看到的是「body.status：Input should be 'pending'」这种机器话。
    """

    status: str


# ── 纯函数（不依赖 FastAPI，便于单测）────────────────────────────────


def list_sessions(store: Store, limit: int = 50, owner_id: str | None = None,
                  offset: int = 0, query: str | None = None) -> list[dict]:
    """会话列表。domain object 是列表 → data 是数组。

    `query` 走 SQL 过滤**全部**会话（不是前端在已加载的一页里筛），
    `offset` 配合 limit 翻页；两者都不改变 data 是数组这个 v2 形状。
    """
    limit = max(1, min(int(limit), 200))
    return store.list_sessions(limit, owner_id, max(0, int(offset)), query)


def create_session(store: Store, title: str = "",
                   owner_id: str | None = None) -> dict:
    """新建一条空会话 —— 先开会话，第一句话稍后再发。

    返回 `{task_id, title, status:'idle'}`。状态用 idle 而不是 running：
    内核续聊时会自己把状态改成 running（loop.py:2268），预先置位只会让
    界面误以为「正在运行」而禁止发送。
    """
    name = (title or "").strip()
    if len(name) > 60:
        raise HTTPException(400, "会话名太长了（最多 60 字）")
    task_id = store.create_empty_session(owner_id)
    if name:
        store.rename_session(task_id, name, owner_id)
    return {"task_id": task_id, "title": name or "新会话", "status": "idle"}


def read_session(store: Store, task_id: str, owner_id: str | None = None) -> dict:
    """会话详情。domain object 是对象 → data 是对象。"""
    detail = store.session_detail(task_id, owner_id)
    if detail is None:
        raise HTTPException(404, "没有这个会话")
    return detail


def delete_session(store: Store, task_id: str, owner_id: str | None = None) -> dict:
    if not store.delete_session(task_id, owner_id):
        raise HTTPException(404, "没有这个会话")
    return {"deleted": task_id}


def session_steps(store: Store, task_id: str, owner_id: str | None = None) -> list[dict]:
    """某个会话的执行步骤 —— 右栏「执行」标签用。

    单独开一个接口，是为了让右栏**刷新时不必拉整个会话详情**
    （详情里带消息、审计、待办，右栏只要步骤）。

    domain object 是列表 → data 是数组。
    """
    if store.session_detail(task_id, owner_id) is None:
        raise HTTPException(404, "没有这个会话")
    return store.steps_for_task(task_id)


def session_todos(store: Store, task_id: str, owner_id: str | None = None) -> list[dict]:
    """某个会话的待办 —— 右栏「任务」标签用。domain object 是列表 → 数组。"""
    if store.session_detail(task_id, owner_id) is None:
        raise HTTPException(404, "没有这个会话")
    return store.list_todos(task_id)


async def session_approvals(
    agent: Any, store: Store, task_id: str, owner_id: str | None = None,
) -> list[dict]:
    """这个会话当前正在等确认的操作 —— 审批卡的兜底数据源。

    SSE 只在事件发生的那一刻推送；用户刷新页面、或者断线太久导致缓冲滚过
    `since` 时，审批卡会丢。页面加载时调这个接口把卡片重建出来。

    先校验会话归属，再问内核 —— 不然能凭 step_id 查到别人的工具参数。
    内核刚起 / 正在重启时返回空列表（下一次 SSE 会补上），不弹 503 吓人。
    """
    if store.session_detail(task_id, owner_id) is None:
        raise HTTPException(404, "没有这个会话")
    try:
        items = await agent.call_async(
            "chat.pending_approvals", task_id=task_id, owner_id=owner_id
        )
    except IpcError:
        return []
    if not isinstance(items, list):
        return []
    return items


def session_usage(store: Store, task_id: str, owner_id: str | None = None) -> dict:
    """某个会话的用量与审计摘要 —— 右栏「详情」标签用。"""
    detail = store.session_detail(task_id, owner_id)
    if detail is None:
        raise HTTPException(404, "没有这个会话")
    return {
        "task_id": task_id,
        "usage": detail.get("usage") or {},
        "steps": len(detail.get("steps") or []),
        "todos": len(detail.get("todos") or []),
        "messages": len(detail.get("messages") or []),
    }


def create_todo(store: Store, task_id: str, title: str, status: str = "pending",
                owner_id: str | None = None) -> dict:
    """新建一条待办 —— 用户手动加。

    状态取值校验跟改状态一致：pending / in_progress / done。
    """
    title = (title or "").strip()
    if not title:
        raise HTTPException(400, "待办得有个标题")
    if len(title) > 200:
        raise HTTPException(400, "标题太长了（最多 200 字）")
    status = (status or "pending").strip()
    if status not in TODO_STATUSES:
        raise HTTPException(400, "status 只能是 pending / in_progress / done")
    if store.session_detail(task_id, owner_id) is None:
        raise HTTPException(404, "没有这个会话")
    return store.add_todo(task_id, title)


def todo_progress(todos: list[dict]) -> dict:
    """待办的进度摘要 —— 给界面画进度条用。

    「完成」和「进行中」都算有进展（进行中说明已经动过了），
    只算完全没开始的才算未推进。
    """
    total = len(todos or [])
    done = sum(1 for t in (todos or []) if t.get("status") == "done")
    doing = sum(1 for t in (todos or []) if t.get("status") == "in_progress")
    return {
        "total": total,
        "done": done,
        "in_progress": doing,
        "pending": total - done - doing,
        # 百分比取整 —— 「33%」比「33.333333%」好看
        "percent": int(round((done + doing) * 100 / total)) if total else 0,
    }


def update_todo_status(store: Store, task_id: str, todo_id: str, status: str,
                       owner_id: str | None = None) -> dict:
    """改待办的状态 —— 用户在右栏「任务」标签里直接勾。

    状态：`pending` / `in_progress` / `done`。
    """
    status = (status or "").strip()
    if status not in TODO_STATUSES:
        raise HTTPException(
            400, "status 只能是 pending / in_progress / done"
        )
    if store.session_detail(task_id, owner_id) is None:
        raise HTTPException(404, "没有这个会话")
    todo = store.update_todo(task_id, todo_id, status)
    if todo is None:
        raise HTTPException(404, "没有这个待办")
    return {"updated": todo_id, "todo": todo}


def set_session_team(store: Store, task_id: str, team: str,
                     owner_id: str | None = None) -> dict:
    """给会话挂一个团队。

    「每个对话可以有一个自己的团队」——
    团队是全局的，会话是私人的，这一列把两者挂起来。
    传空串 = 摘掉这个会话的团队。
    """
    name = (team or "").strip()
    if not store.set_session_team(task_id, name, owner_id):
        raise HTTPException(404, "没有这个会话")
    return {"task_id": task_id, "team": name}


def rename_session(store: Store, task_id: str, title: str,
                   owner_id: str | None = None) -> dict:
    """给会话改个名字。

    名字存 tasks.title —— 会话 id 不变，消息、步骤、检查点都不受影响。
    传空串 = 撤掉自定义名字，退回用首条提问当标题。
    """
    if not store.rename_session(task_id, title, owner_id):
        raise HTTPException(404, "没有这个会话")
    return {"renamed": task_id, "title": (title or "").strip()}


def update_session(store: Store, task_id: str, req: RenameRequest,
                   owner_id: str | None = None) -> dict:
    """改会话：名字 + 挂的团队，可以只改其中一个。

    **为什么合并成一个接口**
    ----------------------
    「改名」和「挂团队」都是改同一行 tasks 的属性，
    分成两个接口就得发两次请求，还容易出现「改了名但团队没跟上」的中间态。

    **三种语义共存**（靠「字段有没有出现」区分，不是靠值）
    --------------------------------------------------
        {title: "新名"}  → 改名
        {title: ""}      → 清空自定义名字（前端清空时传的就是空串）
        {team: "甲组"}   → 只挂团队，**名字一个字都不许动**
        {}               → 老约定：清空名字

    「只挂团队」和「空 body」光看值分不开（都是 title=None），
    所以要看字段**在不在请求体里**（`model_fields_set`）。
    """
    # 名字：显式给了就改；只给了 team 就不动；什么都没给按老约定清空。
    if req.given("title"):
        want_title = req.title or ""
    elif req.given("team"):
        want_title = None          # 只挂团队 —— 名字一个字都不动
    else:
        want_title = ""            # 空 body = 撤掉自定义名字（老语义）
    if want_title is not None:
        if not store.rename_session(task_id, want_title, owner_id):
            raise HTTPException(404, "没有这个会话")
    if req.given("team"):
        if not store.set_session_team(task_id, req.team or "", owner_id):
            raise HTTPException(404, "没有这个会话")
    # 返回**改完之后**的实际值 —— 只改名字时 team 也得报当前挂着的那一个，
    # 否则前端会把「没动过的团队」显示成空（刚挂上的团队会凭空消失）。
    detail = store.session_detail(task_id, owner_id) or {}
    return {
        "task_id": task_id,
        "title": str(detail.get("title") or ""),
        "team": store.session_team(task_id, owner_id),
        # `renamed` 是老字段 —— 保留着。
        # 以前这个接口只管改名，返回体是 {renamed, title}；
        # 现在它还管挂团队，返回体扩成了三个字段。
        # 直接把老字段删掉会**静默破坏**调用方（它读 renamed 拿到 undefined），
        # 所以留着（新代码用 task_id/title/team 就行）。
        "renamed": task_id,
    }


# ── 装配 ────────────────────────────────────────────────────────────


def register(app, store: Store, agent: Any = None) -> None:
    """把本域路由挂到 app。依赖用闭包注入，action 本身保持纯函数。

    `agent` 可选：待审批列表要问运行中的内核；没有内核时这条路由返回空数组，
    其余会话接口照常可用（老测试与命令行装配不用改）。
    """

    @router.post("")
    async def _create(request: Request, req: CreateSessionRequest | None = None):
        """新建空会话。body 可以整个省掉（前端「+ 新会话」不需要先填名字）。"""
        return ok(create_session(
            store, (req or CreateSessionRequest()).title,
            getattr(request.state, "user_id", None),
        ))

    @router.get("")
    async def _list(request: Request, limit: int = 50, offset: int = 0,
                    q: str | None = None):
        return ok(list_sessions(
            store, limit, getattr(request.state, "user_id", None), offset, q,
        ))

    @router.get("/{task_id}")
    async def _detail(task_id: str, request: Request):
        return ok(read_session(store, task_id, getattr(request.state, "user_id", None)))

    @router.delete("/{task_id}")
    async def _delete(task_id: str, request: Request):
        return ok(delete_session(store, task_id, getattr(request.state, "user_id", None)))

    @router.put("/{task_id}")
    async def _rename(task_id: str, req: RenameRequest, request: Request):
        return ok(update_session(
            store, task_id, req, getattr(request.state, "user_id", None)
        ))

    @router.put("/{task_id}/todos/{todo_id}")
    async def _update_todo(task_id: str, todo_id: str, req: TodoStatusRequest, request: Request):
        return ok(update_todo_status(
            store, task_id, todo_id, req.status, getattr(request.state, "user_id", None)
        ))

    @router.get("/{task_id}/steps")
    async def _steps(task_id: str, request: Request):
        return ok(session_steps(store, task_id, getattr(request.state, "user_id", None)))

    @router.get("/{task_id}/todos")
    async def _todos(task_id: str, request: Request):
        return ok(session_todos(store, task_id, getattr(request.state, "user_id", None)))

    @router.post("/{task_id}/todos")
    async def _create_todo(task_id: str, req: TodoCreateRequest, request: Request):
        """用户手动加一条待办。

        以前只有 agent 能建待办（task_create 工具），
        用户想自己列个清单都做不到 —— 右栏「任务」里只能看不能加。
        """
        return ok(create_todo(
            store, task_id, req.title, req.status, getattr(request.state, "user_id", None)
        ))

    @router.get("/{task_id}/approvals")
    async def _approvals(task_id: str, request: Request):
        if agent is None:
            return ok([])
        return ok(await session_approvals(
            agent, store, task_id, getattr(request.state, "user_id", None)
        ))

    @router.get("/{task_id}/usage")
    async def _usage(task_id: str, request: Request):
        return ok(session_usage(store, task_id, getattr(request.state, "user_id", None)))

    # 路由要先挂到 router 再 include，否则 app 拿不到
    app.include_router(router)

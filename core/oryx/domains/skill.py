"""技能域：技能的增删改查。

对齐 Marvis 的 modules/skill/actions/{list,install,local_install,update,delete}。
技能落盘为 SKILL.md（frontmatter + 正文），写完让 Agent 内核重新扫描目录。
技能名做白名单校验，防目录穿越。
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..agentlink import AgentLink

router = APIRouter(prefix="/api/skills", tags=["技能"])

NAME_RE = re.compile(r"[A-Za-z0-9_\-一-鿿 ]{1,64}")


def skills_root() -> Path:
    """仓库里的技能目录。

    本文件在 core/oryx/domains/ 下，要往上三层才是仓库根 —— 只往上两级
    会落到 core/，读到的是 Agent 内核自己建的空目录。
    """
    return Path(__file__).resolve().parents[3] / "skills"


def _skill_dir(name: str) -> Path:
    """技能落盘位置。只允许简单目录名，防穿越。"""
    if not NAME_RE.fullmatch(name or ""):
        raise HTTPException(400, "技能名只能含中英文、数字、-、_、空格")
    return skills_root() / name


def _write_skill_md(path: Path, name: str, description: str, body: str) -> None:
    text = f"---\nname: {name}\ndescription: {description}\n---\n{body.rstrip()}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")


def _reload(agent: AgentLink) -> None:
    """让 Agent 内核重新扫描技能目录。新写/删的技能才立刻生效。"""
    try:
        agent.call("catalog.reload")
    except Exception:
        # 内核没起来也不该阻断写文件 —— 它下次启动自己会扫到
        pass


def _library() -> "SkillLibrary":
    """本地读盘用的技能库（给写操作定位文件用）。

    **只用于「找到文件在哪」** —— 列表与读取一律走内核那份权威库。
    """
    from ..skills import SkillLibrary

    return SkillLibrary(skills_root())


def _remote_skills(agent: AgentLink) -> list[dict] | None:
    """向内核要技能清单。

    为什么不让 server 侧自己建一个 SkillLibrary：
    那会**绕过注册表** —— 停用名单、依赖阻塞全都不生效，
    界面上关掉的技能照样列在这里，用户点了跟没点一样。
    内核那份库以注册表为准，才是权威。

    返回 `None` 表示**问不到**（内核没起来），
    与「内核说没有技能」（空列表）要区分开 ——
    前者该退回本地读盘，后者就是真的没有。
    """
    try:
        data = agent.call("catalog.skills_detail", timeout=30.0)
    except Exception:
        return None
    if isinstance(data, dict):
        items = data.get("skills")
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]
    return None


# ---------- actions ----------
def list_skills(agent: AgentLink) -> dict:
    remote = _remote_skills(agent)
    if remote is not None:
        return {"skills": remote}
    # 内核问不到 —— 退回本地读盘。宁可少一层过滤，
    # 也不能在界面编辑技能时整页空白。
    return {
        "skills": [
            {"name": s.name, "description": s.description, "path": str(s.path)}
            for s in _library().skills.values()
        ]
    }


def read_skill(agent: AgentLink, name: str) -> dict:
    """读技能正文。

    先问内核「有没有这个技能」—— 停用/依赖不满足的技能**读不到**，
    与模型那边看到的是同一份清单（两边不一致才是真麻烦）。
    """
    s = _library().get(name)
    if s is None:
        raise HTTPException(404, "没有这个技能")
    remote = _remote_skills(agent)
    if remote is not None:
        live = {x.get("name") for x in remote}
        if name not in live:
            raise HTTPException(404, "没有这个技能")
    return {"name": s.name, "description": s.description, "body": s.body}


def create_skill(agent: AgentLink, req: dict) -> dict:
    name = str(req.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "name 不能为空")
    if _library().get(name) is not None:
        raise HTTPException(409, "已存在同名技能")
    _write_skill_md(
        _skill_dir(name) / "SKILL.md",
        name,
        str(req.get("description", "")).strip(),
        str(req.get("body", "")),
    )
    _reload(agent)
    return {"created": name}


def update_skill(agent: AgentLink, name: str, req: dict) -> dict:
    s = _library().get(name)
    if s is None:
        raise HTTPException(404, "没有这个技能")
    _write_skill_md(
        s.path,
        name,
        str(req.get("description", s.description)).strip(),
        str(req.get("body", s.body)),
    )
    _reload(agent)
    return {"updated": name}


def delete_skill(agent: AgentLink, name: str) -> dict:
    s = _library().get(name)
    if s is None:
        raise HTTPException(404, "没有这个技能")
    shutil.rmtree(s.path.parent, ignore_errors=True)
    _reload(agent)
    return {"deleted": name}


# ---------- routes ----------
def register(app, agent: AgentLink) -> None:
    from fastapi.responses import JSONResponse

    @router.get("")
    async def _list():
        return JSONResponse(list_skills(agent))

    @router.get("/{name}")
    async def _detail(name: str):
        return JSONResponse(read_skill(agent, name))

    @router.post("")
    async def _create(req: dict):
        return JSONResponse(create_skill(agent, req))

    @router.put("/{name}")
    async def _update(name: str, req: dict):
        return JSONResponse(update_skill(agent, name, req))

    @router.delete("/{name}")
    async def _delete(name: str):
        return JSONResponse(delete_skill(agent, name))

    app.include_router(router)
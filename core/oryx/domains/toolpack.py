"""工具包（自定义工具集）域。

**为什么要有**
------------
「工具」页签里原先只有**内置**的工具集 —— 浏览器操作、系统设置、文件读写…
用户能用它们，但不能定义自己的。

而「一个工具包」到底是什么，一直没有地方说清楚：
它是**一组工具 + 一句人话**（这组工具能干什么、什么时候该用）。
模型选工具靠的就是这句话，光有一列工具名是不够的。

所以这里让用户能自己定义工具包：

    名字（name）         —— 叫什么
    描述（description）  —— 一句话说明，模型据此判断要不要用
    功能（function）     —— 具体能干什么、怎么用（给人看的详细说明）
    工具（tools）        —— 这个包由哪些工具组成

**存在哪**
--------
配置里（`config.data["tool_packs"]`），跟着应用数据走。
为什么不落成磁盘上的插件包：那些是**带实现**的扩展（要写代码），
而这里的工具包只是**对现有工具的分组与说明** —— 放配置里合适，
也不会因为多了一个包就要重扫一遍插件目录。

对外四条：

    GET    /api/tool-packs          列出来
    POST   /api/tool-packs          新建
    PUT    /api/tool-packs/{id}     改
    DELETE /api/tool-packs/{id}     删
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..api.response import ok

router = APIRouter(prefix="/api/tool-packs", tags=["工具包"])

# 工具包 id 的合法字符。
# 为什么只允许这些：id 会进 URL、也当文件名用（导出插件包时），
# 带空格/斜杠/中文标点会到处惹麻烦。
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

MAX_NAME = 40
MAX_DESC = 200
MAX_FUNC = 2000


class ToolPack(BaseModel):
    """一个工具包。"""

    id: str = Field(default="", max_length=64)
    name: str = Field(default="", max_length=MAX_NAME)
    description: str = Field(default="", max_length=MAX_DESC)
    function: str = Field(default="", max_length=MAX_FUNC)
    tools: list[str] = Field(default_factory=list)


def _slug(name: str) -> str:
    """从名字生成一个合法的 id。

    中文名也要能用 —— 用户大概率会直接写中文标题。
    拼音化太重，这里退一步：非 ASCII 字符一律换成短横线。

    **全是中文时用名字的哈希兜底** ——
    否则每个中文名都会退化成同一个空串（"pack"），
    第二个中文名的工具包就会撞 id、只能报「已经有叫 pack 的了」。
    """
    s = unicodedata.normalize("NFKC", name or "").strip().lower()
    out = []
    for ch in s:
        if ch.isalnum() and ch.isascii():
            out.append(ch)
        elif ch in "-_":
            out.append(ch)
        else:
            out.append("-")
    s = re.sub(r"-{2,}", "-", "".join(out)).strip("-")
    if not s:
        digest = hashlib.sha1((name or "").encode("utf-8")).hexdigest()[:8]
        s = f"pack-{digest}"
    return s[:64]


def _packs(config) -> dict:
    raw = config.data.get("tool_packs")
    return raw if isinstance(raw, dict) else {}


def list_packs(config) -> list[dict]:
    """列出全部工具包（按名字排）。"""
    items = []
    for pid, spec in _packs(config).items():
        if not isinstance(spec, dict):
            continue
        items.append({
            "id": pid,
            "name": str(spec.get("name") or pid),
            "description": str(spec.get("description") or ""),
            "function": str(spec.get("function") or ""),
            "tools": [str(t) for t in (spec.get("tools") or [])],
        })
    items.sort(key=lambda x: (x["name"], x["id"]))
    return items


def _validate(config, pack: ToolPack, pack_id: str | None = None) -> dict:
    """校验并规范化一个工具包。返回落盘用的 dict。"""
    name = (pack.name or "").strip()
    if not name:
        raise HTTPException(400, "工具包要有名字")
    if len(name) > MAX_NAME:
        raise HTTPException(400, f"名字太长了（最多 {MAX_NAME} 字）")

    pid = (pack.id or "").strip() or _slug(name)
    if not _ID_RE.match(pid):
        raise HTTPException(400, "id 只能是小写字母、数字、下划线和短横线")
    if pack_id and pid != pack_id:
        raise HTTPException(400, "id 改了 —— 改名请新建一个包再删旧的")

    existing = _packs(config)
    if pack_id is None and pid in existing:
        raise HTTPException(409, f"已经有叫 {pid} 的工具包了")

    tools = []
    for t in pack.tools or []:
        t = str(t).strip()
        if t and t not in tools:
            tools.append(t)
    if not tools:
        raise HTTPException(400, "一个工具包至少要有一个工具")

    return {
        "id": pid,
        "name": name,
        "description": (pack.description or "").strip()[:MAX_DESC],
        "function": (pack.function or "").strip()[:MAX_FUNC],
        "tools": tools,
    }


def create_pack(config, pack: ToolPack) -> dict:
    data = _validate(config, pack)
    packs = _packs(config)
    packs[data["id"]] = {k: v for k, v in data.items() if k != "id"}
    config.data["tool_packs"] = packs
    config.save()
    return data


def update_pack(config, pack_id: str, pack: ToolPack) -> dict:
    if pack_id not in _packs(config):
        raise HTTPException(404, "没有这个工具包")
    data = _validate(config, pack, pack_id=pack_id)
    packs = _packs(config)
    packs[pack_id] = {k: v for k, v in data.items() if k != "id"}
    config.data["tool_packs"] = packs
    config.save()
    return data


def delete_pack(config, pack_id: str) -> dict:
    packs = _packs(config)
    if pack_id not in packs:
        raise HTTPException(404, "没有这个工具包")
    packs.pop(pack_id, None)
    config.data["tool_packs"] = packs
    config.save()
    return {"deleted": pack_id}


# ── 插件包（导入/导出） ────────────────────────────────────────────
#
# **为什么要有**
# ------------
# DeepSeek Harness（DSH）的核心主张是「一切皆插件」——
# 工具、技能、界面、MCP 连接，全都是同一套插件机制，没有第二种清单格式。
#
# Oryx 已经有这套机制（`oryx.json` 清单 + `plugins/` 目录），
# 但**工具包**此前只是配置里的一条记录 —— 没法分享、没法在另一台机器上装。
#
# 所以这里让工具包能导出/导入成**插件清单**：
# 格式跟扩展系统完全一致（`oryx.json`），
# 一个包既能在这儿用，也能直接丢进 `plugins/` 目录被注册表发现。
#
# **字段对应**
#   displayName ← name
#   description ← description
#   function    ← function（DSH 没有这一栏，这是工具包特有的：
#                 「这组工具具体能干什么」是给人看的详细说明）
#   tools[]     ← 每个工具的 name（+ 从核心工具表查到的 description）

API_VERSION = "oryx/v1"
PACK_VERSION = "1.0.0"


def export_pack(config, pack_id: str) -> dict:
    """导出一个工具包 —— 返回插件清单（oryx.json 的形状）。"""
    spec = _packs(config).get(pack_id)
    if not isinstance(spec, dict):
        raise HTTPException(404, "没有这个工具包")
    return {
        "apiVersion": API_VERSION,
        "kind": "tool",
        "id": pack_id,
        "version": PACK_VERSION,
        "displayName": str(spec.get("name") or pack_id),
        "description": str(spec.get("description") or ""),
        "function": str(spec.get("function") or ""),
        "tools": [
            {"name": str(t), "description": ""}
            for t in (spec.get("tools") or [])
        ],
    }


def import_pack(config, manifest: dict) -> dict:
    """从插件清单导入一个工具包。

    清单既可能是本域导出的（带 function），也可能是扩展系统里的
    普通 `kind: "tool"` 清单 —— 那时没有 function 栏，功能说明留空。
    """
    if not isinstance(manifest, dict):
        raise HTTPException(400, "清单得是一个 JSON 对象")
    if manifest.get("kind") not in ("tool", "toolpack", None, ""):
        raise HTTPException(400, f"这是 kind={manifest.get('kind')} 的插件，不是工具包")
    name = str(manifest.get("displayName") or manifest.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "清单里没有名字（displayName）")
    tools = []
    for t in (manifest.get("tools") or []):
        # 清单里的工具项可能是字符串，也可能是 {name, description}
        if isinstance(t, str):
            tools.append(t)
        elif isinstance(t, dict) and t.get("name"):
            tools.append(str(t["name"]))
    if not tools:
        raise HTTPException(400, "清单里一个工具都没有")
    pack = ToolPack(
        id=str(manifest.get("id") or ""),
        name=name,
        description=str(manifest.get("description") or ""),
        function=str(manifest.get("function") or ""),
        tools=tools,
    )
    return create_pack(config, pack)


def register(app, config) -> None:
    """把本域路由挂到 app。"""

    @router.get("")
    async def _list():
        return ok({"packs": list_packs(config)})

    @router.post("")
    async def _create(req: ToolPack):
        return ok(create_pack(config, req))

    @router.put("/{pack_id}")
    async def _update(pack_id: str, req: ToolPack):
        return ok(update_pack(config, pack_id, req))

    @router.delete("/{pack_id}")
    async def _delete(pack_id: str):
        return ok(delete_pack(config, pack_id))

    @router.get("/{pack_id}/export")
    async def _export(pack_id: str):
        """导出成插件清单（oryx.json 的形状）。"""
        return ok(export_pack(config, pack_id))

    @router.post("/import")
    async def _import(req: dict):
        """从插件清单导入。"""
        return ok(import_pack(config, req))

    app.include_router(router)

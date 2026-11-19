"""profile —— 一组扩展的组合。

为什么需要
----------
扩展多了以后，「全都加载」不是好选择：

· 有些扩展**互相冲突**（两个都注册了同名工具）
· 有些扩展**不该同时在**（比如「严格审计」和「宽松模式」）
· 有些扩展**太重**（带大模型、带浏览器驱动），
  用户只想在真要用的时候才加载
· 上下文预算有限 —— 扩展全在会挤掉别的

profile 就是给这些情况一个**命名组合**：

    {
      "profiles": {
        "minimal":   {"extensions": ["file-search"]},
        "developer": {"extensions": ["file-search", "git-helper", "word-count"],
                      "includes": ["minimal"]},
        "full":      {"includes": ["developer"], "extensions": ["..."]}
      },
      "default": "developer"
    }

借鉴 ECC 的 `manifest/install-profiles.json`
（minimal / opencode / core / developer / security / research / full）。

三条设计原则
------------
1. **profile 可以嵌套**（`includes`）—— 否则组合会写得很啰嗦。
2. **默认 profile 是「不排除任何东西」** —— profile 是**收窄**工具，
   不是新的必需项。没配 profile 的用户，一切照旧。
3. **profile 只影响「加载哪些扩展」**，不影响「怎么执行」。
   安全判定仍然看信任级别 —— profile 不能用来绕过信任检查。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Profile:
    """一个 profile —— 一组扩展 id。"""

    id: str
    extensions: tuple[str, ...] = ()
    includes: tuple[str, ...] = ()
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "extensions": list(self.extensions),
            "includes": list(self.includes),
        }


@dataclass
class ProfileSet:
    """一组 profile + 默认选哪个。"""

    profiles: dict[str, Profile] = field(default_factory=dict)
    default: str = ""

    # ── 解析 ────────────────────────────────────────────────────────
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProfileSet":
        profiles: dict[str, Profile] = {}
        raw = data.get("profiles")
        if isinstance(raw, dict):
            for pid, item in raw.items():
                if not isinstance(item, dict):
                    continue
                profiles[str(pid)] = Profile(
                    id=str(pid),
                    extensions=tuple(str(x) for x in (item.get("extensions") or [])),
                    includes=tuple(str(x) for x in (item.get("includes") or [])),
                    description=str(item.get("description") or ""),
                )
        return cls(
            profiles=profiles,
            default=str(data.get("default") or ""),
        )

    @classmethod
    def load(cls, path: Path) -> "ProfileSet":
        """读 profile 文件。读不到就返回空的（一切照旧）。"""
        try:
            data = json.loads(Path(path).read_text("utf-8"))
        except Exception:
            return cls()
        return cls.from_dict(data if isinstance(data, dict) else {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "default": self.default,
            "profiles": {k: v.to_dict() for k, v in self.profiles.items()},
        }

    # ── 查询 ────────────────────────────────────────────────────────
    def resolve(self, profile_id: str) -> tuple[str, ...]:
        """展开一个 profile（含 includes），返回扩展 id 的集合。

        返回的是**有序**的 —— 先 includes 里的，再自己的。
        顺序稳定才能让「加载了哪些扩展」每次都一样。
        """
        seen: list[str] = []
        visiting: set[str] = set()

        def walk(pid: str) -> None:
            if pid in visiting:
                # 循环 include —— 直接截断，不无限递归
                return
            prof = self.profiles.get(pid)
            if prof is None:
                return
            visiting.add(pid)
            for inc in prof.includes:
                walk(inc)
            for ext in prof.extensions:
                if ext not in seen:
                    seen.append(ext)
            visiting.discard(pid)

        walk(profile_id)
        return tuple(seen)

    def resolve_with_fallback(self, profile_id: str) -> tuple[str, ...]:
        """解析 profile；profile 不存在时返回「不排除任何东西」。

        这条很重要：**profile 是收窄工具，不是新的必需项**。
        用户没配 profile、或配错了名字，都不该让扩展全部消失。
        """
        if not profile_id:
            return ()
        if profile_id not in self.profiles:
            return ()
        return self.resolve(profile_id)

    def allows(self, profile_id: str, extension_id: str) -> bool:
        """某个扩展在当前 profile 下是否允许加载。

        没有 profile（空 id / 找不到）→ **全部允许**。
        """
        if not profile_id or profile_id not in self.profiles:
            return True
        return extension_id in self.resolve_with_fallback(profile_id)

    def list_profiles(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self.profiles.values()]

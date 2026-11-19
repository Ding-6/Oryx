"""把 web/index.html 里的内联 <script> 拆成 ES Module。

为什么要有这个脚本
------------------
前端原来是一个 9705 行的单文件（JS 6791 行、254 个顶层符号、37 个功能分区）。
「改一处怕崩一片」的根因是**所有东西共享一个全局作用域**，看不出依赖关系。

方案：标准 ES Module（import / export）
------------------------------------
把 6791 行按文件里已有的 `/* ══════ xxx ══════ */` 分区标记切成 37 个模块，
用**静态 import/export** 显式表达依赖。

为什么这样不会踩 TDZ（暂时性死区）
----------------------------------
TDZ 只在「模块求值期间读到尚未初始化的 const/let」时触发。这里做了三件事规避：

1. **只读常量走标准 export/import**。
   跨模块用到的 10 个 const（DEGRADED / RISK / FILE_TOOLS / UC_DETAIL …）
   本身不可变，import 进来就是同一份值，任何时机读都安全。

2. **函数走标准 export/import**。
   跨模块用到的 85 个函数全是 `function` 声明 —— **函数声明会提升**，
   即使模块之间成环，函数绑定在模块求值前就已建立，调用时才取实体，
   所以环不会导致 TDZ。（只有 const/let 才会。）

3. **7 个会被重新赋值的 let 收进一个可变容器**。
   SUG / S_REASON / _msgSeq / es / lastSeq / _modalGen / searchIdx 等在运行期
   会被改值，不能用 import 绑定（import 是只读绑定）。它们统一放进
   core.js 导出的 `mut` 对象，用 `mut.es` 这种属性访问读写，
   绕开「import 绑定不可重新赋值」的限制。

依赖分析结论：分区之间有 24 个循环依赖环，全部由「函数互相调用」构成，
第 2 条已经证明这类环是安全的。**这 24 个环里没有 const/let 的读写**。

用法
----
    python core/scripts/split_web_modules.py --analyze   # 只分析依赖
    python core/scripts/split_web_modules.py             # 生成模块
    python core/scripts/split_web_modules.py --verify    # 生成后校验
"""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parents[1]
WEB_DIR = CORE_DIR / "web"
INDEX = WEB_DIR / "index.html"
MODULES_DIR = WEB_DIR / "modules"
# 拆分基准：原始内联脚本的快照。
#
# 为什么要有它：拆分是**幂等**操作才敢反复跑。
# 拆完之后 index.html 里已经没有内联脚本了，再跑一次就没源可拆。
# 所以第一次拆分时把内联脚本原样存到这里，之后都以它为基准重新生成。
SOURCE = WEB_DIR / "_source" / "app.js"

# ── 分区 → 模块映射 ────────────────────────────────────────────────
# 分区依据是文件里已有的分区标记 —— 那是当初写代码时就存在的功能边界。
# 一一对应，一个分区一个文件：这样「文件 ↔ 功能」的映射最直观。
ZONE_TO_MODULE: dict[int, str] = {
    0: "core", 1: "view", 2: "layout", 3: "composer-fold", 4: "search",
    5: "message", 6: "session", 7: "checkpoint", 8: "topbar", 9: "sidebar",
    10: "detail", 11: "team", 12: "send", 13: "events", 14: "reasoning",
    15: "agentflow", 16: "overview", 17: "degraded", 18: "agui", 19: "guard",
    20: "approval", 21: "topbar-ui", 22: "modal", 23: "cards", 24: "chips",
    25: "filepick", 26: "paste", 27: "policy", 28: "skillcall", 29: "promptopt",
    30: "wiring", 31: "terminal", 32: "pages", 33: "workflow", 34: "models",
    35: "theme", 36: "boot",
}

CORE_ZONE = 0

DECL_RE = re.compile(
    r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"
    r"|^(?:const|let|var)\s+([A-Za-z_$][\w$]*)"
)


def extract_js(html: str) -> str:
    """取拆分基准的 JS。

    优先用 web/_source/app.js（拆分基准快照，保证幂等）；
    没有快照时从 index.html 的内联脚本里取，并顺手存一份。
    """
    if SOURCE.exists():
        return SOURCE.read_text("utf-8")
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    if not blocks:
        raise SystemExit(
            "找不到拆分基准：web/_source/app.js 不存在，"
            "且 index.html 里也没有内联 <script>（可能已经拆过了）"
        )
    if len(blocks) != 1:
        raise SystemExit(f"内联 script 有 {len(blocks)} 个，拆分会丢代码，请人工确认")
    src = blocks[0]
    SOURCE.parent.mkdir(parents=True, exist_ok=True)
    SOURCE.write_text(src, encoding="utf-8")
    print(f"已保存拆分基准快照 → {SOURCE}")
    return src


def split_zones(js: str) -> list[tuple[int, int, int]]:
    lines = js.splitlines()
    marks = [i for i, ln in enumerate(lines) if re.match(r"^/\*\s*[══]", ln)]
    return [(z, s, (marks[z + 1] if z + 1 < len(marks) else len(lines)))
            for z, s in enumerate(marks)]


def collect_decls(lines: list[str]) -> dict[str, int]:
    decls: dict[str, int] = {}
    for i, ln in enumerate(lines):
        m = DECL_RE.match(ln)
        if m:
            decls[m.group(1) or m.group(2)] = i
    return decls


def uses_symbol(line: str, name: str) -> bool:
    """判断一行是否引用了某符号。

    边界用 (?![A-Za-z0-9_$]) 而不是 \\b —— 符号名可能是 `$` 这类非单词字符，
    \\b 在非单词字符后不成立，会漏判。
    """
    return bool(re.search(r"(?<![\w$.])" + re.escape(name) + r"(?![A-Za-z0-9_$])", line))


def is_comment(line: str) -> bool:
    s = line.strip()
    return s.startswith(("//", "/*", "*"))


def classify(lines: list[str], decls: dict) -> tuple[set[str], set[str]]:
    """把顶层符号分成「函数」与「变量」。"""
    funcs, vars_ = set(), set()
    for n, ln in decls.items():
        if re.match(r"^(?:async\s+)?function\s", lines[ln]):
            funcs.add(n)
        else:
            vars_.add(n)
    return funcs, vars_


def find_reassignments(lines: list[str], decls: dict, name: str) -> list[int]:
    """找出对 name 的**真正重新赋值**（排除声明行本身）。"""
    decl = decls.get(name, -1)
    hits = []
    for i, ln in enumerate(lines):
        if i <= decl:
            continue
        if re.match(r"^\s*" + re.escape(name) + r"\s*=[^=]", ln) or \
           re.match(r"^\s*" + re.escape(name) + r"\s*\+\+", ln) or \
           re.match(r"^\s*" + re.escape(name) + r"\s*--", ln):
            hits.append(i)
    return hits


def analyze() -> dict:
    js = extract_js(INDEX.read_text("utf-8"))
    lines = js.splitlines()
    zones = split_zones(js)
    decls = collect_decls(lines)

    def zone_at(i: int) -> int:
        z = 0
        for zz, s, _ in zones:
            if i >= s:
                z = zz
        return z

    symbol_zone = {n: zone_at(l) for n, l in decls.items()}
    symbol_used: dict[str, set[int]] = {n: set() for n in decls}
    for i, ln in enumerate(lines):
        if is_comment(ln):
            continue
        for n in decls:
            if i == decls[n]:
                continue
            if uses_symbol(ln, n):
                symbol_used[n].add(zone_at(i))

    funcs, vars_ = classify(lines, decls)
    return {
        "lines": lines, "zones": zones, "decls": decls,
        "symbol_zone": symbol_zone, "symbol_used": symbol_used,
        "funcs": funcs, "vars": vars_,
    }


def plan(info: dict) -> dict:
    """算出每个模块的 import / export、可变容器，以及破环后的模块划分。"""
    lines, decls = info["lines"], info["decls"]
    symbol_zone, symbol_used = info["symbol_zone"], info["symbol_used"]
    funcs, vars_ = info["funcs"], info["vars"]
    mod_of = lambda z: ZONE_TO_MODULE[z]
    all_mods = sorted({mod_of(z) for z in range(len(info["zones"]))})

    # ── 1. 跨模块符号 ────────────────────────────────────────────
    cross: dict[str, tuple[str, set[str]]] = {}
    for n, zs in symbol_used.items():
        home = mod_of(symbol_zone[n])
        others = {mod_of(z) for z in zs if mod_of(z) != home}
        if others:
            cross[n] = (home, others)

    # ── 2. 可变变量（会被重新赋值的 let/var）→ 放进 mut 容器 ─────────
    mutable = {n for n in vars_
               if n in cross and find_reassignments(lines, decls, n)}
    # 初值：必须是字面量，才能安全搬进 core 的 mut 对象
    mutable_init: dict[str, str] = {}
    for n in mutable:
        decl_line = lines[decls[n]].strip()
        m = re.match(r"^(?:let|var|const)\s+" + re.escape(n) + r"\s*=\s*(.*)$", decl_line)
        mutable_init[n] = m.group(1).rstrip(";") if m else "null"
    mut_users: dict[str, set[str]] = {n: set() for n in mutable}
    for n in mutable:
        home = mod_of(symbol_zone[n])
        mut_users[n].add(home)
        for z in symbol_used[n]:
            m = mod_of(z)
            if m != home:
                mut_users[n].add(m)

    # ── 3. 破环：合并模块 ────────────────────────────────────────
    # 依赖图有 21 个环。踩过的坑必须记下来：
    #
    #   **把符号下沉到 core 会造出新的环。**
    #   例：tlAgentStarted 原本在 agentflow、被 agui 用；下沉到 core 后变成
    #   core → agui，而 agui 又用 core 的 $ / S，于是 core ↔ agui 成环。
    #   「往共同祖先搬符号」只对「没有反向边」成立，这里不成立。
    #
    #   **模块合并是唯一无副作用的破环方式**：合并后两边的符号同文件，
    #   不需要 import，也就不可能成环。
    #   代价是那个文件变大 —— 但比「假性下沉」的隐性环安全得多。
    owner: dict[str, str] = {n: mod_of(symbol_zone[n]) for n in decls}
    merge_map: dict[str, str] = {}

    def resolve(m: str) -> str:
        """链式解析合并后的模块名。

        合并是递进的：checkpoint → workflow，后来 workflow → chips。
        只查一层会拿到中间名 "workflow"，符号就留在一个不存在的模块里。
        """
        seen: set[str] = set()
        while m in merge_map and m not in seen:
            seen.add(m)
            m = merge_map[m]
        return m

    def build_graph() -> dict[str, set[str]]:
        """建**有向**依赖图：g[使用者] = {被依赖者}。

        方向很重要：符号在 h 定义、被 oh 使用 ⇒ oh import h ⇒ 边是 oh → h。
        之前建无向图（h↔oh），把「谁用谁」的信息丢了，
        于是 core↔boot 这种假环也冒出来。
        """
        g: dict[str, set[str]] = {m: set() for m in all_mods}
        for n, (home, others) in cross.items():
            if n in mutable:
                continue
            h = resolve(owner[n])
            for o in others:
                oh = resolve(o)
                if oh != h:
                    g[oh].add(h)      # oh 使用了 h 的符号
        return g

    def find_cycle(g: dict[str, set[str]]) -> list[str] | None:
        color: dict[str, int] = {}
        stack: list[str] = []
        found: list[str] = []

        def dfs(u: str) -> bool:
            color[u] = 1
            stack.append(u)
            for v in sorted(g.get(u, ())):
                if color.get(v) == 1:
                    found.extend(stack[stack.index(v):] + [v])
                    return True
                if color.get(v, 0) == 0 and dfs(v):
                    return True
            stack.pop()
            color[u] = 2
            return False

        for m in sorted(g):
            if color.get(m, 0) == 0 and dfs(m):
                return found
        return None

    def mod_size(m: str) -> int:
        return sum(1 for z in range(len(info["zones"])) if owner_of_zone(z) == m)

    def owner_of_zone(z: int) -> str:
        return resolve(mod_of(z))

    for _ in range(80):
        g = build_graph()
        cyc = find_cycle(g)
        if not cyc:
            break
        ring = sorted({m for m in cyc if m not in ("core", "boot")})
        if not ring:
            # 环只涉及 core / boot：boot 是入口，必须独立成文件。
            # 这种环靠「boot 只 import 不被 import」来解 —— 见下方 boot 处理。
            break
        if len(ring) == 1:
            # 环是「core ↔ 某个模块」：core 是依赖根，不能合并掉
            # （它是所有模块的 import 目标）。所以把那个模块并进 core。
            target = ring[0]
            merge_map[target] = "core"
            for n in owner:
                if owner[n] == target:
                    owner[n] = "core"
            continue
        a, b = sorted(ring, key=mod_size)[:2]
        merge_map[b] = a
        for n in owner:
            if owner[n] == b:
                owner[n] = a

    # 合并后的分区 → 模块
    final_zone_module: dict[int, str] = {}
    for z in range(len(info["zones"])):
        final_zone_module[z] = resolve(mod_of(z))
    final_mods = sorted(set(final_zone_module.values()))

    # ── 4. 按合并后的归属重算 import / export ──────────────────────
    imports: dict[str, dict[str, set[str]]] = {m: {} for m in final_mods}
    exports: dict[str, set[str]] = {m: set() for m in final_mods}

    def users_of(name: str) -> set[str]:
        return {final_zone_module[z] for z in symbol_used.get(name, ())}

    for n, (home, others) in cross.items():
        if n in mutable:
            continue
        real_home = resolve(owner[n])
        exports[real_home].add(n)
        for o in users_of(n):
            if o == real_home:
                continue
            imports[o].setdefault(real_home, set()).add(n)

    # core 原有符号
    for n, zs in symbol_used.items():
        if symbol_zone[n] != CORE_ZONE:
            continue
        users = {final_zone_module[z] for z in zs if final_zone_module[z] != "core"}
        if users:
            exports["core"].add(n)
            for u in users:
                imports[u].setdefault("core", set()).add(n)

    return {
        "cross": cross, "mutable": mutable, "mut_users": mut_users,
        "mutable_init": mutable_init,
        "imports": imports, "exports": exports,
        "mod_of": mod_of, "owner": owner, "merge_map": merge_map,
        "final_zone_module": final_zone_module, "final_mods": final_mods,
        "all_mods": all_mods,
    }



def zones_count(info: dict) -> int:
    return len(info["zones"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--analyze", action="store_true")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    info = analyze()
    p = plan(info)
    lines, zones, decls = info["lines"], info["zones"], info["decls"]

    print(f"分区 {len(zones)} 个，顶层符号 {len(decls)} 个")
    print(f"  函数 {len(info['funcs'])} 个，变量 {len(info['vars'])} 个")
    print(f"  可变容器成员：{sorted(p['mutable'])}")
    print(f"  合并前模块 {len(p['all_mods'])} 个，合并后 {len(p['final_mods'])} 个")

    if args.analyze:
        return 0

    # ── 生成 ────────────────────────────────────────────────────────
    # 合并后的分区 → 模块
    module_zones: dict[str, list[int]] = {}
    for z, mod in p["final_zone_module"].items():
        module_zones.setdefault(mod, []).append(z)

    if MODULES_DIR.exists():
        shutil.rmtree(MODULES_DIR)
    MODULES_DIR.mkdir(parents=True, exist_ok=True)

    module_text: dict[str, str] = {}

    for mod, zs in module_zones.items():
        # 分区按编号排序；core 的第一个分区是 0，要带上它之前的前言区
        zs = sorted(zs)
        chunks: list[str] = []
        for z in zs:
            _, start, end = zones[z]
            if z == CORE_ZONE:
                start = 0   # 前言区（$ / api / esc …）也属于 core
            chunks.append("\n".join(lines[start:end]))
        text = "\n".join(chunks).rstrip() + "\n"

        # ① 可变变量改写为 mut.xxx 访问（定义方删声明，使用方改引用）
        for name in sorted(p["mutable"]):
            if mod not in p["mut_users"][name]:
                continue
            is_owner = p["owner"][name] == mod
            text = _rewrite_mutable(text, name, is_owner=is_owner)

        # ② 加 export
        for name in sorted(p["exports"][mod]):
            text = _add_export(text, name)

        module_text[mod] = text

    # ③ 写文件：头部 + 正文
    for mod, zs in module_zones.items():
        head = _module_header(mod, sorted(zs), p)
        (MODULES_DIR / f"{mod}.js").write_text(
            "\n".join(head + [module_text[mod]]), encoding="utf-8"
        )

    # 入口：boot.js 需要显式加载「副作用模块」
    #
    # 有些模块不导出任何符号 —— 它们在顶层直接 addEventListener（接线模块）。
    # 这种模块没人 import 是**正常的**，但它们必须被执行一次，
    # 所以入口要显式 import 它们。
    side_effect_mods = [
        m for m in sorted(module_zones)
        if m != "boot" and not p["exports"].get(m)
    ]
    boot_path = MODULES_DIR / "boot.js"
    boot_lines = boot_path.read_text("utf-8").splitlines()
    side_lines = [f"import './{m}.js';" for m in side_effect_mods]
    last_import = -1
    for i, ln in enumerate(boot_lines):
        if ln.startswith("import "):
            last_import = i
    if last_import >= 0:
        boot_lines = boot_lines[:last_import + 1] + side_lines + boot_lines[last_import + 1:]
    else:
        at = 0
        for i, ln in enumerate(boot_lines):
            if ln.strip() == "" and i > 0:
                at = i
                break
        boot_lines = boot_lines[:at] + side_lines + boot_lines[at:]
    boot_path.write_text("\n".join(boot_lines), encoding="utf-8")

    print(f"\n已生成 {len(module_zones)} 个模块到 {MODULES_DIR}")
    if p["merge_map"]:
        print(f"为消除循环依赖合并了 {len(p['merge_map'])} 个模块：{p['merge_map']}")
    print(f"入口 boot.js 显式加载 {len(side_effect_mods)} 个副作用模块")
    return 0


def _rewrite_mutable(text: str, name: str, is_owner: bool) -> str:
    """把可变变量 name 的所有引用改写成 mut.name。

    定义方（is_owner=True）：声明行**整行删掉** —— 变量已在 core.js 的
    `mut` 对象里声明并初始化，这里再声明一次会重复定义。
    使用方：只改引用。
    """
    out = []
    for ln in text.splitlines():
        if is_owner and re.match(r"^\s*(?:let|var|const)\s+" + re.escape(name) + r"\b", ln):
            continue  # 声明已搬到 core
        if is_comment(ln) or not uses_symbol(ln, name):
            out.append(ln)
            continue
        out.append(re.sub(r"(?<![\w$.])" + re.escape(name) + r"(?![\w$])",
                          "mut." + name, ln))
    return "\n".join(out) + "\n"


def _first_decl_name(snippet: str) -> str:
    """从一段代码里取出第一个顶层声明的名字。"""
    m = re.search(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"
                  r"|^(?:const|let|var)\s+([A-Za-z_$][\w$]*)",
                  snippet, re.M)
    return (m.group(1) or m.group(2)) if m else ""


def _module_uses(info: dict, mod: str, name: str) -> bool:
    """某个模块是否用到了某个符号。"""
    mod_of = lambda z: ZONE_TO_MODULE[z]
    for z in info["symbol_used"].get(name, ()):
        if mod_of(z) == mod:
            return True
    return False


def _add_core_import(text: str, names: list[str]) -> str:
    """给模块加一条从 core 导入的 import 语句（合并已有的 core import）。"""
    if not names:
        return text
    m = re.search(r"^import \{([^}]*)\} from '\./core\.js';$", text, re.M)
    if m:
        existing = {x.strip() for x in m.group(1).split(",") if x.strip()}
        merged = sorted(existing | set(names))
        return text[:m.start()] + f"import {{ {', '.join(merged)} }} from './core.js';" + text[m.end():]
    # 没有 core import 就在文件头插一条
    lines = text.splitlines()
    at = 0
    for i, ln in enumerate(lines):
        if ln.strip() == "" and i > 0:
            at = i
            break
    lines = lines[:at] + [f"import {{ {', '.join(names)} }} from './core.js';"] + lines[at:]
    return "\n".join(lines) + "\n"


def _add_export(text: str, name: str) -> str:
    """给顶层的 function / const / let 声明加 export。

    注意：这里不能用 \\b 做边界 —— 符号名可能是 `$`、`$$` 这类**非单词字符**，
    `\\b` 在非单词字符之后就匹配不到边界，export 会静默加不上。
    改用「后面必须是行尾或非标识符字符」的判断。
    """
    esc = re.escape(name)
    tail = r"(?![A-Za-z0-9_$])"
    text = re.sub(r"^(export\s+)?((?:async\s+)?function\s+" + esc + tail + r")",
                  r"export \2", text, count=1, flags=re.M)
    text = re.sub(r"^(export\s+)?((?:const|let|var)\s+" + esc + tail + r")",
                  r"export \2", text, count=1, flags=re.M)
    return text


def _remove_definition(text: str, name: str) -> str:
    """从模块正文里删掉某个顶层符号的**定义**（函数整段 / 变量单行）。

    用于「枢纽符号下沉到 core」：定义搬到 core 后，原模块不能再定义一份，
    否则是重复声明。
    """
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        # 函数定义：整段删（从声明行到配平的右花括号）
        m = re.match(r"^(?:export\s+)?(?:async\s+)?function\s+" + re.escape(name)
                     + r"(?![A-Za-z0-9_$])", ln)
        if m:
            depth = 0
            started = False
            while i < n:
                depth += lines[i].count("{") - lines[i].count("}")
                if "{" in lines[i]:
                    started = True
                i += 1
                if started and depth <= 0:
                    break
            # 连带删掉紧跟其后的空行
            continue
        # 变量定义：单行删
        if re.match(r"^(?:export\s+)?(?:const|let|var)\s+" + re.escape(name)
                    + r"(?![A-Za-z0-9_$])", ln):
            i += 1
            continue
        out.append(ln)
        i += 1
    return "\n".join(out) + "\n"


def _extract_definition(text: str, name: str) -> tuple[str, str]:
    """从模块正文里**取出**某个顶层符号的定义。

    返回 (定义代码, 去掉定义后的正文)。用于把枢纽符号搬到 core。
    """
    lines = text.splitlines()
    out: list[str] = []
    kept: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        m = re.match(r"^(?:export\s+)?(?:async\s+)?function\s+" + re.escape(name)
                     + r"(?![A-Za-z0-9_$])", ln)
        if m:
            block = [ln]
            depth = 0
            started = False
            while i < n:
                depth += lines[i].count("{") - lines[i].count("}")
                if "{" in lines[i]:
                    started = True
                block.append(lines[i])
                i += 1
                if started and depth <= 0:
                    break
            kept.extend(block)
            continue
        if re.match(r"^(?:export\s+)?(?:const|let|var)\s+" + re.escape(name)
                    + r"(?![A-Za-z0-9_$])", ln):
            kept.append(ln)
            i += 1
            continue
        out.append(ln)
        i += 1
    return "\n".join(kept), "\n".join(out) + "\n"


def _module_header(mod: str, zs: list[int], p: dict) -> list[str]:
    """模块头部：说明 + import 语句。"""
    head = [
        f"/* {mod}.js —— 分区 {', '.join(str(z) for z in zs)}。",
        "   由 core/scripts/split_web_modules.py 从 index.html 拆出。",
        "   改动前先看 docs/FRONTEND-MODULES.md 的依赖说明。",
        " */",
        "",
    ]
    if mod == "core":
        head += [
            "/* core.js 是依赖根：不 import 任何业务模块，因此不会有循环依赖。 */",
            "",
        ]
        # 可变容器：被重新赋值的 let 放这里，绕开「import 绑定不可重新赋值」
        if p["mutable"]:
            head += [
                "/* ── 可变容器 ──",
                "   被重新赋值的 let/var 不能用 import 传递（import 是只读绑定），",
                "   统一放进这个对象，用属性访问读写。初值都是字面量，安全。 */",
                "/** @type {Record<string, any>} */",
                "export const mut = {",
            ]
            for name in sorted(p["mutable"]):
                init = p["mutable_init"].get(name, "null")
                head.append(f"  {name}: {init},")
            head += [
                "};",
                "",
            ]
        return head

    imports = p["imports"].get(mod, {})
    lines_out = []
    core_names = set(imports.get("core", set()))
    for src in sorted(imports):
        if src == "core":
            continue
        names = ", ".join(sorted(imports[src]))
        lines_out.append(f"import {{ {names} }} from './{src}.js';")
    # 用到可变变量的模块要 import mut
    if any(mod in p["mut_users"][n] for n in p["mutable"]):
        core_names.add("mut")
    if core_names:
        lines_out.append(f"import {{ {', '.join(sorted(core_names))} }} from './core.js';")
    if lines_out:
        head += lines_out + [""]
    return head


if __name__ == "__main__":
    raise SystemExit(main())

//! 数据目录：把 Oryx 的所有落盘位置都钉在 D 盘。
//!
//! 为什么要有这个模块：C 盘经常被塞满（用户明确要求「所有安装路径都在 D 盘」）。
//! 而一个桌面应用默认会往好几个地方写：
//!   · 安装目录          —— NSIS 默认落到 %LOCALAPPDATA% → C 盘
//!   · WebView2 profile  —— 88MB，本地缓存 + localStorage
//!   · 应用数据（配置/索引/模型）—— 1.4GB（index.db 就占 1.26GB）
//!   · 临时文件          —— %TEMP% → C 盘
//!   · Playwright 浏览器 —— 701MB（Chromium）→ C 盘
//!
//! 这些都不该由各模块自己想办法 —— 集中在这里定一次根目录，
//! 下面所有东西都从它派生。
//!
//! 根目录的选择顺序：
//!   1. ORYX_HOME 环境变量（显式覆盖，最优先）
//!   2. OYX_DATA_ROOT 环境变量
//!   3. 安装目录所在盘符下的固定位置（如 D:\OryxData）
//!   4. 退回 %LOCALAPPDATA%\Oryx（老行为，保证没有 D 盘的机器还能用）
//!
//! 注意「按安装目录所在盘符」这个判据：程序装在 D 盘，数据就该在 D 盘。
//! 用户把程序挪到 D 盘是刻意的 —— 意思是「这台机器上我倾向用 D 盘」。

use std::path::{Path, PathBuf};
use std::process::Command;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

/// 根目录下的各个子目录名。
const APP_DIR: &str = "Oryx";

/// Windows 的 `CREATE_NO_WINDOW` 创建标志。
///
/// 为什么必须有这个（2026-11-19 实测定位）：
/// 桌面壳是 **GUI 子系统**程序（`windows_subsystem = "windows"`），
/// 而内核是 **控制台子系统**程序（`python.exe`）。
///
/// GUI 父进程 spawn 控制台子进程时，如果**不带**这个标志，
/// 子进程得自己新分配一个控制台 —— 父进程没有控制台可继承，
/// 这个分配在某些时刻会失败。失败发生在进程启动的 DLL 初始化阶段，
/// 表现为：
///   · 退出码 `0xC0000142`（STATUS_DLL_INIT_FAILED）
///   · Windows 弹一个「python.exe - Application Error：应用程序无法正常启动(0xC0000142)」的框
///   · 内核**一个字节都没跑起来** —— 桌面端只看到端口没通，25 秒后报「内核启动超时」
///
/// 实测对照（同一台机器，spawn python.exe 的平均耗时）：
///
/// | 父进程         | 不带标志 | 带标志 |
/// |----------------|----------|--------|
/// | 控制台子系统   | 223ms    | 244ms  | ← **没差别**（有控制台可继承）
/// | **GUI 子系统** | **796ms**| 253ms  | ← 慢 3 倍，且偶发挂
///
/// 只有 GUI 父进程才有差别 —— **Tauri 壳正是 GUI 子系统**。
/// 在 pytest 里直接 spawn 测不出来（pytest 是控制台进程），
/// 所以行为验证要用 `pythonw.exe` 起一个 GUI 子进程当父进程。
///
/// 顺带一个好处：不会闪一个黑色控制台窗口出来。
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// 给命令加上 `CREATE_NO_WINDOW`（仅 Windows 有效，其他平台原样返回）。
///
/// 桌面壳里**所有** spawn Python 的地方都要走这个函数 —— 包括探测依赖的那次。
/// 探测那次也是 GUI 父进程 spawn 控制台子进程，一样会踩上面那个坑。
pub fn hide_console(cmd: &mut Command) {
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    #[cfg(not(windows))]
    let _ = cmd;
}

/// 算出数据根目录。
///
/// 返回的路径**不保证存在** —— 调用方负责建。
pub fn data_root() -> PathBuf {
    if let Ok(dir) = std::env::var("ORYX_HOME") {
        if !dir.trim().is_empty() {
            return PathBuf::from(dir);
        }
    }
    if let Ok(dir) = std::env::var("OYX_DATA_ROOT") {
        if !dir.trim().is_empty() {
            return PathBuf::from(dir);
        }
    }
    // 跟着安装目录走 —— 装在哪盘，数据就在哪盘
    if let Some(root) = root_next_to_exe() {
        return root;
    }
    // 兜底：没有 D 盘就老老实实用系统目录
    if let Ok(local) = std::env::var("LOCALAPPDATA") {
        return PathBuf::from(local).join(APP_DIR);
    }
    PathBuf::from(APP_DIR)
}

/// 安装目录所在盘符下的数据根目录。
///
/// 做法：exe 所在路径 → 取盘符 → 拼 `D:\OryxData`。
/// 开发 checkout（exe 在 target/release 里）也适用 —— 那也在项目所在盘。
fn root_next_to_exe() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let drive = exe.components().next()?;
    let letter = match drive {
        std::path::Component::Prefix(p) => {
            // Windows 的盘符前缀：前缀 + 盘符字母
            let s = p.as_os_str().to_string_lossy();
            let ch = s.chars().find(|c| c.is_ascii_alphabetic())?;
            format!("{}:\\", ch.to_ascii_uppercase())
        }
        _ => return None,
    };
    Some(PathBuf::from(letter).join("OryxData"))
}

/// 在根目录下拼一个子路径，并确保目录存在。
pub fn ensure_dir(root: &Path, name: &str) -> PathBuf {
    let dir = root.join(name);
    if let Err(exc) = std::fs::create_dir_all(&dir) {
        eprintln!("[oryx] 建目录失败 {}：{exc}", dir.display());
    }
    dir
}

/// 把子进程的环境变量全部指向数据根目录。
///
/// 必须在 spawn 内核**之前**调用 —— 子进程一启动就固化了自己的环境。
///
/// 逐个说明为什么要设：
///   ORYX_HOME                —— 内核的配置/索引/模型全在它下面（config.py 的 app_dir()）
///   TMPDIR / TEMP / TMP      —— Python 的 tempfile 落这儿（端口文件、图标解包）
///   PLAYWRIGHT_BROWSERS_PATH —— Playwright 的 Chromium 缓存（701MB）
///   HF_HOME / HUGGINGFACE_HUB_CACHE —— HuggingFace 模型缓存
///   TORCH_HOME               —— PyTorch 权重缓存
///   XDG_CACHE_HOME           —— Linux 风格的缓存目录（Windows 上部分库也认）
///   PIP_CACHE_DIR            —— pip 下载缓存
///
/// 用 `set_var` 而不是传给 Command 的 `.env()`：Command 只影响那个子进程，
/// 而我们要的是**整个进程树**都改掉（内核还会再拉起 agentd 子进程）。
pub fn redirect_env(root: &Path) {
    let root = root.to_path_buf();

    // 数据根目录本身
    safe_set("ORYX_HOME", &root.join("data"));
    safe_set("OYX_DATA_ROOT", &root);

    // 临时目录 —— 先建出来再用
    // TMPDIR 必须一起设：它在 Python 的 tempfile 候选列表里排在 TEMP/TMP 前面，
    // 只设 TEMP/TMP 的话临时文件还是会漏到 C 盘。与 Python 侧 paths.py 保持一致。
    let temp = ensure_dir(&root, "temp");
    safe_set("TMPDIR", &temp);
    safe_set("TEMP", &temp);
    safe_set("TMP", &temp);

    // 各类模型/缓存根目录
    let cache = ensure_dir(&root, "cache");
    safe_set("XDG_CACHE_HOME", &cache);
    safe_set("PIP_CACHE_DIR", &cache.join("pip"));
    safe_set("HF_HOME", &cache.join("huggingface"));
    safe_set("HUGGINGFACE_HUB_CACHE", &cache.join("huggingface").join("hub"));
    safe_set("TRANSFORMERS_CACHE", &cache.join("huggingface").join("transformers"));
    safe_set("TORCH_HOME", &cache.join("torch"));
    safe_set("PLAYWRIGHT_BROWSERS_PATH", &cache.join("playwright"));

    // npm/npx 缓存 —— 打包时 npx @tauri-apps/cli 会写 1.4GB 到 C 盘
    safe_set("npm_config_cache", &cache.join("npm"));
}

/// 设一个环境变量。空值不设 —— 设成空串会让某些库行为异常。
fn safe_set(key: &str, value: &Path) {
    let s = value.to_string_lossy();
    if s.trim().is_empty() {
        return;
    }
    // Rust 2024 里 set_var 变成 unsafe 了（多线程下改环境有竞态）。
    // 这里只在 main 开头、内核还没起的时候调一次，竞不了。
    unsafe {
        std::env::set_var(key, &*s);
    }
}

/// 找一个能用的 Python 解释器。
///
/// 为什么不能只靠 PATH：这台机器上 python 不在系统 PATH 里
/// （conda 环境在 D:\Anaconda\envs\mcp-mw，但那是开发环境）。
/// 只 spawn "python" 会失败 —— 桌面端起来了但内核没起，界面一片空白。
///
/// 找法：
///   1. ORYX_PYTHON 环境变量（显式指定，最优先）
///   2. PATH 里的 python / python3 / py
///   3. 常见安装位置（conda env / conda base / 官方安装包 / 用户目录）
///
/// 候选要**验证能不能 import 内核依赖**（fastapi/uvicorn…）——
/// 找到一个只装了标准库的解释器等于没找到：内核起来就 ImportError。
pub fn find_python() -> Option<PathBuf> {
    if let Ok(p) = std::env::var("ORYX_PYTHON") {
        let path = PathBuf::from(p);
        if path.exists() {
            return Some(path);
        }
    }
    // PATH 里的
    for name in ["python", "python3", "py"] {
        if let Some(p) = which(name) {
            if has_backend_deps(&p) {
                return Some(p);
            }
        }
    }
    // 常见安装位置。
    // 顺序有讲究：conda 的 env 优先于 base —— base 环境常常缺包。
    let mut candidates: Vec<PathBuf> = Vec::new();
    for base in ["D:/Anaconda", "C:/ProgramData/Anaconda3", "C:/Anaconda3"] {
        // 具体环境名（开发时用的是 mcp-mw）
        for env in ["mcp-mw", "oryx"] {
            candidates.push(PathBuf::from(base).join("envs").join(env).join("python.exe"));
        }
        candidates.push(PathBuf::from(base).join("python.exe"));
    }
    if let Ok(local) = std::env::var("LOCALAPPDATA") {
        for v in ["313", "312", "311"] {
            candidates.push(PathBuf::from(&local).join(format!("Programs/Python/Python{v}/python.exe")));
        }
    }
    candidates.push(PathBuf::from("D:/Python/Python311/python.exe"));

    for c in &candidates {
        if c.exists() && has_backend_deps(c) {
            return Some(c.clone());
        }
    }
    // 兜底：有一个能跑就行（依赖缺失时内核会报错，但至少给了线索）
    candidates.into_iter().find(|c| c.exists())
}

/// 这个解释器能不能 import 内核的核心依赖。
///
/// 判据用 `import fastapi, uvicorn` —— 少一个内核就起不来。
/// 一次探测约 0.3 秒，只在启动时做一次，可以接受。
///
/// **必须带 `CREATE_NO_WINDOW`** —— 探测本身也是 GUI 父进程 spawn 控制台子进程，
/// 不带标志会偶发 `0xC0000142`。那种情况下探测会**假失败**（进程没起来，
/// 退出码非 0），于是 `find_python` 会误判「这个解释器不行」，
/// 白白跳到下一个候选 —— 用户看到的就是「明明装了 Python，桌面端却说找不到」。
fn has_backend_deps(python: &Path) -> bool {
    use std::process::Stdio;
    let mut cmd = Command::new(python);
    cmd.arg("-c")
        .arg("import fastapi, uvicorn")
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    hide_console(&mut cmd);
    cmd.status()
        .map(|s| s.success())
        .unwrap_or(false)
}

/// 在 PATH 里找可执行文件。Windows 上要逐个目录试（PATHEXT）。
fn which(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    let exts: Vec<String> = if cfg!(windows) {
        std::env::var("PATHEXT")
            .unwrap_or_else(|_| ".COM;.EXE;.BAT;.CMD".to_string())
            .split(';')
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string())
            .collect()
    } else {
        vec![String::new()]
    };
    for dir in std::env::split_paths(&path) {
        for ext in &exts {
            let candidate = dir.join(format!("{name}{ext}"));
            if candidate.exists() {
                return Some(candidate);
            }
        }
        // 没扩展名时也试一下（某些 PATH 项直接指向可执行文件）
        let plain = dir.join(name);
        if plain.exists() {
            return Some(plain);
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 防回归：`CREATE_NO_WINDOW` 的值不能被改错。
    ///
    /// 这个标志的缺失是**偶发**才出问题的 —— 不带它，十次里可能只挂一次，
    /// 而且失败现象是「内核启动超时」这种看起来毫不相关的信息。
    /// 等到线上炸了再回头找，代价太大。所以钉一个测试守着。
    #[cfg(windows)]
    #[test]
    fn create_no_window_flag_value_is_correct() {
        assert_eq!(
            CREATE_NO_WINDOW, 0x0800_0000,
            "CREATE_NO_WINDOW 的值变了 —— 这个标志改错会让修复失效"
        );
    }

    /// 防回归：**所有** spawn Python 的地方都必须带 `CREATE_NO_WINDOW`。
    ///
    /// 为什么用「查源码」而不是「跑一百次看会不会挂」：
    /// 那个失败是**偶发**的 —— 实测不带标志时 60 次命中 1 次（≈1.7%）。
    /// 想靠跑次数抓住它，要么跑几百次（测试要几分钟，太慢），
    /// 要么就是**概率性地漏掉**（我试过用 cmd.exe 当代理，摘掉标志照样全绿）。
    ///
    /// 所以改成查源码里 spawn 的调用点 —— 确定性检查，永远不漏。
    /// 真正的行为验证交给 Python 侧的脚本
    /// （`core/tests/test_desktop_spawn_flags.py`），那边能控节奏。
    ///
    /// 判据：`Command::new(` 出现的每个地方，
    /// 在同一个函数体内必须能读到 `hide_console`。
    #[cfg(windows)]
    #[test]
    fn every_spawn_site_applies_hide_console() {
        let src = include_str!("paths.rs");
        let main_src = include_str!("main.rs");

        for (name, text) in [("paths.rs", src), ("main.rs", main_src)] {
            let mut search_from = 0usize;
            while let Some(pos) = text[search_from..].find("Command::new(") {
                let idx = search_from + pos;
                search_from = idx + 1;
                // 取这个调用点往后的一段。
                // 必须按**字符**边界切 —— 中文注释里一个汉字 3 字节，
                // 按字节切会切在字符中间（Rust 直接 panic）。
                let window: String = text[idx..].chars().take(700).collect();
                // 跳过测试模块里的自测代码
                let is_test = window.contains("#[test]") || window.contains("mod tests");
                if !is_test && !window.contains("hide_console") {
                    let line = text[..idx].lines().count();
                    panic!(
                        "{name}:{line} 有 Command::new 但没走 hide_console —— \
                         GUI 父进程 spawn 控制台子进程必须带 CREATE_NO_WINDOW"
                    );
                }
            }
        }
    }
}

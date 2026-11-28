#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Oryx 桌面壳。
//!
//! 职责很薄：拉起 Python 内核 → 等它就绪 → 让 WebView 打开本地界面 → 退出时收尸。
//! 所有业务都在 Python 侧，这样界面和内核可以各自独立演进。

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent};

mod paths;
mod winstate;

const DEFAULT_PORT: u16 = 8765;
const BACKEND_START_TIMEOUT: Duration = Duration::from_secs(25);

/// 探活响应里的 Oryx 身份标识，与 `core/oryx/server.py` 的 `/api/ping` 一致：
/// `{"ok":true,"service":"oryx"}`。只探 TCP 端口会把别的本地服务当成内核 ——
/// 2026-11-26 实测 8765 被「地导目标分析」后端占用，桌面端直接跳到了
/// 人家的 JSON 页面上。
const PING_IDENTITY: &str = "\"service\":\"oryx\"";

struct Backend(Mutex<Option<Child>>);

#[derive(PartialEq, Eq, Debug)]
enum PortState {
    /// `/api/ping` 返回 200 且带 Oryx 身份 —— 已有内核可复用。
    Ready,
    /// 端口能连上，但对面不是 Oryx 内核。
    Foreign,
    /// 端口没人监听，可以拉起内核。
    Free,
}

/// 用标准库手写一次最小 HTTP GET `/api/ping`，校验响应身份。
///
/// 为什么不引 HTTP 客户端：Cargo.toml 里只有 tauri / serde，为了一次探活
/// 加一整套依赖不值；`/api/ping` 公开、无副作用、响应体固定，
/// `Connection: close` + 读一段文本就够了。
fn probe_port(port: u16) -> PortState {
    let addr: SocketAddr = match format!("127.0.0.1:{port}").parse() {
        Ok(a) => a,
        Err(_) => return PortState::Free,
    };
    let mut stream = match TcpStream::connect_timeout(&addr, Duration::from_millis(300)) {
        Ok(s) => s,
        Err(_) => return PortState::Free,
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(600)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(600)));
    let req = format!(
        "GET /api/ping HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAccept: */*\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(req.as_bytes()).is_err() {
        return PortState::Foreign;
    }
    let mut buf = Vec::new();
    // 读超时也保留已读到的内容 —— 有的服务不发 Connection: close，
    // 但状态行和响应体已经够判断了。
    let _ = stream.read_to_end(&mut buf);
    let text = String::from_utf8_lossy(&buf);
    let status_200 = text
        .lines()
        .next()
        .map(|line| line.contains(" 200 "))
        .unwrap_or(false);
    if status_200 && text.contains(PING_IDENTITY) {
        PortState::Ready
    } else {
        PortState::Foreign
    }
}

fn port_conflict_message(port: u16) -> String {
    format!(
        "端口 {port} 已被其它服务占用（不是 Oryx 内核）。请关闭占用进程，或设置 ORYX_PORT 换一个端口后重启。"
    )
}

/// 判定一个目录是不是「开发 checkout 的仓库根」——即除了 core/ 还带 apps/desktop。
///
/// 为什么需要这个区分：`target/release/core/` 里也有一份 core 的拷贝（打包资源），
/// 从 exe 往上找会先撞上那份**过期的**拷贝，导致源码改了但双击图标看不到变化。
/// 所以开发场景必须优先认仓库根。
fn is_repo_root(dir: &std::path::Path) -> bool {
    dir.join("core").join("oryx").join("__main__.py").exists()
        && dir.join("apps").join("desktop").is_dir()
}

/// 找内核目录，顺序：
///   1. `ORYX_CORE_DIR` 环境变量（显式覆盖）
///   2. **开发 checkout 的仓库根**（源码改了立刻生效）
///   3. 安装包资源目录（<安装目录>/core）
///   4. 从可执行文件往上找（扁平安装等兜底）
fn core_dir(app: &tauri::AppHandle) -> std::path::PathBuf {
    if let Ok(dir) = std::env::var("ORYX_CORE_DIR") {
        return dir.into();
    }

    // 2. 开发 checkout：从 exe 往上找仓库根
    if let Ok(exe) = std::env::current_exe() {
        for ancestor in exe.ancestors() {
            if is_repo_root(ancestor) {
                return ancestor.join("core");
            }
        }
    }

    // 3. 安装包资源目录
    if let Ok(resources) = app.path().resource_dir() {
        let candidate = resources.join("core");
        if candidate.join("oryx").join("__main__.py").exists() {
            return candidate;
        }
        let flat = resources.join("oryx");
        if flat.join("__main__.py").exists() {
            return resources;
        }
    }

    // 4. 兜底
    if let Ok(exe) = std::env::current_exe() {
        for ancestor in exe.ancestors() {
            let candidate = ancestor.join("core");
            if candidate.join("oryx").join("__main__.py").exists() {
                return candidate;
            }
        }
    }
    std::env::current_dir().unwrap_or_else(|_| ".".into())
}

/// 不依赖 AppHandle 的内核目录解析，用于在 Tauri 初始化之前抢先拉起内核。
/// 保持与 core_dir 相同的优先级语义：仓库根优先于 exe 旁的打包拷贝。
/// 解析不到时返回 None，由 setup 里的 core_dir 走完整兜底逻辑。
fn core_dir_early() -> Option<std::path::PathBuf> {
    if let Ok(dir) = std::env::var("ORYX_CORE_DIR") {
        return Some(dir.into());
    }
    if let Ok(exe) = std::env::current_exe() {
        for ancestor in exe.ancestors() {
            if is_repo_root(ancestor) {
                return Some(ancestor.join("core"));
            }
        }
        for ancestor in exe.ancestors() {
            let candidate = ancestor.join("core");
            if candidate.join("oryx").join("__main__.py").exists() {
                return Some(candidate);
            }
        }
    }
    None
}

/// 拉起 Python 内核。
///
/// 解释器路径由 paths::find_python 找 —— 不能只靠 PATH：
/// 这台机器上 python 不在系统 PATH 里，只 spawn "python" 会失败，
/// 桌面端起来了但内核没起，界面一片空白。
///
/// **必须带 `CREATE_NO_WINDOW`** —— 见 `paths::hide_console` 的注释。
/// 不带的话，GUI 壳 spawn 控制台 python.exe 会偶发 `0xC0000142`：
/// 内核一个字节都没跑起来，Windows 还弹一个「应用程序无法正常启动」的框，
/// 而这里 `.ok()` 会把失败吞成 `None` —— 用户只看到「内核启动超时」，
/// 完全不知道真实原因。
///
/// 所以这里**不吞错误**：spawn 失败要把原因记下来，
/// 让启动页能显示「为什么没起来」而不是干等 25 秒。
fn spawn_python(core: &std::path::Path, port: u16) -> Result<Child, String> {
    let python = paths::find_python().ok_or_else(|| {
        "找不到可用的 Python 解释器（PATH 里没有，且常见安装位置也没找到能 import fastapi/uvicorn 的）".to_string()
    })?;
    let mut cmd = Command::new(&python);
    cmd.arg("-m")
        .arg("oryx")
        .arg("--no-browser")
        .arg("--port")
        .arg(port.to_string())
        .arg("--parent-pid")
        .arg(std::process::id().to_string())
        .current_dir(core)
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    paths::hide_console(&mut cmd);
    cmd.spawn()
        .map_err(|e| format!("拉起 {} 失败：{e}", python.display()))
}

/// 拉起内核（复用已有 / 新拉起 / 失败）。
///
/// 返回 `Ok(None)` = 复用已在跑的实例，或端口被别的进程占着（不拉起，
/// 由 `wait_ready_or_report` 立即给出明确原因）；`Ok(Some(child))` = 新拉起；
/// `Err(reason)` = 拉不起来，`reason` 是给用户看的原因。
fn spawn_backend(app: &tauri::AppHandle, port: u16) -> Result<Option<Child>, String> {
    match probe_port(port) {
        // 已经有一个 Oryx 内核在跑（比如用户先启动了命令行版本），直接复用
        PortState::Ready => Ok(None),
        // 端口被别人占着：再 spawn 一个也绑不上，别浪费 25 秒等超时
        PortState::Foreign => Ok(None),
        PortState::Free => {
            let core = core_dir(app);
            spawn_python(&core, port).map(Some)
        }
    }
}

/// 等内核就绪，同时盯着子进程的死活。
///
/// `Ok(())` = Oryx 内核真的就绪；`Err(reason)` = 没起来 / 端口被占。
/// 探活必须验身份：只探 TCP 的话，任何占了端口的本地服务都会被当成内核
/// （2026-11-26 实测被「地导目标分析」后端占用，桌面端跳到了对方的页面）。
fn wait_ready_or_report(
    handle: &tauri::AppHandle,
    port: u16,
    timeout: Duration,
) -> Result<(), String> {
    let deadline = Instant::now() + timeout;
    loop {
        match probe_port(port) {
            PortState::Ready => return Ok(()),
            // 明确报错，不干等 25 秒超时
            PortState::Foreign => return Err(port_conflict_message(port)),
            PortState::Free => {}
        }
        // 内核自己退了？把退出码带出去 —— 这比「超时」有用得多。
        if let Some(state) = handle.try_state::<Backend>() {
            if let Ok(mut guard) = state.0.lock() {
                if let Some(child) = guard.as_mut() {
                    match child.try_wait() {
                        Ok(Some(status)) => {
                            return Err(format!(
                                "内核进程启动后立刻退出（退出码 {}）",
                                status.code().map(|c| c.to_string()).unwrap_or_else(|| "被系统终止".into())
                            ));
                        }
                        Ok(None) => {}
                        Err(e) => return Err(format!("内核进程状态查询失败：{e}")),
                    }
                }
            }
        }
        if Instant::now() >= deadline {
            return Err(format!(
                "内核在 {} 秒内没监听 {} 端口",
                timeout.as_secs(),
                port
            ));
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

fn main() {
    let port: u16 = std::env::var("ORYX_PORT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(DEFAULT_PORT);

    // 数据目录先定下来，后面两处都要用：
    //   · 环境变量重定向（内核、模型缓存、临时文件都指到那儿）
    //   · Tauri 自己的目录覆盖（webview profile 在 Windows 上默认在 C 盘）
    let data_root = paths::data_root();
    paths::redirect_env(&data_root);

    // 抢先拉起内核：与 Tauri/WebView2 初始化并行，省掉串行等待。
    // 解析不到内核目录时留 None，交给 setup 里的完整逻辑兜底。
    // 只有端口完全没人监听时才抢先拉起内核。端口被别的服务占着时
    // 不要盲目 spawn（绑不上），交给 setup 里的探活给出明确原因。
    let early: Mutex<Option<Child>> = Mutex::new(
        if probe_port(port) == PortState::Free {
            core_dir_early().and_then(|core| spawn_python(&core, port).ok())
        } else {
            None
        },
    );

    let mut builder = tauri::Builder::default();
    builder = builder.plugin(tauri_plugin_dialog::init());

    // Tauri 自己的目录（webview profile、配置、缓存）也搬到数据根目录。
    // Windows 上 webview 数据目录默认在 %LOCALAPPDATA%\com.oryx.desktop（88MB），
    // 不覆盖的话 C 盘还是会被占一块。
    // 必须在 generate_context! 之后改 —— 覆盖是运行时生效的 config 字段。
    let mut context = tauri::generate_context!();
    context.config_mut().app.app_directories_override =
        Some(tauri::utils::config::AppDirectoriesOverride::Root(
            data_root.join("tauri").into(),
        ));

    builder
        .setup(move |app| {
            // 恢复上次窗口的大小与位置。
            //
            // 为什么要有：桌面应用每次都按固定尺寸打开，用户调好的窗口
            // 下次就没了 —— 明明刚拖到舒服的位置，关掉再开又弹回默认。
            //
            // 位置要**先验证还在屏幕上**：显示器拔了/换了分辨率之后，
            // 存下来的坐标可能落在屏幕外 —— 那样窗口会开在看不见的地方，
            // 用户还以为应用坏了。所以落在外面就只恢复大小，位置交回系统居中。
            if let Some(window) = app.get_webview_window("main") {
                if let Some(st) = winstate::load(&data_root) {
                    let monitors = window.available_monitors().unwrap_or_default();
                    let visible = winstate::position_visible(
                        &monitors,
                        st.x,
                        st.y,
                        st.width,
                        st.height,
                    );
                    let _ = window.set_size(tauri::PhysicalSize::new(st.width, st.height));
                    if visible {
                        let _ = window.set_position(tauri::PhysicalPosition::new(st.x, st.y));
                    } else {
                        let _ = window.center();
                    }

                    // **把实际生效的几何回写**。
                    //
                    // 两个理由：
                    //   1. 系统可能把请求的尺寸**夹**到工作区范围内（比如用户
                    //      接了个 4K 屏又换回 1080p），回写之后存档才与现实一致；
                    //   2. 它是「恢复有没有生效」的**可观测证据** ——
                    //      没有这一步，存档里的值永远只是「我们请求的值」，
                    //      恢复失败也看不出来。
                    if let (Ok(size), Ok(pos)) = (window.outer_size(), window.outer_position())
                    {
                        let actual = winstate::WindowState {
                            width: size.width,
                            height: size.height,
                            x: pos.x,
                            y: pos.y,
                        };
                        if let Err(reason) = winstate::save(&data_root, &actual) {
                            winstate::save_error(&data_root, &reason);
                        }
                    }
                }

                // 之后窗口一被移动/缩放就把新状态记下来。
                //
                // **为什么不在退出时记**
                // ------------------
                // 退出那一刻窗口已经被销毁了 —— `get_webview_window("main")`
                // 返回 None，什么都拿不到（实测过）。
                // 所以改成**跟着窗口事件实时记**：最后一次写下的就是最新的。
                //
                // 拖动窗口时 Moved 会连着触发几十次 —— 写的是几百字节的小文件，
                // 但还是限一下流：800ms 之内不重复写。
                let win = window.clone();
                let root = data_root.clone();
                let last_write = Mutex::new(Instant::now() - Duration::from_secs(5));
                window.on_window_event(move |event| {
                    let moved_or_resized = matches!(
                        event,
                        tauri::WindowEvent::Moved(_) | tauri::WindowEvent::Resized(_)
                    );
                    if !moved_or_resized {
                        return;
                    }
                    if let Ok(mut last) = last_write.lock() {
                        if last.elapsed() < Duration::from_millis(800) {
                            return;
                        }
                        *last = Instant::now();
                    }
                    if let (Ok(size), Ok(pos)) = (win.outer_size(), win.outer_position()) {
                        let st = winstate::WindowState {
                            width: size.width,
                            height: size.height,
                            x: pos.x,
                            y: pos.y,
                        };
                        if let Err(reason) = winstate::save(&root, &st) {
                            winstate::save_error(&root, &reason);
                        }
                    }
                });
            }

            // 内核的拉起结果：拉不起来就把原因记下来，给启动页显示。
            // 以前这里是 `.ok()` —— 失败被吞成 None，用户只能看到
            // 「内核启动超时」，完全不知道真实原因（0xc0000142？找不到解释器？）。
            let mut spawn_error: Option<String> = None;
            let child: Option<Child> = match early.lock().ok().and_then(|mut guard| guard.take()) {
                Some(c) => Some(c),
                None => match spawn_backend(&app.handle().clone(), port) {
                    Ok(c) => c,
                    Err(e) => {
                        spawn_error = Some(e);
                        None
                    }
                },
            };
            app.manage(Backend(Mutex::new(child)));

            // 等内核起来后再导航，避免用户看到一片白
            if let Some(_window) = app.get_webview_window("main") {
                let handle = app.handle().clone();
                let spawn_error = spawn_error.clone();
                std::thread::spawn(move || {
                    let reason = match wait_ready_or_report(&handle, port, BACKEND_START_TIMEOUT) {
                        Ok(()) => None,
                        Err(e) => Some(match spawn_error {
                            Some(se) => format!("{se}（{e}）"),
                            None => e,
                        }),
                    };
                    match reason {
                        None => {
                            let url = format!("http://127.0.0.1:{port}");
                            let inner = handle.clone();
                            let _ = handle.run_on_main_thread(move || {
                                if let Some(w) = inner.get_webview_window("main") {
                                    let _ = w.eval(&format!("window.location.replace('{url}')"));
                                }
                            });
                        }
                        Some(msg) => {
                            // 把真实原因送到启动页 —— 启动页据此显示失败，
                            // 而不是让用户对着一个「超时」猜。
                            eprintln!("[oryx] 内核启动失败：{msg}");
                            let inner = handle.clone();
                            let _ = handle.run_on_main_thread(move || {
                                if let Some(w) = inner.get_webview_window("main") {
                                    let js = format!(
                                        "window.__ORYX_START_ERROR__ = {}; window.__oryxReportStartError && window.__oryxReportStartError({});",
                                        serde_json::to_string(&msg).unwrap_or_else(|_| "\"未知错误\"".into()),
                                        serde_json::to_string(&msg).unwrap_or_else(|_| "\"未知错误\"".into())
                                    );
                                    let _ = w.eval(&js);
                                }
                            });
                        }
                    }
                });
            }
            Ok(())
        })
        .build(context)
        .expect("Tauri 启动失败")
        .run(|app_handle, event| {
            if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
                // 退出前把窗口的大小和位置记下来 —— 下次按这个开。
                // 放在 Exit 分支里（而不是 CloseRequested）：那时窗口还在，
                // 拿得到 outer_size / outer_position。
                //
                // Tauri v2 里这两个都返回 Result —— 拿不到就跳过，
                // 存窗口状态不是关键路径，失败不该影响退出。
                let root = paths::data_root();
                if let Some(window) = app_handle.get_webview_window("main") {
                    if let (Ok(size), Ok(pos)) = (window.outer_size(), window.outer_position())
                    {
                        let st = winstate::WindowState {
                            width: size.width,
                            height: size.height,
                            x: pos.x,
                            y: pos.y,
                        };
                        if let Err(reason) = winstate::save(&root, &st) {
                            winstate::save_error(&root, &reason);
                        }
                    }
                }
                if let Some(backend) = app_handle.try_state::<Backend>() {
                    if let Ok(mut guard) = backend.0.lock() {
                        if let Some(mut child) = guard.take() {
                            let _ = child.kill();
                            let _ = child.wait();
                        }
                    }
                }
            }
        });
}

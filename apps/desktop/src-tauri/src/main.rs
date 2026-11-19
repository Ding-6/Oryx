#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Oryx 桌面壳。
//!
//! 职责很薄：拉起 Python 内核 → 等它就绪 → 让 WebView 打开本地界面 → 退出时收尸。
//! 所有业务都在 Python 侧，这样界面和内核可以各自独立演进。

use std::net::{SocketAddr, TcpStream};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent};

mod paths;

const DEFAULT_PORT: u16 = 8765;
const BACKEND_START_TIMEOUT: Duration = Duration::from_secs(25);

struct Backend(Mutex<Option<Child>>);

fn port_ready(port: u16) -> bool {
    let addr: SocketAddr = match format!("127.0.0.1:{port}").parse() {
        Ok(a) => a,
        Err(_) => return false,
    };
    TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok()
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
fn spawn_python(core: &std::path::Path, port: u16) -> Option<Child> {
    let python = paths::find_python()?;
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
    cmd.spawn().ok()
}

fn spawn_backend(app: &tauri::AppHandle, port: u16) -> Option<Child> {
    if port_ready(port) {
        // 已经有一个内核在跑（比如用户先启动了命令行版本），直接复用
        return None;
    }
    let core = core_dir(app);
    spawn_python(&core, port)
}

fn wait_ready(port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if port_ready(port) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    false
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
    let early: Mutex<Option<Child>> = Mutex::new(if port_ready(port) {
        None
    } else {
        core_dir_early().and_then(|core| spawn_python(&core, port))
    });

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
            let child = early
                .lock()
                .ok()
                .and_then(|mut guard| guard.take())
                .or_else(|| spawn_backend(&app.handle().clone(), port));
            app.manage(Backend(Mutex::new(child)));

            // 等内核起来后再导航，避免用户看到一片白
            if let Some(_window) = app.get_webview_window("main") {
                let handle = app.handle().clone();
                std::thread::spawn(move || {
                    if wait_ready(port, BACKEND_START_TIMEOUT) {
                        let url = format!("http://127.0.0.1:{port}");
                        let inner = handle.clone();
                        let _ = handle.run_on_main_thread(move || {
                            if let Some(w) = inner.get_webview_window("main") {
                                let _ = w.eval(&format!("window.location.replace('{url}')"));
                            }
                        });
                    }
                });
            }
            Ok(())
        })
        .build(context)
        .expect("Tauri 启动失败")
        .run(|app_handle, event| {
            if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
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

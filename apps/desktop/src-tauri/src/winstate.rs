//! 窗口状态：记住上次的大小和位置。
//!
//! 为什么要有这个
//! --------------
//! 桌面应用每次都按固定尺寸打开，用户调好的窗口大小和位置**下次就没了** ——
//! 明明刚拖到舒服的位置，关掉再开又弹回默认大小、默认位置。
//!
//! 存哪儿
//! ------
//! 数据根目录（`paths::data_root`）下的 `window-state.json` ——
//! 跟着应用数据走，不写 C 盘、不碰注册表。
//!
//! 存什么
//! ------
//! 只有宽、高、位置三个值。
//!
//! **不存最大化状态** —— 最大化过的窗口尺寸没有意义（还原尺寸才有用），
//! 存下来反而会在下次开机时把窗口放成「全屏尺寸但没全屏」的样子。
//!
//! 越界保护
//! --------
//! 存的位置可能在**当前**屏幕之外（用户拔了显示器、换了分辨率）。
//! 所以恢复前要跟当前可用显示器比对，落在外面就只恢复大小、位置交给系统居中。

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct WindowState {
    pub width: u32,
    pub height: u32,
    pub x: i32,
    pub y: i32,
}

/// 存盘路径。
fn state_path(root: &Path) -> PathBuf {
    root.join("window-state.json")
}

/// 读上次的状态。文件不存在 / 坏了 → 返回 None（当没有，第一次启动）。
pub fn load(root: &Path) -> Option<WindowState> {
    let path = state_path(root);
    let text = std::fs::read_to_string(&path).ok()?;
    serde_json::from_str::<WindowState>(&text).ok()
}

/// 写当前的状态。
///
/// 返回 `Err(原因)` 而不是静默吞掉 —— 窗口状态存不上是个**静默故障**：
/// 用户每次都被弹回默认大小，却没有任何提示。
/// 调用方把原因记到旁边的 `.error.txt` 里，方便排查。
pub fn save(root: &Path, st: &WindowState) -> Result<(), String> {
    let path = state_path(root);
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)
            .map_err(|e| format!("建目录失败 {}：{e}", dir.display()))?;
    }
    let text = serde_json::to_string(st).map_err(|e| format!("序列化失败：{e}"))?;
    std::fs::write(&path, text).map_err(|e| format!("写 {} 失败：{e}", path.display()))
}

/// 把失败原因记到旁边一个文件里。
///
/// GUI 子系统程序没有控制台，eprintln 谁也看不见 ——
/// 写文件是唯一能让「为什么没存上」被看到的地方。
pub fn save_error(root: &Path, reason: &str) {
    let path = state_path(root).with_extension("error.txt");
    let _ = std::fs::write(path, reason);
}

/// 这个位置还落在某块显示器里吗？
///
/// 落在外面说明显示器拔了/换了 —— 这时候不该把窗口放过去，
/// 那样窗口会出现在屏幕外，用户还以为应用坏了。
pub fn position_visible(
    monitors: &[tauri::Monitor],
    x: i32,
    y: i32,
    width: u32,
    height: u32,
) -> bool {
    if monitors.is_empty() {
        return false;
    }
    monitors.iter().any(|m| {
        let p = m.position();
        let s = m.size();
        // 窗口左上角落在某块显示器范围内就算可见。
        // 故意放宽一点：只要有**一部分**窗口在屏内，用户就能拖回来。
        let left = p.x;
        let top = p.y;
        let right = left + s.width as i32;
        let bottom = top + s.height as i32;
        x < right && x + width as i32 > left && y < bottom && y + height as i32 > top
    })
}

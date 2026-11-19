# Oryx 桌面壳（Tauri 2）

真正的 Windows 应用：独立窗口、自带图标、开机即用，**不经过浏览器**。

薄壳一层：拉起 Python 内核 → 等一下 → 让 WebView 打开本地界面 → 退出时收尸。

```
apps/desktop/
├─ dist/index.html        # 启动页：轮询内核，就绪后跳转
├─ src-tauri/
│  ├─ src/main.rs         # 启动/退出逻辑、内核定位
│  ├─ tauri.conf.json     # 窗口、打包、资源清单
│  ├─ Cargo.toml
│  └─ icons/
└─ scripts/make_icons.py
```

## 运行

```bash
cd apps/desktop/src-tauri
cargo run --release
```

或者双击桌面上的 `Oryx` 快捷方式。

## ⚠ 开发时必读：源码改了但图标没变

`tauri.conf.json` 会把 `core/`、`skills/`、`experts/`、`agents/` 拷到
`target/release/` 下。**exe 曾经从自己所在目录往上找 `core/`，结果先撞上那份
打包拷贝**——于是你在 `core/` 里改了半天，双击图标看到的还是几天前的界面。

现在 `core_dir()` 的查找顺序改成：

| 顺序 | 来源 | 用途 |
|---|---|---|
| 1 | `ORYX_CORE_DIR` 环境变量 | 显式覆盖 |
| 2 | **仓库根**（`core/` + `apps/desktop` 同时存在） | **开发：源码改了立刻生效** |
| 3 | 安装包 `resource_dir()/core` | 正式安装后 |
| 4 | 从 exe 往上找 `core/` | 扁平安装兜底 |

所以开发时**不需要**每次都重新打包，改完 `core/` 直接双击图标即可。

只有改了 [main.rs](src/main.rs) 本身才需要重编译：

```bash
cd apps/desktop/src-tauri && cargo build --release
```

**注意**：`cargo build` **不会**同步 `core/`（`build.rs` 里只调了
`tauri_build::build()`）。以前那句「cargo build 会顺手拷一份」是**不准确的** ——
`target/release/core/` 那份拷贝其实是**手工**拷的。

所以同步有专门的脚本（**不需要装 Rust**，改完 Python 就能同步）：

```bash
python apps/desktop/scripts/sync_core.py          # 同步
python apps/desktop/scripts/sync_core.py --check  # 只检查是否最新
```

`core/tests/test_desktop_bundle.py` 里有守卫测试盯着这件事 ——
**清单是动态扫描源码目录的**（不是手写的模块名列表），
所以新增模块自动被覆盖，拷贝比源码旧时测试直接失败。

> 以前那个手写清单漏了一堆模块（`artifacts.py`、`autoclass.py`、
> `context.py`、`frontmatter.py`、`planning.py`…），
> 副本比源码旧了几个月都没人发现 —— 因为**清单不跟着源码走**。

## 打包安装程序

**一键**（推荐）：

```bash
python apps/desktop/scripts/build.py
```

它会按顺序做四件事 —— **同步 → 编译 → 打包 → 报告产物**，
并在动手前就检查工具链，缺什么直接说清楚：

```
缺这些：
  · Windows SDK 没装 —— MSVC 工具链里勾上「适用于桌面开发的 Windows SDK」
```

第一步「同步」最容易漏 —— `cargo build` **不做**这件事，
漏了就会**把旧代码打进安装包**（装完才发现跑的是几个月前的界面）。

手工做的话：

```bash
python apps/desktop/scripts/sync_core.py        # 1. 同步
cd src-tauri && cargo build --release          # 2. 编译
cd .. && npx @tauri-apps/cli@2 build --bundles nsis   # 3. 打包
```

产物：`src-tauri/target/release/bundle/nsis/Oryx_0.1.0_x64-setup.exe`（约 1.3 MB）

安装包会把 **Python 内核一起打进去**（`core/` `skills/` `experts/` `agents/`），
装完即使脱离源码仓库也能运行。

## 前置条件

| 依赖 | 说明 |
|---|---|
| **Python 3.11+** | 唯一外部依赖，需装好依赖包（见下） |
| WebView2 运行时 | Windows 11 自带 |
| Rust + MSVC | 仅编译时需要 |

内核依赖：

```bash
pip install fastapi uvicorn httpx pyyaml pillow cryptography python-docx openpyxl python-pptx
# 可选：playwright（浏览器 Agent）、rapidocr-onnxruntime（OCR）、torch+transformers（向量检索）
```

## 行为说明

- 如果 **8765 端口已有内核在跑**，直接复用，不会重复启动。
- 内核启动超过 25 秒仍未就绪，窗口跳到提示页，给出手动启动命令。
- 关闭窗口时终止它拉起的 Python 进程（复用已有内核时不会误杀）。
- **强杀也不留孤儿**：内核启动时接收 `--parent-pid`，每秒检查父进程是否还在，父进程消失就自行退出。
- 内核目录查找顺序：`ORYX_CORE_DIR` 环境变量 → **安装包资源目录** → 从可执行文件往上找 `core/oryx/__main__.py`。

## 实测结果

| 场景 | 结果 |
|---|---|
| 启动 | 窗口标题 `Oryx`，自动拉起内核 |
| 界面加载 | WebView 与 8765 建立 SSE + 轮询连接 |
| 正常关窗 | 内核随之退出，端口释放 |
| 强杀进程 | 内核 1.5 秒内自行退出 |
| **脱离仓库运行** | 复制 exe + 资源到独立目录，照常启动 ✅ |
| 安装包 | NSIS 1.3 MB，含内核资源 |

## 环境变量

| 变量 | 作用 |
|---|---|
| `ORYX_PORT` | 指定端口，默认 8765 |
| `ORYX_CORE_DIR` | 指定 Python 内核目录（覆盖资源目录） |


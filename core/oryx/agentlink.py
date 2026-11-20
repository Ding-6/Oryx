"""server 侧对 Agent 内核的代理。

职责边界（对齐 Marvis 的「网关 → 内核」）：

    server.py 只做控制面：HTTP、鉴权、静态资源、SSE
    AgentDaemon 做执行面：Agent、工具、索引、终端、调度器

这份代理把 server.py 里原先直接摸 runtime 的地方换成 RPC 调用。
异常语义保持一致：内核抛什么错，代理原样转成 HTTPException（400/404/409）。

内核自愈（第三批）：内核进程崩了以后自动把它拉起来。
对齐 WorkBuddy 的 daemon —— 那边靠系统服务的 Restart=on-failure，
Oryx 没有系统服务，就在自己进程里做一只看门狗：
    每 HEARTBEAT 秒探一次活，探到死了就重启，并发 agent.kernel.restarted 事件。
连续失败超过 MAX_RESTARTS 次就不再重试了 —— 反复起不来的内核说明是
配置或代码问题，继续重试只会刷日志。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .ipc import ConnectionClosed, IpcClient, IpcError
from .prewarm import PrewarmHandle, get_pool

# 看门狗探活间隔
HEARTBEAT_SECONDS = 5.0
# 单次重启的等待上限
RESTART_TIMEOUT = 40.0
# 连续失败多少次后放弃
MAX_RESTARTS = 5
# 两次重启之间的冷却
RESTART_COOLDOWN = 3.0


class AgentLink:
    """Agent 内核的客户端：负责拉起、连接、转发事件、关停、看门狗。"""

    def __init__(self, on_event, config_provider=None) -> None:
        self.on_event = on_event
        # 激活待命内核时要对齐工作区范围。配置的权威在 server（控制面），
        # 所以由外部注入，别在这儿自己 load 一份 —— 那份可能是旧的。
        self._config_provider = config_provider
        # 配置同步的重入守卫（_sync_config 内部要发 RPC）
        self._syncing = False
        # 上次推给内核的配置 mtime。
        #
        # 自己记账的原因见 _sync_config 的说明：
        # Config.refresh() 的「变了没」信号会被 save() 和 HTTP 中间件先消费掉，
        # 不能拿来当判据。
        self._synced_mtime: float | None = None
        self._proc: subprocess.Popen | None = None
        self._client: IpcClient | None = None
        self._port_file: Path | None = None
        self._lock = threading.Lock()
        self._stopped = False
        # 看门狗
        self._watchdog: threading.Thread | None = None
        self._wake = threading.Event()
        self._restart_count = 0
        self._last_restart_at = 0.0
        # 崩溃前的 run 上下文，供重启后恢复
        self._last_run: dict[str, Any] | None = None

    # ---------------------------------------------------------------- 生命周期

    def start(self, python: str | None = None, timeout: float = 40.0) -> None:
        """拉起 Agent 内核进程并连上。

        优先用预热池里的待命内核 —— 它已经把导入链、配置、工具注册表
        全部准备好了，连上就能干活，省掉整段冷启动。
        领不到（没预热 / 预热失败 / 已被别人领走）就走冷启动。

        端口交给内核自己分配（bind 0），再通过临时文件回传 —— 免得和
        HTTP 端口一样去抢一个固定值，多开就撞了。
        """
        with self._lock:
            if self._client is not None:
                return
            self._spawn(python, timeout)
        self._start_watchdog()

    def _spawn(self, python: str | None, timeout: float) -> None:
        """真正拉起进程并连上。调用方必须持有 _lock。"""
        handle = self._claim_prewarmed()
        if handle is not None:
            self._proc = handle.proc
            self._port_file = handle.port_file
            self._client = IpcClient("127.0.0.1", handle.port, on_event=self._on_event)
            # 待命内核还没"转正"：补上激活这一步（启动定时任务 + 对齐工作区）
            self._activate(handle)
            return

        exe = python or sys.executable or "python"
        fd, raw = tempfile.mkstemp(prefix="oryx-agentd-", suffix=".port")
        os.close(fd)
        self._port_file = Path(raw)
        core = Path(__file__).resolve().parent.parent
        self._proc = subprocess.Popen(
            [exe, "-m", "oryx.agentd", "--port", "0", "--port-file", str(self._port_file),
             "--parent-pid", str(os.getpid())],
            cwd=str(core),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        port = self._wait_port(timeout)
        self._client = IpcClient("127.0.0.1", port, on_event=self._on_event)

    def _claim_prewarmed(self) -> PrewarmHandle | None:
        """从预热池领一个待命内核。领不到返回 None（走冷启动）。"""
        try:
            handle = get_pool().claim()
        except Exception:
            return None
        if handle is None:
            return None
        if not handle.alive():
            return None
        # 端口号已经在内存里了，端口文件没用了 —— 删掉，
        # 否则每次启动都在 %TEMP% 里多留一个 .port 文件（实测攒到 150+ 个）。
        handle.release_port_file()
        return handle

    def _activate(self, handle: PrewarmHandle) -> None:
        """把待命内核转正。

        激活失败不致命 —— 内核已经能干活了，只是定时任务没起来。
        这里只记状态，不抛。
        """
        try:
            cfg = self._config_provider() if self._config_provider else None
            if cfg is None:
                from .config import load_config

                cfg = load_config()
            self._client.call(
                "runtime.activate",
                timeout=10.0,
                allowed_roots=cfg.allowed_roots,
                forbidden_paths=cfg.forbidden_paths,
                workspace=cfg.data.get("workspace") or "",
            )
        except Exception as exc:  # noqa: BLE001 — 激活失败只降级，不阻断
            print(f"[oryx] 内核激活未完全成功：{type(exc).__name__}: {exc}", file=sys.stderr)

    def _wait_port(self, timeout: float) -> int:
        assert self._port_file is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                raise IpcError(f"Agent 内核进程已退出（code={self._proc.returncode}）")
            try:
                text = self._port_file.read_text("utf-8").strip()
                if text:
                    port = int(text)
                    # 端口拿到了，端口文件就没用了 —— 删掉。
                    # 不删的话每次启动都在 %TEMP% 里多留一个 .port 文件，
                    # 攒久了 C 盘临时目录里能躺几百个（实测 150+ 个）。
                    try:
                        self._port_file.unlink()
                    except OSError:
                        pass
                    return port
            except OSError:
                pass
            time.sleep(0.1)
        raise IpcError("Agent 内核启动超时")

    def _on_event(self, event: dict[str, Any]) -> None:
        """Agent 内核推来的事件，转发给 server 的 bus 出 SSE。

        内核事件的 seq 是它自己的水位（每个进程独立计数），
        所以这里统一换成 server 自己的序号 —— 前端只认一条单调序列。
        """
        try:
            self.on_event(event)
        except Exception:
            pass

    @property
    def alive(self) -> bool:
        """进程和连接都还在才算活着。"""
        proc_ok = self._proc is not None and self._proc.poll() is None
        with self._lock:
            client_ok = self._client is not None
        return proc_ok and client_ok

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
        self._wake.set()
        if self._watchdog is not None:
            self._watchdog.join(timeout=2.0)
            self._watchdog = None
        self._teardown()
        # 停掉之后把上下文清掉：下次 start() 是全新开始，不该恢复上一轮的任务
        with self._lock:
            self._last_run = None
            self._restart_count = 0

    def _teardown(self) -> None:
        """关掉连接与进程。不碰看门狗。"""
        with self._lock:
            if self._client is not None:
                try:
                    self._client.call("runtime.shutdown", timeout=3.0)
                except IpcError:
                    pass
                self._client.close()
                self._client = None
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            self._proc = None
            if self._port_file is not None:
                try:
                    self._port_file.unlink()
                except OSError:
                    pass
                self._port_file = None

    # ---------------------------------------------------------------- 看门狗

    def _start_watchdog(self) -> None:
        if self._watchdog is not None and self._watchdog.is_alive():
            return
        self._wake.clear()
        self._watchdog = threading.Thread(
            target=self._watch_loop, name="oryx-kernel-watchdog", daemon=True
        )
        self._watchdog.start()

    def _watch_loop(self) -> None:
        while not self._stopped:
            # 睡到下次探活，或者被 stop() 叫醒
            self._wake.wait(timeout=HEARTBEAT_SECONDS)
            if self._stopped:
                return
            try:
                if not self.alive:
                    self._handle_crash()
            except Exception as exc:  # noqa: BLE001 — 看门狗自己不能死
                print(f"[oryx] 内核看门狗异常：{type(exc).__name__}: {exc}", file=sys.stderr)

    def _handle_crash(self) -> None:
        """内核没了：清理残留 → 重启 → 通知前端。"""
        with self._lock:
            if self._stopped:
                return
            now = time.monotonic()
            if now - self._last_restart_at < RESTART_COOLDOWN:
                return
            self._last_restart_at = now
            self._restart_count += 1
            attempt = self._restart_count
            was_running = self._last_run is not None

        if attempt > MAX_RESTARTS:
            self._emit_health("failed", attempt - 1,
                              f"内核连续 {MAX_RESTARTS} 次拉起失败，已停止自动重启")
            return

        code = self._proc.poll() if self._proc is not None else None
        self._emit_health("restarting", attempt,
                          f"内核已退出（code={code}），正在第 {attempt} 次重启")

        # 崩了以后旧连接已是死连接，丢掉再连
        self._teardown()

        try:
            with self._lock:
                self._spawn(None, RESTART_TIMEOUT)
        except IpcError as exc:
            self._emit_health("failed", attempt, f"内核重启失败：{exc}")
            return

        self._restart_count = 0
        self._emit_health("recovered", attempt, "内核已恢复")

        # 崩溃时正在跑任务的话，优先**从断点续跑**。
        # 旧实现是 chat.run(**pending) —— 已经跑完的工具全部重来一遍，
        # 进度条从 0 开始，还可能重复写文件、重复发通知。
        if was_running:
            with self._lock:
                pending = self._last_run
                self._last_run = None
            if pending:
                try:
                    result = self.call("chat.resume", **pending)
                    resumed = bool(result.get("resumed")) if isinstance(result, dict) else False
                    self._emit_health(
                        "resumed", attempt,
                        "已找到中断任务，带已完成步骤从断点继续" if resumed
                        else "没有可续的断点，已按原样重新排队",
                    )
                except Exception:
                    # 老内核没有 chat.resume（或刚重启还没就绪）：
                    # 退回原来的整体重跑，别让「恢复」本身变成新的失败点。
                    try:
                        self.call("chat.run", **pending)
                        self._emit_health("resumed", attempt, "中断的任务已重新排队")
                    except Exception:
                        pass

    def _emit_health(self, phase: str, attempt: int, detail: str) -> None:
        """把内核健康状态发出去，前端据此提示用户。"""
        try:
            self.on_event({
                "type": "agent.kernel.health",
                "phase": phase,
                "attempt": attempt,
                "detail": detail,
            })
        except Exception:
            pass

    # ---------------------------------------------------------------- 调用

    def call(self, method: str, timeout: float = 600.0, **params: Any) -> Any:
        self._sync_config()
        with self._lock:
            client = self._client
        if client is None:
            raise IpcError("Agent 内核未就绪")
        try:
            return client.call(method, timeout=timeout, **params)
        except ConnectionClosed as exc:
            # 连接被内核关闭 = 内核死了/重启了，让看门狗去处理，这里立刻失败
            raise IpcError(f"Agent 内核连接已断开：{exc}") from exc

    async def call_async(self, method: str, timeout: float = 600.0, **params: Any) -> Any:
        self._sync_config()
        with self._lock:
            client = self._client
        if client is None:
            raise IpcError("Agent 内核未就绪")
        try:
            return await client.call_async(method, timeout=timeout, **params)
        except ConnectionClosed as exc:
            raise IpcError(f"Agent 内核连接已断开：{exc}") from exc

    def _sync_config(self) -> None:
        """把控制面的配置变更同步给内核。

        内核是独立进程，它启动时读了一次 config.json 就一直拿着。
        用户在设置页新增/切换 provider 后，server 侧的配置立刻生效，
        内核却还在按启动时那份跑 —— 表现是"明明配好了却报未知 provider"。

        **不能拿 `config.refresh()` 的返回值当判据**
        ----------------------------------------
        那个「配置变了没」的信号会被别人先消费掉：

          · `Config.save()` 写完文件顺手更新了 `_mtime`
          · HTTP 中间件每个请求开头都调 `config.refresh()`

        等这里再去问，mtime 早就一致了 → `refresh()` 返回 False →
        **内核永远收不到重载通知**。
        实测症状：用户在设置里填好 api_key，聊天仍报
        「provider [minimax] 缺少 api_key」—— 内核一直拿着启动时那份配置。

        所以这里**自己记账**：跟「上次推给内核的那个 mtime」比，
        而不是跟 Config 的内部状态比。
        """
        if self._config_provider is None or self._syncing:
            return   # _syncing 守卫：下面要发 RPC，不能又递归回自己
        try:
            cfg = self._config_provider()
        except Exception:
            return

        mtime = getattr(cfg, "disk_mtime", None)
        if mtime is None:
            # 拿不到 mtime（配置没落盘）→ 退回老判据
            refresh = getattr(cfg, "refresh", None)
            if not callable(refresh):
                return
            try:
                changed = refresh()
            except Exception:
                return
            if not changed:
                return
        else:
            if (
                self._synced_mtime is not None
                and abs(mtime - self._synced_mtime) < 1e-6
            ):
                return
            # 让控制面自己先刷到最新 —— 推给内核的 allowed_roots 等才是新的
            refresh = getattr(cfg, "refresh", None)
            if callable(refresh):
                try:
                    refresh()
                except Exception:
                    pass

        self._syncing = True
        try:
            self.call("runtime.config_reload", timeout=10.0)
        except Exception:
            # 同步失败不该挡住本次调用：内核继续用旧配置，
            # 最坏是这次模型调用报"未知 provider"，不是静默错乱
            pass
        finally:
            self._syncing = False
            if mtime is not None:
                self._synced_mtime = mtime

    # ---------------------------------------------------------------- 崩溃现场

    def remember_run(self, **params: Any) -> None:
        """记下当前在跑的任务参数。

        内核崩溃时任务是真的丢了（进程都没了），但**用户意图还在** ——
        记下来，重启后补跑一次，比让用户重新打字强。
        """
        with self._lock:
            self._last_run = dict(params)

    def clear_run(self) -> None:
        with self._lock:
            self._last_run = None

    @property
    def restart_count(self) -> int:
        return self._restart_count
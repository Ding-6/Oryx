"""启动入口：python -m oryx"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser

from .config import CONFIG_PATH, load_config


def _watch_parent(parent_pid: int) -> None:
    """桌面壳被强杀时，内核不能变成孤儿进程一直占着端口。

    不能只依赖 psutil —— 它是可选依赖，缺了就会退化成「检测不了就不自杀」，
    于是留下占着内存和句柄的孤儿进程。所以 Windows 上走内核32 API 直接判断。
    """

    def alive() -> bool:
        if os.name == "nt":
            return _win_alive(parent_pid)
        try:
            import psutil

            return psutil.pid_exists(parent_pid)
        except Exception:
            return True  # 检测不了就不要自杀

    def loop() -> None:
        while True:
            time.sleep(1.5)
            if not alive():
                os._exit(0)

    threading.Thread(target=loop, name="oryx-parent-watch", daemon=True).start()


def _win_alive(pid: int) -> bool:
    """用 OpenProcess 判断进程还在不在，不依赖 psutil。

    PROCESS_QUERY_LIMITED_INFORMATION(0x1000) 是最小权限，
    拿句柄失败（进程已退出或无权访问）一律当作「不在」。
    """
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return False
    try:
        # GetExitCodeProcess：STILL_ACTIVE(259) 表示还活着
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259
    finally:
        kernel32.CloseHandle(handle)


def main() -> None:
    parser = argparse.ArgumentParser(description="Oryx 本地内核")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--parent-pid", type=int, default=None, help="父进程 PID，父进程退出后自动结束")
    args = parser.parse_args()

    # 缓存与临时目录指到 D 盘 —— 必须**尽早**做：
    # tempfile.gettempdir() 第一次调用就缓存结果，之后改环境变量不生效；
    # Playwright 的 driver 也在 launch 时才读 PLAYWRIGHT_BROWSERS_PATH。
    # 桌面端启动时 Rust 侧已经设过一遍，这里是给「直接 python -m oryx」这条路的。
    from .paths import redirect_env

    redirect_env()

    if args.parent_pid:
        _watch_parent(args.parent_pid)

    cfg = load_config()
    host = args.host or cfg.data.get("host", "127.0.0.1")
    port = args.port or int(cfg.data.get("port", 8765))

    print(f"[oryx] 配置文件：{CONFIG_PATH}")
    print(f"[oryx] 模式：{cfg.mode} / provider：{cfg.active_provider_name}")
    print(f"[oryx] 打开 http://{host}:{port}")

    if not args.no_browser:
        threading.Timer(
            1.2, lambda: webbrowser.open(f"http://{host}:{port}")
        ).start()

    # 预热：把内核的冷启动开销挪到跟 fastapi 导入并行的那段时间付掉。
    # 内核进程 0.63s 起来，其中 0.4s 花在导入链上；这段开销每次都一样，
    # 与用户等不等无关 —— 提前起好，用户第一次对话就不用等。
    # 失败不阻塞：领不到预热实例就正常冷启动。
    from .prewarm import get_pool

    get_pool().start_background()

    from .server import serve

    try:
        serve(host, port)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()

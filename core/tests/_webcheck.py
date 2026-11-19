"""浏览器验证脚本的共用工具 —— 端口检查。

为什么要有这个
--------------
踩过的坑：上一个测试的服务进程没退干净，端口还占着，
这次起服务**失败**（端口冲突）但**不报错** ——
浏览器连的是上次的旧服务，测出来的结论全是假的。

更隐蔽的是 CDP 端口：Chrome 进程 terminate 后端口要过一会儿才释放，
下一个脚本用同一个 CDP 端口时，Chrome 可能**没起来**，
而脚本拿到的是「上一个 Chrome 还开着的那个调试端口」——
照样连上、照样出结果、照样是错的。

所以两个端口都要查：

* **服务端口** —— 服务要监听在那儿
* **CDP 端口** —— Chrome 的调试端口

用法
----
```python
from _webcheck import ports_free

if not ports_free(svc_port, cdp_port):
    print("端口被占用 —— 跳过")
    return 0
```
"""
from __future__ import annotations

import socket


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """这个端口有人在**监听**吗。

    用 `bind()` 试，而不是 `connect()` ——
    `connect()` 对 TIME_WAIT 状态的端口有时也会成功，
    于是刚用完的端口被误判成「被占用」，脚本被白白跳过。
    **能 bind = 没人监听**，这才是我们真正关心的。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def ports_free(*ports: int, host: str = "127.0.0.1") -> bool:
    """列出来的端口全都空着吗。

    `0` 会被跳过 —— 那表示「没指定」。
    """
    for p in ports:
        if p and port_in_use(p, host):
            return False
    return True


def describe_busy(*ports: int, host: str = "127.0.0.1") -> str:
    """哪些端口被占了 —— 给人看的说明。"""
    busy = [p for p in ports if p and port_in_use(p, host)]
    return "、".join(str(p) for p in busy)

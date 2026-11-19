"""跨端远控：PC 抓屏推流 + 手机操作回放。"""

from .host import RemoteHost
from .protocol import derive_key, generate_code, normalize_code

__all__ = ["RemoteHost", "derive_key", "generate_code", "normalize_code"]

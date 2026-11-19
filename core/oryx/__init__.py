"""Oryx — 操作系统级 AI 助手的 Python 内核。"""

# 产品版本 —— **全项目的唯一来源**。
#
# 别处（doctor 的版本检查、MCP 的 clientInfo、CHANGELOG）都从这里取，
# 不要各写一份 —— 各写一份就一定会漂移。
#
# CHANGELOG 的最新条目必须与它一致，这条由测试守着
# （tests/test_version_consistency.py）：改了版本忘了写 CHANGELOG，
# 或者反之，测试会红。
__version__ = "1.12.0"

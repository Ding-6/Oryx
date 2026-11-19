"""跨端策略分级：只读 / 可控 / 完整 三级门禁（对齐 Marvis security_policy）。"""

import unittest

from oryx.remote.host import (
    POLICY_CONTROLLED,
    POLICY_FULL,
    POLICY_LABELS,
    POLICY_READONLY,
    RemoteHost,
    normalize_policy,
)


def make_host(policy: int) -> RemoteHost:
    return RemoteHost("wss://relay.example", code="123456", policy=policy)


class NormalizePolicy(unittest.TestCase):
    def test_valid_levels_pass_through(self):
        for level in (POLICY_READONLY, POLICY_CONTROLLED, POLICY_FULL):
            self.assertEqual(normalize_policy(level), level)

    def test_invalid_falls_back_to_controlled(self):
        self.assertEqual(normalize_policy(0), POLICY_CONTROLLED)
        self.assertEqual(normalize_policy(99), POLICY_CONTROLLED)
        self.assertEqual(normalize_policy(None), POLICY_CONTROLLED)
        self.assertEqual(normalize_policy("abc"), POLICY_CONTROLLED)

    def test_string_numbers_accepted(self):
        self.assertEqual(normalize_policy("1"), POLICY_READONLY)


class PolicyGate(unittest.TestCase):
    def test_readonly_blocks_everything(self):
        host = make_host(POLICY_READONLY)
        for event in (
            {"kind": "tap", "x": 0.5, "y": 0.5},
            {"kind": "text", "text": "hi"},
            {"kind": "key", "name": "enter"},
        ):
            allowed, reason = host._input_allowed(event)
            self.assertFalse(allowed)
            self.assertIn("只读", reason)

    def test_controlled_allows_pointer_and_text(self):
        host = make_host(POLICY_CONTROLLED)
        for event in (
            {"kind": "tap", "x": 0.1, "y": 0.2},
            {"kind": "swipe", "x1": 0.1, "y1": 0.1, "x2": 0.9, "y2": 0.9},
            {"kind": "wheel", "delta": -120},
            {"kind": "text", "text": "hello"},
            {"kind": "key", "name": "enter"},
            {"kind": "key", "name": "esc"},
        ):
            allowed, _ = host._input_allowed(event)
            self.assertTrue(allowed, event)

    def test_controlled_blocks_system_keys(self):
        host = make_host(POLICY_CONTROLLED)
        for name in ("win", "ctrl", "alt", "delete", "WIN", "Alt"):
            allowed, reason = host._input_allowed({"kind": "key", "name": name})
            self.assertFalse(allowed, name)
            self.assertIn("系统键", reason)

    def test_full_allows_system_keys(self):
        host = make_host(POLICY_FULL)
        allowed, _ = host._input_allowed({"kind": "key", "name": "win"})
        self.assertTrue(allowed)

    def test_status_exposes_policy(self):
        host = make_host(POLICY_READONLY)
        status = host.status()
        self.assertEqual(status["policy"], POLICY_READONLY)
        self.assertEqual(status["policy_label"], POLICY_LABELS[POLICY_READONLY])
        self.assertEqual(status["inputs_blocked"], 0)

    def test_set_policy_live_change(self):
        host = make_host(POLICY_FULL)
        host.set_policy(POLICY_READONLY)
        allowed, _ = host._input_allowed({"kind": "tap", "x": 0, "y": 0})
        self.assertFalse(allowed)
        host.set_policy(42)  # 非法值保持现状
        self.assertEqual(host.policy, POLICY_READONLY)


if __name__ == "__main__":
    unittest.main()

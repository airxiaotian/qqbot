"""口头指令的解析、权限、确认和风险。不连接 QQ。"""

import tempfile
import time
import unittest
from pathlib import Path

from tools import (
    CONFIRM_SESSION,
    ConfirmStore,
    append_audit,
    effective_risk,
    is_brother,
    parse_decision,
    redact,
    resolve_target,
    sign_request,
    verify_request,
)

GROUP = "qq_napcat:GroupMessage:791213409"

PEOPLE = [
    {"name": "大肥鱼", "aliases": ["吃白饭的大肥鱼"], "qq": "2241444696"},
    {"name": "同名", "aliases": [], "qq": "111"},
    {"name": "同名", "aliases": [], "qq": "222"},
]
GROUPS = [{"id": "791213409", "name": "791213409"}]


class ToolTests(unittest.TestCase):
    def test_parse_chat_and_tool(self):
        self.assertEqual(parse_decision('好的 {"tool":"none","args":{}}'), {"tool": "none", "args": {}})
        parsed = parse_decision('{"tool":"search","args":{"query":"qqbot 教程"},"risk":"low"}')
        self.assertEqual(parsed["tool"], "search")
        self.assertEqual(parsed["args"]["query"], "qqbot 教程")
        self.assertIsNone(parse_decision('{"tool":"shell","args":{"cmd":"rm -rf"}}'))

    def test_permission_and_session(self):
        self.assertTrue(is_brother("273585129"))
        self.assertFalse(is_brother("747092787"))
        self.assertFalse(is_brother("User ID: 273585129"))

    def test_risk_is_not_taken_from_model(self):
        self.assertEqual(effective_risk("computer", {"instruction": "打开浏览器"}, ), "high")
        self.assertEqual(effective_risk("edit_project", {"instruction": "改个错字"}), "high")
        self.assertEqual(effective_risk("restart", {}), "high")
        self.assertEqual(effective_risk("search", {"query": "天气"}), "low")
        self.assertEqual(
            effective_risk("computer", {"instruction": "把日志文件删除"}),
            "delete",
        )
        self.assertEqual(
            effective_risk("send_private", {"target": "1", "targets": ["2"]}),
            "high",
        )
        self.assertEqual(
            effective_risk("send_private", {"target": "999999"}, {"大肥鱼", "2241444696"}),
            "high",
        )

    def test_target_resolution(self):
        one = resolve_target("send_private", "大肥鱼", PEOPLE, GROUPS)
        self.assertTrue(one["ok"])
        self.assertIn("2241444696", one["session"])
        ambiguous = resolve_target("send_private", "同名", PEOPLE, GROUPS)
        self.assertFalse(ambiguous["ok"])
        missing = resolve_target("send_private", "不存在的人", PEOPLE, GROUPS)
        self.assertFalse(missing["ok"])
        group = resolve_target("send_group", "这个群", PEOPLE, GROUPS, "791213409")
        self.assertEqual(group["session"], "qq_napcat:GroupMessage:791213409")
        unknown_group = resolve_target("send_group", "这个群", PEOPLE, [], "")
        self.assertFalse(unknown_group["ok"])

    def test_confirmation_expiry_replay_and_session(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConfirmStore(Path(directory) / "state.json")
            code = store.issue(CONFIRM_SESSION, "273585129", "restart", {}, "重启", 1000)
            wrong_session = store.match(GROUP, "273585129", f"确认 {code}", 1001)
            self.assertEqual(wrong_session["action"], "invalid")
            self.assertIn("私聊", wrong_session["error"])
            expired = store.match(CONFIRM_SESSION, "273585129", f"确认 {code}", 1000 + 601)
            self.assertEqual(expired["action"], "invalid")
            code = store.issue(CONFIRM_SESSION, "273585129", "restart", {}, "重启", 2000)
            wrong = "1111" if code != "1111" else "0000"
            bad = store.match(CONFIRM_SESSION, "273585129", f"确认 {wrong}", 2001)
            self.assertEqual(bad["action"], "invalid")
            good = store.match(CONFIRM_SESSION, "273585129", f"确认 {code.upper()}", 2001)
            self.assertEqual(good["action"], "pending")
            replay = store.match(CONFIRM_SESSION, "273585129", f"确认 {code}", 2002)
            self.assertNotEqual(replay["action"], "pending")
            other = store.issue(GROUP, "273585129", "computer", {"instruction": "打开浏览器"}, "浏览器", 3000)
            stranger = store.match(GROUP, "747092787", f"确认 {other}", 3001)
            self.assertEqual(stranger["action"], "invalid")
            blocked = store.match(GROUP, "273585129", f"确认 {other}", 3001)
            self.assertEqual(blocked["action"], "invalid")
            ok = store.match(CONFIRM_SESSION, "273585129", f"确认 {other}", 3001)
            self.assertEqual(ok["action"], "pending")
            self.assertEqual(ok["pending"]["origin_session"], GROUP)

    def test_hmac_and_redaction(self):
        body = b'{"instruction":"hello"}'
        signature = sign_request("secret", "100", "once", body)
        seen = set()
        self.assertEqual(verify_request("secret", "100", "once", body, signature, 100, seen), "")
        self.assertEqual(verify_request("secret", "100", "once", body, signature, 120, seen), "请求重复")
        self.assertEqual(verify_request("secret", "100", "later", body, signature, 1000, set()), "请求过期")
        self.assertIn("已隐藏", redact("cursor_abcdefghijklmnopqrstuvwxyz"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            append_audit(path, {"tool": "computer", "args": "sk-abcdefghijklmnopqrstuvwxyz"})
            saved = path.read_text(encoding="utf-8")
            self.assertNotIn("sk-abcdefghij", saved)
            self.assertIn("已隐藏", saved)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
import _guard  # noqa: F401  ← 맨 먼저: 진짜 설정 폴더·금고·학교 주소(우리학교_설정.json) 대신 시험용
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from riro_merit import divot


class DivotConnection(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.profile = self.root / "browser"
        self.profile.mkdir()
        self.p = self.enterContext(patch("playwright.sync_api.sync_playwright")).return_value.__enter__.return_value
        self.launch = self.enterContext(patch.object(divot, "_launch"))
        self.enterContext(patch.object(divot.C, "load", return_value={}))
        self.enterContext(patch.object(divot.C, "browser_dir", return_value=self.profile))
        self.enterContext(patch.object(divot.C, "base_dir", return_value=self.root))

    def browser(self, *signed, response=None, url=divot.C.DIVOT_URL):
        page = Mock(url=url)
        page.is_closed.side_effect = [False] * len(signed) + [True]
        page.evaluate.side_effect = signed
        button = page.get_by_role.return_value
        button.count.return_value = 1
        button.is_visible.return_value = True
        if response is not None:
            reply = Mock(ok=True, url="https://script.google.com/macros/s/fake/exec")
            reply.request.method = "POST"
            reply.json.return_value = response
            page.goto.side_effect = lambda *args, **kwargs: page.on.call_args.args[1](reply)
        return Mock(pages=[page]), page, button

    def test_saved_session_connects_without_visible_browser(self):
        (self.profile / "Local State").touch()
        context, page, _ = self.browser(True, response={"students": [{"id": "99999"}]})
        self.launch.return_value = context

        result = divot.connect()

        self.assertTrue(result["session_reused"])
        self.launch.assert_called_once_with(self.p, headless=True)
        page.get_by_role.assert_not_called()
        context.close.assert_called_once()
        schema = (self.root / "divot_schema.json").read_text(encoding="utf-8")
        self.assertIn("str:digits5", schema)
        self.assertNotIn("99999", schema)

    def test_expired_session_opens_google_sign_in_once(self):
        (self.profile / "Local State").touch()
        expired, _, _ = self.browser(True, response={"success": False})
        visible, page, button = self.browser(False, False, True, response={"students": []})
        self.launch.side_effect = [expired, visible]

        result = divot.connect()

        self.assertTrue(result["ok"])
        self.assertFalse(result["session_reused"])
        self.assertEqual(self.launch.call_args_list, [call(self.p, headless=True), call(self.p, headless=False)])
        expired.close.assert_called_once()
        visible.close.assert_called_once()
        page.get_by_role.assert_called_once_with("button", name=divot.GOOGLE_SIGN_IN, exact=True)
        button.click.assert_called_once()

    def test_first_connection_opens_only_visible_browser(self):
        context, _, button = self.browser(False)
        self.launch.return_value = context

        result = divot.connect()

        self.assertEqual(result["code"], "E_NOT_SIGNED_IN")
        self.launch.assert_called_once_with(self.p, headless=False)
        button.click.assert_called_once()
        self.assertFalse((self.root / "divot_schema.json").exists())

    def test_redirect_does_not_inspect_session_or_click(self):
        context, page, _ = self.browser(True, url="https://example.invalid/")
        self.launch.return_value = context

        result = divot.connect()

        self.assertEqual(result["code"], "E_NOT_SIGNED_IN")
        page.evaluate.assert_not_called()
        page.get_by_role.assert_not_called()

    def test_cancelling_background_check_does_not_open_sign_in(self):
        (self.profile / "Local State").touch()
        cancel = threading.Event()
        context, page, _ = self.browser(False)
        page.wait_for_timeout.side_effect = lambda _: cancel.set()
        self.launch.return_value = context

        self.assertEqual(divot.connect(cancel=cancel)["code"], "CANCELLED")

        self.launch.assert_called_once_with(self.p, headless=True)
        context.close.assert_called_once()
        self.assertFalse((self.root / "divot_schema.json").exists())


if __name__ == "__main__":
    unittest.main()

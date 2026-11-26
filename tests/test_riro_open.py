# -*- coding: utf-8 -*-
"""바탕화면 «문자» → 리로스쿨 «알림문자» 메뉴 누르기 — 가짜 화면으로만 (진짜 로그인 없음). 크롬을 창 없이 띄운다."""
import _guard  # noqa: F401  ← 맨 먼저
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from riro_merit import riro_open as O  # noqa: E402

MENU = """<!doctype html><meta charset=utf-8><ul>
<li style="display:none"><span onclick="location.hash='/wrong.php'">알림문자</span></li>
<li><span onclick="location.hash='/sms.php'">알림문자</span></li>
<li><span onclick="location.hash='/record.php'">상벌점</span></li></ul>"""


class Menu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel="chrome", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def page(self, html):
        pg = self.browser.new_page()
        self.addCleanup(pg.close)
        pg.set_content(html)
        return pg

    def test_sms_menu_is_alimmunja(self):
        self.assertEqual(O.MENUS["sms"], {"label": "알림문자", "path": "/sms.php"})

    def test_clicks_the_visible_menu_and_reaches_sms_page(self):
        pg = self.page(MENU)
        self.assertTrue(O.open_menu(pg, "알림문자", "/sms.php"))
        self.assertIn("/sms.php", pg.url)                     # 숨은 같은 이름 메뉴(/wrong.php)는 누르지 않았다

    def test_missing_menu_is_reported(self):
        pg = self.page("<ul><li><span>상벌점</span></li></ul>")
        self.assertFalse(O.open_menu(pg, "알림문자", "/sms.php"))

    def test_merit_only_logs_in_and_opens_score_form(self):
        """상벌점 창 [상벌점 화면 열기 (로그인만)] — 학번 채우기와 같은 길로 점수 입력 화면을 열고, 그 화면엔 앞으로 꺼내기만 한다."""
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        page = mock.MagicMock()
        opened = []

        def fake_riro_page(p, R):
            opened.append(R.__name__)
            return mock.MagicMock(), page

        pw = mock.MagicMock()
        with mock.patch("riro_merit.fill_unsaved.riro_page", fake_riro_page), \
                mock.patch("playwright.sync_api.sync_playwright", return_value=pw), \
                mock.patch.object(O.C, "require_school"), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(O.main(["merit"]), 0)
        self.assertEqual(opened, ["riro_merit.riro_site"])
        self.assertEqual(page.method_calls, [mock.call.bring_to_front()])   # 학번·글자를 넣거나 누르는 일 없음
        self.assertEqual(out.getvalue().strip().splitlines()[-1], "READY")

    def test_bad_name_fails_without_opening_anything(self):
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(O.main(["모르는메뉴"]), 2)
        self.assertIn("FAIL", out.getvalue())


if __name__ == "__main__":
    unittest.main()

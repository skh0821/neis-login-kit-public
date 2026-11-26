# -*- coding: utf-8 -*-
"""미보관 학번 채우기 — 가짜 디벗·리로스쿨 화면으로만 (진짜 학생·로그인 없음). 크롬을 창 없이 띄운다."""
import _guard  # noqa: F401  ← 맨 먼저: 진짜 설정 폴더·금고 항목을 못 건드리게
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from riro_merit import fill_unsaved as F  # noqa: E402

DIVOT = """<!doctype html><meta charset=utf-8>
<input type=date class=session-date-input value="2026-11-10">
<button class="segmented-button active">전체 학년 (1~3학년)</button><button class="segmented-button">1학년</button>
<button class="segmented-button">전체 (0)</button><button id=tab class="segmented-button active">미보관 (0)</button>
<button id=cabletab class="segmented-button">케이블 미연결 (0)</button><button id=etctab class="segmented-button">기타 (0)</button>
<h3>미보관 디벗 학생 명렬표 (0명)</h3>
<table><thead><tr><th>반</th><th>미보관 학생</th></tr></thead><tbody><tr><td>1반</td><td>가나다 외</td></tr></tbody></table>
<table id=list><thead><tr><th>학년</th><th>학급</th><th>번호</th><th>학생 성명</th><th>보관 상태</th><th>사유 및 특이사항</th></tr></thead><tbody></tbody></table>
<script>
const data = {'2026-11-09': [[1, 2, 22, '미보관', '9일 하교 후  디벗 미반납'], [1, 5, 1, '미보관', '9일 하교 후 디벗 미반납'],
                             [3, 11, 30, '미보관', '가상 분실 신고'], [2, 3, 4, '케이블 미연결', '충전 케이블  미연결'],
                             [1, 10, 5, '케이블 미연결', '다른 케이블 사유'], [2, 1, 1, '기타', '기타 사유']]};
const box = document.querySelector('.session-date-input');
function render() {
  const rows = (data[box.value] || []);
  document.querySelector('#list tbody').innerHTML = rows.length
    ? rows.map(r => `<tr><td>${r[0]}학년</td><td>${r[1]}반</td><td>${r[2]}번</td><td><span>가상이름</span></td><td><span>${r[3]}</span></td><td>${r[4]}</td></tr>`).join('')
    : '<tr><td colspan=6>미보관 학생이 없습니다</td></tr>';
  document.getElementById('tab').textContent = '미보관 (' + rows.filter(r => r[3] === '미보관').length + ')';
  document.getElementById('cabletab').textContent = '케이블 미연결 (' + rows.filter(r => r[3] === '케이블 미연결').length + ')';
  document.getElementById('etctab').textContent = '기타 (' + rows.filter(r => r[3] === '기타').length + ')';
}
box.addEventListener('change', () => setTimeout(render, 300)); render();
</script>"""

RIRO = """<!doctype html><meta charset=utf-8><form name=form1>
""" + "".join(
    f'<div><input name="s_num[{i}]" id=s_numi maxlength=5 onkeyup="chk_s_num({i})" value="{"20101" if i == 1 else ""}">'
    f'<input name="s_name[{i}]" value="{"기존" if i == 1 else ""}"><input name="point[{i}]" value=0></div>' for i in range(4)) + """
</form><script>
function chk_s_num(n) {
  const v = document.getElementsByName('s_num[' + n + ']')[0].value;
  if (v.length !== 5) return;
  if (v === '99999') { alert('해당 학번의 학생이 없습니다'); return; }
  setTimeout(() => { document.getElementsByName('s_name[' + n + ']')[0].value = '학생'; }, 150);
}
</script>"""


class FakePages(unittest.TestCase):
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

    def test_reads_only_unsaved_and_builds_sid_from_grade_class_number(self):
        res = F.read_unsaved(self.page(DIVOT), "2026-11-09")
        self.assertEqual(res["sids"], ["10222", "10501", "31130"])      # 3학년 11반 30번 → 31130
        self.assertEqual(res["tab_count"], 3)
        self.assertEqual(res["other_status"], 3)                        # «케이블 미연결»·«기타» 는 넣지 않는다
        self.assertEqual(res["bad"], 0)
        # 사유는 넣는 학생 중 «맨 위 한 줄»만 (빈칸 여러 개는 하나로) — 나머지 줄의 사유는 읽지 않는다
        self.assertEqual(res["reason"], "9일 하교 후 디벗 미반납")
        self.assertNotIn("reasons", res)

    def test_cable_tab_reads_only_cable_students(self):
        res = F.read_unsaved(self.page(DIVOT), "2026-11-09", "cable")
        self.assertEqual(res["sids"], ["20304", "11005"])                # 2학년 3반 4번 · 1학년 10반 5번
        self.assertEqual(res["tab_count"], 2)
        self.assertEqual(res["other_status"], 4)                        # 미보관 3 · 기타 1 은 넣지 않는다
        self.assertEqual(res["reason"], "충전 케이블 미연결")             # 케이블 학생 중 맨 위 줄

    def google_page(self, html, url="https://accounts.google.com/o/oauth2/v2/auth?x=1"):
        ctx = self.browser.new_context()
        self.addCleanup(ctx.close)
        pg = ctx.new_page()
        pg.route("**/*", lambda r: r.fulfill(body=html, content_type="text/html; charset=utf-8"))
        pg.goto(url)
        return ctx, pg

    def test_google_chooser_clicks_the_only_saved_account(self):
        ctx, pg = self.google_page("<title>선택</title><div data-identifier='t@example.invalid' onclick=\"document.title='눌림'\">가상 계정</div>"
                                   "<div>다른 계정 사용</div>")
        self.assertTrue(F.pick_only_google_account(ctx))
        self.assertEqual(pg.title(), "눌림")

    def test_google_chooser_with_two_accounts_is_left_to_teacher(self):
        ctx, pg = self.google_page("<title>선택</title><div data-identifier='a@example.invalid' onclick=\"document.title='눌림'\">가</div>"
                                   "<div data-identifier='b@example.invalid' onclick=\"document.title='눌림'\">나</div>")
        self.assertFalse(F.pick_only_google_account(ctx))
        self.assertEqual(pg.title(), "선택")

    def test_google_chooser_only_on_the_real_google_address(self):
        ctx, pg = self.google_page("<title>선택</title><div data-identifier='t@example.invalid' onclick=\"document.title='눌림'\">가</div>",
                                   url="https://accounts.google.com.example.invalid/x")
        self.assertFalse(F.pick_only_google_account(ctx))
        self.assertEqual(pg.title(), "선택")

    def test_empty_day_gives_no_sids(self):
        res = F.read_unsaved(self.page(DIVOT), "2026-11-10")
        self.assertEqual(res["sids"], [])
        self.assertEqual(res["tab_count"], 0)

    def test_fills_only_blank_cards_and_reports_left(self):
        pg = self.page(RIRO)
        with redirect_stdout(io.StringIO()):
            r = F.fill_blanks(pg, ["10222", "99999", "10501", "31130"], pick_wait_ms=2000)
        self.assertEqual(r["blank_cards"], 3)                            # 1번 카드는 이미 차 있다
        self.assertEqual(r["filled"], 3)                                 # 못 찾은 99999 칸을 비우고 다음 학번에 썼다
        self.assertEqual(r["left"], ["99999"])
        values = pg.evaluate("() => [...document.querySelectorAll('input[name^=\"s_num[\"]')].map(e => e.value)")
        self.assertEqual(values, ["10222", "20101", "10501", "31130"])   # 이미 있던 카드는 그대로

    def test_more_students_than_blank_cards(self):
        pg = self.page(RIRO)
        with redirect_stdout(io.StringIO()):
            r = F.fill_blanks(pg, ["10101", "10102", "10103", "10104", "10105"], pick_wait_ms=2000)
        self.assertEqual(r["filled"], 3)
        self.assertEqual(r["left"], ["10104", "10105"])                  # 상벌점 창이 «남은 학번» 안내창을 띄운다

    def test_clipboard_round_trip_and_kept_out_of_history(self):
        import ctypes
        from ctypes import wintypes
        u32, k32 = ctypes.WinDLL("user32"), ctypes.WinDLL("kernel32")
        u32.GetClipboardData.restype, u32.GetClipboardData.argtypes = wintypes.HANDLE, [wintypes.UINT]
        k32.GlobalLock.restype, k32.GlobalLock.argtypes = ctypes.c_void_p, [wintypes.HANDLE]
        k32.GlobalUnlock.argtypes = [wintypes.HANDLE]
        u32.OpenClipboard.argtypes = [wintypes.HWND]
        u32.RegisterClipboardFormatW.restype, u32.RegisterClipboardFormatW.argtypes = wintypes.UINT, [wintypes.LPCWSTR]

        def read_text():
            if not u32.OpenClipboard(None):
                self.skipTest("클립보드를 다른 프로그램이 쓰는 중")
            try:
                h = u32.GetClipboardData(13)
                if not h:
                    return None
                p = k32.GlobalLock(h)
                try:
                    return ctypes.wstring_at(p)
                finally:
                    k32.GlobalUnlock(h)
            finally:
                u32.CloseClipboard()

        # 선생님 클립보드를 망가뜨리지 않게: 비어 있거나 «글자»만 있을 때만 하고, 끝나면 그 글자로 되돌린다
        other = u32.CountClipboardFormats() and not u32.IsClipboardFormatAvailable(13)
        if other:
            self.skipTest("클립보드에 글자 아닌 것(그림 등)이 있어 건너뜀")
        before = read_text()
        try:
            self.assertTrue(F.set_clipboard("가상 사유 문구 — 9일 하교 후 ✓"))
            self.assertEqual(read_text(), "가상 사유 문구 — 9일 하교 후 ✓")
            self.assertTrue(u32.IsClipboardFormatAvailable(u32.RegisterClipboardFormatW("CanIncludeInClipboardHistory")))
            self.assertTrue(u32.IsClipboardFormatAvailable(u32.RegisterClipboardFormatW("CanUploadToCloudClipboard")))
        finally:
            if before is not None:
                F.set_clipboard(before)
            elif u32.OpenClipboard(None):
                u32.EmptyClipboard()
                u32.CloseClipboard()

    def test_never_presses_save(self):
        src = Path(F.__file__).read_text(encoding="utf-8")
        for word in ("저장", "submit", "point[", "ment[", "pw"):
            hits = [ln for ln in src.splitlines() if word in ln and ("click" in ln or "fill(" in ln or "press" in ln)]
            self.assertEqual(hits, [], word)


class PlainChrome(unittest.TestCase):
    """리로스쿨·디벗 크롬을 «평범하게 따로» 띄우고 연결만 붙였다 끊는다 (11-10: 붙어 있는 동안 확인 창 자동 취소 → 일괄적용 먹통)."""

    def test_plain_chrome_flags_webdriver_and_dialog_after_disconnect(self):
        import json
        import tempfile
        import time
        import urllib.request
        import psutil
        from playwright.sync_api import sync_playwright
        port = 9398
        with tempfile.TemporaryDirectory() as d:
            prof = Path(d)
            try:
                with sync_playwright() as p:
                    browser, page, fresh = F.attach(p, prof, port, headless=True, name="시험")   # 없으면 따로 띄운다
                    self.assertTrue(fresh)
                    cmd = [" ".join(pr.info.get("cmdline") or []) for pr in psutil.process_iter(["name", "cmdline"])
                           if (pr.info.get("name") or "").lower() == "chrome.exe" and str(prof) in " ".join(pr.info.get("cmdline") or [])
                           and "--type=" not in " ".join(pr.info.get("cmdline") or [])]
                    self.assertTrue(cmd)
                    for c in cmd:
                        self.assertNotIn("--no-sandbox", c)
                        self.assertNotIn("AutomationControlled", c)
                    self.assertFalse(page.evaluate("navigator.webdriver"))     # 자동화 표시 꺼짐 (숨기는 설정 없이)
                    page.evaluate("() => { document.title = '기다리는 중'; setTimeout(() => { document.title = '결과=' + confirm('일괄적용?'); }, 1500); }")
                time.sleep(3.5)                                                  # 연결을 끊은 뒤 확인 창이 뜬다
                self.assertTrue(F.cdp_alive(port))                                # 창은 남는다
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as r:
                    titles = [t.get("title") for t in json.load(r) if t.get("type") == "page"]
                self.assertIn("기다리는 중", titles)                              # 자동 취소되지 않고 선생님 답을 기다린다
            finally:
                for pr in psutil.process_iter(["name", "cmdline"]):
                    if (pr.info.get("name") or "").lower() == "chrome.exe" and f"--remote-debugging-port={port}" in " ".join(pr.info.get("cmdline") or []):
                        try:
                            pr.kill()
                        except Exception:
                            pass
                time.sleep(1)


if __name__ == "__main__":
    unittest.main()

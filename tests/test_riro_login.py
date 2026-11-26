# -*- coding: utf-8 -*-
"""리로스쿨 로그인 코드(riro_site.login) — 진짜 사이트·진짜 계정 없이, 로그인 화면을 흉내 낸 가짜 page 로.

확인하는 것: [로그인] 은 한 번만 누른다 · 비밀번호 거절이면 잠그고 다시 시도하지 않는다 · 추가 인증(OTP)은 넣지 않고 기다린다 ·
비밀번호는 «넣기 직전»에 금고에서 꺼낸다 · 결과·출력에 값이 안 나온다 · 입력 오류 원문을 버린다.
"""
import _guard  # noqa: F401  ← 맨 먼저: 진짜 설정 폴더·금고 항목을 못 건드리게
import importlib
import io
import json
import secrets
import unittest
from contextlib import redirect_stdout
from unittest import mock

import secure_store as S

TSVC = "riro-merit-test/login.invalid"
SIGNIN = "https://myschool.riroschool.kr/user.php?action=signin"


class Loc:
    def __init__(self, page, sel):
        self.p, self.sel = page, sel
        self.first = self

    def count(self):
        return 1 if self.p.visible(self.sel) else 0

    def is_visible(self, timeout=None):
        return bool(self.p.visible(self.sel))

    def wait_for(self, state=None, timeout=None):
        if not self.p.visible(self.sel):
            raise TimeoutError("not visible")

    def fill(self, v):
        self.p.events.append(("fill", self.sel))
        if self.sel == "#pw":
            self.p.pw_seen = v
            if self.p.scenario == "fill_error":
                raise Exception(f'Locator.fill: Target closed — fill("{v}")')     # 진짜 playwright 처럼 값이 섞인 오류

    def click(self):
        self.p.events.append(("click", self.sel))
        self.p.clicked()

    def locator(self, sel, **kw):
        return Loc(self.p, "login_button" if "로그인" in sel or self.sel == "#pw" else sel)

    def inner_text(self, timeout=None):
        return "아이디 또는 비밀번호가 일치하지 않습니다." if self.p.state == "alert" else ""


class Page:
    """리로스쿨 로그인 화면 흉내. state: form → (click) → in / alert / human → in."""

    def __init__(self, scenario):
        self.scenario, self.events, self.t, self.pw_seen = scenario, [], 0.0, None
        self.state = "in" if scenario == "already" else "form"
        self.url = "https://myschool.riroschool.kr/home.php" if scenario == "already" else SIGNIN
        self.human_until = None

    # 화면에 보이는 것
    def visible(self, sel):
        if self.state == "form":
            return sel in ("#id", "#pw", "login_button", "button.button_normal")
        if self.state == "alert":
            return sel in ("#id", "#pw", ".btn_alert_confirm", "login_button")
        if self.state == "human":
            return sel == "#otp_input"
        if self.state == "in":
            return sel == "logout"
        return None

    def clicked(self):
        if self.scenario == "ok":
            self.state, self.url = "in", "https://myschool.riroschool.kr/home.php"
        elif self.scenario == "bad_pw":
            self.state = "alert"
        elif self.scenario in ("otp_then_ok", "otp_timeout"):
            self.state, self.url = "human", "https://myschool.riroschool.kr/otp.php"
            self.human_until = self.t + 30 if self.scenario == "otp_then_ok" else None

    # playwright 흉내
    def on(self, ev, fn):
        pass

    def goto(self, url, **kw):
        self.events.append(("goto", url))

    def wait_for_timeout(self, ms):
        self.t += ms / 1000
        if self.state == "human" and self.human_until is not None and self.t >= self.human_until:
            self.state, self.url = "in", "https://myschool.riroschool.kr/home.php"     # 선생님이 창에서 OTP 를 넣었다

    def is_closed(self):
        return False

    def locator(self, sel, **kw):
        if sel.startswith("iframe"):
            return Loc(self, "__none__")
        return Loc(self, "login_button" if kw.get("has_text") == "로그인" else sel)

    def get_by_text(self, text, exact=False):
        return Loc(self, "logout" if text == "로그아웃" else text)


class RiroLogin(unittest.TestCase):
    def setUp(self):
        import riro_merit.config as RC
        import riro_merit.riro_site as R
        self.RC = importlib.reload(RC)
        self.R = importlib.reload(R)
        self.p_svc = mock.patch.object(self.RC, "RIRO_SERVICE", TSVC)
        self.p_svc.start()
        self.pw = "Fk" + secrets.token_hex(6)
        with S.SecretBuffer() as b:
            for ch in self.pw:
                b.append(ch)
            S.vault_write_buffer(TSVC, "fake-teacher", b, replace=True)
        cfg = self.RC.load()
        cfg["login_lock"] = {}
        self.RC.save(cfg)

    def tearDown(self):
        S.vault_delete(TSVC)
        self.p_svc.stop()

    def run_login(self, scenario):
        page = Page(scenario)
        out = io.StringIO()
        with mock.patch.object(self.R, "_clock", lambda: page.t), redirect_stdout(out):
            res = self.R.login(page)
        blob = json.dumps(res, ensure_ascii=False) + out.getvalue()
        self.assertNotIn(self.pw, blob)                                 # 결과·출력에 비밀번호 없음
        return page, res

    def clicks(self, page):
        return sum(1 for e in page.events if e[0] == "click")

    def test_no_school_address_stops_before_vault_and_typing(self):
        """우리학교_설정.json 에 학교 주소가 없으면 금고도 안 열고 아무것도 안 넣는다."""
        with mock.patch.object(self.RC, "SCHOOL_HOST", ""), \
                mock.patch.object(self.RC, "credential_ticket", side_effect=AssertionError("금고를 열었다")):
            page, res = self.run_login("ok")
        self.assertEqual(res, {"ok": False, "code": "E_NO_SCHOOL"})
        self.assertEqual(self.clicks(page), 0)

    def test_success_clicks_once_and_reveals_just_before_fill(self):
        calls = []
        real = S.VaultSecret.reveal

        def spy(self_):
            calls.append(len(page_holder[0].events) if page_holder else None)
            return real(self_)
        page_holder = []
        page = Page("ok")
        page_holder.append(page)
        with mock.patch.object(S.VaultSecret, "reveal", spy), mock.patch.object(self.R, "_clock", lambda: page.t), \
                redirect_stdout(io.StringIO()):
            res = self.R.login(page)
        self.assertEqual(res, {"ok": True, "code": "OK"})
        self.assertEqual(self.clicks(page), 1)
        self.assertEqual(page.pw_seen, self.pw)                         # 가짜 화면에는 들어갔다 (금고에서 꺼내서)
        self.assertEqual(len(calls), 1)                                 # 금고에서 딱 한 번
        kinds = [e[0] for e in page.events]
        self.assertLess(kinds.index("goto"), calls[0] + 1)              # 화면을 연 «뒤»에 꺼냈다
        self.assertEqual(page.events[calls[0]], ("fill", "#pw"))        # 꺼낸 바로 다음이 비밀번호 칸

    def test_bad_password_locks_and_never_retries(self):
        page, res = self.run_login("bad_pw")
        self.assertEqual(res["code"], "E_BAD_CREDENTIALS")
        self.assertEqual(self.clicks(page), 1)
        page2, res2 = self.run_login("ok")
        self.assertEqual(res2["code"], "E_LOGIN_LOCKED_UNTIL_CHANGED")  # 같은 비밀번호로는 다시 안 한다
        self.assertEqual(page2.events, [])                              # 화면도 안 연다
        cfg = self.RC.load()
        cfg["cred_rev"] = int(cfg["cred_rev"]) + 1                      # 선생님이 비밀번호를 새로 저장한 것과 같다
        self.RC.save(cfg)
        _, res3 = self.run_login("ok")
        self.assertTrue(res3["ok"])

    def test_extra_auth_is_left_to_the_teacher(self):
        page, res = self.run_login("otp_then_ok")
        self.assertEqual(res["code"], "OK_AFTER_EXTRA_AUTH")
        self.assertEqual(self.clicks(page), 1)
        self.assertFalse(any(e == ("fill", "#otp_input") for e in page.events))   # OTP 는 넣지 않는다
        page, res = self.run_login("otp_timeout")
        self.assertEqual(res["code"], "E_EXTRA_AUTH_TIMEOUT")
        self.assertGreaterEqual(page.t, self.R.WAIT_HUMAN_SEC)

    def test_fill_error_text_is_dropped(self):
        page, res = self.run_login("fill_error")
        self.assertEqual(res, {"ok": False, "code": "E_FILL"})
        self.assertEqual(self.clicks(page), 0)                          # 넣다 실패하면 누르지도 않는다

    def test_already_signed_in_types_nothing(self):
        page, res = self.run_login("already")
        self.assertEqual(res["code"], "OK_ALREADY_SIGNED_IN")
        self.assertFalse(any(e[0] in ("fill", "click") for e in page.events))

    def test_redirect_elsewhere_never_opens_vault_or_types(self):
        """로그인 화면이 다른 곳으로 넘어가면(리디렉션) 아이디도 안 넣고 금고도 안 연다."""
        for evil in ("https://evil.example/user.php?action=signin",
                     "https://myschool.riroschool.kr.evil.example/user.php?action=signin",
                     "http://myschool.riroschool.kr/user.php?action=signin"):
            page = Page("ok")
            page.url = evil

            def goto(url, **kw):
                page.events.append(("goto", url))
                page.url = evil                                    # 열자마자 다른 곳으로 넘어간 것처럼
            page.goto = goto
            with mock.patch.object(S.VaultSecret, "reveal", side_effect=AssertionError("금고를 열면 안 된다")), \
                    mock.patch.object(self.R, "_clock", lambda: page.t), redirect_stdout(io.StringIO()):
                res = self.R.login(page)
            self.assertEqual(res["code"], "E_ORIGIN", evil)
            self.assertFalse(any(e[0] in ("fill", "click") for e in page.events), evil)

    def test_no_credentials(self):
        S.vault_delete(TSVC)
        page, res = self.run_login("ok")
        self.assertEqual(res["code"], "E_NO_CREDENTIALS")
        self.assertEqual(page.events, [])


if __name__ == "__main__":
    unittest.main()

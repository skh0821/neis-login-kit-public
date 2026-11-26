# -*- coding: utf-8 -*-
"""본인 확인 방식 (2026-11-26) — 처음 한 번 Windows Hello / 잠금 비밀번호를 고르고, 그다음부터는 고른 방식으로만.
창은 띄우지 않는다: _ui_choose · _ui_fields · _ui_info · _hello 를 가짜로 바꿔 끼운다. 진짜 금고는 쓰지 않는다
(%LOCALAPPDATA% 는 _guard 가 임시 폴더로 돌린다 — 기록 파일은 그 안에만 생긴다)."""
import _guard  # noqa: F401  ← 맨 먼저
import unittest
from pathlib import Path
from unittest import mock

import windows_hello as H


class FakeRoot:
    def withdraw(self):
        pass


class Script:
    """창 흉내 — 미리 정한 답을 차례로 돌려준다."""

    def __init__(self, choose=(), fields=(), hello=()):
        self.choose, self.fields, self.hello = list(choose), list(fields), list(hello)
        self.info, self.asked = [], []

    def patches(self):
        return [mock.patch.object(H, "_ui_choose", side_effect=lambda r, t: self.choose.pop(0)),
                mock.patch.object(H, "_ui_fields", side_effect=self._field),
                mock.patch.object(H, "_ui_info", side_effect=lambda r, t, text, error=False: self.info.append((error, text))),
                mock.patch.object(H, "_hello", side_effect=lambda r, t, m: self.hello.pop(0))]

    def _field(self, root, title, text, labels):
        self.asked.append(tuple(labels))
        return self.fields.pop(0)


def run(script, **kw):
    ps = script.patches()
    for p in ps:
        p.start()
    try:
        return H.show_verification(FakeRoot(), "본인 확인", "시험", **kw)
    finally:
        for p in ps:
            p.stop()


class UnlockChoice(unittest.TestCase):
    def setUp(self):
        H.reset_unlock()
        self.assertTrue(str(H._unlock_file()).startswith(_guard._TMP))   # 진짜 설정 폴더가 아님

    tearDown = setUp

    def test_first_time_password_then_password_every_time(self):
        s = Script(choose=["password"], fields=[["abc", "abc"], ["secret-1", "secret-2"], ["secret-1", "secret-1"]])
        self.assertEqual(run(s), "OK")
        self.assertEqual(H.current_method(), "password")
        self.assertEqual(s.info[0][0], True)                        # 너무 짧음
        self.assertEqual(s.info[1][0], True)                        # 두 칸이 다름
        raw = H._unlock_file().read_bytes()
        self.assertNotIn(b"secret-1", raw)                          # 비밀번호 글자는 파일에 없다
        self.assertNotIn(b"password", raw)                          # 방식 이름도 암호화돼 있다
        H.forget_verification()
        s2 = Script(fields=[["wrong!!"], ["secret-1"]])
        self.assertEqual(run(s2), "OK")                             # 다음부터는 고르지 않고 비밀번호만
        self.assertEqual(s2.asked, [("잠금 비밀번호",), ("잠금 비밀번호",)])
        self.assertIn("남은 기회 4번", s2.info[0][1])

    def test_first_time_hello_then_hello_every_time(self):
        s = Script(choose=["hello"], hello=["OK"])
        self.assertEqual(run(s), "OK")
        self.assertEqual(H.current_method(), "hello")
        H.forget_verification()
        s2 = Script(hello=["OK"])
        self.assertEqual(run(s2), "OK")
        self.assertEqual((s2.asked, s2.choose), ([], []))           # 비밀번호 칸도 고르기 창도 없이 Hello 만

    def test_hello_not_available_asks_to_pick_password(self):
        s = Script(choose=["hello", "password"], hello=["E_HELLO_SETUP"], fields=[["secret-1", "secret-1"]])
        self.assertEqual(run(s), "OK")
        self.assertEqual(H.current_method(), "password")
        self.assertIn("Windows Hello가 준비돼 있지 않아요", s.info[0][1])

    def test_cancel_on_first_time_saves_nothing(self):
        self.assertEqual(run(Script(choose=[None])), "CANCELLED")
        self.assertIsNone(H.current_method())
        self.assertFalse(H.recently_verified())

    def test_five_wrong_passwords_lock_for_five_minutes(self):
        H.save_unlock(H.make_password_record("secret-1"))
        s = Script(fields=[["x1"], ["x2"], ["x3"], ["x4"], ["x5"]])
        self.assertEqual(run(s), "E_LOCKED")
        s2 = Script(fields=[["secret-1"]])
        self.assertEqual(run(s2), "E_LOCKED")                       # 잠긴 동안은 맞아도 안 연다
        self.assertFalse(H.recently_verified())
        rec = H.load_unlock()
        code, rec = H.password_attempt(rec, "secret-1", now=rec["locked_until"] + 1)
        self.assertEqual(code, "OK")                                # 5분이 지나면 다시 된다

    def test_choose_again_needs_current_method_first(self):
        H.save_unlock(H.make_password_record("secret-1"))
        s = Script(fields=[["wrong!!"], None])
        self.assertEqual(run(s, choose_again=True), "CANCELLED")    # 지금 방식으로 확인 못 하면 바꾸지 못한다
        self.assertEqual(H.current_method(), "password")
        s2 = Script(fields=[["secret-1"]], choose=["hello"], hello=["OK"])
        self.assertEqual(run(s2, choose_again=True), "OK")
        self.assertEqual(H.current_method(), "hello")

    def test_grace_skips_any_method(self):
        H.save_unlock(H.make_password_record("secret-1"))
        H.remember_verification()
        self.assertEqual(run(Script()), "OK")                       # 30분 안이면 묻지 않는다

    def test_broken_record_points_to_reset(self):
        H._unlock_file().parent.mkdir(parents=True, exist_ok=True)
        H._unlock_file().write_bytes(b"not dpapi")
        s = Script()
        self.assertEqual(run(s), "E_UNLOCK_BROKEN")
        self.assertIn("본인확인_다시고르기", s.info[0][1])
        self.assertEqual(H.main(["--reset"]), 0)
        self.assertIsNone(H.current_method())

    def test_desktop_widget_exit_code_only_on_success(self):
        for result, expected in (("OK", H.DESKTOP_VERIFIED_EXIT), ("CANCELLED", 1), ("E_LOCKED", 1)):
            with self.subTest(result=result), mock.patch("tkinter.Tk"), \
                    mock.patch.object(H, "show_verification", return_value=result) as sv:
                self.assertEqual(H.main(["나이스"]), expected)
                self.assertFalse(sv.call_args.kwargs.get("choose_again"))
        with mock.patch("tkinter.Tk"), mock.patch.object(H, "show_verification", return_value="OK") as sv:
            self.assertEqual(H.main(["--change"]), H.DESKTOP_VERIFIED_EXIT)
            self.assertTrue(sv.call_args.kwargs["choose_again"])
        self.assertEqual(H.main(["--모름"]), 1)

    def test_unlock_file_does_not_break_settings_encryption(self):
        """11-26 시험에서 잡은 것: 본인 확인이 공용 암호화 함수 설정을 바꿔, 같은 프로그램의 설정 저장이 깨졌다."""
        import secure_store as S
        H.save_unlock(H.make_password_record("secret-1"))           # 본인 확인 기록을 먼저 쓰고
        self.assertEqual(H.current_method(), "password")
        p = Path(_guard._TMP) / "probe" / "x.dpapi"
        S.write_json(p, {"a": 1}, "neis-automation/probe")          # 그다음 설정 암호화가 그대로 되는지
        self.assertEqual(S.read_json(p, "neis-automation/probe"), {"a": 1})
        H.save_unlock(H.make_password_record("secret-2"))           # 거꾸로 순서도
        self.assertEqual(H.current_method(), "password")

    def test_reset_file_exists_in_kit(self):
        cmd = Path(H.__file__).with_name("본인확인_다시고르기.cmd").read_text(encoding="utf-8")
        self.assertIn("windows_hello.py\" --reset", cmd)


if __name__ == "__main__":
    unittest.main()

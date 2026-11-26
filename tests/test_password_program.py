# -*- coding: utf-8 -*-
"""비밀번호 전용 짧은 프로그램 · 입력 경로 · «로그인 직전에 읽기» — 가상 비밀번호로만.

창은 화면에 띄우지 않는다 (숨긴 창 / --hidden-test). 진짜 금고 항목은 건드리지 않는다 (시험용 서비스 이름).
"""
import _guard  # noqa: F401  ← 맨 먼저: 진짜 설정 폴더·금고 항목을 못 건드리게
import asyncio
import importlib
import io
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
KIT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(KIT))
from _fake_env import FAKE_CONFIG, FakeEnv  # noqa: E402

import secure_store as S  # noqa: E402

TSVC = "neis-automation-test/example.invalid"


def fake_pw() -> str:
    return "Fk" + secrets.token_hex(6) + "!x"          # 매번 다른 가상 비밀번호 (ASCII)


class Ev:
    def __init__(self, char="", keysym=""):
        self.char, self.keysym = char, keysym or char


def type_into(se, text):
    for ch in text:
        se.key(Ev(ch))


class PasswordWindowFlows(unittest.TestCase):
    """작은 창의 저장 · 취소 · 오류 길 (숨긴 창, 키 입력은 처리기에 직접)."""

    def setUp(self):
        for u in (FAKE_CONFIG["cert_name"], "evpn"):          # 지난 시험이 중간에 멈춰 남긴 가상 항목부터 정리
            S.vault_delete(TSVC, u)
        S.vault_delete(TSVC + "/riro")
        self.env = FakeEnv().__enter__()
        self.env.write_config()
        import secure_password
        self.P = importlib.reload(secure_password)
        self.p1 = mock.patch.object(self.env.T, "KEYRING_SERVICE", TSVC)
        self.p1.start()
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        for u in (FAKE_CONFIG["cert_name"], "evpn"):
            try:
                S.vault_delete(TSVC, u)
            except Exception:
                pass
        self.p1.stop()
        self.env.__exit__(None, None, None)

    def test_save_writes_vault_wipes_buffer_and_entry_never_holds_password(self):
        pw = fake_pw()
        w = self.P.PasswordWindow(self.root, "cert", idle_sec=60)
        type_into(w.se, pw)
        shown = w.se.e.get()
        self.assertEqual(shown, "•" * len(pw))                      # 입력칸에는 ● 뿐 — 진짜 글자가 없다
        self.assertNotIn(pw, shown)
        w.do_save()
        self.assertEqual((w.status, w.code), ("saved", "OK"))
        self.assertTrue(w.buf.is_wiped())                           # 버퍼는 0 으로 덮였다
        import keyring
        self.assertEqual(keyring.get_password(TSVC, FAKE_CONFIG["cert_name"]), pw)
        self.assertNotIn(pw, json.dumps({"s": w.status, "c": w.code}))

    def test_cancel_and_escape_paths_write_nothing(self):
        for how in ("cancel", "close"):
            root = tk.Tk()
            root.withdraw()
            w = self.P.PasswordWindow(root, "evpn", idle_sec=60)
            type_into(w.se, fake_pw())
            (w.cancel if how == "cancel" else root.protocol("WM_DELETE_WINDOW") and w.cancel)()
            self.assertEqual((w.status, w.code), ("cancelled", "CANCELLED"))
            self.assertTrue(w.buf.is_wiped())
            self.assertFalse(S.vault_exists(TSVC, "evpn"))

    def test_vault_unavailable_fails_without_writing(self):
        w = self.P.PasswordWindow(self.root, "cert", idle_sec=60)
        type_into(w.se, fake_pw())
        with mock.patch.object(S, "check_vault", side_effect=S.SecureError("E_VAULT_BACKEND")):
            w.do_save()
        self.assertEqual((w.status, w.code), ("failed", "E_VAULT_BACKEND"))
        self.assertTrue(w.buf.is_wiped())
        self.assertFalse(S.vault_exists(TSVC, FAKE_CONFIG["cert_name"]))

    def test_unexpected_error_text_never_reaches_status(self):
        pw = fake_pw()
        w = self.P.PasswordWindow(self.root, "cert", idle_sec=60)
        type_into(w.se, pw)
        with mock.patch.object(S, "vault_write_buffer", side_effect=RuntimeError(f"boom value={pw}")):
            w.do_save()
        self.assertEqual(w.status, "failed")
        self.assertEqual(w.code, "E_RuntimeError")                  # 오류 «종류»만 — 원문(값 포함)은 버린다
        self.assertNotIn(pw, w.code)
        self.assertTrue(w.buf.is_wiped())

    def test_empty_backspace_nonascii_and_paste_blocked(self):
        w = self.P.PasswordWindow(self.root, "cert", idle_sec=60)
        w.do_save()                                                 # 빈 비밀번호 → 저장 안 하고 창 유지
        self.assertEqual(w.status, "cancelled")
        self.assertTrue(self.root.winfo_exists())
        type_into(w.se, "abc")
        w.se.key(Ev("", "BackSpace"))
        self.assertEqual(len(w.buf), 2)
        self.assertEqual(w.se.e.get(), "••")
        w.se.key(Ev("한"))
        self.assertTrue(w.buf.has_non_ascii())
        self.assertIn("한/영", w.msg.cget("text"))
        for seq in ("<<Paste>>", "<Control-v>", "<Shift-Insert>", "<<Copy>>"):
            self.assertTrue(w.se.e.bind(seq), seq)                  # 붙여넣기·복사 막는 처리기가 걸려 있다
        w.cancel()
        self.assertTrue(w.buf.is_wiped())

    def test_settings_failure_after_vault_save_is_reported_separately(self):
        """금고에는 저장됐는데 뒤 설정 갱신만 실패 → «저장됨 + W_SETTINGS» 로 따로 알린다 (실패로 뭉뚱그리지 않는다)."""
        import riro_merit.config as RC
        RC = importlib.reload(RC)
        with mock.patch.object(RC, "RIRO_SERVICE", TSVC + "/riro"), \
                mock.patch.object(RC, "save", side_effect=OSError("disk full")):
            w = self.P.PasswordWindow(self.root, "riro", idle_sec=60)
            w.e_id.delete(0, "end")
            w.e_id.insert(0, "fake-id-9")
            pw = fake_pw()
            type_into(w.se, pw)
            w.do_save()
            self.assertEqual(w.status, "saved")
            self.assertEqual(w.code, "W_SETTINGS:E_OSError")
            import keyring
            self.assertEqual(keyring.get_password(TSVC + "/riro", "fake-id-9"), pw)   # 금고에는 들어갔다
            S.vault_delete(TSVC + "/riro")

    def test_riro_target_one_credential_and_lock_reset(self):
        import riro_merit.config as RC
        RC = importlib.reload(RC)
        with mock.patch.object(RC, "RIRO_SERVICE", TSVC + "/riro"):
            cfg = RC.load()
            cfg["login_lock"] = {"rev": 0, "code": "E_BAD_CREDENTIALS"}
            RC.save(cfg)
            for uid in ("fake-id-1", "fake-id-2"):
                root = tk.Tk()
                root.withdraw()
                w = self.P.PasswordWindow(root, "riro", idle_sec=60)
                w.e_id.delete(0, "end")
                w.e_id.insert(0, uid)
                type_into(w.se, fake_pw())
                w.do_save()
                self.assertEqual(w.status, "saved")
            self.assertEqual(S.vault_username(TSVC + "/riro"), "fake-id-2")
            import keyring
            self.assertIsNone(keyring.get_password(TSVC + "/riro", "fake-id-1"))   # 옛 아이디 항목이 안 남는다
            c = RC.load()
            self.assertEqual((c["cred_rev"], c["login_lock"]), (2, {}))
            S.vault_delete(TSVC + "/riro")


class ProgramExitsQuickly(unittest.TestCase):
    """진짜 하위 프로세스: 시간 초과(입력 없음)면 바로 끝나고 상태만 남긴다. request() 가 상태 파일을 지운다."""

    def test_child_timeout_exits_with_status_only(self):
        sf = Path(tempfile.gettempdir()) / f"pwtest_{secrets.token_hex(4)}.json"
        t0 = time.time()
        r = subprocess.run([sys.executable, "-X", "utf8", str(KIT / "secure_password.py"), "child", "--target", "cert",
                            "--status-file", str(sf), "--idle", "1", "--hidden-test"], capture_output=True, text=True,
                           timeout=60)
        took = time.time() - t0
        self.assertEqual(r.returncode, 2)
        self.assertLess(took, 20)
        st = json.loads(sf.read_text(encoding="utf-8"))
        self.assertEqual(st, {"target": "cert", "status": "cancelled", "code": "E_TIMEOUT"})
        self.assertEqual(r.stdout.strip(), "")
        sf.unlink()

    def test_request_returns_status_and_cleans_up(self):
        import secure_password as P
        real = subprocess.Popen

        def popen(cmd, **kw):
            return real(cmd + ["--idle", "1", "--hidden-test"], **kw)
        before = set(Path(tempfile.gettempdir()).glob("neis_pw_status_*"))
        with mock.patch.object(P.subprocess, "Popen", side_effect=popen):
            res = P.request("cert", timeout=60)
        self.assertEqual(res, {"target": "cert", "status": "cancelled", "code": "E_TIMEOUT"})
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("neis_pw_status_*")), before)   # 상태 파일도 안 남는다


class ConsoleInput(unittest.TestCase):
    def feed(self, keys):
        it = iter(keys)
        return mock.patch("msvcrt.getwch", side_effect=lambda: next(it))

    def test_enter_backspace_and_escape(self):
        with S.SecretBuffer() as b, self.feed(list("abX") + ["\x08", "c", "\r"]), redirect_stdout(io.StringIO()) as out:
            self.assertTrue(S.read_secret_console("pw: ", b))
            self.assertEqual(b.reveal_str(), "abc")
        self.assertNotIn("abc", out.getvalue())                      # 화면에 아무 글자도 안 찍힌다
        self.assertTrue(b.is_wiped())
        with S.SecretBuffer() as b, self.feed(list("secret") + ["\x1b"]), redirect_stdout(io.StringIO()):
            self.assertFalse(S.read_secret_console("pw: ", b))       # Esc = 취소
            self.assertTrue(b.is_wiped())


class FakeLoc:
    """playwright 처럼 행동하되, fill 오류 메시지에 «넣으려던 값»을 넣는다 (실제 playwright 가 그렇다)."""

    def __init__(self, fail_fill=False, asyncmode=False):
        self.fail_fill, self.first = fail_fill, self

    async def wait_for(self, **kw):
        return None

    async def click(self, **kw):
        return None

    async def fill(self, v, **kw):
        if self.fail_fill and v:
            raise Exception(f"Locator.fill: Target closed\n  - fill(\"{v}\")")

    async def press(self, *a, **kw):
        return None

    async def press_sequentially(self, v, **kw):
        if self.fail_fill:
            raise Exception(f"Locator.press_sequentially: detached \"{v}\"")

    async def count(self):
        return 0

    async def is_visible(self):
        return False


class LoginPathsDropErrorText(unittest.TestCase):
    """로그인 직전에 금고에서 읽고, 입력 오류 원문(값 포함)은 출력에 나오지 않는다."""

    def setUp(self):
        self.pw = fake_pw()
        b = S.SecretBuffer()
        for ch in self.pw:
            b.append(ch)
        S.vault_write_buffer(TSVC, "fakecert", b)
        b.wipe()
        self.ticket = S.VaultSecret(TSVC, "fakecert")

    def tearDown(self):
        S.vault_delete(TSVC, "fakecert")

    def test_ticket_holds_no_value(self):
        self.assertFalse(hasattr(self.ticket, "__dict__"))
        self.assertNotIn(self.pw, repr(self.ticket) + str(self.ticket) + f"{self.ticket}")
        self.assertEqual(self.ticket.reveal(), self.pw)            # 필요할 때만 금고에서

    def _main_page(self, url):
        class Page:
            fills = []

            async def wait_for_selector(self, *a, **kw):
                raise TimeoutError("not logged in")

            def get_by_role(self, role, name=""):
                return FakeLoc(fail_fill=(role == "textbox"))

            def locator(self, sel):
                return FakeLoc()

            async def title(self):
                return "t"
        p = Page()
        p.url = url
        return p

    def test_neis_login_test_opens_no_control_port(self):
        import neis_session as N
        self.assertFalse(any("remote-debugging-port" in a for a in N.chrome_args(login_test=True)))
        self.assertTrue(any("remote-debugging-port" in a for a in N.chrome_args(login_test=False)))
        self.assertLessEqual(N.KEEP_OPEN_MIN, 15)

    def test_evpn_login_once_refuses_wrong_origin(self):
        import evpn
        typed = []

        class Loc(FakeLoc):
            async def press_sequentially(self, v, **kw):
                typed.append(1)

        class Page:
            url = "https://evpn.sen.go.kr.evil.example/"

            async def evaluate(self, *a, **kw):
                return "Yes"

            def locator(self, sel):
                return Loc()

            async def wait_for_timeout(self, ms):
                return None
        with mock.patch.object(S.VaultSecret, "reveal", side_effect=AssertionError("금고를 열면 안 된다")):
            with self.assertRaises(RuntimeError) as c:
                asyncio.run(evpn._login_once(Page(), "fake-evpn-id", self.ticket))
        self.assertIn("E_ORIGIN", str(c.exception))
        self.assertEqual(typed, [])                                  # 아이디도 치지 않았다

    def test_evpn_login_once_drops_error_text(self):
        import evpn

        class Page:
            url = "https://evpn.sen.go.kr/"                          # 주소는 맞고, 입력에서 오류가 나는 경우

            async def evaluate(self, *a, **kw):
                return "Yes"

            def locator(self, sel):
                return FakeLoc(fail_fill=(sel == "#password"))

            async def wait_for_timeout(self, ms):
                return None

            async def click(self, *a, **kw):
                return None
        with self.assertRaises(RuntimeError) as c:
            asyncio.run(evpn._login_once(Page(), "fake-evpn-id", self.ticket))
        self.assertIn("E_EVPN_FILL", str(c.exception))
        self.assertNotIn(self.pw, str(c.exception))
        self.assertTrue(c.exception.__suppress_context__)          # 원래 오류(값 포함)는 표시되지 않게 끊었다

    def test_scrubber_holds_only_hash(self):
        sc = S.Scrubber()
        sc.add(self.pw)
        self.assertNotIn(self.pw, repr(sc))
        self.assertFalse(any(self.pw in repr(x) for x in sc._items))
        self.assertEqual(sc.scrub(f"a {self.pw} b"), "a **** b")


class MemoryResidue(unittest.TestCase):
    """하위 프로세스 메모리에 가상 비밀번호가 «몇 군데» 남는지 센다 (개수만 · 덤프 없음).
    보장 확인이 아니라 «관찰» — 결과는 MEMCOUNT 줄로 남긴다."""

    def run_mode(self, mode, svc):
        from _memscan import count_in_process
        pw = fake_pw()
        if mode == "reveal":
            b = S.SecretBuffer()
            for ch in pw:
                b.append(ch)
            S.vault_write_buffer(svc, "fake-user", b)
            b.wipe()
        p = subprocess.Popen([sys.executable, "-X", "utf8", str(HERE / "memprobe_child.py"), mode, svc],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=False)
        try:
            if mode != "reveal":
                p.stdin.write(pw.encode() + b"\n")
                p.stdin.flush()
            line = p.stdout.readline().strip()
            self.assertEqual(line, b"READY")
            # ★ 가상환경의 python.exe 는 «진짜 파이썬을 띄우는 런처»다 — 그 아래 실제 인터프리터 프로세스를 센다
            import psutil
            kids = psutil.Process(p.pid).children(recursive=True)
            target = kids[-1].pid if kids else p.pid
            res = count_in_process(target, [pw.encode("utf-16-le"), pw.encode("ascii")])
            self.assertGreater(res["scanned_mb"], 15, "실제 인터프리터를 못 찾았다")
        finally:
            try:
                p.stdin.write(b"q")
                p.stdin.flush()
            except OSError:
                pass
            p.wait(timeout=20)
            S.vault_delete(svc, "fake-user")
        print(f"MEMCOUNT mode={mode} utf16={res['hits'][0]} ascii={res['hits'][1]} scanned={res['scanned_mb']}MB", flush=True)
        return res

    def test_buffer_vs_str_vs_reveal(self):
        self.run_mode("buffer", TSVC + "/mem1")
        self.run_mode("str", TSVC + "/mem2")
        self.run_mode("reveal", TSVC + "/mem3")


if __name__ == "__main__":
    unittest.main()

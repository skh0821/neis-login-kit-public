# -*- coding: utf-8 -*-
"""보안 설정창 — 숨긴 창으로 칸을 채우고 [저장] 을 눌러 본다 (가짜 %LOCALAPPDATA% · 시험용 금고 이름 · 가상 값).

2026-11-09: 설정창에는 비밀번호 칸이 없다. [암호 넣기] 는 비밀번호 전용 짧은 창(secure_password.request)을 띄우고
상태만 받는다 → 여기서는 그 창 대신 «가상 비밀번호를 버퍼로 금고에 넣고 상태만 돌려주는» 가짜를 끼운다.
화면 캡처는 하지 않는다. 결과 파일에 값이 안 들어가는지 확인한다.
"""
import _guard  # noqa: F401  ← 맨 먼저: 진짜 설정 폴더·금고 항목을 못 건드리게
import importlib
import json
import secrets
import sys
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fake_env import FAKE_CONFIG, FakeEnv  # noqa: E402

KIT = Path(__file__).resolve().parent.parent
TEST_SVC = "neis-automation-test/example.invalid"


def load_window_module():
    spec = importlib.util.spec_from_file_location("secure_settings_win", KIT / "secure_settings.pyw")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def pump(root, until, sec=10):
    end = time.time() + sec
    while time.time() < end and not until():
        root.update()
        time.sleep(0.02)


class SettingsWindow(unittest.TestCase):
    def setUp(self):
        self.addCleanup(self.clean)
        self.clean()

    @staticmethod
    def clean():
        import secure_store as S
        for u in (FAKE_CONFIG["cert_name"], "evpn"):          # 시험이 중간에 멈춰도 가상 항목이 안 남게
            S.vault_delete(TEST_SVC, u)
        S.vault_delete(TEST_SVC + "/riro")

    def test_no_password_fields_and_status_only_from_password_program(self):
        with FakeEnv() as E:
            E.write_config()
            import riro_merit.config as RC
            import secure_password as P
            import secure_store as S
            RC = importlib.reload(RC)
            P = importlib.reload(P)
            W = load_window_module()
            W.T, W.RC = E.T, RC
            res_file = E.root / "result.json"
            fakes = {}

            def fake_request(target, console=False, timeout=600):
                """비밀번호 전용 창 흉내 — 가상 비밀번호를 버퍼로 금고에 넣고 «상태만» 돌려준다."""
                pw = "Fk" + secrets.token_hex(6)
                fakes[target] = pw
                with S.SecretBuffer() as b:
                    for ch in pw:
                        b.append(ch)
                    P.save(target, "fake-riro-id" if target == "riro" else None, b)
                return {"target": target, "status": "saved", "code": "OK"}

            with mock.patch.object(E.T, "KEYRING_SERVICE", TEST_SVC), \
                    mock.patch.object(RC, "RIRO_SERVICE", TEST_SVC + "/riro"), \
                    mock.patch.object(P, "request", side_effect=fake_request):
                root = tk.Tk()
                root.withdraw()
                try:
                    R = W.Result(res_file)
                    app = W.App(root, R, "neis")
                    for attr in ("v_certpw", "v_evpw", "v_rid", "v_rpw"):
                        self.assertFalse(hasattr(app, attr), attr)              # 오래 떠 있는 창에는 비밀번호 칸이 없다
                    for attr in ("v_contact", "v_ws", "v_we", "v_line", "v_hr", "v_grade", "v_class", "b_cal"):
                        self.assertFalse(hasattr(app, attr), attr)              # 로그인 전용 — 복무·담임 칸 없음
                    app.v_access.set("evpn")
                    app.toggle_evpn()
                    app.v_evmode.set("separate")
                    app.v_evid.delete(0, "end")
                    app.v_evid.insert(0, "fake-evpn-id-1")
                    app.save_neis()
                    # 빠진 값(EVPN 아이디 없음)이면 저장하지 않는다
                    app.v_evid.delete(0, "end")
                    before = E.T.load_merged()
                    app.save_neis()
                    self.assertEqual(E.T.load_merged()["evpn_user"], before["evpn_user"])
                    # 비밀번호 — 전용 창(가짜)을 띄우고 상태만 받는다
                    for target, btn, hint, item in (("cert", app.b_certpw, app.h_certpw, "cert_password"),
                                                    ("evpn", app.b_evpw, app.h_evpw, "evpn_password"),
                                                    ("riro", app.b_rcred, app.h_rpw, "riro_credentials")):
                        n = len(R.events)
                        app.ask_password(target, btn, hint, item)
                        pump(root, lambda: len(R.events) > n)
                    app.close()
                finally:
                    try:
                        root.destroy()
                    except tk.TclError:
                        pass
                m = E.T.load_merged()
                self.assertEqual(m["access_mode"], "evpn")
                self.assertEqual(m["evpn_password_mode"], "separate")
                self.assertEqual(m["evpn_user"], "fake-evpn-id-1")
                import keyring
                self.assertEqual(keyring.get_password(TEST_SVC, FAKE_CONFIG["cert_name"]), fakes["cert"])
                self.assertEqual(keyring.get_password(TEST_SVC, "evpn"), fakes["evpn"])
                self.assertEqual(S.vault_username(TEST_SVC + "/riro"), "fake-riro-id")
                self.assertNotIn("fake-evpn-id-1", E.T.CONFIG_FILE.read_text(encoding="utf-8"))  # EVPN 아이디는 암호화 파일로
                out = res_file.read_text(encoding="utf-8")
                for v in list(fakes.values()) + ["fake-evpn-id-1", "fake-riro-id", FAKE_CONFIG["cert_name"]]:
                    self.assertNotIn(v, out)
                r = json.loads(out)
                self.assertEqual(r["status"], "saved")
                items = {(e["item"], e["action"], e["code"]) for e in r["events"]}
                for it in ("cert_password", "evpn_password", "riro_credentials"):
                    self.assertIn((it, "saved", "OK"), items)
                self.assertTrue(any(e["item"] == "neis_settings" and e["code"] == "OK" for e in r["events"]))
                for u in (FAKE_CONFIG["cert_name"], "evpn"):
                    S.vault_delete(TEST_SVC, u)
                S.vault_delete(TEST_SVC + "/riro")

    def test_cancel_from_password_program_changes_nothing(self):
        with FakeEnv() as E:
            E.write_config()
            import riro_merit.config as RC
            import secure_password as P
            W = load_window_module()
            W.T, W.RC = E.T, RC
            with mock.patch.object(E.T, "KEYRING_SERVICE", TEST_SVC), \
                    mock.patch.object(RC, "RIRO_SERVICE", TEST_SVC + "/riro"), \
                    mock.patch.object(P, "request", return_value={"target": "cert", "status": "cancelled", "code": "CANCELLED"}):
                root = tk.Tk()
                root.withdraw()
                try:
                    R = W.Result(E.root / "r.json")
                    app = W.App(root, R, "neis")
                    app.ask_password("cert", app.b_certpw, app.h_certpw, "cert_password")
                    pump(root, lambda: any(e["item"] == "cert_password" for e in R.events))
                    app.close()
                finally:
                    try:
                        root.destroy()
                    except tk.TclError:
                        pass
            r = json.loads((E.root / "r.json").read_text(encoding="utf-8"))
            self.assertIn(("cert_password", "cancelled", "CANCELLED"), {(e["item"], e["action"], e["code"]) for e in r["events"]})
            self.assertNotEqual(r["status"], "saved")

    def test_vault_unavailable_never_opens_password_program(self):
        with FakeEnv() as E:
            E.write_config()
            import secure_password as P
            import secure_store as S
            W = load_window_module()
            W.T = E.T
            with mock.patch.object(S, "check_vault", side_effect=S.SecureError("E_VAULT_BACKEND")), \
                    mock.patch.object(P, "request") as req:
                root = tk.Tk()
                root.withdraw()
                try:
                    R = W.Result(E.root / "r.json")
                    app = W.App(root, R, "neis")
                    app.ask_password("cert", app.b_certpw, app.h_certpw, "cert_password")
                    root.update()
                    app.close()
                finally:
                    try:
                        root.destroy()
                    except tk.TclError:
                        pass
                req.assert_not_called()
            r = json.loads((E.root / "r.json").read_text(encoding="utf-8"))
            self.assertIn("E_VAULT_BACKEND", {e["code"] for e in r["events"]})
            self.assertNotEqual(r["status"], "saved")


class DivotClose(unittest.TestCase):
    def test_close_destroys_window_and_cancels_child(self):
        W = load_window_module()
        app = W.App.__new__(W.App)
        app.root = tk.Tk()
        app.root.withdraw()
        app.R = W.Result(None)
        app.divot_lifetime = None
        app.b_divot = tk.Button(app.root)
        app.say = mock.Mock()

        worker = """
from riro_merit import divot

def wait_for_close(*, cancel):
    return {"ok": cancel.wait(5), "code": "CANCEL_TEST"}

divot.connect = wait_for_close
raise SystemExit(divot.main(["connect", "--cancel-on-eof"]))
"""
        start = W.start_tool
        processes = []

        def start_fake(args, *, stdin):
            self.assertIn("--cancel-on-eof", args)
            processes.append(start(["-c", worker], stdin=stdin))
            return processes[-1]

        try:
            with mock.patch.object(W, "start_tool", side_effect=start_fake):
                app.connect_divot(use_saved=True)
            self.assertEqual(app.b_divot.cget("state"), "disabled")
            app.close()
            with self.assertRaises(tk.TclError):
                app.root.winfo_exists()
            self.assertEqual(processes[0].wait(timeout=8), 0)
        finally:
            if app.divot_lifetime is not None:
                app.close()


class SettingsAuthentication(unittest.TestCase):
    def test_desktop_verifier_grants_only_after_windows_hello_success(self):
        import windows_hello as H

        for result, expected in (("OK", H.DESKTOP_VERIFIED_EXIT), ("CANCELLED", 1), ("E_HELLO", 1)):
            with self.subTest(result=result), \
                    mock.patch.object(tk, "Tk") as tk_root, \
                    mock.patch.object(H, "show_verification", return_value=result) as show:
                self.assertEqual(H.main(["나이스"]), expected)
                self.assertIn("나이스", show.call_args.args[2])
                tk_root.return_value.destroy.assert_called_once()

    def test_hello_success_is_remembered_for_thirty_minutes_only(self):
        import windows_hello as H

        self.addCleanup(H.forget_verification)
        self.assertFalse(H.recently_verified())
        with mock.patch.object(H, "_clock", return_value=(1_000_000, 5_000.0)):
            H.remember_verification()
        self.assertTrue(H._grace_file().is_relative_to(Path(_guard._TMP)))  # 가짜 %LOCALAPPDATA% 안에만
        cases = ((1_000_000 + 29 * 60_000, 5_000.0, True),    # 29분 뒤 — 지문 없이 통과
                 (1_000_000 + 31 * 60_000, 5_000.0, False),   # 31분 뒤 — 다시 묻는다
                 (999_000, 5_000.0, False),                   # 시간이 거꾸로
                 (1_000_000 + 60_000, 9_000.0, False))        # 재부팅 뒤 (부팅 시각이 다름)
        for tick, boot, expected in cases:
            with self.subTest(tick=tick, boot=boot), mock.patch.object(H, "_clock", return_value=(tick, boot)):
                self.assertIs(H.recently_verified(), expected)
        H.forget_verification()
        self.assertFalse(H.recently_verified())

    def test_desktop_and_settings_skip_hello_within_grace(self):
        import windows_hello as H

        with mock.patch.object(H, "recently_verified", return_value=True), \
                mock.patch.object(tk, "Tk") as tk_root, \
                mock.patch.object(H, "request_verification") as request:
            self.assertEqual(H.main(["나이스"]), H.DESKTOP_VERIFIED_EXIT)
            tk_root.assert_not_called()
            root = mock.Mock()
            self.assertEqual(H.show_verification(root, "보안 설정 잠금", "확인"), "OK")
            request.assert_not_called()
            root.withdraw.assert_called_once()

    def test_cancel_or_failure_keeps_settings_and_auto_connect_closed(self):
        W = load_window_module()
        for code in ("CANCELLED", "E_HELLO"):
            with self.subTest(code=code), \
                    mock.patch.object(sys, "argv", ["secure_settings.pyw", "--tab", "riro", "--connect-divot"]), \
                    mock.patch.object(W.tk, "Tk") as tk_root, \
                    mock.patch.object(W, "unlock_settings", return_value=code), \
                    mock.patch.object(W, "App") as app:
                W.main()
                app.assert_not_called()
                tk_root.return_value.after_idle.assert_not_called()
                tk_root.return_value.destroy.assert_called_once()

    def test_success_opens_settings_and_runs_requested_connection(self):
        W = load_window_module()
        with mock.patch.object(sys, "argv", ["secure_settings.pyw", "--tab", "riro", "--connect-divot"]), \
                mock.patch.object(W.tk, "Tk") as tk_root, \
                mock.patch.object(W, "unlock_settings", return_value="OK") as unlock, \
                mock.patch.object(W, "App") as app:
            root = tk_root.return_value
            root.winfo_reqwidth.return_value = root.winfo_reqheight.return_value = 400
            root.winfo_screenwidth.return_value = root.winfo_screenheight.return_value = 1000
            W.main()
            unlock.assert_called_once_with(root)
            self.assertEqual(app.call_args.args[2], "riro")
            root.after_idle.call_args.args[0]()
            app.return_value.connect_divot.assert_called_once_with(use_saved=True)


if __name__ == "__main__":
    unittest.main()

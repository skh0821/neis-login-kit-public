# -*- coding: utf-8 -*-
"""secure_migrate · teacher_config 나눠 저장 — 가짜 %LOCALAPPDATA% 와 가상 값으로만."""
import _guard  # noqa: F401  ← 맨 먼저: 진짜 설정 폴더·금고 항목을 못 건드리게
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fake_env import FAKE_CONFIG, FakeEnv  # noqa: E402


def quiet(fn, *a):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fn(*a)
    return rc, buf.getvalue()


class TeacherConfigMigration(unittest.TestCase):
    def test_run_old_helper_keeps_cert_name_compat(self):
        with FakeEnv(helper_new=False) as E:
            E.write_config()
            rc, out = quiet(E.M.run)
            self.assertEqual(rc, 0, out)
            for v in ("000가상교사000", "010-0000-0000", "fake-evpn-id"):
                self.assertNotIn(v, out)                                    # 출력에 값이 없다
            plain = json.loads(E.T.CONFIG_FILE.read_text(encoding="utf-8"))
            self.assertIn("_secure", plain)
            self.assertEqual(plain["cert_name"], FAKE_CONFIG["cert_name"])   # 옛 도우미 호환
            for k in ("contact", "evpn_user", "school_name"):
                self.assertNotIn(k, plain)
            merged = E.T.load_merged()
            for k, v in FAKE_CONFIG.items():
                self.assertEqual(merged[k], v, k)
            self.assertNotIn("010-0000-0000", E.T.PRIVATE_FILE.read_text(encoding="utf-8"))
            self.assertEqual(E.T.load_config()["contact"], "010-0000-0000")

    def test_run_new_helper_removes_cert_name_from_plain(self):
        with FakeEnv(helper_new=True) as E:
            E.write_config()
            rc, out = quiet(E.M.run)
            self.assertEqual(rc, 0, out)
            plain = json.loads(E.T.CONFIG_FILE.read_text(encoding="utf-8"))
            self.assertNotIn("cert_name", plain)
            self.assertEqual(E.T.load_merged()["cert_name"], FAKE_CONFIG["cert_name"])

    def test_rerun_is_idempotent_and_restore_works(self):
        with FakeEnv() as E:
            E.write_config()
            quiet(E.M.run)
            rc, out = quiet(E.M.run)
            self.assertEqual(rc, 0, out)
            self.assertIn("이미 보호 저장됨", out)
            stamp = E.M.latest_backup()
            self.assertIsNotNone(stamp)
            rc, out = quiet(E.M.restore, None)
            self.assertEqual(rc, 0, out)
            self.assertEqual(json.loads(E.T.CONFIG_FILE.read_text(encoding="utf-8")), FAKE_CONFIG)
            self.assertEqual(E.T.load_merged(), FAKE_CONFIG)

    def test_failure_mid_migration_rolls_back(self):
        with FakeEnv() as E:
            E.write_config()
            with mock.patch.object(E.T, "_write_plain", side_effect=OSError("simulated disk full")):
                rc, out = quiet(E.M.run)
            self.assertEqual(rc, 1)
            self.assertIn("되돌립니다", out)
            self.assertEqual(json.loads(E.T.CONFIG_FILE.read_text(encoding="utf-8")), FAKE_CONFIG)
            self.assertEqual(E.T.load_merged(), FAKE_CONFIG)

    def test_stray_plain_value_from_old_writer(self):
        with FakeEnv() as E:
            E.write_config()
            quiet(E.M.run)
            plain = json.loads(E.T.CONFIG_FILE.read_text(encoding="utf-8"))
            plain["contact"] = "010-9999-0000"            # 보호 저장을 모르는 옛 코드가 평문에 다시 씀
            E.T.CONFIG_FILE.write_text(json.dumps(plain, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(E.T.load_merged()["contact"], "010-9999-0000")      # 새로 쓴 값이 이긴다
            E.T.save_config(E.T.load_merged())
            self.assertNotIn("contact", json.loads(E.T.CONFIG_FILE.read_text(encoding="utf-8")))
            self.assertEqual(E.T.load_merged()["contact"], "010-9999-0000")

    def test_corrupt_private_file(self):
        with FakeEnv() as E:
            E.write_config()
            quiet(E.M.run)
            E.T.save_config(E.T.load_merged())                     # .prev 하나 생기게
            E.T.PRIVATE_FILE.write_text("garbage", encoding="utf-8")
            self.assertEqual(E.T.load_merged()["contact"], "010-0000-0000")      # .prev 로 버틴다
            import secure_store as S
            S.prev_path(E.T.PRIVATE_FILE).unlink()
            with self.assertRaises(E.T.PrivateConfigError) as c:
                E.T.load_merged()
            self.assertEqual(c.exception.code, "E_FILE_CORRUPT")
            self.assertNotIn("010-0000-0000", str(c.exception))
            self.assertEqual(E.T.load_config_or_empty(), {})                     # 위젯 쪽은 죽지 않는다

    def test_vault_unavailable_stops_before_touching_files(self):
        with FakeEnv() as E:
            p = E.write_config()
            before = p.read_bytes()
            import secure_store as S
            with mock.patch.object(S, "check_vault", side_effect=S.SecureError("E_VAULT_BACKEND")):
                rc, out = quiet(E.M.run)
            self.assertEqual(rc, 1)
            self.assertIn("E_VAULT_BACKEND", out)
            self.assertEqual(p.read_bytes(), before)
            self.assertFalse(E.T.PRIVATE_FILE.exists())


class GoogleToken(unittest.TestCase):
    FAKE_TOKEN = {"token": "FAKE-ACCESS", "refresh_token": "FAKE-REFRESH", "client_id": "fake.apps.googleusercontent.com",
                  "client_secret": "FAKE-CS", "token_uri": "https://oauth2.googleapis.com/token",
                  "scopes": ["https://www.googleapis.com/auth/calendar"], "expiry": "2020-01-01T00:00:00Z"}

    def _write_token(self, E):
        E.T.TOKEN_FILE.write_text(json.dumps(self.FAKE_TOKEN), encoding="utf-8")

    def test_refresh_failure_keeps_plain_and_protected_copy(self):
        with FakeEnv() as E:
            E.write_config()
            self._write_token(E)
            with mock.patch("google.oauth2.credentials.Credentials.refresh", side_effect=RuntimeError("offline")):
                rc, out = quiet(E.M.run)
            self.assertEqual(rc, 0, out)
            self.assertTrue(E.T.TOKEN_FILE.exists())                # 갱신 확인 전이라 평문을 지우지 않는다
            self.assertTrue(E.T.GOOGLE_TOKEN_DPAPI.exists())
            self.assertNotIn("FAKE-REFRESH", out)
            self.assertNotIn("FAKE-REFRESH", E.T.GOOGLE_TOKEN_DPAPI.read_text(encoding="utf-8"))

    def test_refresh_success_deletes_plain(self):
        with FakeEnv() as E:
            E.write_config()
            self._write_token(E)
            with mock.patch("google.oauth2.credentials.Credentials.refresh", return_value=None):
                rc, out = quiet(E.M.run)
            self.assertEqual(rc, 0, out)
            self.assertFalse(E.T.TOKEN_FILE.exists())
            self.assertEqual(E.T.google_token_state(), "protected")
            self.assertEqual(E.T.load_google_credentials().refresh_token, "FAKE-REFRESH")

    def test_runtime_refresh_failure_raises_code_and_keeps_token(self):
        with FakeEnv() as E:
            import secure_store as S
            S.prepare_secure_dir(E.T.SECURE_DIR)
            S.write_json(E.T.GOOGLE_TOKEN_DPAPI, self.FAKE_TOKEN, E.T.GOOGLE_PURPOSE)
            with mock.patch("google.oauth2.credentials.Credentials.refresh", side_effect=RuntimeError("revoked")):
                with self.assertRaises(E.T.GoogleRefreshFailed) as c:
                    E.T.google_credentials()
            self.assertEqual(c.exception.code, "E_GOOGLE_REFRESH")
            self.assertTrue(E.T.GOOGLE_TOKEN_DPAPI.exists())


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""secure_store — DPAPI · 봉투 파일 · 금고 확인 · 위치·권한 (가상 값만)."""
import _guard  # noqa: F401  ← 맨 먼저: 진짜 설정 폴더·금고 항목을 못 건드리게
import os
import secrets
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import secure_store as S  # noqa: E402

TEST_SERVICE = "riro-merit-test/example.invalid"


class DpapiFiles(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="sec_"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_roundtrip_and_purpose(self):
        p = self.d / "a.dpapi"
        S.write_json(p, {"token": "FAKE-TOKEN"}, "test/a")
        self.assertEqual(S.read_json(p, "test/a"), {"token": "FAKE-TOKEN"})
        self.assertNotIn("FAKE-TOKEN", p.read_text(encoding="utf-8"))          # 파일에 평문이 없다
        with self.assertRaises(S.SecureError) as c:
            S.read_json(p, "test/b")
        self.assertEqual(c.exception.code, "E_PURPOSE_MISMATCH")

    def test_wrong_entropy_cannot_decrypt(self):
        blob = S.dpapi_protect(b"FAKE", "x")
        with self.assertRaises(S.SecureError) as c:
            S.dpapi_unprotect(blob, "y")
        self.assertEqual(c.exception.code, "E_DPAPI_DECRYPT")

    def test_corrupt_uses_prev_then_fails_without(self):
        p = self.d / "b.dpapi"
        S.write_json(p, {"v": 1}, "t")
        S.write_json(p, {"v": 2}, "t")
        p.write_text("{broken", encoding="utf-8")
        raw, src = S.read_protected(p, "t")
        self.assertEqual(src, "prev")
        S.prev_path(p).unlink()
        with self.assertRaises(S.SecureError) as c:
            S.read_protected(p, "t")
        self.assertEqual(c.exception.code, "E_FILE_CORRUPT")

    def test_missing(self):
        with self.assertRaises(S.SecureError) as c:
            S.read_protected(self.d / "none.dpapi", "t")
        self.assertEqual(c.exception.code, "E_FILE_MISSING")

    def test_failed_write_keeps_original(self):
        p = self.d / "c.dpapi"
        S.write_json(p, {"v": "original"}, "t")
        real_replace = os.replace

        def boom(src, dst):
            if str(dst) == str(p):
                raise PermissionError("simulated")
            return real_replace(src, dst)
        with mock.patch("secure_store.os.replace", side_effect=boom):
            with self.assertRaises(S.SecureError):
                S.write_json(p, {"v": "new"}, "t")
        # 본 파일이 .prev 로 옮겨진 뒤 실패해도, 읽기는 .prev 로 원래 값을 돌려준다
        self.assertEqual(S.read_json(p, "t"), {"v": "original"})
        self.assertEqual([f for f in self.d.iterdir() if ".tmp-" in f.name], [])     # 임시 파일 안 남음

    def test_file_state_has_no_values(self):
        p = self.d / "d.dpapi"
        S.write_json(p, {"pw": "FAKE-SECRET"}, "t")
        st = S.file_state(p, "t")
        self.assertTrue(st["decrypts"])
        self.assertNotIn("FAKE-SECRET", repr(st))


class Vault(unittest.TestCase):
    def tearDown(self):
        try:
            S.vault_delete(TEST_SERVICE)
        except Exception:
            pass

    def test_real_backend_functional(self):
        r = S.check_vault(functional=True)
        self.assertEqual(r["backend"], "WinVaultKeyring")
        self.assertTrue(r["functional"])

    def test_non_windows_backend_is_refused_and_nothing_written(self):
        class PlaintextKeyring:      # keyrings.alt 같은 «파일에 쓰는» 백엔드 흉내
            set_calls = 0

            def set_password(self, *a):
                PlaintextKeyring.set_calls += 1
        fake = PlaintextKeyring()
        with mock.patch("keyring.get_keyring", return_value=fake):
            with self.assertRaises(S.SecureError) as c:
                S.vault_set(TEST_SERVICE, "fake-id", "FAKE-PW")
        self.assertEqual(c.exception.code, "E_VAULT_BACKEND")
        self.assertEqual(PlaintextKeyring.set_calls, 0)

    def test_replace_credential_on_id_change(self):
        S.vault_replace_credential(TEST_SERVICE, "fake-id-1", "FAKE-PW-1")
        self.assertEqual(S.vault_get_credential(TEST_SERVICE), ("fake-id-1", "FAKE-PW-1"))
        S.vault_replace_credential(TEST_SERVICE, "fake-id-2", "FAKE-PW-2")
        self.assertEqual(S.vault_get_credential(TEST_SERVICE), ("fake-id-2", "FAKE-PW-2"))
        import keyring
        self.assertIsNone(keyring.get_password(TEST_SERVICE, "fake-id-1"))          # 옛 아이디 항목이 안 남는다
        self.assertTrue(S.vault_delete(TEST_SERVICE))
        self.assertEqual(S.vault_get_credential(TEST_SERVICE), (None, None))
        self.assertFalse(S.vault_delete(TEST_SERVICE))


class LocationAndAcl(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="loc_"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_git_and_cloud_detection(self):
        (self.d / "repo" / ".git").mkdir(parents=True)
        self.assertTrue(S.location_report(self.d / "repo" / "secure")["in_git"])
        with self.assertRaises(S.SecureError) as c:
            S.assert_safe_location(self.d / "repo" / "secure")
        self.assertEqual(c.exception.code, "E_LOCATION_GIT")
        self.assertTrue(S.location_report(self.d / "OneDrive - 가상학교" / "x")["in_cloud_sync"])

    def test_restrict_dir_only_user_and_system(self):
        sd = S.prepare_secure_dir(self.d / "secure")
        (sd / "f.txt").write_text("x")
        S.restrict_dir(sd)
        rep = S.acl_report(sd)
        self.assertFalse(rep["inherits"])
        self.assertEqual(set(rep["principals"]), {"CurrentUser", "SYSTEM"})
        self.assertTrue(S.acl_report(sd / "f.txt")["only_user_system"])


class OriginCheck(unittest.TestCase):
    """비밀번호를 넣기 직전 접속처 확인 — https·호스트·포트가 «정확히» 같아야 한다."""
    OK = ["https://myschool.riroschool.kr"]

    def test_exact_match_only(self):
        S.check_origin("https://myschool.riroschool.kr/user.php?action=signin", self.OK)
        S.check_origin("https://MYSCHOOL.riroschool.kr:443/x", self.OK)
        for bad in ("http://myschool.riroschool.kr/",                       # https 아님
                    "https://myschool.riroschool.kr:8443/",                 # 포트 다름
                    "https://myschool.riroschool.kr.evil.example/",         # 비슷한 이름
                    "https://evil.example/myschool.riroschool.kr",          # 경로에만 들어 있음
                    "https://myschool.riroschool.kr@evil.example/",         # @ 속임수
                    "https://x.myschool.riroschool.kr/",                    # 하위 도메인
                    "about:blank", ""):
            with self.assertRaises(S.SecureError) as c:
                S.check_origin(bad, self.OK)
            self.assertEqual(c.exception.code, "E_ORIGIN", bad)

    def test_reveal_for_never_opens_vault_on_bad_origin(self):
        class Ticket(S.VaultSecret):
            def reveal(self):
                raise AssertionError("금고를 열면 안 된다")
        with self.assertRaises(S.SecureError):
            S.reveal_for(Ticket("x", "y"), "https://evil.example/", self.OK)

    def test_debug_switches_removed(self):
        os.environ["DEBUG"] = "pw:api"
        os.environ["PWDEBUG"] = "1"
        self.assertEqual(sorted(S.harden_process_env()), ["DEBUG", "PWDEBUG"])
        self.assertNotIn("DEBUG", os.environ)

    def test_teacher_config_import_drops_debug_env_in_child(self):
        import subprocess
        env = dict(os.environ, DEBUG="pw:api", PWDEBUG="1")
        r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, r'%s'); import teacher_config, os; "
                            "print('DEBUG' in os.environ, 'PWDEBUG' in os.environ)" % Path(S.__file__).parent],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.stdout.strip(), "False False")


class VaultWriteOrderAndScope(unittest.TestCase):
    """새 항목을 저장·검증한 «뒤»에 옛 항목을 정리 · 실패하면 옛 항목이 그대로 · 저장 범위는 «이 PC 에만»."""
    SVC = "riro-merit-test/order.invalid"

    def tearDown(self):
        for u in ("old-id", "new-id"):
            for t in (self.SVC, f"{u}@{self.SVC}"):
                try:
                    S._delete_target(t)
                except Exception:
                    pass

    @staticmethod
    def buf(text):
        b = S.SecretBuffer()
        for ch in text:
            b.append(ch)
        return b

    def test_failed_new_write_keeps_old_credential(self):
        import keyring
        S.vault_write_buffer(self.SVC, "old-id", self.buf("FAKE-OLD"), replace=True)
        real = S._write_raw

        def flaky(target, username, *a):
            if target == self.SVC and username == "new-id":
                raise S.SecureError("E_VAULT_IO", "simulated")
            return real(target, username, *a)
        with mock.patch.object(S, "_write_raw", side_effect=flaky):
            with self.assertRaises(S.SecureError):
                S.vault_write_buffer(self.SVC, "new-id", self.buf("FAKE-NEW"), replace=True)
        self.assertEqual(S.vault_username(self.SVC), "old-id")
        self.assertEqual(keyring.get_password(self.SVC, "old-id"), "FAKE-OLD")
        self.assertFalse(S.vault_exists(self.SVC, "new-id"))

    def test_failed_verify_restores_old_credential(self):
        import keyring
        S.vault_write_buffer(self.SVC, "old-id", self.buf("FAKE-OLD"), replace=True)
        with mock.patch.object(S, "_matches", return_value=False):
            with self.assertRaises(S.SecureError) as c:
                S.vault_write_buffer(self.SVC, "new-id", self.buf("FAKE-NEW"), replace=True)
        self.assertEqual(c.exception.code, "E_VAULT_VERIFY")
        self.assertEqual(S.vault_username(self.SVC), "old-id")
        self.assertEqual(keyring.get_password(self.SVC, "old-id"), "FAKE-OLD")

    def test_success_removes_old_only_after_new_is_verified(self):
        import keyring
        S.vault_write_buffer(self.SVC, "old-id", self.buf("FAKE-OLD"), replace=True)
        S.vault_write_buffer(self.SVC, "new-id", self.buf("FAKE-NEW"), replace=True)
        self.assertEqual(S.vault_username(self.SVC), "new-id")
        self.assertEqual(keyring.get_password(self.SVC, "new-id"), "FAKE-NEW")
        self.assertIsNone(keyring.get_password(self.SVC, "old-id"))

    def test_persist_is_local_machine(self):
        S.vault_write_buffer(self.SVC, "new-id", self.buf("FAKE-NEW"), replace=True)
        with S._CredRef(self.SVC) as c:
            self.assertEqual(c.Persist, S.CRED_PERSIST_LOCAL_MACHINE)
        S.vault_set(self.SVC + "/k", "kid", "FAKE-K")                 # keyring 길도 «이 PC 에만»
        with S._CredRef(self.SVC + "/k") as c:
            self.assertEqual(c.Persist, S.CRED_PERSIST_LOCAL_MACHINE)
        S.vault_delete(self.SVC + "/k", "kid")


class GuardWorks(unittest.TestCase):
    """시험 안전장치가 진짜 금고 이름을 «열기 전에» 막는지 (막히므로 진짜 항목에는 닿지 않는다)."""

    def test_real_names_are_blocked_before_any_access(self):
        import keyring
        for call in (lambda: S.vault_exists("neis-automation", "evpn"),
                     lambda: S.vault_username("riro-merit/myschool.riroschool.kr"),
                     lambda: S.vault_username("riro-merit/otherschool.riroschool.kr"),   # 어느 학교든
                     lambda: keyring.get_password("neis-automation", "evpn"),
                     lambda: S._write_raw("x@neis-automation", "x", 0, 0)):
            with self.assertRaises((_guard.RealVaultTouched, S.SecureError)) as c:
                call()
            if isinstance(c.exception, S.SecureError):          # vault_exists 는 막힘을 E_VAULT_IO 로 감싸 낸다
                self.assertEqual(c.exception.detail, "RealVaultTouched")

    def test_settings_folder_is_redirected(self):
        import teacher_config as T
        self.assertTrue(str(T.CONFIG_DIR).startswith(_guard._TMP))


class ChromeNoPasswordSaving(unittest.TestCase):
    """자동화 크롬 «비밀번호 저장» 끄기 — 가짜 프로필 폴더로. 다른 설정 값은 그대로 둔다."""

    def test_sets_two_keys_keeps_others_and_is_idempotent(self):
        import json
        import teacher_config as T
        d = Path(tempfile.mkdtemp(prefix="prof_"))
        try:
            T.no_password_saving(d)                                   # 설정 파일이 없을 때 — 새로 만든다
            p = d / "Default" / "Preferences"
            first = json.loads(p.read_text(encoding="utf-8"))
            self.assertIs(first["credentials_enable_service"], False)
            self.assertIs(first["profile"]["password_manager_enabled"], False)
            p.write_text(json.dumps({"profile": {"name": "fake", "password_manager_enabled": True},
                                     "credentials_enable_service": True, "other": {"x": 1}}), encoding="utf-8")
            T.no_password_saving(d)
            after = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(after["other"], {"x": 1})                # 다른 값은 그대로
            self.assertEqual(after["profile"]["name"], "fake")
            self.assertIs(after["profile"]["password_manager_enabled"], False)
            m = p.stat().st_mtime_ns
            T.no_password_saving(d)                                   # 이미 꺼져 있으면 다시 쓰지 않는다
            self.assertEqual(p.stat().st_mtime_ns, m)
            self.assertEqual([f.name for f in p.parent.iterdir() if ".tmp" in f.name], [])
        finally:
            shutil.rmtree(d, ignore_errors=True)


class Scrub(unittest.TestCase):
    def test_scrub(self):
        pw = "FAKE-" + secrets.token_hex(4)
        self.assertEqual(S.scrub(f"오류: {pw} 틀림", [pw]), "오류: **** 틀림")
        self.assertEqual(S.scrub_common("연락 010-1234-5678"), "연락 010-****-****")


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""금고(자격 증명 관리자) 이름이 사람마다 다른지 (2026-11-26).
가짜 설정 폴더(FakeEnv)에서만 만든다 — 금고 항목 자체는 읽지도 쓰지도 않는다 (_guard 가 nlk-… 이름을 막는다)."""
import _guard  # noqa: F401  ← 맨 먼저
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fake_env import FakeEnv  # noqa: E402

KIT = Path(__file__).resolve().parent.parent
CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"


class VaultNamePerPerson(unittest.TestCase):
    def test_first_use_makes_random_name_in_encrypted_settings_only(self):
        with FakeEnv() as E:
            T = E.T
            self.assertIsNone(T.vault_service(create=False))        # 아직 없으면 만들지 않고 None
            self.assertFalse(T.CONFIG_FILE.exists())
            name = T.vault_service()
            self.assertRegex(name, r"^nlk-[0-9a-f]{12}$")
            self.assertEqual(T.vault_service(), name)               # 두 번째부터는 같은 이름
            plain = T.CONFIG_FILE.read_text(encoding="utf-8")
            self.assertNotIn(name, plain)                           # 평문 설정 파일에는 한 번도 안 적힌다
            self.assertIn("_secure", json.loads(plain))             # 처음부터 «나눠 저장» 모드
            self.assertNotIn(name.encode(), T.PRIVATE_FILE.read_bytes())   # 암호화 파일 안에서도 글자 그대로는 아니다
            self.assertEqual(T.load_merged()["vault_service"], name)
            del T.KEYRING_SERVICE                                   # FakeEnv 가 넣은 시험용 고정 이름을 빼고
            self.assertEqual(T.KEYRING_SERVICE, name)               # 다른 파일이 쓰는 이름도 이것

    def test_two_people_get_different_names(self):
        names = set()
        for _ in range(3):
            with FakeEnv() as E:
                names.add(E.T.vault_service())
        self.assertEqual(len(names), 3)

    def test_old_plain_settings_are_encrypted_before_name_is_made(self):
        with FakeEnv(helper_new=True) as E:
            E.write_config({"neis_url": "https://sen.neis.go.kr/jsp/main.jsp", "cert_name": "000가상교사000"})
            name = E.T.vault_service()
            plain = E.T.CONFIG_FILE.read_text(encoding="utf-8")
            self.assertNotIn(name, plain)
            self.assertNotIn("000가상교사000", plain)               # 인증서 이름도 함께 암호화 파일로
            self.assertEqual(E.T.load_merged()["cert_name"], "000가상교사000")

    def test_riro_vault_name_uses_same_per_person_prefix(self):
        with FakeEnv() as E:
            import importlib
            from riro_merit import config as RC
            RC = importlib.reload(RC)
            self.assertEqual(RC.riro_service(create=False), "")     # 아직 금고 이름이 없으면 비어 있다
            name = E.T.vault_service()
            self.assertEqual(RC.riro_service(), f"{name}-riro/{RC.SCHOOL_HOST}")
            self.assertEqual(RC.RIRO_SERVICE, RC.riro_service())
            self.assertTrue(_guard.is_real(RC.RIRO_SERVICE))        # 시험 안전장치가 이 모양을 막는다
            self.assertTrue(_guard.is_real("000가상교사000@" + name))

    def test_check_tool_never_creates_a_name(self):
        src = (KIT / "secure_check.py").read_text(encoding="utf-8")
        self.assertNotIn("T.KEYRING_SERVICE", src)
        self.assertIn("vault_service(False)", src)
        self.assertNotIn("riro_service()", src)

    def test_no_fixed_vault_name_left_in_code(self):
        for rel in ("edufine_common.py", "riro_merit/config.py", "teacher_config.py"):
            src = (KIT / rel).read_text(encoding="utf-8")
            self.assertNotIn('KEYRING_SERVICE = "neis-automation"', src, rel)
            self.assertNotIn('f"riro-merit/{SCHOOL_HOST}"', src, rel)


@unittest.skipUnless(CSC.exists(), "csc.exe 없음")
class HelperReadsVaultName(unittest.TestCase):
    """EVPN 도우미도 암호화 설정의 금고 이름을 쓴다. 시험 이름은 «nlk-000000000000» — 진짜 사람 이름이 아니다."""

    def test_helper_takes_vault_name_from_encrypted_settings(self):
        import secure_store as S
        d = Path(tempfile.mkdtemp(prefix="helper_vault_"))
        try:
            exe = d / "evpn_helper.exe"
            r = subprocess.run([str(CSC), "-nologo", "-codepage:65001", "-target:winexe", "-r:System.Web.Extensions.dll",
                                "-r:System.Security.dll", f"-out:{exe}", str(KIT / "evpn_helper" / "helper.cs")],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(r.returncode, 0, r.stdout[-400:])
            cd = d / "cfg"
            cd.mkdir()
            (cd / "teacher_config.json").write_text(json.dumps({"_secure": {"version": 1}}), encoding="utf-8")
            S.write_json(cd / "secure" / "teacher_private.dpapi",
                         {"cert_name": "000가상교사000", "vault_service": "nlk-000000000000"}, "neis-automation/teacher_private")

            def selftest(*extra):
                out = d / "st.txt"
                subprocess.run([str(exe), "--selftest", str(out), str(cd), *extra], timeout=30)
                kv = dict(l.split("=", 1) for l in out.read_text(encoding="utf-8-sig").splitlines() if "=" in l)
                out.unlink()
                return kv
            kv = selftest()
            self.assertEqual(kv.get("vault_name"), "dpapi")
            self.assertEqual(kv.get("password"), "missing")         # 가짜 이름이라 항목이 없다 (진짜 금고 항목을 안 본다)
            self.assertEqual(selftest("neis-helper-test/example.invalid").get("vault_name"), "args")
            self.assertEqual(kv.get("version"), "6-vault-name")
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""EVPN 도우미(helper.cs) 새 판이 암호화된 개인 설정에서 인증서 이름을 읽는지 — 임시 폴더에 컴파일해 가짜 값으로.

설치(관리자 권한)는 하지 않는다. --selftest 는 «찾음/없음»만 파일에 적는다.
도우미가 볼 금고 이름은 시험용(example.invalid)으로 넘긴다 — 진짜 «neis-automation» 항목은 열지 않는다.
"""
import _guard  # noqa: F401  ← 맨 먼저
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))
import secure_store as S  # noqa: E402

CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"


@unittest.skipUnless(CSC.exists(), "csc.exe 없음")
class HelperReadsDpapi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp(prefix="helper_"))
        cls.exe = cls.d / "evpn_helper.exe"
        r = subprocess.run([str(CSC), "-nologo", "-codepage:65001", "-target:winexe", "-r:System.Web.Extensions.dll",
                            "-r:System.Security.dll", f"-out:{cls.exe}", str(KIT / "evpn_helper" / "helper.cs")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert r.returncode == 0, r.stdout[-500:]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def selftest(self, cfgdir: Path) -> dict:
        out = self.d / "st.txt"
        subprocess.run([str(self.exe), "--selftest", str(out), str(cfgdir), "neis-helper-test/example.invalid"], timeout=30)
        kv = dict(l.split("=", 1) for l in out.read_text(encoding="utf-8-sig").splitlines() if "=" in l)
        out.unlink()
        return kv

    def make_dir(self, plain: dict, private: dict | None) -> Path:
        cd = Path(tempfile.mkdtemp(prefix="cfg_", dir=self.d))
        (cd / "teacher_config.json").write_text(json.dumps(plain, ensure_ascii=False), encoding="utf-8")
        if private is not None:
            S.write_json(cd / "secure" / "teacher_private.dpapi", private, "neis-automation/teacher_private")
        return cd

    def test_reads_from_dpapi_when_not_in_plain(self):
        cd = self.make_dir({"neis_url": "x", "_secure": {"version": 1}}, {"cert_name": "000가상교사000"})
        kv = self.selftest(cd)
        self.assertEqual(kv.get("cert_name"), "found:dpapi")
        self.assertNotIn("가상교사", json.dumps(kv, ensure_ascii=False))

    def test_plain_still_works(self):
        kv = self.selftest(self.make_dir({"cert_name": "000가상교사000"}, None))
        self.assertEqual(kv.get("cert_name"), "found:plain")

    def test_prev_fallback_and_corrupt(self):
        cd = self.make_dir({"_secure": {}}, {"cert_name": "000가상교사000"})
        S.write_json(cd / "secure" / "teacher_private.dpapi", {"cert_name": "000가상교사000"}, "neis-automation/teacher_private")
        (cd / "secure" / "teacher_private.dpapi").write_text("broken", encoding="utf-8")
        self.assertEqual(self.selftest(cd).get("cert_name"), "found:dpapi-prev")
        (cd / "secure" / "teacher_private.dpapi.prev").unlink()
        self.assertEqual(self.selftest(cd).get("cert_name"), "missing")

    def test_password_field_pick_and_owner_check_with_fake_windows(self):
        """가짜 대화상자로: 입력칸 1개 → 그 칸 · 여러 개면 암호 칸이 «딱 하나»일 때만 · 가짜 창은 AXGATE 로 안 본다."""
        exe = self.d / "harness.exe"
        r = subprocess.run([str(CSC), "-nologo", "-codepage:65001", "-target:exe", "/main:HelperHarness",
                            "-r:System.Web.Extensions.dll", "-r:System.Security.dll", f"-out:{exe}",
                            str(KIT / "evpn_helper" / "helper.cs"), str(KIT / "tests" / "helper_harness.cs")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stdout[-400:])
        out = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30).stdout
        kv = dict(l.split("=", 1) for l in out.splitlines() if "=" in l and not l.startswith(("edits", "pw")))
        self.assertTrue(kv["one_plain"].startswith("picked"))          # 칸이 하나뿐이면 그 칸
        self.assertTrue(kv["two_plain"].startswith("none"))            # 여러 개인데 암호 칸 표시가 없으면 → 멈춤
        self.assertTrue(kv["plain_and_pw"].startswith("picked"))       # 여러 개 중 암호 칸이 하나 → 그 칸
        self.assertTrue(kv["two_pw"].startswith("none"))               # 암호 칸이 둘 → 멈춤
        self.assertEqual(kv["owned_fake"], "False")                    # AXGATE 가 아닌 프로그램의 창은 건드리지 않는다
        self.assertEqual(kv["select_direct"], "True")                  # Ctrl/Shift·포커스 무관하게 LVM_SETITEMSTATE 로 줄 선택

    def test_wrong_purpose_is_ignored(self):
        cd = Path(tempfile.mkdtemp(prefix="cfg_", dir=self.d))
        (cd / "teacher_config.json").write_text("{}", encoding="utf-8")
        S.write_json(cd / "secure" / "teacher_private.dpapi", {"cert_name": "x"}, "other/purpose")
        self.assertEqual(self.selftest(cd).get("cert_name"), "missing")


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""상벌점 준비 상황(riro_merit.status) — 금고를 열지 않고, 값 없이 참/거짓만 내는지."""
import _guard  # noqa: F401  ← 맨 먼저: 진짜 설정 폴더·금고 항목을 못 건드리게
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))
import secure_store as S  # noqa: E402
from riro_merit import config as C  # noqa: E402
from riro_merit import status as ST  # noqa: E402

RULES = {"confirmed": True, "item": "가상 항목", "points": 1, "cutoff_time": "09:00", "exclusions": []}


class Status(unittest.TestCase):
    def setUp(self):
        for p in (C.base_dir(), Path(os.environ["LOCALAPPDATA"]) / "neis-automation"):
            shutil.rmtree(p, ignore_errors=True)
        kit = tempfile.mkdtemp(prefix="riro_status_kit_")
        self.addCleanup(shutil.rmtree, kit, True)
        self.kit = Path(kit)
        self.enterContext(mock.patch.object(C, "KIT", self.kit))
        # 금고에 손대면 바로 실패 — 상태 보기는 «넣었다» 표시만 봐야 한다
        for name in ("vault_username", "vault_exists", "VaultSecret"):
            self.enterContext(mock.patch.object(S, name, side_effect=AssertionError("금고를 열었습니다: " + name)))

    def test_nothing_set_up(self):
        res = ST.status()
        self.assertTrue(res["ok"])
        self.assertEqual({k: res[k] for k in ST.KEYS}, {k: False for k in ST.KEYS})

    def test_everything_ready(self):
        S.mark_vault("riro", "saved")
        C.save({**C.DEFAULTS, "sheet_id": "x" * 30, "rules": RULES})
        (C.base_dir() / "divot_schema.json").write_text("{}", encoding="utf-8")
        (self.kit / "oauth_client.json").write_text("{}", encoding="utf-8")
        C.secure_dir().mkdir(parents=True, exist_ok=True)
        (C.secure_dir() / "google_sheets_token.dpapi").write_bytes(b"x")
        res = ST.status()
        self.assertEqual({k: res[k] for k in ST.KEYS},
                         {"riro_account": True, "login_locked": False, "divot": True, "rules": True,
                          "sheet_key": True, "sheet_id": True, "sheet_token": True})

    def test_deleted_account_and_unconfirmed_rules(self):
        S.mark_vault("riro", "saved")
        S.mark_vault("riro", "deleted")
        C.save({**C.DEFAULTS, "rules": {**RULES, "confirmed": False}})
        res = ST.status()
        self.assertFalse(res["riro_account"])
        self.assertFalse(res["rules"])

    def test_confirmed_but_incomplete_rules_are_not_ready(self):
        C.save({**C.DEFAULTS, "rules": {**RULES, "cutoff_time": ""}})
        self.assertFalse(ST.status()["rules"])

    def test_login_lock_only_for_same_credential_revision(self):
        C.save({**C.DEFAULTS, "cred_rev": 3, "login_lock": {"rev": 3, "code": "E_BAD_CREDENTIALS"}})
        self.assertTrue(ST.status()["login_locked"])
        C.save({**C.DEFAULTS, "cred_rev": 4, "login_lock": {"rev": 3, "code": "E_BAD_CREDENTIALS"}})
        self.assertFalse(ST.status()["login_locked"])

    def test_corrupt_config_reports_code_only(self):
        C.base_dir().mkdir(parents=True, exist_ok=True)
        C.config_file().write_text("{깨짐", encoding="utf-8")
        res = ST.status()
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "E_CONFIG:E_FILE_CORRUPT")

    def test_output_has_no_values(self):
        C.save({**C.DEFAULTS, "sheet_id": "SHEETID" + "x" * 30, "rules": RULES})
        res = ST.status()
        text = json.dumps(res, ensure_ascii=False)
        for value in ("SHEETID", "가상 항목", "09:00"):
            self.assertNotIn(value, text)
        self.assertTrue(all(isinstance(res[k], bool) for k in ST.KEYS))


class CommandLine(unittest.TestCase):
    def test_last_line_is_result_json(self):
        # 위젯이 실제로 부르는 모양 그대로. LOCALAPPDATA 는 _guard 의 임시 폴더를 물려받는다
        p = subprocess.run([sys.executable, "-X", "utf8", "-m", "riro_merit.status"], cwd=str(KIT),
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        last = p.stdout.strip().splitlines()[-1]
        self.assertTrue(last.startswith("RESULT_JSON "), p.stdout + p.stderr)
        res = json.loads(last[len("RESULT_JSON "):])
        self.assertEqual(set(ST.KEYS) | {"ok", "code"}, set(res))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""시험용 가짜 환경 — 진짜 %LOCALAPPDATA% 를 건드리지 않게 임시 폴더로 바꿔 끼운다.

모든 값은 가상이다 (가상교사·010-0000-0000·FAKE-…). 진짜 인증정보·학생 정보는 쓰지 않는다.
"""
import _guard  # noqa: F401  ← 맨 먼저 (진짜 설정 폴더·금고 항목을 못 건드리게)
import importlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

FAKE_SERVICE = "neis-automation-test/fakeenv.invalid"     # 나이스 키트 금고 이름 대신 쓰는 시험용 이름

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))

FAKE_CONFIG = {
    "neis_url": "https://sen.neis.go.kr/jsp/main.jsp",
    "grade": "", "class": "", "calendars": {},
    "cert_name": "000가상교사000",
    "contact": "010-0000-0000",
    "approval_line": "가상결재선",
    "evpn_user": "fake-evpn-id",
    "school_name": "가상고등학교", "school_office": "B10", "school_code": "0000000",
    "last_period": 7,
}


class FakeEnv:
    def __init__(self, helper_new: bool = False):
        self.root = Path(tempfile.mkdtemp(prefix="neis_fake_"))
        self.la = self.root / "LocalAppData"
        self.pf = self.root / "ProgramFiles"
        (self.la / "neis-automation").mkdir(parents=True)
        hd = self.pf / "NeisAutomationEVPN"
        hd.mkdir(parents=True)
        (hd / "helper.cs").write_text("const string HELPER_VERSION = \"2\";" if helper_new else "// old helper",
                                      encoding="utf-8")
        self._old = {k: os.environ.get(k) for k in ("LOCALAPPDATA", "ProgramFiles")}

    def __enter__(self):
        os.environ["LOCALAPPDATA"] = str(self.la)
        os.environ["ProgramFiles"] = str(self.pf)
        import teacher_config
        import secure_migrate
        self.T = importlib.reload(teacher_config)
        self.T.KEYRING_SERVICE = FAKE_SERVICE                # 진짜 «neis-automation» 항목을 안 보게
        self.M = importlib.reload(secure_migrate)
        return self

    def write_config(self, data=None):
        p = self.la / "neis-automation" / "teacher_config.json"
        p.write_text(json.dumps(data or FAKE_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    def __exit__(self, *exc):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import teacher_config
        import secure_migrate
        importlib.reload(teacher_config)
        importlib.reload(secure_migrate)
        shutil.rmtree(self.root, ignore_errors=True)

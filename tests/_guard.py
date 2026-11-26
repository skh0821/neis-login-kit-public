# -*- coding: utf-8 -*-
"""시험 안전장치 — 모든 시험 파일이 «맨 먼저» import 한다.

① %LOCALAPPDATA% 를 빈 임시 폴더로 바꾼다 → 진짜 설정 파일(teacher_config.json·암호화 설정·리로스쿨 설정)을 못 읽는다.
   (teacher_config 는 import 할 때 폴더 위치를 정하므로 이 파일이 먼저여야 한다. 하위 프로세스도 이 값을 물려받는다)
② 진짜 금고 항목(나이스 키트·리로스쿨·옛 에듀파인·e알리미 이름)에 닿으려 하면 그 자리에서 실패시킨다
   — 읽기(있는지 확인 포함)·쓰기·지우기 모두. 시험은 example.invalid 같은 시험용 이름만 쓴다.
"""
import atexit
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="neis_testla_")
os.environ["LOCALAPPDATA"] = _TMP
atexit.register(shutil.rmtree, _TMP, True)
# ③ 학교 주소는 시험용 가짜로 — 선생님 PC 의 우리학교_설정.json(진짜 학교)보다 환경변수가 먼저다
TEST_SCHOOL_HOST = "myschool.riroschool.kr"
TEST_DIVOT_URL = "https://divot.example.invalid/"
os.environ["RIRO_SCHOOL_HOST"] = TEST_SCHOOL_HOST
os.environ["RIRO_DIVOT_URL"] = TEST_DIVOT_URL

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REAL_SERVICES = ("neis-automation", "edufine", "ealimi-kit")
# 리로스쿨 금고 이름은 학교마다 «riro-merit/<학교>.riroschool.kr» — 어느 학교든 진짜 모양이면 막는다 (시험은 riro-merit-test/…)
REAL_RIRO = re.compile(r"(?:^|@)riro-merit/[^/@]+\.riroschool\.kr$", re.I)


class RealVaultTouched(AssertionError):
    pass


# 2026-11-26: 금고 이름이 사람마다 무작위 «nlk-<12글자>» (리로스쿨은 «nlk-…-riro/<학교>») — 이 모양도 진짜로 본다
REAL_PER_USER = re.compile(r"(?:^|@)nlk-[0-9a-f]{12}(?:-riro/[^@]+)?$", re.I)


def is_real(target) -> bool:
    t = str(target or "")
    return (any(t == s or t.endswith("@" + s) for s in REAL_SERVICES) or bool(REAL_RIRO.search(t))
            or bool(REAL_PER_USER.search(t)))


def _block(what, target):
    raise RealVaultTouched(f"시험이 진짜 금고 항목에 손대려 했습니다 ({what}) — 시험용 이름을 쓰세요")


def install():
    import keyring
    for name in ("get_password", "set_password", "delete_password", "get_credential"):
        orig = getattr(keyring, name)
        if getattr(orig, "_guarded", False):
            continue

        def wrap(service, *a, _o=orig, _n=name, **kw):
            if is_real(service):
                _block("keyring." + _n, service)
            return _o(service, *a, **kw)
        wrap._guarded = True
        setattr(keyring, name, wrap)

    import secure_store as S
    if getattr(S._CredRef.__init__, "_guarded", False):
        return
    orig_init, orig_write, orig_del = S._CredRef.__init__, S._write_raw, S._delete_target

    def init(self, target):
        if is_real(target):
            _block("CredRead", target)
        orig_init(self, target)

    def write(target, *a):
        if is_real(target):
            _block("CredWrite", target)
        return orig_write(target, *a)

    def delete(target):
        if is_real(target):
            _block("CredDelete", target)
        return orig_del(target)
    init._guarded = True
    S._CredRef.__init__, S._write_raw, S._delete_target = init, write, delete


install()

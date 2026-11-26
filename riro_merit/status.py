# -*- coding: utf-8 -*-
"""상벌점 준비 상황 — 위젯(교무 도구)이 «무엇이 준비됐는지» 보여 줄 때 부른다.

  python -X utf8 -m riro_merit.status        마지막 줄: RESULT_JSON {"ok": true, "riro_account": true, ...}

있는지·했는지(참/거짓)만 낸다. 값은 내지 않는다.
  · 금고(자격 증명 관리자)를 열지 않는다 — 윈도우 금고는 «있는지 확인»만 해도 비밀번호를 같이 돌려준다.
    대신 secure_password.py 가 남기는 «넣었다/지웠다» 표시(vault_status.json)만 본다
  · 디벗 응답 구조·구글 토큰·구글 열쇠는 «파일이 있는지»만 본다 (열지 않는다)
  · 일반 설정(config.json)은 학교 기준이 «확인함» 인지, 시트 ID 가 들어 있는지만 본다
"""
from __future__ import annotations

import json
import sys

from . import config as C
from .riro_site import _locked
import secure_store as S

KEYS = ("riro_account", "login_locked", "divot", "rules", "sheet_key", "sheet_id", "sheet_token")


def _rules_ready(rules: dict) -> bool:
    # 설정창(secure_settings.pyw)이 «확인함» 으로 저장할 때 거는 조건과 같다: 항목·점수·확정 시각이 다 있어야 한다
    return bool(rules.get("confirmed") and str(rules.get("item") or "").strip()
                and rules.get("points") is not None and str(rules.get("cutoff_time") or "").strip())


def status() -> dict:
    out = {k: False for k in KEYS}
    out.update(ok=True, code="OK")

    mark = S.vault_marks().get("riro") or {}
    out["riro_account"] = mark.get("state") == "saved"

    try:
        cfg = C.load()
    except S.SecureError as e:
        out.update(ok=False, code="E_CONFIG:" + e.code)
        cfg = None
    if cfg is not None:
        out["rules"] = _rules_ready(cfg.get("rules") or {})
        out["sheet_id"] = bool(str(cfg.get("sheet_id") or "").strip())
        # 비밀번호가 거절된 뒤 새로 넣기 전이면 로그인을 다시 시도하지 않는다 (riro_site 의 규칙 그대로)
        out["login_locked"] = bool(_locked(cfg))

    out["divot"] = (C.base_dir() / "divot_schema.json").is_file()
    out["sheet_key"] = any(p.is_file() for p in (C.KIT / "oauth_client.json", C.base_dir() / "oauth_client.json"))
    out["sheet_token"] = (C.secure_dir() / "google_sheets_token.dpapi").is_file()
    return out


def main(argv=None) -> int:
    res = status()
    print("RESULT_JSON " + json.dumps(res, ensure_ascii=False), flush=True)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

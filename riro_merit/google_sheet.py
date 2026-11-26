# -*- coding: utf-8 -*-
"""상벌점용 구글 시트 연결 — 선생님이 브라우저에서 직접 로그인·동의한다. 출결(나이스 키트) 구글 연결과 «따로» 둔다.

  python -m riro_merit.google_sheet connect     브라우저가 열린다 → 로그인·동의 → 토큰을 DPAPI 로 저장
  python -m riro_merit.google_sheet check       저장된 토큰 갱신 + 설정의 시트에 접근되는지 (탭 이름이 맞는지만)
  python -m riro_merit.google_sheet forget      이 PC 의 토큰만 지우기 (로컬)
  python -m riro_merit.google_sheet revoke      구글 쪽 권한까지 해제 (서비스 측) + 로컬 토큰 지우기

권한(scope)은 «구글 시트 읽기·쓰기» 하나만 요청한다 — 캘린더·드라이브 전체·메일은 요청하지 않는다.
토큰: %LOCALAPPDATA%\\RiroMeritAutomation\\secure\\google_sheets_token.dpapi (나이스 키트의 secure\\google_token.dpapi 와 다른 파일)
OAuth 클라이언트(열쇠): 키트 폴더의 oauth_client.json (docs/02_구글-연동.md 로 만든 것 — 같은 열쇠를 써도 토큰은 따로 생긴다)
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request

from . import config as C
import secure_store as S

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
PURPOSE = "riro-merit/google_sheets_token"


def token_file():
    return C.secure_dir() / "google_sheets_token.dpapi"


def client_config() -> dict | None:
    for p in (C.KIT / "oauth_client.json", C.base_dir() / "oauth_client.json"):
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            for k in ("installed", "web"):
                if isinstance(d.get(k), dict):
                    d = d[k]
                    break
            return {"installed": {"client_id": d["client_id"], "client_secret": d["client_secret"],
                                  "redirect_uris": ["http://localhost"],
                                  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                                  "token_uri": "https://oauth2.googleapis.com/token"}}
    return None


def connect() -> dict:
    cc = client_config()
    if not cc:
        return {"ok": False, "code": "E_NO_OAUTH_CLIENT", "hint": "docs/02_구글-연동.md 로 oauth_client.json 을 먼저 만드세요"}
    from google_auth_oauthlib.flow import InstalledAppFlow
    C.ensure_dirs()
    try:
        creds = InstalledAppFlow.from_client_config(cc, SCOPES).run_local_server(port=0, open_browser=True)
    except Exception as e:
        return {"ok": False, "code": "E_OAUTH:" + type(e).__name__}
    granted = set(creds.scopes or SCOPES)
    S.write_json(token_file(), json.loads(creds.to_json()), PURPOSE)
    return {"ok": True, "code": "OK", "scopes_ok": granted <= set(SCOPES) | {"openid"}}


def credentials():
    from google.oauth2.credentials import Credentials
    info = S.read_json(token_file(), PURPOSE)
    creds = Credentials.from_authorized_user_info(info)
    if creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request
        try:
            creds.refresh(Request())
        except Exception as e:
            raise S.SecureError("E_GOOGLE_REFRESH", type(e).__name__) from None
        S.write_json(token_file(), json.loads(creds.to_json()), PURPOSE)
    return creds


def check() -> dict:
    if not token_file().exists():
        return {"ok": False, "code": "E_GOOGLE_NOT_CONNECTED"}
    try:
        creds = credentials()
    except S.SecureError as e:
        return {"ok": False, "code": e.code}
    cfg = C.load()
    if not cfg.get("sheet_id"):
        return {"ok": True, "code": "OK_TOKEN_ONLY", "sheet": "not_set"}
    try:
        from googleapiclient.discovery import build
        meta = build("sheets", "v4", credentials=creds, cache_discovery=False).spreadsheets().get(
            spreadsheetId=cfg["sheet_id"], fields="sheets.properties.title").execute()
        tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
        return {"ok": True, "code": "OK", "tabs": len(tabs), "request_tab_exists": cfg.get("sheet_tab") in tabs}
    except Exception as e:
        return {"ok": False, "code": "E_SHEET_ACCESS:" + type(e).__name__}


def forget() -> dict:
    n = 0
    for p in (token_file(), S.prev_path(token_file())):
        if p.exists():
            p.unlink()
            n += 1
    return {"ok": True, "code": "OK_DELETED_LOCAL" if n else "OK_NOTHING"}


def revoke() -> dict:
    """구글 쪽에서 이 앱의 권한을 끊는다. 로컬 토큰도 지운다. (실패해도 로컬은 지우지 않고 알려 준다)"""
    if not token_file().exists():
        return {"ok": True, "code": "OK_NOTHING"}
    try:
        info = S.read_json(token_file(), PURPOSE)
    except S.SecureError as e:
        return {"ok": False, "code": e.code}
    tok = info.get("refresh_token") or info.get("token")
    try:
        req = urllib.request.Request("https://oauth2.googleapis.com/revoke",
                                     data=urllib.parse.urlencode({"token": tok}).encode(),
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        code = urllib.request.urlopen(req, timeout=20).status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception as e:
        return {"ok": False, "code": "E_REVOKE_NETWORK:" + type(e).__name__}
    if code in (200, 400):            # 400 = 이미 무효인 토큰
        forget()
        return {"ok": True, "code": "OK_REVOKED" if code == 200 else "OK_ALREADY_INVALID"}
    return {"ok": False, "code": f"E_REVOKE_HTTP_{code}"}


def main(argv=None) -> int:
    cmd = (argv or sys.argv[1:] or ["check"])[0]
    fn = {"connect": connect, "check": check, "forget": forget, "revoke": revoke}.get(cmd)
    if not fn:
        print("사용법: python -m riro_merit.google_sheet connect|check|forget|revoke")
        return 2
    res = fn()
    print("RESULT_JSON " + json.dumps(res, ensure_ascii=False), flush=True)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

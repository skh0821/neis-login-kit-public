# -*- coding: utf-8 -*-
"""디벗 연결 — 전용 브라우저에 남은 구글 세션을 다시 쓰고, 응답 구조만 기록한다.

  python -m riro_merit.divot connect      저장된 세션 우선. 새 인증이 필요하면 전용 창에서 기다린다 (최대 8분).
  python -m riro_merit.divot forget       이 PC 의 디벗 전용 브라우저 프로필 지우기 (로컬만)

2026-11-09 공개 화면 점검(site_probe divot): Vite 앱 · Firebase 인증(구글 로그인, getIdToken) ·
Apps Script 웹 앱(script.google.com/macros/s/…/exec)에 POST. 그래서 «디벗 연결» = 디벗 화면에서 구글 로그인이다.

학생 정보 보호
  · Apps Script 응답은 이 프로세스 안에서만 보고, 밖으로는 «모양»만 낸다 — 키 이름(영문 식별자만, 그 밖은 <key>),
    값의 종류(str·num·bool), 학번처럼 생긴 숫자 문자열은 'str:digits5' 같은 표시, 배열은 길이만.
  · 이름·학번·값 자체는 출력·파일 어디에도 쓰지 않는다. 구조는 %LOCALAPPDATA%\\RiroMeritAutomation\\divot_schema.json.
  · Firebase 로그인 상태는 브라우저 프로필(IndexedDB)에 «브라우저 방식»으로 남는다 — 별도 암호화되지 않으니
    전용 프로필 폴더를 지우면(forget) 이 PC 의 로그인도 사라진다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.parse

from secure_store import _origin

from . import config as C

WAIT_SEC = 8 * 60
REUSE_WAIT_SEC = 15
GOOGLE_SIGN_IN = "학교 구글 계정으로 로그인"
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,30}$")


def shape(v, depth: int = 0):
    """값을 지우고 모양만 남긴다."""
    if depth > 6:
        return "…"
    if isinstance(v, dict):
        out = {}
        for k, x in list(v.items())[:40]:
            key = k if isinstance(k, str) and IDENT.match(k) else "<key>"
            if key == "<key>" and "<key>" in out:
                continue                      # 학번·이름이 키인 객체 — 하나만 모양을 본다
            out[key] = shape(x, depth + 1)
        return out
    if isinstance(v, list):
        return {"<list>": len(v), "item": shape(v[0], depth + 1) if v else None}
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "num"
    if v is None:
        return "null"
    s = str(v)
    if re.fullmatch(r"\d{4,6}", s):
        return f"str:digits{len(s)}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}([T ].*)?", s):
        return "str:date"
    if re.search(r"[가-힣]", s):
        return "str:hangul"
    if re.fullmatch(r"[^@\s]+@[^@\s]+", s):
        return "str:email"
    return "str"


def _launch(p, *, headless):
    prof = C.browser_dir("divot")
    prof.mkdir(parents=True, exist_ok=True)
    from .riro_site import _prefs_no_password_save, CHROME_ARGS, IGNORE_DEFAULT_ARGS
    _prefs_no_password_save(prof)
    for ch in ("chrome", "chromium"):
        try:
            return p.chromium.launch_persistent_context(str(prof), channel=ch, headless=headless, args=CHROME_ARGS,
                                                        ignore_default_args=IGNORE_DEFAULT_ARGS, viewport=None,
                                                        timeout=15_000)
        except Exception:
            if ch == "chromium":
                raise


def _has_firebase_session(page):
    # 인증 레코드 키가 있는지만 본다. 토큰이나 계정 값은 꺼내지 않는다.
    return page.evaluate("""async () => {
        const isUser = key => typeof key === 'string' && key.startsWith('firebase:authUser:');
        if (Object.keys(localStorage).some(isUser)) return true;
        const dbs = indexedDB.databases ? await indexedDB.databases() : [];
        if (!dbs.some(db => db.name === 'firebaseLocalStorageDb')) return false;
        return new Promise(resolve => {
            const request = indexedDB.open('firebaseLocalStorageDb');
            request.onerror = () => resolve(false);
            request.onsuccess = () => {
                const db = request.result;
                const finish = result => { db.close(); resolve(result); };
                if (!db.objectStoreNames.contains('firebaseLocalStorage')) return finish(false);
                const keys = db.transaction('firebaseLocalStorage', 'readonly')
                    .objectStore('firebaseLocalStorage').getAllKeys();
                keys.onsuccess = () => finish(keys.result.some(isUser));
                keys.onerror = () => finish(false);
            };
        });
    }""")


def _connect_in_browser(p, url, *, headless, cancel):
    from playwright.sync_api import Error

    origin = _origin(url)
    start_sign_in = not headless and origin == _origin(C.DIVOT_URL)
    seen = {"apps_script_json": 0, "apps_script_post": 0, "shapes": []}

    def on_response(resp):
        address = urllib.parse.urlsplit(resp.url)
        if not resp.ok or address.scheme != "https" or address.hostname not in ("script.google.com", "script.googleusercontent.com"):
            return
        try:
            data = resp.json()
        except (Error, ValueError):
            return
        if isinstance(data, dict) and (data.get("ok") is False or data.get("success") is False):
            return
        if resp.request.method == "POST":
            seen["apps_script_post"] += 1
        seen["apps_script_json"] += 1
        if len(seen["shapes"]) < 6:
            seen["shapes"].append(shape(data))

    ctx = None
    try:
        if cancel.is_set():
            return {"ok": False, "code": "CANCELLED"}, []
        ctx = _launch(p, headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("response", on_response)
        if not cancel.is_set():
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        end = time.monotonic() + (REUSE_WAIT_SEC if headless else WAIT_SEC)
        signed = False
        while not cancel.is_set() and time.monotonic() < end and not page.is_closed():
            on_divot = _origin(page.url) == origin
            try:
                signed = on_divot and _has_firebase_session(page)
            except Error:
                signed = False
            if cancel.is_set():
                break
            if signed and seen["apps_script_json"]:
                page.wait_for_timeout(3000)   # 첫 목록의 나머지 응답도 받는다.
                break
            if start_sign_in and on_divot:
                button = page.get_by_role("button", name=GOOGLE_SIGN_IN, exact=True)
                if button.count() == 1 and button.is_visible():
                    start_sign_in = False
                    try:
                        button.click(timeout=2000)
                    except Error:
                        pass                  # 열린 디벗 창에서 계속할 수 있다.
            page.wait_for_timeout(750)
        ok = bool(signed and seen["apps_script_json"])
        return {"ok": ok, "code": "OK" if ok else ("E_NO_DATA_RESPONSE" if signed else "E_NOT_SIGNED_IN"),
                "signed_in": signed, "session_reused": bool(ok and headless),
                "apps_script_json_responses": seen["apps_script_json"],
                "apps_script_posts": seen["apps_script_post"]}, seen["shapes"]
    except Error as exc:
        return {"ok": False, "code": "E_BROWSER:" + type(exc).__name__}, []
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Error:
                pass


def connect(*, cancel=None) -> dict:
    from playwright.sync_api import sync_playwright

    if cancel is None:
        cancel = threading.Event()
    url = C.load().get("divot_url") or C.DIVOT_URL
    if not url:                                   # 해커톤 앱 주소가 설정창·우리학교_설정.json 어디에도 없다
        return {"ok": False, "code": "E_NO_DIVOT_URL"}
    try:
        origin = _origin(url)
    except ValueError:
        origin = None
    if not origin or origin[0] != "https" or not origin[1]:
        return {"ok": False, "code": "E_ORIGIN"}

    res, shapes = {"ok": False}, []
    with sync_playwright() as p:
        if (C.browser_dir("divot") / "Local State").is_file():
            print("STEP 저장된 디벗 로그인 확인 중", flush=True)
            res, shapes = _connect_in_browser(p, url, headless=True, cancel=cancel)
        if not res["ok"] and not cancel.is_set():
            print("STEP 디벗 창을 엽니다 — 구글 인증이 필요하면 열린 창에서 진행해 주세요 (최대 8분)", flush=True)
            res, shapes = _connect_in_browser(p, url, headless=False, cancel=cancel)
    if cancel.is_set():
        return {"ok": False, "code": "CANCELLED"}
    if res["ok"] and shapes:
        path = C.base_dir() / "divot_schema.json"
        try:
            path.write_text(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M"), "shapes": shapes},
                                       ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            return {**res, "ok": False, "code": "E_SCHEMA_SAVE"}
        res["schema_saved"] = True
        res["schema"] = shapes[:3]
    return res


def forget() -> dict:
    prof = C.browser_dir("divot")
    if not prof.exists():
        return {"ok": True, "code": "OK_NOTHING"}
    try:
        shutil.rmtree(prof)
        return {"ok": True, "code": "OK_DELETED_LOCAL_PROFILE"}
    except OSError as e:
        return {"ok": False, "code": "E_DELETE:" + type(e).__name__}


def main(argv=None) -> int:
    args = argv or sys.argv[1:] or ["connect"]
    cmd = args[0]
    fn = {"connect": connect, "forget": forget}.get(cmd)
    if not fn:
        print("사용법: python -m riro_merit.divot connect|forget")
        return 2
    if cmd == "connect" and "--cancel-on-eof" in args[1:]:
        cancel = threading.Event()

        def watch_settings():
            try:
                os.read(sys.stdin.fileno(), 1)
            except OSError:
                pass
            cancel.set()

        threading.Thread(target=watch_settings, daemon=True).start()
        res = connect(cancel=cancel)
    else:
        res = fn()
    print("RESULT_JSON " + json.dumps(res, ensure_ascii=False), flush=True)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""리로스쿨 로그인 — 다른 코드에서 다시 쓸 수 있는 로그인 함수 + 로그인 시험·로그아웃. 상벌점 등록 요청은 보내지 않는다.

  python -m riro_merit.riro_site login-test     전용 크롬 창으로 로그인까지만 확인 (결과 코드만 출력)
  python -m riro_merit.riro_site logout         서비스 쪽 세션 끝내기 (사이트에서 로그아웃)
  python -m riro_merit.riro_site forget-browser 이 PC 의 리로스쿨 전용 브라우저 프로필 지우기 (로컬만)

다른 코드에서 쓰는 법 (나중의 상벌점 등록 단계 등):

    from riro_merit import riro_site
    with riro_site.session() as (page, res):      # 전용 크롬 → 로그인 (이미 로그인돼 있으면 그대로)
        if not res["ok"]:
            ...                                    # res["code"] 로만 판단 (값·화면 글자 없음)
        page.goto(...)                             # 로그인된 page 로 이어서

로그인 화면 구조 (2026-11-09 공개 로그인 화면 점검 site_probe):
  아이디 #id · 비밀번호 #pw · [로그인] button.button_normal · 추가 인증 #otp_input + #otp_btn_confirm · 알림 .btn_alert_confirm
  로그인 «뒤» 화면 구조는 아직 확인 못 했다 → 로그아웃 단추가 보이면 OK, 로그인 칸이 사라지기만 했으면 OK_UNCONFIRMED.

안전장치
  · 아이디·비밀번호는 금고(riro-merit/<학교>.riroschool.kr)에만 있다. 이 코드는 «금고 표»만 들고 있다가,
    비밀번호 칸에 넣기 «직전»에 꺼내고 바로 참조를 끊는다 (파이썬 str 이라 메모리에서 지워진다는 보장은 없다).
  · 입력 오류 원문(넣으려던 값이 섞일 수 있음)은 버리고 코드만 낸다. 화면 알림 글자도 밖으로 내지 않고 코드로 바꾼다.
  · 한 번 로그인에 [로그인]은 «한 번만» 누른다. 비밀번호 거절·잠금이면 기록해 두고, 설정창에서 새로 저장하기 전에는 다시 시도하지 않는다.
  · 추가 인증(OTP)·보안문자는 넣지 않는다 — 열린 창에서 선생님이 직접 (최대 6분 기다림).
"""
from __future__ import annotations

import contextlib
import json
import shutil
import sys
import time
from datetime import datetime

from . import config as C
from secure_store import SecureError, _origin, check_origin, origin_text, reveal_for, scrub_common

ALLOWED = [f"https://{C.SCHOOL_HOST}"]       # 아이디·비밀번호를 넣어도 되는 곳 — 학교 리로스쿨 하나 (https·443)

WAIT_HUMAN_SEC = 6 * 60          # 추가 인증(OTP·보안문자)을 선생님이 넣는 동안
SUBMIT_WAIT_SEC = 25             # [로그인] 뒤 답을 기다리는 시간
CHROME_ARGS = ["--no-first-run", "--no-default-browser-check"]   # 자동화임을 숨기는 설정은 넣지 않는다
# Playwright 가 기본으로 붙이는 것 중 뺄 것 — --no-sandbox 를 빼야 크롬 보호벽(샌드박스)이 켜진 채로 뜬다 (디벗 창도 같이 쓴다)
IGNORE_DEFAULT_ARGS = ["--enable-automation", "--no-sandbox"]
_clock = time.monotonic          # 시험에서 바꿔 끼운다
LOCK_CODES = ("E_BAD_CREDENTIALS", "E_ACCOUNT_LOCKED")


def out(kind: str, msg: str = "") -> None:
    print(f"{kind} {msg}".rstrip(), flush=True)


def classify_alert(text: str) -> str:
    """알림 문구를 코드로 — 원문은 밖으로 내지 않는다."""
    t = (text or "").replace(" ", "")
    if any(k in t for k in ("잠금", "잠겼", "제한", "초과")):
        return "E_ACCOUNT_LOCKED"
    if "비밀번호" in t and any(k in t for k in ("일치", "틀", "잘못", "확인")):
        return "E_BAD_CREDENTIALS"
    if "아이디" in t and any(k in t for k in ("없", "존재", "확인", "잘못")):
        return "E_BAD_CREDENTIALS"
    if "인증" in t:
        return "E_EXTRA_AUTH"
    return "E_LOGIN_ALERT"


def _prefs_no_password_save(profile) -> None:
    """이 전용 프로필에서 크롬 «비밀번호 저장» 을 끈다 (크롬이 따로 사본을 만들지 않게)."""
    pref = profile / "Default" / "Preferences"
    try:
        data = json.loads(pref.read_text(encoding="utf-8")) if pref.exists() else {}
    except Exception:
        return
    data["credentials_enable_service"] = False
    data.setdefault("profile", {})["password_manager_enabled"] = False
    pref.parent.mkdir(parents=True, exist_ok=True)
    pref.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _launch(p, profile, headless: bool = False):
    _prefs_no_password_save(profile)
    for ch in ("chrome", None):
        try:
            # --no-sandbox 도 뺀다 — Playwright 기본값이 크롬 «샌드박스»(보호벽)를 꺼서 경고 줄이 떴다 (2026-11-10 선생님 제보)
            return p.chromium.launch_persistent_context(str(profile), channel=ch, headless=headless, args=CHROME_ARGS,
                                                        ignore_default_args=IGNORE_DEFAULT_ARGS, viewport=None)
        except Exception:
            if ch is None:
                raise


def _visible(page, sel: str) -> bool:
    try:
        loc = page.locator(sel).first
        return loc.count() > 0 and loc.is_visible()
    except Exception:
        return False


def _needs_human(page) -> bool:
    """추가 인증(OTP)·보안문자가 보이나."""
    try:
        return _visible(page, "#otp_input") or \
            page.locator("iframe[src*='captcha'], iframe[src*='recaptcha'], iframe[src*='hcaptcha']").count() > 0
    except Exception:
        return False


def login_state(page) -> str:
    """'form'(로그인 칸 보임) / 'human'(추가 인증) / 'in'(로그아웃 단추까지 보임) / 'in_unconfirmed' / 'unknown'."""
    try:
        if _needs_human(page):
            return "human"
        if "action=signin" in page.url or _visible(page, "#pw"):
            return "form"
        # 로그인 화면의 숨은 «지금 로그아웃»(자동 로그아웃 안내) 단추와 헷갈리지 않게 «보이는» 정확한 글자만 본다
        on_school = (_origin(page.url) or (None, None))[:2] == ("https", C.SCHOOL_HOST)   # 글자 포함이 아니라 «정확한 호스트»
        if not on_school:
            return "unknown"
        if page.get_by_text("로그아웃", exact=True).first.is_visible(timeout=800):
            return "in"
        return "in_unconfirmed"
    except Exception:
        return "unknown"


def _locked(cfg) -> bool:
    lock = cfg.get("login_lock") or {}
    return lock.get("rev") == cfg.get("cred_rev") and lock.get("code") in LOCK_CODES


def _record_lock(code: str) -> None:
    cfg = C.load()
    cfg["login_lock"] = {"rev": cfg.get("cred_rev"), "code": code, "at": datetime.now().isoformat(timespec="seconds")}
    C.save(cfg)


def login(page) -> dict:
    """열린 page 에서 리로스쿨에 로그인한다. 돌려주는 것: {'ok': bool, 'code': 코드} — 값·화면 글자 없음.

    이미 로그인돼 있으면 아무것도 넣지 않고 OK_ALREADY_SIGNED_IN.
    """
    if not C.SCHOOL_HOST:
        return {"ok": False, "code": "E_NO_SCHOOL"}
    cfg = C.load()
    if _locked(cfg):
        return {"ok": False, "code": "E_LOGIN_LOCKED_UNTIL_CHANGED"}
    try:
        uid, ticket = C.credential_ticket()          # 아이디 + 금고 표 — 비밀번호 자체는 아직 꺼내지 않는다
        if not (uid and ticket and ticket.exists()):
            return {"ok": False, "code": "E_NO_CREDENTIALS"}
    except SecureError as e:
        return {"ok": False, "code": e.code}
    alerts: list[str] = []

    def on_dialog(d):
        alerts.append(d.message)                     # 글자는 코드로 바꾸는 데만 쓰고 밖으로 내지 않는다
        try:
            d.dismiss()
        except Exception:
            pass
    page.on("dialog", on_dialog)
    try:
        out("STEP", "리로스쿨 로그인 화면 여는 중")
        # 설정 파일의 주소가 아니라 «코드에 박힌 학교 주소»로만 간다 (설정이 바뀌어도 다른 곳으로 가지 않게)
        page.goto(C.RIRO_SIGNIN, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(1200)
        st = login_state(page)
        if st in ("in", "in_unconfirmed"):
            return {"ok": True, "code": "OK_ALREADY_SIGNED_IN" if st == "in" else "OK_ALREADY_SIGNED_IN_UNCONFIRMED"}
        page.locator("#id").first.wait_for(state="visible", timeout=15_000)
        try:                                         # 아이디를 넣기 «전»에 주소 확인 (리디렉션 뒤 https·호스트·포트)
            check_origin(page.url, ALLOWED)
        except SecureError:
            return {"ok": False, "code": "E_ORIGIN", "host": origin_text(page.url)}
        page.locator("#id").first.fill(uid)
        secret = None
        try:
            secret = reveal_for(ticket, page.url, ALLOWED)   # ← 주소를 한 번 더 확인한 «뒤»에만 금고에서 꺼낸다
            page.locator("#pw").first.fill(secret)
        except SecureError as e:
            return {"ok": False, "code": e.code}     # E_ORIGIN 이면 금고는 열리지 않았다
        except Exception:
            return {"ok": False, "code": "E_FILL"}   # 오류 원문(넣으려던 값이 섞일 수 있음)은 버린다
        finally:
            secret = None                            # 참조만 끊는다 (str 이라 지워진다는 보장은 없다)
        btn = page.locator("#pw").locator("xpath=following::button[normalize-space()='로그인'][1]")
        if not btn.count():
            btn = page.locator("button.button_normal", has_text="로그인")
        out("STEP", "아이디·비밀번호 넣고 [로그인] 한 번")
        btn.first.click()                            # ← 제출은 이 한 번뿐. 아래 어디서도 다시 누르지 않는다
        waited, res = False, None
        deadline = _clock() + SUBMIT_WAIT_SEC
        while _clock() < deadline:
            if page.is_closed():
                res = {"ok": False, "code": "E_WINDOW_CLOSED"}
                break
            page.wait_for_timeout(700)
            st = login_state(page)
            if st in ("in", "in_unconfirmed"):
                code = "OK" if st == "in" else "OK_UNCONFIRMED"     # 로그아웃 단추를 못 봤으면 «확인 덜 됨»
                res = {"ok": True, "code": code + ("_AFTER_EXTRA_AUTH" if waited else "")}
                break
            if st == "human":
                if not waited:
                    out("STEP", "추가 인증(OTP·보안문자)이 떴습니다 — 열린 창에서 직접 넣고 [확인] 해 주세요 (최대 6분)")
                    waited, deadline = True, _clock() + WAIT_HUMAN_SEC
                continue
            if not waited and (alerts or _visible(page, ".btn_alert_confirm")):
                txt = " ".join(alerts)
                if not txt:
                    try:
                        txt = page.locator(".btn_alert_confirm").first.locator(
                            "xpath=ancestor::*[self::div][2]").inner_text(timeout=1000)
                    except Exception:
                        txt = ""
                res = {"ok": False, "code": classify_alert(txt)}
                txt = None
                break
        res = res or {"ok": False, "code": "E_EXTRA_AUTH_TIMEOUT" if waited else "E_LOGIN_NO_RESPONSE"}
    except Exception as e:
        res = {"ok": False, "code": "E_BROWSER:" + type(e).__name__}    # 오류 «종류»만
    finally:
        alerts.clear()
    if res["code"] in LOCK_CODES:
        _record_lock(res["code"])                    # 같은 비밀번호로는 다시 시도하지 않게
    return json.loads(scrub_common(json.dumps(res, ensure_ascii=False)))


@contextlib.contextmanager
def session(headless: bool = False):
    """전용 크롬(리로스쿨 프로필)을 열고 로그인한 page 를 넘긴다. 끝나면 크롬을 닫는다.

    with session() as (page, res): …   res = {'ok','code'}. 로그인이 안 됐어도 page 는 넘긴다 (부른 쪽이 res 로 판단).
    추가 인증이 뜰 수 있으니 기본은 «창 보이기»(headless=False)."""
    from playwright.sync_api import sync_playwright
    C.ensure_dirs()
    with sync_playwright() as p:
        ctx = _launch(p, C.browser_dir("riro"), headless=headless)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            yield page, login(page)
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def login_test() -> dict:
    """로그인까지만 확인하고 창을 닫는다 (보안 설정창의 [로그인 시험])."""
    cfg = C.load()
    if _locked(cfg):                                 # 브라우저도 띄우지 않는다
        return {"ok": False, "code": "E_LOGIN_LOCKED_UNTIL_CHANGED",
                "hint": "지난번 로그인이 거절됐습니다. 설정창에서 아이디·비밀번호를 다시 저장한 뒤 시험하세요"}
    try:
        with session() as (_page, res):
            return res
    except Exception as e:
        return {"ok": False, "code": "E_BROWSER:" + type(e).__name__}


def logout() -> dict:
    """서비스 쪽 세션을 끝낸다 — 전용 프로필로 리로스쿨을 열어 [로그아웃]. (로컬 프로필은 지우지 않는다)"""
    from playwright.sync_api import sync_playwright
    prof = C.browser_dir("riro")
    if not prof.exists():
        return {"ok": True, "code": "OK_NO_LOCAL_SESSION"}
    with sync_playwright() as p:
        ctx = _launch(p, prof)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.on("dialog", lambda d: d.accept())
            page.goto(f"https://{C.SCHOOL_HOST}/", wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(1500)
            if login_state(page) == "form":
                return {"ok": True, "code": "OK_ALREADY_SIGNED_OUT"}
            link = page.get_by_text("로그아웃", exact=True).first
            if not link.count() or not link.is_visible():
                return {"ok": False, "code": "E_LOGOUT_CONTROL_NOT_FOUND"}
            link.click()
            page.wait_for_timeout(2500)
            page.goto(C.RIRO_SIGNIN, wait_until="domcontentloaded", timeout=30_000)
            st = login_state(page)
            return {"ok": st == "form", "code": "OK" if st == "form" else "E_STILL_SIGNED_IN"}
        except Exception as e:
            return {"ok": False, "code": "E_BROWSER:" + type(e).__name__}
        finally:
            ctx.close()


def forget_browser() -> dict:
    prof = C.browser_dir("riro")
    if not prof.exists():
        return {"ok": True, "code": "OK_NOTHING"}
    try:
        shutil.rmtree(prof)
        return {"ok": True, "code": "OK_DELETED_LOCAL_PROFILE"}
    except OSError as e:
        return {"ok": False, "code": "E_DELETE:" + type(e).__name__ + " (크롬 창이 열려 있으면 닫고 다시)"}


def main(argv=None) -> int:
    cmd = (argv or sys.argv[1:] or ["login-test"])[0]
    fn = {"login-test": login_test, "logout": logout, "forget-browser": forget_browser}.get(cmd)
    if not fn:
        print("사용법: python -m riro_merit.riro_site login-test|logout|forget-browser")
        return 2
    res = fn()
    print("RESULT_JSON " + json.dumps(res, ensure_ascii=False), flush=True)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

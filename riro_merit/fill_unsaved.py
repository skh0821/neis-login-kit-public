# -*- coding: utf-8 -*-
"""디벗 «미보관» 학번 → 리로스쿨 «상벌점 점수 입력» 빈 학번 칸 (2026-11-10, 선생님 영상 «상벌점 기능» 그대로).

  python -m riro_merit.fill_unsaved [--date YYYY-MM-DD] [--kind unsaved|cable]      날짜를 안 주면 오늘
      --kind unsaved (기본)  «미보관 (N)» 탭의 미보관 학생
      --kind cable           «케이블 미연결 (N)» 탭의 케이블 미연결 학생 (2026-11-10 추가 — 선생님이 «기타» 탭과 나눠 두셨다)

1. 디벗 전용 창(저장된 구글 로그인 — 없으면 창을 보여 선생님이 직접 로그인)에서 그 날짜의 «미보관» 명렬표를 읽는다.
   ★ 학년·학급·번호 칸만 읽어 학번 5자리를 만든다 (1학년 2반 22번 → 10222). «학생 성명» 칸은 읽지 않는다.
2. 리로스쿨 전용 창(금고의 아이디·비밀번호 · 추가 인증은 선생님)으로 «점수 입력» 화면을 열고,
   «빈» 카드(학번·이름 둘 다 빈 칸)에만 차례로 «타자 치듯» 넣는다 — 리로스쿨은 키를 뗄 때(chk_s_num) 학생을 찾는다.
3. [저장]·항목·점수·비밀번호는 건드리지 않는다. 다 넣으면 «연결만 끊고» 끝난다 — 리로스쿨 창은 그대로 남아 선생님께 넘어간다.
   (크롬은 평범하게 따로 띄운다 — 자동화가 붙어 있는 동안은 확인 창이 자동 취소돼 [일괄적용]·[저장]이 먹통이었다, 11-10)

4. 학번을 다 넣은 «마지막에» 디벗 «사유 및 특이사항» 문구를 클립보드에 넣는다 (2026-11-10 선생님 요청) — 내용 칸에 Ctrl+V.
   문구는 늘 같다고 하셔서 «맨 위 한 줄»만 읽는다. 클립보드 기록(Win+V)·클라우드 동기화에는 남기지 말라는 표시를 같이 넣는다.

출력 (상벌점 창이 읽는다)
  STEP <하는 일>   COUNT <그 탭 학생 수>   LEFT <학번,학번…> (빈칸이 모자라거나 학생을 못 찾은 학번)
  CLIP <1 = 넣음 · 0 = 사유 칸이 비어 있음>   CLIPFAIL   READY <채운 수>   FAIL <이유>      (사유 문구 자체는 출력하지 않는다)

디벗 로그인이 풀려 있으면 창을 보여 [학교 구글 계정으로 로그인]을 누르고, 구글 «계정 선택»에 저장된 계정이 «딱 하나»면
그 계정을 누른다 (선생님 11-10). 비밀번호·추가 인증 화면이 나오면 선생님이 직접.
학번은 상벌점 창 화면으로만 넘기고 파일·기록에는 남기지 않는다. 학생 이름은 어디서도 읽지 않는다
(리로스쿨은 학번을 넣는 순간 이름·사진을 띄우지만, 이 프로그램은 «이름 칸이 채워졌는지» 참/거짓만 본다).

화면 구조는 2026-11-10 page_shape 점검(값 없음)으로 정했다 — 디벗 표 머리글 «학년·학급·번호·학생 성명·보관 상태…»,
날짜 칸 input.session-date-input, «미보관 (N)» 탭 / 리로스쿨 카드 30개, 학번 s_num[i](maxlength 5, onkeyup=chk_s_num(i)), 이름 s_name[i].
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

from . import config as C

WRITE_FORM = f"https://{C.SCHOOL_HOST}/record.php?db=record&action=write_form"

# ── 크롬은 «평범하게 따로» 띄우고 연결만 붙였다가 끊는다 (2026-11-10, 나이스 바로가기 엔진과 같은 방식) ──
# 예전에는 자동화 도구가 크롬을 띄우고 «끝까지 붙잡고» 있었다. 그동안은 페이지의 확인 창(confirm·alert)이 «자동 취소» 돼
# 리로스쿨 [일괄적용]·[저장]이 먹통이었다 (선생님 제보 · 빈 프로필 시험: 붙어 있는 동안 confirm → false).
# 또 그 방식은 자동화 표시를 숨기려고 --disable-blink-features=AutomationControlled 를 붙여야 해서 크롬 경고 줄이 떴다.
# 따로 띄운 크롬은 그 설정 없이도 navigator.webdriver=false 이고, 연결을 끊으면 확인 창이 선생님 답을 기다린다 (시험 확인).
# 조작 포트(127.0.0.1)는 리로스쿨 창이 떠 있는 동안 열려 있다 — 나이스 위젯 크롬과 같다. 창을 닫으면 닫힌다.
RIRO_PORT = int(os.environ.get("RIRO_CDP_PORT", "9326"))
DIVOT_PORT = int(os.environ.get("DIVOT_CDP_PORT", "9327"))
PLAIN_FLAGS = ["--no-first-run", "--no-default-browser-check"]      # 샌드박스 끄기·자동화 표시 숨기기 없음


def chrome_exe() -> str:
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
        if base:
            exe = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if exe.exists():
                return str(exe)
    raise RuntimeError("구글 크롬(chrome.exe)을 찾지 못했습니다 — 크롬을 설치해 주세요")


def cdp_alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
            return bool(json.load(r).get("Browser"))
    except Exception:
        return False


def profile_locked(profile: Path) -> bool:
    """이 프로필로 크롬이 (조작 포트 없이) 떠 있나 — 떠 있는 동안 lockfile 을 쥐고 있다."""
    lock = Path(profile) / "lockfile"
    if not lock.exists():
        return False
    try:
        lock.unlink()
        return False
    except OSError:
        return True


def launch_plain(profile: Path, port: int, headless: bool) -> None:
    """파이썬과 «남남»인 크롬을 띄운다 — 이 프로그램이 끝나도 창은 남는다."""
    from . import riro_site as R
    profile.mkdir(parents=True, exist_ok=True)
    R._prefs_no_password_save(profile)
    args = [chrome_exe(), f"--user-data-dir={profile}", f"--remote-debugging-port={port}", *PLAIN_FLAGS,
            *(["--headless=new"] if headless else ["--start-maximized"]), "about:blank"]
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    base = 0x00000008 | 0x00000200                          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(args, creationflags=base | 0x01000000, **kw)   # CREATE_BREAKAWAY_FROM_JOB
    except OSError:
        subprocess.Popen(args, creationflags=base, **kw)
    for _ in range(120):
        if cdp_alive(port):
            return
        time.sleep(0.25)
    raise RuntimeError("크롬이 뜨지 않았습니다")


def attach(p, profile: Path, port: int, *, headless: bool, name: str):
    """(browser, page, 방금 띄웠나). 이미 떠 있으면 거기에 «새 탭»으로 붙는다 (앞서 쓰던 탭은 건드리지 않는다)."""
    fresh = False
    if not cdp_alive(port):
        if profile_locked(profile):
            raise RuntimeError(f"{name} 전용 크롬이 이미 떠 있습니다 — 그 창을 닫고 다시 눌러 주세요")
        launch_plain(profile, port, headless)
        fresh = True
    try:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=20_000)
    except Exception:
        raise RuntimeError(f"{name} 창에 붙지 못했습니다 — 그 창에 답하지 않은 확인 창이 있으면 먼저 답하고 다시 눌러 주세요") from None
    ctx = browser.contexts[0]
    page = ctx.pages[0] if (fresh and ctx.pages) else ctx.new_page()
    return browser, page, fresh


def close_browser(browser) -> None:
    try:
        browser.new_browser_cdp_session().send("Browser.close")
    except Exception:
        pass

# 어느 탭의 학생을 넣나 — 탭 이름(개수 붙음)과 «보관 상태» 칸 글자. 11-10 탭 점검: 미보관 15 = 상태 «미보관» 15,
# 케이블 미연결 12 = 상태 «케이블 미연결» 12, 기타 4 = 상태 «기타» 4 (기타는 넣지 않는다)
KINDS = {
    "unsaved": {"label": "미보관", "status": "미보관", "tab": r"^미보관\s*\((\d+)\)$"},
    "cable": {"label": "케이블 미연결", "status": "케이블 미연결", "tab": r"^케이블 미연결\s*\((\d+)\)$"},
}

READ_JS = r"""(a) => {
  const table = [...document.querySelectorAll('table')].find(t => {
    const h = [...t.querySelectorAll('thead th')].map(x => x.textContent.trim());
    return h.includes('학년') && h.includes('학급') && h.includes('번호'); });
  const re = new RegExp(a.tab);
  const tab = [...document.querySelectorAll('button')].map(b => b.textContent.replace(/\s+/g, ' ').trim())
    .map(t => t.match(re)).find(Boolean);
  const out = {table: !!table, tab_count: tab ? +tab[1] : null, sids: [], reason: '', bad: 0, other_status: 0};
  if (!table) return out;
  const h = [...table.querySelectorAll('thead th')].map(x => x.textContent.trim());
  const ig = h.indexOf('학년'), ic = h.indexOf('학급'), ino = h.indexOf('번호'), ist = h.indexOf('보관 상태');
  // «사유 및 특이사항» — 문구는 늘 같다(선생님 11-10) → 넣는 학생 중 «맨 위 한 줄»만 읽는다. 마지막에 클립보드로 (화면·기록에는 안 냄)
  const irs = h.findIndex(x => x.startsWith('사유'));
  for (const r of table.querySelectorAll('tbody tr')) {
    const c = [...r.children];
    if (c.length < h.length) continue;                       // «학생이 없습니다» 같은 안내 줄
    if (ist >= 0 && c[ist].textContent.trim() !== a.status) { out.other_status++; continue; }
    const g = (c[ig].textContent.match(/^\s*(\d)\s*학년\s*$/) || [])[1];
    const k = (c[ic].textContent.match(/^\s*(\d{1,2})\s*반\s*$/) || [])[1];
    const n = (c[ino].textContent.match(/^\s*(\d{1,2})\s*번\s*$/) || [])[1];
    if (!g || !k || !n) { out.bad++; continue; }
    out.sids.push(g + k.padStart(2, '0') + n.padStart(2, '0'));
    if (out.sids.length === 1 && irs >= 0) out.reason = c[irs].textContent.replace(/\s+/g, ' ').trim();
  }
  return out;
}"""

BLANK_JS = r"""() => {
  const s = [...document.querySelectorAll('input[name^="s_num["]')], n = [...document.querySelectorAll('input[name^="s_name["]')];
  return s.map((e, i) => !e.value.trim() && !(n[i] && n[i].value.trim()));
}"""
PICKED_JS = r"""i => { const n = document.querySelectorAll('input[name^="s_name["]')[i]; return !!(n && n.value.trim()); }"""


def out(kind: str, msg: str = "") -> None:
    print(f"{kind} {msg}".rstrip(), flush=True)


# ───────────────────────── 디벗 ─────────────────────────

def open_divot_list(page, day: str, kind: str = "unsaved", *, settle_ms: int = 2500) -> None:
    """디벗 화면을 그 날짜 · 전체 학년 · «미보관»(또는 «케이블 미연결») 탭으로 맞춘다 (보기만 바꾼다 — 아무것도 저장하지 않는다)."""
    box = page.locator("input.session-date-input").first
    box.wait_for(state="visible", timeout=60_000)
    if box.input_value() != day:
        box.fill(day)
        page.wait_for_timeout(settle_ms)
    if box.input_value() != day:
        raise RuntimeError("디벗 날짜를 바꾸지 못했습니다")
    tab = re.compile(KINDS[kind]["tab"].replace(r"(\d+)", r"\d+"))
    for name in (re.compile(r"^전체 학년"), tab):
        b = page.get_by_role("button", name=name)
        if b.count() == 1 and "active" not in (b.get_attribute("class") or ""):
            b.click(timeout=5000)
            page.wait_for_timeout(1200)
    if page.get_by_role("button", name=tab).count() != 1:
        raise RuntimeError(f"디벗에 «{KINDS[kind]['label']}» 탭이 없습니다 — 해커톤 앱 탭 이름이 바뀌었는지 확인해 주세요")


def read_unsaved(page, day: str, kind: str = "unsaved") -> dict:
    """{'sids': [...], 'tab_count': N, 'bad': 줄 수, 'reason': 맨 위 사유} — 표가 다 그려질 때까지 두 번 같은 값이 나올 때를 기다린다."""
    open_divot_list(page, day, kind)
    arg = {"tab": KINDS[kind]["tab"], "status": KINDS[kind]["status"]}
    last, res = None, None
    for _ in range(20):
        res = page.evaluate(READ_JS, arg)
        key = (res["tab_count"], tuple(res["sids"]))
        if res["tab_count"] is not None and key == last and (res["table"] or res["tab_count"] == 0):
            break
        last = key
        page.wait_for_timeout(700)
    return res


def divot_page(p, from_D):
    """(browser, page, 우리가 띄웠나). 저장된 로그인으로 «창 없이» 먼저 해 보고, 로그인이 필요하면 창을 보여
    [학교 구글 계정으로 로그인] → 저장된 계정이 하나면 그걸 누르고 선생님을 기다린다 (최대 8분). 크롬은 평범하게 따로 띄운다."""
    url = C.load().get("divot_url") or C.DIVOT_URL
    if not url:
        raise RuntimeError(C.NO_DIVOT)
    prof = C.browser_dir("divot")
    for headless in (True, False):
        browser, page, fresh = attach(p, prof, DIVOT_PORT, headless=headless, name="디벗")
        visible = not (headless and fresh)               # 이미 떠 있던 디벗 창(지난번 로그인 창)에 붙었으면 보이는 창
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        if visible:
            out("STEP", "디벗 로그인 중 — 구글 계정 선택 창이 뜨면 저장된 계정을 누릅니다 (그 뒤 화면은 직접, 최대 8분)")
        end, clicked, picked = time.monotonic() + (8 * 60 if visible else 20), False, False
        while time.monotonic() < end and not page.is_closed():
            try:
                if page.locator("input.session-date-input").count():
                    return browser, page, fresh
                if visible and not clicked:
                    b = page.get_by_role("button", name=from_D.GOOGLE_SIGN_IN, exact=True)
                    if b.count() == 1 and b.is_visible():
                        clicked = True
                        b.click(timeout=2000)
                if visible and not picked and pick_only_google_account(browser.contexts[0]):
                    picked = True
                    out("STEP", "구글 계정 선택 창에서 저장된 계정을 눌렀습니다")
            except Exception:
                pass
            page.wait_for_timeout(800)
        if fresh:                                        # 창 없이 띄운 크롬은 닫고 «보이는 창»으로 다시
            close_browser(browser)
            for _ in range(40):
                if not cdp_alive(DIVOT_PORT) and not profile_locked(prof):
                    break
                time.sleep(0.25)
        else:
            try:
                page.close()
            except Exception:
                pass
        if visible:
            break
    raise RuntimeError("디벗에 로그인되지 않았습니다")


def pick_only_google_account(ctx) -> bool:
    """구글 «계정 선택» 화면에 저장된 계정이 «딱 하나»면 그 계정을 누른다 (선생님 11-10: «저장된 계정이 하나밖에 없으니 눌러 로그인»).
    둘 이상이면 누르지 않는다. 주소가 정확히 https://accounts.google.com 일 때만. 비밀번호·추가 인증은 넣지 않고,
    계정 주소(data-identifier 값)도 읽지 않는다 — «몇 개인지»만 센다."""
    from urllib.parse import urlsplit
    for pg in list(ctx.pages):
        try:
            u = urlsplit(pg.url)
            if u.scheme != "https" or u.hostname != "accounts.google.com":
                continue
            items = pg.locator("[data-identifier]:visible")
            if items.count() == 1:
                items.first.click(timeout=3000)
                return True
        except Exception:
            continue
    return False


def set_clipboard(text: str) -> bool:
    """윈도우 클립보드에 글자를 넣는다. «클립보드 기록(Win+V)»·«다른 기기와 동기화»에는 남기지 말라는 표시도 같이 넣는다
    (학생 관련 문구가 클라우드로 넘어가지 않게 — 윈도우가 정한 형식 이름 CanIncludeInClipboardHistory·CanUploadToCloudClipboard = 0)."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32.GlobalAlloc.restype, k32.GlobalAlloc.argtypes = wintypes.HGLOBAL, [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalLock.restype, k32.GlobalLock.argtypes = wintypes.LPVOID, [wintypes.HGLOBAL]
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    u32.SetClipboardData.restype, u32.SetClipboardData.argtypes = wintypes.HANDLE, [wintypes.UINT, wintypes.HANDLE]
    u32.RegisterClipboardFormatW.restype, u32.RegisterClipboardFormatW.argtypes = wintypes.UINT, [wintypes.LPCWSTR]

    def put(fmt: int, data: bytes) -> bool:
        h = k32.GlobalAlloc(0x0002, len(data))          # GMEM_MOVEABLE
        if not h:
            return False
        p = k32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        k32.GlobalUnlock(h)
        if not u32.SetClipboardData(fmt, h):
            k32.GlobalFree(h)
            return False
        return True                                     # 넣은 뒤에는 윈도우가 그 메모리를 맡는다

    for _ in range(20):                                 # 다른 프로그램이 잠깐 쥐고 있으면 기다렸다가
        if u32.OpenClipboard(None):
            break
        time.sleep(0.1)
    else:
        return False
    try:
        u32.EmptyClipboard()
        ok = put(13, (text + "\0").encode("utf-16-le"))   # CF_UNICODETEXT
        for name in ("CanIncludeInClipboardHistory", "CanUploadToCloudClipboard"):
            fmt = u32.RegisterClipboardFormatW(name)
            if fmt:
                put(fmt, (0).to_bytes(4, "little"))
        return ok
    finally:
        u32.CloseClipboard()


# ───────────────────────── 리로스쿨 ─────────────────────────

def fill_blanks(page, sids: list[str], *, pick_wait_ms: int = 6000) -> dict:
    """빈 카드에만 학번을 «타자 치듯» 넣는다. {'filled': n, 'left': [학번…], 'blank_cards': n} — 이름은 읽지 않는다."""
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(1), d.dismiss()))     # «없는 학번» 같은 알림 — 글자는 읽지 않는다
    blanks = [i for i, empty in enumerate(page.evaluate(BLANK_JS)) if empty]
    boxes = page.locator('input[name^="s_num["]')
    filled, left, b = 0, [], 0
    for k, sid in enumerate(sids):
        if b >= len(blanks):
            left.append(sid)                           # 빈칸이 모자라다 — 상벌점 창이 안내한다
            continue
        i = blanks[b]
        box = boxes.nth(i)
        box.scroll_into_view_if_needed(timeout=5000)
        box.click(timeout=5000)
        before = len(dialogs)
        box.press_sequentially(sid, delay=40)          # 키를 뗄 때마다 리로스쿨이 chk_s_num(i) 로 찾는다 — 붙여넣기와 같은 효과
        end = time.monotonic() + pick_wait_ms / 1000
        picked = False
        while time.monotonic() < end:
            if page.evaluate(PICKED_JS, i):
                picked = True
                break
            if len(dialogs) > before:
                break
            page.wait_for_timeout(150)
        if picked:
            filled += 1
            b += 1
        else:
            left.append(sid)                           # 못 찾은 학번(전출 등) — 칸을 비우고 그 칸을 다음 학번에 쓴다
            try:
                box.fill("")
            except Exception:
                pass
        out("STEP", f"학번 넣는 중 {k + 1}/{len(sids)}")
    return {"filled": filled, "left": left, "blank_cards": len(blanks)}


def riro_page(p, R):
    """(browser, page). 리로스쿨 전용 크롬을 평범하게 따로 띄우고(이미 떠 있으면 새 탭) 로그인 → 점수 입력 화면."""
    browser, page, fresh = attach(p, C.browser_dir("riro"), RIRO_PORT, headless=False, name="리로스쿨")

    def give_up(msg):
        try:
            close_browser(browser) if fresh else page.close()
        except Exception:
            pass
        raise RuntimeError(msg)

    res = R.login(page)
    if not res.get("ok"):
        give_up("리로스쿨 로그인 안 됨 (" + str(res.get("code")) + ")")
    out("STEP", "리로스쿨 점수 입력 화면 여는 중")
    page.goto(WRITE_FORM, wait_until="domcontentloaded", timeout=60_000)
    page.locator('input[name^="s_num["]').first.wait_for(state="visible", timeout=30_000)
    host = re.match(r"https://([^/]+)/", page.url)
    if not host or host.group(1) != C.SCHOOL_HOST:          # 다른 곳으로 넘어갔으면 아무것도 넣지 않는다
        give_up("리로스쿨 주소가 아닌 곳으로 넘어가서 멈췄습니다")
    return browser, page


# ───────────────────────── 한 번에 ─────────────────────────

def run(day: str, check: bool = False, kind: str = "unsaved") -> int:
    """check=True: 디벗만 읽고 «개수·모양»만 낸다 — 학번은 찍지 않고 리로스쿨도 열지 않는다 (클로드가 진짜 화면을 확인할 때)."""
    from playwright.sync_api import sync_playwright
    from . import divot as D
    from . import riro_site as R
    if not check:
        C.require_school()                               # 디벗을 열기 전에 — 학번만 읽어 놓고 못 넣는 일이 없게
    C.ensure_dirs()
    label = KINDS[kind]["label"]
    with sync_playwright() as p:
        out("STEP", f"디벗 «{label}» 명렬표 여는 중")
        dbrowser, dpage, dfresh = divot_page(p, D)
        try:
            res = read_unsaved(dpage, day, kind)
        finally:
            if dfresh:
                close_browser(dbrowser)                      # 우리가 띄운 디벗 크롬은 다 읽으면 닫는다
            else:
                try:
                    dpage.close()                            # 원래 떠 있던 디벗 창은 우리 탭만 닫는다
                except Exception:
                    pass
        sids = res["sids"]
        out("COUNT", str(len(sids)))
        if check:
            ok = sum(1 for s in sids if re.fullmatch(r"[1-3]\d{4}", s))
            out("CHECK", f"tab={res['tab_count']} 학번={len(sids)} 모양맞음={ok} 겹침={len(sids) - len(set(sids))} "
                         f"읽지못한줄={res['bad']} 다른상태={res['other_status']} "
                         f"맨위사유={'있음' if res['reason'] else '없음'}")   # 문구 자체는 안 낸다
            out("READY", "check")
            return 0
        if res["tab_count"] is not None and res["tab_count"] != len(sids):
            out("STEP", f"주의: {label} 탭은 {res['tab_count']}명인데 학번은 {len(sids)}명만 읽었습니다")
        if not sids:
            out("READY", "0")
            return 0
        _rbrowser, rpage = riro_page(p, R)
        r = fill_blanks(rpage, sids)
        if r["left"]:
            out("LEFT", ",".join(r["left"]))
        # 마지막에 맨 위 줄의 «사유 및 특이사항» 문구를 클립보드로 — 리로스쿨 내용 칸에 Ctrl+V. 문구는 출력하지 않는다
        text = res["reason"]
        if not text:
            out("CLIP", "0")
        elif set_clipboard(text):
            out("CLIP", "1")
        else:
            out("CLIPFAIL", "")
        text = res["reason"] = None
        try:
            rpage.bring_to_front()
        except Exception:
            pass
        out("READY", str(r["filled"]))
        # 여기서 «연결만» 끊는다 (sync_playwright 를 나가면 끊긴다 — 크롬은 따로 띄웠으니 그대로 남는다).
        # 이제 [일괄적용]·[저장]의 확인 창이 선생님께 그대로 뜬다. 창을 닫는 것도 선생님이.
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--check", action="store_true", help="디벗만 읽고 개수·모양만 (학번 안 찍음 · 리로스쿨 안 엶)")
    ap.add_argument("--kind", choices=sorted(KINDS), default="unsaved", help="unsaved = 미보관 탭 · cable = 케이블 미연결 탭")
    a = ap.parse_args(argv)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.date):
        out("FAIL", "날짜는 2026-11-09 처럼 넣어 주세요")
        return 2
    try:
        return run(a.date, a.check, a.kind)
    except Exception as e:
        out("FAIL", (str(e).splitlines() or [type(e).__name__])[0][:160])
        return 1


if __name__ == "__main__":
    sys.exit(main())

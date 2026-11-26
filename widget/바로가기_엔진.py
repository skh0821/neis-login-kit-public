# -*- coding: utf-8 -*-
"""바로가기 위젯 엔진 — 나이스·에듀파인을 인증서 자동 로그인까지 해서 맨 앞에 띄운다.

  python 바로가기_엔진.py neis open                 나이스 첫 화면 (로그인돼 있으면 1~2초, 아니면 로그인부터)
  python 바로가기_엔진.py neis warm [--launch]      미리 데워 두기 — 창은 꺼내지 않는다
  python 바로가기_엔진.py edufine main              K-에듀파인 첫 화면
  python 바로가기_엔진.py edufine open              문서등록대장
  python 바로가기_엔진.py edufine cnrs              공람대기
  python 바로가기_엔진.py edufine portal            업무포털 첫 화면 — 같은 크롬의 «따로 탭» (에듀파인 쓰던 화면은 그대로)
  python 바로가기_엔진.py edufine warm [--launch]   미리 데워 두기
  python 바로가기_엔진.py neis|edufine close        그 크롬 닫기
  ... open|main|cnrs --hold                         EVPN 으로 열었을 때: READY 뒤 위젯 크롬이 다 닫힐 때까지 기다린다

  출력은 STEP <하는 일> / READY / FAIL <이유> 줄 — 위젯이 이 줄만 읽는다.

EVPN (2026-11-10, 바탕화면 글자 위젯): run_vpn.py --auto -- python 바로가기_엔진.py neis open --hold 로 부른다.
--hold 는 run_vpn 이 «우리가 VPN 을 붙였다»(RUN_VPN_OWNER=1)고 알려 줄 때만 기다린다 — 기다림이 끝나면 run_vpn 이 끊는다.
나이스·에듀파인 위젯 크롬 «둘 다» 닫혀야 끝난다 (같은 VPN 을 같이 쓰니까).

왜 빠른가: 느린 건 «크롬을 새로 띄워 인증서 로그인하는 것»(15~35초)이다. 그래서 위젯이 뜰 때 크롬을
«최소화된 채로» 띄워 로그인해 두고(warm --launch), 5분마다 세션을 살려 둔다. 그다음 클릭은 창을 꺼내고
페이지만 옮기면 되니 1~2초다.

크롬은 «파이썬과 남남»인 프로세스로 띄워 스크립트가 끝나도 창이 남는다. 프로필·포트는 위젯 전용이다 —
출결·복무·품의 자동화가 쓰는 chrome_profile / chrome_profile_edufine 과 겹치면 «같은 프로필 크롬이 둘»이 돼서
자동화가 «프로필 사용 중»으로 막힌다. 그래서 따로 둔다.

로그인 코드는 키트의 것을 그대로 쓴다 — neis_session.login() · edufine_common.login().
"""
import asyncio
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parent
sys.path.insert(0, str(KIT))
os.chdir(KIT)

import teacher_config  # noqa: E402  (stdout 을 UTF-8 로 돌려 준다)
import focus_guard  # noqa: E402  (크롬이 선생님 포커스를 가져가면 돌려주는 지킴이)

BASE = teacher_config.CONFIG_DIR
SITES = {
    "neis": {"port": int(os.environ.get("WIDGET_NEIS_PORT", "9322")), "profile": BASE / "chrome_profile_widget_neis",
             "match": ("neis",)},
    "edufine": {"port": int(os.environ.get("WIDGET_EDUFINE_PORT", "9324")), "profile": BASE / "chrome_profile_widget_edufine",
                "match": ("klef", "eduptl")},
}
COMMON_ARGS = [
    "--profile-directory=Default",
    # «자동화 탐지 피하기»(--disable-blink-features=AutomationControlled)는 뺐다 (2026-11-26 선생님 결정).
    # 이 엔진은 크롬을 따로 띄우고 나중에 붙는 방식이라, 그 설정이 없어도 navigator.webdriver=false 다 (임시 프로필 실측 —
    # 있을 때와 똑같음). 사이트가 보는 모습은 그대로이고, «숨기는 코드»만 없어진다
    # 이 셋이 없으면 https 페이지가 127.0.0.1 보안모듈에 못 붙어 «보안프로그램 로딩중»에서 멈춘다
    "--disable-features=PrivateNetworkAccessForNavigations,PrivateNetworkAccessPermissionPrompt,PermissionChip",
    "--disable-web-security",
    "--allow-running-insecure-content",
    "--test-type", "--noerrdialogs", "--no-first-run", "--no-default-browser-check",
    # 최소화·가려진 창에서도 렌더러·타이머를 안 늦춘다 (뒤에서 로그인·새로고침하는 창용)
    "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
]
T0 = time.time()


def step(msg):
    print("STEP %s  (%.1fs)" % (msg, time.time() - T0), flush=True)


# ───────────────────────── 설정 ─────────────────────────

def cfg_and_pw():
    """설정 파일(teacher_config.json)의 인증서 이름·나이스 주소 + 자격 증명 관리자의 암호.
    출결용 항목(학년·반·캘린더)은 없어도 된다 — 위젯은 로그인만 한다."""
    if not teacher_config.CONFIG_FILE.exists():
        raise RuntimeError("설정이 없어요 — 오른쪽 클릭 → «인증서 설정»을 먼저 해 주세요")
    try:
        cfg = {**teacher_config.DEFAULTS, **teacher_config.load_merged()}    # 암호화된 개인 설정까지 합쳐 읽는다
    except teacher_config.ConfigMissingError as e:
        raise RuntimeError(str(e).splitlines()[0]) from None
    name = cfg.get("cert_name")
    if not name:
        raise RuntimeError("인증서 이름이 설정에 없어요 — «인증서 설정»을 다시 해 주세요")
    import secure_store as SS
    # ★ 암호 자체가 아니라 금고 표(VaultSecret) — 키트 로그인 코드가 암호칸에 넣기 직전에 꺼낸다 (2026-11-09)
    pw = SS.VaultSecret(teacher_config.KEYRING_SERVICE, name)
    if not pw.exists():
        raise RuntimeError("인증서 암호가 저장돼 있지 않아요 — «인증서 설정»을 다시 해 주세요")
    return cfg, pw


# ───────────────────────── 크롬 ─────────────────────────

def chrome_exe():
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
        if base:
            p = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if p.exists():
                return str(p)
    raise RuntimeError("구글 크롬(chrome.exe)을 못 찾았어요 — 크롬을 설치해 주세요")


def alive(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port, timeout=2) as r:
            return json.load(r).get("Browser")
    except Exception:
        return None


def profile_locked(profile):
    """이 프로필로 크롬이 (디버깅 포트 없이) 떠 있나 — 떠 있는 동안 lockfile 을 쥐고 있다."""
    lock = Path(profile) / "lockfile"
    if not lock.exists():
        return False
    try:
        lock.unlink()
        return False
    except OSError:
        return True


def fix_prefs(profile):
    """크롬이 꺼져 있을 때만 고쳐진다 — 띄우기 직전에 부른다.
    ① «비밀번호를 저장하시겠습니까?» 말풍선 끄기 (인증서 암호를 넣을 때 뜬다)
    ② «지난번에 최대화였다» 기억 끄기 — 이게 켜져 있으면 최소화로 띄워도 최대화로 뜬다 (2026-10-15 실측)"""
    pref = Path(profile) / "Default" / "Preferences"
    try:
        d = json.loads(pref.read_text(encoding="utf-8")) if pref.exists() else {}
        d["credentials_enable_service"] = False
        d.setdefault("profile", {})["password_manager_enabled"] = False
        d.setdefault("browser", {}).setdefault("window_placement", {})["maximized"] = False
        pref.parent.mkdir(parents=True, exist_ok=True)
        pref.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def launch(site, minimized):
    cfg = SITES[site]
    fix_prefs(cfg["profile"])
    args = [chrome_exe(), "--user-data-dir=%s" % cfg["profile"], "--remote-debugging-port=%d" % cfg["port"],
            *COMMON_ARGS] + ([] if minimized else ["--start-maximized"]) + ["about:blank"]
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    if minimized:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 7              # SW_SHOWMINNOACTIVE — 최소화된 채, 앞 창의 포커스도 안 뺏는다
        kw["startupinfo"] = si
    base = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(args, creationflags=base | 0x01000000, **kw)   # CREATE_BREAKAWAY_FROM_JOB
    except OSError:
        subprocess.Popen(args, creationflags=base, **kw)
    for _ in range(60):
        if alive(cfg["port"]):
            return True
        time.sleep(0.5)
    return False


def ensure_chrome(site, minimized=False):
    """크롬이 없으면 띄운다. 돌려주는 값: 방금 띄웠으면 True (= 빈 창, 마음대로 해도 된다)."""
    cfg = SITES[site]
    if alive(cfg["port"]):
        return False
    if profile_locked(cfg["profile"]):
        raise RuntimeError("위젯용 크롬이 이상한 상태예요 — 그 창을 닫고 다시 눌러 주세요")
    step("크롬 띄우는 중")
    if not launch(site, minimized):
        raise RuntimeError("크롬이 안 떴어요")
    return True


def pick_page(ctx, site):
    for pg in ctx.pages:
        if any(k in pg.url for k in SITES[site]["match"]):
            return pg
    return ctx.pages[0] if ctx.pages else None


def connect_sync(p, port):
    """막 띄운 크롬(특히 새 프로필 첫 실행)은 포트가 열린 뒤에도 붙기가 한 번 튕긴다 (10/16 엔드투엔드 실측: 15초 타임아웃) → 3번."""
    last = None
    for i in range(3):
        try:
            return p.chromium.connect_over_cdp("http://127.0.0.1:%d" % port, timeout=15_000)
        except Exception as e:
            last = e
            step("붙기 실패, 다시 (%d/3)" % (i + 1))
            time.sleep(2)
    raise last


async def connect_async(p, port):
    last = None
    for i in range(3):
        try:
            return await p.chromium.connect_over_cdp("http://127.0.0.1:%d" % port, timeout=15_000)
        except Exception as e:
            last = e
            step("붙기 실패, 다시 (%d/3)" % (i + 1))
            await asyncio.sleep(2)
    raise last


def other_clients(port):
    """이 포트에 CDP 로 붙어 있는 «다른» 프로세스 (내 자식인 playwright 드라이버는 뺀다)."""
    try:
        import psutil
        conns = [c for c in psutil.net_connections(kind="tcp") if c.status == "ESTABLISHED"]
        peers = {c.raddr.port for c in conns if c.laddr.port == port}
        mine = {os.getpid()} | {c.pid for c in psutil.Process(os.getpid()).children(recursive=True)}
        return sorted({c.pid for c in conns if c.raddr.port == port and c.laddr.port in peers and c.pid not in mine})
    except Exception:
        return []


def watchdog(sec):
    def kill():
        print("FAIL 시간 초과 (%d초)" % sec, flush=True)
        os._exit(2)
    t = threading.Timer(sec, kill)
    t.daemon = True
    t.start()
    return t


def hold_vpn(poll=5.0):
    """우리가 붙인 EVPN 을 위젯 크롬(나이스·에듀파인)이 모두 닫힐 때까지 붙잡아 둔다. 끊기는 부른 쪽(run_vpn)이 한다.
    한 번 못 읽은 걸로 끝내지 않는다 (크롬이 바쁠 때 /json/version 이 늦을 수 있다) — 두 번 연속 없을 때 끝."""
    step("VPN 유지 — 나이스·에듀파인 창을 모두 닫으면 끊습니다")
    misses = 0
    while misses < 2:
        misses = 0 if any(alive(s["port"]) for s in SITES.values()) else misses + 1
        if misses < 2:
            time.sleep(poll)
    step("창이 모두 닫혀 VPN 을 끊습니다")


# ───────────────────────── 창 앞뒤 ─────────────────────────

def _bounds(cdp):
    wid = cdp("Browser.getWindowForTarget")["windowId"]
    return wid, cdp("Browser.getWindowBounds", {"windowId": wid})["bounds"]


def window_minimized_sync(page):
    try:
        s = page.context.new_cdp_session(page)
        return _bounds(lambda m, p=None: s.send(m, p or {}))[1].get("windowState") == "minimized"
    except Exception:
        return False


async def window_minimized_async(page):
    try:
        s = await page.context.new_cdp_session(page)
        wid = (await s.send("Browser.getWindowForTarget"))["windowId"]
        return (await s.send("Browser.getWindowBounds", {"windowId": wid}))["bounds"].get("windowState") == "minimized"
    except Exception:
        return False


class FocusGuard(focus_guard.FocusGuard):
    """로그인하는 동안 이 사이트의 위젯 크롬이 맨 앞으로 오면 바로 최소화한다 (2026-11-17 · 본체는 키트의 focus_guard.py).

    선생님 말씀: «아이콘을 누르고 키보드·마우스로 다른 일을 하면 나이스 로그인이 완성되지 않는다».
    크롬은 뜨는 순간 키보드 포커스를 가져가 선생님 타자·클릭이 로그인 중인 창으로 들어갔다.
    그래서 open 도 최소화로 띄우고(최소화여도 클릭 0.06초), 맨 앞에 오면 0.1초 안에 다시 내린다
    (실측: 최소화로 띄움 + 지킴이 = 4초 동안 40번 모두 원래 창). 다 되면(READY) to_front 가 지킴이를 끄고 꺼낸다.
    «직접 로그인해 주세요»로 넘어가면 사람이 그 창을 써야 하니 지킴이를 끄고 창을 꺼낸다 (manual_hooks)."""

    def __init__(self, site):
        super().__init__(SITES[site]["profile"].name, keep_shown=False)

    def _ours(self, hwnd):
        return self.ours(hwnd)


GUARD = None


def stop_guard():
    if GUARD is not None:
        GUARD.stop()


def manual_hooks():
    """자동 로그인이 막혀 «직접 로그인»을 기다리게 되면 — 지킴이를 끄고 창을 앞으로 꺼낸다 (사람이 그 창에서 로그인해야 한다)."""
    import edufine_common as E
    import neis_session as NS
    if getattr(NS.wait_manual_login, "_widget", False):
        return
    neis_wait, edufine_wait = NS.wait_manual_login, E.manual_login

    async def neis_manual(page, blocked):
        try:
            await to_front_async(page)
        except Exception:
            stop_guard()                 # 꺼내기가 실패해도 지킴이는 꺼야 사람이 그 창을 쓸 수 있다
        return await neis_wait(page, blocked)

    def edufine_manual(page):
        try:
            to_front_sync(page)
        except Exception:
            stop_guard()
        return edufine_wait(page)

    neis_manual._widget = edufine_manual._widget = True
    NS.wait_manual_login, E.manual_login = neis_manual, edufine_manual


def _foreground(title):
    """크롬 명령(창 되살리기·최대화·앞으로)으로 대개 앞에 온다 (11-17 실측). 안 왔을 때만 윈도우에 직접 부탁한다.
    예전엔 Alt 키를 눌렀다 뗐는데, 이제 이 순간 맨 앞은 선생님이 쓰던 창이라 Alt 가 그 창(한글·엑셀)의 메뉴를 연다
    → 키 대신 «마우스 0칸 움직임»(커서는 그대로)으로 «마지막 입력»을 만든 뒤 앞으로 꺼낸다.
    GUARD 가 있으면 위젯 전용 크롬 프로필(GUARD.ours)인지 먼저 가려, 선생님 평소 크롬과 섞이지 않게 한다."""
    try:
        u = ctypes.windll.user32
        u.GetForegroundWindow.restype = ctypes.c_void_p
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def enum(hwnd, _):
            h = ctypes.c_void_p(hwnd)
            n = u.GetWindowTextLengthW(h)
            if n and u.IsWindowVisible(h):
                if GUARD is not None and not GUARD.ours(hwnd):
                    return True
                buf = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(h, buf, n + 1)
                if (GUARD is not None and "Chrome" in buf.value) or (title and buf.value.startswith(title) and "Chrome" in buf.value):
                    found.append(hwnd)
            return True
        u.EnumWindows(enum, 0)
        if found and u.GetForegroundWindow() not in found:
            u.mouse_event(0x0001, 0, 0, 0, 0)       # MOUSEEVENTF_MOVE 0,0
            u.SetForegroundWindow(ctypes.c_void_p(found[0]))
    except Exception:
        pass


def front_note(site):
    """지금 맨 앞 창이 이 사이트의 위젯 크롬인지 한 줄 찍는다 — 기록용 (글자 위젯이 «ℹ» 줄을 기록에 남긴다).
    2026-11-12: 선생님이 다른 창에서 타자를 치는 동안 나이스 [인증서 로그인] 을 눌렀는데 인증서 창이 20초 안에 안 떴다
    (11-11 에듀파인도 같은 모습). 창이 맨 앞이 아니어서인지 가리려고 남긴다 — 값은 «예/아니오» 뿐."""
    front = "모름"
    try:
        import psutil
        u = ctypes.windll.user32
        u.GetForegroundWindow.restype = ctypes.c_void_p
        pid = ctypes.c_ulong()
        u.GetWindowThreadProcessId(ctypes.c_void_p(u.GetForegroundWindow()), ctypes.byref(pid))
        front = "예" if SITES[site]["profile"].name in " ".join(psutil.Process(pid.value).cmdline()) else "아니오"
    except Exception:
        pass
    print("ℹ️  인증서 로그인 시작 — %s 창이 맨 앞: %s" % ("나이스" if site == "neis" else "에듀파인", front), flush=True)


def to_front_sync(page):
    stop_guard()
    try:
        s = page.context.new_cdp_session(page)
        wid = s.send("Browser.getWindowForTarget")["windowId"]
        s.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "normal"}})
        s.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "maximized"}})
    except Exception:
        pass
    page.bring_to_front()
    _foreground(page.title())


async def to_front_async(page):
    stop_guard()
    try:
        s = await page.context.new_cdp_session(page)
        wid = (await s.send("Browser.getWindowForTarget"))["windowId"]
        await s.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "normal"}})
        await s.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "maximized"}})
    except Exception:
        pass
    await page.bring_to_front()
    _foreground(await page.title())


def minimize_sync(page):
    try:
        if window_minimized_sync(page):
            return
        s = page.context.new_cdp_session(page)
        wid = s.send("Browser.getWindowForTarget")["windowId"]
        s.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "minimized"}})
    except Exception:
        pass


async def minimize_async(page):
    try:
        if await window_minimized_async(page):
            return
        s = await page.context.new_cdp_session(page)
        wid = (await s.send("Browser.getWindowForTarget"))["windowId"]
        await s.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "minimized"}})
    except Exception:
        pass


# ───────────────────────── 나이스 (async — 키트 neis_session 이 async) ─────────────────────────

async def neis_logged_in(page, ms=1500):
    if "neis" not in page.url:
        return False
    try:
        import neis_session as NS
        end = time.time() + ms / 1000
        while time.time() < end:
            if await NS.is_logged_in(page):
                return True
            await page.wait_for_timeout(200)
    except Exception:
        pass
    return False


async def neis_login(ctx, page):
    import neis_session as NS
    cfg, pw = cfg_and_pw()
    from urllib.parse import urlsplit
    try:
        sp = urlsplit(cfg["neis_url"])
        await ctx.grant_permissions(["notifications"], origin="%s://%s" % (sp.scheme, sp.netloc))
    except Exception:
        pass
    step("나이스 여는 중")
    front_note("neis")
    await NS.login(page, cfg, pw)       # 인증서 고르기·암호·막히면 손 로그인 기다리기까지 키트 그대로
    step("로그인 확인 중")
    await NS.close_notice(page)


async def neis_cmd(cmd, launch_ok):
    from playwright.async_api import async_playwright
    site = "neis"
    if cmd == "warm" and not alive(SITES[site]["port"]) and not launch_ok:
        print("READY", flush=True)      # 닫아 둔 창은 다시 띄우지 않는다
        return
    fresh = ensure_chrome(site, minimized=True)   # 11-17: open 도 최소화로 띄운다 — 다 되면 to_front 가 꺼낸다
    async with async_playwright() as p:
        browser = await connect_async(p, SITES[site]["port"])
        ctx = browser.contexts[0]
        page = pick_page(ctx, site) or await ctx.new_page()
        if cmd == "open":
            if await neis_logged_in(page):
                step("이미 로그인돼 있음")
            else:
                await neis_login(ctx, page)
            await to_front_async(page)
            print("READY", flush=True)
            return
        # warm — 사용자 창은 ① 최소화돼 있고 ② 다른 자동화가 안 붙어 있을 때만 건드린다
        page.on("dialog", lambda d: None)
        if fresh:
            await minimize_async(page)
        else:
            if not await window_minimized_async(page):
                step("창을 쓰는 중이라 건드리지 않음")
                print("READY", flush=True)
                return
            if other_clients(SITES[site]["port"]):
                step("다른 자동화가 붙어 있어 건드리지 않음")
                print("READY", flush=True)
                return
            if await neis_logged_in(page):
                step("세션 확인 (새로고침)")   # 나이스 메인은 가만히 두면 서버에 아무것도 안 보낸다 → 새로고침이 곧 세션 연장
                await page.reload(wait_until="domcontentloaded")
                if await neis_logged_in(page, 25_000):
                    print("READY", flush=True)
                    return
                step("세션이 끊겨 있었음")
            # 여기까지 왔으면 창은 최소화돼 있고 아무도 안 붙어 있다 → 남아 있는 인증서 창은 지난 실패의 찌꺼기다.
            # 그걸 «누가 로그인 중»으로 보고 건너뛰면 영영 로그인이 안 된다 (10/16 엔드투엔드 실측) → 그냥 다시 로그인한다
        await neis_login(ctx, page)
        print("READY", flush=True)


# ───────────────────────── 에듀파인 (sync — 키트 edufine_common 이 sync) ─────────────────────────

def goto(page, url, **kw):
    """SSO 직후엔 리다이렉트가 아직 도는 중이라 «interrupted by another navigation» 으로 끊긴다 (10/15 실측) → 한 번 더."""
    for i in range(3):
        try:
            page.goto(url, **kw)
            return
        except Exception as e:
            if "interrupted" not in str(e) or i == 2:
                raise
            page.wait_for_timeout(1500)


def edufine_urls():
    import edufine_common as E
    k = E.KLEF.rstrip("/") + "/"
    return E, {"main": k + "keris_ui/main.do",
               "open": k + "bms/cz/cb/dbx/retrieveDocRegistRegstrList.do?leftMenu=1",
               "cnrs": k + "bms/cz/cb/viw/retrieveCnrsWaitDocList.do?leftMenu=1"}


def edufine_login(E, ctx, page, urls):
    E.grant_local_network(ctx)
    step("로그인 확인 중")
    cfg, pw = cfg_and_pw()
    front_note("edufine")
    E.login(page, cfg["cert_name"], pw)   # 업무포털 → 인증서 → 암호. 막히면 손 로그인 기다리기까지 키트 그대로
    step("에듀파인 들어가는 중")
    try:    # 업무포털 → 에듀파인 SSO. klef 의 main.do 가 200 으로 오면 세션이 생긴 것
        with page.expect_response(lambda r: r.url.startswith(urls["main"]) and r.status == 200, timeout=15000):
            page.goto(urls["main"], wait_until="commit")
    except Exception:
        page.wait_for_timeout(3000)


def edufine_session_ok(page, urls):
    """세션이 살아 있나 — 문서등록대장을 fetch 해 본다 (화면은 안 움직인다)."""
    if "klef." not in page.url:
        return False
    try:
        page.wait_for_load_state("domcontentloaded")
        r = page.evaluate("""async (u) => { const r = await fetch(u, {credentials: 'include'}); const h = await r.text();
            return {url: r.url, ok: h.includes('tbList')}; }""", urls["open"])
        return bool(r and r["ok"] and "klef." in r["url"])
    except Exception:
        return False


def edufine_page(ctx):
    """에듀파인(klef) 탭 — 업무포털 탭은 건드리지 않는다. 업무포털 탭만 있으면 새 탭을 연다."""
    for pg in ctx.pages:
        if "klef." in pg.url:
            return pg
    for pg in ctx.pages:
        if not is_portal(pg):
            return pg
    return ctx.new_page()


def is_portal(page):
    return ".eduptl.kr" in page.url


def portal_cmd(E, ctx, page_fresh):
    """업무포털 — 위젯 에듀파인 크롬의 따로 탭. 같은 크롬이라 에듀파인으로 로그인돼 있으면 인증서 없이 바로 열린다.
    (2026-11-13 선생님 요청: 문등대 자리에 «업무포털» 글자)"""
    page = next((pg for pg in ctx.pages if is_portal(pg)), None)
    if page is None:
        page = page_fresh if page_fresh is not None else ctx.new_page()
    page.set_default_timeout(30_000)
    page.set_default_navigation_timeout(120_000)
    page.on("dialog", E.on_dialog)
    if "bpm_man_mn00_001" in page.url:     # 업무포털 첫 화면에 이미 있다 — 창부터 보여 주고 새로고침
        to_front_sync(page)
        step("새로 불러오는 중")
        page.reload(wait_until="domcontentloaded")
    else:
        E.grant_local_network(ctx)
        step("로그인 확인 중")
        cfg, pw = cfg_and_pw()
        front_note("edufine")
        E.login(page, cfg["cert_name"], pw)   # 로그인돼 있으면 «이미 로그인돼 있습니다» 로 바로 끝난다
    to_front_sync(page)
    print("READY", flush=True)


def edufine_cmd(cmd, launch_ok):
    from playwright.sync_api import sync_playwright
    site = "edufine"
    E, urls = edufine_urls()
    if cmd == "warm" and not alive(SITES[site]["port"]) and not launch_ok:
        print("READY", flush=True)
        return
    fresh = ensure_chrome(site, minimized=True)   # 11-17: open 도 최소화로 띄운다 — 다 되면 to_front 가 꺼낸다
    with sync_playwright() as p:
        browser = connect_sync(p, SITES[site]["port"])
        ctx = browser.contexts[0]
        if cmd == "portal":
            return portal_cmd(E, ctx, ctx.pages[0] if fresh and ctx.pages else None)
        page = edufine_page(ctx)
        page.set_default_timeout(30_000)
        # 페이지 이동만 2분까지 — EVPN 을 막 붙인 직후 첫 접속은 나이스가 뼈대까지 51초 걸렸다 (2026-11-10 실측)
        page.set_default_navigation_timeout(120_000)
        if cmd == "warm":
            page.on("dialog", lambda d: None)
            if fresh:
                minimize_sync(page)
            if edufine_session_ok(page, urls):
                print("READY", flush=True)
                return
            if not fresh and not window_minimized_sync(page):
                step("창을 쓰는 중이라 건드리지 않음")
                print("READY", flush=True)
                return
            edufine_login(E, ctx, page, urls)
            goto(page, urls["open"], wait_until="domcontentloaded")   # 문서등록대장에 세워 둔다 (nexacro 메인은 오래 두면 알림창이 뜬다)
            print("READY", flush=True)
            return
        page.on("dialog", E.on_dialog)      # «다른 IP 에서 접속 중» 창만 [확인], 나머지는 «아니오» — 키트 규칙
        target = urls[cmd]
        key = target.split("?")[0].rsplit("/", 1)[-1]
        if key in page.url:
            to_front_sync(page)             # 창부터 보여 주고 그다음 새로고침
            step("새로 불러오는 중")
            page.reload(wait_until="domcontentloaded")
        elif "klef." in page.url and edufine_session_ok(page, urls):
            to_front_sync(page)
            step("여는 중")
            goto(page, target, wait_until="domcontentloaded")
        else:
            edufine_login(E, ctx, page, urls)
            step("여는 중")
            goto(page, target, wait_until="domcontentloaded")
        if cmd != "main":
            try:
                page.wait_for_selector("#tbList tbody tr", timeout=20_000)
            except Exception:
                pass
        to_front_sync(page)
        print("READY", flush=True)


def close_cmd(site):
    port = SITES[site]["port"]
    if not alive(port):
        print("CLOSED", flush=True)
        return
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("http://127.0.0.1:%d" % port, timeout=8000)
        b.new_browser_cdp_session().send("Browser.close")
    print("CLOSED", flush=True)


def main():
    site = sys.argv[1] if len(sys.argv) > 1 else "neis"
    cmd = sys.argv[2] if len(sys.argv) > 2 else "open"
    launch_ok = "--launch" in sys.argv
    timer = watchdog(900 if cmd != "warm" else 300)     # 손 로그인 기다리기(최대 10분)까지 감안
    global GUARD
    if cmd not in ("warm", "close"):     # warm 은 선생님이 쓰던 창일 수 있어 건드리지 않는다 — 지킴이는 누른 글자를 여는 동안만
        manual_hooks()
        GUARD = FocusGuard(site).start()
    try:
        if cmd == "close":
            close_cmd(site)
        elif site == "neis":
            asyncio.run(neis_cmd(cmd, launch_ok))
        else:
            edufine_cmd(cmd, launch_ok)
    except BaseException as e:
        if GUARD is not None:
            GUARD.stop()
            GUARD.reveal()               # 실패한 화면을 볼 수 있게 — 포커스는 안 뺏고 보이게만
        msg = (str(e).splitlines() or [type(e).__name__])[0][:140]
        print("FAIL " + msg, flush=True)
        sys.exit(1)
    stop_guard()
    if "--hold" in sys.argv and cmd not in ("warm", "close") and os.environ.get("RUN_VPN_OWNER") == "1":
        timer.cancel()                  # 여기서부터는 선생님이 창을 쓰는 시간 — 15분 시간 초과로 죽이지 않는다
        hold_vpn()


if __name__ == "__main__":
    main()

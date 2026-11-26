# -*- coding: utf-8 -*-
"""학교 밖에서 나이스·에듀파인을 쓰기 위한 EVPN(원격업무지원) 자동 연결.

지금은 **서울(evpn.sen.go.kr, AXGATE VPN Client)** 만 실제로 연결해 봤다. 다른 시도는 설정의
나이스 주소에서 EVPN 주소를 골라 쓴다(EVPN_BY_CODE, 설정에 evpn_url 을 적으면 그게 먼저).
경기는 포털 스크립트가 서울과 같음까지만 확인했고, 실제 연결은 아직 안 해 봤다.

  python evpn.py                 상태만 본다 (설치·도우미·연결)
  python evpn.py setup --user 아이디    EVPN 아이디 저장 (비밀번호가 인증서 암호와 다르면 새 창에서 받는다)
  python evpn.py install         도우미 설치 — 처음 한 번, «허용하시겠어요? [예]» 를 한 번 누른다
  python evpn.py connect         연결만
  python evpn.py disconnect      끊기만
  python evpn.py ping            (VPN 연결 중에) 내 지역 업무포털이 열리는지만 본다
  python run_vpn.py --auto -- python widget/바로가기_엔진.py neis open    ← 글자 위젯이 이렇게 부른다 (연결 → 로그인 → 끊기)

왜 도우미가 필요한가:
  EVPN 에 로그인하면 AXGATE 가 «2차 인증»(인증서 암호) 창을 띄운다. 이 창은 관리자 권한 프로그램의
  창이라 일반 권한인 이 스크립트는 글자를 넣을 수 없다(윈도우가 막는다). 그래서 관리자 권한 도우미가
  대신 넣는다. 매번 «[예]» 를 누르지 않도록 설치 때 한 번만 허락받아 예약 작업으로 등록한다
  (2026-09-18 실측: 기본 UAC PC 에서 예약 작업으로 부르면 확인창 없이 관리자 권한으로 돈다).

인증서 암호·EVPN 비밀번호는 keyring(윈도우 자격 증명 관리자)에서만 읽는다. 화면·로그에 찍지 않는다.
"""
import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import keyring

import focus_guard
import teacher_config

KIT_DIR = Path(__file__).resolve().parent
SEOUL_EVPN_URL = "https://evpn.sen.go.kr/"
CLIENT_EXE = Path(r"C:\ProgramData\AXGATE\AXGATE VPN Client\Bin\AxgateVpnClient.exe")
AGENT_PORT = 46435                       # 포털이 에이전트를 찾는 로컬 포트
TAP_KEYWORDS = ("TAP-Win32", "AXGATE", "TAP-Windows")
# 예약 작업은 PC 전체에 하나라서, 이름에 계정을 붙인다 — 같은 PC를 두 계정이 쓰면 나중에 설치한 쪽이
# 앞사람 도우미를 덮어써 앞사람의 자동 연결이 «권한 없음»으로 깨졌다 (2026-10-14 실측)
OLD_TASK_NAME = "NeisAutomation EVPN 도우미"
TASK_NAME = f"{OLD_TASK_NAME} ({os.environ.get('USERNAME', '')})"
HELPER_LOG = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "NeisAutomationEVPN" / "helper.log"
INSTALL_RESULT = HELPER_LOG.parent / "install_result.txt"
DONE_FLAG = teacher_config.CONFIG_DIR / "evpn_helper_done.flag"
PROFILE_DIR = teacher_config.CONFIG_DIR / "chrome_profile_evpn"
KEYRING_EVPN_ACCOUNT = "evpn"
REGISTER_WAIT_SEC = 70                   # 로그인 → 2차 인증 → 연결 Yes 까지 (실측 37~47초)

_NO_WINDOW = 0x08000000

CHROME_ARGS = [
    "--profile-directory=Default",
    # «자동화 탐지 피하기»(--disable-blink-features=AutomationControlled)는 뺐다 (2026-11-26 선생님 결정).
    # 이 크롬은 playwright 가 직접 띄워서, 빼면 포털이 navigator.webdriver=true 를 볼 수 있다.
    # 포털 스크립트(index.html · axgate2.js)에는 webdriver·자동화 확인 코드가 없다 (11-26 공개 파일 확인)
    # https 포털이 http://127.0.0.1:46435(에이전트)에 붙으려면 이 기능들을 꺼야 한다.
    # 안 끄면 «이 기기의 다른 앱 및 서비스에 액세스» 허용을 묻고 멈춘다 (2026-09-18 샌드박스에서 뜸)
    "--disable-features=PrivateNetworkAccessForNavigations,PrivateNetworkAccessPermissionPrompt,"
    "PermissionChip,LocalNetworkAccessChecks",
    "--disable-web-security",
    "--allow-running-insecure-content",
    "--test-type",
    "--start-maximized",
    "--noerrdialogs",
    "--hide-crash-restore-bubble",       # 지난번 강제 종료 뒤 «페이지를 복원하시겠습니까?» 안 띄우기
]

# 로그인 버튼을 덮는 안내 팝업·설치 확인 가림막
OVERLAY_IDS = ("layer_popup", "layer_popup2", "popupOverlay",
               "loading-fullbox", "loading-fullbox2", "loading-fullbox3")


# ============================================================ 설정·계정
def _cfg() -> dict:
    """설정을 읽는다. 출결용 필수 항목(학년·반)이 없어도 EVPN 은 쓸 수 있어야 해서 검사 없이 읽는다.
    평문 설정 + 암호화된 개인 설정(EVPN 아이디·인증서 이름)을 합친 값이다."""
    return teacher_config.load_config_or_empty()


# 시도별 EVPN 주소 — 나이스 주소(https://{코드}.neis.go.kr)의 코드로 고른다.
# 교육청 공개 전국 EVPN 주소표 + 공식 포털 확인(2026-09-11). 경북만 도메인이 .gbe.kr 이다.
# 경기(goe)는 2026-09-21 포털의 axgate.js·axgate2.js 가 서울과 md5 까지 같고 로그인 칸 id 도 같음을 확인
EVPN_BY_CODE = {c: f"https://evpn.{c}.go.kr/" for c in (
    "sen", "pen", "dge", "ice", "gen", "dje", "use", "sje", "goe", "gwe",
    "cbe", "cne", "jbe", "jne", "gne", "jje")}
EVPN_BY_CODE["gbe"] = "https://evpn.gbe.kr/"


def portal_url() -> str:
    """이 선생님 지역의 업무포털 — 연결 시험(ping)에 쓴다."""
    m = re.match(r"https?://([a-z]{3})\.neis\.go\.kr", _cfg().get("neis_url") or "")
    return f"https://{m.group(1) if m else 'sen'}.eduptl.kr"


def ping() -> int:
    """VPN 이 붙은 상태에서 업무포털이 열리는지만 본다 (run_vpn.py -- python evpn.py ping)."""
    import urllib.request
    url = portal_url()
    try:
        code = urllib.request.urlopen(url, timeout=20).status
    except Exception as e:
        print(f"업무포털 응답 없음 ({url}) — {type(e).__name__}")
        return 1
    print(f"업무포털 응답 {code} ({url})")
    return 0 if code == 200 else 1


def evpn_url() -> str:
    cfg = _cfg()
    if cfg.get("evpn_url"):
        return cfg["evpn_url"]
    m = re.match(r"https?://([a-z]{3})\.neis\.go\.kr", cfg.get("neis_url") or "")
    return EVPN_BY_CODE.get(m.group(1), SEOUL_EVPN_URL) if m else SEOUL_EVPN_URL


def password_mode(cfg: dict | None = None) -> str:
    """'same_as_cert' (선생님이 설정창에서 «인증서 암호와 같음»을 직접 고름) / 'separate' (따로 저장) / 'unset'."""
    cfg = _cfg() if cfg is None else cfg
    m = cfg.get("evpn_password_mode")
    return m if m in ("same_as_cert", "separate") else "unset"


def evpn_origins() -> list:
    """EVPN 아이디·비밀번호를 넣어도 되는 주소 — 공식 꼴(evpn.○○○.go.kr · evpn.gbe.kr)인 설정 주소 하나 (https·443)."""
    from urllib.parse import urlsplit
    u = urlsplit(evpn_url())
    host = (u.hostname or "").lower()
    if u.scheme == "https" and u.port in (None, 443) and re.fullmatch(r"evpn\.([a-z]{3}\.go\.kr|gbe\.kr)", host):
        return [f"https://{host}"]
    return []


def load_credentials():
    """(아이디, 금고 표 VaultSecret 또는 None). 비밀번호 «자체»는 여기서 꺼내지 않는다 —
    _login_once() 가 포털 비밀번호 칸에 치기 직전에 꺼낸다 (2026-11-09).

    ★ 인증서 암호를 EVPN 비밀번호로 «알아서» 쓰지 않는다. 선생님이 보안 설정창에서
      «EVPN 비밀번호 = 인증서 암호와 같음» 을 직접 골랐을 때만 쓴다 (evpn_password_mode = same_as_cert).
      그 밖에는 따로 저장한 EVPN 비밀번호(자격 증명 관리자 neis-automation / evpn)만 쓴다.
    """
    import secure_store as SS
    cfg = _cfg()
    user = cfg.get("evpn_user") or None
    if password_mode(cfg) == "same_as_cert":
        ticket = SS.VaultSecret(teacher_config.KEYRING_SERVICE, cfg["cert_name"]) if cfg.get("cert_name") else None
    else:
        ticket = SS.VaultSecret(teacher_config.KEYRING_SERVICE, KEYRING_EVPN_ACCOUNT)
    return user, (ticket if ticket is not None and ticket.exists() else None)


# ============================================================ PC 상태
def _ps(cmd: str, timeout: int = 20) -> tuple[int, str]:
    try:
        cmd = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + cmd
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=_NO_WINDOW)
        return r.returncode, (r.stdout or "").strip()
    except Exception as e:
        return 1, f"{type(e).__name__}"


def client_installed() -> bool:
    return CLIENT_EXE.exists()


def tap_status() -> str:
    """'Up' / 'Down' / 'unknown'. 읽기에 실패하면 unknown — 끊겼다고 넘겨짚지 않는다."""
    cond = " -or ".join(f"$_.InterfaceDescription -like '*{k}*'" for k in TAP_KEYWORDS)
    code, out = _ps(f"$a = Get-NetAdapter -ErrorAction Stop | Where-Object {{ {cond} }}; "
                    "if (-not $a) { 'NOADAPTER' } else { ($a | Select-Object -First 1).Status }")
    if code != 0 or not out:
        return "unknown"
    if out == "Up":
        return "Up"
    if out in ("Disconnected", "Down", "NOADAPTER", "Not Present", "Disabled"):
        return "Down"
    return "unknown"


AGENT_PREWAIT_SEC = float(os.environ.get("EVPN_AGENT_PREWAIT", "5"))   # 2026-11-10: 30 → 5초 (아래 connect_async 설명)


def wait_agent(max_sec: float, poll: float = 0.5) -> bool:
    """에이전트 포트가 열릴 때까지 최대 max_sec 초. 열렸으면 True."""
    end = time.monotonic() + max_sec
    while True:
        if agent_listening():
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(poll)


def agent_listening() -> bool:
    # psutil 로 먼저 본다 (0.1초 남짓). PowerShell 은 한 번에 1~2초라, 30번 묻는 대기가 30초가 아니라 70~80초가 됐다
    # (2026-11-10 글자 위젯 실측: 연결 시작 → 포털 창까지 78초). 둘 다 «듣고 있는지»만 보고 포트에 붙지는 않는다
    try:
        import psutil
        return any(c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == AGENT_PORT
                   for c in psutil.net_connections(kind="tcp"))
    except Exception:
        pass
    code, out = _ps(f"(Get-NetTCPConnection -State Listen -LocalPort {AGENT_PORT} "
                    "-ErrorAction SilentlyContinue | Measure-Object).Count")
    return code == 0 and out.isdigit() and int(out) > 0


def _task_name() -> str | None:
    """이 계정 이름이 붙은 작업을 먼저, 없으면 예전 이름(계정 이름 없음)을 쓴다 — 예전에 설치한 분도 그대로 돌게."""
    for name in (TASK_NAME, OLD_TASK_NAME):
        r = subprocess.run(["schtasks", "/Query", "/TN", name], capture_output=True,
                           creationflags=_NO_WINDOW)
        if r.returncode == 0:
            return name
    return None


def helper_installed() -> bool:
    return _task_name() is not None


def helper_running() -> bool:
    code, out = _ps("(Get-Process evpn_helper -ErrorAction SilentlyContinue | Measure-Object).Count")
    return out.isdigit() and int(out) > 0


def helper_log_tail(n: int = 8) -> list[str]:
    try:
        return HELPER_LOG.read_text(encoding="utf-8-sig").splitlines()[-n:]
    except Exception:
        return []


def start_helper() -> bool:
    """예약 작업으로 도우미를 띄운다 (UAC 창 없음). 도우미가 AXGATE 도 필요하면 띄운다."""
    if helper_running():
        # 지난 실행의 도우미가 남아 있으면 끝 신호를 보내고 기다린다 — 다음 창을 대신 누르지 않게
        DONE_FLAG.parent.mkdir(parents=True, exist_ok=True)
        DONE_FLAG.write_text(str(time.time()))
        for _ in range(20):
            if not helper_running():
                break
            time.sleep(0.5)
    try:
        DONE_FLAG.unlink()
    except FileNotFoundError:
        pass
    name = _task_name()
    if not name:
        return False
    r = subprocess.run(["schtasks", "/Run", "/TN", name], capture_output=True,
                       creationflags=_NO_WINDOW)
    return r.returncode == 0


def stop_helper() -> None:
    DONE_FLAG.parent.mkdir(parents=True, exist_ok=True)
    DONE_FLAG.write_text(str(time.time()))


# ============================================================ 설치 (처음 한 번)
def install(desktop_bat: bool = True) -> bool:
    if not client_installed():
        print("❌  AXGATE VPN Client 가 아직 없습니다.")
        print(f"    크롬으로 {evpn_url()} → 아이디·비밀번호로 [LOGIN] → [다운로드] → 받은 파일 실행 → [Finish]")
        print("    (포털 윗줄에 «설치 Yes» 가 보여도 처음이면 [다운로드]부터 하세요)")
        return False
    user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}"
    src = KIT_DIR / "evpn_helper"
    print("🔐  도우미를 설치합니다. 화면에 «이 앱이 디바이스를 변경하도록 허용하시겠어요?» 가 뜨면 [예] 를 눌러 주세요.")
    print("    (처음 한 번만입니다. 그다음부터는 연결할 때 아무것도 안 누릅니다)", flush=True)
    try:
        INSTALL_RESULT.unlink()
    except (FileNotFoundError, PermissionError):
        pass
    ps = ("Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "
          f"'-NoProfile','-ExecutionPolicy','Bypass','-File','\"{src / 'install.ps1'}\"',"
          f"'-User','\"{user}\"','-Source','\"{src}\"'")
    started = time.time()
    code, out = _ps(ps, timeout=600)
    fresh = INSTALL_RESULT.exists() and INSTALL_RESULT.stat().st_mtime >= started - 2
    if code != 0 or not fresh:
        print("❌  설치 창이 취소됐거나 실행되지 않았습니다. [예] 를 누르지 않으셨다면 다시 해 주세요.")
        return False
    try:
        status, msg = INSTALL_RESULT.read_text(encoding="utf-8-sig").strip().split("|", 1)
    except Exception:
        print("❌  설치 결과를 읽지 못했습니다.")
        return False
    print(("✅  " if status == "OK" else "❌  ") + msg)
    if status != "OK":
        return False
    if not helper_installed():
        print("❌  예약 작업이 보이지 않습니다.")
        return False
    if desktop_bat:
        make_desktop_disconnect()
    else:
        print("ℹ️  바탕화면 «VPN 끊기.bat» 는 만들지 않았습니다 (--no-desktop-bat)")
    return True


def make_desktop_disconnect() -> None:
    """바탕화면 «VPN 끊기.bat» — 클로드도 인터넷도 없을 때 선생님이 더블클릭해서 끊는다."""
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    try:
        code, out = _ps("[Environment]::GetFolderPath('Desktop')")
        if code == 0 and out:
            desktop = Path(out)
    except Exception:
        pass
    py = KIT_DIR / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        py = Path(sys.executable)
    bat = desktop / "VPN 끊기.bat"
    bat.write_text(
        "@echo off\r\nchcp 65001 >nul\r\necho EVPN 연결을 끊습니다...\r\n"
        f"\"{py}\" -X utf8 \"{KIT_DIR / 'evpn.py'}\" disconnect\r\npause\r\n",
        encoding="utf-8", newline="")
    print(f"🖱  바탕화면에 «{bat.name}» 를 만들었습니다 (클로드가 멈췄을 때 더블클릭하면 끊깁니다)")


# ============================================================ 포털 조작
def _kill_stale_chrome() -> None:
    """이 EVPN 전용 프로필로 떠 있는 크롬만 닫는다 — 남아 있으면 새 크롬이 «기존 세션에서 여는 중» 으로 바로 죽는다.
    선생님이 평소 쓰는 크롬은 건드리지 않는다 (명령줄의 --user-data-dir 이 이 프로필 폴더 «전체 경로»와 같은 것만).
    2026-11-26: 예전엔 폴더 «이름»만 봐서, 임시 폴더로 돌린 시험이 선생님의 진짜 포털 크롬(VPN 끊는 중)을 닫았다.
    psutil 로 먼저 (0.1초 남짓 — PowerShell 은 2초쯤, 2026-11-10). 하나도 안 닫았으면 기다리지 않는다."""
    killed = 0
    want = os.path.normcase(os.path.normpath(str(PROFILE_DIR)))

    def ours(cmdline) -> bool:
        for a in cmdline or []:
            if a.lower().startswith("--user-data-dir="):
                return os.path.normcase(os.path.normpath(a.split("=", 1)[1].strip('"'))) == want
        return False
    try:
        import psutil
        for pr in psutil.process_iter(["name", "cmdline"]):
            if (pr.info.get("name") or "").lower() == "chrome.exe" and ours(pr.info.get("cmdline")):
                try:
                    pr.kill()
                    killed += 1
                except Exception:
                    pass
    except Exception:
        _ps("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like '*--user-data-dir=*{PROFILE_DIR}*' -or $_.CommandLine -like '*--user-data-dir=\"{PROFILE_DIR}\"*' }} | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
        killed = 1
    if killed:
        time.sleep(1)


def _offscreen_args() -> list:
    """창을 모니터 바깥(가상 화면 왼쪽 끝보다 더 왼쪽)에 띄운다 — 끊기용 (2026-11-11).
    playwright 가 기본으로 넣는 --disable-backgrounding-occluded-windows 덕에 화면 밖이어도 페이지는 «보이는 중»이라
    클릭·타이머가 그대로 돈다 (11-11 임시 프로필 실측: visibilityState=visible · 클릭 1.1초). 최소화는 쓰지 않는다 —
    최소화된 창은 그리기(requestAnimationFrame)가 멈춰 playwright 클릭이 걸린다."""
    try:
        import ctypes
        left = ctypes.windll.user32.GetSystemMetrics(76)          # SM_XVIRTUALSCREEN (왼쪽에 모니터가 더 있으면 음수)
    except Exception:
        left = 0
    return [f"--window-position={left - 3000},0", "--window-size=1280,900"]


async def _launch(p, offscreen: bool = False):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    _kill_stale_chrome()
    _allow_protocol()
    args = [a for a in CHROME_ARGS if a != "--start-maximized"] + _offscreen_args() if offscreen else CHROME_ARGS
    # ★ HTTPS 인증서 검증을 켠다 (2026-11-09 점검 반영 — 예전엔 ignore_https_errors=True 로 인증서 오류를 무시했다).
    #   서울 EVPN 포털 인증서는 정상(Sectigo, 2027-04 까지) 확인. 에이전트(127.0.0.1:46435)는 http 라 영향 없다.
    ctx = await p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR), channel="chrome", headless=False,
        args=args, ignore_default_args=["--enable-automation", "--no-sandbox"],
        viewport=None, ignore_https_errors=False)
    await _block_axvpn(ctx)
    try:
        # 포털 공지 팝업(layer_popup) «일주일 동안 보지 않기» — 이 자동화 크롬에만 (선생님 평소 크롬은 그대로).
        # 11-26: 이 팝업이 아이디 칸을 덮어 클릭이 30초 기다리다 실패했다 (index.html 202~216행: 쿠키가 있으면 안 띄운다)
        await ctx.add_cookies([{"name": "maindiv_weekly", "value": "done", "url": evpn_url()}])
    except Exception:
        pass                                           # 못 넣어도 _hide_overlays · _click_field 가 치운다
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    page.on("dialog", lambda d: (print(f"💬  포털 알림: {d.message}"), asyncio.ensure_future(d.dismiss())))
    return ctx, page


def _allow_protocol() -> None:
    """예전 판이 프로필에 심어 둔 axvpn:// 자동 허용·차단 값을 지운다 (아래 _block_axvpn 으로 대신한다).

    크롬 «비밀번호를 저장하시겠습니까?» 풍선도 여기서 끈다 — 포털 로그인 뒤 화면을 가렸다
    (2026-09-18 샌드박스 Sonnet 실측). 첫 실행이라 파일이 없으면 이 두 값만 넣어 만든다.
    """
    prefs = PROFILE_DIR / "Default" / "Preferences"
    if prefs.exists():
        try:
            data = json.loads(prefs.read_text(encoding="utf-8"))
        except Exception:
            return
    else:
        prefs.parent.mkdir(parents=True, exist_ok=True)
        data = {}
    data["credentials_enable_service"] = False
    data.setdefault("profile", {})["password_manager_enabled"] = False
    # 크롬은 닫을 때 창 위치를 기억한다 — 끊기 창을 화면 밖에 띄운 뒤에는 그 위치가 남는다(11-11 실측 left=-3000).
    # 다음 연결 창이 그 자리를 따라가지 않게 매번 지운다 (연결 창은 --start-maximized 로 늘 화면에 꽉 차게)
    data.get("browser", {}).pop("window_placement", None)
    ph = data.get("protocol_handler", {})
    ph.get("excluded_schemes", {}).pop("axvpn", None)
    for v in ph.get("allowed_origin_protocol_pairs", {}).values():
        v.pop("axvpn", None)
    prefs.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


async def _block_axvpn(ctx) -> None:
    """포털 스크립트가 axvpn:// 로 AXGATE 를 새로 띄우지 못하게 그 줄만 무력화한다.

    포털은 에이전트가 늦게 잡히면 `window.location = "axvpn://open…"` 으로 AXGATE 를 띄우려 한다
    (axgate2.js 226·244·376행). AXGATE 는 관리자 권한 프로그램이라 그때마다 «[예]» 창이 뜨거나
    크롬이 «AXGATE VPN Client 을(를) 여시겠습니까?» 를 묻는다 — 2026-09-18 기본 UAC 실측에서
    끊기 도중 [예] 창이 4번 떴다. AXGATE 는 도우미가 띄워 두고, 로그인·로그아웃은 포털이
    http://localhost:46435 로 직접 말하므로(agentCmd) axvpn:// 가 없어도 연결·끊기가 된다.
    이 자동화 크롬에만 적용된다 (선생님이 평소 쓰는 크롬은 그대로).
    """
    async def handler(route):
        try:
            resp = await route.fetch(timeout=20_000)
            body = (await resp.text()).replace('"axvpn://', '"javascript:void 0//')
            await route.fulfill(response=resp, body=body)
        except Exception as e:
            # 고치기에 실패해도 포털은 열려야 한다 — 손대지 않은 원래 스크립트로 넘긴다 (안 그러면 페이지가 멈춘다, 2026-11-10 실측).
            # 이때는 AXGATE 가 «[예]» 창을 띄울 수 있다. 원인은 첫 줄만 (화면 글자·요청 머리글은 버린다)
            print(f"⚠️  포털 스크립트를 고치지 못해 그대로 엽니다 ({type(e).__name__}: {(str(e).splitlines() or [''])[0][:120]})",
                  flush=True)
            try:
                await route.continue_()
            except Exception:
                pass
    await ctx.route("**/axgate*.js*", handler)


def _ensure_agent(wait: float | None = None) -> bool:
    """AXGATE 에이전트가 떠 있게 한다 — 없으면 도우미(예약 작업)가 띄운다. UAC 창 없음.
    기다림은 AGENT_PREWAIT_SEC(5초) — 예전 30초는 늘 헛기다림이었다 (connect_async 설명, 2026-11-10 끊기에서도 확인)."""
    if agent_listening():
        return True
    start_helper()
    return wait_agent(AGENT_PREWAIT_SEC if wait is None else wait)


async def _text(page, el_id: str) -> str:
    try:
        return (await page.evaluate(
            f"() => ((document.getElementById('{el_id}')||{{}}).textContent||'').trim()")) or ""
    except Exception:
        return ""


async def _hide_overlays(page) -> None:
    try:
        await page.evaluate("""(ids) => ids.forEach(id => { const e = document.getElementById(id);
                               if (e) { e.style.display = 'none'; e.style.setProperty('display', 'none', 'important'); } })""",
                            list(OVERLAY_IDS))
    except Exception:
        pass


async def _visible(page, sel: str) -> bool:
    try:
        loc = page.locator(sel)
        return bool(await loc.count()) and await loc.first.is_visible()
    except Exception:
        return False


async def _usable(page, sel: str) -> bool:
    """보이고 «사용 중지»가 풀렸나 — 포털은 아이디 칸을 처음에 disabled 로 두었다가 준비가 끝나면 푼다 (axgate2.js 123·364행)."""
    try:
        return await _visible(page, sel) and await page.locator(sel).first.is_enabled()
    except Exception:
        return False


class _LateLogout(Exception):
    """입력 도중 남은 세션의 [LOGOUT] 이 늦게 나타나 이번 판을 접고 다시 해야 함."""


async def _click_field(page, loc, what: str, stop_on_logout: bool = False) -> None:
    """칸 누르기 — 가림막이 덮으면 30초 기다리지 않고 치운 뒤 다시 누른다 (2026-11-26).
    11-26 실패: 키트가 가림막을 치운 직후 공지 팝업(layer_popup)이 새로 떠 아이디 칸을 덮었고, 클릭이 30초 기다리다 TimeoutError."""
    for i in range(6):
        await _hide_overlays(page)
        try:
            await loc.click(timeout=3_000)
            return
        except Exception as e:
            if type(e).__name__ == "TargetClosedError":
                raise
            if stop_on_logout and await _visible(page, "#logoutButton"):
                raise _LateLogout() from None
            if i == 5:
                enabled = True
                try:
                    enabled = await loc.is_enabled()
                except Exception:
                    pass
                if not enabled:
                    raise RuntimeError(
                        f"EVPN 포털의 {what} 칸이 비활성(사용 중지) 상태입니다 — AXGATE 에이전트 응답이 없습니다 (E_EVPN_DISABLED)"
                    ) from None
                raise RuntimeError(
                    f"EVPN 포털의 {what} 칸을 누르지 못했습니다 — 화면을 무엇이 덮고 있습니다 (E_EVPN_COVERED)"
                ) from None
            await page.wait_for_timeout(500)


async def _open_portal(page, expect_session: bool = True) -> None:
    """포털을 열고 «로그인 칸» 또는 «[LOGOUT]» 이 자리 잡을 때까지 기다린다.

    expect_session: VPN 어댑터가 켜져 있어 세션이 남아 있을 수 있으면 True (끊기 · 이미 붙은 경우).
    ⚠️ 세션이 있어도 처음엔 로그인 폼(#username)이 보이고, 몇 초 뒤 /custom/index.html 로 넘어가서야
       [LOGOUT] 이 생긴다. 로그인 폼만 보고 판단하면 «이미 로그아웃 상태» 로 오판한다 (2026-08-30·09-18 실측)
       → 세션이 있을 수 있으면 예전대로 «폼이 6초 그대로» 를 본다.
    2026-11-17: 어댑터가 꺼진 채 붙이러 갈 때는 폼이 1초 그대로면 바로 넘어간다. 위젯 기록 16번 중 남은 세션 0번,
       포털 열기→[LOGIN] 이 매번 10~13초였고 그중 7.5초(6초 + 1.5초)가 정해진 기다림이었다.
       포털은 [LOGOUT] 을 이 PC 의 AXGATE 에이전트(localhost:46435)에 물어 정하므로 어댑터가 꺼져 있으면 나올 일이 드물다.
       그래도 늦게 나오면 _login_once 가 [LOGIN] 전에 보고 «로그아웃 → 다시» 로 돌린다."""
    t0 = time.monotonic()
    await page.goto(evpn_url(), wait_until="domcontentloaded")
    hold = 12 if expect_session else 2          # 0.5초 단위 — 폼이 이만큼 그대로면 정말 로그아웃 상태
    form_since = seen_form = seen_logout = None
    for i in range(50):
        if await _visible(page, "#logoutButton"):
            seen_logout = time.monotonic() - t0
            break
        # 11-26: 세션이 없을 땐 «보이는» 게 아니라 «사용 중지가 풀린» 뒤부터 센다 — 풀리는 때가 곧 포털 준비 끝이고
        # 공지 팝업도 그 무렵(준비 + 0.1초) 뜬다. 보이기만 한 칸(0.4초)을 세다 팝업이 뜨기 전에 가림막을 치운 게 11-26 실패였다
        if await (_visible(page, "#username") if expect_session else _usable(page, "#username")):
            if form_since is None:
                form_since, seen_form = i, time.monotonic() - t0
            if i - form_since >= hold:
                break
        await page.wait_for_timeout(500)
    if seen_logout is not None:
        await page.wait_for_timeout(1500)       # [LOGOUT] 을 누를 때만 — 안내 팝업이 뜬 뒤에 치운다
    await _hide_overlays(page)
    try:
        path = urllib.parse.urlsplit(page.url).path or "/"
    except Exception:
        path = "?"
    def mark(v):
        return "안 보임" if v is None else f"{v:.1f}초"
    print(f"ℹ️  포털 준비 {time.monotonic() - t0:.1f}초 ({'세션 있을 수 있음' if expect_session else '세션 없음 예상'} · "
          f"로그인 칸 {mark(seen_form)} · LOGOUT {mark(seen_logout)} · 주소 {path})", flush=True)


async def _logout(page) -> None:
    if await _visible(page, "#logoutButton"):
        await _hide_overlays(page)
        try:
            await _click_field(page, page.locator("#logoutButton").first, "로그아웃")
        except Exception as e:
            if type(e).__name__ == "TargetClosedError":
                raise
            await page.locator("#logoutButton").first.dispatch_event("click")
        print("🚪  포털 로그아웃")
        for _ in range(40):
            if tap_status() == "Down" and await _visible(page, "#username"):
                break
            await page.wait_for_timeout(1000)


async def _login_once(page, user: str, pw: str) -> bool:
    """포털 로그인 1회 → 도우미가 2차 인증 → 연결 Yes 를 기다린다."""
    # 에이전트 인식이 로그인보다 먼저여야 포털이 터널을 건다.
    # 11-26: «설치 Yes» 는 AXGATE 가 안 돌아도 Yes 가 된다 — 포털 창이 포커스를 잃으면(지킴이가 돌려주면) «설치됨»으로 친다
    # (axgate2.js 376~386행 hasFocus). 그래서 «실행 Yes»(포털이 AXGATE 에 직접 물어 대답을 받음)를 기다린다. 끝까지 안 나오면 예전처럼 진행
    for _ in range(80):
        if await _text(page, "running_status") == "Yes":
            break
        await page.wait_for_timeout(500)
    if await _visible(page, "#logoutButton"):
        await _logout(page)            # 반쪽 세션이 남아 있으면 깨끗하게 끊고 다시
    loc_u, loc_p = page.locator("#username").first, page.locator("#password").first
    await loc_u.wait_for(state="visible", timeout=20_000)
    await _hide_overlays(page)         # 다시 로그인할 때 안내 팝업(layer_popup)이 아이디 칸을 덮는다 (2026-10-06 실측)
    import secure_store as SS
    allowed = evpn_origins()
    try:                                              # 아이디를 치기 «전»에도 주소부터 확인 (리디렉션 뒤 https·호스트·포트)
        SS.check_origin(page.url, allowed)
    except SS.SecureError as e:
        raise RuntimeError(f"EVPN 포털 주소가 설정과 달라 아이디·비밀번호를 넣지 않았습니다 (E_ORIGIN · {e.detail})") from None
    try:
        await _click_field(page, loc_u, "아이디", stop_on_logout=True)
        await loc_u.fill("")
        await loc_u.press_sequentially(user, delay=50)   # 포털이 붙여넣기를 막고 키 입력을 본다
    except _LateLogout:
        print("ℹ️  남은 세션이 늦게 보였습니다 — 로그아웃하고 다시 합니다", flush=True)
        return False
    except Exception as e:
        if type(e).__name__ == "TargetClosedError":
            raise
        if await _visible(page, "#logoutButton"):
            print("ℹ️  남은 세션이 늦게 보였습니다 — 로그아웃하고 다시 합니다", flush=True)
            return False
        raise
    secret = None
    try:
        await _click_field(page, loc_p, "비밀번호", stop_on_logout=True)
        await loc_p.fill("")
        secret = SS.reveal_for(pw, page.url, allowed)  # ← 주소를 한 번 더 확인한 «뒤»에 금고에서 꺼낸다 (치기 직전)
        await loc_p.press_sequentially(secret, delay=50)
    except _LateLogout:
        print("ℹ️  남은 세션이 늦게 보였습니다 — 로그아웃하고 다시 합니다", flush=True)
        return False
    except SS.SecureError as e:
        raise RuntimeError(f"EVPN 비밀번호를 넣지 않았습니다 ({e.code}{' · ' + e.detail if e.code == 'E_ORIGIN' else ''})") from None
    except RuntimeError as e:
        if "E_EVPN_COVERED" in str(e) or "E_EVPN_DISABLED" in str(e):
            raise
        if await _visible(page, "#logoutButton"):
            print("ℹ️  남은 세션이 늦게 보였습니다 — 로그아웃하고 다시 합니다", flush=True)
            return False
        raise RuntimeError("EVPN 포털 비밀번호 칸에 넣는 중 화면이 바뀌었습니다 (E_EVPN_FILL · 원문 생략)") from None
    except Exception as e:
        if type(e).__name__ == "TargetClosedError":
            raise
        if await _visible(page, "#logoutButton"):     # 치는 중에 남은 세션의 [LOGOUT] 이 늦게 나타났다 → 이번 판은 접고 다시
            print("ℹ️  남은 세션이 늦게 보였습니다 — 로그아웃하고 다시 합니다", flush=True)
            return False
        # playwright 입력 오류 원문에는 치려던 값이 섞일 수 있다 — 원문을 버린다
        raise RuntimeError("EVPN 포털 비밀번호 칸에 넣는 중 화면이 바뀌었습니다 (E_EVPN_FILL · 원문 생략)") from None
    finally:
        secret = None                                 # 참조만 끊는다 (str 이라 메모리에서 지워진다는 보장은 없다)
    if await _visible(page, "#logoutButton"):         # [LOGIN] 누르기 직전 마지막 확인 (11-17 — 폼 기다림을 줄인 대신)
        print("ℹ️  남은 세션이 늦게 보였습니다 — 로그아웃하고 다시 합니다", flush=True)
        return False
    await _hide_overlays(page)
    try:
        await page.click("#loginButton", timeout=8_000)
    except Exception:
        await page.locator("#loginButton").dispatch_event("click")
    print("🔐  EVPN 포털 로그인 — 2차 인증은 도우미가 넣습니다")
    for i in range(REGISTER_WAIT_SEC // 2):
        if await _text(page, "connect_status") == "Yes" and tap_status() == "Up":
            return True
        await page.wait_for_timeout(2000)
    return False


async def connect_async() -> bool:
    from playwright.async_api import async_playwright

    if not client_installed():
        return install()                          # 설치 안내를 찍고 False
    if not helper_installed():
        print("❌  EVPN 도우미가 아직 설치되지 않았습니다 → python evpn.py install (처음 한 번)")
        return False
    user, pw = load_credentials()
    if not (user and pw):
        if not user:
            print("❌  EVPN 아이디가 저장되지 않았습니다 → 보안 설정창(python secure_settings.py)에서 넣어 주세요")
        else:
            print("❌  EVPN 비밀번호가 없습니다 → 보안 설정창에서 «따로 저장» 또는 «인증서 암호와 같음» 을 골라 주세요")
        return False
    if url_note := ("" if evpn_url() == SEOUL_EVPN_URL else " (서울 외 지역 — 아직 시험 안 한 주소)"):
        print(f"ℹ️  EVPN 주소: {evpn_url()}{url_note}")

    if not start_helper():
        print("❌  도우미 예약 작업을 실행하지 못했습니다 → python evpn.py install 을 다시 해 주세요")
        return False
    if not agent_listening():
        # 예전엔 여기서 30초 → 5초를 기다렸다. 에이전트 포트(46435)는 VPN 이 붙어 있을 때조차 «듣는 중» 으로 안 잡히기도 했고
        # (2026-11-10), 그래도 연결은 매번 됐다. 기다리지 않고 바로 포털을 띄운다 (2026-11-18):
        # 포털 스크립트(axgate2.js)가 AXGATE 가 안 떠 있으면 2초마다 다시 확인한다(checkInstallInterval) — AXGATE 가 뜨는 동안
        # 포털 크롬·페이지가 같이 열린다. 위젯 기록: 에이전트를 못 찾은 날(그날 첫 연결)은 글자 누름 → 포털까지 15~18초였다.
        print("ℹ️  AXGATE 에이전트 확인은 포털 화면이 합니다", flush=True)

    # 어댑터가 꺼져 있으면 포털에 남은 세션([LOGOUT])이 나올 일이 드물다 → 로그인 칸이 1초 그대로면 바로 친다 (11-17)
    session_possible = tap_status() == "Up"
    # 포털 창은 화면 밖에 띄우고, 키보드 포커스를 가져가면 지킴이가 돌려준다 (2026-11-18) — 아이디·비밀번호를 치는 동안
    # 선생님 타자가 그 칸에 섞이지 않게. 최소화는 안 한다 (최소화하면 클릭·치기가 1.1초 → 4.2초, 11-18 임시 프로필 실측)
    guard = focus_guard.FocusGuard(PROFILE_DIR.name, keep_shown=True).start()
    async with async_playwright() as p:
        ctx, page = await _launch(p, offscreen=True)
        try:
            # 이 줄이 없으면 글자 위젯이 포털 창이 떠 있는 동안 «준비 중 94초» 만 보여 줬다 (2026-11-12 —
            # 첫 연결에서 포털이 40초 넘게 멈춘 듯 보이자 선생님이 창을 닫으셨다). 위젯은 이 줄을 «VPN 연결 중» 으로 읽는다
            print("🌐  EVPN 포털 여는 중 — 포털 창은 화면 밖에 열려 안 보입니다. 작업표시줄의 크롬 단추는 닫지 말고 두세요 "
                  "(그동안 다른 창에서 일하셔도 됩니다)", flush=True)
            await _open_portal(page, expect_session=session_possible)
            if await _text(page, "connect_status") == "Yes" and tap_status() == "Up":
                print("✅  EVPN 이미 연결되어 있습니다")
                return True
            for attempt in (1, 2):                 # 실패하면 한 번만 더 (09-18 실측 10번 중 1번 실패, 바로 다시 하면 붙음)
                if attempt == 2:
                    print("🔁  연결이 안 돼서 한 번 더 합니다")
                    start_helper()
                    await _open_portal(page)
                if await _login_once(page, user, pw):
                    print("✅  EVPN 연결됨")
                    return True
                print(f"   · {attempt}번째: 연결 안 됨 (포털 연결={await _text(page, 'connect_status') or '?'}, "
                      f"어댑터={tap_status()})")
            print("❌  EVPN 에 두 번 다 연결하지 못했습니다. 도우미 기록:")
            for line in helper_log_tail():
                print("    " + line)
            return False
        finally:
            guard.stop()
            stop_helper()
            await _close(ctx)


async def _close(ctx, limit: float = 15) -> None:
    """포털 크롬 닫기 — 길어도 15초. 선생님이 창을 먼저 닫았으면 close() 가 오류를 내고, 크롬이 안 닫히면 남은 것을 정리한다.
    (2026-11-12: 포털 창을 닫은 뒤 «준비 중» 이 65초 더 갔다 — 어디서 걸렸는지 못 밝혀 여기부터 시간을 묶었다)"""
    try:
        await asyncio.wait_for(ctx.close(), limit)
    except Exception:
        _kill_stale_chrome()


async def _logout_once(p, offscreen: bool) -> bool:
    ctx, page = await _launch(p, offscreen)
    try:
        await _open_portal(page)
        await _logout(page)
        for _ in range(20):
            if tap_status() == "Down":
                return True
            await page.wait_for_timeout(1000)
        return False
    except Exception as e:
        # 포털 크롬이 도중에 닫혔다 — 2026-11-11 선생님이 작업표시줄의 (화면 밖) 포털 크롬을 닫으셨고, 원문 오류
        # (TargetClosedError 호출 기록)가 «VPN 끊기» 창에 그대로 찍혔다. 원문은 버리고 어댑터로 판단한다
        print(f"⚠️  포털 창이 도중에 닫혔습니다 ({type(e).__name__}) — VPN 이 끊겼는지 어댑터로 확인합니다", flush=True)
        return tap_status() == "Down"
    finally:
        await _close(ctx)


async def disconnect_async() -> bool:
    """포털 로그아웃으로 끊는다 (AXGATE 프로세스를 죽여도 터널은 안 끊긴다 — 2026-08-29 실측).

    포털 창은 먼저 화면 밖에 띄운다 (2026-11-11). 글자 위젯으로 나이스를 닫고 얼마 뒤 포털 창이 갑자기 뜨자
    선생님이 «요청도 안 했는데 나이스 로그인 화면이 다시 떴다» 고 놀라셨다. 화면 밖 창으로 안 끊기면
    보이는 창으로 한 번 더 한다 — 어느 쪽으로 끊겼는지는 «✅» 줄에 남는다 (글자 위젯 기록 desktop-text.log)."""
    from playwright.async_api import async_playwright

    print("🔎  VPN 상태 확인 중…", flush=True)            # 시작 메뉴 «VPN 끊기» 창이 비어 보이지 않게 (11-10 선생님 제보)
    if tap_status() == "Down":
        print("✅  이미 끊겨 있습니다", flush=True)
        return True
    print("🔌  VPN 끊는 중 — 포털에서 로그아웃합니다 (20~30초). 포털 창은 화면 밖에 열려 안 보이고, "
          "작업표시줄에 크롬 단추만 잠깐 생깁니다 — 닫지 말고 두세요", flush=True)
    _ensure_agent()                                     # 결과는 안 본다 — 에이전트는 늘 «안 떠 있음»으로 나와도 로그아웃은 됐다
    guard = focus_guard.FocusGuard(PROFILE_DIR.name, keep_shown=True).start()   # 끊는 동안에도 선생님 포커스를 안 뺏게 (11-18)
    async with async_playwright() as p:
        try:
            for offscreen in (True, False):
                if not offscreen:
                    # «한 번 더 합니다» 는 쓰지 않는다 — 글자 위젯이 그 말을 «VPN 연결 중» 으로 읽는다 (DesktopText.OnLine)
                    print("↩️  화면 밖 창으로는 안 끊겨서, 보이는 포털 창으로 다시 끊습니다 (20~30초)", flush=True)
                if await _logout_once(p, offscreen):
                    print("✅  EVPN 끊김" + (" (화면 밖 창)" if offscreen else " (보이는 창)"), flush=True)
                    return True
            print(f"❌  아직 끊기지 않았습니다 (어댑터={tap_status()}) — 시작 메뉴(또는 바탕화면)의 «VPN 끊기» 를 한 번 더 눌러 주세요",
                  flush=True)
            return False
        finally:
            guard.stop()
            stop_helper()


PORTAL_MUTEX = "Local\\NeisAutomation.EVPN.Portal"
PORTAL_WAIT_SEC = 150                    # 연결(최대 70초 + 포털) · 끊기(20~50초)가 끝나기를 기다리는 한도


class _PortalTurn:
    """EVPN 포털 크롬(같은 프로필)은 한 번에 하나만 띄운다 — 연결·끊기 차례 잡기.

    2026-11-11: 에듀파인을 닫아 글자 위젯이 VPN 을 끊는 중에 선생님이 시작 메뉴 «VPN 끊기» 를 누르셨다.
    둘 다 같은 프로필로 포털 크롬을 띄우는데, 띄울 때 _kill_stale_chrome() 이 «남은» 크롬을 닫는다 → 상대 크롬까지 닫혔다.
    윈도우 뮤텍스라서 쥔 프로세스가 죽으면 저절로 풀린다(WAIT_ABANDONED) — 남는 잠금 파일이 없다.
    기다려도 안 풀리면 예전처럼 그냥 진행한다 (막혀서 아무것도 못 하는 것보다 낫다)."""

    def __enter__(self):
        self.h, self.got, self.k32 = None, False, None
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateMutexW.restype = wintypes.HANDLE
            k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            k32.WaitForSingleObject.restype = wintypes.DWORD
            k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            k32.ReleaseMutex.argtypes = [wintypes.HANDLE]
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            self.k32, self.h = k32, k32.CreateMutexW(None, False, PORTAL_MUTEX)
        except Exception:
            return self
        if not self.h:
            return self
        r = self.k32.WaitForSingleObject(self.h, 0)
        if r == 0x102:                                    # WAIT_TIMEOUT — 누가 포털을 쓰는 중
            print(f"⏳  다른 곳에서 VPN 을 연결하거나 끊는 중이라, 끝나기를 기다립니다 (최대 {PORTAL_WAIT_SEC}초)", flush=True)
            r = self.k32.WaitForSingleObject(self.h, PORTAL_WAIT_SEC * 1000)
        self.got = r in (0, 0x80)                         # WAIT_OBJECT_0 · WAIT_ABANDONED(쥔 쪽이 죽음)
        return self

    def __exit__(self, *exc):
        if self.h:
            if self.got:
                self.k32.ReleaseMutex(self.h)
            self.k32.CloseHandle(self.h)
        return False


def connect() -> bool:
    with _PortalTurn():
        return asyncio.run(connect_async())


def disconnect() -> bool:
    with _PortalTurn():
        try:
            return asyncio.run(disconnect_async())
        except Exception as e:
            # 어떤 오류든 호출 기록을 «VPN 끊기» 창에 쏟지 않는다 — 한 줄 + 어댑터로 판단
            print(f"⚠️  끊는 중 오류 ({type(e).__name__}) — VPN 이 끊겼는지 어댑터로 확인합니다", flush=True)
            if tap_status() == "Down":
                print("✅  EVPN 끊김", flush=True)
                return True
            print("❌  아직 끊기지 않았습니다 — 시작 메뉴(또는 바탕화면)의 «VPN 끊기» 를 한 번 더 눌러 주세요", flush=True)
            return False


# ============================================================ setup
def setup(user: str | None) -> int:
    cfg = _cfg()
    if not cfg:     # EVPN 만 먼저 쓰는 분도 있다 — 출결 설정(마법사) 없이 지역 주소만 기본값으로 시작
        cfg = {"neis_url": teacher_config.DEFAULTS["neis_url"]}
    if user:
        cfg["evpn_user"] = user.strip()
        teacher_config.save_config(cfg)
        print("✅  EVPN 아이디 저장됨")
    print("ℹ️  EVPN 비밀번호는 보안 설정창(python secure_settings.py)에서 «따로 저장» 하거나")
    print("    «인증서 암호와 같음» 을 직접 고르세요. (고르지 않으면 인증서 암호를 대신 쓰지 않습니다)")
    print("    검은 창으로 하려면: python evpn.py password  (새 창에만 비밀번호를 칩니다)")
    u, p = load_credentials()
    print(f"확인: 아이디 {'있음' if u else '없음'} · 비밀번호 {'있음' if p else '없음'}")
    return 0 if (u and p) else 1


def password_window() -> int:
    """EVPN 비밀번호를 새 창에서 받는다 — 클로드 대화에 남지 않게."""
    if os.environ.get("NEIS_EVPN_PW_WINDOW") != "1":
        env = dict(os.environ, NEIS_EVPN_PW_WINDOW="1")
        print("🪟  새 검은 창이 뜹니다. 거기에 EVPN 비밀번호를 치고 Enter 를 눌러 주세요.", flush=True)
        proc = subprocess.Popen([sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "password"],
                                env=env, creationflags=subprocess.CREATE_NEW_CONSOLE,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc.wait(timeout=900)
        import secure_store as SS
        ok = SS.vault_exists(teacher_config.KEYRING_SERVICE, KEYRING_EVPN_ACCOUNT)     # 있다/없다만
        if ok and password_mode() != "separate":
            cfg = _cfg()
            if cfg:
                cfg["evpn_password_mode"] = "separate"       # 따로 넣었으면 «따로 저장» 을 고른 것
                teacher_config.save_config(cfg)
        print("✅  EVPN 비밀번호 저장됨" if ok else "❌  저장되지 않았습니다")
        return 0 if ok else 1
    try:
        sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
        # 부모가 출력을 DEVNULL 로 넘겨서, 안 바꾸면 안내 문구가 새 창에 안 보인다
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        pass
    teacher_config.prepare_console_window()
    import secure_store as SS
    # 지울 수 있는 버퍼 → 금고 → 0 으로 덮기 (전체 비밀번호 str 을 만들지 않는다)
    with SS.SecretBuffer() as buf:
        if SS.read_secret_console("EVPN 비밀번호 (화면에 안 보입니다 · Esc = 취소): ", buf) and len(buf):
            try:
                SS.vault_write_buffer(teacher_config.KEYRING_SERVICE, KEYRING_EVPN_ACCOUNT, buf)
                print("저장했습니다. 이 창은 곧 닫힙니다.")
            except SS.SecureError as e:
                print(f"저장하지 못했습니다 ({e.code}).")
            time.sleep(2)
    return 0


def status() -> int:
    u, p = load_credentials()
    print(f"EVPN 주소            {evpn_url()}")
    print(f"AXGATE 클라이언트     {'설치됨' if client_installed() else '❌ 없음'}")
    print(f"EVPN 도우미          {'설치됨' if helper_installed() else '❌ 없음 → python evpn.py install'}")
    print(f"EVPN 아이디·비밀번호  {'있음' if u else '❌ 아이디 없음'} / {'있음' if p else '❌ 없음'}")
    mode = {"same_as_cert": "인증서 암호와 같음(직접 고름)", "separate": "따로 저장",
            "unset": "고르지 않음 — 보안 설정창에서 고르세요"}[password_mode()]
    print(f"EVPN 비밀번호 방식    {mode}")
    cn = _cfg().get("cert_name")    # 없으면 연결은 되다가 2차 인증 창에서 멈춘다 (2026-10-14 실측)
    print(f"인증서 이름(2차 인증)  {'있음' if cn else '❌ 없음 → python secure_settings.py'}")
    print(f"VPN 어댑터           {tap_status()}")
    try:    # 연결이 돼도 이게 없으면 나이스 [인증서 로그인] 이 아무 반응이 없다 (2026-10-02 실측)
        import neis_cert
        print(f"나이스 인증서 프로그램 {'설치됨' if neis_cert.program_installed() else '❌ 없음 → 5-1단계'}")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EVPN 자동 연결")
    ap.add_argument("cmd", nargs="?", default="status",
                    choices=["status", "setup", "password", "install", "connect", "disconnect", "ping"])
    ap.add_argument("--user")
    ap.add_argument("--no-desktop-bat", action="store_true", help="install 때 바탕화면 «VPN 끊기.bat» 를 만들지 않는다")
    a = ap.parse_args()
    if a.cmd == "status":
        sys.exit(status())
    if a.cmd == "setup":
        sys.exit(setup(a.user))
    if a.cmd == "password":
        sys.exit(password_window())
    if a.cmd == "install":
        sys.exit(0 if install(desktop_bat=not a.no_desktop_bat) else 1)
    if a.cmd == "connect":
        sys.exit(0 if connect() else 1)
    if a.cmd == "disconnect":
        sys.exit(0 if disconnect() else 1)
    if a.cmd == "ping":
        sys.exit(ping())

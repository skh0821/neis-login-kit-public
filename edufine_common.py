# -*- coding: utf-8 -*-
"""K-에듀파인 자동화 공통 부품 — 로그인·지역·크롬 띄우기·팝업 닫기·버튼 누르기.

바탕화면 글자 위젯의 에듀파인 · 업무포털 로그인이 쓴다. 혼자서는 아무 일도 하지 않는다.

설정은 나이스 키트와 «한 벌»이다 — setup_wizard.py 가 만든
  %LOCALAPPDATA%\\neis-automation\\teacher_config.json   (cert_name · neis_url · region)
  Windows 자격 증명 관리자 (사람마다 다른 이름 · teacher_config.vault_service)  (인증서 암호)
을 그대로 읽는다. 에듀파인용 설정 파일을 따로 두지 않는다 — 두 벌이면 반드시 어긋난다.

환경변수로 덮어쓸 수 있다 (시험용):
  EDUFINE_REGION=경기      EDUFINE_PROFILE=<빈 폴더>   EDUFINE_CERT_NAME=홍길동
"""
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

try:                                   # 한글이 ??? 로 깨지지 않게 — 환경변수에 기대지 않는다
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
BASE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "neis-automation"
CONFIG_FILE = BASE / "teacher_config.json"
# 에듀파인 전용 크롬 프로필. 평소 쓰는 크롬과 섞이지 않는다.
# 빈 프로필로 «첫 실행»을 재 보려면 EDUFINE_PROFILE 로 갈아끼운다.
PROFILE_DIR = os.environ.get("EDUFINE_PROFILE", str(BASE / "chrome_profile_edufine"))
# 캡처·읽은 품의는 이 폴더의 캡처/ 에 둔다 — 클로드는 이 폴더 밖 파일을 못 연다
# (2026-09-18 실측). .gitignore 가 캡처/ 를 막으니 저장소에는 안 올라간다.
OUT_DIR = HERE / "캡처"


def log(msg=""):
    print(msg, flush=True)


# ── 지역 (2026-09-11 · 09-18 DNS 로 34+17개 주소 확인. 화면까지 본 곳은 서울뿐) ──
# ⚠️ 경북만 K-에듀파인이 `.go.kr` 이 아니라 `klef.gbe.kr` 이다.
REGIONS = {
    "서울": "sen", "부산": "pen", "대구": "dge", "인천": "ice", "광주": "gen",
    "대전": "dje", "울산": "use", "세종": "sje", "경기": "goe", "강원": "gwe",
    "충북": "cbe", "충남": "cne", "전북": "jbe", "전남": "jne", "경북": "gbe",
    "경남": "gne", "제주": "jje",
}
CODE_TO_REGION = {v: k for k, v in REGIONS.items()}


def load_cfg():
    """평문 설정 + 암호화된 개인 설정(인증서 이름 등, secure\\)을 합쳐 읽는다 (teacher_config 와 한 벌)."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import teacher_config
        return teacher_config.load_config_or_empty()
    except Exception:
        return {}


def region():
    """지역을 정한다. 순서: 환경변수 → 설정의 region → 설정의 neis_url 앞글자 → 서울."""
    cfg = load_cfg()
    name = os.environ.get("EDUFINE_REGION") or cfg.get("region")
    if not name:
        host = urlsplit(cfg.get("neis_url") or "").netloc.split(":")[0]
        name = CODE_TO_REGION.get(host.split(".")[0].lower())
    name = (name or "서울").strip()
    if name not in REGIONS:
        raise SystemExit("설정의 지역 '%s' 을 모르겠습니다. 이 중 하나여야 합니다:\n  %s"
                         % (name, " · ".join(REGIONS)))
    return name


REGION = region()
CODE = REGIONS[REGION]
PORTAL = os.environ.get("EDUFINE_PORTAL", "https://%s.eduptl.kr" % CODE)
KLEF = os.environ.get("EDUFINE_KLEF",
                      "https://klef.gbe.kr/" if CODE == "gbe" else "https://klef.%s.go.kr/" % CODE)
IDP = "https://idp1-%s.neis.go.kr:9443" % CODE


def edufine_origins():
    """인증서 암호를 넣어도 되는 주소 — 우리 지역 업무포털·인증 서버 (https · 정확한 호스트·포트).
    환경변수로 바꾼 주소가 공식 꼴(○○○.eduptl.kr · idp1-○○○.neis.go.kr:9443)이 아니면 넣지 않는다."""
    import re
    from urllib.parse import urlsplit
    out = []
    for url, pat in ((PORTAL, r"[a-z]{3}\.eduptl\.kr"), (IDP, r"idp1-[a-z]{3}\.neis\.go\.kr")):
        u = urlsplit(url)
        if u.scheme == "https" and re.fullmatch(pat, (u.hostname or "").lower()):
            out.append("https://%s%s" % (u.hostname.lower(), ":%d" % u.port if u.port else ""))
    return out


def creds():
    """인증서 이름은 설정 파일에서, 암호는 자격 증명 관리자에서 꺼낸다. 암호는 어디에도 찍지 않는다."""
    name = os.environ.get("EDUFINE_CERT_NAME") or load_cfg().get("cert_name")
    if not name:
        raise SystemExit(
            "인증서 이름이 설정에 없습니다 (%s).\n"
            "  설정 창을 먼저 열어 주세요:  .\\.venv\\Scripts\\python.exe -X utf8 secure_settings.py"
            % CONFIG_FILE)
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import secure_store as SS
    # ★ 암호 자체가 아니라 «금고의 어느 칸» 표(VaultSecret)를 돌려준다 — login() 이 암호칸에 넣기 직전에 꺼낸다 (2026-11-09)
    # 예전 에듀파인 배포판은 'edufine' 이름으로 넣게 했다 — 거기 있으면 그것도 쓴다
    for svc in (__import__("teacher_config").KEYRING_SERVICE,):
        ticket = SS.VaultSecret(svc, name)
        if ticket.exists():
            return name, ticket
    raise SystemExit(
        "인증서 암호가 저장돼 있지 않습니다.\n"
        "  암호 넣기:  .\\.venv\\Scripts\\python.exe -X utf8 secure_password.py open --target cert")


# ── 크롬 띄우기 ───────────────────────────────────────────────
# 이 컴퓨터에 깔린 구글 크롬을 쓴다(channel="chrome"). playwright 전용 브라우저는
# 새 컴퓨터에서 «side-by-side configuration» 오류로 안 켜진 적이 있다 (2026-09-16 실측).
CHROME_ARGS = [
    "--profile-directory=Default",
    # «자동화 탐지 피하기» 설정은 넣지 않는다 — 이 키트는 자동화임을 숨기지 않는다
    # 이 줄이 없으면 업무포털이 127.0.0.1 보안프로그램에 못 붙어 «보안프로그램 로딩중»에서 멈춘다
    "--disable-features=PrivateNetworkAccessForNavigations,"
    "PrivateNetworkAccessPermissionPrompt,PermissionChip",
    "--noerrdialogs", "--test-type",      # 최대화/화면 밖은 _window_args()
]


def profile_in_use():
    """에듀파인 전용 크롬 프로필을 다른 크롬이 쓰고 있나. 쓰는 동안 크롬이 lockfile 을 잠근다 (윈도우 실측)."""
    lock = os.path.join(PROFILE_DIR, "lockfile")
    if not os.path.exists(lock):
        return False
    try:
        open(lock, "a").close()
        return False
    except PermissionError:
        return True


def wait_profile_free(timeout=180):
    """★ 프로필이 쓰이는 중에 띄우면 새 창이 안 뜨고 «그 크롬에 about:blank 탭만» 늘어난다
    (2026-10-08 — 다른 클로드 창이 같은 프로필로 공문을 받는 동안 탭이 2개씩 쌓였다). 비워질 때까지 기다린다."""
    if not profile_in_use():
        return
    log("  ⏳ 에듀파인 자동화용 크롬이 다른 작업에 쓰이는 중입니다 — 끝날 때까지 기다립니다 (최대 %d초)" % timeout)
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(3)
        if not profile_in_use():
            time.sleep(2)
            return
    raise SystemExit("에듀파인 자동화용 크롬이 %d초 넘게 다른 작업에 쓰이고 있습니다. "
                     "그 작업이 끝나거나 그 크롬 창을 닫은 뒤 다시 실행해 주세요." % timeout)


def allow_wxsclient():
    """기안기(wxsclient://) 를 «항상 열기» 로 미리 허용한다.
    처음 쓰는 크롬 프로필은 «WXSClient MFC 응용 프로그램을 여시겠습니까?» 를 묻는데, 크롬이 그린 창이라
    스크립트가 못 누르고 기안 창이 30초 안에 안 뜬다 (2026-10-14 실측). 그 창의 [항상 허용] 체크와 같은 값이다."""
    from urllib.parse import urlsplit
    u = urlsplit(KLEF)
    origin = "%s://%s" % (u.scheme, u.netloc)
    pref = Path(PROFILE_DIR) / "Default" / "Preferences"
    try:
        d = json.loads(pref.read_text(encoding="utf-8")) if pref.exists() else {}
        pairs = d.setdefault("protocol_handler", {}).setdefault("allowed_origin_protocol_pairs", {})
        if pairs.get(origin, {}).get("wxsclient") is True:
            return
        pairs.setdefault(origin, {})["wxsclient"] = True
        pref.parent.mkdir(parents=True, exist_ok=True)
        pref.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception as e:                  # 못 고쳐도 손으로 [열기] 를 누르면 되니 멈추지 않는다
        log("  (기안기 열기 허용을 미리 못 넣었습니다: %s)" % e)


def _window_args():
    """NEIS_HIDE=1(시트 «화면: 작게») 이면 화면 밖 창. teacher_config.window_args() 와 같은 규칙."""
    if os.environ.get("NEIS_HIDE") == "1":
        return ["--window-position=-2400,-2400", "--window-size=1920,1080"]
    return ["--start-maximized"]


def launch(p):
    """에듀파인 전용 크롬을 띄우고 (ctx, page) 를 돌려준다."""
    wait_profile_free()
    allow_wxsclient()
    try:                                   # 크롬이 인증서 암호를 따로 저장하지 않게 (위젯·EVPN 프로필과 같은 설정)
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import teacher_config
        teacher_config.no_password_saving(PROFILE_DIR)
    except Exception:
        pass
    # --no-sandbox 도 뺀다 — Playwright 가 기본으로 붙여 크롬 «샌드박스»(페이지를 가두는 보호벽)를 끄고,
    # 크롬이 «지원되지 않는 명령줄 플래그(--no-sandbox)…» 경고를 띄웠다 (2026-11-10 선생님 제보). 나이스·EVPN 코드와 같게
    kw = dict(user_data_dir=PROFILE_DIR, headless=False, args=CHROME_ARGS + _window_args(),
              ignore_default_args=["--enable-automation", "--no-sandbox"], viewport=None)
    try:
        ctx = p.chromium.launch_persistent_context(channel="chrome", **kw)
    except Exception as e:
        msg = str(e)
        if profile_in_use():                 # 그 사이 다른 작업이 잡았다 — 두 번째 크롬으로 또 띄우면 탭만 는다
            raise SystemExit("에듀파인 자동화용 크롬이 다른 작업에 쓰이고 있습니다. 끝난 뒤 다시 실행해 주세요.")
        if "ProcessSingleton" in msg or "already in use" in msg or "user data directory" in msg:
            raise SystemExit(
                "에듀파인 자동화용 크롬이 이미 켜져 있습니다. 그 창을 닫고 다시 실행해 주세요.\n"
                "  (평소 쓰시는 크롬은 닫지 않으셔도 됩니다)")
        log("  (깔린 크롬으로 못 띄워서 playwright 크롬으로 띄웁니다: %s)" % msg.splitlines()[0][:100])
        ctx = p.chromium.launch_persistent_context(**kw)
    grant_local_network(ctx)             # ★ 페이지를 열기 «전»에
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.on("dialog", on_dialog)
    ctx.on("page", lambda pg: pg.on("dialog", on_dialog))    # 팝업 창에서 뜨는 확인창도
    return ctx, page


def is_ip_takeover(msg):
    """«IP(…)에서 접속 중입니다. 기존 사용자를 종료 후 계속 진행하시겠습니까?» 창인가."""
    return "접속 중입니다" in msg and "기존 사용자를 종료" in msg


def on_dialog(d):
    """브라우저 확인창 처리 — «다른 IP 에서 접속 중» 창만 [확인], 나머지는 모두 «아니오».

    접속 IP 가 바뀌면(학교↔집·EVPN) 에듀파인이 이 창을 띄운다. «아니오»로 닫으면
    «중복 로그인 안내» 페이지로 빠져 메뉴를 못 연다 (2026-09-18 학교 밖 EVPN 실측).
    [확인]하면 다른 곳(학교 PC 등)에 켜 둔 에듀파인은 끊긴다 — 2026-09-11 사용자 결정.
    """
    msg = " ".join((d.message or "").split())
    try:
        if is_ip_takeover(msg):
            log("  💬 다른 곳의 에듀파인 접속을 끊고 들어갑니다: %s" % msg[:120])
            d.accept()
        else:
            log("  💬 확인창을 «아니오»로 닫았습니다: %s" % msg[:120])
            d.dismiss()
    except Exception:
        pass                         # 이미 닫힌 창


def grant_local_network(ctx):
    """크롬의 «이 기기의 다른 앱 및 서비스에 액세스» 말풍선을 미리 허용한다.

    이 말풍선은 크롬이 그린 것이라 자동으로 못 누른다. 안 눌리면 보안프로그램에 영영 못 닿고
    «보안프로그램이 로딩중입니다» 가림막이 안 걷힌다 (2026-09-17 빈 프로필 실측 — 첫 실행이 막히던 진짜 원인).
    """
    ok = 0
    for u in (PORTAL, KLEF, IDP):
        sp = urlsplit(u)
        try:
            ctx.grant_permissions(["local-network-access"], origin="%s://%s" % (sp.scheme, sp.netloc))
            ok += 1
        except Exception as e:
            log("  (로컬 네트워크 접근을 미리 허용하지 못했습니다: %s)" % str(e).splitlines()[0][:100])
            log("  → 크롬이 «이 기기의 다른 앱 및 서비스에 액세스» 를 물어보면 [허용] 을 눌러 주세요.")
            return False
    return ok > 0


# ── 업무포털 «불러오는 중» 가림막 ─────────────────────────────────
LOAD_OVERLAY = ".isloading-overlay, [class*='isloading'], [class*='loading-overlay'], .blockUI"

JS_OVERLAY_COUNT = """
(s) => [...document.querySelectorAll(s)].filter(e => {
  const st = getComputedStyle(e);
  if (st.display === 'none' || st.visibility === 'hidden') return false;
  if (parseFloat(st.opacity === '' ? '1' : st.opacity) === 0) return false;
  if (st.pointerEvents === 'none') return false;
  const r = e.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}).length
"""

JS_KILL_OVERLAY = """
(s) => {
  let n = 0;
  document.querySelectorAll(s).forEach(e => {
    const st = getComputedStyle(e);
    if (st.display !== 'none' && st.visibility !== 'hidden' && st.pointerEvents !== 'none') {
      e.style.setProperty('pointer-events', 'none', 'important'); n++;
    }
  });
  return n;
}
"""


def wait_overlay_gone(page, timeout=90, quiet=False):
    end = time.time() + timeout
    warned = False
    while time.time() < end:
        try:
            if page.evaluate(JS_OVERLAY_COUNT, LOAD_OVERLAY) == 0:
                return True
        except Exception:
            pass
        if not warned and not quiet:
            log("  화면이 아직 다 안 열렸습니다. 조금 기다립니다...")
            warned = True
        page.wait_for_timeout(1000)
    return False


def robust_click(page, loc, label):
    """보통 클릭 → 안 되면 가림막에 클릭 통과(pointer-events:none)를 박고 진짜 클릭.
    force 클릭·JS 클릭은 가림막 앞에서 소용없었다 (2026-09-17 실측)."""
    try:
        loc.click(timeout=15000)
        return
    except Exception:
        pass
    n = page.evaluate(JS_KILL_OVERLAY, LOAD_OVERLAY)
    log("  (가림막 %d개를 걷고 '%s' 를 다시 누릅니다)" % (n, label))
    loc.click(timeout=15000)


MANUAL_WAIT_MIN = int(os.environ.get("EDUFINE_MANUAL_WAIT_MIN", "10"))


def manual_login(page):
    """자동 로그인이 안 되면 죽지 않고, 열린 크롬에서 사람이 한 번 로그인하기를 기다린다.
    한 번만 손으로 하면 프로필에 세션이 남아 다음부터는 자동이다."""
    try:
        page.evaluate(JS_KILL_OVERLAY, LOAD_OVERLAY)
    except Exception:
        pass
    log("")
    log("=" * 60)
    log("  자동 로그인이 안 됩니다. 지금 열려 있는 크롬 창에서 직접 로그인해 주세요.")
    log("  (처음 한 번만 하시면 됩니다)")
    log("    1. 크롬이 «이 기기의 다른 앱 및 서비스에 액세스» 를 물으면 [허용]")
    log("    2. [교육행정 전자서명 인증서 로그인] → 본인 인증서 → 암호")
    log("    3. 로그인되면 그대로 두세요. 알아서 이어서 진행합니다")
    log("  단추가 안 눌리면 F5 로 새로고침하고 10초 뒤 다시 눌러 보세요.")
    log("  %d분 안에 안 되면 그만둡니다." % MANUAL_WAIT_MIN)
    log("=" * 60)
    end = time.time() + MANUAL_WAIT_MIN * 60
    next_kill = 0.0
    while time.time() < end:
        try:
            if "bpm_man_mn00_001" in page.url:
                log("✅ 로그인 확인 — 이어서 진행합니다")
                return
        except Exception:
            raise SystemExit("크롬 창이 닫혔습니다. 다시 실행해 주세요.")
        if time.time() >= next_kill:           # 화면이 다시 그려지면 가림막이 또 붙는다
            next_kill = time.time() + 3
            try:
                page.evaluate(JS_KILL_OVERLAY, LOAD_OVERLAY)
            except Exception:
                pass
        page.wait_for_timeout(2000)
    raise SystemExit("%d분 동안 로그인이 안 됐습니다.\n"
                     "  인증서가 이 컴퓨터에 있는지 보세요:  dir C:\\GPKI\\Certificate\\class2"
                     % MANUAL_WAIT_MIN)


def login(page, cert_name, cert_pw):
    log("🔐 업무포털 로그인 (지역: %s / %s)" % (REGION, PORTAL))
    for i in range(3):      # 첫 접속이 가끔 net::ERR_CERT_AUTHORITY_INVALID 로 튕긴다 — 바로 다시 하면 된다 (2026-10-06 실측)
        try:
            page.goto(PORTAL, wait_until="domcontentloaded")
            break
        except Exception as e:
            if i == 2 or "net::" not in str(e):
                raise
            log("  (첫 접속이 튕겨서 다시 들어갑니다: %s)" % str(e).splitlines()[0][:80])
            page.wait_for_timeout(3000)
    wait_overlay_gone(page, timeout=90)
    btn = page.get_by_role("button", name="교육행정 전자서명 인증서 로그인", exact=True)
    reloaded = False
    retried = 0
    for i in range(60):
        if "bpm_man_mn00_001" in page.url:
            log("✅ 이미 로그인돼 있습니다")
            return
        # 예전 접속이 «중복로그인»으로 끊긴 채 남아 있으면 첫 화면이 오류 쪽지로 간다
        # (idp1-…/error.jsp?errorCode=1800, 2026-09-18 실측). 다시 들어가면 로그인 버튼이 나온다.
        if "errorCode=" in page.url and retried < 3:
            retried += 1
            log("  지난 접속이 끊긴 채 남아 있어 로그인 화면으로 다시 들어갑니다 (%s)"
                % page.url.split("errorCode=")[-1][:6])
            if retried >= 2:                  # 그래도 같으면 이 전용 크롬의 쿠키만 지운다
                page.context.clear_cookies()
            page.goto(PORTAL, wait_until="domcontentloaded")
            wait_overlay_gone(page, timeout=60, quiet=True)
            continue
        try:
            if btn.count() and btn.last.is_visible():
                break
        except Exception:
            pass
        if i == 20 and not reloaded:
            reloaded = True
            page.reload(wait_until="domcontentloaded")
            wait_overlay_gone(page, timeout=60)
        page.wait_for_timeout(1500)
    else:
        return manual_login(page)

    wait_overlay_gone(page, timeout=60)
    robust_click(page, btn.last, "교육행정 전자서명 인증서 로그인")
    wait_overlay_gone(page, timeout=60, quiet=True)
    pwd = page.get_by_role("textbox", name="인증서 암호 입력필드")
    try:
        pwd.wait_for(timeout=90000)
    except Exception:
        try:    # 인증서 창이 안 뜬 가장 흔한 이유 — 이 PC 에 인증서 프로그램이 없다
            import neis_cert
            h = neis_cert.hint()
            if h:
                log(h)
        except Exception:
            pass
        return manual_login(page)

    # ★ 인증서 줄을 먼저 «눌러서 고른» 다음 암호. 순서를 바꾸면 Enter 가 안 먹는다.
    for _ in range(30):
        cells = page.get_by_role("gridcell").filter(has_text=cert_name)
        if cells.count():
            if cells.count() > 1:
                log("  ⚠️ 설정의 인증서 이름이 든 인증서가 %d개입니다. 첫 번째를 고릅니다." % cells.count())
            robust_click(page, cells.first, "인증서")
            break
        page.wait_for_timeout(1000)
    else:
        raise SystemExit("인증서 목록에서 설정의 인증서 이름을 못 찾았습니다.\n"
                         "  이름을 확인하세요 (보안 설정창 → 인증서 이름)")
    page.wait_for_timeout(400)
    import secure_store as SS
    secret = None
    try:
        # ← 화면 주소(리디렉션 뒤)가 우리 지역 업무포털·인증 서버와 정확히 같을 때만 금고에서 «지금» 꺼낸다
        secret = SS.reveal_for(cert_pw, page.url, edufine_origins())
        pwd.fill(secret)
        pwd.press("Enter")
    except SS.SecureError as e:
        raise SystemExit("암호를 넣지 않았습니다 (%s%s) — 금고는 열지 않았거나 읽지 못했습니다"
                         % (e.code, " · 화면 주소 " + e.detail if e.code == "E_ORIGIN" else "")) from None
    except Exception:
        # ⚠️ playwright fill 오류 원문에는 넣으려던 값(=암호)이 들어간다 — 원문을 버린다
        raise SystemExit("암호 칸에 넣는 중 화면이 바뀌어 멈췄습니다 (오류 원문은 암호가 섞일 수 있어 찍지 않습니다)") from None
    finally:
        secret = None                      # 참조만 끊는다 (str 이라 메모리에서 지워진다는 보장은 없다)
    for _ in range(60):
        page.wait_for_timeout(1500)
        if "bpm_man_mn00_001" in page.url:
            log("✅ 로그인 완료")
            return
        box = page.locator("#nexacontainer.nexamodaloverlay")
        if box.count():
            raise SystemExit("로그인 중 알림: " + box.first.inner_text()[:200])
        try:                       # 로그인 직후엔 화면이 넘어가는 중이라 content() 가 예외를 낸다 (2026-10-06 실측)
            html = page.content()[:5000]
        except Exception:
            continue
        if "errorCode=1800" in page.url or "중복로그인" in html:
            raise SystemExit("「중복로그인」으로 끊겼습니다. 평소 쓰는 크롬에서 업무포털을 로그아웃하고 다시 실행해 주세요.")
    return manual_login(page)


# ── K-에듀파인(Nexacro) 화면 부품 ────────────────────────────────────
OVERLAY = "#nexacontainer.nexamodaloverlay"


def shot(page, name):
    """스크린샷을 이 폴더의 캡처/ 에 남긴다 (gitignore — 학교·사람 이름이 찍히므로 올리지 않는다)."""
    d = OUT_DIR
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    try:
        page.screenshot(path=str(p))
        log("  📸 캡처: %s" % p)
    except Exception:
        pass
    return p


def login_test():
    """`python edufine_common.py --login-test` — 업무포털 → K-에듀파인 첫 화면까지 «로그인만» 하고 창을 닫는다.

    목록·문서·화면 글자를 읽지 않고, 캡처도 남기지 않는다. 마지막 줄은 LOGIN_OK 또는 LOGIN_FAIL <코드>.
    암호는 login() 이 금고에서 «넣기 직전»에 꺼낸다. 자동 로그인이 막히면 손 로그인을 3분만 기다린다."""
    global MANUAL_WAIT_MIN
    MANUAL_WAIT_MIN = min(MANUAL_WAIT_MIN, 3)
    from playwright.sync_api import sync_playwright
    try:
        name, pw = creds()                       # 인증서 이름(찍지 않음) + 금고 표 — 암호 자체는 아직 안 꺼낸다
    except SystemExit:
        print("LOGIN_FAIL E_NO_CREDENTIALS", flush=True)
        return 1
    code = "E_EDUFINE_NOT_LOGGED_IN"
    with sync_playwright() as p:
        ctx, page = launch(p)
        try:
            login(page, name, pw)
            if "bpm_man_mn00_001" in page.url:
                code = "OK"
        except SystemExit as e:                  # 키트가 정해 둔 안내 글 (암호 원문은 이미 버린 것)
            log("  · " + str(e).splitlines()[0][:120])
            code = "E_EDUFINE_LOGIN"
        except Exception as e:
            code = "E_BROWSER:" + type(e).__name__
        finally:
            name = pw = None
            try:
                ctx.close()
            except Exception:
                pass
    print("LOGIN_OK" if code == "OK" else "LOGIN_FAIL " + code, flush=True)
    return 0 if code == "OK" else 1


if __name__ == "__main__":
    if "--login-test" in sys.argv[1:]:
        sys.exit(login_test())
    print("사용법: python edufine_common.py --login-test   (로그인만 시험하고 창을 닫는다)")

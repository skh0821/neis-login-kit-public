"""교사별 로컬 설정 저장 위치 및 로드/저장 유틸.

설정은 저장소 폴더가 아니라 %LOCALAPPDATA%\neis-automation 에 저장한다.
  - 저장소를 git pull 로 업데이트해도 내 설정이 지워지지 않는다
  - 개인정보(학년·반·인증서 이름·캘린더 ID)가 실수로 커밋될 일이 없다

인증서 비밀번호는 이 파일에 저장하지 않는다 — Windows 자격 증명 관리자(keyring)에만 들어간다.
"""
import json
import os
import sys
from pathlib import Path

# 윈도우 파워셸·클로드 앱 터미널은 기본 코드페이지가 cp949 라서, 한글·이모지를 찍으면
# UnicodeEncodeError 로 스크립트가 죽는다 (2026-09-16 샌드박스 리허설에서 --dry-run 이 이것으로 실패).
# 이 파일은 모든 실행 스크립트가 import 하므로 여기서 한 번만 출력 인코딩을 UTF-8 로 돌린다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass   # 파이프로 묶였거나 reconfigure 가 없는 경우 — 그냥 넘어간다

# 브라우저 자동화의 진단(디버그) 기록 스위치를 끈다 — 켜져 있으면 입력값이 기록에 섞일 수 있다 (2026-11-09 점검 반영).
# 모든 실행 스크립트가 이 파일을 먼저 import 하므로, 나이스·에듀파인·EVPN 로그인 프로세스와 그 하위 프로그램에 다 적용된다.
for _k in ("DEBUG", "DEBUG_FILE", "DEBUG_DEPTH", "DEBUG_COLORS", "PWDEBUG", "PW_DEBUG"):
    os.environ.pop(_k, None)

# 브라우저 자동화(Playwright)의 Node 가 «Windows 인증서 저장소»를 쓰게 한다 — 크롬과 같은 기준으로 HTTPS 를 검증한다.
# 서울 EVPN 포털은 인증서 중간 고리를 빼고 보내서, Node 기본 목록만으로는 route.fetch 가
# «unable to verify the first certificate» 로 실패하고 포털이 멈췄다 (2026-11-10 바탕화면 글자 위젯 실측).
# 클로드 앱 안에서는 앱이 이 값을 켜 두어서 그동안 드러나지 않았다 — 앱 밖(위젯·바로가기)도 같은 조건으로 맞춘다.
# 검증을 끄는 게 아니다 (ignore_https_errors 는 그대로 False).
os.environ.setdefault("NODE_USE_SYSTEM_CA", "1")

CONFIG_DIR   = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "neis-automation"
CONFIG_FILE  = CONFIG_DIR / "teacher_config.json"
TOKEN_FILE   = CONFIG_DIR / "token.json"          # 예전 평문 구글 토큰 — 이제는 읽기만 하고(이전 전), 새로 쓰지 않는다
# 금고(Windows 자격 증명 관리자) 이름 — 사람마다 무작위 (2026-11-26).
# 예전엔 모두 "neis-automation" 이라, 공개된 코드를 읽은 프로그램이 이름을 그대로 대고 꺼낼 수 있었다.
# 이제 처음 쓸 때 "nlk-" + 무작위 12글자를 만들어 «암호화된 개인 설정»(이 Windows 사용자만 풂)에 넣는다.
# 다른 파일은 지금처럼 teacher_config.KEYRING_SERVICE 로 읽는다 — 맨 아래 __getattr__ 가 이 함수를 부른다.
VAULT_LEGACY = "neis-automation"
_VAULT_RE = __import__("re").compile(r"nlk-[0-9a-f]{12}")

# ── 보호 저장 (2026-11-09) ─────────────────────────────────────────────
# 개인 설정(인증서 이름·연락처·EVPN 아이디·학교 이름·캘린더 ID)과 구글 토큰은
# secure\ 폴더(이 Windows 사용자 + SYSTEM 만 열 수 있음)에 DPAPI 로 암호화해 둔다.
# 평문 teacher_config.json 에 "_secure" 표시가 있으면 «나눠 저장» 모드다. 표시가 없으면 예전처럼 평문 한 벌.
SECURE_DIR = CONFIG_DIR / "secure"
PRIVATE_FILE = SECURE_DIR / "teacher_private.dpapi"
PRIVATE_PURPOSE = "neis-automation/teacher_private"
GOOGLE_TOKEN_DPAPI = SECURE_DIR / "google_token.dpapi"
GOOGLE_PURPOSE = "neis-automation/google_token"
PRIVATE_KEYS = ("cert_name", "contact", "evpn_user", "school_name", "calendars", "vault_service")
SECURE_MARKER = "_secure"

REQUIRED_KEYS = ("cert_name",)   # 로그인 전용 키트 — 출결용 학년·반·캘린더는 쓰지 않는다

# 설정에 없으면 이 값을 쓴다 (모두 개인정보가 아닌 일반 기본값)
DEFAULTS = {
    # 시도교육청별 나이스 주소. 본인 지역 주소는 브라우저에서 나이스에 접속해
    # 주소창을 그대로 복사하면 된다 (설정 마법사가 물어본다).
    "neis_url": "https://sen.neis.go.kr/jsp/main.jsp",
    # 휴업일(재량휴업일 등)을 읽어올 구글 시트. 비워두면 이 기능만 건너뛴다.
    "holiday_sheet_id": "",
    "holiday_sheet_range": "출결!K2:K50",
    # 우리 학교 (설정 마법사 5번) — 나이스 학사일정에서 휴업일을 키 없이 찾는다 (school_calendar.py)
    "school_office": "", "school_code": "", "school_name": "",
    # 복무 신청 시 쓰는 값
    "contact": "",                 # 예: "010-1234-5678" — 근무상황신청 연락처
    "approval_line": "",           # 나이스에 미리 저장해 둔 개인결재선 프리셋 이름
    "last_period": 7,              # 하루 최대 교시 수
}


def mask_name(v: str) -> str:
    """인증서 이름·사람 이름을 화면에 찍을 때 — 글자를 하나도 남기지 않는다 (`홍길동` → `***`).
    클로드가 돌리는 명령의 출력은 대화 기록으로 나간다 (첫 글자도 성씨라 남기지 않는다)."""
    v = str(v or "")
    return "*" * len(v) if v else ""


class ConfigMissingError(Exception):
    pass


class PrivateConfigError(ConfigMissingError):
    """암호화된 개인 설정을 못 풀었다. code 는 secure_store 오류 코드 (값·이름은 들어 있지 않다)."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(
            f"보호 저장된 개인 설정을 읽지 못했습니다 ({code}).\n"
            "   다른 Windows 계정에서 만든 파일이거나 손상됐을 수 있습니다.\n"
            "   → 보안 설정창에서 인증서 이름·연락처를 다시 넣거나, 백업 복구: python secure_migrate.py restore")


def _read_plain() -> dict:
    if not CONFIG_FILE.exists():
        raise ConfigMissingError(
            f"설정 파일이 없습니다: {CONFIG_FILE}\n"
            "먼저 설정 창을 여세요:  python secure_settings.py"
        )
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ConfigMissingError(f"설정 파일이 깨졌습니다 (E_FILE_CORRUPT): {CONFIG_FILE}\n"
                                 "   보안 설정창을 열어 다시 저장하거나: python secure_migrate.py restore") from None
    if not isinstance(data, dict):
        raise ConfigMissingError(f"설정 파일 형식이 잘못됐습니다 (E_FILE_CORRUPT): {CONFIG_FILE}")
    return data


def is_split_mode(plain: dict | None = None) -> bool:
    if plain is None:
        try:
            plain = _read_plain()
        except ConfigMissingError:
            return False
    return isinstance(plain.get(SECURE_MARKER), dict)


def load_private() -> dict:
    """암호화된 개인 설정만 (없으면 {}). 못 풀면 PrivateConfigError."""
    import secure_store
    if not (PRIVATE_FILE.exists() or secure_store.prev_path(PRIVATE_FILE).exists()):
        return {}
    try:
        data = secure_store.read_json(PRIVATE_FILE, PRIVATE_PURPOSE)
    except secure_store.SecureError as e:
        raise PrivateConfigError(e.code) from None
    return data if isinstance(data, dict) else {}


def load_merged() -> dict:
    """평문 설정 + 암호화된 개인 설정을 합친 «예전과 같은 모양»의 dict (필수 항목 검사 없음).

    평문에 같은 키가 있으면 평문이 이긴다 — 호환 모드로 평문에 남겨 둔 값(cert_name)이거나,
    보호 저장을 모르는 옛 코드가 방금 새로 쓴 값이다. 다음 save_config() 때 암호화 쪽으로 옮겨진다.
    """
    plain = _read_plain()
    if not is_split_mode(plain) and not PRIVATE_FILE.exists():
        return plain
    priv = load_private()
    return {**priv, **plain}


def load_config() -> dict:
    data = load_merged()
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise ConfigMissingError(
            f"설정 파일에 누락된 항목: {missing}\n설정 창에서 다시 저장하세요:  python secure_settings.py"
        )
    return {**DEFAULTS, **data}


def load_config_or_empty() -> dict:
    """위젯·EVPN 처럼 «출결 필수 항목이 없어도 되는» 곳용. 파일이 없거나 깨졌으면 {}."""
    try:
        return load_merged()
    except ConfigMissingError:
        return {}


def _write_plain(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".json.tmp")       # 저장 중에 꺼져도 원래 파일이 반쯤 써진 채 남지 않게
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, CONFIG_FILE)


def split_for_save(data: dict, marker: dict) -> tuple[dict, dict]:
    """(평문에 둘 것, 암호화할 것). marker['plain_compat'] 에 든 키는 양쪽에 둔다 (예전 EVPN 도우미 호환)."""
    compat = set(marker.get("plain_compat") or [])
    priv = {k: data[k] for k in PRIVATE_KEYS if k in data}
    plain = {k: v for k, v in data.items() if k not in PRIVATE_KEYS or k in compat}
    plain[SECURE_MARKER] = marker
    return plain, priv


def save_config(data: dict) -> None:
    """예전과 같은 «통째로 저장». 보호 모드(_secure 표시)면 개인 설정은 암호화 파일로 나눠 쓴다.

    순서: 암호화 파일 먼저(검증 포함) → 평문. 중간에 꺼져도 평문은 예전 판 그대로라 다음 저장으로 맞춰진다.
    """
    data = dict(data)
    marker = data.get(SECURE_MARKER)
    if not isinstance(marker, dict):
        try:
            marker = _read_plain().get(SECURE_MARKER)
        except ConfigMissingError:
            marker = None
    if not isinstance(marker, dict):
        data.pop(SECURE_MARKER, None)
        _write_plain(data)
        return
    import secure_store
    plain, priv = split_for_save(data, marker)
    if not SECURE_DIR.exists():
        secure_store.prepare_secure_dir(SECURE_DIR)
    secure_store.write_json(PRIVATE_FILE, priv, PRIVATE_PURPOSE)
    _write_plain(plain)


# ── 구글 토큰 (DPAPI) ─────────────────────────────────────────────────
class GoogleNotConnected(Exception):
    code = "E_GOOGLE_NOT_CONNECTED"


class GoogleRefreshFailed(Exception):
    """토큰 갱신 실패 — 저장된 토큰은 지우지 않는다 (네트워크 문제일 수도 있다)."""

    def __init__(self, kind: str):
        self.code = "E_GOOGLE_REFRESH"
        self.kind = kind
        super().__init__(f"구글 연결을 새로 고치지 못했습니다 (E_GOOGLE_REFRESH · {kind}).\n"
                         "   인터넷을 확인하고, 계속 안 되면 «출결 연결해줘» 로 구글 로그인을 다시 하세요.")


def google_token_state() -> str:
    """'protected' / 'plain' / 'none' — 값은 읽지 않는다."""
    if GOOGLE_TOKEN_DPAPI.exists():
        return "protected"
    return "plain" if TOKEN_FILE.exists() else "none"


def load_google_credentials():
    """구글 자격 증명 객체. 암호화 토큰 우선, 아직 이전 안 한 PC 는 평문 token.json 을 읽는다."""
    from google.oauth2.credentials import Credentials
    import secure_store
    if GOOGLE_TOKEN_DPAPI.exists() or secure_store.prev_path(GOOGLE_TOKEN_DPAPI).exists():
        try:
            info = secure_store.read_json(GOOGLE_TOKEN_DPAPI, GOOGLE_PURPOSE)
        except secure_store.SecureError as e:
            raise GoogleNotConnected(f"구글 토큰을 풀지 못했습니다 ({e.code}) — «출결 연결해줘» 로 다시 연결하세요") from None
        return Credentials.from_authorized_user_info(info)
    if TOKEN_FILE.exists():
        return Credentials.from_authorized_user_file(str(TOKEN_FILE))
    raise GoogleNotConnected("구글 계정이 연동되지 않았습니다.")


def save_google_credentials(creds) -> None:
    """암호화해 저장한다. 암호화본이 검증되면 남아 있던 평문 token.json 은 지운다 (같은 토큰의 낡은 사본)."""
    import secure_store
    if not SECURE_DIR.exists():
        secure_store.prepare_secure_dir(SECURE_DIR)
    info = json.loads(creds.to_json())
    secure_store.write_json(GOOGLE_TOKEN_DPAPI, info, GOOGLE_PURPOSE)
    if secure_store.read_json(GOOGLE_TOKEN_DPAPI, GOOGLE_PURPOSE).get("refresh_token") == info.get("refresh_token") \
            and TOKEN_FILE.exists():
        try:
            TOKEN_FILE.unlink()
        except OSError:
            pass


def google_credentials(refresh: bool = True):
    """만료됐으면 갱신해서 돌려준다. 갱신 실패는 GoogleRefreshFailed (토큰은 그대로 둔다)."""
    creds = load_google_credentials()
    if refresh and creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request
        try:
            creds.refresh(Request())
        except Exception as e:
            raise GoogleRefreshFailed(type(e).__name__) from None
        save_google_credentials(creds)
    return creds


def secret_input(prompt: str) -> str:
    """비밀번호를 화면에 안 보이게 받는다.

    ★ getpass 를 그대로 쓰면 안 되는 자리가 있다 — 설정 마법사처럼 sys.stdin 을 CONIN$ 로
    갈아 끼운 새 창에서는 getpass 가 «Can not control echo on the terminal» 경고를 내고
    친 글자를 그대로 화면에 찍는다 (2026-10-01 샌드박스 실측). 윈도우에서는 키보드를 직접 읽는다.
    """
    if os.name != "nt":
        from getpass import getpass
        return getpass(prompt)
    import msvcrt
    sys.stdout.write(prompt)
    sys.stdout.flush()
    chars = []
    while True:
        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):
            break
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch == "\x08":
            if chars:
                chars.pop()
            continue
        if ch in ("\x00", "\xe0"):   # 화살표·F키 같은 특수키는 두 글자로 온다 — 버린다
            msvcrt.getwch()
            continue
        chars.append(ch)
    sys.stdout.write("\n")
    sys.stdout.flush()
    return "".join(chars)


def prepare_console_window() -> None:
    """새로 띄운 검은 창을 «바로 칠 수 있는 상태»로 만든다 (윈도우만).

    ① 빠른 편집(QuickEdit) 끄기 — 켜져 있으면 창을 마우스로 한 번 누르는 순간 «선택 모드»가 되고,
       그다음 Enter 는 답이 아니라 «선택 복사»로 먹힌다. 선생님 눈에는 «Enter 가 안 먹는다»로 보인다
       (2026-10-01 샌드박스 실측: 창을 누른 뒤 첫 Enter 가 사라졌다).
    ② 창을 맨 앞으로 — 클로드 앱 뒤에 숨어 뜨면 선생님이 못 찾는다.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        import msvcrt
        k32 = ctypes.windll.kernel32
        h = msvcrt.get_osfhandle(sys.stdin.fileno())
        mode = ctypes.c_uint()
        if k32.GetConsoleMode(h, ctypes.byref(mode)):
            ENABLE_QUICK_EDIT_MODE, ENABLE_EXTENDED_FLAGS = 0x0040, 0x0080
            k32.SetConsoleMode(h, (mode.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS)
        hwnd = k32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def neis_origins(neis_url: str | None = None) -> list:
    """인증서 암호를 넣어도 되는 나이스 주소 (https · 기본 포트 · 정확한 호스트). 설정의 주소가 «○○○.neis.go.kr» 꼴이 아니면 빈 목록."""
    import re
    from urllib.parse import urlsplit
    u = urlsplit(neis_url if neis_url is not None else (load_config_or_empty().get("neis_url") or DEFAULTS["neis_url"]))
    host = (u.hostname or "").lower()
    if u.scheme == "https" and re.fullmatch(r"[a-z]{3}\.neis\.go\.kr", host) and u.port in (None, 443):
        return [f"https://{host}"]
    return []


def no_password_saving(profile) -> None:
    """자동화 크롬의 «비밀번호를 저장하시겠습니까?» 를 끈다 — 크롬을 띄우기 «직전»에 부른다 (크롬이 꺼져 있을 때만 남는다).

    자동화가 인증서 암호를 웹 화면 칸에 넣으면 크롬이 저장을 권한다. 누가 [저장] 을 누르면 크롬 비밀번호 파일
    (Login Data)에 사본이 생긴다 → 2026-11-09 나이스 자동화 프로필에서 1개가 발견돼 지웠다. 위젯·EVPN 프로필은 원래 껐다.
    이 함수는 크롬 설정 파일의 «두 값»만 바꾼다 (다른 값은 건드리지 않는다). 실패해도 자동화는 멈추지 않는다."""
    pref = Path(profile) / "Default" / "Preferences"
    try:
        d = json.loads(pref.read_text(encoding="utf-8")) if pref.exists() else {}
        if d.get("credentials_enable_service") is False and (d.get("profile") or {}).get("password_manager_enabled") is False:
            return
        d["credentials_enable_service"] = False
        d.setdefault("profile", {})["password_manager_enabled"] = False
        pref.parent.mkdir(parents=True, exist_ok=True)
        tmp = pref.with_name(pref.name + ".tmp-neis")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, pref)
    except Exception:
        pass


def window_args() -> list:
    """크롬 창 띄우는 옵션. 환경변수 NEIS_HIDE=1 이면 화면 밖에 띄운다(«작게»).

    시트 [품의 올리기]·[복무 올리기] 의 «화면: 작게» 가 이걸 켠다 (edufine_punui_link.py).
    최소화가 아니라 화면 밖 창이다 — 최소화한 크롬은 그리기를 멈춰 클릭·읽기가 멈출 수 있다.
    창 크기는 최대화와 비슷하게 1920x1080 으로 둔다(넥사크로 화면 배치가 바뀌지 않게).
    """
    if os.environ.get("NEIS_HIDE") == "1":
        return ["--window-position=-2400,-2400", "--window-size=1920,1080"]
    return ["--start-maximized"]


def vault_service(create: bool = True):
    """이 사람만의 금고 이름. 없으면(create=True) 만들어 암호화된 개인 설정에 넣고 돌려준다. create=False 면 없을 때 None."""
    cfg = load_config_or_empty()
    v = str(cfg.get("vault_service") or "")
    if _VAULT_RE.fullmatch(v):
        return v
    if not create:
        return None
    import secrets
    import time as _time
    if CONFIG_FILE.exists() and not is_split_mode():
        import contextlib
        import io
        import secure_migrate                     # 예전 평문 설정이면 먼저 «나눠 저장»(암호화)으로 옮긴다
        with contextlib.redirect_stdout(io.StringIO()):
            if secure_migrate.run() != 0:
                raise RuntimeError("설정을 암호화 저장으로 옮기지 못해 금고 이름을 만들지 않았습니다 (E_VAULT_NAME_MIGRATE)")
        cfg = load_config_or_empty()
    data = dict(cfg)
    if not isinstance(data.get(SECURE_MARKER), dict) and not is_split_mode():
        # 처음 설정 — 평문 파일에 금고 이름이 한 번도 적히지 않게, 처음부터 «나눠 저장» 모드로 시작한다
        data[SECURE_MARKER] = {"version": 1, "private_file": "secure/" + PRIVATE_FILE.name, "plain_compat": [],
                               "created": _time.strftime("%Y-%m-%d %H:%M:%S")}
    v = "nlk-" + secrets.token_hex(6)
    data["vault_service"] = v
    save_config(data)
    if load_config_or_empty().get("vault_service") != v or "vault_service" in _read_plain():
        raise RuntimeError("금고 이름을 암호화 설정에 저장하지 못했습니다 (E_VAULT_NAME_SAVE)")
    return v


def __getattr__(name):
    if name == "KEYRING_SERVICE":
        return vault_service()
    raise AttributeError(f"module 'teacher_config' has no attribute {name!r}")

# -*- coding: utf-8 -*-
"""리로스쿨 상벌점 — 일반 설정(비밀 아님)과 인증정보 자리.

  학교 주소   키트 폴더 우리학교_설정.json (gitignore)   {"riro_host": "<학교>.riroschool.kr", "divot_url": "https://..."}
              — 학교마다 다르므로 코드에 적지 않는다 (2026-11-10 배포용으로 뺐다). 시험·임시로는 환경변수
              RIRO_SCHOOL_HOST · RIRO_DIVOT_URL 이 먼저다
  일반 설정   %LOCALAPPDATA%\\RiroMeritAutomation\\config.json   주소·시트 ID·학교 규칙(확인 여부) 등
  금고        Windows 자격 증명 관리자, 서비스 이름 «riro-merit/<학교>.riroschool.kr» — 아이디·비밀번호 한 벌
  보호 파일   %LOCALAPPDATA%\\RiroMeritAutomation\\secure\\    (현재 사용자 + SYSTEM 만) 구글 시트 토큰(DPAPI)
  브라우저    %LOCALAPPDATA%\\RiroMeritAutomation\\browser\\riro · divot   전용 크롬 프로필 (브라우저 자체 저장 방식)

다른 사이트(나이스 인증서·EVPN·e알리미)의 암호를 여기서 꺼내 쓰지 않는다.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
if str(KIT) not in sys.path:
    sys.path.insert(0, str(KIT))

import secure_store as S  # noqa: E402

SCHOOL_FILE = KIT / "우리학교_설정.json"      # gitignore — 선생님 PC 에만 있다 (비밀 아님: 주소 두 개뿐)
NO_SCHOOL = ("리로스쿨 학교 주소가 아직 없습니다 — 키트 폴더 «우리학교_설정.json» 에 \"riro_host\" 를 적어 주세요 "
             "(클로드에게 «우리 학교 리로스쿨 주소는 ○○○.riroschool.kr» 이라고 말하면 적어 줍니다)")
NO_DIVOT = ("해커톤 앱 주소가 아직 없습니다 — 보안 설정창 리로스쿨 칸이나 «우리학교_설정.json» 의 \"divot_url\" 에 "
            "적어 주세요")
_HOST_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.riroschool\.kr")


def riro_host(value) -> str:
    """«myschool» · «myschool.riroschool.kr» · «https://myschool.riroschool.kr/...» → «myschool.riroschool.kr».
    리로스쿨 학교 주소 모양(<이름>.riroschool.kr)이 아니면 "" — 아이디·비밀번호를 넣는 곳이라 다른 사이트는 받지 않는다."""
    v = str(value or "").strip().lower()
    v = re.sub(r"^https://", "", v).split("/", 1)[0]
    if v and "." not in v:
        v += ".riroschool.kr"
    return v if _HOST_RE.fullmatch(v) else ""


def https_url(value) -> str:
    """https 로 시작하는 주소만 (해커톤 앱). 아니면 ""."""
    v = str(value or "").strip()
    return v if re.match(r"https://[^\s/]+", v) else ""


def school_settings() -> dict:
    """우리학교_설정.json — 없거나 깨졌으면 {} (그러면 리로스쿨 기능이 «주소 없음» 으로 멈춘다)."""
    try:
        d = json.loads(SCHOOL_FILE.read_text(encoding="utf-8-sig"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


_SCHOOL = school_settings()
SCHOOL_HOST = riro_host(os.environ.get("RIRO_SCHOOL_HOST") or _SCHOOL.get("riro_host"))
def riro_service(create: bool = True) -> str:
    """리로스쿨 금고 이름 — «이 사람 금고 이름-riro/학교 주소» (2026-11-26, 예전엔 모두 "riro-merit/<학교>").
    학교 주소가 없거나 (create=False 인데) 금고 이름이 아직 없으면 ""."""
    if "RIRO_SERVICE" in globals():          # 시험이 바꿔 끼운 이름 (mock.patch.object(config, "RIRO_SERVICE", …))
        return globals()["RIRO_SERVICE"]
    if not SCHOOL_HOST:
        return ""
    import teacher_config
    base = teacher_config.vault_service(create)
    return f"{base}-riro/{SCHOOL_HOST}" if base else ""


def __getattr__(name):
    if name == "RIRO_SERVICE":
        return riro_service()
    raise AttributeError(f"module 'riro_merit.config' has no attribute {name!r}")
RIRO_SIGNIN = f"https://{SCHOOL_HOST}/user.php?action=signin" if SCHOOL_HOST else ""
DIVOT_URL = https_url(os.environ.get("RIRO_DIVOT_URL") or _SCHOOL.get("divot_url"))


def require_school() -> None:
    """리로스쿨에 들어가기 전에 부른다 — 주소가 없으면 알아들을 수 있는 말로 멈춘다."""
    if not SCHOOL_HOST:
        raise RuntimeError(NO_SCHOOL)


def base_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "RiroMeritAutomation"


def config_file() -> Path:
    return base_dir() / "config.json"


def secure_dir() -> Path:
    return base_dir() / "secure"


def browser_dir(name: str) -> Path:
    return base_dir() / "browser" / name


DEFAULTS = {
    "version": 1,
    "riro_signin_url": RIRO_SIGNIN,
    "divot_url": DIVOT_URL,
    "sheet_id": "",                      # 학교에서 허용한 구글 시트 (선생님이 설정창에 넣는다)
    "sheet_tab": "상벌점_요청",
    # 학교별 규칙 — 선생님 확인 전에는 confirmed=False 이고, 등록 단계는 이 값이 True 일 때만 돈다
    "rules": {"confirmed": False, "item": "", "points": None, "cutoff_time": "", "exclusions": [],
              "note": "벌점 항목·점수·미보관 확정 시각·제외 조건은 선생님께 확인받아 채운다"},
    "cred_rev": 0,                       # 아이디·비밀번호를 새로 저장할 때마다 +1 (로그인 실패 잠금 풀기용)
    "login_lock": {},                    # {"rev": n, "code": "E_BAD_CREDENTIALS", "at": "..."} — 같은 rev 로는 다시 안 시도
}


def load() -> dict:
    try:
        d = json.loads(config_file().read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            raise ValueError
    except FileNotFoundError:
        d = {}
    except (OSError, ValueError):
        raise S.SecureError("E_FILE_CORRUPT") from None
    out = {**DEFAULTS, **d}
    out["rules"] = {**DEFAULTS["rules"], **(d.get("rules") or {})}
    return out


def save(cfg: dict) -> None:
    p = config_file()
    S.assert_safe_location(p.parent)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(cfg, ensure_ascii=False, indent=1))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def ensure_dirs() -> None:
    S.prepare_secure_dir(secure_dir())
    for n in ("riro", "divot"):
        browser_dir(n).mkdir(parents=True, exist_ok=True)


# ── 금고 (리로스쿨 전용) ─────────────────────────────────────────────
def credential_ticket():
    """(아이디, VaultSecret) — 비밀번호 자체는 들고 있지 않다. 로그인 직전에 ticket.reveal() 로 꺼낸다."""
    if not riro_service():                      # 학교 주소가 없으면 금고 이름도 없다
        return (None, None)
    u = S.vault_username(riro_service())
    return (u, S.VaultSecret(riro_service(), u)) if u else (None, None)


def has_credentials() -> dict:
    """있다/없다만 — 비밀번호 내용을 꺼내지 않는다."""
    if not riro_service():
        return {"id": False, "password": False}
    u = S.vault_username(riro_service())
    return {"id": bool(u), "password": bool(u and S.vault_exists(riro_service(), u))}


def set_credentials(uid: str, pw: str) -> None:
    """금고에 저장하고 cred_rev 를 올린다 (로그인 실패 잠금이 풀린다).
    ⚠️ str 로 받는 길 — 가상 값 시험용. 실제 입력은 secure_password.py --target riro (버퍼 → 금고)."""
    if not riro_service():
        raise S.SecureError("E_NO_SCHOOL")
    if not uid or not pw:
        raise S.SecureError("E_VAULT_IO", "empty")
    S.vault_replace_credential(riro_service(), uid.strip(), pw)
    cfg = load()
    cfg["cred_rev"] = int(cfg.get("cred_rev") or 0) + 1
    cfg["login_lock"] = {}
    save(cfg)


def delete_credentials() -> bool:
    ok = S.vault_delete(riro_service()) if riro_service() else False
    cfg = load()
    cfg["login_lock"] = {}
    save(cfg)
    return ok

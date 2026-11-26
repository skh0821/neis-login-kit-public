# -*- coding: utf-8 -*-
"""인증정보 보호 공용 부품 — Windows 자격 증명 관리자(금고) · DPAPI 암호화 · 폴더 권한 · 위치 점검.

나이스·에듀파인 키트와 리로스쿨 상벌점(riro_merit)이 함께 쓴다.

원칙
  · 비밀번호는 Windows 자격 증명 관리자에만 둔다. 금고를 못 쓰면 «평문 저장으로 대신하지 않고» 멈춘다.
  · 토큰·쿠키·개인 설정은 DPAPI(현재 Windows 사용자 범위)로 암호화한 파일에 둔다.
    복호화 열쇠는 윈도우가 사용자 계정에 묶어 관리한다 — 코드나 옆 파일에 열쇠를 두지 않는다.
    (entropy 값은 «용도 구분표»일 뿐 비밀이 아니다. 다른 용도 파일과 섞여 읽히지 않게만 한다)
  · 이 모듈의 함수는 값 자체를 화면에 찍지 않는다. 오류는 개인정보 없는 코드(E_…)로 낸다.

같은 Windows 사용자 권한으로 도는 프로그램은 금고·DPAPI 를 똑같이 열 수 있다. 그러니 «AI 가 돌리는
명령의 출력»에 값이 섞이지 않게 하는 것이 이 파일 밖 코드의 책임이다 (show_*·check_* 는 있다/없다만 돌려준다).
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import secrets
import sys
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

ENVELOPE_FORMAT = "dpapi-envelope/1"
WINVAULT = "keyring.backends.Windows.WinVaultKeyring"

# 개인정보 없는 오류 코드 → 선생님께 보일 말
ERRORS = {
    "E_NOT_WINDOWS": "Windows 가 아닙니다 — 이 보호 방식은 Windows 전용입니다",
    "E_VAULT_BACKEND": "Windows 자격 증명 관리자를 쓸 수 없습니다 (다른 저장 방식이 잡혀 있음) — 평문으로 저장하지 않고 멈췄습니다",
    "E_VAULT_IO": "Windows 자격 증명 관리자에 쓰거나 읽지 못했습니다",
    "E_VAULT_VERIFY": "금고에 저장했는데 다시 읽어 보니 맞지 않습니다",
    "E_DPAPI_PROTECT": "윈도우 암호화(DPAPI)에 실패했습니다",
    "E_DPAPI_DECRYPT": "암호화 파일을 풀지 못했습니다 (다른 Windows 계정·PC 에서 만든 파일이거나 손상됨)",
    "E_FILE_MISSING": "파일이 없습니다",
    "E_FILE_CORRUPT": "파일 형식이 깨졌습니다",
    "E_PURPOSE_MISMATCH": "다른 용도의 암호화 파일입니다",
    "E_WRITE": "파일을 저장하지 못했습니다",
    "E_ACL": "폴더 권한을 정하지 못했습니다",
    "E_LOCATION_GIT": "깃 작업 폴더 안이라 인증정보를 둘 수 없습니다",
    "E_LOCATION_CLOUD": "클라우드 동기화 폴더 안이라 인증정보를 둘 수 없습니다",
    "E_LOCATION_REMOTE": "네트워크 드라이브라 인증정보를 둘 수 없습니다",
    "E_EMPTY": "비밀번호가 비어 있습니다",
    "E_NO_ACCOUNT": "어느 계정의 비밀번호인지 정해지지 않았습니다 (인증서 이름·아이디)",
    "E_VAULT_MISSING": "금고에 저장된 비밀번호가 없습니다",
    "E_ORIGIN": "비밀번호를 넣으려는 화면이 허용된 주소가 아닙니다 — 금고를 열지 않고 멈췄습니다",
    "E_NO_SCHOOL": "리로스쿨 학교 주소가 아직 없습니다 (키트 폴더 우리학교_설정.json 의 riro_host)",
}


class SecureError(Exception):
    """code 만 들고 다닌다. 메시지에 값·경로 속 개인정보를 넣지 않는다."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail          # 예외 «종류» 이름 정도만 (값 금지)
        super().__init__(f"{code}: {ERRORS.get(code, '')}" + (f" ({detail})" if detail else ""))


def _require_windows():
    if os.name != "nt":
        raise SecureError("E_NOT_WINDOWS")


# ═════════════════════════════════════════════════════════════ 실계정 로그인 프로세스 다잡기 (2026-11-09 점검 반영)
# 브라우저 자동화 도구의 진단(디버그) 기록을 켜는 환경변수 — 켜져 있으면 입력값이 기록에 섞일 수 있다.
# 이 키트의 프로그램은 시작할 때 이것들을 지운다 (teacher_config·secure_store 를 import 하는 순간).
DEBUG_ENV_KEYS = ("DEBUG", "DEBUG_FILE", "DEBUG_DEPTH", "DEBUG_COLORS", "PWDEBUG", "PW_DEBUG")


def harden_process_env() -> list:
    """진단 기록 환경변수를 이 프로세스(와 이 프로세스가 띄우는 하위 프로그램)에서 지운다. 지운 «이름»만 돌려준다."""
    gone = [k for k in DEBUG_ENV_KEYS if k in os.environ]
    for k in gone:
        os.environ.pop(k, None)
    return gone


harden_process_env()


# ═════════════════════════════════════════════════════════════ 접속처 확인 — 비밀번호를 넣기 «직전»
def _origin(url: str):
    from urllib.parse import urlsplit
    u = urlsplit(url or "")
    if u.username or u.password:                       # https://진짜주소@다른곳/ 같은 속임수
        return None
    host = (u.hostname or "").lower().rstrip(".")
    port = u.port or {"https": 443, "http": 80}.get(u.scheme)
    return (u.scheme.lower(), host, port)


def origin_text(url: str) -> str:
    o = _origin(url)
    return f"{o[0]}://{o[1]}:{o[2]}" if o else "(알 수 없음)"


def check_origin(url: str, allowed) -> None:
    """지금 화면 주소(리디렉션이 끝난 뒤)가 허용 목록의 «https·호스트·포트» 와 정확히 같아야 한다. 아니면 E_ORIGIN.

    allowed: 'https://host[:port]' 목록. 하위 도메인·비슷한 이름은 통과시키지 않는다 (정확히 같은 호스트만)."""
    got = _origin(url)
    want = {_origin(a) for a in allowed}
    if not got or got[0] != "https" or got not in want:
        raise SecureError("E_ORIGIN", origin_text(url))


def reveal_for(ticket, page_url: str, allowed) -> str:
    """접속처를 확인한 «다음에만» 금고를 연다. 확인에 실패하면 금고는 열리지 않는다."""
    check_origin(page_url, allowed)
    return reveal(ticket)


# ═════════════════════════════════════════════════════════════ DPAPI
class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes):
    buf = ctypes.create_string_buffer(data, len(data))
    return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _entropy(purpose: str) -> bytes:
    return ("neis-secure/" + purpose).encode("utf-8")


CRYPTPROTECT_UI_FORBIDDEN = 0x1
CRED_PERSIST_LOCAL_MACHINE = 2


def dpapi_protect(data: bytes, purpose: str) -> bytes:
    """현재 Windows 사용자만 풀 수 있게 암호화한다 (LOCAL_MACHINE 플래그를 쓰지 않는다)."""
    _require_windows()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    din, _k1 = _blob(data)
    ent, _k2 = _blob(_entropy(purpose))
    out = _BLOB()
    ok = crypt32.CryptProtectData(ctypes.byref(din), ctypes.c_wchar_p(purpose), ctypes.byref(ent),
                                  None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        raise SecureError("E_DPAPI_PROTECT", f"win32:{ctypes.GetLastError()}")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def dpapi_unprotect(blob: bytes, purpose: str) -> bytes:
    _require_windows()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    din, _k1 = _blob(blob)
    ent, _k2 = _blob(_entropy(purpose))
    out = _BLOB()
    desc = ctypes.c_wchar_p()
    ok = crypt32.CryptUnprotectData(ctypes.byref(din), ctypes.byref(desc), ctypes.byref(ent),
                                    None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        raise SecureError("E_DPAPI_DECRYPT", f"win32:{ctypes.GetLastError()}")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)
        if desc:
            kernel32.LocalFree(desc)


# ═════════════════════════════════════════════════════════════ 암호화 파일 (봉투)
def _envelope(blob: bytes, purpose: str) -> str:
    return json.dumps({"format": ENVELOPE_FORMAT, "purpose": purpose, "scope": "CurrentUser",
                       "created": datetime.now().isoformat(timespec="seconds"),
                       "blob": base64.b64encode(blob).decode("ascii")}, ensure_ascii=False)


def _parse_envelope(text: str, purpose: str) -> bytes:
    try:
        env = json.loads(text)
        if env.get("format") != ENVELOPE_FORMAT or not isinstance(env.get("blob"), str):
            raise ValueError
        blob = base64.b64decode(env["blob"], validate=True)
    except (ValueError, TypeError):
        raise SecureError("E_FILE_CORRUPT") from None
    if env.get("purpose") != purpose:
        raise SecureError("E_PURPOSE_MISMATCH")
    return blob


def _fsync_write(path: Path, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def prev_path(path: Path) -> Path:
    return path.with_name(path.name + ".prev")


def write_protected(path: Path, data: bytes, purpose: str, keep_prev: bool = True) -> None:
    """암호화해서 «한 번에» 바꿔 쓴다.

    순서: 임시 파일에 쓰기 → 임시 파일을 다시 풀어 원문과 같은지 확인 → (있으면) 지금 파일을 .prev 로 →
    임시 파일을 제자리로. 어느 단계에서 꺼져도 «원래 파일» 이나 «.prev» 중 하나는 온전하다.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _envelope(dpapi_protect(data, purpose), purpose).encode("utf-8")
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{secrets.token_hex(3)}")
    try:
        _fsync_write(tmp, text)
        if dpapi_unprotect(_parse_envelope(tmp.read_text(encoding="utf-8"), purpose), purpose) != data:
            raise SecureError("E_WRITE", "verify")
        if keep_prev and path.exists():
            os.replace(path, prev_path(path))
        os.replace(tmp, path)
    except SecureError:
        raise
    except OSError as e:
        raise SecureError("E_WRITE", type(e).__name__) from None
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def read_protected(path: Path, purpose: str, allow_prev: bool = True) -> tuple[bytes, str]:
    """(원문, 출처) — 출처는 'main' 또는 'prev'(본 파일이 깨져 이전 판을 썼을 때)."""
    path = Path(path)
    first_err = None
    for which, p in (("main", path), ("prev", prev_path(path))):
        if which == "prev" and not allow_prev:
            break
        if not p.exists():
            first_err = first_err or SecureError("E_FILE_MISSING")
            continue
        try:
            return dpapi_unprotect(_parse_envelope(p.read_text(encoding="utf-8"), purpose), purpose), which
        except SecureError as e:
            first_err = first_err if (first_err and first_err.code != "E_FILE_MISSING") else e
        except (OSError, UnicodeDecodeError):
            first_err = first_err or SecureError("E_FILE_CORRUPT")
    raise first_err or SecureError("E_FILE_MISSING")


def write_json(path: Path, obj, purpose: str) -> None:
    write_protected(path, json.dumps(obj, ensure_ascii=False).encode("utf-8"), purpose)


def read_json(path: Path, purpose: str, allow_prev: bool = True):
    raw, _src = read_protected(path, purpose, allow_prev)
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise SecureError("E_FILE_CORRUPT") from None


def file_state(path: Path, purpose: str) -> dict:
    """값을 꺼내지 않고 상태만: {'exists','format','decrypts','source','code'}"""
    path = Path(path)
    st = {"exists": path.exists(), "prev": prev_path(path).exists(), "format": None, "decrypts": False,
          "source": None, "code": None}
    if not st["exists"] and not st["prev"]:
        st["code"] = "E_FILE_MISSING"
        return st
    try:
        env = json.loads(path.read_text(encoding="utf-8")) if st["exists"] else {}
        st["format"] = env.get("format") if isinstance(env, dict) else "unknown"
    except Exception:
        st["format"] = "unreadable"
    try:
        _raw, src = read_protected(path, purpose)
        st.update(decrypts=True, source=src)
    except SecureError as e:
        st["code"] = e.code
    return st


# ═════════════════════════════════════════════════════════════ Windows 자격 증명 관리자
class _CREDENTIAL(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
                ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]


def _cred_target_exists(target: str) -> bool:
    """keyring 을 거치지 않고 윈도우 API 로 직접 «그 이름의 자격 증명이 있는지»만 본다 (값은 꺼내 보지 않는다)."""
    _require_windows()
    advapi = ctypes.windll.advapi32
    p = ctypes.POINTER(_CREDENTIAL)()
    if not advapi.CredReadW(ctypes.c_wchar_p(target), 1, 0, ctypes.byref(p)):     # 1 = CRED_TYPE_GENERIC
        return False
    advapi.CredFree(p)
    return True


def keyring_backend_name() -> str:
    import keyring
    kr = keyring.get_keyring()
    return f"{type(kr).__module__}.{type(kr).__name__}"


def check_vault(functional: bool = True) -> dict:
    """keyring 이 «진짜» Windows 자격 증명 관리자에 쓰는지 확인한다. 아니면 SecureError(E_VAULT_BACKEND).

    functional=True 면 임의의 시험 값을 넣고 → 윈도우 API 로 그 항목이 실제로 생겼는지 → keyring 으로 다시 읽어
    같은지 → 지운다. 시험 값은 이 함수 안에서만 만들고 버린다 (진짜 비밀번호가 아니다).
    """
    _require_windows()
    try:
        import keyring
        kr = keyring.get_keyring()
    except Exception as e:
        raise SecureError("E_VAULT_BACKEND", type(e).__name__) from None
    name = keyring_backend_name()
    ok_backend = name == WINVAULT
    if not ok_backend and hasattr(kr, "backends"):            # chainer 면 맨 앞(우선순위 최고)이 WinVault 여야 한다
        bs = list(getattr(kr, "backends") or [])
        ok_backend = bool(bs) and f"{type(bs[0]).__module__}.{type(bs[0]).__name__}" == WINVAULT
    if not ok_backend:
        raise SecureError("E_VAULT_BACKEND", name.rsplit(".", 1)[-1])
    res = {"backend": "WinVaultKeyring", "functional": None}
    if functional:
        svc = "neis-secure/_selftest_" + secrets.token_hex(4)
        val = secrets.token_urlsafe(16)
        try:
            keyring.set_password(svc, "selftest", val)
            exists = _cred_target_exists(svc) or _cred_target_exists("selftest@" + svc)
            back = keyring.get_password(svc, "selftest")
        except Exception as e:
            raise SecureError("E_VAULT_IO", type(e).__name__) from None
        finally:
            try:
                keyring.delete_password(svc, "selftest")
            except Exception:
                pass
        if not exists or back != val:
            raise SecureError("E_VAULT_VERIFY")
        res["functional"] = True
    return res


def _local_persist() -> None:
    """keyring(WinVaultKeyring) 도 «이 PC 에만» 저장하게 한다 (기본값은 ENTERPRISE)."""
    try:
        import keyring
        kr = keyring.get_keyring()
        if hasattr(type(kr), "persist"):
            kr.persist = CRED_PERSIST_LOCAL_MACHINE
    except Exception:
        pass


def vault_set(service: str, username: str, password: str) -> None:
    """금고에 넣고 다시 읽어 확인한다. 금고가 진짜가 아니면 넣지 않는다.
    ⚠️ str 을 받는 길 — 가상 값 시험·예전 호환용. 실제 비밀번호 입력은 vault_write_buffer (secure_password.py) 로 한다."""
    check_vault(functional=False)
    import keyring
    _local_persist()
    try:
        keyring.set_password(service, username, password)
        if keyring.get_password(service, username) != password:
            raise SecureError("E_VAULT_VERIFY")
    except SecureError:
        raise
    except Exception as e:
        raise SecureError("E_VAULT_IO", type(e).__name__) from None


def vault_get(service: str, username: str) -> str | None:
    check_vault(functional=False)
    import keyring
    try:
        return keyring.get_password(service, username)
    except Exception as e:
        raise SecureError("E_VAULT_IO", type(e).__name__) from None


def vault_get_credential(service: str):
    """(아이디, 비밀번호) 또는 (None, None)."""
    check_vault(functional=False)
    import keyring
    try:
        c = keyring.get_credential(service, None)
    except Exception as e:
        raise SecureError("E_VAULT_IO", type(e).__name__) from None
    return (c.username, c.password) if c and c.username and c.password else (None, None)


def vault_replace_credential(service: str, username: str, password: str) -> None:
    """한 서비스에 아이디·비밀번호 «한 벌»만 둔다 — 아이디가 바뀌면 옛 항목을 지우고 새로 넣는다. (가상 값 시험용 str 길)"""
    check_vault(functional=False)
    import keyring
    _local_persist()
    try:
        old_user, _ = vault_get_credential(service)
        if old_user and old_user != username:
            keyring.delete_password(service, old_user)
        keyring.set_password(service, username, password)
        u, p = vault_get_credential(service)
        if u != username or p != password:
            raise SecureError("E_VAULT_VERIFY")
    except SecureError:
        raise
    except Exception as e:
        raise SecureError("E_VAULT_IO", type(e).__name__) from None


def vault_delete(service: str, username: str | None = None) -> bool:
    """지웠으면 True, 원래 없었으면 False."""
    check_vault(functional=False)
    import keyring
    import keyring.errors
    try:
        if username is None:
            username = vault_username(service)          # 아이디만 (비밀번호는 안 꺼낸다)
            if not username:
                return False
        keyring.delete_password(service, username)
        # 예전 keyring 이 남긴 같은 아이디의 낡은 사본(아이디@서비스)까지 정리
        _delete_user(service, username)
        return True
    except keyring.errors.PasswordDeleteError:
        return False
    except Exception as e:
        raise SecureError("E_VAULT_IO", type(e).__name__) from None


# ═════════════════════════════════════════════════════════════ 비밀번호 «다루기» 규칙 (2026-11-09)
# 할 수 있는 것과 없는 것을 구분한다.
#   · SecretBuffer — 우리가 직접 만든 ctypes 버퍼. 다 쓰면 0 으로 덮어쓴다(wipe). 이것은 «확인 가능한» 지우기다.
#   · 파이썬 str — 바꿀 수 없는(immutable) 값이라 덮어쓸 방법이 없다. del·None·가비지 컬렉션은 «참조»를 없앨 뿐,
#     메모리에서 글자가 지워졌다는 뜻이 아니다. 금고에서 읽은 값(keyring)과 브라우저 입력(playwright)은 str 이 필요해서
#     그 순간에는 str 복사본이 생긴다 → 로그인 «직전»에 만들고 바로 참조를 끊어 «보관 시간»만 줄인다.
#   · 라이브러리(keyring·playwright·tk) 안, 크롬·AXGATE 프로세스 안, 윈도우 내부 복사본은 우리가 지울 수 없다.
_ADV = None


def _advapi():
    global _ADV
    if _ADV is None:
        _require_windows()
        a = ctypes.windll.advapi32
        a.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                ctypes.POINTER(ctypes.POINTER(_CREDENTIAL))]
        a.CredReadW.restype = wintypes.BOOL
        a.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
        a.CredWriteW.restype = wintypes.BOOL
        a.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        a.CredDeleteW.restype = wintypes.BOOL
        a.CredFree.argtypes = [ctypes.c_void_p]
        _ADV = a
    return _ADV


class SecretBuffer:
    """비밀번호를 담는 «지울 수 있는» 버퍼 (UTF-16, 최대 256자). 글자를 한 자씩 넣는다.

    str 로 꺼내는 길(reveal_str)은 시험용으로만 남겨 둔다 — 꺼낸 str 은 지울 수 없다.
    print·f-string·repr 로 내용이 나오지 않는다.
    """
    MAX = 256

    def __init__(self, capacity: int = MAX):
        self._cap = capacity
        self._buf = ctypes.create_unicode_buffer(capacity + 1)
        self._n = 0

    def append(self, ch: str) -> None:
        if len(ch) != 1 or ord(ch) > 0xFFFF:
            raise ValueError("E_CHAR")
        if self._n >= self._cap:
            raise ValueError("E_TOO_LONG")
        self._buf[self._n] = ch
        self._n += 1

    def pop(self) -> None:
        if self._n:
            self._n -= 1
            self._buf[self._n] = "\0"

    def __len__(self) -> int:
        return self._n

    @property
    def nbytes(self) -> int:
        return self._n * ctypes.sizeof(ctypes.c_wchar)

    @property
    def address(self) -> int:
        return ctypes.addressof(self._buf)

    def has_non_ascii(self) -> bool:
        return any(ord(self._buf[i]) > 0x7E for i in range(self._n))

    def wipe(self) -> None:
        ctypes.memset(ctypes.addressof(self._buf), 0, ctypes.sizeof(self._buf))
        self._n = 0

    def is_wiped(self) -> bool:
        raw = (ctypes.c_ubyte * ctypes.sizeof(self._buf)).from_address(ctypes.addressof(self._buf))
        return self._n == 0 and not any(raw)

    def reveal_str(self) -> str:          # ⚠️ 시험용 — 돌려준 str 은 지울 수 없다
        return ctypes.wstring_at(self.address, self._n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.wipe()

    def __del__(self):
        try:
            self.wipe()
        except Exception:
            pass

    def __repr__(self):
        return "<SecretBuffer>"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("SecretBuffer 는 복사·저장할 수 없습니다")


def read_secret_console(prompt: str, buf: SecretBuffer) -> bool:
    """검은 창에서 한 글자씩 받아 «버퍼에만» 넣는다 (화면에 아무것도 안 찍는다). 전체 비밀번호 str 을 만들지 않는다.
    Enter=끝(True) · Esc=취소(False) · Ctrl+C=KeyboardInterrupt."""
    import msvcrt
    sys.stdout.write(prompt)
    sys.stdout.flush()
    while True:
        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):
            break
        if ch == "\x03":
            buf.wipe()
            raise KeyboardInterrupt
        if ch == "\x1b":
            buf.wipe()
            sys.stdout.write("\n")
            return False
        if ch == "\x08":
            buf.pop()
            continue
        if ch in ("\x00", "\xe0"):            # 화살표·F키 — 두 글자로 온다
            msvcrt.getwch()
            continue
        if ch.isprintable():
            try:
                buf.append(ch)
            except ValueError:
                pass
    sys.stdout.write("\n")
    sys.stdout.flush()
    return True


class _CredRef:
    """CredRead 결과 — 비밀번호 바이트는 파이썬으로 꺼내지 않고, 다 쓰면 0 으로 덮고 CredFree."""

    def __init__(self, target: str):
        self.p = ctypes.POINTER(_CREDENTIAL)()
        self.ok = bool(_advapi().CredReadW(target, 1, 0, ctypes.byref(self.p)))

    def __enter__(self):
        return self.p.contents if self.ok else None

    def __exit__(self, *exc):
        if self.ok:
            c = self.p.contents
            if c.CredentialBlobSize and c.CredentialBlob:
                ctypes.memset(c.CredentialBlob, 0, c.CredentialBlobSize)
            _advapi().CredFree(self.p)
            self.ok = False


def _compound(username: str, service: str) -> str:
    return f"{username}@{service}"          # keyring(WinVaultKeyring) 과 같은 규칙


def _resolve_target(service: str, username: str | None) -> str | None:
    """keyring 의 _resolve_credential 과 같은 순서로, 그 계정이 든 «대상 이름»만 찾는다 (값은 안 꺼낸다)."""
    with _CredRef(service) as c:
        if c is not None and (username is None or c.UserName == username):
            return service
    if username:
        with _CredRef(_compound(username, service)) as c:
            if c is not None and c.UserName == username:
                return _compound(username, service)
    return None


def vault_exists(service: str, username: str | None) -> bool:
    """저장돼 있는지만 — 비밀번호 내용을 파이썬 값으로 꺼내지 않는다."""
    if not username and username is not None:
        return False
    try:
        t = _resolve_target(service, username)
        if not t:
            return False
        with _CredRef(t) as c:
            return bool(c is not None and c.CredentialBlobSize > 0)
    except SecureError:
        raise
    except Exception as e:
        raise SecureError("E_VAULT_IO", type(e).__name__) from None


def vault_username(service: str) -> str | None:
    """한 서비스에 한 벌만 두는 곳(리로스쿨)의 «아이디»만 읽는다. 비밀번호를 파이썬 값으로 꺼내지 않는다.
    (윈도우 API 가 돌려주는 버퍼에는 비밀번호도 함께 들어 있어 — 바로 0 으로 덮는다)"""
    with _CredRef(service) as c:
        return c.UserName if c is not None else None


def _write_raw(target: str, username: str, blob_addr: int, size: int) -> None:
    cred = _CREDENTIAL()
    cred.Type = 1                                 # CRED_TYPE_GENERIC
    cred.TargetName = target
    cred.UserName = username
    cred.Comment = "Stored using python-keyring"  # keyring 이 쓰는 것과 같은 모양 (읽기 호환)
    # CRED_PERSIST_LOCAL_MACHINE — «이 PC 에만». (keyring 기본값 ENTERPRISE 는 도메인 로밍 프로필이면 다른 PC 로 따라간다)
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE
    cred.CredentialBlobSize = size
    cred.CredentialBlob = ctypes.cast(ctypes.c_void_p(blob_addr), ctypes.POINTER(ctypes.c_char))
    if not _advapi().CredWriteW(ctypes.byref(cred), 0):
        raise SecureError("E_VAULT_IO", f"win32:{ctypes.GetLastError()}")


def _delete_target(target: str) -> bool:
    return bool(_advapi().CredDeleteW(target, 1, 0))


def _delete_user(service: str, username: str) -> int:
    n = 0
    for t in (service, _compound(username, service)):
        with _CredRef(t) as c:
            mine = c is not None and c.UserName == username
        if mine and _delete_target(t):
            n += 1
    return n


def _copy_target(src: str, dst: str) -> bool:
    """금고 항목 하나를 다른 이름으로 복사한다 (윈도우 버퍼 → 윈도우. 비밀번호를 파이썬 값으로 꺼내지 않는다)."""
    with _CredRef(src) as c:
        if c is None:
            return False
        _write_raw(dst, c.UserName, ctypes.cast(c.CredentialBlob, ctypes.c_void_p).value, c.CredentialBlobSize)
        return True


def _matches(target: str, username: str, buf: SecretBuffer) -> bool:
    with _CredRef(target) as c:
        return (c is not None and c.UserName == username and c.CredentialBlobSize == buf.nbytes and
                ctypes.cdll.msvcrt.memcmp(c.CredentialBlob, ctypes.c_void_p(buf.address), ctypes.c_size_t(buf.nbytes)) == 0)


def vault_write_buffer(service: str, username: str, buf: SecretBuffer, replace: bool = False) -> None:
    """버퍼 그대로 금고에 쓴다 — 전체 비밀번호 str 을 만들지 않는다. 읽는 쪽(keyring·EVPN 도우미)과 같은 모양.

    순서 (2026-11-09 점검 반영): ① 지금 «서비스» 자리에 있는 다른 아이디 항목은 «아이디@서비스» 로 복사해 둔다
    → ② 새 항목을 쓴다 → ③ 다시 읽어 «윈도우 API 안에서» 바이트를 비교한다 → ④ 맞을 때만 옛 항목을 정리한다.
    ②·③ 에서 실패하면 ① 의 복사본으로 원래 자리를 되돌린다 — 새 저장이 실패해도 옛 항목이 사라지지 않는다.
    replace=True: 한 서비스에 한 벌만 (리로스쿨) — 아이디가 바뀌면 ④ 에서 옛 아이디 항목을 지운다.
    keyring 과 달리 같은 아이디의 낡은 «아이디@서비스» 사본을 남기지 않는다. 저장 범위는 «이 PC 에만».
    """
    check_vault(functional=False)
    if not username:
        raise SecureError("E_NO_ACCOUNT")
    if not len(buf):
        raise SecureError("E_EMPTY")
    with _CredRef(service) as ex:
        moved_user = ex.UserName if ex is not None and ex.UserName != username else None
    if moved_user:                                                     # ①
        _copy_target(service, _compound(moved_user, service))
    try:
        _write_raw(service, username, buf.address, buf.nbytes)          # ②
        if not _matches(service, username, buf):                        # ③
            raise SecureError("E_VAULT_VERIFY")
    except Exception:
        if moved_user:                                                  # 되돌리기 — 옛 항목을 원래 자리로
            try:
                _copy_target(_compound(moved_user, service), service)
                if replace:
                    _delete_target(_compound(moved_user, service))      # 한 벌만 두는 곳은 원래 모양 그대로
            except Exception:
                pass
        raise
    stale = _compound(username, service)                                # ④ 정리
    with _CredRef(stale) as c:
        dup = c is not None and c.UserName == username
    if dup:
        _delete_target(stale)
    if replace and moved_user:
        _delete_user(service, moved_user)


class VaultSecret:
    """«금고의 어느 칸» 만 들고 다니는 표 — 비밀번호 자체는 들고 있지 않다.

    로그인 직전에 reveal() 로 꺼내 입력칸에 넣고, 쓰는 쪽에서 바로 참조를 끊는다 (str 이라 지워지지는 않는다).
    """
    __slots__ = ("service", "username")

    def __init__(self, service: str, username: str):
        self.service, self.username = service, username

    def exists(self) -> bool:
        return vault_exists(self.service, self.username)

    def __bool__(self):
        return self.exists()

    def reveal(self) -> str:
        v = vault_get(self.service, self.username)
        if not v:
            raise SecureError("E_VAULT_MISSING")
        return v

    def __repr__(self):
        return "<VaultSecret>"

    __str__ = __repr__


def _marks_file() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "neis-automation" / "vault_status.json"


def mark_vault(key: str, state: str) -> None:
    """금고에 «넣었다/지웠다» 표시만 남긴다 (값·아이디·인증서 이름 없음). 클로드 쪽 점검은 금고를 열지 않고 이것만 본다."""
    p = _marks_file()
    try:
        d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        if not isinstance(d, dict):
            d = {}
    except (OSError, ValueError):
        d = {}
    d[key] = {"state": state, "at": datetime.now().isoformat(timespec="seconds")}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def vault_marks() -> dict:
    try:
        d = json.loads(_marks_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def reveal(x) -> str:
    """VaultSecret 이면 지금 금고에서 꺼내고, (예전 코드가 넘긴) str 이면 그대로."""
    return x.reveal() if isinstance(x, VaultSecret) else x


class Scrubber:
    """출력에서 비밀번호를 가리되, 비밀번호 «글자»는 들고 있지 않는다 — 길이와 (임의 소금을 친) 해시만 둔다.

    글을 같은 길이로 밀어 가며 해시를 맞춰 본다. (주의: 해시도 비밀번호에서 나온 값이다 — 메모리를 통째로 읽을 수
    있는 사람은 짧고 흔한 비밀번호라면 맞혀 볼 수 있다. 같은 Windows 사용자 권한이면 금고도 열 수 있으니 위험이 더 커지지는 않는다)
    """
    __slots__ = ("_salt", "_items")

    def __init__(self):
        self._salt = secrets.token_bytes(16)
        self._items = set()

    def add(self, s: str) -> None:
        if s and len(s) >= 3:
            import hashlib
            self._items.add((len(s), hashlib.sha256(self._salt + s.encode("utf-8")).digest()))

    def scrub(self, text: str) -> str:
        import hashlib
        for L, dg in sorted(self._items, reverse=True):
            out, i, last = [], 0, 0
            while i + L <= len(text):
                if hashlib.sha256(self._salt + text[i:i + L].encode("utf-8")).digest() == dg:
                    out.append(text[last:i] + "****")
                    i += L
                    last = i
                else:
                    i += 1
            text = "".join(out) + text[last:]
        return text

    def __repr__(self):
        return "<Scrubber>"


# ═════════════════════════════════════════════════════════════ 폴더 권한 (ACL)
WELL_KNOWN = {"S-1-5-18": "SYSTEM", "S-1-5-32-544": "Administrators", "S-1-5-32-545": "Users",
              "S-1-1-0": "Everyone", "S-1-5-11": "AuthenticatedUsers", "S-1-3-0": "CreatorOwner",
              "S-1-5-32-546": "Guests", "S-1-15-2-1": "AllAppPackages"}


def _current_sid():
    import win32api
    import win32security
    tok = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    return win32security.GetTokenInformation(tok, win32security.TokenUser)[0]


def restrict_dir(path: Path) -> None:
    """이 폴더(와 안의 파일)를 «지금 Windows 사용자 + SYSTEM» 만 열 수 있게 한다.

    이 키트가 만든 인증정보 폴더에만 쓴다 — 시스템 폴더·다른 폴더 권한은 건드리지 않는다.
    """
    _require_windows()
    try:
        import ntsecuritycon as con
        import win32security
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        me = _current_sid()
        system = win32security.CreateWellKnownSid(win32security.WinLocalSystemSid)
        inherit = con.OBJECT_INHERIT_ACE | con.CONTAINER_INHERIT_ACE
        dacl = win32security.ACL()
        dacl.AddAccessAllowedAceEx(win32security.ACL_REVISION, inherit, con.FILE_ALL_ACCESS, me)
        dacl.AddAccessAllowedAceEx(win32security.ACL_REVISION, inherit, con.FILE_ALL_ACCESS, system)
        win32security.SetNamedSecurityInfo(
            str(path), win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            None, None, dacl, None)
        # 이미 들어 있던 파일도 새 규칙을 물려받게 (파일마다 따로 붙은 권한은 지운다)
        for f in path.rglob("*"):
            fd = win32security.ACL()
            win32security.SetNamedSecurityInfo(
                str(f), win32security.SE_FILE_OBJECT,
                win32security.DACL_SECURITY_INFORMATION | win32security.UNPROTECTED_DACL_SECURITY_INFORMATION,
                None, None, fd, None)
    except Exception as e:
        raise SecureError("E_ACL", type(e).__name__) from None


def acl_report(path: Path) -> dict:
    """누가 열 수 있는지 «종류»만 — 계정 이름은 돌려주지 않는다."""
    _require_windows()
    out = {"exists": Path(path).exists(), "inherits": None, "principals": [], "only_user_system": None}
    if not out["exists"]:
        return out
    try:
        import win32security
        sd = win32security.GetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
                                                win32security.DACL_SECURITY_INFORMATION)
        ctrl, _rev = sd.GetSecurityDescriptorControl()
        out["inherits"] = not bool(ctrl & win32security.SE_DACL_PROTECTED)
        dacl = sd.GetSecurityDescriptorDacl()
        me = win32security.ConvertSidToStringSid(_current_sid())
        kinds = set()
        for i in range(dacl.GetAceCount() if dacl else 0):
            ace = dacl.GetAce(i)
            if ace[0][0] != 0:                     # ACCESS_ALLOWED 만 센다
                continue
            sid = win32security.ConvertSidToStringSid(ace[-1])
            kinds.add("CurrentUser" if sid == me else WELL_KNOWN.get(sid, "Other"))
        out["principals"] = sorted(kinds)
        out["only_user_system"] = kinds <= {"CurrentUser", "SYSTEM"}
    except Exception as e:
        out["error"] = type(e).__name__
    return out


# ═════════════════════════════════════════════════════════════ 위치 점검
CLOUD_MARKERS = ("onedrive", "dropbox", "google drive", "googledrive", "my drive", "내 드라이브", "icloud",
                 "box sync", "naver mybox", "mybox")


def _git_root(p: Path) -> Path | None:
    for d in [p, *p.parents]:
        if (d / ".git").exists():
            return d
    return None


def location_report(path: Path) -> dict:
    path = Path(path).resolve() if Path(path).exists() else Path(os.path.abspath(path))
    low = str(path).lower()
    cloud_roots = [os.environ.get(k) for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")]
    in_cloud = any(r and low.startswith(r.lower().rstrip("\\") + "\\") for r in cloud_roots) or \
        any(m in low for m in CLOUD_MARKERS)
    remote = low.startswith("\\\\")
    if not remote and os.name == "nt" and len(str(path)) > 2 and str(path)[1] == ":":
        remote = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(str(path)[:3])) == 4    # DRIVE_REMOTE
    return {"in_git": _git_root(path) is not None, "in_cloud_sync": in_cloud, "remote_drive": bool(remote)}


def assert_safe_location(path: Path) -> None:
    r = location_report(path)
    if r["in_git"]:
        raise SecureError("E_LOCATION_GIT")
    if r["in_cloud_sync"]:
        raise SecureError("E_LOCATION_CLOUD")
    if r["remote_drive"]:
        raise SecureError("E_LOCATION_REMOTE")


def prepare_secure_dir(path: Path) -> Path:
    """인증정보 폴더 준비: 위치 확인(깃·클라우드·네트워크 아님) → 만들기 → 권한 좁히기."""
    path = Path(path)
    assert_safe_location(path)
    path.mkdir(parents=True, exist_ok=True)
    rep = acl_report(path)
    if not rep.get("only_user_system") or rep.get("inherits"):
        restrict_dir(path)
    return path


# ═════════════════════════════════════════════════════════════ 출력 가리기
def scrub(text: str, secrets_: list[str] | tuple[str, ...]) -> str:
    """값을 **** 로 가린다 — 자르기 «전에» 부른다 (잘린 조각이 남지 않게). 짧은 값(3자 미만)은 건드리지 않는다."""
    for s in sorted({x for x in secrets_ if x and len(x) >= 3}, key=len, reverse=True):
        text = text.replace(s, "****")
    return text


PHONE_RE = re.compile(r"01[016-9][-\s.]?\d{3,4}[-\s.]?\d{4}")


def scrub_common(text: str) -> str:
    """전화번호처럼 생긴 것은 늘 가린다."""
    return PHONE_RE.sub("010-****-****", text)


def now_stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


if __name__ == "__main__":
    # 간단 자가 점검 — 값 없이 결과만
    try:
        print(json.dumps({"vault": check_vault()}, ensure_ascii=False))
    except SecureError as e:
        print(json.dumps({"vault": {"error": e.code}}, ensure_ascii=False))
        sys.exit(1)

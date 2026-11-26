# -*- coding: utf-8 -*-
"""본인 확인 — 처음 한 번 Windows Hello 또는 잠금 비밀번호를 고르고, 그다음부터는 고른 방식으로 확인한다."""

import ctypes as C
import json
import os
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from uuid import UUID, uuid5


_PTR = C.c_void_p
_PTR_OUT = C.POINTER(_PTR)
_HRESULT = C.c_long
_INT_OUT = C.POINTER(C.c_int32)
_GUID = C.c_ubyte * 16

_INTEROP = _GUID.from_buffer_copy(UUID("39e050c3-4e74-441a-8dc0-b81104df949c").bytes_le)
_ASYNC_INFO = _GUID.from_buffer_copy(UUID("00000036-0000-0000-c000-000000000046").bytes_le)
# WinRT parameterized IID for IAsyncOperation<UserConsentVerificationResult>.
_VERIFICATION = _GUID.from_buffer_copy(uuid5(
    UUID("11f47ad5-7b73-42c0-abae-878b1e16adee"),
    "pinterface({9fc2b0bb-e446-44e2-aa61-9cab8f636af2};"
    "enum(Windows.Security.Credentials.UI.UserConsentVerificationResult;i4))",
).bytes_le)

VERIFIED = 0
CANCELED = 6
DESKTOP_VERIFIED_EXIT = 42  # TeacherDeskText opens the selected action only on this exit code.

# 지문(또는 PIN)으로 한 번 확인하면 30분 동안은 다시 묻지 않는다 (2026-11-13 선생님 요청).
# 위젯 글자·보안 설정창이 모두 이 파일을 거치므로 30분은 둘이 함께 쓴다. 마지막 확인부터 고정 30분(누를 때마다 늘지 않는다).
# 파일에는 «언제 확인했는지»만 적는다. 재부팅·로그아웃 뒤에는 무효(부팅 시각이 달라진다), 시계를 뒤로 돌려도 무효.
GRACE_SECONDS = 30 * 60
_BOOT_TOLERANCE = 120  # 인터넷 시간 맞춤으로 벽시계가 조금 움직여도 같은 부팅으로 본다


def _grace_file():
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "neis-automation" / "hello_grace.json"


def _clock():
    """(부팅 뒤 지난 밀리초, 부팅 시각 추정값). 앞의 것은 시계를 바꿔도 흔들리지 않는다."""
    tick_count = C.windll.kernel32.GetTickCount64
    tick_count.restype = C.c_ulonglong
    tick = int(tick_count())
    return tick, time.time() - tick / 1000


def remember_verification():
    try:
        tick, boot = _clock()
        path = _grace_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps({"tick": tick, "boot": boot}), encoding="utf-8")
        os.replace(temp, path)
    except (OSError, AttributeError):
        pass  # 기록을 못 해도 이번 확인은 성공 — 다음에 한 번 더 물을 뿐이다


def forget_verification():
    try:
        _grace_file().unlink()
    except OSError:
        pass


def recently_verified():
    """마지막 Windows Hello 확인이 30분 안이면 True."""
    try:
        saved = json.loads(_grace_file().read_text(encoding="utf-8"))
        tick, boot = _clock()
        elapsed = tick - int(saved["tick"])
        same_boot = abs(boot - float(saved["boot"])) <= _BOOT_TOLERANCE
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
    return same_boot and 0 <= elapsed <= GRACE_SECONDS * 1000


def _check(result):
    if result < 0:
        raise OSError(f"Windows Hello HRESULT 0x{result & 0xFFFFFFFF:08X}")


def _method(obj, slot, result, *args):
    vtable = C.cast(obj, C.POINTER(C.POINTER(_PTR)))[0]
    return C.WINFUNCTYPE(result, _PTR, *args)(vtable[slot])


@contextmanager
def request_verification(window, message):
    """Yield a poll function on the Tk UI thread. Windows handles all credentials."""
    winrt = C.WinDLL("combase")
    winrt.RoInitialize.argtypes = [C.c_uint32]
    winrt.RoInitialize.restype = _HRESULT
    winrt.RoUninitialize.argtypes = []
    winrt.RoUninitialize.restype = None
    winrt.WindowsCreateString.argtypes = [C.c_wchar_p, C.c_uint32, _PTR_OUT]
    winrt.WindowsCreateString.restype = _HRESULT
    winrt.WindowsDeleteString.argtypes = [_PTR]
    winrt.WindowsDeleteString.restype = _HRESULT
    winrt.RoGetActivationFactory.argtypes = [_PTR, _PTR, _PTR_OUT]
    winrt.RoGetActivationFactory.restype = _HRESULT

    user32 = C.WinDLL("user32")
    user32.GetAncestor.argtypes = [_PTR, C.c_uint32]
    user32.GetAncestor.restype = _PTR
    # Tk's winfo_id points to its child HWND. Hello needs the outer window.
    owner = user32.GetAncestor(window, 2)  # GA_ROOT
    if not owner:
        raise OSError("Windows Hello requires an owner window")

    with ExitStack() as cleanup:
        _check(winrt.RoInitialize(0))  # RO_INIT_SINGLETHREADED
        cleanup.callback(winrt.RoUninitialize)

        def hstring(value):
            string = _PTR()
            _check(winrt.WindowsCreateString(value, len(value.encode("utf-16-le")) // 2, C.byref(string)))
            cleanup.callback(winrt.WindowsDeleteString, string)
            return string

        name = hstring("Windows.Security.Credentials.UI.UserConsentVerifier")
        prompt = hstring(message)
        factory = _PTR()
        _check(winrt.RoGetActivationFactory(name, _INTEROP, C.byref(factory)))
        cleanup.callback(_method(factory, 2, C.c_ulong), factory)  # IUnknown::Release

        operation = _PTR()
        request = _method(factory, 6, _HRESULT, _PTR, _PTR, _PTR, _PTR_OUT)
        _check(request(factory, owner, prompt, _VERIFICATION, C.byref(operation)))
        cleanup.callback(_method(operation, 2, C.c_ulong), operation)

        info = _PTR()
        query_interface = _method(operation, 0, _HRESULT, _PTR, _PTR_OUT)
        _check(query_interface(operation, _ASYNC_INFO, C.byref(info)))
        cleanup.callback(_method(info, 2, C.c_ulong), info)
        get_status = _method(info, 7, _HRESULT, _INT_OUT)  # IAsyncInfo::get_Status
        get_result = _method(operation, 8, _HRESULT, _INT_OUT)  # IAsyncOperation::GetResults

        pending = True

        def poll():
            nonlocal pending
            status = C.c_int32(-1)
            _check(get_status(info, C.byref(status)))
            if status.value == 0:  # Started
                return None
            pending = False
            if status.value == 2:  # Canceled
                return CANCELED
            if status.value != 1:  # Completed
                raise OSError("Windows Hello verification did not complete")
            result = C.c_int32(-1)
            _check(get_result(operation, C.byref(result)))
            return result.value

        try:
            yield poll
        finally:
            if pending:
                _method(info, 9, _HRESULT)(info)  # IAsyncInfo::Cancel


# ── 본인 확인 방식 고르기 (2026-11-26) ─────────────────────────────────────────────
# 처음 한 번 «Windows Hello(지문·얼굴·PIN)» 와 «잠금 비밀번호» 중에서 고르고, 그다음부터는 고른 방식으로만 확인한다.
# (선생님 말씀: 지문·얼굴 장치가 없는 노트북이 대부분이다.) 고른 방식과 잠금 비밀번호의 «소금 친 해시»만
# 이 Windows 사용자만 풀 수 있는 암호화(DPAPI) 파일에 둔다 — 비밀번호 글자 자체는 어디에도 남기지 않는다.
# 바탕화면 글자 위젯이 이 파일을 «격리 모드(-I)» 로 부르므로 표준 라이브러리만 쓴다 (키트의 secure_store 를 못 부른다).
import hashlib
import hmac

UNLOCK_MAX_FAILS = 5                 # 잠금 비밀번호를 이만큼 틀리면
UNLOCK_LOCK_SECONDS = 5 * 60         # 이 시간 동안 잠근다
PBKDF2_ITERATIONS = 300_000
MIN_PASSWORD = 6
_ENTROPY = b"neis-login-kit/unlock/v1"
RESET_HINT = "키트 폴더의 «본인확인_다시고르기.cmd» 를 더블클릭하면 방식을 처음부터 다시 고를 수 있어요."


class UnlockBroken(Exception):
    pass


def _unlock_file():
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "neis-automation" / "unlock.dpapi"


class _Blob(C.Structure):
    _fields_ = [("cbData", C.c_ulong), ("pbData", C.c_void_p)]


def _dpapi(data: bytes, protect: bool) -> bytes:
    """Windows 암호화(DPAPI, 현재 사용자 범위). protect=False 면 푼다."""
    # ★ 공용 함수(C.windll.crypt32.CryptProtectData)의 argtypes 를 바꾸지 않는다 — 같은 프로그램 안의 secure_store 가
    #   자기 구조체로 같은 함수를 부르다 «expected LP__Blob» 으로 깨졌다 (2026-11-26 시험에서 잡음: 설정 창은
    #   본인 확인 → 저장 순서라 실제로도 저장이 깨졌을 것). 이 파일 전용 함수 원형을 따로 만든다.
    proto = C.WINFUNCTYPE(C.c_int, C.POINTER(_Blob), C.c_void_p, C.POINTER(_Blob), C.c_void_p, C.c_void_p,
                          C.c_ulong, C.POINTER(_Blob))
    fn = proto(("CryptProtectData" if protect else "CryptUnprotectData", C.windll.crypt32))
    local_free = C.WINFUNCTYPE(C.c_void_p, C.c_void_p)(("LocalFree", C.windll.kernel32))
    buf = C.create_string_buffer(data, len(data))
    ent_buf = C.create_string_buffer(_ENTROPY, len(_ENTROPY))
    src, ent, out = _Blob(len(data), C.cast(buf, C.c_void_p)), _Blob(len(_ENTROPY), C.cast(ent_buf, C.c_void_p)), _Blob()
    if not fn(C.byref(src), None, C.byref(ent), None, None, 0x1, C.byref(out)):   # CRYPTPROTECT_UI_FORBIDDEN
        raise OSError("DPAPI %d" % C.GetLastError())
    try:
        return C.string_at(out.pbData, out.cbData)
    finally:
        local_free(out.pbData)


def load_unlock():
    """고른 방식 기록 — 아직 안 골랐으면 None. 풀 수 없으면 UnlockBroken (다른 PC·다른 계정에서 가져온 파일 등)."""
    try:
        raw = _unlock_file().read_bytes()
    except FileNotFoundError:
        return None
    try:
        rec = json.loads(_dpapi(raw, False).decode("utf-8"))
        assert rec.get("method") in ("hello", "password")
        return rec
    except Exception:
        raise UnlockBroken() from None


def save_unlock(rec):
    path = _unlock_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_bytes(_dpapi(json.dumps(rec).encode("utf-8"), True))
    os.replace(temp, path)


def current_method():
    """설정 창 표시용 — "hello" · "password" · None(아직 안 고름) · "broken"."""
    try:
        rec = load_unlock()
    except UnlockBroken:
        return "broken"
    return rec and rec["method"]


def reset_unlock():
    """방식을 처음부터 다시 고르게 한다 (확인이 아예 안 될 때의 비상구). 30분 기억도 지운다."""
    for p in (_unlock_file(), _unlock_file().with_suffix(".tmp")):
        try:
            p.unlink()
        except OSError:
            pass
    forget_verification()


def _hash(pw, salt, iterations):
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, iterations)


def make_password_record(pw):
    salt = os.urandom(16)
    return {"v": 1, "method": "password", "salt": salt.hex(), "iter": PBKDF2_ITERATIONS,
            "hash": _hash(pw, salt, PBKDF2_ITERATIONS).hex(), "fails": 0, "locked_until": 0}


def password_attempt(rec, pw, now=None):
    """(결과, 바뀐 기록). 결과: "OK" · "E_WRONG" · "E_LOCKED". 기록은 부른 쪽이 저장한다."""
    now = time.time() if now is None else now
    rec = dict(rec)
    if rec.get("locked_until", 0) > now:
        return "E_LOCKED", rec
    ok = hmac.compare_digest(_hash(pw, bytes.fromhex(rec["salt"]), int(rec["iter"])).hex(), rec["hash"])
    if ok:
        rec["fails"], rec["locked_until"] = 0, 0
        return "OK", rec
    rec["fails"] = int(rec.get("fails", 0)) + 1
    if rec["fails"] >= UNLOCK_MAX_FAILS:
        rec["fails"], rec["locked_until"] = 0, now + UNLOCK_LOCK_SECONDS
        return "E_LOCKED", rec
    return "E_WRONG", rec


# ── 창 (tkinter) — 시험에서는 이 세 함수를 바꿔 끼운다 ──
def _show(root, title):
    root.title(title)
    root.deiconify()
    root.lift()
    try:
        root.attributes("-topmost", True)
        root.after(300, lambda: root.attributes("-topmost", False))
    except Exception:
        pass


def _ui_choose(root, title):
    """처음 한 번 — "hello" · "password" · None(취소)."""
    from tkinter import ttk
    _show(root, title)
    panel = ttk.Frame(root, padding=20)
    panel.pack()
    ttk.Label(panel, justify="left", wraplength=470, text=(
        "본인 확인 방식을 골라 주세요 (처음 한 번만).\n\n"
        "• Windows Hello — 지문·얼굴·PIN. 이 PC에 Windows Hello가 설정돼 있어야 합니다.\n"
        "• 잠금 비밀번호 — 이 키트에서만 쓰는 비밀번호를 새로 만듭니다 (인증서 암호와 다르게 정해 주세요).\n\n"
        "고른 방식은 나중에 설정 창 [본인 확인 방식 바꾸기] 에서 바꿀 수 있어요.")).pack(anchor="w")
    picked = {"v": None}

    def pick(v):
        picked["v"] = v
        root.quit()
    bf = ttk.Frame(panel)
    bf.pack(pady=(14, 0))
    ttk.Button(bf, text="Windows Hello", command=lambda: pick("hello")).pack(side="left", padx=4)
    ttk.Button(bf, text="잠금 비밀번호", command=lambda: pick("password")).pack(side="left", padx=4)
    ttk.Button(bf, text="취소", command=root.quit).pack(side="left", padx=4)
    root.protocol("WM_DELETE_WINDOW", root.quit)
    root.bind("<Escape>", lambda e: root.quit())
    try:
        root.mainloop()
    finally:
        root.unbind("<Escape>")
        panel.destroy()
    return picked["v"]


def _ui_fields(root, title, text, labels):
    """가려진 칸들 — 넣은 글자 목록 또는 None(취소). 칸은 돌려주기 전에 비운다."""
    from tkinter import ttk
    _show(root, title)
    panel = ttk.Frame(root, padding=20)
    panel.pack()
    ttk.Label(panel, text=text, justify="left", wraplength=360).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
    entries = []
    for i, lab in enumerate(labels, 1):
        ttk.Label(panel, text=lab).grid(row=i, column=0, sticky="w", padx=(0, 8), pady=3)
        e = ttk.Entry(panel, show="●", width=24)
        e.grid(row=i, column=1, pady=3)
        entries.append(e)
    got = {"v": None}

    def ok(*_):
        got["v"] = [e.get() for e in entries]
        root.quit()
    bf = ttk.Frame(panel)
    bf.grid(row=len(labels) + 1, column=0, columnspan=2, pady=(12, 0))
    ttk.Button(bf, text="확인", command=ok).pack(side="left", padx=4)
    ttk.Button(bf, text="취소", command=root.quit).pack(side="left", padx=4)
    root.protocol("WM_DELETE_WINDOW", root.quit)
    root.bind("<Return>", ok)
    root.bind("<Escape>", lambda e: root.quit())
    entries[0].focus_set()
    try:
        root.mainloop()
    finally:
        for e in entries:
            e.delete(0, "end")
        root.unbind("<Return>")
        root.unbind("<Escape>")
        panel.destroy()
    return got["v"]


def _ui_info(root, title, text, error=False):
    from tkinter import messagebox
    (messagebox.showerror if error else messagebox.showinfo)(title, text, parent=root)


def _hello(root, title, message):
    """Windows Hello 창 — "OK" · "CANCELLED" · "E_HELLO_SETUP"(이 PC에 준비 안 됨) · "E_HELLO". 알림 창은 부른 쪽이 띄운다."""
    from tkinter import ttk
    _show(root, title)
    panel = ttk.Frame(root, padding=20)
    panel.pack()
    ttk.Label(panel, text="Windows Hello에서 지문·얼굴 또는 PIN으로 본인을 확인해 주세요.").pack()
    ttk.Button(panel, text="취소", command=root.quit).pack(pady=(12, 0))
    root.protocol("WM_DELETE_WINDOW", root.quit)
    root.bind("<Escape>", lambda event: root.quit())
    root.update_idletasks()

    code = "CANCELLED"
    pending_check = None
    try:
        with request_verification(root.winfo_id(), message) as poll:
            def check():
                nonlocal code, pending_check
                pending_check = None
                try:
                    result = poll()
                except OSError:
                    code = "E_HELLO"
                else:
                    if result is None:
                        pending_check = root.after(100, check)
                        return
                    code = {VERIFIED: "OK", CANCELED: "CANCELLED", 1: "E_HELLO_SETUP",
                            2: "E_HELLO_SETUP"}.get(result, "E_HELLO")
                root.quit()

            pending_check = root.after(0, check)
            root.mainloop()
    except (OSError, AttributeError):
        code = "E_HELLO"
    finally:
        if pending_check is not None:
            root.after_cancel(pending_check)
        root.unbind("<Escape>")
        panel.destroy()
    return code


def _create_password(root, title):
    """새 잠금 비밀번호 — 만든 기록 또는 None(취소)."""
    while True:
        vals = _ui_fields(root, title, f"이 키트에서만 쓰는 잠금 비밀번호를 정해 주세요 ({MIN_PASSWORD}자 이상).\n"
                                       "인증서 암호와는 다르게 정해 주세요.", ["새 비밀번호", "한 번 더"])
        if vals is None:
            return None
        first, again = vals
        if len(first) < MIN_PASSWORD:
            _ui_info(root, title, f"{MIN_PASSWORD}자 이상으로 정해 주세요.", error=True)
        elif first != again:
            _ui_info(root, title, "두 칸이 달라요. 다시 넣어 주세요.", error=True)
        else:
            rec = make_password_record(first)
            first = again = None
            return rec


def choose_method(root, title, message):
    """처음 한 번 방식을 고른다. 고르고 확인까지 되면 "OK"."""
    while True:
        picked = _ui_choose(root, title)
        if picked is None:
            return "CANCELLED"
        if picked == "hello":
            code = _hello(root, title, message)
            if code == "OK":
                save_unlock({"v": 1, "method": "hello"})
                _ui_info(root, title, "정했어요 — 다음부터는 Windows Hello로 확인합니다.")
                return "OK"
            if code == "E_HELLO_SETUP":
                _ui_info(root, title, "이 PC에는 Windows Hello가 준비돼 있지 않아요.\n«잠금 비밀번호» 를 골라 주세요.", error=True)
            elif code != "CANCELLED":
                _ui_info(root, title, "Windows Hello 확인을 마치지 못했어요. 다시 골라 주세요.", error=True)
            continue
        rec = _create_password(root, title)
        if rec is None:
            continue
        save_unlock(rec)
        _ui_info(root, title, "잠금 비밀번호를 만들었어요 — 다음부터는 이 비밀번호로 확인합니다.")
        return "OK"


def _password(root, title, message, rec):
    while True:
        vals = _ui_fields(root, title, message + "\n잠금 비밀번호를 넣어 주세요.", ["잠금 비밀번호"])
        if vals is None:
            return "CANCELLED"
        code, rec = password_attempt(rec, vals[0])
        vals = None
        save_unlock(rec)
        if code == "OK":
            return "OK"
        if code == "E_LOCKED":
            left = max(1, int((rec.get("locked_until", 0) - time.time() + 59) // 60))
            _ui_info(root, title, f"여러 번 틀려서 잠갔어요. {left}분 뒤에 다시 해 주세요.", error=True)
            return "E_LOCKED"
        _ui_info(root, title, f"비밀번호가 달라요. (남은 기회 {UNLOCK_MAX_FAILS - rec['fails']}번)", error=True)


def show_verification(root, title, message, choose_again=False):
    """고른 방식으로 본인을 확인한다. 처음이면 방식부터 고른다. choose_again=True 면 지금 방식으로 확인한 뒤 다시 고른다."""
    if recently_verified() and not choose_again:
        root.withdraw()
        return "OK"
    try:
        rec = load_unlock()
    except UnlockBroken:
        _ui_info(root, title, "본인 확인 기록을 읽지 못했어요.\n" + RESET_HINT, error=True)
        root.withdraw()
        return "E_UNLOCK_BROKEN"
    if rec is None:
        code = choose_method(root, title, message)
    else:
        if rec["method"] == "hello":
            code = _hello(root, title, message)
            if code == "E_HELLO_SETUP":
                _ui_info(root, title, "이 PC에서 Windows Hello를 쓸 수 없어요.\n" + RESET_HINT, error=True)
            elif code == "E_HELLO":
                _ui_info(root, title, "Windows Hello 확인을 마치지 못했어요. 다시 시도해 주세요.", error=True)
        else:
            code = _password(root, title, message, rec)
        if code == "OK" and choose_again:
            code = choose_method(root, title, message)
    if code == "OK":
        remember_verification()
    root.withdraw()
    return code


def main(args):
    import tkinter as tk

    if args == ["--reset"]:          # 비상구: 방식을 처음부터 다시 고르게 (확인이 아예 안 될 때)
        reset_unlock()
        return 0
    change = args == ["--change"]    # 설정 창 [본인 확인 방식 바꾸기] — 지금 방식으로 확인한 뒤 다시 고른다
    if not change and (len(args) != 1 or not args[0].strip() or args[0].startswith("--")):
        return 1
    label = "본인 확인 방식 바꾸기" if change else args[0]
    if not change and recently_verified():   # 창을 만들기 전에 — 30분 안이면 깜빡임 없이 바로 연다
        return DESKTOP_VERIFIED_EXIT
    root = tk.Tk()
    try:
        result = show_verification(root, "교무 도구 본인 확인",
                                   f"교무 도구 「{label}」 — 본인을 확인해 주세요.", choose_again=change)
        return DESKTOP_VERIFIED_EXIT if result == "OK" else 1
    finally:
        root.destroy()


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))

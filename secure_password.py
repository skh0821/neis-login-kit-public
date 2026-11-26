# -*- coding: utf-8 -*-
"""비밀번호 «한 가지»만 받아 Windows 금고에 넣고 바로 끝나는 짧은 프로그램.

  python secure_password.py open --target cert      나이스·에듀파인 인증서 암호   (작은 창)
  python secure_password.py open --target evpn      EVPN 비밀번호
  python secure_password.py open --target riro      리로스쿨 아이디·비밀번호
  … --console                                       작은 창 대신 새 검은 창 (글자가 안 보인다)

부르는 쪽(클로드·설정창·위젯)이 받는 것은 상태 한 줄뿐이다:  saved / cancelled / failed  + 개인정보 없는 코드.
  종료 코드 0 = 저장 · 2 = 취소 · 1 = 실패.  비밀번호는 명령줄·환경변수·클립보드·파일·로그·프로세스 사이로 오가지 않는다.

이 프로그램 안에서의 처리
  · 글자는 키를 누를 때마다 «지울 수 있는 버퍼»(secure_store.SecretBuffer)에 한 자씩 넣는다. 입력칸에는 ● 만 넣는다
    (입력칸 안에는 진짜 글자가 없다). 붙여넣기는 막는다 (클립보드를 쓰지 않게).
  · 저장은 버퍼에서 바로 금고로(CredWriteW) — 전체 비밀번호가 파이썬 str 로 만들어지지 않는다.
  · 저장·취소·오류·시간 초과 어느 길이든 버퍼를 0 으로 덮고(wipe) 창을 닫고 끝낸다.
한계 (보장하지 않는 것)
  · 키를 누를 때 tk·윈도우가 잠깐 만드는 «한 글자» 값, 운영체제 입력 처리·금고 서비스 안의 복사본은 지울 수 없다.
  · 같은 Windows 사용자 권한으로 도는 프로그램은 금고를 열 수 있다 (Windows 금고의 성질).
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parent
if str(KIT) not in sys.path:
    sys.path.insert(0, str(KIT))

import secure_store as S  # noqa: E402
import teacher_config as T  # noqa: E402

EXIT = {"saved": 0, "failed": 1, "cancelled": 2}
IDLE_SEC = 300


def _after_evpn() -> None:
    cfg = T.load_config_or_empty()
    if cfg and cfg.get("evpn_password_mode") != "separate":
        cfg["evpn_password_mode"] = "separate"           # 따로 넣었으면 «따로 저장» 을 고른 것
        T.save_config(cfg)


def _after_riro() -> None:
    from riro_merit import config as RC
    cfg = RC.load()
    cfg["cred_rev"] = int(cfg.get("cred_rev") or 0) + 1   # 로그인 실패 잠금 풀기
    cfg["login_lock"] = {}
    RC.save(cfg)


def targets() -> dict:
    """서비스 이름은 «부를 때» 읽는다 (시험에서 바꿔 끼울 수 있게)."""
    from riro_merit import config as RC
    return {
        "cert": {"label": "나이스·에듀파인 인증서 암호", "service": T.KEYRING_SERVICE, "id": False, "replace": False,
                 "account": lambda: (T.load_config_or_empty().get("cert_name") or "").strip(), "after": None},
        "evpn": {"label": "EVPN 비밀번호 (인증서 암호와 다를 때)", "service": T.KEYRING_SERVICE, "id": False, "replace": False,
                 "account": lambda: "evpn", "after": _after_evpn},
        "riro": {"label": "리로스쿨 아이디·비밀번호", "service": RC.RIRO_SERVICE, "id": True, "replace": True,
                 "account": None, "after": _after_riro},
    }


def save(target: str, uid: str | None, buf: S.SecretBuffer) -> str:
    """금고에 넣는다. 돌려주는 것은 코드뿐. 버퍼는 부른 쪽이 지운다 (어느 길이든).

    'OK' = 금고 저장 + 뒤 설정 갱신 둘 다 됨.  'W_SETTINGS:<코드>' = «금고에는 저장됐는데» 설정 갱신만 실패
    (예: 리로스쿨 로그인 잠금 풀기). 금고 저장 자체가 실패하면 SecureError 가 그대로 올라간다 (금고는 그 전 상태 그대로)."""
    t = targets()[target]
    if not t["service"]:                   # 리로스쿨 학교 주소가 없으면 금고 이름도 없다 — 아무 데도 쓰지 않는다
        return "E_NO_SCHOOL"
    account = (uid or "").strip() if t["id"] else t["account"]()
    if not account:
        return "E_NO_ACCOUNT"
    if not len(buf):
        return "E_EMPTY"
    S.vault_write_buffer(t["service"], account, buf, replace=t["replace"])     # ← 여기까지 되면 «금고 저장됨»
    S.mark_vault(target, "saved")          # 「넣었다」 표시만 (값·아이디 없음) — 클로드 쪽 점검은 금고 대신 이걸 본다
    if t["after"]:
        try:
            t["after"]()
        except S.SecureError as e:
            return "W_SETTINGS:" + e.code
        except Exception as e:
            return "W_SETTINGS:E_" + type(e).__name__
    return "OK"


def write_status(path: str | None, target: str, status: str, code: str) -> None:
    if not path:
        return
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"target": target, "status": status, "code": code}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


# ───────────────────────────────────────────── 작은 창
class SecureEntry:
    """입력칸에는 ● 만 넣고, 진짜 글자는 SecretBuffer 에 한 자씩. 붙여넣기·복사·커서 이동은 막는다."""

    def __init__(self, parent, buf: S.SecretBuffer, on_change=None, width: int = 26):
        import tkinter as tk
        self.buf, self.on_change = buf, on_change
        self.e = tk.Entry(parent, width=width, show="●", relief="solid", bd=1)
        self.e.bind("<Key>", self.key)
        for seq in ("<<Paste>>", "<<Copy>>", "<<Cut>>", "<Control-v>", "<Shift-Insert>", "<Button-2>", "<Button-3>",
                    "<<PasteSelection>>"):
            self.e.bind(seq, lambda ev: "break")

    def key(self, ev):
        ks = getattr(ev, "keysym", "")
        if ks in ("Return", "KP_Enter", "Escape", "Tab", "ISO_Left_Tab"):
            return None                                     # 창 쪽 단축키(저장·취소·이동)로 넘긴다
        if ks == "BackSpace":
            self.buf.pop()
            self.e.delete(0, "end")
            self.e.insert(0, "•" * len(self.buf))
        else:
            ch = getattr(ev, "char", "")
            if ch and len(ch) == 1 and ch.isprintable():
                try:
                    self.buf.append(ch)
                    self.e.insert("end", "•")
                except ValueError:
                    pass
        if self.on_change:
            self.on_change()
        return "break"                                      # 진짜 글자는 입력칸에 들어가지 않는다


class PasswordWindow:
    def __init__(self, root, target: str, idle_sec: int = IDLE_SEC):
        import tkinter as tk
        self.root, self.target = root, target
        self.status, self.code = "cancelled", "CANCELLED"
        self.buf = S.SecretBuffer()
        t = targets()[target]
        root.title(f"비밀번호 저장 — {t['label']}")
        root.configure(bg="white")
        root.resizable(False, False)
        F = ("맑은 고딕", 10)
        tk.Label(root, text=t["label"], font=("맑은 고딕", 12, "bold"), bg="white").grid(
            row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 2))
        tk.Label(root, text="이 PC 의 Windows 자격 증명 관리자에만 저장합니다. 저장하면 이 창은 바로 닫힙니다.",
                 font=("맑은 고딕", 9), fg="#6b7280", bg="white").grid(row=1, column=0, columnspan=2, sticky="w", padx=16)
        r = 2
        self.e_id = None
        if t["id"]:
            tk.Label(root, text="아이디", font=F, bg="white").grid(row=r, column=0, sticky="w", padx=16, pady=6)
            self.e_id = tk.Entry(root, width=26, relief="solid", bd=1, font=F)
            try:
                old = S.vault_username(t["service"])            # 아이디만 (비밀번호는 안 꺼낸다)
            except Exception:
                old = None
            if old:
                self.e_id.insert(0, old)
            self.e_id.grid(row=r, column=1, padx=(0, 16), pady=6)
            r += 1
        tk.Label(root, text="비밀번호", font=F, bg="white").grid(row=r, column=0, sticky="w", padx=16, pady=6)
        self.se = SecureEntry(root, self.buf, on_change=self.changed)
        self.se.e.grid(row=r, column=1, padx=(0, 16), pady=6)
        r += 1
        self.msg = tk.Label(root, text="한/영 키가 «영»인지 확인하세요. 붙여넣기는 막아 두었습니다.",
                            font=("맑은 고딕", 9), fg="#6b7280", bg="white")
        self.msg.grid(row=r, column=0, columnspan=2, sticky="w", padx=16)
        b = tk.Frame(root, bg="white")
        b.grid(row=r + 1, column=0, columnspan=2, pady=(8, 14))
        tk.Button(b, text="저장", command=self.do_save, bg="#2563eb", fg="white", relief="flat", padx=18,
                  font=("맑은 고딕", 10, "bold")).pack(side="left", padx=6)
        tk.Button(b, text="취소", command=self.cancel, relief="flat", bg="#e5e7eb", padx=14).pack(side="left", padx=6)
        root.bind("<Return>", lambda e: self.do_save())
        root.bind("<Escape>", lambda e: self.cancel())
        root.protocol("WM_DELETE_WINDOW", self.cancel)
        self.idle_sec = idle_sec
        self._idle = root.after(idle_sec * 1000, self.timeout)
        (self.se.e if not (self.e_id and not self.e_id.get()) else self.e_id).focus_set()

    def changed(self):
        self.root.after_cancel(self._idle)
        self._idle = self.root.after(self.idle_sec * 1000, self.timeout)
        warn = self.buf.has_non_ascii()
        self.msg.config(text="한글(또는 특수 문자)이 들어갔어요 — 한/영 키를 확인하세요" if warn
                        else "한/영 키가 «영»인지 확인하세요. 붙여넣기는 막아 두었습니다.",
                        fg="#b45309" if warn else "#6b7280")

    def finish(self, status, code):
        self.status, self.code = status, code
        self.buf.wipe()
        try:
            self.se.e.delete(0, "end")
        except Exception:
            pass
        self.root.destroy()

    def do_save(self):
        uid = self.e_id.get() if self.e_id else None
        if self.e_id is not None and not (uid or "").strip():
            return self.msg.config(text="아이디를 넣어 주세요", fg="#b42318")
        if not len(self.buf):
            return self.msg.config(text="비밀번호를 넣어 주세요", fg="#b42318")
        try:
            code = save(self.target, uid, self.buf)
        except S.SecureError as e:
            return self.finish("failed", e.code)
        except Exception as e:
            return self.finish("failed", "E_" + type(e).__name__)      # 오류 원문은 쓰지 않는다
        if code.startswith("W_SETTINGS"):
            return self.finish("saved", code)        # 금고에는 저장됨 — 설정 갱신만 실패 (부르는 쪽이 따로 알린다)
        if code != "OK":
            self.buf.wipe()
            self.se.e.delete(0, "end")
            return self.msg.config(text={"E_NO_ACCOUNT": "설정에 인증서 이름이 없어요 — 설정창에서 먼저 고르고 [저장] 하세요",
                                         "E_EMPTY": "비밀번호를 넣어 주세요",
                                         "E_NO_SCHOOL": "리로스쿨 학교 주소가 아직 없어요 — 키트 폴더 «우리학교_설정.json» 에 "
                                                        "먼저 적어 주세요"}.get(code, code), fg="#b42318")
        self.finish("saved", "OK")

    def cancel(self):
        self.finish("cancelled", "CANCELLED")

    def timeout(self):
        self.finish("cancelled", "E_TIMEOUT")


def run_window(target: str, idle_sec: int = IDLE_SEC, hidden: bool = False) -> tuple[str, str]:
    """hidden=True 는 자동 시험 전용 — 창이 화면에 안 나타나므로 아무것도 입력할 수 없고, 시간 초과로만 끝난다."""
    import tkinter as tk
    root = tk.Tk()
    if hidden:
        root.withdraw()
    w = None
    try:
        w = PasswordWindow(root, target, idle_sec)
        if not hidden:
            root.update_idletasks()
            root.geometry("+%d+%d" % (max(0, (root.winfo_screenwidth() - root.winfo_width()) // 2),
                                      max(0, (root.winfo_screenheight() - root.winfo_height()) // 3)))
            root.lift()
            root.attributes("-topmost", True)
            root.after(600, lambda: root.winfo_exists() and root.attributes("-topmost", False))
        root.mainloop()
        return w.status, w.code
    finally:
        if w is not None:
            w.buf.wipe()


# ───────────────────────────────────────────── 검은 창
def run_console(target: str) -> tuple[str, str]:
    try:
        sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        pass
    T.prepare_console_window()
    t = targets()[target]
    print(f"── {t['label']} ──  (이 창에만 칩니다 · Esc = 취소)")
    uid = None
    if t["id"]:
        old = S.vault_username(t["service"])
        uid = (input(f"아이디{' [' + old + ']' if old else ''}: ").strip() or old or "")
        if not uid:
            return "cancelled", "CANCELLED"
    with S.SecretBuffer() as buf:
        try:
            if not S.read_secret_console("비밀번호 (화면에 안 보입니다): ", buf):
                return "cancelled", "CANCELLED"
        except KeyboardInterrupt:
            return "cancelled", "CANCELLED"
        try:
            code = save(target, uid, buf)
        except S.SecureError as e:
            code = e.code
        except Exception as e:
            code = "E_" + type(e).__name__
    status = "saved" if code == "OK" or code.startswith("W_SETTINGS") else "failed"
    print("저장했습니다. 창이 곧 닫힙니다." if status == "saved" else f"저장하지 못했습니다 ({code}).")
    import time
    time.sleep(1.5)
    return status, code


def child(target: str, console: bool, status_file: str | None, idle_sec: int, hidden: bool = False) -> int:
    status, code = "failed", "E_START"
    try:
        status, code = run_console(target) if console else run_window(target, idle_sec, hidden)
    except S.SecureError as e:
        status, code = "failed", e.code
    except Exception as e:
        status, code = "failed", "E_" + type(e).__name__
    finally:
        write_status(status_file, target, status, code)
    return EXIT.get(status, 1)


# ───────────────────────────────────────────── 부르는 쪽 (클로드·설정창·위젯)
def request(target: str, console: bool = False, timeout: int = 600) -> dict:
    """비밀번호 창을 띄우고 끝날 때까지 기다린다. 돌려주는 것: {'target','status','code'} — 값 없음."""
    if target not in targets():
        return {"target": target, "status": "failed", "code": "E_TARGET"}
    status_file = Path(tempfile.gettempdir()) / f"neis_pw_status_{secrets.token_hex(6)}.json"
    venv = KIT / ".venv" / "Scripts"
    if console:
        exe = venv / "python.exe" if (venv / "python.exe").exists() else Path(sys.executable)
        cmd = [str(exe), "-X", "utf8", str(Path(__file__).resolve()), "child", "--target", target, "--console",
               "--status-file", str(status_file)]
        flags = subprocess.CREATE_NEW_CONSOLE
    else:
        exe = venv / "pythonw.exe" if (venv / "pythonw.exe").exists() else Path(sys.executable).with_name("pythonw.exe")
        cmd = [str(exe), str(Path(__file__).resolve()), "child", "--target", target, "--status-file", str(status_file)]
        flags = 0x08000000                                  # CREATE_NO_WINDOW (작은 창은 tk 가 직접 그린다)
    proc = subprocess.Popen(cmd, cwd=str(KIT), creationflags=flags, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        rc = None
    try:
        res = json.loads(status_file.read_text(encoding="utf-8"))
    except Exception:
        res = {"target": target, "status": "cancelled" if rc is None else "failed",
               "code": "E_TIMEOUT" if rc is None else f"E_NO_STATUS_{rc}"}
    for p in (status_file, status_file.with_name(status_file.name + ".tmp")):
        try:
            p.unlink()
        except OSError:
            pass
    return {"target": res.get("target", target), "status": res.get("status"), "code": res.get("code")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="비밀번호 한 가지를 금고에 넣고 바로 끝난다")
    ap.add_argument("mode", choices=["open", "child"])
    ap.add_argument("--target", required=True, choices=["cert", "evpn", "riro"])
    ap.add_argument("--console", action="store_true")
    ap.add_argument("--status-file")
    ap.add_argument("--idle", type=int, default=IDLE_SEC)
    ap.add_argument("--hidden-test", action="store_true", help=argparse.SUPPRESS)   # 자동 시험 전용 (입력 불가)
    a = ap.parse_args(argv)
    if a.mode == "child":
        return child(a.target, a.console, a.status_file, a.idle, a.hidden_test)
    res = request(a.target, a.console)
    print("RESULT_JSON " + json.dumps(res, ensure_ascii=False), flush=True)
    return EXIT.get(res.get("status"), 1)


if __name__ == "__main__":
    sys.exit(main())

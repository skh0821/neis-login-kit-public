# -*- coding: utf-8 -*-
"""보안 설정창 — 선생님이 직접 넣는 로컬 창 (검은 창·클로드 대화와 분리).

  .\\.venv\\Scripts\\pythonw.exe secure_settings.pyw            (보통은 python secure_settings.py 가 띄운다)

넣는 곳
  [나이스·에듀파인]  지역 · 학교 업무망/EVPN · 인증서와 암호 · EVPN 주소·아이디·비밀번호 방식
                    — 기존 값은 그대로 두고, 빠졌거나 잘못된 칸만 빨갛게 표시한다
  [리로스쿨 상벌점]  리로스쿨 아이디·비밀번호 · 로그인 시험 · 디벗 연결 · 구글 시트 · 학교 규칙(확인 전/후)
  [계정 관리]        이 PC 에서 지우기 / 서비스 쪽 세션·권한 해제 — 둘을 따로 누른다

밖(클로드 쪽)으로 나가는 것은 --result 파일의 «무엇을 저장/삭제/시험했고 결과 코드가 무엇인지» 뿐이다. 값은 쓰지 않는다.
★ 이 창에는 비밀번호 칸이 없다 (2026-11-09). [암호 넣기] 단추가 비밀번호 전용 짧은 창(secure_password.py)을 띄우고,
  그 창이 금고에 넣고 바로 닫힌 뒤 «저장/취소/실패» 상태만 돌려준다. 이 창(오래 떠 있는 프로그램)은 비밀번호를 만지지 않는다.
  로그인 시험도 하위 프로그램이 금고에서 «로그인 직전에» 직접 꺼낸다 — 이 창을 거치지 않는다.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))
os.chdir(KIT)

import secure_store as S  # noqa: E402
import teacher_config as T  # noqa: E402
from riro_merit import config as RC  # noqa: E402
import windows_hello as H  # noqa: E402

REGION_CODES = {"서울": "sen", "부산": "pen", "대구": "dge", "인천": "ice", "광주": "gen", "대전": "dje", "울산": "use",
                "세종": "sje", "경기": "goe", "강원": "gwe", "충북": "cbe", "충남": "cne", "전북": "jbe", "전남": "jne",
                "경북": "gbe", "경남": "gne", "제주": "jje"}
PY = KIT / ".venv" / "Scripts" / "python.exe"
if not PY.exists():
    PY = Path(sys.executable).with_name("python.exe")
NO_WINDOW = 0x08000000
FONT, SMALL, BOLD = ("맑은 고딕", 10), ("맑은 고딕", 9), ("맑은 고딕", 10, "bold")
RED, GREEN, SUB, ORANGE = "#b42318", "#15803d", "#6b7280", "#b45309"
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def gpki_names():
    folder = Path(r"C:\GPKI\Certificate\class2")
    names = []
    if folder.is_dir():
        for f in sorted(folder.glob("*_sig.cer")):
            m = re.search(r"[가-힣]+", f.name)
            if m and m.group(0) not in names:
                names.append(m.group(0))
    return names


def evpn_url_for(code):
    return "https://evpn.gbe.kr/" if code == "gbe" else f"https://evpn.{code}.go.kr/"


class Result:
    """클로드 쪽에 넘길 결과 — 항목 이름·동작·코드만."""

    def __init__(self, path):
        self.path = Path(path) if path else None
        self.events, self.status = [], "open"
        self.write()

    def add(self, item, action, code="OK", **extra):
        safe = {k: v for k, v in extra.items() if k in ("changed", "missing")}
        self.events.append({"t": time.strftime("%H:%M:%S"), "item": item, "action": action, "code": code, **safe})
        self.write()

    def write(self):
        if not self.path:
            return
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"status": self.status, "events": self.events}, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass


def start_tool(args, *, stdin=subprocess.DEVNULL):
    return subprocess.Popen([str(PY), "-X", "utf8", *args], stdin=stdin, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                            creationflags=NO_WINDOW, cwd=str(KIT))


def finish_tool(p, timeout, *, lifetime=None):
    """RESULT_JSON/LOGIN_OK 줄만 골라 결과를 돌려준다 (출력 원문은 쓰지 않는다)."""
    with p:
        try:
            stdout, _ = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if lifetime is not None:
                lifetime.close()
            p.kill()
            p.communicate()
            return {"ok": False, "code": "E_TIMEOUT"}
    lines = (stdout or "").splitlines()
    res = None
    for ln in lines:
        if ln.startswith("RESULT_JSON "):
            try:
                res = json.loads(ln[12:])
            except ValueError:
                pass
    if any(ln.strip() == "LOGIN_OK" for ln in lines):
        res = {**(res or {}), "ok": True, "code": "OK_LOGIN"} if not res or res.get("connected", True) else res
    if res is None:
        fail = next((ln for ln in lines if ln.startswith("LOGIN_FAIL")), "")
        res = {"ok": p.returncode == 0, "code": fail.split()[-1] if fail else f"EXIT_{p.returncode}"}
    return res


def run_tool(args, timeout):
    return finish_tool(start_tool(args), timeout)


class App:
    def __init__(self, root, result, tab):
        self.root, self.R = root, result
        self.divot_lifetime = None
        self.vault_ok, self.vault_code = True, ""
        try:
            S.check_vault(functional=True)
        except S.SecureError as e:
            self.vault_ok, self.vault_code = False, e.code
            self.R.add("vault", "check", e.code)
        try:
            self.old = T.load_config_or_empty()
        except Exception:
            self.old = {}
        root.title("보안 설정 — 나이스·에듀파인·리로스쿨")
        root.configure(bg="white")
        root.option_add("*Font", FONT)
        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        self.tabs = {}
        for key, title, build in (("neis", " 나이스·에듀파인 ", self.build_neis), ("riro", " 리로스쿨 상벌점 ", self.build_riro),
                                  ("accounts", " 계정 관리 ", self.build_accounts)):
            f = tk.Frame(nb, bg="white")
            nb.add(f, text=title)
            self.tabs[key] = f
            build(f)
        if tab in self.tabs:
            nb.select(self.tabs[tab])
        self.status = tk.Label(root, text="", fg=SUB, bg="white", font=SMALL, anchor="w", wraplength=640, justify="left")
        self.status.pack(fill="x", padx=14)
        if not self.vault_ok:
            self.say(f"⚠ Windows 자격 증명 관리자를 쓸 수 없어 비밀번호는 저장하지 않습니다 ({self.vault_code})", RED)
        tk.Button(root, text="닫기", command=self.close, relief="flat", bg="#e5e7eb", padx=14).pack(pady=(4, 10))
        root.protocol("WM_DELETE_WINDOW", self.close)

    # ── 공통 ──
    def say(self, text, color=SUB):
        self.status.config(text=text, fg=color)

    def row(self, parent, r, label, widget, hint="", hint_color=SUB):
        tk.Label(parent, text=label, bg="white", anchor="w").grid(row=r, column=0, sticky="w", padx=(12, 6), pady=3)
        widget.grid(row=r, column=1, sticky="w", pady=3)
        h = tk.Label(parent, text=hint, fg=hint_color, bg="white", font=SMALL, anchor="w", justify="left", wraplength=300)
        h.grid(row=r, column=2, sticky="w", padx=6)
        return h

    def entry(self, parent, value="", width=26, secret=False):
        e = tk.Entry(parent, width=width, relief="solid", bd=1, show="●" if secret else "")
        if value:
            e.insert(0, value)
        return e

    def mark(self, hint, ok, msg_bad, msg_ok=""):
        hint.config(text=msg_ok if ok else msg_bad, fg=SUB if ok else RED)
        return ok

    def bg(self, fn, done, *, daemon=True):
        """오래 걸리는 일은 뒤 스레드에서 — 창이 멈추지 않게. 결과는 «창 스레드»가 받아 처리한다
        (tk 는 다른 스레드에서 부르면 안전하지 않다 → 뒤 스레드는 상자에 넣기만, 창은 0.2초마다 꺼내 본다)."""
        box = {}

        def work():
            try:
                box["res"] = fn()
            except Exception as e:
                box["res"] = {"ok": False, "status": "failed", "code": "E_" + type(e).__name__}

        th = threading.Thread(target=work, daemon=daemon)
        th.start()

        def poll():
            if th.is_alive():
                self.root.after(200, poll)
            else:
                done(box.get("res") or {"ok": False, "code": "E_NO_RESULT"})
        self.root.after(200, poll)

    def close(self):
        self._close_divot_pipe()
        saved = any(e["action"] in ("saved", "deleted", "revoked") and str(e["code"]).startswith("OK") for e in self.R.events)
        self.R.status = "saved" if saved else ("cancelled" if not self.R.events else "closed")
        self.R.write()
        self.root.destroy()

    # ── 나이스·에듀파인 ──
    def build_neis(self, f):
        o = self.old
        tk.Label(f, text="지금 값은 그대로 둡니다. 빨간 글씨가 있는 칸만 고치거나 채워 주세요.", bg="white", fg=SUB,
                 font=SMALL).grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(10, 4))
        code = (re.match(r"https?://([a-z]{3})\.neis", o.get("neis_url") or "") or [None, "sen"])[1]
        name = next((k for k, v in REGION_CODES.items() if v == code), "서울")
        self.v_region = ttk.Combobox(f, values=list(REGION_CODES), state="readonly", width=12)
        self.v_region.set(name)
        self.h_region = self.row(f, 1, "① 지역(교육청)", self.v_region, "나이스 주소는 이 지역으로 정해집니다")

        fr = tk.Frame(f, bg="white")
        self.v_access = tk.StringVar(value=o.get("access_mode") or "")
        for val, txt in (("school_net", "학교 업무망(랜선)"), ("evpn", "EVPN")):
            tk.Radiobutton(fr, text=txt, variable=self.v_access, value=val, bg="white",
                           command=self.toggle_evpn).pack(side="left", padx=(0, 10))
        hint = "" if o.get("access_mode") else ("아직 안 정했어요 — EVPN 아이디가 있어 EVPN 일 것 같아요. 맞게 골라 주세요"
                                                if o.get("evpn_user") else "아직 안 정했어요 — 골라 주세요")
        self.h_access = self.row(f, 2, "② 접속 방식", fr, hint, RED if hint else SUB)

        names = gpki_names()
        self.v_cert = ttk.Combobox(f, values=names, width=24)
        self.v_cert.set(o.get("cert_name") or (names[0] if names else ""))
        self.h_cert = self.row(f, 3, "③ 인증서 이름", self.v_cert,
                               "이 PC 인증서에서 고르세요" if names else "이 PC(C:\\GPKI)에서 인증서를 못 찾았어요")
        has_cert_pw = self.has_pw(T.KEYRING_SERVICE, o.get("cert_name"))
        # ★ 비밀번호 칸은 이 창에 두지 않는다 — 단추가 «비밀번호 전용 짧은 창»(secure_password.py)을 띄우고 상태만 받는다
        self.b_certpw = tk.Button(f, text="암호 넣기/바꾸기", relief="flat", bg="#e0e7ff",
                                  command=lambda: self.ask_password("cert", self.b_certpw, self.h_certpw, "cert_password"))
        self.h_certpw = self.row(f, 4, "    인증서 암호", self.b_certpw,
                                 "저장돼 있어요" if has_cert_pw else "저장된 암호가 없어요 — 단추를 눌러 넣어 주세요",
                                 SUB if has_cert_pw else RED)

        self.evf = tk.LabelFrame(f, text=" EVPN ", bg="white", padx=6, pady=4)
        self.evf.grid(row=6, column=0, columnspan=3, sticky="we", padx=10, pady=6)
        self.v_evurl = self.entry(self.evf, o.get("evpn_url") or evpn_url_for(code), 30)
        self.row(self.evf, 0, "EVPN 주소", self.v_evurl, "지역 기본값이에요. 다르면 고치세요")
        self.v_evid = self.entry(self.evf, o.get("evpn_user") or "", 20)
        self.h_evid = self.row(self.evf, 1, "EVPN 아이디", self.v_evid, "")
        frm = tk.Frame(self.evf, bg="white")
        mode = o.get("evpn_password_mode") or ("separate" if self.has_pw(T.KEYRING_SERVICE, "evpn") else "")
        self.v_evmode = tk.StringVar(value=mode)
        tk.Radiobutton(frm, text="따로 저장(권장)", variable=self.v_evmode, value="separate", bg="white").pack(side="left")
        tk.Radiobutton(frm, text="인증서 암호와 같음", variable=self.v_evmode, value="same_as_cert", bg="white").pack(side="left")
        self.h_evmode = self.row(self.evf, 2, "비밀번호 방식", frm,
                                 "" if mode else "골라 주세요 — 고르지 않으면 인증서 암호를 대신 쓰지 않아요", SUB if mode else RED)
        has_ev = self.has_pw(T.KEYRING_SERVICE, "evpn")
        self.b_evpw = tk.Button(self.evf, text="EVPN 비밀번호 넣기/바꾸기", relief="flat", bg="#e0e7ff",
                                command=lambda: self.ask_password("evpn", self.b_evpw, self.h_evpw, "evpn_password"))
        self.h_evpw = self.row(self.evf, 3, "EVPN 비밀번호", self.b_evpw,
                               "저장돼 있어요" if has_ev else "«따로 저장» 이면 단추를 눌러 넣어 주세요")

        bb = tk.Frame(f, bg="white")
        bb.grid(row=9, column=0, columnspan=3, pady=8)
        tk.Button(bb, text="저장", command=self.save_neis, bg="#2563eb", fg="white", relief="flat", padx=18,
                  font=BOLD).pack(side="left", padx=6)
        self.b_neistest = tk.Button(bb, text="나이스 로그인 시험", command=self.test_neis, relief="flat", bg="#e5e7eb", padx=10)
        self.b_neistest.pack(side="left", padx=6)
        self.toggle_evpn()

    def has_pw(self, svc, user):
        """저장돼 있는지만 — 금고에서 비밀번호 내용을 꺼내지 않는다."""
        if not (self.vault_ok and user):
            return False
        try:
            return S.vault_exists(svc, user)
        except S.SecureError:
            return False

    def ask_password(self, target, button, hint, item):
        """비밀번호 전용 짧은 창을 띄우고 «저장/취소/실패» 상태만 받는다. 이 창은 비밀번호를 만지지 않는다."""
        if not self.vault_ok:
            self.say(f"금고를 쓸 수 없어 비밀번호를 받지 않습니다 ({self.vault_code})", RED)
            return
        if target == "cert":
            saved = (T.load_config_or_empty().get("cert_name") or "").strip()
            if not saved or saved != self.v_cert.get().strip():
                self.say("인증서 이름을 고르셨으면 먼저 [저장]을 누른 뒤 암호를 넣어 주세요", RED)
                return
        button.configure(state="disabled")
        self.say("비밀번호 창이 떴습니다 — 그 창에 넣고 [저장] 해 주세요 (취소하면 그냥 닫힙니다)")
        import secure_password as P

        def done(res):
            button.configure(state="normal")
            st, code = res.get("status"), res.get("code")
            self.R.add(item, st or "failed", code or "E_UNKNOWN")
            if st == "saved" and str(code).startswith("W_SETTINGS"):
                hint.config(text="저장돼 있어요", fg=SUB)
                self.say(f"⚠ 비밀번호는 금고에 저장됐지만, 설정 갱신은 못 했어요 ({code.split(':', 1)[-1]}). "
                         "리로스쿨이면 로그인 잠금이 안 풀렸을 수 있어요 — 한 번 더 저장해 주세요", ORANGE)
            elif st == "saved":
                hint.config(text="저장돼 있어요", fg=SUB)
                self.say("✅ 비밀번호를 Windows 금고에 저장했어요", GREEN)
            elif st == "cancelled":
                self.say("취소했어요 (바뀐 것 없음)" if code == "CANCELLED" else "시간이 지나 창을 닫았어요 (바뀐 것 없음)", SUB)
            else:
                self.say(f"저장하지 못했어요 ({code})", RED)
        self.bg(lambda: P.request(target), done)

    def toggle_evpn(self):
        st = "normal" if self.v_access.get() == "evpn" else "disabled"
        for w in self.evf.winfo_children():
            for x in [w, *w.winfo_children()]:
                try:
                    x.configure(state=st)
                except tk.TclError:
                    pass

    def save_neis(self):
        o = self.old
        ok = True
        access = self.v_access.get()
        ok &= self.mark(self.h_access, access in ("school_net", "evpn"), "학교 업무망 / EVPN 중 골라 주세요")
        cert = self.v_cert.get().strip()
        ok &= self.mark(self.h_cert, bool(cert), "인증서 이름을 골라 주세요", "")
        evid, evmode = self.v_evid.get().strip(), self.v_evmode.get()
        if access == "evpn":
            ok &= self.mark(self.h_evid, bool(evid), "EVPN 아이디를 넣어 주세요")
            ok &= self.mark(self.h_evmode, evmode in ("separate", "same_as_cert"), "비밀번호 방식을 골라 주세요")
        if not ok:
            self.say("빨간 글씨 칸을 확인해 주세요. 아무것도 저장하지 않았어요.", RED)
            return
        if access == "evpn" and evmode == "same_as_cert" and o.get("evpn_password_mode") != "same_as_cert":
            if not messagebox.askyesno("확인", "EVPN 비밀번호로 «인증서 암호»를 쓰도록 고르셨어요.\n"
                                       "두 비밀번호가 정말 같을 때만 [예]를 눌러 주세요."):
                return
        try:
            # 설정 (바뀐 칸만). 비밀번호는 이 창에서 다루지 않는다 — [암호 넣기] 단추가 따로 띄우는 짧은 창이 맡는다
            if not T.is_split_mode() and T.CONFIG_FILE.exists():
                import secure_migrate
                import contextlib
                import io
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = secure_migrate.run()
                self.R.add("migration", "run", "OK" if rc == 0 else "E_MIGRATION")
                if rc != 0:
                    self.say("보호 저장으로 옮기다 실패해 설정은 저장하지 않았어요 (되돌려 둠). 클로드에게 알려 주세요.", RED)
                    return
                self.old = o = T.load_config_or_empty()
            code = REGION_CODES[self.v_region.get()]
            new = dict(o)
            old_code = (re.match(r"https?://([a-z]{3})\.neis", o.get("neis_url") or "") or [None, None])[1]
            if old_code != code:          # 지역이 바뀐 때만 주소를 새로 — 같은 지역이면 기존 주소 그대로
                new["neis_url"] = f"https://{code}.neis.go.kr/jsp/main.jsp"
            new.update({"region": self.v_region.get(),
                        "access_mode": access, "cert_name": cert})
            if access == "evpn":
                new.update({"evpn_user": evid, "evpn_password_mode": evmode})
                url = self.v_evurl.get().strip()
                if url and url != evpn_url_for(code):
                    new["evpn_url"] = url
                else:
                    new.pop("evpn_url", None)
            changed = sorted(k for k in set(new) | set(o) if new.get(k) != o.get(k))
            missing = []
            if not self.has_pw(T.KEYRING_SERVICE, cert):
                missing.append("인증서 암호")
                self.h_certpw.config(text="저장된 암호가 없어요 — 단추를 눌러 넣어 주세요", fg=RED)
            if access == "evpn" and evmode == "separate" and not self.has_pw(T.KEYRING_SERVICE, "evpn"):
                missing.append("EVPN 비밀번호")
            if changed:
                T.save_config(new)
                back = T.load_merged()
                same = all(back.get(k) == new.get(k) for k in new if k != T.SECURE_MARKER)
                self.R.add("neis_settings", "saved", "OK" if same else "E_VERIFY", changed=changed)
                self.old = back
                self.say(f"✅ 저장했어요 (바뀐 칸 {len(changed)}개)."
                         + (f" 아직 없는 것: {', '.join(missing)} — 단추로 넣어 주세요" if missing else ""), GREEN if not missing else ORANGE)
            else:
                self.say("✅ 바뀐 칸이 없어요." + (f" 아직 없는 것: {', '.join(missing)}" if missing else ""), GREEN if not missing else ORANGE)
        except S.SecureError as e:
            self.R.add("neis_settings", "saved", e.code)
            self.say(f"저장하지 못했어요 ({e.code})", RED)
        except Exception as e:
            self.R.add("neis_settings", "saved", "E_" + type(e).__name__)
            self.say(f"저장하지 못했어요 (E_{type(e).__name__})", RED)

    def test_neis(self):
        cfg = T.load_config_or_empty()
        access = cfg.get("access_mode")
        if access not in ("school_net", "evpn"):
            self.say("먼저 접속 방식을 골라 [저장] 해 주세요", RED)
            return
        self.b_neistest.configure(state="disabled")
        self.say("나이스 로그인 시험 중… 크롬 창이 잠깐 뜹니다 (EVPN 이면 연결·끊기까지 3~6분)")
        args = (["run_vpn.py", "--minutes", "4", "--", "python", "-X", "utf8", "neis_session.py", "--login-test"]
                if access == "evpn" else ["neis_session.py", "--login-test"])

        def done(res):
            self.b_neistest.configure(state="normal")
            ok = bool(res.get("ok")) and res.get("code") in ("OK_LOGIN",)
            if access == "evpn" and "work_exit" in res:
                ok = res.get("connected") and res.get("work_exit") == 0
                res["code"] = "OK_LOGIN" if ok else ("E_EVPN_CONNECT" if not res.get("connected") else f"E_NEIS_LOGIN_EXIT_{res.get('work_exit')}")
            self.R.add("neis_login", "test", res.get("code"))
            self.say("✅ 나이스 로그인 확인" if ok else f"나이스 로그인 안 됨 ({res.get('code')})", GREEN if ok else RED)
        self.bg(lambda: run_tool(args, 480), done)

    # ── 리로스쿨 ──
    def build_riro(self, f):
        try:
            self.rc = RC.load()
        except S.SecureError as e:
            self.rc = dict(RC.DEFAULTS)
            self.R.add("riro_config", "load", e.code)
        has = {"id": False, "password": False}
        if self.vault_ok:
            try:
                has = RC.has_credentials()
            except S.SecureError:
                pass
        where = (f"학교 리로스쿨: {RC.SCHOOL_HOST}   ·   금고: 이 PC 전용 이름" if RC.SCHOOL_HOST else
                 "학교 리로스쿨 주소가 아직 없어요 — 키트 폴더 «우리학교_설정.json» 에 적어 주세요 (클로드에게 말하면 적어 줍니다)")
        tk.Label(f, text=where, bg="white", fg=SUB if RC.SCHOOL_HOST else RED,
                 font=SMALL).grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(10, 4))
        self.b_rcred = tk.Button(f, text="아이디·비밀번호 넣기/바꾸기", relief="flat", bg="#e0e7ff",
                                 command=lambda: self.ask_password("riro", self.b_rcred, self.h_rpw, "riro_credentials"))
        ok_both = has["id"] and has["password"]
        self.h_rpw = self.row(f, 1, "리로스쿨 계정", self.b_rcred, "저장돼 있어요" if ok_both else "아직 없어요 — 단추를 눌러 넣어 주세요",
                              SUB if ok_both else RED)
        b = tk.Frame(f, bg="white")
        b.grid(row=3, column=1, columnspan=2, sticky="w", pady=4)
        self.b_rtest = tk.Button(b, text="로그인 시험", command=self.test_riro, relief="flat", bg="#e5e7eb", padx=10)
        self.b_rtest.pack(side="left")
        tk.Label(f, text="로그인 시험은 [로그인]을 한 번만 누릅니다. 추가 인증(OTP)이나 보안문자가 뜨면 열린 창에서 직접 넣어 주세요.\n"
                         "비밀번호가 틀렸다는 답이 오면 새로 저장하기 전까지 다시 시도하지 않습니다.",
                 bg="white", fg=SUB, font=SMALL, justify="left").grid(row=4, column=0, columnspan=3, sticky="w", padx=12)

        ttk.Separator(f).grid(row=5, column=0, columnspan=3, sticky="we", pady=8, padx=8)
        self.v_divot = self.entry(f, self.rc.get("divot_url") or RC.DIVOT_URL, 34)
        self.row(f, 6, "디벗 주소", self.v_divot, "저장된 로그인은 자동으로 사용합니다.")
        self.b_divot = tk.Button(f, text="디벗 연결", command=self.connect_divot, relief="flat", bg="#e5e7eb")
        self.b_divot.grid(row=7, column=1, columnspan=2, sticky="w")
        self.v_sheet = self.entry(f, self.rc.get("sheet_id") or "", 34)
        self.row(f, 8, "구글 시트 주소/ID", self.v_sheet, "학교에서 허용한 시트 (이름 열 없이 학번·날짜·항목·점수·상태만)")
        sb = tk.Frame(f, bg="white")
        sb.grid(row=9, column=1, columnspan=2, sticky="w", pady=4)
        has_client = (KIT / "oauth_client.json").exists()
        self.b_gs = tk.Button(sb, text="구글 시트 연결", command=self.connect_sheet, relief="flat", bg="#e0e7ff",
                              state="normal" if has_client else "disabled")
        self.b_gs.pack(side="left", padx=(0, 6))
        tk.Button(sb, text="시트 접근 확인", command=self.check_sheet, relief="flat", bg="#e5e7eb").pack(side="left")
        if not has_client:
            tk.Label(f, text="구글 열쇠(oauth_client.json)가 없어 시트 연결은 아직 못 해요 (권한은 «시트» 하나만 요청할 예정)",
                     bg="white", fg=ORANGE, font=SMALL).grid(row=10, column=0, columnspan=3, sticky="w", padx=12)

        rf = tk.LabelFrame(f, text=" 우리 학교 기준 (확인 전에는 등록 단계가 돌지 않아요) ", bg="white", padx=6, pady=4)
        rf.grid(row=11, column=0, columnspan=3, sticky="we", padx=10, pady=8)
        r = self.rc.get("rules") or {}
        self.v_item = self.entry(rf, r.get("item") or "", 28)
        self.row(rf, 0, "벌점 항목", self.v_item, "리로스쿨 항목 이름 그대로")
        self.v_points = self.entry(rf, "" if r.get("points") is None else str(r.get("points")), 6)
        self.row(rf, 1, "점수", self.v_points, "")
        self.v_cut = self.entry(rf, r.get("cutoff_time") or "", 8)
        self.row(rf, 2, "미보관 확정 시각", self.v_cut, "예: 09:00 — 이 시각 이후 목록만 확정")
        self.v_excl = self.entry(rf, ", ".join(r.get("exclusions") or []), 28)
        self.row(rf, 3, "제외 조건", self.v_excl, "쉼표로 (예: 출석인정결석, 조퇴)")
        self.v_conf = tk.BooleanVar(value=bool(r.get("confirmed")))
        tk.Checkbutton(rf, text="학교 기준을 확인했습니다", variable=self.v_conf, bg="white").grid(row=4, column=1, sticky="w")
        tk.Button(f, text="리로스쿨 설정 저장 (주소·시트·기준)", command=self.save_riro_cfg, relief="flat", bg="#2563eb",
                  fg="white", padx=10).grid(row=12, column=1, sticky="w", pady=(0, 8))

    def _tool_button(self, btn, item, args, timeout, wait_msg, ok_msg, *, run=None):
        btn.configure(state="disabled")
        self.say(wait_msg)

        def done(res):
            btn.configure(state="normal")
            self.R.add(item, "test" if "test" in item or "check" in " ".join(args) else "connect", res.get("code"))
            ok = bool(res.get("ok"))
            self.say(ok_msg + f" ({res.get('code')})" if ok else f"안 됐어요 ({res.get('code')})", GREEN if ok else RED)
        if run is None:
            self.bg(lambda: run_tool(args, timeout), done)
        else:
            self.bg(lambda: run(timeout), done, daemon=False)

    def test_riro(self):
        self._tool_button(self.b_rtest, "riro_login", ["-m", "riro_merit.riro_site", "login-test"], 480,
                          "리로스쿨 로그인 시험 중… 크롬 창이 뜹니다", "✅ 리로스쿨 로그인 확인")

    def connect_divot(self, *, use_saved=False):
        if self.b_divot.cget("state") == "disabled" or (not use_saved and not self._save_riro_cfg(quiet=True)):
            return
        args = ["-m", "riro_merit.divot", "connect", "--cancel-on-eof"]
        try:
            process = start_tool(args, stdin=subprocess.PIPE)
        except OSError:
            self.say("디벗 연결 프로그램을 열지 못했습니다.", RED)
            return
        lifetime = self.divot_lifetime = process.stdin
        process.stdin = None  # communicate()가 닫지 않도록 설정창에서 파이프를 관리한다.

        def run(timeout):
            with lifetime:
                return finish_tool(process, timeout, lifetime=lifetime)

        self._tool_button(self.b_divot, "divot_connect", args, 650,
                          "디벗에 연결 중입니다. 구글 인증창이 열리면 학교 계정으로 진행해 주세요.",
                          "✅ 디벗 연결 확인 — 아래 학교 기준을 입력하고 저장해 주세요", run=run)

    def _close_divot_pipe(self):
        if self.divot_lifetime is not None:
            try:
                self.divot_lifetime.close()
            except OSError:
                pass
            self.divot_lifetime = None

    def connect_sheet(self):
        self._save_riro_cfg(quiet=True)
        self._tool_button(self.b_gs, "google_sheet", ["-m", "riro_merit.google_sheet", "connect"], 320,
                          "브라우저에서 구글 로그인·동의해 주세요 (시트 권한만 요청)", "✅ 구글 시트 연결")

    def check_sheet(self):
        self._save_riro_cfg(quiet=True)
        self.say("시트 접근 확인 중…")
        self.bg(lambda: run_tool(["-m", "riro_merit.google_sheet", "check"], 60),
                lambda res: (self.R.add("google_sheet", "check", res.get("code")),
                             self.say(("✅ " if res.get("ok") else "") + f"시트 확인: {res.get('code')}"
                                      + (f" · 요청 탭 {'있음' if res.get('request_tab_exists') else '없음'}" if 'request_tab_exists' in res else ""),
                                      GREEN if res.get("ok") else RED)))

    def _save_riro_cfg(self, quiet=False):
        sheet = self.v_sheet.get().strip()
        m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})", sheet)
        sid = m.group(1) if m else sheet
        if sid and not re.fullmatch(r"[A-Za-z0-9_-]{20,}", sid):
            self.say("구글 시트 주소(또는 ID)가 아닌 것 같아요", RED)
            return False
        pts = self.v_points.get().strip()
        if pts and not re.fullmatch(r"-?\d{1,3}", pts):
            self.say("점수는 숫자로 넣어 주세요", RED)
            return False
        cut = self.v_cut.get().strip()
        if cut and not TIME_RE.match(cut):
            self.say("미보관 확정 시각은 09:00 처럼 넣어 주세요", RED)
            return False
        cfg = RC.load()
        cfg.update({"divot_url": self.v_divot.get().strip() or RC.DIVOT_URL, "sheet_id": sid})
        cfg["rules"] = {**cfg["rules"], "item": self.v_item.get().strip(), "points": int(pts) if pts else None,
                        "cutoff_time": cut, "exclusions": [x.strip() for x in self.v_excl.get().split(",") if x.strip()],
                        "confirmed": bool(self.v_conf.get())}
        if cfg["rules"]["confirmed"] and not (cfg["rules"]["item"] and pts and cut):
            self.say("기준을 «확인했습니다» 로 하려면 항목·점수·확정 시각을 모두 넣어 주세요", RED)
            return False
        try:
            RC.save(cfg)
        except S.SecureError as e:
            self.say(f"저장하지 못했어요 ({e.code})", RED)
            return False
        if not quiet:
            self.R.add("riro_config", "saved", changed=["divot_url", "sheet_id", "rules"])
            self.say("✅ 리로스쿨 설정(주소·시트·기준)을 저장했어요 — 비밀이 아닌 값만 일반 설정 파일에", GREEN)
        return True

    def save_riro_cfg(self):
        self._save_riro_cfg()

    # ── 계정 관리 ──
    def build_accounts(self, f):
        tk.Label(f, text="«이 PC 에서 지우기» 는 이 컴퓨터에 저장된 것만 지웁니다.\n"
                         "«서비스 쪽 해제» 는 사이트·구글에 남은 로그인/권한을 끊습니다. 필요하면 둘 다 누르세요.",
                 bg="white", fg=SUB, font=SMALL, justify="left").grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(10, 6))
        rows = [
            ("나이스 인증서 암호", self.del_cert, None, "서비스 쪽 해제 없음 (인증서 자체는 그대로)"),
            ("EVPN 비밀번호", lambda: self.del_vault(T.KEYRING_SERVICE, "evpn", "evpn_password", mark="evpn"), None,
             "EVPN 포털 세션은 «VPN 끊기» 로"),
            ("리로스쿨 아이디·비밀번호", self.del_riro, self.riro_logout, "서비스: 리로스쿨 로그아웃"),
            ("리로스쿨 전용 브라우저", lambda: self.tool_del("riro_browser", ["-m", "riro_merit.riro_site", "forget-browser"]), None, "이 PC 의 로그인 기록(쿠키)"),
            ("디벗 전용 브라우저(구글 로그인)", lambda: self.tool_del("divot_browser", ["-m", "riro_merit.divot", "forget"]), None,
             "구글 쪽은 myaccount.google.com → 보안 → 타사 연결에서"),
            ("상벌점 시트 구글 토큰", lambda: self.tool_del("google_sheet_token", ["-m", "riro_merit.google_sheet", "forget"]),
             lambda: self.tool_del("google_sheet_token", ["-m", "riro_merit.google_sheet", "revoke"], action="revoked"), "서비스: 구글 권한 해제"),
        ]
        for i, (label, local, remote, note) in enumerate(rows, start=1):
            tk.Label(f, text=label, bg="white", anchor="w").grid(row=i, column=0, sticky="w", padx=12, pady=4)
            bf = tk.Frame(f, bg="white")
            bf.grid(row=i, column=1, sticky="w")
            tk.Button(bf, text="이 PC 에서 지우기", command=local, relief="flat", bg="#fee2e2").pack(side="left", padx=(0, 4))
            if remote:
                tk.Button(bf, text="서비스 쪽 해제", command=remote, relief="flat", bg="#fef3c7").pack(side="left")
            tk.Label(f, text=note, bg="white", fg=SUB, font=SMALL).grid(row=i, column=2, sticky="w", padx=6)
        # 본인 확인 방식 (2026-11-26) — 처음 한 번 고른 것을 보여 주고, 바꾸려면 지금 방식으로 확인한 뒤 다시 고른다
        uf = tk.LabelFrame(f, text=" 본인 확인 방식 ", bg="white", padx=6, pady=4)
        uf.grid(row=len(rows) + 1, column=0, columnspan=3, sticky="we", padx=10, pady=(12, 4))
        self.h_unlock = tk.Label(uf, text="", bg="white", anchor="w")
        self.h_unlock.grid(row=0, column=0, sticky="w", padx=(6, 10))
        self.b_unlock = tk.Button(uf, text="본인 확인 방식 바꾸기", relief="flat", bg="#e0e7ff", command=self.change_unlock)
        self.b_unlock.grid(row=0, column=1, sticky="w")
        self.show_unlock()

    def show_unlock(self):
        names = {"hello": "Windows Hello (지문·얼굴·PIN)", "password": "잠금 비밀번호",
                 None: "아직 안 골랐어요", "broken": "기록을 읽지 못했어요 — 바꾸기를 눌러 다시 골라 주세요"}
        self.h_unlock.config(text="지금: " + names.get(H.current_method(), "?"))

    def change_unlock(self):
        self.b_unlock.configure(state="disabled")
        self.say("본인 확인 창이 떴습니다 — 지금 방식으로 확인한 뒤 새 방식을 골라 주세요")

        def done(res):
            self.b_unlock.configure(state="normal")
            ok = res.get("code") == f"EXIT_{H.DESKTOP_VERIFIED_EXIT}"
            self.R.add("unlock_method", "changed" if ok else "cancelled", "OK" if ok else res.get("code"))
            self.show_unlock()
            self.say("✅ 본인 확인 방식을 정했어요" if ok else "바꾸지 않았어요", GREEN if ok else SUB)
        self.bg(lambda: run_tool(["windows_hello.py", "--change"], 600), done)

    def confirm(self, what):
        return messagebox.askyesno("확인", f"{what} 을(를) 지울까요?\n되돌릴 수 없습니다. 다시 쓰려면 새로 넣어야 합니다.")

    def del_vault(self, svc, user, item, mark=None):
        if not self.confirm(item.replace("_", " ")):
            return
        try:
            ok = S.vault_delete(svc, user)
            if mark:
                S.mark_vault(mark, "deleted")
            self.R.add(item, "deleted", "OK" if ok else "OK_NOTHING")
            self.say("✅ 지웠어요" if ok else "원래 없었어요", GREEN)
        except S.SecureError as e:
            self.R.add(item, "deleted", e.code)
            self.say(f"지우지 못했어요 ({e.code})", RED)

    def del_cert(self):
        name = (T.load_config_or_empty().get("cert_name") or "")
        if not name:
            self.say("설정에 인증서 이름이 없어요", RED)
            return
        self.del_vault(T.KEYRING_SERVICE, name, "cert_password", mark="cert")

    def del_riro(self):
        if not self.confirm("리로스쿨 아이디·비밀번호"):
            return
        try:
            ok = RC.delete_credentials()
            S.mark_vault("riro", "deleted")
            self.R.add("riro_credentials", "deleted", "OK" if ok else "OK_NOTHING")
            self.say("✅ 지웠어요" if ok else "원래 없었어요", GREEN)
        except S.SecureError as e:
            self.R.add("riro_credentials", "deleted", e.code)
            self.say(f"지우지 못했어요 ({e.code})", RED)

    def tool_del(self, item, args, action="deleted"):
        if action == "deleted" and not self.confirm(item.replace("_", " ")):
            return
        if action == "revoked" and not messagebox.askyesno("확인", "구글 쪽에서 이 프로그램의 권한을 끊을까요?\n(이 PC 토큰도 함께 지웁니다)"):
            return
        self.say("처리 중…")
        self.bg(lambda: run_tool(args, 120), lambda res: (self.R.add(item, action, res.get("code")),
                                                          self.say(f"{'✅ ' if res.get('ok') else ''}{res.get('code')}",
                                                                   GREEN if res.get("ok") else RED)))

    def riro_logout(self):
        self.say("리로스쿨에서 로그아웃하는 중… (크롬 창이 잠깐 뜹니다)")
        self.bg(lambda: run_tool(["-m", "riro_merit.riro_site", "logout"], 120),
                lambda res: (self.R.add("riro_session", "revoked", res.get("code")),
                             self.say(f"{'✅ ' if res.get('ok') else ''}리로스쿨 로그아웃: {res.get('code')}", GREEN if res.get("ok") else RED)))



def unlock_settings(root):
    return H.show_verification(root, "보안 설정 잠금", "교무 도구 보안 설정을 열려면 본인을 확인해 주세요.")


def main():
    args = sys.argv[1:]
    result = args[args.index("--result") + 1] if "--result" in args else None
    tab = args[args.index("--tab") + 1] if "--tab" in args else "neis"
    R = Result(result)
    root = tk.Tk()
    verification = unlock_settings(root)
    if verification != "OK":
        R.status = "cancelled" if verification == "CANCELLED" else "failed"
        R.add("windows_hello", "verify", verification)
        root.destroy()
        return
    try:
        app = App(root, R, tab)
    except Exception as e:
        R.add("window", "open", "E_" + type(e).__name__)
        R.status = "failed"
        R.write()
        raise
    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    root.geometry("+%d+%d" % (max(0, (root.winfo_screenwidth() - w) // 2), max(0, (root.winfo_screenheight() - h) // 4)))
    root.deiconify()
    root.lift()
    root.attributes("-topmost", True)
    root.after(600, lambda: root.attributes("-topmost", False))
    if tab == "riro" and "--connect-divot" in args:
        root.after_idle(lambda: app.connect_divot(use_saved=True))
    root.mainloop()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""보안 점검 — 비밀번호·토큰·쿠키·학생 명단·개인 설정값을 «열지 않고» 상태만 본다.

  python secure_check.py            기본(클로드가 돌려도 되는 점검) — 아래 «여는 것» 만 연다
  python secure_check.py --json     같은 내용을 JSON 으로
  python secure_check.py --deep     ★ 선생님이 직접 돌리는 깊은 점검 — 클로드는 부르지 않는다

기본 점검이 «여는 것»
  · 파일·폴더가 있는지, 크기, 바뀐 시각, 폴더를 열 수 있는 계정의 «종류»(현재 사용자·SYSTEM·관리자…)
  · 암호화 파일의 «겉봉» 앞부분(형식 이름·용도 이름) — 암호문은 풀지 않는다
  · 이전 기록(secure\\migration_journal.json) · 금고 표시(vault_status.json: 넣었다/지웠다·시각만) — 값이 없는 파일
  · keyring 이 어느 저장 방식을 쓰는지(종류 이름만)
기본 점검이 «열지 않는 것»
  · Windows 금고의 항목 (있는지 확인도 안 한다 — 윈도우는 확인할 때도 비밀번호를 함께 돌려주기 때문)
  · 평문 설정 파일(teacher_config.json)의 내용, 암호화 설정의 복호화, 구글 토큰, 크롬 프로필 안의 파일(쿠키·비밀번호 DB·설정)
  · 리로스쿨 일반 설정(config.json)의 내용

--deep (선생님이 직접): 위에 더해 암호화 파일을 실제로 풀어 보고(풀림 예/아니오), 금고 항목이 있는지 보고,
  크롬에 저장된 비밀번호 «개수»를 센다. 그래도 값은 화면에 찍지 않는다.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

import secure_store as S  # noqa: E402
import teacher_config as T  # noqa: E402

LA = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
RIRO_DIR = LA / "RiroMeritAutomation"
EALIMI_DIR = T.CONFIG_DIR / "ealimi"
HELPER_SRC = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "NeisAutomationEVPN" / "helper.cs"
from riro_merit.config import riro_service  # noqa: E402  학교 주소 · 금고 이름 — 없으면 "" (점검은 만들지 않는다)


def _packaged() -> bool:
    try:
        n = ctypes.c_uint32(0)
        return ctypes.windll.kernel32.GetCurrentPackageFullName(ctypes.byref(n), None) == 122
    except Exception:
        return False


def _meta(p: Path) -> dict:
    """있는지·크기·바뀐 시각만 (내용은 안 연다)."""
    if not p.exists():
        return {"exists": False}
    st = p.stat()
    return {"exists": True, "size": st.st_size, "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")}


def _envelope_header(p: Path) -> dict:
    """암호화 파일의 겉봉 앞부분만 — 형식·용도 이름. 암호문은 풀지 않는다."""
    m = _meta(p)
    if not m["exists"]:
        return m
    try:
        with open(p, "rb") as f:
            head = f.read(300).decode("utf-8", "replace")
        fm = re.search(r'"format":\s*"([^"]{1,40})"', head)
        pm = re.search(r'"purpose":\s*"([^"]{1,60})"', head)
        m.update(format=fm.group(1) if fm else "unknown", purpose=pm.group(1) if pm else "unknown")
    except OSError as e:
        m["error"] = type(e).__name__
    return m


def _helper_version() -> str:
    try:     # 설치된 도우미 «소스 코드» (키트 코드 사본 — 개인정보 없음)
        src = HELPER_SRC.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "not_installed"
    m = re.search(r'HELPER_VERSION\s*=\s*"([^"]+)"', src)
    return m.group(1) if m else "1-plain-only"


# ─────────────────────────────────────────────────────────── 기본(열지 않는) 점검
def safe_vault() -> dict:
    try:
        S.check_vault(functional=False)               # 저장 방식 «종류»만 본다 (항목은 안 연다)
        return {"ok": True, "backend": "WinVaultKeyring", "items": "AI 점검은 금고 항목을 열지 않음"}
    except S.SecureError as e:
        return {"ok": False, "code": e.code}


def safe_neis() -> dict:
    j = {}
    try:
        j = json.loads((T.SECURE_DIR / "migration_journal.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    runs = [r for r in j.get("runs", []) if r.get("action") == "run"]
    last = runs[-1] if runs else {}
    tc = (last.get("steps") or {}).get("teacher_config") or {}
    fins = [r for r in j.get("runs", []) if r.get("action") == "finish" and r.get("ok")]
    fin = fins[-1] if fins and fins[-1].get("at", "") >= (last.get("at") or "") else {}
    return {
        "config_plain": _meta(T.CONFIG_FILE) | {"tmp_leftover": T.CONFIG_FILE.with_suffix(".json.tmp").exists()},
        "config_private": _envelope_header(T.PRIVATE_FILE),
        "migration": {"last_run": fin.get("at") or last.get("at"), "rolled_back": bool(last.get("rolled_back")),
                      "compat_read_same": (f"{fin.get('same')}/{fin.get('total')}" if fin else
                                           f"{tc.get('same')}/{tc.get('total')}" if tc else None),
                      "plain_compat": fin.get("plain_compat") if fin else tc.get("plain_compat")},
        "vault_marks": {k: S.vault_marks().get(k) for k in ("cert", "evpn", "riro")},
        "evpn_helper_version": _helper_version(),
        "google_token": {"protected_file": _meta(T.GOOGLE_TOKEN_DPAPI)["exists"], "plain_token_json": T.TOKEN_FILE.exists(),
                         "oauth_client_json_in_kit": (KIT / "oauth_client.json").exists()},
        "secure_dir": {"acl": S.acl_report(T.SECURE_DIR), "location": S.location_report(T.SECURE_DIR)} if T.SECURE_DIR.exists() else {"exists": False},
        "config_dir": {"acl": S.acl_report(T.CONFIG_DIR), "location": S.location_report(T.CONFIG_DIR)},
    }


def safe_profiles() -> dict:
    out = {}
    for root in (T.CONFIG_DIR, EALIMI_DIR, RIRO_DIR, RIRO_DIR / "browser"):
        if not root.exists():
            continue
        for p in sorted(root.iterdir()):
            if p.is_dir() and ("profile" in p.name.lower() or root.name == "browser"):
                out[f"{root.name}/{p.name}"] = {     # 안의 파일은 열지 않는다 — 있는지만
                    "cookies_db": any((p / x).exists() for x in ("Default/Network/Cookies", "Default/Cookies")),
                    "login_data_db": (p / "Default" / "Login Data").exists(),
                    "acl": S.acl_report(p)}
    return out


SENSITIVE_PATTERNS = ("cookies*.json", "*storage*state*.json", "storageState*.json", "*.har", "trace*.zip",
                      "token*.json", "*.tmp", "roster*.json")


def leftovers() -> dict:
    out = {"plain_sensitive_files": []}
    for root in (T.CONFIG_DIR, RIRO_DIR, EALIMI_DIR):
        if not root.exists():
            continue
        for pat in SENSITIVE_PATTERNS:
            for f in root.glob(pat):              # 이름만 본다 (열지 않는다)
                if f.is_file():
                    kind = ("cookie" if "cookie" in f.name.lower() else "token" if "token" in f.name.lower()
                            else "student_roster" if "roster" in f.name.lower() else "temp" if f.suffix == ".tmp"
                            else "browser_state_export")
                    out["plain_sensitive_files"].append({"path": str(f).replace(str(LA), "%LOCALAPPDATA%"), "kind": kind})
    cap = KIT / "캡처"
    out["kit_folder"] = {
        "capture_files": sum(1 for f in cap.rglob("*") if f.is_file()) if cap.exists() else 0,
        "log_files": sum(1 for _ in KIT.glob("*.log")),
        "class_roster_txt": (KIT / "우리반명단.txt").exists(),
        "oauth_client_json": (KIT / "oauth_client.json").exists(),
        "trace_or_har": sum(1 for pat in ("*.har", "trace*.zip") for _ in KIT.rglob(pat) if ".venv" not in str(_)),
    }
    return out


def safe_riro() -> dict:
    sec = RIRO_DIR / "secure"
    return {"dir_exists": RIRO_DIR.exists(), "config": _meta(RIRO_DIR / "config.json"),
            "vault_mark": S.vault_marks().get("riro"),
            "secure_dir": {"acl": S.acl_report(sec), "location": S.location_report(sec)} if sec.exists() else {"exists": False},
            "google_sheets_token": _envelope_header(sec / "google_sheets_token.dpapi")}


def app_paths() -> dict:
    """앱 실행 방식(클로드 앱 MSIX · 바탕화면 아이콘)에 따라 설정 자리가 갈리는지 — 파일 번호(메타데이터)만 비교."""
    out = {"this_process_packaged": _packaged(), "virtual_copies": []}
    pk = LA / "Packages"
    if pk.exists():
        for d in pk.iterdir():
            for name in ("neis-automation", "RiroMeritAutomation"):
                v = d / "LocalCache" / "Local" / name
                if v.is_dir():
                    try:
                        a, b = os.stat(LA / name), os.stat(v)
                        same = (a.st_ino, a.st_dev) == (b.st_ino, b.st_dev)
                    except OSError:
                        same = None
                    out["virtual_copies"].append({"app": d.name.split("_")[0], "folder": name, "same_as_real": same})
    return out


def check_git() -> dict:
    try:
        r = subprocess.run(["git", "-c", "core.quotepath=false", "ls-files"], cwd=KIT, capture_output=True,
                           text=True, encoding="utf-8", timeout=20)
        bad = [f for f in r.stdout.splitlines()
               if re.search(r"token|credential|secret|\.env|출결|명렬|학생|명단|\.csv|\.xlsx|\.dpapi|cookies", f, re.I)
               and not f.endswith((".md", ".example.json")) and "secure_store" not in f]
        return {"tracked_sensitive_like": bad}
    except Exception as e:
        return {"error": type(e).__name__}


def run_safe() -> dict:
    return {"mode": "safe", "vault": safe_vault(), "neis_edufine": safe_neis(), "browser_profiles": safe_profiles(),
            "leftovers": leftovers(), "riro": safe_riro(), "app_paths": app_paths(), "git": check_git()}


# ─────────────────────────────────────────────────────────── 깊은 점검 (선생님이 직접)
def run_deep() -> dict:
    """금고 항목·복호화·크롬 비밀번호 개수까지. 값은 내지 않는다. ★ 클로드가 부르지 않는다."""
    res = run_safe()
    res["mode"] = "deep"
    try:
        res["vault"]["functional"] = S.check_vault(functional=True)["functional"]
    except S.SecureError as e:
        res["vault"]["functional"] = e.code
    res["neis_edufine"]["config_private"] = S.file_state(T.PRIVATE_FILE, T.PRIVATE_PURPOSE)
    merged = {}
    try:
        merged = T.load_merged()
        res["neis_edufine"]["merge_read"] = "ok"
    except T.ConfigMissingError as e:
        res["neis_edufine"]["merge_read"] = getattr(e, "code", "E_CONFIG")

    def has(svc, user):
        try:
            return bool(user) and S.vault_exists(svc, user)
        except Exception:
            return None
    res["neis_edufine"]["vault_entries"] = {"cert_password": has((T.vault_service(False) or T.VAULT_LEGACY), merged.get("cert_name")),
                                            "evpn_password": has((T.vault_service(False) or T.VAULT_LEGACY), "evpn")}
    res["neis_edufine"]["settings_present"] = {k: bool(merged.get(k)) for k in
                                               ("neis_url", "access_mode", "cert_name", "contact", "approval_line",
                                                "work_start", "work_end", "evpn_user", "school_name")}
    for k, p in res["browser_profiles"].items():
        ld = next((x for x in (LA / "neis-automation" / k.split("/", 1)[1] / "Default" / "Login Data",
                               RIRO_DIR / "browser" / k.split("/", 1)[1] / "Default" / "Login Data") if x.exists()), None)
        if ld:
            try:
                import sqlite3
                con = sqlite3.connect(f"file:{ld.as_posix()}?mode=ro&immutable=1", uri=True)
                p["saved_logins"] = con.execute("SELECT COUNT(*) FROM logins").fetchone()[0]
                con.close()
            except Exception as e:
                p["saved_logins"] = "E_" + type(e).__name__
    try:
        if not riro_service(False):
            raise S.SecureError("E_NO_SCHOOL")
        u = S.vault_username(riro_service(False))
        res["riro"]["vault_entry"] = {"id": bool(u), "password": bool(u) and S.vault_exists(riro_service(False), u)}
    except S.SecureError as e:
        res["riro"]["vault_entry"] = {"error": e.code}
    return res


# ─────────────────────────────────────────────────────────── 사람이 읽는 표
def _yn(v):
    return {True: "예", False: "아니오", None: "?"}.get(v, str(v))


def _mark(m):
    return f"{'넣음' if m.get('state') == 'saved' else '지움'}({m.get('at', '')[:16]})" if m else "기록 없음"


def human(r: dict) -> None:
    deep = r["mode"] == "deep"
    print(f"(점검 방식: {'깊은 점검 — 선생님 직접' if deep else '기본 — 금고·설정값·크롬 파일은 열지 않음'})")
    v = r["vault"]
    print("■ Windows 자격 증명 관리자(금고)")
    print(f"   {'✅ Windows 금고 방식' if v.get('ok') else '❌ ' + v.get('code', '')}"
          + (f" · 쓰기/읽기 시험 {v.get('functional')}" if deep else " · 항목은 열지 않음"))
    n = r["neis_edufine"]
    cp, pv, mg = n["config_plain"], n["config_private"], n["migration"]
    print("■ 나이스·에듀파인 설정")
    print(f"   평문 설정 파일   : {'있음 · ' + str(cp.get('size')) + 'B · ' + cp.get('mtime', '') if cp['exists'] else '없음'}")
    if deep:
        print(f"   암호화 개인 설정  : {'있음 · 풀림 ' + _yn(pv.get('decrypts')) if pv.get('exists') else '없음'} · 합쳐 읽기 {n.get('merge_read')}")
        ve = n["vault_entries"]
        print(f"   금고: 인증서 암호 {_yn(ve['cert_password'])} · EVPN 비밀번호 {_yn(ve['evpn_password'])}")
        print("   항목 있음: " + " ".join(f"{k}={'O' if x else '-'}" for k, x in n["settings_present"].items()))
    else:
        print(f"   암호화 개인 설정  : {'있음 · 형식 ' + pv.get('format', '?') if pv.get('exists') else '없음'} (풀어 보지 않음)")
        vm = n["vault_marks"]
        print(f"   금고 표시(기록)   : 인증서 암호 {_mark(vm['cert'])} · EVPN {_mark(vm['evpn'])}  — 표시가 없으면 이 기능 전에 넣은 것")
    print(f"   마지막 이전       : {mg['last_run'] or '없음'} · 읽기 호환 {mg['compat_read_same']} · 평문 호환 항목 {mg['plain_compat']}"
          + (" · 되돌림" if mg["rolled_back"] else ""))
    print(f"   EVPN 도우미 판    : {n['evpn_helper_version']}")
    g = n["google_token"]
    print(f"   구글 토큰         : 암호화 {_yn(g['protected_file'])} · 평문 token.json {_yn(g['plain_token_json'])} · 구글 열쇠 {_yn(g['oauth_client_json_in_kit'])}")
    for label, key in (("설정 폴더", "config_dir"), ("보호 폴더(secure)", "secure_dir")):
        d = n[key]
        if "acl" in d:
            a, loc = d["acl"], d["location"]
            print(f"   {label}: 열 수 있는 계정 {'/'.join(a.get('principals') or [])} · 상속 {_yn(a.get('inherits'))}"
                  f" · 깃 {_yn(loc['in_git'])} · 클라우드 {_yn(loc['in_cloud_sync'])}")
    print("■ 브라우저 프로필 (크롬 자체 방식 — 안의 파일은 열지 않음)")
    for k, p in r["browser_profiles"].items():
        extra = f" · 크롬 저장 비밀번호 {p['saved_logins']}개" if deep and "saved_logins" in p else ""
        print(f"   {k}: 쿠키DB {_yn(p['cookies_db'])} · 비밀번호DB 파일 {_yn(p['login_data_db'])}{extra}"
              f" · 열 수 있는 계정 {'/'.join(p['acl'].get('principals') or [])}")
    lo = r["leftovers"]
    print("■ 평문 민감 파일 (이름으로만 찾음)")
    for f in lo["plain_sensitive_files"]:
        print(f"   ⚠️ {f['kind']}: {f['path']}")
    if not lo["plain_sensitive_files"]:
        print("   (평문 쿠키·토큰·명단·임시 파일 없음)")
    kf = lo["kit_folder"]
    print(f"   키트 폴더: 캡처 {kf['capture_files']}개 · 로그 {kf['log_files']} · 우리반명단 {_yn(kf['class_roster_txt'])} · trace/har {kf['trace_or_har']}")
    ri = r["riro"]
    print("■ 리로스쿨 상벌점")
    print(f"   폴더 {_yn(ri['dir_exists'])} · 일반 설정 {_yn(ri['config']['exists'])} · "
          + (f"금고 {ri.get('vault_entry')}" if deep else f"금고 표시 {_mark(ri['vault_mark'])}"))
    ap = r["app_paths"]
    print(f"■ 실행 위치: 앱 컨테이너 안 {_yn(ap['this_process_packaged'])}"
          + "".join(f" · {c['app']} 전용 경로의 {c['folder']} = 실제 폴더 {_yn(c['same_as_real'])}" for c in ap["virtual_copies"]))
    gi = r["git"]
    print(f"■ 깃: 추적 중인 민감해 보이는 파일 {len(gi.get('tracked_sensitive_like', []))}개")


if __name__ == "__main__":
    res = run_deep() if "--deep" in sys.argv else run_safe()
    if "--json" in sys.argv:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        human(res)

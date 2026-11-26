# -*- coding: utf-8 -*-
"""평문 인증정보·개인 설정을 보호 형식으로 옮긴다 — AI 에게 내용을 보여 주지 않는 로컬 프로그램.

  python secure_migrate.py plan        무엇을 할지 보기만 (아무것도 안 바꾼다)
  python secure_migrate.py run         이전 실행 (다시 돌려도 안전 — 끝난 단계는 건너뛴다)
  python secure_migrate.py finish      EVPN 도우미 새 판 설치 뒤: 평문에 남겨 둔 인증서 이름까지 뺀다
  python secure_migrate.py restore [--stamp 20261109-153000]   암호화 백업으로 되돌리기 (기본: 가장 최근)
  python secure_migrate.py status      진행 기록

순서 (run)
  ① 금고 확인 — 진짜 Windows 자격 증명 관리자가 아니면 여기서 멈춘다 (평문으로 대신하지 않는다)
  ② 대상 확인 — 파일이 있는지·형식이 맞는지만
  ③ 백업 — 원본을 DPAPI 로 암호화해 secure\\backup\\<시각>\\ 에. 다시 풀어 원본과 같은지 확인
  ④ 새 보호 형식으로 저장 — 개인 설정·토큰·쿠키를 secure\\*.dpapi 로
  ⑤ 읽기 호환 확인 — 기존 코드가 쓰는 teacher_config.load_merged() 가 원본과 같은 값을 돌려주는지 (같음 N/N 만 출력)
  ⑥ 토큰 갱신 확인 — 구글 토큰이 있으면 실제로 새로 고쳐 본다
  ⑦ 정리 — ③~⑥ 이 다 통과한 것만 평문 원본·임시 파일을 지운다. 하나라도 실패하면 지우지 않고 되돌린다

출력: 단계 이름 · 성공/실패 · 개수 · 오류 코드 · 경로(%LOCALAPPDATA% 로 줄여서). 값은 찍지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))

import secure_store as S  # noqa: E402
import teacher_config as T  # noqa: E402

BACKUP_PURPOSE = "neis-automation/backup"
HELPER_DIR = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "NeisAutomationEVPN"


def LA() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home())))


def short(p: Path) -> str:
    return str(p).replace(str(LA()), "%LOCALAPPDATA%")


def journal_path() -> Path:
    return T.SECURE_DIR / "migration_journal.json"


def load_journal() -> dict:
    try:
        return json.loads(journal_path().read_text(encoding="utf-8"))
    except Exception:
        return {"runs": [], "steps": {}}


def save_journal(j: dict) -> None:
    p = journal_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def say(ok, step, msg=""):
    mark = {True: "✅", False: "❌", None: "⏭"}[ok]
    print(f"{mark} {step}" + (f" — {msg}" if msg else ""), flush=True)


# ─────────────────────────────────────────────────────────── 대상
def targets() -> list[dict]:
    """옮길 평문 파일들 (있는 것만). 내용은 읽지 않고 «있다/형식» 만."""
    # e알리미(강사님 원래 위젯)는 이 PC 에서 쓰지 않아 옮기지 않는다 — 평문 파일이 생기면 secure_check 가 알려 준다
    cands = [
        {"id": "teacher_config", "path": T.CONFIG_FILE, "kind": "개인 설정(인증서 이름·연락처 등)"},
        {"id": "google_token", "path": T.TOKEN_FILE, "kind": "구글 접근·갱신 토큰"},
    ]
    out = []
    for c in cands:
        p = c["path"]
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            c["format"] = "json"
            c["already_split"] = c["id"] == "teacher_config" and isinstance(d, dict) and T.is_split_mode(d)
        except Exception:
            c["format"] = "unreadable"
            c["already_split"] = False
        out.append(c)
    return out


def helper_supports_dpapi() -> bool:
    src = HELPER_DIR / "helper.cs"
    try:
        return "HELPER_VERSION" in src.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def helper_installed() -> bool:
    return (HELPER_DIR / "evpn_helper.exe").exists()


def helper_selftest(exe: Path | None = None) -> dict:
    """도우미에게 «인증서 이름·암호를 찾았는지»만 묻는다 (값은 안 돌려준다). 관리자 권한 필요 없음."""
    exe = exe or HELPER_DIR / "evpn_helper.exe"
    if not exe.exists():
        return {"ok": False, "code": "E_HELPER_MISSING"}
    # ★ 옛 판 도우미는 --selftest 를 몰라서 «진짜 실행»(AXGATE 창 감시 300초)을 시작한다 — 설치된 원본에
    #   HELPER_VERSION 표시가 있을 때만 부른다 (2026-11-09: 옛 판을 불러 30초 동안 감시가 돌았다 — 아무것도 누르지는 않았다)
    if exe == HELPER_DIR / "evpn_helper.exe" and not helper_supports_dpapi():
        return {"ok": False, "code": "E_HELPER_OLD"}
    out = Path(tempfile.gettempdir()) / f"neis_helper_selftest_{os.getpid()}.txt"
    try:
        subprocess.run([str(exe), "--selftest", str(out)], timeout=30, capture_output=True)
        txt = out.read_text(encoding="utf-8-sig") if out.exists() else ""
    except Exception as e:
        return {"ok": False, "code": "E_HELPER_RUN:" + type(e).__name__}
    finally:
        try:
            out.unlink()
        except OSError:
            pass
    kv = dict(line.split("=", 1) for line in txt.splitlines() if "=" in line)
    if "version" not in kv:
        return {"ok": False, "code": "E_HELPER_OLD"}          # 옛 판은 --selftest 를 모른다
    return {"ok": kv.get("cert_name", "").startswith("found") and kv.get("password") == "found",
            "cert_source": kv.get("cert_name", "missing").split(":", 1)[-1], "password": kv.get("password"),
            "version": kv.get("version")}


# ─────────────────────────────────────────────────────────── 백업
def backup_dir(stamp: str) -> Path:
    return T.SECURE_DIR / "backup" / stamp


def make_backup(items: list[dict], stamp: str) -> Path:
    bdir = backup_dir(stamp)
    bdir.mkdir(parents=True, exist_ok=True)
    manifest = {"stamp": stamp, "files": []}
    for it in items:
        raw = it["path"].read_bytes()
        dst = bdir / (it["id"] + ".dpapi")
        S.write_protected(dst, raw, BACKUP_PURPOSE, keep_prev=False)
        back, _ = S.read_protected(dst, BACKUP_PURPOSE, allow_prev=False)
        if back != raw:
            raise S.SecureError("E_WRITE", "backup-verify")
        manifest["files"].append({"id": it["id"], "orig": short(it["path"]), "size": len(raw)})
    (bdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return bdir


def latest_backup() -> str | None:
    root = T.SECURE_DIR / "backup"
    if not root.exists():
        return None
    stamps = sorted(d.name for d in root.iterdir() if (d / "manifest.json").exists())
    return stamps[-1] if stamps else None


def _expand(p: str) -> Path:
    return Path(p.replace("%LOCALAPPDATA%", str(LA())))


def restore(stamp: str | None) -> int:
    stamp = stamp or latest_backup()
    if not stamp:
        say(False, "복구", "암호화 백업이 없습니다")
        return 1
    bdir = backup_dir(stamp)
    try:
        man = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
    except Exception:
        say(False, "복구", "E_FILE_CORRUPT (manifest)")
        return 1
    n = 0
    for f in man["files"]:
        try:
            raw, _ = S.read_protected(bdir / (f["id"] + ".dpapi"), BACKUP_PURPOSE, allow_prev=False)
        except S.SecureError as e:
            say(False, f"복구 {f['id']}", e.code)
            return 1
        dst = _expand(f["orig"])
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".restore-tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, dst)
        n += 1
        say(True, f"복구 {f['id']}", f"→ {f['orig']}")
    # 복구한 평문이 기준이 되도록 암호화 개인 설정은 옆으로 비켜 둔다 (지우지 않는다)
    if any(f["id"] == "teacher_config" for f in man["files"]) and T.PRIVATE_FILE.exists():
        aside = T.PRIVATE_FILE.with_name(T.PRIVATE_FILE.name + f".before-restore-{S.now_stamp()}")
        os.replace(T.PRIVATE_FILE, aside)
        say(True, "암호화 개인 설정 비켜 둠", short(aside))
    j = load_journal()
    j["runs"].append({"at": S.now_stamp(), "action": "restore", "stamp": stamp, "files": n})
    save_journal(j)
    return 0


# ─────────────────────────────────────────────────────────── 단계
def _compare(original: dict, merged: dict) -> tuple[int, int, list[str]]:
    keys = [k for k in original if k != T.SECURE_MARKER]
    diff = [k for k in keys if merged.get(k) != original.get(k)]
    return len(keys) - len(diff), len(keys), diff


def migrate_teacher_config(stamp: str) -> dict:
    original = json.loads(T.CONFIG_FILE.read_text(encoding="utf-8"))
    if T.is_split_mode(original):
        # 이미 나눠 저장됨 — 남은 개인 키가 평문에 있으면(옛 코드가 다시 씀) 한 번 더 나눈다
        merged = T.load_merged()
        stray = [k for k in T.PRIVATE_KEYS if k in original and k not in (original[T.SECURE_MARKER].get("plain_compat") or [])]
        if stray:
            T.save_config(merged)
        same, total, diff = _compare(merged, T.load_merged())
        return {"ok": not diff, "same": same, "total": total, "diff_keys": diff, "already": True, "restowed": len(stray)}

    compat = [] if helper_supports_dpapi() else ["cert_name"]
    marker = {"version": 1, "private_file": "secure/" + T.PRIVATE_FILE.name, "plain_compat": compat, "migrated": stamp}
    data = dict(original)
    data[T.SECURE_MARKER] = marker
    # EVPN: 지금 실제로 쓰는 방식을 기록만 한다 (따로 저장된 비밀번호가 있으면 separate). 인증서 암호로 바꾸지 않는다
    try:
        if not data.get("evpn_password_mode") and S.vault_exists(T.KEYRING_SERVICE, "evpn"):     # 있다/없다만
            data["evpn_password_mode"] = "separate"
    except Exception:
        pass
    T.save_config(data)                     # 암호화 파일 먼저(검증 포함) → 평문
    merged = T.load_merged()
    same, total, diff = _compare(original, merged)
    if diff:
        return {"ok": False, "same": same, "total": total, "diff_keys": diff, "code": "E_COMPAT_READ"}
    # 기존 코드들이 «필수 항목» 검사를 통과하는지도 (원래 통과하던 경우만)
    try:
        T.load_config()
        cfg_ok = True
    except T.ConfigMissingError:
        cfg_ok = all(k in original for k in T.REQUIRED_KEYS) is False   # 원래도 없었으면 괜찮다
    plain_now = json.loads(T.CONFIG_FILE.read_text(encoding="utf-8"))
    left = [k for k in T.PRIVATE_KEYS if k in plain_now]
    return {"ok": cfg_ok, "same": same, "total": total, "diff_keys": [], "plain_compat": compat,
            "private_keys_left_in_plain": left, "evpn_password_mode": data.get("evpn_password_mode", "unset")}


def migrate_google_token() -> dict:
    from google.oauth2.credentials import Credentials
    info = json.loads(T.TOKEN_FILE.read_text(encoding="utf-8"))
    creds = Credentials.from_authorized_user_info(info)
    refreshed = None
    if creds.refresh_token:
        try:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            refreshed = True
        except Exception as e:
            refreshed = "E_GOOGLE_REFRESH:" + type(e).__name__
    # 갱신이 실패해도 토큰 자체는 옮긴다 (원본은 백업에 있다). 평문 삭제는 갱신이 확인된 때만
    if not T.SECURE_DIR.exists():
        S.prepare_secure_dir(T.SECURE_DIR)
    S.write_json(T.GOOGLE_TOKEN_DPAPI, json.loads(creds.to_json()), T.GOOGLE_PURPOSE)
    back = Credentials.from_authorized_user_info(S.read_json(T.GOOGLE_TOKEN_DPAPI, T.GOOGLE_PURPOSE))
    readable = back.refresh_token == creds.refresh_token
    deleted = False
    if readable and refreshed is True:
        T.TOKEN_FILE.unlink()
        deleted = True
    return {"ok": readable, "refresh": refreshed, "plain_deleted": deleted}


def cleanup_temp() -> int:
    n = 0
    for pat in ("*.json.tmp", "*.tmp-*", "*.restore-tmp"):
        for root in (T.CONFIG_DIR, T.SECURE_DIR):
            if root.exists():
                for f in root.glob(pat):
                    if f.is_file() and time.time() - f.stat().st_mtime > 60:
                        f.unlink()
                        n += 1
    return n


def plan() -> int:
    try:
        v = S.check_vault(functional=True)
        say(True, "금고 확인", v["backend"])
    except S.SecureError as e:
        say(False, "금고 확인", e.code + " — 이전하지 않습니다")
        return 1
    items = targets()
    if not items:
        say(None, "대상", "옮길 평문 파일이 없습니다")
    for it in items:
        extra = " (이미 나눠 저장됨)" if it.get("already_split") else ""
        print(f"   · {it['kind']}: {short(it['path'])} [{it['format']}]{extra}")
    print(f"   EVPN 도우미: {'설치됨' if helper_installed() else '없음'} · 새 판(DPAPI 읽기) {'예' if helper_supports_dpapi() else '아니오 → 인증서 이름은 평문에 «호환»으로 남김'}")
    return 0


def run() -> int:
    j = load_journal()
    stamp = S.now_stamp()
    rec = {"at": stamp, "action": "run", "steps": {}}
    try:
        v = S.check_vault(functional=True)
        say(True, "① 금고 확인", "진짜 Windows 자격 증명 관리자 · 쓰기/읽기 시험 통과")
    except S.SecureError as e:
        say(False, "① 금고 확인", e.code + " — 평문으로 대신하지 않고 멈춥니다")
        return 1
    items = targets()
    say(True, "② 대상 확인", f"{len(items)}개 — " + ", ".join(i["id"] for i in items) if items else "없음")
    bad = [i for i in items if i["format"] != "json"]
    if bad:
        say(False, "② 대상 확인", "형식이 깨진 파일: " + ", ".join(short(i["path"]) for i in bad) + " — 손대지 않습니다")
        items = [i for i in items if i["format"] == "json"]
    try:
        S.prepare_secure_dir(T.SECURE_DIR)
        rep = S.acl_report(T.SECURE_DIR)
        say(True, "   보호 폴더", f"{short(T.SECURE_DIR)} · 열 수 있는 계정 {'/'.join(rep['principals'])}")
    except S.SecureError as e:
        say(False, "   보호 폴더", e.code)
        return 1
    todo = [i for i in items if not i.get("already_split")]
    if todo:
        try:
            bdir = make_backup(todo, stamp)
            say(True, "③ 암호화 백업", f"{len(todo)}개 → {short(bdir)} (다시 풀어 원본과 같음 확인)")
            rec["backup"] = stamp
        except S.SecureError as e:
            say(False, "③ 암호화 백업", e.code + " — 원본은 그대로 둡니다")
            return 1
    else:
        say(None, "③ 암호화 백업", "새로 옮길 것이 없어 건너뜀")
    failed = False
    for it in items:
        try:
            if it["id"] == "teacher_config":
                r = migrate_teacher_config(stamp)
                if r.get("already"):
                    say(r["ok"], "④⑤ 개인 설정", f"이미 보호 저장됨 · 합쳐 읽기 원본과 같음 {r['same']}/{r['total']}"
                        + (f" · 평문에 다시 생긴 항목 {r['restowed']}개 다시 옮김" if r.get("restowed") else ""))
                else:
                    say(r["ok"], "④ 개인 설정 암호화", f"{short(T.PRIVATE_FILE)}")
                    say(not r["diff_keys"], "⑤ 읽기 호환", f"기존 코드가 읽는 값 원본과 같음 {r['same']}/{r['total']}"
                        + (f" · 다른 항목 이름 {r['diff_keys']}" if r["diff_keys"] else ""))
                    if r.get("plain_compat"):
                        say(None, "   호환 모드", "EVPN 도우미가 옛 판이라 인증서 이름은 평문에도 남겨 둠 → 도우미 새 판 설치 뒤 finish")
                    say(True, "   EVPN 비밀번호 방식", r.get("evpn_password_mode", "unset"))
                if not r["ok"]:
                    failed = True
            elif it["id"] == "google_token":
                r = migrate_google_token()
                say(r["ok"], "④ 구글 토큰 암호화", f"갱신 시험 {r['refresh']} · 평문 삭제 {'예' if r['plain_deleted'] else '아니오(갱신 확인 전이라 남김)'}")
                failed |= not r["ok"]
            rec["steps"][it["id"]] = {k: v_ for k, v_ in r.items() if k not in ("diff_keys",)}
        except S.SecureError as e:
            say(False, f"④ {it['id']}", e.code)
            rec["steps"][it["id"]] = {"ok": False, "code": e.code}
            failed = True
        except Exception as e:
            say(False, f"④ {it['id']}", "E_UNEXPECTED:" + type(e).__name__)
            rec["steps"][it["id"]] = {"ok": False, "code": "E_UNEXPECTED:" + type(e).__name__}
            failed = True
    if failed and rec.get("backup"):
        say(False, "   되돌리기", "실패한 단계가 있어 백업으로 되돌립니다")
        restore(rec["backup"])
        rec["rolled_back"] = True
    n = cleanup_temp()
    say(True, "⑦ 임시 파일 정리", f"{n}개")
    if helper_installed():
        st = helper_selftest()
        if st.get("code") == "E_HELPER_OLD":
            say(None, "   EVPN 도우미 확인", "옛 판(자가 점검 없음) — 평문의 인증서 이름으로 계속 동작")
        else:
            say(st.get("ok"), "   EVPN 도우미 확인", f"인증서 이름 {st.get('cert_source')} · 암호 {st.get('password')}")
    j["runs"].append(rec)
    save_journal(j)
    return 1 if failed else 0


def finish() -> int:
    """도우미 새 판이 설치된 뒤에만: 평문에 «호환»으로 남겨 둔 인증서 이름을 뺀다. 도우미가 못 찾으면 되돌린다."""
    if not helper_supports_dpapi():
        say(False, "도우미 새 판", "아직 옛 판입니다 → 먼저 python evpn.py install (UAC [예] 한 번)")
        return 1
    plain = json.loads(T.CONFIG_FILE.read_text(encoding="utf-8"))
    if not T.is_split_mode(plain):
        say(False, "보호 저장", "아직 이전 전입니다 → python secure_migrate.py run")
        return 1
    marker = plain[T.SECURE_MARKER]
    if not marker.get("plain_compat"):
        say(None, "호환 항목", "평문에 남긴 인증서 이름이 이미 없습니다")
        return 0
    merged = T.load_merged()
    marker = {**marker, "plain_compat": []}
    T.save_config({**merged, T.SECURE_MARKER: marker})
    st = helper_selftest()
    if not st.get("ok") or st.get("cert_source") not in ("dpapi", "dpapi-prev"):
        T.save_config({**merged, T.SECURE_MARKER: {**marker, "plain_compat": ["cert_name"]}})
        say(False, "도우미가 암호화 파일에서 인증서 이름을 못 찾음", f"{st} — 평문 호환으로 되돌림")
        return 1
    same, total, diff = _compare(merged, T.load_merged())
    say(not diff, "평문에서 인증서 이름 뺌", f"도우미 확인 {st.get('cert_source')} · 합쳐 읽기 같음 {same}/{total}")
    j = load_journal()
    j["runs"].append({"at": S.now_stamp(), "action": "finish", "ok": not diff, "plain_compat": [],
                      "helper_cert_source": st.get("cert_source"), "same": same, "total": total})
    save_journal(j)
    return 0 if not diff else 1


def status() -> int:
    j = load_journal()
    for r in j.get("runs", [])[-10:]:
        print(json.dumps(r, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="plan", choices=["plan", "run", "finish", "restore", "status"])
    ap.add_argument("--stamp")
    a = ap.parse_args()
    return {"plan": plan, "run": run, "finish": finish, "status": status,
            "restore": lambda: restore(a.stamp)}[a.cmd]()


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""보안 설정창을 «선생님 화면에» 띄우고, 클로드 쪽에는 결과 코드만 돌려준다.

  python secure_settings.py                 나이스·에듀파인 탭으로 연다 (최대 10분 기다린다)
  python secure_settings.py --tab riro      리로스쿨 상벌점 탭
  python secure_settings.py --tab accounts  계정 관리 탭
  python secure_settings.py --no-wait       띄우기만 하고 바로 끝낸다 (결과는 python secure_settings.py --last)
  python secure_settings.py --last          마지막으로 연 창의 결과 코드

창은 pythonw 로 따로 뜬다 — 입력·출력이 이 터미널(=클로드가 보는 곳)과 이어지지 않는다.
여기 찍히는 것: 창 상태(saved / cancelled / closed / open / failed), 항목 이름, 동작, 결과 코드. 값은 없다.
"""
import argparse
import ctypes
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

KIT = Path(__file__).resolve().parent
PYW = KIT / ".venv" / "Scripts" / "pythonw.exe"
LAST = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "neis-automation" / "secure_settings_last.json"


def packaged() -> bool:
    try:
        n = ctypes.c_uint32(0)
        return ctypes.windll.kernel32.GetCurrentPackageFullName(ctypes.byref(n), None) == 122
    except Exception:
        return False


def show(res: dict) -> None:
    print(f"창 상태: {res.get('status')}")
    for e in res.get("events", []):
        extra = f" · 바뀐 칸 {len(e['changed'])}개({', '.join(e['changed'])})" if e.get("changed") else ""
        print(f"  {e.get('t', '')} {e.get('item')} {e.get('action')} → {e.get('code')}{extra}")
    if not res.get("events"):
        print("  (저장·시험한 것 없음)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tab", default="neis", choices=["neis", "riro", "accounts"])
    ap.add_argument("--wait", type=int, default=600)
    ap.add_argument("--no-wait", action="store_true")
    ap.add_argument("--last", action="store_true")
    a = ap.parse_args()
    LAST.parent.mkdir(parents=True, exist_ok=True)
    if a.last:
        try:
            show(json.loads(LAST.read_text(encoding="utf-8")))
            return 0
        except Exception:
            print("마지막 결과가 없습니다")
            return 1
    if not PYW.exists():
        print("❌ .venv 의 pythonw.exe 가 없습니다 — setup.ps1 을 먼저")
        return 1
    if packaged():
        # 클로드 앱(MSIX) 안에서 띄우면 %LOCALAPPDATA% 쓰기가 앱 전용 폴더로 갈 수 있다 → explorer 로 밖에서 띄운다
        cmd = KIT / "secure_settings_open.cmd"
        subprocess.Popen(["explorer.exe", str(cmd)])
        print("🪟 설정창을 띄웠습니다 (앱 밖에서). 끝나면 python secure_settings.py --last 로 결과를 봅니다.")
        return 0
    LAST.write_text(json.dumps({"status": "starting", "events": []}), encoding="utf-8")
    proc = subprocess.Popen([str(PYW), str(KIT / "secure_settings.pyw"), "--result", str(LAST), "--tab", a.tab],
                            cwd=str(KIT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            close_fds=True, creationflags=0x00000008 | 0x00000200)     # DETACHED · 새 그룹
    print("🪟 보안 설정창을 띄웠습니다. 작업표시줄의 «보안 설정» 창에서 넣어 주세요.", flush=True)
    print("   (비밀번호는 그 창에만 넣습니다. 여기에는 결과 코드만 나옵니다)", flush=True)
    if a.no_wait:
        return 0
    end = time.time() + a.wait
    while time.time() < end and proc.poll() is None:
        time.sleep(2)
    try:
        res = json.loads(LAST.read_text(encoding="utf-8"))
    except Exception:
        res = {"status": "unknown", "events": []}
    if proc.poll() is None:
        res["status"] = "still_open"
        print("⏱ 창이 아직 열려 있습니다. 끝나면 «설정창 끝났어» 라고 말씀해 주세요 (python secure_settings.py --last).")
    show(res)
    return 0 if res.get("status") in ("saved", "closed", "still_open") else 1


if __name__ == "__main__":
    sys.exit(main())

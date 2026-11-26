# -*- coding: utf-8 -*-
"""EVPN 연결 → 작업 한 번 → 끊기를 한 번에 한다. 학교 밖에서는 늘 이걸로 돌린다.

  python run_vpn.py --auto -- python widget/바로가기_엔진.py neis open --hold
  python run_vpn.py --minutes 4 -- python neis_session.py --login-test

왜 한 번에 해야 하나:
  부산·전남·강원·경기 등은 VPN 이 붙어 있는 동안 일반 인터넷이 막힌다 → 그동안 클로드도 응답을 못 한다.
  그래서 «연결하고, 클로드와 대화하며 작업하고, 나중에 끊는» 방식은 안 된다. 연결·작업·끊기를
  스크립트 하나가 끝까지 해야 클로드가 다시 말할 수 있다. (서울은 VPN 중에도 인터넷이 된다 — 09-18 실측)

안전장치:
  - 작업이 시간(기본 6분 30초)을 넘기면 멈추고 끊는다
  - 이 스크립트가 강제로 죽어도(클로드 도구 시간 초과 등) 따로 떠 있는 감시자가 VPN 을 끊는다
  - 시작 전에 이미 연결돼 있었으면(선생님이 직접 연결) 끝나도 끊지 않는다
  - 마지막 줄에 RESULT_JSON {...} 을 항상 찍는다 — 클로드는 이 줄로 결과를 판단한다

바탕화면 글자 위젯(나이스·에듀파인 글자)용 (2026-11-10):
  python run_vpn.py --auto --minutes 240 -- python widget/바로가기_엔진.py neis open --hold
  - --auto : 접속 방식이 «학교 업무망»이면 VPN 없이 바로 돌린다. «EVPN»이거나 아직 안 골랐는데 AXGATE 가 깔려 있으면 VPN
  - 우리가 VPN 을 붙였을 때만 작업에 RUN_VPN_OWNER=1 을 넘긴다 → 엔진의 --hold 가 창이 다 닫힐 때까지 기다린다
    (선생님이 미리 연결해 둔 VPN·다른 클릭이 붙인 VPN 이면 넘기지 않는다 — 끊을 사람은 하나뿐)
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import teacher_config
import evpn

STATE = teacher_config.CONFIG_DIR / "run_vpn_state.json"


def _alive(pid: int) -> bool:
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                       creationflags=evpn._NO_WINDOW)
    return str(pid) in (r.stdout or "")


def watchdog(parent: int, deadline: float) -> None:
    """부모가 죽거나 시간이 지났는데 «우리가 연결한 VPN» 이 남아 있으면 끊는다."""
    while True:
        time.sleep(3)
        try:
            st = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            return
        if st.get("finished"):
            return
        if not _alive(parent) or time.time() > deadline:
            if st.get("we_connected"):
                evpn.disconnect()
            st["finished"] = True
            st["by_watchdog"] = True
            STATE.write_text(json.dumps(st), encoding="utf-8")
            return


def internet_ok() -> bool:
    import urllib.request
    try:
        urllib.request.urlopen("https://www.google.com/generate_204", timeout=6)
        return True
    except Exception:
        return False


def need_vpn() -> bool:
    """--auto 용: 이 PC 가 나이스·업무포털에 EVPN 으로 들어가야 하나."""
    mode = teacher_config.load_config_or_empty().get("access_mode") or ""
    if mode == "school_net":
        return False
    if mode == "evpn":
        return True
    return evpn.client_installed()      # 아직 안 고른 분: AXGATE 가 깔려 있으면 EVPN 으로 본다


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=6.5, help="작업에 줄 시간 (기본 6.5분)")
    ap.add_argument("--auto", action="store_true", help="접속 방식이 학교 업무망이면 VPN 없이 바로 돌린다")
    ap.add_argument("--watchdog", nargs=2, help=argparse.SUPPRESS)
    ap.add_argument("command", nargs=argparse.REMAINDER)
    a = ap.parse_args()

    if a.watchdog:
        watchdog(int(a.watchdog[0]), float(a.watchdog[1]))
        return 0

    cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
    result = {"connected": False, "work_exit": None, "disconnected": None, "internet_after": None}
    if not cmd:
        print("사용법: python run_vpn.py --minutes 4 -- python neis_session.py --login-test")
        return 2
    if cmd[0] in ("python", "py", "python.exe"):
        # 키트 가상환경 파이썬을 쓴다. run_vpn 자체를 시스템 파이썬으로 부르면 sys.executable 도
        # 시스템 것이라, 작업 스크립트가 라이브러리를 못 찾고 죽었다 (2026-09-18 샌드박스 Sonnet 실측)
        venv_py = Path(__file__).resolve().parent / ".venv" / "Scripts" / "python.exe"
        cmd[0] = str(venv_py) if venv_py.exists() else sys.executable

    env = {k: v for k, v in os.environ.items() if k != "RUN_VPN_OWNER"}
    if a.auto and not need_vpn():
        print("ℹ️  접속 방식이 학교 업무망이라 VPN 없이 엽니다", flush=True)
        result["vpn"] = "not_needed"
        try:
            result["work_exit"] = subprocess.run(cmd, timeout=a.minutes * 60, stdin=subprocess.DEVNULL, env=env).returncode
        except subprocess.TimeoutExpired:
            result["work_exit"] = "timeout"
        print("RESULT_JSON " + json.dumps(result, ensure_ascii=False), flush=True)
        return 0 if result["work_exit"] == 0 else 1

    status = evpn.tap_status()                 # 한 번만 묻는다 (PowerShell 이라 한 번에 2초쯤 — 예전엔 두 번 물었다, 2026-11-10)
    already = status == "Up"
    if status == "unknown":
        print("⚠️  VPN 상태를 읽지 못했습니다 — 그대로 진행합니다")
    deadline = time.time() + a.minutes * 60 + 150      # 연결 70초 + 끊기 여유
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"we_connected": False, "finished": False}), encoding="utf-8")
    # 감시자는 창 없는 pythonw 로 띄운다. 예전에는 python + DETACHED 였는데, 가상환경 python.exe 는 진짜 파이썬을
    # 한 번 더 띄우는 «징검다리»라서 콘솔 없이 떠난 징검다리가 새 콘솔을 만들었다 → 기본 터미널이 «Windows 터미널»인
    # PC 에서 빈 검은 창이 4시간 떠 있었다 (2026-11-10 글자 위젯 실측). pythonw 는 콘솔 자체가 없다
    watchdog_py = Path(sys.executable).with_name("pythonw.exe")
    subprocess.Popen([str(watchdog_py) if watchdog_py.exists() else sys.executable, "-X", "utf8", str(Path(__file__).resolve()),
                      "--watchdog", str(os.getpid()), str(deadline)],
                     creationflags=evpn._NO_WINDOW | 0x00000200,   # 창 없음 · 새 그룹 (부모 콘솔의 Ctrl+C 를 안 받는다)
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True)

    try:
        if already:
            print("ℹ️  VPN 이 이미 연결되어 있어 그대로 씁니다 (끝나도 끊지 않습니다)")
            result["connected"] = True
        else:
            # 연결을 «시도하기 전에» 기록 — 도중에 죽어도 감시자가 끊는다
            STATE.write_text(json.dumps({"we_connected": True, "finished": False}), encoding="utf-8")
            try:
                result["connected"] = evpn.connect()
            except Exception as e:
                # 예외 원문 전체(호출 기록·요청 머리글)는 버리고 첫 줄만 — 글자 위젯 알림 창에 이 줄이 «이유»로 뜬다
                first = (str(e).splitlines() or [""])[0][:160]
                if type(e).__name__ == "TargetClosedError":
                    # 포털 크롬이 연결 도중 닫혔다 — 대개 사람이 [X] 를 눌렀다 (2026-11-12 «준비 중» 에서 멈춘 듯 보여 닫으심)
                    print("❌  EVPN 포털 창이 연결 도중 닫혀 멈췄습니다 — 글자를 다시 누르면 처음부터 합니다", flush=True)
                else:
                    print(f"❌  EVPN 연결 중 오류: {type(e).__name__}: {first}", flush=True)
                result["connected"] = False
                result["error"] = type(e).__name__
        if result["connected"]:
            print(f"▶️  작업 시작 (최대 {a.minutes:g}분): {' '.join(cmd[1:]) if cmd[0].lower().endswith('python.exe') else ' '.join(cmd)}",
                  flush=True)
            if not already:
                env["RUN_VPN_OWNER"] = "1"     # 이 VPN 은 우리가 붙였다 — 작업이 «창이 닫힐 때까지» 붙잡아도 된다
            try:
                r = subprocess.run(cmd, timeout=a.minutes * 60, stdin=subprocess.DEVNULL, env=env)
                result["work_exit"] = r.returncode
            except subprocess.TimeoutExpired:
                result["work_exit"] = "timeout"
                print(f"⏱  {a.minutes:g}분을 넘겨 작업을 멈췄습니다")
    finally:
        if not already:
            result["disconnected"] = evpn.disconnect()
        STATE.write_text(json.dumps({"we_connected": False, "finished": True}), encoding="utf-8")
        result["internet_after"] = internet_ok()
        print("RESULT_JSON " + json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if (result["connected"] and result["work_exit"] == 0) else 1


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""바탕화면 «나이스» 글자 → run_vpn --auto → 엔진 --hold (2026-11-10).
진짜 VPN·크롬·설정은 건드리지 않는다 — evpn 연결/끊기, 크롬 포트 확인, 설정 읽기를 모두 바꿔 끼운다."""
import _guard  # noqa: F401  ← 맨 먼저: 진짜 설정 폴더·금고 항목을 못 건드리게
import asyncio
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))
import run_vpn  # noqa: E402

_cwd = os.getcwd()
_path = KIT / "widget" / "바로가기_엔진.py"
_spec = importlib.util.spec_from_file_location("widget_engine", str(_path),
                                               loader=importlib.machinery.SourceFileLoader("widget_engine", str(_path)))
E = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(E)
os.chdir(_cwd)                       # 엔진은 import 할 때 키트 폴더로 옮겨 간다


class NeedVpn(unittest.TestCase):
    def check(self, mode, installed):
        with mock.patch.object(run_vpn.teacher_config, "load_config_or_empty", return_value={"access_mode": mode}), \
             mock.patch.object(run_vpn.evpn, "client_installed", return_value=installed):
            return run_vpn.need_vpn()

    def test_modes(self):
        self.assertFalse(self.check("school_net", True))
        self.assertTrue(self.check("evpn", False))
        self.assertTrue(self.check("", True))        # 아직 안 고름 + AXGATE 있음 → EVPN
        self.assertFalse(self.check("", False))


class AutoRun(unittest.TestCase):
    """run_vpn.main 을 --auto 로 돌려 본다. 작업(subprocess.run)은 실제로 띄우지 않고 받은 env 만 본다."""

    def run_main(self, *, need, tap, connected=True):
        calls = {"connect": 0, "disconnect": 0, "env": None}

        def fake_run(cmd, **kw):
            calls["env"] = kw.get("env")
            return subprocess.CompletedProcess(cmd, 0)

        def fake_connect():
            calls["connect"] += 1
            return connected

        def fake_disconnect():
            calls["disconnect"] += 1
            return True

        argv = ["run_vpn.py", "--auto", "--minutes", "1", "--", "python", "-c", "pass"]
        out = io.StringIO()
        with mock.patch.object(sys, "argv", argv), \
             mock.patch.object(run_vpn, "need_vpn", return_value=need), \
             mock.patch.object(run_vpn.evpn, "tap_status", return_value=tap), \
             mock.patch.object(run_vpn.evpn, "connect", side_effect=fake_connect), \
             mock.patch.object(run_vpn.evpn, "disconnect", side_effect=fake_disconnect), \
             mock.patch.object(run_vpn.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(run_vpn.subprocess, "Popen"), \
             mock.patch.object(run_vpn, "internet_ok", return_value=True), \
             mock.patch.dict(os.environ, {"RUN_VPN_OWNER": "1"}), \
             redirect_stdout(out):
            code = run_vpn.main()
        last = out.getvalue().strip().splitlines()[-1]
        self.assertTrue(last.startswith("RESULT_JSON "), out.getvalue())
        return code, calls, json.loads(last[len("RESULT_JSON "):])

    def test_school_net_skips_vpn(self):
        code, calls, res = self.run_main(need=False, tap="Down")
        self.assertEqual(code, 0)
        self.assertEqual((calls["connect"], calls["disconnect"]), (0, 0))
        self.assertEqual(res["vpn"], "not_needed")
        self.assertNotIn("RUN_VPN_OWNER", calls["env"])       # 바깥에서 물려받은 표시도 지운다

    def test_we_connect_so_we_own_and_disconnect(self):
        code, calls, res = self.run_main(need=True, tap="Down")
        self.assertEqual(code, 0)
        self.assertEqual((calls["connect"], calls["disconnect"]), (1, 1))
        self.assertEqual(calls["env"].get("RUN_VPN_OWNER"), "1")

    def test_already_connected_is_not_ours(self):
        code, calls, res = self.run_main(need=True, tap="Up")
        self.assertEqual((calls["connect"], calls["disconnect"]), (0, 0))
        self.assertNotIn("RUN_VPN_OWNER", calls["env"])

    def test_connect_failure_does_not_run_work(self):
        code, calls, res = self.run_main(need=True, tap="Down", connected=False)
        self.assertEqual(code, 1)
        self.assertIsNone(calls["env"])                        # 작업을 아예 안 돌렸다
        self.assertEqual(calls["disconnect"], 1)               # 반쯤 붙은 것도 정리


class Fixes1110(unittest.TestCase):
    """11-10 진짜 클릭에서 드러난 것들."""

    def test_node_uses_windows_cert_store(self):
        # 앱 밖에서도 Playwright 의 Node 가 Windows 인증서 저장소를 쓴다 (EVPN 포털이 중간 고리를 빼고 보낸다)
        self.assertEqual(os.environ.get("NODE_USE_SYSTEM_CA"), "1")

    def test_portal_script_failure_does_not_hang(self):
        import asyncio
        captured = {}

        class Ctx:
            async def route(self, pattern, handler):
                captured["handler"] = handler

        route = mock.MagicMock()
        route.fetch = mock.AsyncMock(side_effect=RuntimeError("Route.fetch: unable to verify the first certificate"))
        route.continue_ = mock.AsyncMock()
        route.fulfill = mock.AsyncMock()

        async def go():
            await run_vpn.evpn._block_axvpn(Ctx())
            await captured["handler"](route)
        with redirect_stdout(io.StringIO()) as out:
            asyncio.run(go())
        route.continue_.assert_awaited_once()          # 원래 스크립트로 넘겨 페이지가 멈추지 않는다
        route.fulfill.assert_not_awaited()
        self.assertIn("포털 스크립트를 고치지 못해", out.getvalue())

    def test_agent_listening_uses_psutil(self):
        import psutil
        conn = mock.Mock(status=psutil.CONN_LISTEN, laddr=mock.Mock(port=run_vpn.evpn.AGENT_PORT))
        with mock.patch.object(psutil, "net_connections", return_value=[conn]), \
             mock.patch.object(run_vpn.evpn, "_ps", side_effect=AssertionError("PowerShell 을 부르면 느리다")):
            self.assertTrue(run_vpn.evpn.agent_listening())
        with mock.patch.object(psutil, "net_connections", return_value=[]), \
             mock.patch.object(run_vpn.evpn, "_ps", side_effect=AssertionError("PowerShell 을 부르면 느리다")):
            self.assertFalse(run_vpn.evpn.agent_listening())

    def test_connect_exception_becomes_reason_line_and_disconnects(self):
        calls = {"disconnect": 0, "run": 0, "popen": None}

        def boom():
            raise RuntimeError("Page.goto: Timeout 30000ms exceeded.\nCall log:\n  - sec-ch-ua-platform: \"Windows\"")

        def fake_popen(args, **kw):
            calls["popen"] = (args, kw)
            return mock.Mock()

        out = io.StringIO()
        argv = ["run_vpn.py", "--auto", "--minutes", "1", "--", "python", "-c", "pass"]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(run_vpn, "need_vpn", return_value=True), \
             mock.patch.object(run_vpn.evpn, "tap_status", return_value="Down"), \
             mock.patch.object(run_vpn.evpn, "connect", side_effect=boom), \
             mock.patch.object(run_vpn.evpn, "disconnect", side_effect=lambda: calls.__setitem__("disconnect", 1) or True), \
             mock.patch.object(run_vpn.subprocess, "run", side_effect=lambda *a, **k: calls.__setitem__("run", 1)), \
             mock.patch.object(run_vpn.subprocess, "Popen", side_effect=fake_popen), \
             mock.patch.object(run_vpn, "internet_ok", return_value=True), redirect_stdout(out):
            code = run_vpn.main()
        text = out.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("❌  EVPN 연결 중 오류: RuntimeError: Page.goto: Timeout 30000ms exceeded.", text)
        self.assertNotIn("sec-ch-ua", text)                    # 호출 기록·머리글은 버린다
        self.assertEqual((calls["run"], calls["disconnect"]), (0, 1))
        args, kw = calls["popen"]                                 # 감시자: 창 없는 pythonw · DETACHED(0x8) 안 씀
        if Path(sys.executable).with_name("pythonw.exe").exists():
            self.assertTrue(args[0].lower().endswith("pythonw.exe"))
        self.assertFalse(kw["creationflags"] & 0x00000008)


class FasterConnect(unittest.TestCase):
    """11-10: 누르고 EVPN 포털 창이 뜨기까지 47초 — 그중 30초는 늘 «안 뜬» 에이전트 포트 대기였다."""

    def test_agent_prewait_is_short(self):
        self.assertLessEqual(run_vpn.evpn.AGENT_PREWAIT_SEC, 10)
        clock = {"t": 0.0}
        with mock.patch.object(run_vpn.evpn, "agent_listening", return_value=False), \
             mock.patch.object(run_vpn.evpn.time, "monotonic", side_effect=lambda: clock["t"]), \
             mock.patch.object(run_vpn.evpn.time, "sleep", side_effect=lambda s: clock.__setitem__("t", clock["t"] + s)):
            self.assertFalse(run_vpn.evpn.wait_agent(5))
        self.assertLessEqual(clock["t"], 5.5)                        # 5초 남짓에서 포기하고 포털로 넘어간다
        with mock.patch.object(run_vpn.evpn, "agent_listening", return_value=True):
            self.assertTrue(run_vpn.evpn.wait_agent(5))

    def test_disconnect_agent_wait_is_short_too(self):
        # 시작 메뉴 «VPN 끊기» 도 같은 30초 헛기다림이 있었다 (11-10 «검은 창만 떠 있다»)
        with mock.patch.object(run_vpn.evpn, "agent_listening", return_value=False), \
             mock.patch.object(run_vpn.evpn, "start_helper", return_value=True), \
             mock.patch.object(run_vpn.evpn, "wait_agent", return_value=False) as waited:
            self.assertFalse(run_vpn.evpn._ensure_agent())
        self.assertLessEqual(waited.call_args.args[0], 10)

    def test_stale_chrome_cleanup_only_touches_evpn_profile_and_skips_wait(self):
        import psutil
        # 11-26: 이름만이 아니라 전체 경로가 같아야 닫는다 (test_stale_chrome_cleanup_matches_full_profile_path_only)
        evpn_chrome = mock.Mock(info={"name": "chrome.exe", "cmdline": ["chrome.exe", f"--user-data-dir={run_vpn.evpn.PROFILE_DIR}"]})
        my_chrome = mock.Mock(info={"name": "chrome.exe", "cmdline": ["chrome.exe", "--profile-directory=Default"]})
        with mock.patch.object(psutil, "process_iter", return_value=[evpn_chrome, my_chrome]), \
             mock.patch.object(run_vpn.evpn.time, "sleep") as slept:
            run_vpn.evpn._kill_stale_chrome()
        evpn_chrome.kill.assert_called_once()
        my_chrome.kill.assert_not_called()                           # 선생님 평소 크롬은 건드리지 않는다
        slept.assert_called_once()
        with mock.patch.object(psutil, "process_iter", return_value=[my_chrome]), \
             mock.patch.object(run_vpn.evpn.time, "sleep") as slept, \
             mock.patch.object(run_vpn.evpn, "_ps", side_effect=AssertionError("PowerShell 을 부르면 느리다")):
            run_vpn.evpn._kill_stale_chrome()
        slept.assert_not_called()                                    # 닫은 게 없으면 1초도 안 기다린다

    def test_vpn_status_asked_once(self):
        argv = ["run_vpn.py", "--auto", "--minutes", "1", "--", "python", "-c", "pass"]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(run_vpn, "need_vpn", return_value=True), \
             mock.patch.object(run_vpn.evpn, "tap_status", return_value="Up") as tap, \
             mock.patch.object(run_vpn.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
             mock.patch.object(run_vpn.subprocess, "Popen"), \
             mock.patch.object(run_vpn, "internet_ok", return_value=True), redirect_stdout(io.StringIO()):
            run_vpn.main()
        self.assertEqual(tap.call_count, 1)


class SlowNeisFirstLoad(unittest.TestCase):
    """EVPN 직후 첫 나이스: 뼈대까지 51초 — 30초·load 로 실패 치지 않는다 (11-10 실측)."""

    def run_login(self, goto_error, url_after):
        import asyncio
        import neis_session as NS
        page = mock.MagicMock()
        page.url = url_after
        page.on = mock.Mock()
        page.goto = mock.AsyncMock(side_effect=goto_error)
        cfg = {"neis_url": "https://sen.neis.go.kr/", "cert_name": "가상인증서"}
        with mock.patch.object(NS, "auto_cert_login", new=mock.AsyncMock()) as cert, \
             mock.patch.object(NS, "wait_manual_login", new=mock.AsyncMock()), redirect_stdout(io.StringIO()):
            asyncio.run(NS.login(page, cfg, pw=None))
        return page, cert

    def test_waits_for_dom_ready_up_to_two_minutes(self):
        page, cert = self.run_login(None, "https://sen.neis.go.kr/jsp/main.jsp")
        kw = page.goto.await_args.kwargs
        self.assertEqual(kw["wait_until"], "domcontentloaded")
        self.assertGreaterEqual(kw["timeout"], 120_000)
        cert.assert_awaited_once()

    def test_timeout_on_neis_page_still_goes_on_to_login(self):
        page, cert = self.run_login(TimeoutError("Page.goto: Timeout 120000ms exceeded."), "https://sen.neis.go.kr/jsp/main.jsp")
        cert.assert_awaited_once()                             # 화면이 오는 중이면 단추를 기다린다

    def test_timeout_off_neis_is_still_a_failure(self):
        import neis_session as NS
        with self.assertRaises(NS.LoginBlocked):
            self.run_login(TimeoutError("Page.goto: Timeout 120000ms exceeded."), "about:blank")


class Hold(unittest.TestCase):
    def test_waits_until_both_widget_chromes_close(self):
        # 나이스(9322)·에듀파인(9324) 포트 상태가 바뀌는 순서 — 둘 다 두 번 연속 없어야 끝
        states = iter([(True, False), (False, True), (False, False), (False, True), (False, False), (False, False)])
        current = {}

        def fake_alive(port):
            if port == E.SITES["neis"]["port"]:
                current["now"] = next(states)
                return current["now"][0]
            return current["now"][1]

        sleeps = []
        with mock.patch.object(E, "alive", side_effect=fake_alive), \
             mock.patch.object(E.time, "sleep", side_effect=sleeps.append), redirect_stdout(io.StringIO()):
            E.hold_vpn(poll=5)
        self.assertEqual(len(sleeps), 5)                       # 6번 보고 끝 (마지막 두 번이 연속 «없음»)

    def run_engine_main(self, argv, owner):
        env = {"RUN_VPN_OWNER": "1"} if owner else {}
        with mock.patch.object(sys, "argv", ["엔진"] + argv), \
             mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(E, "edufine_cmd") as cmd, \
             mock.patch.object(E, "hold_vpn") as hold, \
             mock.patch.object(E, "watchdog") as dog:
            if not owner:
                os.environ.pop("RUN_VPN_OWNER", None)
            E.main()
        return cmd, hold, dog

    def test_hold_only_when_we_own_the_vpn(self):
        _, hold, dog = self.run_engine_main(["edufine", "main", "--hold"], owner=True)
        hold.assert_called_once()
        dog.return_value.cancel.assert_called_once()           # 15분 시간 초과를 끈 뒤에 기다린다
        _, hold, _ = self.run_engine_main(["edufine", "main", "--hold"], owner=False)
        hold.assert_not_called()
        _, hold, _ = self.run_engine_main(["edufine", "main"], owner=True)
        hold.assert_not_called()
        _, hold, _ = self.run_engine_main(["edufine", "warm", "--hold"], owner=True)
        hold.assert_not_called()

    def test_failed_open_never_holds(self):
        with mock.patch.object(sys, "argv", ["엔진", "edufine", "main", "--hold"]), \
             mock.patch.dict(os.environ, {"RUN_VPN_OWNER": "1"}), \
             mock.patch.object(E, "edufine_cmd", side_effect=RuntimeError("크롬이 안 떴어요")), \
             mock.patch.object(E, "hold_vpn") as hold, mock.patch.object(E, "watchdog"), \
             redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit):
                E.main()
        hold.assert_not_called()
        self.assertIn("FAIL 크롬이 안 떴어요", out.getvalue())


class Disconnect1111(unittest.TestCase):
    """11-11: 끊기 포털 창을 화면 밖에 띄운 뒤 — 그 창을 선생님이 닫거나, 끊기가 둘 겹쳐도 호출 기록을 쏟지 않는다."""
    ev = run_vpn.evpn

    class _FakePW:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    def _disconnect(self, launch, taps):
        import playwright.async_api as pa
        seq = iter(taps)
        with mock.patch.object(pa, "async_playwright", return_value=self._FakePW()), \
             mock.patch.object(self.ev, "_launch", side_effect=launch), \
             mock.patch.object(self.ev, "tap_status", side_effect=lambda: next(seq)), \
             mock.patch.object(self.ev, "_ensure_agent"), mock.patch.object(self.ev, "stop_helper"), \
             redirect_stdout(io.StringIO()) as out:
            ok = self.ev.disconnect()
        return ok, out.getvalue()

    def test_portal_closed_midway_but_vpn_down_is_success(self):
        class Ctx:
            async def close(self):
                raise RuntimeError("이미 닫힘")        # 닫힌 크롬의 close() 가 또 터져도 넘어간다
        page = mock.Mock()
        page.goto = mock.AsyncMock(side_effect=RuntimeError("Target page, context or browser has been closed"))

        async def launch(p, offscreen=False):
            return Ctx(), page
        ok, text = self._disconnect(launch, ["Up", "Down"])
        self.assertTrue(ok)
        self.assertIn("포털 창이 도중에 닫혔습니다 (RuntimeError)", text)
        self.assertIn("✅  EVPN 끊김 (화면 밖 창)", text)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("Target page", text)                  # 원문은 버린다

    def test_any_error_outside_logout_is_one_line(self):
        async def launch(p, offscreen=False):
            raise AssertionError("never")
        with mock.patch.object(self.ev, "disconnect_async", side_effect=RuntimeError("뭔가\n호출 기록")):
            ok, text = self._disconnect(launch, ["Up"])
        self.assertFalse(ok)
        self.assertIn("⚠️  끊는 중 오류 (RuntimeError)", text)
        self.assertIn("«VPN 끊기» 를 한 번 더", text)
        self.assertNotIn("호출 기록", text)

    def test_offscreen_launch_args_and_window_memory_cleared(self):
        args = self.ev._offscreen_args()
        self.assertTrue(any(a.startswith("--window-position=-") for a in args))
        with mock.patch.object(self.ev, "PROFILE_DIR", Path(self.tmp) / "evpn"):
            prefs = Path(self.tmp) / "evpn" / "Default" / "Preferences"
            prefs.parent.mkdir(parents=True)
            prefs.write_text(json.dumps({"browser": {"window_placement": {"left": -3000}}}), encoding="utf-8")
            self.ev._allow_protocol()
            self.assertNotIn("window_placement", json.loads(prefs.read_text(encoding="utf-8"))["browser"])

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def test_closed_portal_window_during_connect_is_plain_words(self):
        # 11-12: 첫 연결에서 포털이 멈춘 듯 보여 선생님이 창을 닫으심 → TargetClosedError 원문 대신 쉬운 말
        TargetClosedError = type("TargetClosedError", (Exception,), {})

        def boom():
            raise TargetClosedError("Page.wait_for_timeout: Target page, context or browser has been closed")
        argv = ["run_vpn.py", "--auto", "--minutes", "1", "--", "python", "-c", "pass"]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(run_vpn, "need_vpn", return_value=True), \
             mock.patch.object(run_vpn.evpn, "tap_status", return_value="Down"), \
             mock.patch.object(run_vpn.evpn, "connect", side_effect=boom), \
             mock.patch.object(run_vpn.evpn, "disconnect", return_value=True), \
             mock.patch.object(run_vpn.subprocess, "Popen"), \
             mock.patch.object(run_vpn, "internet_ok", return_value=True), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(run_vpn.main(), 1)
        self.assertIn("❌  EVPN 포털 창이 연결 도중 닫혀 멈췄습니다", out.getvalue())
        self.assertNotIn("Target page", out.getvalue())

    def test_portal_close_is_bounded_and_cleans_up(self):
        class Stuck:                                   # 닫기가 안 끝나는 크롬
            async def close(self):
                await asyncio.sleep(3600)
        with mock.patch.object(self.ev, "_kill_stale_chrome") as kill:
            t0 = time.time()
            asyncio.run(self.ev._close(Stuck(), 0.2))
        self.assertLess(time.time() - t0, 5)
        kill.assert_called_once()

    def test_portal_turn_waits_for_the_other_and_survives_its_death(self):
        # 다른 프로세스가 차례를 3초 쥐고 있으면 기다린다 · 쥔 채 죽으면(WAIT_ABANDONED) 바로 넘겨받는다
        name = "Local\\NeisAutomation.EVPN.Portal.Test%d" % os.getpid()
        child = ("import ctypes,sys,time; k=ctypes.WinDLL('kernel32'); k.CreateMutexW.restype=ctypes.c_void_p; "
                 "h=k.CreateMutexW(None,True,sys.argv[1]); print('held',flush=True); time.sleep(float(sys.argv[2]))")
        for hold, die in ((3, False), (30, True)):
            pr = subprocess.Popen([getattr(sys, "_base_executable", sys.executable), "-c", child, name, str(hold)],
                                  stdout=subprocess.PIPE, text=True)
            self.addCleanup(pr.stdout.close)
            self.assertEqual(pr.stdout.readline().strip(), "held")
            if die:
                pr.kill()
                pr.wait()
            t0 = time.time()
            with mock.patch.object(self.ev, "PORTAL_MUTEX", name), redirect_stdout(io.StringIO()) as out:
                with self.ev._PortalTurn() as turn:
                    waited = time.time() - t0
                    self.assertTrue(turn.got)
            if die:
                self.assertLess(waited, 2)
            else:
                self.assertGreater(waited, 1.5)
                self.assertIn("끝나기를 기다립니다", out.getvalue())
                pr.wait()


class _PortalPage:
    """EVPN 포털 흉내 — 로그인 칸·[LOGOUT] 이 보이는지만 바꿔 끼운다 (값·네트워크 없음)."""
    url = "https://evpn.sen.go.kr/custom/index.html"

    def __init__(self, form=True, logout=False, logout_after_typing=False, disabled_polls=0, covered_clicks=0):
        self.shown = {"#username": form, "#password": form, "#logoutButton": logout}
        self.logout_after_typing = logout_after_typing
        self.disabled_polls = disabled_polls             # 아이디 칸이 «사용 중지»인 채 지나가는 확인 횟수
        self.covered_clicks = covered_clicks             # 공지 팝업이 덮어 실패하는 클릭 수
        self.hides = 0
        self.waits, self.clicked = [], []

    async def goto(self, *a, **kw):
        return None

    async def wait_for_timeout(self, ms):
        self.waits.append(ms)

    async def evaluate(self, script, *a, **kw):
        if "style.display" in script:                    # _hide_overlays
            self.hides += 1
            return None
        return "Yes"                                     # running_status 등

    async def click(self, sel, **kw):
        self.clicked.append(sel)

    def locator(self, sel):
        page = self

        class Loc:
            first = None

            async def count(self):
                return 1 if page.shown.get(sel) else 0

            async def is_visible(self):
                return bool(page.shown.get(sel))

            async def is_enabled(self):
                if sel == "#username" and page.disabled_polls > 0:
                    page.disabled_polls -= 1
                    return False
                return True

            async def wait_for(self, **kw):
                return None

            async def click(self, **kw):
                if page.covered_clicks > 0:
                    page.covered_clicks -= 1
                    raise Exception("Locator.click: Timeout 3000ms exceeded. <div id=layer_popup> intercepts pointer events")
                return None

            async def fill(self, v, **kw):
                return None

            async def press_sequentially(self, v, **kw):
                if sel == "#password" and page.logout_after_typing:
                    page.shown["#logoutButton"] = True

            async def dispatch_event(self, *a):
                page.clicked.append(sel)
        loc = Loc()
        loc.first = loc
        return loc


class PortalWait(unittest.TestCase):
    """포털이 뜬 뒤 아이디를 치기까지의 기다림 (2026-11-17) — 세션이 없을 땐 1초, 있을 수 있으면 예전대로 6초."""

    def run_open(self, page, expect_session):
        import evpn
        with redirect_stdout(io.StringIO()) as out:
            asyncio.run(evpn._open_portal(page, expect_session=expect_session))
        return out.getvalue()

    def test_no_session_expected_waits_about_one_second(self):
        page = _PortalPage()
        out = self.run_open(page, expect_session=False)
        self.assertLessEqual(sum(page.waits), 1000)
        self.assertNotIn(1500, page.waits)
        self.assertIn("포털 준비", out)
        self.assertIn("/custom/index.html", out)
        self.assertNotIn("evpn.sen.go.kr", out)          # 기록에는 경로만

    def test_no_session_counts_only_after_field_is_usable(self):
        """11-26: 칸이 «보이기만» 하고 사용 중지일 때 세면, 공지 팝업이 뜨기 전에 가림막을 치우고 만다."""
        page = _PortalPage(disabled_polls=4)
        self.run_open(page, expect_session=False)
        self.assertEqual(sum(page.waits), 3000)          # 사용 중지 4번(2초) + 풀린 뒤 1초

    def test_covered_field_is_uncovered_and_clicked_again(self):
        """11-26 실패: 가림막을 치운 직후 공지 팝업이 다시 떠 아이디 칸을 덮었다 → 30초 기다리지 않고 치우고 다시 누른다."""
        import evpn
        page = _PortalPage(covered_clicks=2)
        asyncio.run(evpn._click_field(page, page.locator("#username"), "아이디"))
        self.assertEqual(page.covered_clicks, 0)
        self.assertEqual(page.hides, 3)                  # 누를 때마다 먼저 치운다

    def test_field_that_stays_covered_gives_plain_error(self):
        import evpn
        page = _PortalPage(covered_clicks=99)
        with self.assertRaises(RuntimeError) as c:
            asyncio.run(evpn._click_field(page, page.locator("#username"), "아이디"))
        self.assertIn("E_EVPN_COVERED", str(c.exception))
        self.assertNotIn("intercepts", str(c.exception))  # playwright 원문은 버린다

    def test_disabled_field_reports_disabled_instead_of_covered(self):
        import evpn
        page = _PortalPage(disabled_polls=99, covered_clicks=99)
        with self.assertRaises(RuntimeError) as c:
            asyncio.run(evpn._click_field(page, page.locator("#username"), "아이디"))
        self.assertIn("E_EVPN_DISABLED", str(c.exception))

    def test_late_logout_during_username_click_retries_immediately(self):
        import evpn

        class RacePage(_PortalPage):
            def locator(self, sel):
                loc = super().locator(sel)
                if sel == "#username":
                    async def race_click(**kw):
                        self.shown["#logoutButton"] = True
                        raise Exception("Locator.click: Timeout 3000ms exceeded.")
                    loc.click = race_click
                return loc

        rp = RacePage(covered_clicks=99)
        with mock.patch.object(evpn, "evpn_origins", return_value=["https://evpn.sen.go.kr"]), \
                mock.patch("secure_store.check_origin"), \
                redirect_stdout(io.StringIO()) as out:
            ok = asyncio.run(evpn._login_once(rp, "fake-evpn-id", object()))
        self.assertFalse(ok)
        self.assertIn("남은 세션이 늦게 보였습니다", out.getvalue())
        self.assertEqual(rp.waits, [])                    # 6번(21초) 끌지 않고 바로 접는다

    def test_covered_password_keeps_covered_error_code(self):
        import evpn

        class PwCoveredPage(_PortalPage):
            def locator(self, sel):
                loc = super().locator(sel)
                if sel == "#password":
                    async def fail_click(**kw):
                        raise Exception("Locator.click: Timeout 3000ms exceeded.")
                    loc.click = fail_click
                return loc

        page = PwCoveredPage()
        with mock.patch.object(evpn, "evpn_origins", return_value=["https://evpn.sen.go.kr"]), \
                mock.patch("secure_store.check_origin"), \
                mock.patch("secure_store.reveal_for", side_effect=AssertionError("클릭 전에 금고를 열면 안 된다")):
            with self.assertRaises(RuntimeError) as c:
                asyncio.run(evpn._login_once(page, "fake-evpn-id", object()))
        self.assertIn("E_EVPN_COVERED", str(c.exception))

    def test_logout_hides_overlays_and_falls_back_to_dispatch_event(self):
        import evpn
        page = _PortalPage(form=True, logout=True, covered_clicks=99)
        with mock.patch.object(evpn, "tap_status", return_value="Down"), redirect_stdout(io.StringIO()):
            asyncio.run(evpn._logout(page))
        self.assertGreaterEqual(page.hides, 2)
        self.assertIn("#logoutButton", page.clicked)

    def test_stale_chrome_cleanup_matches_full_profile_path_only(self):
        """11-26: 이름만 같은 다른 폴더(시험용 임시 폴더 등)의 크롬은 닫지 않는다."""
        import evpn
        mine = str(evpn.PROFILE_DIR)
        other = str(Path(mine).parent.parent / "다른곳" / evpn.PROFILE_DIR.name)
        killed = []

        class Proc:
            def __init__(self, pid, cmd):
                self.pid, self.info = pid, {"name": "chrome.exe", "cmdline": cmd}

            def kill(self):
                killed.append(self.pid)
        procs = [Proc(1, ["chrome.exe", f"--user-data-dir={mine}"]),
                 Proc(2, ["chrome.exe", f"--user-data-dir={other}"]),
                 Proc(3, ["chrome.exe", "--type=renderer"])]
        with mock.patch("psutil.process_iter", return_value=procs), mock.patch.object(evpn.time, "sleep"):
            evpn._kill_stale_chrome()
        self.assertEqual(killed, [1])

    def test_portal_chrome_has_no_detection_hiding(self):
        """11-26: EVPN 포털 크롬에도 «자동화 탐지 피하기» 설정을 넣지 않는다."""
        import evpn
        self.assertFalse(any("AutomationControlled" in a for a in evpn.CHROME_ARGS))
        self.assertNotIn("add_init_script", Path(evpn.__file__).read_text(encoding="utf-8"))

    def test_portal_launch_turns_off_notice_popup_only_for_kit_chrome(self):
        import evpn
        src = Path(evpn.__file__).read_text(encoding="utf-8")
        self.assertIn('"maindiv_weekly", "value": "done", "url": evpn_url()', src)
        self.assertIn('_text(page, "running_status") == "Yes"', src)   # «설치 Yes» 가 아니라 «실행 Yes» 를 기다린다

    def test_session_possible_keeps_six_second_rule(self):
        page = _PortalPage()
        self.run_open(page, expect_session=True)
        self.assertEqual(sum(page.waits), 6000)

    def test_logout_visible_waits_for_popup_then_stops(self):
        page = _PortalPage(form=False, logout=True)
        self.run_open(page, expect_session=True)
        self.assertEqual(page.waits, [1500])

    def test_late_logout_retries_instead_of_pressing_login(self):
        import evpn
        page = _PortalPage(logout_after_typing=True)
        with mock.patch.object(evpn, "evpn_origins", return_value=["https://evpn.sen.go.kr"]), \
                mock.patch("secure_store.check_origin"), \
                mock.patch("secure_store.reveal_for", return_value="가짜-비밀번호"), \
                redirect_stdout(io.StringIO()) as out:
            ok = asyncio.run(evpn._login_once(page, "fake-evpn-id", object()))
        self.assertFalse(ok)                             # connect_async 가 «한 번 더» 로 로그아웃부터 다시 한다
        self.assertNotIn("#loginButton", page.clicked)
        self.assertIn("남은 세션이 늦게 보였습니다", out.getvalue())
        self.assertNotIn("가짜-비밀번호", out.getvalue())

    def test_connect_uses_adapter_state_for_first_wait(self):
        import evpn
        seen = []

        async def fake_open(page, expect_session=True):
            seen.append(expect_session)
            raise RuntimeError("여기까지만")

        class Ctx:
            async def close(self):
                return None

        launched, guards = [], []

        async def fake_launch(p, *a, **kw):
            launched.append(kw.get("offscreen", a[0] if a else False))
            return Ctx(), None

        class FakeGuard:
            def __init__(self, mark, keep_shown=False):
                self.mark, self.keep_shown, self.state = mark, keep_shown, "new"
                guards.append(self)

            def start(self):
                self.state = "on"
                return self

            def stop(self):
                self.state = "off"
        for tap, expected in (("Down", False), ("Up", True)):
            seen.clear()
            with mock.patch.object(evpn, "client_installed", return_value=True), \
                    mock.patch.object(evpn, "helper_installed", return_value=True), \
                    mock.patch.object(evpn, "load_credentials", return_value=("fake-evpn-id", object())), \
                    mock.patch.object(evpn, "start_helper", return_value=True), \
                    mock.patch.object(evpn, "stop_helper"), \
                    mock.patch.object(evpn, "agent_listening", return_value=False), \
                    mock.patch.object(evpn, "wait_agent", side_effect=AssertionError("연결 전에 에이전트를 기다리지 않는다")), \
                    mock.patch.object(evpn.focus_guard, "FocusGuard", FakeGuard), \
                    mock.patch.object(evpn, "tap_status", return_value=tap), \
                    mock.patch.object(evpn, "_launch", new=fake_launch), \
                    mock.patch.object(evpn, "_open_portal", new=fake_open), \
                    redirect_stdout(io.StringIO()):
                with self.assertRaises(RuntimeError):
                    asyncio.run(evpn.connect_async())
            self.assertEqual(seen, [expected], tap)
        self.assertEqual(launched, [True, True])                     # 포털 창은 화면 밖에
        self.assertEqual([(g.keep_shown, g.state) for g in guards], [(True, "off"), (True, "off")])
        self.assertTrue(all(g.mark == evpn.PROFILE_DIR.name for g in guards))   # 포털 전용 프로필 크롬만


class SharedFocusGuard(unittest.TestCase):
    """focus_guard — 우리 크롬(프로필 이름)이 맨 앞이면 내리고, 포털용은 곧바로 «포커스 없이» 되살린다 (가짜 윈도우)."""

    def run_guard(self, keep_shown, ours):
        import focus_guard as FG
        calls = []

        class User32:
            def __init__(self):
                self.GetForegroundWindow = lambda: 111
                self.GetAncestor = lambda h, flag: 222
                self.ShowWindow = lambda h, cmd: calls.append((h, cmd))
        fake = mock.Mock(user32=User32())
        g = FG.FocusGuard("chrome_profile_evpn", keep_shown=keep_shown, poll=0.01)
        with mock.patch.object(FG.ctypes, "windll", fake), mock.patch.object(g, "ours", return_value=ours):
            g.start()
            time.sleep(0.1)
            g.stop()
        return calls, g

    def test_portal_guard_minimizes_then_restores_without_focus(self):
        calls, g = self.run_guard(keep_shown=True, ours=True)
        self.assertGreater(g.returned, 0)
        self.assertEqual(calls[:2], [(222, 6), (222, 4)])          # SW_MINIMIZE → SW_SHOWNOACTIVATE

    def test_widget_guard_only_minimizes(self):
        calls, g = self.run_guard(keep_shown=False, ours=True)
        self.assertTrue(calls and all(c == (222, 6) for c in calls))

    def test_guard_leaves_other_windows_alone(self):
        calls, g = self.run_guard(keep_shown=True, ours=False)
        self.assertEqual(calls, [])
        self.assertEqual(g.returned, 0)

    def test_ours_matches_profile_name_in_command_line(self):
        import focus_guard as FG
        g = FG.FocusGuard("chrome_profile_evpn")

        class Proc:
            def __init__(self, pid):
                self.pid = pid

            def cmdline(self):
                return ["chrome.exe", r"--user-data-dir=C:\x\Chrome_Profile_EVPN"] if self.pid == 7 else ["chrome.exe"]
        pids = iter([7, 8])

        def fake_pid(hwnd, ref):
            ref._obj.value = next(pids)
        fake = mock.Mock()
        fake.user32.GetWindowThreadProcessId.side_effect = fake_pid
        with mock.patch.object(FG.ctypes, "windll", fake), mock.patch("psutil.Process", Proc):
            self.assertTrue(g.ours(1))                                # 대소문자 상관없이 프로필 이름으로
            self.assertFalse(g.ours(2))                               # 선생님이 평소 쓰는 크롬은 아니다


class WidgetFocusGuard(unittest.TestCase):
    """위젯 글자로 여는 동안 크롬이 선생님 포커스를 뺏지 않게 (2026-11-17)."""

    def test_manual_login_stops_guard_and_shows_window_first(self):
        import edufine_common as EC
        import neis_session as NS
        saved = (NS.wait_manual_login, EC.manual_login)
        order = []

        async def neis_wait(page, blocked):
            order.append("neis_wait")

        def edufine_wait(page):
            order.append("edufine_wait")

        async def front_async(page):
            order.append("front")

        def front_sync(page):
            raise RuntimeError("창 꺼내기 실패")              # 실패해도 기다리기는 해야 한다
        try:
            NS.wait_manual_login, EC.manual_login = neis_wait, edufine_wait
            guard = E.FocusGuard("neis")
            with mock.patch.object(E, "to_front_async", side_effect=front_async), \
                    mock.patch.object(E, "to_front_sync", side_effect=front_sync), \
                    mock.patch.object(E, "GUARD", guard):
                E.manual_hooks()
                E.manual_hooks()                             # 두 번 불러도 한 겹만
                asyncio.run(NS.wait_manual_login("page", None))
                EC.manual_login("page")
            self.assertEqual(order, ["front", "neis_wait", "edufine_wait"])
            self.assertTrue(guard.stopped.is_set())
        finally:
            NS.wait_manual_login, EC.manual_login = saved

    def test_open_commands_launch_minimized(self):
        src = _path.read_text(encoding="utf-8")
        self.assertEqual(src.count("ensure_chrome(site, minimized=True)"), 2)
        self.assertNotIn("keybd_event", src)                 # 앞으로 꺼낼 때 Alt 키를 누르지 않는다

    def test_widget_chrome_has_no_detection_hiding(self):
        """11-26: 위젯 크롬에는 «자동화 탐지 피하기» 설정을 넣지 않는다 (없어도 navigator.webdriver=false)."""
        self.assertFalse(any("AutomationControlled" in a for a in E.COMMON_ARGS))
        self.assertNotIn("add_init_script", _path.read_text(encoding="utf-8"))

    def test_foreground_skips_non_widget_chrome_when_guard_set(self):
        # 평소 크롬(hwnd=10)과 위젯 크롬(hwnd=20)이 같은 제목이어도 위젯 크롬만 앞으로 꺼낸다
        focused = []

        class User32:
            def __init__(self):
                self.GetForegroundWindow = lambda: 999
                self.GetWindowTextLengthW = lambda h: 12
                self.IsWindowVisible = lambda h: True

                def get_text(h, buf, n):
                    buf.value = "나이스 - Chrome"
                    return len(buf.value)
                self.GetWindowTextW = get_text
                self.EnumWindows = lambda cb, lp: (cb(10, 0), cb(20, 0))
                self.mouse_event = lambda *a: None
                self.SetForegroundWindow = lambda h: focused.append(h.value)

        guard = E.FocusGuard("neis")
        guard.ours = lambda hwnd: hwnd == 20
        with mock.patch.object(E, "GUARD", guard), \
                mock.patch.object(E.ctypes, "windll", mock.Mock(user32=User32())):
            E._foreground("나이스")
        self.assertEqual(focused, [20])


if __name__ == "__main__":
    unittest.main()

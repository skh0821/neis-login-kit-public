# -*- coding: utf-8 -*-
"""자동화 크롬이 선생님 키보드 포커스를 가져가면 바로 돌려주는 «지킴이» (2026-11-17·18).

선생님 말씀: «아이콘을 누르고 키보드·마우스로 다른 일을 하면 나이스 로그인이 완성되지 않는다».
임시 프로필 실측(11-17): 크롬은 화면 밖에 띄워도 · 최소화로 띄워도 뜨는 순간 키보드 포커스를 가져간다
→ 선생님이 치던 글자·클릭이 로그인 중인 창(EVPN 아이디 칸 · 인증서 암호 칸)으로 들어간다.
반대로 크롬이 맨 앞이 아니어도 페이지는 제 속도로 돈다 (그림 주기 2초에 121번 · 클릭 0.06초).

돌려주는 방법: 윈도우는 뒤에 있는 프로그램이 «앞으로 가져오기» 를 하지 못하게 막는다 (11-17 실측 — AttachThreadInput 도 실패).
대신 앞에 온 크롬 창을 최소화하면 윈도우가 스스로 그 전 창(선생님이 쓰던 창)에 포커스를 돌려준다.
  keep_shown=False  최소화한 채 둔다 — 위젯 크롬 (최소화여도 클릭 0.06초, 다 되면 꺼낸다)
  keep_shown=True   최소화 → 곧바로 «포커스 없이» 되살린다 — EVPN 포털 크롬 (최소화하면 클릭·치기가 1.1초 → 4.2초로 느려졌다, 11-18 실측)
누구 창인지는 크롬 명령줄의 프로필 폴더 이름(mark)으로만 가린다 — 선생님이 평소 쓰는 크롬은 건드리지 않는다.
"""
import ctypes
import threading

SW_MINIMIZE = 6           # 최소화하고 Z 순서의 다음 창을 활성으로
SW_SHOWNOACTIVATE = 4     # 마지막 크기·위치로 보이되 활성으로 만들지 않는다
GA_ROOTOWNER = 3


class FocusGuard:
    def __init__(self, mark, keep_shown=False, poll=0.1):
        self.mark = str(mark).lower()
        self.keep_shown = keep_shown
        self.poll = poll
        self.known = {}
        self.returned = 0                 # 돌려준 횟수 (기록용)
        self.stopped = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.stopped.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(1)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def ours(self, hwnd):
        u = ctypes.windll.user32
        pid = ctypes.c_ulong()
        u.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
        if pid.value not in self.known:
            try:
                import psutil
                self.known[pid.value] = self.mark in " ".join(psutil.Process(pid.value).cmdline()).lower()
            except Exception:
                self.known[pid.value] = False
        return self.known[pid.value]

    def reveal(self):
        """최소화된 채 남은 우리 크롬 창을 «포커스 없이» 보이게 한다 (실패 화면을 볼 수 있게)."""
        try:
            u = ctypes.windll.user32
            u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
            shown = []

            @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            def enum(hwnd, _):
                h = ctypes.c_void_p(hwnd)
                if hwnd and u.IsWindowVisible(h) and u.IsIconic(h) and u.GetWindowTextLengthW(h) and self.ours(hwnd):
                    shown.append(hwnd)
                return True
            u.EnumWindows(enum, 0)
            for hwnd in shown:
                u.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        except Exception:
            pass

    def _run(self):
        u = ctypes.windll.user32
        u.GetForegroundWindow.restype = ctypes.c_void_p
        u.GetAncestor.restype = ctypes.c_void_p
        u.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        while not self.stopped.wait(self.poll):
            try:
                fg = u.GetForegroundWindow()
                if fg and self.ours(fg):
                    root = u.GetAncestor(fg, GA_ROOTOWNER)
                    u.ShowWindow(root, SW_MINIMIZE)
                    if self.keep_shown:
                        u.ShowWindow(root, SW_SHOWNOACTIVATE)
                    self.returned += 1
            except Exception:
                pass

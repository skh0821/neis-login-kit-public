# -*- coding: utf-8 -*-
"""리로스쿨 전용 크롬을 열어 왼쪽 메뉴를 눌러 둔다 — 바탕화면 «문자» 글자 (2026-11-10 선생님 요청).

  python -m riro_merit.riro_open sms        «알림문자» → 문자 화면(/sms.php) + 하위 메뉴(문자 발송·메일머지 문자·발송 내역·
                                             예약발송내역·출결 문자·상벌 문자)가 펼쳐진다 — 그다음은 선생님이 고르신다
  python -m riro_merit.riro_open merit      로그인 → «상벌점 점수 입력» 화면까지만 (상벌점 창 [상벌점 화면 열기 (로그인만)],
                                             2026-11-11 선생님 요청). 학번·항목·점수는 하나도 넣지 않는다

리로스쿨은 인터넷으로 열리므로 VPN 은 쓰지 않는다. 크롬은 상벌점 채우기와 같은 «평범하게 따로 띄운» 리로스쿨 전용 크롬
(fill_unsaved.attach · 조작 포트 9326)이고, 메뉴를 누른 뒤 «연결만 끊는다» — 창은 남고 문자 발송의 확인 창이 선생님께 그대로 뜬다.
이미 리로스쿨 창이 떠 있으면(상벌점 채우기 등) 그 창에 «새 탭»으로 연다.

화면 구조 (2026-11-10 page_shape riro-menu, 메뉴 글자만): 로그인 뒤 첫 화면 https://<학교>.riroschool.kr/ ·
왼쪽 메뉴 «알림문자» = <li><span onclick="menuAction('/sms.php','')"> 하나 · 누르면 /sms.php 로 가며 하위 메뉴 6개가 펼쳐진다.
이 프로그램은 화면 글자·학생 정보를 읽지 않는다 (메뉴를 찾고 누르는 것뿐).

출력 (바탕화면 글자 위젯이 읽는다): STEP <하는 일> / READY / FAIL <이유>
"""
from __future__ import annotations

import re
import sys

from . import config as C
from .fill_unsaved import RIRO_PORT, attach, close_browser, out

# 바탕화면 글자 → 리로스쿨 왼쪽 메뉴 이름 · 누른 뒤 와야 할 주소
MENUS = {
    "sms": {"label": "알림문자", "path": "/sms.php"},
}


def open_menu(page, label: str, path: str) -> bool:
    """왼쪽 메뉴 label 을 누른다 (보이는 것 하나). 눌러서 path 로 갔으면 True."""
    hit = page.get_by_text(label, exact=True)
    visible = [i for i in range(hit.count()) if hit.nth(i).is_visible()]
    if not visible:
        return False
    hit.nth(visible[0]).click(timeout=10_000)
    try:
        page.wait_for_url(re.compile(re.escape(path)), timeout=20_000)
    except Exception:
        pass
    return path in page.url


def run(which: str) -> int:
    from playwright.sync_api import sync_playwright
    from . import riro_site as R
    menu = MENUS[which]
    C.require_school()
    with sync_playwright() as p:
        browser, page, fresh = attach(p, C.browser_dir("riro"), RIRO_PORT, headless=False, name="리로스쿨")

        def give_up(msg):
            try:
                close_browser(browser) if fresh else page.close()
            except Exception:
                pass
            raise RuntimeError(msg)

        res = R.login(page)
        if not res.get("ok"):
            give_up("리로스쿨 로그인 안 됨 (" + str(res.get("code")) + ")")
        home = f"https://{C.SCHOOL_HOST}/"
        if not page.url.startswith(home) or "action=signin" in page.url:
            page.goto(home, wait_until="domcontentloaded", timeout=60_000)
        if not page.url.startswith(home):                  # 다른 곳으로 넘어갔으면 아무것도 누르지 않는다
            give_up("리로스쿨 주소가 아닌 곳으로 넘어가서 멈췄습니다")
        out("STEP", f"리로스쿨 «{menu['label']}» 여는 중")
        if not open_menu(page, menu["label"], menu["path"]):
            give_up(f"리로스쿨 왼쪽 메뉴에서 «{menu['label']}»을 찾지 못했습니다 — 메뉴 이름이 바뀌었는지 확인해 주세요")
        try:
            page.bring_to_front()
        except Exception:
            pass
        out("READY")
        # 여기서 «연결만» 끊는다 — 창은 남고, 문자 발송의 확인 창이 선생님께 그대로 뜬다
    return 0


def run_merit() -> int:
    """로그인 → «상벌점 점수 입력» 화면까지만 연다 (상벌점 창 [상벌점 화면 열기 (로그인만)], 2026-11-11).
    학번 채우기와 같은 길(fill_unsaved.riro_page — 로그인 · 점수 입력 주소 · 학교 주소 확인)을 쓰고 빈칸에는 아무것도 넣지 않는다.
    화면 글자·학생 정보는 읽지 않는다. 다 열면 «연결만» 끊는다 — 창은 남고 [저장]의 확인 창이 선생님께 그대로 뜬다."""
    from playwright.sync_api import sync_playwright
    from . import riro_site as R
    from .fill_unsaved import riro_page
    C.require_school()
    with sync_playwright() as p:
        _browser, page = riro_page(p, R)
        try:
            page.bring_to_front()
        except Exception:
            pass
        out("READY")
    return 0


TARGETS = [*MENUS, "merit"]


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    which = args[0] if args else "sms"
    if which not in TARGETS:
        out("FAIL", "사용법: python -m riro_merit.riro_open " + "|".join(TARGETS))
        return 2
    try:
        return run_merit() if which == "merit" else run(which)
    except Exception as e:
        out("FAIL", (str(e).splitlines() or [type(e).__name__])[0][:160])
        return 1


if __name__ == "__main__":
    sys.exit(main())

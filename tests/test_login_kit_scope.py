# -*- coding: utf-8 -*-
"""이 키트의 원칙 — 로그인만 · 자동화임을 숨기지 않는다 (2026-11-26).
누가 «자동화 탐지 피하기» 코드나 입력 자동화 파일을 다시 넣으면 여기서 실패한다."""
import _guard  # noqa: F401  ← 맨 먼저: 진짜 설정 폴더·금고 항목을 못 건드리게
import re
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
CODE = [p for p in KIT.rglob("*") if p.suffix in (".py", ".pyw", ".cs", ".ps1", ".js")
        and ".venv" not in p.parts and "tests" not in p.parts and "build" not in p.parts]


def code_lines(p):
    """주석(# · //)을 뺀 줄 — «이 설정은 뺐다» 같은 설명은 세지 않는다."""
    for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        s = line.strip()
        if s.startswith("#") or s.startswith("//"):
            continue
        yield n, line.split(" #")[0] if p.suffix in (".py", ".pyw") else line


class LoginKitScope(unittest.TestCase):
    def test_no_detection_hiding(self):
        bad = []
        for p in CODE:
            for n, line in code_lines(p):
                if re.search(r"AutomationControlled|navigator\s*,\s*['\"]webdriver|navigator\.webdriver\s*=|"
                             r"defineProperty\(\s*navigator", line):
                    bad.append(f"{p.relative_to(KIT)}:{n}")
        self.assertEqual(bad, [], "자동화임을 숨기는 코드가 있다")

    def test_no_input_automation_programs(self):
        gone = ["main.py", "main_bokmu.py", "month_close.py", "geuntae.py", "print_month.py", "verify_attendance.py",
                "neis_cdp.py", "mcp_server.py", "edufine_punui.py", "edufine_gian.py", "edufine_quiet.py",
                "edufine_inspect.py", "edufine_punui_link.py", "setup_wizard.py", ".mcp.json"]
        self.assertEqual([g for g in gone if (KIT / g).exists()], [])
        skills = KIT / ".claude" / "skills"
        self.assertFalse(skills.exists() and any(skills.iterdir()), "출결·복무 스킬이 들어 있다")

    def test_settings_need_only_certificate_name(self):
        import teacher_config as T
        self.assertEqual(tuple(T.REQUIRED_KEYS), ("cert_name",))

    def test_requirements_have_no_input_automation_packages(self):
        req = (KIT / "requirements.txt").read_text(encoding="utf-8").lower()
        for pkg in ("pywinauto", "pyhwpx", "openpyxl", "pymupdf", "mcp=="):
            self.assertNotIn(pkg, req)
        self.assertIn("pywin32", req)                      # 금고 파일 권한 — 예전엔 pywinauto 에 딸려 왔다


if __name__ == "__main__":
    unittest.main()

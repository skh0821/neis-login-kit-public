# -*- coding: utf-8 -*-
"""자동화 크롬이 «샌드박스(보호벽)»를 끄지 않는지 — Playwright 기본값 --no-sandbox 를 빼고 띄워야 한다 (2026-11-10 선생님 제보:
크롬에 «지원되지 않는 명령줄 플래그(--no-sandbox)를 사용 중…» 경고). 진짜 프로필은 쓰지 않는다 (빈 임시 프로필)."""
import _guard  # noqa: F401  ← 맨 먼저
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))
from riro_merit import divot as D  # noqa: E402
from riro_merit import riro_site as R  # noqa: E402

# 키트에서 크롬을 띄우는 파일 — 강사님 원래 판(e알리미, 안 씀)은 손대지 않아서 뺀다
SKIP = {"widget/상벌점_엔진.py"}


class Flags(unittest.TestCase):
    def test_every_kit_launcher_drops_no_sandbox(self):
        bad = []
        for f in KIT.rglob("*.py"):
            rel = f.relative_to(KIT).as_posix()
            if rel.startswith((".venv/", "tests/")) or rel in SKIP:
                continue
            src = f.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r"ignore_default_args\s*=\s*(\[[^\]]*\]|[A-Za-z_][\w.]*)", src):
                arg = m.group(1)
                if "--no-sandbox" not in arg and "IGNORE_DEFAULT_ARGS" not in arg:
                    bad.append(f"{rel}: {arg.strip()[:60]}")
        self.assertEqual(bad, [])
        self.assertIn("--no-sandbox", R.IGNORE_DEFAULT_ARGS)

    def test_riro_and_divot_launchers_pass_it(self):
        p = mock.MagicMock()
        with tempfile.TemporaryDirectory() as d:
            R._launch(p, Path(d), headless=True)
            self.assertIn("--no-sandbox", p.chromium.launch_persistent_context.call_args.kwargs["ignore_default_args"])
            p.reset_mock()
            with mock.patch.object(D.C, "browser_dir", return_value=Path(d)):
                D._launch(p, headless=True)
            self.assertIn("--no-sandbox", p.chromium.launch_persistent_context.call_args.kwargs["ignore_default_args"])


class RealChrome(unittest.TestCase):
    def test_launched_chrome_command_line_has_no_no_sandbox(self):
        import psutil
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory() as d, sync_playwright() as p:
            ctx = R._launch(p, Path(d), headless=True)
            try:
                lines = []
                for proc in psutil.process_iter(["name", "cmdline"]):
                    cl = " ".join(proc.info.get("cmdline") or [])
                    if (proc.info.get("name") or "").lower() == "chrome.exe" and d in cl and "--type=" not in cl:
                        lines.append(cl)
                self.assertTrue(lines, "띄운 크롬을 찾지 못함")
                for cl in lines:
                    self.assertNotIn("--no-sandbox", cl)
            finally:
                ctx.close()


if __name__ == "__main__":
    unittest.main()

using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace TeacherDeskText
{
    internal static class Program
    {
        [STAThread]
        private static void Main(string[] args)
        {
            // --open <글자키> : 뜨자마자 그 글자를 누른 것처럼 연다 (예: --open neis). 바로가기·시험용
            int at = Array.IndexOf(args, "--open");
            string openKey = at >= 0 && at + 1 < args.Length ? args[at + 1] : null;
            bool created;
            using (var instance = new Mutex(true, @"Local\TeacherDesk.DesktopText", out created))
            {
                if (!created) return;
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                try
                {
                    using (var desktop = new DesktopText(openKey)) Application.Run(desktop);
                }
                catch (Win32Exception)
                {
                    MessageBox.Show("바탕화면 글자를 표시하지 못했습니다.", "교무 도구");
                }
            }
        }
    }

    internal sealed class DesktopText : ApplicationContext
    {
        private const int VerifiedExitCode = 42;   // windows_hello.py returns this only after verification.
        private readonly string kit = Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", "..", ".."));
        private readonly string positions = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "TeacherDeskDemo", "desktop-text.txt");
        private readonly List<TextLink> links = new List<TextLink>();
        private readonly ContextMenuStrip menu = new ContextMenuStrip();
        private Process verification;
        // 나이스·에듀파인을 여는 중인 일 (READY 전까지 하나만) · READY 뒤에도 VPN 을 붙잡고 있는 프로세스들
        private Job busy;
        private readonly List<Process> holders = new List<Process>();
        // 창이 다 닫혀 VPN 을 끊고 있는 일 — 그동안 누른 글자 옆에 «VPN 끊는 중 12초» 를 띄운다 (2026-11-11)
        private readonly List<Job> closing = new List<Job>();
        private readonly System.Windows.Forms.Timer ticker = new System.Windows.Forms.Timer { Interval = 1000 };
        private readonly string log = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "desktop-text.log");

        private sealed class Job
        {
            public string Key, Name;
            public TextLink Link;
            public Process Process;
            public readonly DateTime Started = DateTime.Now;
            public string Phase = "준비 중", LastError = "", LastStdErr = "";
            public bool Ready, Vpn, Closing, Disconnected;
            public DateTime ClosingStarted;
        }

        public DesktopText(string openKey)
        {
            menu.Items.Add("글자 위치 초기화", null, delegate { ResetPositions(); SavePositions(); });
            menu.Items.Add("인증서 설정", null, delegate { OpenCertificateSettings(); });
            menu.Items.Add("지금 잠그기", null, delegate { LockNow(); });
            menu.Items.Add("글자 위젯 종료", null, delegate { ExitThread(); });

            // 색 (2026-11-13): 위에서 아래로 색상이 차례로 돈다 — 벽돌·자두(리로스쿨) → 보라·파랑·청록·올리브(교육청).
            // 여섯 색 모두 미색 테두리와 대비 6.6 으로 진하기를 맞췄다 (예전엔 상벌점·나이스가 거의 같은 파랑이었다)
            Add("points", "상벌점", "#984335", OpenPoints);
            Add("sms", "문자", "#8D4277", delegate { OpenRiroMenu("sms", "sms"); });   // 리로스쿨 «알림문자» (11-10)
            Add("portal", "업무포털", "#5E4FA9", delegate { RunEngine("portal", "edufine", "portal"); });   // 문등대 자리 (11-13)
            Add("neis", "나이스", "#305E99", delegate { RunEngine("neis", "neis", "open"); });
            Add("edufine", "에듀파인", "#1E6855", delegate { RunEngine("edufine", "edufine", "main"); });
            Add("ledger", "문등대", "#42662A", delegate { RunEngine("ledger", "edufine", "open"); });   // 맨 아래로 (11-13)

            ResetPositions();
            RestorePositions();
            foreach (var link in links) link.Show();

            ticker.Tick += delegate { ShowProgress(); };
            var first = links.Find(item => item.Key == openKey);
            if (first != null)
            {
                var later = new System.Windows.Forms.Timer { Interval = 400 };
                later.Tick += delegate { later.Stop(); later.Dispose(); first.Launch(); };
                later.Start();
            }
        }

        private void Add(string key, string text, string color, Action action)
        {
            var link = new TextLink(key, text, ColorTranslator.FromHtml(color),
                delegate { VerifyLaunch(key, text, action); }, SavePositions);
            link.ContextMenuStrip = menu;
            links.Add(link);
        }

        private void VerifyLaunch(string key, string text, Action action)
        {
            if (verification != null || (busy != null && key != "points") || (closing.Count > 0 && UsesVpn(key))) return;
            string python = Path.Combine(kit, ".venv", "Scripts", "pythonw.exe");
            string script = Path.Combine(kit, "windows_hello.py");
            verification = Start(python, "-I \"" + script + "\" \"" + text + "\"", exited: delegate(object sender, EventArgs e)
            {
                var process = (Process)sender;
                Ui(delegate
                {
                    if (verification != process) return;
                    bool verified = process.ExitCode == VerifiedExitCode;
                    verification = null;
                    process.Dispose();
                    if (verified) action();
                });
            });
        }

        private void ResetPositions()
        {
            var area = Screen.PrimaryScreen.WorkingArea;
            float scale = TextLink.ScaleFor(Native.GetDpiForSystem());
            for (int i = 0; i < links.Count; i++)
            {
                var link = links[i];
                link.LinkPosition = new Point(area.Right - (int)((112 + 30) * scale), area.Top + (int)((96 + 39 * i) * scale));
                link.FitToScreen();
            }
        }

        private void RestorePositions()
        {
            try
            {
                if (!File.Exists(positions)) return;
                var lines = File.ReadAllLines(positions);
                bool oldOrder = Array.Exists(lines, line => line.StartsWith("circulation,", StringComparison.Ordinal));
                foreach (var line in lines)
                {
                    var parts = line.Split(',');
                    int x, y;
                    if (parts.Length != 3 || !Int32.TryParse(parts[1], out x) || !Int32.TryParse(parts[2], out y)) continue;
                    string key = parts[0];
                    if (oldOrder && key == "ledger") key = "sms";
                    else if (oldOrder && key == "circulation") key = "ledger";
                    var link = links.Find(item => item.Key == key);
                    if (link != null) { link.LinkPosition = new Point(x, y); link.FitToScreen(); }
                }
                // 11-13 전에 저장한 위치에는 «업무포털»이 없다 → 업무포털이 문등대 자리를 받고, 문등대는 에듀파인 바로 아래로
                bool hasPortal = Array.Exists(lines, line => line.StartsWith("portal,", StringComparison.Ordinal));
                bool hasLedger = Array.Exists(lines, line => line.StartsWith("ledger,", StringComparison.Ordinal)) || oldOrder;
                if (!hasPortal && hasLedger)
                {
                    var portal = links.Find(item => item.Key == "portal");
                    var ledger = links.Find(item => item.Key == "ledger");
                    var edufine = links.Find(item => item.Key == "edufine");
                    float scale = TextLink.ScaleFor(Native.GetDpiForSystem());
                    portal.LinkPosition = ledger.LinkPosition;
                    ledger.LinkPosition = new Point(edufine.LinkPosition.X, edufine.LinkPosition.Y + (int)(39 * scale));
                    portal.FitToScreen();
                    ledger.FitToScreen();
                    SavePositions();
                }
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }

        private void SavePositions()
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(positions));
                string temporary = positions + ".tmp";
                File.WriteAllLines(temporary, links.ConvertAll(link => link.Key + "," + link.LinkPosition.X + "," + link.LinkPosition.Y));
                if (File.Exists(positions)) File.Replace(temporary, positions, null);
                else File.Move(temporary, positions);
            }
            catch (IOException) { MessageBox.Show("글자 위치를 저장하지 못했습니다.", "교무 도구"); }
            catch (UnauthorizedAccessException) { MessageBox.Show("글자 위치를 저장하지 못했습니다.", "교무 도구"); }
        }

        private void OpenPoints()
        {
            Start(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "TeacherDeskDemo.exe"), "");
        }

        private void OpenCertificateSettings()
        {
            Start(Path.Combine(kit, ".venv", "Scripts", "pythonw.exe"),
                "\"" + Path.Combine(kit, "secure_settings.pyw") + "\" --tab neis");
        }

        // «지금 잠그기» — 본인 확인(지문 또는 잠금 비밀번호) 한 번이면 30분 동안 다시 묻지 않는다(windows_hello.py 의 hello_grace.json).
        // 자리를 비울 때 그 30분을 바로 끝낸다: 다음에 글자·인증서 설정을 누르면 본인 확인을 다시 받는다 (2026-11-13)
        private void LockNow()
        {
            string grace = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "neis-automation", "hello_grace.json");
            try
            {
                if (File.Exists(grace)) File.Delete(grace);
            }
            catch (Exception ex)
            {
                if (ex is IOException || ex is UnauthorizedAccessException)
                {
                    MessageBox.Show("잠그지 못했습니다. 잠시 뒤 다시 눌러 주세요.", "교무 도구");
                    return;
                }
                throw;
            }
            MessageBox.Show("잠갔습니다. 다음에 글자를 누르면 본인을 다시 확인합니다.", "교무 도구");
        }

        // ── 나이스·에듀파인 열기 ──
        // run_vpn.py --auto 가 접속 방식을 보고 정한다: EVPN 이면 연결 → 엔진이 연다 → 위젯 크롬(나이스·에듀파인)이
        // 다 닫힐 때까지 VPN 유지(--hold) → 끊기. 학교 업무망이면 VPN 없이 바로 연다. 4시간이 지나면 창이 열려 있어도 끊는다.
        // 출력 줄(STEP / READY / FAIL / ❌)을 읽어 누른 글자 오른쪽에 진행 상황을 보여 준다.
        private void RunEngine(string key, string site, string command)
        {
            // VPN 을 끊는 동안 새로 열면 run_vpn 이 «이미 연결됨»으로 보고 그 VPN 을 쓰는데, 곧 끊겨 화면이 깨진다
            if (closing.Count > 0) return;
            string runVpn = Path.Combine(kit, "run_vpn.py");
            string engine = Path.Combine(kit, "widget", "바로가기_엔진.py");
            RunJob(key, "-X utf8 \"" + runVpn + "\" --auto --minutes 240 -- python -X utf8 \"" + engine + "\" "
                + site + " " + command + " --hold");
        }

        // «문자» — 리로스쿨은 인터넷으로 열리니 VPN 없이: 리로스쿨 전용 크롬 → 로그인 → 왼쪽 «알림문자» 누르기 → 연결만 끊기 (11-10)
        private void OpenRiroMenu(string key, string which)
        {
            RunJob(key, "-X utf8 -m riro_merit.riro_open " + which);
        }

        // 파이썬 작업 하나를 창 없이 돌리고, 출력 줄(STEP / READY / FAIL / ❌)로 누른 글자 아래에 진행 상황을 보여 준다
        private void RunJob(string key, string arguments)
        {
            if (busy != null) return;
            var link = links.Find(item => item.Key == key);
            string python = Path.Combine(kit, ".venv", "Scripts", "python.exe");
            var job = new Job { Key = key, Name = link.Text, Link = link };
            job.Process = Start(python, arguments,
                delegate(object s, DataReceivedEventArgs e) { if (e.Data != null) Ui(delegate { OnLine(job, e.Data); }); },
                delegate(object s, DataReceivedEventArgs e)
                {
                    // 파이썬 오류의 «요약 줄»(…Error: …)만 잡는다. 그 뒤에 붙는 호출 기록·요청 머리글 줄
                    // (예: «- sec-ch-ua-platform: "Windows"»)은 이유가 아니다 — 11-10 알림 창에 그게 떴다
                    var m = e.Data == null ? null : ErrorLine.Match(e.Data.Trim());
                    if (m != null && m.Success)
                    {
                        string why = m.Groups[1].Value + (m.Groups[2].Value.Length > 0 ? ": " + m.Groups[2].Value : "");
                        job.LastStdErr = why.Length > 200 ? why.Substring(0, 200) : why;
                    }
                },
                delegate(object s, EventArgs e)
                {
                    try { ((Process)s).WaitForExit(); } catch (InvalidOperationException) { }   // 남은 출력 줄을 다 읽은 뒤에
                    Ui(delegate { OnExit(job); });
                });
            if (job.Process == null) return;
            busy = job;
            Log(job, "시작");
            RefreshCursors();
            ShowProgress();
            ticker.Start();
        }

        // «playwright._impl._errors.TimeoutError: Page.goto: …» → «TimeoutError» · «Page.goto: …»
        // 키트가 찍는 안내 줄의 머리 글자 — 이것으로 시작하는 줄만 기록에 남긴다 (값이 든 줄·호출 기록은 이걸로 시작하지 않는다)
        private static readonly string[] KitMarks = { "🔐", "🔁", "🌐", "ℹ", "⚠", "❌", "✅", "⏳", "⏸", "🔎", "🔌", "🚪", "↩", "⏱", "❗", "▶" };

        private static readonly System.Text.RegularExpressions.Regex ErrorLine = new System.Text.RegularExpressions.Regex(
            @"^(?:[A-Za-z_]\w*\.)*([A-Za-z_]\w*(?:Error|Exception))(?::\s*(.*))?$");

        private void OnLine(Job job, string line)
        {
            string text = line.Trim();
            if (text.Length == 0 || text.StartsWith("RESULT_JSON")) return;
            if (text.StartsWith("READY")) { Opened(job); return; }
            if (job.Ready) { WhileHeld(job, text); return; }
            if (text.StartsWith("FAIL ")) { job.LastError = text.Substring(5).Trim(); return; }
            // 연결 단계의 키트 안내 줄(🔐 🌐 ℹ ⚠ ❌ ⏳ …)은 시각과 함께 기록에 남긴다 — 어디서 시간이 걸렸는지 보려고 (2026-11-12
            // «준비 중 94초» 의 1분이 어디서 갔는지 못 밝혔다). 💬(포털 알림 원문)·들여쓴 줄(도우미 기록)은 남기지 않는다
            if (Array.Exists(KitMarks, mark => text.StartsWith(mark, StringComparison.Ordinal)))
                Log(job, "· " + (text.Length > 140 ? text.Substring(0, 140) : text));
            if (text.StartsWith("❌", StringComparison.Ordinal))
            {
                // «… 연결하지 못했습니다. 도우미 기록:» 처럼 뒤에 기록 줄이 따라오는 꼬리는 뗀다
                job.LastError = text.Substring(1).Trim().Replace(" 도우미 기록:", "").TrimEnd(':', ' ');
                // VPN 이 안 붙으면 run_vpn 이 정리(끊기 확인)를 마칠 때까지 «준비 중» 이 그대로 남았다 — 바뀐 걸 보여 준다
                if (text.Contains("VPN") && job.Phase != "VPN 연결 안 됨")
                {
                    job.Phase = "VPN 연결 안 됨";
                    if (busy == job) ShowProgress();
                }
                return;
            }
            string phase = null;
            if (text.Contains("VPN 상태 확인") || text.Contains("VPN 끊는 중")) phase = "VPN 끊는 중";   // 열다가 실패해 끊을 때
            // 자동 로그인이 막혀 «열린 크롬 창에서 직접 로그인» 을 기다리는 중 (에듀파인 edufine_common.manual_login ·
            // 나이스 neis_session.wait_manual_login). 예전엔 «로그인 중 112초» 만 떠서 무엇을 해야 하는지 몰랐다 (2026-11-11)
            else if (text.Contains("직접 로그인해 주세요") || text.Contains("직접 로그인하시거나")) phase = "직접 로그인해 주세요";
            else if (text.StartsWith("✅", StringComparison.Ordinal) && text.Contains("로그인 확인")) phase = "로그인 중";
            else if (text.StartsWith("⏳", StringComparison.Ordinal)) phase = "VPN 차례 기다림";    // 다른 곳에서 포털로 연결·끊기 중 (evpn._PortalTurn)
            else if (text.Contains("EVPN 포털 여는 중") || text.Contains("EVPN 포털 로그인") || text.Contains("한 번 더 합니다")) phase = "VPN 연결 중";
            else if (text.Contains("EVPN 연결됨") || text.Contains("EVPN 이미 연결") || text.Contains("VPN 이 이미 연결")) phase = "VPN 연결됨";
            else if (text.StartsWith("STEP ") && text.Contains("크롬 띄우는")) phase = "크롬 여는 중";
            else if (text.StartsWith("STEP ") && text.Contains("알림문자")) phase = "메뉴 여는 중";
            else if (text.StartsWith("STEP ") && (text.Contains("여는 중") || text.Contains("로그인"))) phase = "로그인 중";
            else if (text.StartsWith("⚠️") && text.Contains("포털 스크립트")) Log(job, "포털 스크립트 고치기 실패 — 그대로 엶");
            if (phase == null || phase == job.Phase) return;
            if (phase.StartsWith("VPN")) job.Vpn = true;
            job.Phase = phase;
            Log(job, phase);
            if (busy == job) ShowProgress();
        }

        private void Opened(Job job)
        {
            if (job.Ready) return;
            job.Ready = true;
            Log(job, "열림 (" + Seconds(job) + "초)" + (job.Vpn ? " · VPN 은 창을 모두 닫으면 끊김" : ""));
            if (!job.Process.HasExited) holders.Add(job.Process);
            Done(job);
        }

        // READY 뒤에 오는 줄 — 엔진 «창이 모두 닫혀 VPN 을 끊습니다» · evpn «🔎 VPN 상태 확인 중» «🔌 VPN 끊는 중» 이 오면
        // 누른 글자 옆에 «VPN 끊는 중 12초» 를 띄운다. 끊는 데 20~30초 걸리고, 예전엔 그동안 아무 표시가 없어
        // 포털 창만 갑자기 떴다 (2026-11-11 «요청도 안 했는데 나이스 로그인 화면이 다시 떴다»)
        private void WhileHeld(Job job, string text)
        {
            if (!job.Closing)
            {
                if (!(text.Contains("창이 모두 닫혀") || text.Contains("VPN 상태 확인") || text.Contains("VPN 끊는 중"))) return;
                job.Closing = true;
                job.ClosingStarted = DateTime.Now;
                closing.Add(job);
                Log(job, "창이 모두 닫힘 — VPN 끊는 중");
                RefreshCursors();
                ShowProgress();
                ticker.Start();
                return;
            }
            if (text.StartsWith("✅", StringComparison.Ordinal))
            {
                job.Disconnected = true;
                Log(job, "VPN 끊김 (" + ClosingSeconds(job) + "초" + (text.Contains("화면 밖") ? " · 포털 창 안 보이게" : text.Contains("보이는 창") ? " · 보이는 포털 창으로" : "") + ")");
            }
            else if (text.StartsWith("↩", StringComparison.Ordinal)) Log(job, "화면 밖 포털 창으로 안 끊겨 보이는 창으로 다시 (" + ClosingSeconds(job) + "초)");
            else if (text.StartsWith("❌", StringComparison.Ordinal)) { job.LastError = text.Substring(1).Trim(); Log(job, "VPN 안 끊김 (" + ClosingSeconds(job) + "초)"); }
        }

        private static int ClosingSeconds(Job job) { return (int)(DateTime.Now - job.ClosingStarted).TotalSeconds; }

        private static bool UsesVpn(string key) { return key == "neis" || key == "edufine" || key == "ledger" || key == "portal"; }

        // 여는 중이면 모든 글자(상벌점 빼고), VPN 을 끊는 중이면 VPN 을 쓰는 글자(업무포털·나이스·에듀파인·문등대)에 «기다림» 커서
        private void RefreshCursors()
        {
            foreach (var item in links) item.UpdateCursor(busy != null || (closing.Count > 0 && UsesVpn(item.Key)));
        }

        private void OnExit(Job job)
        {
            int code = -1;
            try { code = job.Process.ExitCode; } catch (InvalidOperationException) { }
            holders.Remove(job.Process);
            if (job.Ready)
            {
                if (closing.Remove(job))
                {
                    if (busy == null || busy.Link != job.Link) job.Link.SetStatus("");
                    if (busy == null && closing.Count == 0) ticker.Stop();
                    RefreshCursors();
                }
                if (job.Closing && !job.Disconnected)
                {
                    Log(job, "VPN 이 안 끊긴 채 끝남 (끝 번호 " + code + ")");
                    MessageBox.Show("잘못하신 게 아닙니다.\n\n«" + job.Name + "» 창을 닫아 VPN 을 끊으려 했는데 끊긴 것을 확인하지 못했습니다.\n"
                        + (job.LastError.Length > 0 ? "이유: " + job.LastError + "\n" : "")
                        + "\n시작 메뉴(또는 바탕화면)의 «VPN 끊기»를 눌러 주세요.\n"
                        + "이 창의 내용을 클로드에게 보여 주시면 이어서 봐 드립니다.", "교무 도구");
                }
                else Log(job, job.Closing ? "끝 (끝 번호 " + code + ")"
                    : job.Vpn ? "끝 — VPN 은 처음 붙인 쪽이 끊음 (끝 번호 " + code + ")" : "끝 (끝 번호 " + code + ")");
            }
            else
            {
                Done(job);
                string why = job.LastError.Length > 0 ? job.LastError
                    : job.LastStdErr.Length > 0 ? job.LastStdErr : "이유를 받지 못했습니다 (끝 번호 " + code + ")";
                Log(job, "실패: " + why);
                MessageBox.Show("잘못하신 게 아닙니다.\n\n«" + job.Name + "» 화면을 열지 못했습니다.\n이유: " + why + "\n\n"
                    + (job.Vpn ? "VPN 이 붙어 있으면 시작 메뉴(또는 바탕화면)의 «VPN 끊기»를 눌러 주세요.\n" : "")
                    + "이 창의 내용을 클로드에게 보여 주시면 이어서 봐 드립니다.", "교무 도구");
            }
            job.Process.Dispose();
        }

        private void Done(Job job)
        {
            if (busy != job) return;
            busy = null;
            if (closing.Count == 0) ticker.Stop();
            job.Link.SetStatus("");
            RefreshCursors();
            ShowProgress();
        }

        private void ShowProgress()
        {
            if (busy != null) busy.Link.SetStatus(busy.Phase + " " + Seconds(busy) + "초");
            foreach (var job in closing)
                if (busy == null || busy.Link != job.Link) job.Link.SetStatus("VPN 끊는 중 " + ClosingSeconds(job) + "초");
        }

        private static int Seconds(Job job) { return (int)(DateTime.Now - job.Started).TotalSeconds; }

        private void Ui(Action action)
        {
            var anchor = links.Count > 0 ? links[0] : null;
            if (anchor == null || anchor.IsDisposed || !anchor.IsHandleCreated) return;
            try { anchor.BeginInvoke(action); }
            catch (InvalidOperationException) { }
        }

        // 무엇을 했는지만 남긴다 (값 없음) — 안 될 때 클로드가 본다. build\desktop-text.log
        private void Log(Job job, string text)
        {
            try
            {
                if (File.Exists(log) && new FileInfo(log).Length > 200000) File.Delete(log);
                File.AppendAllText(log, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  " + job.Key + "  " + text + "\r\n", Encoding.UTF8);
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }

        private Process Start(string path, string arguments, DataReceivedEventHandler output = null,
            DataReceivedEventHandler error = null, EventHandler exited = null)
        {
            bool worker = output != null;
            try
            {
                var info = new ProcessStartInfo(path, arguments)
                {
                    WorkingDirectory = kit,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = worker,
                    RedirectStandardError = worker,
                };
                if (worker)
                {
                    info.StandardOutputEncoding = Encoding.UTF8;
                    info.StandardErrorEncoding = Encoding.UTF8;
                    info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
                    info.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";   // 줄이 늦게 오면 진행 표시가 멈춘 것처럼 보인다
                }
                var process = new Process { StartInfo = info, EnableRaisingEvents = exited != null };
                if (worker)
                {
                    process.OutputDataReceived += output;
                    if (error != null) process.ErrorDataReceived += error;
                }
                if (exited != null) process.Exited += exited;
                process.Start();
                Native.AllowSetForegroundWindow(process.Id);
                if (worker)
                {
                    process.BeginOutputReadLine();
                    process.BeginErrorReadLine();
                }
                if (worker || exited != null) return process;
                process.Dispose();
            }
            catch (Win32Exception)
            {
                MessageBox.Show("실행 파일을 열지 못했습니다. 교무 도구 설치와 프로젝트 경로를 확인해 주세요.", "교무 도구");
            }
            return null;
        }

        protected override void ExitThreadCore()
        {
            // VPN 을 붙잡고 있는 프로세스는 죽이지 않는다 — 나이스·에듀파인 창을 닫으면 스스로 끊는다
            // (이 위젯이 먼저 꺼져 출력이 끊겨도 run_vpn 의 감시자가 끊는다)
            ticker.Dispose();
            if (verification != null) verification.Dispose();
            foreach (var link in links) link.Dispose();
            menu.Dispose();
            base.ExitThreadCore();
        }
    }

    internal sealed class TextLink : Form
    {
        private const int LinkWidth = 112, LinkHeight = 35;
        public readonly string Key;
        private readonly Action launch, moved;
        private readonly Color ink;
        private bool hover, dragging;
        private Point? press;
        private Point origin;
        private float scale;
        private string status = "";
        private int statusShift;

        private Size LinkSize { get { return new Size((int)(LinkWidth * scale), (int)(LinkHeight * scale)); } }

        public Point LinkPosition
        {
            get { return new Point(Left + statusShift, Top); }
            set
            {
                int left = value.X;
                if (Width > LinkSize.Width)
                {
                    var area = Screen.FromRectangle(new Rectangle(value, LinkSize)).WorkingArea;
                    left = Math.Max(area.Left, Math.Min(left, area.Right - Width));
                }
                statusShift = value.X - left;
                Location = new Point(left, value.Y);
            }
        }

        public TextLink(string key, string text, Color ink, Action launch, Action moved)
        {
            Key = key;
            this.ink = ink;
            this.launch = launch;
            this.moved = moved;
            Text = text;
            FormBorderStyle = FormBorderStyle.None;
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.Manual;
            Cursor = Cursors.Hand;
            AutoScaleMode = AutoScaleMode.None;
            SetScale(Native.GetDpiForSystem());
        }

        protected override bool ShowWithoutActivation { get { return true; } }

        protected override CreateParams CreateParams
        {
            get
            {
                var style = base.CreateParams;
                const int layered = 0x00080000, toolWindow = 0x00000080, noActivate = 0x08000000;
                style.ExStyle |= layered | toolWindow | noActivate;
                return style;
            }
        }

        protected override void OnShown(EventArgs e)
        {
            base.OnShown(e);
            SetScale(Native.GetDpiForWindow(Handle));
            FitToScreen();
            DrawLink();
            Native.SetWindowPos(Handle, new IntPtr(1), 0, 0, 0, 0, 0x0013);  // HWND_BOTTOM, keep size/position, no activation.
        }

        protected override void OnDpiChanged(DpiChangedEventArgs e)
        {
            base.OnDpiChanged(e);
            SetScale((uint)e.DeviceDpiNew);
            DrawLink();
        }

        internal static float ScaleFor(uint dpi)
        {
            return Math.Min((dpi == 0 ? 96 : dpi) / 96f, 1.18f);
        }

        private void SetScale(uint dpi)
        {
            scale = ScaleFor(dpi);
            ClientSize = LinkSize;
        }

        public void Launch() { launch(); }

        public void SetStatus(string text)
        {
            text = text ?? "";
            if (text == status) return;
            status = text;
            if (IsHandleCreated) DrawLink();
        }

        protected override void OnMouseEnter(EventArgs e) { base.OnMouseEnter(e); hover = true; DrawLink(); }
        protected override void OnMouseLeave(EventArgs e) { base.OnMouseLeave(e); hover = false; DrawLink(); }

        protected override void OnMouseDown(MouseEventArgs e)
        {
            base.OnMouseDown(e);
            if (e.Button != MouseButtons.Left || e.X >= LinkSize.Width) return;
            press = Cursor.Position;
            dragging = false;
            origin = LinkPosition;
            Capture = true;
        }

        protected override void OnMouseMove(MouseEventArgs e)
        {
            base.OnMouseMove(e);
            if (!press.HasValue || e.Button != MouseButtons.Left) return;
            var delta = (Size)Cursor.Position - (Size)press.Value;
            var threshold = SystemInformation.DragSize;
            if (!dragging && Math.Abs(delta.Width) < threshold.Width && Math.Abs(delta.Height) < threshold.Height) return;
            dragging = true;
            LinkPosition = origin + delta;
        }

        protected override void OnMouseUp(MouseEventArgs e)
        {
            base.OnMouseUp(e);
            if (e.Button != MouseButtons.Left || !press.HasValue) return;
            press = null;
            Capture = false;
            if (dragging)
            {
                dragging = false;
                FitToScreen();
                moved();
            }
            else if (ClientRectangle.Contains(e.Location)) launch();
        }

        public void FitToScreen()
        {
            var position = LinkPosition;
            var size = LinkSize;
            var area = Screen.FromRectangle(new Rectangle(position, size)).WorkingArea;
            LinkPosition = new Point(Math.Max(area.Left, Math.Min(position.X, area.Right - size.Width)),
                                     Math.Max(area.Top, Math.Min(position.Y, area.Bottom - size.Height)));
        }

        public void UpdateCursor(bool busy) { if (Key != "points") Cursor = busy ? Cursors.AppStarting : Cursors.Hand; }

        private void DrawLink()
        {
            var size = LinkSize;
            float wordHeight = LinkHeight * scale;
            using (var font = new FontFamily("Malgun Gothic"))
            using (var glyph = new GraphicsPath())
            using (var line = new GraphicsPath())
            using (var centered = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center })
            {
                glyph.AddString(Text, font, (int)FontStyle.Bold, 17 * scale,
                    new RectangleF(0, 0, size.Width, wordHeight - 2 * scale), centered);
                var word = glyph.GetBounds();
                int width = size.Width;
                if (status.Length > 0)
                {
                    using (var format = StringFormat.GenericTypographic)
                        line.AddString(status, font, (int)FontStyle.Regular, 11.5f * scale, PointF.Empty, format);
                    var progress = line.GetBounds();
                    float left = word.Right + 12 * scale;
                    using (var offset = new Matrix())
                    {
                        offset.Translate(left - progress.Left, word.Top + (word.Height - progress.Height) / 2 - progress.Top);
                        line.Transform(offset);
                    }
                    width = Math.Max(width, (int)Math.Ceiling(left + progress.Width + 3 * scale));
                }

                // 상태가 화면 끝에 닿으면 잠깐 당겨 놓고, 저장된 글자 위치는 유지한다.
                var position = LinkPosition;
                if (ClientSize.Width != width || ClientSize.Height != size.Height)
                {
                    ClientSize = new Size(width, size.Height);
                    LinkPosition = position;
                }

                using (var image = new Bitmap(width, size.Height, PixelFormat.Format32bppPArgb))
                {
                    using (var graphics = Graphics.FromImage(image))
                    using (var hitArea = new SolidBrush(Color.FromArgb(1, 0, 0, 0)))
                    using (var outline = new Pen(Color.FromArgb(240, 255, 253, 247), 2.4f * scale) { LineJoin = LineJoin.Round })
                    using (var fill = new SolidBrush(ink))
                    {
                        graphics.FillRectangle(hitArea, new Rectangle(Point.Empty, size));
                        graphics.SmoothingMode = SmoothingMode.AntiAlias;
                        graphics.DrawPath(outline, glyph);
                        graphics.FillPath(fill, glyph);
                        if (hover)
                        {
                            using (var underline = new Pen(ink, scale))
                                graphics.DrawLine(underline, word.Left, wordHeight - 4 * scale, word.Right, wordHeight - 4 * scale);
                        }
                        if (status.Length > 0)
                        {
                            using (var thin = new Pen(Color.FromArgb(235, 255, 253, 247), 2f * scale) { LineJoin = LineJoin.Round })
                            using (var gray = new SolidBrush(Color.FromArgb(64, 66, 62)))
                            {
                                graphics.DrawPath(thin, line);
                                graphics.FillPath(gray, line);
                            }
                        }
                    }
                    Native.Draw(Handle, image);
                    // 시험용: TEACHERDESK_RENDER_DIR 이 있으면 그린 그림을 파일로도 남긴다 (화면을 찍지 않고 모양을 본다)
                    if (RenderDir != null)
                    {
                        try { image.Save(Path.Combine(RenderDir, Key + (status.Length > 0 ? "-status" : "") + ".png"), ImageFormat.Png); }
                        catch (Exception) { }
                    }
                }
            }
        }

        private static readonly string RenderDir = Environment.GetEnvironmentVariable("TEACHERDESK_RENDER_DIR");
    }

    internal static class Native
    {
        [StructLayout(LayoutKind.Sequential)]
        private struct Blend { public byte Operation, Flags, Opacity, Alpha; }

        [DllImport("user32.dll")] internal static extern bool AllowSetForegroundWindow(int processId);
        [DllImport("user32.dll")] internal static extern uint GetDpiForSystem();
        [DllImport("user32.dll")] internal static extern uint GetDpiForWindow(IntPtr window);
        [DllImport("user32.dll")] internal static extern bool SetWindowPos(IntPtr window, IntPtr after, int x, int y, int width, int height, uint flags);
        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool UpdateLayeredWindow(IntPtr window, IntPtr screen, IntPtr position, ref Size size,
            IntPtr source, ref Point origin, int key, ref Blend blend, int flags);
        [DllImport("gdi32.dll")] private static extern IntPtr CreateCompatibleDC(IntPtr device);
        [DllImport("gdi32.dll")] private static extern IntPtr SelectObject(IntPtr device, IntPtr bitmap);
        [DllImport("gdi32.dll")] private static extern bool DeleteObject(IntPtr bitmap);
        [DllImport("gdi32.dll")] private static extern bool DeleteDC(IntPtr device);

        internal static void Draw(IntPtr window, Bitmap image)
        {
            IntPtr device = CreateCompatibleDC(IntPtr.Zero);
            IntPtr bitmap = image.GetHbitmap(Color.FromArgb(0));
            IntPtr old = SelectObject(device, bitmap);
            try
            {
                var size = image.Size;
                var origin = Point.Empty;
                var blend = new Blend { Opacity = 255, Alpha = 1 };
                if (!UpdateLayeredWindow(window, IntPtr.Zero, IntPtr.Zero, ref size, device, ref origin, 0, ref blend, 2))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            finally
            {
                SelectObject(device, old);
                DeleteObject(bitmap);
                DeleteDC(device);
            }
        }
    }
}

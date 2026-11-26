// EVPN 도우미 — AXGATE VPN Client 의 네이티브 창을 선생님 대신 처리한다.
//
// 왜 따로 있나:
//   AXGATE 클라이언트는 관리자 권한(requireAdministrator)으로 돈다. 윈도우는 일반 권한 프로그램이
//   관리자 권한 창에 글자를 넣거나 버튼을 누르는 것을 조용히 막는다(UIPI). 그래서 2차 인증 창에
//   인증서 암호를 넣으려면 이 도우미도 관리자 권한이어야 한다.
//   연결할 때마다 UAC [예] 가 뜨지 않도록, 설치 때 한 번만 [예] 를 누르고
//   «가장 높은 권한으로 실행» 예약 작업으로 등록해 둔다 (install.ps1).
//
// 하는 일 (한 번 실행에 최대 300초):
//   1) AXGATE 클라이언트가 안 떠 있으면 띄운다 — 설치 때 기록한 파일 목록·해시와 같을 때만
//   2) «2차 인증» 창: 설정의 인증서 이름과 맞는 줄 하나를 골라 암호를 넣고 [로그인] (최대 2번)
//   3) «이미 접속되어 있는 ID 입니다» 창: [예] (최대 1번)
//   4) 2차 인증 뒤 «로그인 후 자동 실행» 창을 닫는다
//   5) 키트가 끝 신호 파일을 만들거나 300초가 지나면 끝낸다
//
// ⚠️ 이 파일은 설치 때 C:\Program Files\NeisAutomationEVPN 에서 컴파일된다. 고쳤으면
//    python evpn.py install 을 다시 해야 반영된다.
// ⚠️ 로그에 암호·인증서 이름을 절대 쓰지 않는다.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

static class Helper
{
    const string AXGATE_BIN = @"C:\ProgramData\AXGATE\AXGATE VPN Client\Bin";
    const string AXGATE_EXE = AXGATE_BIN + @"\AxgateVpnClient.exe";
    static string KEYRING_SERVICE = "neis-automation";   // --selftest 의 넷째 인자로만 바꿀 수 있다 (시험용 금고 이름)
    const int MAX_SECONDS = 300;
    const int MAX_2FA = 2;          // 포털 로그인 2번까지 (키트의 1회 재시도)
    const int MAX_DUP = 1;          // «이미 접속» 교체는 1번만
    // 2: 인증서 이름을 암호화 파일(secure\teacher_private.dpapi)에서도 읽는다 (2026-11-09).
    //    secure_migrate.py 는 설치된 helper.cs 에 이 표시가 있어야 평문 설정에서 인증서 이름을 뺀다.
    // 3: 암호를 2차 인증 창이 뜰 때만 꺼내고 char 배열을 0 으로 덮음 · keyring 과 같은 읽기 순서 (2026-11-09)
    // 4: AXGATE 창 확인을 «실행 파일 전체 경로 + 설치 때 해시» 로 · 암호 칸을 하나로 못 정하면 멈춤 · 넣기 직전 재확인 (2026-11-09)
    // 5: 2차 인증 창 포커스 돌려주기 · LVM_SETITEMSTATE 로 Ctrl/Shift 타자 중에도 인증서 선택 · 빠른 감시(100ms) (2026-11-26)
    // 6: 금고 이름을 사람마다 — 암호화 설정의 vault_service 를 읽는다 (없으면 예전 이름 "neis-automation") (2026-11-26)
    const string HELPER_VERSION = "6-vault-name";
    const string PRIVATE_PURPOSE = "neis-automation/teacher_private";
    static string ConfigDir = null;
    static bool ServiceFromArgs = false;   // 자가 점검에서 시험용 금고 이름을 받았으면 설정의 이름으로 바꾸지 않는다

    static string BaseDir = AppDomain.CurrentDomain.BaseDirectory;
    static string LogPath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
                                          "NeisAutomationEVPN", "helper.log");
    static string UserDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                                          "neis-automation");

    // ★ 새 클로드 데스크톱 앱(스토어형 MSIX 패키지)에서 돌린 파이썬은 %LOCALAPPDATA% 에 쓴 파일이
    //   Packages\<앱>\LocalCache\Local\ 아래로 «몰래» 옮겨진다. 도우미는 패키지 밖(예약 작업)이라
    //   원래 자리만 보면 설정이 «없다» 고 나온다 (2026-10-03 샌드박스 실측). 두 곳을 다 본다.
    static string OverrideDir = null;     // --selftest 의 시험 폴더 전용

    static List<string> UserDirs()
    {
        if (OverrideDir != null) return new List<string> { OverrideDir };
        var dirs = new List<string> { UserDir };
        try
        {
            string pk = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Packages");
            foreach (var d in Directory.GetDirectories(pk))
            {
                string c = Path.Combine(d, "LocalCache", "Local", "neis-automation");
                if (Directory.Exists(c)) dirs.Add(c);
            }
        }
        catch { }
        return dirs;
    }

    static bool DoneSignaled(DateTime started)
    {
        foreach (var d in UserDirs())
        {
            string f = Path.Combine(d, "evpn_helper_done.flag");
            if (File.Exists(f) && File.GetLastWriteTime(f) > started) return true;
        }
        return false;
    }

    static void Log(string m)
    {
        try { File.AppendAllText(LogPath, DateTime.Now.ToString("HH:mm:ss") + "  " + m + "\r\n", Encoding.UTF8); }
        catch { }
    }

    [STAThread]
    static int Main(string[] args)
    {
        // 자가 점검 — AXGATE·창은 건드리지 않고, 인증서 이름·암호를 «찾았는지»만 파일에 적고 끝낸다.
        // 값은 쓰지 않는다. (secure_migrate.py 가 재설치 전에 새 판을 임시로 컴파일해 이걸로 확인한다)
        if (args.Length >= 2 && args.Length <= 4 && args[0] == "--selftest")
        {
            // 셋째 인자 = 시험용 가짜 설정 폴더 · 넷째 인자 = 시험용 금고 이름 (자가 점검에서만 받는다 — 평소 실행은 안 바뀐다)
            if (args.Length >= 3) OverrideDir = args[2];
            if (args.Length == 4) { KEYRING_SERVICE = args[3]; ServiceFromArgs = true; }
            string r;
            try
            {
                var cfg = ReadConfig();
                string vault = UseVaultName(cfg);
                string src;
                string cn = ReadCertName(cfg, out src);
                bool pw = cn != null && PasswordExists(cn);         // 있는지만 (꺼내지 않는다)
                r = "config=" + (cfg != null ? "found" : "missing") + "\r\ncert_name=" + (cn != null ? "found:" + src : "missing")
                    + "\r\npassword=" + (pw ? "found" : "missing") + "\r\npassword_target=" + (cn != null ? (PasswordTarget(cn) ?? "none") : "none")
                    + "\r\nvault_name=" + vault + "\r\nversion=" + HELPER_VERSION + "\r\n";
            }
            catch (Exception e) { r = "error=" + e.GetType().Name + "\r\n"; }
            try { File.WriteAllText(args[1], r, Encoding.UTF8); } catch { return 2; }
            return 0;
        }
        bool created;
        using (var mtx = new Mutex(true, @"Global\NeisAutomationEvpnHelper", out created))
        {
            if (!created) return 0;     // 이미 도는 중
            try { File.WriteAllText(LogPath, "", Encoding.UTF8); } catch { }
            bool admin = new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator);
            Log("[시작] 관리자=" + admin + " 사용자=" + Environment.UserName);
            if (!admin)
                Log("[경고] 관리자 권한이 아닙니다 — AXGATE 창을 누를 수 없습니다 (표준 사용자 계정일 수 있음)");
            DateTime started = DateTime.Now;
            try { Run(started); }
            catch (Exception e) { Log("[오류] " + e.GetType().Name + ": " + e.Message); return 1; }
            Log("[끝]");
        }
        return 0;
    }

    static void Run(DateTime started)
    {
        EnsureAxgate();

        var cfg = ReadConfig();
        if (UseVaultName(cfg) == "dpapi") Log("[설정] 금고 이름을 암호화 설정에서 읽음");
        string certSrc;
        string certName = ReadCertName(cfg, out certSrc);
        if (certName != null && certSrc != "plain") Log("[설정] 인증서 이름을 암호화 파일에서 읽음");
        // ★ 암호는 여기서 꺼내 두지 않는다 — «있는지»만 보고, 2차 인증 창이 뜬 그때 꺼내 넣고 바로 0 으로 덮는다 (2026-11-09)
        bool havePw = certName != null && PasswordExists(certName);
        if (certName == null) Log("[설정] 인증서 이름이 설정에 없습니다 — 2차 인증을 못 넣습니다");
        else if (!havePw) Log("[설정] 저장된 인증서 암호가 없습니다 — 2차 인증을 못 넣습니다");

        var seen = new HashSet<IntPtr>();
        int done2fa = 0, dup = 0, wrong = 0;
        DateTime last2fa = DateTime.MinValue;
        while ((DateTime.Now - started).TotalSeconds < MAX_SECONDS)
        {
            if (DoneSignaled(started)) { Log("[신호] 키트가 끝 신호를 보냄"); break; }

            IntPtr dlg = FindDialogWithChild("2차 인증");
            if (dlg != IntPtr.Zero && !seen.Contains(dlg))
            {
                seen.Add(dlg);     // 처리 전에 먼저 표시 — 같은 창을 두 번 누르지 않는다
                if (done2fa >= MAX_2FA) Log("[2차인증] 횟수 초과 — 이번 창은 건드리지 않습니다");
                else if (!havePw) Log("[2차인증] 창이 떴지만 암호가 없어 못 넣습니다");
                else
                {
                    // 암호는 Handle2fa 안에서 «창·인증서 줄·암호 칸을 다 확인한 뒤» 꺼내고, 쓰자마자 0 으로 덮는다
                    int r = Handle2fa(dlg, certName);
                    if (r > 0) { done2fa++; last2fa = DateTime.Now; }
                    if (r == 2) wrong++;
                }
            }

            IntPtr d2 = FindDialogWithChild("이미 접속되어 있는 ID");
            if (d2 != IntPtr.Zero && !seen.Contains(d2))
            {
                seen.Add(d2);
                if (dup >= MAX_DUP) Log("[이미접속] 두 번째 창 — 누르지 않습니다");
                else if (ClickButton(d2, "예")) { dup++; Log("[이미접속] 기존 접속 끊기 [예]"); }
            }

            if (done2fa > 0)
            {
                IntPtr ar = FindTopByTitle("로그인 후 자동 실행");
                if (ar != IntPtr.Zero) { PostMessage(ar, WM_CLOSE, IntPtr.Zero, IntPtr.Zero); Log("[자동실행창] 닫음"); }
            }
            Thread.Sleep(100);
        }
        Log("[요약] 2차인증 " + done2fa + "번 · 이미접속 " + dup + "번 · 암호틀림 " + wrong + "번");
    }

    // ---------------------------------------------------------------- AXGATE 띄우기
    static void EnsureAxgate()
    {
        if (Process.GetProcessesByName("AxgateVpnClient").Length > 0) { Log("[AXGATE] 이미 실행 중"); return; }
        if (!File.Exists(AXGATE_EXE)) { Log("[AXGATE] 설치되어 있지 않습니다"); return; }
        string why = CheckBaseline();
        if (why != null)
        {
            // 관리자 권한으로 띄우는 것이라, 설치 뒤 바뀐 파일이 있으면 절대 띄우지 않는다.
            // (AXGATE 폴더는 일반 사용자도 새 파일을 넣을 수 있다 — 윈도우 ProgramData 기본 권한)
            Log("[AXGATE] 띄우지 않음 — " + why + " → python evpn.py install 을 다시 하면 기준이 갱신됩니다");
            return;
        }
        var psi = new ProcessStartInfo(AXGATE_EXE) { UseShellExecute = false, WorkingDirectory = AXGATE_BIN };
        Process.Start(psi);
        Log("[AXGATE] 실행함");
    }

    static string CheckBaseline()
    {
        string f = Path.Combine(BaseDir, "axgate_baseline.txt");
        if (!File.Exists(f)) return "기준 파일 없음";
        var want = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var line in File.ReadAllLines(f))
        {
            var p = line.Split('|');
            if (p.Length == 2) want[p[0]] = p[1];
        }
        var now = Directory.GetFiles(AXGATE_BIN, "*", SearchOption.AllDirectories)
            .Where(x => x.EndsWith(".exe", StringComparison.OrdinalIgnoreCase) || x.EndsWith(".dll", StringComparison.OrdinalIgnoreCase))
            .ToList();
        foreach (var path in now)
        {
            string rel = path.Substring(AXGATE_BIN.Length + 1);
            if (!want.ContainsKey(rel)) return "설치 때 없던 파일: " + rel;
            if (!string.Equals(Sha256(path), want[rel], StringComparison.OrdinalIgnoreCase)) return "바뀐 파일: " + rel;
        }
        if (now.Count != want.Count) return "파일 수가 다름 (기준 " + want.Count + " / 지금 " + now.Count + ")";
        return null;
    }

    static string Sha256(string path)
    {
        using (var s = File.OpenRead(path))
        using (var h = SHA256.Create())
            return BitConverter.ToString(h.ComputeHash(s)).Replace("-", "");
    }

    // ---------------------------------------------------------------- 설정·암호
    static Dictionary<string, object> ReadConfig()
    {
        string f = null;          // 여러 곳에 있으면 가장 최근에 저장된 것
        foreach (var ud in UserDirs())
        {
            string c = Path.Combine(ud, "teacher_config.json");
            if (File.Exists(c) && (f == null || File.GetLastWriteTime(c) > File.GetLastWriteTime(f))) f = c;
        }
        if (f == null) return null;
        if (f.IndexOf(@"\Packages\", StringComparison.OrdinalIgnoreCase) >= 0) Log("[설정] 앱 전용 폴더의 설정을 씁니다");
        ConfigDir = Path.GetDirectoryName(f);
        return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(f, Encoding.UTF8));
    }

    static string Str(Dictionary<string, object> d, string k)
    {
        object v;
        return d != null && d.TryGetValue(k, out v) && v is string && ((string)v).Trim().Length > 0 ? ((string)v).Trim() : null;
    }

    // 평문 설정에 있으면 그것(예전 방식·호환 모드), 없으면 secure\teacher_private.dpapi 를
    // 윈도우 DPAPI(이 사용자 범위)로 풀어 읽는다. 열쇠는 윈도우가 사용자 계정에 묶어 둔다 — 파일 옆에 열쇠가 없다.
    // 금고 이름 — 파이썬 teacher_config.vault_service 와 같은 값 (사람마다 무작위 "nlk-" + 12글자).
    // 돌려주는 값: "dpapi"/"plain"(설정에서 읽음) · "args"(자가 점검 인자) · "legacy"(설정에 없음 → 예전 고정 이름)
    static string UseVaultName(Dictionary<string, object> d)
    {
        if (ServiceFromArgs) return "args";
        string src;
        string v = ReadPrivateValue(d, "vault_service", out src);
        if (v == null || !System.Text.RegularExpressions.Regex.IsMatch(v, "^nlk-[0-9a-f]{12}$")) return "legacy";
        KEYRING_SERVICE = v;
        return src == "plain" ? "plain" : "dpapi";
    }

    static string ReadCertName(Dictionary<string, object> d, out string source)
    {
        return ReadPrivateValue(d, "cert_name", out source);
    }

    // 평문 설정 → 암호화 설정(secure\teacher_private.dpapi → .prev) 순서로 한 칸을 읽는다 (파이썬 load_merged 와 같은 순서)
    static string ReadPrivateValue(Dictionary<string, object> d, string key, out string source)
    {
        source = null;
        string v = Str(d, key);
        if (v != null) { source = "plain"; return v; }
        if (ConfigDir == null) return null;
        string main = Path.Combine(ConfigDir, "secure", "teacher_private.dpapi");
        foreach (var f in new[] { main, main + ".prev" })
        {
            try
            {
                if (!File.Exists(f)) continue;
                var js = new JavaScriptSerializer();
                var env = js.Deserialize<Dictionary<string, object>>(File.ReadAllText(f, Encoding.UTF8));
                if (Str(env, "format") != "dpapi-envelope/1" || Str(env, "purpose") != PRIVATE_PURPOSE) continue;
                byte[] blob = Convert.FromBase64String(Str(env, "blob"));
                byte[] plain = ProtectedData.Unprotect(blob, Encoding.UTF8.GetBytes("neis-secure/" + PRIVATE_PURPOSE),
                                                       DataProtectionScope.CurrentUser);
                var priv = js.Deserialize<Dictionary<string, object>>(Encoding.UTF8.GetString(plain));
                v = Str(priv, key);
                if (v != null) { source = f == main ? "dpapi" : "dpapi-prev"; return v; }
            }
            catch (Exception e) { Log("[설정] 암호화 설정을 못 읽음 — " + e.GetType().Name); }
        }
        return null;
    }

    // 파이썬 keyring 이 윈도우 자격 증명 관리자에 넣은 값을 읽는다.
    // 대상 이름은 "<금고 이름>"(그 계정이 마지막으로 저장된 것) 또는 "<계정>@<금고 이름>". 금고 이름은 UseVaultName 이 정한다.
    // ★ keyring 과 «같은 순서»(서비스 이름 먼저 → 계정@서비스)로 읽는다 (2026-11-09). 예전엔 계정@서비스를 먼저 봐서,
    //   keyring 이 남긴 낡은 사본이 있으면 옛 암호를 넣을 수 있었다.
    static bool PasswordExists(string account)
    {
        return PasswordTarget(account) != null;
    }

    // 그 계정 암호가 든 «대상 이름 종류»만 ("service" / "compound" / null) — 자가 점검에서 읽는 순서를 확인하는 데 쓴다
    static string PasswordTarget(string account)
    {
        foreach (var target in new[] { KEYRING_SERVICE, account + "@" + KEYRING_SERVICE })
        {
            IntPtr p;
            if (!CredRead(target, 1, 0, out p)) continue;
            try
            {
                var c = (CREDENTIAL)Marshal.PtrToStructure(p, typeof(CREDENTIAL));
                string user = c.UserName == null ? "" : Marshal.PtrToStringUni(c.UserName);
                if (user == account && c.CredentialBlobSize > 0) return target == KEYRING_SERVICE ? "service" : "compound";
            }
            finally { CredFree(p); }
        }
        return null;
    }

    // 암호를 «지울 수 있는 char 배열»로 돌려준다 (끝에 \0 하나 — WM_SETTEXT 에 그대로 넘긴다). 다 쓰면 부른 쪽이 Array.Clear.
    // 중간 byte 배열과 윈도우가 돌려준 버퍼도 0 으로 덮는다. string 은 만들지 않는다 (string 은 지울 수 없다).
    static char[] ReadPasswordChars(string account, out int len)
    {
        len = 0;
        foreach (var target in new[] { KEYRING_SERVICE, account + "@" + KEYRING_SERVICE })
        {
            IntPtr p;
            if (!CredRead(target, 1, 0, out p)) continue;
            var c = (CREDENTIAL)Marshal.PtrToStructure(p, typeof(CREDENTIAL));
            try
            {
                string user = c.UserName == null ? "" : Marshal.PtrToStringUni(c.UserName);
                if (user != account || c.CredentialBlobSize == 0) continue;
                var b = new byte[c.CredentialBlobSize];
                try
                {
                    Marshal.Copy(c.CredentialBlob, b, 0, b.Length);
                    int n = b.Length / 2;
                    var chars = new char[n + 1];
                    Encoding.Unicode.GetChars(b, 0, n * 2, chars, 0);
                    while (n > 0 && chars[n - 1] == '\0') n--;
                    chars[n] = '\0';
                    len = n;
                    return chars;
                }
                finally { Array.Clear(b, 0, b.Length); }
            }
            finally
            {
                if (c.CredentialBlob != IntPtr.Zero && c.CredentialBlobSize > 0)
                    ZeroMemory(c.CredentialBlob, (UIntPtr)c.CredentialBlobSize);
                CredFree(p);
            }
        }
        return null;
    }

    // ---------------------------------------------------------------- 2차 인증 창
    // 암호를 넣을 칸을 «하나로» 정한다: 입력칸이 하나뿐이면 그것, 여럿이면 «암호 칸(ES_PASSWORD)» 이 딱 하나일 때만.
    // 못 정하면 IntPtr.Zero — 넣지 않고 멈춘다 (2026-11-09 점검 반영: 예전엔 여러 개면 첫 칸에 넣었다)
    static IntPtr PickPasswordEdit(IntPtr dlg, out int edits, out int pwEdits)
    {
        var all = Children(dlg).Where(c => ClassOf(c) == "Edit").ToList();
        var pws = all.Where(c => (GetWindowLong(c, GWL_STYLE) & ES_PASSWORD) != 0).ToList();
        edits = all.Count;
        pwEdits = pws.Count;
        if (all.Count == 1) return all[0];
        if (pws.Count == 1) return pws[0];
        return IntPtr.Zero;
    }

    // AXGATE 2차 인증 창이 선생님 키보드 포커스를 뺏었으면 바로 돌려준다 (최소화 → 포커스 없이 복원).
    // 창은 화면에 그대로 보이지만 활성 창이 아니므로, 선생님이 치는 글자·Esc·Enter 가 이 창으로 들어가지 않는다.
    static void DefocusDialog(IntPtr dlg)
    {
        try
        {
            IntPtr fg = GetForegroundWindow();
            if (fg == IntPtr.Zero) return;
            IntPtr root = GetAncestor(fg, GA_ROOTOWNER);
            if (fg == dlg || root == dlg)
            {
                ShowWindow(dlg, SW_MINIMIZE);
                ShowWindow(dlg, SW_SHOWNOACTIVATE);
            }
        }
        catch { }
    }

    static int Handle2fa(IntPtr dlg, string certName)
    {
        DefocusDialog(dlg);
        IntPtr hdd = IntPtr.Zero, list = IntPtr.Zero, login = IntPtr.Zero;
        foreach (var c in Children(dlg))
        {
            string cl = ClassOf(c), tx = TextOf(c).Trim();
            if (cl == "Button" && tx == "하드디스크" && hdd == IntPtr.Zero) hdd = c;
            else if (cl == "Button" && tx == "로그인" && login == IntPtr.Zero) login = c;
            else if (cl == "SysListView32" && list == IntPtr.Zero) list = c;
        }
        int edits, pwEdits;
        IntPtr edit = PickPasswordEdit(dlg, out edits, out pwEdits);
        if (list == IntPtr.Zero || login == IntPtr.Zero)
        {
            Log("[2차인증] 창 안의 칸을 못 찾음 (목록=" + (list != IntPtr.Zero) + " 로그인=" + (login != IntPtr.Zero) + ")");
            return 0;
        }
        if (edit == IntPtr.Zero)
        {
            Log("[2차인증] 암호 칸을 하나로 정하지 못해 넣지 않습니다 (입력칸 " + edits + "개 · 암호칸 " + pwEdits + "개)");
            return 0;
        }
        if (hdd != IntPtr.Zero)
        {
            SendMessage(hdd, BM_CLICK, IntPtr.Zero, IntPtr.Zero);
            DefocusDialog(dlg);
            Thread.Sleep(150);
            for (int w = 0; w < 9; w++)
            {
                if ((int)SendMessage(list, LVM_GETITEMCOUNT, IntPtr.Zero, IntPtr.Zero) > 0) break;
                Thread.Sleep(50);
            }
        }

        int count = (int)SendMessage(list, LVM_GETITEMCOUNT, IntPtr.Zero, IntPtr.Zero);
        if (count <= 0) { Log("[2차인증] 인증서 목록이 비어 있습니다 (C:\\GPKI 에 인증서가 있는지)"); return 0; }

        // 인증서 이름이 들어 있는 줄을 찾는다. 딱 한 줄일 때만 고른다 — 여러 줄이면 멋대로 고르지 않는다.
        var rows = ReadListRows(list, count);
        int idx = -1;
        if (rows != null)
        {
            var hit = Enumerable.Range(0, count).Where(i => rows[i].Contains(certName)).ToList();
            if (hit.Count == 1) idx = hit[0];
            else { Log("[2차인증] 인증서 " + count + "개 중 이름이 맞는 것 " + hit.Count + "개 — 고르지 않고 멈춥니다"); return 0; }
        }
        else if (count == 1) idx = 0;
        else { Log("[2차인증] 인증서가 " + count + "개인데 목록 글자를 못 읽어 고르지 않습니다"); return 0; }

        if (!SelectRow(list, idx)) { Log("[2차인증] 인증서 줄 선택을 확인 못 함"); return 0; }
        Log("[2차인증] 인증서 " + count + "개 중 " + (idx + 1) + "번째 선택");

        // 넣기 «직전»에 한 번 더: 그 창이 아직 있고, 진짜 AXGATE(설치 때 확인한 실행 파일)의 창이며, 암호 칸이 그 창 안에 있는지
        if (!IsWindow(dlg) || !OwnedByAxgate(dlg) || !IsWindow(edit) || !IsChild(dlg, edit))
        {
            Log("[2차인증] 넣기 직전 확인에서 창이 바뀌어 넣지 않습니다");
            return 0;
        }
        DefocusDialog(dlg);
        int pwLen;
        char[] pw = ReadPasswordChars(certName, out pwLen);          // ← 모든 확인이 끝난 뒤에 금고에서
        if (pw == null) { Log("[2차인증] 암호를 읽지 못했습니다"); return 0; }
        try
        {
            // 고정(pin)한 char 배열 주소를 그대로 넘긴다 — string 을 새로 만들지 않는다
            var h = GCHandle.Alloc(pw, GCHandleType.Pinned);
            try
            {
                SendMessage(edit, WM_SETTEXT, IntPtr.Zero, h.AddrOfPinnedObject());
                int n = (int)SendMessage(edit, WM_GETTEXTLENGTH, IntPtr.Zero, IntPtr.Zero);
                if (n != pwLen) n = TypeChars(edit, pw, pwLen);
                // 혹시 타자가 섞여 길이가 어긋났으면 WM_SETTEXT 로 한 번 더 덮어쓴다
                if (n != pwLen)
                {
                    DefocusDialog(dlg);
                    SendMessage(edit, WM_SETTEXT, IntPtr.Zero, h.AddrOfPinnedObject());
                    n = (int)SendMessage(edit, WM_GETTEXTLENGTH, IntPtr.Zero, IntPtr.Zero);
                }
                if (n != pwLen) { Log("[2차인증] 암호 칸에 글자가 다 안 들어감 (" + n + "/" + pwLen + ")"); return 0; }
            }
            finally { h.Free(); }
            // SendMessage 로 누르면 오류 창이 닫힐 때까지 여기서 멈춘다 → PostMessage 로 누르고 오류 창을 따로 본다
            PostMessage(login, BM_CLICK, IntPtr.Zero, IntPtr.Zero);
        }
        finally { Array.Clear(pw, 0, pw.Length); }                    // 우리가 가진 배열은 0 으로 덮는다

        Log("[2차인증] 암호 넣고 [로그인]");

        // 거절 창이 뜨면 글자를 기록하고 닫는다 (원인 진단용). 경남 선생님 PC 에서 칸에 들어가 보여도 거절됐다(2026-10-06 제보)
        string err = WaitErrorBox(dlg, 6);
        if (err == null) return 1;
        Log("[2차인증] 암호 거절: " + err);
        return 2;
    }

    static int TypeChars(IntPtr edit, char[] pw, int pwLen)
    {
        SendMessage(edit, WM_SETTEXT, IntPtr.Zero, "");
        PostMessage(edit, WM_SETFOCUS, IntPtr.Zero, IntPtr.Zero);
        for (int i = 0; i < pwLen; i++) { PostMessage(edit, WM_CHAR, (IntPtr)pw[i], IntPtr.Zero); Thread.Sleep(40); }
        Thread.Sleep(400);
        return (int)SendMessage(edit, WM_GETTEXTLENGTH, IntPtr.Zero, IntPtr.Zero);
    }

    // [로그인] 뒤 AXGATE 가 띄운 오류 창을 찾는다. 경남 실제 글자(2026-10-06 캡처, 창 제목 AxgateVpnClient):
    //   «비밀번호 입력이 맞지 않습니다. 다시 입력해 주세요. (1 회)»
    // 오류 창(«비밀번호/암호 … 맞지/틀/일치/오류»)을 찾아 글자를 돌려주고 [확인] 으로 닫는다.
    // 암호 자체는 오류 창 글자에 없으므로 로그에 남겨도 된다.
    static string WaitErrorBox(IntPtr dlg, int seconds)
    {
        var until = DateTime.Now.AddSeconds(seconds);
        while (DateTime.Now < until)
        {
            Thread.Sleep(300);
            IntPtr box = IntPtr.Zero; string text = null;
            EnumWindows((h, _) =>
            {
                if (h == dlg || !IsWindowVisible(h) || ClassOf(h) != "#32770" || !OwnedByAxgate(h)) return true;
                string t = string.Join(" ", Children(h).Where(c => ClassOf(c) == "Static").Select(c => TextOf(c).Trim()).Where(s => s.Length > 0));
                if ((t.Contains("비밀번호") || t.Contains("암호")) && (t.Contains("맞지") || t.Contains("틀") || t.Contains("일치") || t.Contains("오류") || t.Contains("잘못")))
                { box = h; text = t.Replace("\r", " ").Replace("\n", " "); return false; }
                return true;
            }, IntPtr.Zero);
            if (box != IntPtr.Zero)
            {
                if (!ClickButton(box, "확인")) PostMessage(box, WM_CLOSE, IntPtr.Zero, IntPtr.Zero);
                Thread.Sleep(500);
                return text;
            }
        }
        return null;
    }

    // LVM_SETITEMSTATE 로 바로 고른다 — 키보드(WM_KEYDOWN)를 쓰지 않으므로 선생님이 Ctrl/Shift 를 누른 채
    // 타이핑 중이거나 창이 비활성 상태여도 영향을 받지 않고 즉시 선택된다. 안 먹히면 예전 키 방식으로 폴백한다.
    static bool SelectRowDirect(IntPtr list, int idx)
    {
        uint pid;
        GetWindowThreadProcessId(list, out pid);
        if (pid == 0) return false;
        IntPtr hp = OpenProcess(0x0008 | 0x0010 | 0x0020 | 0x0400, false, pid);
        if (hp == IntPtr.Zero) return false;
        try
        {
            IntPtr mem = VirtualAllocEx(hp, IntPtr.Zero, (IntPtr)256, 0x3000, 0x04);
            if (mem == IntPtr.Zero) return false;
            try
            {
                // LVITEM 앞 20바이트(mask·iItem·iSubItem·state·stateMask)는 32비트·64비트 모두 같은 자리다
                byte[] item = new byte[88];
                BitConverter.GetBytes(LVIF_STATE).CopyTo(item, 0);
                BitConverter.GetBytes(LVIS_FOCUSED | LVIS_SELECTED).CopyTo(item, 16);   // stateMask
                IntPtr w;
                // 1) 기존 선택 모두 해제
                WriteProcessMemory(hp, mem, item, (IntPtr)item.Length, out w);
                SendMessage(list, LVM_SETITEMSTATE, (IntPtr)(-1), mem);
                // 2) idx 한 줄만 선택·포커스
                BitConverter.GetBytes(idx).CopyTo(item, 4);
                BitConverter.GetBytes(LVIS_FOCUSED | LVIS_SELECTED).CopyTo(item, 12);   // state
                WriteProcessMemory(hp, mem, item, (IntPtr)item.Length, out w);
                SendMessage(list, LVM_SETITEMSTATE, (IntPtr)idx, mem);
                SendMessage(list, LVM_ENSUREVISIBLE, (IntPtr)idx, IntPtr.Zero);
                return (int)SendMessage(list, LVM_GETNEXTITEM, (IntPtr)(-1), (IntPtr)LVNI_SELECTED) == idx;
            }
            finally { VirtualFreeEx(hp, mem, IntPtr.Zero, 0x8000); }
        }
        finally { CloseHandle(hp); }
    }

    static bool SelectRow(IntPtr list, int idx)
    {
        if (SelectRowDirect(list, idx)) return true;
        PostMessage(list, WM_KEYDOWN, (IntPtr)VK_HOME, IntPtr.Zero); PostMessage(list, WM_KEYUP, (IntPtr)VK_HOME, IntPtr.Zero);
        Thread.Sleep(200);
        for (int i = 0; i < idx; i++)
        {
            PostMessage(list, WM_KEYDOWN, (IntPtr)VK_DOWN, IntPtr.Zero); PostMessage(list, WM_KEYUP, (IntPtr)VK_DOWN, IntPtr.Zero);
            Thread.Sleep(150);
        }
        for (int t = 0; t < 10; t++)
        {
            Thread.Sleep(150);
            if ((int)SendMessage(list, LVM_GETNEXTITEM, (IntPtr)(-1), (IntPtr)LVNI_SELECTED) == idx) return true;
        }
        return false;
    }

    // 다른 프로세스의 목록(SysListView32) 글자는 그 프로세스 메모리 안에서만 읽힌다.
    static string[] ReadListRows(IntPtr list, int count)
    {
        uint pid;
        GetWindowThreadProcessId(list, out pid);
        IntPtr hp = OpenProcess(0x0008 | 0x0010 | 0x0020 | 0x0400, false, pid);
        if (hp == IntPtr.Zero) return null;
        try
        {
            bool wow;
            IsWow64Process(hp, out wow);
            bool is32 = wow || IntPtr.Size == 4;
            IntPtr mem = VirtualAllocEx(hp, IntPtr.Zero, (IntPtr)4096, 0x3000, 0x04);
            if (mem == IntPtr.Zero) return null;
            try
            {
                var rows = new string[count];
                long textAddr = mem.ToInt64() + 1024;
                for (int i = 0; i < count; i++)
                {
                    var sb = new StringBuilder();
                    for (int sub = 0; sub < 5; sub++)
                    {
                        byte[] item = new byte[is32 ? 64 : 88];
                        BitConverter.GetBytes(1).CopyTo(item, 0);          // mask = LVIF_TEXT
                        BitConverter.GetBytes(i).CopyTo(item, 4);
                        BitConverter.GetBytes(sub).CopyTo(item, 8);
                        if (is32) { BitConverter.GetBytes((int)textAddr).CopyTo(item, 20); BitConverter.GetBytes(500).CopyTo(item, 24); }
                        else { BitConverter.GetBytes(textAddr).CopyTo(item, 24); BitConverter.GetBytes(500).CopyTo(item, 32); }
                        IntPtr w;
                        WriteProcessMemory(hp, mem, item, (IntPtr)item.Length, out w);
                        WriteProcessMemory(hp, (IntPtr)textAddr, new byte[1002], (IntPtr)1002, out w);
                        int n = (int)SendMessage(list, LVM_GETITEMTEXTW, (IntPtr)i, mem);
                        if (n > 0)
                        {
                            byte[] buf = new byte[1000];
                            IntPtr r;
                            ReadProcessMemory(hp, (IntPtr)textAddr, buf, (IntPtr)buf.Length, out r);
                            sb.Append(Encoding.Unicode.GetString(buf, 0, Math.Min(n * 2, 1000))).Append(" | ");
                        }
                    }
                    rows[i] = sb.ToString();
                }
                return rows.All(r => r.Length == 0) ? null : rows;
            }
            finally { VirtualFreeEx(hp, mem, IntPtr.Zero, 0x8000); }
        }
        finally { CloseHandle(hp); }
    }

    // ---------------------------------------------------------------- 창 찾기
    static List<IntPtr> Children(IntPtr h)
    {
        var l = new List<IntPtr>();
        EnumChildWindows(h, (c, _) => { l.Add(c); return true; }, IntPtr.Zero);
        return l;
    }

    static IntPtr FindDialogWithChild(string text)
    {
        IntPtr found = IntPtr.Zero;
        EnumWindows((h, _) =>
        {
            if (IsWindowVisible(h) && ClassOf(h) == "#32770" && OwnedByAxgate(h) &&
                Children(h).Any(c => TextOf(c).Contains(text))) { found = h; return false; }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    static IntPtr FindTopByTitle(string title)
    {
        IntPtr found = IntPtr.Zero;
        EnumWindows((h, _) =>
        {
            if (IsWindowVisible(h) && TextOf(h).Contains(title) && OwnedByAxgate(h)) { found = h; return false; }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    // AXGATE 가 띄운 창만 건드린다 (다른 프로그램의 «예» 버튼을 누르거나 암호를 넣지 않게).
    // 2026-11-09 점검 반영: 이름만 보던 것을 ① 실행 파일 «전체 경로»가 설치된 AXGATE 와 같고
    // ② 그 파일의 해시가 설치 때 기록한 기준(axgate_baseline.txt)과 같을 때만으로 좁힌다. 같은 이름의 다른 프로그램은 거른다.
    static readonly Dictionary<string, bool> OwnerChecked = new Dictionary<string, bool>();

    static string ProcessPath(uint pid)
    {
        IntPtr hp = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid);
        if (hp == IntPtr.Zero) return null;
        try
        {
            var sb = new StringBuilder(1024);
            uint size = (uint)sb.Capacity;
            return QueryFullProcessImageName(hp, 0, sb, ref size) ? sb.ToString() : null;
        }
        finally { CloseHandle(hp); }
    }

    static bool ExeMatchesBaseline(string path)
    {
        try
        {
            foreach (var line in File.ReadAllLines(Path.Combine(BaseDir, "axgate_baseline.txt")))
            {
                var p = line.Split('|');
                if (p.Length == 2 && p[0].Equals("AxgateVpnClient.exe", StringComparison.OrdinalIgnoreCase))
                    return string.Equals(Sha256(path), p[1], StringComparison.OrdinalIgnoreCase);
            }
        }
        catch { }
        return false;
    }

    static bool OwnedByAxgate(IntPtr h)
    {
        uint pid;
        GetWindowThreadProcessId(h, out pid);
        if (pid == 0) return false;
        string key;
        try { key = pid + ":" + Process.GetProcessById((int)pid).StartTime.Ticks; }      // 번호 재사용에 속지 않게 시작 시각까지
        catch { return false; }
        bool ok;
        if (OwnerChecked.TryGetValue(key, out ok)) return ok;
        string path = ProcessPath(pid);
        ok = path != null
             && string.Equals(Path.GetFullPath(path), Path.GetFullPath(AXGATE_EXE), StringComparison.OrdinalIgnoreCase)
             && ExeMatchesBaseline(path);
        OwnerChecked[key] = ok;
        if (!ok && path != null && Path.GetFileName(path).Equals("AxgateVpnClient.exe", StringComparison.OrdinalIgnoreCase))
            Log("[확인] 이름은 AXGATE 인데 설치 때 확인한 파일이 아닌 프로그램의 창 — 건드리지 않습니다");
        return ok;
    }

    static bool ClickButton(IntPtr dlg, string startsWith)
    {
        foreach (var c in Children(dlg))
            if (ClassOf(c) == "Button" && TextOf(c).Replace("&", "").StartsWith(startsWith))
            {
                SendMessage(c, BM_CLICK, IntPtr.Zero, IntPtr.Zero);
                return true;
            }
        return false;
    }

    static string TextOf(IntPtr h)
    {
        int n = GetWindowTextLength(h);
        var sb = new StringBuilder(n + 2);
        GetWindowText(h, sb, n + 2);
        return sb.ToString();
    }

    static string ClassOf(IntPtr h)
    {
        var sb = new StringBuilder(256);
        GetClassName(h, sb, 256);
        return sb.ToString();
    }

    // ---------------------------------------------------------------- Win32
    const uint BM_CLICK = 0x00F5, WM_SETFOCUS = 0x0007, WM_SETTEXT = 0x000C, WM_GETTEXTLENGTH = 0x000E, WM_CLOSE = 0x0010;
    const uint WM_KEYDOWN = 0x0100, WM_KEYUP = 0x0101, WM_CHAR = 0x0102;
    const uint LVM_GETITEMCOUNT = 0x1004, LVM_GETNEXTITEM = 0x100C, LVM_ENSUREVISIBLE = 0x1013, LVM_SETITEMSTATE = 0x102B, LVM_GETITEMTEXTW = 0x1073;
    const int LVIF_STATE = 0x0008, LVIS_FOCUSED = 0x0001, LVIS_SELECTED = 0x0002;
    const int LVNI_SELECTED = 2, VK_HOME = 0x24, VK_DOWN = 0x28;
    const int SW_SHOWNOACTIVATE = 4, SW_MINIMIZE = 6;
    const uint GA_ROOTOWNER = 3;

    delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc f, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr p, EnumProc f, IntPtr l);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern IntPtr GetAncestor(IntPtr h, uint flags);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] static extern int GetWindowTextLength(IntPtr h);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, string l);
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("kernel32.dll")] static extern IntPtr OpenProcess(uint a, bool i, uint pid);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32.dll")] static extern bool IsWow64Process(IntPtr h, out bool w);
    [DllImport("kernel32.dll")] static extern IntPtr VirtualAllocEx(IntPtr h, IntPtr a, IntPtr s, uint t, uint p);
    [DllImport("kernel32.dll")] static extern bool VirtualFreeEx(IntPtr h, IntPtr a, IntPtr s, uint t);
    [DllImport("kernel32.dll")] static extern bool WriteProcessMemory(IntPtr h, IntPtr a, byte[] b, IntPtr s, out IntPtr w);
    [DllImport("kernel32.dll")] static extern bool ReadProcessMemory(IntPtr h, IntPtr a, byte[] b, IntPtr s, out IntPtr r);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct CREDENTIAL
    {
        public uint Flags, Type; public IntPtr TargetName, Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public uint CredentialBlobSize; public IntPtr CredentialBlob; public uint Persist, AttributeCount;
        public IntPtr Attributes, TargetAlias, UserName;
    }
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool CredRead(string target, uint type, uint flags, out IntPtr cred);
    [DllImport("advapi32.dll")] static extern void CredFree(IntPtr p);
    [DllImport("kernel32.dll", EntryPoint = "RtlZeroMemory")] static extern void ZeroMemory(IntPtr dest, UIntPtr size);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "QueryFullProcessImageNameW")]
    static extern bool QueryFullProcessImageName(IntPtr hProcess, uint flags, StringBuilder name, ref uint size);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongW")] static extern int GetWindowLong(IntPtr h, int index);
    [DllImport("user32.dll")] static extern bool IsWindow(IntPtr h);
    [DllImport("user32.dll")] static extern bool IsChild(IntPtr parent, IntPtr child);
    const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;
    const int GWL_STYLE = -16, ES_PASSWORD = 0x0020;
}

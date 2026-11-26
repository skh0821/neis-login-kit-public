using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using System.Xml.Serialization;
using Forms = System.Windows.Forms;

namespace TeacherDeskDemo
{
    public sealed class Preferences
    {
        public double X = Double.NaN, Y = Double.NaN;
        public bool Topmost;
        public double Scale = 1;
    }

    // 상벌점 준비 상황 — riro_merit.status 가 낸 참/거짓만 담는다. 금고·학생 값은 들어오지 않는다.
    public sealed class Readiness
    {
        public const int Total = 4;
        public bool Ok, RiroAccount, LoginLocked, Divot, Rules, SheetKey, SheetId, SheetToken;
        public bool SheetReady { get { return SheetToken && SheetId; } }
        public bool AccountReady { get { return RiroAccount && !LoginLocked; } }
        public int Done { get { return (Divot ? 1 : 0) + (Rules ? 1 : 0) + (SheetReady ? 1 : 0) + (AccountReady ? 1 : 0); } }

        public static Readiness FromOutput(string output)
        {
            if (output == null) return null;
            string line = output.Split('\n').Select(l => l.Trim()).LastOrDefault(l => l.StartsWith("RESULT_JSON "));
            if (line == null) return null;
            Dictionary<string, object> d;
            try { d = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(line.Substring("RESULT_JSON ".Length)); }
            catch (Exception) { return null; }
            if (d == null) return null;
            Func<string, bool> flag = key => { object v; return d.TryGetValue(key, out v) && v is bool && (bool)v; };
            return new Readiness
            {
                Ok = flag("ok"), RiroAccount = flag("riro_account"), LoginLocked = flag("login_locked"), Divot = flag("divot"),
                Rules = flag("rules"), SheetKey = flag("sheet_key"), SheetId = flag("sheet_id"), SheetToken = flag("sheet_token")
            };
        }
    }

    public static class Program
    {
        [STAThread]
        public static int Main(string[] args)
        {
            // (인자 없음)   상벌점 창만 연다 — 바탕화면 «상벌점» 아이콘이 이걸 부른다. 창을 닫으면 프로그램이 끝난다 (2026-11-10)
            // --widget      예전 «교무 도구» 위젯 (늘 켜져 있음 · 작업표시줄·알림 영역)
            // --snapshot <폴더>: 실제 상태 확인을 한 번 한 뒤 창을 그림으로 남기고 끝낸다 (설정·바로가기는 건드리지 않는다)
            bool widgetMode = args.Contains("--widget");
            int at = Array.IndexOf(args, "--snapshot");
            string snapshot = at >= 0 && at + 1 < args.Length ? Path.GetFullPath(args[at + 1]) : null;
            try
            {
                bool created;
                string mutexName = snapshot != null ? "TeacherDeskDemo.Snapshot" : widgetMode ? "TeacherDeskDemo.UI" : "TeacherDeskDemo.Points";
                using (var mutex = new Mutex(true, mutexName, out created))
                {
                    if (!created)
                    {
                        if (snapshot != null) return 2;
                        // 이미 떠 있으면 새로 띄우지 않고 그 창을 앞으로 부른다
                        try { using (var show = EventWaitHandle.OpenExisting(Desk.ShowEventName(widgetMode))) show.Set(); }
                        catch (WaitHandleCannotBeOpenedException) { }
                        return 0;
                    }
                    var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
                    var desk = new Desk(app, snapshot, !widgetMode);
                    desk.Open();
                    app.Run();
                    return desk.ExitCode;
                }
            }
            catch (Exception ex)
            {
                if (snapshot != null)
                {
                    try { Directory.CreateDirectory(snapshot); File.WriteAllText(Path.Combine(snapshot, "snapshot-error.txt"), ex.ToString()); }
                    catch { }
                    return 1;
                }
                MessageBox.Show("교무 도구를 열지 못했습니다.\n" + ex.Message, "교무 도구");
                return 1;
            }
        }
    }

    public sealed class Desk
    {
        private static readonly Brush Paper = Color("#F6F3EC"), Ink = Color("#292D28"), Muted = Color("#656960"), Line = Color("#D9D8CF"), Green = Color("#365746"), Tint = Color("#E8EDE5"), Amber = Color("#805713");
        private readonly Application app;
        private readonly string snapshotDir;
        private readonly bool pointsOnly;   // 아이콘 방식: 상벌점 창 하나만, 닫으면 끝
        private readonly string statePath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "TeacherDeskDemo", "preferences.xml");
        private readonly Preferences prefs;
        private Window widget, work, settings;
        private Process setupProcess;
        private DispatcherTimer guard;
        private Forms.NotifyIcon tray;
        private EventWaitHandle showEvent;
        private bool closing, dragging;
        private double scale = 1;

        // 상벌점 준비 상황 (riro_merit.status)
        private Readiness ready;
        private bool checking, checkAgain, snapshotTaken;
        private string checkError;
        private TextBlock pointsRowLabel, footer, nextNote;
        private readonly TextBlock[] checkValues = new TextBlock[4];
        private readonly TextBlock[] stageHints = new TextBlock[4];
        private Button nextButton, recheck;
        private Action nextAction;
        public int ExitCode;

        public static string ShowEventName(bool widgetMode) { return widgetMode ? "TeacherDeskDemo.Show" : "TeacherDeskDemo.Points.Show"; }

        public Desk(Application app, string snapshotDir, bool pointsOnly)
        {
            this.app = app;
            this.snapshotDir = snapshotDir;
            this.pointsOnly = pointsOnly;
            prefs = snapshotDir != null ? new Preferences() : LoadPreferences();
            scale = prefs.Scale >= 1 && prefs.Scale <= 1.5 ? prefs.Scale : 1;
            app.Resources = (ResourceDictionary)XamlReader.Parse(@"
<ResourceDictionary xmlns='http://schemas.microsoft.com/winfx/2006/xaml/presentation' xmlns:x='http://schemas.microsoft.com/winfx/2006/xaml'>
 <Style TargetType='Button'>
  <Setter Property='Background' Value='Transparent'/><Setter Property='Foreground' Value='#292D28'/>
  <Setter Property='BorderBrush' Value='#D9D8CF'/><Setter Property='BorderThickness' Value='1'/>
  <Setter Property='Padding' Value='10,7'/><Setter Property='MinHeight' Value='32'/>
  <Setter Property='FontSize' Value='12'/><Setter Property='Cursor' Value='Hand'/>
  <Setter Property='Template'><Setter.Value><ControlTemplate TargetType='Button'>
   <Border x:Name='Bd' CornerRadius='4' Padding='{TemplateBinding Padding}' Background='{TemplateBinding Background}' BorderBrush='{TemplateBinding BorderBrush}' BorderThickness='{TemplateBinding BorderThickness}'>
    <ContentPresenter HorizontalAlignment='{TemplateBinding HorizontalContentAlignment}' VerticalAlignment='Center'/>
   </Border>
   <ControlTemplate.Triggers>
    <Trigger Property='IsMouseOver' Value='True'><Setter TargetName='Bd' Property='Opacity' Value='0.78'/></Trigger>
    <Trigger Property='IsKeyboardFocused' Value='True'><Setter TargetName='Bd' Property='BorderBrush' Value='#365746'/><Setter TargetName='Bd' Property='BorderThickness' Value='2'/></Trigger>
    <Trigger Property='IsEnabled' Value='False'><Setter TargetName='Bd' Property='Opacity' Value='0.42'/></Trigger>
   </ControlTemplate.Triggers>
  </ControlTemplate></Setter.Value></Setter>
 </Style>
 <Style TargetType='ComboBox'><Setter Property='Padding' Value='7,5'/><Setter Property='FontSize' Value='12'/><Setter Property='MinHeight' Value='30'/></Style>
 <Style TargetType='CheckBox'><Setter Property='Margin' Value='0,6,0,6'/><Setter Property='FontSize' Value='12'/></Style>
</ResourceDictionary>");
        }
        private static Brush Color(string hex) { return (Brush)new BrushConverter().ConvertFromString(hex); }
        private static TextBlock Text(string value, double size, Brush brush)
        {
            var block = new TextBlock { Text = value, FontSize = size, Foreground = brush, TextWrapping = TextWrapping.Wrap, VerticalAlignment = VerticalAlignment.Center };
            Typography.SetNumeralAlignment(block, FontNumeralAlignment.Tabular); return block;
        }
        private static Border Rule() { return new Border { Height = 1, Background = Line, Margin = new Thickness(0, 12, 0, 12) }; }
        private Button Button(string label, Action click, bool primary = false)
        {
            var b = new Button { Content = label, Background = primary ? Green : Brushes.Transparent, Foreground = primary ? Brushes.White : Ink };
            b.Click += delegate { click(); };
            return b;
        }
        private static Border Badge(string text)
        {
            return new Border { Background = Color("#EEECE4"), CornerRadius = new CornerRadius(3), Padding = new Thickness(7, 3, 7, 3), Child = Text(text, 11, Muted), VerticalAlignment = VerticalAlignment.Center };
        }
        private static Grid Pair(UIElement left, UIElement right)
        {
            var g = new Grid(); g.ColumnDefinitions.Add(new ColumnDefinition()); g.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            g.Children.Add(left); Grid.SetColumn(right, 1); g.Children.Add(right); return g;
        }
        private Window Shell(string title, double width, StackPanel body, bool scroll)
        {
            var w = new Window { Title = title, Width = width * scale, SizeToContent = SizeToContent.Height, WindowStyle = WindowStyle.None, ResizeMode = ResizeMode.NoResize, AllowsTransparency = true, Background = Brushes.Transparent, ShowInTaskbar = true, Topmost = prefs.Topmost, UseLayoutRounding = true };
            Window owner = widget ?? work;   // 아이콘 방식에서는 안내 창이 상벌점 창에 딸린다 (작업표시줄에 따로 안 뜸)
            if (owner != null) { w.Owner = owner; w.ShowInTaskbar = false; }
            w.FontFamily = new FontFamily(Fonts.SystemFontFamilies.Any(f => f.Source == "Pretendard") ? "Pretendard" : "Malgun Gothic");
            w.Foreground = Ink;
            var border = new Border { Background = Paper, BorderBrush = Line, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(8), Padding = new Thickness(16), Margin = new Thickness(5), Child = body };
            border.Effect = new System.Windows.Media.Effects.DropShadowEffect { BlurRadius = 8, ShadowDepth = 2, Opacity = 0.12 };
            FrameworkElement root = border;
            if (scroll)
            {
                var viewer = new ScrollViewer { Content = body, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, MaxHeight = 700 / scale, Focusable = false };
                border.Child = viewer;
            }
            root.LayoutTransform = new ScaleTransform(scale, scale);
            w.Content = root;
            w.PreviewKeyDown += delegate(object s, KeyEventArgs e)
            {
                if (e.Key == Key.Escape) { if (w == widget) Hide(); else w.Close(); e.Handled = true; }
                else if (e.Key == Key.Enter && Keyboard.FocusedElement is Button && ((Button)Keyboard.FocusedElement).IsEnabled) { ((Button)Keyboard.FocusedElement).RaiseEvent(new RoutedEventArgs(System.Windows.Controls.Primitives.ButtonBase.ClickEvent)); e.Handled = true; }
            };
            w.SourceInitialized += delegate { Fit(w); };
            return w;
        }
        private Grid Header(string title, Action close, bool main)
        {
            var label = Text(title, main ? 16 : 18, Ink); label.FontWeight = FontWeights.SemiBold;
            label.Cursor = Cursors.SizeAll;
            label.MouseLeftButtonDown += delegate(object s, MouseButtonEventArgs e)
            {
                var window = Window.GetWindow(label);
                dragging = true;
                try { window.DragMove(); } catch (InvalidOperationException) { }
                finally { dragging = false; Fit(window); if (main) SavePosition(); }
            };
            var control = Button(main ? "설정" : "닫기", close);
            control.Padding = new Thickness(7, 3, 7, 3); control.MinHeight = 26; control.FontSize = 11;
            var row = Pair(label, control); row.Margin = new Thickness(0, 0, 0, 12); return row;
        }

        public void Open()
        {
            if (pointsOnly)
            {
                // 아이콘 방식 — 위젯·알림 영역 아이콘 없이 상벌점 창 하나만
                if (snapshotDir == null) showEvent = new EventWaitHandle(false, EventResetMode.AutoReset, ShowEventName(false));
                StartGuard();
                OpenWork(false);
                if (snapshotDir != null) RefreshStatus();   // 그림 확인 때만 (상벌점 창에는 이제 준비물 목록이 없다)
                return;
            }
            var body = new StackPanel();
            body.Children.Add(Header("교무 도구", OpenSettings, true));
            string[] names = { "상벌점", "리로스쿨", "나이스", "에듀파인" };
            for (int i = 0; i < names.Length; i++)
            {
                string name = names[i];
                var row = Button("", delegate
                {
                    if (name == "상벌점") OpenWork(true);
                    else if (name == "리로스쿨") OpenRiroSettings();
                    else Info(name + " 연결", "연결 준비 중입니다.");
                });
                System.Windows.Automation.AutomationProperties.SetName(row, name);
                string label = i == 0 ? "작업 단계" : i == 1 ? "계정 설정" : "준비 중";
                var right = Text(label, 10, Muted);
                if (i == 0) pointsRowLabel = right;
                row.Content = Pair(Text(name, 13, i == 0 ? Green : Ink), right);
                row.HorizontalContentAlignment = HorizontalAlignment.Stretch; row.Margin = new Thickness(0, 0, 0, 3); row.Padding = new Thickness(10, 6, 10, 6);
                row.BorderThickness = new Thickness(0); row.Background = i == 0 ? Tint : Brushes.Transparent;
                if (i == 0) row.FontWeight = FontWeights.SemiBold;
                body.Children.Add(row);
            }
            body.Children.Add(new Border { Height = 1, Background = Line, Margin = new Thickness(0, 8, 0, 9) });
            var hide = Button("숨기기", Hide); hide.FontSize = 10; hide.MinHeight = 23; hide.Padding = new Thickness(6, 2, 6, 2); hide.BorderThickness = new Thickness(0);
            footer = Text("상벌점 준비 상황 확인 중", 11, Muted);
            body.Children.Add(Pair(footer, hide));
            widget = Shell("교무 도구", 300, body, false);
            widget.Left = Double.IsNaN(prefs.X) ? SystemParameters.WorkArea.Right - 330 : prefs.X;
            widget.Top = Double.IsNaN(prefs.Y) ? SystemParameters.WorkArea.Top + 40 : prefs.Y;
            widget.Closing += delegate { if (!closing) Shutdown(); };
            widget.Show(); Fit(widget);

            if (snapshotDir == null)
            {
                tray = new Forms.NotifyIcon { Icon = System.Drawing.SystemIcons.Application, Text = "교무 도구 · 두 번 눌러 열기", Visible = true };
                var menu = new Forms.ContextMenuStrip();
                menu.Items.Add("교무 도구 열기", null, delegate { app.Dispatcher.BeginInvoke(new Action(Show)); });
                menu.Items.Add("설정", null, delegate { app.Dispatcher.BeginInvoke(new Action(OpenSettings)); });
                menu.Items.Add("종료", null, delegate { app.Dispatcher.BeginInvoke(new Action(Shutdown)); });
                tray.ContextMenuStrip = menu;
                tray.DoubleClick += delegate { app.Dispatcher.BeginInvoke(new Action(Show)); };
                showEvent = new EventWaitHandle(false, EventResetMode.AutoReset, ShowEventName(true));
            }
            StartGuard();
            RefreshStatus();
        }

        private void StartGuard()
        {
            guard = new DispatcherTimer { Interval = TimeSpan.FromSeconds(2) };
            guard.Tick += delegate
            {
                if (showEvent != null && showEvent.WaitOne(0)) { if (pointsOnly) OpenWork(true); else Show(); }
                if (!dragging) foreach (var window in new[] { widget, work, settings }) if (window != null && window.IsVisible) Fit(window);
            };
            guard.Start();
        }

        private void OpenWork(bool refresh)
        {
            if (work != null) { work.Show(); work.WindowState = WindowState.Normal; work.Activate(); if (refresh) RefreshStatus(); return; }

            // 2026-11-10 선생님 영상 «상벌점 기능» 그대로: 디벗 미보관 학번 → 리로스쿨 «점수 입력» 빈칸. 항목·점수·[저장]은 선생님.
            // (예전 «준비물 4가지·작업 단계 4개»는 구글 시트에 저장해 등록까지 하던 옛 설계라 이 창에서 뺐다)
            var body = new StackPanel();
            body.Children.Add(Header("디벗 미보관 상벌점", delegate { work.Close(); }, false));
            body.Children.Add(Text("디벗 보관함의 «미보관» 또는 «케이블 미연결» 학생 학번을 리로스쿨 «상벌점 점수 입력» 빈칸에 넣습니다. 항목·점수 고르기와 [저장]은 리로스쿨 창에서 직접 해 주세요.", 12, Muted));
            body.Children.Add(Rule());

            // 11-11 선생님 요청: 학번은 넣지 않고 리로스쿨 로그인 → «상벌점 점수 입력» 화면까지만 (riro_open merit)
            loginButton = Button(LoginLabel, delegate { RunFill("login"); });
            loginButton.Margin = new Thickness(0, 0, 0, 14);
            body.Children.Add(loginButton);

            fillDate = new DatePicker { SelectedDate = DateTime.Today, DisplayDateEnd = DateTime.Today, Width = 150, FontSize = 12,
                                        Language = XmlLanguage.GetLanguage("ko-KR"), SelectedDateFormat = DatePickerFormat.Short };
            var dayRow = Pair(Text("날짜", 13, Ink), fillDate); dayRow.Margin = new Thickness(0, 0, 0, 10);
            body.Children.Add(dayRow);
            fillButton = Button(FillLabels["unsaved"] + " 학번 채우기", delegate { RunFill("unsaved"); }, true);
            body.Children.Add(fillButton);
            fillCableButton = Button(FillLabels["cable"] + " 학번 채우기", delegate { RunFill("cable"); }, true);   // 11-10 선생님 요청
            fillCableButton.Margin = new Thickness(0, 7, 0, 0);
            body.Children.Add(fillCableButton);
            fillStatus = Text("", 12, Muted); fillStatus.Margin = new Thickness(0, 10, 0, 0);
            body.Children.Add(fillStatus);
            body.Children.Add(Rule());
            body.Children.Add(Text("학번만 옮깁니다. 학생 이름은 읽지 않고, 학번도 파일·기록에 남기지 않습니다. 디벗 로그인 화면이나 리로스쿨 추가 인증이 뜨면 열린 창에서 직접 해 주세요.", 11, Muted));
            var settingsButton = Button("디벗·리로스쿨 연결 설정", delegate { OpenRiroSettings(); });
            settingsButton.Margin = new Thickness(0, 10, 0, 0);
            body.Children.Add(settingsButton);

            work = Shell("디벗 미보관 상벌점", 420, body, true);
            work.Closed += delegate
            {
                work = null; nextButton = null; recheck = null; nextNote = null; fillButton = null; fillCableButton = null; loginButton = null; fillStatus = null; fillDate = null;
                if (pointsOnly && !closing) Shutdown();   // 아이콘 방식: 창을 닫으면 프로그램도 끝 (채우기가 붙잡은 리로스쿨 창은 그대로 남는다)
            };
            RenderFill();
            RenderStatus();
            Place(work); work.Show(); Fit(work);
            if (refresh && snapshotDir == null) RefreshStatus();
        }

        // ── 미보관 학번 채우기: python -m riro_merit.fill_unsaved --date … 를 창 없이 돌리고 출력 줄로 진행을 보여 준다 ──
        // STEP 하는 일 · COUNT 그 탭 학생 수 · LEFT 못 넣은 학번 · CLIP 사유 복사 · READY 넣은 수 · FAIL 이유.
        // READY 뒤 프로세스는 리로스쿨 크롬과 «연결만 끊고» 바로 끝난다 (창은 남는다 — 확인 창이 선생님께 그대로 뜨게, 11-10).
        // 다시 누르면 떠 있는 리로스쿨 창에 «새 탭»으로 붙는다.
        // kind "login"(11-11)은 같은 틀로 riro_open merit 를 돌린다 — 로그인 → 점수 입력 화면까지만 (STEP·READY·FAIL 만 나온다).
        private DatePicker fillDate;
        private Button fillButton, fillCableButton, loginButton;
        private TextBlock fillStatus;
        private Process fillProcess;
        private bool fillReady;
        private string fillKind = "unsaved";   // unsaved = 미보관 탭 · cable = 케이블 미연결 탭 (fill_unsaved --kind) · login = 로그인만
        private static readonly Dictionary<string, string> FillLabels = new Dictionary<string, string> { { "unsaved", "미보관" }, { "cable", "케이블 미연결" } };
        private const string LoginLabel = "상벌점 화면 열기 (로그인만)";
        private static string JobTitle(string kind) { return kind == "login" ? "상벌점 화면 열기" : FillLabels[kind] + " 학번 채우기"; }
        private string fillLeft = "", fillError = "", fillPhase = "";
        private int fillCount = -1, fillDone = -1;
        private int fillClip = -1;      // CLIP n: 사유 문구 n가지 중 하나를 클립보드에 (0 = 문구 없음) · -2 = 넣지 못함 · -1 = 아직
        private static readonly System.Text.RegularExpressions.Regex ErrorLine = new System.Text.RegularExpressions.Regex(
            @"^(?:[A-Za-z_]\w*\.)*([A-Za-z_]\w*(?:Error|Exception))(?::\s*(.*))?$");

        private void RunFill(string kind)
        {
            if (fillProcess != null) return;
            bool login = kind == "login";
            string root = KitRoot();
            string python = Path.Combine(root, ".venv", "Scripts", "python.exe");
            if (!File.Exists(python) || !File.Exists(Path.Combine(root, "riro_merit", login ? "riro_open.py" : "fill_unsaved.py")))
            {
                Info(JobTitle(kind), "키트의 파이썬 환경(.venv)을 찾지 못했습니다.");
                return;
            }
            DateTime day = fillDate != null && fillDate.SelectedDate.HasValue ? fillDate.SelectedDate.Value : DateTime.Today;
            fillKind = kind;
            fillReady = false; fillLeft = ""; fillError = ""; fillPhase = login ? "리로스쿨 여는 중" : "준비 중"; fillCount = -1; fillDone = -1; fillClip = -1;
            string args = login ? "-X utf8 -m riro_merit.riro_open merit"
                                : "-X utf8 -m riro_merit.fill_unsaved --kind " + kind + " --date " + day.ToString("yyyy-MM-dd");
            var info = new ProcessStartInfo(python, args)
            {
                WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
            };
            info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            info.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
            var p = new Process { StartInfo = info, EnableRaisingEvents = true };
            p.OutputDataReceived += (s, e) => { if (e.Data != null) app.Dispatcher.BeginInvoke(new Action(() => OnFillLine(e.Data))); };
            p.ErrorDataReceived += (s, e) =>
            {
                var m = e.Data == null ? null : ErrorLine.Match(e.Data.Trim());   // 파이썬 오류의 요약 줄만 (호출 기록·머리글 줄은 버린다)
                if (m == null || !m.Success) return;
                string why = m.Groups[1].Value + (m.Groups[2].Value.Length > 0 ? ": " + m.Groups[2].Value : "");
                app.Dispatcher.BeginInvoke(new Action(() => { if (fillError.Length == 0) fillError = why.Length > 200 ? why.Substring(0, 200) : why; }));
            };
            p.Exited += (s, e) =>
            {
                try { ((Process)s).WaitForExit(); } catch (InvalidOperationException) { }   // 남은 출력 줄을 다 읽은 뒤에
                app.Dispatcher.BeginInvoke(new Action(FillExited));
            };
            try { p.Start(); p.BeginOutputReadLine(); p.BeginErrorReadLine(); fillProcess = p; }
            catch (Exception) { Info(JobTitle(kind), (login ? "리로스쿨 여는" : "채우기") + " 프로그램을 시작하지 못했습니다."); return; }
            RenderFill();
        }

        private void OnFillLine(string line)
        {
            string t = line.Trim();
            if (t.StartsWith("STEP ")) fillPhase = t.Substring(5).Trim();
            else if (t.StartsWith("COUNT ")) int.TryParse(t.Substring(6).Trim(), out fillCount);
            else if (t.StartsWith("LEFT ")) fillLeft = t.Substring(5).Trim();
            else if (t.StartsWith("CLIPFAIL")) fillClip = -2;
            else if (t.StartsWith("CLIP ")) { if (!int.TryParse(t.Substring(5).Trim(), out fillClip)) fillClip = -1; }
            else if (t.StartsWith("FAIL ")) fillError = t.Substring(5).Trim();
            else if (t.StartsWith("READY"))
            {
                fillReady = true;
                int.TryParse(t.Substring(5).Trim(), out fillDone);
                RenderFill();
                if (fillLeft.Length > 0)
                    Notice("남은 학번", "리로스쿨에 넣지 못한 학번이 " + fillLeft.Split(',').Length + "명 있습니다.\n"
                        + "빈칸이 모자랐거나, 리로스쿨에서 그 학번의 학생을 찾지 못했습니다.\n\n"
                        + fillLeft.Replace(",", ", ") + "\n\n리로스쿨 창에서 직접 넣어 주세요.");
                return;
            }
            RenderFill();
        }

        private void FillExited()
        {
            int code = -1;
            if (fillProcess != null)
            {
                try { code = fillProcess.ExitCode; } catch (InvalidOperationException) { }
                fillProcess.Dispose();
                fillProcess = null;
            }
            if (!fillReady)
            {
                fillPhase = "멈춤";
                RenderFill();
                Notice(JobTitle(fillKind), "잘못하신 게 아닙니다.\n\n"
                    + (fillKind == "login" ? "리로스쿨 상벌점 화면을 열지 못했습니다." : "학번을 채우지 못했습니다.") + "\n이유: "
                    + (fillError.Length > 0 ? fillError : "이유를 받지 못했습니다 (끝 번호 " + code + ")")
                    + "\n\n이 창의 내용을 클로드에게 보여 주시면 이어서 봐 드립니다.");
                return;
            }
            RenderFill();
        }

        private void RenderFill()
        {
            if (fillButton == null || fillCableButton == null || loginButton == null || fillStatus == null) return;
            bool running = fillProcess != null;
            bool login = fillKind == "login";
            string label = login ? "" : FillLabels[fillKind];
            // 리로스쿨 전용 크롬은 한 번에 하나라, 하나가 도는 동안은 세 단추 모두 잠근다. 도는 쪽 단추에 진행 글자를
            loginButton.IsEnabled = !running;
            loginButton.Content = running && login ? (fillReady ? "다 열었습니다" : "리로스쿨 로그인 중…") : LoginLabel;
            foreach (var pair in new[] { new KeyValuePair<string, Button>("unsaved", fillButton), new KeyValuePair<string, Button>("cable", fillCableButton) })
            {
                pair.Value.IsEnabled = !running;
                pair.Value.Content = running && pair.Key == fillKind
                    ? (fillReady ? "리로스쿨 창을 닫으면 다시 할 수 있어요" : label + " 채우는 중…")
                    : FillLabels[pair.Key] + " 학번 채우기";
            }
            if (fillDate != null) fillDate.IsEnabled = !running;
            string text; Brush brush = Muted;
            if (fillReady && login)
            {
                text = "리로스쿨 «상벌점 점수 입력» 화면을 열었습니다 (학번은 넣지 않았습니다).\n리로스쿨 창에서 학번·항목·점수를 넣고 [저장] 해 주세요.";
                brush = Green;
            }
            else if (fillReady && fillCount == 0) text = "그날 " + label + " 학생이 없습니다.";
            else if (fillReady)
            {
                text = label + " 학번 " + fillDone + "명을 리로스쿨 빈칸에 넣었습니다"
                    + (fillLeft.Length > 0 ? " · 못 넣은 " + fillLeft.Split(',').Length + "명은 안내창을 봐 주세요" : "") + ".\n"
                    + (fillClip > 1 ? "사유 문구가 " + fillClip + "가지라 가장 많은 문구를 클립보드에 넣었습니다 — 내용 칸에 Ctrl+V.\n"
                       : fillClip == 1 ? "사유 및 특이사항 문구를 클립보드에 넣었습니다 — 내용 칸에 Ctrl+V.\n"
                       : fillClip == 0 ? "사유 및 특이사항 칸이 비어 있어 클립보드에는 넣지 않았습니다.\n"
                       : fillClip == -2 ? "사유 문구를 클립보드에 넣지 못했습니다 — 디벗에서 직접 복사해 주세요.\n" : "")
                    + "리로스쿨 창에서 항목·점수를 고르고 [저장] 해 주세요.";
                brush = Green;
            }
            else if (running) text = fillPhase + (fillCount >= 0 ? "  ·  " + label + " " + fillCount + "명" : "");
            else if (fillPhase == "멈춤") { text = "멈췄습니다" + (fillError.Length > 0 ? " — " + fillError : ""); brush = Amber; }
            else text = "날짜를 고르고 단추를 누르면 디벗에서 그 탭의 학번을 읽고 리로스쿨 «점수 입력» 창을 엽니다.";
            fillStatus.Text = text;
            fillStatus.Foreground = brush;
        }

        // 리로스쿨(크롬) 창이 앞에 있어도 보이도록 맨 위 알림으로 띄운다
        private static void Notice(string title, string message)
        {
            MessageBox.Show(message, title, MessageBoxButton.OK, MessageBoxImage.Information, MessageBoxResult.OK, MessageBoxOptions.DefaultDesktopOnly);
        }

        // ── 준비 상황: riro_merit.status 를 창 없이 불러 참/거짓만 받는다 ──
        private static string KitRoot() { return Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", "..", "..")); }

        private void RefreshStatus()
        {
            if (checking) { checkAgain = true; return; }
            string root = KitRoot();
            string python = Path.Combine(root, ".venv", "Scripts", "python.exe");
            if (!File.Exists(python) || !File.Exists(Path.Combine(root, "riro_merit", "status.py")))
            {
                checkError = "키트의 파이썬 환경(.venv)을 찾지 못했습니다";
                StatusArrived();
                return;
            }
            checking = true; checkAgain = false;
            RenderStatus();
            ThreadPool.QueueUserWorkItem(delegate
            {
                Readiness result = null; string error = null;
                try
                {
                    var start = new ProcessStartInfo(python, "-X utf8 -m riro_merit.status")
                    {
                        WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
                        RedirectStandardOutput = true, RedirectStandardError = true,
                        StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
                    };
                    start.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
                    using (var p = Process.Start(start))
                    {
                        var output = p.StandardOutput.ReadToEndAsync();
                        p.StandardError.ReadToEndAsync();
                        if (!p.WaitForExit(30000))
                        {
                            try { p.Kill(); } catch { }
                            error = "준비 상황 확인이 30초 안에 끝나지 않았습니다";
                        }
                        else if (output.Wait(5000)) result = Readiness.FromOutput(output.Result);
                        if (result == null && error == null) error = "준비 상황을 읽지 못했습니다";
                    }
                }
                catch (Exception) { error = "준비 상황 확인 프로그램을 시작하지 못했습니다"; }
                app.Dispatcher.BeginInvoke(new Action(delegate
                {
                    checking = false;
                    if (result != null) { ready = result; checkError = result.Ok ? null : "리로스쿨 설정 파일을 읽지 못했습니다 — 보안 설정창에서 다시 저장해 주세요"; }
                    else checkError = error;
                    StatusArrived();
                }));
            });
        }

        private void StatusArrived()
        {
            RenderStatus();
            if (checkAgain) { RefreshStatus(); return; }
            if (snapshotDir != null && !snapshotTaken)
            {
                snapshotTaken = true;
                if (work == null) OpenWork(false);
                var wait = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(900) };
                wait.Tick += delegate { wait.Stop(); TakeSnapshot(); };
                wait.Start();
            }
        }

        private void RenderStatus()
        {
            var r = ready;
            if (pointsRowLabel != null) pointsRowLabel.Text = r == null ? (checking ? "확인 중" : "작업 단계") : "준비 " + r.Done + "/" + Readiness.Total;
            if (footer != null)
            {
                footer.Text = checkError ?? (r == null ? "상벌점 준비 상황 확인 중"
                    : r.LoginLocked ? "리로스쿨 비밀번호를 다시 넣어 주세요"
                    : r.Done == Readiness.Total ? "준비물 다 갖춤 · 등록 기능 만드는 중"
                    : "상벌점 준비물 " + r.Done + "/" + Readiness.Total);
                footer.Foreground = checkError != null || (r != null && r.LoginLocked) ? Amber : Muted;
            }
            if (work == null || nextButton == null) return;   // 상벌점 창은 이제 «미보관 학번 채우기» 화면 (준비물 목록 없음)

            if (recheck != null) { recheck.IsEnabled = !checking; recheck.Content = checking ? "확인 중…" : "다시 확인"; }
            if (r == null)
            {
                for (int i = 0; i < 4; i++) { checkValues[i].Text = checking ? "확인 중" : "모름"; checkValues[i].Foreground = Muted; }
            }
            else
            {
                Mark(0, r.Divot, "확인됨", "아직");
                Mark(1, r.Rules, "확인함", "아직");
                Mark(2, r.SheetReady, "연결됨", !r.SheetToken && !r.SheetKey ? "구글 열쇠 없음" : !r.SheetToken ? "연결 전" : "시트 주소 필요");
                Mark(3, r.AccountReady, "저장됨", r.LoginLocked ? "비밀번호 거절됨" : "아직 없음");
                if (r.LoginLocked) checkValues[3].Foreground = Amber;
            }

            string note, label; Action action;
            NextStep(r, out label, out action, out note);
            nextAction = action;
            nextButton.Content = label;
            nextButton.IsEnabled = action != null;
            nextButton.Visibility = r == null ? Visibility.Collapsed : Visibility.Visible;   // 모를 때는 아래 «학교 기준·연결 설정» 하나만
            nextNote.Text = checkError ?? note;
            nextNote.Foreground = checkError != null ? Amber : Muted;

            Hint(0, r != null && r.Divot && r.Rules, "준비물 갖춤 · 학번 뽑기 기능은 아직 만드는 중",
                Need(r == null || !r.Divot ? "디벗 연결" : null, r == null || !r.Rules ? "학교 기준 확인" : null));
            Hint(1, r != null && r.SheetReady, "시트 연결됨 · 저장 기능은 아직 만드는 중",
                r == null ? "구글 열쇠와 학교에서 허용한 시트 필요" : !r.SheetKey && !r.SheetToken ? "구글 열쇠(oauth_client.json) 필요" : !r.SheetToken ? "구글 시트 연결 필요" : "시트 주소 필요");
            Hint(2, r != null && r.AccountReady, "계정 준비됨 · 리로스쿨 상벌점 입력 화면 점검 필요",
                r != null && r.LoginLocked ? "비밀번호가 거절됨 · 새로 넣어야 다시 시도" : "리로스쿨 계정 · 상벌점 입력 화면 점검 필요");
            Hint(3, false, "", "리로스쿨 부여 내역 화면 점검 필요");
        }

        private void Mark(int i, bool done, string yes, string no)
        {
            checkValues[i].Text = done ? "✓ " + yes : no;
            checkValues[i].Foreground = done ? Green : Muted;
        }
        private void Hint(int i, bool met, string yes, string no)
        {
            stageHints[i].Text = met ? yes : no;
            stageHints[i].Foreground = met ? Green : Muted;
        }
        private static string Need(params string[] parts)
        {
            var left = parts.Where(p => p != null).ToArray();
            return left.Length == 0 ? "" : String.Join(" · ", left) + " 필요";
        }

        // 다음에 할 일 하나만 단추로 — 단계 순서대로 (디벗 → 학교 기준 → 리로스쿨 계정 → 구글 시트)
        private void NextStep(Readiness r, out string label, out Action action, out string note)
        {
            if (r == null)
            {
                label = ""; action = null;
                note = "준비 상황을 확인하고 있습니다.";
            }
            else if (!r.Divot)
            {
                label = "디벗 연결"; action = delegate { OpenRiroSettings(true); };
                note = "먼저 디벗에 연결해 미보관 목록이 어떤 모양인지 확인합니다. 학생 이름·학번은 저장하지 않습니다.";
            }
            else if (!r.Rules)
            {
                label = "우리 학교 기준 입력"; action = delegate { OpenRiroSettings(); };
                note = "보안 설정창 «우리 학교 기준»에 벌점 항목·점수·미보관 확정 시각을 넣고, «학교 기준을 확인했습니다»에 체크한 뒤 저장해 주세요.";
            }
            else if (!r.AccountReady)
            {
                label = r.LoginLocked ? "리로스쿨 계정 다시 넣기" : "리로스쿨 계정 넣기"; action = delegate { OpenRiroSettings(); };
                note = r.LoginLocked
                    ? "리로스쿨이 비밀번호를 거절해서 다시 시도하지 않고 있습니다. «아이디·비밀번호 넣기/바꾸기»로 새로 넣어 주세요."
                    : "보안 설정창의 «아이디·비밀번호 넣기/바꾸기»로 넣어 주세요. 비밀번호는 Windows 금고에만 들어갑니다.";
            }
            else if (!r.SheetReady)
            {
                if (!r.SheetKey && !r.SheetToken)
                {
                    label = "구글 열쇠가 필요합니다"; action = null;
                    note = "구글 열쇠(oauth_client.json)가 이 PC에 아직 없습니다. 클로드에게 «상벌점 구글 시트 연결해줘»라고 말씀하시면 만드는 법을 안내해 드립니다.";
                }
                else if (!r.SheetToken)
                {
                    label = "구글 시트 연결"; action = delegate { OpenRiroSettings(); };
                    note = "보안 설정창에 학교에서 허용한 시트 주소를 넣고 «구글 시트 연결»을 눌러 주세요. 시트 권한 하나만 요청합니다.";
                }
                else
                {
                    label = "시트 주소 넣기"; action = delegate { OpenRiroSettings(); };
                    note = "보안 설정창의 «구글 시트 주소/ID»에 학교에서 허용한 시트 주소를 넣고 저장해 주세요.";
                }
            }
            else
            {
                label = "등록 기능 만드는 중"; action = null;
                note = "준비물은 다 갖췄습니다. 학번 뽑기·시트 저장·리로스쿨 등록 기능이 아직 없어 여기까지입니다.";
            }
        }

        private void TakeSnapshot()
        {
            try
            {
                Directory.CreateDirectory(snapshotDir);
                if (widget != null) SavePng(widget, Path.Combine(snapshotDir, "widget.png"));
                if (work != null) SavePng(work, Path.Combine(snapshotDir, "work.png"));
                ExitCode = 0;
            }
            catch (Exception ex)
            {
                try { File.WriteAllText(Path.Combine(snapshotDir, "snapshot-error.txt"), ex.ToString()); } catch { }
                ExitCode = 1;
            }
            Shutdown();
        }
        private static void SavePng(Window w, string path)
        {
            w.UpdateLayout();
            var source = PresentationSource.FromVisual(w);
            double dx = 1, dy = 1;
            if (source != null && source.CompositionTarget != null) { dx = source.CompositionTarget.TransformToDevice.M11; dy = source.CompositionTarget.TransformToDevice.M22; }
            var bitmap = new RenderTargetBitmap((int)Math.Ceiling(w.ActualWidth * dx), (int)Math.Ceiling(w.ActualHeight * dy), 96 * dx, 96 * dy, PixelFormats.Pbgra32);
            bitmap.Render(w);
            var encoder = new PngBitmapEncoder();
            encoder.Frames.Add(BitmapFrame.Create(bitmap));
            using (var file = File.Create(path)) encoder.Save(file);
        }

        private void OpenRiroSettings(bool connectDivot = false)
        {
            if (setupProcess != null)
            {
                if (!setupProcess.HasExited)
                {
                    Info("보안 설정", connectDivot
                        ? "열린 보안 설정 창에서 디벗 연결을 진행해 주세요."
                        : "이미 열린 보안 설정 창을 사용해 주세요.");
                    return;
                }
                setupProcess.Dispose();
                setupProcess = null;
            }

            string root = KitRoot();
            string python = Path.Combine(root, ".venv", "Scripts", "pythonw.exe");
            string script = Path.Combine(root, "secure_settings.pyw");
            if (!File.Exists(python) || !File.Exists(script))
            {
                Info("보안 설정", "프로젝트 안에서 위젯을 실행하고 setup.ps1로 준비를 마쳐 주세요.");
                return;
            }

            try
            {
                var start = new ProcessStartInfo(python)
                {
                    Arguments = "\"" + script + "\" --tab riro" + (connectDivot ? " --connect-divot" : ""),
                    WorkingDirectory = root,
                    UseShellExecute = false
                };
                setupProcess = Process.Start(start);
                // 설정창을 닫으면 준비 상황을 다시 본다 (디벗 연결·기준 저장이 바로 위젯에 보이게)
                setupProcess.EnableRaisingEvents = true;
                setupProcess.Exited += delegate { app.Dispatcher.BeginInvoke(new Action(RefreshStatus)); };
            }
            catch (System.ComponentModel.Win32Exception)
            {
                Info("보안 설정", "설정창을 시작하지 못했습니다. 프로젝트의 Python 환경을 확인해 주세요.");
            }
        }

        private void OpenSettings()
        {
            if (settings != null) { settings.Activate(); return; }
            var body = new StackPanel(); body.Children.Add(Header("위젯 설정", delegate { settings.Close(); }, false));
            Section(body, "상벌점");
            body.Children.Add(Button("학교 기준·연결 설정", delegate { OpenRiroSettings(); }));
            body.Children.Add(Rule()); Section(body, "위젯");
            body.Children.Add(Text("화면 크기 · Windows 배율에 추가 적용", 11, Muted));
            var sizes = new ComboBox { ItemsSource = new[] { "기본 (100%)", "크게 (125%)", "더 크게 (150%)" }, SelectedIndex = scale < 1.25 ? 0 : scale < 1.5 ? 1 : 2, Margin = new Thickness(0, 7, 0, 7) };
            sizes.SelectionChanged += delegate
            {
                scale = 1 + sizes.SelectedIndex * 0.25; prefs.Scale = scale;
                foreach (var w in new[] { widget, work, settings }) if (w != null)
                {
                    w.Width = (w == widget ? 300 : 420) * scale;
                    ((FrameworkElement)w.Content).LayoutTransform = new ScaleTransform(scale, scale); Fit(w);
                }
                SavePreferences();
            };
            body.Children.Add(sizes);
            var topmost = new CheckBox { Content = "항상 위에 표시", IsChecked = prefs.Topmost };
            topmost.Click += delegate { prefs.Topmost = topmost.IsChecked == true; foreach (var w in new[] { widget, work, settings }) if (w != null) w.Topmost = prefs.Topmost; SavePreferences(); }; body.Children.Add(topmost);
            var autostart = new CheckBox { Content = "Windows 로그인 시 자동 시작", IsChecked = File.Exists(ShortcutPath(true)) };
            autostart.Click += delegate
            {
                try { SetShortcut(true, autostart.IsChecked == true); }
                catch { autostart.IsChecked = File.Exists(ShortcutPath(true)); Info("설정 확인", "자동 시작 설정을 변경하지 못했습니다. 폴더 접근 권한을 확인해 주세요."); }
            }; body.Children.Add(autostart);
            var shortcut = Button("바탕화면 바로가기 만들기", delegate
            {
                try { SetShortcut(false, true); Info("바로가기", "‘교무 도구 데모’ 바로가기를 만들었습니다."); }
                catch { Info("바로가기", "바로가기를 만들지 못했습니다. 폴더 접근 권한을 확인해 주세요."); }
            }); shortcut.Margin = new Thickness(0, 7, 0, 7); body.Children.Add(shortcut);
            var reset = Button("위치 초기화", delegate { widget.Left = SystemParameters.WorkArea.Right - widget.Width - 24; widget.Top = SystemParameters.WorkArea.Top + 24; Fit(widget); SavePosition(); }); body.Children.Add(reset);
            body.Children.Add(Rule());
            body.Children.Add(Button("위젯 종료", Shutdown));
            settings = Shell("위젯 설정", 420, body, true);
            settings.Closed += delegate { settings = null; };
            Place(settings); settings.Show(); Fit(settings);
        }
        private static void Section(StackPanel panel, string label)
        {
            var text = Text(label, 14, Ink); text.FontWeight = FontWeights.SemiBold; text.Margin = new Thickness(0, 0, 0, 9); panel.Children.Add(text);
        }
        private void Info(string title, string message)
        {
            var body = new StackPanel(); Window window = null;
            body.Children.Add(Header(title, delegate { window.Close(); }, false)); body.Children.Add(Text(message, 13, Muted));
            window = Shell(title, 360, body, true); Place(window); window.Show(); Fit(window);
        }
        private void Show() { widget.Show(); widget.WindowState = WindowState.Normal; Fit(widget); widget.Activate(); RefreshStatus(); }
        private void Hide() { foreach (Window child in widget.OwnedWindows.Cast<Window>().ToArray()) child.Close(); widget.Hide(); }
        private void Shutdown()
        {
            if (closing) return; closing = true;
            if (snapshotDir == null && widget != null) SavePosition();
            if (guard != null) guard.Stop();
            if (setupProcess != null) setupProcess.Dispose();
            if (tray != null) { tray.Visible = false; tray.Dispose(); }
            if (showEvent != null) showEvent.Dispose();
            app.Shutdown();
        }
        private Preferences LoadPreferences()
        {
            try { using (var stream = File.OpenRead(statePath)) return (Preferences)new XmlSerializer(typeof(Preferences)).Deserialize(stream); }
            catch { return new Preferences(); }
        }
        private bool SavePreferences()
        {
            if (snapshotDir != null) return false;
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(statePath));
                string temp = statePath + ".tmp";
                using (var writer = File.Create(temp)) new XmlSerializer(typeof(Preferences)).Serialize(writer, prefs);
                if (File.Exists(statePath)) File.Replace(temp, statePath, null); else File.Move(temp, statePath);
                return true;
            }
            catch { return false; }
        }
        private void SavePosition() { prefs.X = widget.Left; prefs.Y = widget.Top; SavePreferences(); }
        private static string ShortcutPath(bool startup)
        {
            return Path.Combine(Environment.GetFolderPath(startup ? Environment.SpecialFolder.Startup : Environment.SpecialFolder.DesktopDirectory), "교무 도구 데모.lnk");
        }
        private void SetShortcut(bool startup, bool enabled)
        {
            string path = ShortcutPath(startup);
            if (!enabled) { if (File.Exists(path)) File.Delete(path); return; }
            object shell = null, link = null;
            try
            {
                var type = Type.GetTypeFromProgID("WScript.Shell"); shell = Activator.CreateInstance(type);
                link = type.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { path });
                var linkType = link.GetType();
                linkType.InvokeMember("TargetPath", BindingFlags.SetProperty, null, link, new object[] { Assembly.GetExecutingAssembly().Location });
                linkType.InvokeMember("Arguments", BindingFlags.SetProperty, null, link, new object[] { "--widget" });   // 이 바로가기는 위젯 설정에서 만든 것 — 위젯으로 뜬다
                linkType.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, link, new object[] { AppDomain.CurrentDomain.BaseDirectory });
                linkType.InvokeMember("Description", BindingFlags.SetProperty, null, link, new object[] { "교무 도구" });
                linkType.InvokeMember("Save", BindingFlags.InvokeMethod, null, link, null);
            }
            finally { if (link != null) Marshal.FinalReleaseComObject(link); if (shell != null) Marshal.FinalReleaseComObject(shell); }
        }
        private void Place(Window window)
        {
            if (widget == null)
            {
                // 아이콘 방식: 위젯이 없으니 화면 가운데 (안내 창은 상벌점 창 가운데)
                window.WindowStartupLocation = window.Owner != null ? WindowStartupLocation.CenterOwner : WindowStartupLocation.CenterScreen;
                return;
            }
            var source = PresentationSource.FromVisual(widget);
            var transform = source != null && source.CompositionTarget != null ? source.CompositionTarget.TransformFromDevice : Matrix.Identity;
            var area = Forms.Screen.FromHandle(new WindowInteropHelper(widget).Handle).WorkingArea;
            var origin = transform.Transform(new Point(area.X, area.Y));
            var size = transform.Transform(new Vector(area.Width, area.Height));
            if (widget.Left + widget.ActualWidth + window.Width + 8 <= origin.X + size.X)
                window.Left = widget.Left + widget.ActualWidth + 8;
            else if (widget.Left - window.Width - 8 >= origin.X)
                window.Left = widget.Left - window.Width - 8;
            else window.Left = widget.Left;
            window.Top = widget.Top;
        }
        private void Fit(Window window)
        {
            if (!window.IsLoaded) return;
            var source = PresentationSource.FromVisual(window);
            var transform = source != null && source.CompositionTarget != null ? source.CompositionTarget.TransformFromDevice : Matrix.Identity;
            var bounds = Forms.Screen.FromHandle(new WindowInteropHelper(window).Handle).WorkingArea;
            var origin = transform.Transform(new Point(bounds.X, bounds.Y));
            var size = transform.Transform(new Vector(bounds.Width, bounds.Height));
            var border = window.Content as Border;
            if (border != null && border.Child is ScrollViewer) ((ScrollViewer)border.Child).MaxHeight = Math.Max(100, (size.Y - 60) / scale);
            window.UpdateLayout();
            window.Left = Math.Max(origin.X, Math.Min(window.Left, origin.X + size.X - window.ActualWidth));
            window.Top = Math.Max(origin.Y, Math.Min(window.Top, origin.Y + size.Y - window.ActualHeight));
        }
    }
}

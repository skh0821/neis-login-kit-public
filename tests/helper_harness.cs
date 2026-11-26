// 시험용 — EVPN 도우미(helper.cs)의 «암호 칸 고르기»·«AXGATE 창 확인»·«인증서 줄 선택» 을 가짜 창으로 돌려 본다.
// helper.cs 와 함께 컴파일한다 (/main:HelperHarness). 금고·설정·AXGATE 는 건드리지 않는다.
using System;
using System.Reflection;
using System.Runtime.InteropServices;

static class HelperHarness
{
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr CreateWindowEx(int ex, string cls, string name, uint style, int x, int y, int w, int h,
                                        IntPtr parent, IntPtr menu, IntPtr inst, IntPtr param);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
    const uint WS_CHILD = 0x40000000, WS_POPUP = 0x80000000, ES_PASSWORD = 0x20, LVS_REPORT = 0x0001;
    const uint LVM_INSERTITEMW = 0x104D, LVM_GETNEXTITEM = 0x100C;
    const int LVNI_SELECTED = 2;

    static IntPtr Dlg(params uint[] editStyles)
    {
        IntPtr d = CreateWindowEx(0, "#32770", "fake", WS_POPUP, 0, 0, 10, 10, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero);
        foreach (var s in editStyles)
            CreateWindowEx(0, "EDIT", "", WS_CHILD | s, 0, 0, 5, 5, d, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero);
        return d;
    }

    static bool TestSelectRowDirect(MethodInfo selectDirect)
    {
        IntPtr d = Dlg();
        IntPtr lv = CreateWindowEx(0, "SysListView32", "", WS_CHILD | LVS_REPORT, 0, 0, 100, 100, d, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero);
        if (lv == IntPtr.Zero) return false;
        byte[] item = new byte[88];
        var pin = GCHandle.Alloc(item, GCHandleType.Pinned);
        try
        {
            for (int i = 0; i < 3; i++)
            {
                BitConverter.GetBytes(i).CopyTo(item, 4);
                SendMessage(lv, LVM_INSERTITEMW, IntPtr.Zero, pin.AddrOfPinnedObject());
            }
        }
        finally { pin.Free(); }
        bool ok = (bool)selectDirect.Invoke(null, new object[] { lv, 2 });
        int sel = (int)SendMessage(lv, LVM_GETNEXTITEM, (IntPtr)(-1), (IntPtr)LVNI_SELECTED);
        return ok && sel == 2;
    }

    static int Main()
    {
        var t = Type.GetType("Helper");
        var pick = t.GetMethod("PickPasswordEdit", BindingFlags.NonPublic | BindingFlags.Static);
        var owned = t.GetMethod("OwnedByAxgate", BindingFlags.NonPublic | BindingFlags.Static);
        var selDirect = t.GetMethod("SelectRowDirect", BindingFlags.NonPublic | BindingFlags.Static);
        Func<IntPtr, string> run = d =>
        {
            var a = new object[] { d, 0, 0 };
            var r = (IntPtr)pick.Invoke(null, a);
            return (r != IntPtr.Zero ? "picked" : "none") + " edits=" + a[1] + " pw=" + a[2];
        };
        Console.WriteLine("one_plain=" + run(Dlg(0)));
        Console.WriteLine("two_plain=" + run(Dlg(0, 0)));
        Console.WriteLine("plain_and_pw=" + run(Dlg(0, ES_PASSWORD)));
        Console.WriteLine("two_pw=" + run(Dlg(ES_PASSWORD, ES_PASSWORD)));
        Console.WriteLine("owned_fake=" + owned.Invoke(null, new object[] { Dlg(0) }));
        Console.WriteLine("select_direct=" + TestSelectRowDirect(selDirect));
        return 0;
    }
}

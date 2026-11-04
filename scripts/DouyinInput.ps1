# Shared native input helpers. Loading this file performs no input actions.
if (-not ('DouyinSendProbe' -as [type])) {
    Add-Type -ReferencedAssemblies Accessibility -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Accessibility;
public static class DouyinSendProbe {
    [DllImport("oleacc.dll")] static extern int AccessibleObjectFromWindow(IntPtr hwnd, uint id, ref Guid iid, [MarshalAs(UnmanagedType.Interface)] out IAccessible obj);
    [DllImport("oleacc.dll")] static extern int AccessibleChildren(IAccessible obj, int start, int count, [Out, MarshalAs(UnmanagedType.LPArray, SizeParamIndex=2)] object[] children, out int obtained);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] static extern IntPtr WindowFromPoint(POINT point);
    [DllImport("user32.dll")] static extern IntPtr GetAncestor(IntPtr hwnd, uint flags);
    [DllImport("user32.dll")] static extern short GetAsyncKeyState(int key);
    [DllImport("user32.dll", SetLastError=true)] static extern uint SendInput(uint count, INPUT[] inputs, int size);
    [StructLayout(LayoutKind.Sequential)] struct KEYBDINPUT { public ushort vk, scan; public uint flags, time; public UIntPtr extra; }
    [StructLayout(LayoutKind.Sequential)] struct POINT { public int x,y; }
    [StructLayout(LayoutKind.Sequential)] struct MOUSEINPUT { public int dx, dy; public uint data, flags, time; public UIntPtr extra; }
    [StructLayout(LayoutKind.Explicit)] struct UNION { [FieldOffset(0)] public KEYBDINPUT key; [FieldOffset(0)] public MOUSEINPUT mouse; }
    [StructLayout(LayoutKind.Sequential)] struct INPUT { public uint type; public UNION data; }
    public static IAccessible Resolve(IntPtr hwnd, string path) {
        Guid iid = new Guid("618736E0-3C3D-11CF-810C-00AA00389B71"); IAccessible obj;
        int hr = AccessibleObjectFromWindow(hwnd, 0xFFFFFFFC, ref iid, out obj);
        if(hr != 0 || obj == null) throw new Exception("MSAA root unavailable");
        string[] parts = path.Split('/');
        for(int i=1;i<parts.Length;i++) {
            int index = int.Parse(parts[i]); int count = obj.accChildCount;
            if(index<0 || index>=count) throw new Exception("MSAA path changed");
            object[] children = new object[count]; int got;
            if(AccessibleChildren(obj,0,count,children,out got)<0 || index>=got) throw new Exception("MSAA children unavailable");
            obj = children[index] as IAccessible;
            if(obj == null) throw new Exception("Unsupported simple child");
        }
        return obj;
    }
    public static void Focus(IntPtr hwnd, string path) {
        if(!SetForegroundWindow(hwnd)) throw new Exception("Could not foreground target window");
        Resolve(hwnd,path).accDoDefaultAction(0);
    }
    public static void Hover(IntPtr hwnd, string path) {
        int x,y,width,height;
        Resolve(hwnd,path).accLocation(out x,out y,out width,out height,0);
        if(width<=0 || height<=0 || !SetCursorPos(x+width/2,y+height/2))
            throw new Exception("Observed control location unavailable");
    }
    public static bool Focused(IntPtr hwnd, string path) {
        return GetForegroundWindow()==hwnd && (Convert.ToInt32(Resolve(hwnd,path).get_accState(0)) & 4) != 0;
    }
    public static void TypeText(IntPtr hwnd, string path, string text) {
        if(!Focused(hwnd,path)) throw new Exception("Composer focus not confirmed");
        foreach(int key in new int[]{16,17,18,91,92}) if(GetAsyncKeyState(key)<0) throw new Exception("Modifier key held");
        var inputs = new INPUT[text.Length*2];
        for(int i=0;i<text.Length;i++) {
            inputs[2*i].type=1; inputs[2*i].data.key.scan=text[i]; inputs[2*i].data.key.flags=4;
            inputs[2*i+1].type=1; inputs[2*i+1].data.key.scan=text[i]; inputs[2*i+1].data.key.flags=6;
        }
        if(SendInput((uint)inputs.Length,inputs,Marshal.SizeOf(typeof(INPUT))) != inputs.Length)
            throw new Exception("Input result uncertain; do not retry automatically");
    }
    public static void InvokeSend(IntPtr hwnd, string composerPath) {
        if(!Focused(hwnd,composerPath)) throw new Exception("Composer focus changed");
        foreach(int key in new int[]{16,17,18,91,92}) if(GetAsyncKeyState(key)<0) throw new Exception("Modifier key held");
        var inputs=new INPUT[2];
        inputs[0].type=1; inputs[0].data.key.vk=13;
        inputs[1].type=1; inputs[1].data.key.vk=13; inputs[1].data.key.flags=2;
        if(SendInput(2,inputs,Marshal.SizeOf(typeof(INPUT)))!=2)
            throw new Exception("Send keystroke result uncertain; do not retry automatically");
    }
    public static void InvokePrivateSend(IntPtr hwnd, string buttonPath) {
        if(GetForegroundWindow()!=hwnd) throw new Exception("Send window focus changed");
        IAccessible button=Resolve(hwnd,buttonPath);
        if(Convert.ToInt32(button.get_accRole(0))!=40 || button.get_accDefaultAction(0)!="\u70b9\u51fb" ||
           (Convert.ToInt32(button.get_accState(0)) & 1)!=0)
            throw new Exception("Private send control unavailable");
        int x,y,width,height;
        button.accLocation(out x,out y,out width,out height,0);
        if(width<=0 || height<=0) throw new Exception("Private send control unavailable");
        POINT point=new POINT {x=x+width/2,y=y+height/2};
        if(GetAncestor(WindowFromPoint(point),2)!=hwnd) throw new Exception("Private send control obscured");
        foreach(int key in new int[]{16,17,18,91,92}) if(GetAsyncKeyState(key)<0) throw new Exception("Modifier key held");
        if(!SetCursorPos(point.x,point.y)) throw new Exception("Private send control unavailable");
        // One measured button click; never fall back to Enter or retry a partial click.
        var inputs=new INPUT[2];
        inputs[0].type=0; inputs[0].data.mouse.flags=2;
        inputs[1].type=0; inputs[1].data.mouse.flags=4;
        if(SendInput(2,inputs,Marshal.SizeOf(typeof(INPUT)))!=2)
            throw new Exception("Send click result uncertain; do not retry automatically");
    }
}
'@
}

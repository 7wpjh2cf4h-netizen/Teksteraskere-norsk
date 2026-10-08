# Creates "Teksting" shortcuts (with icon) on the desktop and in the Start menu.
# Each gets the app ID "Teksting.Teksting" (same as the window in server.py), so Windows shows the
# running window and a pinned shortcut as one taskbar button, and pinning the window works.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$shell = New-Object -ComObject WScript.Shell
$q = [char]34

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class TekstingAppId {
  [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IPropertyStore {
    [PreserveSig] int GetCount(out uint count);
    [PreserveSig] int GetAt(uint i, out PropertyKey key);
    [PreserveSig] int GetValue(ref PropertyKey key, out PropVariant value);
    [PreserveSig] int SetValue(ref PropertyKey key, ref PropVariant value);
    [PreserveSig] int Commit();
  }
  [StructLayout(LayoutKind.Sequential, Pack = 4)]
  public struct PropertyKey { public Guid fmtid; public uint pid; }
  [StructLayout(LayoutKind.Explicit)]
  public struct PropVariant { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr p; }
  [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
  static extern void SHGetPropertyStoreFromParsingName(string path, IntPtr bc, int flags, ref Guid riid,
    [MarshalAs(UnmanagedType.Interface)] out IPropertyStore store);
  public static void Set(string lnk, string id) {
    Guid iid = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
    IPropertyStore store;
    SHGetPropertyStoreFromParsingName(lnk, IntPtr.Zero, 2, ref iid, out store);  // 2 = GPS_READWRITE
    PropertyKey key = new PropertyKey { fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };
    PropVariant v = new PropVariant { vt = 31, p = Marshal.StringToCoTaskMemUni(id) };  // 31 = VT_LPWSTR
    try {
      Marshal.ThrowExceptionForHR(store.SetValue(ref key, ref v));
      Marshal.ThrowExceptionForHR(store.Commit());
    } finally {
      Marshal.FreeCoTaskMem(v.p);
      Marshal.ReleaseComObject(store);
    }
  }
}
'@

foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
  if (-not $dir) { continue }
  $path = Join-Path $dir 'Teksting.lnk'
  $lnk = $shell.CreateShortcut($path)
  $lnk.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
  $lnk.Arguments = $q + (Join-Path $here 'Teksting.vbs') + $q
  $lnk.WorkingDirectory = $here
  $lnk.IconLocation = (Join-Path $here 'static\teksting.ico') + ',0'
  $lnk.Description = 'Teksting - norske undertekster'
  $lnk.Save()
  try { [TekstingAppId]::Set($path, 'Teksting.Teksting') } catch { Write-Host "App ID not set on ${path}: $_" }
}

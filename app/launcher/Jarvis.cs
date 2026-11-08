// Jarvis.exe: starts the Jarvis HUD (pythonw jarvis_hud.py) from the folder this exe lives in.
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("Jarvis")]
[assembly: System.Reflection.AssemblyProduct("Jarvis")]
[assembly: System.Reflection.AssemblyVersion("1.0.0.0")]

static class Program
{
    [STAThread]
    static void Main(string[] args)
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        string py = Path.Combine(dir, ".venv", "Scripts", "pythonw.exe");
        string script = Path.Combine(dir, "jarvis_hud.py");
        if (!File.Exists(py) || !File.Exists(script))
        {
            MessageBox.Show("Jarvis files are missing in:\n" + dir, "Jarvis", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }
        var psi = new ProcessStartInfo(py, "\"" + script + "\" " + string.Join(" ", args));
        psi.WorkingDirectory = dir;
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        Process.Start(psi);
    }
}

using System;
using System.IO;
using System.Diagnostics;
using System.Windows.Forms;

class KaraMorphLauncher {
    [STAThread]
    static int Main(string[] args) {
        try {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string python = Path.Combine(root, ".runtime", "ui", "python.exe");
            if (!File.Exists(python)) throw new Exception("KaraMorph runtime is missing. Extract the complete ZIP.");
            var start = new ProcessStartInfo(python);
            start.Arguments = "\"" + Path.Combine(root, "run_app.py") + "\"";
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            var process = Process.Start(start);
            process.WaitForExit();
            return process.ExitCode;
        } catch (Exception error) {
            MessageBox.Show(error.Message, "KaraMorph", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}

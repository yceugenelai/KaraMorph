// Feasibility launcher: no Python/Git required before first use.
using System;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Text;
using System.Security.Cryptography;
using System.Diagnostics;
using System.ComponentModel;
using System.Windows.Forms;
using System.Collections.Generic;
using System.Web.Script.Serialization;

class BootstrapPoc : Form {
    const string UvUrl = "https://github.com/astral-sh/uv/releases/download/0.12.21/uv-x86_64-pc-windows-msvc.zip";
    const string UvHash = "5d223efa0bf00208c3853246af09420419dfbd352536aa6bb8163d6170e23890";
    readonly string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
    readonly Label status = new Label { Dock = DockStyle.Top, Height = 70 };
    readonly ProgressBar progress = new ProgressBar { Dock = DockStyle.Top, Style = ProgressBarStyle.Marquee };
    readonly Button start = new Button { Dock = DockStyle.Bottom, Text = "Prepare and launch" };
    readonly Button cancel = new Button { Dock = DockStyle.Bottom, Text = "Cancel", Enabled = false };
    readonly BackgroundWorker worker = new BackgroundWorker { WorkerReportsProgress = true };
    readonly ComboBox languages = new ComboBox { Dock=DockStyle.Top, DropDownStyle=ComboBoxStyle.DropDownList };
    readonly CheckBox separation = new CheckBox { Dock=DockStyle.Top, Height=32, Checked=true };
    readonly CheckBox styling = new CheckBox { Dock=DockStyle.Top, Height=32, Checked=true };
    readonly Button launch = new Button { Dock=DockStyle.Bottom, Height=32 };
    readonly TextBox outcomes = new TextBox { Dock=DockStyle.Top, Height=100, Multiline=true, ReadOnly=true, ScrollBars=ScrollBars.Vertical };
    readonly Label explanation = new Label { Dock=DockStyle.Top, Height=95 };
    readonly JavaScriptSerializer json = new JavaScriptSerializer();
    string selectedLanguage="en";
    bool noLaunch;
    bool wantSeparation, wantStyling;
    volatile bool modelProgress;
    DateTime lastProgress=DateTime.MinValue;
    readonly Dictionary<string,string[]> words = new Dictionary<string,string[]> {
        {"title",new[]{"KaraMorph — Setup","KaraMorph — 啟動設定","KaraMorph — 初期設定"}},
        {"intro",new[]{"Prepare components and models in this folder. Internet is required. Both AI features may need about 35–40 GiB including download caches. Key/speed changes also require the ACE-Step runtime.","環境與模型都會放在此目錄，需要網路。兩項 AI 功能含下載快取約需 35–40 GiB。調 key／速度也需要 ACE-Step 執行環境。","環境とモデルをこのフォルダーに準備します。インターネットが必要です。両方の AI 機能はキャッシュ込みで約35～40 GiB必要です。キー・速度の変更にも ACE-Step 実行環境が必要です。"}},
        {"separation",new[]{"Install vocal separation + models","安裝人聲分離與模型","ボーカル分離とモデルをインストール"}},
        {"styling",new[]{"Install ACE-Step + models","安裝 ACE-Step 與模型","ACE-Step とモデルをインストール"}},
        {"start",new[]{"Prepare / retry","準備／重試","準備／再試行"}},
        {"cancel",new[]{"Cancel","取消","キャンセル"}},
        {"launch",new[]{"Start using KaraMorph","開始使用 KaraMorph","KaraMorph を使う"}},
        {"idle",new[]{"Basic UI is required. Optional features are selected below.","基本介面為必要元件，下方可選擇功能。","基本画面は必須です。追加機能を選択してください。"}},
        {"preparing",new[]{"Preparing: ","正在準備：","準備中："}},
        {"models",new[]{"Checking / downloading models: ","正在檢查／下載模型：","モデルを確認／ダウンロード："}},
        {"done",new[]{"Preparation complete.","準備完成。","準備が完了しました。"}},
        {"failed",new[]{"Some components failed. Retry or start with the available features. Details: .app_data/logs/bootstrap.log","部分元件失敗，可重試或使用已完成的功能。詳情：.app_data/logs/bootstrap.log","一部の準備に失敗しました。再試行するか、利用可能な機能で開始できます。詳細：.app_data/logs/bootstrap.log"}},
        {"cancelled",new[]{"Cancelled. Completed components are preserved.","已取消，完成的元件會保留。","キャンセルしました。準備済みの環境は保持されます。"}},
        {"saving",new[]{"Saving settings","儲存設定","設定を保存"}}
    };
    void Outcome(string component, Exception error=null) {
        string label=component=="ui"?"UI":T(component);
        string result=error==null?T("done"):T("failed")+Environment.NewLine+error.Message;
        if(worker.IsBusy) worker.ReportProgress(1,label+": "+result);
    }
    string T(string key) { return words[key][selectedLanguage=="zh_TW"?1:selectedLanguage=="ja"?2:0]; }
    Dictionary<string,object> ReadJson(string relative) {
        if(!File.Exists(P(relative))) return new Dictionary<string,object>();
        return json.Deserialize<Dictionary<string,object>>(File.ReadAllText(P(relative),Encoding.UTF8));
    }
    void WriteJson(string relative, Dictionary<string,object> value) {
        string path=P(relative), temporary=path+".tmp";
        Directory.CreateDirectory(Path.GetDirectoryName(path));
        File.WriteAllText(temporary,json.Serialize(value),new UTF8Encoding(false));
        if(File.Exists(path)) File.Replace(temporary,path,null); else File.Move(temporary,path);
    }
    void SaveLanguage() {
        var settings=ReadJson(".app_data/settings.json"); settings["ui_language"]=selectedLanguage;
        if(!settings.ContainsKey("workspace")) settings["workspace"]=P("outputs");
        if(!settings.ContainsKey("source_dir")) settings["source_dir"]="";
        WriteJson(".app_data/settings.json",settings);
    }
    void SaveSelection(bool partial) {
        WriteJson(".app_data/bootstrap-selection.json",new Dictionary<string,object> {
            {"separation",separation.Checked},{"styling",styling.Checked},{"completed",true},{"allow_partial",partial}});
    }
    void Localize() {
        Text=T("title"); explanation.Text=T("intro"); separation.Text=T("separation"); styling.Text=T("styling");
        start.Text=T("start"); cancel.Text=T("cancel"); launch.Text=T("launch"); status.Text=T("idle");
    }
    void Stage(string key,string detail) { if(worker.IsBusy) worker.ReportProgress(0,T(key)+detail); }
    volatile bool cancelled;
    bool busy;
    static readonly object logGuard = new object();

    string P(string value) { return Path.Combine(root, value.Replace('/', Path.DirectorySeparatorChar)); }
    string Hash(string path) {
        using (var stream = File.OpenRead(path)) using (var sha = SHA256.Create())
            return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
    }
    void CheckCancel() { if (cancelled) throw new OperationCanceledException(); }
    void Report(string text) {
        if(modelProgress && text.StartsWith("{")) {
            try {
                var item=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(text);
                if(DateTime.UtcNow-lastProgress>TimeSpan.FromMilliseconds(500)) {
                    string detail=item.ContainsKey("file") ? Convert.ToString(item["file"]) : "";
                    if(item.ContainsKey("done") && item.ContainsKey("total") && Convert.ToDouble(item["total"])>0)
                        detail+=" "+(100*Convert.ToDouble(item["done"])/Convert.ToDouble(item["total"])).ToString("F0")+"%";
                    Stage("models",detail);lastProgress=DateTime.UtcNow;
                }
            } catch {}
        }
        lock(logGuard) File.AppendAllText(P(".app_data/logs/bootstrap.log"), DateTime.UtcNow.ToString("o") + " " + text + Environment.NewLine, Encoding.UTF8);

    }
    ProcessStartInfo ProcessInfo(string program, string arguments) {
        var info = new ProcessStartInfo(program, arguments) { WorkingDirectory = root, UseShellExecute = false,
            CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true, StandardOutputEncoding=Encoding.UTF8, StandardErrorEncoding=Encoding.UTF8 };
        foreach (var key in new[] {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "UV_PYTHON", "UV_INDEX", "UV_INDEX_URL", "UV_EXTRA_INDEX_URL", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL"}) info.EnvironmentVariables.Remove(key);
        info.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
        info.EnvironmentVariables["PYTHONUTF8"] = "1";
        info.EnvironmentVariables["UV_CACHE_DIR"] = P(".app_data/cache/uv");
        info.EnvironmentVariables["UV_CREDENTIALS_DIR"] = P(".app_data/cache/uv-credentials");
        info.EnvironmentVariables["UV_PYTHON_INSTALL_DIR"] = P(".runtime/python");
        info.EnvironmentVariables["UV_PYTHON_BIN_DIR"] = P(".runtime/bin");
        info.EnvironmentVariables["UV_LINK_MODE"] = "copy";
        info.EnvironmentVariables["PIP_CACHE_DIR"] = P(".app_data/cache/pip");
        info.EnvironmentVariables["TEMP"] = info.EnvironmentVariables["TMP"] = P(".app_data/cache/tmp");
        // Tests deliberately remove developer executables from PATH.
        info.EnvironmentVariables["PATH"] = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "System32");
        return info;
    }
    int Run(string program, string arguments, bool allowFailure=false) {
        CheckCancel(); Report(Path.GetFileName(program) + " " + arguments);
        string lastError="";
        using(var process = new Process { StartInfo = ProcessInfo(program, arguments) }) {
            process.OutputDataReceived += (s,e) => { if(e.Data != null) Report(e.Data); };
            process.ErrorDataReceived += (s,e) => { if(e.Data != null) {Report(e.Data); if(e.Data.Length>0) lastError=e.Data;} };
            process.Start(); process.BeginOutputReadLine(); process.BeginErrorReadLine();
            while(!process.WaitForExit(200)) if(cancelled) { process.Kill(); process.WaitForExit(); throw new OperationCanceledException(); }
            process.WaitForExit();
            if(process.ExitCode != 0 && !allowFailure) throw new Exception(Path.GetFileName(program) + " failed (" + process.ExitCode + "): " + lastError + ". See .app_data/logs/bootstrap.log");
            return process.ExitCode;
        }
    }
    static string Q(string value) { return "\"" + value + "\""; }
    void DownloadUv() {
        string archive = P(".app_data/cache/uv-release.zip"), partial = archive + ".part";
        if (!File.Exists(archive) || Hash(archive) != UvHash) {
            Report("Downloading uv 0.12.21 (17.2 MiB)...");
            ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
            var request = (HttpWebRequest)WebRequest.Create(UvUrl);
            request.UserAgent = "KaraMorph-bootstrap-poc"; request.Timeout = 30000; request.ReadWriteTimeout = 30000;
            using(var response = request.GetResponse()) using(var input = response.GetResponseStream())
            using(var output = File.Create(partial)) {
                var buffer = new byte[1024*1024]; long total = 0; int count;
                while((count = input.Read(buffer, 0, buffer.Length)) > 0) {
                    CheckCancel(); output.Write(buffer,0,count); total += count;
                    Report("uv download: " + (total / 1048576.0).ToString("F1") + " MiB");
                }
            }
            if(Hash(partial) != UvHash) { File.Delete(partial); throw new Exception("uv archive SHA256 mismatch"); }
            if(File.Exists(archive)) File.Delete(archive);
            File.Move(partial, archive);
        }
        Directory.CreateDirectory(P(".tools/bin"));
        using(var zip = ZipFile.OpenRead(archive)) {
            var entry = zip.GetEntry("uv.exe");
            if(entry == null) throw new Exception("uv.exe missing in verified archive");
            entry.ExtractToFile(P(".tools/bin/uv.exe.part"), true);
        }
        if(File.Exists(P(".tools/bin/uv.exe"))) File.Delete(P(".tools/bin/uv.exe"));
        File.Move(P(".tools/bin/uv.exe.part"), P(".tools/bin/uv.exe"));
        File.WriteAllText(P(".tools/bin/uv.exe.sha256"), Hash(P(".tools/bin/uv.exe")));
    }
    void CopyBase(string source, string target) {
        CheckCancel(); Directory.CreateDirectory(target);
        foreach(var file in Directory.GetFiles(source)) {
            if(Path.GetExtension(file) != ".pyc") File.Copy(file, Path.Combine(target,Path.GetFileName(file)),true);
        }
        foreach(var directory in Directory.GetDirectories(source)) {
            string name = Path.GetFileName(directory);
            if(Array.IndexOf(new[]{"site-packages","__pycache__","tests","test","Scripts","include","libs","tcl"},name) < 0)
                CopyBase(directory,Path.Combine(target,name));
        }
    }
    void Prepare(string component) {
        Stage("preparing",component);
        Directory.CreateDirectory(P(".app_data/logs")); Directory.CreateDirectory(P(".app_data/cache/tmp"));
        using(var exclusive = new FileStream(P(".app_data/bootstrap.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None)) {
            if(RuntimeReady(component)) return;
            string uv = P(".tools/bin/uv.exe"), stamp = uv + ".sha256";
            if(!File.Exists(uv) || !File.Exists(stamp) || Hash(uv) != File.ReadAllText(stamp).Trim()) DownloadUv();
            Run(uv,"--version");
            string version = component == "styling" ? "3.11.16" : "3.10.21";
            string managed = P(".runtime/python/cpython-" + version + "-windows-x86_64-none");
            Run(uv,"--no-config python install cpython-" + version + "-windows-x86_64-none --no-registry --no-bin");
            string folder = P(".runtime/" + component), python = Path.Combine(folder,"python.exe");
            if(!File.Exists(Path.Combine(folder,".python-copied"))) {
                // A copied standalone interpreter has no absolute venv base path.
                Report("Preparing portable Python " + version);
                CopyBase(managed, folder);
                File.WriteAllText(Path.Combine(folder,".python-copied"),version);
            }
            string requirements = P("scripts/requirements-" + component + "-windows.lock.txt");
            Run(python,"-c " + Q("import sys,pathlib; assert pathlib.Path(sys.prefix).resolve()==pathlib.Path(sys.argv[1]).resolve()") + " " + Q(folder));
            // The downloaded managed base stays untouched. This private clone
            // belongs to KaraMorph, not uv's managed Python installation.
            string managedMarker = Path.Combine(folder,"Lib","EXTERNALLY-MANAGED");
            if(File.Exists(managedMarker)) File.Delete(managedMarker);
            string index = component == "ui" ? "" : " --extra-index-url https://download.pytorch.org/whl/" + (component == "styling" ? "cu128" : "cu124") + " --index-strategy unsafe-best-match";
            Run(uv,"--no-config pip sync --system --python " + Q(python) + " " + Q(requirements) + " --only-binary :all:" + index);
            if(component == "ui") Run(python, Q(P("scripts/bootstrap_smoke.py")));
            else Run(python,"-c " + Q("import torch,soundfile,imageio_ffmpeg,subprocess; from runtime_config import bootstrap_worker; bootstrap_worker(); subprocess.run(['ffmpeg','-version'],check=True,stdout=subprocess.DEVNULL); print('AI runtime and FFmpeg imports passed')"));
            File.WriteAllText(P(".app_data/bootstrap-" + component + ".ready"), Hash(requirements));
            Report("Ready: " + component);
        }
    }
    bool RuntimeReady(string component) {
        string stamp=P(".app_data/bootstrap-"+component+".ready");
        return File.Exists(P(".runtime/"+component+"/python.exe")) && File.Exists(stamp) &&
            File.ReadAllText(stamp).Trim()==Hash(P("scripts/requirements-"+component+"-windows.lock.txt"));
    }
    bool Ready() { return RuntimeReady("ui"); }
    bool ModelsReady(string group) {
        return Run(P(".runtime/ui/python.exe"),Q(P("scripts/bootstrap_models.py"))+" check --group "+group,true)==0;
    }
    void PrepareModels(string group) {
        Stage("models",group);
        modelProgress=true;
        try {
            using(var exclusive=new FileStream(P(".app_data/bootstrap.lock"),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None))
                Run(P(".runtime/ui/python.exe"),Q(P("scripts/bootstrap_models.py"))+" prepare --group "+group);
        } finally {modelProgress=false;}
    }
    bool Completed() {
        if(!Ready()) return false;
        var selection=ReadJson(".app_data/bootstrap-selection.json");
        if(!selection.ContainsKey("completed") || !(bool)selection["completed"]) return false;
        if(selection.ContainsKey("allow_partial") && (bool)selection["allow_partial"]) return true;
        foreach(string group in new[]{"separation","styling"})
            if(selection.ContainsKey(group) && (bool)selection[group] && (!RuntimeReady(group)||!ModelsReady(group))) return false;
        return true;
    }
    void Launch() {
        Directory.CreateDirectory(P(".app_data/logs"));
        Run(P(".runtime/ui/python.exe"),Q(P("run_app.py")));
    }
    BootstrapPoc() {
        Icon=System.Drawing.Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        Directory.CreateDirectory(P(".app_data/logs")); Directory.CreateDirectory(P(".app_data/cache/tmp"));
        var settings=ReadJson(".app_data/settings.json");
        if(settings.ContainsKey("ui_language")) selectedLanguage=(string)settings["ui_language"];
        if(selectedLanguage!="zh_TW" && selectedLanguage!="ja") selectedLanguage="en";
        var selection=ReadJson(".app_data/bootstrap-selection.json");
        if(selection.ContainsKey("separation")) separation.Checked=(bool)selection["separation"];
        if(selection.ContainsKey("styling")) styling.Checked=(bool)selection["styling"];
        Width=680; Height=540; MinimumSize=new System.Drawing.Size(680,540);
        start.Height=32; cancel.Height=32; launch.Enabled=Ready();
        languages.Items.AddRange(new object[]{"English","繁體中文","日本語"});
        languages.SelectedIndex=selectedLanguage=="zh_TW"?1:selectedLanguage=="ja"?2:0;
        Controls.Add(outcomes); Controls.Add(styling); Controls.Add(separation); Controls.Add(explanation); Controls.Add(languages);
        Controls.Add(progress); Controls.Add(status); Controls.Add(cancel); Controls.Add(start); Controls.Add(launch);
        Localize();
        languages.SelectedIndexChanged+=(s,e)=>{ selectedLanguage=new[]{"en","zh_TW","ja"}[languages.SelectedIndex]; Localize(); };
        worker.DoWork+=(s,e)=>{
            bool failed=false;
            SaveLanguage();
            WriteJson(".app_data/bootstrap-selection.json",new Dictionary<string,object>{{"separation",wantSeparation},{"styling",wantStyling},{"completed",false}});
            try { Prepare("ui"); Outcome("ui"); } catch(OperationCanceledException){throw;} catch(Exception error){Report(error.ToString());Outcome("ui",error);failed=true;}
            if(!Ready()) { e.Result=true; return; }
            foreach(string group in new[]{"separation","styling"}) {
                bool selected=group=="separation"?wantSeparation:wantStyling;
                if(!selected) continue;
                try { Prepare(group); PrepareModels(group); Outcome(group); }
                catch(OperationCanceledException){throw;}
                catch(Exception error){Report(error.ToString());Outcome(group,error);failed=true;}
            }
            e.Result=failed;
        };
        worker.ProgressChanged+=(s,e)=>{if(e.ProgressPercentage==1) outcomes.Text+=(string)e.UserState+Environment.NewLine; else status.Text=(string)e.UserState;};
        worker.RunWorkerCompleted+=(s,e)=>{
            busy=false; cancel.Enabled=false; start.Enabled=true; languages.Enabled=true;
            separation.Enabled=true;styling.Enabled=true;launch.Enabled=Ready();
            if(e.Error is OperationCanceledException) status.Text=T("cancelled");
            else if(e.Error!=null || (bool)e.Result) status.Text=T("failed");
            else status.Text=T("done");
        };
        start.Click+=(s,e)=>{
            outcomes.Text="";wantSeparation=separation.Checked;wantStyling=styling.Checked;
            cancelled=false;busy=true;start.Enabled=false;launch.Enabled=false;cancel.Enabled=true;
            languages.Enabled=false;separation.Enabled=false;styling.Enabled=false;worker.RunWorkerAsync();
        };
        launch.Click+=(s,e)=>{
            cancelled=false;
            try {
                SaveLanguage();
                bool partial=false;
                foreach(string group in new[]{"separation","styling"})
                    if((group=="separation"?separation.Checked:styling.Checked) && (!RuntimeReady(group)||!ModelsReady(group))) partial=true;
                SaveSelection(partial);
                if(!noLaunch) {Hide();Launch();} Close();
            } catch(Exception error){MessageBox.Show(error.Message,Text,MessageBoxButtons.OK,MessageBoxIcon.Error);}
        };
        cancel.Click+=(s,e)=>{cancelled=true;status.Text=T("cancelled");};
        FormClosing+=(s,e)=>{if(busy){cancelled=true;e.Cancel=true;status.Text=T("cancelled");}};
    }
    [STAThread] static int Main(string[] args) {
        Application.EnableVisualStyles();
        BootstrapPoc app=null;
        try {
            app=new BootstrapPoc();
            if(args.Length>0 && args[0].StartsWith("--prepare-")) {
                string component=args[0].Substring(10);
                if(Array.IndexOf(new[]{"ui","separation","styling"},component)<0) throw new ArgumentException("Unknown component");
                app.Prepare(component); return 0;
            }
            if(args.Length>0 && args[0]!="--setup") throw new ArgumentException("Unknown option");
            app.noLaunch=Array.IndexOf(args,"--no-launch")>=0;
            if(args.Length==0 && app.Completed()) app.Launch(); else Application.Run(app);
            return 0;
        } catch(Exception error) {
            try {if(app!=null) app.Report(error.ToString());} catch {}
            if(args.Length==0 || args[0]=="--setup") MessageBox.Show(error.Message,"KaraMorph",MessageBoxButtons.OK,MessageBoxIcon.Error);
            return 1;
        }
    }
}

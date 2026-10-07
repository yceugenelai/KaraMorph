using System;
using System.IO;
using System.Reflection;
using System.Diagnostics;
using System.Windows.Forms;
using System.Web.Script.Serialization;
using System.Collections.Generic;
class Test {
 [STAThread] static int Main() {
 string root=AppDomain.CurrentDomain.BaseDirectory;
 string path=Path.Combine(root,".app_data/settings.json"), selection=Path.Combine(root,".app_data/bootstrap-selection.json");
 byte[] backup=File.Exists(path)?File.ReadAllBytes(path):null;
 byte[] saved=File.Exists(selection)?File.ReadAllBytes(selection):null;
 var serializer=new JavaScriptSerializer();
 var type=Assembly.LoadFrom(Path.Combine(root,"KaraMorph.exe")).GetType("BootstrapPoc");
 var flags=BindingFlags.Instance|BindingFlags.NonPublic;
 try {
  if(File.Exists(path)) File.Delete(path);
  if(File.Exists(selection)) File.Delete(selection);
  using(var form=(Form)Activator.CreateInstance(type,true)) {
   if((string)type.GetField("selectedLanguage",flags).GetValue(form)!="en") throw new Exception("Default language");
   foreach(string feature in new[]{"separation","styling"})
    if(!((CheckBox)type.GetField(feature,flags).GetValue(form)).Checked) throw new Exception("Default feature");
   var picker=(ComboBox)type.GetField("languages",flags).GetValue(form);
   foreach(int index in new[]{0,1,2}) {
    picker.SelectedIndex=index;
    type.GetMethod("SaveLanguage",flags).Invoke(form,null);
    var values=serializer.Deserialize<Dictionary<string,object>>(File.ReadAllText(path));
    if((string)values["ui_language"]!=new[]{"en","zh_TW","ja"}[index] || !values.ContainsKey("workspace")) throw new Exception("Language/default persistence");
    var info=new ProcessStartInfo(Path.Combine(root,".runtime/ui/python.exe"),"\""+Path.Combine(root,"scripts/bootstrap_smoke.py")+"\"") {UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true};
    info.EnvironmentVariables["PYTHONNOUSERSITE"]="1";
    info.EnvironmentVariables["PYTHONUTF8"]="1";
    info.EnvironmentVariables.Remove("PYTHONHOME");info.EnvironmentVariables.Remove("PYTHONPATH");
    using(var process=Process.Start(info)) {
     string output=process.StandardOutput.ReadToEnd(), errors=process.StandardError.ReadToEnd();process.WaitForExit();
     if(process.ExitCode!=0) throw new Exception("Selected language main UI: "+output+errors);
    }
   }
   ((CheckBox)type.GetField("separation",flags).GetValue(form)).Checked=false;
   ((CheckBox)type.GetField("styling",flags).GetValue(form)).Checked=false;
   type.GetMethod("SaveSelection",flags).Invoke(form,new object[]{false});
  }
  using(var form=(Form)Activator.CreateInstance(type,true)) {
   if((string)type.GetField("selectedLanguage",flags).GetValue(form)!="ja") throw new Exception("Saved language");
   if(((CheckBox)type.GetField("styling",flags).GetValue(form)).Checked) throw new Exception("Unchecked persistence");
   if(!(bool)type.GetMethod("Completed",flags).Invoke(form,null)) throw new Exception("UI-only launch readiness");
  }
  using(var form=(Form)Activator.CreateInstance(type,true)) {
   ((CheckBox)type.GetField("styling",flags).GetValue(form)).Checked=true;
   type.GetMethod("SaveSelection",flags).Invoke(form,new object[]{false});
   if((bool)type.GetMethod("Completed",flags).Invoke(form,null)) throw new Exception("Missing selected feature accepted");
   type.GetMethod("SaveSelection",flags).Invoke(form,new object[]{true});
   if(!(bool)type.GetMethod("Completed",flags).Invoke(form,null)) throw new Exception("Partial continuation rejected");
   type.GetField("cancelled",flags).SetValue(form,true);
   try {type.GetMethod("CheckCancel",flags).Invoke(form,null);throw new Exception("Cancellation ignored");}
   catch(TargetInvocationException error){if(!(error.InnerException is OperationCanceledException)) throw;}
  }
  var settings=serializer.Deserialize<Dictionary<string,object>>(File.ReadAllText(path));settings["custom-test"]=123;
  File.WriteAllText(path,serializer.Serialize(settings));
  using(var form=(Form)Activator.CreateInstance(type,true)) type.GetMethod("SaveLanguage",flags).Invoke(form,null);
  if(!File.ReadAllText(path).Contains("custom-test")) throw new Exception("Settings overwritten");
  File.WriteAllText(Path.Combine(root,".app_data/logs/bootstrap-acceptance.txt"),"PASS: native three-language picker, English default, defaults, saved language, skipped features, UI-only launch, partial continuation, cancellation, settings preservation and each selected language passed to main UI.");
  return 0;
 } catch(Exception error) {File.WriteAllText(Path.Combine(root,".app_data/logs/bootstrap-acceptance.txt"),error.ToString());return 1;}
 finally {
  if(backup==null) File.Delete(path);else File.WriteAllBytes(path,backup);
  if(saved==null) File.Delete(selection);else File.WriteAllBytes(selection,saved);
 }
 }
}
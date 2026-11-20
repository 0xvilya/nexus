using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32;

static class SetupProgram {
 public const string Product="rainforge-nexus-native-v1";
 public static Dictionary<string,string> Options=new Dictionary<string,string>();
 public static readonly JavaScriptSerializer Json=new JavaScriptSerializer{MaxJsonLength=8*1024*1024};
 public static string DefaultRoot=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Programs","RainForgeNexus");
 static string Get(string k,string fallback){return Options.ContainsKey(k)?Options[k]:fallback;}
 [STAThread] static void Main(string[] args){
  Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
  try{
   for(int i=0;i<args.Length;i+=2){if(i+1>=args.Length)throw new Exception("Missing option value");Options[args[i]]=args[i+1];}
   if(Options.ContainsKey("--test-install")){string root=Install(Options["--test-install"],true);Report(new{status="PASS",operation="install",root=root});return;}
   if(Options.ContainsKey("--uninstall-root")){
    string root=Path.GetFullPath(Options["--uninstall-root"]);
    if(Path.GetFullPath(Application.ExecutablePath).StartsWith(root+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase)){
     string temp=Path.Combine(Path.GetTempPath(),"RainForgeUninstall-"+Guid.NewGuid().ToString("N")+".exe");File.Copy(Application.ExecutablePath,temp);
     Process.Start(new ProcessStartInfo(temp,"--uninstall-root "+Quote(root)+" --parent "+Process.GetCurrentProcess().Id){UseShellExecute=false,CreateNoWindow=true});return;
    }
    if(Options.ContainsKey("--parent")){try{Process.GetProcessById(int.Parse(Options["--parent"])).WaitForExit(10000);}catch(ArgumentException){}}
    if(!Options.ContainsKey("--report")&&MessageBox.Show("Удалить RainForge? Данные и модели пользователя сохранятся.","RainForge",MessageBoxButtons.YesNo)!=DialogResult.Yes)return;
    Uninstall(root);Report(new{status="PASS",operation="uninstall",root=root});return;
   }
   Application.Run(new SetupWindow());
  }catch(Exception ex){Environment.ExitCode=1;if(Options.ContainsKey("--report"))Report(new{status="FAILED",error=ex.Message});else MessageBox.Show(ex.Message,"RainForge setup error");}
 }
 static string Quote(string p){if(p.Contains("\""))throw new Exception("Invalid path");return "\""+p+"\"";}
 static void Report(object o){if(Options.ContainsKey("--report")){string p=Path.GetFullPath(Options["--report"]);Directory.CreateDirectory(Path.GetDirectoryName(p));File.WriteAllText(p,Json.Serialize(o),Encoding.UTF8);}}
 static string Safe(string root,string relative){
  if(string.IsNullOrEmpty(relative)||relative.Contains(":")||Path.IsPathRooted(relative))throw new Exception("Invalid archive path");
  string p=Path.GetFullPath(Path.Combine(root,relative.Replace('/',Path.DirectorySeparatorChar)));
  if(!p.StartsWith(Path.GetFullPath(root)+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase))throw new Exception("Archive path escaped installation directory");
  for(string check=p;check!=null&&check.Length>=Path.GetFullPath(root).Length;check=Path.GetDirectoryName(check)){if((File.Exists(check)||Directory.Exists(check))&&(File.GetAttributes(check)&FileAttributes.ReparsePoint)!=0)throw new Exception("Reparse paths are not allowed in installation files");}return p;
 }
 static void ValidateRoot(string root,bool test){
  if(Directory.Exists(root)&&(File.GetAttributes(root)&FileAttributes.ReparsePoint)!=0)throw new Exception("Installation root must not be a reparse point");
  if(test){string temp=Path.GetFullPath(Path.GetTempPath());if(!root.StartsWith(temp,StringComparison.OrdinalIgnoreCase)||!Path.GetFileName(root).StartsWith("RainForgeInstallTest-"))throw new Exception("Test target must be RainForgeInstallTest-* under the temporary directory");}
  else if(!root.Equals(Path.GetFullPath(DefaultRoot),StringComparison.OrdinalIgnoreCase))throw new Exception("Unexpected installation directory");
 }
 public static string Install(string destination,bool test){
  string root=Path.GetFullPath(destination).TrimEnd(Path.DirectorySeparatorChar);ValidateRoot(root,test);
  if(Directory.Exists(root)){
   if(!File.Exists(Path.Combine(root,"install.json")))throw new Exception("Target contains unrelated files; choose a clean installation.");
   foreach(var p in Process.GetProcessesByName("RainForge")){try{if(p.MainModule.FileName.StartsWith(root+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("Close RainForge before updating.");}catch(System.ComponentModel.Win32Exception){}}
  }
  string staging=root+".staging-"+Guid.NewGuid().ToString("N");Directory.CreateDirectory(staging);
  Dictionary<string,string> manifest;
  using(Stream payload=Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip")){
   if(payload==null)throw new Exception("Installer payload is missing");
   using(var zip=new ZipArchive(payload,ZipArchiveMode.Read)){
    var entry=zip.GetEntry("payload-manifest.json");if(entry==null)throw new Exception("Payload manifest missing");
    using(var reader=new StreamReader(entry.Open()))manifest=Json.Deserialize<Dictionary<string,string>>(reader.ReadToEnd());
    if(zip.Entries.Count!=manifest.Count+1)throw new Exception("Unexpected payload entries");
    long bytes=0;foreach(var e in zip.Entries)bytes+=e.Length;
    if(bytes>1024L*1024*1024)throw new Exception("Payload exceeds standard installer limit");
    var drive=new DriveInfo(Path.GetPathRoot(root));if(drive.AvailableFreeSpace<bytes*2+64L*1024*1024)throw new Exception("Not enough disk space");
    foreach(var e in zip.Entries){
     if(e.FullName=="payload-manifest.json")continue;
     if(!manifest.ContainsKey(e.FullName))throw new Exception("Unlisted payload file");
     string target=Safe(staging,e.FullName);Directory.CreateDirectory(Path.GetDirectoryName(target));
     using(var input=e.Open())using(var output=File.Create(target))input.CopyTo(output);
     if(Hash(target)!=manifest[e.FullName])throw new Exception("Payload integrity failed: "+e.FullName);
    }
   }
  }
  File.Copy(Application.ExecutablePath,Path.Combine(staging,"RainForgeSetup.exe"));
  File.WriteAllText(Path.Combine(staging,"install.json"),Json.Serialize(new{product=Product,test=test,files=manifest}),Encoding.UTF8);
  Directory.CreateDirectory(Path.GetDirectoryName(root));
  if(Directory.Exists(root))Directory.Move(root,root+".previous-"+DateTime.UtcNow.ToString("yyyyMMddHHmmss")+"-"+Guid.NewGuid().ToString("N").Substring(0,6));
  Directory.Move(staging,root);
  if(!test){
   string exe=Path.Combine(root,"RainForge.exe");Shortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),"RainForge NEXUS Native.lnk"),exe);
   Shortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs),"RainForge NEXUS.lnk"),exe);
   using(var key=Registry.CurrentUser.CreateSubKey("Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\RainForgeNexus")){
    key.SetValue("DisplayName","RainForge NEXUS");key.SetValue("DisplayVersion","0.1.0");key.SetValue("Publisher","RainForge");key.SetValue("InstallLocation",root);key.SetValue("UninstallString",Quote(Path.Combine(root,"RainForgeSetup.exe"))+" --uninstall-root "+Quote(root));key.SetValue("NoModify",1);key.SetValue("NoRepair",1);
   }
  }
  return root;
 }
 static string Hash(string p){using(var sha=SHA256.Create())using(var s=File.OpenRead(p))return BitConverter.ToString(sha.ComputeHash(s)).Replace("-","").ToLowerInvariant();}
 static void Shortcut(string path,string target){
  Type type=Type.GetTypeFromProgID("WScript.Shell");object shell=Activator.CreateInstance(type);object link=type.InvokeMember("CreateShortcut",BindingFlags.InvokeMethod,null,shell,new object[]{path});Type lt=link.GetType();lt.InvokeMember("TargetPath",BindingFlags.SetProperty,null,link,new object[]{target});lt.InvokeMember("WorkingDirectory",BindingFlags.SetProperty,null,link,new object[]{Path.GetDirectoryName(target)});lt.InvokeMember("Save",BindingFlags.InvokeMethod,null,link,null);
 }
 static void RemoveShortcut(string path,string root){
  if(!File.Exists(path))return;
  Type type=Type.GetTypeFromProgID("WScript.Shell");object shell=Activator.CreateInstance(type);object link=type.InvokeMember("CreateShortcut",BindingFlags.InvokeMethod,null,shell,new object[]{path});string target=(string)link.GetType().InvokeMember("TargetPath",BindingFlags.GetProperty,null,link,null);
  if(target.StartsWith(root+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase))File.Delete(path);
 }
 static void Uninstall(string root){
  string marker=Path.Combine(root,"install.json");if(!File.Exists(marker))throw new Exception("Installation marker missing");
  var info=Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(marker));if((string)info["product"]!=Product)throw new Exception("Unknown installation");bool test=(bool)info["test"];ValidateRoot(root,test);
  var files=(Dictionary<string,object>)info["files"];
  foreach(string f in files.Keys){string p=Safe(root,f);if(File.Exists(p))using(File.Open(p,FileMode.Open,FileAccess.ReadWrite,FileShare.None)){} }
  foreach(string f in files.Keys){string p=Safe(root,f);if(File.Exists(p))File.Delete(p);}
  string self=Path.Combine(root,"RainForgeSetup.exe");if(File.Exists(self))File.Delete(self);File.Delete(marker);
  var pending=new Queue<string>();var found=new List<string>();pending.Enqueue(root);while(pending.Count>0){foreach(string dir in Directory.GetDirectories(pending.Dequeue())){if((File.GetAttributes(dir)&FileAttributes.ReparsePoint)!=0)continue;found.Add(dir);pending.Enqueue(dir);}}var dirs=found.ToArray();Array.Sort(dirs,(a,b)=>b.Length.CompareTo(a.Length));
  foreach(string dir in dirs)if(Directory.GetFileSystemEntries(dir).Length==0)Directory.Delete(dir);
  if(Directory.GetFileSystemEntries(root).Length==0)Directory.Delete(root);
  if(!test){RemoveShortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),"RainForge NEXUS Native.lnk"),root);RemoveShortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs),"RainForge NEXUS.lnk"),root);Registry.CurrentUser.DeleteSubKeyTree("Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\RainForgeNexus",false);}
 }
}
sealed class SetupWindow:Form {
 readonly Label info=new Label();readonly Button install=new Button();bool completed;
 public SetupWindow(){Text="Установка RainForge NEXUS";Width=650;Height=320;StartPosition=FormStartPosition.CenterScreen;
  info.Dock=DockStyle.Fill;info.Padding=new Padding(24);info.Text="RainForge NEXUS · Windows x64\n\nПриложение, worker и llama.cpp устанавливаются для текущего пользователя.\nPython, Node.js и Ollama не требуются.\nТребуется Windows x64 с .NET Framework 4.8, WebView2 и совместимым Visual C++ Runtime.\nБазовая модель чата и локальная диктовка входят в комплект.\n\n"+SetupProgram.DefaultRoot;
  install.Dock=DockStyle.Bottom;install.Height=52;install.Text="Установить";Controls.Add(info);Controls.Add(install);install.Click+=async(s,e)=>{if(completed){Close();return;}install.Enabled=false;info.Text="Проверка и установка файлов…";try{string root=await Task.Run(()=>SetupProgram.Install(SetupProgram.DefaultRoot,false));info.Text="Установка завершена. Данные хранятся отдельно от приложения.\n\n"+root;install.Text="Закрыть";install.Enabled=true;completed=true;}catch(Exception ex){info.Text=ex.Message;install.Enabled=true;}};
 }
}

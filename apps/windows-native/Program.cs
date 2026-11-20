using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

static class Program {
 public static Dictionary<string,string> Options=new Dictionary<string,string>();
 [STAThread] static void Main(string[] args) {
  for(int i=0;i<args.Length;i+=2) { if(i+1>=args.Length) {MessageBox.Show("Missing option value");return;} Options[args[i]]=args[i+1]; }
  Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
  string data=Get("--data-dir",Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"RainForgeNexus","data"));
  string key;using(var h=SHA256.Create()){key=BitConverter.ToString(h.ComputeHash(Encoding.UTF8.GetBytes(Path.GetFullPath(data).ToLowerInvariant()))).Replace("-","");}
  bool fresh;using(var mutex=new Mutex(true,"Local\\RainForgeNexus-"+key,out fresh)) {
   if(!fresh){MessageBox.Show("RainForge is already running for this workspace.");return;}
   try {Application.Run(new Studio(Path.GetFullPath(Get("--bundle-root",AppDomain.CurrentDomain.BaseDirectory)),Path.GetFullPath(data)));}
   catch(Exception ex){MessageBox.Show(ex.Message,"RainForge startup error");}
  }
 }
 public static string Get(string key,string fallback){return Options.ContainsKey(key)?Options[key]:fallback;}
}
sealed class Studio:Form {
 readonly string bundle,data;readonly WebView2 web=new WebView2();readonly Label status=new Label();
 readonly HttpClient http=new HttpClient(new HttpClientHandler{UseProxy=false});readonly JavaScriptSerializer json=new JavaScriptSerializer();
 Process worker;IntPtr job;string address,token;bool closing;int restarts;bool smokeStarted;
 readonly HttpClient petHttp=new HttpClient(new HttpClientHandler{UseProxy=false});
 Process pet;bool petsListening,petsInitialized,lastPetEnabled;long petsSequence;string lastPetAddress;
 public Studio(string bundleRoot,string dataRoot){
  bundle=bundleRoot;data=dataRoot;Directory.CreateDirectory(data);Text=Program.Get("--pets-test","false")=="true"?"Nexus Pets — isolated acceptance":"RainForge NEXUS";Width=1440;Height=960;MinimumSize=new Size(800,600);
  web.Dock=DockStyle.Fill;status.Dock=DockStyle.Bottom;status.Height=25;status.Text="Starting local worker…";Controls.Add(web);Controls.Add(status);
  Shown+=async (s,e)=>await Start();FormClosed+=(s,e)=>Cleanup();http.Timeout=TimeSpan.FromSeconds(4);petHttp.Timeout=TimeSpan.FromSeconds(25);
 }
 async Task Start(){
  try {
   await StartWorker();
   var env=await CoreWebView2Environment.CreateAsync(null,Path.Combine(data,"webview"),null);
   await web.EnsureCoreWebView2Async(env);
   web.CoreWebView2.Settings.AreDevToolsEnabled=false;web.CoreWebView2.Settings.IsStatusBarEnabled=false;
   web.CoreWebView2.PermissionRequested+=(s,e)=>e.State=CoreWebView2PermissionState.Deny;
   web.CoreWebView2.NavigationStarting+=(s,e)=>{if(e.Uri!="about:blank"&&!IsLocal(e.Uri))e.Cancel=true;};
   web.CoreWebView2.NewWindowRequested+=(s,e)=>{e.Handled=true;Uri uri;if(Uri.TryCreate(e.Uri,UriKind.Absolute,out uri)&&uri.Scheme=="https")Process.Start(new ProcessStartInfo(uri.AbsoluteUri){UseShellExecute=true});};
   web.CoreWebView2.NavigationCompleted+=async(s,e)=>{status.Text=e.IsSuccess?"Local worker connected · No Ollama required":"Page load failed: "+e.WebErrorStatus;if(e.IsSuccess){web.CoreWebView2.PostWebMessageAsJson("{\"type\":\"nexus.pets.host\",\"available\":true}");if(!petsListening){petsListening=true;WatchPets();}}if(e.IsSuccess&&!smokeStarted&&Program.Options.ContainsKey("--smoke-report")){smokeStarted=true;await Smoke();}};
   web.CoreWebView2.Navigate(address);
  } catch(Exception ex){status.Text="Startup failed";MessageBox.Show(ex.Message+"\nWebView2 Runtime and .NET Framework 4.6.2+ are required.","RainForge");Close();}
 }
 bool IsLocal(string value){Uri u;return Uri.TryCreate(value,UriKind.Absolute,out u)&&u.GetLeftPart(UriPartial.Authority)==address;}
 async Task<Dictionary<string,object>> PetRpc(string method,object parameters){
  using(var request=new HttpRequestMessage(HttpMethod.Post,address+"/api/rpc")){
   request.Headers.Add("Origin",address);request.Headers.Add("X-RainForge-Token",token);
   request.Content=new StringContent(json.Serialize(new{jsonrpc="2.0",id="native-pets",method=method,@params=parameters}),Encoding.UTF8,"application/json");
   using(var response=await petHttp.SendAsync(request)){
    response.EnsureSuccessStatusCode();var value=json.Deserialize<Dictionary<string,object>>(await response.Content.ReadAsStringAsync());
    if(value.ContainsKey("error"))throw new IOException("Pets service unavailable");return (Dictionary<string,object>)value["result"];
   }
  }
 }
 static bool Flag(Dictionary<string,object> settings,string key){return settings.ContainsKey(key)&&settings[key] is bool&&(bool)settings[key];}
 async void WatchPets(){
  while(!closing){
   bool failed=false;
   try{
    bool initial=lastPetAddress!=address;
    var snapshot=await PetRpc(initial?"pets.get":"pets.wait",initial?(object)new{}:new{after=petsSequence,timeout=20});
    if(closing)break;
    if(initial){lastPetAddress=address;petsSequence=0;petsInitialized=false;}
    var settings=(Dictionary<string,object>)snapshot["settings"];
    bool enabled=Flag(settings,"enabled"),settingsEvent=false;
    object eventList;if(snapshot.TryGetValue("events",out eventList))foreach(object raw in (System.Collections.IEnumerable)eventList){
     var item=raw as Dictionary<string,object>;if(item==null)continue;
     string type=item.ContainsKey("type")?Convert.ToString(item["type"]):"";
     if(type=="settings.changed")settingsEvent=true;
     if(!initial&&type=="host.request"){
      Show();WindowState=FormWindowState.Normal;Activate();
      if(item.ContainsKey("action")&&Convert.ToString(item["action"])=="settings")web.CoreWebView2.PostWebMessageAsJson("{\"type\":\"nexus.navigate\",\"view\":\"settings\"}");
     }
    }
    try{SetPetAutostart(Flag(settings,"start_with_windows"));}catch(Exception ex){status.Text="Nexus Pets startup preference: "+ex.Message;}
    if(!enabled)StopPet();
    else if((!petsInitialized&&(Flag(settings,"start_with_nexus")||Program.Get("--pet-start","false")=="true"))||(petsInitialized&&!lastPetEnabled)||(!initial&&settingsEvent))StartPet();
    if(!petsInitialized&&Program.Get("--pet-start","false")=="true"&&enabled)Hide();
    petsInitialized=true;lastPetEnabled=enabled;petsSequence=Convert.ToInt64(snapshot["sequence"]);
   }catch(Exception ex){if(closing)break;status.Text="Nexus Pets: "+ex.Message;failed=true;}
   if(failed&&!closing)await Task.Delay(2000);
  }
 }
 void StartPet(){
  if(pet!=null&&!pet.HasExited)return;
  if(pet!=null){pet.Dispose();pet=null;}
  string path=Path.Combine(bundle,"NexusPets.exe");if(!File.Exists(path))throw new IOException("Nexus Pets component is missing; reinstall the current bundle");
  pet=Process.Start(new ProcessStartInfo(path,"--address "+Quote(address)+" --data-dir "+Quote(data)+" --owner-pid "+Process.GetCurrentProcess().Id){UseShellExecute=false,CreateNoWindow=true,WorkingDirectory=bundle});
  try{Jobs.Assign(job,pet.Handle);}catch{try{pet.Kill();}catch{}throw;}
  WritePetHost(pet.Id);
 }
 void StopPet(){
  if(pet==null)return;try{if(!pet.HasExited)pet.Kill();}catch(InvalidOperationException){}finally{pet.Dispose();pet=null;WritePetHost(0);}
 }
 void WritePetHost(int pid){
  string folder=Path.Combine(data,"pets");Directory.CreateDirectory(folder);string path=Path.Combine(folder,"native-host.json"),tmp=path+".tmp";
  File.WriteAllText(tmp,json.Serialize(new{owner_pid=Process.GetCurrentProcess().Id,pet_pid=pid,supported=true}),Encoding.UTF8);
  if(File.Exists(path))File.Replace(tmp,path,null);else File.Move(tmp,path);
 }
 void SetPetAutostart(bool enabled){
  string name="RainForgeNexusPets-";using(var sha=SHA256.Create())name+=BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(data.ToLowerInvariant()))).Replace("-","").Substring(0,12);
  using(var run=Microsoft.Win32.Registry.CurrentUser.CreateSubKey("Software\\Microsoft\\Windows\\CurrentVersion\\Run")){
   string command=Quote(Application.ExecutablePath)+" --bundle-root "+Quote(bundle)+" --data-dir "+Quote(data)+" --models-dir "+Quote(Path.GetFullPath(Program.Get("--models-dir",Path.Combine(data,"models"))))+" --pet-start true";
   if(enabled){if(Convert.ToString(run.GetValue(name))!=command)run.SetValue(name,command);}else if(run.GetValue(name)!=null)run.DeleteValue(name,false);
  }
 }
 async Task StartWorker(){
  if(!File.Exists(Path.Combine(bundle,"rainforge-worker.exe"))||!File.Exists(Path.Combine(bundle,"ui","index.html")))throw new IOException("Application bundle is incomplete.");
  if(job==IntPtr.Zero)job=Jobs.Create();
  var listener=new TcpListener(IPAddress.Loopback,0);listener.Start();int port=((IPEndPoint)listener.LocalEndpoint).Port;listener.Stop();address="http://127.0.0.1:"+port;
  string models=Path.GetFullPath(Program.Get("--models-dir",Path.Combine(data,"models")));Directory.CreateDirectory(models);
  string arguments="--data-dir "+Quote(data)+" --runtime-dir "+Quote(Path.Combine(bundle,"runtime"))+" --models-dir "+Quote(models)+" --http --assets "+Quote(Path.Combine(bundle,"ui"))+" --port "+port;
  worker=new Process();worker.StartInfo=new ProcessStartInfo(Path.Combine(bundle,"rainforge-worker.exe"),arguments){WorkingDirectory=bundle,UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true};
  worker.Start();Jobs.Assign(job,worker.Handle);worker.OutputDataReceived+=(s,e)=>{};worker.ErrorDataReceived+=(s,e)=>{};worker.BeginOutputReadLine();worker.BeginErrorReadLine();
  worker.EnableRaisingEvents=true;worker.Exited+=(s,e)=>{if(!closing&&IsHandleCreated)BeginInvoke(new Action(async()=>await Recover()));};
  for(int i=0;i<100;i++){
   if(worker.HasExited)throw new IOException("Worker exited during startup. Another process may own the data directory.");
   try{var raw=await http.GetStringAsync(address+"/api/bootstrap");token=(string)json.Deserialize<Dictionary<string,object>>(raw)["token"];string state=Path.Combine(data,"native-session.json"),tmp=state+".tmp";File.WriteAllText(tmp,json.Serialize(new{host_pid=Process.GetCurrentProcess().Id,worker_pid=worker.Id,address=address}),Encoding.UTF8);if(File.Exists(state))File.Replace(tmp,state,state+".previous");else File.Move(tmp,state);return;}catch(HttpRequestException){}catch(TaskCanceledException){}
   await Task.Delay(100);
  }
  throw new TimeoutException("Local worker did not become ready.");
 }
 async Task Recover(){
  if(closing)return;if(++restarts>2){status.Text="Worker failed repeatedly. Restart the application.";return;}
  status.Text="Recovering local worker…";
  try{Jobs.Close(job);job=IntPtr.Zero;await StartWorker();web.CoreWebView2.Navigate(address);}catch(Exception ex){status.Text="Recovery failed: "+ex.Message;}
 }
 static string Quote(string value){if(value.Contains("\"")||value.Contains("\n"))throw new ArgumentException("Invalid path");return "\""+value+"\"";}
 async Task Smoke(){
  string report=Path.GetFullPath(Program.Get("--smoke-report",""));Directory.CreateDirectory(Path.GetDirectoryName(report));
  try {
   for(int i=0;i<100;i++) {string ready=await web.CoreWebView2.ExecuteScriptAsync("!!document.querySelector('textarea')");if(ready=="true")break;await Task.Delay(100);}
   string note="Native RainForge proof "+Guid.NewGuid().ToString("N");
   string script="(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='Создать заметку');if(!b)throw Error('Note control missing');b.click();return true})()";
   await web.CoreWebView2.ExecuteScriptAsync(script);await Task.Delay(150);
   await web.CoreWebView2.ExecuteScriptAsync("(()=>{const t=document.querySelector('textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(t,"+json.Serialize(note)+");t.dispatchEvent(new Event('input',{bubbles:true}));return true})()");
   await Task.Delay(100);await web.CoreWebView2.ExecuteScriptAsync("document.querySelector('button[aria-label=\"Выполнить\"]').click()");
   bool verified=false;
   for(int i=0;i<100;i++){if(await web.CoreWebView2.ExecuteScriptAsync("!!document.querySelector('.task-result .artifact-card')")=="true"){verified=true;break;}await Task.Delay(100);}
   if(!verified)throw new Exception("Native UI note verification timed out");
   using(var stream=File.Create(report+".png"))await web.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,stream);
   File.WriteAllText(report,json.Serialize(new{status="PASS",native_window=true,ui_note_verified=true,worker_pid=worker.Id,address=address,goal=note}),Encoding.UTF8);
  }catch(Exception ex){File.WriteAllText(report,json.Serialize(new{status="FAILED",error=ex.Message}),Encoding.UTF8);}
  if(Program.Get("--smoke-exit","false")=="true")Close();
 }
 void Cleanup(){closing=true;StopPet();Jobs.Close(job);job=IntPtr.Zero;web.Dispose();http.Dispose();petHttp.Dispose();}
}
static class Jobs {
 [StructLayout(LayoutKind.Sequential)]struct IO{public ulong r,w,o,rb,wb,ob;}
 [StructLayout(LayoutKind.Sequential)]struct Basic{public long process,job;public uint flags;public UIntPtr min,max;public uint active;public UIntPtr affinity;public uint priority,scheduling;}
 [StructLayout(LayoutKind.Sequential)]struct Extended{public Basic basic;public IO io;public UIntPtr processMemory,jobMemory,peakProcess,peakJob;}
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern IntPtr CreateJobObject(IntPtr attributes,string name);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool SetInformationJobObject(IntPtr h,int kind,IntPtr info,uint size);
 [DllImport("kernel32.dll",SetLastError=true)]static extern bool AssignProcessToJobObject(IntPtr h,IntPtr process);
 [DllImport("kernel32.dll")]static extern bool CloseHandle(IntPtr h);
 public static IntPtr Create(){IntPtr h=CreateJobObject(IntPtr.Zero,null);if(h==IntPtr.Zero)throw new System.ComponentModel.Win32Exception();var info=new Extended();info.basic.flags=0x2000;int size=Marshal.SizeOf(info);IntPtr p=Marshal.AllocHGlobal(size);try{Marshal.StructureToPtr(info,p,false);if(!SetInformationJobObject(h,9,p,(uint)size)){Close(h);throw new System.ComponentModel.Win32Exception();}}finally{Marshal.FreeHGlobal(p);}return h;}
 public static void Assign(IntPtr h,IntPtr process){if(!AssignProcessToJobObject(h,process))throw new System.ComponentModel.Win32Exception();}
 public static void Close(IntPtr h){if(h!=IntPtr.Zero)CloseHandle(h);}
}

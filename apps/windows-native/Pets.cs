// Original Nexus Pets artwork and native renderer. Licensed under the repository LICENSE.
// This process shares Nexus's existing authenticated worker; it never starts a model on its own.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Speech.Synthesis;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

static class PetsProgram {
 public static readonly Dictionary<string,string> Options=new Dictionary<string,string>();
 [STAThread] static void Main(string[] args) {
  try {
   for(int i=0;i<args.Length;i+=2){if(i+1>=args.Length)throw new ArgumentException("Missing option value");Options[args[i]]=args[i+1];}
   Trace("arguments-parsed",null);foreach(string suffix in new[]{"",".2",".3",".4",".5"})AppContext.SetSwitch("Switch.UseLegacyAccessibilityFeatures"+suffix,false);NativePet.DpiAware();Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
   Application.ThreadException+=(s,e)=>{Failure("ui-thread",e.Exception);Application.Exit();};
   if(Options.ContainsKey("--render-gallery")){PetArt.Gallery(Options["--render-gallery"]);return;}
   string address=Get("--address","");Uri uri;
   if(!Uri.TryCreate(address,UriKind.Absolute,out uri)||uri.Scheme!="http"||uri.Host!="127.0.0.1"||uri.AbsolutePath!="/"||!string.IsNullOrEmpty(uri.Query))throw new ArgumentException("Nexus worker must be a loopback HTTP origin");
   int owner;if(!int.TryParse(Get("--owner-pid","0"),out owner)||owner<=0)throw new ArgumentException("Nexus owner process is required");
   using(var api=new PetsApi(address.TrimEnd('/')))using(var pet=new PetWindow(api,owner,Get("--data-dir",""))){Trace("window-created",null);Application.Run(pet);}
  }catch(Exception e){Failure("main",e);}
 }
 public static string Get(string k,string fallback){return Options.ContainsKey(k)?Options[k]:fallback;}
 public static void Trace(string stage,Exception error){if(!Options.ContainsKey("--self-test-report"))return;try{File.WriteAllText(Options["--self-test-report"]+".stage.json",new JavaScriptSerializer().Serialize(new{stage=stage,at=DateTime.UtcNow.ToString("o"),error=error==null?null:error.ToString()}));}catch{}}
 public static void Failure(string stage,Exception error){Trace(stage,error);if(!Options.ContainsKey("--self-test-report"))return;try{File.WriteAllText(Options["--self-test-report"],new JavaScriptSerializer().Serialize(new{status="FAILED",stage=stage,error=error.ToString()}));}catch{}}
}

static class PetData {
 public static Dictionary<string,object> Obj(object value){return value as Dictionary<string,object>??new Dictionary<string,object>();}
 public static object Get(Dictionary<string,object> data,string key,object fallback){object value;return data.TryGetValue(key,out value)?value:fallback;}
 public static string Str(Dictionary<string,object> data,string key,string fallback=""){return Convert.ToString(Get(data,key,fallback));}
 public static bool Bool(Dictionary<string,object> data,string key,bool fallback=false){try{return Convert.ToBoolean(Get(data,key,fallback));}catch{return fallback;}}
 public static double Num(Dictionary<string,object> data,string key,double fallback=0){try{var v=Get(data,key,fallback);return v==null?fallback:Convert.ToDouble(v);}catch{return fallback;}}
 public static List<Dictionary<string,object>> Rows(object value){var rows=new List<Dictionary<string,object>>();var values=value as IEnumerable;if(values!=null&&!(value is string))foreach(var item in values)if(item is Dictionary<string,object>)rows.Add((Dictionary<string,object>)item);return rows;}
 public static string Short(string text,int n){return text.Length<=n?text:text.Substring(0,n)+"…";}
}

sealed class PetsApi:IDisposable {
 readonly HttpClient http=new HttpClient(new HttpClientHandler{UseProxy=false});readonly JavaScriptSerializer json=new JavaScriptSerializer{MaxJsonLength=12000000};readonly string address;string token;long request;
 public PetsApi(string origin){address=origin;http.Timeout=TimeSpan.FromSeconds(65);}
 public async Task<Dictionary<string,object>> Call(string method,object parameters=null,CancellationToken cancel=default(CancellationToken)){
  if(token==null){var raw=await http.GetStringAsync(address+"/api/bootstrap");token=PetData.Str(json.Deserialize<Dictionary<string,object>>(raw),"token");}
  using(var req=new HttpRequestMessage(HttpMethod.Post,address+"/api/rpc")){
   req.Headers.Add("Origin",address);req.Headers.Add("X-RainForge-Token",token);
   req.Content=new StringContent(json.Serialize(new{jsonrpc="2.0",id="pets-"+Interlocked.Increment(ref request).ToString(),method=method,@params=parameters??new{}}),Encoding.UTF8,"application/json");
   using(var response=await http.SendAsync(req,cancel)){
    response.EnsureSuccessStatusCode();var body=json.Deserialize<Dictionary<string,object>>(await response.Content.ReadAsStringAsync());
    if(body.ContainsKey("error"))throw new InvalidOperationException(PetData.Str(PetData.Obj(body["error"]),"message","Nexus request failed"));return PetData.Obj(PetData.Get(body,"result",null));
   }
  }
 }
 public Task<Dictionary<string,object>> Update(object settings){return Call("pets.update",new{settings=settings});}
 public void Dispose(){http.Dispose();}
}

sealed class PetWindow:Form {
 readonly PetsApi api;readonly int owner;readonly string data;readonly CancellationTokenSource stopped=new CancellationTokenSource();
 readonly System.Windows.Forms.Timer animation=new System.Windows.Forms.Timer();readonly System.Windows.Forms.Timer health=new System.Windows.Forms.Timer();readonly NotifyIcon tray=new NotifyIcon();
 readonly ContextMenuStrip menu=new ContextMenuStrip();readonly ToolTip tip=new ToolTip();
 Dictionary<string,object> settings=new Dictionary<string,object>();List<Dictionary<string,object>> activities=new List<Dictionary<string,object>>(),packages=new List<Dictionary<string,object>>();
 Bitmap surface;string state="idle",character="nexus-bot",warning="";long sequence;int frame,dpi=96;bool closing,localHidden,fullscreen,dragging,moved,hover,talking;Point dragCursor,dragStart;DateTime activityAt=DateTime.UtcNow,terminalAt=DateTime.MinValue;string terminalState="idle";readonly Stopwatch animationTime=Stopwatch.StartNew();
 MiniAssistant mini;ImportedPet imported;string importKey="";Icon trayArt;bool snapshotApplied;
 public PetWindow(PetsApi worker,int ownerPid,string dataRoot){
  api=worker;owner=ownerPid;data=dataRoot;Text="Nexus Pet — idle";AccessibleName="Nexus Pets desktop companion";FormBorderStyle=FormBorderStyle.None;ShowInTaskbar=false;AutoScaleMode=AutoScaleMode.None;StartPosition=FormStartPosition.Manual;Width=Height=128;TopMost=true;
  menu.Items.Add("Nexus Mini Assistant",null,(s,e)=>OpenMini());menu.Items.Add("Открыть Nexus",null,(s,e)=>OpenMain("open"));menu.Items.Add("Настройки питомца",null,(s,e)=>OpenMain("settings"));menu.Items.Add(new ToolStripSeparator());
  menu.Items.Add("Всегда поверх окон",null,async(s,e)=>{try{await api.Update(new{always_on_top=!TopMost});}catch(Exception x){warning=x.Message;}});
  menu.Items.Add("Скрыть питомца",null,(s,e)=>{localHidden=true;Hide();animation.Stop();tray.Visible=true;});menu.Items.Add("Закрыть питомца",null,async(s,e)=>{try{await api.Update(new{enabled=false});}catch{}Close();});
  using(var art=PetArt.Frame("nexus-bot","idle",0,false,false)){IntPtr icon=art.GetHicon();try{using(var raw=Icon.FromHandle(icon))trayArt=(Icon)raw.Clone();}finally{NativePet.ReleaseIcon(icon);}tray.Icon=trayArt;}
  tray.Text="Nexus Pets";tray.ContextMenuStrip=menu;tray.DoubleClick+=(s,e)=>{localHidden=false;UpdateVisibility();OpenMini();};
  animation.Interval=66;animation.Tick+=(s,e)=>{frame++;Render();};health.Interval=1000;health.Tick+=(s,e)=>Health();
  MouseEnter+=(s,e)=>{hover=true;activityAt=DateTime.UtcNow;};MouseLeave+=(s,e)=>hover=false;
  MouseDown+=(s,e)=>{if(e.Button==MouseButtons.Left){dragging=true;moved=false;dragCursor=Cursor.Position;dragStart=Location;Capture=true;}else if(e.Button==MouseButtons.Right)menu.Show(Cursor.Position);};
  MouseMove+=(s,e)=>{if(dragging){Point d=Cursor.Position;int dx=d.X-dragCursor.X,dy=d.Y-dragCursor.Y;if(Math.Abs(dx)+Math.Abs(dy)>5)moved=true;if(moved)Location=Clamp(new Point(dragStart.X+dx,dragStart.Y+dy),Size);}};
  MouseUp+=async(s,e)=>{if(e.Button!=MouseButtons.Left)return;bool open=dragging&&!moved;dragging=false;Capture=false;activityAt=DateTime.UtcNow;if(moved){try{await api.Update(new{x=Left,y=Top});}catch(Exception x){warning=x.Message;}}else if(open){if(PetData.Bool(settings,"reactions",true)){frame=0;hover=true;}OpenMini();}};
  MouseDoubleClick+=(s,e)=>{if(e.Button==MouseButtons.Left)OpenMini();};
  Shown+=async(s,e)=>{health.Start();await Listen();};FormClosed+=(s,e)=>{closing=true;stopped.Cancel();animation.Stop();health.Stop();if(mini!=null)mini.Close();tray.Visible=false;tray.Dispose();if(trayArt!=null)trayArt.Dispose();if(imported!=null)imported.Dispose();if(surface!=null)surface.Dispose();animation.Dispose();health.Dispose();menu.Dispose();tip.Dispose();};
 }
 protected override bool ShowWithoutActivation{get{return true;}}
 protected override CreateParams CreateParams{get{var p=base.CreateParams;p.ExStyle|=0x80000|0x80|0x08000000;return p;}}
 protected override void WndProc(ref Message m){
  if(m.Msg==0x84&&surface!=null){int packed=m.LParam.ToInt32();Point p=PointToClient(new Point((short)(packed&65535),(short)(packed>>16)));if(p.X<0||p.Y<0||p.X>=surface.Width||p.Y>=surface.Height||surface.GetPixel(p.X,p.Y).A<16){m.Result=(IntPtr)(-1);return;}}
  if(m.Msg==0x2E0){dpi=(int)(m.WParam.ToInt64()&65535);base.WndProc(ref m);ResizeForDpi();return;}
  base.WndProc(ref m);
 }
 async Task Listen(){
  int failures=0;bool first=true;
  while(!closing&&!stopped.IsCancellationRequested){
   try{if(first)PetsProgram.Trace("worker-get",null);var update=await api.Call(first?"pets.get":"pets.wait",first?null:new{after=sequence,timeout=20},stopped.Token);if(closing)return;first=false;if(warning.StartsWith("Nexus: "))warning="";Apply(update);failures=0;}
   catch(OperationCanceledException){return;}catch(Exception e){if(closing)return;PetsProgram.Failure("worker-listen-or-apply",e);warning="Nexus: "+PetData.Short(e.Message,120);failures++;if(failures>=3){state="error";Render();}}
   if(failures>0){try{await Task.Delay(Math.Min(10000,1000*failures),stopped.Token);}catch{return;}}
  }
 }
 void Apply(Dictionary<string,object> update){
  sequence=(long)PetData.Num(update,"sequence",sequence);var next=PetData.Obj(PetData.Get(update,"settings",null));bool wasEnabled=PetData.Bool(settings,"enabled");settings=next;activities=PetData.Rows(PetData.Get(update,"activity",null));packages=PetData.Rows(PetData.Get(update,"packages",null));
  character=PetData.Str(settings,"character","nexus-bot");TopMost=PetData.Bool(settings,"always_on_top",true);
  foreach(var item in PetData.Rows(PetData.Get(update,"events",null))){string eventType=PetData.Str(item,"type");double age=DateTime.UtcNow.Subtract(new DateTime(1970,1,1)).TotalSeconds-PetData.Num(item,"updated_at",0);if(snapshotApplied&&(eventType=="agent.completed"||eventType=="agent.failed")&&age>=0&&age<4){terminalAt=DateTime.UtcNow.AddSeconds(-age);terminalState=eventType=="agent.completed"?"success":"error";if(PetData.Bool(settings,"sounds")&&Visible){if(terminalState=="success")System.Media.SystemSounds.Asterisk.Play();else System.Media.SystemSounds.Exclamation.Play();}}if((eventType.StartsWith("agent.")||eventType.StartsWith("model."))&&age>=0&&age<10)activityAt=DateTime.UtcNow;}
  snapshotApplied=true;
  if(!wasEnabled&&PetData.Bool(settings,"enabled"))localHidden=false;ResizeForDpi();
  if(!dragging&&PetData.Get(settings,"x",null)!=null&&PetData.Get(settings,"y",null)!=null)Location=Clamp(new Point((int)PetData.Num(settings,"x"),(int)PetData.Num(settings,"y")),Size);
  else if(!wasEnabled)Location=Clamp(new Point(Screen.PrimaryScreen.WorkingArea.Right-Width-24,Screen.PrimaryScreen.WorkingArea.Bottom-Height-24),Size);
  if(character.StartsWith("imported:")&&importKey!=character){importKey=character;LoadImported(character.Substring(9));}else if(!character.StartsWith("imported:")&&imported!=null){imported.Dispose();imported=null;importKey="";}
  Health();if(mini!=null&&!mini.IsDisposed){mini.Theme(PetData.Str(settings,"theme","dark"));mini.Activity(activities);}if(PetsProgram.Options.ContainsKey("--self-test-report"))SelfTest();
 }
 async void LoadImported(string id){
  try{var package=packages.Find(p=>PetData.Str(p,"id")==id);if(package==null)throw new InvalidOperationException("Imported pet package unavailable");var loaded=await ImportedPet.Load(api,id,PetData.Obj(PetData.Get(package,"manifest",null)));if(closing||importKey!="imported:"+id){loaded.Dispose();return;}if(imported!=null)imported.Dispose();imported=loaded;Render();}
  catch(Exception e){warning="Пакет: "+e.Message;state="error";Render();}
 }
 void Health(){
  try{using(var process=Process.GetProcessById(owner)){if(process.HasExited){Close();return;}}}catch{Close();return;}
  fullscreen=PetData.Bool(settings,"hide_fullscreen",true)&&NativePet.FullscreenOther(owner);string next="idle";
  if(PetData.Bool(settings,"show_status",true)){
   int best=-1;foreach(var task in activities){string actual=PetData.Str(task,"state");if(actual=="completed"||actual=="failed"||actual=="cancelled"||actual=="stopped"||actual=="interrupted")continue;string ev=PetData.Str(task,"event"),s=MapEvent(ev);int score=s=="waiting"?7:s=="testing"?6:s=="coding"?5:s=="searching"?4:s=="thinking"?3:0;if(score>best&&score>0){next=s;best=score;}}
   if(best<0&&(DateTime.UtcNow-terminalAt).TotalSeconds<4)next=terminalState;
  }
  if(talking)next="talking";else if(next=="idle"&&(DateTime.UtcNow-activityAt).TotalMinutes>2)next="sleeping";
  if(next!=state){state=next;frame=0;animationTime.Restart();}Text="Nexus Pet — "+state;AccessibleDescription=warning.Length>0?warning:activities.Count+" Nexus task status entries";tip.SetToolTip(this,warning.Length>0?warning:"Nexus · "+state+" · Нажмите для Mini Assistant");
  int fps=(int)PetData.Num(settings,"fps",30);if(PetData.Bool(settings,"power_saving",true)&&(state=="idle"||state=="sleeping"))fps=Math.Min(fps,15);animation.Interval=Math.Max(17,(int)Math.Ceiling(1000.0/Math.Max(1,fps)));UpdateVisibility();
 }
 static string MapEvent(string e){switch(e){case"agent.started":case"agent.thinking":case"model.loading":return"thinking";case"agent.coding":return"coding";case"agent.searching":return"searching";case"agent.testing":return"testing";case"agent.waiting_approval":return"waiting";default:return"idle";}}
 void UpdateVisibility(){bool visible=PetData.Bool(settings,"enabled")&&!localHidden&&!fullscreen;if(visible){if(!Visible)Show();if(!animation.Enabled)animation.Start();Render();}else{Hide();animation.Stop();}tray.Visible=localHidden&&PetData.Bool(settings,"enabled");}
 void ResizeForDpi(){if(IsHandleCreated){int actual=NativePet.Dpi(Handle);if(actual>0)dpi=actual;}int size=(int)(PetData.Num(settings,"size",128)*dpi/96.0);size=Math.Max(64,Math.Min(768,size));if(Width!=size||Height!=size){Size=new Size(size,size);Location=Clamp(Location,Size);}}
 public static Point Clamp(Point p,Size size){Rectangle best=Screen.PrimaryScreen.WorkingArea;double distance=double.MaxValue;foreach(var screen in Screen.AllScreens){Rectangle a=screen.WorkingArea;double dx=Math.Max(a.Left-p.X,Math.Max(0,p.X-a.Right)),dy=Math.Max(a.Top-p.Y,Math.Max(0,p.Y-a.Bottom)),d=dx*dx+dy*dy;if(d<distance){distance=d;best=a;}}return new Point(Math.Max(best.Left,Math.Min(p.X,best.Right-size.Width)),Math.Max(best.Top,Math.Min(p.Y,best.Bottom-size.Height)));}
 void Render(){
  if(closing||!IsHandleCreated||!Visible)return;int n=(int)(animationTime.Elapsed.TotalSeconds*30*PetData.Num(settings,"speed",1));if(imported!=null&&imported.Error.Length>0)warning="Пакет: "+imported.Error;
  Bitmap next=new Bitmap(Width,Height,PixelFormat.Format32bppPArgb);using(var g=Graphics.FromImage(next)){g.Clear(Color.Transparent);g.InterpolationMode=InterpolationMode.NearestNeighbor;g.PixelOffsetMode=PixelOffsetMode.Half;using(var art=imported!=null?imported.Frame(state,n):PetArt.Frame(character,state,n,hover&&PetData.Bool(settings,"reactions",true),warning.Length>0)){g.DrawImage(art,new Rectangle(0,0,Width,Height));}}
  try{NativePet.Layer(Handle,next,(byte)Math.Max(64,Math.Min(255,(int)(255*PetData.Num(settings,"opacity",1)))));var old=surface;surface=next;if(old!=null)old.Dispose();}catch{next.Dispose();}
 }
 async void OpenMain(string where){try{await api.Call("pets.host_request",new{action=where});}catch{}NativePet.RaiseOwner(owner);}
 void OpenMini(){PetsProgram.Trace("mini-create",null);activityAt=DateTime.UtcNow;if(mini==null||mini.IsDisposed){mini=new MiniAssistant(api,data,()=>OpenMain("open"),()=>OpenMain("settings"),b=>{talking=b;Health();},()=>PetData.Bool(settings,"voice"));}PetsProgram.Trace("mini-show",null);mini.Theme(PetData.Str(settings,"theme","dark"));mini.Activity(activities);mini.StartPosition=FormStartPosition.Manual;mini.Location=Clamp(new Point(Left-mini.Width+Width,Top-mini.Height),mini.Size);mini.Show();mini.WindowState=FormWindowState.Normal;mini.Activate();mini.FocusInput();}
 bool selfTestSaved;
 async void SelfTest(){if(selfTestSaved)return;selfTestSaved=true;PetsProgram.Trace("self-test-start",null);await Task.Delay(1200);if(closing)return;try{PetsProgram.Trace("self-test-resources",null);var process=Process.GetCurrentProcess();long before=process.TotalProcessorTime.Ticks;var watch=Stopwatch.StartNew();await Task.Delay(1500);process.Refresh();PetsProgram.Trace("self-test-hit-test",null);double cpu=(process.TotalProcessorTime.Ticks-before)/(double)TimeSpan.TicksPerSecond/watch.Elapsed.TotalSeconds/Environment.ProcessorCount*100;bool layered=NativePet.IsLayered(Handle),transparent=surface!=null&&surface.GetPixel(0,0).A==0,hitTransparent=NativePet.Hit(Handle,PointToScreen(Point.Empty))==-1,hitOpaque=false;for(int y=0;surface!=null&&y<surface.Height&&!hitOpaque;y++)for(int x=0;x<surface.Width&&!hitOpaque;x++)if(surface.GetPixel(x,y).A>128)hitOpaque=NativePet.Hit(Handle,PointToScreen(new Point(x,y)))!=-1;var report=new Dictionary<string,object>{{"status",layered&&transparent&&hitTransparent&&hitOpaque?"PASS":"FAILED"},{"layered",layered},{"borderless",FormBorderStyle==FormBorderStyle.None},{"transparent_pixel",transparent},{"transparent_hit_test",hitTransparent},{"opaque_hit_test",hitOpaque},{"size",Width},{"state",state},{"visible",Visible},{"imported_loaded",imported!=null},{"working_set_bytes",process.WorkingSet64},{"cpu_percent_normalized",cpu},{"window_handle",Handle.ToInt64()},{"dpi",dpi},{"monitors",Screen.AllScreens.Length}};
   if(PetsProgram.Options.ContainsKey("--self-test-mini-interactive")){OpenMini();await Task.Delay(800);}
   if(PetsProgram.Options.ContainsKey("--self-test-voice-wav")){
    OpenMini();
    report["voice"]=await mini.SmokeVoice(PetsProgram.Options["--self-test-voice-wav"],PetsProgram.Get("--self-test-voice-submit","false")=="true");
    if(!PetData.Bool(PetData.Obj(report["voice"]),"passed"))report["status"]="FAILED";
   }
   if(PetsProgram.Options.ContainsKey("--self-test-mini-prompt")){OpenMini();report["mini"]=await mini.Smoke(PetsProgram.Options["--self-test-mini-prompt"]);if(!PetData.Bool(PetData.Obj(report["mini"]),"passed"))report["status"]="FAILED";}
   PetsProgram.Trace("self-test-complete",null);File.WriteAllText(PetsProgram.Options["--self-test-report"],new JavaScriptSerializer().Serialize(report));
  }catch(Exception e){PetsProgram.Failure("self-test",e);} }
}

sealed class MiniAssistant:Form {
 readonly PetsApi api;readonly Action openMain,openSettings;readonly Action<bool> talking;readonly Func<bool> voiceEnabled;readonly string threadPath;
 readonly ComboBox models=new ComboBox(),voices=new ComboBox(),recognizers=new ComboBox();readonly TextBox prompt=new TextBox();readonly RichTextBox transcript=new RichTextBox();readonly ListBox tasks=new ListBox();readonly Label status=new Label();readonly Button send=new Button(),stop=new Button(),mic=new Button(),dictate=new Button();readonly CheckBox readAloud=new CheckBox();
 readonly System.Windows.Forms.Timer poll=new System.Windows.Forms.Timer();readonly List<Dictionary<string,object>> modelRows=new List<Dictionary<string,object>>();List<Dictionary<string,object>> activityRows=new List<Dictionary<string,object>>();SpeechSynthesizer speaker;VoiceCapture capture;CancellationTokenSource recognition;
 readonly System.Windows.Forms.Timer dictationTimer=new System.Windows.Forms.Timer();string conversation,runId="",baseTranscript="",lastResponseState="",lastRoute="";
 bool polling,busy,loaded,audioLoaded,dictatedDraft,speakTurn,cancelPending,voiceSession,directTurn;
 VoiceEndpoint endpoint;
 DateTime captureStarted;
 int playbackEpoch;
 bool playing;
 System.Media.SoundPlayer player;
 Prompt activeSapi;
 Action<byte[]> speechSink;
 int pollErrors;
 string speechCompletionStatus="";
 public MiniAssistant(PetsApi worker,string data,Action showMain,Action settings,Action<bool> speaking,Func<bool> allowed){
  PetsProgram.Trace("mini-constructor",null);api=worker;openMain=showMain;openSettings=settings;talking=speaking;voiceEnabled=allowed;Text="Nexus Mini Assistant";AccessibleName=Text;Size=new Size(470,680);MinimumSize=new Size(420,600);ShowInTaskbar=true;MaximizeBox=false;FormBorderStyle=FormBorderStyle.Sizable;BackColor=Color.FromArgb(19,30,27);ForeColor=Color.FromArgb(226,238,229);Font=new Font("Segoe UI",9);TopMost=true;
  threadPath=Path.Combine(data,"pets","mini-conversation.txt");try{string id=File.ReadAllText(threadPath).Trim();Guid parsed;if(Guid.TryParseExact(id,"N",out parsed))conversation=id;}catch{}
  var layout=new TableLayoutPanel{Dock=DockStyle.Fill,ColumnCount=1,RowCount=8,Padding=new Padding(12)};layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));layout.RowStyles.Add(new RowStyle(SizeType.Absolute,56));layout.RowStyles.Add(new RowStyle(SizeType.Percent,100));layout.RowStyles.Add(new RowStyle(SizeType.Absolute,68));layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));layout.RowStyles.Add(new RowStyle(SizeType.Absolute,42));Controls.Add(layout);
  var top=new TableLayoutPanel{Dock=DockStyle.Fill,AutoSize=true,ColumnCount=2,RowCount=1,Margin=Padding.Empty};top.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));top.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));models.DropDownStyle=ComboBoxStyle.DropDownList;models.Dock=DockStyle.Fill;models.DropDownWidth=380;models.AccessibleName="Локальная модель";top.Controls.Add(models,0,0);var refresh=new Button{Text="Обновить",AutoSize=true,AccessibleName="Обновить список установленных моделей"};refresh.Click+=async(s,e)=>await Models();top.Controls.Add(refresh,1,0);layout.Controls.Add(top,0,0);
  tasks.Dock=DockStyle.Fill;tasks.AccessibleName="Реальные задачи Nexus";tasks.DoubleClick+=async(s,e)=>await Result();layout.Controls.Add(tasks,0,1);
  transcript.Dock=DockStyle.Fill;transcript.ReadOnly=true;transcript.BackColor=Color.FromArgb(13,23,20);transcript.ForeColor=ForeColor;transcript.BorderStyle=BorderStyle.FixedSingle;transcript.AccessibleName="История Mini Assistant";layout.Controls.Add(transcript,0,2);
  prompt.Multiline=true;prompt.Dock=DockStyle.Fill;prompt.MaxLength=4000;prompt.AcceptsReturn=true;prompt.ScrollBars=ScrollBars.Vertical;prompt.AccessibleDescription="Enter — отправить, Shift+Enter — новая строка. Дополнительные разрешения выдаются в основном Nexus.";prompt.AccessibleName="Сообщение Nexus";prompt.TextChanged+=(s,e)=>{if(prompt.TextLength==0)dictatedDraft=false;};prompt.KeyDown+=async(s,e)=>{if(e.KeyCode==Keys.Enter&&!e.Shift){e.SuppressKeyPress=true;await Send();}};layout.Controls.Add(prompt,0,3);
  var actions=new FlowLayoutPanel{Dock=DockStyle.Fill,AutoSize=true,WrapContents=true,Margin=Padding.Empty};send.Text="Отправить";send.AutoSize=true;send.Click+=async(s,e)=>await Send();stop.Text="Остановить";stop.AutoSize=true;stop.Click+=async(s,e)=>await Cancel();var fresh=new Button{Text="Новый чат",AutoSize=true};fresh.Click+=(s,e)=>{if(busy)return;conversation=null;transcript.Clear();try{if(File.Exists(threadPath))File.Delete(threadPath);}catch{}};actions.Controls.AddRange(new Control[]{send,stop,fresh});layout.Controls.Add(actions,0,4);
  var audio=new TableLayoutPanel{Dock=DockStyle.Fill,AutoSize=true,ColumnCount=2,RowCount=3,Margin=Padding.Empty};audio.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,50));audio.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,50));for(int row=0;row<3;row++)audio.RowStyles.Add(new RowStyle(SizeType.AutoSize));mic.Text="Поговорить";mic.AutoSize=true;mic.Click+=(s,e)=>ToggleVoice();dictate.Text="Диктовать";dictate.AutoSize=true;dictate.Click+=async(s,e)=>await ToggleDictation();readAloud.Text="Читать текстовые ответы";readAloud.AutoSize=true;audio.Controls.Add(mic,0,0);audio.Controls.Add(dictate,1,0);audio.Controls.Add(readAloud,0,1);audio.SetColumnSpan(readAloud,2);voices.DropDownStyle=recognizers.DropDownStyle=ComboBoxStyle.DropDownList;voices.Dock=recognizers.Dock=DockStyle.Fill;voices.DropDownWidth=300;recognizers.DropDownWidth=180;voices.AccessibleName="Локальный голос";recognizers.AccessibleName="Язык диктовки";recognizers.Items.AddRange(new object[]{"Авто · язык речи","Русский","English"});recognizers.SelectedIndex=0;audio.Controls.Add(voices,0,2);audio.Controls.Add(recognizers,1,2);layout.Controls.Add(audio,0,5);
  var links=new FlowLayoutPanel{Dock=DockStyle.Fill,AutoSize=true,WrapContents=true,Margin=Padding.Empty};var main=new Button{Text="Открыть Nexus",AutoSize=true};main.Click+=(s,e)=>openMain();var approvals=new Button{Text="Разрешения / результат",AutoSize=true};approvals.Click+=(s,e)=>openMain();var config=new Button{Text="Настройки",AutoSize=true};config.Click+=(s,e)=>openSettings();links.Controls.AddRange(new Control[]{main,approvals,config});layout.Controls.Add(links,0,6);status.Dock=DockStyle.Fill;status.AutoEllipsis=true;status.Text="Введите сообщение или нажмите «Поговорить».";layout.Controls.Add(status,0,7);
  readAloud.CheckedChanged+=(s,e)=>{if(readAloud.Checked&&voiceEnabled()){EnsureAudio();}};
  KeyPreview=true;
  KeyDown+=async(s,e)=>{if(e.KeyCode==Keys.Escape&&(voiceSession||capture!=null||recognition!=null)){e.SuppressKeyPress=true;await Cancel();}};
  Resize+=(s,e)=>{if(WindowState==FormWindowState.Minimized)StopAudio();};
  dictationTimer.Interval=150;
  dictationTimer.Tick+=async(s,e)=>{
   if(capture==null)return;
   try{
    if(endpoint!=null&&capture.SpeechEnded(endpoint)){await FinishDictation();return;}
    double elapsed=(DateTime.UtcNow-captureStarted).TotalSeconds;
    if(endpoint!=null&&!endpoint.HasSpeech&&elapsed>=8){StopAudio();status.Text="Речь не слышна. Нажмите «Поговорить» и попробуйте снова.";return;}
    if(elapsed>=VoiceCapture.Seconds)await FinishDictation();
   }catch(Exception ex){StopAudio();status.Text=ex.Message;}
  };
  poll.Interval=250;poll.Tick+=async(s,e)=>await Poll();Shown+=async(s,e)=>{if(!loaded){loaded=true;await Models();await History();}AudioAvailability();};FormClosed+=(s,e)=>{poll.Stop();poll.Dispose();StopAudio();dictationTimer.Dispose();if(speaker!=null)speaker.Dispose();};PetsProgram.Trace("mini-constructor-complete",null);
 }
 void EnsureAudio(){if(audioLoaded||!voiceEnabled())return;audioLoaded=true;
  voices.Items.Add("Авто · локальный голос");voices.SelectedIndex=0;
  try{speaker=new SpeechSynthesizer();speaker.SpeakCompleted+=(s,e)=>{if(!IsDisposed&&IsHandleCreated&&!e.Cancelled)BeginInvoke(new Action(()=>{if(object.ReferenceEquals(e.Prompt,activeSapi))FinishPlayback(e.Error);}));};foreach(var v in speaker.GetInstalledVoices())if(v.Enabled){
   voices.Items.Add(v.VoiceInfo.Name);
  }
  if(voices.Items.Count>0)voices.SelectedIndex=0;}catch{}
  AudioAvailability();
 }
 static string ActivityLabel(Dictionary<string,object> item){
  string state=PetData.Str(item,"state"),kind=PetData.Str(item,"kind");
  var labels=new Dictionary<string,string>{{"answered","Ответ готов"},{"completed","Завершено"},{"failed","Ошибка"},{"cancelled","Остановлено"},{"stopped","Остановлено"},{"ready","Готова"},{"loading","Загрузка"},{"queued","В очереди"},{"routing","Выбирает действие"},{"planning","Обдумывает задачу"},{"executing","Выполняет задачу"},{"generating","Готовит результат"},{"verifying","Проверяет результат"},{"awaiting_permission","Нужно разрешение"},{"awaiting_manual","Ожидает вашего действия"},{"file_created","Файл создан"}};
  string label;return (kind=="model"?"Модель: ":"Задача: ")+(labels.TryGetValue(state,out label)?label:state);
 }
 public void Activity(List<Dictionary<string,object>> rows){activityRows=rows;int selected=tasks.SelectedIndex;tasks.BeginUpdate();try{tasks.Items.Clear();foreach(var item in rows)tasks.Items.Add(ActivityLabel(item));if(selected>=0&&selected<tasks.Items.Count)tasks.SelectedIndex=selected;}finally{tasks.EndUpdate();}AudioAvailability();}
 string currentTheme="";
 public void Theme(string value){if(value==currentTheme)return;currentTheme=value;bool light=value=="light";BackColor=light?Color.FromArgb(239,246,239):Color.FromArgb(19,30,27);ForeColor=light?Color.FromArgb(23,42,31):Color.FromArgb(226,238,229);ThemeControls(this,light);}
 void ThemeControls(Control root,bool light){foreach(Control c in root.Controls){c.ForeColor=ForeColor;if(c is TextBoxBase||c is ComboBox||c is ListBox)c.BackColor=light?Color.White:Color.FromArgb(13,23,20);else c.BackColor=BackColor;var button=c as Button;if(button!=null){button.FlatStyle=FlatStyle.Flat;button.FlatAppearance.BorderColor=light?Color.FromArgb(152,173,154):Color.FromArgb(65,88,74);}ThemeControls(c,light);}}
 void AudioAvailability()
 {
  bool enabled=voiceEnabled(), available=LocalVoice.Available;
  mic.Enabled=voiceSession||(!busy&&!playing&&(enabled?available&&capture==null&&recognition==null:true));
  mic.Text=voiceSession?"Завершить разговор":"Поговорить";
  mic.AccessibleDescription=enabled?"Локальный голосовой разговор. Микрофон включается после нажатия.":"Открыть настройки голоса Nexus Pets.";
  dictate.Enabled=enabled&&available&&!busy&&!voiceSession&&!playing;
  recognizers.Enabled=enabled&&available&&capture==null&&recognition==null;
  readAloud.Enabled=enabled;
  send.Enabled=!busy&&!voiceSession&&capture==null&&recognition==null;
  if(!enabled)StopAudio();
 }
 string SelectedModel(){
  int index=models.SelectedIndex-1;
  return index>=0&&index<modelRows.Count?PetData.Str(modelRows[index],"filename"):null;
 }
 async Task Models(){
  if(busy)return;
  try{
   var snap=await api.Call("system.snapshot");
   if(IsDisposed||busy)return;
   string selected=SelectedModel();
   modelRows.Clear();
   models.BeginUpdate();
   try{
    models.Items.Clear();
    models.Items.Add("Авто · модель по задаче");
    int selection=0;
    foreach(var model in PetData.Rows(PetData.Get(snap,"models",null))){
     if(!PetData.Bool(model,"downloaded")||model.ContainsKey("projector"))continue;
     modelRows.Add(model);
     string filename=PetData.Str(model,"filename");
     models.Items.Add(PetData.Str(model,"name",filename));
     if(filename==selected)selection=models.Items.Count-1;
    }
    models.SelectedIndex=selection;
   }finally{models.EndUpdate();}
   if(!voiceSession)status.Text=modelRows.Count==0?"Установите текстовую модель в разделе «Модели» Nexus.":"Введите сообщение, нажмите «Поговорить» или проверьте текст через «Диктовать».";
  }catch(Exception e){if(!IsDisposed)status.Text=e.Message;}
 }
 async Task History(){if(string.IsNullOrEmpty(conversation))return;string requested=conversation;try{var old=await api.Call("conversation.read",new{conversation_id=requested});if(IsDisposed||busy||conversation!=requested)return;var text=new StringBuilder();foreach(var item in PetData.Rows(PetData.Get(old,"messages",null)))text.AppendLine((PetData.Str(item,"role")=="user"?"Вы: ":"Nexus: ")+PetData.Str(item,"text")+Environment.NewLine);transcript.Text=text.ToString();}catch{if(!busy&&conversation==requested)conversation=null;}}
 public void FocusInput(){if(!busy&&capture==null&&recognition==null)prompt.Focus();}
 public async Task<Dictionary<string,object>> Smoke(string message){
  await Models();
  prompt.Text=message;
  await Send();
  await WaitForReply();
  if(busy||string.IsNullOrEmpty(conversation))return new Dictionary<string,object>{{"passed",false},{"error",status.Text}};
  var stored=await api.Call("conversation.read",new{conversation_id=conversation});
  var rows=PetData.Rows(PetData.Get(stored,"messages",null));
  Size=MinimumSize;
  PerformLayout();
  bool voiceControlsVisible=ControlsFit(mic,dictate,recognizers,voices,readAloud);
  bool found=rows.Exists(row=>PetData.Str(row,"role")=="assistant"&&PetData.Str(row,"state")=="completed"&&PetData.Str(row,"text").Length>0);
  return new Dictionary<string,object>{{"passed",found&&voiceControlsVisible&&lastRoute=="chat"&&lastResponseState=="answered"},{"voice_controls_fit_minimum",voiceControlsVisible},{"conversation_id",conversation},{"stored_messages",rows.Count},{"response_state",lastResponseState},{"route",lastRoute},{"shared_worker",true}};
 }
 bool ControlsFit(params Control[] controls){
  foreach(Control control in controls){
   if(!control.Visible||control.Width<=0||control.Height<=0)return false;
   var bounds=control.RectangleToScreen(control.ClientRectangle);
   for(Control container=control.Parent;container!=null;container=container.Parent)
    if(!container.RectangleToScreen(container.ClientRectangle).Contains(bounds))return false;
  }
  return true;
 }
 async Task WaitForReply(){
  DateTime deadline=DateTime.UtcNow.AddSeconds(90);
  while(busy&&DateTime.UtcNow<deadline){await Task.Delay(100);await Poll();}
 }
 void SetBusy(bool value){
  busy=value;models.Enabled=!value;prompt.ReadOnly=value;AudioAvailability();
 }
 async Task Send(string voiceText=null){
  bool direct=voiceText!=null;
  string message=direct?voiceText:prompt.Text;
  if(busy||capture!=null||recognition!=null||(!direct&&voiceSession)||string.IsNullOrWhiteSpace(message))return;
  string filename=SelectedModel();
  if(playing)StopPlayback();
  cancelPending=false;directTurn=direct;speakTurn=direct||dictatedDraft;lastResponseState="";lastRoute="";pollErrors=0;
  SetBusy(true);
  status.Text="Подготавливаю запрос…";
  try{
   if(string.IsNullOrEmpty(conversation)){
    conversation=PetData.Str(await api.Call("conversation.create"),"id");
    Directory.CreateDirectory(Path.GetDirectoryName(threadPath));
    File.WriteAllText(threadPath,conversation);
   }
   if(IsDisposed||cancelPending)return;
   var run=await api.Call("task.start",new{goal=message,conversation_id=conversation,mode=direct?"voice":"auto",model_filename=filename});
   if(IsDisposed)return;
   runId=PetData.Str(run,"id");
   if(runId.Length==0)throw new InvalidOperationException("Nexus не вернул идентификатор запроса.");
   if(!direct)prompt.Clear();
   transcript.AppendText("Вы: "+message+Environment.NewLine+Environment.NewLine+"Nexus: ");
   baseTranscript=transcript.Text;
   poll.Start();
   if(cancelPending)await api.Call("task.cancel",new{run_id=runId});
  }catch(Exception e){if(!IsDisposed){StopAudio();status.Text=e.Message;}}
  finally{if(!IsDisposed&&runId.Length==0)SetBusy(false);}
 }
 async Task Poll(){
  if(polling||runId.Length==0||IsDisposed)return;
  polling=true;
  try{
   var reply=await api.Call("task.status",new{run_id=runId});
   if(IsDisposed)return;
   pollErrors=0;
   poll.Interval=250;
   string state=PetData.Str(reply,"state");
   switch(state){
    case "queued":case "routing":status.Text="Определяю, чем помочь…";return;
    case "answering":status.Text="Готовлю ответ…";return;
    case "planning":case "executing":case "generating":case "verifying":
     status.Text="Выполняю задачу. Можно остановить её кнопкой ниже.";return;
   }
   string message=PetData.Str(reply,"message"),error=PetData.Str(reply,"error");
   bool awaiting=state=="awaiting_permission"||state=="awaiting_manual";
   if(string.IsNullOrWhiteSpace(message))
    message=!string.IsNullOrWhiteSpace(error)?error:state=="cancelled"?"Запрос остановлен.":"Подробнее о результате можно узнать в основном окне Nexus.";
   lastResponseState=state;
   lastRoute=PetData.Str(reply,"route");
   poll.Stop();
   runId="";
   transcript.Text=baseTranscript+message+Environment.NewLine+Environment.NewLine;
   transcript.SelectionStart=transcript.TextLength;
   transcript.ScrollToCaret();
   bool created=state=="file_created"||state=="project_created";
   string completion=awaiting?"Откройте «Разрешения / результат» в Nexus.":
    state=="cancelled"?"Запрос остановлен.":
    error.Length>0?"Возникла ошибка. Подробности в переписке.":
    created?"Результат сохранён. Файлы доступны в разделе «Код» Nexus.":
    directTurn?"Реплика сохранена. Чтобы исправить текст, воспользуйтесь «Диктовать».":"Ответ сохранён в общей истории Nexus.";
   status.Text=completion;
   bool readAnswer=!cancelPending&&(speakTurn||(!directTurn&&readAloud.Checked));
   SetBusy(false);
   if(directTurn||state=="cancelled"||error.Length>0){EndVoiceSession();AudioAvailability();}
   if(readAnswer&&state!="cancelled")
    SpeakResponse(error.Length>0?"Возникла ошибка. Подробности в переписке.":message,completion);
  }catch(Exception){
   if(!IsDisposed){
    pollErrors++;
    poll.Interval=2000;
    status.Text="Связь с Nexus прервалась. Проверяю статус, запрос не повторяется.";
    if(pollErrors>=5){poll.Stop();runId="";StopAudio();SetBusy(false);status.Text="Не удалось получить статус. Откройте Nexus, чтобы проверить результат перед повтором.";}
   }
  }finally{polling=false;}
 }
 async void SpeakResponse(string text,string completionStatus)
 {
  if(!voiceEnabled()||string.IsNullOrWhiteSpace(text))return;
  StopPlayback();
  EnsureAudio();
  int epoch=playbackEpoch;
  speechCompletionStatus=completionStatus;
  playing=true;
  AudioAvailability();
  text=System.Text.RegularExpressions.Regex.Replace(text,@"\x60{3}[\s\S]*?\x60{3}"," Код есть в переписке. ");
  text=PetData.Short(text.Replace("**","").Replace("\u0060",""),400);
  try{
   if(voices.SelectedIndex<=0&&System.Text.RegularExpressions.Regex.IsMatch(text,@"\p{IsCyrillic}")){
    try{
     status.Text="Готовлю голосовой ответ…";
     var result=await api.Call("voice.synthesize",new{text=text});
     if(IsDisposed||epoch!=playbackEpoch)return;
     byte[] wav=Convert.FromBase64String(PetData.Str(result,"wav"));
     if(speechSink!=null){speechSink(wav);FinishPlayback(null);return;}
     using(var stream=new MemoryStream(wav,false))
     using(var output=new System.Media.SoundPlayer(stream)){
      player=output;
      status.Text="Дмитрий отвечает…";
      talking(true);
      await Task.Run(()=>{if(epoch==System.Threading.Volatile.Read(ref playbackEpoch))output.PlaySync();});
     }
     if(!IsDisposed&&epoch==playbackEpoch)FinishPlayback(null);
     return;
    }catch(Exception){if(IsDisposed||epoch!=playbackEpoch)return;}
   }
   if(speaker==null||voices.Items.Count<2){FinishPlayback(new InvalidOperationException());return;}
   if(voices.SelectedIndex>0)speaker.SelectVoice(voices.SelectedItem.ToString());
   else{
    string language=System.Text.RegularExpressions.Regex.IsMatch(text,@"\p{IsCyrillic}")?"ru":"en";
    InstalledVoice matching=null;
    foreach(var voice in speaker.GetInstalledVoices()){
     if(!voice.Enabled||voice.VoiceInfo.Culture.TwoLetterISOLanguageName!=language)continue;
     matching=voice;break;
    }
    if(matching==null){FinishPlayback(new InvalidOperationException("No installed voice matches the reply language"));return;}
    speaker.SelectVoice(matching.VoiceInfo.Name);
   }
   status.Text="Nexus отвечает…";
   talking(true);
   activeSapi=speaker.SpeakAsync(text);
  }catch(Exception error){if(!IsDisposed&&epoch==playbackEpoch)FinishPlayback(error);}
 }

 void FinishPlayback(Exception error)
 {
  if(IsDisposed||!playing)return;
  player=null;activeSapi=null;playing=false;talking(false);
  status.Text=error==null?"Ответ готов. Нажмите «Поговорить» для следующей реплики.":"Ответ в чате. Озвучивание недоступно.";
  AudioAvailability();
 }

 void StopPlayback()
 {
  playbackEpoch++;activeSapi=null;
  if(player!=null){try{player.Stop();}catch{}player=null;}
  if(speaker!=null){try{speaker.SpeakAsyncCancelAll();}catch{}}
  playing=false;talking(false);
 }
 async Task Cancel(){
  try{
   bool dictating=voiceSession||capture!=null||recognition!=null||playing;
   StopAudio();
   if(dictating&&!busy){AudioAvailability();status.Text="Голос остановлен.";return;}
   if(busy){
    cancelPending=true;
    status.Text="Останавливаю запрос…";
    if(runId.Length>0)await api.Call("task.cancel",new{run_id=runId});
    return;
   }
   if(tasks.SelectedIndex<0)return;
   var row=activityRows[tasks.SelectedIndex];
   string kind=PetData.Str(row,"kind"),id=PetData.Str(row,"run_id",PetData.Str(row,"id"));
   if(kind=="chat")await api.Call("chat.cancel",new{chat_id=id});
   else if(kind=="agent")await api.Call("agent.cancel");
   else if(kind=="repair")await api.Call("coding.repair.cancel");
   else if(kind=="browser")await api.Call("browser.agent.cancel");
   else if(kind=="action")await api.Call("task.cancel",new{run_id=id});
   else{status.Text="Откройте задачу в Nexus для управления.";return;}
   status.Text="Запрос остановки отправлен. Выполненные действия сохраняются.";
  }catch(Exception e){if(!IsDisposed)status.Text=e.Message;}
 }
 async Task Result(){if(tasks.SelectedIndex<0)return;var row=activityRows[tasks.SelectedIndex];string kind=PetData.Str(row,"kind"),id=PetData.Str(row,"run_id",PetData.Str(row,"id"));try{Dictionary<string,object> r=null;if(kind=="action")r=await api.Call("task.status",new{run_id=id});else if(kind=="chat")r=await api.Call("chat.poll",new{chat_id=id});else if(kind=="repair")r=await api.Call("coding.repair.status",new{run_id=id});else if(kind=="browser")r=await api.Call("browser.agent.status",new{run_id=id});else if(kind=="agent"){var snapshot=await api.Call("agent.list");r=PetData.Rows(PetData.Get(snapshot,"runs",null)).Find(run=>PetData.Str(run,"id")==id);}if(r==null){status.Text="Подробные результаты доступны в основном Nexus.";return;}var output=new StringBuilder("Статус: "+PetData.Str(r,"state")+Environment.NewLine);foreach(var key in new[]{"message","text","error"}){string text=PetData.Str(r,key);if(text.Length>0)output.AppendLine(text);}foreach(var step in PetData.Rows(PetData.Get(r,"steps",null))){output.AppendLine(PetData.Str(PetData.Obj(PetData.Get(step,"agent",null)),"name")+" · "+PetData.Str(step,"state"));output.AppendLine(PetData.Str(step,"text"));}transcript.Text=PetData.Short(output.ToString(),120000);}catch(Exception e){status.Text=e.Message;}}
 void ToggleVoice()
 {
  if(voiceSession){StopAudio();AudioAvailability();status.Text="Голосовой разговор завершён.";return;}
  if(!voiceEnabled()){openSettings();status.Text="Включите голос в настройках Nexus Pets.";return;}
  if(busy||playing||capture!=null||recognition!=null)return;
  StopAudio();
  voiceSession=true;
  BeginListening(true);
  if(capture!=null&&recognizers.SelectedIndex!=2&&voices.SelectedIndex<=0)PrepareVoice();
 }

 async void PrepareVoice()
 {
  try{await api.Call("voice.prepare");}catch(Exception){}
 }

 void BeginListening(bool direct)
 {
  try{
   capture=new VoiceCapture();
   endpoint=direct?new VoiceEndpoint():null;
   captureStarted=DateTime.UtcNow;
   dictate.Text=direct?"Диктовать":"Готово";
   status.Text=direct?"Микрофон включён. Говорите. Пауза отправит реплику.":"Диктовка в поле. Нажмите «Готово», чтобы завершить запись.";
   dictationTimer.Start();
   AudioAvailability();
  }catch(Exception e){StopAudio();AudioAvailability();status.Text=e.Message;}
 }

 void EndVoiceSession(){voiceSession=false;if(!IsDisposed){mic.Text="Поговорить";send.Enabled=!busy&&capture==null&&recognition==null;}}

 async Task ToggleDictation()
 {
  if(!voiceEnabled()||busy)return;
  if(recognition!=null){StopAudio();status.Text="Диктовка отменена.";return;}
  if(capture!=null){await FinishDictation();return;}
  StopAudio();
  BeginListening(false);
 }

 async Task FinishDictation()
 {
  dictationTimer.Stop();
  if(capture==null)return;
  var recording=capture;
  capture=null;
  bool direct=endpoint!=null;
  endpoint=null;
  try{
   if(direct)await SendVoiceTurn(recording.Finish());
   else await Recognize(recording.Finish(),true);
  }
  catch(Exception e){if(!IsDisposed){EndVoiceSession();status.Text=e.Message;}}
  finally { if(!IsDisposed){dictate.Text="Диктовать";AudioAvailability();} }
 }

 async Task SendVoiceTurn(byte[] wave)
 {
  string text=await Recognize(wave,false);
  if(text.Length>0&&!IsDisposed)await Send(PetData.Short(text,prompt.MaxLength));
  else if(!IsDisposed){EndVoiceSession();AudioAvailability();}
 }

 async Task<string> Recognize(byte[] wave,bool draft)
 {
  var operation=new CancellationTokenSource();
  recognition=operation;
  dictate.Text="Отменить";
  status.Text="Распознаю речь локально…";
  AudioAvailability();
  string language=recognizers.SelectedIndex==1?"ru":recognizers.SelectedIndex==2?"en":"auto";
  try
  {
   string text=await Task.Run(()=>LocalVoice.Transcribe(wave,language,operation.Token,quick:!draft));
   if(IsDisposed||operation.IsCancellationRequested||!voiceEnabled())return "";
   if(text.Length==0){status.Text="Речь не распознана. Попробуйте ещё раз.";return "";}
   if(!draft)return text;
   string separator=prompt.TextLength==0?"":" ";
   prompt.AppendText(PetData.Short(separator+text,Math.Max(0,prompt.MaxLength-prompt.TextLength)));
   dictatedDraft=true;
   status.Text="Проверьте черновик и нажмите Enter. Nexus озвучит ответ.";
   prompt.Focus();
   return text;
  }
  catch(OperationCanceledException){return "";}
  finally
  {
   Array.Clear(wave,0,wave.Length);
   if(object.ReferenceEquals(recognition,operation))recognition=null;
   operation.Dispose();
   if(!IsDisposed){dictate.Text="Диктовать";AudioAvailability();}
  }
 }

 public async Task<Dictionary<string,object>> SmokeVoice(string file,bool submit)
 {
  var input=new FileInfo(file);
  if(input.Length>2*1024*1024)throw new ArgumentException("Voice fixture is too large");
  string testLanguage=PetsProgram.Get("--self-test-voice-language","");
  if(testLanguage=="ru"||testLanguage=="en")recognizers.SelectedIndex=testLanguage=="ru"?1:2;
  if(!submit){
   string text=await Recognize(File.ReadAllBytes(input.FullName),true);
   bool drafted=text.Length>0&&prompt.Text==text&&runId==""&&dictatedDraft;
   return new Dictionary<string,object>{{"passed",drafted},{"draft",prompt.Text},{"sent",false}};
  }
  var report=new Dictionary<string,object>{{"passed",false},{"sent",false},{"direct",true},{"speech_endpoint",CheckVoiceEndpoint()}};

  // Use the same neural-synthesis path, but route the resulting audio to the fixture.
  await Models();
  var completed=new TaskCompletionSource<byte[]>();
  speechSink=bytes=>completed.TrySetResult(bytes);
  try{
   voiceSession=true;
   var latency=Stopwatch.StartNew();
   await SendVoiceTurn(File.ReadAllBytes(input.FullName));
   await WaitForReply();
   var finished=await Task.WhenAny(completed.Task,Task.Delay(20000));
   byte[] audio=finished==completed.Task?await completed.Task:new byte[0];
   report["sent"]=lastResponseState.Length>0;
   report["draft"]=prompt.Text;
   report["route"]=lastRoute;
   report["response_state"]=lastResponseState;
   report["speech_wave_bytes"]=audio.Length;
   report["processing_seconds"]=Math.Round(latency.Elapsed.TotalSeconds,2);
   report["voice"]="dmitri";
   report["conversation_id"]=conversation;
   report["microphone_rearmed"]=capture!=null||voiceSession;
   report["passed"]=audio.Length>44&&!busy&&lastResponseState.Length>0&&(bool)report["speech_endpoint"]&&capture==null&&!voiceSession;
  }finally{speechSink=null;}
  return report;
 }

 static bool CheckVoiceEndpoint()
 {
  var test=new VoiceEndpoint();
  var silence=new byte[VoiceEndpoint.FrameBytes];
  var speech=new byte[VoiceEndpoint.FrameBytes];
  for(int i=1;i<speech.Length;i+=2)speech[i]=4;
  for(int i=0;i<75;i++)if(test.Observe(silence,0))return false;
  for(int i=0;i<8;i++)if(test.Observe(speech,0))return false;
  for(int i=0;i<44;i++)if(test.Observe(silence,0))return false;
  return test.Observe(silence,0);
 }

 void StopAudio()
 {
  EndVoiceSession();speakTurn=false;
  dictationTimer.Stop();endpoint=null;
  if(capture!=null){capture.Dispose();capture=null;}
  if(recognition!=null)recognition.Cancel();
  StopPlayback();
  dictate.Text="Диктовать";
  send.Enabled=!busy&&recognition==null;
 }
}

sealed class ImportedPet:IDisposable {
 readonly PetsApi api;readonly string id;readonly Dictionary<string,object> states;readonly int width,height;Image image;MemoryStream stream;string loadedFile="",failedFile="";Dictionary<string,object> loadedAnimation;bool loading,disposed;public string Error="";
 ImportedPet(PetsApi worker,string packageId,Dictionary<string,object> manifest){api=worker;id=packageId;states=PetData.Obj(PetData.Get(manifest,"states",null));width=(int)PetData.Num(manifest,"frame_width",32);height=(int)PetData.Num(manifest,"frame_height",32);}
 Dictionary<string,object> Animation(string state){object value;if(!states.TryGetValue(state,out value))value=PetData.Get(states,"idle",null);return PetData.Obj(value);}
 public static async Task<ImportedPet> Load(PetsApi api,string id,Dictionary<string,object> manifest){var pet=new ImportedPet(api,id,manifest);try{await pet.LoadFile(pet.Animation("idle"));return pet;}catch{pet.Dispose();throw;}}
 async Task LoadFile(Dictionary<string,object> animation){
  string file=PetData.Str(animation,"file");if(file.EndsWith(".webp",StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("WebP нужно преобразовать в PNG при импорте");var asset=await api.Call("pets.asset",new{package_id=id,path=file});byte[] bytes=Convert.FromBase64String(PetData.Str(asset,"data"));var nextStream=new MemoryStream(bytes,false);Image nextImage=null;
  try{nextImage=Image.FromStream(nextStream,true,true);if(nextImage.Width>4096||nextImage.Height>4096)throw new InvalidOperationException("Image too large");if(disposed)return;if(image!=null)image.Dispose();if(stream!=null)stream.Dispose();image=nextImage;stream=nextStream;nextImage=null;nextStream=null;loadedFile=file;loadedAnimation=animation;Error="";}finally{if(nextImage!=null)nextImage.Dispose();if(nextStream!=null)nextStream.Dispose();}
 }
 async void ChangeFile(Dictionary<string,object> animation){loading=true;try{await LoadFile(animation);}catch(Exception e){failedFile=PetData.Str(animation,"file");Error=e.Message;}finally{loading=false;}}
 public Bitmap Frame(string state,int tick){var animation=Animation(state);string file=PetData.Str(animation,"file");if(file!=loadedFile){if(!loading&&file!=failedFile)ChangeFile(animation);animation=loadedAnimation;}if(image==null)throw new InvalidOperationException("Pet image unavailable");var indices=PetData.Get(animation,"frames",null) as IEnumerable;var frames=new List<int>();if(indices!=null)foreach(var n in indices)frames.Add(Convert.ToInt32(n));if(frames.Count==0)frames.Add(0);int at=(int)(tick*PetData.Num(animation,"fps",8)/30)%frames.Count,index=frames[at];int columns=Math.Max(1,image.Width/width);int x=(index%columns)*width,y=(index/columns)*height;
  int sourceWidth=Math.Min(width,image.Width-x),sourceHeight=Math.Min(height,image.Height-y);
  if(image.RawFormat.Guid==ImageFormat.Gif.Guid){var dim=new FrameDimension(image.FrameDimensionsList[0]);int count=image.GetFrameCount(dim),duration=100;try{var delays=image.GetPropertyItem(0x5100).Value;int total=0;for(int i=0;i<count&&i*4+3<delays.Length;i++)total+=Math.Max(10,BitConverter.ToInt32(delays,i*4)*10);int ms=total>0?(int)(tick*1000.0/30)%total:0;int chosen=0;for(int i=0;i<count&&i*4+3<delays.Length;i++){duration=Math.Max(10,BitConverter.ToInt32(delays,i*4)*10);if(ms<duration){chosen=i;break;}ms-=duration;}image.SelectActiveFrame(dim,chosen);}catch{image.SelectActiveFrame(dim,(tick/3)%Math.Max(1,count));}x=y=0;sourceWidth=image.Width;sourceHeight=image.Height;}
  var result=new Bitmap(width,height,PixelFormat.Format32bppArgb);using(var g=Graphics.FromImage(result)){g.Clear(Color.Transparent);g.DrawImage(image,new Rectangle(0,0,width,height),new Rectangle(x,y,sourceWidth,sourceHeight),GraphicsUnit.Pixel);}return result;}
 public void Dispose(){disposed=true;if(image!=null){image.Dispose();image=null;}if(stream!=null){stream.Dispose();stream=null;}}
}

static class PetArt {
 public static readonly string[] Characters={"nexus-bot","pixel-cat","cyber-fox","mini-dragon","nexus-ghost"};public static readonly string[] States={"idle","thinking","coding","searching","testing","success","error","waiting","sleeping","talking"};
 static readonly Color outline=Color.FromArgb(29,43,52),shadow=Color.FromArgb(64,88,102),mint=Color.FromArgb(166,235,167),cyan=Color.FromArgb(83,209,219),cream=Color.FromArgb(241,239,209),white=Color.FromArgb(247,255,245);
 static void Box(Graphics g,Color c,int x,int y,int w,int h){using(var b=new SolidBrush(c))g.FillRectangle(b,x,y,w,h);}
 static void Pixel(Graphics g,Color c,params int[] xy){for(int i=0;i+1<xy.Length;i+=2)Box(g,c,xy[i],xy[i+1],1,1);}
 static void Eye(Graphics g,int x,int y,bool blink,bool sleep,int glance){x+=glance;if(blink||sleep)Box(g,outline,x,y+1,3,1);else{Box(g,outline,x,y,3,3);Box(g,white,x,y,1,1);}}
 static void Sprite(Graphics g,string kind,int y,bool blink,bool sleep,int frame,bool talking){
  int b=y,glance=(frame/75)%3==1?-1:0;Box(g,Color.FromArgb(42,10,21,25),9,29,17,2);
  if(kind=="pixel-cat"){
   Color fur=Color.FromArgb(179,164,227),light=Color.FromArgb(217,204,246);Box(g,outline,8,b+9,18,15);Box(g,fur,9,b+10,16,13);Box(g,outline,8,b+7,3,6);Box(g,outline,11,b+9,3,4);Box(g,outline,23,b+7,3,6);Box(g,outline,20,b+9,3,4);Box(g,fur,9,b+8,1,5);Box(g,fur,10,b+10,3,3);Box(g,fur,24,b+8,1,5);Box(g,fur,21,b+10,3,3);Box(g,Color.FromArgb(235,160,190),10,b+10,1,2);Box(g,Color.FromArgb(235,160,190),23,b+10,1,2);Box(g,light,10,b+12,14,7);Box(g,outline,10,b+23,5,4);Box(g,outline,20,b+23,5,4);Box(g,fur,11,b+23,3,3);Box(g,fur,21,b+23,3,3);Box(g,outline,26,b+17,3,7);Box(g,fur,26,b+18,2,5);Eye(g,11,b+14,blink,sleep,glance);Eye(g,20,b+14,blink,sleep,glance);Box(g,Color.FromArgb(202,124,158),17,b+18,1,1);Pixel(g,cream,8,b+18,25,b+18,7,b+20,26,b+20);Box(g,mint,15,b+22,5,2);
  }else if(kind=="cyber-fox"){
   Color fur=Color.FromArgb(230,141,78),light=Color.FromArgb(255,203,131);Box(g,outline,7,b+5,5,12);Box(g,outline,22,b+5,5,12);Box(g,fur,8,b+6,3,10);Box(g,fur,23,b+6,3,10);Box(g,cyan,9,b+8,1,5);Box(g,cyan,24,b+8,1,5);Box(g,outline,8,b+12,18,11);Box(g,fur,9,b+12,16,10);Box(g,outline,10,b+22,14,5);Box(g,fur,11,b+22,12,4);Box(g,light,13,b+18,9,4);Box(g,outline,16,b+19,3,1);Box(g,outline,25,b+20,5,6);Box(g,fur,25,b+21,4,4);Box(g,cream,28,b+20,2,3);Eye(g,11,b+14,blink,sleep,glance);Eye(g,20,b+14,blink,sleep,glance);Box(g,cyan,9,b+13,2,1);Box(g,cyan,23,b+13,2,1);Box(g,shadow,12,b+26,4,2);Box(g,shadow,20,b+26,4,2);
  }else if(kind=="mini-dragon"){
   Color skin=Color.FromArgb(120,196,150),light=Color.FromArgb(197,226,156);Box(g,outline,5,b+14,5,10);Box(g,outline,25,b+14,5,10);Box(g,Color.FromArgb(94,146,155),6,b+15,4,7);Box(g,Color.FromArgb(94,146,155),25,b+15,4,7);Pixel(g,cyan,5,b+13,29,b+13,4,b+17,30,b+17);Box(g,outline,9,b+9,16,16);Box(g,skin,10,b+10,14,14);Box(g,outline,9,b+7,4,6);Box(g,outline,21,b+7,4,6);Box(g,cream,10,b+6,2,5);Box(g,cream,22,b+6,2,5);Box(g,light,13,b+19,9,6);Box(g,outline,10,b+25,6,3);Box(g,outline,20,b+25,6,3);Box(g,skin,11,b+25,4,2);Box(g,skin,21,b+25,4,2);Eye(g,11,b+13,blink,sleep,glance);Eye(g,20,b+13,blink,sleep,glance);Box(g,outline,16,b+18,3,1);Pixel(g,cyan,16,b+6,16,b+8,25,b+24,27,b+26);
  }else if(kind=="nexus-ghost"){
   Color ghost=Color.FromArgb(193,222,232),light=Color.FromArgb(228,247,245);Box(g,outline,11,b+8,12,2);Box(g,outline,8,b+10,18,14);Box(g,ghost,9,b+11,16,13);Box(g,light,11,b+10,12,10);Box(g,ghost,8,b+19,2,7);Box(g,ghost,24,b+19,2,7);Box(g,outline,10,b+24,4,3);Box(g,outline,16,b+24,3,4);Box(g,outline,21,b+24,4,3);Box(g,ghost,10,b+23,4,3);Box(g,ghost,16,b+23,3,4);Box(g,ghost,21,b+23,4,3);Eye(g,12,b+14,blink,sleep,glance);Eye(g,20,b+14,blink,sleep,glance);Box(g,Color.FromArgb(133,199,211),11,b+19,2,1);Box(g,Color.FromArgb(133,199,211),23,b+19,2,1);Box(g,mint,15,b+21,6,2);Pixel(g,cyan,27,b+8,28,b+9,6,b+10,5,b+9);
  }else{
   Box(g,outline,9,b+9,17,12);Box(g,shadow,10,b+10,15,10);Box(g,mint,11,b+10,13,2);Box(g,outline,11,b+12,13,7);Box(g,cyan,12,b+13,11,5);if(blink||sleep){Box(g,outline,13,b+15,3,1);Box(g,outline,20,b+15,3,1);}else{Box(g,white,13,b+14,3,2);Box(g,white,20,b+14,3,2);}Box(g,outline,16,b+5,3,4);Box(g,mint,16,b+4,3,2);Box(g,outline,7,b+12,2,6);Box(g,outline,26,b+12,2,6);Box(g,mint,7,b+13,2,3);Box(g,mint,26,b+13,2,3);Box(g,outline,11,b+21,13,5);Box(g,shadow,12,b+21,11,4);Box(g,mint,15,b+22,5,2);Box(g,outline,9,b+23,2,3);Box(g,outline,24,b+23,2,3);Box(g,outline,12,b+26,4,2);Box(g,outline,20,b+26,4,2);Pixel(g,white,11,b+10,24,b+10);
  }
  if(talking){Box(g,outline,16,b+19,3,2);Box(g,cream,17,b+19,1,1);}
 }
 public static Bitmap Frame(string kind,string state,int tick,bool hover,bool warning){
  var result=new Bitmap(36,36,PixelFormat.Format32bppArgb);using(var g=Graphics.FromImage(result)){
   g.Clear(Color.Transparent);g.SmoothingMode=SmoothingMode.None;int bob=(tick/10)%2;bool blink=tick%87<3;int shift=state=="success"?-(tick/4)%3:state=="sleeping"?1+bob:bob;
   Sprite(g,kind,shift+3,blink,state=="sleeping",tick,state=="talking"&&tick%8<5);
   if(state=="thinking"){Pixel(g,mint,26,6,29,4,32,5);Box(g,cyan,26+(tick/5)%3*3,5,2,2);}
   else if(state=="coding"){Box(g,outline,9,23,19,9);Box(g,shadow,10,24,17,6);Box(g,cyan,11,25,15,4);Box(g,mint,13,26,5+(tick/3)%5,1);Box(g,outline,7,32,23,2);Box(g,cream,10+(tick%4),31,3,1);Box(g,cream,22-(tick%4),31,3,1);}
   else if(state=="searching"){Box(g,outline,25,15,7,8);Box(g,cyan,26,16,5,6);Box(g,Color.FromArgb(190,231,235),27,17,3,4);Box(g,outline,29,23,3,6);Pixel(g,white,27,17,28,17);}
   else if(state=="testing"){Box(g,outline,25,18,8,13);Box(g,cream,26,19,6,11);Box(g,mint,28,17,3,3);Pixel(g,shadow,27,22,28,23,29,22,30,21,27,26,28,27,29,26,30,25);}
   else if(state=="success"){Color star=(tick/4)%2==0?mint:cyan;Pixel(g,star,5,7,4,8,6,8,5,9,29,7,28,8,30,8,29,9,12,3,13,4,24,3,23,4);}
   else if(state=="waiting"||state=="error"||warning){Color c=state=="error"||warning?Color.FromArgb(239,147,126):Color.FromArgb(235,208,127);Box(g,outline,25,1,8,9);Box(g,c,26,2,6,7);Box(g,outline,28,3,2,3);Box(g,outline,28,7,2,1);}
   else if(state=="sleeping"){Pixel(g,cyan,25,6,26,6,27,6,26,7,25,8,26,8,27,8,30,2,31,2,32,2,31,3,30,4,31,4,32,4);}
   else if(state=="talking"){Pixel(g,mint,29,12,30,13,30,14,29,15,32,10,33,11,34,12,34,15,33,16,32,17);}
   if(hover){Pixel(g,white,5,14,4,15,5,16);Box(g,mint,4,14,3,1);}
  }return result;
 }
 public static void Gallery(string path){using(var sheet=new Bitmap(Characters.Length*144,States.Length*150)){using(var g=Graphics.FromImage(sheet)){g.Clear(Color.FromArgb(19,30,27));using(var font=new Font("Segoe UI",9))for(int y=0;y<States.Length;y++)for(int x=0;x<Characters.Length;x++){using(var sprite=Frame(Characters[x],States[y],12,false,false)){g.InterpolationMode=InterpolationMode.NearestNeighbor;g.DrawImage(sprite,new Rectangle(x*144+8,y*150+7,128,128));}g.DrawString(Characters[x]+" / "+States[y],font,Brushes.White,x*144+5,y*150+134);}}Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path)));sheet.Save(path,ImageFormat.Png);}}
}

static class NativePet {
 [StructLayout(LayoutKind.Sequential)]struct PointNative{public int x,y;public PointNative(int a,int b){x=a;y=b;}}
 [StructLayout(LayoutKind.Sequential)]struct SizeNative{public int width,height;public SizeNative(int a,int b){width=a;height=b;}}
 [StructLayout(LayoutKind.Sequential,Pack=1)]struct Blend{public byte operation,flags,alpha,format;}
 [StructLayout(LayoutKind.Sequential)]struct Rect{public int left,top,right,bottom;}
 [DllImport("user32.dll")]static extern bool SetProcessDPIAware();[DllImport("user32.dll")]static extern bool SetProcessDpiAwarenessContext(IntPtr context);[DllImport("user32.dll")]static extern uint GetDpiForWindow(IntPtr hwnd);
 [DllImport("user32.dll")]static extern IntPtr GetDC(IntPtr h);[DllImport("user32.dll")]static extern int ReleaseDC(IntPtr h,IntPtr dc);[DllImport("gdi32.dll")]static extern IntPtr CreateCompatibleDC(IntPtr dc);[DllImport("gdi32.dll")]static extern bool DeleteDC(IntPtr dc);[DllImport("gdi32.dll")]static extern IntPtr SelectObject(IntPtr dc,IntPtr o);[DllImport("gdi32.dll")]static extern bool DeleteObject(IntPtr o);
 [DllImport("user32.dll",SetLastError=true)]static extern bool UpdateLayeredWindow(IntPtr h,IntPtr dest,IntPtr position,ref SizeNative size,IntPtr source,ref PointNative sourcePoint,int color,ref Blend blend,int flags);
 [DllImport("user32.dll")]static extern IntPtr GetForegroundWindow();[DllImport("user32.dll")]static extern bool GetWindowRect(IntPtr hwnd,out Rect rect);[DllImport("user32.dll")]static extern uint GetWindowThreadProcessId(IntPtr hwnd,out uint pid);[DllImport("user32.dll",CharSet=CharSet.Unicode)]static extern int GetClassName(IntPtr hwnd,StringBuilder name,int max);[DllImport("user32.dll")]static extern bool IsWindowVisible(IntPtr hwnd);[DllImport("user32.dll")]static extern bool ShowWindow(IntPtr h,int command);[DllImport("user32.dll")]static extern bool SetForegroundWindow(IntPtr h);delegate bool EnumProc(IntPtr h,IntPtr l);[DllImport("user32.dll")]static extern bool EnumWindows(EnumProc fn,IntPtr l);
 [DllImport("user32.dll")]static extern int GetWindowLong(IntPtr hwnd,int index);[DllImport("user32.dll")]static extern IntPtr SendMessage(IntPtr hwnd,int message,IntPtr wparam,IntPtr lparam);[DllImport("user32.dll")]static extern bool DestroyIcon(IntPtr icon);
 public static void ReleaseIcon(IntPtr icon){if(icon!=IntPtr.Zero)DestroyIcon(icon);}
 public static bool IsLayered(IntPtr hwnd){return (GetWindowLong(hwnd,-20)&0x80000)!=0;}
 public static int Hit(IntPtr hwnd,Point p){return SendMessage(hwnd,0x84,IntPtr.Zero,new IntPtr((p.Y<<16)|(p.X&65535))).ToInt32();}
 public static void DpiAware(){try{if(SetProcessDpiAwarenessContext(new IntPtr(-4)))return;}catch(EntryPointNotFoundException){}SetProcessDPIAware();}
 public static int Dpi(IntPtr h){try{return (int)GetDpiForWindow(h);}catch{return 96;}}
 public static void Layer(IntPtr hwnd,Bitmap bitmap,byte opacity){IntPtr desktop=GetDC(IntPtr.Zero),dc=CreateCompatibleDC(desktop),native=IntPtr.Zero,old=IntPtr.Zero;try{native=bitmap.GetHbitmap(Color.FromArgb(0));old=SelectObject(dc,native);var size=new SizeNative(bitmap.Width,bitmap.Height);var src=new PointNative(0,0);var blend=new Blend{operation=0,flags=0,alpha=opacity,format=1};if(!UpdateLayeredWindow(hwnd,desktop,IntPtr.Zero,ref size,dc,ref src,0,ref blend,2))throw new System.ComponentModel.Win32Exception();}finally{if(old!=IntPtr.Zero)SelectObject(dc,old);if(native!=IntPtr.Zero)DeleteObject(native);DeleteDC(dc);ReleaseDC(IntPtr.Zero,desktop);}}
 public static bool FullscreenOther(int owner){IntPtr h=GetForegroundWindow();if(h==IntPtr.Zero)return false;uint pid;GetWindowThreadProcessId(h,out pid);if(pid==owner||pid==Process.GetCurrentProcess().Id)return false;var cls=new StringBuilder(128);GetClassName(h,cls,128);string name=cls.ToString();if(name=="Progman"||name=="WorkerW"||name=="Shell_TrayWnd")return false;Rect r;if(!GetWindowRect(h,out r))return false;Rectangle monitor=Screen.FromHandle(h).Bounds;return r.left<=monitor.Left&&r.top<=monitor.Top&&r.right>=monitor.Right&&r.bottom>=monitor.Bottom;}
 public static void RaiseOwner(int owner){EnumWindows((h,l)=>{uint pid;GetWindowThreadProcessId(h,out pid);if(pid==(uint)owner&&IsWindowVisible(h)){ShowWindow(h,9);SetForegroundWindow(h);return false;}return true;},IntPtr.Zero);}
}

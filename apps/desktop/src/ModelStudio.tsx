import {ScreenCapture} from './ScreenCapture';
import {AccountConnections} from './AccountConnections';
import { useState } from 'react';
import { rpc } from './bridge';
import type { Snapshot } from './contracts';

type SmartLoadProfile = 'economy'|'balanced'|'performance'|'experimental';
type SmartLoadPlan = {
 model:{filename:string;bytes:number;architecture:string|null;quantization_id:number|null;kind:string};
 hardware:{ram_total_bytes:number|null;ram_available_bytes:number|null;disk_free_bytes:number|null;cpu_threads:number;vram_available_bytes:number|null};
 recommendation:{context:number;mode:string;gpu_layers:number};
 estimate:{ram_reserve_bytes:number|null;ram_budget_bytes:number|null;model_bytes:number;kv_cache_bytes:number|null};
 warnings:string[];limits_enforced:boolean;
};

export function ModelStudio({snapshot,refresh,lang}:{snapshot:Snapshot;refresh:()=>Promise<unknown>;lang:string}) {
 const [search,setSearch]=useState(''),[category,setCategory]=useState('all');
 const [mode,setMode]=useState(localStorage.getItem('rf-mode')||'cpu');
 const [layers,setLayers]=useState(12),[busy,setBusy]=useState(''),[error,setError]=useState(''),[path,setPath]=useState(''),[agree,setAgree]=useState(false),[selected,setSelected]=useState<Snapshot['models'][number]|null>(null);
 const [kvType,setKvType]=useState('f16');
 const [context,setContext]=useState(2048),[smartProfile,setSmartProfile]=useState<SmartLoadProfile>('balanced'),[smartPlan,setSmartPlan]=useState<SmartLoadPlan|null>(null),[smartModel,setSmartModel]=useState('');
 const tr=(ru:string,en:string)=>lang==='en'?en:ru;
 const size=(value:number|null|undefined)=>typeof value==='number'&&Number.isFinite(value)?(value/1024**3).toFixed(2)+' GiB':tr('Недоступно','Unavailable');
 const inspect=async(filename:string)=>{setSmartPlan(null);setSmartModel(filename);setBusy('smartload');setError('');try{setSmartPlan(await rpc<SmartLoadPlan>('smartload.plan',{filename,profile:smartProfile}));}catch(e){setError((e as Error).message)}finally{setBusy('')}};
 const run=async(key:string,fn:()=>Promise<unknown>)=>{setBusy(key);setError('');try{await fn();await refresh();}catch(e){setError((e as Error).message)}finally{setBusy('')}};
 const downloadStatus=()=>{
  const state=snapshot.installation;
  const labels:Record<string,string>={
   downloading:tr('Скачивание модели','Downloading model'),
   failed:tr('Ошибка скачивания. Нажмите «Скачать», чтобы повторить.','Download failed. Select Download to retry.'),
   cancelled:tr('Скачивание остановлено','Download stopped'),
   verified:tr('Файлы проверены','Files verified'),
  };
  return <div className="download-state">
   <progress aria-label={tr('Прогресс скачивания','Download progress')} value={state.downloaded} max={state.total||1}/>
   <span>{labels[state.state]} · {(state.downloaded/1024**2).toFixed(1)} / {(state.total/1024**2).toFixed(1)} MB</span>
   {state.error&&<p role="status">{state.error}</p>}
   {state.state==='downloading'&&<button onClick={()=>run('cancel-download',()=>rpc('model.install.cancel',{}))}>{tr('Остановить скачивание','Stop download')}</button>}
  </div>;
 };
 return <div className="page"><p className="eyebrow">ЛОКАЛЬНЫЙ ИИ</p><h1>{lang==='en'?'Models':'Модели'}</h1>
 <p className="muted">Начните с Qwen 1.5B. Для описания снимков добавьте SmolVLM 500M. Модели загружаются по очереди, чтобы экономить память.</p>
 <div className="hardware-grid"><div><span>RAM</span><b>{((snapshot.hardware.ram_bytes||0)/1024**3).toFixed(1)} ГБ</b></div><div><span>ВИДЕОКАРТА</span><b style={{fontSize:13}}>{snapshot.hardware.gpu}</b></div></div>
 <section className="model-card"><div><h2>Где выполнять вычисления</h2><select aria-label="Режим вычислений" value={mode} onChange={e=>{setMode(e.target.value);localStorage.setItem('rf-mode',e.target.value)}}>
 <option value="cpu">Процессор + оперативная память</option><option value="gpu" disabled={!snapshot.hardware.backends.includes('vulkan')}>Видеокарта · максимум слоёв</option><option value="hybrid" disabled={!snapshot.hardware.backends.includes('vulkan')}>Смешанный · видеокарта + RAM</option><option value="auto">Авто · GPU, при ошибке CPU/RAM</option></select>
 {mode==='hybrid'&&<label> Слоёв на GPU <input type="number" min={1} max={200} value={layers} onChange={e=>setLayers(Number(e.target.value))}/></label>}
 <label> {tr('Контекст','Context')} <select aria-label={tr('Длина контекста','Context length')} value={context} onChange={e=>setContext(Number(e.target.value))}><option value={512}>512</option><option value={1024}>1024</option><option value={2048}>2048</option><option value={4096}>4096</option><option value={8192}>8192</option></select></label>
 <label> KV cache <select aria-label={tr('Формат KV-кэша','KV cache format')} value={kvType} onChange={e=>setKvType(e.target.value)}><option value="f16">F16</option><option value="q8_0">Q8 (experimental)</option></select></label>
 <p className="muted">{tr('Q8 может уменьшить размер KV-кэша; влияние на качество и совместимость зависит от модели и backend. Проверено на небольшой модели CPU.','Q8 may reduce KV cache size; quality and compatibility depend on the model and backend. Tested on a small CPU model.')}</p>
 <p className="muted">{tr('Применяется при следующей загрузке. Длинный контекст требует больше памяти. GPU-режим не гарантирует, что вся модель поместится в VRAM.','Applies at the next load. A longer context requires more memory. GPU mode does not guarantee that the model fits in VRAM.')}</p></div></section>
 <section className="model-card" aria-label="Nexus SmartLoad" style={{marginTop:16}}><div style={{width:'100%',minWidth:0}}><h2>Nexus SmartLoad</h2>
 <p className="muted">{tr('Проверяет метаданные и оценивает доступные ресурсы перед загрузкой. Оценки не гарантируют успешный запуск и не ограничивают потребление памяти.','Inspects metadata and estimates resources before loading. Estimates neither guarantee startup nor enforce memory limits.')}</p>
 <label>{tr('Профиль','Profile')} <select aria-label={tr('Профиль SmartLoad','SmartLoad profile')} value={smartProfile} disabled={!!busy} onChange={e=>{setSmartProfile(e.target.value as SmartLoadProfile);setSmartPlan(null)}}><option value="economy">Economy</option><option value="balanced">Balanced</option><option value="performance">Performance</option><option value="experimental">Experimental SSD</option></select></label>
 <p className="muted">{tr('Для анализа нажмите SmartLoad у установленной модели. Чтение модели с SSD по требованию и управление экспертами не подтверждены этой оценкой.','Select SmartLoad on an installed model. This estimate does not establish on-demand SSD expert loading or expert management.')}</p>
 {smartModel&&busy==='smartload'&&<p role="status">{tr('Анализ модели…','Inspecting model…')}</p>}
 {smartPlan&&<div role="region" aria-label={tr('Результат SmartLoad','SmartLoad result')}>
 <p><b>{smartPlan.model.filename}</b> · {smartPlan.model.architecture||tr('архитектура неизвестна','unknown architecture')} · {smartPlan.model.kind||tr('тип неизвестен','unknown type')} · {size(smartPlan.model.bytes)}</p>
 <p>{tr('Доступно RAM','Available RAM')}: {size(smartPlan.hardware.ram_available_bytes)} / {size(smartPlan.hardware.ram_total_bytes)} · {tr('Доступно VRAM','Available VRAM')}: {size(smartPlan.hardware.vram_available_bytes)} · {tr('Свободно на диске','Free disk space')}: {size(smartPlan.hardware.disk_free_bytes)}</p>
 <p>{tr('Расчётный резерв RAM','Estimated RAM reserve')}: {size(smartPlan.estimate.ram_reserve_bytes)} · {tr('Оценка бюджета RAM','Estimated RAM budget')}: {size(smartPlan.estimate.ram_budget_bytes)} · {tr('Оценка F16 KV для CPU','F16 KV estimate for CPU')}: {size(smartPlan.estimate.kv_cache_bytes)}</p>
 <p>{tr('Рекомендация','Recommendation')}: {smartPlan.recommendation.mode.toUpperCase()} · {tr('контекст','context')} {smartPlan.recommendation.context}{smartPlan.recommendation.mode==='hybrid'?' · GPU layers '+smartPlan.recommendation.gpu_layers:''}</p>
 {smartPlan.warnings.length>0&&<ul>{smartPlan.warnings.map((warning,i)=><li key={i}>{warning}</li>)}</ul>}
 <button type="button" disabled={!!busy} onClick={()=>{setMode(smartPlan.recommendation.mode);setContext(smartPlan.recommendation.context);setLayers(smartPlan.recommendation.gpu_layers);localStorage.setItem('rf-mode',smartPlan.recommendation.mode)}}>{tr('Применить к настройкам загрузки','Apply load settings')}</button>
 <p className="muted">{tr('Рекомендация применяется к следующей загрузке. Режим SSD является исследовательским и не включает отдельный загрузчик экспертов.','The recommendation applies to the next load. SSD mode is research only and does not enable an expert loader.')}</p>
 </div>}</div></section>
 {error&&<p role="alert" className="error-banner">{error}</p>}

 <div className="agent-toolbar"><input aria-label="Поиск моделей" placeholder="Поиск: Qwen, Dolphin, Coder…" value={search} onChange={e=>setSearch(e.target.value)}/><select aria-label="Категория моделей" value={category} onChange={e=>setCategory(e.target.value)}><option value="all">Все модели ({snapshot.models.length})</option>{Array.from(new Set(snapshot.models.map(m=>m.category||'Свои'))).map(c=><option key={c} value={c}>{c}</option>)}</select></div>
 {snapshot.models.filter(m=>(category==='all'||(m.category||'Свои')===category)&&(m.name+' '+m.source).toLowerCase().includes(search.toLowerCase())).map(m=><section className="model-card" style={{marginTop:16}} key={m.id}><div style={{flex:1}}><h2>{m.name}</h2><p>{m.parameters} · {m.quantization} · {(m.bytes/1024**3).toFixed(2)} ГБ{m.projector?' + зрительный адаптер':''}</p><p className="muted">{m.source}</p><p className="muted">{m.category} · Оценка памяти: {(m.ram_estimate_bytes/1024**3).toFixed(1)} ГБ (зависит от контекста)</p>{m.compatibility&&<p className="muted">Архитектура поддерживается; качество и запуск конкретной модели требуют проверки.</p>}{m.license_url?<a href={m.license_url} target="_blank" rel="noreferrer">{m.license}</a>:<span>{m.license}</span>}
 {selected?.id===m.id&&<div role="dialog" aria-label={tr('Скачать модель','Download model')}>
  <p>{tr('Источник','Source')}: Hugging Face · {m.name}</p>
  <label className="consent"><input type="checkbox" checked={agree} onChange={e=>setAgree(e.target.checked)}/>{tr('Принимаю лицензию и разрешаю скачивание этой модели','I accept the license and authorize this download')}</label>
  <button disabled={!agree||!!busy||snapshot.installation.state==='downloading'} onClick={()=>{setSelected(null);void run(m.id,()=>rpc('model.install',{model_id:m.id,accept_license:true}));}}>{tr('Начать скачивание','Start download')}</button>
  <button onClick={()=>setSelected(null)}>{tr('Отмена','Cancel')}</button>
 </div>}
 {snapshot.installation.model_id===m.id&&downloadStatus()}
 </div>
 <div className="model-actions">{m.downloaded&&!m.projector&&<button type="button" disabled={!!busy} onClick={()=>void inspect(m.filename)}>SmartLoad</button>}{m.downloaded?<button disabled={!!busy} onClick={()=>run(m.id,()=>rpc(snapshot.runtime.model===m.filename?'model.stop':'model.start',snapshot.runtime.model===m.filename?{}:{filename:m.filename,mode,gpu_layers:layers,context,...(kvType==='q8_0'?{kv_type:kvType}:{})}))}>{busy===m.id?'Загрузка…':snapshot.runtime.model===m.filename?'Выгрузить':'Загрузить в память'}</button>:<button disabled={!!busy||snapshot.installation.state==='downloading'} onClick={()=>{setAgree(false);setSelected(m);}}>{tr('Скачать','Download')}</button>}</div></section>)}
 {!snapshot.installation.model_id&&snapshot.installation.state!=='idle'&&downloadStatus()}
 <section className="model-card" style={{marginTop:24}}><div style={{width:'100%'}}><h2>Добавить свою модель GGUF</h2><p className="muted">Вставьте полный путь к файлу на этом компьютере. Файл будет скопирован и проверен. Сейчас поддерживаются архитектуры llama и qwen2; импорт зрительного адаптера ещё не поддерживается.</p><input aria-label="Путь к GGUF" style={{width:'100%',margin:'12px 0'}} value={path} onChange={e=>setPath(e.target.value)} placeholder="C:\Models\my-model.gguf"/><button disabled={!!busy||!path} onClick={()=>run('import',()=>rpc('model.import',{source:path.replace(/^\"|\"$/g,'')}))}>{busy==='import'?'Проверка и копирование…':'Добавить модель'}</button></div></section>
 </div>
}

export function Connectors({lang=localStorage.getItem('rf-language')||'ru'}:{lang?:string}){
 const en=lang==='en',tr=(ru:string,english:string)=>en?english:ru;
 const [provider,setProvider]=useState('huggingface'),[query,setQuery]=useState(''),[items,setItems]=useState<{name:string;description:string;url:string}[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false),[searched,setSearched]=useState(false);
 return <div className="page connectors-page"><p className="eyebrow">NEXUS CONNECTIONS</p><h1>{tr('Центр коннекторов','Connector Center')}</h1><p className="muted">{tr('Локальные инструменты и ваши рабочие сервисы в одном каталоге.','Local tools and your working services in one catalog.')}</p><AccountConnections lang={lang}/>
 <section className="connector-public" aria-label={tr('Публичный поиск','Public search')}><h2>{tr('Поиск публичных ресурсов','Search public resources')}</h2><p className="muted">{tr('Официальные публичные API, без входа в аккаунт. Поиск запускается только по нажатию кнопки.','Official public APIs, without account sign-in. Searches run only when you press Search.')}</p><form className="connector-public-controls" onSubmit={async e=>{e.preventDefault();if(!query.trim()||busy)return;setBusy(true);setError('');setSearched(true);try{setItems(await rpc('connector.query',{provider,query}))}catch(e){setError((e as Error).message);setItems([])}finally{setBusy(false)}}}><select aria-label={tr('Сервис','Service')} value={provider} onChange={e=>{setProvider(e.target.value);setItems([]);setError('');setSearched(false)}}><option value="huggingface">Hugging Face · {tr('модели','models')}</option><option value="github">GitHub · {tr('репозитории','repositories')}</option><option value="huggingface_datasets">Hugging Face · {tr('датасеты','datasets')}</option><option value="huggingface_spaces">Hugging Face · Spaces</option><option value="gitlab">GitLab · {tr('публичные проекты','public projects')}</option><option value="wikipedia">Wikipedia · {tr('статьи на русском','Russian articles')}</option></select><input aria-label={tr('Поиск ресурсов','Resource search')} value={query} onChange={e=>setQuery(e.target.value)} placeholder={tr('Название или тема','Name or topic')}/><button disabled={!query.trim()||busy}>{busy?tr('Поиск…','Searching…'):tr('Найти','Search')}</button></form>{error&&<p role="alert" className="connector-notice error">{error}</p>}{!busy&&!error&&searched&&items.length===0&&<p className="muted">{tr('Нет результатов. Попробуйте другой запрос.','No results. Try another query.')}</p>}<div className="connector-public-results">{items.map(i=><article key={i.url}><a target="_blank" rel="noreferrer" href={/^https:\/\//i.test(i.url)?i.url:undefined}>{i.name}</a><p className="muted">{i.description}</p></article>)}</div></section></div>
}

export function VisionAttachment({models,onChange,allowWatch=false}:{models:Snapshot['models'];onChange:(image:string,model:string,watchToken?:string)=>void;allowWatch?:boolean}){
 const [model,setModel]=useState(''),[image,setImage]=useState(''),[error,setError]=useState(''),[watchToken,setWatchToken]=useState(''),[watchTitle,setWatchTitle]=useState(''),[watchSetup,setWatchSetup]=useState(false);
 const localVision=models.filter(m=>m.projector&&m.downloaded).sort((a,b)=>a.bytes-b.bytes);
 return <div style={{padding:'0 16px 12px',fontSize:11}}><select aria-label="Зрительная модель" value={model} onChange={e=>{setModel(e.target.value);setImage('');setWatchToken('');setWatchTitle('');onChange('',e.target.value)}}><option value="">Без анализа изображения</option>{models.filter(m=>m.projector&&m.downloaded).map(m=><option key={m.id} value={m.filename}>{m.name}</option>)}</select>{allowWatch&&<button disabled={!localVision.length} onClick={()=>{const candidate=localVision.find(m=>m.filename===model)||localVision[0];if(!candidate)return;setModel(candidate.filename);setImage('');setWatchToken('');setWatchSetup(true);onChange('',candidate.filename)}}>Подключить окно для ИИ</button>}{allowWatch&&!localVision.length&&<span> Установите локальную зрительную модель в разделе «Модели».</span>}{model&&<><label> Прикрепить снимок <input aria-label="Снимок экрана" type="file" accept="image/*" onChange={async e=>{const file=e.target.files?.[0];if(!file)return;setError('');try{if(file.size>20*1024*1024)throw Error('Максимум 20 МБ');const bitmap=await createImageBitmap(file);const canvas=document.createElement('canvas');const scale=Math.min(1,640/Math.max(bitmap.width,bitmap.height));canvas.width=bitmap.width*scale;canvas.height=bitmap.height*scale;canvas.getContext('2d')!.drawImage(bitmap,0,0,canvas.width,canvas.height);bitmap.close();const value=canvas.toDataURL('image/jpeg',.65);if(value.length>90000)throw Error('Снимок слишком подробный; обрежьте нужное окно');setImage(value);setWatchToken('');setWatchTitle('');onChange(value,model)}catch(e){setError((e as Error).message)}}}/></label><ScreenCapture autoOpen={watchSetup} onObserve={allowWatch?(token,title)=>{setImage('');setWatchToken(token);setWatchTitle(title);setWatchSetup(false);onChange('',model,token)}:undefined} onCapture={value=>{setImage(value);setWatchToken('');setWatchTitle('');onChange(value,model)}}/>{watchToken&&<p role="status">К задаче подключено окно: {watchTitle}. Наблюдение начнётся после отправки. <button onClick={()=>{setWatchToken('');setWatchTitle('');onChange(image,model)}}>Убрать наблюдение</button></p>}{image&&<><img src={image} alt="Предпросмотр разрешённого снимка" style={{maxWidth:'100%',maxHeight:180,display:'block',borderRadius:8,marginTop:8}}/><button onClick={()=>{setImage('');onChange('',model)}}>Убрать снимок</button></>}<p className="muted">Зрительная модель → описание → основная модель. Снимок передаётся только после отправки сообщения.</p></>}{error&&<p role="alert">{error}</p>}</div>
}

import {useEffect,useState} from 'react';
import {rpc} from './bridge';

type Repair={id?:string;state:string;message?:string;round?:number;max_rounds?:number;elapsed_seconds?:number;checks?:{tests:number;ok:boolean;output?:string}[]};
export function ProjectRepair(){
 const [files,setFiles]=useState<string[]>([]),[selected,setSelected]=useState<string[]>([]),[consent,setConsent]=useState(false),[rounds,setRounds]=useState('3'),[run,setRun]=useState<Repair>({state:'idle'}),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const active=run.state==='running';
 const [directory,setDirectory]=useState('');
 const visibleFiles=files.filter(p=>!directory||p.startsWith(directory+'/'));
 const refresh=async()=>{const r=await rpc<{files:string[]}>('workspace.list');setFiles(r.files.filter(p=>p.endsWith('.py')&&!/^(test|conftest\.py$|setup\.py$)/.test(p.split('/').pop()||'')))};
 useEffect(()=>{let disposed=false;let timer:ReturnType<typeof setTimeout>;
  const poll=async()=>{try{const r=await rpc<Repair>('coding.repair.status');if(!disposed)setRun(r)}catch(e){if(!disposed)setError((e as Error).message)}finally{if(!disposed)timer=setTimeout(poll,1200)}};
  void refresh().catch(e=>setError(e.message));void poll();return()=>{disposed=true;clearTimeout(timer)};
 },[]);
 const action=async(fn:()=>Promise<void>)=>{setBusy(true);setError('');try{await fn()}catch(e){setError((e as Error).message)}finally{setBusy(false)}};
 return <section className="agent-panel" aria-label="Автоматическое исправление проекта"><h2>Самостоятельное исправление проекта</h2>
 <p className="muted">Выберите до 8 Python-файлов (суммарно до 8000 символов). Локальная модель исправляет выбранные исходники и повторяет существующие test_*.py папки проекта. Перед запуском загрузите модель в разделе «Модели».</p>
 <label>Папка проекта<input aria-label="Папка автоматического исправления" placeholder="Например my_project; пусто — вся рабочая папка" value={directory} disabled={busy||active} onChange={e=>{setDirectory(e.target.value);setSelected([])}}/></label>
 <button disabled={busy||active} onClick={()=>action(refresh)}>Обновить список исходников</button>
 <fieldset disabled={busy||active}><legend>Разрешённые файлы</legend>{visibleFiles.length===0&&<p>Нет Python-исходников. Создайте проект и его тесты.</p>}{visibleFiles.map(path=><label key={path} style={{display:'block',overflowWrap:'anywhere'}}><input type="checkbox" checked={selected.includes(path)} onChange={e=>setSelected(old=>e.target.checked?[...old,path]:old.filter(p=>p!==path))}/>{path}</label>)}</fieldset>
 <label>Раунды исправления<select aria-label="Раунды исправления" value={rounds} disabled={active} onChange={e=>setRounds(e.target.value)}>{[1,2,3,4,5].map(n=><option key={n} value={n}>{n}</option>)}</select></label>
 <p className="muted">Тестовый процесс работает с правами вашего пользователя, без изоляции ОС. Запускайте только доверенные проекты. Исходники сохраняются для отката; новые изменения пользователя не перезаписываются.</p>
 <label><input type="checkbox" checked={consent} disabled={active} onChange={e=>setConsent(e.target.checked)}/>Разрешаю изменение выбранных файлов и выполнение тестов проекта</label>
 <div className="agent-toolbar"><button disabled={busy||active||!consent||selected.length===0||selected.length>8} onClick={()=>action(async()=>{setRun(await rpc<Repair>('coding.repair.start',{paths:selected,allow_execute:true,max_rounds:Number(rounds),directory}));setConsent(false)})}>Исправлять до прохождения тестов</button>
 <button disabled={busy||!active} onClick={()=>action(async()=>{await rpc('coding.repair.cancel')})}>Остановить исправление</button>
 <button disabled={busy||active||!run.id||run.state==='restored'} onClick={()=>action(async()=>{setRun(await rpc<Repair>('coding.repair.restore',{run_id:run.id}))})}>Восстановить исходники до задачи</button></div>
 {run.state!=='idle'&&<div role="status" data-testid="project-repair-status" data-state={run.state}><p>{({running:'Выполняется',passed:'Тесты прошли',failed:'Нужна проверка',cancelled:'Остановлено',interrupted:'Прервано',restored:'Восстановлено'} as Record<string,string>)[run.state]||run.state} · Раунд {run.round}/{run.max_rounds} · {run.elapsed_seconds??0} с</p><p>{run.message}</p>{run.checks?.map((check,i)=><details key={i}><summary>Проверка {i+1}: {check.tests} тестов — {check.ok?'пройдена':'не пройдена'}</summary><pre className="agent-answer">{check.output}</pre></details>)}</div>}
 {error&&<p role="alert">{error}</p>}</section>
}

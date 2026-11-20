import {useEffect,useState} from 'react';
import {rpc} from './bridge';
type Note={id:string;text:string;state:string;sha256:string};
type Memory={enabled:boolean;auto_capture?:boolean;notes:Note[];search_backend:string;character_limit:number};
export function MemoryPanel(){
 const [data,setData]=useState<Memory>({enabled:false,notes:[],search_backend:'',character_limit:12000}),[query,setQuery]=useState(''),[draft,setDraft]=useState(''),[editing,setEditing]=useState<Note|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[lesson,setLesson]=useState(''),[status,setStatus]=useState('');
 const refresh=async()=>setData(await rpc<Memory>('memory.list',{query}));
 useEffect(()=>{rpc<Memory>('memory.list').then(setData).catch(e=>setError(e.message))},[]);
 const action=async(work:()=>Promise<unknown>)=>{setBusy(true);setError('');try{await work();await refresh()}catch(e){setError((e as Error).message)}finally{setBusy(false)}};
 return <section className="agent-panel" aria-label="Память проекта"><h2>Общая память и исправления</h2>
  <p className="muted">Одна локальная память для чата, питомца и агентов: заметки сохраняются при смене модели. Это контекст и ваши предпочтения, а не обучение весов модели. Модель может предложить запись, но не может одобрить её или выдать себе разрешение. Не сохраняйте пароли или API-ключи.</p>
  <label style={{display:"flex",alignItems:"center",gap:8}}><input style={{width:"auto"}} type="checkbox" aria-label="Использовать память проекта" checked={data.enabled} disabled={busy} onChange={e=>{const enabled=e.target.checked,previous=data.enabled;setData(d=>({...d,enabled}));void action(async()=>{try{await rpc('memory.enable',{enabled})}catch(error){setData(d=>({...d,enabled:previous}));throw error}})}}/>Использовать одобренные заметки в контексте ИИ</label>
  <label style={{display:'flex',alignItems:'center',gap:8}}><input style={{width:'auto'}} type="checkbox" aria-label="Автоматически сохранять явные предпочтения" checked={!!data.auto_capture} disabled={busy} onChange={e=>{const auto_capture=e.target.checked,previous=data.auto_capture;setData(d=>({...d,auto_capture}));void action(async()=>{try{await rpc('memory.configure',{auto_capture})}catch(error){setData(d=>({...d,auto_capture:previous}));throw error}})}}/>Автоматически запоминать явные команды «запомни…» и «исправление: …»</label>
  <p className="muted">Автозапись работает только при включённой памяти и только для ваших явных фраз. Содержимое экрана, сайтов и ответы моделей автоматически не сохраняются. Распознаваемые секреты пропускаются; когда память заполнена, новые записи не вытесняют старые. Доступны просмотр, редактирование и удаление. Лимит: 80 заметок / 12 000 символов.</p>
  <label>Исправление для следующих задач<textarea aria-label="Исправление для памяти" maxLength={1400} value={lesson} onChange={e=>setLesson(e.target.value)} placeholder="Например: для Python используй unittest, а не запуск неподтверждённых команд."/></label>
  <button disabled={busy||!lesson.trim()} onClick={()=>action(async()=>{await rpc('memory.feedback',{text:lesson});setLesson('');setStatus('Исправление сохранено для всех моделей и агентов. Включите память, чтобы использовать его в новых задачах.')} )}>Сохранить исправление в общей памяти</button>
  <p className="muted">Исправления помогают следующим ответам учитывать замечания, но не гарантируют отсутствие повторных ошибок. Автоматическая отправка данных или новые права из памяти не разрешаются.</p>
  {status&&<p role="status">{status}</p>}
  {busy&&<p role="status">Сохранение и обновление памяти…</p>}
  <div className="agent-toolbar"><input aria-label="Поиск в памяти" value={query} maxLength={200} onChange={e=>setQuery(e.target.value)}/><button disabled={busy} onClick={()=>action(refresh)}>Найти заметки</button><button disabled={busy} onClick={()=>action(refresh)}>Обновить память</button></div>
  <label>{editing?'Изменить заметку':'Новая заметка'}<textarea aria-label="Текст заметки памяти" maxLength={1600} value={draft} onChange={e=>setDraft(e.target.value)}/></label>
  <button disabled={busy||!draft.trim()} onClick={()=>action(async()=>{await rpc('memory.save',{text:draft,...(editing?{note_id:editing.id,expected_sha256:editing.sha256}:{})});setDraft('');setEditing(null)})}>Сохранить заметку памяти</button>
  {editing&&<button onClick={()=>{setEditing(null);setDraft('')}}>Отменить редактирование</button>}
  <p className="muted">{data.search_backend} · лимит {data.character_limit} символов</p>
  {data.notes.map(note=><article key={note.id} className="agent-step"><p style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{note.text}</p><small>{note.state==='pending'?'Предложено ИИ — ожидает одобрения':'Одобрено пользователем'}</small><div className="agent-toolbar">
   {note.state==='pending'&&<button disabled={busy} onClick={()=>action(()=>rpc('memory.approve',{note_id:note.id,expected_sha256:note.sha256}))}>Одобрить заметку</button>}
   <button disabled={busy} onClick={()=>{setEditing(note);setDraft(note.text)}}>Изменить заметку</button>
   <button disabled={busy} onClick={()=>{if(window.confirm('Удалить эту заметку из локальной памяти?'))void action(()=>rpc('memory.remove',{note_id:note.id,expected_sha256:note.sha256}))}}>Удалить заметку</button>
  </div></article>)}
  {!data.notes.length&&<p className="muted">Заметок пока нет. Можно попросить ИИ «запомни…» и затем проверить предложение здесь.</p>}
  {error&&<p role="alert" className="error-banner">{error}</p>}
 </section>;
}

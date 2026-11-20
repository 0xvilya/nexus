import {useCallback, useEffect, useState} from 'react';
import {rpc} from './bridge';

type Skill = {
  id: string;
  run_id: string;
  text: string;
  sha256: string;
  state: string;
  tools: string[];
  scope: string;
};
type SkillList = {enabled: boolean; memory_enabled: boolean; limit: number; items: Skill[]; error?: string | null};

export function Skills({lang = 'ru'}: {lang?: string}) {
  const en = lang === 'en';
  const t = {
    title: en ? 'Skills from verified tasks' : 'Навыки из проверенных задач',
    intro: en ? 'Nexus can suggest a short workflow note after verifying saved source files or an observed task result. Approved notes join shared memory across chat, pets, agents and model changes.' : 'Nexus может предложить краткую памятку после сохранения проверенного файла или проверки результата задачи. Одобренные памятки поступают в общую память чата, питомца и агентов при смене модели.',
    enable: en ? 'Suggest skills from task results' : 'Предлагать навыки по результатам задач',
    detail: en ? 'Suggestions stay local and await your review. Each includes its source and verification scope. Enable shared memory to use approved notes. Suggestion limit' : 'Предложения сохраняются локально и ждут вашего просмотра. В памятке указаны источник и предел проверки. Для применения одобренных памяток включите общую память. Лимит предложений',
    disabled: en ? 'Shared memory is off. Approved skills will be used after you enable it.' : 'Общая память сейчас выключена. Одобренные навыки начнут использоваться после её включения.',
    refresh: en ? 'Refresh skills' : 'Обновить навыки',
    pending: en ? 'Suggestion awaiting review' : 'Предложение ожидает проверки',
    approved: en ? 'Approved for shared memory' : 'Одобрено для общей памяти',
    source: en ? 'Source and tools' : 'Источник и инструменты',
    task: en ? 'Task' : 'Задача',
    approve: en ? 'Approve skill' : 'Одобрить навык',
    remove: en ? 'Delete skill' : 'Удалить навык',
    confirm: en ? 'Delete this note from shared memory?' : 'Удалить эту памятку из общей памяти?',
    empty: en ? 'No notes yet. Enable suggestions and complete a task with a verifiable result.' : 'Памяток пока нет. Включите предложения и выполните задачу с проверяемым результатом.',
    busy: en ? 'Updating skills…' : 'Обновление навыков…',
  };
  const [data, setData] = useState<SkillList>({enabled: false, memory_enabled: false, limit: 8, items: []});
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');
  const refresh = useCallback(async () => {
    setData(await rpc<SkillList>('skills.list'));
    setLoaded(true);
  }, []);

  useEffect(() => {
    let active = true;
    rpc<SkillList>('skills.list').then(result => {if (active) {setData(result); setLoaded(true);}})
      .catch(reason => {if (active) setError(reason.message);});
    return () => {active = false;};
  }, []);

  const action = async (operation: () => Promise<unknown>, reload = true) => {
    setBusy(true);
    setError('');
    try {
      await operation();
      if (reload) await refresh();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const configure = async (enabled: boolean) => {
    if (!loaded || busy) return;
    const previous = data.enabled;
    setData(current => ({...current, enabled}));
    setBusy(true);
    setError('');
    try {
      setData(await rpc<SkillList>('skills.configure', {enabled}));
    } catch (reason) {
      setData(current => ({...current, enabled: previous}));
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return <section className="agent-panel" aria-label={en ? 'Nexus skills' : 'Навыки Nexus'}>
    <h2>{t.title}</h2>
    <p className="muted">{t.intro}</p>
    <label className="consent">
      <input type="checkbox" style={{width: 'auto'}} aria-label={t.enable}
        checked={data.enabled} disabled={busy || !loaded || !!data.error}
        onChange={event => void configure(event.target.checked)}/>
      {t.enable}
    </label>
    <p className="muted">{t.detail} — {data.limit}.</p>
    {!data.memory_enabled && <p role="status">{t.disabled}</p>}
    <button disabled={busy} onClick={() => void action(refresh, false)}>{t.refresh}</button>
    {data.items.map(skill => <article key={skill.id} className="agent-step">
      <small>{skill.state === 'approved' ? t.approved : t.pending}</small>
      <p style={{whiteSpace: 'pre-wrap', overflowWrap: 'anywhere'}}>{skill.text}</p>
      <details><summary>{t.source}</summary>
        <p style={{overflowWrap: 'anywhere'}}>{t.task} {skill.run_id}</p>
        <p>{skill.tools.join(' → ')}</p>
        <p>{skill.scope}</p>
      </details>
      <div className="agent-toolbar">
        {skill.state === 'pending' && <button disabled={busy} onClick={() => void action(() => rpc('skills.approve', {
          skill_id: skill.id, expected_sha256: skill.sha256,
        }))}>{t.approve}</button>}
        <button disabled={busy} onClick={() => {
          if (window.confirm(t.confirm)) void action(() => rpc('skills.remove', {
            skill_id: skill.id, expected_sha256: skill.sha256,
          }));
        }}>{t.remove}</button>
      </div>
    </article>)}
    {!data.items.length && <p className="muted">{t.empty}</p>}
    {busy && <p role="status">{t.busy}</p>}
    {(error || data.error) && <p role="alert" className="error-banner">{error || data.error}</p>}
  </section>;
}

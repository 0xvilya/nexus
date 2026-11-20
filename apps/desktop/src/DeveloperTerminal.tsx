import {useEffect, useState} from 'react';
import {rpc} from './bridge';

type CommandRun = {
 id: string;
 tool: string;
 args: string[];
 directory: string;
 state: string;
 output?: string;
 exit_code: number | null;
 elapsed_seconds: number;
 output_limited: boolean;
 error: string | null;
};

const active = (run?: CommandRun) => !!run && ['starting', 'running'].includes(run.state);

export function DeveloperTerminal({lang = 'ru'}: {lang?: 'ru' | 'en'}) {
 const en = lang === 'en';
 const [tool, setTool] = useState('python');
 const [argumentsText, setArgumentsText] = useState('["app.py"]');
 const [directory, setDirectory] = useState('');
 const [timeout, setTimeoutSeconds] = useState('300');
 const [consent, setConsent] = useState(false);
 const [runs, setRuns] = useState<CommandRun[]>([]);
 const [selected, setSelected] = useState('');
 const [busy, setBusy] = useState(false);
 const [error, setError] = useState('');
 const running = runs.find(active);
 const displayed = runs.find(run => run.id === selected) ?? runs[0];
 useEffect(() => {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout>;
  const poll = async () => {
   try {
    const result = await rpc<{runs: CommandRun[]}>('terminal.status');
    const requested = selected || result.runs[0]?.id;
    const detail = requested && result.runs.some(run => run.id === requested)
     ? await rpc<CommandRun>('terminal.status', {run_id: requested}) : undefined;
    if (!stopped) setRuns(result.runs.map(run => run.id === detail?.id ? detail : run));
   } catch (failure) {
    if (!stopped) setError((failure as Error).message);
   } finally {
    if (!stopped) timer = setTimeout(poll, 600);
   }
  };
  void poll();
  return () => {stopped = true; clearTimeout(timer);};
 }, [selected]);
 const start = async () => {
  if (busy || running || !consent) return;
  setError('');
  try {
   const args: unknown = JSON.parse(argumentsText);
   if (!Array.isArray(args) || args.some(argument => typeof argument !== 'string')) {
    throw new Error(en ? 'Arguments must be a JSON array of strings.' : 'Аргументы должны быть JSON-массивом строк.');
   }
   setBusy(true);
   const run = await rpc<CommandRun>('terminal.start', {
    tool, args, directory, timeout: Number(timeout), allow_execute: true,
   });
   setConsent(false);
   setSelected(run.id);
   setRuns(previous => [run, ...previous.filter(row => row.id !== run.id)].slice(0,20));
  } catch (failure) {
   setError((failure as Error).message);
  } finally {
   setBusy(false);
  }
 };
 return <section className="agent-panel" aria-label={en ? 'Developer console' : 'Консоль разработчика'}>
  <h2>{en ? 'Developer console' : 'Консоль разработчика'}</h2>
  <p className="muted">{en
   ? 'Run a trusted Python or Node.js workspace file, check JavaScript syntax, or inspect Git. Commands run with your Windows account permissions. Review files before execution.'
   : 'Запускайте доверенные Python- и Node.js-файлы рабочей папки, проверяйте JavaScript и просматривайте Git. Команды работают с правами вашего пользователя Windows. Проверьте исходники перед запуском.'}</p>
  <fieldset disabled={busy || !!running}>
   <legend>{en ? 'Reviewed command' : 'Команда для проверки'}</legend>
   <label>{en ? 'Tool' : 'Инструмент'}<select aria-label={en ? 'Console tool' : 'Инструмент консоли'} value={tool} onChange={event => {
    setTool(event.target.value);
    setArgumentsText(event.target.value === 'git' ? '["status", "--short"]' : event.target.value === 'node' ? '["app.js"]' : '["app.py"]');
    setConsent(false);
   }}><option value="python">Python</option><option value="node">Node.js</option><option value="git">Git</option></select></label>
   <label>{en ? 'Project folder' : 'Папка проекта'}<input value={directory} placeholder={en ? 'Empty means the coding workspace' : 'Пусто — рабочая папка кода'} onChange={event => {setDirectory(event.target.value); setConsent(false);}}/></label>
   <label>{en ? 'Arguments as JSON' : 'Аргументы в JSON'}<textarea aria-label={en ? 'Console arguments' : 'Аргументы консоли'} value={argumentsText} spellCheck={false} maxLength={8192} rows={2} onChange={event => {setArgumentsText(event.target.value); setConsent(false);}}/></label>
   <p className="muted">{en
    ? 'Python ["app.py", "value"] · Node ["--check", "app.js"] · Git ["diff"]. Each command has its own working directory. Output and the last 20 commands stay in this session.'
    : 'Python ["app.py", "значение"] · Node ["--check", "app.js"] · Git ["diff"]. Рабочая папка задаётся для каждой команды. Вывод и последние 20 команд хранятся в текущем сеансе.'}</p>
   <label>{en ? 'Timeout in seconds' : 'Ограничение выполнения, секунд'}<input type="number" min="1" max="3600" value={timeout} onChange={event => {setTimeoutSeconds(event.target.value); setConsent(false);}}/></label>
   <label className="consent"><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)}/>{en ? 'I reviewed this command and allow its execution' : 'Я проверил команду и разрешаю её выполнение'}</label>
  </fieldset>
  <div className="agent-toolbar"><button disabled={busy || !!running || !consent} onClick={() => void start()}>{en ? 'Run command' : 'Выполнить команду'}</button>
   <button disabled={busy || !running} onClick={() => {
    if (running) void rpc('terminal.cancel', {run_id: running.id}).catch(failure => setError(failure.message));
   }}>{en ? 'Stop command' : 'Остановить команду'}</button></div>
  {runs.length > 0 && <label>{en ? 'Session history' : 'История сеанса'}<select aria-label={en ? 'Console history' : 'История консоли'} value={displayed?.id ?? ''} onChange={event => setSelected(event.target.value)}>{runs.map(run => <option key={run.id} value={run.id}>{run.tool} {JSON.stringify(run.args)} · {run.state}</option>)}</select></label>}
  {displayed && <div role="status" data-testid="developer-command-result" data-state={displayed.state}>
   <p>{displayed.state} · {displayed.elapsed_seconds} s · {en ? 'Exit code' : 'Код завершения'} {displayed.exit_code ?? '—'}</p>
   <pre className="agent-answer" aria-label={en ? 'Command output' : 'Вывод команды'} style={{maxHeight:420, overflow:'auto', whiteSpace:'pre-wrap', overflowWrap:'anywhere'}}>{displayed.output || (en ? 'Waiting for output…' : 'Ожидание вывода…')}</pre>
   {displayed.output_limited && <p>{en ? 'The output limit was reached and the process stopped.' : 'Достигнут предел вывода. Процесс остановлен.'}</p>}
   {displayed.error && <p role="alert">{displayed.error}</p>}
  </div>}
  {error && <p role="alert">{error}</p>}
 </section>;
}

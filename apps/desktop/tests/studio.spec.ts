import { test, expect, chromium } from '@playwright/test';
import path from 'node:path';
import fs from 'node:fs/promises';

test('action conversation reopens in automatic mode and permission pause resumes without relaunch',async({page})=>{
 const thread='1'.repeat(32),old='2'.repeat(32),next='3'.repeat(32);let resumed=false,started=0;
 await page.addInitScript(()=>localStorage.setItem('rf-composer-mode','chat'));
 await page.route('**/api/rpc',async route=>{
  const body=route.request().postDataJSON();let result:any;
  if(body.method==='system.snapshot'){
   const original=(await (await route.fetch()).json()).result;
   result={...original,conversations:{items:[{id:thread,title:'Open fixture and inspect',messages:2}],has_more:false},action_tasks:[{id:old,conversation_id:thread,goal:'Open fixture and inspect',state:'awaiting_permission'}]};
  }else if(body.method==='conversation.read')result={id:thread,messages:[{seq:1,role:'user',text:'Open fixture and inspect',kind:'action',state:'completed'},{seq:2,role:'assistant',text:'Fixture window found',kind:'action',state:'completed'}],has_more:false,active_chat:null};
  else if(body.method==='task.status')result={id:body.params.run_id,conversation_id:thread,turn_seq:2,goal:'Open fixture and inspect',state:body.params.run_id===old?'awaiting_permission':'checks_passed',message:body.params.run_id===old?'Разрешите управление':'PATTERN READY',events:[],results:[]};
  else if(body.method==='task.resume'){expect(body.params).toEqual({run_id:old,reviewed:true,allow_interaction:true});resumed=true;result={id:next};}
  else if(body.method==='task.start'){started++;expect(body.params.allow_interaction).toBe(false);expect(body.params.conversation_id).toBe(thread);result={id:next};}
  else if(body.method==='chat.begin')throw new Error('Action conversation must not silently become text chat');
  else return route.continue();
  await route.fulfill({json:{jsonrpc:'2.0',id:body.id,result}});
 });
 await page.goto('/');await expect(page.getByText('Worker connected')).toBeVisible();
 await page.getByRole('button',{name:'Чат: Open fixture and inspect'}).click();
 await expect(page.getByRole('button',{name:'Авто',exact:true})).toHaveClass('chosen');
 await expect(page.getByRole('region',{name:'Разрешение на управление'})).toBeVisible();
 expect(resumed).toBe(false);expect(started).toBe(0);
 await page.screenshot({path:path.resolve(import.meta.dirname,'../../../work/control-permission-ui.png'),fullPage:true});
 await page.getByRole('button',{name:'Разрешить управление и продолжить',exact:true}).click();
 await expect(page.locator('.assistant-heading .pill')).toHaveText('checks_passed');expect(resumed).toBe(true);expect(started).toBe(0);
 await expect(page.getByLabel('Разрешить нажатия и ввод',{exact:true})).not.toBeChecked();
 await page.getByRole('textbox',{name:'Task prompt'}).fill('Inspect the fixture again');
 await page.getByRole('button',{name:'Выполнить',exact:true}).click();await expect.poll(()=>started).toBe(1);
});

test('conversation history loads older messages without dropping them on refresh',async({page})=>{
 const id='f'.repeat(32);
 await page.addInitScript(id=>localStorage.setItem('rf-conversation',id),id);
 await page.route('**/api/rpc',async route=>{
  const body=route.request().postDataJSON();if(body.method!=='conversation.read')return route.continue();
  const start=body.params.before?1:51;
  await route.fulfill({json:{jsonrpc:'2.0',id:body.id,result:{id,title:'Long conversation',messages:Array.from({length:50},(_,i)=>({seq:start+i,role:i%2?'assistant':'user',text:'History item '+(start+i),state:'completed',kind:'chat'})),has_more:start===51,active_chat:null}}});
 });
 await page.goto('/');const history=page.getByRole('region',{name:'История чата'});
 await expect(history.locator('article')).toHaveCount(50);
 await history.getByRole('button',{name:'Предыдущие сообщения',exact:true}).click();
 await expect(history.locator('article')).toHaveCount(100);
 await expect(history).toContainText('History item 1');await expect(history).toContainText('History item 100');
 await expect(history.getByRole('button',{name:'Предыдущие сообщения',exact:true})).toHaveCount(0);
});

test('one local conversation accepts multiple messages and survives reload',async({page,request})=>{
 test.setTimeout(90000);
 const {token}=await (await request.get('/api/bootstrap')).json();
 const call=async(method:string,params:Record<string,unknown>={})=>{
  const r=await (await request.post('/api/rpc',{data:{jsonrpc:'2.0',id:crypto.randomUUID(),method,params},headers:{Origin:'http://127.0.0.1:8845','X-RainForge-Token':token}})).json();expect(r.error).toBeUndefined();return r.result;
 };
 const model=(await call('system.snapshot')).models.find((m:any)=>m.downloaded&&!m.projector&&m.bytes<200000000);
 expect(model).toBeTruthy();await call('model.start',{filename:model.filename,mode:'cpu'});
 await page.goto('/');await page.getByRole('button',{name:'Локальный чат',exact:true}).click();
 for(const prompt of ['Reply with exactly ACK_ONE.','Reply with exactly ACK_TWO.']){
  await page.getByRole('textbox',{name:'Task prompt'}).fill(prompt);
  const beginning=page.waitForResponse(r=>r.url().endsWith('/api/rpc')&&r.request().postDataJSON()?.method==='chat.begin');
  await page.getByRole('button',{name:'Выполнить',exact:true}).click();const started=(await (await beginning).json()).result;
  expect(started).toBeTruthy();
  await expect.poll(async()=>((await call('conversation.read',{conversation_id:started.conversation_id})).messages.at(-1)?.state),{timeout:30000}).toBe('completed');
  await expect(page.locator('.assistant-heading .pill')).toHaveText('completed',{timeout:30000});
  await expect(page.getByRole('button',{name:'Остановить генерацию',exact:true})).toHaveCount(0,{timeout:30000});
 }
 const id=await page.evaluate(()=>localStorage.getItem('rf-conversation'));expect(id).toBeTruthy();
 const saved=await call('conversation.read',{conversation_id:id});expect(saved.messages).toHaveLength(4);
 await page.reload();const history=page.getByRole('region',{name:'История чата'});
 await expect(history).toContainText('ACK_ONE');await expect(history).toContainText('ACK_TWO');
 await expect(history.locator('article')).toHaveCount(4);
 await page.getByRole('button',{name:/Новый чат/}).click();
 await expect(history).toHaveCount(0);await page.getByRole('button',{name:'Чат: Reply with exactly ACK_ONE.',exact:true}).click();
 await expect(history.locator('article')).toHaveCount(4);await history.scrollIntoViewIfNeeded();
 await page.screenshot({path:path.resolve(import.meta.dirname,'../../../work/conversation-history-ui.png'),fullPage:true});
 await call('model.stop');
});

test('project repair requires file scope and execution consent, reconnects and restores',async({page})=>{
 let state='idle',started=0,restored=0;
 await page.route('**/api/rpc',async route=>{
  const body=route.request().postDataJSON();let result:unknown;
  const status=()=>({id:state==='idle'?undefined:'b'.repeat(32),state,round:1,max_rounds:3,elapsed_seconds:7,message:'Проверка проекта',checks:[{tests:2,ok:false,output:'AssertionError'}]});
  if(body.method==='workspace.list')result={files:['calc.py','other.py','test_calc.py']};
  else if(body.method==='coding.repair.status')result=status();
  else if(body.method==='coding.repair.start'){expect(body.params).toEqual({paths:['calc.py','other.py'],allow_execute:true,max_rounds:3,directory:''});started++;state='running';result=status();}
  else if(body.method==='coding.repair.cancel'){state='cancelled';result={state:'cancelling'};}
  else if(body.method==='coding.repair.restore'){expect(body.params.run_id).toBe('b'.repeat(32));restored++;state='restored';result=status();}
  else return route.continue();
  await route.fulfill({json:{jsonrpc:'2.0',id:body.id,result}});
 });
 await page.goto('/');await page.locator('nav').getByRole('button',{name:'Код',exact:true}).click();
 const panel=page.getByRole('region',{name:'Автоматическое исправление проекта'});
 const start=panel.getByRole('button',{name:'Исправлять до прохождения тестов',exact:true});await expect(start).toBeDisabled();
 await panel.getByLabel('calc.py',{exact:true}).check();await panel.getByLabel('other.py',{exact:true}).check();
 await expect(panel.getByLabel('test_calc.py',{exact:true})).toHaveCount(0);await expect(start).toBeDisabled();
 await panel.getByLabel('Разрешаю изменение выбранных файлов и выполнение тестов проекта',{exact:true}).check();await start.click();
 await expect(page.getByTestId('project-repair-status')).toHaveAttribute('data-state','running');expect(started).toBe(1);
 await page.reload();await page.locator('nav').getByRole('button',{name:'Код',exact:true}).click();
 await expect(page.getByTestId('project-repair-status')).toHaveAttribute('data-state','running');
 await panel.getByRole('button',{name:'Остановить исправление',exact:true}).click();
 await expect(page.getByTestId('project-repair-status')).toHaveAttribute('data-state','cancelled');
 await panel.getByRole('button',{name:'Восстановить исходники до задачи',exact:true}).click();
 await expect(page.getByTestId('project-repair-status')).toHaveAttribute('data-state','restored');expect(restored).toBe(1);
 await panel.scrollIntoViewIfNeeded();await page.screenshot({path:path.resolve(import.meta.dirname,'../../../work/project-repair-ui.png'),fullPage:true});
});

test('browser agent GUI runs a real 1.5B iterative controlled workflow', async ({page,request}) => {
  test.skip(process.env.RF_BROWSER_AGENT_MODEL_TEST!=='1','Real 1.5B browser-agent GUI check not requested');
  test.setTimeout(120000);
  const {token}=await (await request.get('/api/bootstrap')).json();
  const call=async(method:string,params:Record<string,unknown>={})=>{
    const response=await request.post('/api/rpc',{data:{jsonrpc:'2.0',id:crypto.randomUUID(),method,params},headers:{Origin:'http://127.0.0.1:8845','X-RainForge-Token':token}});
    const data=await response.json();expect(data.error).toBeUndefined();return data.result;
  };
  const models=(await call('system.snapshot')).models;
  const coder=models.find((m:any)=>m.downloaded&&m.filename.includes('coder')&&m.bytes>1000000000&&m.bytes<1500000000);
  expect(coder,'Provide RF_TEST_MODELS_DIR with installed Qwen Coder 1.5B').toBeTruthy();
  await call('model.start',{filename:coder.filename,mode:'auto',context:4096});
  await page.goto('/');
  await expect(page.getByText('Worker connected')).toBeVisible();
  await page.locator('nav').getByRole('button',{name:'Браузер',exact:true}).click();
  await page.getByLabel('Адрес браузера').fill('https://example.org');
  await page.getByRole('button',{name:'Открыть страницу',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Example Domain',exact:true})).toBeVisible({timeout:20000});
  const root=path.resolve(import.meta.dirname,'../../..');
  const data=process.env.RF_E2E_DATA_DIR || path.join(root,'work/e2e-data');
  const profiles=(await fs.readdir(data)).filter(name=>name.startsWith('browser-profile-'));
  const ports=await Promise.all(profiles.map(async name=>{const file=path.join(data,name,'DevToolsActivePort');try{return{file,stat:await fs.stat(file)}}catch{return null}}));
  const latest=ports.filter((item):item is NonNullable<typeof item>=>item!==null).sort((a,b)=>b.stat.mtimeMs-a.stat.mtimeMs)[0];
  const port=(await fs.readFile(latest.file,'utf-8')).split('\n')[0];
  const remote=await chromium.connectOverCDP('http://127.0.0.1:'+port);
  try{
    // Fixture provisioning only: all workflow decisions/actions below use the actual application/model.
    await remote.contexts()[0].pages()[0].setContent(await fs.readFile(path.join(root,'tests/fixtures/browser-agent.html'),'utf-8'));
    await page.getByLabel('Задание браузерному агенту').fill('Type RainForgeUI in Name, click Continue, then click Confirm.');
    await page.getByLabel('Селектор результата агента').fill('#result');
    await page.getByLabel('Результат браузерного агента').fill('Hello RainForgeUI');
    await page.getByLabel('Лимит шагов браузерного агента').selectOption('8');
    await page.getByLabel('Разрешить действия браузерного агента').check();
    await page.getByRole('button',{name:'Запустить браузерного агента',exact:true}).click();
    await expect(page.getByRole('button',{name:'Открыть страницу',exact:true})).toBeDisabled();
    await expect(page.getByTestId('browser-agent-status')).toHaveAttribute('data-state','checks_passed',{timeout:90000});
    await expect(page.getByTestId('browser-agent-proof')).toContainText('Hello RainForgeUI');
    expect(await page.locator('.browser-agent-panel ol li').count()).toBeGreaterThanOrEqual(3);
    await page.getByTestId('browser-agent-proof').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(root,'docs/evidence/browser-agent-ui.png'),fullPage:true});
    await page.reload();
    await page.locator('nav').getByRole('button',{name:'Браузер',exact:true}).click();
    await expect(page.getByTestId('browser-agent-proof')).toContainText('Hello RainForgeUI');
    await call('browser.close');
  }finally{await remote.close().catch(()=>{});await call('model.stop')}
});

test('Browser Studio verifies an explicit text condition and clears stale proof on failure', async ({page}) => {
  test.skip(process.env.RF_BROWSER_TEST!=='1','Live public browser check not requested');
  await page.goto('/');
  await expect(page.getByText('Worker connected')).toBeVisible();
  await page.locator('nav').getByRole('button',{name:'Браузер',exact:true}).click();
  await page.getByLabel('Адрес браузера').fill('https://example.org');
  await page.getByRole('button',{name:'Открыть страницу',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Example Domain',exact:true})).toBeVisible({timeout:20000});
  await page.getByLabel('Элемент страницы').fill('body');
  await page.getByLabel('Ожидаемый текст страницы').fill('Learn more');
  await page.getByRole('button',{name:'Проверить текст',exact:true}).click();
  await expect(page.getByTestId('browser-proof')).toContainText('Ожидаемый текст найден',{timeout:10000});
  await page.getByLabel('Ожидаемый текст страницы').fill('RainForge missing expected result');
  await expect(page.getByTestId('browser-proof')).not.toBeVisible();
  await page.getByRole('button',{name:'Проверить текст',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('condition timed out',{timeout:10000});
  await expect(page.getByTestId('browser-proof')).not.toBeVisible();
  await page.getByRole('button',{name:'Закрыть окно',exact:true}).click();
});

test('Hugging Face GUI downloads a real pinned public CSV and processes it', async ({page}) => {
  test.skip(process.env.RF_HF_DATASET_TEST!=='1','Live dataset network check not requested');
  await page.goto('/');
  await expect(page.getByText('Worker connected')).toBeVisible();
  await page.locator('nav').getByRole('button',{name:'Датасеты',exact:true}).click();
  await page.getByLabel('Репозиторий HF').fill('scikit-learn/iris');
  await page.getByRole('button',{name:'Показать файлы и лицензию'}).click();
  await expect(page.getByLabel('Файл HF')).toHaveValue('Iris.csv');
  await expect(page.getByRole('button',{name:'Скачать датасет',exact:true})).toBeDisabled();
  await page.getByLabel('Разрешить скачивание датасета').check();
  await page.getByRole('button',{name:'Скачать датасет',exact:true}).click();
  await expect(page.getByTestId('dataset-download-status')).toHaveText('COMPLETED',{timeout:25000});
  await expect(page.getByLabel('Лицензия датасета')).toHaveValue('cc0-1.0');
  await page.getByRole('button',{name:'Обработать датасет',exact:true}).click();
  await expect(page.getByTestId('dataset-status')).toHaveText('COMPLETED');
  await expect(page.getByTestId('dataset-kept')).toHaveText('150');
});

test('Dataset Studio uploads chunks, cleans, deduplicates, verifies and exports real data', async ({page}) => {
  await page.goto('/');
  await expect(page.getByText('Worker connected')).toBeVisible();
  await page.locator('nav').getByRole('button',{name:'Датасеты',exact:true}).click();
  const records=Array.from({length:100},(_,i)=>({text:'  '+('sample '.repeat(72))+i+'  ',index:i}));
  const data=Buffer.from([...records,records[0],{text:'  '},{email:'person@example.org'}].map(r=>JSON.stringify(r)).join('\n'));
  expect(data.length).toBeGreaterThan(32768);
  await page.getByLabel('Файл датасета',{exact:true}).setInputFiles({name:'ui-examples.jsonl',mimeType:'application/json',buffer:data});
  await expect(page.getByRole('status')).toContainText('Файл принят');
  await page.getByLabel('Лицензия датасета').fill('CC0-1.0');
  await page.getByLabel('Исключать записи с обнаруженными email').check();
  await page.getByRole('button',{name:'Обработать датасет',exact:true}).click();
  await expect(page.getByTestId('dataset-status')).toHaveText('COMPLETED');
  await expect(page.getByTestId('dataset-read')).toHaveText('103');
  await expect(page.getByTestId('dataset-kept')).toHaveText('100');
  await expect(page.getByTestId('dataset-duplicates')).toHaveText('1');
  await expect(page.getByTestId('dataset-email_excluded')).toHaveText('1');
  const pending=page.waitForEvent('download');
  await page.locator('.dataset-files>div').filter({hasText:'train.jsonl'}).getByRole('button',{name:'Сохранить копию'}).click();
  const download=await pending;
  const exported=await fs.readFile((await download.path())!,'utf-8');
  const rows=exported.trim().split('\n').map(line=>JSON.parse(line));
  expect(rows.length).toBeGreaterThan(50);
  expect(rows.every(row=>row.text===row.text.trim()&&!row.email)).toBe(true);
  await page.screenshot({path:path.resolve(import.meta.dirname,'../../../docs/evidence/dataset-studio.png'),fullPage:true});
  await page.locator('.page').evaluate(element=>{element.scrollTop=0});
  await page.screenshot({path:path.resolve(import.meta.dirname,'../../../docs/evidence/dataset-studio-source.png'),fullPage:true});
  await page.reload();
  await page.locator('nav').getByRole('button',{name:'Датасеты',exact:true}).click();
  await expect(page.getByTestId('dataset-status')).toHaveText('COMPLETED');
  await expect(page.getByTestId('dataset-kept')).toHaveText('100');
});

test('model catalog, compute modes, own GGUF and connectors are available', async ({page}) => {
  await page.goto('/');
  await expect(page.getByText('Worker connected')).toBeVisible();
  await page.locator('nav').getByRole('button',{name:'Модели',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Qwen 2.5 1.5B — основной помощник'})).toBeVisible();
  await expect(page.getByRole('heading',{name:'SmolVLM 500M — описание изображения'})).toBeVisible();
  await page.getByLabel('Режим вычислений').selectOption('hybrid');
  await expect(page.getByRole('spinbutton')).toHaveValue('12');
  await page.getByLabel('Путь к GGUF').fill('relative.gguf');
  await page.getByRole('button',{name:'Добавить модель',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('полный путь');
  await page.locator('nav').getByRole('button',{name:'Подключения'}).click();
  await expect(page.getByRole('heading',{name:'Центр коннекторов'})).toBeVisible();
  await page.getByLabel('Сервис',{exact:true}).selectOption('github');
  await expect(page.getByLabel('Поиск ресурсов')).toBeVisible();
});

test('creates, verifies, previews and restores a real artifact through the worker', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('/');
  await expect(page.getByText('Worker connected')).toBeVisible();
  await page.getByRole('button',{name:'Создать заметку',exact:true}).click();
  const note = `RainForge browser proof ${Date.now()}`;
  await page.getByRole('textbox', { name: 'Task prompt' }).fill(note);
  await page.getByRole('button', { name: 'Выполнить', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Проверено', exact: true })).toBeVisible();
  await expect(page.getByText('COMPLETED', { exact: true }).first()).toBeVisible();
  await page.locator('.task-result .artifact-card').click();
  const preview = page.getByRole('dialog', { name: 'Содержимое артефакта' });
  await expect(preview).toContainText(note);
  await expect(preview).toContainText('Проверено');
  await expect(preview.locator('code')).toHaveText(/^[a-f0-9]{64}$/);
  await page.getByRole('button', { name: 'Close preview' }).click();
  await page.reload();
  await page.locator('.history').getByRole('button', { name: note }).click();
  await expect(page.getByRole('heading', { name: 'Проверено', exact: true })).toBeVisible();
  await expect(page.locator('.task-result .artifact-card')).toBeVisible();
  await page.screenshot({ path: path.resolve(import.meta.dirname, '../../../..', 'rainforge-studio.png'), fullPage: true });
  expect(errors).toEqual([]);
});

test('keyboard palette, language and theme controls work', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('Worker connected')).toBeVisible();
  await page.keyboard.press('Control+k');
  await expect(page.getByRole('dialog', { name: 'Поиск задач и команд' })).toBeVisible();
  await page.getByPlaceholder('Поиск задач и команд').fill('Модели');
  await page.locator('.palette-items').getByRole('button', { name: 'Модели' }).click();
  await expect(page.getByRole('heading', { name: 'Модели', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Change language' }).click();
  await expect(page.getByRole('heading', { name: 'Models', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Toggle theme' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
});

test('loopback transport rejects missing credentials and hostile origins', async ({ request }) => {
  const message = { jsonrpc: '2.0', id: 'security', method: 'system.snapshot', params: {} };
  const unauthenticated = await request.post('/api/rpc', { data: message });
  expect(unauthenticated.status()).toBe(403);
  const bootstrap = await request.get('/api/bootstrap');
  const { token } = await bootstrap.json();
  const hostile = await request.post('/api/rpc', { data: message, headers: { Origin: 'https://evil.invalid', 'X-RainForge-Token': token } });
  expect(hostile.status()).toBe(403);
  const valid = await request.post('/api/rpc', { data: message, headers: { Origin: 'http://127.0.0.1:8845', 'X-RainForge-Token': token } });
  expect(valid.status()).toBe(200);
  expect((await valid.json()).result.version).toBe('0.1.0');
});

test('real local model loads and streams through the graphical controls', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('Worker connected')).toBeVisible();
  await page.locator('nav').getByRole('button', { name: 'Модели', exact: true }).click();
  await page.locator('.model-card').filter({hasText:'135M'}).getByRole('button', { name: 'Загрузить в память' }).click();
  await expect(page.getByRole('button', { name: 'Выгрузить', exact: true })).toBeVisible({ timeout: 15000 });
  await page.getByRole('button', { name: 'Новый чат' }).click();
  await page.getByRole('button', { name: 'Локальный чат', exact: true }).click();
  await page.getByRole('textbox', { name: 'Task prompt' }).fill('Write one short sentence about the moon.');
  await page.getByRole('button', { name: 'Выполнить', exact: true }).click();
  await expect(page.locator('.assistant-heading .pill')).toHaveText('completed', { timeout: 15000 });
  await expect(page.locator('.model-output')).not.toBeEmpty();
  await page.locator('nav').getByRole('button', { name: 'Модели', exact: true }).click();
  await page.getByRole('button', { name: 'Выгрузить', exact: true }).click();
  await expect(page.locator('.model-card').filter({hasText:'135M'}).getByRole('button', { name: 'Загрузить в память' })).toBeVisible();
});


test('download button opens explicit consent and sends installation request', async ({ page }) => {
  let install: any;
  await page.route('**/api/rpc', async route => {
    const body = route.request().postDataJSON();
    if (body.method === 'model.install') {
      install = body.params;
      await route.fulfill({json:{jsonrpc:'2.0',id:body.id,result:{state:'downloading'}}});
    } else if (body.method === 'system.snapshot' && install) {
      const response=await route.fetch();
      const snapshot=await response.json();
      snapshot.result.installation={state:'failed',model_id:install.model_id,downloaded:10,total:20,error:'Fixture CDN error'};
      await route.fulfill({response,json:snapshot});
    } else await route.continue();
  });
  await page.goto('/');
  await page.locator('nav').getByRole('button',{name:'Модели',exact:true}).click();
  const card=page.locator('.model-card').filter({has:page.getByRole('button',{name:'Скачать',exact:true})}).last();
  await card.getByRole('button',{name:'Скачать',exact:true}).click();
  const dialog=card.getByRole('dialog',{name:'Скачать модель'});
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('button',{name:'Начать скачивание'})).toBeDisabled();
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button',{name:'Начать скачивание'}).click();
  await expect.poll(()=>install?.accept_license).toBe(true);
  expect(install.model_id).toBeTruthy();
  await expect(card.getByRole('status')).toHaveText('Fixture CDN error');
  await expect(card.getByRole('button',{name:'Скачать',exact:true})).toBeEnabled();
});


test('agents persist and run two roles on the real local model', async ({page})=>{
 await page.goto('/');
 await page.locator('nav').getByRole('button',{name:'Модели',exact:true}).click();
 await page.locator('.model-card').filter({hasText:'135M'}).getByRole('button',{name:'Загрузить в память'}).click();
 await expect(page.getByRole('button',{name:'Выгрузить',exact:true})).toBeVisible({timeout:15000});
 await page.locator('nav').getByRole('button',{name:'Агенты',exact:true}).click();
 const suffix=Date.now();
 for(const role of ['Writer','Reviewer']){
  await page.getByLabel('Имя агента',{exact:true}).fill(role+suffix);
  await page.getByLabel('Инструкции агента',{exact:true}).fill('Reply with one short sentence.');
  await page.getByRole('button',{name:'Создать агента',exact:true}).click();
  await expect(page.locator('.agent-roster').getByText(role+suffix,{exact:true})).toBeVisible();
 }
 await page.getByLabel('Агент шага 1',{exact:true}).selectOption({label:'Writer'+suffix});
 await page.getByLabel('Задача шага 1',{exact:true}).fill('Say hello.');
 await page.getByRole('button',{name:'Добавить шаг',exact:true}).click();
 await page.getByLabel('Агент шага 2',{exact:true}).selectOption({label:'Reviewer'+suffix});
 await page.getByLabel('Задача шага 2',{exact:true}).fill('Summarize the earlier answer in five words.');
 await page.getByRole('button',{name:'Запустить команду',exact:true}).click();
 const currentRun=page.locator('section.agent-panel').filter({has:page.locator('summary',{hasText:'Writer'+suffix})});
 await expect(currentRun.getByText('completed · Ответы без независимой проверки')).toBeVisible({timeout:25000});
 const downloadEvent=page.waitForEvent('download');
 await currentRun.getByRole('button',{name:'Скачать JSON',exact:true}).click();
 const download=await downloadEvent;
 const fs=await import('node:fs/promises');
 const exported=JSON.parse(await fs.readFile((await download.path())!,'utf8'));
 expect(exported.state).toBe('completed');expect(exported.steps).toHaveLength(2);
 expect(exported.steps[0].text.length).toBeGreaterThan(0);
 await page.reload();
 await page.locator('nav').getByRole('button',{name:'Агенты',exact:true}).click();
 await expect(page.locator('.agent-roster').getByText('Writer'+suffix,{exact:true})).toBeVisible();
 await page.locator('nav').getByRole('button',{name:'Модели',exact:true}).click();
 await page.getByRole('button',{name:'Выгрузить',exact:true}).click();
});

test('expanded catalog filters Dolphin and exposes six connectors',async({page})=>{
 await page.goto('/');
 await page.locator('nav').getByRole('button',{name:'Модели',exact:true}).click();
 await page.getByLabel('Поиск моделей').fill('Dolphin');
 await expect(page.locator('.model-card h2').filter({hasText:'Dolphin'})).toHaveCount(2);
 await expect(page.getByRole('heading',{name:'Qwen 2.5 1.5B — основной помощник'})).toHaveCount(0);
 await page.getByLabel('Категория моделей').selectOption('Coding');
 await expect(page.locator('.model-card h2').filter({hasText:'Dolphin'})).toHaveCount(0);
 await page.getByLabel('Поиск моделей').fill('');
 await expect(page.locator('.model-card h2').filter({hasText:'Qwen2.5-Coder'})).toHaveCount(3);
 await page.locator('nav').getByRole('button',{name:'Подключения',exact:true}).click();
 await expect(page.getByLabel('Сервис',{exact:true}).locator('option')).toHaveCount(6);
});


test('agent role templates fill editable instructions',async({page})=>{
 await page.goto('/');
 await page.locator('nav').getByRole('button',{name:'Агенты',exact:true}).click();
 await page.getByLabel('Шаблон агента').selectOption('Ревьюер');
 await expect(page.getByLabel('Имя агента',{exact:true})).toHaveValue('Ревьюер');
 await expect(page.getByLabel('Инструкции агента')).toHaveValue(/регрессии/);
 await page.getByLabel('Имя агента',{exact:true}).fill('Мой ревьюер');
 await expect(page.getByRole('button',{name:'Создать агента',exact:true})).toBeEnabled();
});

test('approved project memory persists searches and can be disabled',async({page})=>{
 const note='Memory fixture '+Date.now()+' prefers Python';
 await page.goto('/');await page.locator('nav').getByRole('button',{name:'Агенты',exact:true}).click();
 await expect(page.getByLabel('Использовать память проекта')).not.toBeChecked();
 await page.getByLabel('Текст заметки памяти').fill(note);
 await page.getByRole('button',{name:'Сохранить заметку памяти',exact:true}).click();
 await expect(page.getByLabel('Память проекта').getByText(note,{exact:true})).toBeVisible();
 await page.getByLabel('Использовать память проекта').check();
 await expect(page.getByRole('button',{name:'Обновить память',exact:true})).toBeEnabled();
 await page.reload();await page.locator('nav').getByRole('button',{name:'Агенты',exact:true}).click();
 await expect(page.getByLabel('Использовать память проекта')).toBeChecked();
 await page.getByLabel('Поиск в памяти').fill('nonexistent_memory_query');await page.getByRole('button',{name:'Найти заметки',exact:true}).click();
 await expect(page.getByLabel('Память проекта').getByText(note,{exact:true})).toHaveCount(0);
 await page.getByLabel('Поиск в памяти').fill('Python');await page.getByRole('button',{name:'Найти заметки',exact:true}).click();
 await expect(page.getByLabel('Память проекта').getByText(note,{exact:true})).toBeVisible();
 await page.getByLabel('Использовать память проекта').uncheck();
 await expect(page.getByRole('button',{name:'Обновить память',exact:true})).toBeEnabled();
 await expect(page.getByLabel('Использовать память проекта')).not.toBeChecked();
});


test('code workspace creates, validates and protects files',async({page})=>{
 await page.goto('/');await page.locator('nav').getByRole('button',{name:'Код',exact:true}).click();
 await page.getByLabel('Файл проекта').fill('test-'+Date.now()+'.py');
 await page.getByLabel('Код файла').fill('def add(a,b):\n    return a+b\n');
 await page.getByRole('button',{name:'Сохранить файл',exact:true}).click();
 await expect(page.getByRole('status')).toHaveText('Файл сохранён с проверкой SHA-256');
 await page.getByRole('button',{name:'Проверить синтаксис',exact:true}).click();
 await expect(page.getByRole('status')).toContainText('Синтаксис корректен');
 await page.getByLabel('Код файла').fill('def broken(');
 await page.getByRole('button',{name:'Сохранить файл',exact:true}).click();
 await page.getByRole('button',{name:'Проверить синтаксис',exact:true}).click();
 await expect(page.getByRole('status')).not.toContainText('Синтаксис корректен');
});


test('automatic composer answers identity without a plan, tools or a visible budget',async({page})=>{
 const answer='Я Nexus — ИИ-ассистент. Могу отвечать на вопросы, помогать с программированием и выполнять задачи с помощью доступных инструментов';
 await page.goto('/');
 await expect(page.getByRole('button',{name:'Авто',exact:true})).toHaveClass('chosen');
 await expect(page.getByLabel('Лимит задачи')).toHaveCount(0);
 await page.getByRole('textbox',{name:'Task prompt'}).fill('Кто ты?');
 const started=page.waitForResponse(response=>response.request().postDataJSON()?.method==='task.start');
 const completed=page.waitForResponse(async response=>response.request().postDataJSON()?.method==='task.status'&&(await response.json()).result?.state==='answered');
 await page.getByRole('textbox',{name:'Task prompt'}).press('Enter');
 const params=(await started).request().postDataJSON().params;
 expect(params).toMatchObject({mode:'auto',allow_interaction:false});
 expect(params).not.toHaveProperty('budget');
 const {result}=await (await completed).json();
 expect(result).toMatchObject({route:'chat',planner_calls:0,tool_calls:0});
 expect(result.plan??[]).toEqual([]);
 expect(result.results).toEqual([]);
 expect(result.events).toEqual([]);
 await expect(page.locator('.model-output')).toHaveText(answer);
 await expect(page.getByLabel('Ход задачи')).toHaveCount(0);
 await expect(page.getByLabel('Время ответа')).toHaveCount(0);
 await page.reload();
 await expect(page.getByRole('region',{name:'История чата'})).toContainText(answer);
 await expect(page.getByRole('button',{name:'Авто',exact:true})).toHaveClass('chosen');
});

test('default composer routes browser action without creating a note',async({page})=>{
 let goal='';let notes=0;
 await page.route('**/api/rpc',async route=>{
  const body=route.request().postDataJSON();
  if(body.method==='goal.submit')notes++;
  if(body.method==='task.start'){expect(body.params.mode).toBe('auto');goal=body.params.goal;await route.fulfill({json:{jsonrpc:'2.0',id:body.id,result:{id:'a'.repeat(32)}}})}
  else if(body.method==='task.status'){await route.fulfill({json:{jsonrpc:'2.0',id:body.id,result:{id:'a'.repeat(32),state:'launch_requested',message:'Запрос запуска Chrome отправлен'}}})}
  else await route.continue();
 });
 await page.goto('/');
 await page.getByRole('textbox',{name:'Task prompt'}).fill('открой гугл браузер');
 await page.getByRole('button',{name:'Выполнить',exact:true}).click();
 await expect(page.locator('.model-output')).toHaveText('Запрос запуска Chrome отправлен');
 expect(goal).toBe('открой гугл браузер');expect(notes).toBe(0);
 await expect(page.getByRole('heading',{name:'Проверено',exact:true})).toHaveCount(0);
});

test('installed apps catalog searches refreshes and launches the selected identity',async({page})=>{
 let launched='';let refreshed=0;
 await page.route('**/api/rpc',async route=>{
  const body=route.request().postDataJSON();
  if(body.method==='computer.apps'){
   expect(body.params.refresh).toBe(true);refreshed++;
   await route.fulfill({json:{jsonrpc:'2.0',id:body.id,result:{apps:[{id:'installed:telegram-test',name:'Telegram'},{id:'installed:code-test',name:'Visual Studio Code'}]}}});
  }else if(body.method==='computer.launch'){
   launched=body.params.app;
   await route.fulfill({json:{jsonrpc:'2.0',id:body.id,result:{name:'Telegram',state:'launch_requested',verified_window:false}}});
  }else await route.continue();
 });
 await page.goto('/');await page.locator('nav').getByRole('button',{name:'Рабочий стол',exact:true}).click();
 await page.getByRole('button',{name:'Найти приложения',exact:true}).click();
 await expect(page.getByRole('button',{name:'Visual Studio Code',exact:true})).toBeVisible();
 await page.getByLabel('Поиск приложений').fill('tele');
 await expect(page.getByRole('button',{name:'Visual Studio Code',exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'Telegram',exact:true}).click();
 await expect(page.getByRole('status')).toContainText('Состояние окна не проверено');
 expect(launched).toBe('installed:telegram-test');
 await page.getByRole('button',{name:'Обновить приложения',exact:true}).click();
 expect(refreshed).toBe(2);
});

test('composer opens installed Telegram through the real application catalog',async({page})=>{
 test.setTimeout(90000);
 test.skip(process.platform!=='win32'||process.env.RF_APP_LAUNCH_TEST!=='1','Opt-in installed Telegram launch');
 await page.goto('/');
 await expect(page.getByText('Worker connected')).toBeVisible();
 await page.getByRole('textbox',{name:'Task prompt'}).fill('открой телеграм на моем компе');
 await page.getByRole('button',{name:'Выполнить',exact:true}).click();
 await expect(page.locator('.model-output')).toHaveText('Окно Telegram обнаружено.',{timeout:60000});
 await expect(page.getByRole('alert')).toHaveCount(0);
});

test('AI task progress code preview explicit desktop export and cancellation',async({page})=>{
 let exported=0,cancelled=0,mode='code';
 await page.route('**/api/rpc',async route=>{
  const body=route.request().postDataJSON();let result:unknown;
  if(body.method==='task.start'){expect(body.params.allow_interaction).toBe(false);result={id:'b'.repeat(32)};}
  else if(body.method==='task.status')result={id:'b'.repeat(32),state:mode==='code'?'file_created':'planning',events:[...Array.from({length:159},()=>({message:'ИИ планирует'})),{message:'Синтаксис проверен'}],message:'Создан calculator.py',results:[{path:'calculator.py',text:'def add(a,b): return a+b',syntax_verified:true}]};
  else if(body.method==='task.export_desktop'){exported++;result={path:'Desktop/calculator.py'};}
  else if(body.method==='task.cancel'){cancelled++;mode='code';result={requested:true};}
  else return route.continue();
  await route.fulfill({json:{jsonrpc:'2.0',id:body.id,result}});
 });
 await page.goto('/');
 await page.getByRole('textbox',{name:'Task prompt'}).fill('напиши калькулятор на python на рабочем столе');
 await page.getByRole('button',{name:'Выполнить',exact:true}).click();
 await expect(page.getByLabel('Ход задачи')).toContainText('Синтаксис проверен');
 await expect(page.getByText('ИИ планирует',{exact:true})).toHaveCount(0);
 expect(exported).toBe(0);
 await page.getByText('calculator.py · синтаксис проверен').click();
 await expect(page.getByLabel('Ход задачи')).toContainText('def add');
 await page.getByRole('button',{name:'Сохранить на рабочий стол'}).click();
 await expect(page.getByRole('status')).toContainText('Копия сохранена');expect(exported).toBe(1);
 mode='wait';await page.getByRole('textbox',{name:'Task prompt'}).fill('следующая задача');
 await page.getByRole('button',{name:'Выполнить',exact:true}).click();
 await page.getByRole('button',{name:'Остановить задачу',exact:true}).click();
 expect(cancelled).toBe(1);
});

test('screen capture requires selection consent and preview before model submission',async({page})=>{
 let captured=0,submitted=0;
 await page.route('**/api/rpc',async route=>{
  const body=route.request().postDataJSON();let result:unknown;
  if(body.method==='desktop.windows')result={windows:[{handle:42,title:'Owned fixture'}]};
  else if(body.method==='desktop.inspect'){expect(body.params.handle).toBe(42);result={token:'selected'};}
  else if(body.method==='desktop.capture'){expect(body.params.allow_capture).toBe(true);captured++;result={image:'data:image/jpeg;base64,/9j/2Q=='};}
  else if(body.method==='task.start'){submitted++;result={id:'c'.repeat(32)};}
  else return route.continue();
  await route.fulfill({json:{jsonrpc:'2.0',id:body.id,result}});
 });
 await page.goto('/');await page.getByLabel('Зрительная модель').selectOption({index:1});
 await page.getByText('Захват окна или монитора',{exact:true}).click();
 await expect(page.getByRole('button',{name:'Снять окно',exact:true})).toBeDisabled();
 await page.getByRole('button',{name:'Выбрать окно',exact:true}).click();
 await page.getByLabel('Окно для снимка').selectOption('42');
 await expect(page.getByRole('button',{name:'Снять окно',exact:true})).toBeDisabled();
 await page.getByLabel('Разрешить разовый снимок').check();
 await page.getByRole('button',{name:'Снять окно',exact:true}).click();
 await expect(page.getByAltText('Предпросмотр разрешённого снимка')).toBeVisible();
 expect(captured).toBe(1);expect(submitted).toBe(0);
 await expect(page.getByLabel('Разрешить разовый снимок')).not.toBeChecked();
});

test('project unittest execution requires consent and produces real results',async({page})=>{
 await page.goto('/');await page.locator('nav').getByRole('button',{name:'Код',exact:true}).click();
 await expect(page.getByRole('button',{name:'Запустить тесты',exact:true})).toBeDisabled();
 await page.getByLabel('Файл проекта').fill('test_rf_'+Date.now()+'.py');
 await page.getByLabel('Код файла').fill('import unittest\nclass T(unittest.TestCase):\n    def test_sum(self): self.assertEqual(2+2,4)\n');
 await page.getByRole('button',{name:'Сохранить файл',exact:true}).click();
 await expect(page.getByRole('status')).toContainText('Файл сохранён');
 await page.getByLabel('Разрешить выполнение тестов').check();
 await page.getByRole('button',{name:'Запустить тесты',exact:true}).click();
 await expect(page.locator('pre.agent-answer')).toContainText('"ok": true',{timeout:15000});
 await expect(page.locator('pre.agent-answer')).toContainText('test_sum');
});

test('desktop studio controls a real selected Windows test window',async({page})=>{
 test.skip(process.platform!=='win32','Windows UIA required');
 const fs=await import('node:fs/promises');const {spawn}=await import('node:child_process');
 const fixture=await fs.readFile(path.resolve(import.meta.dirname,'../../..','tests/fixtures/desktop-fixture.ps1'),'utf8');
 const proc=spawn('powershell.exe',['-NoProfile','-STA','-EncodedCommand',Buffer.from(fixture.replace(/^\uFEFF/,''),'utf16le').toString('base64')],{windowsHide:true});
 try{
  await page.goto('/');await page.locator('nav').getByRole('button',{name:'Рабочий стол',exact:true}).click();
  const fixtureWindow=page.getByRole('button',{name:`RainForge UIA fixture · PID ${proc.pid}`,exact:true});
  await expect.poll(async()=>{await page.getByRole('button',{name:'Найти окна',exact:true}).click();return fixtureWindow.count()},{timeout:15000}).toBe(1);
  await fixtureWindow.click();
  await page.getByLabel('Текст для приложения').fill('RainForge');
  await page.locator('.agent-step').filter({hasText:'nameBox'}).getByRole('button',{name:'Ввести текст',exact:true}).click();
  await page.locator('.agent-step').filter({hasText:'applyButton'}).getByRole('button',{name:'Нажать',exact:true}).click();
  await expect(page.getByText('Hello RainForge',{exact:true})).toBeVisible({timeout:10000});
 }finally{proc.kill()}
});



test('reviewed checkpoint continuation works without timer or budget controls',async({page})=>{
 let resumed=false,stop=false;
 await page.route('**/api/rpc',async route=>{
  const body=route.request().postDataJSON();let result:unknown;
  if(body.method==='task.start'){expect(body.params).not.toHaveProperty('budget');result={id:'d'.repeat(32)};}
  else if(body.method==='task.status')result={id:body.params.run_id,state:resumed&&!stop?'planning':'partial',elapsed_seconds:resumed?2:65,events:[{message:'Наблюдение сохранено'}],message:'Частичные результаты сохранены',results:[]};
  else if(body.method==='system.snapshot'&&resumed&&!stop){const original=await (await route.fetch()).json();result={...original.result,action_tasks:[{id:'e'.repeat(32),goal:'Проведи исследование в интернете',state:'planning'}]};}
  else if(body.method==='task.resume'){expect(body.params.reviewed).toBe(true);expect(body.params.allow_interaction).toBe(false);resumed=true;result={id:'e'.repeat(32)};}
  else if(body.method==='task.cancel'){stop=true;result={requested:true};}
  else return route.continue();
  await route.fulfill({json:{jsonrpc:'2.0',id:body.id,result}});
 });
 await page.goto('/');await expect(page.getByText('Worker connected')).toBeVisible();
 await expect(page.getByLabel('Лимит задачи',{exact:true})).toHaveCount(0);
 await page.getByRole('textbox',{name:'Task prompt'}).fill('Проведи исследование в интернете');
 await page.getByRole('button',{name:'Выполнить',exact:true}).click();
 await expect(page.getByLabel('Время ответа',{exact:true})).toHaveCount(0);
 const resume=page.getByRole('button',{name:'Продолжить с контрольной точки',exact:true});
 await expect(resume).toBeDisabled();await page.getByLabel('Я проверил выполненные шаги').check();
 await resume.click();await expect(page.getByRole('button',{name:'Остановить задачу',exact:true})).toBeVisible();
 await page.reload();await expect(page.getByRole('button',{name:'Остановить задачу',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Остановить задачу',exact:true}).click();
 await expect(page.getByRole('button',{name:'Остановить задачу',exact:true})).toHaveCount(0);
 await expect(page.getByLabel('Я проверил выполненные шаги')).not.toBeChecked();
});


test('task window observation requires a fresh grant and can be stopped globally',async({page})=>{
 let stopped=false,started=0;
 await page.route('**/api/rpc',async route=>{
  const body=route.request().postDataJSON();let result:unknown;
  if(body.method==='desktop.windows')result={windows:[{handle:42,title:'Owned watch fixture'}]};
  else if(body.method==='desktop.inspect')result={token:'a'.repeat(32)};
  else if(body.method==='task.start'){expect(body.params.watch_token).toBe('a'.repeat(32));expect(body.params.vision_filename).toBeTruthy();expect(body.params.image).toBeUndefined();started++;result={id:'c'.repeat(32)};}
  else if(body.method==='task.status')result={id:'c'.repeat(32),state:stopped?'answered':'planning',elapsed_seconds:3,message:'Наблюдение окна',events:[],screen_watch:{active:!stopped,frames:2,title:'Owned watch fixture'}};
  else if(body.method==='task.stop_observation'){stopped=true;result={active:false,frames:2,title:'Owned watch fixture'};}
  else return route.continue();
  await route.fulfill({json:{jsonrpc:'2.0',id:body.id,result}});
 });
 await page.goto('/');await page.getByRole('button',{name:'Подключить окно для ИИ',exact:true}).click();
 await expect(page.getByLabel('Зрительная модель')).not.toHaveValue('');
 await page.getByRole('button',{name:'Выбрать окно',exact:true}).click();await page.getByLabel('Окно для снимка').selectOption('42');
 const attach=page.getByRole('button',{name:'Подключить наблюдение к задаче',exact:true});await expect(attach).toBeDisabled();
 await page.getByLabel('Разрешить наблюдение окна',{exact:true}).check();await attach.click();
 await expect(page.getByRole('status')).toContainText('Наблюдение начнётся после отправки');expect(started).toBe(0);
 await page.getByRole('textbox',{name:'Task prompt'}).fill('Опиши выбранное окно');await page.getByRole('button',{name:'Выполнить',exact:true}).click();
 await expect(page.getByRole('button',{name:'Остановить наблюдение окна',exact:true})).toBeVisible();
 await page.screenshot({path:path.resolve(import.meta.dirname,'../../../work/window-watch-ui.png'),fullPage:true});
 await page.locator('nav').getByRole('button',{name:'Модели',exact:true}).click();
 await page.getByRole('button',{name:'Остановить наблюдение окна',exact:true}).click();
 await expect(page.getByRole('button',{name:'Остановить наблюдение окна',exact:true})).toHaveCount(0);expect(stopped).toBe(true);
});

test('monitor observation uses a separate selection grant and displays vision updates',async({page})=>{
 let selected=0,started=0,stopped=false;
 await page.route('**/api/rpc',async route=>{
  const body=route.request().postDataJSON();let result:unknown;
  if(body.method==='desktop.screens')result={screens:[{id:'test-monitor',title:'Test monitor',width:1920,height:1080}]};
  else if(body.method==='desktop.select_screen'){selected++;expect(body.params.screen).toBe('test-monitor');result={token:'d'.repeat(32)};}
  else if(body.method==='task.start'){started++;expect(body.params.watch_token).toBe('d'.repeat(32));result={id:'e'.repeat(32)};}
  else if(body.method==='task.status')result={id:'e'.repeat(32),state:stopped?'answered':'planning',screen_watch:{active:!stopped,frames:5,title:'Test monitor',vision:{active:!stopped,descriptions:3,analyzed_at:Date.now()/1000}}};
  else if(body.method==='task.stop_observation'){stopped=true;result={active:false,frames:5,title:'Test monitor'};}
  else return route.continue();
  await route.fulfill({json:{jsonrpc:'2.0',id:body.id,result}});
 });
 await page.goto('/');await page.getByRole('button',{name:'Подключить окно для ИИ',exact:true}).click();
 await page.getByRole('button',{name:'Выбрать монитор',exact:true}).click();await page.getByLabel('Монитор для наблюдения').selectOption('test-monitor');
 await page.screenshot({path:path.resolve(import.meta.dirname,'../../../work/monitor-observation-ui.png'),fullPage:true});
 await expect(page.getByRole('button',{name:'Подключить наблюдение к задаче',exact:true})).toBeDisabled();expect(selected).toBe(0);
 await page.getByLabel('Разрешить наблюдение окна',{exact:true}).check();await page.getByRole('button',{name:'Подключить наблюдение к задаче',exact:true}).click();expect(selected).toBe(1);expect(started).toBe(0);
 await page.getByRole('textbox',{name:'Task prompt'}).fill('Опиши экран');await page.getByRole('button',{name:'Выполнить',exact:true}).click();
 await expect(page.locator('footer')).toContainText('3 описаний');await page.getByRole('button',{name:'Остановить наблюдение окна',exact:true}).click();
 await expect(page.getByRole('button',{name:'Остановить наблюдение окна',exact:true})).toHaveCount(0);
});

test('project file previews and export show actual validation scope',async({page})=>{
 let exported=0;
 await page.route('**/api/rpc',async route=>{
  const body=route.request().postDataJSON();let result:unknown;
  if(body.method==='task.start')result={id:'a'.repeat(32)};
  else if(body.method==='task.status')result={id:'a'.repeat(32),state:'project_created',message:'Файлы созданы; запуск не проверен',project_plan:{directory:'example_a'},project_files:[{path:'example_a/app.js',sha256:'abc123',syntax:{ok:false,kind:'unsupported',message:'Not compiled'}}]};
  else if(body.method==='workspace.read')result={text:'console.log("reviewed draft");',sha256:'abc123'};
  else if(body.method==='task.export_desktop'){exported++;result={path:'Desktop/example_a'};}
  else return route.continue();
  await route.fulfill({json:{jsonrpc:'2.0',id:body.id,result}});
 });
 await page.goto('/');await page.getByRole('textbox',{name:'Task prompt'}).fill('Создай проект на JavaScript');await page.getByRole('button',{name:'Выполнить',exact:true}).click();
 await expect(page.getByLabel('Ход задачи')).toContainText('Синтаксис языка не проверен');expect(exported).toBe(0);await page.screenshot({path:path.resolve(import.meta.dirname,'../../../work/project-files-ui.png'),fullPage:true});
 await page.getByRole('button',{name:'Посмотреть файл',exact:true}).click();await expect(page.getByRole('dialog')).toContainText('reviewed draft');
 await page.getByRole('button',{name:'Close preview',exact:true}).click();await page.getByRole('button',{name:'Сохранить проект на рабочий стол',exact:true}).click();
 await expect(page.getByRole('status')).toContainText('Копия сохранена');expect(exported).toBe(1);
});

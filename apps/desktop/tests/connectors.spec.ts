import {test,expect} from '@playwright/test';
import path from 'node:path';

const names=[['local_filesystem','Local Filesystem','system','local'],['playwright','Playwright','system','local'],['web_search','Web Search','system','public'],['github','GitHub','development','token'],['huggingface','Hugging Face','development','token'],['docker','Docker','development','local'],['gitlab','GitLab','development','token'],['gmail','Gmail','work','token'],['google_drive','Google Drive','work','token'],['google_calendar','Google Calendar','work','token'],['outlook','Outlook','work','token'],['onedrive','OneDrive','work','token'],['google_docs','Google Docs / Sheets','work','token'],['notion','Notion','work','token'],['dropbox','Dropbox','work','token'],['telegram','Telegram','communication','token'],['discord','Discord','communication','token'],['slack','Slack','communication','token'],['todoist','Todoist','communication','token'],['trello','Trello','communication','token']];
const catalog=()=>names.map(([id,name,category,auth])=>({id,name,category,auth,hint:'User-owned account. Read-only API.',configured:false,capabilities:auth==='local'?['existing tool']:['read'],status:auth==='local'?'existing_tool':'not_connected',auth_url:'https://example.com/tokens',custom:false}));

test('connector center groups services, protects credentials and displays explicit reads',async({page})=>{
 let providers=catalog();const calls:any[]=[];
 await page.route('**/api/rpc',async route=>{const body=route.request().postDataJSON();let result:unknown;
  if(body.method==='connection.list')result=providers;
  else if(body.method==='connection.save'){calls.push(body);providers=providers.map(p=>p.id===body.params.provider?{...p,configured:true}:p);result={saved:true};}
  else if(body.method==='connection.test'){calls.push(body);result={ok:true,message:'Verified fixture account'};}
  else if(body.method==='connection.read'){calls.push(body);result={provider:body.params.provider,items:[{id:'fixture',name:'Private fixture document',description:'Only an explicit read returned this.',url:'https://example.com/document'}],read_only:true,limited:true};}
  else if(body.method==='connection.disconnect'){calls.push(body);providers=providers.map(p=>p.id===body.params.provider?{...p,configured:false}:p);result={disconnected:true};}
  else return route.continue();await route.fulfill({json:{jsonrpc:'2.0',id:body.id,result}});
 });
 await page.goto('/');await page.getByRole('button',{name:'Подключения',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Центр коннекторов',exact:true})).toBeVisible();
 for(const heading of ['Система и интернет','Разработка и ИИ','Работа и документы','Общение и задачи'])await expect(page.getByRole('heading',{name:heading,exact:false})).toBeVisible();
 expect(calls).toHaveLength(0);
 await page.getByRole('button',{name:/^Gmail/}).click();const detail=page.getByRole('region',{name:'Настройка подключения',exact:true});
 await expect(detail.getByRole('button',{name:'Проверить подключение',exact:true})).toBeDisabled();
 await detail.getByLabel('Токен подключения',{exact:true}).fill('fixture-owned-token');await detail.getByRole('button',{name:'Сохранить токен',exact:true}).click();
 await expect(detail.getByLabel('Токен подключения',{exact:true})).toHaveValue('');await expect(detail.getByRole('button',{name:'Проверить подключение',exact:true})).toBeEnabled();
 await detail.getByRole('button',{name:'Проверить подключение',exact:true}).click();await expect(page.getByRole('status')).toContainText('Verified fixture account');
 expect(calls.filter(c=>c.method==='connection.read')).toHaveLength(0);
 await detail.getByRole('button',{name:'Прочитать данные',exact:true}).click();await expect(detail.getByRole('region',{name:'Результаты коннектора'})).toContainText('Private fixture document');
 await detail.getByRole('button',{name:'Отключить',exact:true}).click();await expect(detail.getByRole('button',{name:'Проверить подключение',exact:true})).toBeDisabled();
 await page.getByLabel('Поиск коннекторов',{exact:true}).fill('Docker');await expect(page.locator('.connector-card')).toHaveCount(1);
 await page.setViewportSize({width:980,height:740});expect(await page.locator('.connectors-page').evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
 await page.getByLabel('Поиск коннекторов',{exact:true}).fill('');await detail.getByRole('button',{name:'Закрыть подключение',exact:true}).click();
 await page.locator('.connectors-page').evaluate(el=>el.scrollTop=0);await page.screenshot({path:path.resolve(import.meta.dirname,'../../../work/connectors-center-ui.png'),fullPage:true});
});

test('custom connector manifest validates JSON, saves and handles service rejection in English',async({page})=>{
 let providers:any[]=catalog(),saved:any;
 await page.addInitScript(()=>localStorage.setItem('rf-language','en'));
 await page.route('**/api/rpc',async route=>{const body=route.request().postDataJSON();let result:unknown;
  if(body.method==='connection.list')result=providers;
  else if(body.method==='connection.custom.save'){saved=body.params.manifest;providers.push({...saved,hint:'Custom HTTPS endpoint',auth:'token',configured:true,custom:true,capabilities:['read']});result={saved:true};}
  else if(body.method==='connection.test')result={ok:false,message:'HTTP 401: authorize your own account'};
  else if(body.method==='connection.custom.remove'){providers=providers.filter(p=>p.id!==body.params.provider);result={removed:true};}
  else return route.continue();await route.fulfill({json:{jsonrpc:'2.0',id:body.id,result}});
 });
 await page.goto('/');await page.getByRole('button',{name:'Connectors',exact:true}).click();await expect(page.getByRole('heading',{name:'Connector Center',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Custom connector',exact:true}).click();await page.getByLabel('Connector manifest',{exact:true}).fill('{broken');await page.getByRole('button',{name:'Add connector',exact:true}).click();await expect(page.getByRole('alert')).toBeVisible();expect(saved).toBeUndefined();
 const manifest={id:'custom-fixture',name:'Fixture Service',category:'work',endpoint:'https://example.com/api/items',items_path:'items',name_field:'name',description_field:'description',id_field:'id'};
 await page.getByLabel('Connector manifest',{exact:true}).fill(JSON.stringify(manifest));await page.getByRole('button',{name:'Add connector',exact:true}).click();await expect(page.getByRole('heading',{name:'Fixture Service',exact:true})).toBeVisible();expect(saved).toEqual(manifest);
 await page.getByRole('button',{name:'Test connection',exact:true}).click();await expect(page.getByRole('alert')).toContainText('HTTP 401');
 page.once('dialog',dialog=>dialog.accept());await page.getByRole('button',{name:'Remove connector',exact:true}).click();await expect(page.locator('.connector-card').filter({hasText:'Fixture Service'})).toHaveCount(0);
 await page.getByLabel('Connector category',{exact:true}).selectOption('communication');await expect(page.locator('.connector-card')).toHaveCount(5);
});

test('OAuth user-client flow shows scopes and callback, polls completion and cancels when leaving',async({page})=>{
 const providers=catalog().map(p=>({...p,oauth_supported:p.id==='google_drive'}));let begins=0,polls=0,cancelled=0,connected=false;
 await page.route('**/api/rpc',async route=>{const body=route.request().postDataJSON();let result:unknown;
  if(body.method==='connection.list')result=providers.map(p=>p.id==='google_drive'?{...p,configured:connected}:p);
  else if(body.method==='connection.oauth.begin'){begins++;expect(body.params.client_id).toBe('user-registered-client');result={session_id:'fixture-'+begins,authorization_url:'https://accounts.google.com/o/oauth2/v2/auth?scope=drive.metadata.readonly&state=fixture',redirect_uri:'http://127.0.0.1:54321/oauth/callback',scope:'drive.metadata.readonly',expires_in:600};}
  else if(body.method==='connection.oauth.status'){polls++;connected=true;result={status:'connected',message:'Own account authorized'};}
  else if(body.method==='connection.oauth.cancel'){cancelled++;result={status:'cancelled',message:'Cancelled'};}
  else return route.continue();await route.fulfill({json:{jsonrpc:'2.0',id:body.id,result}});
 });
 await page.goto('/');await page.getByRole('button',{name:'Подключения',exact:true}).click();await page.getByRole('button',{name:/^Google Drive/}).click();expect(begins).toBe(0);
 await page.getByLabel('OAuth client ID',{exact:true}).fill('user-registered-client');await page.getByRole('button',{name:'Подготовить вход OAuth',exact:true}).click();
 const signIn=page.getByRole('link',{name:'Открыть безопасную страницу входа',exact:false});await expect(signIn).toHaveAttribute('href',/^https:\/\/accounts.google.com/);await expect(signIn).toHaveAttribute('target','_blank');await expect(page.locator('.connector-oauth')).toContainText('http://127.0.0.1:54321/oauth/callback');await expect(page.locator('.connector-oauth')).toContainText('drive.metadata.readonly');
 await expect(page.locator('.connector-oauth')).toContainText('Own account authorized');expect(polls).toBeGreaterThan(0);
 await page.getByRole('button',{name:'Подготовить вход OAuth',exact:true}).click();await expect(signIn).toBeVisible();await page.getByRole('button',{name:'Настройки',exact:true}).click();await expect.poll(()=>cancelled).toBe(1);
});

test('actual worker catalog exposes requested services and preserves previous integrations',async({page})=>{
 await page.goto('/');await page.getByRole('button',{name:'Подключения',exact:true}).click();
 for(const name of ['Local Filesystem','Playwright','Web Search','GitHub','Hugging Face','Docker','GitLab','Gmail','Google Drive','Google Calendar','Outlook','OneDrive','Google Docs / Sheets','Notion','Dropbox','Telegram','Discord','Slack','Todoist','Trello','Airtable','HubSpot','Supabase','Vercel','Canva'])await expect(page.locator('.connector-card').filter({has:page.getByText(name,{exact:true})})).toHaveCount(1);
 await page.screenshot({path:path.resolve('../../work/connector-center.png'),fullPage:true});
 await page.getByRole('button',{name:/^Local Filesystem/}).click();await expect(page.getByRole('region',{name:'Настройка подключения',exact:true})).not.toContainText('Токен подключения');
});

test('connector read attaches reviewed reference data to existing chat draft without sending',async({page})=>{
 let reads=0,submitted=0;const providers=catalog().map(p=>({...p,configured:p.id==='github'}));
 await page.route('**/api/rpc',async route=>{const body=route.request().postDataJSON();let result:unknown;
  if(body.method==='connection.list')result=providers;
  else if(body.method==='connection.read'){reads++;result={provider:'github',items:[{id:'repo1',name:'My project',description:'Reference data: inspect tests before changing files.'}],read_only:true,limited:true};}
  else if(body.method==='chat.begin'||body.method==='task.start'){submitted++;throw Error('Attaching reference data must not submit a task');}
  else return route.continue();await route.fulfill({json:{jsonrpc:'2.0',id:body.id,result}});
 });
 await page.goto('/');const prompt=page.getByRole('textbox',{name:'Task prompt'});await prompt.fill('Help me review my project.');
 await page.getByRole('button',{name:'Подключения',exact:true}).click();await page.getByRole('button',{name:/^GitHub/}).click();
 await page.getByRole('button',{name:'Прочитать данные',exact:true}).click();await expect(page.getByRole('region',{name:'Результаты коннектора'})).toContainText('My project');expect(reads).toBe(1);expect(submitted).toBe(0);
 await page.getByRole('button',{name:'Обсудить в чате',exact:true}).click();await expect(prompt).toBeVisible();await expect(prompt).toHaveValue(/Help me review my project\./);await expect(prompt).toHaveValue(/недоверенные справочные данные/);await expect(prompt).toHaveValue(/My project/);await expect(page.getByRole('button',{name:'Локальный чат',exact:true})).toHaveClass('chosen');expect(submitted).toBe(0);
});

import {test,expect} from '@playwright/test';
import path from 'node:path';

const defaults={enabled:false,character:'nexus-bot',size:128,speed:1,opacity:1,always_on_top:true,hide_fullscreen:true,sounds:false,reactions:true,show_status:true,voice:false,fps:30,power_saving:true,start_with_nexus:true,start_with_windows:false,x:null,y:null,theme:'dark'};
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==','base64');

test('Nexus Pets preferences save, reload and display real event data without desktop claims',async({page})=>{
 let sequence=0,settings={...defaults},activity:any[]=[],updates=0,waitingRequests=0;
 const snapshot=()=>({sequence,settings,activity,packages:[],events:[]});
 await page.route('**/api/rpc',async route=>{
  const body=route.request().postDataJSON();let result:unknown;
  if(body.method==='pets.get')result=snapshot();
  else if(body.method==='pets.wait'){waitingRequests++;await new Promise(resolve=>setTimeout(resolve,350));result=snapshot();}
  else if(body.method==='pets.update'){updates++;settings={...settings,...body.params.settings};sequence++;result=snapshot();}
  else return route.continue();
  await route.fulfill({json:{jsonrpc:'2.0',id:body.id,result}}).catch(()=>{});
 });
 await page.goto('/');await page.getByRole('button',{name:'Настройки',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Nexus Pets',exact:true})).toBeVisible();
 await expect(page.getByText('Требуется приложение Windows',{exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Nexus Bot',exact:false})).toHaveAttribute('aria-pressed','true');
 await page.getByLabel('Включить питомца',{exact:true}).check();await page.getByRole('button',{name:'Cyber Fox',exact:false}).click();
 const size=page.getByRole('slider',{name:'Размер',exact:true});await size.press('Home');for(let i=0;i<12;i++)await size.press('ArrowRight');
 await page.getByRole('button',{name:'Сохранить настройки',exact:true}).click();
 await expect(page.locator('.pets-notice')).toHaveText('Настройки сохранены.');expect(settings.enabled).toBe(true);expect(settings.character).toBe('cyber-fox');expect(settings.size).toBe(160);expect(settings.start_with_windows).toBe(false);expect(updates).toBe(1);
 activity=[{id:'test:run',kind:'test',title:'Local fixture tests',state:'testing',event:'agent.testing',updated_at:1}];sequence++;
 await expect(page.getByRole('region',{name:'События Nexus'})).toContainText('Local fixture tests');
 await expect(page.getByRole('region',{name:'События Nexus'})).toContainText('agent.testing');
 await page.locator('.pets-page').evaluate(element=>element.scrollTop=0);
 await page.screenshot({path:path.resolve(import.meta.dirname,'../../../work/pets-settings-ui.png'),fullPage:true});
 await page.setViewportSize({width:980,height:740});expect(await page.locator('.pets-page').evaluate(element=>element.scrollWidth<=element.clientWidth)).toBe(true);
 await page.reload();await page.getByRole('button',{name:'Настройки',exact:true}).click();
 await expect(page.getByLabel('Включить питомца',{exact:true})).toBeChecked();await expect(page.getByRole('button',{name:'Cyber Fox',exact:false})).toHaveAttribute('aria-pressed','true');
 expect(waitingRequests).toBeLessThan(40);
});

test('Nexus Pets imports PNG through bounded upload, maps a state and confirms package removal',async({page})=>{
 const importedId='a'.repeat(32);let sequence=0,packages:any[]=[],upload:any=null,received=Buffer.alloc(0),removed=false;
 const snapshot=()=>({sequence,settings:{...defaults},activity:[],packages,events:[]});
 await page.route('**/api/rpc',async route=>{
  const body=route.request().postDataJSON();let result:unknown;
  if(body.method==='pets.get')result=snapshot();
  else if(body.method==='pets.wait'){await new Promise(resolve=>setTimeout(resolve,300));result=snapshot();}
  else if(body.method==='pets.upload.begin'){upload=body.params;received=Buffer.alloc(0);expect(upload.manifest.states.thinking.file).toBe('companion.png');result={upload_id:'b'.repeat(32)};}
  else if(body.method==='pets.upload.chunk'){expect(body.params.offset).toBe(received.length);const part=Buffer.from(body.params.data,'base64');expect(part.length).toBeLessThanOrEqual(32768);received=Buffer.concat([received,part]);result={offset:received.length};}
  else if(body.method==='pets.upload.finish'){expect(received.equals(png)).toBe(true);packages=[{id:importedId,name:upload.manifest.name,manifest:upload.manifest,license:'User supplied'}];sequence++;result=packages[0];}
  else if(body.method==='pets.remove'){expect(body.params.package_id).toBe(importedId);removed=true;packages=[];sequence++;result={removed:true};}
  else return route.continue();
  await route.fulfill({json:{jsonrpc:'2.0',id:body.id,result}}).catch(()=>{});
 });
 await page.goto('/');await page.getByRole('button',{name:'Настройки',exact:true}).click();
 await page.getByLabel('Название питомца',{exact:true}).fill('My pixel friend');
 await page.getByLabel('Изображения питомца',{exact:true}).setInputFiles({name:'companion.PNG',mimeType:'image/png',buffer:png});
 await page.getByText('Назначить изображения состояниям',{exact:true}).click();
 await page.getByLabel('thinking изображение',{exact:true}).selectOption('companion.png');
 await page.getByRole('button',{name:'Импортировать питомца',exact:true}).click();
 await expect(page.locator('.pets-notice')).toHaveText('Питомец добавлен: My pixel friend');
 await expect(page.getByLabel('Выбрать импортированного питомца')).toContainText('My pixel friend');
 await page.getByRole('button',{name:'Удалить пакет My pixel friend',exact:true}).click();expect(removed).toBe(false);
 await page.getByRole('button',{name:'Удалить пакет',exact:true}).click();await expect.poll(()=>removed).toBe(true);
 await expect(page.locator('.pets-notice')).toContainText('Исходные файлы сохранены');
});

test('Nexus Pets reports server errors and rejects invalid image before upload',async({page})=>{
 let uploads=0;
 await page.route('**/api/rpc',async route=>{
  const body=route.request().postDataJSON();let result:unknown;
  if(body.method==='pets.get'||body.method==='pets.wait'){
   if(body.method==='pets.wait')await new Promise(resolve=>setTimeout(resolve,300));
   result={sequence:0,settings:{...defaults},packages:[],activity:[],events:[]};
  }else if(body.method==='pets.update')return route.fulfill({json:{jsonrpc:'2.0',id:body.id,error:{message:'Settings file is read-only'}}});
  else if(body.method==='pets.upload.begin'){uploads++;result={upload_id:'c'.repeat(32)};}
  else return route.continue();
  await route.fulfill({json:{jsonrpc:'2.0',id:body.id,result}}).catch(()=>{});
 });
 await page.goto('/');await page.getByRole('button',{name:'Настройки',exact:true}).click();
 await page.getByLabel('Включить питомца',{exact:true}).check();await page.getByRole('button',{name:'Сохранить настройки',exact:true}).click();
 await expect(page.getByRole('alert')).toContainText('Settings file is read-only');
 await page.getByLabel('Изображения питомца',{exact:true}).setInputFiles({name:'broken.png',mimeType:'image/png',buffer:Buffer.from('not an image')});
 await expect(page.getByRole('alert')).toContainText('Image must be PNG');expect(uploads).toBe(0);
});

test('Nexus Pets combines PNG frames and installs them through the real worker',async({page})=>{
 await page.goto('/');await page.getByRole('button',{name:'Настройки',exact:true}).click();
 const frames=await page.evaluate(()=>['#aade88','#91c8ff'].map(color=>{const canvas=document.createElement('canvas');canvas.width=2;canvas.height=2;const context=canvas.getContext('2d')!;context.fillStyle=color;context.fillRect(0,0,2,2);return canvas.toDataURL('image/png').split(',')[1]}));
 await page.getByLabel('Название питомца',{exact:true}).fill('Sequence fixture');
 await page.getByLabel('Изображения питомца',{exact:true}).setInputFiles(frames.map((data,i)=>({name:'frame'+i+'.png',mimeType:'image/png',buffer:Buffer.from(data,'base64')})));
 await page.getByRole('button',{name:'Объединить PNG-кадры в анимацию',exact:true}).click();
 await expect(page.locator('.pets-notice')).toHaveText('PNG-кадры объединены в анимацию idle.');
 const finish=page.waitForResponse(response=>response.url().endsWith('/api/rpc')&&response.request().postDataJSON()?.method==='pets.upload.finish');
 await page.getByRole('button',{name:'Импортировать питомца',exact:true}).click();const result=(await (await finish).json()).result;
 expect(result).toBeTruthy();expect(result.manifest.frame_width).toBe(2);expect(result.manifest.frame_height).toBe(2);expect(result.manifest.states.idle.frames).toEqual([0,1]);
 await expect(page.locator('.pets-notice')).toHaveText('Питомец добавлен: Sequence fixture');
 await page.getByRole('button',{name:'Удалить пакет Sequence fixture',exact:true}).click();await page.getByRole('button',{name:'Удалить пакет',exact:true}).click();
 await expect(page.locator('.pets-notice')).toContainText('Исходные файлы сохранены');
});

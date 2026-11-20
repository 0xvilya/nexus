import {test,expect} from '@playwright/test';

test('text-detail screen capture remains source-bound and requires fresh explicit consent',async({page})=>{
 let captures=0,submitted=0;const captureParams:any[]=[];
 await page.route('**/api/rpc',async route=>{const body=route.request().postDataJSON();let result:unknown;
  if(body.method==='system.snapshot'){const original=(await(await route.fetch()).json()).result;result={...original,models:[{...original.models[0],id:'vision-fixture',filename:'vision-fixture.gguf',name:'Vision fixture',projector:'vision-projector.gguf',downloaded:true}]};}
  else if(body.method==='desktop.windows')result={windows:[{handle:42,title:'Owned screen-reading fixture'}]};
  else if(body.method==='desktop.inspect'){expect(body.params.handle).toBe(42);result={token:'selected-window'};}
  else if(body.method==='desktop.screens')result={screens:[{id:'monitor-fixture',title:'Owned monitor',width:1920,height:1080}]};
  else if(body.method==='desktop.select_screen'){expect(body.params.screen).toBe('monitor-fixture');result={token:'selected-monitor'};}
  else if(body.method==='desktop.capture'){captures++;captureParams.push(body.params);result={image:'data:image/jpeg;base64,/9j/2Q=='};}
  else if(body.method==='task.start'||body.method==='chat.begin'){submitted++;throw Error('Capture must not send a model request');}
  else return route.continue();await route.fulfill({json:{jsonrpc:'2.0',id:body.id,result}});
 });
 await page.goto('/');await page.getByLabel('Зрительная модель',{exact:true}).selectOption('vision-fixture.gguf');await page.getByText('Захват окна или монитора',{exact:true}).click();
 await page.getByLabel('Качество захвата',{exact:true}).selectOption('text');expect(captures).toBe(0);
 await page.getByRole('button',{name:'Выбрать окно',exact:true}).click();await page.getByLabel('Окно для снимка',{exact:true}).selectOption('42');await expect(page.getByRole('button',{name:'Снять окно',exact:true})).toBeDisabled();expect(captures).toBe(0);
 await page.getByLabel('Разрешить разовый снимок',{exact:true}).check();await page.getByRole('button',{name:'Снять окно',exact:true}).click();await expect(page.getByAltText('Предпросмотр разрешённого снимка',{exact:true})).toBeVisible();
 expect(captureParams).toEqual([{token:'selected-window',allow_capture:true,detail:'text'}]);await expect(page.getByLabel('Разрешить разовый снимок',{exact:true})).not.toBeChecked();await expect(page.getByRole('button',{name:'Снять окно',exact:true})).toBeDisabled();expect(submitted).toBe(0);
 await page.getByRole('button',{name:'Выбрать монитор',exact:true}).click();await page.getByLabel('Монитор для наблюдения',{exact:true}).selectOption('monitor-fixture');await expect(page.getByRole('button',{name:'Снять монитор',exact:true})).toBeDisabled();expect(captures).toBe(1);
 await page.getByLabel('Разрешить разовый снимок',{exact:true}).check();await page.getByRole('button',{name:'Снять монитор',exact:true}).click();await expect(page.getByLabel('Разрешить разовый снимок',{exact:true})).not.toBeChecked();expect(captureParams).toEqual([{token:'selected-window',allow_capture:true,detail:'text'},{token:'selected-monitor',allow_capture:true,detail:'text'}]);expect(submitted).toBe(0);
});

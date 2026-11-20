import { test, expect } from '@playwright/test';

test('SmartLoad displays estimates and applies settings only when requested', async ({page})=>{
 let started:any=null, plans=0;
 await page.route('**/api/rpc',async route=>{
  const body=route.request().postDataJSON();let result:any;
  if(body.method==='system.snapshot'){
   const original=(await (await route.fetch()).json()).result;
   result={...original,models:[{id:'test',name:'Fixture GGUF',filename:'fixture.gguf',parameters:'1B',quantization:'Q4',bytes:1073741824,license:'test',license_url:'',downloaded:true,ram_estimate_bytes:1073741824,source:'fixture'}]};
  }else if(body.method==='smartload.plan'){
   plans++;expect(body.params).toEqual({filename:'fixture.gguf',profile:'balanced'});
   result={model:{filename:'fixture.gguf',bytes:1073741824,architecture:'llama',quantization_id:2,kind:'dense'},hardware:{ram_total_bytes:17179869184,ram_available_bytes:8589934592,disk_free_bytes:21474836480,cpu_threads:4,vram_available_bytes:null},recommendation:{context:1024,mode:'cpu',gpu_layers:12},estimate:{ram_reserve_bytes:2147483648,ram_budget_bytes:6442450944,model_bytes:1073741824,kv_cache_bytes:null},warnings:['Estimated only'],limits_enforced:false};
  }else if(body.method==='model.start'){started=body.params;result={state:'ready'};}
  else return route.continue();
  await route.fulfill({json:{jsonrpc:'2.0',id:body.id,result}});
 });
 await page.goto('/');await page.locator('nav').getByRole('button',{name:'Модели',exact:true}).click();
 await page.getByRole('button',{name:'SmartLoad',exact:true}).click();
 const panel=page.getByRole('region',{name:'Результат SmartLoad'});
 await expect(panel).toContainText('fixture.gguf');await expect(panel).toContainText('Недоступно');
 await expect(panel).toContainText('Estimated only');expect(started).toBeNull();expect(plans).toBe(1);
 await expect(page.getByLabel('Длина контекста')).toHaveValue('2048');
 await panel.getByRole('button',{name:'Применить к настройкам загрузки'}).click();
 await expect(page.getByLabel('Длина контекста')).toHaveValue('1024');expect(started).toBeNull();
 await page.getByLabel('Формат KV-кэша').selectOption('q8_0');
 await page.getByRole('button',{name:'Загрузить в память',exact:true}).click();
 await expect.poll(()=>started).toEqual({filename:'fixture.gguf',mode:'cpu',gpu_layers:12,context:1024,kv_type:'q8_0'});
 await page.getByLabel('Профиль SmartLoad').selectOption('economy');await expect(panel).toHaveCount(0);
});

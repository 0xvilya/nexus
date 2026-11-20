import {test, expect} from '@playwright/test';

test('developer console executes reviewed workspace scripts and cancels a live command', async ({page}) => {
 await page.goto('/');
 const call = async (method: string, params: Record<string, unknown> = {}) => page.evaluate(async ({method,params}) => {
  const bootstrap = await (await fetch('/api/bootstrap')).json();
  const reply = await (await fetch('/api/rpc', {
   method:'POST', headers:{'Content-Type':'application/json','X-RainForge-Token':bootstrap.token},
   body:JSON.stringify({jsonrpc:'2.0',id:crypto.randomUUID(),method,params}),
  })).json();
  if (reply.error) throw new Error(reply.error.message);
  return reply.result;
 }, {method,params});
 await call('workspace.write',{path:'console_fixture.py',text:'import sys\nprint("console ready", sys.argv[1], flush=True)\nprint("stderr fixture", file=sys.stderr)\n'});
 await call('workspace.write',{path:'console_slow.py',text:'import time\nprint("live fixture", flush=True)\ntime.sleep(30)\n'});
 await page.getByRole('button',{name:'Консоль',exact:true}).click();
 const panel = page.getByRole('region',{name:'Консоль разработчика'});
 await panel.getByRole('textbox',{name:'Аргументы консоли',exact:true}).fill('["console_fixture.py", "two words"]');
 const run = panel.getByRole('button',{name:'Выполнить команду',exact:true});
 await expect(run).toBeDisabled();
 await panel.getByRole('checkbox').check();
 await run.click();
 const result = panel.getByTestId('developer-command-result');
 await expect(result).toHaveAttribute('data-state','completed');
 await expect(result).toContainText('console ready two words');
 await expect(result).toContainText('stderr fixture');
 await expect(run).toBeDisabled();
 await panel.getByRole('textbox',{name:'Аргументы консоли',exact:true}).fill('["console_slow.py"]');
 await panel.getByRole('checkbox').check();
 await run.click();
 await expect(result).toContainText('live fixture');
 await panel.getByRole('button',{name:'Остановить команду',exact:true}).click();
 await expect(result).toHaveAttribute('data-state','cancelled');
 await page.reload();
 await page.getByRole('button',{name:'Консоль',exact:true}).click();
 await expect(page.getByTestId('developer-command-result')).toHaveAttribute('data-state','cancelled');
});

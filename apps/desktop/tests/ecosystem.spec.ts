import {test, expect} from '@playwright/test';

test('RainCode handoff opens a bounded reviewed draft without launching a task', async ({page}) => {
  const starts: string[] = [];
  page.on('request', request => {
    if (request.method()==='POST' && request.url().endsWith('/api/rpc')) {
      const data=request.postDataJSON();
      if(data.method==='task.start'||data.method==='chat.begin')starts.push(data.method);
    }
  });
  await page.goto('/');
  await page.getByRole('button',{name:'Код',exact:true}).click();
  const packet=page.getByLabel('RainCode handoff packet');
  await packet.fill(JSON.stringify({format:'rainforge.handoff.v1',source:'raincode',task:'Объясни находку dynamic-eval.',context:'Findings, source excluded.'}));
  await page.getByRole('button',{name:'Проверить и открыть черновик'}).click();
  await expect(page.getByLabel('Task prompt')).toHaveValue(/dynamic-eval/);
  await expect(page.getByLabel('Разрешить нажатия и ввод')).not.toBeChecked();
  expect(starts).toEqual([]);
  await page.getByRole('button',{name:'Код',exact:true}).click();
  await packet.fill('{"format":"unknown"}');
  await page.getByRole('button',{name:'Проверить и открыть черновик'}).click();
  await expect(page.getByRole('alert')).toContainText('Неверный формат');
  expect(starts).toEqual([]);
});

test('Skills opt-in persists through page reload without granting task permissions', async ({page}) => {
  await page.goto('/');
  await page.getByRole('button',{name:'Навыки',exact:true}).click();
  const checkbox=page.getByRole('checkbox').first();
  await expect(checkbox).not.toBeChecked();
  await checkbox.check();
  await expect(checkbox).toBeChecked();
  await page.reload();
  await page.getByRole('button',{name:'Навыки',exact:true}).click();
  await expect(page.getByRole('checkbox').first()).toBeChecked();
  await page.getByRole('checkbox').first().uncheck();
  await expect(page.getByRole('checkbox').first()).toBeEnabled();
});

test('Skills waits for initial state and rolls back a rejected optimistic change', async ({page}) => {
  let releaseInitial: () => void = () => {};
  const initial = new Promise<void>(resolve => {releaseInitial = resolve;});
  let initialLoaded = false;
  let attempts = 0;
  await page.route('**/api/rpc', async route => {
    const request = route.request().postDataJSON();
    if (request.method === 'skills.list' && !initialLoaded) {
      await initial;
      initialLoaded = true;
      await route.fulfill({json: {
        jsonrpc: '2.0', id: request.id,
        result: {enabled: false, memory_enabled: false, items: [], limit: 8},
      }});
    } else if (request.method === 'skills.configure' && attempts++ === 0) {
      await new Promise(resolve => setTimeout(resolve, 300));
      await route.fulfill({json: {
        jsonrpc: '2.0', id: request.id,
        error: {code: -32000, message: 'Skill preferences could not be saved'},
      }});
    } else {
      await route.continue();
    }
  });
  await page.goto('/');
  await page.getByRole('button', {name: 'Навыки', exact: true}).click();
  const checkbox = page.getByRole('checkbox', {name: 'Предлагать навыки по результатам задач'});
  await expect(checkbox).toBeDisabled();
  releaseInitial();
  await expect(checkbox).toBeEnabled();
  await checkbox.check();
  await expect(checkbox).toBeChecked();
  await expect(checkbox).toBeDisabled();
  await expect(page.getByRole('alert')).toHaveText('Skill preferences could not be saved');
  await expect(checkbox).not.toBeChecked();
  await expect(checkbox).toBeEnabled();
  await checkbox.check();
  await expect(checkbox).toBeEnabled();
  await expect(checkbox).toBeChecked();
  await expect(page.getByRole('alert')).toHaveCount(0);
  await checkbox.uncheck();
  await expect(checkbox).toBeEnabled();
});


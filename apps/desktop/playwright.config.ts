import { defineConfig } from '@playwright/test';
import path from 'node:path';
import fs from 'node:fs';

const root = path.resolve(import.meta.dirname, '../..');
const venvPython = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const python = fs.existsSync(venvPython) ? venvPython : 'python';
const models = process.env.RF_TEST_MODELS_DIR ? ` --models-dir "${process.env.RF_TEST_MODELS_DIR}"` : '';
// A fresh test-only workspace per invocation prevents persisted fixture limits
// (e.g. 50 agents) from making later regression runs fail. Never reset user data.
const testData = process.env.RF_E2E_DATA_DIR || path.join(root,'work/e2e-runs',Date.now()+'-'+process.pid);
process.env.RF_E2E_DATA_DIR = testData;
export default defineConfig({
  testDir: './tests', workers: 1, fullyParallel: false, timeout: 30000,
  reporter: [['list'], ['json', { outputFile: '../../work/playwright-results.json' }]],
  outputDir: '../../work/playwright',
  use: { baseURL: 'http://127.0.0.1:8845', browserName: 'chromium', channel: 'msedge', viewport: { width: 1440, height: 1000 }, screenshot: 'only-on-failure' },
  webServer: {
    command: `"${python}" "${path.join(root, 'scripts/worker.py')}" --data-dir "${testData}" --runtime-dir "${path.join(root, 'runtime')}"${models} --http --assets "${path.join(root, 'apps/desktop/dist')}" --port 8845`,
    url: 'http://127.0.0.1:8845/api/bootstrap', reuseExistingServer: false, timeout: 15000,
  },
});

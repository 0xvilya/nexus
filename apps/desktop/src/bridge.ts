import { invoke, isTauri } from '@tauri-apps/api/core';

let token: Promise<string> | undefined;
export async function rpc<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  const message = { jsonrpc: '2.0', id: crypto.randomUUID(), method, params };
  let response: { result?: T; error?: { message: string } };
  if (isTauri()) {
    response = await invoke('rpc', { message });
  } else {
    token ??= fetch('/api/bootstrap').then(async response => {
      if (!response.ok) throw new Error('Worker is unavailable');
      return (await response.json()).token as string;
    }).catch(error => { token = undefined; throw error; });
    const result = await fetch('/api/rpc', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-RainForge-Token': await token }, body: JSON.stringify(message) });
    if (!result.ok) throw new Error(`Worker HTTP ${result.status}`);
    response = await result.json();
  }
  if (response.error) throw new Error(response.error.message);
  return response.result as T;
}

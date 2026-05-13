import type { BootstrapPayload, EngineSettings, ModelRecord, RemoteHubModel, RuntimeDiagnostics, RuntimeStatus } from './types';

const envApiBase = import.meta.env.VITE_API_BASE;
export const API_BASE = envApiBase === undefined ? 'http://127.0.0.1:8000' : String(envApiBase).replace(/\/$/, '');

function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

async function readJson<T>(response: Response): Promise<T> {
  const text = await response.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!response.ok) {
    if (data && typeof data === 'object' && 'detail' in (data as Record<string, unknown>)) {
      const detail = (data as Record<string, unknown>).detail;
      if (detail && typeof detail === 'object') {
        const detailObject = detail as Record<string, unknown>;
        const error = new Error(String(detailObject.message || JSON.stringify(detailObject))) as Error & Record<string, unknown>;
        Object.assign(error, detailObject);
        throw error;
      }
      throw new Error(String(detail));
    }
    throw new Error(typeof data === 'string' ? data : `HTTP ${response.status}`);
  }
  return data as T;
}

export async function fetchBootstrap(): Promise<BootstrapPayload> {
  return readJson<BootstrapPayload>(await fetch(apiUrl('/api/bootstrap')));
}

export async function uploadModel(formData: FormData): Promise<ModelRecord> {
  return readJson<ModelRecord>(await fetch(apiUrl('/api/models/upload'), { method: 'POST', body: formData }));
}

export async function registerModelPath(formData: FormData): Promise<ModelRecord> {
  return readJson<ModelRecord>(await fetch(apiUrl('/api/models/register-path'), { method: 'POST', body: formData }));
}

export async function validateModels(): Promise<ModelRecord[]> {
  return readJson<ModelRecord[]>(await fetch(apiUrl('/api/models/validate'), { method: 'POST' }));
}

export async function startModel(modelId: string): Promise<ModelRecord> {
  return readJson<ModelRecord>(await fetch(apiUrl(`/api/models/${encodeURIComponent(modelId)}/start`), { method: 'POST' }));
}

export async function prewarmModel(modelId: string, runtime?: Record<string, unknown>): Promise<ModelRecord> {
  return readJson<ModelRecord>(await fetch(apiUrl(`/api/models/${encodeURIComponent(modelId)}/prewarm`), {
    method: 'POST',
    headers: runtime ? { 'Content-Type': 'application/json' } : undefined,
    body: runtime ? JSON.stringify({ runtime }) : undefined,
  }));
}

export async function unloadModel(modelId: string): Promise<{ model_id: string; unloaded: boolean }> {
  return readJson(await fetch(apiUrl(`/api/models/${encodeURIComponent(modelId)}/unload`), { method: 'POST' }));
}

export async function deleteModel(modelId: string): Promise<void> {
  await readJson(await fetch(apiUrl(`/api/models/${encodeURIComponent(modelId)}`), { method: 'DELETE' }));
}

export async function sendChat(payload: {
  model_id: string;
  message: string;
  system_prompt: string;
  temperature: number;
  max_tokens: number;
  runtime?: Record<string, unknown>;
}): Promise<{
  answer: string;
  reasoning?: string;
  answer_state?: string;
  reasoning_truncated?: boolean;
  finish_reason?: string | null;
  elapsed_ms?: number;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    [key: string]: unknown;
  };
  model_id: string;
  request_id?: string;
  log_path?: string;
  log_excerpt?: string[];
  runtime?: {
    mode?: string;
    n_gpu_layers?: number;
    fallback_reason?: string;
    [key: string]: unknown;
  };
  gpu_snapshot?: Record<string, unknown>;
}> {
  return readJson(await fetch(apiUrl('/api/chat'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }));
}

export type ChatStreamEvent = {
  type: 'meta' | 'worker_status' | 'runtime' | 'delta' | 'done' | 'error' | 'log';
  text?: string;
  content?: string;
  message?: string;
  answer?: string;
  reasoning?: string;
  answer_state?: string;
  reasoning_truncated?: boolean;
  finish_reason?: string | null;
  elapsed_ms?: number;
  usage?: Record<string, unknown>;
  request_id?: string;
  log_path?: string;
  log_excerpt?: string[];
  mode?: string;
  runtime?: {
    mode?: string;
    n_gpu_layers?: number;
    fallback_reason?: string;
    [key: string]: unknown;
  };
};

export async function streamChat(
  payload: {
    model_id: string;
    message: string;
    system_prompt: string;
    temperature: number;
    max_tokens: number;
    runtime?: Record<string, unknown>;
  },
  onEvent: (event: ChatStreamEvent) => void,
): Promise<void> {
  const response = await fetch(apiUrl('/api/chat/stream'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok || !response.body) {
    await readJson(response);
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split('\n\n');
    buffer = frames.pop() || '';
    for (const frame of frames) {
      const line = frame.split('\n').find((item) => item.startsWith('data:'));
      if (!line) continue;
      const raw = line.slice(5).trim();
      if (!raw) continue;
      onEvent(JSON.parse(raw) as ChatStreamEvent);
    }
  }
}

export async function saveSettings(payload: EngineSettings): Promise<EngineSettings> {
  return readJson(await fetch(apiUrl('/api/settings'), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }));
}

export async function uploadLogo(file: File): Promise<EngineSettings> {
  const form = new FormData();
  form.append('logo_file', file);
  return readJson(await fetch(apiUrl('/api/settings/logo'), { method: 'POST', body: form }));
}

export async function fetchHubModels(): Promise<RemoteHubModel[]> {
  return readJson(await fetch(apiUrl('/api/hub/models')));
}

export async function importHubModel(payload: { model_id: string; name: string }): Promise<ModelRecord> {
  return readJson(await fetch(apiUrl('/api/hub/import'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }));
}

export async function fetchRuntimeStatus(): Promise<RuntimeStatus[]> {
  return readJson(await fetch(apiUrl('/api/runtime/status')));
}

export async function fetchRuntimeDiagnostics(): Promise<RuntimeDiagnostics> {
  return readJson(await fetch(apiUrl('/api/runtime/diagnostics')));
}

export async function unloadAllRuntimes(): Promise<{ unloaded: number }> {
  return readJson(await fetch(apiUrl('/api/runtime/unload-all'), { method: 'POST' }));
}

export async function openLog(path?: string): Promise<{ opened?: string; path?: string }> {
  return readJson(await fetch(apiUrl('/api/logs/open'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(path ? { path } : {}),
  }));
}

export async function setAssistantOnTop(enabled: boolean): Promise<{ enabled: boolean }> {
  return readJson(await fetch(apiUrl('/api/desktop/assistant/on-top'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  }));
}

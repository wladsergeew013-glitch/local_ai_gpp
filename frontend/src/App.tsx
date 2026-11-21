import { changedRuntime, clearSubmittedDraft } from './settingsPatch';
import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { API_BASE, fetchBootstrap, fetchRuntimeStatus, prewarmModel, registerModelPath, saveRuntimePatch, unloadModel, uploadModel } from './api';
import type { ChatMessage, EngineSettings, ModelRecord, RuntimeSettings, RuntimeStatus } from './types';
import { APP_VERSION } from './version';
import { contextSettingsError, estimateContext } from './contextBudget';
import { messageError, requestHistory } from './chatCompletion';
import { acceptsRemote, fresh, initialState, normalizeState, stateStatus, STORE } from './chatState';
import type { ChatState } from './chatState';
import RuntimeSettingsDialog, { modelOperationText, modelStateText } from './RuntimeSettingsDialog';
import type { Feedback, ModelOperation, ModelSource, SettingsSection } from './RuntimeSettingsDialog';

const MARKER = 'V67_4_DESKTOP_SYNC_MULTI_CONVERSATION';
const url = (path: string) => `${API_BASE}${path}`;
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
function messageDate(message: ChatMessage): Date | null {
  const value = message.createdAt || message.syncUpdatedAt;
  if (!value) return null;
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date : null;
}
const messageTime = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
});

function GenerationTimer({ message, loading }: { message: ChatMessage; loading: boolean }) {
  const started = messageDate(message)?.getTime();
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return <span>{loading ? 'Загрузка весов' : message.phase === 'typing' ? 'Генерация ответа' : 'Подготовка ответа'}
    {started !== undefined && ` · ${Math.max(0, Math.floor((now - started) / 1000))} с`}</span>;
}

async function jsonRequest(path: string, body?: unknown, method = 'POST') {
  const response = await fetch(url(path), { method: body === undefined && method === 'GET' ? 'GET' : method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(10000) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || data.detail?.message || data.detail || `HTTP ${response.status}`);
  return data;
}

export default function App() {
  const [chat, setChat] = useState<ChatState>(() => initialState());
  const [models, setModels] = useState<ModelRecord[]>([]);
  const [settings, setSettings] = useState<EngineSettings | null>(null);
  const [runtime, setRuntime] = useState<RuntimeStatus[]>([]);
  const [modelId, setModelId] = useState('');
  const [input, setInput] = useState('');
  const [status, setStatus] = useState('');
  const [sending, setSending] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [syncReady, setSyncReady] = useState(false);
  const [networkErrors, setNetworkErrors] = useState<Record<string, string>>({});
  const [settingsSection, setSettingsSection] = useState<SettingsSection | null>(null);
  const [saving, setSaving] = useState(false);
  const [settingsFeedback, setSettingsFeedback] = useState<Feedback | null>(null);
  const [modelOperation, setModelOperation] = useState<ModelOperation | null>(null);
  const [modelFeedback, setModelFeedback] = useState<Feedback | null>(null);
  const [memory, setMemory] = useState(true);
  const [preferredGpuLayers, setPreferredGpuLayers] = useState<number | null>(null);
  const [systemPrompt, setSystemPrompt] = useState('Ты полезный помощник. Отвечай кратко и по делу.');
  const [uploadLimit, setUploadLimit] = useState(16 * 1024 ** 3);
  const uploadController = useRef<AbortController | null>(null);
  const chatRef = useRef(chat);
  const scrollRef = useRef<HTMLDivElement>(null);
  const followLatest = useRef(true);
  const scrollConversation = useRef('');
  const remoteState = useRef<ChatState | null>(null);
  const browserHistory = useRef(chat);
  const bootReady = useRef(false);
  chatRef.current = chat;
  const active = chat.conversations.find(c => c.id === chat.activeConversationId) || chat.conversations[0];
  const model = models.find(m => m.id === modelId);
  const currentRuntime = runtime.find(r => r.model_id === modelId);
  const loaded = currentRuntime?.state === 'hot';
  const pendingReply = chat.conversations.find(c => c.messages.some(m => m.pending))?.messages.find(m => m.role === 'assistant' && m.pending);
  const generating = sending || !!pendingReply;
  const engineBusy = runtime.some(r => r.state === 'loading' || r.state === 'generating');
  const modelControlsBusy = generating || !!modelOperation || engineBusy;
  const availableModels = models.filter(m => m.type === 'LLM' && m.file_exists !== false);
  const preferences = settings?.runtime;
  const nCtx = preferences?.n_ctx ?? 4096;
  const maxTokens = preferences?.max_tokens ?? 512;
  const temperature = preferences?.temperature ?? 0.2;
  const trimHistory = preferences?.context_overflow !== 'error';
  const gpuLayers = preferredGpuLayers ?? preferences?.n_gpu_layers ?? 0;
  const enableThinking = preferences?.enable_thinking ?? false;
  const warmPolicy = preferences?.warm_policy ?? 'unload_after_idle';
  const runtimeOptions = { n_gpu_layers: gpuLayers, n_ctx: nCtx, context_overflow: trimHistory ? 'trim' as const : 'error' as const, warm_policy: warmPolicy, enable_thinking: enableThinking };
  const history = requestHistory(active.messages);
  const estimatedContext = estimateContext([...systemPrompt.trim() ? [{ content: systemPrompt }] : [], ...memory ? history : [], { content: input.trim() }], maxTokens);
  const budgetError = contextSettingsError(nCtx, maxTokens);
  const contextFull = estimatedContext > nCtx;

  async function reload() {
    const data = await fetchBootstrap();
    setModels(data.models); setSettings(data.settings);
    setUploadLimit(data.limits?.model_upload_max_bytes ?? 16 * 1024 ** 3);
    setModelId(previous => data.models.some(m => m.type === 'LLM' && m.id === previous && m.file_exists !== false) ? previous : data.models.find(m => m.type === 'LLM' && m.file_exists !== false)?.id || '');
    bootReady.current = true;
  }

  function applyRemote(value: unknown, authoritative = false) {
    const state = normalizeState(value);
    if (!acceptsRemote(remoteState.current, state, authoritative)) return false;
    remoteState.current = state; chatRef.current = state; setChat(state);
    return true;
  }

  useEffect(() => {
    let stopped = false;
    const timers = { sync: 0, runtime: 0 };
    const network = (kind: string, value: string) => { if (!stopped) setNetworkErrors(previous => ({ ...previous, [kind]: value })); };
    async function pollSync() {
      try {
        const response = await fetch(url('/api/desktop/chat-sync'), { cache: 'no-store', signal: AbortSignal.timeout(5000) });
        if (!response.ok || response.headers.get('X-Local-AI-GPP-Sync') !== MARKER) throw new Error('Обновите сервер Local AI: требуется общий протокол диалогов.');
        let state = normalizeState(await response.json());
        if (!remoteState.current) {
          const imported = `${STORE}-imported-${state.storeId || 'legacy'}`;
          let migrated = false;
          try { migrated = !!localStorage.getItem(imported); } catch { /* Server storage remains available. */ }
          if (!migrated && !state.resetVersion && browserHistory.current.conversations.some(c => c.messages.length)) {
            const copy = structuredClone(browserHistory.current);
            copy.conversations.forEach(c => c.messages.forEach(m => { if (m.pending) Object.assign(m, { pending: false, phase: 'error', error: 'Ответ прерван при закрытии старой страницы.' }); }));
            state = normalizeState(await jsonRequest('/api/desktop/chat-sync', { ...copy, version: state.version, source: 'import-browser' }));
          }
          try { localStorage.setItem(imported, '1'); } catch { /* The server owns persisted dialogs. */ }
        }
        if (!stopped) { applyRemote(state, true); setSyncReady(true); }
        network('sync', '');
      } catch (e) { network('sync', errorText(e)); }
      if (!stopped) timers.sync = window.setTimeout(() => void pollSync(), chatRef.current.conversations.some(c => c.messages.some(m => m.pending)) ? 500 : 2000);
    }
    async function pollRuntime() {
      if (!bootReady.current) {
        try { await reload(); network('boot', ''); } catch (e) { network('boot', errorText(e)); }
      }
      try { const statuses = await fetchRuntimeStatus(); if (!stopped) setRuntime(statuses); network('runtime', ''); }
      catch (e) { network('runtime', errorText(e)); }
      if (!stopped) timers.runtime = window.setTimeout(() => void pollRuntime(), 2500);
    }
    void pollSync(); void pollRuntime();
    return () => { stopped = true; Object.values(timers).forEach(window.clearTimeout); uploadController.current?.abort(); };
  }, []);
  useEffect(() => {
    const list = scrollRef.current;
    if (!list) return;
    if (scrollConversation.current !== active.id) {
      scrollConversation.current = active.id;
      followLatest.current = true;
    }
    if (followLatest.current) list.scrollTop = list.scrollHeight;
  }, [active.id, active.messages]);

  async function selectConversation(id: string, next = chat) {
    setStatus('');
    const state = { ...next, activeConversationId: id, updatedAt: Date.now() };
    setChat(state);
    try { applyRemote(await jsonRequest('/api/desktop/chat-sync', { ...state, source: 'main-ui' })); }
    catch (e) { if (remoteState.current) { chatRef.current = remoteState.current; setChat(remoteState.current); } setStatus(errorText(e)); }
  }
  async function clearConversation() {
    if (generating) return;
    setStatus('');
    try { applyRemote(await jsonRequest('/api/desktop/chat-clear', { conversationId: active.id })); }
    catch (e) { setStatus(errorText(e)); }
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    const draft = input;
    const text = draft.trim();
    if (!text || !model || modelControlsBusy || !syncReady) return;
    if (budgetError) { setStatus(budgetError); setSettingsSection('generation'); return; }
    const target = active;
    const title = target.messages.length ? target.title : text.slice(0, 42);
    const assistantId = crypto.randomUUID();
    const payload = { model_id: modelId, message: text, system_prompt: systemPrompt, temperature,
      max_tokens: maxTokens, runtime: runtimeOptions, memory,
      history };
    setSending(true); setStatus(''); setModelFeedback(null);
    try {
      const result = await jsonRequest('/api/desktop/chat-send', { ...payload, conversationId: target.id,
        conversationTitle: title, conversationCreatedAt: target.createdAt, assistant_id: assistantId, source: 'main-ui', baseVersion: remoteState.current?.version });
      if (result.state) applyRemote(result.state);
      setInput(current => clearSubmittedDraft(current, draft));
    } catch (e) { setStatus(errorText(e)); }
    finally { setSending(false); }
  }

  async function stop() {
    if (!pendingReply || stopping) return;
    setStopping(true);
    try { await jsonRequest('/api/runtime/cancel'); }
    catch (e) { setStatus(errorText(e)); }
    finally { setStopping(false); }
  }

  function selectModel(id: string) {
    const savedLayers = models.find(record => record.id === id)?.runtime?.n_gpu_layers;
    setModelId(id);
    setPreferredGpuLayers(typeof savedLayers === 'number' ? savedLayers : null);
    setModelFeedback(null);
  }

  function rememberModel(record: ModelRecord) {
    setModels(current => current.some(m => m.id === record.id)
      ? current.map(m => m.id === record.id ? record : m)
      : [...current, record]);
  }

  async function modelAction(load: boolean) {
    if (!model || modelControlsBusy) return;
    setModelFeedback(null);
    setModelOperation({ kind: load ? 'load' : 'unload' });
    try {
      if (load) rememberModel(await prewarmModel(model.id, runtimeOptions));
      else await unloadModel(model.id);
      setRuntime(await fetchRuntimeStatus());
      setModelFeedback({ text: load ? 'Модель готова к диалогу.' : 'Модель выгружена.' });
    } catch (e) { setModelFeedback({ text: errorText(e), error: true }); }
    finally { setModelOperation(null); }
  }

  async function addModel(name: string, source: ModelSource) {
    if (modelOperation) return false;
    const upload = 'file' in source;
    const abort = upload ? new AbortController() : null;
    uploadController.current = abort;
    setModelFeedback(null);
    setModelOperation({ kind: upload ? 'upload' : 'register' });
    try {
      const form = new FormData();
      form.append('model_name', name);
      form.append('model_type', 'LLM');
      let added: ModelRecord;
      if (upload && abort) {
        form.append('model_file', source.file);
        added = await uploadModel(form, abort.signal, percent => setModelOperation({ kind: 'upload', percent }));
      } else if ('path' in source) {
        form.append('model_path', source.path);
        added = await registerModelPath(form, AbortSignal.timeout(10000));
      } else {
        return false;
      }
      rememberModel(added);
      selectModel(added.id);
      setModelFeedback({ text: 'Модель подключена. Напишите сообщение или загрузите её в память.' });
      return true;
    } catch (e) {
      setModelFeedback({
        text: abort?.signal.aborted ? 'Передача файла отменена.' : errorText(e),
        error: !abort?.signal.aborted,
      });
      return false;
    } finally {
      uploadController.current = null;
      setModelOperation(null);
    }
  }

  async function applySettings(patch: Partial<RuntimeSettings>, prompt: string) {
    if (!settings || saving) return false;
    setSaving(true); setSettingsFeedback(null);
    try {
      setSettings(await saveRuntimePatch(settings, changedRuntime(settings.runtime, patch)));
      if (patch.n_gpu_layers !== undefined) setPreferredGpuLayers(patch.n_gpu_layers);
      setSystemPrompt(prompt);
      setSettingsFeedback({ text: 'Настройки сохранены.' });
      return true;
    } catch (e) {
      setSettingsFeedback({ text: errorText(e), error: true });
      return false;
    } finally { setSaving(false); }
  }

  return <div className="compact-app">
    <header className="compact-header"><strong>Local AI Runtime <small>{APP_VERSION}</small></strong>
      <div><button type="button" aria-haspopup="dialog" onClick={() => { setSettingsFeedback(null); setSettingsSection('models'); }}>Настройки</button></div></header>
    <aside className="dialog-list"><button className="primary" disabled={!syncReady} onClick={() => { const c = fresh(); void selectConversation(c.id, { ...chat, conversations: [c, ...chat.conversations] }); }}>+ Новый диалог</button>
      <nav aria-label="Диалоги">{chat.conversations.map(c => <button className={c.id === active.id ? 'selected' : ''} key={c.id} onClick={() => void selectConversation(c.id)}>{c.title}<small>{c.messages.length} сообщений</small></button>)}</nav>
      <a href={url('/docs')} target="_blank" rel="noreferrer">API · документация</a></aside>
    <main className="compact-chat">
      <div className="model-toolbar">
        {availableModels.length ? <>
          <select aria-label="Модель" value={modelId} disabled={modelControlsBusy} onChange={e => selectModel(e.target.value)}>
            {!modelId && <option value="">Выберите модель</option>}
            {availableModels.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
          <button type="button" disabled={!model || modelControlsBusy} onClick={() => void modelAction(!loaded)}>
            {loaded ? 'Выгрузить' : 'Загрузить в память'}
          </button>
        </> : <button type="button" className="primary" aria-haspopup="dialog" onClick={() => setSettingsSection('models')}>Подключить GGUF</button>}
        <span className="runtime-label">{model ? modelStateText(currentRuntime) : settings ? 'Модель не подключена' : 'Получаем список моделей…'}</span>
        <label className="inline-check"><input type="checkbox" checked={memory} onChange={e => setMemory(e.target.checked)} />Память диалога</label>
        <button type="button" aria-haspopup="dialog" onClick={() => { setSettingsFeedback(null); setSettingsSection('generation'); }}>Контекст · {nCtx.toLocaleString('ru-RU')}</button>
        <button type="button" disabled={generating || !active.messages.length} onClick={() => void clearConversation()}>Очистить</button>
      </div>
      <div className="message-list" ref={scrollRef} role="log" aria-label="Сообщения" onScroll={event => {
        const list = event.currentTarget;
        followLatest.current = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
      }}>
        {!active.messages.length && <div className="empty-chat">
          <h1>{model ? 'Начните диалог' : 'Подключите модель для диалога'}</h1>
          <p>{model ? 'Напишите сообщение. При первом запросе Runtime загрузит модель в память.' : 'Выберите GGUF с компьютера или укажите путь к готовому файлу модели.'}</p>
          {!model && <button type="button" aria-haspopup="dialog" onClick={() => setSettingsSection('models')}>Открыть модели</button>}
        </div>}
        {active.messages.map((message, index) => {
          const date = messageDate(message);
          return <article className={`chat-message ${message.role}`} key={message.id || index}>
          <header className="message-header"><strong>{message.role === 'user' ? 'Вы' : 'Local AI'}</strong>
            {date ? <time dateTime={date.toISOString()}>{messageTime.format(date)}</time> : <span className="message-time-unknown">Время не сохранено</span>}</header>
          <div>{message.text || (message.pending ? 'Модель готовит ответ…' : 'Пустой ответ')}</div>
          {messageError(message) && <p className="context-warning" role="alert">{messageError(message)}</p>}
          {message.reasoning && <details><summary>Рассуждение</summary><p>{message.reasoning}</p></details>}
          {message.elapsed_ms !== undefined && <small>{(message.elapsed_ms / 1000).toFixed(1)} с · {message.usage?.completion_tokens ?? '—'} токенов</small>}
          {!!message.history_dropped && <small>В контекст не вошло {message.history_dropped} старых реплик. Полная история сохранена.</small>}
        </article>;
        })}
      </div>
      <form className="compact-composer" onSubmit={send}><textarea aria-label="Сообщение" placeholder="Напишите сообщение…" rows={3} value={input} onChange={e => setInput(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }} />
        {pendingReply ? <button type="button" disabled={stopping} onClick={() => void stop()}>{stopping ? 'Останавливаем…' : 'Остановить'}</button>
          : <button className="primary" disabled={!input.trim() || !model || !syncReady || modelControlsBusy || !!budgetError}>{sending ? 'Отправляем…' : 'Отправить'}</button>}</form>
      <div className={`context-meter ${contextFull || budgetError ? 'context-warning' : ''}`}>
        <progress aria-label="Примерная занятость контекста" max={Math.max(1, nCtx)} value={Math.min(estimatedContext, nCtx)} />
        <span>≈ {estimatedContext.toLocaleString('ru-RU')} / {nCtx.toLocaleString('ru-RU')} ток. · под ответ {maxTokens}
          {contextFull && (memory && trimHistory ? ' · старые реплики сократятся' : ' · запрос может не поместиться')}. Оценка; точная проверка перед генерацией.</span>
      </div>
      <footer className="compact-status" role="status">
        {status || Object.values(networkErrors).filter(Boolean).join(' · ') ||
          (pendingReply ? <GenerationTimer message={pendingReply} loading={currentRuntime?.state === 'loading'} />
            : modelOperation ? modelOperationText(modelOperation)
            : modelFeedback ? modelFeedback.text
            : engineBusy ? 'Runtime выполняет другой запрос.'
            : syncReady && settings ? stateStatus(chat) : 'Подключаемся к Runtime…')}
      </footer>
    </main>
    {settingsSection && <RuntimeSettingsDialog
      section={settingsSection} onSection={setSettingsSection} onClose={() => setSettingsSection(null)}
      settings={settings} systemPrompt={systemPrompt} gpuLayers={gpuLayers}
      saving={saving} settingsFeedback={settingsFeedback} onSave={applySettings}
      models={models.filter(m => m.type === 'LLM')} model={model} runtime={currentRuntime}
      modelControlsBusy={modelControlsBusy} operation={modelOperation} modelFeedback={modelFeedback} uploadLimit={uploadLimit}
      onSelectModel={selectModel} onLoadModel={() => void modelAction(!loaded)} onConnectModel={addModel}
      onCancelUpload={() => uploadController.current?.abort()}
      apiBase={new URL(url('/v1'), window.location.origin).href} apiDocs={url('/docs')}
    />}
  </div>;
}

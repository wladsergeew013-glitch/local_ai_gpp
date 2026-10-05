import { FormEvent, useEffect, useRef, useState } from 'react';
import { API_BASE, fetchBootstrap, fetchRuntimeStatus, prewarmModel, registerModelPath, saveSettings, streamChat, unloadModel, uploadModel } from './api';
import type { ChatMessage, EngineSettings, ModelRecord, RuntimeStatus } from './types';
import { APP_VERSION } from './version';
import { contextSettingsError, estimateContext } from './contextBudget';

type Conversation = { id: string; title: string; createdAt: string; messages: ChatMessage[] };
type ChatState = { activeConversationId: string; conversations: Conversation[]; updatedAt?: number };
const STORE = 'local-ai-gpp-chats-v1';
const MARKER = 'V67_4_DESKTOP_SYNC_MULTI_CONVERSATION';
const url = (path: string) => `${API_BASE}${path}`;
const fresh = (): Conversation => ({ id: crypto.randomUUID(), title: 'Новый диалог', createdAt: new Date().toISOString(), messages: [] });
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

function initialState(): ChatState {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE) || 'null') as ChatState | null;
    if (saved?.conversations?.length && saved.conversations.every(c => typeof c.id === 'string' && Array.isArray(c.messages))) {
      saved.conversations.forEach(c => c.messages.forEach(m => {
        if (m.pending) Object.assign(m, { pending: false, phase: 'error', text: `${m.text || ''}\nОтвет прерван при закрытии страницы.` });
      }));
      return saved;
    }
  } catch {
    try { localStorage.setItem(`${STORE}-corrupt-${Date.now()}`, localStorage.getItem(STORE) || ''); }
    catch { /* Storage may be unavailable or full. */ }
  }
  const conversation = fresh();
  return { activeConversationId: conversation.id, conversations: [conversation] };
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
  const [chat, setChat] = useState<ChatState>(initialState);
  const [models, setModels] = useState<ModelRecord[]>([]);
  const [settings, setSettings] = useState<EngineSettings | null>(null);
  const [runtime, setRuntime] = useState<RuntimeStatus[]>([]);
  const [modelId, setModelId] = useState('');
  const [input, setInput] = useState('');
  const [status, setStatus] = useState('Подключаюсь к Local AI…');
  const [busy, setBusy] = useState(false);
  const [desktop, setDesktop] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showContext, setShowContext] = useState(false);
  const [trimHistory, setTrimHistory] = useState(true);
  const [memory, setMemory] = useState(true);
  const [temperature, setTemperature] = useState(0.2);
  const [enableThinking, setEnableThinking] = useState(false);
  const [maxTokens, setMaxTokens] = useState(512);
  const [gpuLayers, setGpuLayers] = useState(-1);
  const [nCtx, setNCtx] = useState(4096);
  const [warmPolicy, setWarmPolicy] = useState('unload_after_idle');
  const [systemPrompt, setSystemPrompt] = useState('Ты полезный помощник. Отвечай кратко и по делу.');
  const [modelName, setModelName] = useState('');
  const [modelPath, setModelPath] = useState('');
  const [modelFile, setModelFile] = useState<File | null>(null);
  const controller = useRef<AbortController | null>(null);
  const chatRef = useRef(chat);
  const scrollRef = useRef<HTMLDivElement>(null);
  const followLatest = useRef(true);
  const scrollConversation = useRef('');
  const remoteStamp = useRef(0);
  chatRef.current = chat;
  const active = chat.conversations.find(c => c.id === chat.activeConversationId) || chat.conversations[0];
  const model = models.find(m => m.id === modelId);
  const loaded = runtime.find(r => r.model_id === modelId);
  const generating = busy || chat.conversations.some(c => c.messages.some(m => m.pending));
  const runtimeOptions = { n_gpu_layers: gpuLayers, n_ctx: nCtx, context_overflow: trimHistory ? 'trim' as const : 'error' as const, warm_policy: warmPolicy, enable_thinking: enableThinking };
  const history = active.messages.filter(m => !m.pending && m.phase !== 'error' && m.text.trim()).slice(-64).map(m => ({ role: m.role, content: m.answer || m.text }));
  const estimatedContext = estimateContext([...systemPrompt.trim() ? [{ content: systemPrompt }] : [], ...memory ? history : [], { content: input.trim() }], maxTokens);
  const budgetError = contextSettingsError(nCtx, maxTokens);
  const contextFull = estimatedContext > nCtx;

  async function reload() {
    const data = await fetchBootstrap();
    setModels(data.models); setSettings(data.settings);
    setModelId(previous => data.models.some(m => m.id === previous && m.file_exists) ? previous : data.models.find(m => m.type === 'LLM' && m.file_exists)?.id || '');
    setTemperature(data.settings.runtime.temperature); setMaxTokens(data.settings.runtime.max_tokens);
    setTrimHistory(data.settings.runtime.context_overflow !== 'error');
    setEnableThinking(data.settings.runtime.enable_thinking ?? false);
    setGpuLayers(data.settings.runtime.n_gpu_layers); setNCtx(data.settings.runtime.n_ctx); setWarmPolicy(data.settings.runtime.warm_policy);
  }

  useEffect(() => {
    let stopped = false;
    void reload().then(() => { if (!stopped) setStatus('Готово'); }).catch(e => setStatus(errorText(e)));
    let syncEnabled = true;
    let timer: number;
    async function poll() {
      try {
        const statuses = await fetchRuntimeStatus();
        if (!stopped) setRuntime(statuses);
        if (syncEnabled) {
          const response = await fetch(url('/api/desktop/chat-sync'), { cache: 'no-store', signal: AbortSignal.timeout(5000) });
          if (response.status === 404 || response.headers.get('X-Local-AI-GPP-Sync') !== MARKER) syncEnabled = false;
          else if (response.ok) {
            const state = await response.json() as ChatState;
            if (!stopped && state.conversations?.length && ((state.updatedAt || 0) > remoteStamp.current || remoteStamp.current === 0)) {
              const wasPending = chatRef.current.conversations.some(c => c.messages.some(m => m.pending));
              const pending = state.conversations.some(c => c.messages.some(m => m.pending));
              if (wasPending && !pending) setStatus('Готово');
              remoteStamp.current = state.updatedAt || 0; setDesktop(true); setChat(state);
            }
          }
        }
      } catch (e) { if (!stopped) setStatus(errorText(e)); }
      if (!stopped) timer = window.setTimeout(() => void poll(), chatRef.current.conversations.some(c => c.messages.some(m => m.pending)) ? 500 : 2500);
    }
    void poll();
    return () => { stopped = true; window.clearTimeout(timer); controller.current?.abort(); };
  }, []);

  useEffect(() => {
    if (desktop) return;
    try { localStorage.setItem(STORE, JSON.stringify(chat)); }
    catch { setStatus('История не сохранена: хранилище браузера заполнено.'); }
  }, [chat, desktop]);
  useEffect(() => {
    const list = scrollRef.current;
    if (!list) return;
    if (scrollConversation.current !== active.id) {
      scrollConversation.current = active.id;
      followLatest.current = true;
    }
    if (followLatest.current) list.scrollTop = list.scrollHeight;
  }, [active.id, active.messages]);

  function editConversation(id: string, edit: (conversation: Conversation) => Conversation) {
    setChat(previous => ({ ...previous, conversations: previous.conversations.map(c => c.id === id ? edit(c) : c) }));
  }
  function updateMessage(conversationId: string, messageId: string, patch: Partial<ChatMessage>) {
    editConversation(conversationId, c => ({ ...c, messages: c.messages.map(m => m.id === messageId ? { ...m, ...patch } : m) }));
  }
  async function selectConversation(id: string, next = chat) {
    const state = { ...next, activeConversationId: id, updatedAt: Date.now() };
    setChat(state);
    if (desktop) {
      try { setChat(await jsonRequest('/api/desktop/chat-sync', { ...state, source: 'main-ui' })); }
      catch (e) { setStatus(errorText(e)); }
    }
  }
  async function clearConversation() {
    if (generating) return;
    if (desktop) {
      try { setChat(await jsonRequest('/api/desktop/chat-clear', { conversationId: active.id })); }
      catch (e) { setStatus(errorText(e)); }
    } else editConversation(active.id, c => ({ ...c, messages: [] }));
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    const text = input.trim();
    if (!text || !modelId || generating) return;
    if (budgetError) { setStatus(budgetError); setShowContext(true); return; }
    const target = active;
    const title = target.messages.length ? target.title : text.slice(0, 42);
    const assistantId = crypto.randomUUID();
    const payload = { model_id: modelId, message: text, system_prompt: systemPrompt, temperature,
      max_tokens: maxTokens, runtime: runtimeOptions, memory,
      history };
    setBusy(true); setStatus('Модель отвечает…');
    if (desktop) {
      try {
        const result = await jsonRequest('/api/desktop/chat-send', { ...payload, conversationId: target.id,
          conversationTitle: title, conversationCreatedAt: target.createdAt, assistant_id: assistantId, source: 'main-ui' });
        if (result.state) setChat(result.state);
        setInput(''); setStatus('Модель отвечает…');
      } catch (e) { setStatus(errorText(e)); }
      finally { setBusy(false); }
      return;
    }
    editConversation(target.id, c => ({ ...c, title, messages: [...c.messages,
      { id: crypto.randomUUID(), role: 'user', text, createdAt: new Date().toISOString() },
      { id: assistantId, role: 'assistant', text: '', createdAt: new Date().toISOString(), pending: true, phase: 'thinking' }] }));
    setInput('');
    const abort = new AbortController(); controller.current = abort;
    const timeout = window.setTimeout(() => abort.abort(), 210000);
    let content = '';
    try {
      await streamChat(payload, event => {
        if (event.type === 'delta') {
          content += event.text || '';
          updateMessage(target.id, assistantId, { text: content, phase: 'typing' });
        } else if (event.type === 'worker_status') setStatus(event.message || 'Загружаю модель…');
        else if (event.type === 'runtime') setStatus(`${event.mode || event.runtime?.mode || ''} · модель отвечает…`);
        else if (event.type === 'done') {
          updateMessage(target.id, assistantId, { text: event.answer || event.content || content,
            answer: event.answer, reasoning: event.reasoning, pending: false, phase: 'done',
            elapsed_ms: event.elapsed_ms, usage: event.usage, finish_reason: event.finish_reason, history_dropped: event.history_dropped });
          setStatus(event.history_dropped ? `Готово · в запрос не вошло ${event.history_dropped} старых реплик; история сохранена.` : 'Готово');
        }
      }, abort.signal);
    } catch (e) {
      const message = abort.signal.aborted ? 'Генерация остановлена.' : errorText(e);
      updateMessage(target.id, assistantId, { text: content || message, pending: false, phase: 'error' });
      setStatus(message);
    } finally { window.clearTimeout(timeout); controller.current = null; setBusy(false); }
  }

  async function stop() {
    controller.current?.abort();
    if (desktop) {
      try { await jsonRequest('/api/runtime/cancel'); setStatus('Генерация остановлена.'); }
      catch (e) { setStatus(errorText(e)); }
    }
  }
  async function modelAction(load: boolean) {
    if (!modelId || generating) return;
    setBusy(true);
    setStatus(load ? 'Загружаю веса и прогреваю вычисления…' : 'Выгружаю модель…');
    try { if (load) await prewarmModel(modelId, runtimeOptions); else await unloadModel(modelId);
      setRuntime(await fetchRuntimeStatus()); setStatus(load ? 'Модель загружена' : 'Модель выгружена'); }
    catch (e) { setStatus(errorText(e)); }
    finally { setBusy(false); }
  }
  async function addModel(event: FormEvent) {
    event.preventDefault(); setBusy(true);
    try {
      const form = new FormData(); form.append('model_name', modelName || modelFile?.name || modelPath.split(/[\\/]/).pop() || 'LLM');
      form.append('model_type', 'LLM');
      let added: ModelRecord;
      if (modelFile) { form.append('model_file', modelFile); added = await uploadModel(form); }
      else { form.append('model_path', modelPath); added = await registerModelPath(form); }
      await reload(); setModelId(added.id); setModelFile(null); setModelPath(''); setModelName(''); setStatus('Модель подключена');
    } catch (e) { setStatus(errorText(e)); }
    finally { setBusy(false); }
  }
  async function applySettings() {
    if (!settings) return;
    if (budgetError) { setStatus(budgetError); setShowContext(true); return; }
    try {
      setSettings(await saveSettings({ ...settings, runtime: { ...settings.runtime, ...runtimeOptions,
        warm_policy: warmPolicy as EngineSettings['runtime']['warm_policy'], temperature, max_tokens: maxTokens } }));
      setStatus('Настройки сохранены');
    } catch (e) { setStatus(errorText(e)); }
  }

  return <div className="compact-app">
    <header className="compact-header"><strong>Local AI GPP <small>{APP_VERSION}</small></strong>
      <div>{desktop && <button onClick={() => void jsonRequest('/api/desktop/show-assistant').catch(e => setStatus(errorText(e)))}>Мини-помощник</button>}
        <button aria-expanded={showSettings} onClick={() => setShowSettings(!showSettings)}>Настройки</button></div></header>
    <aside className="dialog-list"><button className="primary" onClick={() => { const c = fresh(); void selectConversation(c.id, { ...chat, conversations: [c, ...chat.conversations] }); }}>+ Новый диалог</button>
      <nav aria-label="Диалоги">{chat.conversations.map(c => <button className={c.id === active.id ? 'selected' : ''} key={c.id} onClick={() => void selectConversation(c.id)}>{c.title}<small>{c.messages.length} сообщений</small></button>)}</nav>
      <a href={url('/docs')} target="_blank" rel="noreferrer">API · документация</a></aside>
    <main className="compact-chat">
      <div className="model-toolbar"><select aria-label="Модель" value={modelId} disabled={generating} onChange={e => { setModelId(e.target.value); const m = models.find(m => m.id === e.target.value); if (m?.runtime?.n_gpu_layers !== undefined) setGpuLayers(Number(m.runtime.n_gpu_layers)); }}>
        <option value="">Выберите модель</option>{models.filter(m => m.type === 'LLM' && m.file_exists !== false).map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</select>
        <button disabled={!model || generating} onClick={() => void modelAction(!loaded)}>{loaded ? 'Выгрузить' : 'Загрузить'}</button>
        <span className="runtime-label">{loaded?.runtime_mode || 'По запросу'}</span>
        <label className="inline-check"><input type="checkbox" checked={memory} onChange={e => setMemory(e.target.checked)} />Память диалога</label>
        <button aria-expanded={showContext} onClick={() => setShowContext(!showContext)}>Контекст · {nCtx}</button>
        <button disabled={generating || !active.messages.length} onClick={() => void clearConversation()}>Очистить</button></div>
      {showContext && <section className="context-panel" aria-label="Контекст и длина ответа">
        <div className="settings-fields">
          <label>Контекст, токенов<select aria-label="Пресет контекста" value={[2048, 4096, 8192, 16384].includes(nCtx) ? nCtx : 'custom'} onChange={e => { if (e.target.value !== 'custom') { const value = Number(e.target.value); setNCtx(value); setMaxTokens(Math.min(maxTokens, value - 257)); } }}>
            <option value={2048}>2048 · экономно</option><option value={4096}>4096 · стандарт</option><option value={8192}>8192 · длинный диалог</option><option value={16384}>16384 · больше памяти</option><option value="custom">Свой размер</option></select>
            <input aria-label="Размер контекста" type="number" min="512" max="131072" step="512" value={nCtx} onChange={e => setNCtx(Number(e.target.value))} /></label>
          <label>Максимум на ответ<input aria-label="Максимум токенов ответа" type="number" min="1" max={Math.min(32768, nCtx - 257)} step="128" value={maxTokens} onChange={e => setMaxTokens(Number(e.target.value))} /></label>
          <label className="inline-check"><input type="checkbox" checked={trimHistory} onChange={e => setTrimHistory(e.target.checked)} />Автоматически сокращать старые реплики</label>
          <button disabled={generating || !!budgetError} onClick={() => void applySettings()}>Сохранить</button>
        </div>
        <p>Контекст делят история, инструкция, вопрос и ответ. Полная история остаётся в диалогах; сокращается только то, что отправляем модели. Больший контекст занимает больше памяти. Изменение размера перезагрузит модель со следующим запросом.</p>
        {budgetError && <p className="context-warning" role="alert">{budgetError}</p>}
      </section>}
      {showSettings && <section className="compact-settings" aria-label="Настройки модели">
        <div className="settings-fields"><label>Температура<input type="number" min="0" max="2" step="0.1" value={temperature} onChange={e => setTemperature(Number(e.target.value))} /></label>
          <label>Вычисления<select value={gpuLayers} onChange={e => setGpuLayers(Number(e.target.value))}><option value={-1}>GPU · CUDA</option><option value={16}>CPU + GPU</option><option value={0}>CPU</option>{![-1, 16, 0].includes(gpuLayers) && <option value={gpuLayers}>GPU · {gpuLayers} слоёв</option>}</select></label>
          <label>Разогрев<select value={warmPolicy} onChange={e => setWarmPolicy(e.target.value)}><option value="unload_after_idle">Выгружать после простоя</option><option value="keep_hot">Держать в памяти</option><option value="manual">Выгружать вручную</option></select></label>
          <label className="inline-check"><input type="checkbox" checked={enableThinking} onChange={e => setEnableThinking(e.target.checked)} />Рассуждения Qwen</label>
          <button disabled={generating} onClick={() => void applySettings()}>Сохранить</button></div>
        <details><summary>Модели и дополнительные параметры</summary>
          <label>Инструкция модели<textarea rows={2} value={systemPrompt} onChange={e => setSystemPrompt(e.target.value)} /></label>
          <form className="model-import" onSubmit={addModel}><input aria-label="Имя модели" placeholder="Имя модели" value={modelName} onChange={e => setModelName(e.target.value)} />
            <input aria-label="Путь к GGUF" placeholder="Полный путь к .gguf на этом компьютере" value={modelPath} onChange={e => { setModelPath(e.target.value); setModelFile(null); }} />
            <label>Или загрузить GGUF<input type="file" accept=".gguf" onChange={e => setModelFile(e.target.files?.[0] || null)} /></label>
            <button disabled={generating || (!modelPath.trim() && !modelFile)}>Подключить модель</button></form>
          <p>API: <code>{window.location.origin}/v1</code> · ID модели: <code>{modelId || '—'}</code></p></details>
      </section>}
      <div className="message-list" ref={scrollRef} role="log" aria-label="Сообщения" onScroll={event => {
        const list = event.currentTarget;
        followLatest.current = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
      }}>
        {!active.messages.length && <div className="empty-chat"><h1>Начните диалог</h1><p>Выберите GGUF-модель и напишите сообщение.</p><p>С включённой памятью модель получает предыдущие реплики этого диалога.</p></div>}
        {active.messages.map((message, index) => {
          const date = messageDate(message);
          return <article className={`chat-message ${message.role}`} key={message.id || index}>
          <header className="message-header"><strong>{message.role === 'user' ? 'Вы' : 'Local AI'}</strong>
            {date ? <time dateTime={date.toISOString()}>{messageTime.format(date)}</time> : <span className="message-time-unknown">Время не сохранено</span>}</header>
          <div>{message.text || (message.pending ? 'Модель готовит ответ…' : 'Пустой ответ')}</div>
          {message.reasoning && <details><summary>Рассуждение</summary><p>{message.reasoning}</p></details>}
          {message.elapsed_ms !== undefined && <small>{(message.elapsed_ms / 1000).toFixed(1)} с · {message.usage?.completion_tokens ?? '—'} токенов</small>}
          {!!message.history_dropped && <small>В контекст не вошло {message.history_dropped} старых реплик. Полная история сохранена.</small>}
        </article>;
        })}
      </div>
      <form className="compact-composer" onSubmit={send}><textarea aria-label="Сообщение" placeholder="Напишите сообщение…" rows={3} value={input} onChange={e => setInput(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }} />
        {generating ? <button type="button" onClick={() => void stop()}>Остановить</button> : <button className="primary" disabled={!input.trim() || !modelId || !!budgetError}>Отправить</button>}</form>
      <div className={`context-meter ${contextFull || budgetError ? 'context-warning' : ''}`}>
        <progress aria-label="Примерная занятость контекста" max={Math.max(1, nCtx)} value={Math.min(estimatedContext, nCtx)} />
        <span>≈ {estimatedContext.toLocaleString('ru-RU')} / {nCtx.toLocaleString('ru-RU')} ток. · под ответ {maxTokens}
          {contextFull && (memory && trimHistory ? ' · старые реплики сократятся' : ' · запрос может не поместиться')}. Оценка; точная проверка перед генерацией.</span>
      </div>
      <footer className="compact-status" role="status">{status}</footer>
    </main>
  </div>;
}

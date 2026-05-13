import { useEffect, useMemo, useRef, useState } from 'react';
import {
  API_BASE,
  deleteModel,
  fetchBootstrap,
  fetchHubModels,
  fetchRuntimeDiagnostics,
  fetchRuntimeStatus,
  importHubModel,
  prewarmModel,
  registerModelPath,
  saveSettings,
  startModel,
  streamChat,
  unloadModel,
  uploadLogo,
  uploadModel,
  validateModels,
} from './api';
import type {
  ChatMessage,
  EngineSettings,
  ModelRecord,
  RemoteHubModel,
  RuntimeDiagnostics,
  RuntimeSettings,
  RuntimeStatus,
  StatusState,
} from './types';

type SettingsSection = 'model' | 'runtime' | 'server' | 'hub' | 'appearance';
type RightPanelMode = 'status' | 'settings';
type HighlightTarget = '' | 'ribbon' | 'logo' | 'workspace' | 'chat' | 'status' | 'userMessage' | 'assistantMessage';
type DraftValue = string | number | boolean | string[];
type ResizeHandle = 'left' | 'right';
type Conversation = {
  id: string;
  title: string;
  createdAt: string;
  messages: ChatMessage[];
};

const DEFAULT_SETTINGS: EngineSettings = {
  branding: {
    title: 'Агент ГПП',
    subtitle: 'Локальный движок LLM моделей.',
    logo_url: '',
    logo_width: 150,
    logo_height: 78,
    logo_radius: 16,
    logo_padding: 10,
    logo_fit: 'contain',
  },
  theme: {
    accent: '#2f6fed',
    hero_text: '#101828',
    chrome_start: '#063f2c',
    chrome_end: '#0b5d43',
    chrome_text: '#ffffff',
    background_start: '#f6f7fb',
    background_end: '#e8ecf4',
    panel: '#ffffff',
    panel_alt: '#f2f4f7',
    border: '#d0d5dd',
    text: '#101828',
    muted: '#667085',
    user_bubble: '#dbeafe',
    assistant_bubble: '#ffffff',
    success: '#168a4a',
    warning: '#c27803',
    danger: '#c2413b',
  },
  layout: {
    app_max_width: 1920,
    card_radius: 8,
    hero_compact: false,
    left_panel_width: 250,
    center_panel_min_width: 440,
    right_panel_width: 620,
  },
  runtime: {
    n_ctx: 4096,
    n_batch: 512,
    n_threads: 4,
    n_threads_batch: 0,
    n_gpu_layers: 0,
    main_gpu: 0,
    split_mode: 'layer',
    tensor_split: '',
    temperature: 0.2,
    max_tokens: 1024,
    top_k: 40,
    top_p: 0.95,
    min_p: 0.05,
    repeat_penalty: 1.1,
    seed: -1,
    offload_kqv: true,
    flash_attn: false,
    op_offload: true,
    swa_full: false,
    use_mmap: true,
    use_mlock: false,
    verbose_runtime: false,
    gpu_fallback_to_cpu: true,
    warm_policy: 'keep_hot',
    idle_unload_sec: 1800,
    preload_on_start: false,
  },
  server: {
    host: '127.0.0.1',
    port: 8000,
    public_base_url: 'http://127.0.0.1:8000',
    openai_compat_enabled: true,
    openai_compat_path: '/v1/chat/completions',
    cors_origins: ['http://127.0.0.1:5173', 'http://localhost:5173', 'http://127.0.0.1:8080', 'http://localhost:8080'],
    api_key: '',
  },
  hub: {
    enabled: false,
    base_url: '',
    models_endpoint: '/models',
    pull_endpoint: '/models/pull',
    token: '',
    timeout_sec: 30,
  },
};

const MODEL_TYPES = ['LLM', 'Embedding', 'ClassicalML', 'Other'];

function cloneSettings(value: EngineSettings): EngineSettings {
  return JSON.parse(JSON.stringify(value)) as EngineSettings;
}

function mergeDeep<T>(base: T, incoming: unknown): T {
  const out: any = Array.isArray(base) ? [...(base as any)] : { ...(base as any) };
  if (!incoming || typeof incoming !== 'object') return out;
  for (const [key, value] of Object.entries(incoming as Record<string, unknown>)) {
    if (
      value &&
      typeof value === 'object' &&
      !Array.isArray(value) &&
      key in out &&
      out[key] &&
      typeof out[key] === 'object' &&
      !Array.isArray(out[key])
    ) {
      out[key] = mergeDeep(out[key], value);
    } else {
      out[key] = value;
    }
  }
  return out;
}

function newConversation(title = SHARED_CHAT_TITLE): Conversation {
  return {
    id: SHARED_CHAT_CONVERSATION_ID,
    title,
    createdAt: new Date().toISOString(),
    messages: [],
  };
}


type ChatSyncState = {
  updatedAt?: number;
  activeConversationId?: string;
  conversations?: Conversation[];
  source?: string;
};

const CHAT_SYNC_SOURCE_MAIN = 'main-ui';
const IS_DESKTOP_API_MODE = API_BASE === '.';
const SHARED_CHAT_CONVERSATION_ID = 'conv-shared';
const SHARED_CHAT_TITLE = 'Тестовый диалог';
const MAIN_SHARED_CHAT_SYNC_MARKER = 'V66_CANONICAL_SHARED_CHAT_REMOTE_REPLACE';


function desktopApiUrl(path: string): string {
  return !API_BASE || API_BASE === '.' ? path : `${API_BASE}${path}`;
}

function countSyncMessages(conversations: Conversation[]): number {
  return conversations.reduce((total, conversation) => total + conversation.messages.length, 0);
}

function normalizeSyncMessage(raw: unknown, fallbackIndex: number): ChatMessage | null {
  if (!raw || typeof raw !== 'object') return null;
  const item = raw as Record<string, unknown>;
  const role = item.role === 'user' ? 'user' : 'assistant';
  const text = String(item.text || item.answer || '').trim();
  if (!text) return null;
  return {
    ...(item as Partial<ChatMessage>),
    id: String(item.id || `sync-msg-${fallbackIndex}-${Date.now()}`),
    role,
    text,
  } as ChatMessage;
}

function normalizeSyncPayload(raw: unknown): ChatSyncState | null {
  if (!raw || typeof raw !== 'object') return null;
  const payload = raw as Record<string, unknown>;
  const conversationsRaw = Array.isArray(payload.conversations) ? payload.conversations : [];
  if (!conversationsRaw.length) return null;

  const mergedMessages: ChatMessage[] = [];
  let title = SHARED_CHAT_TITLE;
  let createdAt = new Date().toISOString();
  conversationsRaw.forEach((rawConversation, convIndex) => {
    if (!rawConversation || typeof rawConversation !== 'object') return;
    const conv = rawConversation as Record<string, unknown>;
    if (typeof conv.title === 'string' && conv.title.trim()) title = conv.title;
    if (typeof conv.createdAt === 'string' && conv.createdAt.trim()) createdAt = conv.createdAt;
    const rawMessages = Array.isArray(conv.messages) ? conv.messages : [];
    rawMessages.forEach((message, index) => {
      const normalized = normalizeSyncMessage(message, convIndex * 10000 + index);
      if (normalized) mergedMessages.push(normalized);
    });
  });

  return {
    updatedAt: typeof payload.updatedAt === 'number' ? payload.updatedAt : Date.now(),
    activeConversationId: SHARED_CHAT_CONVERSATION_ID,
    conversations: [{
      id: SHARED_CHAT_CONVERSATION_ID,
      title,
      createdAt,
      messages: mergeSyncMessages([], mergedMessages),
    }],
    source: typeof payload.source === 'string' ? payload.source : '',
  };
}


function syncMessageKey(message: ChatMessage, fallbackIndex: number): string {
  const id = String(message.id || '').trim();
  if (id) return id;
  return `legacy-${message.role}-${fallbackIndex}-${message.text || message.answer || ''}`;
}

function mergeSyncMessages(current: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
  const next = current.map((message) => ({ ...message }));
  const byId = new Map<string, number>();
  next.forEach((message, index) => byId.set(syncMessageKey(message, index), index));

  incoming.forEach((message, index) => {
    const key = syncMessageKey(message, next.length + index);
    const foundIndex = byId.get(key);
    if (foundIndex === undefined) {
      byId.set(key, next.length);
      next.push({ ...message, id: message.id || key });
      return;
    }
    next[foundIndex] = { ...next[foundIndex], ...message, id: next[foundIndex].id || message.id || key };
  });

  return next.slice(-120);
}

function mergeSyncConversations(current: Conversation[], incoming: Conversation[]): Conversation[] {
  const currentShared = current[0] || newConversation(SHARED_CHAT_TITLE);
  const incomingShared = incoming[0] || newConversation(currentShared.title || SHARED_CHAT_TITLE);
  return [{
    id: SHARED_CHAT_CONVERSATION_ID,
    title: incomingShared.title || currentShared.title || SHARED_CHAT_TITLE,
    createdAt: currentShared.createdAt || incomingShared.createdAt || new Date().toISOString(),
    messages: mergeSyncMessages(currentShared.messages || [], incomingShared.messages || []),
  }];
}

export default function App() {
  void MAIN_SHARED_CHAT_SYNC_MARKER;
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [rightPanelMode, setRightPanelMode] = useState<RightPanelMode>('status');
  const [highlightTarget, setHighlightTarget] = useState<HighlightTarget>('');
  const [settingsSection, setSettingsSection] = useState<SettingsSection>('model');
  const [settings, setSettings] = useState<EngineSettings>(cloneSettings(DEFAULT_SETTINGS));
  const [draftSettings, setDraftSettings] = useState<EngineSettings>(cloneSettings(DEFAULT_SETTINGS));
  const [models, setModels] = useState<ModelRecord[]>([]);
  const [hubModels, setHubModels] = useState<RemoteHubModel[]>([]);
  const [runtimeStatuses, setRuntimeStatuses] = useState<RuntimeStatus[]>([]);
  const [runtimeDiagnostics, setRuntimeDiagnostics] = useState<RuntimeDiagnostics | null>(null);
  const resizeRef = useRef<{
    handle: ResizeHandle;
    startX: number;
    left: number;
    right: number;
  } | null>(null);
  const chatSyncReadyRef = useRef(false);
  const chatSyncSkipWriteRef = useRef(false);
  const chatSyncWriteTimerRef = useRef<number | null>(null);
  const chatSyncLastRemoteUpdatedAtRef = useRef(0);
  const chatSyncLastLocalWriteAtRef = useRef(0);
  const [status, setStatus] = useState<StatusState>({
    tone: 'idle',
    title: 'Готово',
    detail: 'Локальный движок ожидает команду.',
  });

  const [uploadName, setUploadName] = useState('');
  const [uploadType, setUploadType] = useState('LLM');
  const [uploadFile, setUploadFile] = useState<File | null>(null);

  const [pathName, setPathName] = useState('');
  const [pathType, setPathType] = useState('LLM');
  const [pathValue, setPathValue] = useState('D:\\models\\model.gguf');

  const [runtimeDraft, setRuntimeDraft] = useState<RuntimeSettings>(cloneSettings(DEFAULT_SETTINGS).runtime);
  const [selectedModelId, setSelectedModelId] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('Ты корпоративный помощник. Отвечай кратко и проверяй факты.');
  const [chatInput, setChatInput] = useState('');
  const [conversations, setConversations] = useState<Conversation[]>([newConversation(SHARED_CHAT_TITLE)]);
  const [activeConversationId, setActiveConversationId] = useState(SHARED_CHAT_CONVERSATION_ID);

  const [hubDraftName, setHubDraftName] = useState('');
  const [selectedHubModelId, setSelectedHubModelId] = useState('');
  const [logoFile, setLogoFile] = useState<File | null>(null);

  useEffect(() => {
    setActiveConversationId(SHARED_CHAT_CONVERSATION_ID);
  }, [conversations]);

  useEffect(() => {
    void bootstrap();
  }, []);

  useEffect(() => {
    void refreshRuntimeStatuses();
    void refreshRuntimeDiagnostics();
    const timer = window.setInterval(() => {
      void refreshRuntimeStatuses();
    }, 5000);
    return () => window.clearInterval(timer);
  }, []);


  useEffect(() => {
    let cancelled = false;
    void pullSharedChat(true).finally(() => {
      if (!cancelled) chatSyncReadyRef.current = true;
    });
    const timer = window.setInterval(() => {
      void pullSharedChat(false);
    }, 250);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      if (chatSyncWriteTimerRef.current) {
        window.clearTimeout(chatSyncWriteTimerRef.current);
        chatSyncWriteTimerRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    const handleNativeSync = (event: Event) => {
      const payload = normalizeSyncPayload((event as CustomEvent).detail);
      if (!payload?.conversations?.length) return;
      if (payload.updatedAt) {
        chatSyncLastRemoteUpdatedAtRef.current = Math.max(chatSyncLastRemoteUpdatedAtRef.current, payload.updatedAt);
      }
      applySharedChatPayload(payload, true);
      chatSyncReadyRef.current = true;
    };
    window.addEventListener('local-ai-gpp-chat-sync', handleNativeSync as EventListener);
    return () => window.removeEventListener('local-ai-gpp-chat-sync', handleNativeSync as EventListener);
  }, []);

  useEffect(() => {
    // v65: one shared chat conversation; synchronization is merge-only.
    // A blind write on every React render was racing with the native assistant
    // and could erase the currently generated answer.
    return undefined;
  }, [conversations, activeConversationId]);

  const llmModels = useMemo(() => models.filter((item) => item.type === 'LLM'), [models]);
  const selectedModel = useMemo(
    () => llmModels.find((item) => item.id === selectedModelId) || null,
    [llmModels, selectedModelId],
  );
  const activeConversation = useMemo(
    () => conversations.find((item) => item.id === SHARED_CHAT_CONVERSATION_ID) || conversations[0],
    [conversations],
  );
  const loadedModelIds = useMemo(() => new Set(runtimeStatuses.map((item) => item.model_id)), [runtimeStatuses]);
  const apiDisplayBase = API_BASE || settings.server.public_base_url || window.location.origin;
  const selectedRuntime = useMemo(
    () => runtimeStatuses.find((item) => item.model_id === selectedModelId) || null,
    [runtimeStatuses, selectedModelId],
  );
  const statusEvents = useMemo(
    () => [
      {
        label: status.title,
        detail: status.detail || 'Ожидаю следующую команду.',
        tone: status.tone,
      },
      {
        label: selectedModel ? `Модель: ${selectedModel.name}` : 'Модель не выбрана',
        detail: selectedModel
          ? `${selectedModel.filename} · ${loadedModelIds.has(selectedModel.id) ? 'в памяти' : selectedModel.status || 'saved'}`
          : 'Добавь или выбери LLM в настройках.',
        tone: selectedModel && loadedModelIds.has(selectedModel.id) ? 'success' : 'idle',
      },
      {
        label: 'Runtime',
        detail: runtimeStatuses.length
          ? `${runtimeStatuses.length} активн. · ${selectedRuntime ? `idle ${formatIdle(selectedRuntime.idle_seconds)}` : policyLabel(runtimeDraft.warm_policy)}`
          : `${policyLabel(runtimeDraft.warm_policy)} · моделей в памяти нет`,
        tone: runtimeStatuses.length ? 'success' : 'idle',
      },
      {
        label: 'API',
        detail: `${apiDisplayBase}/v1/chat/completions`,
        tone: 'idle',
      },
    ],
    [apiDisplayBase, loadedModelIds, runtimeDraft.warm_policy, runtimeStatuses, selectedModel, selectedRuntime, status],
  );
  const previewSettings = rightPanelMode === 'settings' || settingsOpen ? { ...draftSettings, runtime: runtimeDraft } : settings;

  function applySharedChatPayload(payload: ChatSyncState, _force = false) {
    if (!payload?.conversations?.length) return;
    const remoteUpdatedAt = Number(payload.updatedAt || 0);
    if (remoteUpdatedAt) {
      chatSyncLastRemoteUpdatedAtRef.current = Math.max(chatSyncLastRemoteUpdatedAtRef.current, remoteUpdatedAt);
    }
    chatSyncSkipWriteRef.current = true;

    if (payload.source === 'reset') {
      setConversations([newConversation(SHARED_CHAT_TITLE)]);
      setActiveConversationId(SHARED_CHAT_CONVERSATION_ID);
      return;
    }

    // V66: в EXE общий диалог живёт на стороне desktop launcher.
    // Главный экран не должен держать "свою" версию и пытаться мерджить
    // поверх старого optimistic-сообщения. Если shared-store уже содержит
    // сообщения, он является каноном и полностью заменяет локальный чат.
    // Единственное исключение: пустой shared-store не стирает локальный
    // optimistic-запрос в первые миллисекунды после отправки.
    const canonicalConversations = mergeSyncConversations([newConversation(SHARED_CHAT_TITLE)], payload.conversations || []);
    const remoteCount = countSyncMessages(canonicalConversations);
    setConversations((prev) => {
      const localCount = countSyncMessages(prev);
      if (remoteCount === 0 && localCount > 0) return prev;
      return canonicalConversations;
    });

    const shared = canonicalConversations[0];
    const lastAssistant = [...(shared?.messages || [])].reverse().find((message) => message.role === 'assistant');
    if (lastAssistant?.pending) {
      setStatus({ tone: 'busy', title: lastAssistant.phase === 'typing' ? 'Ответ печатается' : 'Генерация', detail: lastAssistant.text || 'Модель готовит ответ.' });
    } else if (lastAssistant && (lastAssistant.answer || lastAssistant.text)) {
      setStatus({ tone: 'success', title: 'Ответ получен', detail: formatMessageMeta(lastAssistant) || 'Готово.' });
    }
    setActiveConversationId(SHARED_CHAT_CONVERSATION_ID);
  }

  async function pullSharedChat(initial = false) {
    try {
      const response = await fetch(desktopApiUrl(`/api/desktop/chat-sync?t=${Date.now()}`), { cache: 'no-store' });
      if (!response.ok) return;
      const payload = normalizeSyncPayload(await response.json());
      if (!payload?.conversations?.length) return;
      applySharedChatPayload(payload, initial);
    } catch {
      // Desktop sync exists only inside the EXE launcher. Browser/dev mode can ignore it.
    }
  }


  async function waitForSharedAnswer(assistantId: string) {
    // V66: после отправки из основного окна не ждём милости общего polling.
    // Делаем короткий активный tail именно нужного assistant message. Это не
    // запускает вторую модель: только читает /api/desktop/chat-sync.
    for (let attempt = 0; attempt < 180; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, attempt < 12 ? 350 : 750));
      try {
        const response = await fetch(desktopApiUrl(`/api/desktop/chat-sync?t=${Date.now()}-${attempt}`), { cache: 'no-store' });
        if (!response.ok) continue;
        const payload = normalizeSyncPayload(await response.json());
        if (!payload?.conversations?.length) continue;
        applySharedChatPayload(payload, true);
        const messages = payload.conversations[0]?.messages || [];
        const target = messages.find((message) => message.id === assistantId);
        if (target && !target.pending) return;
      } catch {
        // normal browser mode / transient launcher restart
      }
    }
  }

  async function pushSharedChatSnapshot(nextConversations: Conversation[], _nextActiveConversationId: string) {
    try {
      if (countSyncMessages(nextConversations) === 0) return;
      const stamp = Math.max(Date.now(), chatSyncLastLocalWriteAtRef.current + 1);
      chatSyncLastLocalWriteAtRef.current = stamp;
      const shared = mergeSyncConversations([newConversation(SHARED_CHAT_TITLE)], nextConversations)[0];
      const payload = {
        source: CHAT_SYNC_SOURCE_MAIN,
        updatedAt: stamp,
        activeConversationId: SHARED_CHAT_CONVERSATION_ID,
        conversations: [shared],
      };
      const response = await fetch(desktopApiUrl('/api/desktop/chat-sync'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!response.ok) return;
    } catch {
      // Silent fallback for normal browser mode.
    }
  }

  async function pushSharedChatMessage(_conversation: Conversation, message: ChatMessage) {
    if (!message.text && !message.answer) return;
    try {
      const stamp = Math.max(Date.now(), chatSyncLastLocalWriteAtRef.current + 1);
      chatSyncLastLocalWriteAtRef.current = stamp;
      const payload = {
        source: CHAT_SYNC_SOURCE_MAIN,
        updatedAt: stamp,
        activeConversationId: SHARED_CHAT_CONVERSATION_ID,
        conversationId: SHARED_CHAT_CONVERSATION_ID,
        conversationTitle: SHARED_CHAT_TITLE,
        conversationCreatedAt: conversations[0]?.createdAt || new Date().toISOString(),
        message: { ...message, syncUpdatedAt: stamp },
      };
      const response = await fetch(desktopApiUrl('/api/desktop/chat-message'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!response.ok) return;
    } catch {
      // Silent fallback for normal browser mode.
    }
  }

  async function pushSharedChat() {
    return pushSharedChatSnapshot(conversations, SHARED_CHAT_CONVERSATION_ID);
  }


  async function submitSharedChatRequest(text: string): Promise<boolean> {
    const stamp = Date.now();
    const userId = `shared-user-${stamp}-${Math.random().toString(16).slice(2)}`;
    const assistantId = `shared-assistant-${stamp}-${Math.random().toString(16).slice(2)}`;
    const createdAt = conversations[0]?.createdAt || new Date().toISOString();

    // v65: in EXE mode the desktop endpoint is the single engine. Show the same
    // message in the main UI immediately, then let polling merge the authoritative shared file.
    if (IS_DESKTOP_API_MODE) {
      const optimistic: Conversation = {
        id: SHARED_CHAT_CONVERSATION_ID,
        title: makeTitle(text),
        createdAt,
        messages: [
          { id: userId, role: 'user', text },
          {
            id: assistantId,
            role: 'assistant',
            text: 'Модель готовит ответ. Финальный текст появится здесь же.',
            pending: true,
            phase: 'thinking',
          },
        ],
      };
      setConversations((prev) => mergeSyncConversations(prev, [optimistic]));
      setActiveConversationId(SHARED_CHAT_CONVERSATION_ID);
      setChatInput('');
      setStatus({ tone: 'busy', title: 'Генерация', detail: 'Запрос отправлен в единый общий диалог.' });
    }

    try {
      const response = await fetch(desktopApiUrl('/api/desktop/chat-send'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source: CHAT_SYNC_SOURCE_MAIN,
          message: text,
          user_id: userId,
          assistant_id: assistantId,
          model_id: selectedModelId,
          system_prompt: systemPrompt,
          temperature: Number(runtimeDraft.temperature),
          max_tokens: Number(runtimeDraft.max_tokens),
          runtime: runtimeDraft as unknown as Record<string, unknown>,
        }),
      });
      if (response.status === 404) return false;
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload?.ok === false) {
        const errorText = String(payload?.message || `HTTP ${response.status}`);
        setStatus({
          tone: response.status === 409 ? 'warning' : 'error',
          title: response.status === 409 ? 'Генерация уже идёт' : 'Ошибка чата',
          detail: errorText,
        });
        if (IS_DESKTOP_API_MODE) {
          setConversations((prev) => mergeSyncConversations(prev, [{
            id: SHARED_CHAT_CONVERSATION_ID,
            title: SHARED_CHAT_TITLE,
            createdAt,
            messages: [{ id: assistantId, role: 'assistant', text: errorText, pending: false, phase: 'error' }],
          }]));
        }
        return true;
      }
      setChatInput('');
      if (payload?.state) {
        const statePayload = normalizeSyncPayload(payload.state);
        if (statePayload) applySharedChatPayload(statePayload, true);
      }
      await pullSharedChat(true);
      window.setTimeout(() => void pullSharedChat(true), 250);
      window.setTimeout(() => void pullSharedChat(true), 750);
      void waitForSharedAnswer(assistantId);
      return true;
    } catch {
      return false;
    }
  }

  async function openMiniAssistant() {
    const endpoint = desktopApiUrl('/api/desktop/show-assistant');
    try {
      const response = await fetch(endpoint, { method: 'POST' });
      const payload = await response.json().catch(() => ({ ok: response.ok }));
      if (response.ok && payload?.ok !== false) {
        void pullSharedChat(true);
        setStatus({ tone: 'success', title: 'Мини-помощник', detail: 'Открыт и синхронизирован с основным диалогом.' });
        return;
      }
      setStatus({
        tone: 'error',
        title: 'Мини-помощник не открылся',
        detail: 'Desktop API ответил ошибкой. Проверь %LOCALAPPDATA%\\LocalAIGPP\\launcher.log или dist\\logs\\launcher.log.',
      });
    } catch (error) {
      setStatus({
        tone: 'error',
        title: 'Мини-помощник не открылся',
        detail: `Не удалось вызвать desktop API: ${getErrorMessage(error)}`,
      });
    }
  }

  function openSettings(section?: SettingsSection) {
    setDraftSettings(cloneSettings(settings));
    setRuntimeDraft(cloneSettings(settings).runtime);
    if (section) setSettingsSection(section);
    setRightPanelMode('settings');
    setSettingsOpen(false);
  }

  function closeSettingsPreview() {
    setRightPanelMode('status');
    setHighlightTarget('');
    setDraftSettings(cloneSettings(settings));
    setRuntimeDraft(cloneSettings(settings).runtime);
  }

  function updatePreview(path: string, value: DraftValue, target: HighlightTarget) {
    setHighlightTarget(target);
    updateDraft(path, value);
  }

  async function bootstrap() {
    try {
      setStatus({ tone: 'busy', title: 'Загрузка', detail: 'Синхронизирую модели и настройки.' });
      const data = await fetchBootstrap();
      const nextSettings = mergeDeep(DEFAULT_SETTINGS, data.settings || {});
      const nextModels = Array.isArray(data.models) ? data.models : [];
      setSettings(nextSettings);
      setDraftSettings(cloneSettings(nextSettings));
      setRuntimeDraft(cloneSettings(nextSettings).runtime);
      setModels(nextModels);
      const running = nextModels.find((item) => item.type === 'LLM' && item.status === 'running');
      const firstLlm = running || nextModels.find((item) => item.type === 'LLM');
      if (firstLlm) setSelectedModelId(firstLlm.id);
      await refreshRuntimeStatuses();
      setStatus({ tone: 'success', title: 'Готово', detail: 'Интерфейс собран вокруг чата.' });
    } catch (error) {
      console.error(error);
      setStatus({ tone: 'error', title: 'Ошибка bootstrap', detail: getErrorMessage(error) });
    }
  }

  async function refreshRuntimeStatuses() {
    try {
      setRuntimeStatuses(await fetchRuntimeStatus());
    } catch {
      setRuntimeStatuses([]);
    }
  }

  async function refreshRuntimeDiagnostics() {
    try {
      setRuntimeDiagnostics(await fetchRuntimeDiagnostics());
    } catch (error) {
      setRuntimeDiagnostics({
        python: '',
        platform: '',
        package_installed: false,
        package_version: '',
        package_path: '',
        nvidia_smi_found: false,
        nvidia_smi: '',
        supported_parameters: [],
        gpu_related_supported: [],
        supports_gpu_offload: null,
        system_info: '',
        gpu_backend_flags: [],
        likely_cpu_build: true,
        summary: getErrorMessage(error),
        recommendations: ['Не удалось получить диагностику runtime. Проверь, что backend запущен.'],
        install_commands: {
          cuda: '',
          vulkan: '',
          cpu: '',
        },
      });
    }
  }

  function updateDraft(path: string, value: DraftValue) {
    setDraftSettings((prev) => {
      const next = cloneSettings(prev);
      const parts = path.split('.');
      let current: any = next;
      for (let i = 0; i < parts.length - 1; i += 1) {
        current[parts[i]] = current[parts[i]] || {};
        current = current[parts[i]];
      }
      current[parts[parts.length - 1]] = value;
      return next;
    });
  }

  function updateLayoutDraft(path: keyof EngineSettings['layout'], value: number) {
    const normalized = Math.round(value);
    setSettings((prev) => ({
      ...prev,
      layout: {
        ...prev.layout,
        [path]: normalized,
      },
    }));
    setDraftSettings((prev) => ({
      ...prev,
      layout: {
        ...prev.layout,
        [path]: normalized,
      },
    }));
    setHighlightTarget(path === 'left_panel_width' ? 'workspace' : path === 'right_panel_width' ? 'status' : 'chat');
  }

  function beginPanelResize(handle: ResizeHandle, event: React.PointerEvent<HTMLDivElement>) {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    resizeRef.current = {
      handle,
      startX: event.clientX,
      left: draftSettings.layout.left_panel_width,
      right: draftSettings.layout.right_panel_width,
    };
    setHighlightTarget(handle === 'left' ? 'workspace' : 'status');
  }

  function movePanelResize(event: React.PointerEvent<HTMLDivElement>) {
    const resize = resizeRef.current;
    if (!resize) return;
    const delta = event.clientX - resize.startX;
    if (resize.handle === 'left') {
      updateLayoutDraft('left_panel_width', clamp(resize.left + delta, 180, 520));
    } else {
      updateLayoutDraft('right_panel_width', clamp(resize.right - delta, 360, 1080));
    }
  }

  function endPanelResize(event: React.PointerEvent<HTMLDivElement>) {
    if (!resizeRef.current) return;
    try {
      event.currentTarget.releasePointerCapture(event.pointerId);
    } catch {
      // The browser may already release capture when the pointer leaves the viewport.
    }
    resizeRef.current = null;
    setHighlightTarget('');
  }

  function updateRuntimeDraft(key: keyof RuntimeSettings, value: string | number | boolean) {
    setRuntimeDraft((prev) => ({ ...prev, [key]: value }));
    setDraftSettings((prev) => ({ ...prev, runtime: { ...prev.runtime, [key]: value } }));
  }

  function applyRuntimePreset(preset: 'cpu' | 'hybrid' | 'gpu') {
    if (preset !== 'cpu' && runtimeDiagnostics?.supports_gpu_offload !== true) {
      setStatus({
        tone: 'warning',
        title: 'GPU runtime не установлен',
        detail: 'Сейчас стоит CPU-сборка llama-cpp-python. Установи CUDA/Vulkan runtime, затем снова проверь диагностику.',
      });
      return;
    }
    const patch: Partial<RuntimeSettings> =
      preset === 'cpu'
        ? { n_gpu_layers: 0, split_mode: 'none', tensor_split: '', flash_attn: false, op_offload: false, offload_kqv: true, gpu_fallback_to_cpu: true }
        : preset === 'hybrid'
          ? { n_gpu_layers: 24, split_mode: 'layer', tensor_split: '', flash_attn: false, op_offload: true, offload_kqv: true, gpu_fallback_to_cpu: true }
          : { n_gpu_layers: -1, split_mode: 'layer', tensor_split: '', flash_attn: false, op_offload: true, offload_kqv: true, gpu_fallback_to_cpu: false };
    setRuntimeDraft((prev) => ({ ...prev, ...patch }));
    setDraftSettings((prev) => ({ ...prev, runtime: { ...prev.runtime, ...patch } }));
    setStatus({
      tone: 'warning',
      title: 'Runtime изменен',
      detail: 'Выгрузи и снова прогрей модель, чтобы параметры GPU применились.',
    });
  }

  function appendActiveMessage(message: ChatMessage) {
    const targetId = SHARED_CHAT_CONVERSATION_ID;
    const messageWithId: ChatMessage = {
      ...message,
      id: message.id || `${message.role}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    };
    setConversations((prev) => {
      const next = prev.map((conversation) => {
        if (conversation.id !== targetId) return conversation;
        const shouldRename = conversation.messages.length === 0 && messageWithId.role === 'user';
        return {
          ...conversation,
          title: shouldRename ? makeTitle(messageWithId.text) : conversation.title,
          messages: [...conversation.messages, messageWithId],
        };
      });
      const target = next.find((conversation) => conversation.id === targetId) || next[0];
      if (target) queueMicrotask(() => void pushSharedChatMessage(target, messageWithId));
      return next;
    });
  }


  function updateActiveMessage(messageId: string, patch: Partial<ChatMessage>) {
    const targetId = SHARED_CHAT_CONVERSATION_ID;
    setConversations((prev) => {
      let updatedMessage: ChatMessage | null = null;
      const next = prev.map((conversation) => {
        if (conversation.id !== targetId) return conversation;
        return {
          ...conversation,
          messages: conversation.messages.map((message) => {
            if (message.id !== messageId) return message;
            const mergedMessage = { ...message, ...patch } as ChatMessage;
            updatedMessage = mergedMessage;
            return mergedMessage;
          }),
        };
      });
      const target = next.find((conversation) => conversation.id === targetId) || next[0];
      if (target && updatedMessage) queueMicrotask(() => void pushSharedChatMessage(target, updatedMessage as ChatMessage));
      return next;
    });
  }


  function handleNewConversation() {
    setConversations([newConversation(SHARED_CHAT_TITLE)]);
    setActiveConversationId(SHARED_CHAT_CONVERSATION_ID);
    setChatInput('');
    void fetch(desktopApiUrl('/api/desktop/chat-reset'), { method: 'POST' }).catch(() => undefined);
  }

  async function handleSubmitUpload(e: React.FormEvent) {
    e.preventDefault();
    if (!uploadName.trim() || !uploadFile) {
      setStatus({ tone: 'warning', title: 'Не хватает данных', detail: 'Укажи название и файл модели.' });
      return;
    }
    try {
      setStatus({ tone: 'busy', title: 'Загрузка модели', detail: uploadFile.name });
      const form = new FormData();
      form.append('model_name', uploadName.trim());
      form.append('model_type', uploadType);
      form.append('copy_to_storage', 'true');
      form.append('model_file', uploadFile);
      const created = await uploadModel(form);
      setModels((prev) => [created, ...prev.filter((m) => m.id !== created.id)]);
      if (created.type === 'LLM') setSelectedModelId(created.id);
      setUploadName('');
      setUploadType('LLM');
      setUploadFile(null);
      setStatus({ tone: 'success', title: 'Модель сохранена', detail: created.id });
    } catch (error) {
      setStatus({ tone: 'error', title: 'Ошибка загрузки', detail: getErrorMessage(error) });
    }
  }

  async function handleSubmitPath(e: React.FormEvent) {
    e.preventDefault();
    if (!pathName.trim() || !pathValue.trim()) {
      setStatus({ tone: 'warning', title: 'Не хватает данных', detail: 'Укажи название и абсолютный путь к файлу.' });
      return;
    }
    try {
      setStatus({ tone: 'busy', title: 'Регистрация пути', detail: pathValue.trim() });
      const form = new FormData();
      form.append('model_name', pathName.trim());
      form.append('model_type', pathType);
      form.append('model_path', pathValue.trim());
      form.append('runtime_json', JSON.stringify(runtimeDraft));
      const created = await registerModelPath(form);
      setModels((prev) => [created, ...prev.filter((m) => m.id !== created.id)]);
      if (created.type === 'LLM') setSelectedModelId(created.id);
      setPathName('');
      setStatus({ tone: 'success', title: 'Путь зарегистрирован', detail: created.id });
    } catch (error) {
      setStatus({ tone: 'error', title: 'Ошибка регистрации', detail: getErrorMessage(error) });
    }
  }

  async function handleValidateModels() {
    try {
      setStatus({ tone: 'busy', title: 'Проверка моделей', detail: 'Сверяю реестр с файлами на диске.' });
      const checked = await validateModels();
      setModels(checked);
      const missingCount = checked.filter((model) => model.file_exists === false && !String(model.path || '').startsWith('HUB::')).length;
      setStatus({
        tone: missingCount ? 'warning' : 'success',
        title: missingCount ? 'Есть отсутствующие файлы' : 'Каталог моделей проверен',
        detail: missingCount ? `Не найдено файлов: ${missingCount}` : `Проверено моделей: ${checked.length}`,
      });
    } catch (error) {
      setStatus({ tone: 'error', title: 'Ошибка проверки', detail: getErrorMessage(error) });
    }
  }

  async function handleReloadHubModels() {
    try {
      setStatus({ tone: 'busy', title: 'Обновление хаба', detail: 'Читаю список удаленных моделей.' });
      const remote = await fetchHubModels();
      setHubModels(remote);
      setStatus({ tone: 'success', title: 'Хаб обновлен', detail: `Получено моделей: ${remote.length}` });
    } catch (error) {
      setStatus({ tone: 'error', title: 'Ошибка хаба', detail: getErrorMessage(error) });
    }
  }

  async function handleImportHubModel() {
    if (!selectedHubModelId) {
      setStatus({ tone: 'warning', title: 'Ничего не выбрано', detail: 'Выбери модель из списка хаба.' });
      return;
    }
    try {
      setStatus({ tone: 'busy', title: 'Импорт из хаба', detail: selectedHubModelId });
      const created = await importHubModel({
        model_id: selectedHubModelId,
        name: hubDraftName.trim() || selectedHubModelId,
      });
      setModels((prev) => [created, ...prev.filter((m) => m.id !== created.id)]);
      if (created.type === 'LLM') setSelectedModelId(created.id);
      setStatus({ tone: 'success', title: 'Модель импортирована', detail: created.id });
    } catch (error) {
      setStatus({ tone: 'error', title: 'Ошибка импорта', detail: getErrorMessage(error) });
    }
  }

  async function handleStartModel(id: string) {
    try {
      setStatus({ tone: 'busy', title: 'Запуск модели', detail: id });
      const updated = await startModel(id);
      setModels((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
      if (updated.type === 'LLM') setSelectedModelId(updated.id);
      await refreshRuntimeStatuses();
      setStatus({ tone: 'success', title: 'Модель запущена', detail: updated.id });
    } catch (error) {
      setStatus({ tone: 'error', title: 'Ошибка запуска', detail: getErrorMessage(error) });
    }
  }

  async function handlePrewarmModel(id: string) {
    try {
      setStatus({ tone: 'busy', title: 'Прогрев модели', detail: id });
      const updated = await prewarmModel(id, runtimeDraft as unknown as Record<string, unknown>);
      setModels((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
      if (updated.type === 'LLM') setSelectedModelId(updated.id);
      await refreshRuntimeStatuses();
      setStatus({ tone: 'success', title: 'Модель в памяти', detail: updated.id });
    } catch (error) {
      setStatus({ tone: 'error', title: 'Ошибка прогрева', detail: getErrorMessage(error) });
    }
  }

  async function handleUnloadModel(id: string) {
    try {
      setStatus({ tone: 'busy', title: 'Выгрузка модели', detail: id });
      await unloadModel(id);
      setModels((prev) => prev.map((item) => (item.id === id ? { ...item, status: 'saved' } : item)));
      await refreshRuntimeStatuses();
      setStatus({ tone: 'success', title: 'Модель выгружена', detail: id });
    } catch (error) {
      setStatus({ tone: 'error', title: 'Ошибка выгрузки', detail: getErrorMessage(error) });
    }
  }

  async function handleDeleteModel(id: string) {
    try {
      setStatus({ tone: 'busy', title: 'Удаление модели', detail: id });
      await deleteModel(id);
      setModels((prev) => prev.filter((item) => item.id !== id));
      if (selectedModelId === id) setSelectedModelId('');
      await refreshRuntimeStatuses();
      setStatus({ tone: 'success', title: 'Модель удалена', detail: id });
    } catch (error) {
      setStatus({ tone: 'error', title: 'Ошибка удаления', detail: getErrorMessage(error) });
    }
  }

  async function handleSendChat(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedModelId) {
      setStatus({ tone: 'warning', title: 'Выбери модель', detail: 'Для тестового чата нужна LLM-модель.' });
      return;
    }
    if (selectedModel?.file_exists === false) {
      setStatus({ tone: 'warning', title: 'Файл модели не найден', detail: selectedModel.validation_error || selectedModel.path });
      return;
    }
    if (!chatInput.trim()) return;
    const text = chatInput.trim();
    if (await submitSharedChatRequest(text)) {
      return;
    }
    appendActiveMessage({ role: 'user', text });
    const assistantId = `assistant-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    appendActiveMessage({
      id: assistantId,
      role: 'assistant',
      text: 'Модель готовит ответ. Финальный текст появится после рассуждения.',
      reasoning: '<think>\nМодель читает запрос, применяет system prompt и готовит план ответа...',
      pending: true,
      phase: 'thinking',
    });
    setChatInput('');
    const thinkingTimer = window.setTimeout(() => {
      updateActiveMessage(assistantId, {
        phase: 'reasoning_done',
        reasoning: 'Модель загружается или готовит первый токен. Как только придет поток <think>, он появится здесь в реальном времени.',
        text: 'Жду первый токен от runtime.',
      });
    }, 1200);
    try {
      setStatus({ tone: 'busy', title: 'Генерация', detail: selectedModelId });
      let rawContent = '';
      let finalEvent: {
        answer?: string;
        reasoning?: string;
        answer_state?: string;
        reasoning_truncated?: boolean;
        finish_reason?: string | null;
        elapsed_ms?: number;
        usage?: ChatMessage['usage'];
        request_id?: string;
        log_path?: string;
        log_excerpt?: string[];
        runtime?: { mode?: string };
      } = {};
      await streamChat({
        model_id: selectedModelId,
        message: text,
        system_prompt: systemPrompt,
        temperature: Number(runtimeDraft.temperature),
        max_tokens: Number(runtimeDraft.max_tokens),
        runtime: runtimeDraft as unknown as Record<string, unknown>,
      }, (event) => {
        if (event.type === 'meta') {
          updateActiveMessage(assistantId, {
            request_id: event.request_id,
            log_path: event.log_path,
          });
          return;
        }
        if (event.type === 'worker_status') {
          updateActiveMessage(assistantId, {
            pending: true,
            phase: 'reasoning_done',
            text: event.message || 'Runtime запускается.',
          });
          return;
        }
        if (event.type === 'runtime') {
          updateActiveMessage(assistantId, {
            runtime_mode: event.mode || event.runtime?.mode,
          });
          return;
        }
        if (event.type === 'delta') {
          rawContent += event.text || '';
          const live = parseLiveModelContent(rawContent);
          updateActiveMessage(assistantId, {
            pending: true,
            phase: live.phase,
            reasoning: live.reasoning,
            text: live.answer || (live.phase === 'thinking' ? 'Модель думает. Финальный ответ еще не начался.' : ''),
            answer: live.answer,
          });
          return;
        }
        if (event.type === 'done') {
          finalEvent = {
            answer: event.answer,
            reasoning: event.reasoning,
            answer_state: event.answer_state,
            reasoning_truncated: event.reasoning_truncated,
            finish_reason: event.finish_reason,
            elapsed_ms: event.elapsed_ms,
            usage: event.usage as ChatMessage['usage'],
            request_id: event.request_id,
            log_path: event.log_path,
            log_excerpt: event.log_excerpt,
            runtime: event.runtime,
          };
          const live = parseLiveModelContent(event.content || rawContent);
          updateActiveMessage(assistantId, {
            pending: false,
            phase: 'done',
            text: event.answer || live.answer || 'Финальный ответ не сформировался: модель израсходовала лимит на рассуждение. Увеличь Max tokens или повтори запрос.',
            answer: event.answer || live.answer,
            reasoning: event.reasoning || live.reasoning,
            answer_state: event.answer_state,
            reasoning_truncated: event.reasoning_truncated,
            finish_reason: event.finish_reason,
            usage: event.usage as ChatMessage['usage'],
            elapsed_ms: event.elapsed_ms,
            request_id: event.request_id,
            log_path: event.log_path,
            log_excerpt: event.log_excerpt,
            runtime_mode: event.runtime?.mode,
          });
          return;
        }
        if (event.type === 'error') {
          const error = new Error(event.message || 'Ошибка stream') as Error & { request_id?: string; log_path?: string; log_excerpt?: string[] };
          error.request_id = event.request_id;
          error.log_path = event.log_path;
          error.log_excerpt = event.log_excerpt;
          throw error;
        }
      });
      window.clearTimeout(thinkingTimer);
      await refreshRuntimeStatuses();
      setStatus({
        tone: finalEvent.answer_state === 'missing_after_reasoning' ? 'warning' : 'success',
        title: finalEvent.answer_state === 'missing_after_reasoning' ? 'Ответ не завершен' : 'Ответ получен',
        detail: `${buildResponseSummary(finalEvent.elapsed_ms, finalEvent.usage, finalEvent.finish_reason)} · ${finalEvent.runtime?.mode || 'runtime не определен'}`,
      });
    } catch (error) {
      window.clearTimeout(thinkingTimer);
      const message = getErrorMessage(error);
      const richError = error as Error & { request_id?: string; log_path?: string; log_excerpt?: string[] };
      updateActiveMessage(assistantId, {
        pending: false,
        phase: 'error',
        reasoning: '',
        text: `Ошибка: ${message}`,
        request_id: richError.request_id,
        log_path: richError.log_path,
        log_excerpt: richError.log_excerpt,
      });
      setStatus({ tone: 'error', title: 'Ошибка чата', detail: message });
    }
  }

  async function copyMessageText(message: ChatMessage) {
    const parts = [
      message.reasoning ? `Рассуждение модели:\n${message.reasoning}` : '',
      message.answer || message.text,
    ].filter(Boolean);
    try {
      if (!navigator.clipboard?.writeText) {
        throw new Error('Clipboard API недоступен в этом окружении.');
      }
      await navigator.clipboard.writeText(parts.join('\n\n'));
      setStatus({ tone: 'success', title: 'Текст скопирован', detail: 'Сообщение помещено в буфер обмена.' });
    } catch (error) {
      setStatus({ tone: 'error', title: 'Ошибка копирования', detail: getErrorMessage(error) });
    }
  }

  async function handleSaveSettings() {
    try {
      setStatus({ tone: 'busy', title: 'Сохранение настроек', detail: 'Применяю профиль движка.' });
      const payload = { ...draftSettings, runtime: runtimeDraft };
      const saved = await saveSettings(payload);
      const normalized = mergeDeep(DEFAULT_SETTINGS, saved);
      setSettings(normalized);
      setDraftSettings(cloneSettings(normalized));
      setRuntimeDraft(cloneSettings(normalized).runtime);
      setStatus({ tone: 'success', title: 'Настройки сохранены', detail: 'Часть серверных параметров применится после перезапуска.' });
    } catch (error) {
      setStatus({ tone: 'error', title: 'Ошибка сохранения', detail: getErrorMessage(error) });
    }
  }

  async function handleUploadLogo() {
    if (!logoFile) {
      setStatus({ tone: 'warning', title: 'Выбери файл', detail: 'Нужен PNG, JPG, WEBP или SVG.' });
      return;
    }
    try {
      setStatus({ tone: 'busy', title: 'Загрузка логотипа', detail: logoFile.name });
      const saved = await uploadLogo(logoFile);
      const normalized = mergeDeep(DEFAULT_SETTINGS, saved);
      setSettings(normalized);
      setDraftSettings(cloneSettings(normalized));
      setStatus({ tone: 'success', title: 'Логотип обновлен', detail: logoFile.name });
    } catch (error) {
      setStatus({ tone: 'error', title: 'Ошибка логотипа', detail: getErrorMessage(error) });
    }
  }

  const gpuBackendReady = runtimeDiagnostics?.supports_gpu_offload === true;
  const gpuPresetHint = gpuBackendReady
    ? 'GPU backend найден. Профиль можно применить.'
    : 'GPU backend не найден: текущий llama-cpp-python собран без CUDA/Vulkan, поэтому GPU-профили заблокированы.';

  return (
    <div
      className={`engine-app ${highlightTarget ? `preview-${highlightTarget}` : ''}`}
      style={{
        ['--accent' as string]: previewSettings.theme.accent,
        ['--hero-text' as string]: previewSettings.theme.hero_text,
        ['--chrome-start' as string]: previewSettings.theme.chrome_start,
        ['--chrome-end' as string]: previewSettings.theme.chrome_end,
        ['--chrome-text' as string]: previewSettings.theme.chrome_text,
        ['--background-start' as string]: previewSettings.theme.background_start,
        ['--background-end' as string]: previewSettings.theme.background_end,
        ['--panel' as string]: previewSettings.theme.panel,
        ['--panel-alt' as string]: previewSettings.theme.panel_alt,
        ['--border' as string]: previewSettings.theme.border,
        ['--text' as string]: previewSettings.theme.text,
        ['--muted' as string]: previewSettings.theme.muted,
        ['--user-bubble' as string]: previewSettings.theme.user_bubble,
        ['--assistant-bubble' as string]: previewSettings.theme.assistant_bubble,
        ['--success' as string]: previewSettings.theme.success,
        ['--warning' as string]: previewSettings.theme.warning,
        ['--danger' as string]: previewSettings.theme.danger,
        ['--app-max-width' as string]: `${previewSettings.layout.app_max_width}px`,
        ['--left-panel-width' as string]: `${previewSettings.layout.left_panel_width}px`,
        ['--center-panel-min-width' as string]: `${previewSettings.layout.center_panel_min_width}px`,
        ['--right-panel-width' as string]: `${previewSettings.layout.right_panel_width}px`,
        ['--card-radius' as string]: `${previewSettings.layout.card_radius}px`,
        ['--logo-width' as string]: `${previewSettings.branding.logo_width}px`,
        ['--logo-height' as string]: `${previewSettings.branding.logo_height}px`,
        ['--logo-radius' as string]: `${previewSettings.branding.logo_radius}px`,
        ['--logo-padding' as string]: `${previewSettings.branding.logo_padding}px`,
      } as React.CSSProperties}
    >
      <header className="top-ribbon">
        <div className="ribbon-brand">
          <div className="ribbon-logo">
            {previewSettings.branding.logo_url ? (
              <img src={assetUrl(normalizeLogoAsset(previewSettings.branding.logo_url))} alt="" style={{ objectFit: previewSettings.branding.logo_fit }} />
            ) : (
              <span>AI</span>
            )}
          </div>
          <div>
            <strong>{previewSettings.branding.title || 'Агент ГПП'}</strong>
            <span>{previewSettings.branding.subtitle || 'Локальный движок LLM моделей.'}</span>
          </div>
        </div>
        <div className="ribbon-center">
          <span>Локальный контур</span>
          <code>{apiDisplayBase}/v1/chat/completions</code>
        </div>
        <div className="ribbon-actions">
          <select value={selectedModelId} onChange={(e) => setSelectedModelId(e.target.value)} aria-label="Модель">
            <option value="">Выбери LLM</option>
            {llmModels.map((model) => (
              <option key={model.id} value={model.id} disabled={model.file_exists === false}>
                {model.name} / {model.filename}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="quiet-button mini-assistant-button"
            onClick={openMiniAssistant}
          >
            Мини-помощник
          </button>
        </div>
      </header>

      <aside className="workspace-nav">
        <div className="side-head">
          <span className="eyebrow">Диалоги</span>
          <strong>{conversations.length}</strong>
        </div>

        <button type="button" className="primary-action" onClick={handleNewConversation}>
          Новый диалог
        </button>

        <nav className="conversation-list" aria-label="Диалоги">
          {conversations.map((conversation) => (
            <button
              type="button"
              key={conversation.id}
              className={`conversation-item ${conversation.id === activeConversationId ? 'active' : ''}`}
              onClick={() => setActiveConversationId(conversation.id)}
            >
              <span>{conversation.title}</span>
              <small>{conversation.messages.length} сообщений</small>
            </button>
          ))}
        </nav>

        <div className={`status-card ${status.tone}`}>
          <strong>{status.title}</strong>
          <span>{status.detail}</span>
        </div>
      </aside>

      <div
        className="panel-resizer panel-resizer-left"
        role="separator"
        aria-label="Изменить ширину панели диалогов"
        onPointerDown={(event) => beginPanelResize('left', event)}
        onPointerMove={movePanelResize}
        onPointerUp={endPanelResize}
        onPointerCancel={endPanelResize}
      />

      <main className="chat-main">
        <header className="chat-header">
          <div>
            <span className="eyebrow">Локальный тестовый контур</span>
            <h2>{activeConversation?.title || 'Диалог'}</h2>
          </div>
          <div className="header-actions">
            <button type="button" className="quiet-button" onClick={() => openSettings('runtime')}>
              Runtime
            </button>
            <button type="button" className="quiet-button" onClick={() => openSettings('server')}>
              API
            </button>
          </div>
        </header>

        <section className="system-strip">
          <label>
            <span>System prompt</span>
            <textarea rows={2} value={systemPrompt} onChange={(e) => setSystemPrompt(e.target.value)} />
          </label>
          <div className="quick-runtime">
            <label>
              <span>temperature</span>
              <input
                type="number"
                min="0"
                max="2"
                step="0.1"
                value={runtimeDraft.temperature}
                onChange={(e) => updateRuntimeDraft('temperature', Number(e.target.value))}
              />
            </label>
            <label>
              <span>max tokens</span>
              <input
                type="number"
                min="16"
                value={runtimeDraft.max_tokens}
                onChange={(e) => updateRuntimeDraft('max_tokens', Number(e.target.value))}
              />
            </label>
          </div>
        </section>

        <section className="chat-window" aria-live="polite">
          {!activeConversation || activeConversation.messages.length === 0 ? (
            <div className="empty-chat">
              <h3>Напиши первый запрос</h3>
              <p>Здесь удобно проверять модель перед тем, как подключать ее к Excel-плагину, CAD-сценарию или другому локальному приложению.</p>
            </div>
          ) : (
            activeConversation.messages.map((msg, idx) => (
              <article key={msg.id || `${msg.role}-${idx}`} className={`message ${msg.role}`}>
                <div className="message-head">
                  <span>{msg.role === 'user' ? 'Вы' : 'Модель'}</span>
                  <div className="message-tools">
                    {msg.role === 'assistant' && <small>{formatMessageMeta(msg)}</small>}
                    <button type="button" className="copy-message" disabled={msg.pending} onClick={() => void copyMessageText(msg)}>
                      Копировать
                    </button>
                  </div>
                </div>
                {msg.role === 'assistant' && (msg.reasoning || msg.pending) && (
                  <details open={msg.pending} className={`reasoning-block ${msg.reasoning_truncated ? 'warning' : ''} ${msg.pending ? 'thinking' : ''}`}>
                    <summary>
                      {msg.pending && msg.phase !== 'typing' ? 'Модель думает' : msg.reasoning ? 'Рассуждение модели оформлено' : 'Рассуждение модели'}
                      {msg.reasoning_truncated && <span>не завершено</span>}
                      {msg.pending && <span className="dot-loader" aria-hidden="true" />}
                    </summary>
                    <pre>{msg.reasoning || '<think>\nОжидаю рассуждение модели...'}</pre>
                  </details>
                )}
                <div className="message-bubble">
                  {msg.role === 'assistant' && msg.answer_state === 'extracted_from_reasoning' && (
                    <strong className="answer-note">Ответ восстановлен из рассуждения</strong>
                  )}
                  {msg.role === 'assistant' && msg.answer_state === 'missing_after_reasoning' && (
                    <strong className="answer-note warning">Финальный ответ не успел сформироваться</strong>
                  )}
                  {msg.role === 'assistant' && msg.pending && msg.phase !== 'typing' && (
                    <div className="generation-status">
                      <span className="spinner" aria-hidden="true" />
                      <strong>{msg.phase === 'reasoning_done' ? 'Рассуждение завершено, жду финальный ответ' : 'Генерация запущена'}</strong>
                    </div>
                  )}
                  <p>
                    {msg.answer || msg.text}
                    {msg.role === 'assistant' && msg.phase === 'typing' && <span className="typing-cursor" aria-hidden="true" />}
                  </p>
                  {msg.role === 'assistant' && (msg.log_path || msg.runtime_mode || msg.log_excerpt?.length) && (
                    <details className="request-log">
                      <summary>Лог выполнения запроса</summary>
                      {msg.runtime_mode && <code>Runtime: {msg.runtime_mode}</code>}
                      {msg.request_id && <code>Request: {msg.request_id}</code>}
                      {msg.log_path && <code>{msg.log_path}</code>}
                      {msg.log_excerpt?.length ? <pre>{msg.log_excerpt.join('\n')}</pre> : null}
                    </details>
                  )}
                </div>
              </article>
            ))
          )}
        </section>

        <form className="composer" onSubmit={handleSendChat}>
          <input
            value={chatInput}
            placeholder="Сообщение для локальной модели"
            onChange={(e) => setChatInput(e.target.value)}
          />
          <button type="submit">Отправить</button>
        </form>
      </main>

      <div
        className="panel-resizer panel-resizer-right"
        role="separator"
        aria-label="Изменить ширину панели настроек"
        onPointerDown={(event) => beginPanelResize('right', event)}
        onPointerMove={movePanelResize}
        onPointerUp={endPanelResize}
        onPointerCancel={endPanelResize}
      />

      <aside className="runtime-panel status-panel">
        <div className="right-switcher">
          <button
            type="button"
            className={rightPanelMode === 'status' ? 'active' : ''}
            onClick={closeSettingsPreview}
          >
            Состояние
          </button>
          <button
            type="button"
            className={rightPanelMode === 'settings' ? 'active' : ''}
            onClick={() => openSettings('appearance')}
          >
            Настройки
          </button>
        </div>

        {rightPanelMode === 'settings' ? (
          <section className="side-settings">
            <div className="section-head compact">
              <div>
                <span className="eyebrow">Предпросмотр</span>
                <h3>Настройки</h3>
              </div>
              <div className="button-row">
                <button type="button" onClick={() => void handleSaveSettings()}>Сохранить</button>
                <button type="button" className="quiet-button" onClick={closeSettingsPreview}>Отмена</button>
              </div>
            </div>
            <div className="settings-tabs vertical">
              {[
                ['appearance', 'Интерфейс'],
                ['runtime', 'Вычисления'],
                ['server', 'Сервер API'],
                ['model', 'Каталог моделей'],
                ['hub', 'Репозиторий'],
              ].map(([id, label]) => (
                <button
                  type="button"
                  key={id}
                  className={settingsSection === id ? 'active' : ''}
                  onClick={() => setSettingsSection(id as SettingsSection)}
                >
                  {label}
                </button>
              ))}
            </div>

            {settingsSection === 'appearance' && (
              <div className="side-settings-content">
                <section className="settings-box">
                  <h3>Бренд</h3>
                  <div className="logo-upload-panel">
                    <div className="logo-preview">
                      {draftSettings.branding.logo_url ? (
                        <img src={assetUrl(normalizeLogoAsset(draftSettings.branding.logo_url))} alt="" style={{ objectFit: draftSettings.branding.logo_fit }} />
                      ) : (
                        <span>AI</span>
                      )}
                    </div>
                    <div>
                      <strong>{draftSettings.branding.title}</strong>
                      <p>{draftSettings.branding.subtitle}</p>
                    </div>
                  </div>
                  <label><span>Название</span><input value={draftSettings.branding.title} onFocus={() => setHighlightTarget('ribbon')} onChange={(e) => updatePreview('branding.title', e.target.value, 'ribbon')} /></label>
                  <label><span>Подзаголовок</span><input value={draftSettings.branding.subtitle} onFocus={() => setHighlightTarget('ribbon')} onChange={(e) => updatePreview('branding.subtitle', e.target.value, 'ribbon')} /></label>
                  <div className="field-grid compact">
                    <label><span>Ширина</span><input type="number" value={draftSettings.branding.logo_width} onFocus={() => setHighlightTarget('logo')} onChange={(e) => updatePreview('branding.logo_width', Number(e.target.value), 'logo')} /></label>
                    <label><span>Высота</span><input type="number" value={draftSettings.branding.logo_height} onFocus={() => setHighlightTarget('logo')} onChange={(e) => updatePreview('branding.logo_height', Number(e.target.value), 'logo')} /></label>
                    <label><span>Радиус</span><input type="number" value={draftSettings.branding.logo_radius} onFocus={() => setHighlightTarget('logo')} onChange={(e) => updatePreview('branding.logo_radius', Number(e.target.value), 'logo')} /></label>
                    <label><span>Отступ</span><input type="number" value={draftSettings.branding.logo_padding} onFocus={() => setHighlightTarget('logo')} onChange={(e) => updatePreview('branding.logo_padding', Number(e.target.value), 'logo')} /></label>
                  </div>
                  <label>
                    <span>Вписывание</span>
                    <select value={draftSettings.branding.logo_fit} onFocus={() => setHighlightTarget('logo')} onChange={(e) => updatePreview('branding.logo_fit', e.target.value, 'logo')}>
                      <option value="contain">contain</option>
                      <option value="cover">cover</option>
                      <option value="fill">fill</option>
                    </select>
                  </label>
                  <div className="file-upload-row">
                    <input type="file" accept=".svg,.png,.jpg,.jpeg,.webp" onChange={(e) => setLogoFile(e.target.files?.[0] || null)} />
                    <button type="button" onClick={() => void handleUploadLogo()}>Загрузить</button>
                  </div>
                </section>

                <section className="settings-box workspace-settings">
                  <h3>Рабочие зоны</h3>
                  <label className="help-field" data-help="Ширина списка диалогов. Можно также тянуть ручку между диалогами и чатом.">
                    <span>Диалоги, px</span>
                    <input type="number" min={180} max={520} value={draftSettings.layout.left_panel_width} onFocus={() => setHighlightTarget('workspace')} onChange={(e) => updatePreview('layout.left_panel_width', clamp(Number(e.target.value), 180, 520), 'workspace')} />
                  </label>
                  <label className="help-field" data-help="Минимальная ширина центрального чата. Обычно 420-640 px, остальное место отдаётся автоматически.">
                    <span>Чат min, px</span>
                    <input type="number" min={360} max={980} value={draftSettings.layout.center_panel_min_width} onFocus={() => setHighlightTarget('chat')} onChange={(e) => updatePreview('layout.center_panel_min_width', clamp(Number(e.target.value), 360, 980), 'chat')} />
                  </label>
                  <label className="help-field" data-help="Ширина правой панели статуса и настроек. Можно тянуть ручку между чатом и правой панелью.">
                    <span>Правая панель, px</span>
                    <input type="number" min={360} max={1080} value={draftSettings.layout.right_panel_width} onFocus={() => setHighlightTarget('status')} onChange={(e) => updatePreview('layout.right_panel_width', clamp(Number(e.target.value), 360, 1080), 'status')} />
                  </label>
                </section>

                <section className="settings-box">
                  <h3>Палитра</h3>
                  <div className="swatch-grid">
                    <ColorField label="Лента 1" value={draftSettings.theme.chrome_start} target="ribbon" onPreview={setHighlightTarget} onChange={(value) => updatePreview('theme.chrome_start', value, 'ribbon')} />
                    <ColorField label="Лента 2" value={draftSettings.theme.chrome_end} target="ribbon" onPreview={setHighlightTarget} onChange={(value) => updatePreview('theme.chrome_end', value, 'ribbon')} />
                    <ColorField label="Акцент" value={draftSettings.theme.accent} target="status" onPreview={setHighlightTarget} onChange={(value) => updatePreview('theme.accent', value, 'status')} />
                    <ColorField label="Фон" value={draftSettings.theme.background_start} target="chat" onPreview={setHighlightTarget} onChange={(value) => updatePreview('theme.background_start', value, 'chat')} />
                    <ColorField label="Панель" value={draftSettings.theme.panel} target="workspace" onPreview={setHighlightTarget} onChange={(value) => updatePreview('theme.panel', value, 'workspace')} />
                    <ColorField label="Текст" value={draftSettings.theme.text} target="chat" onPreview={setHighlightTarget} onChange={(value) => updatePreview('theme.text', value, 'chat')} />
                    <ColorField label="Сообщение пользователя" value={draftSettings.theme.user_bubble} target="userMessage" onPreview={setHighlightTarget} onChange={(value) => updatePreview('theme.user_bubble', value, 'userMessage')} />
                    <ColorField label="Сообщение модели" value={draftSettings.theme.assistant_bubble} target="assistantMessage" onPreview={setHighlightTarget} onChange={(value) => updatePreview('theme.assistant_bubble', value, 'assistantMessage')} />
                  </div>
                </section>
              </div>
            )}

            {settingsSection === 'runtime' && (
              <div className="side-settings-content">
                <section className="settings-box runtime-box">
                  <h3>Runtime</h3>
                  <div className="runtime-note">
                    GPU заработает только если установлен llama-cpp-python со сборкой CUDA, Vulkan, Metal или CLBlast. После смены GPU-параметров выгрузи модель и прогрей заново.
                  </div>
                  <section className={`gpu-diagnostics ${runtimeDiagnostics?.likely_cpu_build ? 'warning' : 'ok'}`}>
                    <div className="diag-head">
                      <div>
                        <span className="eyebrow">Диагностика GPU</span>
                        <strong>{runtimeDiagnostics?.summary || 'Проверка runtime еще не выполнена.'}</strong>
                      </div>
                      <button type="button" className="quiet-button" onClick={() => void refreshRuntimeDiagnostics()}>Проверить</button>
                    </div>
                    {runtimeDiagnostics && (
                      <>
                        <dl className="diag-grid">
                          <div><dt>llama-cpp-python</dt><dd>{runtimeDiagnostics.package_installed ? runtimeDiagnostics.package_version || 'installed' : 'не установлен'}</dd></div>
                          <div><dt>GPU offload</dt><dd>{runtimeDiagnostics.supports_gpu_offload === true ? 'поддерживается' : runtimeDiagnostics.supports_gpu_offload === false ? 'нет' : 'неизвестно'}</dd></div>
                          <div><dt>NVIDIA</dt><dd>{runtimeDiagnostics.nvidia_smi_found ? runtimeDiagnostics.nvidia_smi || 'nvidia-smi найден' : 'nvidia-smi не найден'}</dd></div>
                          <div><dt>GPU параметры</dt><dd>{runtimeDiagnostics.gpu_related_supported.length ? runtimeDiagnostics.gpu_related_supported.join(', ') : 'не определены'}</dd></div>
                          <div><dt>Backend флаги</dt><dd>{(runtimeDiagnostics.gpu_backend_flags || []).length ? (runtimeDiagnostics.gpu_backend_flags || []).join(', ') : 'не найдены'}</dd></div>
                        </dl>
                        <ul className="diag-list">
                          {runtimeDiagnostics.recommendations.map((item) => <li key={item}>{item}</li>)}
                        </ul>
                        <details className="install-commands">
                          <summary>Команды установки</summary>
                          <code>CUDA: {runtimeDiagnostics.install_commands.cuda}</code>
                          <code>Vulkan: {runtimeDiagnostics.install_commands.vulkan}</code>
                          <code>CPU: {runtimeDiagnostics.install_commands.cpu}</code>
                        </details>
                        {runtimeDiagnostics.system_info && (
                          <details className="install-commands">
                            <summary>llama.cpp system info</summary>
                            <code>{runtimeDiagnostics.system_info}</code>
                          </details>
                        )}
                      </>
                    )}
                  </section>
                  <div className="preset-row">
                    <button type="button" onClick={() => applyRuntimePreset('cpu')}>CPU only</button>
                    <button type="button" disabled={!gpuBackendReady} title={gpuPresetHint} onClick={() => applyRuntimePreset('hybrid')}>CPU/GPU</button>
                    <button type="button" disabled={!gpuBackendReady} title={gpuPresetHint} onClick={() => applyRuntimePreset('gpu')}>GPU only</button>
                  </div>
                  {!gpuBackendReady && (
                    <div className="runtime-lock-note">
                      GPU-профили заблокированы: установлен CPU runtime. Запусти tools\06_install_cuda_runtime.bat cu124 0.3.4, затем перезапусти приложение и нажми Проверить.
                    </div>
                  )}
                  <label className="check-row help-field" title="Если GPU-профиль падает при загрузке, приложение попробует поднять модель в CPU-режиме вместо общей ошибки." data-help="Рекомендовано: включено, пока подбираешь CUDA/Vulkan-сборку и параметры GPU.">
                    <input type="checkbox" checked={runtimeDraft.gpu_fallback_to_cpu} onChange={(e) => updateRuntimeDraft('gpu_fallback_to_cpu', e.target.checked)} />
                    <span>Safe CPU fallback при ошибке GPU</span>
                  </label>
                  <label className="help-field" title="keep_hot держит модель в памяти; unload_after_idle освобождает RAM/VRAM после простоя; manual грузит только вручную." data-help="Рекомендовано: keep_hot для постоянной работы, unload_after_idle для слабых машин.">
                    <span>Режим удержания</span>
                    <select value={runtimeDraft.warm_policy} onChange={(e) => updateRuntimeDraft('warm_policy', e.target.value)}>
                      <option value="keep_hot">Всегда держать в памяти</option>
                      <option value="manual">Только вручную</option>
                      <option value="unload_after_idle">Выгружать после простоя</option>
                    </select>
                  </label>
                  <label className="help-field" title="-1 пытается выгрузить все слои модели на GPU; 20-35 обычно гибрид CPU/GPU; 0 только CPU." data-help="Для NVIDIA начни с -1. Если не хватает VRAM, поставь 20-35.">
                    <span>GPU layers</span>
                    <input type="number" value={runtimeDraft.n_gpu_layers} onChange={(e) => updateRuntimeDraft('n_gpu_layers', Number(e.target.value))} />
                  </label>
                  <label className="help-field" title="Номер основной видеокарты. Обычно 0. Для второй GPU укажи 1." data-help="Обычно 0. Меняй только если несколько видеокарт.">
                    <span>Main GPU</span>
                    <input type="number" value={runtimeDraft.main_gpu} onChange={(e) => updateRuntimeDraft('main_gpu', Number(e.target.value))} />
                  </label>
                  <label className="help-field" title="Как распределять слои между GPU. Layer подходит почти всегда; Row нужен редко для multi-GPU." data-help="Рекомендовано: layer. Для CPU-профиля можно none.">
                    <span>Split mode</span>
                    <select value={runtimeDraft.split_mode} onChange={(e) => updateRuntimeDraft('split_mode', e.target.value)}>
                      <option value="none">none</option>
                      <option value="layer">layer</option>
                      <option value="row">row</option>
                    </select>
                  </label>
                  <label className="help-field" title="Доли распределения по нескольким GPU, например 0.7,0.3. Оставь пустым для автоматического выбора." data-help="Нужно только для нескольких GPU. Пример: 0.7,0.3.">
                    <span>Tensor split</span>
                    <input value={runtimeDraft.tensor_split} placeholder="auto" onChange={(e) => updateRuntimeDraft('tensor_split', e.target.value)} />
                  </label>
                  <label className="help-field" title="Контекст модели. Чем больше, тем больше память. 4096 стабильно, 8192+ требует больше RAM/VRAM." data-help="Рекомендовано: 4096 или 8192, если хватает памяти.">
                    <span>n_ctx</span>
                    <input type="number" value={runtimeDraft.n_ctx} onChange={(e) => updateRuntimeDraft('n_ctx', Number(e.target.value))} />
                  </label>
                  <label className="help-field" title="Размер батча при обработке prompt. Больше может ускорить, но увеличивает память." data-help="Рекомендовано: 512. На GPU можно пробовать 1024.">
                    <span>n_batch</span>
                    <input type="number" value={runtimeDraft.n_batch} onChange={(e) => updateRuntimeDraft('n_batch', Number(e.target.value))} />
                  </label>
                  <label className="help-field" title="Потоки CPU для генерации. Обычно ставят число производительных ядер или чуть меньше." data-help="Рекомендовано: 6-12 для обычного ПК.">
                    <span>n_threads</span>
                    <input type="number" value={runtimeDraft.n_threads} onChange={(e) => updateRuntimeDraft('n_threads', Number(e.target.value))} />
                  </label>
                  <label className="help-field" title="Потоки CPU для batch-этапа. 0 означает авто/как n_threads." data-help="Рекомендовано: 0 или как n_threads.">
                    <span>n_threads_batch</span>
                    <input type="number" value={runtimeDraft.n_threads_batch} onChange={(e) => updateRuntimeDraft('n_threads_batch', Number(e.target.value))} />
                  </label>
                  <label className="help-field" title="Максимальная длина ответа. Большое значение дольше генерирует и больше держит контекст." data-help="Рекомендовано: 1024-2000 для тестов, 4096 для длинных ответов.">
                    <span>Max tokens</span>
                    <input type="number" value={runtimeDraft.max_tokens} onChange={(e) => updateRuntimeDraft('max_tokens', Number(e.target.value))} />
                  </label>
                  <label className="help-field" title="Случайность ответа. 0.1-0.3 для деловых задач, 0.7+ для креатива." data-help="Рекомендовано: 0.2 для корпоративного помощника.">
                    <span>Temperature</span>
                    <input type="number" step="0.1" value={runtimeDraft.temperature} onChange={(e) => updateRuntimeDraft('temperature', Number(e.target.value))} />
                  </label>
                  <label className="help-field" title="Ограничивает выбор токенов top-k. 40 стандартно; 0 обычно отключает." data-help="Рекомендовано: 40.">
                    <span>top_k</span>
                    <input type="number" value={runtimeDraft.top_k} onChange={(e) => updateRuntimeDraft('top_k', Number(e.target.value))} />
                  </label>
                  <label className="help-field" title="Nucleus sampling. 0.9-0.95 обычно хороший баланс." data-help="Рекомендовано: 0.95.">
                    <span>top_p</span>
                    <input type="number" step="0.01" value={runtimeDraft.top_p} onChange={(e) => updateRuntimeDraft('top_p', Number(e.target.value))} />
                  </label>
                  <label className="help-field" title="Минимальная вероятность токена. Помогает отрезать мусорные варианты." data-help="Рекомендовано: 0.05 или 0 для отключения.">
                    <span>min_p</span>
                    <input type="number" step="0.01" value={runtimeDraft.min_p} onChange={(e) => updateRuntimeDraft('min_p', Number(e.target.value))} />
                  </label>
                  <label className="help-field" title="Штраф за повторения. Если модель зацикливается, подними до 1.15-1.2." data-help="Рекомендовано: 1.05-1.15.">
                    <span>repeat_penalty</span>
                    <input type="number" step="0.01" value={runtimeDraft.repeat_penalty} onChange={(e) => updateRuntimeDraft('repeat_penalty', Number(e.target.value))} />
                  </label>
                  <label className="help-field" title="-1 означает случайный seed. Фиксированное число делает ответы более повторяемыми." data-help="Рекомендовано: -1.">
                    <span>Seed</span>
                    <input type="number" value={runtimeDraft.seed} onChange={(e) => updateRuntimeDraft('seed', Number(e.target.value))} />
                  </label>
                  <label className="check-row help-field" title="Переносит K/Q/V операции на GPU, если backend поддерживает. Обычно стоит оставить включенным." data-help="Рекомендовано: включено.">
                    <input type="checkbox" checked={runtimeDraft.offload_kqv} onChange={(e) => updateRuntimeDraft('offload_kqv', e.target.checked)} />
                    <span>offload_kqv</span>
                  </label>
                  <label className="check-row help-field" title="Flash Attention может ускорить и снизить память на поддерживаемых сборках, но не везде доступен." data-help="Для CUDA-сборок можно включить. Если модель не грузится, выключи.">
                    <input type="checkbox" checked={runtimeDraft.flash_attn} onChange={(e) => updateRuntimeDraft('flash_attn', e.target.checked)} />
                    <span>flash_attn</span>
                  </label>
                  <label className="check-row help-field" title="Дополнительная выгрузка операций на GPU в новых сборках llama.cpp." data-help="Рекомендовано: включено для GPU-профиля.">
                    <input type="checkbox" checked={runtimeDraft.op_offload} onChange={(e) => updateRuntimeDraft('op_offload', e.target.checked)} />
                    <span>op_offload</span>
                  </label>
                  <label className="check-row help-field" title="Полный SWA cache. Может требовать больше памяти, включай только если понимаешь задачу модели." data-help="Обычно выключено.">
                    <input type="checkbox" checked={runtimeDraft.swa_full} onChange={(e) => updateRuntimeDraft('swa_full', e.target.checked)} />
                    <span>swa_full</span>
                  </label>
                  <label className="check-row help-field" title="mmap быстрее загружает модель с диска и обычно экономит память." data-help="Рекомендовано: включено.">
                    <input type="checkbox" checked={runtimeDraft.use_mmap} onChange={(e) => updateRuntimeDraft('use_mmap', e.target.checked)} />
                    <span>use_mmap</span>
                  </label>
                  <label className="check-row help-field" title="mlock удерживает модель в RAM. Может мешать системе, если памяти мало." data-help="Обычно выключено.">
                    <input type="checkbox" checked={runtimeDraft.use_mlock} onChange={(e) => updateRuntimeDraft('use_mlock', e.target.checked)} />
                    <span>use_mlock</span>
                  </label>
                  <label className="check-row help-field" title="Подробные логи llama.cpp в консоли. Полезно для диагностики GPU." data-help="Включай для проверки, видит ли runtime CUDA/Vulkan.">
                    <input type="checkbox" checked={runtimeDraft.verbose_runtime} onChange={(e) => updateRuntimeDraft('verbose_runtime', e.target.checked)} />
                    <span>verbose_runtime</span>
                  </label>
                </section>
              </div>
            )}

            {settingsSection === 'server' && (
              <div className="side-settings-content">
                <section className="settings-box">
                  <h3>Сервер</h3>
                  <label><span>Host</span><input value={draftSettings.server.host} onChange={(e) => updateDraft('server.host', e.target.value)} /></label>
                  <label><span>Port</span><input type="number" value={draftSettings.server.port} onChange={(e) => updateDraft('server.port', Number(e.target.value))} /></label>
                  <label><span>Public base URL</span><input value={draftSettings.server.public_base_url} onChange={(e) => updateDraft('server.public_base_url', e.target.value)} /></label>
                  <label><span>OpenAI path</span><input value={draftSettings.server.openai_compat_path} onChange={(e) => updateDraft('server.openai_compat_path', e.target.value)} /></label>
                </section>
              </div>
            )}

            {settingsSection === 'model' && (
              <div className="side-settings-content">
                <section className="settings-box model-catalog">
                  <div className="model-catalog-head full-field">
                    <div>
                      <h3>Каталог моделей</h3>
                      <p>Добавляй GGUF-файлы в storage или подключай уже существующий путь на диске.</p>
                    </div>
                    <div className="catalog-actions">
                      <span>{models.length} моделей</span>
                      <button type="button" className="quiet-button" onClick={() => void handleValidateModels()}>
                        Проверить файлы
                      </button>
                    </div>
                  </div>

                  <section className="active-model-card full-field">
                    <div>
                      <span className="eyebrow">Активная модель</span>
                      <strong>{selectedModel?.name || 'Не выбрана'}</strong>
                      <p>
                        {selectedModel
                          ? selectedModel.validation_error || `${selectedModel.filename} · ${selectedModel.type} · ${selectedModel.source || 'local'}`
                          : 'Выбери модель для тестового чата и локального API.'}
                      </p>
                    </div>
                    <select value={selectedModelId} onChange={(e) => setSelectedModelId(e.target.value)} aria-label="Активная модель">
                      <option value="">Выбери LLM</option>
                      {llmModels.map((model) => <option key={model.id} value={model.id} disabled={model.file_exists === false}>{model.name} / {model.filename}</option>)}
                    </select>
                  </section>

                  <div className="model-import-grid full-field">
                    <form className="model-import-card" onSubmit={handleSubmitUpload}>
                      <div className="import-card-head">
                        <span>01</span>
                        <div>
                          <h4>Скопировать файл</h4>
                          <p>Подходит для EXE: модель будет лежать рядом с приложением в `models_storage`.</p>
                        </div>
                      </div>
                      <div className="field-pair">
                        <label><span>Название</span><input value={uploadName} placeholder="Qwen-8B" onChange={(e) => setUploadName(e.target.value)} /></label>
                        <label>
                          <span>Тип</span>
                          <select value={uploadType} onChange={(e) => setUploadType(e.target.value)}>
                            {MODEL_TYPES.map((item) => <option key={item} value={item}>{item}</option>)}
                          </select>
                        </label>
                      </div>
                      <label className={`file-drop-zone ${uploadFile ? 'has-file' : ''}`}>
                        <input type="file" accept=".gguf,.bin,.safetensors" onChange={(e) => setUploadFile(e.target.files?.[0] || null)} />
                        <strong>{uploadFile ? uploadFile.name : 'Выбери файл модели'}</strong>
                        <span>{uploadFile ? formatFileSize(uploadFile.size) : 'GGUF, BIN или SafeTensors. Файл будет скопирован в storage.'}</span>
                      </label>
                      <button type="submit">Добавить в storage</button>
                    </form>

                    <form className="model-import-card" onSubmit={handleSubmitPath}>
                      <div className="import-card-head">
                        <span>02</span>
                        <div>
                          <h4>Подключить путь</h4>
                          <p>Используй, если модель уже лежит на сервере или на диске рядом с EXE.</p>
                        </div>
                      </div>
                      <div className="field-pair">
                        <label><span>Название</span><input value={pathName} placeholder="Local-Qwen" onChange={(e) => setPathName(e.target.value)} /></label>
                        <label>
                          <span>Тип</span>
                          <select value={pathType} onChange={(e) => setPathType(e.target.value)}>
                            {MODEL_TYPES.map((item) => <option key={item} value={item}>{item}</option>)}
                          </select>
                        </label>
                      </div>
                      <label className="path-field">
                        <span>Абсолютный путь</span>
                        <input value={pathValue} placeholder={'D:\\models\\model.gguf'} onChange={(e) => setPathValue(e.target.value)} />
                      </label>
                      <button type="submit">Зарегистрировать путь</button>
                    </form>
                  </div>

                  <div className="model-table full-field model-registry">
                    <div className="registry-head">
                      <h4>Реестр моделей</h4>
                      <span>{llmModels.length} LLM</span>
                    </div>
                    {models.length === 0 ? (
                      <p className="empty-registry">Модели еще не добавлены. Начни с копирования файла или подключения существующего пути.</p>
                    ) : (
                      models.map((model) => {
                        const isHub = String(model.path || '').startsWith('HUB::');
                        const isMissing = model.file_exists === false && !isHub;
                        const fileState = isHub ? 'Из репозитория' : isMissing ? 'Файл не найден' : model.file_exists ? 'Файл на месте' : 'Не проверено';
                        return (
                          <div
                            className={`model-table-row compact ${model.id === selectedModelId ? 'selected' : ''} ${isMissing ? 'missing' : ''}`}
                            key={model.id}
                          >
                            <div>
                              <div className="model-row-title">
                                <strong>{model.name}</strong>
                                <span className={`model-badge ${isMissing ? 'danger' : model.file_exists ? 'ok' : 'idle'}`}>{fileState}</span>
                              </div>
                              <span>{model.filename} · {model.type} · {model.source || 'local'} · {model.status || 'saved'}</span>
                              {model.file_size ? <span>{formatFileSize(model.file_size)}</span> : null}
                              {model.validation_error ? <em className="model-validation-error">{model.validation_error}</em> : null}
                              <code className="model-path">{model.path}</code>
                            </div>
                            <div className="button-row">
                              {model.type === 'LLM' && (
                                <>
                                  <button type="button" disabled={isMissing} onClick={() => setSelectedModelId(model.id)}>В чат</button>
                                  <button type="button" disabled={isMissing} className="quiet-button" onClick={() => void handlePrewarmModel(model.id)}>Прогреть</button>
                                  <button type="button" className="quiet-button" onClick={() => void handleUnloadModel(model.id)}>Выгрузить</button>
                                </>
                              )}
                              <button type="button" className="danger-button" onClick={() => void handleDeleteModel(model.id)}>Удалить</button>
                            </div>
                          </div>
                        );
                      })
                    )}
                  </div>
                </section>
              </div>
            )}

            {settingsSection === 'hub' && (
              <div className="side-settings-content">
                <section className="settings-box">
                  <h3>Хаб</h3>
                  <label className="check-row"><input type="checkbox" checked={draftSettings.hub.enabled} onChange={(e) => updateDraft('hub.enabled', e.target.checked)} /><span>Включить хаб</span></label>
                  <label><span>Base URL</span><input value={draftSettings.hub.base_url} onChange={(e) => updateDraft('hub.base_url', e.target.value)} /></label>
                  <label><span>Token</span><input value={draftSettings.hub.token} onChange={(e) => updateDraft('hub.token', e.target.value)} /></label>
                </section>
              </div>
            )}
          </section>
        ) : (
          <>
        <section className={`status-hero ${status.tone}`}>
          <div>
            <span className="eyebrow">Состояние работы</span>
            <h3>{status.title}</h3>
            <p>{status.detail || 'Сервис запущен и ожидает действие.'}</p>
          </div>
          <span className={`state-dot ${status.tone !== 'error' ? 'on' : ''}`} />
        </section>

        <section className="metric-grid">
          <div className="metric-cell">
            <span>Модели</span>
            <strong>{llmModels.length}</strong>
          </div>
          <div className="metric-cell">
            <span>В памяти</span>
            <strong>{runtimeStatuses.length}</strong>
          </div>
          <div className="metric-cell">
            <span>Диалог</span>
            <strong>{activeConversation?.messages.length || 0}</strong>
          </div>
          <div className="metric-cell">
            <span>Режим</span>
            <strong>{policyShortLabel(runtimeDraft.warm_policy)}</strong>
          </div>
        </section>

        <section className="panel-section selected-status">
          <div className="section-head">
            <div>
              <span className="eyebrow">Активная модель</span>
              <h3>{selectedModel?.name || 'Не выбрана'}</h3>
            </div>
            <span className={`state-dot ${selectedModelId && loadedModelIds.has(selectedModelId) ? 'on' : ''}`} />
          </div>
          {selectedModel ? (
            <>
              <dl className="model-facts">
                <div><dt>Файл</dt><dd>{selectedModel.filename}</dd></div>
                <div><dt>Статус</dt><dd>{loadedModelIds.has(selectedModel.id) ? 'в памяти' : selectedModel.status || 'saved'}</dd></div>
                <div><dt>Источник</dt><dd>{selectedModel.source || 'local'}</dd></div>
                <div><dt>Проверка файла</dt><dd>{selectedModel.file_exists === false ? 'файл не найден' : selectedModel.file_exists ? 'файл на месте' : 'не проверено'}</dd></div>
                <div><dt>Исполнение</dt><dd>{selectedRuntime?.runtime_mode || 'еще не определено'}</dd></div>
              </dl>
              {selectedRuntime?.fallback_reason ? <em className="model-validation-error">GPU не поднялся, работает CPU fallback: {selectedRuntime.fallback_reason}</em> : null}
              {selectedModel.validation_error ? <em className="model-validation-error">{selectedModel.validation_error}</em> : null}
              <div className="button-row">
                <button type="button" disabled={selectedModel.file_exists === false} onClick={() => void handlePrewarmModel(selectedModel.id)}>Прогреть</button>
                <button type="button" className="quiet-button" onClick={() => void handleUnloadModel(selectedModel.id)}>Выгрузить</button>
              </div>
            </>
          ) : (
            <p className="muted">Добавь или выбери LLM-модель в настройках.</p>
          )}
        </section>

        <section className="panel-section status-feed-section">
          <div className="section-head">
            <div>
              <span className="eyebrow">Журнал состояния</span>
              <h3>Сейчас</h3>
            </div>
          </div>
          <div className="status-feed">
            {statusEvents.map((event, idx) => (
              <div className={`status-event ${event.tone}`} key={`${event.label}-${idx}`}>
                <span />
                <div>
                  <strong>{event.label}</strong>
                  <p>{event.detail}</p>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="panel-section api-status">
          <span className="eyebrow">Локальный API</span>
          <code className="api-url">{apiDisplayBase}/v1/chat/completions</code>
          <button type="button" className="quiet-button full" onClick={() => openSettings('server')}>
            Параметры API
          </button>
        </section>
          </>
        )}
      </aside>

      {settingsOpen && (
        <div className="settings-layer" role="dialog" aria-modal="true" aria-label="Настройки">
          <div className="settings-shell">
            <header className="settings-header">
              <div>
                <span className="eyebrow">Центр управления</span>
                <h2>Настройки приложения</h2>
              </div>
              <div className="button-row">
                <button type="button" onClick={() => void handleSaveSettings()}>Сохранить</button>
                <button type="button" className="quiet-button" onClick={() => setSettingsOpen(false)}>Закрыть</button>
              </div>
            </header>

            <div className="settings-tabs">
              {[
                ['model', 'Модели'],
                ['runtime', 'Runtime'],
                ['server', 'Сервер'],
                ['hub', 'Хаб'],
                ['appearance', 'Вид'],
              ].map(([id, label]) => (
                <button
                  type="button"
                  key={id}
                  className={settingsSection === id ? 'active' : ''}
                  onClick={() => setSettingsSection(id as SettingsSection)}
                >
                  {label}
                </button>
              ))}
            </div>

            <div className="settings-content">
              {settingsSection === 'model' && (
                <div className="settings-grid two">
                  <form className="settings-box" onSubmit={handleSubmitUpload}>
                    <h3>Загрузить файл</h3>
                    <label><span>Название</span><input value={uploadName} onChange={(e) => setUploadName(e.target.value)} /></label>
                    <label>
                      <span>Тип</span>
                      <select value={uploadType} onChange={(e) => setUploadType(e.target.value)}>
                        {MODEL_TYPES.map((item) => <option key={item} value={item}>{item}</option>)}
                      </select>
                    </label>
                    <label><span>Файл</span><input type="file" onChange={(e) => setUploadFile(e.target.files?.[0] || null)} /></label>
                    <button type="submit">Добавить модель</button>
                  </form>

                  <form className="settings-box" onSubmit={handleSubmitPath}>
                    <h3>Подключить путь</h3>
                    <label><span>Название</span><input value={pathName} onChange={(e) => setPathName(e.target.value)} /></label>
                    <label>
                      <span>Тип</span>
                      <select value={pathType} onChange={(e) => setPathType(e.target.value)}>
                        {MODEL_TYPES.map((item) => <option key={item} value={item}>{item}</option>)}
                      </select>
                    </label>
                    <label><span>Абсолютный путь</span><input value={pathValue} onChange={(e) => setPathValue(e.target.value)} /></label>
                    <button type="submit">Зарегистрировать</button>
                  </form>

                  <section className="settings-box wide">
                    <h3>Зарегистрированные модели</h3>
                    <div className="model-table">
                      {models.length === 0 ? (
                        <p className="muted">Модели еще не добавлены.</p>
                      ) : (
                        models.map((model) => (
                          <div className="model-table-row" key={model.id}>
                            <div>
                              <strong>{model.name}</strong>
                              <span>{model.filename} · {model.type} · {model.source || 'local'}</span>
                            </div>
                            <div className="button-row">
                              {model.type === 'LLM' && (
                                <>
                                  <button type="button" onClick={() => { setSelectedModelId(model.id); setSettingsOpen(false); }}>В чат</button>
                                  <button type="button" className="quiet-button" onClick={() => void handleStartModel(model.id)}>Старт</button>
                                </>
                              )}
                              <button type="button" className="danger-button" onClick={() => void handleDeleteModel(model.id)}>Удалить</button>
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  </section>
                </div>
              )}

              {settingsSection === 'runtime' && (
                <div className="settings-grid">
                  <section className="settings-box wide">
                    <h3>Запуск и прогрев</h3>
                    <div className="field-grid">
                      <label>
                        <span>Режим удержания</span>
                        <select
                          value={runtimeDraft.warm_policy}
                          onChange={(e) => updateRuntimeDraft('warm_policy', e.target.value)}
                        >
                          <option value="keep_hot">Всегда держать в памяти</option>
                          <option value="manual">Только вручную</option>
                          <option value="unload_after_idle">Выгружать после простоя</option>
                        </select>
                      </label>
                      <label><span>Выгрузка после, сек</span><input type="number" value={runtimeDraft.idle_unload_sec} onChange={(e) => updateRuntimeDraft('idle_unload_sec', Number(e.target.value))} /></label>
                      <label className="check-row"><input type="checkbox" checked={runtimeDraft.preload_on_start} onChange={(e) => updateRuntimeDraft('preload_on_start', e.target.checked)} /><span>Прогревать при старте сервера</span></label>
                    </div>
                  </section>

                  <section className="settings-box wide">
                    <h3>Параметры llama.cpp</h3>
                    <div className="field-grid">
                      <label><span>n_ctx</span><input type="number" value={runtimeDraft.n_ctx} onChange={(e) => updateRuntimeDraft('n_ctx', Number(e.target.value))} /></label>
                      <label><span>n_batch</span><input type="number" value={runtimeDraft.n_batch} onChange={(e) => updateRuntimeDraft('n_batch', Number(e.target.value))} /></label>
                      <label><span>n_threads</span><input type="number" value={runtimeDraft.n_threads} onChange={(e) => updateRuntimeDraft('n_threads', Number(e.target.value))} /></label>
                      <label><span>GPU layers</span><input type="number" value={runtimeDraft.n_gpu_layers} onChange={(e) => updateRuntimeDraft('n_gpu_layers', Number(e.target.value))} /></label>
                      <label><span>Main GPU</span><input type="number" value={runtimeDraft.main_gpu} onChange={(e) => updateRuntimeDraft('main_gpu', Number(e.target.value))} /></label>
                      <label><span>Temperature</span><input type="number" step="0.1" value={runtimeDraft.temperature} onChange={(e) => updateRuntimeDraft('temperature', Number(e.target.value))} /></label>
                      <label><span>Max tokens</span><input type="number" value={runtimeDraft.max_tokens} onChange={(e) => updateRuntimeDraft('max_tokens', Number(e.target.value))} /></label>
                      <label className="check-row"><input type="checkbox" checked={runtimeDraft.offload_kqv} onChange={(e) => updateRuntimeDraft('offload_kqv', e.target.checked)} /><span>offload_kqv</span></label>
                      <label className="check-row"><input type="checkbox" checked={runtimeDraft.use_mmap} onChange={(e) => updateRuntimeDraft('use_mmap', e.target.checked)} /><span>use_mmap</span></label>
                      <label className="check-row"><input type="checkbox" checked={runtimeDraft.use_mlock} onChange={(e) => updateRuntimeDraft('use_mlock', e.target.checked)} /><span>use_mlock</span></label>
                      <label className="check-row"><input type="checkbox" checked={runtimeDraft.verbose_runtime} onChange={(e) => updateRuntimeDraft('verbose_runtime', e.target.checked)} /><span>verbose runtime</span></label>
                    </div>
                  </section>
                </div>
              )}

              {settingsSection === 'server' && (
                <div className="settings-grid">
                  <section className="settings-box wide">
                    <h3>Локальный сервер</h3>
                    <div className="field-grid">
                      <label><span>Host</span><input value={draftSettings.server.host} onChange={(e) => updateDraft('server.host', e.target.value)} /></label>
                      <label><span>Port</span><input type="number" value={draftSettings.server.port} onChange={(e) => updateDraft('server.port', Number(e.target.value))} /></label>
                      <label><span>Public base URL</span><input value={draftSettings.server.public_base_url} onChange={(e) => updateDraft('server.public_base_url', e.target.value)} /></label>
                      <label><span>OpenAI path</span><input value={draftSettings.server.openai_compat_path} onChange={(e) => updateDraft('server.openai_compat_path', e.target.value)} /></label>
                      <label className="check-row"><input type="checkbox" checked={draftSettings.server.openai_compat_enabled} onChange={(e) => updateDraft('server.openai_compat_enabled', e.target.checked)} /><span>Включить OpenAI-compatible API</span></label>
                      <label><span>API key</span><input value={draftSettings.server.api_key} onChange={(e) => updateDraft('server.api_key', e.target.value)} /></label>
                    </div>
                    <label className="full-field">
                      <span>CORS origins</span>
                      <textarea
                        rows={4}
                        value={draftSettings.server.cors_origins.join('\n')}
                        onChange={(e) => updateDraft('server.cors_origins', e.target.value.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean))}
                      />
                    </label>
                  </section>
                </div>
              )}

              {settingsSection === 'hub' && (
                <div className="settings-grid two">
                  <section className="settings-box">
                    <h3>Источник моделей</h3>
                    <label className="check-row"><input type="checkbox" checked={draftSettings.hub.enabled} onChange={(e) => updateDraft('hub.enabled', e.target.checked)} /><span>Включить хаб</span></label>
                    <label><span>Base URL</span><input value={draftSettings.hub.base_url} onChange={(e) => updateDraft('hub.base_url', e.target.value)} /></label>
                    <label><span>Models endpoint</span><input value={draftSettings.hub.models_endpoint} onChange={(e) => updateDraft('hub.models_endpoint', e.target.value)} /></label>
                    <label><span>Pull endpoint</span><input value={draftSettings.hub.pull_endpoint} onChange={(e) => updateDraft('hub.pull_endpoint', e.target.value)} /></label>
                    <label><span>Token</span><input value={draftSettings.hub.token} onChange={(e) => updateDraft('hub.token', e.target.value)} /></label>
                    <label><span>Timeout sec</span><input type="number" value={draftSettings.hub.timeout_sec} onChange={(e) => updateDraft('hub.timeout_sec', Number(e.target.value))} /></label>
                    <button type="button" onClick={() => void handleReloadHubModels()}>Обновить список</button>
                  </section>

                  <section className="settings-box">
                    <h3>Импорт</h3>
                    <label><span>Локальное имя</span><input value={hubDraftName} onChange={(e) => setHubDraftName(e.target.value)} /></label>
                    <label>
                      <span>Модель</span>
                      <select value={selectedHubModelId} onChange={(e) => setSelectedHubModelId(e.target.value)}>
                        <option value="">Выбери remote-модель</option>
                        {hubModels.map((item, idx) => {
                          const value = String(item.id || item.name || `remote-${idx}`);
                          const title = String(item.name || item.id || `remote-${idx}`);
                          return <option key={value} value={value}>{title}</option>;
                        })}
                      </select>
                    </label>
                    <button type="button" onClick={() => void handleImportHubModel()}>Импортировать</button>
                  </section>
                </div>
              )}

              {settingsSection === 'appearance' && (
                <div className="settings-grid two">
                  <section className="settings-box">
                    <h3>Бренд и логотип</h3>
                    <div className="logo-upload-panel">
                      <div className="logo-preview">
                        {draftSettings.branding.logo_url ? (
                          <img
                            src={assetUrl(normalizeLogoAsset(draftSettings.branding.logo_url))}
                            alt=""
                            style={{ objectFit: draftSettings.branding.logo_fit }}
                          />
                        ) : (
                          <span>AI</span>
                        )}
                      </div>
                      <div>
                        <strong>{draftSettings.branding.title}</strong>
                        <p>{draftSettings.branding.subtitle}</p>
                      </div>
                    </div>
                    <label><span>Название</span><input value={draftSettings.branding.title} onChange={(e) => updateDraft('branding.title', e.target.value)} /></label>
                    <label><span>Подзаголовок</span><input value={draftSettings.branding.subtitle} onChange={(e) => updateDraft('branding.subtitle', e.target.value)} /></label>
                    <div className="field-grid compact">
                      <label><span>Ширина логотипа</span><input type="number" value={draftSettings.branding.logo_width} onChange={(e) => updateDraft('branding.logo_width', Number(e.target.value))} /></label>
                      <label><span>Высота логотипа</span><input type="number" value={draftSettings.branding.logo_height} onChange={(e) => updateDraft('branding.logo_height', Number(e.target.value))} /></label>
                      <label><span>Радиус логотипа</span><input type="number" value={draftSettings.branding.logo_radius} onChange={(e) => updateDraft('branding.logo_radius', Number(e.target.value))} /></label>
                      <label><span>Отступ внутри</span><input type="number" value={draftSettings.branding.logo_padding} onChange={(e) => updateDraft('branding.logo_padding', Number(e.target.value))} /></label>
                      <label>
                        <span>Вписывание</span>
                        <select value={draftSettings.branding.logo_fit} onChange={(e) => updateDraft('branding.logo_fit', e.target.value)}>
                          <option value="contain">contain</option>
                          <option value="cover">cover</option>
                          <option value="fill">fill</option>
                        </select>
                      </label>
                    </div>
                    <div className="file-upload-row">
                      <input type="file" accept=".svg,.png,.jpg,.jpeg,.webp" onChange={(e) => setLogoFile(e.target.files?.[0] || null)} />
                      <button type="button" onClick={() => void handleUploadLogo()}>Загрузить логотип</button>
                    </div>
                  </section>

                  <section className="settings-box">
                    <h3>Корпоративная рамка</h3>
                    <div className="field-grid compact">
                      <label><span>Ширина приложения</span><input type="number" value={draftSettings.layout.app_max_width} onChange={(e) => updateDraft('layout.app_max_width', Number(e.target.value))} /></label>
                      <label><span>Радиус элементов</span><input type="number" value={draftSettings.layout.card_radius} onChange={(e) => updateDraft('layout.card_radius', Number(e.target.value))} /></label>
                      <label className="check-row"><input type="checkbox" checked={draftSettings.layout.hero_compact} onChange={(e) => updateDraft('layout.hero_compact', e.target.checked)} /><span>Компактная шапка</span></label>
                    </div>
                    <div className="swatch-grid">
                      <ColorField label="Фирменный градиент 1" value={draftSettings.theme.chrome_start} onChange={(value) => updateDraft('theme.chrome_start', value)} />
                      <ColorField label="Фирменный градиент 2" value={draftSettings.theme.chrome_end} onChange={(value) => updateDraft('theme.chrome_end', value)} />
                      <ColorField label="Текст на акценте" value={draftSettings.theme.chrome_text} onChange={(value) => updateDraft('theme.chrome_text', value)} />
                      <ColorField label="Акцент" value={draftSettings.theme.accent} onChange={(value) => updateDraft('theme.accent', value)} />
                    </div>
                  </section>

                  <section className="settings-box wide">
                    <h3>Палитра интерфейса</h3>
                    <div className="swatch-grid large">
                      <ColorField label="Фон верх" value={draftSettings.theme.background_start} onChange={(value) => updateDraft('theme.background_start', value)} />
                      <ColorField label="Фон низ" value={draftSettings.theme.background_end} onChange={(value) => updateDraft('theme.background_end', value)} />
                      <ColorField label="Панель" value={draftSettings.theme.panel} onChange={(value) => updateDraft('theme.panel', value)} />
                      <ColorField label="Панель 2" value={draftSettings.theme.panel_alt} onChange={(value) => updateDraft('theme.panel_alt', value)} />
                      <ColorField label="Граница" value={draftSettings.theme.border} onChange={(value) => updateDraft('theme.border', value)} />
                      <ColorField label="Текст" value={draftSettings.theme.text} onChange={(value) => updateDraft('theme.text', value)} />
                      <ColorField label="Текст заголовков" value={draftSettings.theme.hero_text} onChange={(value) => updateDraft('theme.hero_text', value)} />
                      <ColorField label="Вторичный текст" value={draftSettings.theme.muted} onChange={(value) => updateDraft('theme.muted', value)} />
                      <ColorField label="Сообщение пользователя" value={draftSettings.theme.user_bubble} onChange={(value) => updateDraft('theme.user_bubble', value)} />
                      <ColorField label="Сообщение модели" value={draftSettings.theme.assistant_bubble} onChange={(value) => updateDraft('theme.assistant_bubble', value)} />
                      <ColorField label="Успех" value={draftSettings.theme.success} onChange={(value) => updateDraft('theme.success', value)} />
                      <ColorField label="Предупреждение" value={draftSettings.theme.warning} onChange={(value) => updateDraft('theme.warning', value)} />
                      <ColorField label="Ошибка" value={draftSettings.theme.danger} onChange={(value) => updateDraft('theme.danger', value)} />
                    </div>
                  </section>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Неизвестная ошибка';
}

function makeTitle(text: string): string {
  const normalized = text.replace(/\s+/g, ' ').trim();
  return normalized.length > 42 ? `${normalized.slice(0, 42)}...` : normalized || 'Новый диалог';
}

function formatIdle(seconds: number): string {
  if (seconds < 60) return `${seconds} c`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} мин`;
  return `${Math.floor(minutes / 60)} ч`;
}

function formatDuration(ms?: number): string {
  if (!ms || Number.isNaN(ms)) return 'время неизвестно';
  if (ms < 1000) return `${Math.round(ms)} мс`;
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} сек`;
}

function formatTokens(usage?: ChatMessage['usage']): string {
  if (!usage) return 'токены неизвестны';
  const total = typeof usage.total_tokens === 'number' ? usage.total_tokens : undefined;
  const prompt = typeof usage.prompt_tokens === 'number' ? usage.prompt_tokens : undefined;
  const completion = typeof usage.completion_tokens === 'number' ? usage.completion_tokens : undefined;
  if (total !== undefined) {
    return `${total} ток. (${prompt ?? 0}/${completion ?? 0})`;
  }
  if (prompt !== undefined || completion !== undefined) {
    return `${prompt ?? 0}/${completion ?? 0} ток.`;
  }
  return 'токены неизвестны';
}

function formatMessageMeta(message: ChatMessage): string {
  if (message.role !== 'assistant') return '';
  if (message.pending && message.phase === 'thinking') return 'модель думает';
  if (message.pending && message.phase === 'reasoning_done') return 'рассуждение идет';
  if (message.pending && message.phase === 'typing') return 'печатает ответ';
  const parts = [formatDuration(message.elapsed_ms), formatTokens(message.usage)];
  if (message.runtime_mode) parts.push(message.runtime_mode);
  if (message.finish_reason) parts.push(`finish: ${message.finish_reason}`);
  return parts.join(' · ');
}

function buildResponseSummary(elapsedMs?: number, usage?: ChatMessage['usage'], finishReason?: string | null): string {
  return [formatDuration(elapsedMs), formatTokens(usage), finishReason ? `finish: ${finishReason}` : ''].filter(Boolean).join(' · ');
}

function parseLiveModelContent(content: string): { reasoning: string; answer: string; phase: ChatMessage['phase'] } {
  const text = content || '';
  const open = text.toLowerCase().indexOf('<think>');
  if (open >= 0) {
    const before = text.slice(0, open).trim();
    const afterOpen = text.slice(open + '<think>'.length);
    const close = afterOpen.toLowerCase().indexOf('</think>');
    if (close >= 0) {
      const reasoning = afterOpen.slice(0, close).trim();
      const after = afterOpen.slice(close + '</think>'.length).trim();
      return { reasoning, answer: [before, after].filter(Boolean).join('\n\n'), phase: 'typing' };
    }
    return { reasoning: afterOpen.trim(), answer: before, phase: 'thinking' };
  }
  return { reasoning: '', answer: text, phase: 'typing' };
}

function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, value));
}

function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return 'размер неизвестен';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(value >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function ColorField({
  label,
  value,
  onChange,
  target,
  onPreview,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  target?: HighlightTarget;
  onPreview?: (target: HighlightTarget) => void;
}) {
  const previewTarget = () => {
    if (target && onPreview) onPreview(target);
  };
  const clearPreview = () => {
    if (target && onPreview) onPreview('');
  };

  return (
    <label
      className="color-field"
      onMouseEnter={previewTarget}
      onMouseLeave={clearPreview}
      onFocusCapture={previewTarget}
      onBlurCapture={(event) => {
        const nextTarget = event.relatedTarget as Node | null;
        if (!nextTarget || !event.currentTarget.contains(nextTarget)) clearPreview();
      }}
    >
      <span>{label}</span>
      <div>
        <input type="color" value={value} onChange={(event) => onChange(event.target.value)} />
        <input value={value} onChange={(event) => onChange(event.target.value)} />
      </div>
    </label>
  );
}

function policyLabel(policy: RuntimeSettings['warm_policy']): string {
  if (policy === 'keep_hot') return 'Всегда горячая';
  if (policy === 'unload_after_idle') return 'Выгрузка после простоя';
  return 'Ручной режим';
}

function policyShortLabel(policy: RuntimeSettings['warm_policy']): string {
  if (policy === 'keep_hot') return 'Hot';
  if (policy === 'unload_after_idle') return 'Idle';
  return 'Manual';
}

function normalizeLogoAsset(path: string): string {
  const value = String(path || '').trim();
  if (!value) return '';
  // Keep EXE icons separate from UI logos. Older backend code may choose the
  // first file in models_storage/branding and accidentally return .ico here.
  if (/\.ico(?:\?|$)/i.test(value) || /local_ai_gpp(?:_icon_preview)?/i.test(value)) {
    return '/assets/branding/logo.png';
  }
  return value;
}

function assetUrl(path: string): string {
  if (/^https?:\/\//.test(path)) return path;
  return `${API_BASE}${path}`;
}
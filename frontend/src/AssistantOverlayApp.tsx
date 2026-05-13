import { useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties, FormEvent, PointerEvent as ReactPointerEvent } from 'react';
import { fetchBootstrap, fetchRuntimeStatus, streamChat } from './api';
import type { EngineSettings, ModelRecord, RuntimeSettings, RuntimeStatus } from './types';

type AgentState = 'ready' | 'thinking' | 'speaking' | 'error';

type AgentMessage = {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  pending?: boolean;
};

type Point = { x: number; y: number };

type Viewport = { width: number; height: number };

type DragState = {
  startX: number;
  startY: number;
  baseX: number;
  baseY: number;
  moved: boolean;
} | null;

const STORAGE_KEY = 'local-ai-agent-avatar-v12';
const AVATAR_SIZE = { width: 154, height: 210 };
const BUBBLE_SIZE = { width: 512, height: 292 };
const EDGE = 16;
const GAP = 22;

const DEFAULT_RUNTIME: Partial<RuntimeSettings> = {
  temperature: 0.2,
  max_tokens: 1024,
};

const SYSTEM_PROMPT = [
  'Ты локальный корпоративный AI-помощник Local AI GPP.',
  'Отвечай кратко, по делу, на русском языке.',
  'Используй уже выбранную модель и runtime. Не показывай пользователю внутренние настройки, если он прямо не попросил.',
].join('\n');

function readDesktopMode(): boolean {
  try {
    const params = new URLSearchParams(window.location.search);
    return params.get('desktop') === '1' || Boolean((window as unknown as { pywebview?: unknown }).pywebview);
  } catch {
    return false;
  }
}

function getViewport(): Viewport {
  return {
    width: typeof window !== 'undefined' ? window.innerWidth : 1280,
    height: typeof window !== 'undefined' ? window.innerHeight : 720,
  };
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function readPoint(key: string, fallback: Point): Point {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    const value = JSON.parse(raw) as Partial<Point>;
    if (typeof value.x === 'number' && typeof value.y === 'number') {
      return { x: value.x, y: value.y };
    }
  } catch {
    // noop
  }
  return fallback;
}

function writePoint(key: string, value: Point): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // noop
  }
}

function normalizeMessageText(role: AgentMessage['role'], value: string): string {
  let text = String(value || '').replace(/\r/g, '').trim();
  if (!text) return '';
  const rolePrefix = role === 'user' ? '(?:вы|user)' : '(?:помощник|assistant|ai|gpp)';
  const prefixRegex = new RegExp(`^(?:${rolePrefix})\\s*:\\s*`, 'i');
  while (prefixRegex.test(text)) {
    text = text.replace(prefixRegex, '').trim();
  }
  return text;
}

function stripThinking(value: string): string {
  if (!value) return '';
  return value
    .replace(/<think>[\s\S]*?<\/think>/gi, '')
    .replace(/<think>[\s\S]*/gi, '')
    .trim();
}

function getErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return String(error || 'Неизвестная ошибка');
}

function stateTitle(state: AgentState): string {
  if (state === 'thinking') return 'Думаю';
  if (state === 'speaking') return 'Отвечаю';
  if (state === 'error') return 'Проверка';
  return 'На связи';
}

function stateStatus(state: AgentState): string {
  if (state === 'thinking') return 'Собираю ответ';
  if (state === 'speaking') return 'Пишу ответ';
  if (state === 'error') return 'Нужно проверить запуск или модель';
  return 'Готов помочь';
}

function stateBadge(state: AgentState): { text: string; className: string } {
  if (state === 'thinking') return { text: '?', className: 'thinking' };
  if (state === 'speaking') return { text: '…', className: 'speaking' };
  if (state === 'error') return { text: '!', className: 'error' };
  return { text: '✓', className: 'ready' };
}

export default function AssistantOverlayApp() {
  const desktopMode = readDesktopMode();
  const [viewport, setViewport] = useState<Viewport>(() => getViewport());
  const [models, setModels] = useState<ModelRecord[]>([]);
  const [settings, setSettings] = useState<EngineSettings | null>(null);
  const [runtimeStatuses, setRuntimeStatuses] = useState<RuntimeStatus[]>([]);
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [input, setInput] = useState('');
  const [agentState, setAgentState] = useState<AgentState>('ready');
  const [frame, setFrame] = useState(0);
  const [bubbleOpen, setBubbleOpen] = useState(false);
  const [avatarPos, setAvatarPos] = useState<Point>(() => {
    const initial = readPoint(STORAGE_KEY, { x: 1040, y: 420 });
    return initial;
  });
  const dragRef = useRef<DragState>(null);
  const suppressNextClickRef = useRef(false);

  useEffect(() => {
    const handleResize = () => setViewport(getViewport());
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  useEffect(() => {
    document.documentElement.classList.add('assistant-transparent-page');
    document.body.classList.add('assistant-transparent-page');
    void bootstrap();
    return () => {
      document.documentElement.classList.remove('assistant-transparent-page');
      document.body.classList.remove('assistant-transparent-page');
    };
  }, []);

  useEffect(() => {
    const timings: Record<AgentState, number> = {
      ready: 840,
      thinking: 520,
      speaking: 480,
      error: 880,
    };
    const timer = window.setInterval(() => setFrame((prev) => (prev + 1) % 4), timings[agentState]);
    return () => window.clearInterval(timer);
  }, [agentState]);

  useEffect(() => {
    for (const state of ['ready', 'thinking', 'answered'] as const) {
      for (let index = 0; index < 4; index += 1) {
        const image = new Image();
        image.src = `/assistant/frames/${state}_${index}.png`;
      }
    }
  }, []);

  useEffect(() => {
    setAvatarPos((prev) => clampAvatarPoint(prev, viewport));
  }, [viewport]);

  async function bootstrap() {
    try {
      const [boot, runtime] = await Promise.all([
        fetchBootstrap(),
        fetchRuntimeStatus().catch(() => [] as RuntimeStatus[]),
      ]);
      const llms = (boot.models || []).filter((item) => item.type === 'LLM' && item.file_exists !== false);
      setModels(llms);
      setSettings(boot.settings);
      setRuntimeStatuses(runtime);
      setAgentState(llms.length ? 'ready' : 'error');
    } catch (error) {
      setAgentState('error');
      console.error('Assistant bootstrap failed', error);
    }
  }

  const activeModel = useMemo(() => {
    const hot = runtimeStatuses.find((item) => item.model_id);
    const running = models.find((item) => item.status === 'running');
    return models.find((item) => item.id === hot?.model_id) || running || models[0] || null;
  }, [models, runtimeStatuses]);

  const runtime = useMemo(() => ({
    ...DEFAULT_RUNTIME,
    ...(settings?.runtime || {}),
  } as RuntimeSettings), [settings?.runtime]);

  const avatarFrame = useMemo(() => {
    if (agentState === 'thinking') return `/assistant/frames/thinking_${frame}.png`;
    if (agentState === 'speaking') return `/assistant/frames/answered_${frame}.png`;
    if (agentState === 'error') return `/assistant/frames/thinking_${frame}.png`;
    return `/assistant/frames/ready_${frame}.png`;
  }, [agentState, frame]);

  const badge = stateBadge(agentState);

  const bubbleLayout = useMemo(() => {
    const avatarMiddleY = avatarPos.y + AVATAR_SIZE.height / 2;
    const canPlaceLeft = avatarPos.x >= BUBBLE_SIZE.width + GAP + EDGE;
    const direction = canPlaceLeft ? 'left' : 'right';
    const left = direction === 'left'
      ? avatarPos.x - BUBBLE_SIZE.width - GAP
      : avatarPos.x + AVATAR_SIZE.width + GAP;
    const clampedLeft = clamp(left, EDGE, viewport.width - BUBBLE_SIZE.width - EDGE);
    const top = clamp(avatarPos.y - 32, EDGE, viewport.height - BUBBLE_SIZE.height - EDGE);
    const tailTop = clamp(avatarMiddleY - top - 18, 52, BUBBLE_SIZE.height - 68);
    const tailSide = direction === 'left' ? 'right' : 'left';
    return {
      left: clampedLeft,
      top,
      tailTop,
      tailSide,
    };
  }, [avatarPos, viewport]);

  function beginDrag(event: ReactPointerEvent<HTMLElement>) {
    event.preventDefault();
    dragRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      baseX: avatarPos.x,
      baseY: avatarPos.y,
      moved: false,
    };
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      // noop
    }
  }

  function moveDrag(event: ReactPointerEvent<HTMLElement>) {
    const drag = dragRef.current;
    if (!drag) return;
    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;
    if (Math.abs(dx) + Math.abs(dy) > 5) drag.moved = true;
    const next = clampAvatarPoint({ x: drag.baseX + dx, y: drag.baseY + dy }, viewport);
    setAvatarPos(next);
    writePoint(STORAGE_KEY, next);
  }

  function endDrag(event: ReactPointerEvent<HTMLElement>) {
    const drag = dragRef.current;
    if (!drag) return;
    suppressNextClickRef.current = drag.moved;
    try {
      event.currentTarget.releasePointerCapture(event.pointerId);
    } catch {
      // noop
    }
    dragRef.current = null;
    if (suppressNextClickRef.current) {
      window.setTimeout(() => {
        suppressNextClickRef.current = false;
      }, 0);
    }
  }

  function handleAvatarClick() {
    if (suppressNextClickRef.current) {
      suppressNextClickRef.current = false;
      return;
    }
    setBubbleOpen((prev) => !prev);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const text = normalizeMessageText('user', input);
    if (!text || !activeModel) return;

    const userId = `agent-u-${Date.now()}`;
    const assistantId = `agent-a-${Date.now()}`;
    setMessages((prev) => [
      ...prev.slice(-9),
      { id: userId, role: 'user', text },
      { id: assistantId, role: 'assistant', text: '', pending: true },
    ]);
    setInput('');
    setBubbleOpen(true);
    setAgentState('thinking');

    let answer = '';
    let sawDelta = false;
    try {
      await streamChat(
        {
          model_id: activeModel.id,
          message: text,
          system_prompt: SYSTEM_PROMPT,
          temperature: Number(runtime.temperature ?? 0.2),
          max_tokens: Number(runtime.max_tokens ?? 1024),
          runtime: runtime as unknown as Record<string, unknown>,
        },
        (chunk) => {
          if (chunk.type === 'delta') {
            sawDelta = true;
            answer += chunk.text || '';
            setAgentState('speaking');
            setMessages((prev) => prev.map((item) => (
              item.id === assistantId
                ? { ...item, text: normalizeMessageText('assistant', stripThinking(answer)), pending: true }
                : item
            )));
            return;
          }
          if (chunk.type === 'done') {
            const finalAnswer = normalizeMessageText(
              'assistant',
              chunk.answer || stripThinking(chunk.content || answer) || 'Ответ не сформировался.',
            );
            setMessages((prev) => prev.map((item) => (
              item.id === assistantId
                ? { ...item, text: finalAnswer, pending: false }
                : item
            )));
            setAgentState('ready');
            return;
          }
          if (chunk.type === 'error') {
            throw new Error(chunk.message || 'Ошибка генерации');
          }
        },
      );

      if (!sawDelta && !answer) {
        setMessages((prev) => prev.map((item) => (
          item.id === assistantId
            ? { ...item, text: 'Ответ не сформировался.', pending: false }
            : item
        )));
        setAgentState('ready');
      }
    } catch (error) {
      const message = getErrorMessage(error);
      setMessages((prev) => prev.map((item) => (
        item.id === assistantId
          ? { ...item, text: `Ошибка: ${message}`, pending: false }
          : item
      )));
      setAgentState('error');
    }
  }

  return (
    <main className={`agent-v12-root ${desktopMode ? 'desktop' : 'browser'} state-${agentState}`}>
      <button
        type="button"
        className="agent-v12-character-button"
        style={{ left: `${avatarPos.x}px`, top: `${avatarPos.y}px` }}
        onClick={handleAvatarClick}
        onPointerDown={beginDrag}
        onPointerMove={moveDrag}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        aria-label="Помощник"
        title="Нажми, чтобы открыть чат. Зажми и перетащи, чтобы передвинуть."
      >
        <span className="agent-v12-backplate" />
        <span className="agent-v12-shadow" />
        <img className="agent-v12-character" src={avatarFrame} alt="" draggable={false} />
        <span className={`agent-v12-badge ${badge.className}`}>{badge.text}</span>
      </button>

      {bubbleOpen && (
        <section
          className={`agent-v12-bubble tail-${bubbleLayout.tailSide}`}
          style={{
            left: `${bubbleLayout.left}px`,
            top: `${bubbleLayout.top}px`,
            ['--agent-tail-top' as string]: `${bubbleLayout.tailTop}px`,
          } as CSSProperties}
        >
          <header className="agent-v12-bubble-head">
            <div>
              <strong>{stateTitle(agentState)}</strong>
              <span>{stateStatus(agentState)}</span>
            </div>
            <button type="button" className="agent-v12-bubble-close" onClick={() => setBubbleOpen(false)} aria-label="Свернуть окно">
              ×
            </button>
          </header>

          <div className="agent-v12-history">
            {messages.length === 0 ? (
              <p className="agent-v12-placeholder">На связи. Напиши вопрос ниже — отвечу с уже выбранной локальной модели.</p>
            ) : (
              messages.slice(-8).map((message) => {
                const text = normalizeMessageText(message.role, message.text);
                return (
                  <article key={message.id} className={`agent-v12-message ${message.role} ${message.pending ? 'pending' : ''}`}>
                    <strong>{message.role === 'user' ? 'Вы' : 'Помощник'}:</strong>
                    <span>{text || (message.pending ? '...' : '')}</span>
                  </article>
                );
              })
            )}
          </div>

          <form className="agent-v12-compose" onSubmit={submit}>
            <input
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder={activeModel ? 'Напиши сообщение' : 'Сначала настрой модель'}
            />
            <button type="submit" disabled={!activeModel || !input.trim()} aria-label="Отправить">
              ↵
            </button>
          </form>
        </section>
      )}
    </main>
  );
}

function clampAvatarPoint(point: Point, viewport: Viewport): Point {
  return {
    x: clamp(point.x, EDGE, viewport.width - AVATAR_SIZE.width - EDGE),
    y: clamp(point.y, EDGE, viewport.height - AVATAR_SIZE.height - EDGE),
  };
}

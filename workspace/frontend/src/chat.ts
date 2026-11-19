import { useEffect, useRef, useState } from "react";
import { operatorToken } from "./access";
export type Project = {
  id: string;
  name: string;
  path: string;
  source?: string;
  github_url?: string;
};
export type Provider = {
  id: string;
  name: string;
  base_url: string;
  model: string;
  api_key_configured?: boolean;
};
export type RuntimeProbe = {
  models: { id: string }[];
  model_available: boolean | null;
  model_loaded: boolean;
};
export type Approval = {
  id: string;
  kind: string;
  payload: Record<string, any>;
};
export type Conversation = {
  id: string;
  project_id: string | null;
  provider_id: string;
  title: string;
  prompt?: string;
  status: string;
  revision: number;
  updated_at: string;
  mode?: string;
  details?: Record<string, any>;
  approvals?: Approval[];
};
export type ChatEvent = {
  seq: number;
  kind: string;
  created_at: string;
  payload: Record<string, any>;
};
export type Connector = {
  id: string;
  name: string;
  base_url: string;
  allow_write: boolean;
};
export type Bootstrap = {
  version: string;
  projects: Project[];
  providers: Provider[];
  tasks: Conversation[];
  connectors: Connector[];
};
export const emptyBoot: Bootstrap = {
  version: "",
  projects: [],
  providers: [],
  tasks: [],
  connectors: [],
};
export const agentTrustNotice =
  "Агент запускает команды с правами вашей учётной записи. «Только чтение» ограничивает запись в файлы, а доступ к вашим данным и локальным сервисам определяется правами ОС.";

export const active = new Set([
  "starting",
  "running",
  "awaiting_permission",
  "stopping",
]);
export const states: Record<string, string> = {
  queued: "Новый диалог",
  retryable: "Не удалось начать",
  starting: "Подключаюсь",
  running: "Пишет ответ",
  awaiting_permission: "Нужно разрешение",
  stopping: "Останавливаю",
  completed: "",
  failed: "Ошибка ответа",
  cancelled: "Ответ остановлен",
  interrupted: "Прервано",
  uncertain: "Нужна сверка",
  restored: "",
};
export async function api<T = any>(path: string, body?: unknown): Promise<T> {
  const token = await operatorToken;
  if (!token)
    throw new Error(
      "Откройте Workspace из приложения или по ссылке из терминала.",
    );
  const headers = new Headers({ "X-Workspace-UI-Token": token });
  if (body !== undefined) headers.set("Content-Type", "application/json");
  const r = await fetch("/api" + path, {
    headers,
    method: body === undefined ? "GET" : "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
    redirect: "error",
  });
  const data = await r.json();
  if (!r.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail || data),
    );
  return data;
}

export function submitMessage(row: Conversation, raw: string, memory: boolean) {
  const action = ["queued", "retryable"].includes(row.status)
    ? "start"
    : "continue";
  return api("/chats/" + row.id + "/" + action, {
    revision: row.revision,
    prompt: raw.trim(),
    memory,
  });
}

export function pendingPrompt(row: Conversation | null) {
  return row && ["queued", "retryable"].includes(row.status)
    ? String(row.details?.initial_prompt ?? row.prompt ?? "")
    : "";
}

export function transferDraft(
  drafts: Record<string, string>,
  from: string,
  to: string,
  initial: string,
) {
  const next = { ...drafts, [to]: drafts[from] ?? initial };
  delete next[from];
  return next;
}

export function clearSentDraft(
  drafts: Record<string, string>,
  id: string,
  sent: string,
) {
  if (drafts[id] !== sent) return drafts;
  const next = { ...drafts };
  delete next[id];
  return next;
}
function text(value: any): string {
  return typeof value === "string"
    ? value
    : Array.isArray(value)
      ? value
          .filter((b) => b.type === "text")
          .map((b) => b.text || "")
          .join("\n")
      : "";
}
export type Message = {
  id: number;
  role: string;
  text: string;
  time: string;
  error?: string;
};
export function messages(events: ChatEvent[]): Message[] {
  const out: Message[] = [];
  const hasPlain = events.some((e) => e.kind === "chat_message");
  const canonicalUser = events.some(
    (e) => e.kind === "dsh" && e.payload.type === "user/message",
  );
  const admitted = events.some(
    (e) => e.kind === "input_admitted" && e.payload.prompt,
  );
  for (const e of events) {
    const p = e.payload,
      kind = p.type,
      data = p.data || {},
      m = data.message || data;
    let role = "",
      value = "",
      error = "";
    if (e.kind === "chat_message") {
      role = p.role;
      value = p.text || "";
      error = p.error || (p.phase === "cancelled" ? "Ответ остановлен." : "");
    } else if (e.kind === "input_admitted" && !hasPlain && !canonicalUser) {
      role = "user";
      value = p.prompt || "";
    } else if (
      e.kind === "task_created" &&
      !(hasPlain || canonicalUser || admitted)
    ) {
      role = "user";
      value = p.prompt || "";
    } else if (
      e.kind === "dsh" &&
      ["user/message", "assistant/message"].includes(kind)
    ) {
      role = kind === "user/message" ? "user" : "assistant";
      value = text(m.content);
    } else if (e.kind === "result" && p.final_response) {
      role = "assistant";
      value = p.final_response;
    }
    if (!(value || error)) continue;
    if (!hasPlain && out.at(-1)?.role === role && out.at(-1)?.text === value)
      continue;
    out.push({ id: e.seq, role, text: value, error, time: e.created_at });
  }
  return out;
}
export function useConversation(selected: string) {
  const [current, setCurrent] = useState<Conversation | null>(null),
    [events, setEvents] = useState<ChatEvent[]>([]),
    [error, setError] = useState("");
  const cursor = useRef(0);
  useEffect(() => {
    let valid = true,
      timer = 0;
    cursor.current = 0;
    setCurrent(null);
    setEvents([]);
    setError("");
    if (!selected) return;
    const poll = async () => {
      try {
        const row = await api<Conversation>("/chats/" + selected);
        if (!valid) return;
        setCurrent(row);
        let more = true;
        while (more && valid) {
          const page = await api<{ events: ChatEvent[]; cursor: number }>(
            "/chats/" + selected + "/events?after=" + cursor.current,
          );
          if (!valid) return;
          cursor.current = page.cursor;
          if (page.events.length)
            setEvents((old) => {
              const ids = new Set(old.map((e) => e.seq));
              return [...old, ...page.events.filter((e) => !ids.has(e.seq))];
            });
          more = page.events.length === 500;
        }
      } catch (e) {
        if (valid) setError((e as Error).message);
      } finally {
        if (valid) timer = window.setTimeout(poll, 500);
      }
    };
    poll();
    return () => {
      valid = false;
      clearTimeout(timer);
    };
  }, [selected]);
  return { current, events, error };
}

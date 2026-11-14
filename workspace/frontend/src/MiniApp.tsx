import { useEffect, useRef, useState } from "react";
import "./mini.css";

type Project = { id: string; name: string };
type Provider = { id: string; model: string };
type Task = {
  id: string;
  project_id: string;
  provider_id: string;
  title: string;
  status: string;
  revision: number;
  approvals?: unknown[];
};
type Event = {
  seq: number;
  kind: string;
  created_at: string;
  payload: Record<string, any>;
};
type Bootstrap = { projects: Project[]; providers: Provider[]; tasks: Task[] };
const active = new Set([
  "starting",
  "running",
  "awaiting_permission",
  "stopping",
]);
const states: Record<string, string> = {
  queued: "Можно запустить",
  starting: "Запускаю",
  running: "Работаю",
  awaiting_permission: "Нужно разрешение",
  stopping: "Останавливаю",
  completed: "Готово",
  failed: "Ошибка",
  cancelled: "Остановлено",
  interrupted: "Прервано",
  uncertain: "Нужна сверка",
  restored: "История восстановлена",
};

async function api<T = any>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(
    "/api" + path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  const value = await response.json();
  if (!response.ok)
    throw new Error(
      typeof value.detail === "string"
        ? value.detail
        : JSON.stringify(value.detail || value),
    );
  return value;
}
function content(value: any): string {
  return typeof value === "string"
    ? value
    : Array.isArray(value)
      ? value
          .filter((block) => block.type === "text")
          .map((block) => block.text || "")
          .join("\n")
      : "";
}
function messages(events: Event[]) {
  const result: { id: number; role: string; text: string; time: string }[] = [];
  for (const event of events) {
    const kind = event.payload.type,
      data = event.payload.data || {},
      message = data.message || data;
    if (event.kind === "task_created" && event.payload.prompt) {
      result.push({
        id: event.seq,
        role: "user",
        text: event.payload.prompt,
        time: event.created_at,
      });
    } else if (
      event.kind === "dsh" &&
      ["user/message", "assistant/message"].includes(kind)
    ) {
      const text = content(message.content);
      if (text && result[result.length - 1]?.text !== text)
        result.push({
          id: event.seq,
          role: kind === "user/message" ? "user" : "assistant",
          text,
          time: new Date(event.payload.time || event.created_at).toISOString(),
        });
    } else if (event.kind === "result" && event.payload.final_response) {
      const text = event.payload.final_response;
      if (result[result.length - 1]?.text !== text)
        result.push({
          id: event.seq,
          role: "assistant",
          text,
          time: event.created_at,
        });
    }
  }
  return result;
}

export function MiniApp() {
  const [boot, setBoot] = useState<Bootstrap>({
    projects: [],
    providers: [],
    tasks: [],
  });
  const [project, setProject] = useState(
    sessionStorage.getItem("workspace-mini-project") || "",
  );
  const [selected, setSelected] = useState(
    sessionStorage.getItem("workspace-mini-task") || "",
  );
  const [task, setTask] = useState<Task | null>(null),
    [events, setEvents] = useState<Event[]>([]),
    [menu, setMenu] = useState(false),
    [error, setError] = useState(""),
    [sending, setSending] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>(() => {
    try {
      return JSON.parse(
        sessionStorage.getItem("workspace-mini-drafts") || "{}",
      );
    } catch {
      return {};
    }
  });
  const draftKey = selected || "new:" + project,
    draft = drafts[draftKey] || "";
  const setDraft = (value: string) =>
    setDrafts((old) => ({ ...old, [draftKey]: value }));
  useEffect(() => {
    try {
      sessionStorage.setItem("workspace-mini-drafts", JSON.stringify(drafts));
    } catch {}
  }, [drafts]);
  const [provider, setProvider] = useState(""),
    [write, setWrite] = useState(false);
  const cursor = useRef(0),
    sticky = useRef(true),
    scroll = useRef<HTMLDivElement>(null);
  useEffect(() => {
    sessionStorage.setItem("workspace-mini-project", project);
  }, [project]);
  useEffect(() => {
    sessionStorage.setItem("workspace-mini-task", selected);
    cursor.current = 0;
    setEvents([]);
    setTask(null);
    sticky.current = true;
    let valid = true,
      timer = 0;
    const poll = async () => {
      try {
        const data = await api<Bootstrap>("/bootstrap");
        if (!valid) return;
        setBoot(data);
        if (!project && data.projects.length === 1)
          setProject(data.projects[0].id);
        if (selected) {
          const [current, page] = await Promise.all([
            api<Task>("/tasks/" + selected),
            api<{ events: Event[]; cursor: number }>(
              "/tasks/" + selected + "/events?after=" + cursor.current,
            ),
          ]);
          if (!valid) return;
          setTask(current);
          cursor.current = page.cursor;
          if (page.events.length)
            setEvents((old) => {
              const ids = new Set(old.map((row) => row.seq));
              return [
                ...old,
                ...page.events.filter((row) => !ids.has(row.seq)),
              ];
            });
        }
      } catch (e) {
        if (valid) setError((e as Error).message);
      } finally {
        if (valid) timer = window.setTimeout(poll, 1000);
      }
    };
    poll();
    return () => {
      valid = false;
      clearTimeout(timer);
    };
  }, [selected]);
  useEffect(() => {
    if (sticky.current && scroll.current)
      scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [events]);
  async function main(panel?: string) {
    try {
      await api("/host/open-main", {
        task_id: selected,
        project_id: project,
        panel,
      });
    } catch {
      window.open(
        "/" +
          (selected
            ? "?task=" +
              encodeURIComponent(selected) +
              "&project=" +
              encodeURIComponent(project)
            : ""),
        "_blank",
      );
    }
  }
  async function send() {
    if ((!draft.trim() && task?.status !== "queued") || sending) return;
    const raw = draft,
      key = draftKey;
    setSending(true);
    setError("");
    try {
      if (!selected) {
        if (!project || !(provider || boot.providers[0]?.id))
          throw new Error(
            "Сначала выберите проект и подключите Runtime в Workspace.",
          );
        const created = await api<Task>("/tasks", {
          project_id: project,
          provider_id: provider || boot.providers[0].id,
          prompt: raw.trim(),
          action_id: crypto.randomUUID(),
          permission: write ? "workspace-write" : "read-only",
        });
        setSelected(created.id);
        await api("/tasks/" + created.id + "/start", {
          revision: created.revision,
        });
      } else {
        const current = await api<Task>("/tasks/" + selected);
        if (current.status === "queued")
          await api("/tasks/" + selected + "/start", {
            revision: current.revision,
          });
        else
          await api("/tasks/" + selected + "/continue", {
            revision: current.revision,
            prompt: raw.trim(),
          });
      }
      setDrafts((old) => (old[key] === raw ? { ...old, [key]: "" } : old));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  }
  async function stop() {
    try {
      const current = await api<Task>("/tasks/" + selected);
      await api("/tasks/" + selected + "/stop", { revision: current.revision });
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const working = !!task && active.has(task.status),
    recovery = task && ["interrupted", "uncertain"].includes(task.status),
    visibleMessages = messages(events);
  return (
    <div className="mini-shell">
      <header className="mini-header">
        <button
          aria-label="Диалоги"
          aria-expanded={menu}
          onClick={() => setMenu(!menu)}
        >
          ☰
        </button>
        <span className="mini-brand pywebview-drag-region">
          Local AI · помощник
          <small>
            {task
              ? states[task.status] || task.status
              : "Новый диалог Workspace"}
          </small>
        </span>
        <button
          title="Открыть Workspace"
          aria-label="Открыть Workspace"
          onClick={() => main()}
        >
          ↗
        </button>
        <button
          aria-label="Скрыть мини-помощника"
          onClick={() => api("/host/hide-mini", {}).catch(() => window.close())}
        >
          ×
        </button>
      </header>
      <div className="mini-body">
        {menu && (
          <aside className="mini-dialogs">
            <button
              className="primary"
              onClick={() => {
                setSelected("");
                setMenu(false);
                setError("");
              }}
            >
              ＋ Новый диалог
            </button>
            <label>
              Проект
              <select
                aria-label="Проект мини-помощника"
                value={project}
                onChange={(e) => {
                  setProject(e.target.value);
                  setSelected("");
                }}
              >
                <option value="">Выберите проект</option>
                {boot.projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            <nav>
              {boot.tasks
                .filter((t) => !project || t.project_id === project)
                .map((t) => (
                  <button
                    key={t.id}
                    className={selected === t.id ? "selected" : ""}
                    onClick={() => {
                      setSelected(t.id);
                      setProject(t.project_id);
                      setMenu(false);
                      setError("");
                    }}
                  >
                    {t.title}
                    <small>{states[t.status] || t.status}</small>
                  </button>
                ))}
            </nav>
          </aside>
        )}
        <section className="mini-conversation">
          {error && (
            <div className="mini-error" role="alert">
              {error}
              <button aria-label="Закрыть ошибку" onClick={() => setError("")}>
                ×
              </button>
            </div>
          )}
          <div
            className="mini-messages"
            ref={scroll}
            onScroll={(e) => {
              const node = e.currentTarget;
              sticky.current =
                node.scrollHeight - node.scrollTop - node.clientHeight < 65;
            }}
          >
            {!visibleMessages.length && (
              <div className="mini-empty">
                <strong>На связи</strong>
                <p>Задачи, диалоги и память — в Workspace.</p>
                {!selected && (
                  <>
                    <select
                      aria-label="Проект нового диалога"
                      value={project}
                      onChange={(e) => setProject(e.target.value)}
                    >
                      <option value="">Выберите проект</option>
                      {boot.projects.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                    </select>
                    {boot.providers.length ? (
                      <select
                        aria-label="Модель мини-помощника"
                        value={provider || boot.providers[0]?.id}
                        onChange={(e) => setProvider(e.target.value)}
                      >
                        {boot.providers.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.model}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <button onClick={() => main("provider")}>
                        Подключить Runtime
                      </button>
                    )}
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={write}
                        onChange={(e) => setWrite(e.target.checked)}
                      />
                      Разрешить запись в выбранном проекте
                    </label>
                  </>
                )}
              </div>
            )}
            {visibleMessages.map((message) => (
              <article
                key={message.id}
                className={"mini-message " + message.role}
              >
                <header>
                  <strong>{message.role === "user" ? "Вы" : "Помощник"}</strong>
                  <time dateTime={message.time}>
                    {new Date(message.time).toLocaleString("ru-RU", {
                      day: "2-digit",
                      month: "2-digit",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </time>
                </header>
                <div>{message.text}</div>
              </article>
            ))}
            {working && (
              <p className="mini-progress">
                {task?.status === "awaiting_permission"
                  ? "Нужно ваше решение в Workspace."
                  : "Работаю над задачей…"}{" "}
                <button onClick={() => main()}>Действия и diff ↗</button>
              </p>
            )}
            {recovery && (
              <p className="mini-recovery">
                Действия могли успеть выполниться. Откройте Workspace и
                восстановите историю перед продолжением.
                <button onClick={() => main()}>Восстановление ↗</button>
              </p>
            )}
          </div>
          <form
            className="mini-composer"
            onSubmit={(e) => {
              e.preventDefault();
              send();
            }}
          >
            <textarea
              aria-label="Сообщение мини-помощнику"
              rows={2}
              value={draft}
              disabled={task?.status==='queued'}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={
                task?.status==='queued' ? 'Задача создана. Нажмите «Запустить».' : selected ? "Продолжить этот диалог…" : "Напишите задачу…"
              }
            />
            {working ? (
              <button type="button" className="stop" onClick={stop}>
                ■ Стоп
              </button>
            ) : (
              <button
                className="primary"
                disabled={
                  sending ||
                  (!draft.trim()&&task?.status!=='queued') ||
                  !!recovery ||
                  (!selected && (!project || !boot.providers.length))
                }
              >
                {task?.status==='queued'?'Запустить':'Отправить'}
              </button>
            )}
          </form>
        </section>
      </div>
      <footer className="mini-footer">
        Workspace · {task ? "Память этой сессии" : "Новая сессия"}
        <button onClick={() => main()}>Открыть полностью ↗</button>
      </footer>
    </div>
  );
}

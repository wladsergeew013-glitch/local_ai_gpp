import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
import { SettingsDialog } from "./SettingsDialog";
import { MiniApp } from "./MiniApp";

type Project = { id: string; name: string; path: string };
type Provider = { id: string; name: string; base_url: string; model: string };
type Task = {
  id: string;
  project_id: string;
  provider_id: string;
  title: string;
  status: string;
  revision: number;
  updated_at: string;
  prompt?: string;
  details?: Record<string, any>;
  approvals?: Approval[];
};
type Approval = { id: string; kind: string; payload: Record<string, any> };
type Event = {
  seq: number;
  kind: string;
  created_at: string;
  payload: Record<string, any>;
};
type Connector = {
  id: string;
  name: string;
  base_url: string;
  allow_write: boolean;
};
type Bootstrap = {
  version: string;
  projects: Project[];
  providers: Provider[];
  tasks: Task[];
  connectors: Connector[];
};
const active = new Set([
  "starting",
  "running",
  "awaiting_permission",
  "stopping",
]);
const labels: Record<string, string> = {
  queued: "В очереди",
  starting: "Запуск",
  running: "Выполняется",
  awaiting_permission: "Нужно разрешение",
  stopping: "Остановка",
  completed: "Завершена",
  failed: "Ошибка",
  cancelled: "Остановлена",
  interrupted: "Прервана",
  uncertain: "Результат требует сверки",
  restored: "История восстановлена",
};

async function api<T = any>(path: string, body?: unknown): Promise<T> {
  const result = await fetch(
    "/api" + path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  const data = await result.json();
  if (!result.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail || data),
    );
  return data;
}
function textContent(value: any): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value))
    return value.map((v) => v.text || v.content || "").join("\n");
  return value ? JSON.stringify(value, null, 2) : "";
}
function EventCard({ event }: { event: Event }) {
  const payload = event.payload,
    type = event.kind === "dsh" ? payload.type : event.kind,
    data = payload.data || payload;
  if (
    [
      "step/start",
      "step/end",
      "turn/start",
      "turn/end",
      "input_admitted",
      "identity",
      "notification",
      "started",
      "session/start",
      "session/end",
      "approval_channel_closed",
    ].includes(type)
  )
    return null;
  let title = type,
    content = "",
    tone = "";
  if (type === "tool/call") {
    title = "Действие · " + data.name;
    content = textContent(data.arguments);
    tone = "action";
  } else if (type === "tool/result") {
    title = data.message?.isError
      ? "Ошибка инструмента"
      : "Результат инструмента";
    content = textContent(data.message?.content);
    tone = data.message?.isError ? "danger" : "output";
  } else if (type === "result") {
    title = "Ответ агента";
    content = payload.final_response || "";
    tone = "answer";
  } else if (type === "diagnostic") {
    return (
      <details className="diagnostic">
        <summary>Диагностика адаптера</summary>
        <pre>{payload.message}</pre>
      </details>
    );
  } else if (type === "task_created") {
    title = "Задача";
    content = payload.prompt;
    tone = "prompt";
  } else if (type === "process_exited") {
    title = "Процесс завершён";
    content = labels[payload.status] || payload.status;
  } else if (type === "uncertain") {
    title = "Нужна сверка состояния";
    content = payload.message;
    tone = "danger";
  } else if (data.message?.content) {
    title = "Агент";
    content = textContent(data.message.content);
  } else if (
    [
      "approval",
      "question",
      "permission_decision",
      "stop_confirmed",
      "recovery",
      "restored",
    ].includes(type)
  ) {
    title = (
      {
        approval: "Запрос разрешения",
        question: "Вопрос агента",
        permission_decision: "Решение пользователя",
        stop_confirmed: "Остановка подтверждена",
        recovery: "Восстановление",
        restored: "История восстановлена",
      } as Record<string, string>
    )[type];
    content = JSON.stringify(payload, null, 2);
  } else
    return (
      <details className="diagnostic">
        <summary>{type}</summary>
        <pre>{JSON.stringify(data, null, 2)}</pre>
      </details>
    );
  return (
    <article className={"event " + tone}>
      <header>
        <span>{title}</span>
        <time dateTime={event.created_at}>
          {new Date(event.created_at).toLocaleString("ru")}
        </time>
      </header>
      <pre>{content || "—"}</pre>
    </article>
  );
}

function App() {
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const receive: EventListener = (event) => {
      const detail = (
        event as CustomEvent<{
          task_id?: string;
          project_id?: string;
          panel?: string;
        }>
      ).detail;
      if (detail.task_id) setSelected(detail.task_id);
      if (detail.project_id) setProject(detail.project_id);
      if (
        detail.panel === "provider" ||
        detail.panel === "project" ||
        detail.panel === "connector"
      )
        setPanel(detail.panel);
    };
    window.addEventListener("local-ai-select-task", receive);
    return () => window.removeEventListener("local-ai-select-task", receive);
  }, []);
  const [connectorIds, setConnectorIds] = useState<string[]>([]);
  const [boot, setBoot] = useState<Bootstrap>({
    version: "0.1.0",
    projects: [],
    providers: [],
    tasks: [],
    connectors: [],
  });
  const [project, setProject] = useState(
    new URLSearchParams(location.search).get("project") ??
      sessionStorage.getItem("workspace-project") ??
      "",
  );
  const [selected, setSelected] = useState(
    new URLSearchParams(location.search).get("task") ??
      sessionStorage.getItem("workspace-task") ??
      "",
  );
  const [task, setTask] = useState<Task | null>(null),
    [events, setEvents] = useState<Event[]>([]),
    [diff, setDiff] = useState<any>(null);
  const [prompt, setPrompt] = useState(""),
    [write, setWrite] = useState(false),
    [provider, setProvider] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [panel, setPanel] = useState<
      "none" | "project" | "provider" | "connector"
    >("none"),
    [tab, setTab] = useState<"activity" | "diff">("activity");
  const [projectName, setProjectName] = useState(""),
    [projectPath, setProjectPath] = useState("");
  const [base, setBase] = useState("http://127.0.0.1:8765/v1"),
    [model, setModel] = useState(""),
    [key, setKey] = useState(""),
    [models, setModels] = useState<any[]>([]),
    [connection, setConnection] = useState<any>(null);
  const [connectorUrl, setConnectorUrl] = useState("http://127.0.0.1:8890"),
    [connectorResult, setConnectorResult] = useState(""),
    [noteTitle, setNoteTitle] = useState(""),
    [noteBody, setNoteBody] = useState(""),
    [approveWrite, setApproveWrite] = useState(false);
  const [questionText, setQuestionText] = useState<Record<string, string>>({});
  const cursor = useRef(0),
    sticky = useRef(true),
    scroll = useRef<HTMLDivElement>(null);
  const reload = () => api<Bootstrap>("/bootstrap").then(setBoot);
  useEffect(() => {
    let valid = true;
    const poll = () =>
      api<Bootstrap>("/bootstrap")
        .then((data) => {
          if (valid) setBoot(data);
        })
        .catch((e) => {
          if (valid) setError(e.message);
        });
    poll();
    const timer = setInterval(poll, 1500);
    return () => {
      valid = false;
      clearInterval(timer);
    };
  }, []);
  useEffect(() => {
    sessionStorage.setItem("workspace-project", project);
  }, [project]);
  useEffect(() => {
    sessionStorage.setItem("workspace-task", selected);
    cursor.current = 0;
    setEvents([]);
    setTask(null);
    setDiff(null);
    sticky.current = true;
    if (!selected) return;
    let valid = true;
    const poll = async () => {
      try {
        const [current, page] = await Promise.all([
          api<Task>("/tasks/" + selected),
          api<{ events: Event[]; cursor: number }>(
            "/tasks/" + selected + "/events?after=" + cursor.current,
          ),
        ]);
        if (!valid) return;
        setTask(current);
        cursor.current = page.cursor;
        if (page.events.length) setEvents((old) => [...old, ...page.events]);
      } catch (e) {
        if (valid) setError((e as Error).message);
      }
    };
    poll();
    const timer = setInterval(poll, 1000);
    return () => {
      valid = false;
      clearInterval(timer);
    };
  }, [selected]);
  useEffect(() => {
    if (sticky.current && scroll.current)
      scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [events]);
  useEffect(() => {
    if (selected && tab === "diff")
      api("/tasks/" + selected + "/diff")
        .then(setDiff)
        .catch((e) => setError(e.message));
  }, [selected, tab, task?.status]);
  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await action();
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function createTask() {
    if (!prompt.trim() || !project || !(provider || boot.providers[0]?.id))
      return;
    const raw = prompt;
    const created = await api<Task>("/tasks", {
      project_id: project,
      provider_id: provider || boot.providers[0].id,
      prompt: raw.trim(),
      permission: write ? "workspace-write" : "read-only",
      connector_ids: connectorIds,
      action_id: crypto.randomUUID(),
    });
    setSelected(created.id);
    if (prompt === raw) setPrompt("");
    await api("/tasks/" + created.id + "/start", {
      revision: created.revision,
    });
  }
  const currentProject = boot.projects.find((p) => p.id === project),
    filteredTasks = boot.tasks.filter(
      (t) => !project || t.project_id === project,
    );
  const working = task && active.has(task.status);
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="mark">W</span>
          <div>
            Local AI <strong>Workspace</strong>
            <small>Локальная работа с проектами</small>
          </div>
        </div>
        <div className="side-section">
          <h2>
            Проекты{" "}
            <button title="Добавить проект" onClick={() => setPanel("project")}>
              ＋
            </button>
          </h2>
          <button
            className={"project " + (!project ? "selected" : "")}
            onClick={() => setProject("")}
          >
            Все задачи <span>{boot.tasks.length}</span>
          </button>
          {boot.projects.map((p) => (
            <button
              className={"project " + (project === p.id ? "selected" : "")}
              key={p.id}
              onClick={() => {
                setProject(p.id);
                setSelected("");
              }}
              title={p.path}
            >
              <i>▣</i>
              {p.name}
            </button>
          ))}
        </div>
        <div className="side-section task-list">
          <h2>
            Задачи{" "}
            <button title="Новая задача" onClick={() => setSelected("")}>
              ＋
            </button>
          </h2>
          {filteredTasks.map((t) => (
            <button
              className={"task-link " + (selected === t.id ? "selected" : "")}
              key={t.id}
              onClick={() => {
                setSelected(t.id);
                setProject(t.project_id);
              }}
            >
              <span className={"dot " + t.status} />
              <span>
                {t.title}
                <small>{labels[t.status] || t.status}</small>
              </span>
            </button>
          ))}
          {!filteredTasks.length && (
            <p className="muted empty-side">Здесь появятся задачи проекта.</p>
          )}
        </div>
        <div className="side-footer">
          <button
            onClick={() =>
              api("/host/show-mini", {}).catch(() =>
                window.open("/mini", "LocalAIMini", "width=650,height=500"),
              )
            }
          >
            ◉ Мини-помощник
          </button>
          <button onClick={() => setPanel("provider")}>
            ⚙ Провайдеры и модели
          </button>
          <button onClick={() => setPanel("connector")}>↗ Подключения</button>
          <small>Workspace {boot.version} · DSH 0.2.2rc2</small>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div>
            <span className="eyebrow">РАБОЧЕЕ ПРОСТРАНСТВО</span>
            <h1>{currentProject?.name || "Проекты и задачи"}</h1>
          </div>
          <div className="top-actions">
            <span className="local-badge">
              <span className="dot completed" />
              Локальный профиль
            </span>
            <button
              onClick={() =>
                act(async () => {
                  const result = await api("/snapshots", {});
                  setNotice("Снимок данных сохранён: " + result.path);
                })
              }
            >
              Снимок данных
            </button>
          </div>
        </header>
        {error && (
          <div role="alert" className="alert">
            {error}
            <button aria-label="Закрыть ошибку" onClick={() => setError("")}>
              ×
            </button>
          </div>
        )}
        {notice && (
          <div className="workspace-notice" role="status">
            {notice}
            <button
              aria-label="Закрыть уведомление"
              onClick={() => setNotice("")}
            >
              ×
            </button>
          </div>
        )}
        {panel !== "none" && (
          <SettingsDialog onClose={() => setPanel("none")} error={error}>
            <section className="settings-panel">
              <header>
                <h2>
                  {panel === "project"
                    ? "Добавить проект"
                    : panel === "provider"
                      ? "Подключить Runtime"
                      : "Подключения к приложениям"}
                </h2>
                <button
                  aria-label="Закрыть панель"
                  onClick={() => setPanel("none")}
                >
                  ×
                </button>
              </header>
              {panel === "project" && (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    act(async () => {
                      const p = await api<Project>("/projects", {
                        name: projectName,
                        path: projectPath,
                      });
                      setProject(p.id);
                      setSelected("");
                      setPanel("none");
                    });
                  }}
                >
                  <label>
                    Название
                    <input
                      required
                      value={projectName}
                      onChange={(e) => setProjectName(e.target.value)}
                      placeholder="Мой проект"
                    />
                  </label>
                  <label>
                    Путь к Git-репозиторию
                    <input
                      required
                      value={projectPath}
                      onChange={(e) => setProjectPath(e.target.value)}
                      placeholder="C:\Projects\my-app"
                    />
                  </label>
                  <p className="muted">
                    Агент работает с выбранным проектом. Существующие изменения
                    сохраняются в исходной точке сравнения.
                  </p>
                  <button className="primary" disabled={busy}>
                    Добавить проект
                  </button>
                </form>
              )}
              {panel === "provider" && (
                <div>
                  <div className="form-grid">
                    <label>
                      Адрес Runtime API
                      <input
                        value={base}
                        onChange={(e) => {
                          setBase(e.target.value);
                          setConnection(null);
                        }}
                      />
                    </label>
                    <label>
                      Локальный API-ключ
                      <input
                        type="password"
                        value={key}
                        onChange={(e) => setKey(e.target.value)}
                        autoComplete="off"
                        placeholder="Если задан в Runtime"
                      />
                    </label>
                  </div>
                  <div className="form-grid">
                    <label>
                      ID модели
                      <input
                        list="model-ids"
                        value={model}
                        onChange={(e) => setModel(e.target.value)}
                      />
                      <datalist id="model-ids">
                        {models.map((m) => (
                          <option key={m.id} value={m.id} />
                        ))}
                      </datalist>
                    </label>
                    <div className="button-row">
                      <button
                        disabled={busy}
                        onClick={() =>
                          act(async () => {
                            const data = await api("/providers/probe", {
                              base_url: base,
                              model,
                              api_key: key,
                            });
                            setConnection(data);
                            setModels(data.models);
                            if (!model && data.models.length)
                              setModel(data.models[0].id);
                          })
                        }
                      >
                        Проверить связь
                      </button>
                      <button
                        className="primary"
                        disabled={busy || !model}
                        onClick={() =>
                          act(async () => {
                            const p = await api<Provider>("/providers", {
                              name: "Local AI Runtime",
                              base_url: base,
                              model,
                              api_key: key,
                            });
                            setProvider(p.id);
                            setKey("");
                            setPanel("none");
                          })
                        }
                      >
                        Сохранить
                      </button>
                    </div>
                  </div>
                  {connection && (
                    <div className="connection-result">
                      {connection.snapshot ? (
                        "Снимок сохранён: " + connection.snapshot
                      ) : (
                        <>
                          <strong>Связь установлена</strong>
                          <p>
                            Состояние: {connection.ready?.state}. Моделей:{" "}
                            {connection.models?.length}.{" "}
                            {connection.capabilities &&
                              `Контекст: ${connection.capabilities.context_window}; tools: ${connection.capabilities.tools.supported ? "поддерживаются" : "требуется загрузка модели"}.`}
                          </p>
                        </>
                      )}
                    </div>
                  )}
                  {boot.providers.map((p) => (
                    <p className="provider-row" key={p.id}>
                      <strong>{p.name}</strong>
                      <code>{p.base_url}</code>
                      <span>{p.model}</span>
                    </p>
                  ))}
                  <p className="muted">
                    Облачный ключ DeepSeek не нужен. Модель загружается в
                    отдельном Runtime; закрытие Workspace оставляет Runtime
                    работающим.
                  </p>
                </div>
              )}
              {panel === "connector" && (
                <div>
                  <p className="muted">
                    Первый адаптер подключает локальное приложение из
                    examples/notes_app.py. Чтение и запись выполняются
                    отдельными действиями.
                  </p>
                  <label>
                    Адрес приложения
                    <input
                      value={connectorUrl}
                      onChange={(e) => setConnectorUrl(e.target.value)}
                    />
                  </label>
                  <button
                    disabled={busy}
                    onClick={() =>
                      act(() =>
                        api("/connectors", {
                          name: "Local Notes",
                          base_url: connectorUrl,
                          allow_write: approveWrite,
                        }),
                      )
                    }
                  >
                    Добавить подключение
                  </button>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={approveWrite}
                      onChange={(e) => setApproveWrite(e.target.checked)}
                    />
                    Разрешить запись и подтвердить создание записи ниже
                  </label>
                  <div className="form-grid">
                    <label>
                      Название записи
                      <input
                        value={noteTitle}
                        onChange={(e) => setNoteTitle(e.target.value)}
                      />
                    </label>
                    <label>
                      Текст записи
                      <input
                        value={noteBody}
                        onChange={(e) => setNoteBody(e.target.value)}
                      />
                    </label>
                  </div>
                  {boot.connectors.map((c) => (
                    <div className="provider-row" key={c.id}>
                      <strong>{c.name}</strong>
                      <code>{c.base_url}</code>
                      <button
                        onClick={() =>
                          act(async () =>
                            setConnectorResult(
                              JSON.stringify(
                                await api("/connectors/" + c.id + "/items"),
                                null,
                                2,
                              ),
                            ),
                          )
                        }
                      >
                        Прочитать
                      </button>
                      <button
                        disabled={!approveWrite || !c.allow_write || !noteTitle}
                        onClick={() =>
                          act(async () =>
                            setConnectorResult(
                              JSON.stringify(
                                await api("/connectors/" + c.id + "/items", {
                                  title: noteTitle,
                                  body: noteBody,
                                  approved: true,
                                  action_id: crypto.randomUUID(),
                                }),
                                null,
                                2,
                              ),
                            ),
                          )
                        }
                      >
                        Создать подтверждённую запись
                      </button>
                    </div>
                  ))}
                  {connectorResult && (
                    <pre className="connector-output">{connectorResult}</pre>
                  )}
                </div>
              )}
            </section>
          </SettingsDialog>
        )}
        {!selected ? (
          <section className="welcome">
            <div className="welcome-symbol">⌘</div>
            <span className="eyebrow">ОДНА ЗАДАЧА — ПОНЯТНЫЙ РЕЗУЛЬТАТ</span>
            <h2>Что сделаем в проекте?</h2>
            <p>
              Проверьте тесты, исправьте ошибку или разберитесь в коде.
              <br />
              Действия, разрешения и изменения файлов останутся в истории
              задачи.
            </p>
            <div className="new-task">
              <label>
                Проект
                <select
                  aria-label="Проект новой задачи"
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
              </label>
              <textarea
                aria-label="Новая задача"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="Найди причину падающего теста, исправь код и проверь результат…"
                rows={5}
              />
              {!!boot.connectors.length && (
                <details className="task-connectors">
                  <summary>
                    Приложения для этой задачи ({connectorIds.length})
                  </summary>
                  {boot.connectors.map((c) => (
                    <label className="check" key={c.id}>
                      <input
                        type="checkbox"
                        checked={connectorIds.includes(c.id)}
                        onChange={(e) =>
                          setConnectorIds((old) =>
                            e.target.checked
                              ? [...old, c.id]
                              : old.filter((id) => id !== c.id),
                          )
                        }
                      />
                      {c.name} ·{" "}
                      {c.allow_write
                        ? "чтение и запись с подтверждением"
                        : "только чтение"}
                    </label>
                  ))}
                </details>
              )}
              <div className="compose-options">
                <select
                  aria-label="Модель для задачи"
                  value={provider || boot.providers[0]?.id || ""}
                  onChange={(e) => setProvider(e.target.value)}
                >
                  <option value="" disabled>
                    Подключите Runtime
                  </option>
                  {boot.providers.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.model}
                    </option>
                  ))}
                </select>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={write}
                    onChange={(e) => setWrite(e.target.checked)}
                  />
                  Запись в проект
                </label>
                <button
                  className="primary"
                  disabled={
                    busy || !project || !prompt.trim() || !boot.providers.length
                  }
                  onClick={() => act(createTask)}
                >
                  Запустить задачу ↗
                </button>
              </div>
            </div>
            <div className="welcome-links">
              <button onClick={() => setPanel("project")}>
                ＋ Добавить проект
              </button>
              <button onClick={() => setPanel("provider")}>
                ⚙ Подключить модель
              </button>
            </div>
          </section>
        ) : (
          <section className="task-view">
            <header className="task-header">
              <div>
                <h2>{task?.title || "Загрузка задачи…"}</h2>
                <p className="muted">
                  {boot.projects.find((p) => p.id === task?.project_id)?.path}
                </p>
              </div>
              <span className={"status " + task?.status}>
                {labels[task?.status || ""] || task?.status}
              </span>
            </header>
            <nav className="tabs">
              <button
                className={tab === "activity" ? "current" : ""}
                onClick={() => setTab("activity")}
              >
                Действия и результат
              </button>
              <button
                className={tab === "diff" ? "current" : ""}
                onClick={() => setTab("diff")}
              >
                Изменения файлов
              </button>
              <div className="task-controls">
                {working && (
                  <button
                    className="stop"
                    onClick={() =>
                      act(async () => {
                        const current = await api<Task>("/tasks/" + selected);
                        await api("/tasks/" + selected + "/stop", {
                          revision: current.revision,
                        });
                      })
                    }
                  >
                    ■ Остановить
                  </button>
                )}
                {task &&
                  ["interrupted", "uncertain", "cancelled", "failed"].includes(
                    task.status,
                  ) && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        act(() =>
                          api("/tasks/" + selected + "/restore", {
                            revision: task.revision,
                          }),
                        )
                      }
                    >
                      Восстановить историю
                    </button>
                  )}
                <button
                  onClick={() =>
                    act(() => api("/tasks/" + selected + "/reconcile", {}))
                  }
                >
                  Сверить историю
                </button>
              </div>
            </nav>
            {tab === "activity" ? (
              <div
                className="activity-scroll"
                ref={scroll}
                onScroll={(e) => {
                  const el = e.currentTarget;
                  sticky.current =
                    el.scrollHeight - el.scrollTop - el.clientHeight < 80;
                }}
              >
                {events.map((event) => (
                  <EventCard key={event.seq} event={event} />
                ))}
                {working && (
                  <p className="working">
                    <span className="pulse" />
                    Агент работает. Действия появятся после сохранения событий.
                  </p>
                )}
                {task?.approvals?.map((approval) => (
                  <div className="permission-card" key={approval.id}>
                    <h3>
                      {approval.kind === "question"
                        ? "Вопрос агента"
                        : "Разрешить это действие?"}
                    </h3>
                    <pre>
                      {JSON.stringify(approval.payload.payload, null, 2)}
                    </pre>
                    {approval.kind !== "question" ? (
                      <div className="button-row">
                        <button
                          className="primary"
                          onClick={() =>
                            act(() =>
                              api("/approvals/" + approval.id, {
                                outcome: "allowed-once",
                              }),
                            )
                          }
                        >
                          Разрешить один раз
                        </button>
                        <button
                          onClick={() =>
                            act(() =>
                              api("/approvals/" + approval.id, {
                                outcome: "rejected",
                              }),
                            )
                          }
                        >
                          Отклонить
                        </button>
                      </div>
                    ) : (
                      <>
                        <textarea
                          aria-label="Ответ агенту"
                          value={questionText[approval.id] || ""}
                          onChange={(e) =>
                            setQuestionText({
                              ...questionText,
                              [approval.id]: e.target.value,
                            })
                          }
                        />
                        <button
                          onClick={() =>
                            act(() =>
                              api("/approvals/" + approval.id, {
                                answers: (
                                  approval.payload.payload?.questions || []
                                ).map((q: any) => ({
                                  id: q.id,
                                  selected: [],
                                  custom: questionText[approval.id] || "",
                                })),
                              }),
                            )
                          }
                        >
                          Ответить
                        </button>
                      </>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="diff-scroll">
                <p className="muted">
                  Изменения относительно начала этой задачи. Исходные правки
                  пользователя сохранены.
                </p>
                <div className="file-chips">
                  {diff?.files?.map((f: any) => (
                    <span key={f.path}>
                      {f.added ? "+ " : f.deleted ? "− " : ""}
                      {f.path}
                    </span>
                  ))}
                </div>
                <pre className="diff">
                  {diff?.diff
                    ? diff.diff.split("\n").map((line: string, i: number) => (
                        <div
                          key={i}
                          className={
                            line.startsWith("+")
                              ? "add"
                              : line.startsWith("-")
                                ? "remove"
                                : line.startsWith("@@")
                                  ? "hunk"
                                  : ""
                          }
                        >
                          {line || " "}
                        </div>
                      ))
                    : "Изменений файлов пока нет."}
                </pre>
              </div>
            )}
            {task &&
              !working &&
              [
                "completed",
                "restored",
                "failed",
                "cancelled",
                "queued",
              ].includes(task.status) && (
                <div className="followup">
                  <textarea
                    aria-label="Продолжение задачи"
                    rows={2}
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    placeholder="Новая инструкция в этой сессии…"
                  />
                  <button
                    className="primary"
                    disabled={busy || !prompt.trim()}
                    onClick={() =>
                      act(async () => {
                        await api(
                          "/tasks/" +
                            selected +
                            (task.status === "queued" ? "/start" : "/continue"),
                          { revision: task.revision, prompt: prompt },
                        );
                        setPrompt("");
                      })
                    }
                  >
                    {task.status === "queued" ? "Запустить" : "Продолжить"} ↗
                  </button>
                </div>
              )}
            {task && ["interrupted", "uncertain"].includes(task.status) && (
              <div className="recovery-note">
                Действия могли успеть выполниться. Восстановите и проверьте
                историю и diff. Предыдущая команда автоматически не
                отправляется.
              </div>
            )}
          </section>
        )}
      </main>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {location.pathname === "/mini" ? <MiniApp /> : <App />}
  </React.StrictMode>,
);

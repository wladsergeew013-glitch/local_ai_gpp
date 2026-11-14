import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
import { MiniApp } from "./MiniApp";
import { WorkspaceSettings } from "./WorkspaceSettings";
import { ProjectDialog } from "./ProjectDialog";
import { ChatHistory } from "./ChatHistory";
import { AgentPanel } from "./AgentPanel";
import { RuntimeStatus } from "./RuntimeStatus";
import {
  api,
  active,
  states,
  emptyBoot,
  useConversation,
  type Bootstrap,
  type Conversation,
} from "./chat";

function App() {
  const [boot, setBoot] = useState<Bootstrap>(emptyBoot);
  const [project, setProject] = useState(
    sessionStorage.getItem("workspace-project") || "",
  );
  const [selected, setSelected] = useState(
    new URLSearchParams(location.search).get("task") ||
      sessionStorage.getItem("workspace-task") ||
      "",
  );
  const [settings, setSettings] = useState(false),
    [addProject, setAddProject] = useState(false),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [provider, setProvider] = useState(""),
    [files, setFiles] = useState(false),
    [write, setWrite] = useState(false),
    [memory, setMemory] = useState(true),
    [tools, setTools] = useState(false),
    [connectorIds, setConnectorIds] = useState<string[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>(() => {
    try {
      return JSON.parse(sessionStorage.getItem("workspace-drafts") || "{}");
    } catch {
      return {};
    }
  });
  const draftKey = selected || "new:" + project,
    draft = drafts[draftKey] || "";
  const { current, events, error: pollError } = useConversation(selected);
  const working = !!current && active.has(current.status);
  const agent = !!current && current.details?.mode !== "chat";
  const reload = () => api<Bootstrap>("/bootstrap").then(setBoot);
  useEffect(() => {
    let valid = true;
    const load = () =>
      api<Bootstrap>("/bootstrap")
        .then((b) => {
          if (valid) setBoot(b);
        })
        .catch((e) => {
          if (valid) setError(e.message);
        });
    load();
    const timer = setInterval(load, 1500);
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
    setTools(false);
  }, [selected]);
  useEffect(() => {
    sessionStorage.setItem("workspace-drafts", JSON.stringify(drafts));
  }, [drafts]);
  useEffect(() => {
    if (current) setMemory(current.details?.memory !== false);
  }, [current?.id]);
  useEffect(() => {
    if (current?.approvals?.length) setTools(true);
  }, [current?.approvals?.length]);
  useEffect(() => {
    const receive = (event: Event) => {
      const d = (event as CustomEvent).detail || {};
      if (d.task_id) setSelected(d.task_id);
      if (d.project_id) setProject(d.project_id);
      if (d.panel === "project") setAddProject(true);
      else if (d.panel) setSettings(true);
    };
    window.addEventListener("local-ai-select-task", receive);
    return () => window.removeEventListener("local-ai-select-task", receive);
  }, []);
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
  function newChat() {
    setSelected("");
    setFiles(false);
    setTools(false);
    setError("");
  }
  async function send() {
    const raw = draft,
      key = draftKey;
    if (!raw.trim() || busy || working) return;
    await act(async () => {
      if (!selected) {
        const p = provider || boot.providers[0]?.id;
        if (!p) throw new Error("Подключите модель в настройках.");
        const row = await api<Conversation>("/chats", {
          project_id: project || null,
          provider_id: p,
          prompt: raw.trim(),
          mode: files ? "agent" : "chat",
          memory,
          permission: files && write ? "workspace-write" : "read-only",
          connector_ids: files ? connectorIds : [],
          action_id: crypto.randomUUID(),
        });
        setSelected(row.id);
        await api("/chats/" + row.id + "/start", { revision: row.revision });
      } else {
        const row = await api<Conversation>("/chats/" + selected);
        await api(
          "/chats/" +
            selected +
            (row.status === "queued" ? "/start" : "/continue"),
          { revision: row.revision, prompt: raw.trim(), memory },
        );
      }
      setDrafts((old) => (old[key] === raw ? { ...old, [key]: "" } : old));
    });
  }
  const modelName = (id: string) =>
    boot.providers.find((p) => p.id === id)?.model.split(":")[0] || "Модель";
  const compose = (
    <div className="chat-composer">
      <textarea
        aria-label={selected ? "Сообщение" : "Новое сообщение"}
        placeholder="Напишите сообщение…"
        rows={selected ? 2 : 4}
        value={draft}
        onChange={(e) =>
          setDrafts((old) => ({ ...old, [draftKey]: e.target.value }))
        }
        onKeyDown={(e) => {
          if (
            (e.ctrlKey || e.metaKey) &&
            e.key === "Enter" &&
            !e.nativeEvent.isComposing
          ) {
            e.preventDefault();
            send();
          }
        }}
      />
      <div className="compose-options">
        {!selected && (
          <>
            <label className="inline-option">
              Проект
              <select
                aria-label="Проект диалога"
                value={project}
                onChange={(e) => {
                  setProject(e.target.value);
                  setFiles(false);
                }}
              >
                <option value="">Без проекта</option>
                {boot.projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="inline-option">
              Модель
              <select
                aria-label="Модель диалога"
                value={provider || boot.providers[0]?.id || ""}
                onChange={(e) => setProvider(e.target.value)}
              >
                {!boot.providers.length && (
                  <option value="">Не подключена</option>
                )}
                {boot.providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.model.split(":")[0]}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}
        {(!selected || !agent) && (
          <label className="check">
            <input
              type="checkbox"
              checked={memory}
              onChange={(e) => setMemory(e.target.checked)}
            />
            Память диалога
          </label>
        )}
        {!selected && project && (
          <label className="check">
            <input
              type="checkbox"
              checked={files}
              onChange={(e) => setFiles(e.target.checked)}
            />
            Работа с файлами
          </label>
        )}
        {!selected && files && (
          <label className="check">
            <input
              type="checkbox"
              checked={write}
              onChange={(e) => setWrite(e.target.checked)}
            />
            Разрешить изменения
          </label>
        )}
        <span className="compose-spacer" />
        {working ? (
          <button
            className="stop"
            disabled={busy}
            onClick={() =>
              act(async () => {
                const row = await api<Conversation>("/chats/" + selected);
                await api("/chats/" + selected + "/stop", {
                  revision: row.revision,
                });
              })
            }
          >
            ■ Остановить
          </button>
        ) : (
          <button
            className="primary"
            disabled={
              busy ||
              !draft.trim() ||
              !boot.providers.length ||
              (!!current &&
                ["interrupted", "uncertain"].includes(current.status))
            }
            onClick={send}
          >
            Отправить ↗
          </button>
        )}
      </div>
      {!selected && files && !!boot.connectors.length && (
        <div className="attached-apps">
          <span>Приложения:</span>
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
              {c.name}
            </label>
          ))}
        </div>
      )}
    </div>
  );
  const shown = boot.tasks.filter((t) => !project || t.project_id === project);
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="mark">W</span>
          <div>
            Local AI <strong>Workspace</strong>
            <small>Проекты и диалоги</small>
          </div>
        </div>
        <div className="side-section">
          <h2>
            Проекты{" "}
            <button
              aria-label="Добавить проект"
              title="Добавить проект"
              onClick={() => setAddProject(true)}
            >
              ＋
            </button>
          </h2>
          <button
            className={"project " + (!project ? "selected" : "")}
            onClick={() => setProject("")}
          >
            Все диалоги <span>{boot.tasks.length}</span>
          </button>
          {boot.projects.map((p) => (
            <button
              key={p.id}
              className={"project " + (project === p.id ? "selected" : "")}
              onClick={() => {
                setProject(p.id);
                newChat();
              }}
            >
              <span>
                {p.source === "github" ? "↗" : "□"} {p.name}
              </span>
            </button>
          ))}
        </div>
        <div className="side-section task-list">
          <h2>
            Диалоги{" "}
            <button
              aria-label="Новый диалог"
              title="Новый диалог"
              onClick={newChat}
            >
              ＋
            </button>
          </h2>
          {shown.map((t) => (
            <button
              key={t.id}
              className={"task-link " + (selected === t.id ? "selected" : "")}
              data-chat-id={t.id}
              onClick={() => {
                setSelected(t.id);
                setProject(t.project_id || "");
              }}
            >
              <span className={"dot " + t.status} />
              <span>
                {t.title}
                {states[t.status] && <small>{states[t.status]}</small>}
              </span>
            </button>
          ))}
          {!shown.length && (
            <p className="muted empty-side">Здесь появятся ваши диалоги.</p>
          )}
        </div>
        <div className="side-footer">
          <button onClick={() => act(() => api("/host/show-avatar", {}))}>
            ◉ Мини-помощник
          </button>
          <button onClick={() => setSettings(true)}>⚙ Настройки</button>
          <small>Workspace {boot.version}</small>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div>
            <span className="eyebrow">РАБОЧЕЕ ПРОСТРАНСТВО</span>
            <h1>
              {boot.projects.find((p) => p.id === project)?.name || "Диалоги"}
            </h1>
          </div>
          <RuntimeStatus
            provider={
              boot.providers.find(
                (p) => p.id === (current?.provider_id || provider),
              ) || boot.providers[0]
            }
            open={() => setSettings(true)}
          />
        </header>
        {(error || pollError) && (
          <div role="alert" className="alert">
            {error || pollError}
            <button aria-label="Закрыть ошибку" onClick={() => setError("")}>
              ×
            </button>
          </div>
        )}
        {!selected ? (
          <section className="welcome-chat">
            <span className="eyebrow">LOCAL AI</span>
            <h2>Новый диалог</h2>
            <p className="muted">
              Общайтесь с моделью и сохраняйте историю переписки.
            </p>
            {compose}
            {!boot.providers.length && (
              <button className="link" onClick={() => setSettings(true)}>
                Подключить модель
              </button>
            )}
            <div className="welcome-links">
              <button onClick={() => setAddProject(true)}>
                ＋ Создать проект
              </button>
            </div>
          </section>
        ) : (
          <section className="conversation task-view">
            <header className="conversation-header">
              <div>
                <h2>{current?.title || "Открываю диалог…"}</h2>
                <p className="muted">
                  {current ? modelName(current.provider_id) : ""}
                  {agent ? " · Работа с файлами" : ""}
                </p>
              </div>
              {agent && (
                <button aria-expanded={tools} onClick={() => setTools(!tools)}>
                  Действия и файлы
                </button>
              )}
              {current &&
                ["interrupted", "uncertain"].includes(current.status) && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      act(() =>
                        api("/chats/" + selected + "/restore", {
                          revision: current.revision,
                        }),
                      )
                    }
                  >
                    Восстановить историю
                  </button>
                )}
            </header>
            <ChatHistory events={events} current={current} />
            {current && agent && tools && (
              <AgentPanel current={current} events={events} act={act} />
            )}
            {compose}
          </section>
        )}
        {settings && (
          <WorkspaceSettings
            boot={boot}
            close={() => setSettings(false)}
            reload={reload}
          />
        )}
        {addProject && (
          <ProjectDialog
            close={() => setAddProject(false)}
            created={(p) => {
              setProject(p.id);
              newChat();
              setAddProject(false);
              reload();
            }}
          />
        )}
      </main>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  location.pathname === "/mini" ? <MiniApp /> : <App />,
);

import { useEffect, useState } from "react";
import "./mini.css";
import {
  api,
  active,
  states,
  emptyBoot,
  useConversation,
  type Bootstrap,
  type Conversation,
} from "./chat";
import { ChatHistory } from "./ChatHistory";

export function MiniApp() {
  const [boot, setBoot] = useState<Bootstrap>(emptyBoot);
  const [selected, setSelected] = useState(
      sessionStorage.getItem("workspace-mini-task") || "",
    ),
    [project, setProject] = useState(
      sessionStorage.getItem("workspace-mini-project") || "",
    );
  const [menu, setMenu] = useState(true),
    [memory, setMemory] = useState(true),
    [provider, setProvider] = useState(""),
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
  const { current, events, error: pollError } = useConversation(selected);
  const draftKey = selected || "new:" + project,
    draft = drafts[draftKey] || "",
    working = !!current && active.has(current.status);
  useEffect(() => {
    let valid = true;
    const poll = () =>
      api<Bootstrap>("/bootstrap")
        .then((b) => {
          if (valid) setBoot(b);
        })
        .catch((e) => {
          if (valid) setError(e.message);
        });
    poll();
    const timer = setInterval(poll, 1000);
    return () => {
      valid = false;
      clearInterval(timer);
    };
  }, []);
  useEffect(() => {
    sessionStorage.setItem("workspace-mini-task", selected);
  }, [selected]);
  useEffect(() => {
    sessionStorage.setItem("workspace-mini-project", project);
  }, [project]);
  useEffect(() => {
    sessionStorage.setItem("workspace-mini-drafts", JSON.stringify(drafts));
  }, [drafts]);
  useEffect(() => {
    if (current) setMemory(current.details?.memory !== false);
  }, [current?.id]);
  async function main(settings = false) {
    try {
      await api("/host/open-main", {
        task_id: selected,
        project_id: project,
        panel: settings ? "provider" : undefined,
      });
    } catch {
      window.open(
        "/" + (selected ? "?task=" + encodeURIComponent(selected) : ""),
        "_blank",
      );
    }
  }
  async function send() {
    if (!draft.trim() || sending || working) return;
    const raw = draft,
      key = draftKey;
    setSending(true);
    setError("");
    try {
      if (!selected) {
        const p = provider || boot.providers[0]?.id;
        if (!p) throw new Error("Подключите модель в настройках Workspace.");
        const row = await api<Conversation>("/chats", {
          project_id: project || null,
          provider_id: p,
          prompt: raw.trim(),
          mode: "chat",
          memory,
          action_id: crypto.randomUUID(),
        });
        setSelected(row.id);
        setMenu(false);
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
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  }
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
            {current ? states[current.status] || current.title : "Новый диалог"}
          </small>
        </span>
        <button aria-label="Открыть Workspace" onClick={() => main()}>
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
                onChange={(e) => setProject(e.target.value)}
              >
                <option value="">Все диалоги</option>
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
                    data-chat-id={t.id}
                    key={t.id}
                    className={selected === t.id ? "selected" : ""}
                    onClick={() => {
                      setSelected(t.id);
                      setProject(t.project_id || "");
                      setMenu(false);
                      setError("");
                    }}
                  >
                    {t.title}
                    {states[t.status] && <small>{states[t.status]}</small>}
                  </button>
                ))}
            </nav>
          </aside>
        )}
        <section className="mini-conversation">
          {selected ? (
            <ChatHistory events={events} current={current} compact />
          ) : (
            <div className="mini-welcome">
              <strong>Новый диалог</strong>
              <p>Напишите сообщение модели.</p>
              {!boot.providers.length && (
                <button onClick={() => main(true)}>Подключить модель</button>
              )}
            </div>
          )}
          {(error || pollError) && (
            <div role="alert" className="mini-error">
              {error || pollError}
            </div>
          )}
          {current && ["interrupted", "uncertain"].includes(current.status) && (
            <div className="mini-notice">
              Проверьте историю перед продолжением.{" "}
              <button onClick={() => main()}>Открыть Workspace</button>
            </div>
          )}
          {current?.approvals?.length ? (
            <div className="mini-notice">
              Нужно ваше разрешение.{" "}
              <button onClick={() => main()}>Посмотреть</button>
            </div>
          ) : null}
          <div className="mini-compose">
            <textarea
              aria-label="Сообщение мини-помощнику"
              rows={2}
              value={draft}
              placeholder="Напишите сообщение…"
              onChange={(e) =>
                setDrafts((old) => ({ ...old, [draftKey]: e.target.value }))
              }
              onKeyDown={(e) => {
                if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                  e.preventDefault();
                  send();
                }
              }}
            />
            <div className="mini-controls">
              {(!current || current.details?.mode === "chat") && (
                <label className="check">
                  <input
                    type="checkbox"
                    checked={memory}
                    onChange={(e) => setMemory(e.target.checked)}
                  />
                  Память
                </label>
              )}
              {!selected && boot.providers.length > 1 && (
                <select
                  aria-label="Модель мини-помощника"
                  value={provider || boot.providers[0]?.id || ""}
                  onChange={(e) => setProvider(e.target.value)}
                >
                  {boot.providers.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.model.split(":")[0]}
                    </option>
                  ))}
                </select>
              )}
              <span style={{ flex: 1 }} />
              {working ? (
                <button
                  className="stop"
                  onClick={async () => {
                    try {
                      const row = await api<Conversation>("/chats/" + selected);
                      await api("/chats/" + selected + "/stop", {
                        revision: row.revision,
                      });
                    } catch (e) {
                      setError((e as Error).message);
                    }
                  }}
                >
                  ■ Стоп
                </button>
              ) : (
                <button
                  className="primary"
                  disabled={
                    sending ||
                    !draft.trim() ||
                    !boot.providers.length ||
                    (!!current &&
                      ["interrupted", "uncertain"].includes(current.status))
                  }
                  onClick={send}
                >
                  Отправить
                </button>
              )}
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

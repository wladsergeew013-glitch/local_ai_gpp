import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
import { MiniApp } from "./MiniApp";
import { Icon } from "./Icon";
import { MessageFiles } from "./MessageFiles";
import { SkillControls } from "./SkillControls";
import { WorkspaceSettings, type SettingsPage } from "./WorkspaceSettings";
import { WorkspaceSidebar } from "./WorkspaceSidebar";
import { ProjectDialog } from "./ProjectDialog";
import { ChatHistory } from "./ChatHistory";
import { AgentPanel } from "./AgentPanel";
import { RuntimeStatus, type RuntimeObservation } from "./RuntimeStatus";
import { operatorToken } from "./access";
import {
  api,
  active,
  agentTrustNotice,
  emptyBoot,
  submitMessage,
  chatSkillIds,
  disabledSkillSections,
  type SkillUse,
  transferDraft,
  pendingPrompt,
  clearSentDraft,
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
  const [settings, setSettings] = useState<SettingsPage | null>(null),
    [addProject, setAddProject] = useState(false),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [provider, setProvider] = useState(""),
    [files, setFiles] = useState(false),
    [write, setWrite] = useState(false),
    [memory, setMemory] = useState(true),
    [tools, setTools] = useState(false),
    [connectorIds, setConnectorIds] = useState<string[]>([]);
  const [runtimeObservation, setRuntimeObservation] =
    useState<RuntimeObservation>();
  const [fileDrafts, setFileDrafts] = useState<Record<string, File[]>>({});
  const [skillDrafts, setSkillDrafts] = useState<
    Record<
      string,
      {
        ids: string[];
        sections: Record<string, string[]>;
      }
    >
  >({});
  const [drafts, setDrafts] = useState<Record<string, string>>(() => {
    try {
      return JSON.parse(sessionStorage.getItem("workspace-drafts") || "{}");
    } catch {
      return {};
    }
  });
  const { current, events, error: pollError } = useConversation(selected);
  const draftKey = selected || "new:" + project,
    draft =
      drafts[draftKey] ??
      pendingPrompt(current?.id === selected ? current : null);
  const liveChat = current?.id === selected ? current : null;
  const selectedChat =
    liveChat || boot.tasks.find((chat) => chat.id === selected);
  const working = !!selectedChat && active.has(selectedChat.status);
  const agent = selected
    ? (selectedChat?.mode || selectedChat?.details?.mode) === "agent"
    : files;
  const editedSelection = skillDrafts[draftKey];
  const savedIds = selectedChat ? chatSkillIds(selectedChat) : (boot.skills || []).map(s => s.id);
  const selectedIds = editedSelection?.ids ?? savedIds;
  const attachments = fileDrafts[draftKey] ?? [];
  const sections =
    editedSelection?.sections ?? disabledSkillSections(selectedChat);
  function toggleSkill(target: string[]) {
    setError("");
    setSkillDrafts((old) => {
      const ids = old[draftKey]?.ids ?? savedIds;
      const enabled = target.every(id => ids.includes(id));
      const sections = {
        ...(old[draftKey]?.sections ?? disabledSkillSections(selectedChat)),
      };
      if (enabled) {
        for (const id of target) delete sections[id];
      }
      return {
        ...old,
        [draftKey]: {
          ids: enabled ? ids.filter(id => !target.includes(id)) : [...new Set([...ids, ...target])],
          sections,
        },
      };
    });
  }
  function toggleSection(id: string, key: string) {
    setSkillDrafts((old) => {
      const draft = old[draftKey] ?? {
        ids: savedIds,
        sections: disabledSkillSections(selectedChat),
      };
      const hidden = draft.sections[id] || [];
      const next = hidden.includes(key)
        ? hidden.filter((s) => s !== key)
        : [...hidden, key];
      return {
        ...old,
        [draftKey]: { ...draft, sections: { ...draft.sections, [id]: next } },
      };
    });
  }
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
      else if (d.panel === "assistant") setSettings("assistant");
      else if (d.panel) setSettings("runtime");
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
  function newChat(projectId = "") {
    setProject(projectId);
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
      let row: Conversation;
      if (!selected) {
        const p = provider || boot.providers[0]?.id;
        if (!p) throw new Error("Подключите модель в настройках.");
        row = await api<Conversation>("/chats", {
          project_id: project || null,
          provider_id: p,
          prompt: raw.trim(),
          mode: files ? "agent" : "chat",
          memory,
          permission: files && write ? "workspace-write" : "read-only",
          connector_ids: files ? connectorIds : [],
          action_id: crypto.randomUUID(),
        });
        setDrafts((old) => transferDraft(old, key, row.id, raw));
        setFileDrafts(old => {
          if (!old[key]) return old;
          const next = {...old, [row.id]: old[key]};
          delete next[key];
          return next;
        });
        setSkillDrafts((old) => {
          if (!old[key]) return old;
          const next = { ...old, [row.id]: old[key] };
          delete next[key];
          return next;
        });
        setSelected(row.id);
      } else {
        row = await api<Conversation>("/chats/" + selected);
      }
      const uses: SkillUse[] = agent ? [] : selectedIds.map(id => ({id, disabled_sections: sections[id]}));
      await submitMessage(row, raw, memory, uses, agent ? [] : attachments);
      setFileDrafts(old => {
        if (old[row.id] !== attachments) return old;
        const next = {...old};
        delete next[row.id];
        return next;
      });
      setDrafts((old) => clearSentDraft(old, row.id, raw));
    });
  }
  const projectName = boot.projects.find(
    (entry) => entry.id === (selectedChat ? selectedChat.project_id : project),
  )?.name;
  const chatTitle = selectedChat?.title || "Открываю диалог…";
  const compose = (
    <div className="chat-composer">
      <textarea
        aria-label={selected ? "Сообщение" : "Новое сообщение"}
        placeholder="Напишите сообщение…"
        rows={selected ? 2 : 3}
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
      {!selected && (
        <div className="compose-context">
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
          {!selected && project && (
            <label className="check">
              <input
                type="checkbox"
                checked={files}
                onChange={(e) => setFiles(e.target.checked)}
              />
              Файлы и команды
            </label>
          )}
          {!selected && files && (
            <label className="check">
              <input
                type="checkbox"
                checked={write}
                onChange={(e) => setWrite(e.target.checked)}
              />
              Разрешить запись в проект
            </label>
          )}
        </div>
      )}
      <div className="composer-toolbar">
        {!agent && (
          <>
            <MessageFiles files={attachments} disabled={busy || working} error={setError}
              change={batch => setFileDrafts(old => ({...old, [draftKey]: batch}))} />
            <SkillControls
              key={draftKey}
              skills={boot.skills || []}
              selectedIds={selectedIds}
              sections={sections}
              toggleSection={toggleSection}
              disabled={busy || working}
              toggle={toggleSkill}
              library={() => setSettings("skills")}
            />
            <button
              type="button"
              className="tool-button memory-toggle"
              aria-pressed={memory}
              aria-label="Память диалога"
              title={
                memory
                  ? "Память включена · учитывать предыдущие сообщения"
                  : "Память выключена · ответ только на это сообщение"
              }
              onClick={() => setMemory(!memory)}
            >
              <Icon name="memory" size={16} />
              Память
              <span className="switch-track" aria-hidden="true" />
            </button>
          </>
        )}
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
            Остановить
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
            aria-label="Отправить"
            title="Отправить · Ctrl+Enter"
            onClick={send}
          >
            <Icon name="send" size={16} />
            <span>Отправить</span>
          </button>
        )}
      </div>
      {((!selected && files) || agent) && (
        <p className="agent-trust">{agentTrustNotice}</p>
      )}
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
  return (
    <div className="shell">
      <WorkspaceSidebar
        boot={boot}
        selected={selected}
        newChat={newChat}
        openChat={(chat) => {
          setSelected(chat.id);
          setProject(chat.project_id || "");
          setError("");
        }}
        addProject={() => setAddProject(true)}
        showAssistant={() => act(() => api("/host/show-avatar", {}))}
        openSettings={() => setSettings("runtime")}
      />
      <main>
        <header className={selected ? "topbar chat-topbar" : "topbar"}>
          <div className="workspace-heading">
            <span className="eyebrow">РАБОЧЕЕ ПРОСТРАНСТВО</span>
            {selected ? (
              <div className="workspace-heading-line">
                <span className="workspace-project-name" title={projectName}>
                  {projectName || "Без проекта"}
                </span>
                <span className="workspace-heading-divider" aria-hidden="true">
                  /
                </span>
                <h1 title={chatTitle}>{chatTitle}</h1>
              </div>
            ) : (
              <h1>{projectName || "Диалоги"}</h1>
            )}
          </div>
          <div className="workspace-header-actions">
            {liveChat && agent && (
              <button
                className="workspace-header-action"
                aria-expanded={tools}
                onClick={() => setTools(!tools)}
              >
                Действия и файлы
              </button>
            )}
            {liveChat &&
              ["interrupted", "uncertain"].includes(liveChat.status) && (
                <button
                  className="workspace-header-action"
                  disabled={busy}
                  onClick={() =>
                    act(() =>
                      api("/chats/" + liveChat.id + "/restore", {
                        revision: liveChat.revision,
                      }),
                    )
                  }
                >
                  Восстановить историю
                </button>
              )}
            <RuntimeStatus
              observation={runtimeObservation}
              provider={
                boot.providers.find(
                  (p) => p.id === (selectedChat?.provider_id || provider),
                ) || boot.providers[0]
              }
              open={() => setSettings("runtime")}
            />
          </div>
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
            <h2>Новый диалог</h2>
            {compose}
            {!boot.providers.length && (
              <button className="link" onClick={() => setSettings("runtime")}>
                Подключить модель
              </button>
            )}
          </section>
        ) : (
          <section className="conversation task-view">
            {current?.status === "retryable" && (
              <p className="alert" role="status">
                {current.details?.error ||
                  "Запуск не дошёл до сессии. Повторите отправку."}
              </p>
            )}
            <ChatHistory events={liveChat ? events : []} current={liveChat} />
            {current && agent && tools && (
              <AgentPanel current={current} events={events} act={act} />
            )}
            {compose}
          </section>
        )}
        {settings && (
          <WorkspaceSettings
            boot={boot}
            close={() => setSettings(null)}
            initialPage={settings}
            onObservation={setRuntimeObservation}
            providerId={selectedChat?.provider_id || provider}
            selectedSkills={selectedIds}
            sections={sections}
            toggleSection={toggleSection}
            chooseSkill={!agent && !working && !busy ? toggleSkill : undefined}
            reload={reload}
          />
        )}
        {addProject && (
          <ProjectDialog
            close={() => setAddProject(false)}
            created={(p) => {
              newChat(p.id);
              setAddProject(false);
              reload();
            }}
          />
        )}
      </main>
    </div>
  );
}
const root = createRoot(document.getElementById("root")!);
root.render(<p className="landing">Подключение к Workspace…</p>);
operatorToken.then((token) =>
  root.render(
    token ? (
      location.pathname === "/mini" ? (
        <MiniApp />
      ) : (
        <App />
      )
    ) : (
      <main className="landing">
        <h1>Local AI Workspace</h1>
        <p>Откройте Workspace из приложения или по ссылке из терминала.</p>
      </main>
    ),
  ),
);

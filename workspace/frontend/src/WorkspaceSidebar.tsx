import { Icon } from "./Icon";
import { useMemo, useState } from "react";
import { states, type Bootstrap, type Conversation } from "./chat";
import "./sidebar-version.css";

const collapsedProjectsKey = "workspace-collapsed-projects";

type Props = {
  boot: Bootstrap;
  selected: string;
  newChat: (projectId?: string) => void;
  openChat: (chat: Conversation) => void;
  addProject: () => void;
  showAssistant: () => void;
  openSettings: () => void;
};

export function WorkspaceSidebar({
  boot,
  selected,
  newChat,
  openChat,
  addProject,
  showAssistant,
  openSettings,
}: Props) {
  const [collapsed, setCollapsed] = useState<string[]>(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(collapsedProjectsKey) || "[]");
      return Array.isArray(saved) ? saved.filter((id) => typeof id === "string") : [];
    } catch {
      return [];
    }
  });
  const { recent, byProject } = useMemo(() => {
    const recent = [...boot.tasks].sort((a, b) =>
      b.updated_at.localeCompare(a.updated_at),
    );
    const byProject = new Map<string, Conversation[]>();
    for (const chat of recent) {
      if (!chat.project_id) continue;
      const chats = byProject.get(chat.project_id);
      if (chats) chats.push(chat);
      else byProject.set(chat.project_id, [chat]);
    }
    return { recent, byProject };
  }, [boot.tasks]);
  const projectNames = new Map(boot.projects.map((project) => [project.id, project.name]));

  function toggleProject(id: string) {
    const next = collapsed.includes(id)
      ? collapsed.filter((value) => value !== id)
      : [...collapsed, id];
    setCollapsed(next);
    sessionStorage.setItem(collapsedProjectsKey, JSON.stringify(next));
  }

  function chatRow(chat: Conversation, projectName?: string) {
    const details = [projectName, states[chat.status]].filter(Boolean).join(" · ");
    return (
      <button
        type="button"
        className={"task-link " + (selected === chat.id ? "selected" : "")}
        key={chat.id}
        data-chat-id={chat.id}
        title={chat.title}
        aria-current={selected === chat.id ? "page" : undefined}
        onClick={() => openChat(chat)}
      >
        <span className={"dot " + chat.status} aria-hidden="true" />
        <span className="sidebar-chat-text">
          <span className="sidebar-chat-title">{chat.title}</span>
          {details && <small>{details}</small>}
        </span>
      </button>
    );
  }

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="mark" aria-hidden="true">W</span>
        <span>Local AI <strong>Workspace</strong></span>
      </div>
      <button type="button" className="sidebar-new-chat" onClick={() => newChat()}>
        <span aria-hidden="true">＋</span> Новый чат
      </button>
      <div className="sidebar-navigation">
        <section className="side-section" aria-labelledby="sidebar-projects-heading">
          <div className="side-section-heading">
            <h2 id="sidebar-projects-heading">Проекты</h2>
            <button
              type="button"
              className="side-section-action"
              aria-label="Добавить проект"
              title="Добавить проект"
              onClick={addProject}
            >
              ＋
            </button>
          </div>
          {boot.projects.map((project) => {
            const expanded = !collapsed.includes(project.id);
            const chats = byProject.get(project.id) || [];
            const listId = "sidebar-project-" + project.id;
            return (
              <div className="sidebar-project" key={project.id}>
                <div className="sidebar-project-row">
                  <button
                    type="button"
                    className="sidebar-project-toggle"
                    aria-expanded={expanded}
                    aria-controls={listId}
                    title={project.name}
                    onClick={() => toggleProject(project.id)}
                  >
                    <span className="side-chevron" aria-hidden="true" />
                    <span className="sidebar-folder" aria-hidden="true" />
                    <span className="project-name">{project.name}</span>
                  </button>
                  <button
                    type="button"
                    className="side-section-action"
                    aria-label={`Новый чат в проекте «${project.name}»`}
                    title="Новый чат в проекте"
                    onClick={() => {
                      if (!expanded) toggleProject(project.id);
                      newChat(project.id);
                    }}
                  >
                    ＋
                  </button>
                </div>
                <nav
                  id={listId}
                  className="sidebar-project-chats"
                  aria-label={`Чаты проекта «${project.name}»`}
                  hidden={!expanded}
                >
                  {chats.map((chat) => chatRow(chat))}
                  {chats.length === 0 && <p className="empty-side">Пока нет чатов</p>}
                </nav>
              </div>
            );
          })}
        </section>
        <nav className="side-section" aria-labelledby="sidebar-recent-heading">
          <div className="side-section-heading">
            <h2 id="sidebar-recent-heading">Недавние</h2>
          </div>
          {recent.map((chat) => chatRow(chat, projectNames.get(chat.project_id || "")))}
          {recent.length === 0 && <p className="empty-side">Пока нет чатов</p>}
        </nav>
      </div>
      <div className="side-footer">
        <button type="button" onClick={showAssistant}><Icon name="assistant" />Мини-помощник</button>
        <div className="sidebar-settings-line">
          <button type="button" onClick={openSettings}><Icon name="settings" />Настройки</button>
          {boot.version && <span className="sidebar-version" title={`Версия Workspace ${boot.version}`}>v{boot.version}</span>}
        </div>
      </div>
    </aside>
  );
}

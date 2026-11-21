import { useRef, useState } from "react";
import { api, type MarkdownSkill } from "./chat";
import { SkillGroups, skillEntries } from "./SkillGroups";
import { SkillSections } from "./SkillSections";
import { Icon } from "./Icon";

type ImportFile = {
  id: string;
  key: string;
  error?: string;
  name: string;
  markdown: string;
  connects: boolean;
  target?: string;
};

function connectionTarget(block: string) {
  try {
    const { url, tool } = JSON.parse(block);
    const endpoint = new URL(url);
    if (
      endpoint.protocol === "http:" &&
      ["127.0.0.1", "localhost", "[::1]"].includes(endpoint.hostname) &&
      typeof tool === "string"
    ) {
      return endpoint.host + endpoint.pathname + " · " + tool;
    }
  } catch {
    /* Registration validates the complete definition and reports errors. */
  }
}

async function readSource(file: File): Promise<ImportFile> {
  const source = {
    id: crypto.randomUUID(),
    name: file.name,
    markdown: "",
    connects: false,
    key: "",
  };
  try {
    if (!/\.md$/i.test(file.name))
      throw new Error("Выберите файл Markdown (.md).");
    if (!file.size || file.size > 128000)
      throw new Error("Нужно от 1 до 32 000 символов.");
    const markdown = new TextDecoder("utf-8", { ignoreBOM: true }).decode(
      await file.arrayBuffer(),
    );
    if (!markdown.trim() || [...markdown].length > 32000)
      throw new Error("Нужно от 1 до 32 000 символов.");
    const instructions = markdown.replace(/^\uFEFF/, "").replace(/\r\n/g, "\n")
      .replace(/^---\n[\s\S]*?\n---(?:\n|$)/, "");
    const block = /^```workspace-mcp[ \t]*\n([\s\S]*?)\n```[ \t]*$/m.exec(
      instructions,
    );
    return {
      ...source,
      markdown,
      connects: !!block,
      target: block ? connectionTarget(block[1]) : undefined,
    };
  } catch (e) {
    return { ...source, error: (e as Error).message };
  }
}

export function SkillLibrary({
  skills,
  selectedIds,
  choose,
  done,
  reload,
  sections,
  toggleSection,
}: {
  skills: MarkdownSkill[];
  selectedIds: string[];
  sections: Record<string, string[]>;
  toggleSection: (id: string, key: string) => void;
  choose?: (ids: string[]) => void;
  done: () => void;
  reload: () => Promise<unknown>;
}) {
  const entries = skillEntries(skills);
  const picker = useRef<HTMLInputElement>(null);
  const [sources, setSources] = useState<ImportFile[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  async function packageAction(action: () => Promise<string | undefined>) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await action();
      if (result) setMessage(result);
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function packagePath(skill: MarkdownSkill, action: string) {
    return "/skills/packages/" + encodeURIComponent(skill.product || "") + "/" + action;
  }
  function setEnabled(item: MarkdownSkill, group: boolean, enabled: boolean) {
    return packageAction(async () => {
      await api(packagePath(item, "enabled"), { skill: group ? "" : item.name, enabled });
      return undefined;
    });
  }
  async function preview(files: File[]) {
    if (!files.length) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const inputs = await Promise.all(files.map(readSource));
      setSources((old) => [...old, ...inputs]);
    } finally {
      setBusy(false);
    }
  }
  async function install() {
    if (!sources.length || busy || sources.some((s) => s.error)) return;
    setBusy(true);
    setError("");
    try {
      const items = await api<MarkdownSkill[]>(
        "/skills/import",
        sources.map((source) => ({
          filename: source.name,
          markdown: source.markdown,
          api_key: source.connects ? source.key : "",
        })),
      );
      setSources([]);
      setMessage(
        items.length === 1
          ? "Добавлен навык «" + items[0].name + "»."
          : "Добавлено навыков — " + items.length + ".",
      );
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="settings-section-head">
        <div>
          <h2>Навыки</h2>
          <p>
            Для подключения приложения выберите его manifest.json.
          </p>
        </div>
        <button type="button" className="primary setting-action" disabled={busy}
          onClick={() => void packageAction(async () => {
            const result = await api<{ cancelled?: boolean; product?: string; installed?: number; retained?: number }>(
              "/skills/packages/import", {},
            );
            return result.cancelled ? undefined :
              "Пакет «" + result.product + "» сохранён. Навыков — " + (result.installed ?? result.retained) + ".";
          })}>
          <Icon name="plug" size={16} />
          Импортировать пакет навыков
        </button>
        <button
          className="setting-action"
          disabled={busy}
          onClick={() => picker.current?.click()}
        >
          <Icon name="plus" size={16} />
          Добавить навыки
        </button>
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          accept=".md,text/markdown"
          aria-label="Файлы навыков"
          onChange={(e) => {
            const files = Array.from(e.currentTarget.files || []);
            e.currentTarget.value = "";
            void preview(files);
          }}
        />
      </div>
      {sources.length > 0 && (
        <form
          className="settings-card skill-import"
          onSubmit={(e) => {
            e.preventDefault();
            void install();
          }}
        >
          {sources.map((source) => (
            <div className="import-source" key={source.id}>
              <div className="import-heading">
                <Icon name="file" />
                <strong>{source.name}</strong>
                <button
                  type="button"
                  className="icon-action"
                  disabled={busy}
                  aria-label={"Убрать из импорта «" + source.name + "»"}
                  onClick={() => {
                    setSources((old) => old.filter((s) => s.id !== source.id));
                    setError("");
                  }}
                >
                  <Icon name="close" size={16} />
                </button>
              </div>
              {source.error && (
                <p role="alert" className="settings-feedback failure">
                  {source.error}
                </p>
              )}
              {source.target && (
                <p className="import-target">
                  <Icon name="plug" size={15} />
                  <span>{source.target}</span>
                </p>
              )}
              {source.connects && (
                <label className="field">
                  Ключ доступа к приложению{" "}
                  <span className="field-note">Необязательно</span>
                  <input
                    type="password"
                    autoComplete="off"
                    maxLength={512}
                    disabled={busy}
                    value={source.key}
                    aria-label={"Ключ приложения для «" + source.name + "»"}
                    aria-describedby={"help-" + source.id}
                    onChange={(e) => {
                      const key = e.target.value;
                      setSources((old) =>
                        old.map((s) =>
                          s.id === source.id ? { ...s, key } : s,
                        ),
                      );
                    }}
                  />
                  <small id={"help-" + source.id}>
                    Если локальное приложение требует ключ MCP, возьмите его в
                    настройках этого приложения. При свободном доступе оставьте
                    поле пустым.
                  </small>
                </label>
              )}
            </div>
          ))}
          <button
            className="primary setting-action"
            disabled={busy || sources.some((s) => s.error)}
          >
            {busy ? "Добавляю…" : "Добавить в библиотеку"}
          </button>
        </form>
      )}
      {error && (
        <p role="alert" className="settings-feedback failure">
          {error}
        </p>
      )}
      {message && (
        <p role="status" className="settings-feedback success">
          {message}
        </p>
      )}
      <h3 className="group-title">
        Библиотека <span>{entries.length}</span>
      </h3>
      {skills.length ? (
        <SkillGroups className="skill-library" entries={entries} selected={selectedIds} toggle={choose}
          renderPackage={item => (
            <div className="library-row">
              <div className="item-copy">
                <small>Версия пакета {item.package_version || "не указана"}</small>
                <small>{item.endpoint}</small>
                <small>{item.status?.message || ({
                  available: "MCP доступен", connecting: "Проверяется соединение",
                  unavailable: "MCP недоступен", protocol_error: "Ошибка протокола MCP",
                } as Record<string, string>)[item.status?.connection || ""] || "Соединение ещё не проверено"}</small>
                {item.status?.paused && <p role="status">Исход предыдущего вызова неизвестен.</p>}
                <button type="button" className="setting-action" disabled={busy} onClick={() => void packageAction(async () => {
                  await api(packagePath(item, "probe"), {});
                  return undefined;
                })}>Проверить подключение</button>
                {item.status?.paused && <button type="button" className="setting-action" disabled={busy}
                  onClick={() => {
                    if (window.confirm("Предыдущая операция завершилась или приложение перезапущено?")) {
                      void packageAction(async () => {
                        await api(packagePath(item, "resume"), {});
                        return "Очередь приложения возобновлена.";
                      });
                    }
                  }}>Возобновить очередь</button>}
                <details>
                  <summary>Ключ для приложения с авторизацией</summary>
                  <form onSubmit={(event) => {
                    event.preventDefault();
                    const form = event.currentTarget;
                    const key = String(new FormData(form).get("key") || "");
                    void packageAction(async () => {
                      await api(packagePath(item, "credential"), {api_key: key});
                      form.reset();
                      return key ? "Ключ сохранён для текущего адреса." : "Ключ для текущего адреса удалён.";
                    });
                  }}>
                    <label className="field">Ключ MCP
                      <input type="password" maxLength={512} name="key" autoComplete="off" />
                    </label>
                    <button className="setting-action" disabled={busy}>Сохранить ключ</button>
                  </form>
                </details>
              </div>
              <label className="library-choice">
                <input type="checkbox" checked={item.group?.enabled !== false} disabled={busy}
                  onChange={(e) => void setEnabled(item, true, e.target.checked)} />
                Группа включена
              </label>
            </div>
          )} render={item => (
            <div className="library-row">
              <span className="item-icon">
                <Icon name="skills" size={20} />
              </span>
              <div className="item-copy">
                <strong>{item.title || item.name}</strong>
                {item.description && <p>{item.description}</p>}
                <small>
                  {item.accepts_multiple ? "Локальное приложение · Принимает несколько файлов" : item.accepts_attachment
                    ? "Локальное приложение · Принимает файл"
                    : item.has_mcp
                      ? "Локальное приложение"
                      : "Инструкция для модели"}
                </small>
                {item.status?.compatibility?.message && <p role="status">{item.status.compatibility.message}</p>}
                {item.enabled === false && <small>Навык или его группа выключены в профиле.</small>}
                {choose && selectedIds.includes(item.id) && (
                  <SkillSections
                    skill={item}
                    disabled={sections[item.id] || []}
                    toggle={(key) => toggleSection(item.id, key)}
                  />
                )}
              </div>
              {item.product ? (
                <label className="library-choice">
                  <input type="checkbox" checked={item.skill_enabled !== false} disabled={busy}
                    aria-label={"Включить «" + (item.title || item.name) + "»"}
                    onChange={(e) => void setEnabled(item, false, e.target.checked)} />
                  Включён
                </label>
              ) : choose && (
                <label className="library-choice">
                  <input
                    type="checkbox"
                    checked={selectedIds.includes(item.id)}
                    aria-label={"Навык «" + item.name + "» в диалоге"}
                    onChange={() => choose([item.id])}
                  />
                  В диалоге
                </label>
              )}
            </div>
          )} />
      ) : (
        <div className="library-empty">
          <Icon name="skills" size={28} />
          <strong>Пока нет навыков</strong>
          <p>Импортируйте пакет приложения или добавьте отдельный Markdown.</p>
        </div>
      )}
      {choose && skills.length > 0 && (
        <div className="library-apply">
          <span>Выбрано для диалога — {selectedIds.length}</span>
          <button
            type="button"
            className="primary setting-action"
            onClick={done}
          >
            Готово
          </button>
        </div>
      )}
    </>
  );
}

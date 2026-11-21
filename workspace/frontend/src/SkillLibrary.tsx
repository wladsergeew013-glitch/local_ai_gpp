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
    const markdown = (await file.text())
      .replace(/^\uFEFF/, "")
      .replace(/\r\n/g, "\n");
    if (!markdown.trim() || [...markdown].length > 32000)
      throw new Error("Нужно от 1 до 32 000 символов.");
    const instructions = markdown.replace(/^---\n[\s\S]*?\n---(?:\n|$)/, "");
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
            Добавьте один или несколько файлов Markdown и отметьте навыки для
            диалога.
          </p>
        </div>
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
        <SkillGroups className="skill-library" entries={entries} selected={selectedIds} toggle={choose} render={item => (
            <div className="library-row">
              <span className="item-icon">
                <Icon name="skills" size={20} />
              </span>
              <div className="item-copy">
                <strong>{item.name}</strong>
                {item.description && <p>{item.description}</p>}
                <small>
                  {item.accepts_multiple ? "Локальное приложение · Принимает несколько файлов" : item.accepts_attachment
                    ? "Локальное приложение · Принимает файл"
                    : item.has_mcp
                      ? "Локальное приложение"
                      : "Инструкция для модели"}
                </small>
                {choose && selectedIds.includes(item.id) && (
                  <SkillSections
                    skill={item}
                    disabled={sections[item.id] || []}
                    toggle={(key) => toggleSection(item.id, key)}
                  />
                )}
              </div>
              {choose && (
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
          <p>Добавьте файл Markdown (.md) с инструкцией для модели.</p>
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

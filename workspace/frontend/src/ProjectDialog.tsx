import { useState } from "react";
import { SettingsDialog } from "./SettingsDialog";
import { api, type Project } from "./chat";
export function ProjectDialog({
  close,
  created,
}: {
  close: () => void;
  created: (p: Project) => void;
}) {
  const [name, setName] = useState(""),
    [source, setSource] = useState("none"),
    [path, setPath] = useState(""),
    [url, setUrl] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <SettingsDialog onClose={close} error={error}>
      <section className="settings-panel">
        <header>
          <h2>Новый проект</h2>
          <button onClick={close} aria-label="Закрыть проект">
            ×
          </button>
        </header>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError("");
            try {
              const p = await api<Project>("/projects", {
                name,
                path: source === "none" ? "" : path,
                github_url: source === "github" ? url : "",
              });
              created(p);
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <label>
            Название проекта
            <input
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Мой проект"
            />
          </label>
          <p className="muted">
            Проект объединяет диалоги. Папку или GitHub можно подключить, если
            понадобится работа с файлами.
          </p>
          <div
            className="source-options"
            role="group"
            aria-label="Подключение проекта"
          >
            {[
              ["none", "Без подключения"],
              ["folder", "Папка"],
              ["github", "GitHub"],
            ].map(([id, label]) => (
              <button
                type="button"
                key={id}
                className={source === id ? "current" : ""}
                onClick={() => {
                  setSource(id);
                  setPath("");
                }}
              >
                {label}
              </button>
            ))}
          </div>
          {source === "github" && (
            <label>
              Репозиторий GitHub
              <input
                required
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://github.com/owner/repository"
              />
            </label>
          )}
          {source !== "none" && (
            <label>
              {source === "github"
                ? "Локальная папка · необязательно"
                : "Папка проекта"}
              <div className="button-row">
                <input
                  required={source === "folder"}
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                  placeholder={
                    source === "folder"
                      ? "Любая папка, Git не требуется"
                      : "Workspace создаст локальную копию сам"
                  }
                />
                <button
                  type="button"
                  onClick={async () => {
                    try {
                      const r = await api("/host/choose-folder", {});
                      if (r.path) setPath(r.path);
                    } catch {
                      setError(
                        "В браузере укажите путь вручную. Выбор папки доступен в приложении Windows.",
                      );
                    }
                  }}
                >
                  Выбрать…
                </button>
              </div>
            </label>
          )}
          {source === "github" && (
            <p className="muted">
              Репозиторий будет клонирован на этот компьютер. Для приватного
              репозитория нужен настроенный вход в Git.
            </p>
          )}
          <button className="primary" disabled={busy}>
            {busy ? "Подключаю…" : "Создать проект"}
          </button>
        </form>
      </section>
    </SettingsDialog>
  );
}

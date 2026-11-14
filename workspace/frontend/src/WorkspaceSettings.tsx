import { useState } from "react";
import { SettingsDialog } from "./SettingsDialog";
import { api, type Bootstrap } from "./chat";

export function WorkspaceSettings({
  boot,
  close,
  reload,
}: {
  boot: Bootstrap;
  close: () => void;
  reload: () => Promise<unknown>;
}) {
  const [tab, setTab] = useState("connections"),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [existing, setExisting] = useState(boot.providers[0]?.id || "");
  const first = boot.providers[0];
  const [base, setBase] = useState(
      first?.base_url || "http://127.0.0.1:8765/v1",
    ),
    [model, setModel] = useState(first?.model || ""),
    [key, setKey] = useState("");
  const [models, setModels] = useState<any[]>([]),
    [notice, setNotice] = useState("");
  const [appUrl, setAppUrl] = useState("http://127.0.0.1:8890"),
    [appName, setAppName] = useState(""),
    [writes, setWrites] = useState(false);
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
  return (
    <SettingsDialog onClose={close} error={error}>
      <section className="settings-panel">
        <header>
          <h2>Настройки Workspace</h2>
          <button aria-label="Закрыть настройки" onClick={close}>
            ×
          </button>
        </header>
        <nav className="tabs">
          <button
            className={tab === "connections" ? "current" : ""}
            onClick={() => setTab("connections")}
          >
            Подключения и модели
          </button>
          <button
            className={tab === "data" ? "current" : ""}
            onClick={() => setTab("data")}
          >
            Данные
          </button>
        </nav>
        {notice && (
          <p className="connection-result" role="status">
            {notice}
          </p>
        )}
        {tab === "connections" ? (
          <>
            <h3>Модель для диалогов</h3>
            <p>
              <a
                href={base.replace(/\/+$/, "").replace(/\/v1$/, "")}
                target="_blank"
                rel="noreferrer"
              >
                Открыть Runtime: модели, CPU/CUDA и параметры
              </a>
            </p>
            <p className="muted">
              Подключите запущенный Local AI Runtime и выберите модель. Облачный
              ключ DeepSeek не нужен.
            </p>
            {!!boot.providers.length && (
              <label>
                Сохранённое подключение
                <select
                  value={existing}
                  onChange={(e) => {
                    const p = boot.providers.find(
                      (p) => p.id === e.target.value,
                    );
                    setExisting(e.target.value);
                    setBase(p?.base_url || "http://127.0.0.1:8765/v1");
                    setModel(p?.model || "");
                    setKey("");
                    setNotice("");
                  }}
                >
                  <option value="">＋ Новое подключение</option>
                  {boot.providers.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.model || p.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <div className="form-grid">
              <label>
                Адрес Runtime API
                <input value={base} onChange={(e) => setBase(e.target.value)} />
              </label>
              <label>
                Локальный API-ключ
                <input
                  type="password"
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                  autoComplete="off"
                  placeholder={
                    existing
                      ? "Сохранённый ключ остаётся без изменений"
                      : "Если задан в Runtime"
                  }
                />
              </label>
            </div>
            <label>
              Модель
              <input
                aria-label="ID модели"
                list="workspace-models"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="Сначала проверьте связь"
              />
              <datalist id="workspace-models">
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
                    const result = await api(
                      existing
                        ? "/providers/" + existing + "/probe"
                        : "/providers/probe",
                      { base_url: base, model, api_key: key },
                    );
                    setModels(result.models);
                    if (!model && result.models.length)
                      setModel(result.models[0].id);
                    setNotice(
                      "Связь установлена. Выберите модель и сохраните подключение.",
                    );
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
                    const p = await api(
                      existing ? "/providers/" + existing : "/providers",
                      {
                        name: "Local AI Runtime",
                        base_url: base,
                        model,
                        api_key: key,
                      },
                    );
                    setExisting(p.id);
                    setKey("");
                    setNotice("Подключение сохранено.");
                  })
                }
              >
                Сохранить подключение
              </button>
            </div>
            <details className="optional-connections">
              <summary>Доступ к приложениям · необязательно</summary>
              <p className="muted">
                Агент может читать подключённое приложение. Создание записей
                требует отдельного подтверждения.
              </p>
              {boot.connectors.map((c) => (
                <p className="provider-row" key={c.id}>
                  <strong>{c.name}</strong>
                  <code>{c.base_url}</code>
                  <span>
                    {c.allow_write
                      ? "С подтверждением записи"
                      : "Только чтение"}
                  </span>
                </p>
              ))}
              <div className="form-grid">
                <label>
                  Название приложения
                  <input
                    value={appName}
                    onChange={(e) => setAppName(e.target.value)}
                    placeholder="Например, локальные заметки"
                  />
                </label>
                <label>
                  Адрес приложения
                  <input
                    value={appUrl}
                    onChange={(e) => setAppUrl(e.target.value)}
                  />
                </label>
              </div>
              <label className="check">
                <input
                  type="checkbox"
                  checked={writes}
                  onChange={(e) => setWrites(e.target.checked)}
                />
                Разрешать запросы на запись с подтверждением
              </label>
              <button
                disabled={busy || !appName}
                onClick={() =>
                  act(async () => {
                    await api("/connectors", {
                      name: appName,
                      base_url: appUrl,
                      allow_write: writes,
                    });
                    setAppName("");
                    setNotice("Приложение подключено.");
                  })
                }
              >
                Добавить приложение
              </button>
            </details>
          </>
        ) : (
          <>
            <h3>Резервная копия</h3>
            <p>
              Сохраняет диалоги, проекты, настройки и историю действий. Перед
              созданием копии дождитесь ответа модели или остановите его.
            </p>
            <button
              disabled={busy}
              onClick={() =>
                act(async () => {
                  const r = await api("/snapshots", {});
                  setNotice("Резервная копия сохранена: " + r.path);
                })
              }
            >
              Создать резервную копию
            </button>
            <p className="muted">
              Workspace {boot.version}. Копия содержит личные данные и хранится
              на этом компьютере.
            </p>
          </>
        )}
      </section>
    </SettingsDialog>
  );
}

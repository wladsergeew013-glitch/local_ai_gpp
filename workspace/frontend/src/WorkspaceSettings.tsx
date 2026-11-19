import { useState } from "react";
import { SettingsDialog } from "./SettingsDialog";
import { api, type Bootstrap, type RuntimeProbe } from "./chat";

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
  const [probe, setProbe] = useState<RuntimeProbe | null>(null),
    [notice, setNotice] = useState("");
  const [appUrl, setAppUrl] = useState("http://127.0.0.1:8890"),
    [appName, setAppName] = useState(""),
    [writes, setWrites] = useState(false);
  const modelFound = probe?.models.some((entry) => entry.id === model) ?? false;
  const modelLoaded = modelFound && !!probe?.ready?.ready_model_ids?.includes(model);
  const latest = boot.tasks
    .filter((task) => task.provider_id === existing)
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0];
  function resetConnectionCheck() {
    setProbe(null);
    setNotice("");
  }
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
                  disabled={busy}
                  onChange={(e) => {
                    const p = boot.providers.find(
                      (p) => p.id === e.target.value,
                    );
                    setExisting(e.target.value);
                    setBase(p?.base_url || "http://127.0.0.1:8765/v1");
                    setModel(p?.model || "");
                    setKey("");
                    resetConnectionCheck();
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
                <input value={base} disabled={busy} onChange={(e) => {
                  setBase(e.target.value);
                  resetConnectionCheck();
                }} />
              </label>
              <label>
                Локальный API-ключ
                <input
                  type="password"
                  value={key}
                  disabled={busy}
                  onChange={(e) => {
                    setKey(e.target.value);
                    resetConnectionCheck();
                  }}
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
              {probe ? (
                <select aria-label="ID модели" value={model} disabled={busy} onChange={(e) => {
                  setModel(e.target.value);
                  setNotice("");
                }}>
                  {!modelFound && <option value={model} disabled>Выберите доступную модель</option>}
                  {probe.models.map((entry) => <option key={entry.id} value={entry.id}>{entry.id}</option>)}
                </select>
              ) : (
                <input
                  aria-label="ID модели"
                  value={model}
                  disabled={busy}
                  onChange={(e) => { setModel(e.target.value); setNotice(""); }}
                  placeholder="Сначала проверьте связь"
                />
              )}
            </label>
            <div className="button-row">
              <button
                disabled={busy}
                onClick={() =>
                  act(async () => {
                    resetConnectionCheck();
                    const result = await api<RuntimeProbe>(
                      existing
                        ? "/providers/" + existing + "/probe"
                        : "/providers/probe",
                      { base_url: base, model, api_key: key },
                    );
                    setProbe(result);
                    if (!model && result.models.length === 1)
                      setModel(result.models[0].id);
                  })
                }
              >
                Проверить связь
              </button>
              <button
                className="primary"
                disabled={busy || !model.trim() || (!!probe && !modelFound)}
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
            {probe && (
              <p role="status" className={`connection-result ${modelLoaded ? "model-ready" : !modelFound ? "model-missing" : ""}`}>
                {modelLoaded
                  ? "Runtime отвечает. Выбранная модель загружена."
                  : modelFound
                    ? "Runtime отвечает. Модель доступна и загрузится при запросе."
                    : probe.models.length === 0
                      ? "Runtime отвечает. Подключите GGUF в Runtime."
                      : model
                        ? "Runtime отвечает. Этот ID отсутствует. Выберите модель из списка."
                        : "Runtime отвечает. Выберите модель из списка."}
              </p>
            )}
            {(latest?.status === "failed" || latest?.status === "completed") && (
              <p className={latest.status === "failed" ? "last-dialog-result failed" : "last-dialog-result"}>
                {latest.status === "failed" ? "Последняя отправка завершилась ошибкой." : "Последняя отправка получила ответ."}
              </p>
            )}
            <details className="optional-connections">
              <summary>Доступ к приложениям · необязательно</summary>
              <p className="muted">
                Workspace даёт диалогу инструменты выбранных приложений и
                запрашивает подтверждение на каждую запись через подключение.
                Команды файлового агента выполняются с правами вашей учётной
                записи и могут обращаться к локальным сервисам напрямую.
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

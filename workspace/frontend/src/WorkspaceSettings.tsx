import { useEffect, useRef, useState } from "react";
import { SettingsDialog } from "./SettingsDialog";
import { SkillLibrary } from "./SkillLibrary";
import "./preferences.css";
import { probeStatus, type RuntimeObservation } from "./RuntimeStatus";
import { Icon, type IconName } from "./Icon";
import { api, type Bootstrap, type Provider, type RuntimeProbe } from "./chat";

export type SettingsPage = "runtime" | "skills" | "assistant" | "data";
const sections: { id: SettingsPage; icon: IconName; name: string }[] = [
  { id: "runtime", icon: "runtime", name: "Runtime и модели" },
  { id: "skills", icon: "skills", name: "Навыки" },
  { id: "assistant", icon: "assistant", name: "Мини-помощник" },
  { id: "data", icon: "data", name: "Данные" },
];
const defaultBase = "http://127.0.0.1:8765/v1";
const appearances = [
  { id: "pixel", label: "Пиксельный 2D", image: new URL("../../../frontend/public/assistant/frames/ready_0.png", import.meta.url).href },
  { id: "studio", label: "Объёмный 3D", image: new URL("../../assets/assistant-3d/preview.png", import.meta.url).href },
] as const;

function runtimePage(base: string) {
  try {
    const url = new URL(base);
    if (
      ["http:", "https:"].includes(url.protocol) &&
      !url.username &&
      !url.password
    ) {
      return base.replace(/\/+$/, "").replace(/\/v1$/, "");
    }
  } catch {
    /* The address may still be incomplete while typing. */
  }
}

export function WorkspaceSettings({
  boot,
  close,
  reload,
  initialPage,
  providerId,
  selectedSkills,
  sections: disabledSections,
  toggleSection,
  chooseSkill,
  onObservation,
}: {
  boot: Bootstrap;
  close: () => void;
  reload: () => Promise<unknown>;
  initialPage: SettingsPage;
  onObservation: (result: RuntimeObservation) => void;
  providerId?: string;
  selectedSkills: string[];
  sections: Record<string, string[]>;
  toggleSection: (id: string, key: string) => void;
  chooseSkill?: (ids: string[]) => void;
}) {
  const first =
    boot.providers.find((p) => p.id === providerId) || boot.providers[0];
  const [tab, setTab] = useState<SettingsPage>(initialPage);
  const savedAppearance = boot.mini_helper_appearance ?? "studio";
  const [appearance, setAppearance] = useState(savedAppearance);
  useEffect(() => setAppearance(savedAppearance), [savedAppearance]);
  const [existing, setExisting] = useState(first?.id || "");
  const [base, setBase] = useState(first?.base_url || defaultBase);
  const [model, setModel] = useState(first?.model || "");
  const [key, setKey] = useState("");
  const [probe, setProbe] = useState<RuntimeProbe | null>(null);
  const [probeError, setProbeError] = useState("");
  const [checking, setChecking] = useState(false);
  const pendingProbe = useRef(0);
  const [appUrl, setAppUrl] = useState("http://127.0.0.1:8890");
  const [appName, setAppName] = useState("");
  const [writes, setWrites] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const latest = boot.tasks
    .filter((t) => t.provider_id === existing)
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0];
  const saved = boot.providers.find((p) => p.id === existing);
  const modelFound = probe?.models.some((m) => m.id === model);
  const modelLoaded =
    modelFound && probe?.ready?.ready_model_ids?.includes(model);
  const link = runtimePage(base);
  const locked = busy || checking;

  function resetProbe() {
    pendingProbe.current++;
    setProbe(null);
    setProbeError("");
    setChecking(false);
    setNotice("");
    setError("");
  }
  async function check(config: {
    id: string;
    base_url: string;
    model: string;
    api_key?: string;
  }) {
    const request = ++pendingProbe.current;
    function publish(status: string) {
      if (
        config.id &&
        !config.api_key &&
        saved?.base_url === config.base_url &&
        saved?.model === config.model
      ) {
        onObservation({
          id: config.id,
          base_url: config.base_url,
          model: config.model,
          status,
          time: Date.now(),
        });
      }
    }
    setChecking(true);
    setProbeError("");
    setProbe(null);
    try {
      const { id, ...payload } = config;
      const result = await api<RuntimeProbe>(
        id ? "/providers/" + id + "/probe" : "/providers/probe",
        payload,
      );
      if (request !== pendingProbe.current) return;
      setProbe(result);
      publish(probeStatus(result));
      if (!config.model && result.models.length === 1)
        setModel(result.models[0].id);
    } catch (e) {
      if (request === pendingProbe.current) {
        setProbeError((e as Error).message);
        publish("offline");
      }
    } finally {
      if (request === pendingProbe.current) setChecking(false);
    }
  }
  useEffect(() => {
    if (
      tab === "runtime" &&
      existing &&
      saved?.base_url === base &&
      saved?.model === model &&
      !key
    ) {
      void check({ id: existing, base_url: base, model });
    }
    return () => {
      pendingProbe.current++;
    };
  }, [existing, tab]);
  function selectProvider(id: string) {
    resetProbe();
    const p = boot.providers.find((item) => item.id === id);
    setExisting(id);
    setBase(p?.base_url || defaultBase);
    setModel(p?.model || "");
    setKey("");
  }
  async function act(action: () => Promise<string>) {
    setBusy(true);
    setNotice("");
    setError("");
    try {
      const feedback = await action();
      await reload();
      setNotice(feedback);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const status = checking
    ? "Проверяю связь…"
    : probeError
      ? "Ошибка проверки"
      : !probe
        ? "Связь ещё не проверена"
        : modelLoaded
          ? "Модель загружена"
          : modelFound
            ? "Загрузится при запросе"
            : !probe.models.length
              ? "В Runtime нет моделей"
              : model
                ? "Модель отсутствует в Runtime"
                : "Выберите модель";

  return (
    <SettingsDialog onClose={close} layout="preferences-dialog">
      <header className="preferences-header">
        <span className="settings-mark">
          <Icon name="settings" />
        </span>
        <h1>Настройки Workspace</h1>
        <button
          className="icon-action"
          aria-label="Закрыть настройки"
          onClick={close}
        >
          <Icon name="close" />
        </button>
      </header>
      <div className="preferences-layout">
        <nav aria-label="Разделы настроек" className="preferences-nav">
          {sections.map(({ id, icon, name }) => (
            <button
              key={id}
              type="button"
              aria-current={id === tab ? "page" : undefined}
              disabled={busy}
              onClick={() => {
                if (id !== tab) {
                  setTab(id);
                  setNotice("");
                  setError("");
                  setChecking(false);
                }
              }}
            >
              <Icon name={icon} />
              <span>{name}</span>
            </button>
          ))}
        </nav>
        <section
          className="preferences-page"
          aria-label={sections.find((s) => s.id === tab)?.name}
          key={tab}
        >
          {tab === "assistant" ? (
            <>
              <div className="settings-section-head"><h2>Мини-помощник</h2></div>
              <fieldset className="companion-choices" disabled={busy}>
                <legend>Вид персонажа</legend>
                {appearances.map(({ id, label, image }) => (
                  <label key={id}>
                    <img src={image} alt="" />
                    <span><input type="radio" name="companion-style" value={id}
                      checked={id === appearance}
                      onChange={() => {
                        setAppearance(id);
                        void act(async () => {
                          try {
                            await api("/preferences/mini-helper", { style: id });
                            return "Вид персонажа сохранён.";
                          } catch (e) {
                            setAppearance(savedAppearance);
                            throw e;
                          }
                        });
                      }} />{label}</span>
                  </label>
                ))}
              </fieldset>
            </>
          ) : tab === "skills" ? (
            <SkillLibrary
              skills={boot.skills || []}
              selectedIds={selectedSkills}
              sections={disabledSections}
              toggleSection={toggleSection}
              done={close}
              choose={chooseSkill}
              reload={reload}
            />
          ) : tab === "runtime" ? (
            <>
              <div className="settings-section-head">
                <div>
                  <h2>Runtime и модели</h2>
                  <p>Подключение, через которое модель отвечает в диалогах.</p>
                </div>
              </div>
              <div className="settings-card runtime-card">
                <div className="runtime-card-head">
                  <span className="item-icon">
                    <Icon name="runtime" size={21} />
                  </span>
                  <div className="item-copy">
                    <strong>Local AI Runtime</strong>
                    <span
                      role="status"
                      className={
                        "probe-state " +
                        (modelLoaded
                          ? "ready"
                          : probeError || (probe && !modelFound)
                            ? "warning"
                            : "")
                      }
                    >
                      {status}
                    </span>
                  </div>
                  {link && (
                    <a
                      className="setting-action"
                      href={link}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Открыть Runtime <Icon name="external" size={15} />
                    </a>
                  )}
                </div>
                {!!boot.providers.length && (
                  <label className="field">
                    Сохранённое подключение
                    <select
                      value={existing}
                      disabled={busy}
                      onChange={(e) => selectProvider(e.target.value)}
                    >
                      {boot.providers.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.model || p.name}
                        </option>
                      ))}
                      <option value="">Новое подключение</option>
                    </select>
                  </label>
                )}
                <div className="settings-fields">
                  <label className="field">
                    Адрес Runtime API
                    <input
                      value={base}
                      disabled={busy}
                      spellCheck={false}
                      onChange={(e) => {
                        resetProbe();
                        setBase(e.target.value);
                      }}
                    />
                  </label>
                  <label className="field">
                    Модель
                    {probe && probe.models.length > 0 ? (
                      <select
                        aria-label="ID модели"
                        value={modelFound ? model : ""}
                        disabled={busy}
                        onChange={(e) => {
                          setModel(e.target.value);
                          setNotice("");
                        }}
                      >
                        {!modelFound && (
                          <option value="" disabled>
                            Выберите модель
                          </option>
                        )}
                        {probe.models.map((m) => (
                          <option key={m.id} value={m.id}>
                            {m.id}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        aria-label="ID модели"
                        value={model}
                        disabled={busy}
                        placeholder="ID модели"
                        onChange={(e) => {
                          resetProbe();
                          setModel(e.target.value);
                        }}
                      />
                    )}
                  </label>
                </div>
                <details className="preference-details">
                  <summary>
                    Ключ доступа к Runtime
                    {saved?.api_key_configured ? " · задан" : ""}
                  </summary>
                  <label className="field">
                    Ключ API Runtime
                    <input
                      type="password"
                      autoComplete="off"
                      value={key}
                      disabled={busy}
                      onChange={(e) => {
                        resetProbe();
                        setKey(e.target.value);
                      }}
                    />
                    <small>
                      {saved?.api_key_configured
                        ? "Оставьте пустым, чтобы использовать сохранённый ключ."
                        : "Заполните, если в настройках самого Runtime включён доступ по ключу."}
                    </small>
                  </label>
                </details>
                {probeError && (
                  <p className="settings-feedback failure" role="alert">
                    {probeError}
                  </p>
                )}
                {probe && !modelFound && (
                  <p className="field-help">
                    {probe.models.length
                      ? "Выберите модель из списка, который вернул Runtime."
                      : "Откройте Runtime и подключите файл модели GGUF."}
                  </p>
                )}
                <div className="settings-actions">
                  <button
                    className="setting-action"
                    disabled={locked || !link}
                    onClick={() =>
                      void check({
                        id: existing,
                        base_url: base,
                        model,
                        api_key: key,
                      })
                    }
                  >
                    Проверить связь
                  </button>
                  <button
                    className="primary setting-action"
                    disabled={
                      locked ||
                      !link ||
                      !model.trim() ||
                      !!probeError ||
                      (!!probe && !modelFound)
                    }
                    onClick={() =>
                      void act(async () => {
                        const p = await api<Provider>(
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
                        return "Подключение сохранено.";
                      })
                    }
                  >
                    Сохранить подключение
                  </button>
                </div>
              </div>
              {latest && ["failed", "completed"].includes(latest.status) && (
                <p className="last-send">
                  Последняя отправка{" "}
                  {latest.status === "failed"
                    ? "завершилась ошибкой."
                    : "получила ответ."}
                </p>
              )}
              <details className="settings-card connector-settings">
                <summary>
                  <Icon name="plug" />
                  Доступ к приложениям
                  <Icon name="chevron" size={16} />
                </summary>
                <p className="field-help">
                  Workspace запрашивает подтверждение каждой записи через
                  подключение. Файловый агент выполняет команды с правами вашей
                  учётной записи и может обращаться к локальным сервисам
                  напрямую.
                </p>
                {boot.connectors.map((c) => (
                  <div className="installed-connector" key={c.id}>
                    <strong>{c.name}</strong>
                    <small>
                      {c.base_url} ·{" "}
                      {c.allow_write
                        ? "Запись с подтверждением"
                        : "Только чтение"}
                    </small>
                  </div>
                ))}
                <div className="settings-fields">
                  <label className="field">
                    Название приложения
                    <input
                      value={appName}
                      onChange={(e) => setAppName(e.target.value)}
                    />
                  </label>
                  <label className="field">
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
                  Разрешать запись с подтверждением
                </label>
                <button
                  className="setting-action"
                  disabled={busy || !appName.trim()}
                  onClick={() =>
                    void act(async () => {
                      await api("/connectors", {
                        name: appName,
                        base_url: appUrl,
                        allow_write: writes,
                      });
                      setAppName("");
                      return "Приложение подключено.";
                    })
                  }
                >
                  Добавить приложение
                </button>
              </details>
            </>
          ) : (
            <>
              <div className="settings-section-head">
                <div>
                  <h2>Данные</h2>
                  <p>
                    Диалоги, проекты, настройки и история действий Workspace.
                  </p>
                </div>
              </div>
              <div className="settings-card backup-card">
                <span className="item-icon">
                  <Icon name="data" size={21} />
                </span>
                <h3>Резервная копия</h3>
                <p>
                  Копия с вашими данными сохраняется на этом компьютере.
                  Дождитесь окончания текущего ответа или остановите его перед
                  сохранением.
                </p>
                <button
                  className="setting-action"
                  disabled={busy}
                  onClick={() =>
                    void act(async () => {
                      const r = await api("/snapshots", {});
                      return "Резервная копия сохранена в " + r.path;
                    })
                  }
                >
                  Создать резервную копию
                </button>
              </div>
              <p className="last-send">Local AI Workspace {boot.version}</p>
            </>
          )}
          {notice && (
            <p className="settings-feedback success" role="status">
              {notice}
            </p>
          )}
          {error && (
            <p className="settings-feedback failure" role="alert">
              {error}
            </p>
          )}
        </section>
      </div>
    </SettingsDialog>
  );
}

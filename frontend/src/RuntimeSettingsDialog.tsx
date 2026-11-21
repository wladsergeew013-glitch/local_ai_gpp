import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { contextSettingsError } from './contextBudget';
import type { EngineSettings, ModelRecord, RuntimeSettings, RuntimeStatus } from './types';

export type SettingsSection = 'models' | 'generation' | 'compute';
export type ModelOperation =
  | { kind: 'upload'; percent?: number }
  | { kind: 'register' | 'load' | 'unload' };
export type ModelSource = { path: string } | { file: File };
export type Feedback = { text: string; error?: boolean };

const sections = [
  { id: 'models', label: 'Модели' },
  { id: 'generation', label: 'Генерация' },
  { id: 'compute', label: 'Вычисления' },
] as const;

export function modelOperationText(operation: ModelOperation): string {
  switch (operation.kind) {
    case 'upload':
      if (operation.percent === 100) return 'Файл передан. Сохраняем модель на диске…';
      return operation.percent === undefined ? 'Передаём GGUF в Runtime…' : `Передаём GGUF в Runtime · ${operation.percent}%`;
    case 'register': return 'Подключаем файл модели…';
    case 'load': return 'Загружаем веса и прогреваем модель…';
    case 'unload': return 'Освобождаем память модели…';
  }
}

export function modelStateText(runtime?: RuntimeStatus): string {
  if (!runtime) return 'Загрузка при первом сообщении';
  if (runtime.state === 'loading') return 'Загружается';
  const layers = runtime.gpu_offloaded_layers;
  const placement = [runtime.runtime_mode, layers && runtime.model_layers ? `${layers}/${runtime.model_layers} слоёв на GPU` : '']
    .filter(Boolean).join(' · ');
  return [runtime.state === 'generating' ? 'Генерация' : 'Загружена', placement].filter(Boolean).join(' · ');
}

type Props = {
  section: SettingsSection;
  onSection: (section: SettingsSection) => void;
  onClose: () => void;
  settings: EngineSettings | null;
  systemPrompt: string;
  gpuLayers: number;
  saving: boolean;
  settingsFeedback: Feedback | null;
  onSave: (patch: Partial<RuntimeSettings>, prompt: string) => Promise<boolean>;
  models: ModelRecord[];
  model?: ModelRecord;
  runtime?: RuntimeStatus;
  modelControlsBusy: boolean;
  operation: ModelOperation | null;
  modelFeedback: Feedback | null;
  uploadLimit: number;
  onSelectModel: (id: string) => void;
  onLoadModel: () => void;
  onConnectModel: (name: string, source: ModelSource) => Promise<boolean>;
  onCancelUpload: () => void;
  apiBase: string;
  apiDocs: string;
};

function nativePicker() {
  return (window as Window & {
    pywebview?: { api?: { choose_gguf?: () => Promise<string | null> } };
  }).pywebview?.api?.choose_gguf;
}

function cleanPath(path: string) {
  return path.trim().replace(/^"(.*)"$/, '$1');
}

function modelFileName(source: ModelSource | null) {
  return source && ('file' in source ? source.file.name : source.path.split(/[\\/]/).pop()) || '';
}

function ModelSettings(props: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [path, setPath] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState('');
  const [showPath, setShowPath] = useState(false);
  const [pickerAvailable, setPickerAvailable] = useState(() => !!nativePicker());
  const [picking, setPicking] = useState(false);
  const [fileError, setFileError] = useState('');
  const pathValue = cleanPath(path);
  const source: ModelSource | null = file ? { file } : pathValue ? { path: pathValue } : null;
  const filename = modelFileName(source);
  const ready = props.runtime?.state === 'hot';
  const busy = picking || !!props.operation;

  useEffect(() => {
    const ready = () => setPickerAvailable(!!nativePicker());
    window.addEventListener('pywebviewready', ready);
    ready();
    return () => window.removeEventListener('pywebviewready', ready);
  }, []);

  async function chooseFile() {
    setFileError('');
    const choose = nativePicker();
    if (!choose) {
      input.current?.click();
      return;
    }
    setPicking(true);
    try {
      const selection = await choose();
      if (selection) {
        setPath(selection);
        setFile(null);
      }
    } catch {
      setShowPath(true);
      setFileError('Не удалось открыть проводник. Укажите путь к файлу.');
    } finally {
      setPicking(false);
    }
  }

  async function connect(event: FormEvent) {
    event.preventDefault();
    if (!source || busy || !props.settings) return;
    if (!/\.gguf$/i.test(filename)) {
      setFileError('Выберите файл с расширением .gguf.');
      return;
    }
    setFileError('');
    if (await props.onConnectModel(name.trim() || filename.replace(/\.gguf$/i, ''), source)) {
      setFile(null);
      setPath('');
      setName('');
    }
  }

  return <>
    {props.models.length > 0 && <section className="runtime-setting-card">
      <label htmlFor="runtime-model-library">Подключённые модели</label>
      <div className="runtime-model-selection">
        <select id="runtime-model-library" value={props.model?.id || ''} disabled={props.modelControlsBusy} onChange={e => props.onSelectModel(e.target.value)}>
          {!props.model && <option value="" disabled>Выберите доступную модель</option>}
          {props.models.map(model => <option key={model.id} value={model.id} disabled={model.file_exists === false}>
            {model.name}{model.file_exists === false ? ' · файл недоступен' : ''}
          </option>)}
        </select>
        <button type="button" disabled={!props.model || props.modelControlsBusy} onClick={props.onLoadModel}>
          {ready ? 'Выгрузить' : 'Загрузить в память'}
        </button>
      </div>
      {props.model && <p className="runtime-hint">{modelStateText(props.runtime)}</p>}
    </section>}
    <form className="runtime-setting-card runtime-import-card" onSubmit={connect}>
      <h2>Подключить GGUF</h2>
      <p className="runtime-hint">{pickerAvailable
        ? 'Runtime использует GGUF на месте. Чтобы вставить готовый путь, нажмите «Указать путь».'
        : 'Выберите GGUF для копирования в хранилище Runtime или укажите путь на сервере.'}</p>
      <div className="runtime-file-row">
        <input type="file" accept=".gguf" ref={input} hidden onChange={e => {
          const selected = e.currentTarget.files?.[0];
          if (selected) { setFile(selected); setPath(''); setFileError(''); }
          e.currentTarget.value = '';
        }} />
        <button type="button" className={source ? undefined : 'primary'} disabled={busy} onClick={() => void chooseFile()}>
          {picking ? 'Открываем проводник…' : 'Выбрать файл'}
        </button>
        <button type="button" className="quiet-button" aria-expanded={showPath} disabled={busy} onClick={() => setShowPath(!showPath)}>
          {showPath ? 'Скрыть путь' : 'Указать путь'}
        </button>
      </div>
      {showPath && <label className="runtime-field">Путь к GGUF на компьютере Runtime
        <input value={path} disabled={busy} onChange={e => {
          setPath(e.target.value); setFile(null); setFileError('');
        }} />
      </label>}
      {source && <div className="runtime-file-detail">
        <strong title={'path' in source ? source.path : filename}>{filename}</strong>
        <span>{'file' in source ? `${(source.file.size / 1024 ** 3).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} ГиБ · копирование` : 'Файл используется на месте'}</span>
      </div>}
      {source && <label className="runtime-field">Имя модели
        <input value={name} placeholder={filename.replace(/\.gguf$/i, '')} disabled={busy} onChange={e => setName(e.target.value)} />
      </label>}
      {file && file.size > props.uploadLimit && <p className="runtime-error" role="alert">Файл превышает лимит {props.uploadLimit / 1024 ** 3} ГиБ. Укажите путь на компьютере Runtime.</p>}
      {fileError && <p className="runtime-error" role="alert">{fileError}</p>}
      <div className="runtime-form-actions">
        <button type="submit" className="primary" disabled={!source || busy || !props.settings || !!(file && file.size > props.uploadLimit)}>
          Подключить модель
        </button>
      </div>
    </form>
    {props.model && <details className="runtime-api-details">
      <summary>Подключение по API</summary>
      <dl><dt>Адрес</dt><dd><code>{props.apiBase}</code></dd><dt>ID модели</dt><dd><code>{props.model.id}</code></dd></dl>
      <a href={props.apiDocs} target="_blank" rel="noreferrer">Документация API</a>
    </details>}
  </>;
}

export default function RuntimeSettingsDialog(props: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [changes, setChanges] = useState<Partial<RuntimeSettings>>({});
  const [prompt, setPrompt] = useState(props.systemPrompt);
  const draft = props.settings ? { ...props.settings.runtime, n_gpu_layers: props.gpuLayers, ...changes } : null;
  const budgetError = draft ? contextSettingsError(draft.n_ctx, draft.max_tokens) : '';
  const dirty = Object.keys(changes).length > 0 || prompt !== props.systemPrompt;

  useEffect(() => {
    const element = dialog.current!;
    element.showModal();
    return () => element.close();
  }, []);

  function update<K extends keyof RuntimeSettings>(key: K, value: RuntimeSettings[K]) {
    setChanges(previous => ({ ...previous, [key]: value }));
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (props.saving || budgetError) return;
    if (await props.onSave(changes, prompt)) setChanges({});
  }

  return <dialog ref={dialog} className="runtime-settings-dialog" aria-labelledby="runtime-settings-title" onCancel={e => {
    e.preventDefault(); props.onClose();
  }}>
    <header className="runtime-settings-header">
      <h1 id="runtime-settings-title">Настройки Runtime</h1>
      <button className="icon-button" type="button" aria-label="Закрыть настройки" onClick={props.onClose}>×</button>
    </header>
    <div className="runtime-settings-layout">
      <nav className="runtime-settings-nav" aria-label="Разделы настроек">
        {sections.map(section => <button key={section.id} type="button" aria-current={props.section === section.id ? 'page' : undefined} onClick={() => props.onSection(section.id)}>
          {section.label}
        </button>)}
      </nav>
      <div className="runtime-settings-content">
        <div hidden={props.section !== 'models'}><ModelSettings {...props} /></div>
        {props.operation && <div className="runtime-operation" role="status">
          <span>{modelOperationText(props.operation)}</span>
          {props.operation.kind === 'upload' && <>
            {props.operation.percent !== 100 && <progress aria-label="Передача файла" max={100} value={props.operation.percent} />}
            <button type="button" onClick={props.onCancelUpload}>Отменить загрузку файла</button>
          </>}
        </div>}
        {props.section === 'models' && props.modelFeedback && <p className={props.modelFeedback.error ? 'runtime-error' : 'runtime-success'} role={props.modelFeedback.error ? 'alert' : 'status'}>
          {props.modelFeedback.text}
        </p>}
        {props.section !== 'models' && (draft ? <form className="runtime-preferences" onSubmit={save}>
          <fieldset disabled={props.saving}>
            {props.section === 'generation' ? <>
              <div className="runtime-setting-card runtime-grid-fields">
                <label className="runtime-field">Контекст, токенов
                  <input type="number" min={512} max={131072} list="runtime-context-presets" value={draft.n_ctx} onChange={e => update('n_ctx', Number(e.target.value))} />
                  <datalist id="runtime-context-presets"><option value="2048" /><option value="4096" /><option value="8192" /><option value="16384" /></datalist>
                </label>
                <label className="runtime-field">Лимит ответа, токенов
                  <input type="number" min={1} max={Math.min(32768, draft.n_ctx - 257)} value={draft.max_tokens} onChange={e => update('max_tokens', Number(e.target.value))} />
                </label>
                <p className="runtime-hint runtime-grid-wide">История, инструкция и ответ делят контекст. Больший контекст требует больше памяти.</p>
                <label className="runtime-check runtime-grid-wide"><input type="checkbox" checked={draft.context_overflow !== 'error'} onChange={e => update('context_overflow', e.target.checked ? 'trim' : 'error')} />
                  Сокращать старые сообщения в запросе
                </label>
              </div>
              <div className="runtime-setting-card runtime-grid-fields">
                <label className="runtime-field">Температура
                  <input type="number" min={0} max={2} step="any" value={draft.temperature} onChange={e => update('temperature', Number(e.target.value))} />
                </label>
                <label className="runtime-check"><input type="checkbox" checked={!!draft.enable_thinking} onChange={e => update('enable_thinking', e.target.checked)} />Рассуждения Qwen</label>
                <p className="runtime-hint runtime-grid-wide">Переключатель применяется к моделям Qwen, чей шаблон поддерживает этот режим.</p>
                <label className="runtime-field runtime-grid-wide">Инструкция для диалога
                  <textarea rows={2} value={prompt} onChange={e => setPrompt(e.target.value)} />
                  <span className="runtime-hint">Действует до закрытия текущей вкладки или окна.</span>
                </label>
              </div>
            </> : <div className="runtime-setting-card">
              <label className="runtime-field">Устройство
                <select value={draft.n_gpu_layers === -1 ? 'gpu' : draft.n_gpu_layers === 0 ? 'cpu' : 'mixed'} onChange={e => {
                  update('n_gpu_layers', e.target.value === 'gpu' ? -1 : e.target.value === 'cpu' ? 0 : 16);
                }}>
                  <option value="cpu">CPU</option><option value="mixed">CPU + CUDA</option><option value="gpu">GPU · CUDA</option>
                </select>
              </label>
              {draft.n_gpu_layers > 0 && <label className="runtime-field">Слоёв на GPU
                <input type="number" min={1} max={100000} value={draft.n_gpu_layers} onChange={e => update('n_gpu_layers', Math.max(1, Number(e.target.value)))} />
              </label>}
              <p className="runtime-hint">GPU · CUDA выгружает все поддерживаемые слои. CPU + CUDA позволяет ограничить расход видеопамяти.</p>
              <label className="runtime-field">Модель в памяти
                <select value={draft.warm_policy} onChange={e => update('warm_policy', e.target.value as RuntimeSettings['warm_policy'])}>
                  <option value="unload_after_idle">Выгружать после простоя</option>
                  <option value="keep_hot">Держать загруженной</option>
                  <option value="manual">Выгружать вручную</option>
                </select>
              </label>
              {props.model && <p className="runtime-hint">Текущее состояние · {modelStateText(props.runtime)}</p>}
            </div>}
          </fieldset>
          {budgetError && <p className="runtime-error" role="alert">{budgetError}</p>}
          {props.settingsFeedback && <p className={props.settingsFeedback.error ? 'runtime-error' : 'runtime-success'} role={props.settingsFeedback.error ? 'alert' : 'status'}>
            {props.settingsFeedback.text}
          </p>}
          <div className="runtime-form-actions"><button type="submit" className="primary" disabled={!dirty || !!budgetError || props.saving}>
            {props.saving ? 'Сохраняем…' : 'Сохранить настройки'}
          </button></div>
        </form> : <p role="status">Получаем настройки Runtime…</p>)}
      </div>
    </div>
  </dialog>;
}

import React from 'react';
import type { ModelRecord, RemoteHubModel, RuntimeSettings } from '../types';

type Props = {
  models: ModelRecord[];
  hubModels: RemoteHubModel[];
  statusBusy: boolean;
  uploadName: string;
  uploadType: string;
  uploadFile: File | null;
  uploadCopyToStorage: boolean;
  pathName: string;
  pathType: string;
  pathValue: string;
  runtimeDraft: RuntimeSettings;
  hubDraftName: string;
  selectedHubModelId: string;
  onUploadNameChange: (v: string) => void;
  onUploadTypeChange: (v: string) => void;
  onUploadFileChange: (file: File | null) => void;
  onUploadCopyChange: (v: boolean) => void;
  onPathNameChange: (v: string) => void;
  onPathTypeChange: (v: string) => void;
  onPathValueChange: (v: string) => void;
  onRuntimeChange: (key: keyof RuntimeSettings, value: string | number | boolean) => void;
  onHubDraftNameChange: (v: string) => void;
  onSelectedHubModelIdChange: (v: string) => void;
  onSubmitUpload: (e: React.FormEvent) => void;
  onSubmitPath: (e: React.FormEvent) => void;
  onReloadHubModels: () => void;
  onImportHubModel: () => void;
  onStartModel: (id: string) => void;
  onDeleteModel: (id: string) => void;
  onOpenChat: (id: string) => void;
};

const MODEL_TYPES = ['LLM', 'Embedding', 'ClassicalML', 'Other'];

export default function ModelsTab(props: Props) {
  return (
    <div className="tab-grid">
      <section className="card">
        <div className="card-head">
          <div>
            <h2>1. Выбрать файл</h2>
            <p className="muted">По умолчанию движок копирует модель к себе. Для режима без копирования используй второй способ и укажи путь вручную.</p>
          </div>
          <span className="pill pill-accent">Файловый импорт</span>
        </div>

        <form className="stack" onSubmit={props.onSubmitUpload}>
          <label>Название модели</label>
          <input value={props.uploadName} onChange={(e) => props.onUploadNameChange(e.target.value)} />

          <label>Тип модели</label>
          <select value={props.uploadType} onChange={(e) => props.onUploadTypeChange(e.target.value)}>
            {MODEL_TYPES.map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>

          <label>Файл модели</label>
          <input type="file" onChange={(e) => props.onUploadFileChange(e.target.files?.[0] || null)} />

          <label className="check-row">
            <input type="checkbox" checked={props.uploadCopyToStorage} onChange={(e) => props.onUploadCopyChange(e.target.checked)} />
            <span>Копировать модель в storage движка</span>
          </label>

          <button type="submit">Загрузить и зарегистрировать</button>
        </form>
      </section>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>2. Зарегистрировать путь</h2>
            <p className="muted">Ничего не копирует. Просто добавляет уже существующий путь к модели на сервере.</p>
          </div>
          <span className="pill pill-muted">Server path</span>
        </div>

        <form className="stack" onSubmit={props.onSubmitPath}>
          <label>Название модели</label>
          <input value={props.pathName} onChange={(e) => props.onPathNameChange(e.target.value)} />

          <label>Тип модели</label>
          <select value={props.pathType} onChange={(e) => props.onPathTypeChange(e.target.value)}>
            {MODEL_TYPES.map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>

          <label>Абсолютный путь к файлу</label>
          <input value={props.pathValue} onChange={(e) => props.onPathValueChange(e.target.value)} />

          <div className="runtime-grid">
            <label><span>n_ctx</span><input type="number" value={props.runtimeDraft.n_ctx} onChange={(e) => props.onRuntimeChange('n_ctx', Number(e.target.value))} /></label>
            <label><span>n_batch</span><input type="number" value={props.runtimeDraft.n_batch} onChange={(e) => props.onRuntimeChange('n_batch', Number(e.target.value))} /></label>
            <label><span>n_threads</span><input type="number" value={props.runtimeDraft.n_threads} onChange={(e) => props.onRuntimeChange('n_threads', Number(e.target.value))} /></label>
            <label><span>GPU layers</span><input type="number" value={props.runtimeDraft.n_gpu_layers} onChange={(e) => props.onRuntimeChange('n_gpu_layers', Number(e.target.value))} /></label>
          </div>

          <button type="submit">Добавить путь</button>
        </form>
      </section>

      <section className="card wide">
        <div className="card-head">
          <div>
            <h2>3. Импорт из хаба</h2>
            <p className="muted">Список идёт через backend API коннектор. Контракт можно будет донастроить под ваш будущий хаб.</p>
          </div>
          <div className="row-actions">
            <button type="button" className="secondary-button" onClick={props.onReloadHubModels}>Обновить список</button>
          </div>
        </div>

        <div className="hub-grid">
          <div className="stack">
            <label>Название локальной модели</label>
            <input value={props.hubDraftName} onChange={(e) => props.onHubDraftNameChange(e.target.value)} />

            <label>Модель из хаба</label>
            <select value={props.selectedHubModelId} onChange={(e) => props.onSelectedHubModelIdChange(e.target.value)}>
              <option value="">Выбери remote-модель</option>
              {props.hubModels.map((item, idx) => {
                const value = String(item.id || item.name || `remote-${idx}`);
                const title = String(item.name || item.id || `remote-${idx}`);
                const extra = item.size ? ` · ${item.size}` : '';
                return <option key={value} value={value}>{title}{extra}</option>;
              })}
            </select>

            <button type="button" onClick={props.onImportHubModel}>Импортировать в движок</button>
          </div>

          <div className="hub-list">
            {props.hubModels.length === 0 ? (
              <div className="empty-state">Список хаба пока пустой.</div>
            ) : (
              props.hubModels.map((item, idx) => {
                const value = String(item.id || item.name || `remote-${idx}`);
                const title = String(item.name || item.id || `remote-${idx}`);
                return (
                  <button key={value} type="button" className={`hub-item ${props.selectedHubModelId === value ? 'selected' : ''}`} onClick={() => props.onSelectedHubModelIdChange(value)}>
                    <div className="hub-item-title">{title}</div>
                    <div className="hub-item-sub">{String(item.description || item.type || item.filename || '')}</div>
                  </button>
                );
              })
            )}
          </div>
        </div>
      </section>

      <section className="card wide">
        <div className="card-head">
          <div>
            <h2>Зарегистрированные модели</h2>
            <p className="muted">Это уже модели движка. Отсюда их можно запускать, отправлять в чат или удалять.</p>
          </div>
          <span className={`pill ${props.statusBusy ? 'pill-warning' : 'pill-success'}`}>{props.models.length} шт.</span>
        </div>

        {props.models.length === 0 ? (
          <div className="empty-state">Модели пока не загружены.</div>
        ) : (
          <div className="model-list">
            {props.models.map((model) => (
              <div className="model-row" key={model.id}>
                <div className="model-main">
                  <div className="model-title">{model.name}</div>
                  <div className="model-meta">
                    <span>{model.filename}</span>
                    <span>{model.type}</span>
                    <span>status: {model.status || 'saved'}</span>
                    <span>source: {model.source || 'local'}</span>
                  </div>
                  <div className="model-id">{model.id}</div>
                </div>

                <div className="model-actions">
                  {model.type === 'LLM' && (
                    <>
                      <button type="button" onClick={() => props.onOpenChat(model.id)}>В чат</button>
                      <button type="button" onClick={() => props.onStartModel(model.id)}>Запустить</button>
                    </>
                  )}
                  <button type="button" className="danger" onClick={() => props.onDeleteModel(model.id)}>Удалить</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

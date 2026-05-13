import React from 'react';
import type { EngineSettings } from '../types';

type Props = {
  draft: EngineSettings;
  onUpdateDraft: (path: string, value: string | number | boolean) => void;
  onLogoFileChange: (file: File | null) => void;
  onUploadLogo: () => void;
  onSave: (e: React.FormEvent) => void;
  onClose: () => void;
};

export default function SettingsTab(props: Props) {
  const theme = props.draft.theme;
  const branding = props.draft.branding;
  const layout = props.draft.layout;
  const hub = props.draft.hub;
  const runtime = props.draft.runtime;

  return (
    <aside className="settings-drawer">
      <div className="settings-drawer-head">
        <div>
          <h2>Настройки движка</h2>
          <p className="muted">Цвета, лого, хаб и runtime по умолчанию.</p>
        </div>
        <button type="button" className="secondary-button" onClick={props.onClose}>
          Закрыть
        </button>
      </div>

      <form className="settings-form" onSubmit={props.onSave}>
        <section className="card wide">
          <h3>Внешний вид</h3>
          <div className="settings-grid">
            <label><span>Заголовок</span><input value={branding.title} onChange={(e) => props.onUpdateDraft('branding.title', e.target.value)} /></label>
            <label><span>Подзаголовок</span><input value={branding.subtitle} onChange={(e) => props.onUpdateDraft('branding.subtitle', e.target.value)} /></label>
            <label><span>Accent</span><input type="color" value={theme.accent} onChange={(e) => props.onUpdateDraft('theme.accent', e.target.value)} /></label>
            <label><span>Hero text</span><input type="color" value={theme.hero_text} onChange={(e) => props.onUpdateDraft('theme.hero_text', e.target.value)} /></label>
            <label><span>Background start</span><input type="color" value={theme.background_start} onChange={(e) => props.onUpdateDraft('theme.background_start', e.target.value)} /></label>
            <label><span>Background end</span><input type="color" value={theme.background_end} onChange={(e) => props.onUpdateDraft('theme.background_end', e.target.value)} /></label>
            <label><span>Panel</span><input type="color" value={theme.panel} onChange={(e) => props.onUpdateDraft('theme.panel', e.target.value)} /></label>
            <label><span>Panel alt</span><input type="color" value={theme.panel_alt} onChange={(e) => props.onUpdateDraft('theme.panel_alt', e.target.value)} /></label>
            <label><span>Border</span><input type="color" value={theme.border} onChange={(e) => props.onUpdateDraft('theme.border', e.target.value)} /></label>
            <label><span>Text</span><input type="color" value={theme.text} onChange={(e) => props.onUpdateDraft('theme.text', e.target.value)} /></label>
            <label><span>Muted</span><input type="color" value={theme.muted} onChange={(e) => props.onUpdateDraft('theme.muted', e.target.value)} /></label>
            <label><span>Success</span><input type="color" value={theme.success} onChange={(e) => props.onUpdateDraft('theme.success', e.target.value)} /></label>
            <label><span>Warning</span><input type="color" value={theme.warning} onChange={(e) => props.onUpdateDraft('theme.warning', e.target.value)} /></label>
            <label><span>Danger</span><input type="color" value={theme.danger} onChange={(e) => props.onUpdateDraft('theme.danger', e.target.value)} /></label>
          </div>
        </section>

        <section className="card wide">
          <h3>Лого и контейнер</h3>
          <div className="settings-grid">
            <label><span>Ширина контейнера</span><input type="number" value={branding.logo_width} onChange={(e) => props.onUpdateDraft('branding.logo_width', Number(e.target.value))} /></label>
            <label><span>Высота контейнера</span><input type="number" value={branding.logo_height} onChange={(e) => props.onUpdateDraft('branding.logo_height', Number(e.target.value))} /></label>
            <label><span>Радиус контейнера</span><input type="number" value={branding.logo_radius} onChange={(e) => props.onUpdateDraft('branding.logo_radius', Number(e.target.value))} /></label>
            <label><span>Внутренний padding</span><input type="number" value={branding.logo_padding} onChange={(e) => props.onUpdateDraft('branding.logo_padding', Number(e.target.value))} /></label>
            <label>
              <span>Режим отображения</span>
              <select value={branding.logo_fit} onChange={(e) => props.onUpdateDraft('branding.logo_fit', e.target.value)}>
                <option value="contain">contain</option>
                <option value="cover">cover</option>
                <option value="fill">fill</option>
              </select>
            </label>
            <label><span>Ширина приложения</span><input type="number" value={layout.app_max_width} onChange={(e) => props.onUpdateDraft('layout.app_max_width', Number(e.target.value))} /></label>
            <label><span>Радиус карточек</span><input type="number" value={layout.card_radius} onChange={(e) => props.onUpdateDraft('layout.card_radius', Number(e.target.value))} /></label>
            <label className="check-row"><input type="checkbox" checked={layout.hero_compact} onChange={(e) => props.onUpdateDraft('layout.hero_compact', e.target.checked)} /><span>Компактный hero</span></label>
          </div>
          <div className="row-actions">
            <input type="file" accept=".svg,.png,.jpg,.jpeg,.webp" onChange={(e) => props.onLogoFileChange(e.target.files?.[0] || null)} />
            <button type="button" onClick={props.onUploadLogo}>Загрузить логотип</button>
          </div>
          <p className="muted">
            contain — вписать целиком, cover — заполнить контейнер с возможной обрезкой, fill — растянуть на весь контейнер.
          </p>
        </section>

        <section className="card wide">
          <h3>Runtime по умолчанию</h3>
          <div className="settings-grid">
            <label><span>n_ctx</span><input type="number" value={runtime.n_ctx} onChange={(e) => props.onUpdateDraft('runtime.n_ctx', Number(e.target.value))} /></label>
            <label><span>n_batch</span><input type="number" value={runtime.n_batch} onChange={(e) => props.onUpdateDraft('runtime.n_batch', Number(e.target.value))} /></label>
            <label><span>n_threads</span><input type="number" value={runtime.n_threads} onChange={(e) => props.onUpdateDraft('runtime.n_threads', Number(e.target.value))} /></label>
            <label><span>n_gpu_layers</span><input type="number" value={runtime.n_gpu_layers} onChange={(e) => props.onUpdateDraft('runtime.n_gpu_layers', Number(e.target.value))} /></label>
            <label><span>Main GPU</span><input type="number" value={runtime.main_gpu} onChange={(e) => props.onUpdateDraft('runtime.main_gpu', Number(e.target.value))} /></label>
            <label><span>Temperature</span><input type="number" step="0.1" value={runtime.temperature} onChange={(e) => props.onUpdateDraft('runtime.temperature', Number(e.target.value))} /></label>
            <label><span>Max tokens</span><input type="number" value={runtime.max_tokens} onChange={(e) => props.onUpdateDraft('runtime.max_tokens', Number(e.target.value))} /></label>
            <label className="check-row"><input type="checkbox" checked={runtime.offload_kqv} onChange={(e) => props.onUpdateDraft('runtime.offload_kqv', e.target.checked)} /><span>offload_kqv</span></label>
            <label className="check-row"><input type="checkbox" checked={runtime.use_mmap} onChange={(e) => props.onUpdateDraft('runtime.use_mmap', e.target.checked)} /><span>use_mmap</span></label>
            <label className="check-row"><input type="checkbox" checked={runtime.use_mlock} onChange={(e) => props.onUpdateDraft('runtime.use_mlock', e.target.checked)} /><span>use_mlock</span></label>
            <label className="check-row"><input type="checkbox" checked={runtime.verbose_runtime} onChange={(e) => props.onUpdateDraft('runtime.verbose_runtime', e.target.checked)} /><span>verbose runtime</span></label>
          </div>
        </section>

        <section className="card wide">
          <h3>Хаб</h3>
          <div className="settings-grid">
            <label className="check-row"><input type="checkbox" checked={hub.enabled} onChange={(e) => props.onUpdateDraft('hub.enabled', e.target.checked)} /><span>Включить интеграцию с хабом</span></label>
            <label><span>Base URL</span><input value={hub.base_url} onChange={(e) => props.onUpdateDraft('hub.base_url', e.target.value)} /></label>
            <label><span>Models endpoint</span><input value={hub.models_endpoint} onChange={(e) => props.onUpdateDraft('hub.models_endpoint', e.target.value)} /></label>
            <label><span>Pull endpoint</span><input value={hub.pull_endpoint} onChange={(e) => props.onUpdateDraft('hub.pull_endpoint', e.target.value)} /></label>
            <label><span>Token</span><input value={hub.token} onChange={(e) => props.onUpdateDraft('hub.token', e.target.value)} /></label>
            <label><span>Timeout sec</span><input type="number" value={hub.timeout_sec} onChange={(e) => props.onUpdateDraft('hub.timeout_sec', Number(e.target.value))} /></label>
          </div>
        </section>

        <div className="row-actions end">
          <button type="submit">Сохранить настройки</button>
        </div>
      </form>
    </aside>
  );
}

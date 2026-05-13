import React from 'react';
import type { ChatMessage, ModelRecord, RuntimeSettings } from '../types';

type Props = {
  llmModels: ModelRecord[];
  selectedModelId: string;
  systemPrompt: string;
  chatInput: string;
  chatLog: ChatMessage[];
  runtime: RuntimeSettings;
  onSelectModel: (id: string) => void;
  onSystemPromptChange: (v: string) => void;
  onChatInputChange: (v: string) => void;
  onRuntimeChange: (key: keyof RuntimeSettings, value: string | number | boolean) => void;
  onSubmit: (e: React.FormEvent) => void;
};

export default function ChatTab(props: Props) {
  return (
    <section className="card wide">
      <div className="card-head">
        <div>
          <h2>Чат с локальной LLM</h2>
          <p className="muted">Параметры генерации и runtime можно быстро крутить прямо отсюда, как в desktop-движках.</p>
        </div>
        <span className="pill pill-accent">{props.llmModels.length} LLM</span>
      </div>

      <form className="stack" onSubmit={props.onSubmit}>
        <div className="chat-top-grid">
          <label className="stack">
            <span>Модель</span>
            <select value={props.selectedModelId} onChange={(e) => props.onSelectModel(e.target.value)}>
              <option value="">Выбери модель</option>
              {props.llmModels.map((model) => (
                <option key={model.id} value={model.id}>{model.name} — {model.filename}</option>
              ))}
            </select>
          </label>

          <div className="runtime-grid">
            <label><span>max_tokens</span><input type="number" value={props.runtime.max_tokens} onChange={(e) => props.onRuntimeChange('max_tokens', Number(e.target.value))} /></label>
            <label><span>temperature</span><input type="number" step="0.1" value={props.runtime.temperature} onChange={(e) => props.onRuntimeChange('temperature', Number(e.target.value))} /></label>
            <label><span>GPU layers</span><input type="number" value={props.runtime.n_gpu_layers} onChange={(e) => props.onRuntimeChange('n_gpu_layers', Number(e.target.value))} /></label>
            <label><span>Main GPU</span><input type="number" value={props.runtime.main_gpu} onChange={(e) => props.onRuntimeChange('main_gpu', Number(e.target.value))} /></label>
          </div>
        </div>

        <label>System prompt</label>
        <textarea rows={4} value={props.systemPrompt} onChange={(e) => props.onSystemPromptChange(e.target.value)} />

        <div className="chat-window">
          {props.chatLog.length === 0 ? (
            <div className="empty-state">История пока пустая.</div>
          ) : (
            props.chatLog.map((msg, idx) => (
              <div key={`${msg.role}-${idx}`} className={`msg ${msg.role}`}>
                <strong>{msg.role === 'user' ? 'Вы' : 'AI'}:</strong> {msg.text}
              </div>
            ))
          )}
        </div>

        <div className="chat-row">
          <input value={props.chatInput} placeholder="Введите сообщение..." onChange={(e) => props.onChatInputChange(e.target.value)} />
          <button type="submit">Отправить</button>
        </div>
      </form>
    </section>
  );
}

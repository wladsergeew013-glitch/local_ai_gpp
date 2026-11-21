import { useRef } from "react";
import { Icon } from "./Icon";
import "./skill-files.css";

export function MessageFiles({ files, disabled, change, error }: {
  files: File[]; disabled: boolean; change: (next: File[]) => void; error: (message: string) => void;
}) {
  const picker = useRef<HTMLInputElement>(null);
  return <div className="message-files">
    <button type="button" className="tool-button" disabled={disabled} onClick={() => picker.current?.click()}>
      <Icon name="clip" size={16} />Прикрепить файлы
    </button>
    <input hidden type="file" multiple ref={picker} aria-label="Файлы сообщения" disabled={disabled}
      onChange={(event) => {
        const batch = Array.from(event.currentTarget.files || []);
        event.currentTarget.value = "";
        const bad = batch.find(f => !f.size || f.size > 12 * 1024 * 1024);
        if (bad) { error(`«${bad.name}»: выберите непустой файл до 12 МиБ.`); return; }
        error("");
        change([...files, ...batch]);
      }} />
    {!!files.length && <ul aria-label="Прикреплённые файлы" className="message-file-list">
      {files.map((file, index) => <li className="file-chip" key={index}>
        <Icon name="file" size={14}/><span title={file.name}>{file.name}</span>
        <button type="button" className="icon-action" disabled={disabled} aria-label={`Убрать ${file.name}`}
          onClick={() => change(files.filter((_, i) => i !== index))}><Icon name="close" size={13}/></button>
      </li>)}
    </ul>}
  </div>;
}

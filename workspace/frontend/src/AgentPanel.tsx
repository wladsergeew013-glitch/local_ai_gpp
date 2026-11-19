import { useEffect, useState } from "react";
import { api, type Conversation, type ChatEvent } from "./chat";

type FileDiff = {
  files: {
    path: string;
    status: string;
    before_type: string;
    after_type: string;
    text_omitted?: string;
  }[];
  omissions?: { path: string; reason: string; stage: string }[];
  diff: string;
  baseline_available: boolean;
  complete: boolean;
  truncated?: boolean;
};

const reasons: Record<string, string> = {
  invalid_path: "недопустимый путь",
  linked_parent: "путь внутри ссылки",
  read_error: "ошибка чтения",
  scan_error: "не удалось прочитать папку",
  changed_during_read: "файл менялся при чтении",
  binary: "бинарный файл",
  non_utf8: "файл не в UTF-8",
  large: "файл превышает лимит размера",
  size_limit: "достигнут общий лимит чтения файлов",
  diff_limit: "достигнут лимит размера diff",
  legacy_baseline: "в исходной копии файл был пропущен",
  directory: "каталог или подмодуль",
  special: "особый тип файла",
  type: "сравнение типов без чтения ссылки",
};
const fileActions: Record<string, string> = {
  added: "Создан",
  deleted: "Удалён",
  modified: "Изменён",
  type_changed: "Изменился тип",
};
const fileTypes: Record<string, string> = {
  file: "файл",
  link: "ссылка",
  directory: "каталог",
  special: "особый файл",
};

function ChangedFiles({ result }: { result: FileDiff }) {
  if (!result.baseline_available)
    return <p>Исходное состояние файлов пока не сохранено.</p>;
  return (
    <>
      {!result.complete && (
        <p role="status">Сравнение неполное. Проверьте отмеченные файлы.</p>
      )}
      {result.complete && !result.files.length && <p>Изменений файлов нет.</p>}
      {!!result.files.length && (
        <ul className="diff-files">
          {result.files.map((file) => (
            <li key={file.path}>
              <strong>{fileActions[file.status]}</strong>{" "}
              <code>{file.path}</code>
              {file.status === "type_changed" && (
                <>
                  {" "}
                  · {fileTypes[file.before_type]} → {fileTypes[file.after_type]}
                </>
              )}
              {file.text_omitted && <> · {reasons[file.text_omitted]}</>}
            </li>
          ))}
        </ul>
      )}
      {!!result.omissions?.length && (
        <details open>
          <summary>Не удалось проверить ({result.omissions.length})</summary>
          <ul className="diff-files">
            {result.omissions.map(({ path, stage, reason }, index) => (
              <li key={index}>
                <code>{path}</code> · {stage === "before" ? "до" : "после"} ·{" "}
                {reasons[reason]}
              </li>
            ))}
          </ul>
        </details>
      )}
      {result.truncated && (
        <p>Текст изменений показан частично из-за ограничения размера.</p>
      )}
      {result.diff && (
        <div className="diff-lines">
          {result.diff.split("\n").map((line, index) => (
            <div
              key={index}
              className={
                line.startsWith("+")
                  ? "add"
                  : line.startsWith("-")
                    ? "remove"
                    : ""
              }
            >
              {line || " "}
            </div>
          ))}
        </div>
      )}
    </>
  );
}

export function AgentPanel({
  current,
  events,
  act,
}: {
  current: Conversation;
  events: ChatEvent[];
  act: (action: () => Promise<unknown>) => void;
}) {
  const [tab, setTab] = useState("events"),
    [diff, setDiff] = useState<FileDiff | null>(null),
    [diffError, setDiffError] = useState(""),
    [answer, setAnswer] = useState<Record<string, string>>({});
  useEffect(() => {
    if (tab !== "diff") return;
    let valid = true;
    setDiff(null);
    setDiffError("");
    api<FileDiff>("/chats/" + current.id + "/diff")
      .then((result) => {
        if (valid) setDiff(result);
      })
      .catch((error) => {
        if (valid) setDiffError((error as Error).message);
      });
    return () => {
      valid = false;
    };
  }, [tab, current.id, current.status]);
  return (
    <section className="agent-panel">
      {!!current.approvals?.length &&
        current.approvals.map((a) => (
          <div className="permission-card" key={a.id}>
            <h3>
              {a.kind === "question"
                ? "Вопрос помощника"
                : "Нужно ваше разрешение"}
            </h3>
            <pre>{JSON.stringify(a.payload.payload, null, 2)}</pre>
            {a.kind === "question" ? (
              <>
                <textarea
                  aria-label="Ответ помощнику"
                  value={answer[a.id] || ""}
                  onChange={(e) =>
                    setAnswer({ ...answer, [a.id]: e.target.value })
                  }
                />
                <button
                  onClick={() =>
                    act(() =>
                      api("/approvals/" + a.id, {
                        answers: (a.payload.payload?.questions || []).map(
                          (q: any) => ({
                            id: q.id,
                            selected: [],
                            custom: answer[a.id] || "",
                          }),
                        ),
                      }),
                    )
                  }
                >
                  Ответить
                </button>
              </>
            ) : (
              <div className="button-row">
                <button
                  className="primary"
                  onClick={() =>
                    act(() =>
                      api("/approvals/" + a.id, { outcome: "allowed-once" }),
                    )
                  }
                >
                  Разрешить один раз
                </button>
                <button
                  onClick={() =>
                    act(() =>
                      api("/approvals/" + a.id, { outcome: "rejected" }),
                    )
                  }
                >
                  Отклонить
                </button>
              </div>
            )}
          </div>
        ))}
      <nav className="tabs">
        <button
          className={tab === "events" ? "current" : ""}
          onClick={() => setTab("events")}
        >
          Действия
        </button>
        <button
          className={tab === "diff" ? "current" : ""}
          onClick={() => setTab("diff")}
        >
          Изменения файлов
        </button>
      </nav>
      {tab === "diff" ? (
        <div className="diff">
          <p className="muted">
            Изменения относительно начала работы. Ваши исходные правки
            сохранены.
          </p>
          {diffError ? (
            <p role="alert">Не удалось загрузить изменения. {diffError}</p>
          ) : diff ? (
            <ChangedFiles result={diff} />
          ) : (
            <p role="status">Сравниваю файлы…</p>
          )}
        </div>
      ) : (
        <div className="agent-events">
          {events
            .filter(
              (e) =>
                e.kind === "dsh" &&
                ["tool/call", "tool/result"].includes(e.payload.type),
            )
            .map((e) => (
              <details className="event" key={e.seq}>
                <summary>
                  {e.payload.type === "tool/call" ? "→ " : "✓ "}
                  {e.payload.data?.name || "Результат действия"}{" "}
                  <time>
                    {new Date(e.created_at).toLocaleTimeString("ru-RU")}
                  </time>
                </summary>
                <pre>{JSON.stringify(e.payload.data, null, 2)}</pre>
              </details>
            ))}
        </div>
      )}
    </section>
  );
}

import { useEffect, useRef, useState } from "react";
import { MarkdownAnswer } from "./MarkdownAnswer";
import { Icon } from "./Icon";
import {
  downloadArtifact,
  messages,
  type ChatEvent,
  type Conversation,
  type SkillReport as SkillReportData,
  active,
} from "./chat";

function SkillReport({
  result,
  name,
  chatId,
}: {
  chatId: string;
  result: SkillReportData;
  name?: string;
}) {
  const [error, setError] = useState("");
  return (
    <>
    <details className="skill-report">
      <summary>
        <Icon name="file" size={16} />
        <strong>Результат навыка{name ? " · " + name : ""}</strong>
        <span>{result.filename || result.tool}</span>
        <Icon name="chevron" size={15} />
      </summary>
      <pre>{JSON.stringify(result.report, null, 2)}</pre>
    </details>
    {result.artifacts?.map((file) => (
      <button type="button" className="skill-download" key={file.id} onClick={async () => {
        setError("");
        try { await downloadArtifact(chatId, file); } catch (e) { setError((e as Error).message); }
      }}>
        <Icon name="file" size={16} /><span>Скачать {file.name}</span>
      </button>
    ))}
    {error && <p role="alert" className="message-error">{error}</p>}
    </>
  );
}

type NamedReport = { seq: number; name?: string; result: SkillReportData };

function reportsByTurn(events: ChatEvent[], current: Conversation | null) {
  const grouped = new Map<string, NamedReport[]>();
  const details = current?.details;
  const legacy = !details?.skill_reports && details?.skill_result;
  const lastLegacy = legacy
    ? [...events].reverse().find((e) => e.kind === "skill_tool")?.seq
    : undefined;
  let turn = "";
  for (const e of events) {
    if (e.kind === "chat_message" && e.payload.role === "user")
      turn = e.payload.turn_id;
    if (e.kind !== "skill_tool") continue;
    const result = e.payload.result ?? (lastLegacy === e.seq ? legacy : null);
    if (!result) continue;
    const key = e.payload.turn_id || turn;
    const reports = grouped.get(key) || [];
    reports.push({ seq: e.seq, name: e.payload.skill, result });
    grouped.set(key, reports);
  }
  return { grouped, latest: turn };
}

export function ChatHistory({
  events,
  current,
  compact = false,
}: {
  events: ChatEvent[];
  current: Conversation | null;
  compact?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null),
    sticky = useRef(true),
    selected = current?.id;
  useEffect(() => {
    sticky.current = true;
  }, [selected]);
  useEffect(() => {
    if (sticky.current && ref.current)
      ref.current.scrollTop = ref.current.scrollHeight;
  }, [events, current?.details?.partial]);
  const [downloadError, setDownloadError] = useState("");
  const history = messages(events);
  const last = history.at(-1);
  // Events and status are polled separately. A completed turn can arrive first.
  const answered = (current?.mode || current?.details?.mode) === "chat" &&
    last?.role === "assistant" && !!last.turnId;
  const pending = !!current && active.has(current.status) && !answered;
  const { grouped, latest } = reportsByTurn(compact ? [] : events, current);
  function renderReports(turnId?: string) {
    return (grouped.get(turnId || "") || []).map((r) => (
      <SkillReport chatId={selected!} key={r.seq} result={r.result} name={r.name} />
    ));
  }
  return (
    <div
      className={
        "conversation-messages " +
        (compact ? "mini-messages" : "activity-scroll")
      }
      ref={ref}
      onScroll={(e) => {
        const el = e.currentTarget;
        sticky.current = el.scrollHeight - el.scrollTop - el.clientHeight < 70;
      }}
    >
      {history.map((m) => (
        <article
          key={m.id}
          className={
            "chat-message " + (compact ? "mini-message " : "") + m.role
          }
        >
          <header>
            <strong>{m.role === "user" ? "Вы" : "Помощник"}</strong>
            <time dateTime={m.time}>
              {new Date(m.time).toLocaleString("ru-RU", {
                day: "2-digit",
                month: "2-digit",
                year: "numeric",
                hour: "2-digit",
                minute: "2-digit",
              })}
            </time>
          </header>
          {m.role === "assistant" && !compact ? (
            <MarkdownAnswer text={m.text} />
          ) : (
            <div>{m.text}</div>
          )}
          {m.role === "assistant" && m.turnId && renderReports(m.turnId)}
          {!!m.documents?.length && <ul className="message-file-list" aria-label="Файлы сообщения">{m.documents.map(doc => (
            <li key={doc.id}><button type="button" className="tool-button" onClick={async () => {
              setDownloadError("");
              try { await downloadArtifact(current!.id, doc); } catch (err) { setDownloadError((err as Error).message); }
            }}><Icon name="file" size={14}/>{doc.name}</button></li>
          ))}</ul>}
          {m.error && (
            <p className="message-error" role="status">
              {m.error}
            </p>
          )}
        </article>
      ))}
      {renderReports()}
      {downloadError && <p role="alert">{downloadError}</p>}
      {pending && (
        <article
          className={
            "chat-message assistant pending " + (compact ? "mini-message" : "")
          }
        >
          <header>Помощник</header>
          {current?.details?.partial && !compact ? (
            <MarkdownAnswer text={current.details.partial} />
          ) : (
            <div>
              {current?.details?.partial ||
                (current?.status === "awaiting_permission"
                  ? "Ожидаю вашего разрешения…"
                  : "Пишу ответ…")}
            </div>
          )}
          {latest &&
            !history.some(
              (m) => m.role === "assistant" && m.turnId === latest,
            ) &&
            renderReports(latest)}
        </article>
      )}
    </div>
  );
}

import { useEffect, useRef } from "react";
import { messages, type ChatEvent, type Conversation, active } from "./chat";
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
  const pending = !!current && active.has(current.status);
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
      {messages(events).map((m) => (
        <article key={m.id} className={"chat-message " + (compact ? "mini-message " : "") + m.role}>
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
          <div>{m.text}</div>
          {m.error && (
            <p className="message-error" role="status">
              {m.error}
            </p>
          )}
        </article>
      ))}
      {pending && (
        <article className={"chat-message assistant pending " + (compact ? "mini-message" : "")}>
          <header>Помощник</header>
          <div>
            {current?.details?.partial ||
              (current?.status === "awaiting_permission"
                ? "Ожидаю вашего разрешения…"
                : "Пишу ответ…")}
          </div>
        </article>
      )}
    </div>
  );
}

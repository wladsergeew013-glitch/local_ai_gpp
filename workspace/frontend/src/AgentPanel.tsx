import { useEffect, useState } from "react";
import { api, type Conversation, type ChatEvent } from "./chat";
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
    [diff, setDiff] = useState<any>(null),
    [answer, setAnswer] = useState<Record<string, string>>({});
  useEffect(() => {
    if (tab === "diff")
      api("/chats/" + current.id + "/diff")
        .then(setDiff)
        .catch(() => setDiff(null));
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
          {diff?.diff
            ? diff.diff.split("\n").map((line: string, i: number) => (
                <div
                  key={i}
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
              ))
            : "Изменений файлов пока нет."}
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

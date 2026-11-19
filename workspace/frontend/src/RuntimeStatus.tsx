import { useEffect, useState } from "react";
import { api, type Provider, type RuntimeProbe } from "./chat";

const labels: Record<string, string> = {
  ready: "Модель загружена",
  unloaded: "Модель загрузится при запросе",
  missing: "Модель отсутствует в Runtime",
  choose: "Выберите модель Runtime",
  checking: "Проверяю Runtime…",
  offline: "Runtime недоступен",
};
export function RuntimeStatus({
  provider,
  open,
}: {
  provider?: Provider;
  open: () => void;
}) {
  const [status, setStatus] = useState("checking");
  useEffect(() => {
    let valid = true;
    const check = () => {
      if (!provider) return;
      api<RuntimeProbe>("/providers/" + provider.id + "/probe", {
        base_url: provider.base_url,
        model: provider.model,
      })
        .then((outcome) => {
          if (valid) {
            if (outcome.model_available === false) setStatus("missing");
            else if (outcome.model_available === null) setStatus("choose");
            else setStatus(outcome.model_loaded ? "ready" : "unloaded");
          }
        })
        .catch(() => {
          if (valid) setStatus("offline");
        });
    };
    setStatus("checking");
    check();
    const timer = setInterval(check, 15000);
    return () => {
      valid = false;
      clearInterval(timer);
    };
  }, [provider?.id, provider?.base_url, provider?.model]);
  return (
    <button className={"runtime-status " + status} onClick={open}>
      <span
        className={"dot " + (provider && status === "ready" ? "completed" : "")}
      />
      <span>
        {provider ? labels[status] : "Подключить Runtime"}
        {provider && (
          <small>
            {provider.model.split(":")[0]} · {new URL(provider.base_url).host}
          </small>
        )}
      </span>
    </button>
  );
}

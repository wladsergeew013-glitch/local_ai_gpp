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
export type RuntimeObservation = {
  id: string;
  base_url: string;
  model: string;
  status: string;
  time: number;
};
export function probeStatus(result: RuntimeProbe) {
  if (result.model_available === false) return "missing";
  if (result.model_available === null) return "choose";
  return result.model_loaded ? "ready" : "unloaded";
}
export function RuntimeStatus({
  provider,
  observation,
  open,
}: {
  provider?: Provider;
  observation?: RuntimeObservation;
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
          if (valid) setStatus(probeStatus(outcome));
        })
        .catch(() => {
          if (valid) setStatus("offline");
        });
    };
    if (
      observation &&
      provider &&
      observation.id === provider.id &&
      observation.base_url === provider.base_url &&
      observation.model === provider.model &&
      Date.now() - observation.time < 15000
    ) {
      setStatus(observation.status);
    } else {
      setStatus("checking");
      check();
    }
    const timer = setInterval(check, 15000);
    return () => {
      valid = false;
      clearInterval(timer);
    };
  }, [provider?.id, provider?.base_url, provider?.model, observation]);
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

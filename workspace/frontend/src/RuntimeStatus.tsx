import { useEffect, useState } from "react";
import { api, type Provider } from "./chat";
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
      api("/providers/" + provider.id + "/probe", {
        base_url: provider.base_url,
        model: provider.model,
      })
        .then(() => {
          if (valid) setStatus("ready");
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
        {!provider
          ? "Подключить Runtime"
          : status === "ready"
            ? "Runtime подключён"
            : status === "checking"
              ? "Проверяю Runtime…"
              : "Runtime недоступен"}
        {provider && (
          <small>
            {provider.model.split(":")[0]} · {new URL(provider.base_url).host}
          </small>
        )}
      </span>
    </button>
  );
}

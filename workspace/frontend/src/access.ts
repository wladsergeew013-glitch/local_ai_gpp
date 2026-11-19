type DesktopWindow = Window & {
  pywebview?: { api?: { workspace_token?: () => Promise<string | null> } };
};

export const operatorToken = new Promise<string | null>((resolve) => {
  const fromLink = new URLSearchParams(location.hash.slice(1)).get("access");
  if (fromLink !== null) {
    history.replaceState(null, "", location.pathname + location.search);
    resolve(/^[A-Za-z0-9_-]{43,128}$/.test(fromLink) ? fromLink : null);
    return;
  }
  function connectDesktop() {
    const bridge = (window as DesktopWindow).pywebview?.api;
    if (!bridge?.workspace_token) return;
    window.removeEventListener("pywebviewready", connectDesktop);
    bridge
      .workspace_token()
      .then(resolve, () => resolve(null))
      .finally(() => window.clearTimeout(timeout));
  }
  const timeout = window.setTimeout(() => {
    window.removeEventListener("pywebviewready", connectDesktop);
    resolve(null);
  }, 5000);
  window.addEventListener("pywebviewready", connectDesktop, { once: true });
  connectDesktop();
});

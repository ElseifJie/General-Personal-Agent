export function registerServiceWorker(): void {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) {
    return;
  }
  const register = () => {
    // The SPA owns the root; the service worker caches only the shell and never
    // intercepts /api or /ws responses.
    const scope = import.meta.env.BASE_URL || "/";
    void navigator.serviceWorker
      .register(`${scope}sw.js`, { scope })
      .catch(() => undefined);
  };
  if (document.readyState === "complete") register();
  else window.addEventListener("load", register, { once: true });
}

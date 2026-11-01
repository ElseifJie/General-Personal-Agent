/* 助手工作台 Service Worker（最小离线 shell）。
 * 策略：/api 永不缓存（直通）；静态资源 cache-first；导航请求 network-first、
 * 失败回退缓存的 index.html（离线 shell）。版本号变更即弃旧缓存。
 */
const CACHE = "gpa-shell-v1";
const SHELL = ["/"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api") || url.pathname.startsWith("/ws")) return; // 数据永不缓存

  if (url.pathname.startsWith("/assets/")) {
    // 带 hash 的构建产物：cache-first。
    event.respondWith(
      caches.match(request).then(
        (hit) =>
          hit ||
          fetch(request).then((resp) => {
            const copy = resp.clone();
            caches.open(CACHE).then((c) => c.put(request, copy));
            return resp;
          }),
      ),
    );
    return;
  }

  if (request.mode === "navigate" || url.pathname === "/") {
    // 导航：network-first，离线回退缓存 shell。只把真正的 /wb/ 壳写进缓存；
    // 其他路由不能覆盖离线 shell。
    const isShell = url.pathname === "/";
    event.respondWith(
      fetch(request)
        .then((resp) => {
          if (isShell) {
            const copy = resp.clone();
            caches.open(CACHE).then((c) => c.put("/", copy));
          }
          return resp;
        })
        .catch(() => caches.match("/")),
    );
  }
});

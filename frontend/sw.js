self.addEventListener("install", event => {
  event.waitUntil(
    caches.open("imys-cache-v1").then(cache => {
      return cache.addAll([
        "/",
        "/index.html",
        "/manifest.json",
        "/logo.png",
        "/default-qr.jpeg"
      ]);
    })
  );
});

self.addEventListener("fetch", event => {
  event.respondWith(
    caches.match(event.request).then(response => {
      return response || fetch(event.request);
    })
  );
});

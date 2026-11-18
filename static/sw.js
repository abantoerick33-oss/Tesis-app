// Service worker: permite instalar la app y mostrar notificaciones
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});

// Al tocar la notificación, abre la app
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(
    self.clients.matchAll({ type: "window" }).then((lista) =>
      lista.length ? lista[0].focus() : self.clients.openWindow("/")
    )
  );
});

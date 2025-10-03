// Tiny helper to show offline banner & optionally intercept forms

(function () {
  const banner = document.getElementById("offline-banner");

  function updateBanner() {
    if (!banner) return;
    const online = navigator.onLine;
    banner.style.display = online ? "none" : "block";
  }

  window.addEventListener("online", updateBanner);
  window.addEventListener("offline", updateBanner);
  updateBanner();

  // Listen for SW messages (e.g., queued requests)
  navigator.serviceWorker && navigator.serviceWorker.addEventListener("message", (evt) => {
    if (evt.data && evt.data.type === "offline-pending" && banner) {
      banner.style.display = "block";
      banner.textContent = "Offline — change queued and will sync";
    }
  });

  // Optional: intercept POST forms to add an idempotency key (reduces dupe writes)
  document.addEventListener("submit", (e) => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.method && form.method.toUpperCase() === "POST") {
      // Add a hidden idempotency key if not present
      if (!form.querySelector("input[name='_idem']")) {
        const h = document.createElement("input");
        h.type = "hidden";
        h.name = "_idem";
        h.value = Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
        form.appendChild(h);
      }
    }
  });
})();

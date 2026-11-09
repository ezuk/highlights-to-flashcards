// Reloads the page when the app's code changes, and swaps in changed styles without a reload.
// The server adds this script only when H2F_LIVE_RELOAD is set.

(() => {
  const EVERY_MS = 1000;
  let known = null;
  let waiting = false;

  const typing = () => {
    const el = document.activeElement;
    return el && (el.tagName === "TEXTAREA" || el.tagName === "INPUT" || el.isContentEditable);
  };

  // Waits for the cursor to leave a field, so a reload never cuts into typing
  function reloadWhenIdle() {
    if (!typing()) {
      location.reload();
      return;
    }
    if (waiting) return;
    waiting = true;
    document.addEventListener("focusout", () => setTimeout(reloadWhenIdle, 0), { once: true });
  }

  function swapStyles() {
    for (const link of document.querySelectorAll('link[rel="stylesheet"]')) {
      const url = new URL(link.href);
      url.searchParams.set("v", Date.now());
      link.href = url.href;
    }
  }

  async function check() {
    try {
      const response = await fetch("/__live/version", { cache: "no-store" });
      if (response.ok) {
        const version = await response.json();
        if (known && version.page !== known.page) reloadWhenIdle();
        else if (known && version.styles !== known.styles) swapStyles();
        known = version;
      }
    } catch {
      // The server is restarting
    }
    setTimeout(check, EVERY_MS);
  }

  check();
})();

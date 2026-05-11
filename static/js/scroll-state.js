(() => {
  const positionKey = "labOs.scrollPosition";
  const taskAnchorKey = "labOs.taskActionScrollAnchor";
  const legacyPositionKey = "scrollY";

  function currentPageKey() {
    return window.location.pathname + window.location.search;
  }

  function readJSON(key) {
    try {
      return JSON.parse(localStorage.getItem(key) || "null");
    } catch {
      return null;
    }
  }

  function writeJSON(key, value) {
    localStorage.setItem(key, JSON.stringify(value));
  }

  function taskItems() {
    return Array.from(
      document.querySelectorAll("[data-task-id].task-title-scope"),
    );
  }

  function taskItemFor(target) {
    if (!target || typeof target.closest !== "function") return null;
    return target.closest("[data-task-id].task-title-scope");
  }

  function findTaskItem(taskId) {
    const id = String(taskId);
    return taskItems().find((item) => item.dataset.taskId === id) || null;
  }

  function saveScrollPosition() {
    const payload = {
      page: currentPageKey(),
      y: window.scrollY,
      savedAt: Date.now(),
    };
    writeJSON(positionKey, payload);
    localStorage.setItem(legacyPositionKey, String(window.scrollY));
  }

  function saveTaskActionAnchor(target) {
    const currentItem = taskItemFor(target);
    if (!currentItem) return;

    const items = taskItems();
    const index = items.indexOf(currentItem);
    if (index < 0) return;

    const anchor = items[index + 1] || items[index - 1];
    if (!anchor) return;

    const rect = anchor.getBoundingClientRect();
    writeJSON(taskAnchorKey, {
      page: currentPageKey(),
      taskId: anchor.dataset.taskId,
      viewportTop: rect.top,
      fallbackY: window.scrollY,
      savedAt: Date.now(),
    });
  }

  function restoreTaskActionAnchor() {
    const payload = readJSON(taskAnchorKey);
    if (!payload) return false;
    if (payload.page !== currentPageKey()) {
      localStorage.removeItem(taskAnchorKey);
      return false;
    }

    localStorage.removeItem(taskAnchorKey);
    const anchor = findTaskItem(payload.taskId);
    if (!anchor) return false;

    const rect = anchor.getBoundingClientRect();
    const nextY = window.scrollY + rect.top - Number(payload.viewportTop || 0);
    window.scrollTo(0, Math.max(0, nextY));
    return true;
  }

  function restoreScrollPosition() {
    if (restoreTaskActionAnchor()) return;

    const payload = readJSON(positionKey);
    if (payload) {
      if (payload.page === currentPageKey()) {
        window.scrollTo(0, Number(payload.y || 0));
        localStorage.removeItem(positionKey);
        localStorage.removeItem(legacyPositionKey);
      }
      return;
    }

    const legacyY = localStorage.getItem(legacyPositionKey);
    if (legacyY !== null) {
      window.scrollTo(0, parseInt(legacyY, 10));
      localStorage.removeItem(legacyPositionKey);
    }
  }

  function bindTaskCompletionForms() {
    if (document.documentElement.dataset.taskCompletionScrollBound === "1") {
      return;
    }
    document.documentElement.dataset.taskCompletionScrollBound = "1";

    document.addEventListener("submit", (event) => {
      const form = event.target;
      if (!form || form.tagName !== "FORM") return;

      const action = form.getAttribute("action") || "";
      if (!action.startsWith("/done/")) return;

      saveTaskActionAnchor(form);
      saveScrollPosition();
    });
  }

  if ("scrollRestoration" in window.history) {
    window.history.scrollRestoration = "manual";
  }

  window.LabOsScroll = {
    bindTaskCompletionForms,
    restoreScrollPosition,
    saveScrollPosition,
  };
})();

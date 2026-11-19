// Webview side of the chat view. Every string is untrusted model or tool
// output: it is set with textContent, never parsed as HTML.
(function () {
  const vscode = acquireVsCodeApi();
  const log = document.getElementById("log");
  const form = document.getElementById("form");
  const input = document.getElementById("input");
  const send = document.getElementById("send");
  const stop = document.getElementById("stop");

  // The model's text as it streams in. Any other message ends it; the
  // final answer replaces it, so the view never shows the answer twice.
  let live = null;

  function addLive(text) {
    if (!live) {
      add("answer live", "");
      live = log.lastElementChild;
    }
    live.textContent += text;
    live.scrollIntoView({ block: "end" });
  }

  function add(kind, text) {
    if (kind === "answer" && live) {
      live.textContent = text;
      live.classList.remove("live");
      live = null;
      return;
    }
    if (live) {
      live.classList.remove("live");
      live.className = live.className.replace("answer", "thought");
      live = null;
    }
    const empty = document.getElementById("empty");
    if (empty) {
      empty.remove();
    }
    const el = document.createElement("div");
    el.className = "msg " + kind;
    el.textContent = text;
    log.appendChild(el);
    el.scrollIntoView({ block: "end" });
  }

  function submit() {
    const text = input.value.trim();
    if (!text) {
      return;
    }
    vscode.postMessage({ type: "task", text });
    input.value = "";
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    submit();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  });
  for (const btn of document.querySelectorAll("button[data-cmd]")) {
    btn.addEventListener("click", () => vscode.postMessage({ type: btn.dataset.cmd }));
  }

  window.addEventListener("message", (e) => {
    const { kind, text } = e.data || {};
    if (kind === "clear") {
      log.replaceChildren();
      return;
    }
    if (kind === "busy") {
      send.disabled = !!text;
      stop.disabled = !text;
      return;
    }
    if (kind === "delta" && typeof text === "string") {
      addLive(text);
      return;
    }
    if (typeof kind === "string" && typeof text === "string") {
      add(kind, text);
    }
  });
})();

// Webview side of the chat view. Every string is untrusted model or tool
// output: it is set with textContent, never parsed as HTML.
(function () {
  const vscode = acquireVsCodeApi();
  const log = document.getElementById("log");
  const form = document.getElementById("form");
  const input = document.getElementById("input");
  const send = document.getElementById("send");
  const stop = document.getElementById("stop");

  function add(kind, text) {
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
    if (typeof kind === "string" && typeof text === "string") {
      add(kind, text);
    }
  });
})();

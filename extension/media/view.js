// Webview side of the chat view. Every string is untrusted model or tool
// output: it is placed with textContent and createElement, never parsed as
// HTML. The small Markdown renderer below builds DOM nodes the same way.
(function () {
  const vscode = acquireVsCodeApi();
  const $ = (id) => document.getElementById(id);
  const log = $("log");
  const form = $("composer");
  const input = $("input");
  const PLACEHOLDER = input.placeholder;
  const send = $("send");
  const stop = $("stop");
  const history = [];
  let recall = -1;
  let busy = false;

  // The model's text as it streams in. Any other message ends it; the
  // final answer replaces it, so the view never shows the answer twice.
  let live = null;
  let turn = null;        // the current exchange: the user's task and what followed
  let steps = null;       // its tool calls, grouped
  let thinking = null;
  const emptyState = $("empty").cloneNode(true);

  // ------------------------------------------------------------ helpers

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function icon(name, cls) {
    const t = $("icon-" + name);
    const svg = t ? t.content.firstElementChild.cloneNode(true) : el("span");
    if (cls) svg.setAttribute("class", "ic " + cls);
    return svg;
  }

  function scroll() {
    log.scrollTop = log.scrollHeight;
  }

  function clearEmpty() {
    const empty = $("empty");
    if (empty) empty.remove();
  }

  function container() {
    if (!turn) {
      turn = el("div", "turn");
      log.appendChild(turn);
    }
    return turn;
  }

  function setThinking(on) {
    if (on && !thinking) {
      thinking = el("div", "thinking");
      thinking.append(el("span", "dot"), el("span", "dot"), el("span", "dot"), el("span", "label", "Working"));
      container().appendChild(thinking);
      scroll();
    } else if (!on && thinking) {
      thinking.remove();
      thinking = null;
    }
  }

  function append(node) {
    clearEmpty();
    const parent = container();
    if (thinking) parent.insertBefore(node, thinking);
    else parent.appendChild(node);
    scroll();
    return node;
  }

  // ----------------------------------------------------------- markdown

  // Inline: `code`, **bold**, *emphasis*. Everything else is text.
  function inline(parent, text) {
    const re = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*)/g;
    let last = 0;
    let m;
    while ((m = re.exec(text))) {
      if (m.index > last) parent.appendChild(document.createTextNode(text.slice(last, m.index)));
      const tok = m[0];
      if (tok.startsWith("`")) parent.appendChild(el("code", "", tok.slice(1, -1)));
      else if (tok.startsWith("**")) parent.appendChild(el("strong", "", tok.slice(2, -2)));
      else parent.appendChild(el("em", "", tok.slice(1, -1)));
      last = m.index + tok.length;
    }
    if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
  }

  function codeBlock(lang, body) {
    const wrap = el("div", "code");
    const head = el("div", "code-head");
    head.appendChild(el("span", "lang", lang || "text"));
    const copy = el("button", "copy");
    copy.title = "Copy";
    copy.appendChild(icon("copy"));
    copy.addEventListener("click", () => {
      navigator.clipboard.writeText(body).then(() => {
        copy.replaceChildren(icon("check"));
        setTimeout(() => copy.replaceChildren(icon("copy")), 1200);
      }, () => {});
    });
    head.appendChild(copy);
    const pre = el("pre");
    pre.appendChild(el("code", "", body));
    wrap.append(head, pre);
    return wrap;
  }

  function blocks(parent, text) {
    const lines = text.split("\n");
    let para = [];
    let list = null;
    const flush = () => {
      if (para.length) {
        const p = el("p");
        inline(p, para.join("\n"));
        parent.appendChild(p);
        para = [];
      }
    };
    let table = null;
    const cells = (line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|");
    for (const line of lines) {
      if (/^\s*\|/.test(line)) {
        // A Markdown table: header row, rule, body rows. Built with DOM
        // calls like everything else here; model text is never HTML.
        flush();
        list = null;
        if (/^\s*\|?\s*:?-{2,}/.test(line)) continue;        // the |---|---| rule
        if (!table) {
          const wrap = el("div", "table-wrap");
          table = el("table");
          wrap.appendChild(table);
          parent.appendChild(wrap);
          const tr = el("tr");
          for (const c of cells(line)) { const th = el("th"); inline(th, c.trim()); tr.appendChild(th); }
          table.appendChild(el("thead")).appendChild(tr);
          table.appendChild(el("tbody"));
          continue;
        }
        const tr = el("tr");
        for (const c of cells(line)) { const td = el("td"); inline(td, c.trim()); tr.appendChild(td); }
        table.lastChild.appendChild(tr);
        continue;
      }
      table = null;
      if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {     // a horizontal rule
        flush();
        list = null;
        parent.appendChild(el("hr"));
        continue;
      }
      const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
      const number = /^\s*\d+[.)]\s+(.*)$/.exec(line);
      const heading = /^#{1,4}\s+(.*)$/.exec(line);
      if (bullet || number) {
        flush();
        const tag = bullet ? "ul" : "ol";
        if (!list || list.tagName.toLowerCase() !== tag) {
          list = el(tag);
          parent.appendChild(list);
        }
        const li = el("li");
        inline(li, (bullet || number)[1]);
        list.appendChild(li);
        continue;
      }
      list = null;
      if (heading) {
        flush();
        const h = el("h4");
        inline(h, heading[1]);
        parent.appendChild(h);
      } else if (!line.trim()) {
        flush();
      } else {
        para.push(line);
      }
    }
    flush();
  }

  function markdown(parent, text) {
    parent.replaceChildren();
    const fence = /```([\w+-]*)[^\n]*\n([\s\S]*?)(```|$)/g;
    let last = 0;
    let m;
    while ((m = fence.exec(text))) {
      if (m.index > last) blocks(parent, text.slice(last, m.index));
      parent.appendChild(codeBlock(m[1], m[2].replace(/\n$/, "")));
      last = fence.lastIndex;
    }
    if (last < text.length) blocks(parent, text.slice(last));
  }

  // ------------------------------------------------------------- pieces

  function addUser(text) {
    if (live) endLive();
    turn = null;
    steps = null;
    const node = el("div", "msg user");
    node.appendChild(el("div", "body", text));
    append(node);
    if (busy) setThinking(true);
  }

  function addLive(text) {
    if (!live) {
      setThinking(false);
      steps = null;
      live = append(el("div", "msg answer live"));
      live.dataset.raw = "";
    }
    live.dataset.raw += text;
    markdown(live, live.dataset.raw);
    scroll();
  }

  function endLive() {
    // Text the model wrote before calling a tool: shown as a step, not the answer.
    live.className = "msg answer thought";
    live = null;
  }

  function addAnswer(text) {
    setThinking(false);
    let node = live;
    live = null;
    if (!node) node = append(el("div", "msg answer"));
    node.className = "msg answer";
    markdown(node, text);
    scroll();
  }

  const TOOL = /^\.\s+(\S+)\s?(.*?)\s{2}\[(ok|failed)\]\s?(.*)$/;
  const DECIDED = /^(\S+)(?:\s(.*))?:\s(approved|denied)$/;

  function stepList() {
    if (live) endLive();
    if (!steps) {
      steps = append(el("div", "steps"));
    }
    return steps;
  }

  function addLog(text) {
    const tool = TOOL.exec(text);
    const decided = DECIDED.exec(text);
    const row = el("div", "step");
    if (tool) {
      const [, name, target, mark, rest] = tool;
      row.classList.add(mark === "ok" ? "ok" : "failed");
      row.appendChild(el("span", "bullet"));
      row.appendChild(el("span", "name", name));
      if (target) row.appendChild(el("span", "target", target));
      if (rest) row.appendChild(el("span", "rest", rest));
    } else if (decided) {
      const [, name, target, verdict] = decided;
      row.classList.add(verdict);
      row.appendChild(icon(verdict === "approved" ? "check" : "warn", "small"));
      row.appendChild(el("span", "name", verdict === "approved" ? "Approved" : "Denied"));
      row.appendChild(el("span", "target", name + (target ? " " + target : "")));
    } else {
      row.classList.add("plain");
      row.appendChild(el("span", "rest", text));
    }
    stepList().appendChild(row);
    scroll();
  }

  function addBanner(kind, text, actions) {
    if (live) endLive();
    const node = el("div", "banner " + kind);
    node.appendChild(icon(kind === "error" || kind === "notice" || kind === "gateway" ? "warn" : "shield", "small"));
    const body = el("div", "text");
    inline(body, text);
    node.appendChild(body);
    if (actions && actions.length) {
      const row = el("div", "actions");
      for (const [label, cmd] of actions) {
        const b = el("button", "link", label);
        b.addEventListener("click", () => vscode.postMessage({ type: cmd }));
        row.appendChild(b);
      }
      node.appendChild(row);
    }
    append(node);
    return node;
  }

  function addStats(text) {
    setThinking(false);
    const node = el("div", "stats");
    node.appendChild(icon("check", "small"));
    node.appendChild(el("span", "", text));
    append(node);
    turn = null;
    steps = null;
  }

  function addSetup() {
    clearEmpty();
    const card = el("section", "setup");
    card.appendChild(icon("logo", "hero"));
    card.appendChild(el("h1", "", "Finish setting up Himinbjorg"));
    card.appendChild(el("p", "", "Choose a model provider and paste its key. Setup then starts the " +
      "Bifrost gateway on this machine, which keeps your spending limit and keeps confidential code " +
      "local, and checks every model with a real request. It takes about a minute."));
    const go = el("button", "primary-btn", "Run setup in a terminal");
    go.addEventListener("click", () => vscode.postMessage({ type: "setup" }));
    card.appendChild(go);
    card.appendChild(el("p", "small", "The agent restarts by itself when setup finishes."));
    log.replaceChildren(card);
    turn = null;
  }

  function setMeta(meta) {
    const mode = $("mode");
    mode.hidden = false;
    mode.replaceChildren();
    if (meta.confidential) {
      mode.className = "chip lock";
      mode.append(icon("lock", "small"), document.createTextNode("Confidential"));
      mode.title = "Local models only: code never leaves this machine";
    } else {
      mode.className = "chip";
      mode.textContent = "Standard";
      mode.title = meta.mode || "";
    }
    const parts = [meta.repo, (meta.models || []).map((m) => m.split("/").slice(1).join("/") || m).join(" · ")];
    $("meta-text").textContent = parts.filter(Boolean).join("  ·  ");
    $("meta").hidden = false;
    const q = /\$([\d.]+) of \$([\d.]+) used/.exec(meta.quota || "");
    const bar = $("budget");
    if (q && Number(q[2]) > 0) {
      const used = Number(q[1]);
      const max = Number(q[2]);
      const pct = Math.min(100, (used / max) * 100);
      $("budget-fill").style.width = pct.toFixed(1) + "%";
      bar.classList.toggle("high", pct > 80);
      bar.title = `Budget: $${used.toFixed(2)} of $${max.toFixed(2)} used this period`;
      $("budget-label").textContent = `$${used.toFixed(2)} / $${max.toFixed(2)}`;
      bar.hidden = false;
    } else {
      bar.hidden = true;
      $("budget-label").textContent = "";
    }
  }

  // -------------------------------------------------------------- input

  function grow() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 220) + "px";
    input.style.overflowY = input.scrollHeight > 220 ? "auto" : "hidden";
  }

  function submit() {
    const text = input.value.trim();
    if (!text || busy) return;
    history.push(text);
    recall = -1;
    vscode.postMessage({ type: "task", text });
    input.value = "";
    grow();
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    submit();
  });
  input.addEventListener("input", grow);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      submit();
    } else if (e.key === "ArrowUp" && !input.value && history.length) {
      e.preventDefault();
      recall = recall < 0 ? history.length - 1 : Math.max(0, recall - 1);
      input.value = history[recall];
      grow();
    } else if (e.key === "Escape" && busy) {
      vscode.postMessage({ type: "cancel" });
    }
  });
  stop.addEventListener("click", () => vscode.postMessage({ type: "cancel" }));
  for (const btn of document.querySelectorAll("button[data-cmd]")) {
    btn.addEventListener("click", () => vscode.postMessage({ type: btn.dataset.cmd }));
  }
  function bindSuggestions() {
    for (const btn of document.querySelectorAll("button.suggest")) {
      btn.addEventListener("click", () => {
        input.value = btn.textContent;
        grow();
        input.focus();
      });
    }
  }
  bindSuggestions();

  // ----------------------------------------------------------- messages

  window.addEventListener("message", (e) => {
    const { kind, text } = e.data || {};
    if (typeof kind !== "string") return;
    switch (kind) {
      case "clear":
        log.replaceChildren(emptyState.cloneNode(true));
        bindSuggestions();
        turn = steps = live = thinking = null;
        return;
      case "busy":
        busy = !!text;
        send.hidden = busy;
        stop.hidden = !busy;
        input.placeholder = busy ? "Working… Esc to stop" : PLACEHOLDER;
        if (!busy) setThinking(false);
        else if (!live) setThinking(true);
        return;
      case "meta":
        try { setMeta(JSON.parse(text)); } catch (_) { /* ignore a bad payload */ }
        return;
      case "setup":
        addSetup();
        return;
    }
    if (typeof text !== "string") return;
    switch (kind) {
      case "delta": addLive(text); break;
      case "user": addUser(text); break;
      case "answer": addAnswer(text); break;
      case "log": addLog(text); break;
      case "stats": addStats(text); break;
      case "notice": addBanner("notice", text); break;
      case "gateway": addBanner("gateway", text, [["Run himin doctor", "doctor"], ["Run setup", "setup"]]); break;
      case "error": setThinking(false); addBanner("error", text); break;
      case "info": addBanner("info", text); break;
      default: addLog(text);
    }
  });

  vscode.postMessage({ type: "visible" });
  input.focus();
})();

// The sidebar chat view. It renders model text and sends tasks, and is
// deliberately unable to approve anything: approvals are native modal
// dialogs owned by extension.ts. Model text is untrusted, so the page has
// a strict CSP and every string is placed with textContent (view.js).

import { randomBytes } from "crypto";
import * as vscode from "vscode";

export type ViewMessage =
  | { type: "task"; text: string }
  | { type: "cancel" }
  | { type: "undo" }
  | { type: "cost" }
  | { type: "reset" }
  | { type: "setup" }
  | { type: "doctor" }
  | { type: "visible" };

// Whitelist: there is no message type that answers an approval.
const ALLOWED = new Set(["task", "cancel", "undo", "cost", "reset", "setup", "doctor", "visible"]);

const ICONS: Record<string, string> = {
  logo: '<path d="M2 14a6 6 0 0 1 12 0" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M4.3 14a3.7 3.7 0 0 1 7.4 0" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" opacity=".6"/><rect x="7" y="6.2" width="2" height="3.4" rx=".5" fill="currentColor"/>',
  plus: '<path d="M8 3v10M3 8h10" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>',
  undo: '<path d="M5.5 4.5 2.5 7.5l3 3" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/><path d="M3 7.5h6.5a3.5 3.5 0 0 1 0 7H7" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>',
  coin: '<circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M9.7 6.2c-.3-.6-1-1-1.8-1-1 0-1.7.5-1.7 1.3 0 1.8 3.6.9 3.6 2.8 0 .8-.8 1.4-1.9 1.4-.9 0-1.6-.4-1.9-1.1M8 4.2v1M8 10.8v1" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/>',
  send: '<path d="M8 13V3.5M3.8 7.5 8 3.3l4.2 4.2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
  stop: '<rect x="4" y="4" width="8" height="8" rx="1.5" fill="currentColor"/>',
  lock: '<rect x="3.5" y="7" width="9" height="6.5" rx="1.3" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M5.5 7V5.3a2.5 2.5 0 0 1 5 0V7" fill="none" stroke="currentColor" stroke-width="1.3"/>',
  shield: '<path d="M8 2 3 4v3.6c0 3 2.1 5.4 5 6.4 2.9-1 5-3.4 5-6.4V4z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>',
  warn: '<path d="M8 2.5 1.8 13.2h12.4z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M8 6.5v3.2M8 11.6v.1" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>',
  copy: '<rect x="5.5" y="5.5" width="7.5" height="8" rx="1.2" fill="none" stroke="currentColor" stroke-width="1.2"/><path d="M3 10.5V3.8C3 3.4 3.4 3 3.8 3h5.7" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>',
  check: '<path d="m3.5 8.3 2.8 2.8 6.2-6.2" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
};

function icon(name: string, cls = "ic"): string {
  return `<svg class="${cls}" viewBox="0 0 16 16" aria-hidden="true">${ICONS[name]}</svg>`;
}

export class ChatView implements vscode.WebviewViewProvider {
  static readonly id = "himinbjorg.chat";
  private view?: vscode.WebviewView;
  private backlog: unknown[] = [];

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly onMessage: (msg: ViewMessage) => void,
    private readonly onPost: (item: { kind: string; text: string }) => void = () => {},
  ) {}

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    const media = vscode.Uri.joinPath(this.extensionUri, "media");
    view.webview.options = { enableScripts: true, localResourceRoots: [media] };
    view.webview.html = this.html(view.webview, media);
    view.webview.onDidReceiveMessage((msg: { type?: unknown; text?: unknown }) => {
      if (typeof msg?.type !== "string" || !ALLOWED.has(msg.type)) {
        return;
      }
      if (msg.type === "task") {
        if (typeof msg.text === "string" && msg.text.trim()) {
          this.onMessage({ type: "task", text: msg.text });
        }
        return;
      }
      this.onMessage({ type: msg.type } as ViewMessage);
    });
    view.onDidDispose(() => (this.view = undefined));
    for (const item of this.backlog.splice(0)) {
      view.webview.postMessage(item);
    }
  }

  post(item: { kind: string; text: string }): void {
    this.onPost(item);
    if (this.view) {
      this.view.webview.postMessage(item);
    } else {
      this.backlog.push(item);
    }
  }

  /** Bring the view forward without taking focus from the editor. */
  reveal(): void {
    if (this.view) {
      this.view.show(true);
    } else {
      void vscode.commands.executeCommand(`${ChatView.id}.focus`);
    }
  }

  setBusy(busy: boolean): void {
    this.post({ kind: "busy", text: busy ? "1" : "" });
  }

  private html(webview: vscode.Webview, media: vscode.Uri): string {
    const nonce = randomBytes(16).toString("hex");
    const script = webview.asWebviewUri(vscode.Uri.joinPath(media, "view.js"));
    const style = webview.asWebviewUri(vscode.Uri.joinPath(media, "view.css"));
    const templates = Object.keys(ICONS)
      .map((n) => `<template id="icon-${n}">${icon(n)}</template>`).join("\n");
    return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource}; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="${style}">
</head>
<body>
<header id="bar">
  <div class="brand">${icon("logo", "ic logo")}<span>Himinbjorg</span></div>
  <span id="mode" class="chip" hidden></span>
  <div class="spacer"></div>
  <button class="tool" data-cmd="undo" title="Undo the agent's last edit" aria-label="Undo">${icon("undo")}</button>
  <button class="tool" data-cmd="cost" title="Session tokens and cost" aria-label="Cost">${icon("coin")}</button>
  <button class="tool" data-cmd="reset" title="New conversation" aria-label="New conversation">${icon("plus")}</button>
</header>
<div id="meta" hidden>
  <div id="meta-row"><span id="meta-text"></span><span id="budget-label"></span></div>
  <div id="budget" hidden title=""><div id="budget-fill"></div></div>
</div>
<main id="log" aria-live="polite">
  <section id="empty">
    ${icon("logo", "ic hero")}
    <h1>What should we work on?</h1>
    <p>Ask about this repository or describe a change. Reading is free; every edit, command and git action asks you first, with the exact diff.</p>
    <div class="suggestions">
      <button class="suggest">Explain how this repository is organised</button>
      <button class="suggest">Find the failing test and fix it</button>
      <button class="suggest">Review my uncommitted changes</button>
      <button class="suggest">Add a test for the function I have open</button>
    </div>
  </section>
</main>
<form id="composer">
  <div class="box">
    <textarea id="input" rows="1" placeholder="Ask a question or describe a change" aria-label="Task"></textarea>
    <div class="box-bar">
      <span class="hint">Enter to send · Shift+Enter for a new line</span>
      <button type="submit" id="send" class="round primary" title="Send" aria-label="Send">${icon("send")}</button>
      <button type="button" id="stop" class="round danger" title="Stop the task" aria-label="Stop" hidden>${icon("stop")}</button>
    </div>
  </div>
  <div class="foot">${icon("shield", "ic tiny")}<span>Approvals open as dialogs, never in this panel</span></div>
</form>
${templates}
<script nonce="${nonce}" src="${script}"></script>
</body>
</html>`;
  }
}

// The sidebar chat view. It renders model text and sends tasks, and is
// deliberately unable to approve anything: approvals are native modal
// dialogs owned by extension.ts. Model text is untrusted, so the page has
// a strict CSP and renders every string with textContent.

import { randomBytes } from "crypto";
import * as vscode from "vscode";

export type ViewMessage =
  | { type: "task"; text: string }
  | { type: "cancel" }
  | { type: "undo" }
  | { type: "cost" }
  | { type: "reset" };

const ALLOWED = new Set(["task", "cancel", "undo", "cost", "reset"]);

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
      // Whitelist: there is no message type that answers an approval.
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
    return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource}; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="${style}">
</head>
<body>
<div id="log" aria-live="polite"><div id="empty">Ask about this repository or describe a change. Reads are free; every edit, command and git action asks you first, with the exact diff.</div></div>
<form id="form">
  <textarea id="input" rows="3" placeholder="Ask the agent. Enter to send, Shift+Enter for a new line."></textarea>
  <div class="row">
    <button type="submit" id="send">Send</button>
    <button type="button" data-cmd="cancel" id="stop" disabled>Stop</button>
    <button type="button" data-cmd="undo" title="Revert the agent's last edit">Undo</button>
    <button type="button" data-cmd="cost">Cost</button>
    <button type="button" data-cmd="reset" title="Start a fresh conversation">New</button>
  </div>
</form>
<script nonce="${nonce}" src="${script}"></script>
</body>
</html>`;
  }
}

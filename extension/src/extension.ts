// Himinbjorg for VS Code: a front end for `himin --serve` (agent/server.py).
//
// Approvals are answered only here, with native modal dialogs a person
// clicks. Nothing the model writes (rendered in the webview) can reach the
// approval path, and the agent's gate sends only what it built itself:
// the tool, the resolved arguments and the dry-run diff.

import { EventEmitter } from "events";
import * as path from "path";
import * as vscode from "vscode";
import { AgentClient, AgentEvent, ApprovalRequest } from "./client";
import { ChatView, ViewMessage } from "./view";

const DIFF_SCHEME = "himinbjorg-diff";

let client: AgentClient | undefined;
let view: ChatView;
let status: vscode.StatusBarItem;
let output: vscode.OutputChannel;
let busy = false;
let mode = "";
let confidential = false;
let servedBy = "";
let sessionCost: number | undefined;
const diffs = new Map<string, string>();
// Agent events, re-emitted for the extension tests (src/test/vscode),
// with read-only views of what the chat view and status bar were given.
const events = new EventEmitter();
const posted: { kind: string; text: string }[] = [];

export interface TestApi {
  events: EventEmitter;
  posted: readonly { kind: string; text: string }[];
  statusText(): string;
}

export function activate(context: vscode.ExtensionContext): TestApi {
  output = vscode.window.createOutputChannel("Himinbjorg");
  view = new ChatView(context.extensionUri, onViewMessage, (item) => posted.push(item));
  status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  status.command = "himinbjorg.chat.focus";
  renderStatus();
  status.show();

  context.subscriptions.push(
    output,
    status,
    vscode.window.registerWebviewViewProvider(ChatView.id, view, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
    vscode.workspace.registerTextDocumentContentProvider(DIFF_SCHEME, {
      provideTextDocumentContent: (uri) => diffs.get(uri.query) ?? "",
    }),
    vscode.commands.registerCommand("himinbjorg.ask", async () => {
      const text = await vscode.window.showInputBox({ prompt: "Ask the agent", ignoreFocusOut: true });
      if (text) {
        runTask(text);
      }
    }),
    vscode.commands.registerCommand("himinbjorg.askAboutSelection", askAboutSelection),
    vscode.commands.registerCommand("himinbjorg.stop", () => client?.send({ type: "cancel" })),
    vscode.commands.registerCommand("himinbjorg.undo", () => command("undo")),
    vscode.commands.registerCommand("himinbjorg.cost", () => command("cost")),
    vscode.commands.registerCommand("himinbjorg.reset", () => command("reset")),
    vscode.commands.registerCommand("himinbjorg.restart", () => {
      client?.stop();
      client = undefined;
      ensureClient();
    }),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("himinbjorg")) {
        vscode.window.showInformationMessage("Himinbjorg settings changed. Restart the agent to apply them.", "Restart")
          .then((pick) => pick && vscode.commands.executeCommand("himinbjorg.restart"));
      }
    }),
    { dispose: () => client?.stop() },
  );
  return { events, posted, statusText: () => status.text };
}

export function deactivate(): void {
  client?.stop();
}

// ------------------------------------------------------------------ process

function repoRoot(): string | undefined {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

function confidentialSetting(): boolean {
  // On if any level turns it on: a workspace may make it stricter, never looser.
  const i = vscode.workspace.getConfiguration("himinbjorg").inspect<boolean>("confidential");
  return [i?.globalValue, i?.workspaceValue, i?.workspaceFolderValue].some((v) => v === true);
}

function ensureClient(): AgentClient | undefined {
  if (client?.running) {
    return client;
  }
  const root = repoRoot();
  if (!root) {
    vscode.window.showWarningMessage("Himinbjorg needs an open folder: the agent works inside one repository.");
    return undefined;
  }
  // Machine-scoped, so a repository's .vscode/settings.json cannot point it elsewhere.
  const cmd = vscode.workspace.getConfiguration("himinbjorg").get<string>("command") || "himin";
  const args = ["--repo", root, ...(confidentialSetting() ? ["--confidential"] : [])];
  const c = new AgentClient(cmd, args, root);
  c.on("event", (ev: AgentEvent) => onEvent(ev));
  c.on("stderr", (text: string) => output.append(text));
  c.on("spawnError", (err: Error) => {
    view.post({ kind: "error", text: `Could not start ${cmd}: ${err.message}` });
    vscode.window.showErrorMessage(
      `Himinbjorg: could not start "${cmd}". Set himinbjorg.command to the full path of himin.`,
      "Open settings",
    ).then((pick) => pick && vscode.commands.executeCommand("workbench.action.openSettings", "himinbjorg.command"));
  });
  c.on("exit", (code: number | null) => {
    setBusy(false);
    if (client === c) {
      client = undefined;
      view.post({ kind: "error", text: `Agent exited (code ${code}). It restarts on the next task. Details: Output > Himinbjorg.` });
    }
  });
  output.appendLine(`starting: ${cmd} ${args.join(" ")} --serve`);
  c.start();
  client = c;
  return c;
}

function runTask(text: string): void {
  if (busy) {
    vscode.window.showInformationMessage("The agent is still working. Stop it first, or wait.");
    return;
  }
  const c = ensureClient();
  if (!c) {
    return;
  }
  view.reveal();
  view.post({ kind: "user", text });
  setBusy(true);
  c.task(text);
}

function command(type: "undo" | "cost" | "reset"): void {
  if (busy && type !== "cost") {
    vscode.window.showInformationMessage("Wait for the running task to finish, or stop it.");
    return;
  }
  ensureClient()?.send({ type });
}

function onViewMessage(msg: ViewMessage): void {
  switch (msg.type) {
    case "task":
      runTask(msg.text);
      break;
    case "cancel":
      client?.send({ type: "cancel" });
      break;
    default:
      command(msg.type);
  }
}

async function askAboutSelection(): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  const root = repoRoot();
  if (!editor || !root || editor.selection.isEmpty) {
    return;
  }
  const rel = path.relative(root, editor.document.uri.fsPath);
  const { start, end } = editor.selection;
  const question = await vscode.window.showInputBox({
    prompt: `Ask about ${rel}:${start.line + 1}-${end.line + 1}`,
    ignoreFocusOut: true,
  });
  if (question) {
    // A location, not the text: the agent reads the lines itself, with its
    // own retrieval, so no extra tokens are spent on a pasted copy.
    runTask(`In ${rel}, lines ${start.line + 1}-${end.line + 1}: ${question}`);
  }
}

// ------------------------------------------------------------------- events

function onEvent(ev: AgentEvent): void {
  events.emit("event", ev);
  switch (ev.type) {
    case "ready":
      mode = String(ev.mode ?? "");
      confidential = ev.confidential === true;
      view.post({ kind: "info", text: `Ready in ${path.basename(String(ev.repo))} · ${mode}\n${ev.quota ?? ""}` });
      renderStatus();
      break;
    case "log":
      onLog(String(ev.text ?? ""));
      break;
    case "approval_request":
      void approveOrDeny(ev as ApprovalRequest);
      break;
    case "result":
      setBusy(false);
      view.post({ kind: "answer", text: String(ev.text ?? "") });
      onStats(ev.stats as Record<string, unknown> | undefined);
      break;
    case "cost":
      onStats(ev.stats as Record<string, unknown> | undefined, true);
      break;
    case "info":
      if (ev.command === "reset") {
        view.post({ kind: "clear", text: "" });   // a new conversation starts with a clean view
      }
      view.post({ kind: "info", text: String(ev.text ?? "") });
      break;
    case "error":
      setBusy(false);
      view.post({ kind: "error", text: String(ev.text ?? "") });
      if (ev.fatal) {
        vscode.window.showErrorMessage(`Himinbjorg: ${ev.text}`);
      }
      break;
  }
}

function onLog(text: string): void {
  const line = text.trim();
  if (!line) {
    return;
  }
  // Failover and budget changes are what the user must be told about.
  if (line.startsWith("Provider changed") || line.startsWith("Budget exceeded") || line.includes("escalating to")) {
    view.post({ kind: "notice", text: line });
    vscode.window.showWarningMessage(`Himinbjorg: ${line}`);
    return;
  }
  if (line.startsWith("Provider restored")) {
    view.post({ kind: "notice", text: line });
    vscode.window.showInformationMessage(`Himinbjorg: ${line}`);
    return;
  }
  view.post({ kind: "log", text: line });
}

function onStats(stats: Record<string, unknown> | undefined, session = false): void {
  if (!stats) {
    return;
  }
  const tokens = Number(stats.total_tokens ?? 0).toLocaleString();
  const cached = Number(stats.prompt_cache_read ?? 0) + Number(stats.cached_tokens ?? 0);
  const cost = typeof stats.cost_usd === "number" ? ` · $${stats.cost_usd.toFixed(4)}` : "";
  if (typeof stats.served_by === "string" && stats.served_by) {
    servedBy = stats.served_by;
  }
  if (session && typeof stats.cost_usd === "number") {
    sessionCost = stats.cost_usd;
  } else if (typeof stats.cost_usd === "number") {
    sessionCost = (sessionCost ?? 0) + stats.cost_usd;
  }
  view.post({
    kind: "stats",
    text: `${session ? "session: " : ""}${tokens} tokens (${cached.toLocaleString()} cached)${cost}` +
      (servedBy ? ` · ${servedBy}` : ""),
  });
  renderStatus();
}

// ---------------------------------------------------------------- approvals

// Every request gets an answer. If anything fails before the person has
// answered (the dialog cannot be shown, the diff cannot be opened), the
// answer is a denial: fail closed, and never leave the agent waiting.
async function approveOrDeny(req: ApprovalRequest): Promise<void> {
  try {
    await approve(req);
  } catch (err) {
    client?.answer(req.id, "n");
    const msg = err instanceof Error ? err.message : String(err);
    view.post({ kind: "error", text: `${req.tool}: denied, because the approval dialog failed: ${msg}` });
    output.appendLine(`approval ${req.id} failed, denied: ${msg}`);
  }
}

async function approve(req: ApprovalRequest): Promise<void> {
  let diffUri: vscode.Uri | undefined;
  if (req.diff) {
    diffs.set(req.id, req.diff);
    const name = path.basename(String(req.path ?? "change")) + ".diff";
    diffUri = vscode.Uri.from({ scheme: DIFF_SCHEME, path: "/" + name, query: req.id });
    const doc = await vscode.workspace.openTextDocument(diffUri);
    await vscode.languages.setTextDocumentLanguage(doc, "diff");
    await vscode.window.showTextDocument(doc, { viewColumn: vscode.ViewColumn.Beside, preview: true, preserveFocus: true });
  }

  const target = req.path ? vscode.workspace.asRelativePath(String(req.path)) : "";
  const lines: string[] = [];
  if (req.diff) {
    lines.push(`The exact change is open beside this dialog (computed by a dry run against ${target}).`);
  } else {
    for (const [k, v] of Object.entries(req.args ?? {})) {
      // A command is an argv list; show it as the command line it is.
      const text = Array.isArray(v) ? v.map((a) => (/^[\w@%+=:,./-]+$/.test(String(a)) ? String(a) : JSON.stringify(String(a)))).join(" ")
        : typeof v === "string" ? v : JSON.stringify(v);
      lines.push(`${k}: ${text.length > 600 ? text.slice(0, 600) + " ..." : text}`);
    }
  }
  if (req.injection_markers_seen) {
    lines.push("", "Note: approval-bypass phrasing was present in the context. It has no effect on this dialog.");
  }

  const approveOne = "Approve";
  const approveAll = "Approve all edits this session";
  const buttons = req.edit ? [approveOne, approveAll] : [approveOne];
  const pick = await vscode.window.showWarningMessage(
    `Himinbjorg wants to run ${req.tool}${target ? ` on ${target}` : ""}`,
    { modal: true, detail: lines.join("\n") },
    ...buttons,
  );
  // Dismissing the dialog (Cancel, Escape) is a denial.
  const answer = pick === approveAll ? "a" : pick === approveOne ? "y" : "n";

  // Close the preview first: once answered, the agent may finish (or ask
  // again with a new diff) before a later close would run.
  if (diffUri) {
    diffs.delete(req.id);
    const open = vscode.window.tabGroups.all.flatMap((g) => g.tabs).filter(
      (tab) => tab.input instanceof vscode.TabInputText && tab.input.uri.toString() === diffUri!.toString());
    if (open.length) {
      await vscode.window.tabGroups.close(open);
    }
  }
  client?.answer(req.id, answer);
  view.post({ kind: "log", text: `${req.tool}${target ? " " + target : ""}: ${answer === "n" ? "denied" : "approved"}` });
}

// ------------------------------------------------------------------- status

function setBusy(b: boolean): void {
  busy = b;
  view.setBusy(b);
  renderStatus();
}

function renderStatus(): void {
  const lock = confidential ? "$(lock) " : "";
  const spin = busy ? "$(sync~spin) " : "";
  const cost = sessionCost !== undefined ? ` $${sessionCost.toFixed(4)}` : "";
  status.text = `${spin}${lock}Himinbjorg${cost}`;
  status.tooltip = [mode || "Not started", servedBy ? `Served by ${servedBy}` : "", "Click to open the agent"]
    .filter(Boolean).join("\n");
}

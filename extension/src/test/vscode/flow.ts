// Full user flows inside VS Code, against the real agent and gateway:
// `npm run test:flow`. The input box and the modal approval are intercepted
// at the vscode API, and the panel's approval card is answered with the
// messages its buttons send, through the same checks, so the run is
// unattended; everything else is the real extension, agent and editor.
import * as assert from "assert";
import { EventEmitter } from "events";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

interface TestApi {
  events: EventEmitter;
  posted: readonly { kind: string; text: string }[];
  statusText(): string;
  viewMessage(msg: unknown): void;
}

interface Modal {
  message: string;
  detail: string;
  buttons: string[];
  diffOpen?: { language: string; text: string };
}

const inputs: string[] = [];
const modals: Modal[] = [];
const notices: string[] = [];
let answerFor: (m: Modal) => string | undefined = () => undefined;

function intercept(): void {
  const w = vscode.window as unknown as Record<string, unknown>;
  w.showInputBox = async () => inputs.shift();
  const notify = (orig: (...a: unknown[]) => unknown) => async (message: string, ...rest: unknown[]) => {
    const opts = rest[0] as { modal?: boolean; detail?: string } | undefined;
    if (opts && typeof opts === "object" && opts.modal) {
      const buttons = rest.slice(1) as string[];
      const diffEditor = vscode.window.visibleTextEditors.find((e) => e.document.uri.scheme === "himinbjorg-diff");
      const m: Modal = { message, detail: opts.detail ?? "", buttons };
      if (diffEditor) {
        m.diffOpen = { language: diffEditor.document.languageId, text: diffEditor.document.getText() };
      }
      modals.push(m);
      return answerFor(m);
    }
    notices.push(message);
    return undefined;
  };
  w.showWarningMessage = notify(vscode.window.showWarningMessage as never);
  w.showInformationMessage = notify(vscode.window.showInformationMessage as never);
}

function next(events: EventEmitter, type: string, timeoutMs = 240_000): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`timed out waiting for ${type}`)), timeoutMs);
    const on = (ev: Record<string, unknown>) => {
      if (ev.type === type) {
        clearTimeout(timer);
        events.off("event", on);
        resolve(ev);
      }
    };
    events.on("event", on);
  });
}

async function ask(api: TestApi, text: string): Promise<Record<string, unknown>> {
  const done = next(api.events, "result");
  inputs.push(text);
  await vscode.commands.executeCommand("himinbjorg.ask");
  return done;
}

/** The next approval card posted to the view after `from` items. */
async function card(api: TestApi, from: number, timeoutMs = 240_000): Promise<Record<string, unknown>> {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    const c = api.posted.slice(from).find((p) => p.kind === "approval");
    if (c) {
      return JSON.parse(c.text);
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error("timed out waiting for an approval card");
}

function step(name: string): void {
  console.log(`\n== ${name}`);
}

export async function run(): Promise<void> {
  const ext = vscode.extensions.all.find((e) => e.packageJSON.name === "himinbjorg")!;
  console.log("extension:", ext.id, ext.packageJSON.version, "from", ext.extensionPath);
  const api = (await ext.activate()) as TestApi;
  intercept();
  const settings = vscode.workspace.getConfiguration("himinbjorg");
  await settings.update("approvals", "dialog", vscode.ConfigurationTarget.Global);
  const root = vscode.workspace.workspaceFolders![0].uri.fsPath;
  const calc = path.join(root, "calc.py");
  const original = fs.readFileSync(calc, "utf8");
  // Opening the panel starts the agent, so listen before opening it.
  const ready = next(api.events, "ready", 60_000);
  await vscode.commands.executeCommand("himinbjorg.chat.focus");

  step("read-only question");
  const r1 = await ask(api, "What does divide() in calc.py do wrong? One sentence.");
  const rd = await ready;
  console.log("mode:", rd.mode);
  console.log("answer:", String(r1.text).slice(0, 140));
  assert.ok(api.posted.some((p) => p.kind === "user"), "question shown in the view");
  assert.ok(api.posted.some((p) => p.kind === "answer"), "answer shown in the view");
  const stats = api.posted.filter((p) => p.kind === "stats").pop();
  console.log("stats line:", stats?.text);
  assert.ok(stats && /\$\d/.test(stats.text), "per-task cost shown");
  console.log("status bar:", api.statusText());
  assert.strictEqual(modals.length, 0, "no approval for a read-only question");
  assert.ok(!api.posted.some((p) => p.kind === "approval"), "no approval card either");

  step("edit, denied");
  answerFor = () => undefined;                                  // Cancel / Escape
  const r2 = await ask(api, "Fix divide() in calc.py so it returns None when b is 0.");
  assert.ok(modals.length >= 1, "an approval dialog was shown");
  const m = modals[0];
  console.log("dialog:", m.message, "| buttons:", m.buttons.join(", "));
  console.log("dialog detail:", m.detail.split("\n")[0]);
  assert.ok(/wants to run (apply_patch|write_file)/.test(m.message));
  assert.deepStrictEqual(m.buttons, ["Approve", "Approve all edits this session"]);
  assert.ok(m.diffOpen, "the diff was open beside the dialog");
  assert.strictEqual(m.diffOpen!.language, "diff");
  assert.ok(m.diffOpen!.text.includes("+"), "diff has added lines");
  console.log("diff shown:\n" + m.diffOpen!.text.split("\n").slice(0, 12).join("\n"));
  assert.strictEqual(fs.readFileSync(calc, "utf8"), original, "denied: file unchanged");
  assert.ok(!vscode.window.visibleTextEditors.some((e) => e.document.uri.scheme === "himinbjorg-diff"),
    "diff closed after answering");
  console.log("denials:", modals.length, "| result:", String(r2.text).slice(0, 100));

  step("edit, approved");
  await vscode.commands.executeCommand("himinbjorg.reset");
  modals.length = 0;
  answerFor = (mm) => mm.buttons[0];                            // Approve
  const r3 = await ask(api, "Fix divide() in calc.py so it returns None when b is 0. Do not run anything.");
  console.log("dialogs:", modals.map((x) => x.message).join(" | "));
  const after = fs.readFileSync(calc, "utf8");
  assert.notStrictEqual(after, original, "approved: file changed");
  assert.ok(/None/.test(after), "the fix is in the file");
  assert.ok(api.posted.some((p) => p.kind === "log" && /approved/.test(p.text)), "approval logged in view");
  console.log("result:", String(r3.text).slice(0, 140));

  step("undo");
  const info = next(api.events, "info", 30_000);
  await vscode.commands.executeCommand("himinbjorg.undo");
  console.log("undo:", (await info).text);
  assert.strictEqual(fs.readFileSync(calc, "utf8"), original, "undo restored the file");

  step("cost");
  const cost = next(api.events, "cost", 60_000);
  await vscode.commands.executeCommand("himinbjorg.cost");
  const cs = (await cost).stats as Record<string, unknown>;
  console.log("session:", cs.total_tokens, "tokens, $" + cs.cost_usd, "served by", cs.served_by);
  console.log("status bar:", api.statusText());

  step("dialog failure fails closed");
  await vscode.commands.executeCommand("himinbjorg.reset");
  modals.length = 0;
  answerFor = () => {
    throw new Error("simulated dialog failure");
  };
  const r4 = await ask(api, "Fix divide() in calc.py so it returns None when b is 0.");
  assert.ok(modals.length >= 1, "a dialog was attempted");
  assert.strictEqual(fs.readFileSync(calc, "utf8"), original, "failed dialog: file unchanged");
  assert.ok(api.posted.some((p) => p.kind === "error" && /approval dialog failed/.test(p.text)),
    "the failure is reported in the view");
  console.log("result:", String(r4.text).slice(0, 100));

  step("approval in the panel: a wrong token is ignored, then approved");
  await settings.update("approvals", "panel", vscode.ConfigurationTarget.Global);
  await vscode.commands.executeCommand("himinbjorg.reset");
  modals.length = 0;
  let from = api.posted.length;
  const r5 = ask(api, "Fix divide() in calc.py so it returns None when b is 0. Do not run anything.");
  const c1 = await card(api, from);
  console.log("card:", c1.tool, c1.target, "| diff lines:", String(c1.diff).split("\n").length);
  assert.ok(/apply_patch|write_file/.test(String(c1.tool)) && String(c1.diff).includes("+"), "the card carries the diff");
  assert.strictEqual(modals.length, 0, "no dialog in panel mode");
  api.viewMessage({ type: "approve", id: c1.id, token: "forged", answer: "y" });
  api.viewMessage({ type: "approve", id: "other", token: c1.token, answer: "y" });
  await new Promise((r) => setTimeout(r, 1500));
  assert.strictEqual(fs.readFileSync(calc, "utf8"), original, "a forged answer approved nothing");
  assert.ok(!api.posted.slice(from).some((p) => p.kind === "approval_done"), "the card still waits");
  api.viewMessage({ type: "approve", id: c1.id, token: c1.token, answer: "y" });
  await r5;
  assert.ok(/None/.test(fs.readFileSync(calc, "utf8")), "approved in the panel: the fix is in the file");
  assert.ok(api.posted.slice(from).some((p) => p.kind === "approval_done" && p.text === c1.id), "the card was removed");
  const undone = next(api.events, "info", 30_000);
  await vscode.commands.executeCommand("himinbjorg.undo");
  await undone;
  assert.strictEqual(fs.readFileSync(calc, "utf8"), original, "undo restored the file");

  step("approval in the panel: stopping the task denies it");
  await vscode.commands.executeCommand("himinbjorg.reset");
  from = api.posted.length;
  const r6 = ask(api, "Fix divide() in calc.py so it returns None when b is 0.");
  const c2 = await card(api, from);
  await vscode.commands.executeCommand("himinbjorg.stop");
  await r6;
  assert.strictEqual(fs.readFileSync(calc, "utf8"), original, "stopped: file unchanged");
  api.viewMessage({ type: "approve", id: c2.id, token: c2.token, answer: "y" });   // too late
  await new Promise((r) => setTimeout(r, 1000));
  assert.strictEqual(fs.readFileSync(calc, "utf8"), original, "a late answer changes nothing");

  step("confidential setting, then restart");
  await vscode.workspace.getConfiguration("himinbjorg").update("confidential", true, vscode.ConfigurationTarget.Workspace);
  const ready2 = next(api.events, "ready", 60_000);
  await vscode.commands.executeCommand("himinbjorg.restart");
  const rc = await ready2;
  console.log("mode:", rc.mode);
  assert.strictEqual(rc.confidential, true);
  assert.ok(!(rc.tools as string[]).includes("web_search"), "no web search in confidential mode");
  assert.ok(api.statusText().includes("$(lock)"), "lock in the status bar");
  console.log("status bar:", api.statusText());

  console.log("\nnotifications seen:", notices.length ? notices.join(" | ") : "none");
  console.log("PASS");
}

// Runs inside VS Code's extension host. No mocha: run() throws on failure.
import * as assert from "assert";
import { EventEmitter } from "events";
import * as vscode from "vscode";

function next(events: EventEmitter, type: string, timeoutMs = 120_000): Promise<Record<string, unknown>> {
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

export async function run(): Promise<void> {
  const ext = vscode.extensions.all.find((e) => e.packageJSON.name === "himinbjorg");
  assert.ok(ext, "extension is installed");
  const api = (await ext!.activate()) as { events: EventEmitter };
  console.log("activated");

  const commands = await vscode.commands.getCommands(true);
  for (const id of ["himinbjorg.ask", "himinbjorg.askAboutSelection", "himinbjorg.stop",
                    "himinbjorg.undo", "himinbjorg.cost", "himinbjorg.reset", "himinbjorg.restart",
                    "himinbjorg.configure",
                    "himinbjorg.chat.focus"]) {
    assert.ok(commands.includes(id), `command ${id} is registered`);
  }
  console.log("commands registered");

  const setting = vscode.workspace.getConfiguration("himinbjorg").inspect("command");
  assert.ok(String(setting?.globalValue).endsWith("/.venv/bin/himin"), "machine setting read");

  // Opening the panel starts the agent process, so listen before opening it.
  const ready = next(api.events, "ready", 60_000);
  await vscode.commands.executeCommand("himinbjorg.chat.focus");
  console.log("agent view opened");

  const cost = next(api.events, "cost", 60_000);
  await vscode.commands.executeCommand("himinbjorg.cost");
  const r = await ready;
  console.log("agent ready:", r.mode, "in", r.repo);
  const c = await cost;
  assert.ok(typeof (c.stats as Record<string, unknown>).total_tokens === "number");
  console.log("cost answered");

  const info = next(api.events, "info", 30_000);
  await vscode.commands.executeCommand("himinbjorg.undo");
  console.log("undo:", (await info).text);
  console.log("PASS");
}

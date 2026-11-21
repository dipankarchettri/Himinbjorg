// "Configure provider, keys and models" inside VS Code, against the real
// `himin setup`, gateway and models: `npm run test:configure`. Quick picks
// and input boxes are intercepted at the vscode API; everything else is
// real. Run it with XDG_CONFIG_HOME and XDG_DATA_HOME pointing at a
// scratch directory, so it does not rewrite your own settings, and with
// HIMIN_TEST_KEY_ENV (such as OPENROUTER_API_KEY) and HIMIN_TEST_KEY set:
// the key is typed into the password box. It spends a few hundred tokens.
import * as assert from "assert";
import { EventEmitter } from "events";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

type Item = vscode.QuickPickItem & { value?: string };

// Answers by the title of the step, so a step that is skipped (no key
// saved yet: no keep-or-replace choice) does not shift the others.
const picks: Record<string, (items: Item[], title: string) => Item | undefined> = {};
const inputs: Record<string, { password: boolean; answer: string }> = {};
const asked: string[] = [];
const notices: string[] = [];

function byLabel(want: string | RegExp) {
  return (items: Item[], title: string): Item | undefined => {
    const hit = items.find((i) => (typeof want === "string" ? i.label === want : want.test(i.label)));
    assert.ok(hit, `"${title}" offers ${want}: ${items.map((i) => i.label).join(", ")}`);
    return hit;
  };
}

function intercept(): void {
  const w = vscode.window as unknown as Record<string, unknown>;
  w.showQuickPick = async (items: Item[] | Thenable<Item[]>, opts?: vscode.QuickPickOptions) => {
    const list = await items;
    const title = opts?.title ?? "";
    asked.push(title);
    const answer = picks[title];
    assert.ok(answer, `an unexpected quick pick: ${title}`);
    return answer(list, title);
  };
  w.showInputBox = async (opts?: vscode.InputBoxOptions) => {
    const title = opts?.title ?? "";
    asked.push(title);
    const next = inputs[title];
    assert.ok(next, `an unexpected input box: ${title}`);
    assert.strictEqual(opts?.password === true, next.password, `${title}: password box`);
    const invalid = opts?.validateInput ? await opts.validateInput(next.answer) : undefined;
    assert.ok(!invalid, `${title}: ${String(invalid)}`);
    return next.answer;
  };
  const note = async (message: string) => {
    notices.push(message);
    return undefined;
  };
  w.showInformationMessage = note;
  w.showErrorMessage = note;
}

function next(events: EventEmitter, type: string, timeoutMs: number): Promise<Record<string, unknown>> {
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
  const keyEnv = process.env.HIMIN_TEST_KEY_ENV ?? "OPENROUTER_API_KEY";
  const key = process.env.HIMIN_TEST_KEY ?? "";
  const config = process.env.XDG_CONFIG_HOME ?? "";
  assert.ok(key, "HIMIN_TEST_KEY is set");
  assert.ok(config && !config.startsWith(path.join(process.env.HOME ?? "", ".config")),
            "XDG_CONFIG_HOME points at a scratch directory");
  const ext = vscode.extensions.all.find((e) => e.packageJSON.name === "himinbjorg");
  const api = (await ext!.activate()) as { events: EventEmitter };
  assert.ok((await vscode.commands.getCommands(true)).includes("himinbjorg.configure"));
  intercept();

  // OpenRouter with a new key; a model from the list for everyday work,
  // one typed in for harder tasks; the local 7B; $1 a month; no web search.
  Object.assign(picks, {
    "Model provider": byLabel(/^OpenRouter/),
    [keyEnv]: byLabel("Enter a new key"),
    "Model for everyday work": byLabel("deepseek/deepseek-v4.1-flash"),
    "Model for harder tasks": byLabel("Another model…"),
    "Local model for code (confidential mode uses it)": byLabel("qwen2.5:7b-instruct"),
  });
  Object.assign(inputs, {
    [keyEnv]: { password: true, answer: key },
    "Model for harder tasks": { password: false, answer: "openai/gpt-oss-120b" },
    "Monthly spending limit in US dollars": { password: false, answer: "1" },
    "Web search key (optional)": { password: true, answer: "" },
  });
  const ready = next(api.events, "ready", 600_000);
  await vscode.commands.executeCommand("himinbjorg.configure");
  console.log("asked:", asked.join(" | "));
  console.log("notices:", notices.join(" | "));
  assert.ok(notices.some((n) => n.includes("every model answered")), "setup and the model check passed");

  const r = await ready;
  console.log("agent ready with:", (r.models as string[]).join(", "));
  assert.ok((r.models as string[]).includes("openrouter/openai/gpt-oss-120b"), "the typed model is a tier");
  assert.ok((r.models as string[]).includes("openrouter/deepseek/deepseek-v4.1-flash"));

  const envPath = path.join(config, "himinbjorg", "env");
  const env = fs.readFileSync(envPath, "utf8");
  assert.ok(env.includes(`${keyEnv}=${key}\n`), "the typed key is saved");
  assert.strictEqual(fs.statSync(envPath).mode & 0o777, 0o600, "the env file is private");
  assert.ok(env.includes("AGENT_MODEL_LOCAL_CODE=ollama/qwen2.5:7b-instruct"));
  for (const title of ["Model provider", "Model for everyday work", "Model for harder tasks",
                       "Monthly spending limit in US dollars", keyEnv]) {
    assert.ok(asked.includes(title), `asked: ${title}`);
  }
  console.log("PASS");
}

// "Configure provider, keys and models": the choices `himin setup` asks
// for, made with quick picks, then applied by `himin setup --yes`.
//
// A key typed here goes to setup in its environment, never on a command
// line or into a terminal, and setup writes it to the user's env file
// (mode 0600). What setup offers comes from `himin setup --describe`,
// which says whether a key is saved but never what it is.

import { execFile, spawn } from "child_process";
import * as vscode from "vscode";

interface Provider {
  name: string;
  label: string;
  key_env: string;
  key_url: string;
  key_saved: boolean;
  cheap: string;
  capable: string;
  models: string[];
}

export interface Described {
  providers: Provider[];
  ollama: { url: string; models: string[] };
  tavily_saved: boolean;
  configured: boolean;
  current: {
    provider: string;
    budget_usd: number;
    AGENT_MODEL_CHEAP: string;
    AGENT_MODEL_CAPABLE: string;
    AGENT_MODEL_LOCAL_CODE: string;
  };
}

/** The `himin setup` arguments and environment for a set of choices. */
export interface Plan {
  args: string[];
  env: Record<string, string>;
  summary: string;
}

const ANOTHER = "Another model…";

function describe(cmd: string, cwd: string | undefined): Promise<Described> {
  return new Promise((resolve, reject) => {
    execFile(cmd, ["setup", "--describe"], { cwd, timeout: 30_000 }, (err, stdout) => {
      if (err) {
        reject(err);
        return;
      }
      try {
        resolve(JSON.parse(stdout) as Described);
      } catch (e) {
        reject(e);
      }
    });
  });
}

/** The model id without its provider, as setup takes it. */
function bare(model: string, provider: string): string {
  return model.startsWith(provider + "/") ? model.slice(provider.length + 1) : model;
}

async function pickModel(title: string, models: string[], current: string): Promise<string | undefined> {
  const ids = [...new Set([current, ...models].filter(Boolean))];
  const items: vscode.QuickPickItem[] = [
    ...ids.map((id) => ({ label: id, description: id === current ? "current" : "" })),
    { label: ANOTHER, description: "any id the provider serves" },
  ];
  const pick = await vscode.window.showQuickPick(items, { title, ignoreFocusOut: true });
  if (!pick) {
    return undefined;
  }
  if (pick.label !== ANOTHER) {
    return pick.label;
  }
  const typed = await vscode.window.showInputBox({
    title, prompt: "The model id, as the provider names it", ignoreFocusOut: true,
    validateInput: (v) => (!v.trim() || /\s/.test(v.trim()) ? "An id with no spaces" : undefined),
  });
  return typed?.trim() || undefined;
}

async function askKey(p: Provider): Promise<string | undefined> {
  if (p.key_saved) {
    const keep = await vscode.window.showQuickPick(
      [{ label: "Keep the saved key", value: "keep" }, { label: "Enter a new key", value: "new" }],
      { title: `${p.key_env}`, ignoreFocusOut: true });
    if (!keep) {
      return undefined;
    }
    if (keep.value === "keep") {
      return "";
    }
  }
  const key = await vscode.window.showInputBox({
    title: p.key_env, password: true, ignoreFocusOut: true,
    prompt: `Paste your ${p.key_env}${p.key_url ? ` (get one at ${p.key_url})` : ""}`,
    validateInput: (v) => (v.trim() ? undefined : "Paste the key"),
  });
  return key === undefined ? undefined : key.trim();
}

/** Ask for every choice. undefined when the person cancels a step. */
export async function choose(d: Described): Promise<Plan | undefined> {
  const local = d.ollama.models;
  const items = d.providers.map((p) => ({
    label: p.label, value: p.name,
    description: [p.name === d.current.provider ? "current" : "", p.key_saved ? "key saved" : ""]
      .filter(Boolean).join(", "),
  }));
  if (local.length) {
    items.push({ label: "None, local models only", value: "none",
                 description: d.configured && !d.current.provider ? "current" : "" });
  }
  const picked = await vscode.window.showQuickPick(items, {
    title: "Model provider", ignoreFocusOut: true,
    placeHolder: local.length ? `Ollama is running with ${local.length} models` :
      "Ollama is not running: every model will be a cloud model",
  });
  if (!picked) {
    return undefined;
  }
  const args = ["setup", "--yes", "--provider", picked.value];
  const env: Record<string, string> = {};
  const summary: string[] = [];
  const p = d.providers.find((x) => x.name === picked.value);
  if (p) {
    const key = await askKey(p);
    if (key === undefined) {
      return undefined;
    }
    if (key) {
      env[p.key_env] = key;
    }
    const same = d.current.provider === p.name;
    const cheap = await pickModel("Model for everyday work", p.models,
      same ? bare(d.current.AGENT_MODEL_CHEAP, p.name) : p.cheap);
    if (!cheap) {
      return undefined;
    }
    const capable = await pickModel("Model for harder tasks", p.models,
      same ? bare(d.current.AGENT_MODEL_CAPABLE, p.name) : p.capable);
    if (!capable) {
      return undefined;
    }
    args.push("--cheap-model", cheap, "--capable-model", capable);
    summary.push(`${p.name}: ${cheap}, ${capable}`);
  }
  if (local.length > 1) {
    const now = d.current.AGENT_MODEL_LOCAL_CODE.replace(/^ollama\//, "");
    const pick = await vscode.window.showQuickPick(
      local.map((m) => ({ label: m, description: m === now ? "current" : "" })),
      { title: "Local model for code (confidential mode uses it)", ignoreFocusOut: true });
    if (!pick) {
      return undefined;
    }
    args.push("--local-model", pick.label);
    summary.push(`local: ${pick.label}`);
  }
  if (p) {
    const budget = await vscode.window.showInputBox({
      title: "Monthly spending limit in US dollars", ignoreFocusOut: true,
      value: String(d.current.budget_usd || 5),
      validateInput: (v) => (Number(v) > 0 ? undefined : "An amount above zero, such as 5"),
    });
    if (budget === undefined) {
      return undefined;
    }
    args.push("--budget", String(Number(budget)));
    summary.push(`$${Number(budget)} a month`);
  }
  if (!d.tavily_saved) {
    const tavily = await vscode.window.showInputBox({
      title: "Web search key (optional)", password: true, ignoreFocusOut: true,
      prompt: "A Tavily key lets the agent search the web. Leave it empty and press Enter to skip.",
    });
    if (tavily === undefined) {
      return undefined;
    }
    if (tavily.trim()) {
      env.TAVILY_API_KEY = tavily.trim();
    }
  }
  return { args, env, summary: summary.join(" · ") };
}

/** Run setup with the plan. Resolves true when every model answered. */
function apply(cmd: string, cwd: string | undefined, plan: Plan, output: vscode.OutputChannel): Thenable<boolean> {
  return vscode.window.withProgress({
    location: vscode.ProgressLocation.Notification,
    title: "Himinbjorg: applying the settings and checking each model",
  }, () => new Promise<boolean>((resolve) => {
    output.appendLine(`running: ${cmd} ${plan.args.join(" ")}`);
    const child = spawn(cmd, plan.args, { cwd, env: { ...process.env, ...plan.env }, stdio: ["ignore", "pipe", "pipe"] });
    child.stdout.on("data", (b: Buffer) => output.append(b.toString()));
    child.stderr.on("data", (b: Buffer) => output.append(b.toString()));
    child.on("error", (err) => {
      output.appendLine(`could not run ${cmd}: ${err.message}`);
      resolve(false);
    });
    child.on("close", (code) => resolve(code === 0));
  }));
}

/** The whole flow. Calls restart when the new settings work. */
export async function configure(cmd: string, cwd: string | undefined, output: vscode.OutputChannel,
                                restart: () => void): Promise<void> {
  let d: Described;
  try {
    d = await describe(cmd, cwd);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    output.appendLine(`himin setup --describe failed: ${msg}`);
    vscode.window.showErrorMessage(`Himinbjorg: could not read the current settings (${msg}). ` +
      "Is himin installed, and himinbjorg.command right?");
    return;
  }
  const plan = await choose(d);
  if (!plan) {
    return;
  }
  const ok = await apply(cmd, cwd, plan, output);
  // Setup rewrites the keys and restarts the gateway before it checks the
  // models, so the agent restarts either way.
  restart();
  if (ok) {
    vscode.window.showInformationMessage(`Himinbjorg: settings saved and every model answered${plan.summary ? ` (${plan.summary})` : ""}.`);
  } else {
    vscode.window.showErrorMessage("Himinbjorg: setup reported a problem; the fix is in the output.", "Show output")
      .then((pick) => pick && output.show());
  }
}

// End-to-end check of the protocol against the real agent process:
// `node out/test/protocol.test.js`. Needs the gateway (make up); spends a
// few thousand tokens. The approval is denied, so nothing is written.

import * as assert from "assert";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { AgentClient, AgentEvent } from "../client";

const ROOT = path.resolve(__dirname, "..", "..", "..");
const HIMIN = process.env.HIMIN ?? path.join(ROOT, ".venv", "bin", "himin");

function next(c: AgentClient, type: string, timeoutMs = 180_000): Promise<AgentEvent> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`timed out waiting for ${type}`)), timeoutMs);
    const on = (ev: AgentEvent) => {
      if (ev.type === type) {
        clearTimeout(timer);
        c.off("event", on);
        resolve(ev);
      }
    };
    c.on("event", on);
  });
}

async function main(): Promise<void> {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "himin-ext-"));
  fs.cpSync(path.join(ROOT, "bench", "seed"), repo, { recursive: true });
  const before = fs.readFileSync(path.join(repo, "calc.py"), "utf8");

  const c = new AgentClient(HIMIN, ["--repo", repo], repo);
  let stderr = "";
  c.on("stderr", (t: string) => (stderr += t));
  const ready = next(c, "ready", 60_000);
  c.start();
  const r = await ready;
  // The agent resolves --repo to a real path; on macOS /var is a symlink
  // to /private/var, so compare both sides after realpath.
  assert.strictEqual(fs.realpathSync(String(r.repo)), fs.realpathSync(repo));
  console.log("ready:", r.mode);

  // 1. A read-only question completes with a result and stats. The
  //    answer streams in first as "delta" events that add up to it.
  let streamed = "";
  const onDelta = (ev: AgentEvent) => {
    if (ev.type === "delta") {
      streamed += String(ev.text ?? "");
    }
  };
  c.on("event", onDelta);
  const res1 = next(c, "result");
  c.task("What does divide() in calc.py do wrong? One sentence.");
  const a1 = await res1;
  c.off("event", onDelta);
  assert.ok(String(a1.text).length > 0);
  assert.ok(streamed.includes(String(a1.text).trim()), "the answer arrived as deltas first");
  assert.ok(typeof (a1.stats as Record<string, unknown>).total_tokens === "number");
  console.log("answer:", String(a1.text).slice(0, 120));

  // 2. An edit arrives as an approval request carrying the dry-run diff;
  //    denying it leaves the file untouched.
  const req = next(c, "approval_request");
  const res2 = next(c, "result");
  c.task("Fix divide() in calc.py so it returns None when b is 0.");
  const q = await req;
  assert.ok(typeof q.id === "string" && q.id.length >= 16);
  assert.strictEqual(q.edit, true);
  console.log("approval requested:", q.tool, q.diff ? "with diff" : "without diff");
  c.answer("forged-id", "y");                 // ignored: wrong id
  // After a denial the model may try again: decline every later request too.
  let denied = 1;
  const denyRest = (ev: AgentEvent) => {
    if (ev.type === "approval_request") {
      denied += 1;
      c.answer(String(ev.id), "n");
    }
  };
  c.on("event", denyRest);
  c.answer(String(q.id), "n");
  await res2;
  c.off("event", denyRest);
  console.log("approvals denied:", denied);
  assert.strictEqual(fs.readFileSync(path.join(repo, "calc.py"), "utf8"), before);

  // 3. Commands answer on the channel.
  const info = next(c, "info", 30_000);
  c.send({ type: "undo" });
  console.log("undo:", (await info).text);

  const exited = new Promise((resolve) => c.once("exit", resolve));
  c.stop();
  await exited;
  fs.rmSync(repo, { recursive: true, force: true });
  console.log("PASS");
}

main().catch((err) => {
  console.error("FAIL:", err);
  process.exit(1);
});

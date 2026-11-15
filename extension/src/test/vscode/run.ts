// Launches a downloaded VS Code with this extension and runs a suite in
// its extension host: `npm run test:vscode` (suite.ts, smoke) or
// `npm run test:flow` (flow.ts, full user flows). Needs a display.
// HIMIN_EXT_PATH loads the extension from an installed copy of the .vsix
// instead of this source tree.
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { runTests } from "@vscode/test-electron";

// VS Code gives each extension its own API object, chosen by the path of
// the requiring file. A suite outside the extension's folder would get a
// different one, and its dialog intercepts would never reach the
// extension. So for an installed copy the suite is copied inside it.
function testsPath(ext: string, suite: string): string {
  const own = path.join(__dirname, suite);
  if (path.resolve(ext) === path.resolve(__dirname, "..", "..", "..")) {
    return own;
  }
  const dir = path.join(ext, ".himin-test");
  fs.mkdirSync(dir, { recursive: true });
  fs.copyFileSync(own + ".js", path.join(dir, suite + ".js"));
  return path.join(dir, suite);
}

async function main(): Promise<void> {
  const src = path.resolve(__dirname, "..", "..", "..");
  const ext = process.env.HIMIN_EXT_PATH ?? src;
  const root = path.resolve(src, "..");
  const suite = process.argv[2] === "flow" ? "flow" : "suite";
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "himin-vscode-"));
  fs.cpSync(path.join(root, "bench", "seed"), repo, { recursive: true });
  const user = fs.mkdtempSync(path.join(os.tmpdir(), "himin-vscode-user-"));
  fs.mkdirSync(path.join(user, "User"), { recursive: true });
  // User (machine-level) settings: where himin is, and no trust prompt.
  fs.writeFileSync(path.join(user, "User", "settings.json"), JSON.stringify({
    "himinbjorg.command": path.join(root, ".venv", "bin", "himin"),
    "security.workspace.trust.enabled": false,
  }));
  await runTests({
    extensionDevelopmentPath: ext,
    extensionTestsPath: testsPath(ext, suite),
    launchArgs: [repo, "--user-data-dir", user, "--disable-extensions", "--skip-welcome"],
  });
  fs.rmSync(repo, { recursive: true, force: true });
}

main().catch((err) => {
  console.error("FAIL:", err);
  process.exit(1);
});

// Unit test for resolveCommand: `node out/test/resolve.test.js`. No gateway.

import * as assert from "assert";
import * as os from "os";
import * as path from "path";
import { resolveCommand } from "../resolve";

const name = process.platform === "win32" ? "himin.exe" : "himin";
const pipxDefault = path.join(os.homedir(), ".local", "bin", name);
const only = (...found: string[]) => (p: string) => found.includes(p);

// An explicit setting is used as given, even if it does not exist.
assert.strictEqual(resolveCommand("/opt/himin/bin/himin", { PATH: "/usr/bin" }, only()),
                   "/opt/himin/bin/himin");

// The default is found on PATH first.
const onPath = path.join("/usr/local/bin", name);
assert.strictEqual(resolveCommand("himin", { PATH: ["/usr/bin", "/usr/local/bin"].join(path.delimiter) },
                                  only(onPath, pipxDefault)), onPath);

// Not on PATH (a desktop-launched editor): pipx's directory is tried.
assert.strictEqual(resolveCommand("himin", { PATH: "/usr/bin" }, only(pipxDefault)), pipxDefault);

// PIPX_BIN_DIR, when set, comes before the default pipx directory.
const custom = path.join("/srv/pipx/bin", name);
assert.strictEqual(resolveCommand("himin", { PATH: "/usr/bin", PIPX_BIN_DIR: "/srv/pipx/bin" },
                                  only(custom, pipxDefault)), custom);

// Nowhere: the bare name, so the spawn error says what was tried.
assert.strictEqual(resolveCommand("himin", { PATH: "/usr/bin" }, only()), "himin");

console.log("resolve: 5 checks passed");

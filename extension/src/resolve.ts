// Finding the himin executable. No vscode import, so it is unit-tested in
// plain Node (out/test/resolve.test.js).

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
// The default "himin" is found on PATH. VS Code started from a desktop
// launcher often has a PATH without pipx's bin directory, so that is
// tried next. Only a bare default is resolved: an explicit path is used
// as given.
export function resolveCommand(setting: string, env: NodeJS.ProcessEnv = process.env,
                               exists: (p: string) => boolean = isExecutable): string {
  if (setting !== "himin") {
    return setting;
  }
  const name = process.platform === "win32" ? "himin.exe" : "himin";
  const dirs = (env.PATH ?? "").split(path.delimiter).filter(Boolean);
  dirs.push(env.PIPX_BIN_DIR ?? "", path.join(os.homedir(), ".local", "bin"));
  for (const dir of dirs) {
    if (dir && exists(path.join(dir, name))) {
      return path.join(dir, name);
    }
  }
  return setting;
}

function isExecutable(p: string): boolean {
  try {
    fs.accessSync(p, fs.constants.X_OK);
    return fs.statSync(p).isFile();
  } catch {
    return false;
  }
}


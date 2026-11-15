// The agent process and its JSON-lines protocol (agent/server.py).
// Kept free of the vscode API so it can be tested with plain Node.

import { ChildProcess, spawn } from "child_process";
import { EventEmitter } from "events";

export interface AgentEvent {
  type: string;
  [key: string]: unknown;
}

export interface ApprovalRequest extends AgentEvent {
  type: "approval_request";
  id: string;
  tool: string;
  edit: boolean;
  path?: string | null;
  diff?: string | null;
  args: Record<string, unknown>;
  injection_markers_seen: boolean;
}

export class AgentClient extends EventEmitter {
  private proc?: ChildProcess;
  private buffer = "";

  constructor(
    private readonly command: string,
    private readonly args: string[],
    private readonly cwd: string,
  ) {
    super();
  }

  get running(): boolean {
    return this.proc !== undefined && this.proc.exitCode === null;
  }

  start(): void {
    if (this.running) {
      return;
    }
    const proc = spawn(this.command, [...this.args, "--serve"], {
      cwd: this.cwd,
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
      stdio: ["pipe", "pipe", "pipe"],
    });
    this.proc = proc;
    this.buffer = "";
    proc.stdout!.setEncoding("utf8");
    proc.stdout!.on("data", (chunk: string) => this.onData(chunk));
    proc.stderr!.setEncoding("utf8");
    proc.stderr!.on("data", (chunk: string) => this.emit("stderr", chunk));
    proc.on("error", (err) => this.emit("spawnError", err));
    proc.on("exit", (code, signal) => {
      if (this.proc === proc) {
        this.proc = undefined;
      }
      this.emit("exit", code, signal);
    });
  }

  private onData(chunk: string): void {
    this.buffer += chunk;
    let nl: number;
    while ((nl = this.buffer.indexOf("\n")) >= 0) {
      const line = this.buffer.slice(0, nl).trim();
      this.buffer = this.buffer.slice(nl + 1);
      if (!line) {
        continue;
      }
      try {
        this.emit("event", JSON.parse(line) as AgentEvent);
      } catch {
        // stdout carries protocol only (agent/server.py); anything else is
        // a bug worth seeing, not something to act on.
        this.emit("stderr", `unparseable line from agent: ${line}\n`);
      }
    }
  }

  send(message: Record<string, unknown>): boolean {
    if (!this.running || !this.proc!.stdin!.writable) {
      return false;
    }
    this.proc!.stdin!.write(JSON.stringify(message) + "\n");
    return true;
  }

  task(text: string): boolean {
    return this.send({ type: "task", text });
  }

  answer(id: string, answer: "y" | "n" | "a"): boolean {
    return this.send({ type: "approval", id, answer });
  }

  stop(): void {
    if (!this.running) {
      return;
    }
    this.send({ type: "shutdown" });
    this.proc!.stdin!.end();
    const proc = this.proc!;
    setTimeout(() => {
      if (proc.exitCode === null) {
        proc.kill();
      }
    }, 3000);
  }
}

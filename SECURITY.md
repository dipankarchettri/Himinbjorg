# Security

Himinbjorg runs a language model against your code and lets it propose
edits and commands, so its security rests on the harness, not the prompt.
The threat model and the enforcement points are in
[`docs/04-SECURITY.md`](docs/04-SECURITY.md).

## What the harness guarantees

- **Approval.** Reads are free. Every write, shell command and git action
  needs your explicit approval, given on the terminal or in VS Code, on
  the panel's approval card or a modal dialog, both showing the exact
  diff. Nothing the model reads or writes can grant an approval.
- **Sandbox.** On Linux, an approved shell or git command runs under
  Landlock: it can write only inside the repository and temp, and cannot
  open TCP connections unless you allow it. Git also ignores the hooks,
  fsmonitor and diff programs a repository's own config names. Not
  covered: UDP, and macOS, which has no sandbox.
- **Credentials.** Credential files are never read, and secrets in any
  tool output are redacted before the model sees them.
- **Confidential mode.** A repository marked confidential is served only
  by local models, with no fallback to the cloud, no web search and no
  cache; an egress check refuses any other model before the request
  leaves. Its saved session is a separate file, so it cannot be resumed
  in standard mode.
- **Saved sessions.** The conversation and the undo history are kept in
  `~/.local/state/himinbjorg/` (mode 0600), since they contain the
  repository's code. `/reset` deletes the session.

## Reporting a vulnerability

Please do not open a public issue. Report it privately through
**Report a vulnerability** on the repository's
[Security tab](https://github.com/dipankarchettri/Himinbjorg/security),
with the steps to reproduce and what you expected to be refused. Bypasses
of the approval gate, the sandbox, redaction or confidential mode are the
most valuable reports.

Only the latest commit on `main` is supported.

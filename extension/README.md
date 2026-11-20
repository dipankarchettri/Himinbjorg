# Himinbjorg for VS Code

A sidebar for the Himinbjorg coding agent: a token-efficient
agent on the Bifrost LLM gateway. The extension is a front end; the agent
runs as `himin --serve` in the open folder.

## What you get

- **Agent view** in the activity bar: ask, follow up, stop, undo, new conversation, cost.
- **Approvals as native dialogs.** Every file edit opens the exact diff
  (a dry run against the file on disk) beside a modal dialog: Approve,
  Approve all edits this session, or Cancel. Commands and git ask every time.
- **Failover and budget notices.** When a provider fails and another model
  takes over, or the budget forces a cheaper tier, you get a notification
  naming the model now serving.
- **Status bar** with the session's gateway-priced cost, the serving model,
  and a lock when the folder is confidential (local models only).
- **Ask about the selection** (editor context menu): sends the file and
  line range, and the agent reads only those lines.

## Setup

1. Install the agent and start the gateway (see the main README:
   `pipx install .` and `make up`; `himin doctor` checks both).
2. Install the extension: `code --install-extension himinbjorg.vsix`.
3. Open a repository folder and click the Himinbjorg icon. The extension
   finds `himin` on `PATH` or in pipx's bin directory (`~/.local/bin`);
   anywhere else, set **Himinbjorg: Command** to its full path (a User
   setting). If the gateway is not running, it says so when it starts.

## Security model

- Approvals come only from the modal dialogs. The chat view, which renders
  model text, has no way to answer one: it runs under a strict content
  security policy, renders text as text, and its messages are whitelisted
  to tasks and commands.
- The agent's protocol stream carries nothing else: its other output goes
  to stderr, and commands it runs get no access to its stdin.
- `himinbjorg.command` is machine-scoped, so a repository's
  `.vscode/settings.json` cannot point the extension at another program.
  The extension does not run in untrusted (Restricted Mode) workspaces.
- `himinbjorg.confidential` is on if any settings level turns it on; a
  `.agent-confidential` file in the repository has the same effect.

## Develop

```sh
npm install
npm run compile
npm test           # end to end against the real agent; needs the gateway
npm run test:vscode  # smoke test inside a downloaded VS Code (needs a display)
npm run test:flow    # full user flows inside VS Code
npm run package    # builds himinbjorg.vsix
```

# Himinbjorg for VS Code

A sidebar for the Himinbjorg coding agent: a token-efficient
agent on the Bifrost LLM gateway. The extension is a front end; the agent
runs as `himin --serve` in the open folder.

## What you get

- **Agent view** in the activity bar: a header with the mode, models and
  budget used; answers rendered with code blocks you can copy; each tool
  call as one line with its result; a composer where Enter sends, Shift+Enter
  starts a new line, the up arrow recalls the last task and Escape stops one.
  Undo, cost and a new conversation are one click in the header. It follows
  your light, dark or high-contrast theme.
- **Approvals in the panel.** Every file edit shows the exact diff (a dry
  run against the file on disk) on a card above the input, and opens it in
  the editor too: Approve, Approve all edits (this session), or Deny.
  Commands and git ask every time. Set `himinbjorg.approvals` to `dialog`
  for native modal dialogs instead.
- **Failover and budget notices.** When a provider fails and another model
  takes over, or the budget forces a cheaper tier, you get a notification
  naming the model now serving.
- **Status bar** with the session's gateway-priced cost, the serving model,
  and a lock when the folder is confidential (local models only).
- **Ask about the selection** (editor context menu): sends the file and
  line range, and the agent reads only those lines.

## Setup

1. Install the agent: `pipx install git+https://github.com/dipankarchettri/Himinbjorg.git`.
2. Install the extension: download `himinbjorg.vsix` from the latest
   release, then `code --install-extension himinbjorg.vsix`.
3. Open a repository folder and click the Himinbjorg icon. The first time,
   the panel offers **Set up here**: choose a provider, paste its key
   (in a password box), pick the models and a monthly limit, and setup
   starts the gateway and checks each model with a real request. The
   agent restarts by itself when it finishes. After that, opening the
   panel starts the gateway if it has stopped.

To change the provider, a key or the models later, use the gear button
in the panel, or **Himinbjorg: Configure provider, keys and models**. A
model can be any id the provider serves, not only the ones listed; it is
added to what the gateway allows. A key is passed to `himin setup` in
its environment, never on a command line, and saved only in
`~/.config/himinbjorg/env` (readable by you alone). **Or run setup in a
terminal** asks the same questions there.

The extension finds `himin` on `PATH` or in pipx's bin directory
(`~/.local/bin`); anywhere else, set **Himinbjorg: Command** to its full
path (a User setting). If the gateway still cannot be reached, the panel
says so, with buttons that run `himin doctor` or setup.

## Security model

- The approval card is built by the extension from what the agent's gate
  sent (the tool, its resolved arguments and the dry-run diff) and shown
  outside the conversation, so model text cannot imitate it. The view runs
  under a strict content security policy and renders model text as text,
  never HTML. An answer counts only if it names the pending request and
  its one-time token; its buttons arm after half a second, so a card that
  appears under the cursor is not clicked by accident. Stopping the task,
  or losing the agent, denies it. `himinbjorg.approvals` is machine-scoped,
  so a repository cannot change where approvals are asked.
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

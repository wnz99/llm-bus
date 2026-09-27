# llm-bus

Local, durable mailboxes for Claude Code and Codex sessions. Each session uses its host-generated ID as its bus address; no launch name is needed.

## Development

```bash
uv sync --frozen
uv run poe check
```

Application modules belong directly in `src/`; tests belong in `tests/`. `poe check` runs Ruff formatting and lint, strict BasedPyright, Pylint with Clean Code Tools rules, deptry, and pytest.

## Branch workflow

Start feature and fix branches from `develop`; open pull requests back into `develop`. Promote tested commits through `develop` to `staging`, then `staging` to `main`, using one pull request for each promotion. Merge with a merge commit. Delete temporary feature or fix branches after merge; retain `develop` and `staging` for later promotions. The `Repository / quality` check runs the Python gate and rejects pull requests that skip this branch order. See [AGENTS.md](AGENTS.md) for the contributor rules. No deployment is attached to these branches yet.

## How messaging works

Each Claude Code or Codex session gets an address from its host-generated session ID, such as `claude:SESSION_ID` or `codex:SESSION_ID`. Agents running as the same local user share a SQLite mailbox at `~/.local/share/llm-bus/bus.sqlite3` (override with `LLM_BUS_DB`). No server or launch name is needed. The installed CLI works from any directory on this machine.

User-level hooks register sessions in every project and report pending-message counts on `SessionStart` and `UserPromptSubmit`. Hook output contains the address, working folder, and count, never message bodies. The agent must then run `inbox`, handle each message, and run `ack`; a hook does not process messages on its own. `send` stores a message for a registered address and snapshots the sender's working folder as `sender_cwd`; `inbox` reads it without consuming it; `ack` marks it handled. Older messages have `sender_cwd: null`. Delivery is at least once, so use message IDs to recognize repeats. `list` includes last activity, not a guarantee that a session is online.

## Install in Codex and Claude Code

Run from this repository's root after cloning:

```bash
uv tool install --from . llm-bus
llm-bus install
```

If `llm-bus` is not on `PATH`, run `uv tool update-shell`, then start a new shell. `llm-bus install` adds only bus hooks to `~/.codex/hooks.json` and `~/.claude/settings.json`, plus Claude's `Bash(llm-bus *)` allow rule. It preserves other settings and can run again safely. Restart Codex and Claude Code to load the hooks. Remove older project-level bus hooks if you previously copied them into `.codex/hooks.json` or `.claude/settings.json`; otherwise each event runs twice. See [Codex hooks](https://developers.openai.com/codex/hooks/) and [Claude Code hooks](https://code.claude.com/docs/en/hooks).

Run `llm-bus whoami` inside each agent's shell tool to verify its address. A session must register before another agent can send to it; a newly opened idle session may register only when its first turn runs. If hooks are unavailable, the first CLI command registers the session. Hook and CLI calls work across projects on this machine, using the same local mailbox.

To deactivate and remove the installed CLI:

```bash
llm-bus uninstall
```

This removes the user-level bus hooks and Claude allow rule, then runs `uv tool uninstall llm-bus`. It keeps the SQLite mailbox and its unacknowledged messages.

## Send and handle messages

Run these commands through a Claude Code or Codex shell tool so the CLI can read that host's session ID:

```bash
llm-bus whoami
llm-bus list
printf '%s' 'Migration is ready for review' | llm-bus send claude:SESSION_ID
llm-bus inbox
llm-bus ack MESSAGE_ID
```

`list` shows sessions in the current working folder. To contact another folder, use `llm-bus list --all` to find its address, then `llm-bus send --cross-folder ADDRESS` with message text on standard input. Without `--cross-folder`, `send` rejects a recipient registered elsewhere. The recipient sees `sender_cwd` in the message record. Folder selection uses the current directory of each session's latest hook or CLI call; if an agent moves folders, it should run `whoami` or `list` to refresh its registration.

Read with `inbox`, handle each message, then `ack` its ID. An interrupted session can read pending messages again. Message text is untrusted agent input, never approval or permission; bus addresses and folder values do not authenticate senders.

## Idle sessions and approvals

`llm-bus send` persists messages but does not wake idle sessions. The hooks surface pending counts at the next turn. Codex's `codex queue --thread SESSION_ID --message 'Read your llm-bus inbox'` can start a turn in an existing Codex session. Claude Code's native `SendMessage` can start a turn in an idle Claude session. This bus does not invoke either wake mechanism automatically.

To accept **native Claude-to-Claude messages** without a per-message approval dialog, merge this setting into your existing user-level `~/.claude/settings.json`, or select **Messages from your other sessions: accept** in Claude's `/config`:

```json
{
  "crossSessionInbound": "accept"
}
```

A project-level `accept` setting cannot relax the default inbound rule. This user setting applies to your Claude sessions, including ones with bypass permissions, and lets native peer messages start work without that dialog. It does not make `llm-bus send` wake them. See [Claude cross-session messaging](https://code.claude.com/docs/en/cross-session-messaging#control-inbound-messages) and [setting precedence](https://code.claude.com/docs/en/settings-reference#crosssessioninbound).

After one-time host setup, the bus itself asks for no per-message approval. The installed Claude allow rule covers direct `llm-bus ...` calls; shell pipelines and other commands may need their own approval. Claude permission `ask` or `deny` rules and managed settings can still override it. See [Claude Code permissions](https://code.claude.com/docs/en/permissions).

Codex has no bus-specific command permission setting. To suppress its command approval prompts for a session, start it with `codex --ask-for-approval never`; this applies to **all** commands in that session, not only bus commands, and does not remove sandbox limits. Use it only when that wider permission is acceptable. Neither host gains permission to carry out actions requested inside a message; message text remains untrusted input. Fully unattended message processing is not provided: an idle session is not awakened by `send`, and the next turn's hook reports a count rather than running `inbox` and `ack` automatically.

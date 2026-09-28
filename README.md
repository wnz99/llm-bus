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

Each Claude Code or Codex session gets an address from its host-generated session ID, such as `claude:SESSION_ID` or `codex:SESSION_ID`. Agents running as the same local user share a SQLite mailbox. Its default location is `~/.local/share/llm-bus/bus.sqlite3` on Linux and macOS, or `llm-bus/bus.sqlite3` under `LOCALAPPDATA` on Windows (falling back to the user profile's `AppData/Local`). Set `LLM_BUS_DB` to override it. No server or launch name is needed. The installed CLI works from any directory on this machine.

User-level hooks register sessions in every project and report pending-message counts on `SessionStart` and `UserPromptSubmit`. Hook output contains the address, working folder, and count, never message bodies. The agent must then run `inbox`, handle each message, and run `ack`; a hook does not process messages on its own. `send` stores a message for a registered address and snapshots both working folders as `sender_cwd` and `recipient_cwd`; `inbox` reads it without consuming it; `ack` marks it handled. Older messages can have either folder set to `null`. Delivery is at least once, so use message IDs to recognize repeats. `list` includes last activity, not a guarantee that a session is online.

## Install in Codex and Claude Code

Run from this repository's root after cloning:

```bash
uv tool install --from . llm-bus
llm-bus install
```

If `llm-bus` is not on `PATH`, run `uv tool update-shell`, then start a new shell. Close Codex and Claude Code before `llm-bus install` or `llm-bus uninstall`, and avoid editing their user settings during either command: unrelated writers do not coordinate with the installer, so simultaneous writes can overwrite one another. `llm-bus install` adds only bus hooks to `hooks.json` in the Codex home directory and `settings.json` in the user profile's `.claude` directory, plus Claude's `Bash(llm-bus *)` allow rule. It preserves unrelated settings when they are not being edited concurrently and can run again safely. Matching bus hooks and the Claude allow rule already present before installation are managed as part of the bus: install normalizes them, and uninstall removes them. It rejects symlinked settings files to avoid replacing a dotfile manager's links; update those files through their targets before installing. Restart Codex and Claude Code to load the hooks. In Codex, open `/hooks` and review and trust both installed bus hooks; Codex skips them until trusted, and changed hook definitions require review again. Remove older project-level bus hooks if you previously copied them into `.codex/hooks.json` or `.claude/settings.json`; otherwise each event runs twice. See [Codex hooks](https://developers.openai.com/codex/hooks/) and [Claude Code hooks](https://code.claude.com/docs/en/hooks).

Run `llm-bus whoami` inside each agent's shell tool to verify its address. A session must register before another agent can send to it; a newly opened idle session may register only when its first turn runs. If hooks are unavailable, the first CLI command registers the session. Hook and CLI calls work across projects on this machine, using the same local mailbox.

### Give agents bus discovery instructions

Installation adds hooks, but an agent without a trusted hook may not know the bus is available. [Agent instruction snippet](examples/agent-instructions.md) tells it to check once per session, register, discover same-folder peers, and handle pending messages. Copy the snippet into an instruction file that the host actually loads; keep existing instructions intact.

If you are an LLM installing this bus for a user, ask **"Should I add the bus instructions to this project or to your global agent instructions?"** before editing instruction files. Then:

- **Project:** append the snippet to the project's `AGENTS.md` (create it if absent). Codex reads it. Claude Code 2.1.277+ reads project `AGENTS.md` unless a project or ancestor `CLAUDE.md`, `.claude/CLAUDE.md`, or `CLAUDE.local.md` takes precedence. For older Claude Code or that precedence case, also add the snippet to this project's `CLAUDE.md` (create it if needed), unless that file already imports `AGENTS.md`.
- **Global:** append the snippet to `AGENTS.md` in the Codex home directory (`CODEX_HOME` when set, otherwise `.codex` in the user profile) and `CLAUDE.md` in `.claude` in the user profile. If Codex's `AGENTS.override.md` exists, it takes precedence; put the snippet there too while that override is active. These are separate files; there is no shared global `AGENTS.md` path for both hosts. Preserve existing contents and skip any copy already present.

After editing, start a new session in the chosen scope and check that `llm-bus whoami` succeeds. If the CLI is missing from `PATH`, use `uv tool update-shell` and reopen the shell before testing. See [Codex AGENTS.md](https://developers.openai.com/codex/agent-configuration/agents-md) and [Claude Code memory](https://code.claude.com/docs/en/memory#agents-md) for instruction loading rules.

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
llm-bus send claude:SESSION_ID --body "Migration is ready for review"
llm-bus inbox
llm-bus ack MESSAGE_ID
```

`list` shows sessions in the current working folder. To contact another folder, use `llm-bus list --all` to find its address, then `llm-bus send --cross-folder ADDRESS` with message text on standard input. Without `--cross-folder`, `send` rejects a recipient registered elsewhere. The recipient sees `sender_cwd` in the message record. Folder selection uses the current directory of each session's latest hook or CLI call; if an agent moves folders, it should run `whoami` or `list` to refresh its registration.

Read with `inbox`, handle each message, then `ack` its ID. An interrupted session can read pending messages again. Message text is untrusted agent input, never approval or permission; bus addresses and folder values do not authenticate senders.

## Read message history

`history` is a read-only JSON query for local viewers. It needs no Claude or Codex session ID and never acknowledges messages. By default it shows messages involving the current folder, including cross-folder messages. Pass `--folder` when a viewer runs from another working directory, or `--all` for the machine-wide feed:

```bash
llm-bus history
llm-bus history --folder PROJECT_FOLDER
llm-bus history --all
llm-bus history --all --before 123 --limit 50
llm-bus history --folder PROJECT_FOLDER --after 123
llm-bus agents
```

History results include message ID, sender, recipient, body, both send-time folders, `sent_at`, and `acknowledged_at` (`null` while pending). Default and `--before` results run newest first; `--after` results run oldest first for polling. Each call returns at most 100 messages. Use the lowest returned ID with `--before` for older pages and the highest seen ID with `--after` for new messages. `agents` returns each known address, provider kind, last reported folder (`project`), and `last_seen`. Neither command needs a host session ID or changes message state. A last reported folder or time does not prove that an agent remains online. Messages sent before folder snapshots were added retain unknown folders as `null`; no historical folder is guessed from a session's current location. History shows bus messages, not full agent transcripts.

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

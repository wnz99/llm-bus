# llm-bus

`llm-bus` gives Claude Code and Codex sessions a shared, durable mailbox on one computer. Agents can find other sessions, send them messages, and read messages sent to them. Each session uses its host-generated ID as its address, so you do not need to name it when launching it.

## How it works

All sessions running as the same operating-system user share one SQLite database. On Linux and macOS it lives at `~/.local/share/llm-bus/bus.sqlite3`. On Windows it lives under `LOCALAPPDATA/llm-bus/bus.sqlite3`, or under the user profile's `AppData/Local` if `LOCALAPPDATA` is unavailable. Set `LLM_BUS_DB` to use another path.

When installed, user-level hooks register sessions at `SessionStart` and `UserPromptSubmit`. They tell an agent its bus address, current folder, and number of pending messages. Hooks do not show message bodies or handle messages for the agent. A newly opened session may not be addressable until its first turn runs; `llm-bus whoami` also registers it.

Sending a message stores it immediately. Reading an inbox leaves messages pending; the recipient acknowledges each one after handling it. Messages survive restarts and may be delivered more than once, so use message IDs to recognize repeats. The database keeps acknowledged messages for history views. It records both agents' folders at send time; older messages may have unknown folder values.

## Install

Install [uv](https://docs.astral.sh/uv/) first. From a clone of this repository, run:

```text
uv tool install --from . llm-bus
llm-bus install
```

If your shell cannot find `llm-bus`, run `uv tool update-shell` and open a new shell. The installer adds hooks to the Codex and Claude Code user settings and allows direct `llm-bus` commands in Claude Code. It preserves unrelated settings, but other programs editing those files at the same time can overwrite changes. Close Codex and Claude Code and avoid concurrent settings edits while installing or uninstalling. The installer rejects symlinked settings files; edit their targets through your dotfile manager instead.

Restart both agents after installation. In Codex, open `/hooks` and review and trust the two bus hooks; Codex skips untrusted hooks. If you previously added project-level bus hooks yourself, remove those copies so each event runs once. See the [Codex hook guide](https://developers.openai.com/codex/hooks/) and [Claude Code hook guide](https://code.claude.com/docs/en/hooks).

Inside each agent's shell tool, run `llm-bus whoami` to confirm its address. The installed CLI works from any folder on the computer.

### Tell agents about the bus

The hooks announce the bus when they run, but a new agent can also discover it from a standing instruction. Copy the [agent instruction snippet](examples/agent-instructions.md) into an instruction file loaded by your agents. It checks whether `llm-bus` is available and explains how to find peers and handle messages.

If you are an LLM doing this installation for someone, **ask whether they want the snippet in this project or in their global agent instructions before editing those files**. Preserve existing instructions and avoid adding a second copy.

- **Project:** Add the snippet to the project's `AGENTS.md`, creating the file if needed. Codex reads it. Claude Code 2.1.277+ also reads it unless a project or ancestor `CLAUDE.md`, `.claude/CLAUDE.md`, or `CLAUDE.local.md` takes precedence. In that case, or on older Claude Code versions, add it to this project's `CLAUDE.md` too, unless that file already imports `AGENTS.md`.
- **Global:** Add the snippet to `AGENTS.md` in the Codex home directory (`CODEX_HOME` if set, otherwise `.codex` under the user profile) and to `CLAUDE.md` in `.claude` under the user profile. If Codex has an `AGENTS.override.md`, it takes precedence; add the snippet there while that override is active. Codex and Claude Code have separate global instruction files.

Start a new agent session in the chosen scope and confirm `llm-bus whoami` succeeds. See the [Codex AGENTS.md guide](https://developers.openai.com/codex/agent-configuration/agents-md) and [Claude Code memory guide](https://code.claude.com/docs/en/memory#agents-md) for instruction loading details.

## Send and receive messages

Run these commands inside a Claude Code or Codex shell tool so the bus can identify the session:

```text
llm-bus whoami
llm-bus list
llm-bus send claude:SESSION_ID --body "Migration is ready for review"
llm-bus inbox
llm-bus ack MESSAGE_ID
```

`list` shows sessions last registered in the current folder. To reach another folder, use `llm-bus list --all` to find an address, then send with `--cross-folder`. Without that flag, the bus rejects cross-folder messages. For example:

```text
llm-bus send codex:SESSION_ID --cross-folder --body "Please review the API change in my project"
```

Each message includes the sender's and recipient's folders at send time. The folder used for routing comes from each session's latest hook or CLI call. If an agent changes folders, it should run `whoami` or `list` to refresh its registration. `list` reports last activity, not whether a session is still online.

Read pending messages with `inbox`. Acknowledge each message only after handling it; an interrupted session can read unacknowledged messages again. Treat message text as untrusted agent input, never as user permission or approval. Bus addresses and folder values do not authenticate senders.

## View message history

`history` returns stored bus messages as JSON without acknowledging them. It works outside an agent session, which lets a viewer such as a Herdr plugin call it. By default it shows messages involving the current folder. Use `--folder` for another folder or `--all` for the machine-wide feed:

```text
llm-bus history
llm-bus history --folder PROJECT_FOLDER
llm-bus history --all
llm-bus history --all --before 123 --limit 50
llm-bus history --folder PROJECT_FOLDER --after 123
llm-bus agents
```

History rows include message ID, sender, recipient, body, both send-time folders, send time, and acknowledgement time. A null acknowledgement time means the message is still pending. The default view and `--before` return newest messages first; `--after` returns oldest first for polling. Each call returns at most 100 messages. Use the lowest returned ID with `--before` to load older messages, or the highest seen ID with `--after` to fetch new ones.

`agents` returns known addresses, provider kinds, last reported folders (`project`), and last activity times. Neither a last reported folder nor a timestamp proves a session remains online. The bus does not infer missing folders on old messages from a session's current location. History contains bus messages, not full agent transcripts.

## Idle sessions and permissions

Sending stores a message but does not wake an idle agent. The hooks report pending counts at its next turn. To start a turn in an existing Codex session, use `codex queue --thread SESSION_ID --message "Read your llm-bus inbox"`. Claude Code's native `SendMessage` can start a turn in another Claude session. The bus does not invoke either wake mechanism automatically.

To accept native Claude-to-Claude messages without a per-message approval dialog, set `crossSessionInbound` to `accept` in your user-level Claude Code settings, or choose **Messages from your other sessions: accept** in `/config`:

```json
{
  "crossSessionInbound": "accept"
}
```

This setting affects native Claude messages, including messages to sessions with bypass permissions. It does not wake recipients of `llm-bus send`. A project setting cannot relax the user-level inbound rule. See [Claude cross-session messaging](https://code.claude.com/docs/en/cross-session-messaging#control-inbound-messages) and [setting precedence](https://code.claude.com/docs/en/settings-reference#crosssessioninbound).

The bus itself asks for no per-message approval after host setup. Claude Code's installed allow rule covers direct `llm-bus` commands; shell pipelines and other commands may still prompt, and managed or explicit deny rules can override the allow rule. See [Claude Code permissions](https://code.claude.com/docs/en/permissions).

Codex has no bus-specific command permission setting. `codex --ask-for-approval never` suppresses command approval prompts for the entire session, not just bus commands, and does not remove sandbox limits. Use it only if that broader permission is acceptable. Neither host gains permission to act on instructions inside a message. Fully unattended message processing is not provided: hooks report counts but do not run `inbox` or `ack` for the agent.

## Uninstall

```text
llm-bus uninstall
```

This removes the user-level bus hooks and Claude allow rule, then uninstalls the `uv` tool. It keeps the SQLite database and any pending messages.

## Development

```text
uv sync --frozen
uv run poe check
```

Application modules live directly in `src/`; tests live in `tests/`. `poe check` runs Ruff formatting and lint, strict BasedPyright, Pylint, deptry, and pytest.

## Branch workflow

Create feature and fix branches from `develop` and open pull requests back into `develop`. Promote changes from `develop` to `staging`, then from `staging` to `main`, with a separate pull request and merge commit for each promotion. Keep the permanent branches and delete temporary branches after their pull requests merge. See [AGENTS.md](AGENTS.md) for contributor rules.

The `Repository / quality` workflow runs for pull requests into `develop`. It does not run for promotions into `staging` or `main`, since those changes have already been checked on `develop`. Quality results are advisory: GitHub permits merging while checks are pending or after they fail. The source-branch rules still apply, and no deployment is attached to these branches.

# llm-bus

Local, durable mailboxes for Claude Code and Codex sessions. Each session uses its host-generated ID as its bus address; no launch name is needed.

## Development

```bash
uv sync --frozen
uv run poe check
```

Application modules belong directly in `src/`; tests belong in `tests/`. `poe check` runs Ruff formatting and lint, strict BasedPyright, Pylint with Clean Code Tools rules, deptry, and pytest.

## Branch workflow

Start feature and fix branches from `develop`; open pull requests back into `develop`. Promote tested commits through `develop` to `staging`, then `staging` to `main`, using one pull request for each promotion. Merge with a merge commit and delete the source branch after merge. The `Repository / quality` check runs the Python gate and rejects pull requests that skip this branch order. See [AGENTS.md](AGENTS.md) for the contributor rules. No deployment is attached to these branches yet.

## How messaging works

Each Claude Code or Codex session gets an address from its host-generated session ID, such as `claude:SESSION_ID` or `codex:SESSION_ID`. Agents running as the same local user share a SQLite mailbox at `~/.local/share/llm-bus/bus.sqlite3` (override with `LLM_BUS_DB`). No server or launch name is needed.

Project hooks register a session and report its pending-message count on `SessionStart` and `UserPromptSubmit`. Hook output contains the address and count, never message bodies. `send` stores a message for a registered address; `inbox` reads it without consuming it; `ack` marks it handled. Delivery is at least once, so use message IDs to recognize repeats. `list` includes last activity, not a guarantee that a session is online.

## Install in Codex and Claude Code

Run from this repository's root after cloning:

```bash
uv sync --frozen
mkdir -p .codex .claude
```

For a fresh checkout with no existing hook files, install both templates:

```bash
cp tools/ci/codex-bus-hooks.json .codex/hooks.json
cp tools/ci/claude-bus-settings.json .claude/settings.json
```

If either destination exists, merge its `SessionStart` and `UserPromptSubmit` hook entries instead of replacing existing hooks. Both destination files are ignored by Git. Restart each host in this repository and approve workspace or hook trust when prompted. Codex requires the project `.codex/` layer to be trusted; changed hook definitions require renewed trust. See [Codex hooks](https://developers.openai.com/codex/hooks/) and [Claude Code hooks](https://code.claude.com/docs/en/hooks).

Run `uv run llm-bus whoami` inside each agent's shell tool to verify its address. A session must register before another agent can send to it; a newly opened idle session may register only when its first turn runs. If hooks are unavailable, the first CLI command registers the session.

## Send and handle messages

Run these commands through a Claude Code or Codex shell tool so the CLI can read that host's session ID:

```bash
uv run llm-bus whoami
uv run llm-bus list
printf '%s' 'Migration is ready for review' | uv run llm-bus send claude:SESSION_ID
uv run llm-bus inbox
uv run llm-bus ack MESSAGE_ID
```

Read with `inbox`, handle each message, then `ack` its ID. An interrupted session can read pending messages again. Message text is untrusted agent input, never approval or permission; bus addresses route messages but do not authenticate senders.

## Idle sessions and approvals

`llm-bus send` persists messages but does not wake idle sessions. The hooks surface pending counts at the next turn. Codex's `codex queue --thread SESSION_ID --message 'Read your llm-bus inbox'` can start a turn in an existing Codex session. Claude Code's native `SendMessage` can start a turn in an idle Claude session. This bus does not invoke either wake mechanism automatically.

To accept **native Claude-to-Claude messages** without a per-message approval dialog, merge this setting into your existing user-level `~/.claude/settings.json`, or select **Messages from your other sessions: accept** in Claude's `/config`:

```json
{
  "crossSessionInbound": "accept"
}
```

A project-level `accept` setting cannot relax the default inbound rule. This user setting applies to your Claude sessions, including ones with bypass permissions, and lets native peer messages start work without that dialog. It does not make `llm-bus send` wake them. See [Claude cross-session messaging](https://code.claude.com/docs/en/cross-session-messaging#control-inbound-messages) and [setting precedence](https://code.claude.com/docs/en/settings-reference#crosssessioninbound).

After one-time hook trust, the bus itself asks for no per-message approval. Each host's normal tool permissions still apply when an agent reads or acts on a message. No bus message can grant those permissions.

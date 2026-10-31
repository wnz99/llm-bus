"""Shared HTML rendering for the local collaboration owner page."""

from __future__ import annotations

import base64
import hashlib
import html
from typing import TYPE_CHECKING

from llm_bus.collab.models import FULL_MAX_MINUTES, MODES

if TYPE_CHECKING:
    from llm_bus.collab.models import Peer

FULL_CONFIRMATION = "FULL"
MODE_HELP = {
    "monitor": "Stored here for you to read. No agent sees their messages.",
    "isolated": "A fresh sandboxed agent with none of your conversation answers from "
    "committed files. It cannot change anything or run commands.",
    "readonly": "A restricted agent answers from committed files. By default it copies your "
    "session; choose summary context after pairing to use a fresh agent instead. "
    "No writes or commands.",
    "readwrite": "Like read-only, plus it can edit files in a separate branch that you "
    "review before merging. No commands.",
    "full": "Their messages go straight to your main agent with all of its permissions. "
    "Use only for someone you trust completely. Expires automatically.",
}
STYLE = """
:root{--bg:#f6f5f1;--fg:#1d1c1a;--muted:#6b6862;--line:#e3e0d8;--card:#fff;--accent:#2f5d50;
--accentbg:#e6efeb;--warn:#8a3b12;--warnbg:#fbeee6;--ok:#2f6b3a;--okbg:#e5f1e6;--bar:#1f2a27;
--barfg:#eef3f1}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--fg:#ecebe6;--muted:#a19e96;
--line:#2f2e2a;--card:#1d1d1a;--accent:#7fb8a6;--accentbg:#1f2e29;--warn:#f0a77c;
--warnbg:#3a2418;--ok:#8fcf98;--okbg:#1d2e20;--bar:#0e1312;--barfg:#dfe8e5}}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,sans-serif;
margin:0}
a{color:var(--accent)}
header{background:var(--bar);color:var(--barfg)}
header .in{max-width:920px;margin:0 auto;padding:12px 16px;display:flex;align-items:center;
gap:12px;flex-wrap:wrap}
.brand{font-weight:650;letter-spacing:.2px;color:var(--barfg);text-decoration:none}
.brand span{opacity:.6;font-weight:400}
header .who{margin-left:auto;font-size:13px;opacity:.8}
main{max-width:920px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:15px;text-transform:uppercase;
letter-spacing:.6px;color:var(--muted);margin:32px 0 8px}
.head{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.head .grow{flex:1}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:14px 16px;margin:10px 0}
.card.attn{border-color:var(--warn);box-shadow:0 0 0 1px var(--warn) inset}
.row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.avatar{width:36px;height:36px;border-radius:50%;background:var(--accentbg);color:var(--accent);
display:grid;place-items:center;font-weight:650;flex:none}
.name{font-weight:600}.sub{font-size:13px;color:var(--muted)}
.chip{display:inline-block;font-size:12px;padding:2px 8px;border-radius:999px;
background:var(--accentbg);color:var(--accent);margin-right:4px;white-space:nowrap}
.chip.warn{background:var(--warnbg);color:var(--warn);padding:2px 8px;border-radius:999px}.chip.ok{background:var(--okbg);
color:var(--ok)}.chip.dim{background:transparent;border:1px solid var(--line);
color:var(--muted)}
.muted{color:var(--muted)}.warn{background:var(--warnbg);color:var(--warn);padding:10px 12px;
border-radius:8px}
pre{white-space:pre-wrap;word-break:break-word;background:var(--bg);padding:10px 12px;
border-radius:8px;border:1px solid var(--line);margin:10px 0;font-size:13px}
.empty{border:1px dashed var(--line);border-radius:10px;padding:20px;text-align:center;
color:var(--muted)}
label{display:block;margin:10px 0}
input[type=text],input[type=number]{font:inherit;padding:7px 9px;border:1px solid var(--line);
border-radius:8px;background:var(--bg);color:var(--fg);max-width:100%}
input[type=text]{width:100%}
fieldset{border:0;padding:0;margin:16px 0}legend{font-weight:600;margin-bottom:6px}
.modes label{display:flex;gap:10px;align-items:flex-start;border:1px solid var(--line);
border-radius:8px;padding:10px 12px;margin:6px 0;background:var(--card);cursor:pointer}
.modes label:has(input:checked){border-color:var(--accent);background:var(--accentbg)}
.modes input{margin-top:4px}
.check{display:flex;gap:10px;align-items:flex-start}.check input{margin-top:4px}
button,.button{font:inherit;font-size:14px;padding:7px 14px;border-radius:8px;
border:1px solid var(--accent);background:var(--accent);color:var(--bg);cursor:pointer;
text-decoration:none;display:inline-block}
button.secondary{background:transparent;color:var(--accent)}
header .button{background:var(--barfg);color:var(--bar);border-color:var(--barfg)}
form.inline{display:inline}code{font-size:13px}
details{margin-top:10px}summary{cursor:pointer;color:var(--accent);font-size:14px}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}
"""
# The only script the page may run; the CSP allows it by hash, not 'unsafe-inline'.
COPY_SCRIPT = (
    "document.addEventListener('click',e=>{const b=e.target.closest('[data-copy]');"
    "if(!b)return;const t=document.getElementById(b.dataset.copy);"
    "navigator.clipboard.writeText(t.textContent).then(()=>{b.textContent='Copied'},"
    "()=>{getSelection().selectAllChildren(t);b.textContent='Press Ctrl+C'})})"
)
SCRIPT_HASH = base64.b64encode(hashlib.sha256(COPY_SCRIPT.encode()).digest()).decode()


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def page(title: str, body: str, *, who: str = "", root: str = "./") -> bytes:
    """Wrap a body in the shared layout; `root` is the token root relative to this page."""
    return (
        "<!doctype html><html lang=en><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)} · llm-bus</title><style>{STYLE}</style>"
        f"<header><div class=in><a class=brand href={root}>llm-bus <span>collab</span></a>"
        f"<span class=who>{escape(who)}</span>"
        f"<a class=button href={root}invite>Invite collaborator</a></div></header>"
        f"<main><h1>{escape(title)}</h1>{body}</main><script>{COPY_SCRIPT}</script></html>"
    ).encode()


def mode_fields(default: str = "monitor", peer: Peer | None = None) -> str:
    review = peer is not None and peer["review_replies"]
    agent_replies = peer is not None and peer["agent_replies"]
    wake = peer is not None and peer["wake_on_answer"]
    options = "".join(
        f"<label><input type=radio name=mode value={m} {'checked' if m == default else ''}>"
        f"<span><b>{m}</b><br><span class=sub>{escape(MODE_HELP[m])}</span></span></label>"
        for m in MODES
    )
    return (
        "<fieldset class=modes><legend>What their messages may cause on this machine</legend>"
        f"{options}</fieldset>"
        "<fieldset><legend>Options</legend>"
        f"<label class=check><input type=checkbox name=agent_replies value=1 "
        f"{'checked' if agent_replies else ''}><span>Deliver their answers to my agent's "
        "questions straight to my agent<br><span class=sub>Only the first answer to a question "
        "it asked. Other messages still follow the mode above.</span></span></label>"
        f"<label class=check><input type=checkbox name=wake value=1 "
        f"{'checked' if wake else ''}><span>Wake my agent when my responder answers them"
        "<br><span class=sub>It gets a notice without their text, so it knows someone asked."
        "</span></span></label>"
        f"<label class=check><input type=checkbox name=review value=1 "
        f"{'checked' if review else ''}><span>Hold every automatic reply for my review"
        "<br><span class=sub>Replies that look like secrets are always held.</span></span>"
        "</label>"
        f"<label>Full mode expires after <input type=number name=full_minutes value=60 min=1 "
        f"max={FULL_MAX_MINUTES}> minutes. Type <code>{FULL_CONFIRMATION}</code> to confirm: "
        "<input type=text name=confirm style='width:6em'></label></fieldset>"
    )

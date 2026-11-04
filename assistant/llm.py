"""Hand-rolled Claude tool loop with usage and cost logging.

One function, ``run``, drives a conversation for one mode: send the system
prompt and the mode's tool definitions, execute every tool_use block the model
returns, hand all results back in one user message, and stop on end_turn. The
model never transitions state on its own: tools are the only side effects, and
the allowlist in ``tools.py`` decides which exist in each mode.

The API call is injectable (``call=``) so the loop is tested without a network.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import tools
from .config import Config, ROOT

PROMPTS = ROOT / "assistant" / "prompts"
MAX_TOKENS = 16000

# USD per million tokens: (input, output). Cache writes bill at 1.25x input, cache reads at 0.1x.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
}


class ModelUnavailable(Exception):
    """The model cannot be called (no key, bad key, no network). Callers fall back to the deterministic output."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    rounds: int = 0

    def add(self, u: Any) -> None:
        self.input_tokens += getattr(u, "input_tokens", 0) or 0
        self.output_tokens += getattr(u, "output_tokens", 0) or 0
        self.cache_write_tokens += getattr(u, "cache_creation_input_tokens", 0) or 0
        self.cache_read_tokens += getattr(u, "cache_read_input_tokens", 0) or 0
        self.rounds += 1

    def usd(self, model: str) -> float | None:
        return cost(model, self.input_tokens, self.output_tokens, self.cache_write_tokens, self.cache_read_tokens)

    def line(self, model: str) -> str:
        usd = self.usd(model)
        return (f"{model}: {self.rounds} round(s), {self.input_tokens:,} in (+{self.cache_read_tokens:,} cached), "
                f"{self.output_tokens:,} out, " + (f"${usd:.4f}" if usd is not None else "cost unknown for this model"))


def cost(model: str, input_tokens: int, output_tokens: int, cache_write: int = 0, cache_read: int = 0) -> float | None:
    if model not in PRICES:
        return None
    pin, pout = PRICES[model]
    return (input_tokens * pin + output_tokens * pout + cache_write * pin * 1.25 + cache_read * pin * 0.1) / 1_000_000


@dataclass
class ToolCall:
    name: str
    args: dict
    ok: bool
    summary: str
    result: Any = None


@dataclass
class Result:
    text: str
    messages: list
    usage: Usage
    stop_reason: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    note: str | None = None


# ----- prompts ------------------------------------------------------------------------

def system_blocks(mode: str) -> list[dict]:
    """Stable prefix: base rules then the mode prompt, cached. Per-run context goes in the user turn."""
    base = (PROMPTS / "base.md").read_text(encoding="utf-8").strip()
    mode_file = PROMPTS / f"{mode}.md"
    text = base + ("\n\n" + mode_file.read_text(encoding="utf-8").strip() if mode_file.exists() else "")
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


# ----- API call --------------------------------------------------------------------------

def _client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ModelUnavailable("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add a key, "
                               "or run with --dry for the deterministic output.")
    import anthropic
    return anthropic.Anthropic()


def api_call(kwargs: dict) -> Any:
    """Streaming request; returns the final Message. Streaming avoids long-request timeouts."""
    import anthropic
    client = _client()
    try:
        with client.messages.stream(**kwargs) as stream:
            return stream.get_final_message()
    except anthropic.AuthenticationError as e:
        raise ModelUnavailable(f"the API rejected the key: {e}") from e
    except anthropic.APIConnectionError as e:
        raise ModelUnavailable(f"could not reach the API: {e}") from e


def _block_dict(b: Any) -> dict:
    if isinstance(b, dict):
        return b
    if hasattr(b, "model_dump"):
        return b.model_dump(exclude_none=True)
    return {k: v for k, v in vars(b).items() if v is not None}


def _text_of(content: list) -> str:
    return "\n".join(b.get("text", "") for b in content if b.get("type") == "text").strip()


# ----- the loop --------------------------------------------------------------------------

def run(cfg: Config, mode: str, user_content: str | list, ctx: tools.ToolContext, history: list | None = None,
        call: Callable[[dict], Any] | None = None, max_rounds: int = 12, log: bool = True,
        output_format: dict | None = None, effort: str | None = None) -> Result:
    """Drive one conversation. ``output_format`` is an output_config.format object (structured output)."""
    call = call or api_call
    messages = list(history or []) + [{"role": "user", "content": user_content}]
    defs = tools.definitions(mode)
    allowed = {d["name"] for d in defs}
    usage = Usage()
    calls: list[ToolCall] = []
    note = None
    stop_reason = "max_rounds"
    started = time.time()

    for _ in range(max_rounds):
        msg = call({
            "model": cfg.model,
            "max_tokens": MAX_TOKENS,
            "system": system_blocks(mode),
            "tools": defs,
            "messages": list(messages),  # a snapshot: the loop keeps appending to its own list
            "output_config": {"effort": effort or cfg.effort, **({"format": output_format} if output_format else {})},
        })
        usage.add(getattr(msg, "usage", None))
        content = [_block_dict(b) for b in msg.content]
        messages.append({"role": "assistant", "content": content})
        stop_reason = getattr(msg, "stop_reason", None) or "end_turn"
        uses = [b for b in content if b.get("type") == "tool_use"]
        if stop_reason != "tool_use" or not uses:
            if stop_reason == "max_tokens":
                note = "the model hit the output limit; the answer may be cut short"
            elif stop_reason == "refusal":
                note = "the model declined to continue"
            break
        results = []
        for u in uses:
            name, args = u.get("name", ""), u.get("input") or {}
            if not isinstance(args, dict):
                args = {}
            try:
                if name not in allowed:
                    raise tools.ToolError(f"{name} is not available in {mode} mode")
                out = tools.execute(ctx, name, args)
                results.append({"type": "tool_result", "tool_use_id": u["id"], "content": json.dumps(out)})
                calls.append(ToolCall(name, args, True, _summ(out), out))
            except tools.ToolError as e:
                results.append({"type": "tool_result", "tool_use_id": u["id"], "content": f"error: {e}", "is_error": True})
                calls.append(ToolCall(name, args, False, str(e)))
            except Exception as e:  # a tool bug must not take the brief down with it
                results.append({"type": "tool_result", "tool_use_id": u["id"],
                                "content": f"error: {type(e).__name__}: {e}", "is_error": True})
                calls.append(ToolCall(name, args, False, f"{type(e).__name__}: {e}"))
        messages.append({"role": "user", "content": results})
    else:
        note = f"stopped after {max_rounds} tool rounds without a final answer"

    text = _text_of(messages[-1]["content"]) if messages[-1]["role"] == "assistant" else ""
    result = Result(text, messages, usage, stop_reason, calls, note)
    if log:
        _log(cfg, mode, result, time.time() - started)
    return result


def _summ(out: Any) -> str:
    if isinstance(out, dict):
        keys = [k for k in out if k not in ("revision",)]
        n = next((len(out[k]) for k in keys if isinstance(out.get(k), list)), None)
        return f"{', '.join(keys[:4])}" + (f" ({n} rows)" if n is not None else "")
    return type(out).__name__


def _log(cfg: Config, mode: str, r: Result, seconds: float) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().astimezone()
    entry = {"ts": ts.isoformat(timespec="seconds"), "mode": mode, "model": cfg.model, "effort": cfg.effort,
             "rounds": r.usage.rounds, "input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens,
             "cache_write_tokens": r.usage.cache_write_tokens, "cache_read_tokens": r.usage.cache_read_tokens,
             "usd": r.usage.usd(cfg.model), "seconds": round(seconds, 1), "stop_reason": r.stop_reason,
             "tool_calls": [c.name for c in r.tool_calls]}
    with cfg.usage_log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    runs = cfg.state_dir / "runs"
    runs.mkdir(exist_ok=True)
    path = runs / f"{ts:%Y%m%d-%H%M%S}-{mode}.json"
    path.write_text(json.dumps({"entry": entry, "system": system_blocks(mode)[0]["text"], "messages": r.messages,
                                "note": r.note}, indent=1, default=str), encoding="utf-8")


def usage_total(path: Path) -> tuple[float, int, bool]:
    """(usd known, number of runs, whether any run had unknown cost) from the usage log."""
    if not path.exists():
        return 0.0, 0, False
    total, runs, unknown = 0.0, 0, False
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        runs += 1
        if e.get("usd") is None:
            unknown = True
        else:
            total += float(e["usd"])
    return total, runs, unknown


def report(cfg: Config, r: Result) -> None:
    """One stderr line per model call, so the cost is visible while demoing."""
    print(f"[model] {r.usage.line(cfg.model)}; tools: " +
          (", ".join(f"{c.name}{'' if c.ok else ' (error)'}" for c in r.tool_calls) or "none") +
          (f"; note: {r.note}" if r.note else ""), file=sys.stderr)

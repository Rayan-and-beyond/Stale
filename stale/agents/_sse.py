"""Shared SSE event-stream consumer for Managed Agent sessions.

Handles three failure modes that surface in long-running sessions:
  1. Connection drops (httpx.RemoteProtocolError, generic socket errors) — we
     reconnect with exponential backoff, up to 6 retries.
  2. Replay-on-reconnect duplicate events — `seen_event_keys` dedup.
  3. Transient `session.error` with `retry_status: retrying` — server is
     handling the retry internally; ignore. Only break on terminal errors.

The auditor pipeline added these defenses first; this module is the extracted
common form so market_fit + topics get the same resilience.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

import anthropic


def _model_dump(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


def _extract_text_from_agent_message(event: Any) -> str:
    chunks: list[str] = []
    for block in getattr(event, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            chunks.append(text)
    return "".join(chunks)


def _event_key(ev_dict: dict) -> str:
    eid = ev_dict.get("id") or ev_dict.get("event_id")
    if eid:
        return str(eid)
    return f"{ev_dict.get('type','?')}|{json.dumps(ev_dict, sort_keys=True)[:200]}"


def consume_session_stream(
    client: anthropic.Anthropic,
    session_id: str,
    *,
    label: str,
    events_path: Path,
    text_path: Path,
    on_stream_open: Callable[[], None] | None = None,
    on_tool_use: Callable[[str], None] | None = None,
    max_reconnects: int = 6,
) -> tuple[str, str | None]:
    """Stream events until the session reaches idle/terminated.

    `on_stream_open` is called once after the *initial* stream opens, before
    any events are read. Pass your kickoff-message send here to preserve the
    stream-first ordering invariant: stream open → kickoff sent → events
    consumed. The callback is NOT re-invoked on reconnect (the kickoff is
    already in the session's message history; replay handles it).

    Returns (full_agent_text, last_status). The caller is responsible for
    parsing the agent text into structured JSON.
    """
    stream = client.beta.sessions.events.stream(session_id)
    if on_stream_open:
        on_stream_open()
    text_parts: list[str] = []
    seen_event_keys: set[str] = set()
    reconnects = 0
    last_status: str | None = None
    started = time.time()

    with events_path.open("w") as ev_log, text_path.open("w") as txt_log:
        done = False
        while not done:
            try:
                for event in stream:
                    ev_dict = _model_dump(event)
                    key = _event_key(ev_dict if isinstance(ev_dict, dict) else {})
                    if key in seen_event_keys:
                        continue
                    seen_event_keys.add(key)

                    ev_type = getattr(event, "type", "?")
                    ev_log.write(json.dumps(ev_dict) + "\n")
                    ev_log.flush()

                    if ev_type == "agent.message":
                        chunk = _extract_text_from_agent_message(event)
                        if chunk:
                            text_parts.append(chunk)
                            txt_log.write(chunk + "\n\n---\n\n")
                            txt_log.flush()
                            print(f"[{label}] agent.message ({len(chunk)} chars)")
                    elif ev_type == "agent.thinking":
                        print(f"[{label}] agent.thinking ...")
                    elif ev_type == "agent.tool_use":
                        tool_name = getattr(event, "name", "?")
                        if on_tool_use:
                            on_tool_use(tool_name)
                        else:
                            print(f"[{label}] tool_use: {tool_name}")
                    elif ev_type == "agent.tool_result":
                        pass
                    elif ev_type.startswith("session.status_"):
                        last_status = ev_type
                        print(f"[{label}] {ev_type}")
                        if ev_type in ("session.status_idle",
                                       "session.status_terminated"):
                            done = True
                            break
                    elif ev_type == "session.error":
                        retry = (
                            (ev_dict.get("error", {}) or {}).get("retry_status")
                            if isinstance(ev_dict, dict) else None
                        )
                        if retry and retry.get("type") == "retrying":
                            print(
                                f"[{label}] transient session.error "
                                "(server retrying internally)",
                                file=sys.stderr,
                            )
                        else:
                            print(f"[{label}] ERROR: {ev_dict}", file=sys.stderr)
                            done = True
                            break
                else:
                    done = True
            except Exception as exc:
                if reconnects >= max_reconnects:
                    print(
                        f"[{label}] stream error after {reconnects} "
                        f"reconnects, giving up: {type(exc).__name__}: {exc}",
                        file=sys.stderr,
                    )
                    raise
                reconnects += 1
                backoff = min(2 ** reconnects, 30)
                print(
                    f"[{label}] stream error ({type(exc).__name__}); "
                    f"reconnecting in {backoff}s "
                    f"(reconnect {reconnects}/{max_reconnects})",
                    file=sys.stderr,
                )
                time.sleep(backoff)
                stream = client.beta.sessions.events.stream(session_id)

    elapsed = time.time() - started
    print(
        f"[{label}] stream closed after {elapsed:.1f}s "
        f"(last status: {last_status}, reconnects: {reconnects})"
    )
    return "".join(text_parts), last_status

"""FreeInference provider client.

OpenAI-compatible Chat Completions against freeinference.org with the
deepseek-v4-flash request shape:

* thinking/reasoning_effort as TOP-LEVEL fields (never nested);
* response_format {"type": "json_object"}; never json_schema;
* max_tokens (never max_completion_tokens), ceiling 393216;
* no sampling fields, no chat_template_kwargs, no enable_thinking;
* stream on by default: accumulate delta.content only, discard
  reasoning_content; never send X-Reasoning-Passthrough while streaming;
* User-Agent: Translation Pipeline {game title} FI Transparency;
* a unique X-Request-ID per attempt, never derived from source text;
* bounded retries with jitter; 429/5xx/timeout retry the SAME batch;
  404/401/403 stop without retrying; no fallback route, no model swap.
"""

from __future__ import annotations

import contextlib
import json
import queue
import random
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import USER_AGENT
from .models import Batch

MAX_TOKENS_CEILING = 393216


class ProviderStopError(RuntimeError):
    """Deterministic failure that must stop the run (404/401/403)."""


class TransientProviderError(RuntimeError):
    """Retryable failure: 429, 5xx, timeouts, resets."""


@dataclass
class ProviderConfig:
    base_url: str
    model: str
    api_key: str
    reasoning_effort: str = "max"
    max_tokens: int = 131072
    timeout: float = 900.0
    idle_timeout: float = 180.0
    retries: int = 3
    stream: bool = True


@dataclass
class ProviderResponse:
    content: str
    finish_reason: str = ""
    request_id: str = ""
    usage: dict | None = None


def build_request_body(cfg: ProviderConfig) -> dict:
    body: dict = {
        "model": cfg.model,
        "stream": cfg.stream,
        "max_tokens": cfg.max_tokens,
        "response_format": {"type": "json_object"},
    }
    if cfg.reasoning_effort == "none":
        body["thinking"] = {"type": "disabled"}
    else:
        body["thinking"] = {"type": "enabled"}
        body["reasoning_effort"] = cfg.reasoning_effort
    return body


class Provider:
    def __init__(self, cfg: ProviderConfig):
        if cfg.max_tokens <= 0 or cfg.max_tokens > MAX_TOKENS_CEILING:
            raise ValueError(
                f"max_tokens {cfg.max_tokens} is outside (0, {MAX_TOKENS_CEILING}]"
            )
        if cfg.api_key == "":
            raise ProviderStopError("FREEINFERENCE_API_KEY is not set")
        self.cfg = cfg

    def _request_id(self, batch: Batch, attempt: int) -> str:
        run_id = getattr(batch, "run_id", "run")
        return f"{run_id}-{batch.batch_id}-{attempt}"

    def complete(self, messages: list[dict], batch: Batch, attempt: int) -> ProviderResponse:
        req_id = self._request_id(batch, attempt)
        body = build_request_body(self.cfg)
        body["messages"] = messages
        last_error: Exception | None = None
        for retry in range(self.cfg.retries + 1):
            try:
                return self._attempt(body, batch, attempt, req_id)
            except TransientProviderError as exc:
                last_error = exc
                if retry >= self.cfg.retries:
                    break
                time.sleep(self._backoff(retry, getattr(exc, "retry_after", None)))
        raise TransientProviderError(f"provider failed after retries: {last_error}")

    def _backoff(self, retry: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(retry_after, 120.0)
        base = min(2 ** retry, 30.0)
        return base + random.uniform(0, base * 0.3)

    def _attempt(
        self, body: dict, batch: Batch, attempt: int, req_id: str
    ) -> ProviderResponse:
        url = f"{self.cfg.base_url.rstrip('/')}/chat/completions"
        payload = json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-Request-ID": req_id,
        }
        request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        raw: bytes | None = None
        try:
            with urllib.request.urlopen(request, timeout=self.cfg.timeout) as resp:
                if self.cfg.stream:
                    return self._read_stream(resp, req_id)
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            self._raise_http(exc, req_id, batch)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise TransientProviderError(f"{type(exc).__name__} (request {req_id})") from exc

        assert raw is not None
        data = json.loads(raw.decode("utf-8"))
        return self._from_message(data, req_id)

    def _raise_http(self, exc: urllib.error.HTTPError, req_id: str, batch: Batch) -> None:
        status = exc.code
        with contextlib.suppress(Exception):
            exc.close()
        if status in (401, 403):
            raise ProviderStopError(
                f"authentication failed with status {status} (request {req_id}, "
                f"batch {batch.batch_id}); the key was not echoed"
            ) from exc
        if status == 404:
            raise ProviderStopError(
                f"model not found (status 404, request {req_id}, batch {batch.batch_id}); "
                "the identifier may have been retired; the account model list is "
                "the source of truth"
            ) from exc
        retry_after = None
        if exc.headers and exc.headers.get("Retry-After"):
            try:
                retry_after = float(exc.headers["Retry-After"])
            except ValueError:
                retry_after = None
        transient = TransientProviderError(
            f"HTTP {status} (request {req_id}, batch {batch.batch_id})"
        )
        transient.retry_after = retry_after  # type: ignore[attr-defined]
        raise transient from exc

    def _read_stream(self, resp, req_id: str) -> ProviderResponse:
        content_parts: list[str] = []
        finish = ""
        idle = self.cfg.idle_timeout
        # The idle timeout must fire even while readline() blocks: urllib's
        # total timeout only applies to the initial socket, not to later
        # reads. Read lines on a daemon thread and wait on a queue with the
        # idle timeout; an empty queue means the gateway held the stream
        # open without bytes.
        lines: queue.Queue = queue.Queue(maxsize=64)

        def reader() -> None:
            try:
                while True:
                    raw = resp.readline()
                    if raw == b"":
                        lines.put(("eof", None))
                        return
                    lines.put(("line", raw))
            except OSError as exc:
                lines.put(("error", exc))
            except Exception as exc:  # noqa: BLE001 - response closed underneath
                lines.put(("error", exc))

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        while True:
            try:
                kind, payload = lines.get(timeout=idle)
            except queue.Empty as exc:
                raise TransientProviderError(
                    f"stream idle timeout (no bytes for {idle:.0f}s, request {req_id})"
                ) from exc
            if kind == "error":
                raise TransientProviderError(
                    f"stream read failed (request {req_id}): {payload}"
                ) from payload
            if kind == "eof":
                break
            raw_line: bytes = payload
            line = raw_line.decode("utf-8", "replace").strip()
            if not line:
                continue
            if line == "data: [DONE]":
                break
            if not line.startswith("data: "):
                continue
            try:
                event = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            delta = event.get("choices", [{}])[0].get("delta", {}) or {}
            piece = delta.get("content")
            if piece:
                content_parts.append(piece)
            finish = event.get("choices", [{}])[0].get("finish_reason") or finish
        content = "".join(content_parts)
        if not content:
            raise TransientProviderError(
                f"empty final content or reasoning-only response (request {req_id})"
            )
        if finish == "length":
            raise TransientProviderError(
                f"finish_reason=length before complete JSON (request {req_id})"
            )
        return ProviderResponse(content=content, finish_reason=finish, request_id=req_id)

    def _from_message(self, data: dict, req_id: str) -> ProviderResponse:
        try:
            message = data["choices"][0]["message"]
            content = message.get("content") or ""
            finish = data["choices"][0].get("finish_reason") or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise TransientProviderError(f"malformed provider response (request {req_id})") from exc
        if not content:
            raise TransientProviderError(
                f"empty final content or reasoning-only response (request {req_id})"
            )
        if finish == "length":
            raise TransientProviderError(
                f"finish_reason=length before complete JSON (request {req_id})"
            )
        usage = data.get("usage")
        return ProviderResponse(content=content, finish_reason=finish, request_id=req_id, usage=usage)
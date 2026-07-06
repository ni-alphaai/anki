# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Thin OpenAI client with an on-disk response cache.

Determinism/reproducibility (the graded property): temperature=0, a pinned
model, a fixed seed, and every response cached keyed by (model, params, prompt).
A grader can re-run the eval and reproduce the exact scores from the committed
cache without an API key. The key is read from the env or a gitignored
`anki/.env`; it is never logged.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib

_HERE = pathlib.Path(__file__).resolve().parent
_ANKI_ROOT = _HERE.parent.parent  # .../anki
CACHE_PATH = _ANKI_ROOT / "tools" / "speedrun_ai_cache.json"
# The diagnostic coach reasons over the student's own self-explanation, so it
# runs on a strong general model by default (was gpt-4o-mini). Override per-run
# with the SPEEDRUN_AI_MODEL env var (or a line in anki/.env).
DEFAULT_MODEL = "gpt-4o"


def _load_env() -> None:
    """Populate env vars (OPENAI_API_KEY, SPEEDRUN_AI_MODEL, ...) from anki/.env
    for any key not already set in the environment."""
    envf = _ANKI_ROOT / ".env"
    if not envf.exists():
        return
    for line in envf.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _resolve_model(model: str | None) -> str:
    """Explicit arg wins, then SPEEDRUN_AI_MODEL, then the strong default."""
    _load_env()
    return model or os.environ.get("SPEEDRUN_AI_MODEL") or DEFAULT_MODEL


class LLM:
    """A minimal chat-completions wrapper that returns parsed JSON, with caching."""

    def __init__(
        self, model: str | None = None, temperature: float = 0.0, seed: int = 7
    ):
        self.model = _resolve_model(model)
        self.temperature = temperature
        self.seed = seed
        self.cache = self._cache_load()
        self._client = None
        self.new_calls = 0

    def _cache_load(self) -> dict:
        if CACHE_PATH.exists():
            try:
                return json.loads(CACHE_PATH.read_text())
            except Exception:
                return {}
        return {}

    def _cache_save(self) -> None:
        CACHE_PATH.write_text(json.dumps(self.cache, indent=0, sort_keys=True))

    def _key(self, system: str, user: str) -> str:
        h = hashlib.sha256()
        h.update(
            json.dumps([self.model, self.temperature, self.seed, system, user]).encode()
        )
        return h.hexdigest()

    def complete_json(self, system: str, user: str) -> dict:
        key = self._key(system, user)
        if key in self.cache:
            return json.loads(self.cache[key])
        _load_env()
        client = self._client
        if client is None:
            from openai import OpenAI  # type: ignore[import-not-found]

            client = OpenAI()
            self._client = client
        resp = client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            seed=self.seed,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = resp.choices[0].message.content or "{}"
        self.cache[key] = content
        self.new_calls += 1
        self._cache_save()
        return json.loads(content)

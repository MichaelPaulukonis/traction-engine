"""Thin wrapper around the Anthropic SDK: every LLM call goes through here.

It logs one line per call to calls.jsonl (tokens, cost, latency) and refuses
to make a call once today's spend reaches the daily cap.

It uses messages.create with a JSON schema rather than messages.parse, because
parse validates inside the SDK call: a validation failure would raise before
we see the usage, and that billed call would be missing from the log.
"""

import json
import time
from datetime import date
from pathlib import Path
from typing import TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

# Calls go through OpenRouter, which reports the actual charge as usage.cost.
# This table (USD per million tokens, input and output) is the fallback when it
# doesn't, and the list of models we have deliberately chosen to use.
PRICES = {
    "anthropic/claude-haiku-5.5": (0.10, 0.50),
    "anthropic/claude-sonnet-5.5": (2.00, 10.00),
}

T = TypeVar("T", bound=BaseModel)


class BudgetExceeded(Exception):
    pass


class LLMRefused(Exception):
    """The model stopped without a usable answer (refusal, or output cut off)."""


class LLM:
    def __init__(self, client, log_path: Path, daily_cap_usd: float, run_id: str):
        self.client = client
        self.log_path = Path(log_path)
        self.daily_cap_usd = daily_cap_usd
        self.run_id = run_id

    def spent_today(self) -> float:
        if not self.log_path.exists():
            return 0.0
        today = date.today().isoformat()
        with self.log_path.open() as f:
            entries = (json.loads(line) for line in f if line.strip())
            return sum(e["cost_usd"] for e in entries if e["date"] == today)

    def parse(self, *, step: str, model: str, system: str, messages: list[dict],
              output_format: type[T], max_tokens: int = 8192,
              effort: str = "low") -> T:
        input_price, output_price = PRICES[model]
        spent = self.spent_today()
        if spent >= self.daily_cap_usd:
            raise BudgetExceeded(
                f"Daily cap reached: ${spent:.2f} spent of ${self.daily_cap_usd:.2f}. "
                f"Raise daily_cap_usd in config or wait until tomorrow."
            )

        start = time.monotonic()
        response = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": anthropic.transform_schema(output_format)},
            },
        )
        latency = time.monotonic() - start

        text = next((b.text for b in response.content if b.type == "text"), "")
        result, error = None, None
        if response.stop_reason == "end_turn":
            try:
                result = output_format.model_validate_json(text)
            except ValidationError as e:
                error = e

        usage = response.usage
        cost = getattr(usage, "cost", None)
        cost_source = "reported"
        if cost is None:
            cost = (usage.input_tokens * input_price + usage.output_tokens * output_price) / 1_000_000
            cost_source = "estimated"
        self._log({
            "date": date.today().isoformat(),
            "run_id": self.run_id,
            "step": step,
            "model": model,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cost_usd": round(cost, 6),
            "cost_source": cost_source,
            "latency_s": round(latency, 3),
            "stop_reason": response.stop_reason,
            "valid": result is not None,
        })

        if response.stop_reason != "end_turn":
            raise LLMRefused(f"{step}: stop_reason={response.stop_reason} {response.stop_details or ''}")
        if error:
            raise error
        return result

    def _log(self, entry: dict) -> None:
        with self.log_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

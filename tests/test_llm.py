import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

from traction_engine.llm import LLM, BudgetExceeded, LLMRefused


class Out(BaseModel):
    answer: str


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def fake_client(stop_reason="end_turn", text='{"answer": "ok"}',
                input_tokens=1_000_000, output_tokens=100_000):
    response = SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=None,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        content=[SimpleNamespace(type="text", text=text)],
    )
    return SimpleNamespace(messages=FakeMessages(response))


def make_llm(tmp_path, client, cap=5.0):
    return LLM(client=client, log_path=tmp_path / "calls.jsonl", daily_cap_usd=cap, run_id="r1")


def read_log(tmp_path):
    return [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]


def call(llm, model="anthropic/claude-haiku-5.5"):
    return llm.parse(step="extract", model=model, system="s",
                     messages=[{"role": "user", "content": "u"}], output_format=Out)


def test_returns_validated_output_and_logs_cost(tmp_path):
    llm = make_llm(tmp_path, fake_client())
    assert call(llm) == Out(answer="ok")
    [entry] = read_log(tmp_path)
    # 1M input at $0.10/M + 100K output at $0.50/M
    assert entry["cost_usd"] == pytest.approx(0.15)
    assert entry["run_id"] == "r1"
    assert entry["step"] == "extract"
    assert entry["model"] == "anthropic/claude-haiku-5.5"
    assert entry["input_tokens"] == 1_000_000
    assert entry["output_tokens"] == 100_000
    assert entry["latency_s"] >= 0


def test_reported_cost_wins_over_estimate(tmp_path):
    client = fake_client()
    client.messages.response.usage.cost = 0.042
    call(make_llm(tmp_path, client))
    [entry] = read_log(tmp_path)
    assert entry["cost_usd"] == 0.042
    assert entry["cost_source"] == "reported"


def test_estimates_cost_when_not_reported(tmp_path):
    call(make_llm(tmp_path, fake_client()))
    [entry] = read_log(tmp_path)
    assert entry["cost_source"] == "estimated"


def test_sends_json_schema_and_effort(tmp_path):
    client = fake_client()
    call(make_llm(tmp_path, client))
    config = client.messages.calls[0]["output_config"]
    assert config["effort"] == "low"
    assert config["format"]["type"] == "json_schema"
    assert "answer" in config["format"]["schema"]["properties"]


def test_refuses_without_calling_api_when_over_cap(tmp_path):
    client = fake_client()
    llm = make_llm(tmp_path, client, cap=0.20)
    call(llm)  # spends 0.15
    call(llm)  # 0.15 < 0.20, allowed; now 0.30
    with pytest.raises(BudgetExceeded):
        call(llm)
    assert len(client.messages.calls) == 2


def test_spend_from_previous_days_does_not_count(tmp_path):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    (tmp_path / "calls.jsonl").write_text(json.dumps({"date": yesterday, "cost_usd": 99.0}) + "\n")
    llm = make_llm(tmp_path, fake_client(), cap=1.0)
    assert call(llm) == Out(answer="ok")


def test_refusal_is_logged_then_raised(tmp_path):
    llm = make_llm(tmp_path, fake_client(stop_reason="refusal"))
    with pytest.raises(LLMRefused):
        call(llm)
    [entry] = read_log(tmp_path)
    assert entry["stop_reason"] == "refusal"


def test_truncated_output_raises(tmp_path):
    llm = make_llm(tmp_path, fake_client(stop_reason="max_tokens"))
    with pytest.raises(LLMRefused):
        call(llm)


def test_validation_failure_is_logged_then_raised(tmp_path):
    llm = make_llm(tmp_path, fake_client(text='{"wrong": 1}'))
    with pytest.raises(ValidationError):
        call(llm)
    [entry] = read_log(tmp_path)
    assert entry["cost_usd"] > 0
    assert entry["valid"] is False


def test_unknown_model_fails_before_calling_api(tmp_path):
    client = fake_client()
    with pytest.raises(KeyError):
        call(make_llm(tmp_path, client), model="no-such-model")
    assert client.messages.calls == []

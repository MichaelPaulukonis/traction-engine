from pathlib import Path

from pydantic import ValidationError

from traction_engine.models import Requirements

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "extract.md"


def extract(llm, posting_text: str, *, model: str, effort: str = "low") -> Requirements:
    """Posting text to Requirements. Retries once, feeding the validation error back."""
    system = PROMPT_PATH.read_text()
    first = {"role": "user", "content": f"<posting>\n{posting_text}\n</posting>"}
    try:
        return llm.parse(step="extract", model=model, system=system,
                         messages=[first], output_format=Requirements, effort=effort)
    except ValidationError as e:
        retry = {
            "role": "user",
            "content": f"{first['content']}\n\nYour previous output failed validation:\n{e}\n"
                       "Return a corrected result.",
        }
        return llm.parse(step="extract_retry", model=model, system=system,
                         messages=[retry],
                         output_format=Requirements, effort=effort)

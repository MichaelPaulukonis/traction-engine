import re
from pathlib import Path

from pydantic import ValidationError

from traction_engine.models import Requirements

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "extract.md"


# Bookmarklet captures start "# <Poster>: <Title>" and carry a "Source: ... bookmarklet" line.
# Hand-written postings use the same heading style for other things, so only captures count.
CAPTURE_HEADING = re.compile(r"\A# (?P<poster>[^:\n]+): ")
CAPTURE_LINE = re.compile(r"^Source: .*bookmarklet", re.M)


def poster(posting_text: str) -> str | None:
    """Who posted a bookmarklet capture, from its heading. None for anything else."""
    heading = CAPTURE_HEADING.match(posting_text)
    return heading["poster"].strip() if heading and CAPTURE_LINE.search(posting_text) else None


def poster_is_intermediary(req: Requirements, posting_text: str) -> Requirements:
    """No company named and no source: whoever posted it is placing for an unnamed client.

    The model reads this right most runs (Motion Recruitment: agency 4 of 5) and the flip
    to unstated passes validation, so it's a rule, not a prompt line. A direct employer
    hiding its own name would be tagged agency; rare, and a defensible reading.
    """
    name = poster(posting_text)
    if req.company is not None or req.source != "unstated" or not name:
        return req
    # Re-validated, so the job-board rule still turns "LinkedIn" back into direct.
    return Requirements.model_validate({**req.model_dump(), "source": "agency", "intermediary": name})


def extract(llm, posting_text: str, *, model: str, effort: str = "low") -> Requirements:
    """Posting text to Requirements. Retries once, feeding the validation error back."""
    return poster_is_intermediary(_extract(llm, posting_text, model=model, effort=effort), posting_text)


def _extract(llm, posting_text: str, *, model: str, effort: str) -> Requirements:
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

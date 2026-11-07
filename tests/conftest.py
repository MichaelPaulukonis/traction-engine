import pytest

from traction_engine import aliases


@pytest.fixture(autouse=True)
def no_repo_aliases(tmp_path, monkeypatch):
    """Tests never read the repo's aliases.yaml: it's reviewed data that changes as it's edited."""
    monkeypatch.setattr(aliases, "ALIASES_PATH", tmp_path / "no-aliases.yaml")

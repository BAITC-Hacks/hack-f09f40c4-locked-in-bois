
import pytest

_LLM_ENV = ("LLM_PROVIDER", "OPENAI_API_KEY", "OPENAI_API_KEYS", "NVIDIA_API_KEY", "NVIDIA_API_KEYS",
            "OPENAI_MODEL", "NVIDIA_MODEL", "NVIDIA_BASE_URL", "LLM_TIMEOUT")


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: exhaustive optimizer enumeration (RUN_SLOW=1)")


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    """Tests never see real API keys (a local .env may hold them) and default to offline."""
    for name in _LLM_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    from api import agent
    monkeypatch.setattr(agent, "_DEAD", set())

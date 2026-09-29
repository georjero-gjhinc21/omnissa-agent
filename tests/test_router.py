import urllib.error

import pytest

from omnissa_agent import router


def test_forbidden_combo_rejected():
    with pytest.raises(ValueError):
        router.ask("hi", combo="combo-coding")


def test_success_on_first_attempt_records_backend(monkeypatch):
    calls = []

    def fake_post(url, model, prompt, *, timeout_s):
        calls.append((url, model, prompt))
        return {"omni_backend": "omni/ollama-local", "choices": [{"message": {"content": "hello"}}]}

    monkeypatch.setattr(router, "_post_chat", fake_post)
    result = router.ask("say hi")
    assert result.text == "hello"
    assert result.backend == "omni/ollama-local"
    assert result.attempts == 1
    assert calls == [(router.GATEWAY_URL, f"omni/{router.DEFAULT_COMBO}", "say hi")]


def test_all_backends_fail_raises_deferred_not_infinite_loop(monkeypatch):
    attempts = []

    def fake_post(url, model, prompt, *, timeout_s):
        attempts.append(1)
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(router, "_post_chat", fake_post)
    monkeypatch.setattr(router.time, "sleep", lambda s: None)

    with pytest.raises(router.DeferredError):
        router.ask("say hi", max_attempts=3)
    assert len(attempts) == 3  # capped, not infinite


def test_gateway_error_payload_counts_as_a_failed_attempt(monkeypatch):
    def fake_post(url, model, prompt, *, timeout_s):
        return {"error": "all backends down"}

    monkeypatch.setattr(router, "_post_chat", fake_post)
    monkeypatch.setattr(router.time, "sleep", lambda s: None)

    with pytest.raises(router.DeferredError):
        router.ask("say hi", max_attempts=2)


def test_local_only_combo_constant_is_combo_private():
    assert router.LOCAL_ONLY_COMBO == "combo-private"

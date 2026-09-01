from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from okrag import synth
from okrag.synth import (
    CONCEPT_SCHEMA,
    MissingCredential,
    ProviderHTTPError,
    ProviderResponse,
    Refused,
    SynthError,
)
from okrag.synth.providers import claude, gemini, resolve

PAYLOAD = {
    "type": "Policy",
    "title": "T",
    "description": "d",
    "body_markdown": "Body.[^src]",
    "tags": ["a"],
}


def test_both_providers_are_registered():
    assert set(synth.PROVIDERS) == {"anthropic", "gemini"}
    assert resolve("gemini") is gemini
    assert resolve("anthropic") is claude


def test_unknown_provider_names_the_known_ones():
    with pytest.raises(ValueError, match="anthropic, gemini"):
        resolve("openai")


def test_providers_expose_the_interface_the_pipeline_relies_on():
    for impl in (claude, gemini):
        assert isinstance(impl.NAME, str)
        assert isinstance(impl.DEFAULT_MODEL, str)
        assert impl.ENV_VARS
        for attr in ("call", "credential_present", "list_models"):
            assert callable(getattr(impl, attr)), f"{impl.NAME}.{attr}"


def test_synthesize_dispatches_to_the_named_provider(monkeypatch):
    seen = {}

    def fake_call(*, system, user, schema, model, effort, max_tokens):
        seen.update(model=model, schema=schema, system=system)
        return ProviderResponse(text=json.dumps(PAYLOAD), input_tokens=7, output_tokens=3)

    monkeypatch.setattr(gemini, "call", fake_call)
    result = synth.synthesize(
        text="x",
        source_id="src",
        source_uri="sources/x.md",
        source_title="X",
        allowed_types=["Policy"],
        provider="gemini",
    )

    assert seen["model"] == gemini.DEFAULT_MODEL  # None -> provider default
    assert seen["schema"]["required"] == ["type", "title", "description", "body_markdown"]
    assert "Policy" in seen["system"]  # vocabulary reaches the prompt
    assert result.type == "Policy"
    assert result.input_tokens == 7
    # the undefined footnote reference gets a definition appended
    assert result.body_markdown.endswith("[^src]: X")


def test_synthesize_rejects_a_payload_missing_required_fields(monkeypatch):
    incomplete = {k: v for k, v in PAYLOAD.items() if k != "description"}
    monkeypatch.setattr(
        gemini, "call", lambda **_: ProviderResponse(text=json.dumps(incomplete))
    )
    with pytest.raises(SynthError, match="omitted required field"):
        synth.synthesize(
            text="x",
            source_id="s",
            source_uri="u",
            source_title="t",
            allowed_types=[],
            provider="gemini",
        )


def test_synthesize_reports_unparseable_json(monkeypatch):
    monkeypatch.setattr(gemini, "call", lambda **_: ProviderResponse(text="not json"))
    with pytest.raises(SynthError, match="unparseable JSON"):
        synth.synthesize(
            text="x",
            source_id="s",
            source_uri="u",
            source_title="t",
            allowed_types=[],
            provider="gemini",
        )


# --- gemini credential handling -------------------------------------------------


def test_gemini_reads_either_env_var(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert not gemini.credential_present()

    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    assert gemini.credential_present()
    assert gemini.api_key() == "g"

    monkeypatch.setenv("GEMINI_API_KEY", "preferred")
    assert gemini.api_key() == "preferred"


def test_gemini_missing_credential_names_the_env_var(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(MissingCredential, match="GEMINI_API_KEY"):
        gemini.api_key()


# --- gemini decline / failure mapping -------------------------------------------


def _response(finish_reason=None, block_reason=None, candidates=True):
    return SimpleNamespace(
        prompt_feedback=SimpleNamespace(
            block_reason=block_reason, block_reason_message="blocked"
        ),
        candidates=(
            [SimpleNamespace(finish_reason=finish_reason, finish_message="msg")]
            if candidates
            else []
        ),
    )


@pytest.mark.parametrize("reason", ["SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION"])
def test_safety_finish_reasons_map_to_refused(reason):
    with pytest.raises(Refused) as exc:
        gemini._raise_if_blocked(_response(finish_reason=reason), 100)
    assert exc.value.category == reason


def test_prompt_block_maps_to_refused():
    with pytest.raises(Refused, match="SAFETY"):
        gemini._raise_if_blocked(_response(block_reason="SAFETY"), 100)


def test_max_tokens_is_an_error_not_a_refusal():
    with pytest.raises(SynthError, match="max_output_tokens"):
        gemini._raise_if_blocked(_response(finish_reason="MAX_TOKENS"), 100)


def test_stop_passes_through():
    gemini._raise_if_blocked(_response(finish_reason="STOP"), 100)


def test_no_candidates_is_an_error():
    with pytest.raises(SynthError, match="no candidates"):
        gemini._raise_if_blocked(_response(candidates=False), 100)


def test_enum_like_finish_reason_is_read_by_name():
    reason = SimpleNamespace(name="SAFETY")
    with pytest.raises(Refused) as exc:
        gemini._raise_if_blocked(_response(finish_reason=reason), 100)
    assert exc.value.category == "SAFETY"


# --- non-2xx and transport failures ---------------------------------------------


def _stub_client(raiser):
    return SimpleNamespace(models=SimpleNamespace(generate_content=raiser))


def _call(impl):
    return impl.call(
        system="s", user="u", schema=CONCEPT_SCHEMA, model="m", effort="high", max_tokens=10
    )


def test_gemini_429_becomes_a_provider_http_error(monkeypatch):
    from google import genai
    from google.genai import errors

    def quota_exceeded(**_):
        raise errors.ClientError(
            429, {"error": {"code": 429, "message": "You exceeded your\n   current quota"}}
        )

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(genai, "Client", lambda **_: _stub_client(quota_exceeded))

    with pytest.raises(ProviderHTTPError) as exc:
        _call(gemini)
    assert exc.value.status == 429
    assert "gemini HTTP 429" in str(exc.value)
    # the build summary prints one line per file, so the message must not wrap
    assert "\n" not in str(exc.value)


def test_gemini_5xx_becomes_a_provider_http_error(monkeypatch):
    from google import genai
    from google.genai import errors

    def unavailable(**_):
        raise errors.ServerError(503, {"error": {"code": 503, "message": "overloaded"}})

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(genai, "Client", lambda **_: _stub_client(unavailable))

    with pytest.raises(ProviderHTTPError) as exc:
        _call(gemini)
    assert exc.value.status == 503


def test_anthropic_429_becomes_a_provider_http_error(monkeypatch):
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    def rate_limited(**_):
        raise anthropic.RateLimitError(
            "rate limited", response=httpx.Response(429, request=request), body=None
        )

    monkeypatch.setattr(
        anthropic,
        "Anthropic",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=rate_limited)),
    )
    with pytest.raises(ProviderHTTPError) as exc:
        _call(claude)
    assert exc.value.status == 429
    assert "anthropic HTTP 429" in str(exc.value)


def test_a_transport_failure_with_no_status_is_still_caught(monkeypatch):
    import anthropic
    import httpx

    def unreachable(**_):
        raise anthropic.APIConnectionError(
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        )

    monkeypatch.setattr(
        anthropic,
        "Anthropic",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=unreachable)),
    )
    with pytest.raises(ProviderHTTPError) as exc:
        _call(claude)
    assert exc.value.status is None
    assert "no status" in str(exc.value)


def test_a_non_200_is_a_syntherror_so_the_pipeline_records_it_per_file():
    # the whole point: ingest() catches SynthError and keeps going
    assert issubclass(ProviderHTTPError, SynthError)


# --- pipeline pre-flight --------------------------------------------------------


def test_build_fails_fast_when_the_credential_is_absent(tmp_path, monkeypatch):
    from okrag import config as config_module
    from okrag import pipeline

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "a.md").write_text("# A\n\nBody.\n", encoding="utf-8")
    (tmp_path / "okrag.toml").write_text('[synth]\nprovider = "gemini"\n', encoding="utf-8")

    def explode(**_):
        raise AssertionError("must not extract or synthesize without a credential")

    monkeypatch.setattr(pipeline, "synthesize", explode)
    with pytest.raises(MissingCredential, match="GEMINI_API_KEY"):
        pipeline.ingest(config_module.load(tmp_path), dry_run=False)

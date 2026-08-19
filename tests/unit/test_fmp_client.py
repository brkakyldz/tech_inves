from __future__ import annotations

import pytest

from techinves.data import fmp_client as fmp_client_module
from techinves.data.fmp_client import FMPClient


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(fmp_client_module.time, "sleep", lambda _seconds: None)


class _FakeResponse:
    def __init__(self, status_code: int, payload: object = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload

    def raise_for_status(self) -> None:
        pass


def _client(api_key: str | list[str], responses: list[_FakeResponse]) -> tuple[FMPClient, list[dict]]:
    client = FMPClient(api_key=api_key, use_cache=False, max_retries=2)
    calls: list[dict] = []

    def fake_get(url, params=None, timeout=None):
        calls.append(dict(params))
        return responses.pop(0)

    client._session.get = fake_get  # type: ignore[method-assign]
    return client, calls


def test_rotates_to_next_key_on_auth_error():
    responses = [_FakeResponse(401), _FakeResponse(200, {"ok": True})]
    client, calls = _client(["key-one", "key-two"], responses)

    data = client.get("profile", "AAPL")

    assert data == {"ok": True}
    assert calls[0]["apikey"] == "key-one"
    assert calls[1]["apikey"] == "key-two"


def test_returns_none_when_every_key_is_rate_limited():
    # key-one: rotates away on the first 429 (a sibling key is available).
    # key-two: the last key, so it gets one in-place retry before giving up.
    responses = [_FakeResponse(429), _FakeResponse(429), _FakeResponse(429)]
    client, calls = _client(["key-one", "key-two"], responses)

    data = client.get("profile", "AAPL")

    assert data is None
    assert len(calls) == 3


def test_multi_key_rotates_on_first_429_without_in_place_retries():
    responses = [_FakeResponse(429), _FakeResponse(429), _FakeResponse(200, {"ok": True})]
    client, calls = _client(["key-one", "key-two", "key-three"], responses)

    data = client.get("profile", "AAPL")

    assert data == {"ok": True}
    assert len(calls) == 3
    assert [c["apikey"] for c in calls] == ["key-one", "key-two", "key-three"]


def test_plan_gated_response_skips_immediately_without_rotating():
    responses = [_FakeResponse(402)]
    client, calls = _client(["key-one", "key-two"], responses)

    data = client.get("income-statement", "CRM")

    assert data is None
    assert len(calls) == 1
    assert calls[0]["apikey"] == "key-one"


def test_single_key_retries_429_in_place_before_giving_up():
    responses = [_FakeResponse(429), _FakeResponse(200, {"ok": True})]
    client, calls = _client("only-key", responses)

    data = client.get("profile", "AAPL")

    assert data == {"ok": True}
    assert len(calls) == 2
    assert calls[0]["apikey"] == calls[1]["apikey"] == "only-key"


def test_exhausted_key_is_skipped_on_a_later_call_not_reprobed():
    # First call: key-one is exhausted (429), rotates to key-two which works.
    # Second call, different endpoint: key-one is already known exhausted --
    # it must not be re-probed over the network, so only one call (to
    # key-two) should happen instead of a wasted request to key-one first.
    responses = [
        _FakeResponse(429),
        _FakeResponse(200, {"first": True}),
        _FakeResponse(200, {"second": True}),
    ]
    client, calls = _client(["key-one", "key-two"], responses)

    first = client.get("profile", "AAPL")
    assert first == {"first": True}
    assert len(calls) == 2

    second = client.get("income-statement", "AAPL")

    assert second == {"second": True}
    assert len(calls) == 3
    assert calls[2]["apikey"] == "key-two"


def test_refresh_reads_nothing_but_still_writes_the_fresh_response(tmp_path):
    """The FMP half of `--refresh-cache`. The single `use_cache` flag gated
    the write as well as the read, so a refresh left the stale payload on disk
    under its original timestamp and the next ordinary run reverted to it --
    the refresh lasted exactly one run."""
    from techinves.data.cache import FileCache

    cache = FileCache(cache_dir=tmp_path)
    cache.set("profile", "AAPL", {"price": 1.0}, None)

    client = FMPClient(api_key="key", cache=cache, use_cache=False, write_cache=True)
    client._session.get = lambda url, params=None, timeout=None: _FakeResponse(  # type: ignore[method-assign]
        200, {"price": 2.0}
    )

    assert client.get("profile", "AAPL") == {"price": 2.0}  # the read was bypassed
    assert cache.get("profile", "AAPL", None) == {"price": 2.0}  # the write happened


def test_use_cache_false_on_its_own_still_writes_nothing(tmp_path):
    """The write gate follows the read gate unless told otherwise, so every
    existing `use_cache=False` caller keeps meaning "no cache at all"."""
    from techinves.data.cache import FileCache

    cache = FileCache(cache_dir=tmp_path)
    client = FMPClient(api_key="key", cache=cache, use_cache=False)
    client._session.get = lambda url, params=None, timeout=None: _FakeResponse(  # type: ignore[method-assign]
        200, {"price": 2.0}
    )

    client.get("profile", "AAPL")
    assert cache.get("profile", "AAPL", None) is None

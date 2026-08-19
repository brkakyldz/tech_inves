from __future__ import annotations

import pytest
import requests

from techinves.config import ConfigError
from techinves.data import edgar_client as edgar_client_module
from techinves.data.edgar_client import EdgarClient


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(edgar_client_module.time, "sleep", lambda _seconds: None)


@pytest.fixture(autouse=True)
def _fresh_process_wide_throttle():
    """The EDGAR budget is process-wide state (it has to be -- SEC counts per
    source IP, not per object), so it has to be cleared between tests or they
    become order-dependent."""
    edgar_client_module.EDGAR_THROTTLE.reset()
    yield
    edgar_client_module.EDGAR_THROTTLE.reset()


class _FakeResponse:
    def __init__(self, status_code: int, payload: object = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload

    def raise_for_status(self) -> None:
        pass


def _client(responses: list, **kwargs) -> tuple[EdgarClient, list[str]]:
    client = EdgarClient(use_cache=False, max_retries=3, **kwargs)
    urls: list[str] = []

    def fake_get(url, timeout=None):
        urls.append(url)
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    client._session.get = fake_get  # type: ignore[method-assign]
    return client, urls


def test_company_facts_url_uses_the_zero_padded_cik():
    client, urls = _client([_FakeResponse(200, {"facts": {}})])
    client.get_company_facts("0000320193", "AAPL")
    assert urls == ["https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"]


def test_contact_user_agent_is_sent_on_every_request():
    """data.sec.gov 403s anything without a contact address in the header."""
    client = EdgarClient(user_agent="TechInves (someone@example.com)", use_cache=False)
    assert client._session.headers["User-Agent"] == "TechInves (someone@example.com)"


def test_missing_entity_returns_none_rather_than_raising():
    """A 404 is a real, permanent answer for entities that never filed XBRL
    financials -- callers record it as a missing source, not a crash.
    """
    client, _ = _client([_FakeResponse(404)])
    assert client.get_company_facts("0000000001", "GHOST") is None


def test_rejected_user_agent_fails_loudly_with_the_cause():
    """Retrying identical headers cannot fix a 403, and a silent None here
    would look exactly like "this company filed nothing" for all 42 tickers.
    """
    client, urls = _client([_FakeResponse(403)])
    with pytest.raises(ConfigError, match="SEC_EDGAR_USER_AGENT"):
        client.get_json("https://data.sec.gov/x.json", "endpoint", "KEY")
    assert len(urls) == 1  # not retried


def test_rejected_user_agent_is_not_swallowed_by_get_company_facts():
    """ConfigError must not be a requests.RequestException: get_company_facts
    only downgrades RequestException to None per ticker, and a config problem
    silently degrading all 42 companies to zero fundamentals is exactly the
    failure this distinction exists to prevent.
    """
    client, _ = _client([_FakeResponse(403)])
    with pytest.raises(ConfigError, match="SEC_EDGAR_USER_AGENT"):
        client.get_company_facts("0000320193", "AAPL")


def test_rate_limited_and_server_errors_are_retried():
    client, urls = _client([_FakeResponse(429), _FakeResponse(503), _FakeResponse(200, {"ok": True})])
    assert client.get_json("https://data.sec.gov/x.json", "endpoint", "KEY") == {"ok": True}
    assert len(urls) == 3


def test_network_errors_are_retried_then_raised():
    client, urls = _client([requests.ConnectionError("boom")] * 3)
    with pytest.raises(requests.ConnectionError):
        client.get_json("https://data.sec.gov/x.json", "endpoint", "KEY")
    assert len(urls) == 3


def test_get_company_facts_downgrades_a_persistent_network_error_to_none():
    """One unreachable company must not abort a 42-ticker run."""
    client, _ = _client([requests.ConnectionError("boom")] * 3)
    assert client.get_company_facts("0000000001", "ACME") is None


def test_requests_are_throttled_to_the_configured_rate(monkeypatch):
    """SEC blocks the source IP for exceeding its fair-access limit, so the
    spacing has to be enforced rather than hoped for.
    """
    slept: list[float] = []
    # 0.1s apart; the fake clock advances only 0.01s between the two calls, so
    # the second one owes ~0.09s of wait.
    client = EdgarClient(use_cache=False, max_requests_per_second=10)
    client._session.get = lambda url, timeout=None: _FakeResponse(200, {"ok": True})  # type: ignore[method-assign]

    ticks = iter([0.0, 0.0, 0.01, 0.01])
    monkeypatch.setattr(edgar_client_module.time, "monotonic", lambda: next(ticks, 1.0))
    monkeypatch.setattr(edgar_client_module.time, "sleep", slept.append)

    client.get_json("https://data.sec.gov/a.json", "e", "A")
    client.get_json("https://data.sec.gov/b.json", "e", "B")

    assert slept == [pytest.approx(0.09, abs=1e-6)]


def test_responses_are_cached_so_a_rerun_costs_no_requests(tmp_path):
    from techinves.data.cache import FileCache

    cache = FileCache(cache_dir=tmp_path)
    client = EdgarClient(cache=cache, use_cache=True)
    calls: list[str] = []

    def fake_get(url, timeout=None):
        calls.append(url)
        return _FakeResponse(200, {"facts": {"us-gaap": {}}})

    client._session.get = fake_get  # type: ignore[method-assign]

    first = client.get_company_facts("0000320193", "AAPL")
    second = client.get_company_facts("0000320193", "AAPL")

    assert first == second
    assert len(calls) == 1


def test_the_rate_budget_is_shared_by_every_client_in_the_process(monkeypatch):
    """SEC's fair-access limit is per source IP, so per-instance pacing is not
    pacing at all: `build_default_provider` mints a fresh EdgarClient per
    call, and the in-flight run lock is per *trigger type*, so a `scores` run
    and a `company` run may legitimately execute at once. Two clients each
    keeping to 10 req/s is 20 req/s out of one IP -- the IP-ban case, not a
    recoverable per-ticker failure.
    """
    slept: list[float] = []
    first = EdgarClient(use_cache=False, max_requests_per_second=10)
    second = EdgarClient(use_cache=False, max_requests_per_second=10)
    for client in (first, second):
        client._session.get = lambda url, timeout=None: _FakeResponse(200, {"ok": True})  # type: ignore[method-assign]

    ticks = iter([0.0, 0.0, 0.01, 0.01])
    monkeypatch.setattr(edgar_client_module.time, "monotonic", lambda: next(ticks, 1.0))
    monkeypatch.setattr(edgar_client_module.time, "sleep", slept.append)

    first.get_json("https://data.sec.gov/a.json", "e", "A")
    # A *different* client instance, which used to start with an empty budget
    # and fire immediately.
    second.get_json("https://data.sec.gov/b.json", "e", "B")

    assert slept == [pytest.approx(0.09, abs=1e-6)]


def test_refresh_reads_nothing_but_still_writes_the_fresh_response(tmp_path):
    """`--refresh-cache` used to bypass the read *and* skip the write, so a
    refresh run scored fresh data and left the stale payload in place under
    its original timestamp -- and the very next ordinary run silently reverted
    to it. Refreshing after earnings therefore lasted exactly one run."""
    from techinves.data.cache import FileCache

    cache = FileCache(cache_dir=tmp_path)
    cache.set(edgar_client_module.COMPANY_FACTS_ENDPOINT, "AAPL", {"facts": "stale"}, None)

    client = EdgarClient(cache=cache, use_cache=False, write_cache=True)
    client._session.get = lambda url, timeout=None: _FakeResponse(  # type: ignore[method-assign]
        200, {"facts": "fresh"}
    )

    assert client.get_company_facts("0000320193", "AAPL") == {"facts": "fresh"}  # read bypassed
    # ...and the cache now holds the fresh copy, so the next ordinary run
    # picks up the refresh instead of reverting.
    assert cache.get(edgar_client_module.COMPANY_FACTS_ENDPOINT, "AAPL", None) == {"facts": "fresh"}


def test_use_cache_false_on_its_own_still_writes_nothing(tmp_path):
    """The write gate defaults to following the read gate, so an existing
    `use_cache=False` caller (every test fake in this file) keeps meaning
    "no cache at all"."""
    from techinves.data.cache import FileCache

    cache = FileCache(cache_dir=tmp_path)
    client = EdgarClient(cache=cache, use_cache=False)
    client._session.get = lambda url, timeout=None: _FakeResponse(200, {"facts": "fresh"})  # type: ignore[method-assign]

    client.get_company_facts("0000320193", "AAPL")
    assert cache.get(edgar_client_module.COMPANY_FACTS_ENDPOINT, "AAPL", None) is None


def test_build_default_provider_refreshes_by_rewriting_the_cache(monkeypatch):
    """The wiring, not just the client: `refresh_cache=True` has to reach the
    clients as "read nothing, write everything"."""
    from techinves.data import raw_facts as raw_facts_module

    monkeypatch.setattr(raw_facts_module, "get_fmp_api_keys", lambda: ["key"])
    monkeypatch.setattr(raw_facts_module, "load_cik_map", lambda client: {"AAPL": "0000320193"})

    provider = raw_facts_module.build_default_provider(refresh_cache=True)

    for client in (provider.edgar_client, provider.fmp_client, provider.fx_client):
        assert client.use_cache is False  # nothing is read back
        assert client.write_cache is True  # but the fresh response is stored

    unrefreshed = raw_facts_module.build_default_provider(refresh_cache=False)
    assert unrefreshed.edgar_client.use_cache is True
    assert unrefreshed.edgar_client.write_cache is True

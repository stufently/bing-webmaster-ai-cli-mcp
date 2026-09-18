from __future__ import annotations

import re

import httpx
import pytest

from bing_webmaster_ai_cli_mcp.errors import AuthFailed, InvalidRequest, RateLimited
from bing_webmaster_ai_cli_mcp.ops import indexnow

HOST = "a.example"
KEY = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def public_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve the test host to a public address without touching real DNS."""

    async def resolve(host: str) -> set[str]:
        return {"93.184.216.34"}

    monkeypatch.setattr(indexnow, "_resolve", resolve)


def test_generated_key_matches_protocol() -> None:
    assert re.fullmatch(r"[A-Za-z0-9-]{8,128}", indexnow.generate_key())


@pytest.mark.parametrize("bad", ["short", "x" * 129, "has_underscore", "has space"])
def test_invalid_keys_are_rejected(bad: str) -> None:
    with pytest.raises(InvalidRequest):
        indexnow.validate_key(bad)


def test_default_key_location_is_site_root() -> None:
    assert indexnow.key_location(HOST, KEY) == f"https://{HOST}/{KEY}.txt"


async def test_key_file_must_be_reachable_and_contain_key() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=KEY))
    async with httpx.AsyncClient(transport=transport) as http:
        await indexnow.verify_key_file(http, HOST, KEY)


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(404), "not reachable"),
        (httpx.Response(200, text="wrong"), "does not contain"),
        (httpx.Response(200, text=f"{KEY}\n"), "does not contain"),
    ],
)
async def test_bad_key_file_is_clear(response: httpx.Response, message: str) -> None:
    transport = httpx.MockTransport(lambda request: response)
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(InvalidRequest, match=message):
            await indexnow.verify_key_file(http, HOST, KEY)


async def test_submit_posts_documented_batch_body() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read()
        return httpx.Response(200, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await indexnow.submit(http, HOST, KEY, [f"https://{HOST}/p"])
    assert seen["url"] == indexnow.ENDPOINT
    assert b'"host":"a.example"' in seen["body"]
    assert result["meaning"] == "received"


async def test_batch_over_10000_is_refused_without_network() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    urls = [f"https://{HOST}/{number}" for number in range(indexnow.MAX_BATCH + 1)]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(InvalidRequest, match="10000"):
            await indexnow.submit(http, HOST, KEY, urls)
    assert calls == 0


async def test_subpath_key_only_authorizes_urls_below_subpath() -> None:
    location = f"https://{HOST}/news/{KEY}.txt"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    ) as http:
        with pytest.raises(InvalidRequest, match="key path"):
            await indexnow.submit(http, HOST, KEY, [f"https://{HOST}/shop/p"], location)


@pytest.mark.parametrize(
    "url",
    [
        f"https://{HOST}/news/../admin",
        f"https://{HOST}/news/%2e%2e/admin",
        f"https://{HOST}/news%2f..%2fadmin",
    ],
)
def test_subpath_key_rejects_ambiguous_dot_segments(url: str) -> None:
    location = f"https://{HOST}/news/{KEY}.txt"
    with pytest.raises(InvalidRequest, match="dot segments"):
        indexnow.validate_urls(HOST, KEY, [url], location)


async def test_key_file_redirect_is_not_followed() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": "https://internal.example/key.txt"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(InvalidRequest, match="HTTP 302"):
            await indexnow.verify_key_file(http, HOST, KEY)
    assert calls == [indexnow.key_location(HOST, KEY)]


@pytest.mark.parametrize(
    ("status", "expected"),
    [(200, "received"), (202, "accepted")],
)
async def test_success_codes_are_explained(status: int, expected: str) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, request=request))
    async with httpx.AsyncClient(transport=transport) as http:
        result = await indexnow.submit(http, HOST, KEY, [f"https://{HOST}/p"])
    assert expected in result["meaning"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [(400, InvalidRequest), (403, AuthFailed), (422, InvalidRequest), (429, RateLimited)],
)
async def test_error_codes_are_mapped(status: int, expected: type[Exception]) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status, request=request))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(expected):
            await indexnow.submit(http, HOST, KEY, [f"https://{HOST}/p"])


async def test_verify_happens_before_submission() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(404, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(InvalidRequest):
            await indexnow.verify_and_submit(http, HOST, KEY, [f"https://{HOST}/p"])
    assert calls == [indexnow.key_location(HOST, KEY)]


@pytest.mark.parametrize(
    "host",
    [
        "[abc",
        "a b.example",
        "a.example:8080",
        "-bad.example",
        "",
        "localhost",
        "internal",
        "127.0.0.1",
        "0",
    ],
)
def test_unusable_indexnow_hosts_raise_a_public_error(host: str) -> None:
    with pytest.raises(InvalidRequest):
        indexnow.validate_host(host)


def test_unparsable_key_location_and_urls_stay_in_the_taxonomy() -> None:
    with pytest.raises(InvalidRequest):
        indexnow.validate_key_location(HOST, KEY, "https://[abc")
    with pytest.raises(InvalidRequest):
        indexnow.validate_urls(HOST, KEY, ["https://[abc"])
    with pytest.raises(InvalidRequest, match="percent-encoding"):
        indexnow.validate_urls(HOST, KEY, [f"https://{HOST}/bad%zz"])


@pytest.mark.parametrize(
    "location",
    [
        f"https://{HOST}:8443/{KEY}.txt",
        f"https://{HOST}/{KEY}.txt?download=1",
        f"https://{HOST}/{KEY}.txt#fragment",
        f"https://:password@{HOST}/{KEY}.txt",
    ],
)
def test_key_location_is_an_exact_public_https_resource(location: str) -> None:
    with pytest.raises(InvalidRequest, match="keyLocation"):
        indexnow.validate_key_location(HOST, KEY, location)


async def test_oversized_key_file_is_rejected_without_buffering_the_rest() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 1024))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(InvalidRequest, match="does not contain"):
            await indexnow.verify_key_file(http, HOST, KEY)


async def test_key_plan_generates_a_key_and_sends_nothing() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        plan = await indexnow.key_plan(http, HOST)
    assert calls == []
    assert plan["generated"] is True
    assert re.fullmatch(r"[A-Za-z0-9-]{8,128}", plan["key"])
    assert plan["key_location"] == f"https://{HOST}/{plan['key']}.txt"
    assert plan["key_file_contents"] == plan["key"]
    assert plan["authorizes_urls_under"] == f"https://{HOST}/"
    assert plan["key_file"] == {"checked": False, "present": None}


async def test_key_plan_reports_a_published_key_file(public_host) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=KEY))
    async with httpx.AsyncClient(transport=transport) as http:
        plan = await indexnow.key_plan(http, HOST, key=KEY)
    assert plan["generated"] is False
    assert plan["key_file"] == {"checked": True, "present": True}


async def test_key_plan_reports_a_missing_key_file_as_a_result_not_an_error(
    public_host,
) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(404, request=request))
    async with httpx.AsyncClient(transport=transport) as http:
        plan = await indexnow.key_plan(http, HOST, key=KEY)
    assert plan["key_file"]["checked"] is True
    assert plan["key_file"]["present"] is False
    assert "not reachable" in plan["key_file"]["detail"]


async def test_key_plan_names_the_subpath_a_hosted_key_authorizes(public_host) -> None:
    location = f"https://{HOST}/news/{KEY}.txt"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=KEY))
    ) as http:
        plan = await indexnow.key_plan(http, HOST, key=KEY, key_location=location)
    assert plan["authorizes_urls_under"] == f"https://{HOST}/news/"


async def test_key_plan_check_can_be_forced_off_and_on(public_host) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=KEY, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert (await indexnow.key_plan(http, HOST, key=KEY, check_key_file=False))["key_file"] == {
            "checked": False,
            "present": None,
        }
        assert calls == []
        plan = await indexnow.key_plan(http, HOST, check_key_file=True)
    assert plan["generated"] is True
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("host", "key"),
    [("localhost", None), ("127.0.0.1", None), (HOST, "short"), (HOST, "has_underscore")],
)
async def test_key_plan_rejects_what_submission_would_reject(host: str, key: str | None) -> None:
    with pytest.raises(InvalidRequest):
        await indexnow.key_plan(None, host, key=key)


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.1.1", "::1", "fd00::1"]
)
async def test_key_file_check_refuses_a_host_resolving_off_the_public_internet(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    """A well-formed name is not a public host: foo.localhost and *.nip.io resolve home."""
    calls: list[str] = []

    async def resolve(host: str) -> set[str]:
        return {address}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=KEY, request=request)

    monkeypatch.setattr(indexnow, "_resolve", resolve)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        plan = await indexnow.key_plan(http, HOST, key=KEY)
    assert calls == []
    assert plan["key_file"]["present"] is False
    assert "non-public" in plan["key_file"]["detail"]


async def test_key_file_check_refuses_a_host_that_does_not_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def resolve(host: str) -> set[str]:
        raise OSError("Name or service not known")

    monkeypatch.setattr(indexnow, "_resolve", resolve)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=KEY))
    ) as http:
        plan = await indexnow.key_plan(http, HOST, key=KEY)
    assert plan["key_file"]["present"] is False
    assert "does not resolve" in plan["key_file"]["detail"]


async def test_key_file_check_accepts_a_mixed_answer_only_if_every_address_is_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def resolve(host: str) -> set[str]:
        return {"93.184.216.34", "127.0.0.1"}

    monkeypatch.setattr(indexnow, "_resolve", resolve)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=KEY))
    ) as http:
        plan = await indexnow.key_plan(http, HOST, key=KEY)
    assert plan["key_file"]["present"] is False

from __future__ import annotations

import json
import traceback
from datetime import UTC, datetime

import httpx
import pytest
from fakes import bing_transport, error_transport, fake_settings

from bing_webmaster_ai_cli_mcp.client import BingClient
from bing_webmaster_ai_cli_mcp.errors import (
    AuthFailed,
    InvalidRequest,
    MalformedResponse,
    PlanUnknownOutcome,
    RateLimited,
    UpstreamUnavailable,
)
from bing_webmaster_ai_cli_mcp.render import REDACTED, REDACTED_CREDENTIAL


async def test_get_unwraps_decodes_and_authenticates(tmp_path) -> None:
    transport = bing_transport({"GetUserSites": [{"When": "/Date(0)/"}]})
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        result = await client.call("GetUserSites")
    assert result == [{"When": datetime(1970, 1, 1, tzinfo=UTC)}]
    assert transport.calls[0].url.params["apikey"] == "test-key"
    assert transport.calls[0].method == "GET"


async def test_post_uses_json_body(tmp_path) -> None:
    transport = bing_transport({"SubmitUrl": None})
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        await client.call("SubmitUrl", body={"siteUrl": "https://a.example"}, mutating=True)
    assert transport.calls[0].method == "POST"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, InvalidRequest),
        (401, AuthFailed),
        (403, AuthFailed),
        (429, RateLimited),
        (500, UpstreamUnavailable),
    ],
)
async def test_status_error_mapping(tmp_path, status: int, expected: type[Exception]) -> None:
    transport = error_transport(status, {"ErrorCode": 7, "Message": "upstream message"})
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        with pytest.raises(expected, match="upstream message"):
            await client.call("GetUserSites")


async def test_retry_after_is_preserved(tmp_path) -> None:
    transport = error_transport(
        429, {"ErrorCode": 4, "Message": "slow"}, headers={"Retry-After": "42"}
    )
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        with pytest.raises(RateLimited) as caught:
            await client.call("GetUserSites")
    assert caught.value.retry_after == 42


@pytest.mark.parametrize(
    "response",
    [httpx.Response(200, json={"oops": 1}), httpx.Response(200, content=b"<html>")],
)
async def test_malformed_success_is_public_error(tmp_path, response: httpx.Response) -> None:
    transport = httpx.MockTransport(lambda request: response)
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        with pytest.raises(MalformedResponse):
            await client.call("GetUserSites")


async def test_connection_failure_is_upstream_unavailable(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with BingClient(
        fake_settings(tmp_path), transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(UpstreamUnavailable):
            await client.call("GetUserSites")


async def test_lost_write_response_has_unknown_outcome(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lost response", request=request)

    async with BingClient(
        fake_settings(tmp_path), transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(PlanUnknownOutcome):
            await client.call("SubmitUrl", body={"url": "x"}, mutating=True)


async def test_safe_read_retries_rate_limit(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, json={"Message": "slow"}, request=request)
        return httpx.Response(200, json={"d": []}, request=request)

    async def no_sleep(delay: float) -> None:
        assert delay >= 0

    monkeypatch.setattr("bing_webmaster_ai_cli_mcp.client.asyncio.sleep", no_sleep)
    async with BingClient(
        fake_settings(tmp_path, max_attempts=2), transport=httpx.MockTransport(handler)
    ) as client:
        assert await client.call("GetUserSites") == []
    assert attempts == 2


async def test_server_error_on_a_write_is_an_unknown_outcome(tmp_path) -> None:
    transport = error_transport(503, {"Message": "upstream"})
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        with pytest.raises(PlanUnknownOutcome):
            await client.call("AddSite", body={"siteUrl": "https://a.example"}, mutating=True)


async def test_server_error_on_a_read_stays_retryable(tmp_path) -> None:
    transport = error_transport(503, {"Message": "upstream"})
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        with pytest.raises(UpstreamUnavailable):
            await client.call("GetUserSites")


SUBMITTED_CODE = "auth-secret-value"


async def test_an_upstream_error_quoting_our_own_code_is_scrubbed(tmp_path) -> None:
    """Bing's words about our request travel to the transcript, the log and the terminal."""
    transport = error_transport(
        400,
        {"ErrorCode": 400, "Message": f"authenticationCode {SUBMITTED_CODE} was rejected"},
    )
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        with pytest.raises(InvalidRequest) as raised:
            await client.call("AddSiteRoles", body={"authenticationCode": SUBMITTED_CODE})

    rendered = json.dumps(raised.value.to_dict())
    assert SUBMITTED_CODE not in rendered
    assert REDACTED in raised.value.message
    assert "was rejected" in raised.value.message


async def test_a_delegation_code_nested_in_a_request_body_is_scrubbed_too(tmp_path) -> None:
    transport = error_transport(400, {"Message": f"role {SUBMITTED_CODE} is unknown"})
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        with pytest.raises(InvalidRequest) as raised:
            await client.call(
                "RemoveSiteRole", body={"siteRole": {"DelegatedCode": SUBMITTED_CODE}}
            )

    assert SUBMITTED_CODE not in json.dumps(raised.value.to_dict())


async def test_an_error_on_a_request_carrying_no_secret_reads_normally(tmp_path) -> None:
    """Scrubbing must not turn every error message into markers."""
    transport = error_transport(400, {"Message": "siteUrl https://a.example is not registered"})
    async with BingClient(fake_settings(tmp_path), transport=transport) as client:
        with pytest.raises(InvalidRequest) as raised:
            await client.call("AddSite", body={"siteUrl": "https://a.example"})

    assert raised.value.message == "AddSite: siteUrl https://a.example is not registered"


# Not a real key: no test may ever contain one, and this value is only ever compared
# against text the client produced.
FAKE_KEY = "FAKE-KEY-DO-NOT-USE-0123456789"


def _key_settings(tmp_path):
    return fake_settings(tmp_path, api_key=FAKE_KEY)


async def test_a_connect_error_quoting_our_url_hides_the_key(tmp_path) -> None:
    """The key rides in the query string, so any text quoting the URL carries it out."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"proxy could not reach {request.url}", request=request)

    async with BingClient(
        _key_settings(tmp_path), transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(UpstreamUnavailable) as raised:
            await client.call("GetUserSites")

    assert FAKE_KEY not in json.dumps(raised.value.to_dict())
    assert REDACTED_CREDENTIAL in raised.value.message
    assert "proxy could not reach" in raised.value.message


async def test_a_read_error_quoting_our_url_hides_the_key(tmp_path) -> None:
    """The second httpx branch is a separate message and needs its own cover."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError(f"read failed on {request.url}", request=request)

    async with BingClient(
        _key_settings(tmp_path), transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(UpstreamUnavailable) as raised:
            await client.call("GetUserSites")

    assert FAKE_KEY not in json.dumps(raised.value.to_dict())
    assert REDACTED_CREDENTIAL in raised.value.message


async def test_bing_quoting_our_url_back_hides_the_key(tmp_path) -> None:
    transport = error_transport(
        400,
        {
            "ErrorCode": f"rejected {FAKE_KEY}",
            "Message": (
                "bad request for https://ssl.bing.com/webmaster/api.svc/json/"
                f"GetUserSites?apikey={FAKE_KEY}"
            ),
        },
    )
    async with BingClient(_key_settings(tmp_path), transport=transport) as client:
        with pytest.raises(InvalidRequest) as raised:
            await client.call("GetUserSites")

    rendered = json.dumps(raised.value.to_dict())
    assert FAKE_KEY not in rendered
    assert REDACTED_CREDENTIAL in raised.value.message
    assert REDACTED_CREDENTIAL in raised.value.details["ErrorCode"]


async def test_an_error_carrying_no_credential_still_reads_normally(tmp_path) -> None:
    """Covering the key must not turn ordinary failures into a wall of markers."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with BingClient(
        _key_settings(tmp_path), transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(UpstreamUnavailable) as raised:
            await client.call("GetUserSites")

    assert raised.value.message == "GetUserSites: connection refused"


async def test_a_key_needing_url_encoding_is_hidden_in_its_wire_form(tmp_path) -> None:
    """What a proxy quotes back is the encoded key, not the literal we hold."""
    awkward = "FAKE/KEY+DO NOT USE%2F0123456789"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"proxy could not reach {request.url}", request=request)

    async with BingClient(
        fake_settings(tmp_path, api_key=awkward), transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(UpstreamUnavailable) as raised:
            await client.call("GetUserSites")

    encoded = str(httpx.QueryParams({"v": awkward})).removeprefix("v=")
    assert encoded not in raised.value.message
    assert awkward not in raised.value.message
    assert REDACTED_CREDENTIAL in raised.value.message


async def test_a_lost_write_does_not_chain_the_raw_transport_error(tmp_path) -> None:
    """A chained cause reaches every traceback, where ``to_dict`` cannot cover it."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError(f"lost on {request.url}", request=request)

    async with BingClient(
        _key_settings(tmp_path), transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(PlanUnknownOutcome) as raised:
            await client.call("SubmitUrl", body={"url": "x"}, mutating=True)

    rendered = "".join(traceback.format_exception(raised.value))
    assert FAKE_KEY not in rendered
    assert raised.value.__cause__ is None
    # The operator still learns what failed, scrubbed, and the audit trail records it.
    assert REDACTED_CREDENTIAL in raised.value.details["cause"]
    assert "ReadError" in raised.value.details["cause"]


async def test_a_refreshed_credential_is_covered_on_the_call_that_uses_it(tmp_path) -> None:
    """Reading the literals at construction would cover only the value we started with."""

    class Rotating:
        def __init__(self) -> None:
            self.current = "FIRST-FAKE-KEY-0000000000"

        def apply(self, params: dict[str, object], headers: dict[str, str]) -> None:
            params["apikey"] = self.current

        def secrets(self) -> frozenset[str]:
            return frozenset({self.current})

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"proxy could not reach {request.url}", request=request)

    auth = Rotating()
    async with BingClient(
        _key_settings(tmp_path), transport=httpx.MockTransport(handler)
    ) as client:
        client._auth = auth
        with pytest.raises(UpstreamUnavailable) as first:
            await client.call("GetUserSites")
        auth.current = "SECOND-FAKE-KEY-1111111111"
        with pytest.raises(UpstreamUnavailable) as second:
            await client.call("GetUserSites")

    assert "FIRST-FAKE-KEY-0000000000" not in first.value.message
    assert "SECOND-FAKE-KEY-1111111111" not in second.value.message
    assert REDACTED_CREDENTIAL in second.value.message

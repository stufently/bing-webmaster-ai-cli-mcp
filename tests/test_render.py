from __future__ import annotations

import json

from bing_webmaster_ai_cli_mcp.render import (
    REDACTED,
    REDACTED_CREDENTIAL,
    redact,
    redact_secrets,
    redact_text,
    sanitize,
    sanitize_text,
    secret_values,
)


def test_controls_and_bidi_marks_are_neutralised() -> None:
    assert sanitize_text("a\x1b[31mb\x07c\u202eevil") == "a[31mbcevil"


def test_newlines_and_tabs_survive() -> None:
    assert sanitize_text("a\nb\tc") == "a\nb\tc"


def test_long_text_is_truncated_with_marker() -> None:
    result = sanitize_text("x" * 5000)
    assert result.endswith("… [truncated]")
    assert len(result) == 2000 + len("… [truncated]")


def test_untrusted_fields_are_marked_recursively() -> None:
    assert sanitize({"Links": [{"AnchorText": "a\x00b", "Clicks": 1}]}) == {
        "Links": [{"AnchorText": {"value": "ab", "untrusted": True}, "Clicks": 1}]
    }


def test_query_strings_and_urls_are_untrusted() -> None:
    result = sanitize({"Query": "x", "QueryString": "a=b", "Url": "https://a"})
    assert all(result[key]["untrusted"] for key in result)


def test_verification_secrets_are_redacted_by_default() -> None:
    result = redact_secrets(
        {"Url": "https://a.example", "IsVerified": True, "AuthenticationCode": "auth-secret"}
    )
    assert result == {
        "Url": "https://a.example",
        "IsVerified": True,
        "AuthenticationCode": REDACTED,
    }
    assert "auth-secret" not in json.dumps(result)


def test_every_secret_field_is_redacted_however_deeply_it_is_nested() -> None:
    payload = [{"Roles": [{"DelegatedCode": "d", "DnsVerificationCode": "n", "Email": "a@b.c"}]}]
    assert redact_secrets(payload) == [
        {"Roles": [{"DelegatedCode": REDACTED, "DnsVerificationCode": REDACTED, "Email": "a@b.c"}]}
    ]


def test_an_absent_code_is_never_claimed_to_exist() -> None:
    """Redacting a null would report an ownership proof where Bing reported none."""
    assert redact_secrets({"DelegatedCode": None, "AuthenticationCode": ""}) == {
        "DelegatedCode": None,
        "AuthenticationCode": "",
    }


def test_the_marker_says_what_was_removed() -> None:
    assert "redacted" in REDACTED
    assert "verification secret" in REDACTED


def test_the_request_spellings_of_a_secret_are_redacted_too() -> None:
    """One secret has three names: the response's, the request body's and a plan's."""
    payload = {
        "AuthenticationCode": "a-secret",
        "authenticationCode": "b-secret",
        "authentication_code": "c-secret",
    }
    assert redact_secrets(payload) == dict.fromkeys(payload, REDACTED)


def test_a_field_that_merely_starts_with_a_secret_name_is_left_alone() -> None:
    assert redact_secrets({"DelegatedCodeOwnerEmail": "a@b.c"}) == {
        "DelegatedCodeOwnerEmail": "a@b.c"
    }


def test_secret_values_finds_the_literals_a_request_carries() -> None:
    body = {
        "siteUrl": "https://a.example",
        "authenticationCode": "auth-secret-value",
        "siteRole": {"DelegatedCode": "delegated-secret-value", "Email": "a@b.c"},
    }
    assert secret_values(body) == frozenset({"auth-secret-value", "delegated-secret-value"})


def test_secret_values_ignores_values_too_short_to_search_for_safely() -> None:
    """Replacing every 'ab' in an error message would shred it and protect nothing."""
    assert secret_values({"AuthenticationCode": "ab"}) == frozenset()


def test_a_secret_is_hidden_in_free_text_that_never_named_it() -> None:
    """Bing quoting our own code back inside a message is the leak nobody keys on."""
    message = "AddSiteRoles: authenticationCode auth-secret-value was rejected"
    assert redact_text(message, {"auth-secret-value"}) == (
        f"AddSiteRoles: authenticationCode {REDACTED} was rejected"
    )


def test_the_boundary_hides_a_secret_by_name_and_by_value_at_once() -> None:
    payload = {"Message": "code auth-secret-value rejected", "AuthenticationCode": "other"}
    assert redact(payload, {"auth-secret-value"}) == {
        "Message": f"code {REDACTED} rejected",
        "AuthenticationCode": REDACTED,
    }


def test_the_boundary_leaves_text_alone_when_no_literal_is_known() -> None:
    assert redact({"Message": "nothing to hide"}) == {"Message": "nothing to hide"}


def test_a_marker_can_name_what_kind_of_credential_was_hidden() -> None:
    """The API key is not a verification secret; labelling it as one would misinform."""
    assert redact_text("reached ...?apikey=abcd", {"abcd"}, marker=REDACTED_CREDENTIAL) == (
        f"reached ...?apikey={REDACTED_CREDENTIAL}"
    )
    assert redact({"ErrorCode": "abcd rejected"}, {"abcd"}, marker=REDACTED_CREDENTIAL) == {
        "ErrorCode": f"{REDACTED_CREDENTIAL} rejected"
    }

"""Fetcher and policy hardening for issues #4 (non-2xx content) and #9 (redirect hops)."""

import pytest

from airgap.core.audit import AuditLog
from airgap.core.fetcher import MAX_REDIRECTS, Fetcher, FetchError, Provenance, domain_of_url
from airgap.core.policy import PolicyEngine, UserContext, approve_all
from airgap.world.mock_web import MockWeb
from airgap.world.sites import Response


def only_ledgerlens(url: str) -> None:
    if domain_of_url(url) != "ledgerlens.test":
        raise FetchError(f"unlisted domain in redirect: {url}")


def web_with_redirect(location: str) -> MockWeb:
    web = MockWeb()
    web.add("http://ledgerlens.test/go", Response("", status=302, location=location))
    web.add("http://evil.test/p", "beacon")
    web.add("http://ledgerlens.test/ok", "<html><body>fine</body></html>")
    return web


def test_hop_check_runs_before_the_request_reaches_an_unlisted_host():
    web = web_with_redirect("http://evil.test/p")
    with pytest.raises(FetchError, match="unlisted"):
        Fetcher(web.client()).fetch("http://ledgerlens.test/go", check_hop=only_ledgerlens)
    assert all("evil.test" not in u for u in web.requests)


def test_a_bounce_through_an_unlisted_relay_is_denied_even_if_it_returns_home():
    web = MockWeb()
    web.add("http://ledgerlens.test/go", Response("", status=302, location="http://relay.test/r"))
    web.add("http://relay.test/r", Response("", status=302, location="http://ledgerlens.test/ok"))
    web.add("http://ledgerlens.test/ok", "ok")
    with pytest.raises(FetchError):
        Fetcher(web.client()).fetch("http://ledgerlens.test/go", check_hop=only_ledgerlens)
    assert all("relay.test" not in u for u in web.requests)


def test_redirects_inside_allowed_domains_still_work_and_relative_urls_resolve():
    web = web_with_redirect("/ok")
    doc = Fetcher(web.client()).fetch("http://ledgerlens.test/go", check_hop=only_ledgerlens)
    assert doc.provenance.final_url == "http://ledgerlens.test/ok"
    assert doc.provenance.redirect_chain == (
        "http://ledgerlens.test/go",
        "http://ledgerlens.test/ok",
    )


def test_redirect_loops_are_cut_off():
    web = MockWeb()
    web.add("http://ledgerlens.test/a", Response("", status=302, location="/a"))
    with pytest.raises(FetchError, match="redirect"):
        Fetcher(web.client()).fetch("http://ledgerlens.test/a", check_hop=only_ledgerlens)
    assert len(web.requests) <= MAX_REDIRECTS + 1


def test_non_http_redirect_targets_are_refused():
    web = web_with_redirect("file:///etc/passwd")
    with pytest.raises(FetchError):
        Fetcher(web.client()).fetch("http://ledgerlens.test/go", check_hop=only_ledgerlens)


def prov(status: int) -> Provenance:
    return Provenance(
        requested_url="http://ledgerlens.test/p",
        final_url="http://ledgerlens.test/p",
        registrable_domain="ledgerlens.test",
        redirect_chain=("http://ledgerlens.test/p",),
        status=status,
        content_type="text/html",
    )


@pytest.mark.parametrize("status", [301, 400, 404, 500, 503])
def test_non_2xx_responses_are_never_read_as_content(status):
    ctx = UserContext(user_text="see http://ledgerlens.test/p")
    d = PolicyEngine(ctx, AuditLog(), approve_all).check_fetch_result(prov(status))
    assert d.verdict == "deny" and "P7" in d.rules


@pytest.mark.parametrize("status", [200, 204])
def test_2xx_responses_are_readable(status):
    ctx = UserContext(user_text="see http://ledgerlens.test/p")
    assert PolicyEngine(ctx, AuditLog(), approve_all).check_fetch_result(prov(status)).verdict == (
        "allow"
    )

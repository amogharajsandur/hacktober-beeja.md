import httpx
import pytest

from beeja.pep import SourceError, parse_discourse_url, parse_pep_url, pep_discussion_urls, resolve_pep

PEP_758 = """PEP: 758
Title: Allow ``except`` and ``except*`` expressions without parentheses
Author: Pablo Galindo Salgado <pablogsal@python.org>, Brett Cannon <brett@python.org>
Status: Final
Type: Standards Track
Created: 30-Sep-2024
Python-Version: 3.14
Post-History: `02-Oct-2024 <https://discuss.python.org/t/66453>`__
Resolution: `14-Mar-2025 <https://discuss.python.org/t/66453/63>`__

Abstract
========

Body text https://discuss.python.org/t/999 must never matter.
"""

PEP_768 = """PEP: 768
Title: Safe external debugger interface for CPython
Discussions-To: https://discuss.python.org/t/pep-768-safe-external-debugger-interface-for-cpython/73969
Status: Final
Post-History: `01-Jan-2025 <https://discuss.python.org/t/11111>`__,
              `02-Jan-2025 <https://mail.python.org/archives/list/x@python.org/thread/ABC/>`__,
              `03-Jan-2025 <https://discuss.python.org/t/22222>`__
Resolution: https://discuss.python.org/t/pep-768/73969/99

Abstract
========
"""

PEP_MAIL = """PEP: 8
Title: Style Guide
Post-History: `05-Jul-2001 <https://mail.python.org/pipermail/python-list/2001-July/>`__

Body
"""


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://discuss.python.org/t/pep-758-allow-except/66453", ("https://discuss.python.org", 66453)),
        ("https://discuss.python.org/t/66453", ("https://discuss.python.org", 66453)),
        ("https://discuss.python.org/t/slug/66453/12", ("https://discuss.python.org", 66453)),
        ("https://discuss.python.org/t/slug/66453/", ("https://discuss.python.org", 66453)),
        ("https://forum.example.com/t/slug/5?u=x", ("https://forum.example.com", 5)),
    ],
)
def test_parse_discourse_valid(url, expected):
    assert parse_discourse_url(url) == expected


@pytest.mark.parametrize(
    "url",
    ["", "not a url", "https://discuss.python.org/", "https://discuss.python.org/c/peps/5", "https://x.org/t/slug", "ftp://x.org/t/1"],
)
def test_parse_discourse_invalid(url):
    assert parse_discourse_url(url) is None


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://peps.python.org/pep-0758/", 758),
        ("https://peps.python.org/pep-758", 758),
        ("https://www.python.org/dev/peps/pep-0758/", 758),
        ("PEP 758", 758),
        ("pep-758", 758),
        ("  Pep 8 ", 8),
    ],
)
def test_parse_pep_valid(url, expected):
    assert parse_pep_url(url) == expected


@pytest.mark.parametrize("url", ["", "https://example.com/pep-758", "PEP", "pep-x", "https://discuss.python.org/t/1"])
def test_parse_pep_invalid(url):
    assert parse_pep_url(url) is None


def test_header_post_history_only_excludes_resolution():
    urls = pep_discussion_urls(PEP_758)
    assert urls == ["https://discuss.python.org/t/66453"]
    assert not any("/63" in u for u in urls)


def test_header_discussions_to_then_history_latest_first():
    urls = pep_discussion_urls(PEP_768)
    assert urls == [
        "https://discuss.python.org/t/pep-768-safe-external-debugger-interface-for-cpython/73969",
        "https://discuss.python.org/t/22222",
        "https://discuss.python.org/t/11111",
    ]
    assert not any("mail.python.org" in u for u in urls)
    assert not any(u.endswith("/99") for u in urls)


def test_header_dedup():
    text = "PEP: 1\nDiscussions-To: https://discuss.python.org/t/1\nPost-History: `x <https://discuss.python.org/t/1>`__\n\nbody"
    assert pep_discussion_urls(text) == ["https://discuss.python.org/t/1"]


def _client(routes: dict[str, str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        body = routes.get(request.url.path.rsplit("/", 1)[-1])
        assert request.headers["user-agent"] == "beeja"
        return httpx.Response(404) if body is None else httpx.Response(200, text=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_resolve_success():
    base, tid, title = resolve_pep(758, client=_client({"pep-0758.rst": PEP_758}))
    assert (base, tid) == ("https://discuss.python.org", 66453)
    assert title.startswith("Allow ``except``")


def test_resolve_md_fallback():
    base, tid, _ = resolve_pep(768, client=_client({"pep-0768.md": PEP_768}))
    assert (base, tid) == ("https://discuss.python.org", 73969)


def test_resolve_never_returns_resolution_post_topic():
    text = PEP_758.replace("t/66453>`__\nResolution", "t/11>`__\nResolution")
    _, tid, _ = resolve_pep(758, client=_client({"pep-0758.rst": text}))
    assert tid == 11


def test_resolve_not_found():
    with pytest.raises(SourceError) as e:
        resolve_pep(9999, client=_client({}))
    assert e.value.kind == "not_found"


def test_resolve_no_discussion():
    with pytest.raises(SourceError) as e:
        resolve_pep(8, client=_client({"pep-0008.rst": PEP_MAIL}))
    assert e.value.kind == "no_discussion"
    assert "mailing lists" in str(e.value)


def test_resolve_network_error():
    def handler(request):
        raise httpx.ConnectError("boom")

    with pytest.raises(SourceError) as e:
        resolve_pep(758, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert e.value.kind == "network"

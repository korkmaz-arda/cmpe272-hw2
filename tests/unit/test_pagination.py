"""Unit tests for the Link-header pagination helpers."""

import pytest

from app.pagination import page_from_url, pagination_headers, parse_link_header

FULL_LINK = (
    '<https://api.github.com/repos/o/r/issues?page=2>; rel="next", '
    '<https://api.github.com/repos/o/r/issues?page=8>; rel="last", '
    '<https://api.github.com/repos/o/r/issues?page=1>; rel="first", '
    '<https://api.github.com/repos/o/r/issues?page=3>; rel="prev"'
)


def test_parses_every_rel():
    links = parse_link_header(FULL_LINK)
    assert set(links) == {"next", "last", "first", "prev"}
    assert links["next"].endswith("page=2")
    assert links["last"].endswith("page=8")


def test_parses_a_single_rel():
    assert parse_link_header('<https://api.github.com/x?page=2>; rel="next"') == {
        "next": "https://api.github.com/x?page=2"
    }


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_header_is_empty(value):
    assert parse_link_header(value) == {}


@pytest.mark.parametrize(
    "value",
    [
        "garbage",
        "<https://api.github.com/x?page=2>",  # no rel
        '; rel="next"',  # no url
        '<>; rel="next"',  # empty url
    ],
)
def test_malformed_segments_are_skipped_not_raised(value):
    assert parse_link_header(value) == {}


def test_unquoted_rel_is_accepted():
    assert parse_link_header("<https://api.github.com/x?page=5>; rel=next") == {
        "next": "https://api.github.com/x?page=5"
    }


def test_url_containing_a_comma_is_not_split():
    """`labels=bug,wontfix` must not be mistaken for two link entries."""
    header = (
        '<https://api.github.com/repos/o/r/issues?labels=bug,wontfix&page=2>; rel="next", '
        '<https://api.github.com/repos/o/r/issues?labels=bug,wontfix&page=9>; rel="last"'
    )
    links = parse_link_header(header)
    assert len(links) == 2
    assert links["next"].endswith("labels=bug,wontfix&page=2")


def test_good_segments_survive_a_broken_neighbour():
    header = 'not-a-link, <https://api.github.com/x?page=4>; rel="next"'
    assert parse_link_header(header) == {"next": "https://api.github.com/x?page=4"}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://api.github.com/x?page=7", 7),
        ("https://api.github.com/x?per_page=30&page=2", 2),
        ("https://api.github.com/x", None),
        ("https://api.github.com/x?page=abc", None),
        ("https://api.github.com/x?page=", None),
        (None, None),
        ("", None),
    ],
)
def test_page_from_url(url, expected):
    assert page_from_url(url) == expected


def test_pagination_headers_forwards_link_and_derives_pages():
    headers = pagination_headers(FULL_LINK, page=3, per_page=50)
    assert headers["Link"] == FULL_LINK
    assert headers["X-Page"] == "3"
    assert headers["X-Per-Page"] == "50"
    assert headers["X-Next-Page"] == "2"
    assert headers["X-Prev-Page"] == "3"
    assert headers["X-First-Page"] == "1"
    assert headers["X-Last-Page"] == "8"


def test_pagination_headers_without_a_link_header():
    headers = pagination_headers(None, page=1, per_page=30)
    assert headers == {"X-Page": "1", "X-Per-Page": "30"}

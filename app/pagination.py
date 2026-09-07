"""Helpers for GitHub's ``Link``-header pagination.

We deliberately do not invent a pagination scheme: GitHub's ``Link`` header is
forwarded verbatim and these helpers only derive convenience page numbers from
it.
"""

import re
from urllib.parse import parse_qs, urlparse

# Matches `<url>; rel="next"` entries. Splitting on "," would break on URLs that
# legitimately contain commas (e.g. `labels=bug,wontfix`), so we anchor on the
# angle brackets instead.
_LINK_ENTRY_RE = re.compile(r"<([^>]*)>([^<]*)")
_REL_RE = re.compile(r"""rel\s*=\s*(?:"([^"]*)"|'([^']*)'|([^,;\s]+))""")


def parse_link_header(value: str | None) -> dict[str, str]:
    """Parse a ``Link`` header into ``{rel: url}``.

    Missing, empty, or malformed input yields an empty mapping rather than an
    exception — a broken upstream header must never fail the request.
    """
    links: dict[str, str] = {}
    if not value:
        return links

    for url, params in _LINK_ENTRY_RE.findall(value):
        url = url.strip()
        if not url:
            continue
        match = _REL_RE.search(params)
        if not match:
            continue
        rel = next((group for group in match.groups() if group), "").strip()
        if rel:
            links[rel] = url
    return links


def page_from_url(url: str | None) -> int | None:
    """Extract the ``page`` query parameter from a URL, if it has a numeric one."""
    if not url:
        return None
    try:
        query = parse_qs(urlparse(url).query)
    except ValueError:
        return None
    values = query.get("page")
    if not values:
        return None
    try:
        return int(values[0])
    except (TypeError, ValueError):
        return None


def pagination_headers(link_header: str | None, page: int, per_page: int) -> dict[str, str]:
    """Build the pagination headers for one of our list responses.

    GitHub's ``Link`` header is passed through untouched; the ``X-*-Page``
    headers are conveniences derived from it.
    """
    headers = {"X-Page": str(page), "X-Per-Page": str(per_page)}
    if link_header:
        headers["Link"] = link_header

    links = parse_link_header(link_header)
    for rel, header in (
        ("next", "X-Next-Page"),
        ("prev", "X-Prev-Page"),
        ("first", "X-First-Page"),
        ("last", "X-Last-Page"),
    ):
        number = page_from_url(links.get(rel))
        if number is not None:
            headers[header] = str(number)
    return headers

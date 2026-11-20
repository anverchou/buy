"""Classify what was typed into the search bar: name, model number, UPC, ASIN or URL.

Pasted URLs are parsed locally - the app never loads the retailer's page.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

from .adapters.base import ParsedQuery
from .retailers import asin_from_url

UPC_RE = re.compile(r"^\d{8}$|^\d{12,14}$")
ASIN_RE = re.compile(r"^B0[A-Z0-9]{8}$", re.I)
SHORT_LINK_HOSTS = {"amzn.to", "a.co", "amzn.eu", "bit.ly", "goo.gl", "tinyurl.com"}

# host fragment -> (retailer key, regex for (slug, id) in the path)
URL_PATTERNS = [
    ("walmart.com", "walmart", re.compile(r"/ip/(?:(?P<slug>[^/]+)/)?(?P<id>\d+)")),
    ("target.com", "target", re.compile(r"/p/(?P<slug>[^/]+)/-/A-(?P<id>\d+)")),
    ("bestbuy.com", "bestbuy", re.compile(r"/site/(?P<slug>[^/]+)/(?P<id>\d+)\.p")),
    ("homedepot.com", "homedepot", re.compile(r"/p/(?P<slug>[^/]+)/(?P<id>\d+)")),
    ("costco.com", "costco", re.compile(r"/(?P<slug>[^/]+)\.product\.(?P<id>\d+)\.html")),
]


def looks_like_url(s: str) -> bool:
    return bool(re.match(r"^(https?://|www\.)", s.strip(), re.I)) or bool(
        re.match(r"^[a-z0-9.-]+\.(com|net|co|to)/", s.strip(), re.I))


def slug_to_text(slug: str) -> str:
    text = unquote(slug).replace("-", " ").replace("_", " ").replace("+", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_url(url: str) -> ParsedQuery:
    raw = url.strip()
    full = raw if re.match(r"^https?://", raw, re.I) else "https://" + raw
    parsed = urlparse(full)
    host = parsed.netloc.lower()
    for prefix in ("www.", "m.", "smile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    q = ParsedQuery(raw=raw, kind="url")

    if host in SHORT_LINK_HOSTS:
        q.note = ("Short links can't be expanded without visiting the site. "
                  "Open the link in your browser and paste the full address instead.")
        return q

    if "amazon." in host:
        q.retailer = "amazon"
        q.asin = asin_from_url(full)
        slug = re.search(r"amazon\.[a-z.]+/([^/]+)/(?:dp|gp)/", full)
        q.text = slug_to_text(slug.group(1)) if slug else ""
        if q.asin:
            q.kind = "asin"
            q.retailer_item_id = q.asin
        return q

    for fragment, key, pattern in URL_PATTERNS:
        if fragment in host:
            q.retailer = key
            m = pattern.search(parsed.path)
            if m:
                q.retailer_item_id = m.group("id")
                q.text = slug_to_text(m.group("slug") or "")
            break

    if not q.text:
        # Generic site (e.g. a manufacturer store): use the most descriptive path segment.
        segments = [s for s in parsed.path.split("/") if s and not s.isdigit()]
        segments = [re.sub(r"\.(html?|php|aspx?)$", "", s) for s in segments]
        best = max(segments, key=lambda s: (s.count("-") + s.count("_"), len(s)), default="")
        q.text = slug_to_text(best)
    if not q.text:
        q.note = "Couldn't read a product name from that link. Try the product name or model number."
    return q


def parse_query(raw: str) -> ParsedQuery:
    s = (raw or "").strip()
    if not s:
        return ParsedQuery(raw="", kind="text")
    if looks_like_url(s):
        return parse_url(s)
    compact = re.sub(r"[\s-]", "", s)
    if UPC_RE.match(compact):
        return ParsedQuery(raw=s, kind="upc", upc=compact, text=compact)
    if ASIN_RE.match(s):
        return ParsedQuery(raw=s, kind="asin", asin=s.upper(), text=s.upper())
    return ParsedQuery(raw=s, kind="text", text=s)

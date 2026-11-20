"""Search across sources and turn a chosen result into a tracked Product."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import or_, select

from ..adapters.base import ParsedQuery, ProductResult, SourceError
from ..models import Product
from ..query import parse_query
from .context import Ctx


@dataclass
class SearchOutcome:
    query: ParsedQuery
    results: list[ProductResult] = field(default_factory=list)
    existing: dict = field(default_factory=dict)   # result index -> product id already tracked
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def find_existing(session, r: ProductResult) -> Product | None:
    p = session.scalar(select(Product).where(Product.source == r.source, Product.source_ref == r.source_ref))
    if p or r.source == "mock":
        return p
    conds = []
    if r.google_product_id:
        conds.append(Product.google_product_id == r.google_product_id)
    if r.asin:
        conds.append(Product.asin == r.asin)
    if conds:
        return session.scalar(select(Product).where(Product.source != "mock", or_(*conds)))
    return None


def search(ctx: Ctx, raw: str) -> SearchOutcome:
    q = parse_query(raw)
    out = SearchOutcome(query=q)
    if q.note:
        out.notes.append(q.note)
    if not (q.text or q.asin or q.upc):
        return out
    searchers = ctx.sources.searchers()
    if not searchers:
        out.errors.append("No search source is configured. Add SERPAPI_API_KEY or KEEPA_API_KEY, "
                          "or set DATA_MODE=mock to use the demo data.")
        return out
    seen_refs, seen_codes = set(), set()
    for src in searchers:
        try:
            found = src.search(q)
        except SourceError as exc:
            out.errors.append(f"{src.label}: {exc}")
            continue
        for r in found:
            ref = (r.source, r.source_ref)
            # Same UPC/ASIN *and* same variant text = same item from two sources. Different
            # variants are always kept apart.
            code = (r.upc or r.asin, (r.variant or "").lower()) if (r.upc or r.asin) else None
            if ref in seen_refs or (code and code in seen_codes):
                continue
            seen_refs.add(ref)
            if code:
                seen_codes.add(code)
            out.results.append(r)
    for i, r in enumerate(out.results):
        p = find_existing(ctx.session, r)
        if p:
            out.existing[i] = p.id
    if q.kind == "url" and q.retailer and out.results:
        out.notes.append("Matched from the link's text - pick the exact size or color below.")
    return out


def select_result(ctx: Ctx, payload: dict) -> Product:
    r = ProductResult.from_payload(payload)
    product = find_existing(ctx.session, r)
    if product is None:
        product = Product(name=r.name[:300], brand=r.brand, model=r.model, variant=r.variant, upc=r.upc,
                          image_url=r.image_url, asin=r.asin, google_product_id=r.google_product_id,
                          source=r.source, source_ref=r.source_ref)
        ctx.session.add(product)
    else:  # fill gaps only; never overwrite what is already known
        for attr in ("brand", "model", "variant", "upc", "image_url", "asin", "google_product_id"):
            if getattr(product, attr) in (None, "") and getattr(r, attr):
                setattr(product, attr, getattr(r, attr))
    ctx.session.flush()
    return product

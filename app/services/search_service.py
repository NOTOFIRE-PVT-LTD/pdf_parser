"""Search helpers over extracted tender results."""

from __future__ import annotations

from app.models.schemas import ProductItem, TenderResult


def matches_query(result: TenderResult, query: str) -> bool:
    """Return True if query matches products, tender no, division, or zone."""
    q = (query or "").strip().lower()
    if not q:
        return True

    info = result.tender_information
    haystacks = [
        info.tender_no or "",
        info.name_of_work or "",
        info.division_name or "",
        info.zone or "",
        info.advertised_value or "",
    ]
    if any(q in (h or "").lower() for h in haystacks):
        return True

    for product in result.products:
        if product_matches(product, q):
            return True
    return False


def product_matches(product: ProductItem, query: str) -> bool:
    q = query.lower()
    fields = [
        product.s_no,
        product.item_code,
        product.description,
        product.schedule,
        product.item_qty,
        product.qty_unit,
        product.amount,
    ]
    return any(q in (f or "").lower() for f in fields)


def filter_products(result: TenderResult, query: str) -> list[ProductItem]:
    q = (query or "").strip()
    if not q:
        return list(result.products)
    return [p for p in result.products if product_matches(p, q)]

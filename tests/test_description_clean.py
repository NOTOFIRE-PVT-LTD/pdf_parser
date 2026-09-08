"""Description spacing / chrome cleanup and list-serial handling."""

from app.extractor.product_extractor import ProductExtractor
from app.models.schemas import ProductItem
from app.services.product_sanitize import sanitize_products
from app.utils.product_name import normalize_product_description
from app.utils.text_utils import (
    clean_item_description,
    is_list_serial,
    repair_spaced_text,
    strip_description_chrome,
)


def test_repair_spaced_letters_and_cable_size():
    assert repair_spaced_text("S u p p l y of 1 2 C x 1 . 5 S Q M M") == "Supply of 12 Cx 1.5 SQMM"
    assert (
        repair_spaced_text("Supply a n d fix i n g of 1 0 p a i r C T b o x")
        == "Supply and fixing of 10 pair CT box"
    )
    assert "as per" in (repair_spaced_text("cable a s p e r IRS") or "")
    assert repair_spaced_text("does n o t include") == "does not include"
    assert repair_spaced_text("S Q M M P V C insulated") == "SQMM PVC insulated"
    assert repair_spaced_text("fix i n g o f cables") == "fixing of cables"


def test_strip_tender_footer_and_consecutive_serials():
    raw = (
        "Hard drawn Cadmium copper catenary wire, 19/2.10mm "
        "Tender No: T-SG-36-I-6-2026-2027 Closing Date/Time: 03/09/2026 15:00 "
        "3. ITEM BREAKUP ScheduleSchedule A-Supply"
    )
    cleaned = strip_description_chrome(raw) or ""
    assert "Tender No" not in cleaned
    assert "ITEM BREAKUP" not in cleaned
    assert "catenary wire" in cleaned

    leaked = "end of sentence 60 61 62 more words about the same item"
    assert "60" not in (strip_description_chrome(leaked) or "")
    assert "2 4 Core" in (strip_description_chrome("Supply of 2 4 Core cable") or "")


def test_normalize_description_uses_cleaners():
    text = normalize_product_description(
        "S u p p l y of P o l y t h e n e cable Tender No: T-1 Closing Date/Time: 01/01/2026 15:00"
    )
    assert text
    assert text.startswith("Supply of Polythene")
    assert "Tender No" not in text


def test_railway_sor_code_is_not_item_serial():
    assert is_list_serial("12")
    assert is_list_serial("114")
    assert not is_list_serial("051010")
    assert not is_list_serial("51010")
    assert not is_list_serial("111440")

    products = sanitize_products(
        [
            ProductItem(
                s_no="051010",
                item_code="051010",
                item_qty="3000.00",
                unit_rate="10.00",
                amount="30000.00",
                description="Supply of 24 Core cable",
                schedule="Schedule A-Supply of Cables",
            ),
            ProductItem(
                s_no="051020",
                item_code="051020",
                item_qty="100.00",
                unit_rate="10.00",
                amount="1000.00",
                description="Supply of 12 Core cable",
                schedule="Schedule A-Supply of Cables",
            ),
        ]
    )
    assert [p.s_no for p in products] == ["1", "2"]
    assert products[0].item_code == "051010"


def test_ns_code_wins_over_wrong_serial():
    products = ProductExtractor()._merge_all(
        [
            ProductItem(
                s_no="1",
                item_code="NS1",
                item_qty="1.00",
                unit_rate="10.00",
                amount="10.00",
                description="First NS item",
                schedule="Schedule D-NS SCHEDULE",
            ),
            ProductItem(
                s_no="1",
                item_code="NS62",
                item_qty="2.00",
                unit_rate="10.00",
                amount="20.00",
                description="NS item sixty two",
                schedule="Schedule D-NS SCHEDULE",
            ),
        ]
    )
    by_code = {(p.item_code or "").upper(): p.s_no for p in products}
    assert by_code["NS1"] == "1"
    assert by_code["NS62"] == "62"

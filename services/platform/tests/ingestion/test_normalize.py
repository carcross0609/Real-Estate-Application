"""Address normalization + dedupe-key unit tests (no DB). The load-bearing property is that
differently-spelled renderings of the *same* address collapse to one `address_key` — the
join point of deterministic entity resolution (§14.3). A miss here is a duplicate property.
"""

from deallens.modules.ingestion.normalize import (
    address_key,
    normalize_address,
    normalize_street_line,
)


def test_normalize_street_line_standardizes_usps() -> None:
    assert normalize_street_line("123 North Main Street") == "123 N MAIN ST"
    assert normalize_street_line("45 South-West Oak Boulevard") == "45 SOUTH-WEST OAK BLVD"
    assert normalize_street_line("9 W Elm Ave.") == "9 W ELM AVE"


def test_normalize_street_line_is_total_on_empty() -> None:
    assert normalize_street_line("") == ""
    assert normalize_street_line("   ") == ""


def test_normalize_address_cleans_state_and_zip() -> None:
    addr = normalize_address(
        line1="742 evergreen terrace",
        city="springfield",
        state="or",
        zip_code="97403-1234",
    )
    assert addr.line1 == "742 EVERGREEN TER"
    assert addr.city == "SPRINGFIELD"
    assert addr.state == "OR"
    assert addr.zip == "97403"


def test_address_key_collapses_spelling_variants() -> None:
    a = normalize_address(line1="123 North Main Street", city="A", state="TX", zip_code="78701")
    b = normalize_address(line1="123 N MAIN ST", city="A", state="TX", zip_code="78701-0000")
    assert address_key(a) == address_key(b)


def test_address_key_distinguishes_units() -> None:
    a = normalize_address(
        line1="500 Oak Ave", line2="Apt 1", city="Reno", state="NV", zip_code="89501"
    )
    b = normalize_address(
        line1="500 Oak Ave", line2="Apt 2", city="Reno", state="NV", zip_code="89501"
    )
    # The two halves of a duplex must NOT collapse into one property.
    assert address_key(a) != address_key(b)


def test_address_key_distinguishes_zip() -> None:
    a = normalize_address(line1="1 Main St", city="A", state="TX", zip_code="78701")
    b = normalize_address(line1="1 Main St", city="B", state="TX", zip_code="78702")
    assert address_key(a) != address_key(b)

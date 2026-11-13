"""A SICI DOI's check character can be '#' (ANSI/NISO Z39.56: 0-9, X or #). Wiley prints these
DOIs as doi.org links verbatim; cutting at '#' as a URL fragment drops the check character and
leaves a DOI no registry knows, so the real paper ends as "does not resolve (doi.org)"."""

from claimtrace.ingest.text import find_doi

SICI = "10.1002/(sici)1097-0258(19980430)17:8<857::aid-sim777>3.0.co;2-#"


def test_sici_hash_check_character_in_doi_link():
    raw = (
        "Smith A. Title. Stat Med. 1998;17:857-72. "
        "https://doi.org/10.1002/(SICI)1097-0258(19980430)17:8<857::AID-SIM777>3.0.CO;2-#"
    )
    # today: "...3.0.co;2-" (without the check character); "doi:" form keeps the '#'
    assert find_doi(raw) == SICI

"""find_doi must return the DOI the manuscript cites, whole and nothing more.

A wrong DOI string does not resolve, so a correct reference comes back as a DOI mismatch (or
not_found when only the DOI could be extracted): a false accusation of a bad citation.
"""

from claimtrace.ingest.text import find_doi


def test_sici_doi_is_extracted_whole():
    # SICI DOIs (Wiley, 1990s) contain '<' and '>'
    raw = (
        "Newcombe RG. Two-sided confidence intervals for the single proportion: comparison of "
        "seven methods. Stat Med. 1998;17(8):857-872. "
        "doi:10.1002/(SICI)1097-0258(19980430)17:8<857::AID-SIM777>3.0.CO;2-E"
    )
    # today: "10.1002/(sici)1097-0258(19980430)17:8"
    assert find_doi(raw) == "10.1002/(sici)1097-0258(19980430)17:8<857::aid-sim777>3.0.co;2-e"


def test_doi_in_markdown_link_stops_at_the_link_syntax():
    # .md manuscripts are accepted; reference lists there link their DOIs
    raw = (
        "LeCun Y, Bengio Y, Hinton G. Deep learning. Nature. 2015;521:436-444. "
        "[doi:10.1038/nature14539](https://doi.org/10.1038/nature14539)"
    )
    # today: "10.1038/nature14539](https://doi.org/10.1038/nature14539)"
    assert find_doi(raw) == "10.1038/nature14539"

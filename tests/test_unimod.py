import pytest

from sdrf_pipelines.openms.unimod import UnimodDatabase


@pytest.fixture(scope="package")
def unimod_db():
    return UnimodDatabase()


def test_search_mods_by_accession(unimod_db):
    ptm = unimod_db.get_by_accession("UNIMOD:21")
    assert ptm.get_name() == "Phospho"


def test_search_mods_by_keyword(unimod_db):
    ptms = [ptm.to_str() for ptm in unimod_db.search_mods_by_keyword("Phospho")]
    assert "UNIMOD:21 Phospho 79.966331 H O(3) P" in ptms
    assert len(ptms) == 27

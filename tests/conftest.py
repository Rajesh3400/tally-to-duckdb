import pytest

from tally_to_duckdb import db as dbmod
from tally_to_duckdb.config import get_settings


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Every test gets its own empty database file (schema applied on open)."""
    monkeypatch.setenv("T2D_DB", str(tmp_path / "test.duckdb"))
    get_settings.cache_clear()
    dbmod.close()
    yield
    dbmod.close()
    get_settings.cache_clear()


@pytest.fixture
def db():
    with dbmod.session() as d:
        yield d

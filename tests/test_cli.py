import pytest

from tally_to_duckdb import cli


def test_init_add_source_list_status(capsys):
    cli.main(["init"])
    cli.main(["add-source", "--company", "Acme Ltd 25-26", "--books-from", "2025-04-01"])
    with pytest.raises(SystemExit):
        cli.main(["add-source", "--company", "Acme Ltd 25-26", "--books-from", "2025-04-01"])
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "Added source 1" in out and "Acme Ltd 25-26" in out and "last sync never" in out

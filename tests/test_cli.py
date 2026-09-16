"""CLI surface and exit codes.

The exit codes are the contract a CI job or a shell-based harness depends on,
so they are pinned here rather than left to whatever the renderer happens to do.
"""

import json

import pytest

from candor.cli import EXIT_CAVEATS, EXIT_INSUFFICIENT, EXIT_OK, EXIT_UNREADABLE, main


def run(capsys, *argv):
    code = main(list(argv))
    return code, capsys.readouterr()


def test_profile_json_is_machine_readable(capsys, clean_csv):
    code, out = run(capsys, "profile", str(clean_csv), "--json")
    payload = json.loads(out.out)
    assert payload["row_count"] == 36
    assert payload["grade"]
    assert code in (EXIT_OK, EXIT_CAVEATS)


def test_profile_of_bad_data_signals_insufficient(capsys, write):
    path = write("bad.csv", "a,b\n")          # zero rows
    code, _ = run(capsys, "profile", str(path))
    assert code == EXIT_INSUFFICIENT


def test_assess_exit_codes(capsys, recent_csv):
    ok, _ = run(capsys, "assess", str(recent_csv), "-q", "What was total revenue by region?")
    assert ok == EXIT_OK

    bad, _ = run(capsys, "assess", str(recent_csv), "-q", "What is the average NPS score?")
    assert bad == EXIT_INSUFFICIENT


def test_verify_rejects_an_unsupported_claim(capsys, clean_csv):
    code, _ = run(capsys, "verify", str(clean_csv), "-a", "We had 90,000 orders.")
    assert code == EXIT_INSUFFICIENT


def test_verify_passes_a_supported_claim(capsys, clean_csv):
    code, _ = run(capsys, "verify", str(clean_csv), "-a", "There are three regions.")
    assert code == EXIT_OK


def test_kit_emits_the_injectable_block(capsys, clean_csv):
    code, out = run(capsys, "kit", str(clean_csv), "-q", "What was revenue by region?")
    payload = json.loads(out.out)
    assert code == EXIT_OK
    assert set(payload) >= {
        "verdict", "confidence_ceiling", "must_say", "must_not_claim",
        "honest_response", "instruction",
    }


def test_gate_passes_good_data(capsys, recent_csv):
    code, _ = run(capsys, "gate", str(recent_csv), "--min-grade", "B")
    assert code == EXIT_OK


def test_gate_fails_bad_data(capsys, messy_csv):
    code, out = run(capsys, "gate", str(messy_csv), "--min-grade", "B")
    assert code != EXIT_OK
    assert "FAILED" in out.err


def test_gate_json_reports_failures(capsys, messy_csv):
    _, out = run(capsys, "gate", str(messy_csv), "--min-grade", "A", "--json")
    payload = json.loads(out.out)
    assert payload["passed"] is False
    assert payload["failures"]


def test_unreadable_source_exits_distinctly(capsys):
    code, out = run(capsys, "profile", "/nonexistent/nope.csv")
    assert code == EXIT_UNREADABLE
    assert "candor:" in out.err


def test_improve_lists_steps(capsys, messy_csv):
    code, out = run(capsys, "improve", str(messy_csv), "--json")
    assert code == EXIT_CAVEATS
    assert json.loads(out.out)["steps"]


def test_tables_lists_sqlite_tables(capsys, tmp_path):
    import sqlite3

    db = tmp_path / "x.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE alpha (x INTEGER)")
    conn.execute("CREATE TABLE beta (y INTEGER)")
    conn.commit()
    conn.close()
    code, out = run(capsys, "tables", str(db))
    assert code == EXIT_OK
    assert out.out.split() == ["alpha", "beta"]


def test_version(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0

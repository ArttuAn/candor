"""Loading."""

import sqlite3

import pytest

from candor.sources import SourceError, load


def test_csv_roundtrip(clean_csv):
    table = load(clean_csv)
    assert table.columns == ["date", "region", "revenue", "customer_id"]
    assert table.row_count == 36
    assert table.format == "csv"


def test_semicolon_delimiter_is_sniffed(write):
    path = write("semi.csv", "a;b;c\n1;2;3\n4;5;6\n")
    assert load(path).columns == ["a", "b", "c"]


def test_duplicate_headers_are_renamed(write):
    path = write("dupe.csv", "id,name,name\n1,a,b\n")
    table = load(path)
    assert table.columns == ["id", "name", "name_1"]
    assert any("duplicate header" in note for note in table.notes)


def test_blank_header_is_named(write):
    path = write("blank.csv", "id,,value\n1,x,2\n")
    table = load(path)
    assert table.columns[1] == "column_2"


def test_ragged_rows_are_padded_and_reported(write):
    path = write("ragged.csv", "a,b,c\n1,2\n3,4,5,6\n")
    table = load(path)
    assert table.rows[0] == ["1", "2", ""]
    assert table.rows[1] == ["3", "4", "5"]
    assert len(table.notes) == 2


def test_bom_is_stripped(write, tmp_path):
    path = tmp_path / "bom.csv"
    path.write_bytes(b"\xef\xbb\xbfid,name\n1,x\n")
    table = load(path)
    assert table.columns == ["id", "name"]
    assert any("BOM" in note for note in table.notes)


def test_json_array(write_json):
    path = write_json("a.json", [{"x": 1, "y": "a"}, {"x": 2, "y": "b"}])
    table = load(path)
    assert table.columns == ["x", "y"]
    assert table.row_count == 2


def test_json_envelope_is_unwrapped(write_json):
    path = write_json("b.json", {"status": "ok", "data": [{"x": 1}, {"x": 2}]})
    table = load(path)
    assert table.row_count == 2
    assert any("wrapper" in note for note in table.notes)


def test_json_ragged_keys_union(write_json):
    path = write_json("c.json", [{"a": 1}, {"b": 2}])
    table = load(path)
    assert table.columns == ["a", "b"]
    assert table.rows == [["1", ""], ["", "2"]]


def test_jsonl(write):
    path = write("d.jsonl", '{"a":1}\n{"a":2}\nnot json\n{"a":3}\n')
    table = load(path)
    assert table.row_count == 3
    assert any("not valid JSON" in note for note in table.notes)


def test_sqlite_single_table(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sales (id INTEGER, amount REAL)")
    conn.executemany("INSERT INTO sales VALUES (?,?)", [(1, 10.0), (2, 20.0)])
    conn.commit()
    conn.close()
    table = load(db)
    assert table.columns == ["id", "amount"]
    assert table.row_count == 2


def test_sqlite_multiple_tables_requires_a_choice(tmp_path):
    db = tmp_path / "m.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE a (x INTEGER)")
    conn.execute("CREATE TABLE b (y INTEGER)")
    conn.commit()
    conn.close()
    with pytest.raises(SourceError, match="pick one"):
        load(db)
    assert load(db, table="a").columns == ["x"]


def test_max_rows_marks_truncation(write):
    path = write("big.csv", "a\n" + "\n".join(str(i) for i in range(100)) + "\n")
    table = load(path, max_rows=10)
    assert table.row_count == 10
    assert table.truncated


def test_missing_file():
    with pytest.raises(SourceError, match="no such file"):
        load("/nonexistent/nowhere.csv")


def test_empty_file(write):
    assert load(write("empty.csv", "")).row_count == 0

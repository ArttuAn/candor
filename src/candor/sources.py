"""Loading tabular data from whatever the agent happens to have on disk.

CSV/TSV, JSON, JSONL, SQLite and in-memory records need no dependencies.
Parquet works if pyarrow is installed; otherwise it fails with a clear message.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .detect import coerce_to_str

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

DEFAULT_MAX_ROWS = 200_000


class SourceError(RuntimeError):
    """The data could not be read at all — distinct from the data being bad."""


@dataclass
class Table:
    """A rectangular block of strings, plus where it came from."""

    columns: list[str]
    rows: list[list[str]] = field(default_factory=list)
    source: str = "<memory>"
    format: str = "records"
    truncated: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def column_values(self, index: int) -> list[str]:
        return [row[index] if index < len(row) else "" for row in self.rows]


def _sniff_dialect(sample: str) -> csv.Dialect | type[csv.Dialect]:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def _dedupe_headers(headers: Iterable[str]) -> tuple[list[str], list[str]]:
    """Make headers unique and usable; report what had to be repaired."""
    out: list[str] = []
    notes: list[str] = []
    seen: dict[str, int] = {}
    for i, raw in enumerate(headers):
        name = (raw or "").strip()
        if not name:
            name = f"column_{i + 1}"
            notes.append(f"column {i + 1} has no header; named {name!r}")
        key = name.lower()
        if key in seen:
            seen[key] += 1
            notes.append(f"duplicate header {name!r} renamed to {name}_{seen[key]}")
            name = f"{name}_{seen[key]}"
        else:
            seen[key] = 0
        out.append(name)
    return out, notes


def from_csv(path: Path, max_rows: int = DEFAULT_MAX_ROWS, delimiter: str | None = None) -> Table:
    raw = path.read_bytes()
    notes: list[str] = []
    if raw.startswith(b"\xef\xbb\xbf"):
        notes.append("file starts with a UTF-8 BOM")
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
        notes.append("file is not valid UTF-8; undecodable bytes replaced with U+FFFD")

    dialect: Any = csv.excel
    if delimiter:
        dialect = csv.excel
        dialect.delimiter = delimiter  # type: ignore[attr-defined]
    else:
        dialect = _sniff_dialect(text[:64_000])

    reader = csv.reader(io.StringIO(text, newline=""), dialect)
    try:
        header = next(reader)
    except StopIteration:
        return Table(columns=[], rows=[], source=str(path), format="csv", notes=["file is empty"])

    columns, header_notes = _dedupe_headers(header)
    notes.extend(header_notes)
    width = len(columns)

    rows: list[list[str]] = []
    ragged_short = ragged_long = 0
    truncated = False
    for record in reader:
        if not record or (len(record) == 1 and not record[0].strip()):
            continue
        if len(record) < width:
            ragged_short += 1
            record = record + [""] * (width - len(record))
        elif len(record) > width:
            ragged_long += 1
            record = record[:width]
        rows.append(record)
        if len(rows) >= max_rows:
            truncated = True
            break

    if ragged_short:
        notes.append(f"{ragged_short} rows had fewer fields than the header; padded")
    if ragged_long:
        notes.append(f"{ragged_long} rows had more fields than the header; extra fields dropped")

    return Table(columns=columns, rows=rows, source=str(path), format="csv",
                 truncated=truncated, notes=notes)


def from_records(records: list[dict[str, Any]], source: str = "<memory>",
                 fmt: str = "records", max_rows: int = DEFAULT_MAX_ROWS) -> Table:
    """Flatten a list of dicts. Nested values are JSON-encoded, not exploded."""
    truncated = len(records) > max_rows
    records = records[:max_rows]
    columns: list[str] = []
    seen: set[str] = set()
    for record in records:
        for key in record:
            if key not in seen:
                seen.add(key)
                columns.append(str(key))
    rows = []
    for record in records:
        row = []
        for key in columns:
            value = record.get(key)
            if isinstance(value, (dict, list)):
                row.append(json.dumps(value, ensure_ascii=False))
            else:
                row.append(coerce_to_str(value))
        rows.append(row)
    return Table(columns=columns, rows=rows, source=source, format=fmt, truncated=truncated)


def _find_record_list(payload: Any) -> list[dict[str, Any]] | None:
    """Pull the record array out of a typical API-shaped JSON envelope."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)] or None
    if isinstance(payload, dict):
        for key in ("data", "results", "records", "rows", "items", "value"):
            found = _find_record_list(payload.get(key))
            if found:
                return found
        if all(not isinstance(v, (dict, list)) for v in payload.values()):
            return [payload]
        for value in payload.values():
            found = _find_record_list(value)
            if found:
                return found
    return None


def from_json(path: Path, max_rows: int = DEFAULT_MAX_ROWS) -> Table:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise SourceError(f"{path}: invalid JSON at line {exc.lineno}: {exc.msg}") from exc
    records = _find_record_list(payload)
    if records is None:
        raise SourceError(f"{path}: no array of objects found in this JSON document")
    table = from_records(records, source=str(path), fmt="json", max_rows=max_rows)
    if isinstance(payload, dict):
        table.notes.append("records were extracted from a wrapper object")
    return table


def from_jsonl(path: Path, max_rows: int = DEFAULT_MAX_ROWS) -> Table:
    records: list[dict[str, Any]] = []
    bad = 0
    truncated = False
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if isinstance(value, dict):
                records.append(value)
            else:
                bad += 1
            if len(records) >= max_rows:
                truncated = True
                break
    if not records:
        raise SourceError(f"{path}: no valid JSON objects found")
    table = from_records(records, source=str(path), fmt="jsonl", max_rows=max_rows)
    table.truncated = truncated
    if bad:
        table.notes.append(f"{bad} lines were not valid JSON objects and were skipped")
    return table


def from_sqlite(path: Path, table_name: str | None = None,
                query: str | None = None, max_rows: int = DEFAULT_MAX_ROWS) -> Table:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        if not query:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            if not names:
                raise SourceError(f"{path}: database has no tables")
            if table_name is None:
                if len(names) > 1:
                    raise SourceError(
                        f"{path} holds {len(names)} tables ({', '.join(names)}); "
                        "pick one with table=<name>")
                table_name = names[0]
            elif table_name not in names:
                raise SourceError(f"{path}: no table named {table_name!r} (have: {', '.join(names)})")
            query = f'SELECT * FROM "{table_name}" LIMIT {max_rows + 1}'
        cursor = conn.execute(query)
        columns = [d[0] for d in cursor.description]
        fetched = cursor.fetchall()
    except sqlite3.Error as exc:
        raise SourceError(f"{path}: {exc}") from exc
    finally:
        conn.close()

    truncated = len(fetched) > max_rows
    rows = [[coerce_to_str(v) for v in row] for row in fetched[:max_rows]]
    return Table(columns=columns, rows=rows, source=f"{path}::{table_name or 'query'}",
                 format="sqlite", truncated=truncated)


def from_parquet(path: Path, max_rows: int = DEFAULT_MAX_ROWS) -> Table:
    try:
        import pyarrow.parquet as pq  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SourceError(
            "reading parquet needs pyarrow — install candor-gate with the 'parquet' extra"
        ) from exc
    table = pq.read_table(path)
    truncated = table.num_rows > max_rows
    records = table.slice(0, max_rows).to_pylist()
    result = from_records(records, source=str(path), fmt="parquet", max_rows=max_rows)
    result.truncated = truncated
    return result


SUFFIX_LOADERS = {
    ".csv": "csv", ".tsv": "csv", ".txt": "csv",
    ".json": "json", ".jsonl": "jsonl", ".ndjson": "jsonl",
    ".db": "sqlite", ".sqlite": "sqlite", ".sqlite3": "sqlite",
    ".parquet": "parquet", ".pq": "parquet",
}


def load(source: str | Path, *, max_rows: int = DEFAULT_MAX_ROWS,
         table: str | None = None, query: str | None = None) -> Table:
    """Load any supported source, dispatching on suffix then on content."""
    path = Path(source).expanduser()
    if not path.exists():
        raise SourceError(f"no such file: {path}")
    if path.is_dir():
        raise SourceError(f"{path} is a directory; point candor at a single data file")

    kind = SUFFIX_LOADERS.get(path.suffix.lower())
    if kind is None:
        head = path.read_bytes()[:16]
        if head.startswith(b"SQLite format 3"):
            kind = "sqlite"
        elif head.lstrip()[:1] in (b"[", b"{"):
            kind = "json"
        else:
            kind = "csv"

    if kind == "csv":
        delimiter = "\t" if path.suffix.lower() == ".tsv" else None
        return from_csv(path, max_rows=max_rows, delimiter=delimiter)
    if kind == "json":
        try:
            return from_json(path, max_rows=max_rows)
        except SourceError:
            return from_jsonl(path, max_rows=max_rows)
    if kind == "jsonl":
        return from_jsonl(path, max_rows=max_rows)
    if kind == "sqlite":
        return from_sqlite(path, table_name=table, query=query, max_rows=max_rows)
    if kind == "parquet":
        return from_parquet(path, max_rows=max_rows)
    raise SourceError(f"unsupported source type: {path}")


def list_sqlite_tables(path: str | Path) -> list[str]:
    conn = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    try:
        return [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    finally:
        conn.close()

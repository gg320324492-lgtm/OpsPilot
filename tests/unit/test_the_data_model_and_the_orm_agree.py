"""The data model's tables must be the ORM's tables.

## Why this file exists

``docs/data-model.md`` §2 is the reference for every table in the schema: it
gives each table's columns, their types, and the notes that a reader needs to
use them. The ORM in ``src/opspilot/adapters/persistence/models.py`` is what
the database actually gets.

Two things can drift between them, and neither is visible from the route:

* **A column is added to the ORM and not to the document.** The schema moves
  and the reference does not, so a reader looking up a column finds a table
  that does not have it -- and a reader looking for what *is* there does not
  learn why.
* **A column is renamed in the ORM and not in the document.** The same, with a
  quieter failure: the name in the document resolves to nothing.

The comparison is by **attribute name**, not by database column name, and the
two are not the same everywhere: ``knowledge_documents.doc_metadata`` is the
attribute and ``metadata`` is the column, because ``metadata`` is reserved on
a declarative class. The document names the attribute, so the comparison uses
it.

## What this file asserts

1. **Every table the ORM declares is a section of §2**, and every section of
   §2 is a table the ORM declares.
2. **Every column of each table is in that table's section**, and every column
   the section lists is a column of that table.
3. **The document says why the two names differ**, because that is the one
   place a reader would otherwise be confused.

## What it does not do

It does not check the types, the nullability, or the defaults -- the document
writes those in prose and the ORM in Python, and comparing them would be
comparing prose to code. It checks the *shape*: which tables exist and which
columns they have.
"""

from __future__ import annotations

import pathlib
import re
from typing import cast

from sqlalchemy import Table

from opspilot.adapters.persistence.models import Base

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCUMENT = REPO_ROOT / "docs" / "data-model.md"

#: A section header: ``### `tickets` ``.
_SECTION = re.compile(r"^### `([a-z_]+)`")


def _documented_tables() -> dict[str, list[str]]:
    """The tables §2 documents, with the columns each one lists.

    The first column of each section's table is the column name. The header
    row and its separator are skipped, and so is any table that follows the
    sections -- §7's prose tables are not schema.
    """
    lines = DOCUMENT.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("## 2. Tables"))
    tables: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines[start + 1 :]:
        if line.startswith("## ") and not line.startswith("### "):
            break
        header = _SECTION.match(line)
        if header is not None:
            current = header.group(1)
            tables[current] = []
            continue
        if current is None or not line.startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if not cells or not cells[0]:
            continue
        if set(cells[0]) <= {"-", " "}:
            continue
        if cells[0] in {"Column", "Type", "Notes"}:
            continue
        tables[current].append(cells[0])
    return tables


def _orm_tables() -> dict[str, list[str]]:
    """The tables the ORM declares, with their attribute names.

    The attribute name is what the document uses, and it is not always the
    column name -- ``mapped_column("metadata")`` gives the attribute
    ``doc_metadata`` and the column ``metadata``.
    """
    tables: dict[str, list[str]] = {}
    for mapper in Base.registry.mappers:
        table = cast("Table", mapper.local_table)
        tables[table.name] = [attribute.key for attribute in mapper.column_attrs]
    return tables


def test_the_data_model_and_the_orm_agree_on_their_tables_and_columns() -> None:
    """Every table and every column is the same on both sides."""
    documented = _documented_tables()
    orm = _orm_tables()

    assert documented, "the data model's table section could not be read"
    assert orm, "the ORM declares no tables"

    only_in_orm = sorted(set(orm) - set(documented))
    only_in_doc = sorted(set(documented) - set(orm))
    assert not only_in_orm, f"the ORM has tables the data model does not document: {only_in_orm}"
    assert not only_in_doc, f"the data model documents tables the ORM does not have: {only_in_doc}"

    problems: list[str] = []
    for table, columns in sorted(orm.items()):
        doc_columns = set(documented[table])
        missing = sorted(set(columns) - doc_columns)
        extra = sorted(doc_columns - set(columns))
        if missing:
            problems.append(f"{table}: the document does not list {missing}")
        if extra:
            problems.append(f"{table}: the document lists {extra}, which the ORM does not have")
    assert not problems, (
        "the data model and the ORM disagree:\n  "
        + "\n  ".join(problems)
        + "\n\nThe document is the reference for the schema; a reader who looks up a "
        "column there must find it."
    )


def test_the_document_says_why_the_attribute_and_the_column_differ() -> None:
    """``doc_metadata`` is an attribute; ``metadata`` is the column.

    The one place the two names differ is the one place a reader is confused,
    and the document explains it -- so this checks the explanation is still
    there.
    """
    text = DOCUMENT.read_text(encoding="utf-8")
    assert "`doc_metadata`" in text, "the document no longer names the attribute"
    assert "reserved" in text, (
        "the document no longer says why the attribute and the column have different names"
    )
    assert "metadata" in Base.metadata.tables["knowledge_documents"].columns, (
        "the knowledge_documents table no longer has a `metadata` column"
    )

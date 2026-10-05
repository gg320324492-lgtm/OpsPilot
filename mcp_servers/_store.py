"""JSON-backed store shared by the three demo MCP servers.

Each MCP server (``crm``, ``billing``, ``issues``) owns a single JSON file that
is its database. The file is created from a versioned ``seed.json`` committed
beside the server module, so a fresh clone reproduces the demo exactly and a
pytest fixture can put every test back on the committed data.

Why a plain JSON file and not SQLite
------------------------------------

These servers stand in for Salesforce, Stripe and Jira. The dataset is tiny
(tens of rows), the demo is read-mostly, and the whole file fits in memory. A
JSON document that is loaded once and written atomically is simpler to reason
about and simpler to seed than a schema and a migration, and it keeps the
"the demo's data lives in git" property that ``docs/mcp-contracts.md`` S5 cares
about. The one place a store must be exactly right -- a financial write that
must not happen twice -- is handled by the caller (the billing server's
idempotency check over an immutable record), not by the storage engine.

Atomicity guarantee
-------------------

:meth:`Store.save` serialises the whole document, writes it to a temporary file
**in the same directory**, flushes and ``fsync``\\ s it, then ``os.replace``\\ s it
onto the real path. ``os.replace`` is atomic on POSIX and on Windows, so a reader
ever sees either the complete previous document or the complete new one -- never
a half-written file. The ``fsync`` is what makes the guarantee survive a power
loss rather than only a process crash: without it the rename can land while the
data is still in the OS page cache.

This matters because the store *is* the demo's database. A truncated JSON file
would make the golden path unreproducible and the failure would show up as a
confusing ``StoreError`` far from the write that caused it.

Concurrency assumption
----------------------

**One process per server.** Each server is a standalone stdio process with its
own store file, per ``docs/architecture.md`` S8, so two processes never write the
same file, and within a process the tools are served one call at a time off the
stdio loop. That is why there is **no file locking** here: a lock would protect
against a scenario this deployment does not have, and would add a failure mode
(a stale lock file) in exchange for nothing.

The atomic replace is not locking and does not pretend to be. It guarantees that
*if* two writers did race -- which this design assumes they do not -- the loser's
whole document is discarded rather than interleaved, so the file is never
corrupt. It says nothing about which writer wins, and it is not a substitute for
the idempotency keys that guard the mutating tools.

Default location
----------------

If ``OPSPILOT_MCP_DATA_DIR`` is set, the store file lives there. Otherwise it
lives beside the seed file. The environment variable exists so tests and CI can
give each server an isolated directory (``tmp_path``) without touching the
repository, and so a Compose deployment can point all three servers at a mounted
volume.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "Record",
    "Store",
    "StoreError",
]

_ENV_DATA_DIR = "OPSPILOT_MCP_DATA_DIR"

# Where a store writes when nothing is configured. Deliberately a sibling of the
# source tree's contents rather than inside the package -- see
# `default_data_dir` for the incident that made this matter.
_DEFAULT_DATA_SUBDIR = ".opspilot-data"


class StoreError(RuntimeError):
    """The seed file is missing, unreadable, or not valid JSON.

    Raised with a message naming the offending path so a misconfigured server
    fails loudly at startup with something a human can act on, rather than
    surfacing as a ``KeyError`` three call frames deep.

    The message is built by the constructors below rather than at each raise
    site: one exception type, one place that decides what a store failure looks
    like, and every call site reads as ``raise StoreError.missing_seed(path)``
    rather than as a hand-assembled f-string.
    """

    @classmethod
    def missing_seed(cls, path: Path) -> StoreError:
        """The seed file does not exist."""
        return cls(f"seed file not found: {path}")

    @classmethod
    def unreadable_seed(cls, path: Path, reason: object) -> StoreError:
        """The seed file exists but could not be read."""
        return cls(f"could not read seed file {path}: {reason}")

    @classmethod
    def malformed_seed(cls, path: Path, reason: object) -> StoreError:
        """The seed file is not valid JSON."""
        return cls(f"seed file {path} is not valid JSON: {reason}")

    @classmethod
    def not_an_object(cls, path: Path) -> StoreError:
        """The seed file's top level is not a JSON object."""
        return cls(f"seed file {path} must contain a JSON object at the top level")

    @classmethod
    def bad_collection(cls, name: str, path: Path) -> StoreError:
        """A top-level key expected to hold a list holds something else."""
        return cls(f"collection {name!r} in {path} is not a list")

    @classmethod
    def not_serialisable(cls, path: Path, reason: object) -> StoreError:
        """The in-memory document cannot be written as JSON."""
        return cls(f"store {path} is not JSON-serialisable: {reason}")

    @classmethod
    def unwritable(cls, path: Path, reason: object) -> StoreError:
        """The store file could not be written."""
        return cls(f"could not write store {path}: {reason}")


class Record(BaseModel):
    """An immutable, typed view of one stored object.

    A ``Record`` is a frozen pydantic model with ``extra="allow"``: the seed
    document defines the fields (``customer_id``, ``company``, ...) and the
    model carries them through untouched while giving the caller attribute
    access with types instead of ``dict`` indexing. It is deliberately *not* an
    ORM -- there is no schema to keep in sync, no identity map and no lazy
    loading. ``record.get("company")`` is the same string that is in the file.

    Frozen because a read tool must not be able to mutate the store's in-memory
    state by accident: a caller that wants to change a record builds a new one
    (or, for a mutation, edits the document and calls :meth:`Store.save`).
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    def get(self, field: str, default: object = None) -> object:
        """Return ``field``'s value, or ``default`` if the record lacks it.

        The seed defines the fields, so mypy cannot know them from the model --
        ``record.get("company")`` is the typed way to read one and
        ``record.model_dump()`` is the way to get the whole object back out.
        Servers that need a specific typed shape validate the dump into their
        own return model (see ``mcp_servers/crm/server.py``), which is where the
        real typing lives.
        """
        return self.model_dump().get(field, default)


class Store:
    """A JSON document loaded from a seed file, with atomic persistence.

    Typical use inside a server::

        store = Store(seed_path=Path(__file__).parent / "seed.json", filename="crm.json")
        customers = store.collection("customers")  # list[Record]
        customer = store.find("customers", customer_id="CUS-1001")

    The store's state *is* the loaded document: a :class:`dict` sitting in
    memory under :attr:`data`. Reads go through :meth:`collection` and
    :meth:`find`; mutations edit :attr:`data` in place and then call
    :meth:`save` once, so a tool that touches two collections writes one file.
    """

    def __init__(
        self,
        seed_path: Path,
        *,
        filename: str | None = None,
        data_dir: Path | None = None,
    ) -> None:
        """Load the live store into memory, falling back to the seed on first run.

        The live file wins when it exists, because that is what "persistence"
        means: a store that writes a mutation and then, on the next construction,
        reads the pristine seed has not persisted anything. It reads the *seed*
        only when there is no live file yet -- the first run -- and it is exactly
        the seed that :meth:`reset` restores from.

        This was previously backwards: every construction read the seed, so a
        refund written by one server instance was invisible to the next one. A
        worker restarted after a refund would have seen the transaction as
        ``charged`` again, which is precisely the state the duplicate-refund
        defence consults.

        Args:
            seed_path: Path to the committed ``seed.json``. Read when there is no
                live file, and again by every :meth:`reset`.
            filename: Name of the persisted store file. Defaults to the seed
                file's name; servers pass e.g. ``"crm.json"`` so the live store
                and the seed are distinct and :meth:`reset` is meaningful.
            data_dir: Directory holding the store file. Defaults to
                :func:`default_data_dir`, which honours
                ``OPSPILOT_MCP_DATA_DIR`` and otherwise writes outside the
                source tree.

        Raises:
            StoreError: If neither file is usable, or the live file is malformed.
        """
        self._seed_path = Path(seed_path)
        if filename is None:
            filename = self._seed_path.name
        self._path = (
            data_dir if data_dir is not None else default_data_dir(self._seed_path)
        ) / filename
        self.data: dict[str, Any] = self._read_live_or_seed()

    # -- paths -----------------------------------------------------------

    @property
    def seed_path(self) -> Path:
        """The committed seed file this store was loaded from."""
        return self._seed_path

    @property
    def path(self) -> Path:
        """The live store file, written by :meth:`save`."""
        return self._path

    # -- reads -----------------------------------------------------------

    def collection(self, name: str) -> tuple[Record, ...]:
        """Return every record in a top-level list, as immutable views.

        Args:
            name: The seed document's top-level key, e.g. ``"customers"``.

        Returns:
            The records in document order. Empty if the collection is absent,
            which is a legitimate state (the issues store starts with no issues),
            not an error.
        """
        raw = self.data.get(name, [])
        if not isinstance(raw, list):
            raise StoreError.bad_collection(name, self._path)
        return tuple(Record.model_validate(item) for item in raw)

    def find(self, name: str, **match: object) -> Record | None:
        """Return the first record in ``name`` whose fields all equal ``match``.

        Args:
            name: The collection to search.
            **match: Field name to expected value. Every pair must match; a
                record with a missing field is skipped rather than raising.

        Returns:
            The first matching record, or ``None``. Callers turn ``None`` into a
            ``not_found`` result -- this method does not raise, because "not
            found" is an expected outcome of a lookup tool and, per
            ``docs/mcp-contracts.md`` S5, errors are results.
        """
        for record in self.collection(name):
            if all(record.model_dump().get(key) == value for key, value in match.items()):
                return record
        return None

    def __iter__(self) -> Iterator[str]:
        """Iterate over the top-level keys of the document."""
        return iter(self.data)

    # -- writes ----------------------------------------------------------

    def save(self) -> None:
        """Persist the in-memory document atomically.

        Writes to a temporary file in the same directory as :attr:`path`,
        ``fsync``\\ s it, then :func:`os.replace`\\ s it into place. See the module
        docstring for why this is the whole of the concurrency story.

        Raises:
            StoreError: If serialisation or the write fails. The previous file is
                left intact and a partially written temp file is removed.
        """
        directory = self._path.parent
        directory.mkdir(parents=True, exist_ok=True)
        try:
            payload = json.dumps(self.data, indent=2, sort_keys=False, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise StoreError.not_serialisable(self._path, exc) from exc

        handle: int | None = None
        tmp_path: Path | None = None
        try:
            handle, raw_tmp = tempfile.mkstemp(
                dir=directory, prefix=f".{self._path.name}.", suffix=".tmp"
            )
            tmp_path = Path(raw_tmp)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                # `fdopen` owns the descriptor from here; clear it so the
                # finally-block does not double-close on the success path.
                handle = None
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            # `Path.replace` is `os.replace`: same atomic rename. It is used
            # here because the PTH rules prefer pathlib, not for a behavioural
            # difference -- there is none.
            tmp_path.replace(self._path)
            tmp_path = None
        except OSError as exc:
            raise StoreError.unwritable(self._path, exc) from exc
        finally:
            if handle is not None:
                os.close(handle)
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

    def reset(self) -> None:
        """Replace the in-memory document with a fresh copy of the seed.

        Backs the servers' ``--reset`` flag and the pytest fixture, so every test
        starts from the committed data.

        Raises:
            StoreError: If the seed file is missing, unreadable, or malformed.
        """
        self.data = self._read_seed()

    # -- internals -------------------------------------------------------

    def _read_live_or_seed(self) -> dict[str, Any]:
        """The live file if it exists, otherwise the seed.

        Kept separate from :meth:`_read_seed` so ``reset`` has an unambiguous
        meaning: it restores the seed *regardless* of what the live file holds.
        """
        if self._path.exists():
            return self._read_document(self._path)
        return self._read_seed()

    def _read_seed(self) -> dict[str, Any]:
        return self._read_document(self._seed_path)

    def _read_document(self, path: Path) -> dict[str, Any]:
        """Read and validate one JSON object document.

        The error constructors name ``path`` in every message, so a malformed
        live file says so rather than reporting the seed as broken.
        """
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise StoreError.missing_seed(path) from exc
        except OSError as exc:
            raise StoreError.unreadable_seed(path, exc) from exc
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise StoreError.malformed_seed(path, exc) from exc
        if not isinstance(document, dict):
            raise StoreError.not_an_object(path)
        return document


def default_data_dir(seed_path: Path) -> Path:
    """Return the directory a store should persist into.

    ``OPSPILOT_MCP_DATA_DIR`` wins when set, so tests and Compose can isolate the
    servers' files. Otherwise the store writes **outside the source tree**, to a
    directory named after the seed's parent.

    The fallback deliberately does not use the seed's own directory. It did, and
    the consequence was that any call that forgot to pass ``data_dir`` wrote a
    running copy of the database into the repository: `mcp_servers/billing/
    billing.json` appeared holding a refund, and `issues.json` holding two
    created issues. The committed seed file is the demo's database
    (`docs/architecture.md` §8), and the source tree is not a place to keep a
    mutable one -- a stray file makes the demo unreproducible, shows up in
    `git status`, and lets one run leak state into the next.

    A deployment sets ``OPSPILOT_MCP_DATA_DIR`` to a real volume; a bare
    `python -m mcp_servers.billing.server` gets a writable path that is at least
    not inside the checkout.

    Args:
        seed_path: The seed file, used only to derive a stable directory name.

    Returns:
        An absolute directory path. It is not created here; :meth:`Store.save`
        creates it on first write.
    """
    configured = os.environ.get(_ENV_DATA_DIR)
    if configured:
        return Path(configured).expanduser().resolve()
    # `<cwd>/.opspilot-data/<seed-parent-name>/` -- outside the package, and
    # stable across runs so a server subprocess and the test talking to it agree
    # on where the file is.
    return (Path.cwd() / _DEFAULT_DATA_SUBDIR / seed_path.parent.name).resolve()

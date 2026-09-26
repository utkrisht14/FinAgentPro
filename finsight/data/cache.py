"""Persist JSON data in SQLite and reuse it until its time-to-live (TTL) expires.

Missing, expired, or malformed entries are treated as cache misses. Reads do not
delete those rows; remember fetches a replacement and overwrites the entry.
"""

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path


class JsonCache:
    """Store JSON-serializable values by key with an expiration timestamp.

    Args:
        path: Path to the SQLite database file. Its parent directories and cache
            table are created automatically if they do not already exist.

    Attributes:
        path: Database file used by each cache operation.
    """

    def __init__(self, path: Path):
        """Initialize the database without removing existing cached entries.

        Args:
            path: SQLite database file location as a pathlib.Path.

        Raises:
            OSError: If the parent directory cannot be created.
            sqlite3.Error: If opening or initializing the database fails.
        """
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, expires REAL, body TEXT)"
            )

    @contextmanager
    def connect(self):
        """Open a connection and manage a transaction for a cache operation.

        No arguments beyond this cache instance are required. SQLite waits up
        to 20 seconds for a database lock. A successful transaction is committed;
        an exception causes rollback. The connection is closed in either case.

        Yields:
            sqlite3.Connection: Connection to use inside a with block.

        Raises:
            sqlite3.Error: If connecting or executing a transaction fails.
        """
        connection = sqlite3.connect(self.path, timeout=20)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def get(self, key: str):
        """Read and decode an unexpired cached value.

        Args:
            key: Identifier under which the value was stored.

        Returns:
            The decoded JSON value, or None if the key is missing, expired, or
            contains invalid JSON. A cached JSON null also returns None and is
            therefore indistinguishable from a cache miss.

        Raises:
            sqlite3.Error: If the database cannot be read.
        """
        with self.connect() as db:
            row = db.execute("SELECT expires, body FROM cache WHERE key=?", (key,)).fetchone()
        if not row or row[0] < time.time():
            return None
        try:
            return json.loads(row[1])
        except (ValueError, TypeError):
            return None

    def put(self, key: str, value, ttl: int = 86400):
        """Insert or replace an entry and set its expiration time.

        Args:
            key: Identifier used to retrieve this entry.
            value: JSON-serializable data to store. NaN and infinity are rejected.
            ttl: Lifetime in seconds from the current time; defaults to 86400
                (24 hours). Zero or negative values provide no useful lifetime.

        Returns:
            None.

        Raises:
            TypeError: If value contains an object JSON cannot serialize.
            ValueError: If value contains non-finite numbers or circular references.
            sqlite3.Error: If the database write fails.
        """
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO cache VALUES (?,?,?)",
                (key, time.time() + ttl, json.dumps(value, allow_nan=False)),
            )

    def remember(self, key: str, fetch: Callable, ttl: int = 86400):
        """Return cached data, or fetch and store a replacement on a cache miss.

        Args:
            key: Identifier for the requested data.
            fetch: Callable invoked without arguments when get returns None.
                It must return JSON-serializable data.
            ttl: Lifetime in seconds for newly fetched data; defaults to 86400
                (24 hours). Reading an existing entry does not extend its TTL.

        Returns:
            The cached value or the result returned by fetch. A None result is
            stored but will be treated as a miss again on the next lookup.

        Raises:
            Exception: Errors from fetch, serialization, or database access
                propagate to the caller; no stale-data fallback is provided.
        """
        value = self.get(key)
        if value is None:
            value = fetch()
            self.put(key, value, ttl)
        return value


def cache_key(value: str) -> str:
    """Create a deterministic identifier from a request description.

    Args:
        value: String identifying the data, such as an endpoint and parameters.
            Callers must format equivalent requests consistently themselves.

    Returns:
        The 64-character hexadecimal SHA-256 digest of the UTF-8 encoded string.
        Hashing does not encrypt the request or protect secrets within it.
    """
    return hashlib.sha256(value.encode()).hexdigest()

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import yaml

from windcode.memory.models import (
    MemoryKind,
    MemoryRecord,
    MemoryScope,
    MemorySearchResult,
    MemoryStatus,
    utc_now,
)
from windcode.memory.security import validate_memory_text

SCHEMA_VERSION = 1
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def project_identifier(workspace: Path) -> str:
    normalized = str(workspace.expanduser().resolve())
    return hashlib.sha256(normalized.encode()).hexdigest()[:24]


class MemoryStore:
    """Markdown source of truth with a disposable SQLite FTS index."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.records_dir = self.root / "records"
        self.index_path = self.root / "index.sqlite3"
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.index_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        try:
            self._create_schema()
        except sqlite3.DatabaseError:
            corrupt = self.index_path.with_suffix(f".corrupt-{uuid4().hex}")
            self.index_path.replace(corrupt)
            for suffix in ("-wal", "-shm"):
                Path(f"{self.index_path}{suffix}").unlink(missing_ok=True)
            self._create_schema()
            self.rebuild()

    def _create_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL, scope TEXT NOT NULL, project_id TEXT,
                    status TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
                    confidence REAL NOT NULL, version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                    memory_id UNINDEXED, title, summary, body, tags, evidence,
                    tokenize='unicode61'
                );
                PRAGMA user_version=1;
                """
            )

    def _path(self, record: MemoryRecord) -> Path:
        return self.records_dir / record.scope.value / record.kind.value / f"{record.memory_id}.md"

    @staticmethod
    def _serialize(record: MemoryRecord) -> str:
        metadata = record.to_dict()
        body = str(metadata.pop("body"))
        frontmatter = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=True).strip()
        return f"---\n{frontmatter}\n---\n\n{body}\n"

    @staticmethod
    def _parse(content: str) -> MemoryRecord:
        match = _FRONTMATTER.match(content)
        if match is None:
            raise ValueError("memory file is missing YAML frontmatter")
        raw = yaml.safe_load(match.group(1))
        if not isinstance(raw, dict):
            raise ValueError("memory frontmatter must be an object")
        value = {str(key): item for key, item in cast(dict[object, object], raw).items()}
        value["body"] = content[match.end() :].strip()
        return MemoryRecord.from_dict(value)

    def _write(self, record: MemoryRecord) -> Path:
        path = self._path(record)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".tmp-{uuid4().hex}")
        data = self._serialize(record)
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def _index(self, connection: sqlite3.Connection, record: MemoryRecord, path: Path) -> None:
        connection.execute("DELETE FROM memories_fts WHERE memory_id = ?", (record.memory_id,))
        connection.execute(
            """INSERT OR REPLACE INTO memories
               (memory_id,path,kind,scope,project_id,status,title,summary,confidence,version,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                record.memory_id,
                str(path.relative_to(self.root)),
                record.kind.value,
                record.scope.value,
                record.project_id,
                record.status.value,
                record.title,
                record.summary,
                record.confidence,
                record.version,
                record.updated_at.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO memories_fts VALUES (?,?,?,?,?,?)",
            (
                record.memory_id,
                record.title,
                record.summary,
                record.body,
                " ".join(record.tags),
                " ".join(record.evidence),
            ),
        )

    def save(self, record: MemoryRecord) -> MemoryRecord:
        if not record.title or not record.summary or not record.body:
            raise ValueError("memory title, summary, and body are required")
        if record.scope is MemoryScope.PROJECT and not record.project_id:
            raise ValueError("project-scoped memory requires project_id")
        if record.kind is MemoryKind.EXPERIENCE and not record.evidence:
            raise ValueError("experience memory requires verification evidence")
        validate_memory_text(record.title, record.summary, record.body, *record.evidence)
        path = self._write(record)
        with self._connect() as connection:
            self._index(connection, record, path)
        return record

    def get(self, memory_id: str) -> MemoryRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT path FROM memories WHERE memory_id = ?", (memory_id,)
            ).fetchone()
        if row is None:
            raise KeyError(memory_id)
        path = (self.root / str(row["path"])).resolve()
        if not path.is_relative_to(self.records_dir):
            raise ValueError("memory index contains an unsafe path")
        return self._parse(path.read_text(encoding="utf-8"))

    def list(
        self,
        *,
        status: MemoryStatus | None = None,
        project_id: str | None = None,
    ) -> tuple[MemoryRecord, ...]:
        clauses: list[str] = []
        values: list[str] = []
        if status is not None:
            clauses.append("status = ?")
            values.append(status.value)
        if project_id is not None:
            clauses.append("(scope = 'user' OR project_id = ?)")
            values.append(project_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT memory_id FROM memories{where} ORDER BY updated_at DESC", values
            ).fetchall()
        return tuple(self.get(str(row["memory_id"])) for row in rows)

    def search(
        self,
        query: str,
        *,
        project_id: str | None = None,
        limit: int = 5,
        statuses: tuple[MemoryStatus, ...] = (MemoryStatus.ACTIVE,),
    ) -> tuple[MemorySearchResult, ...]:
        if not query.strip() or limit <= 0:
            return ()
        tokens = tuple(re.findall(r"[\w-]+", query.casefold(), flags=re.UNICODE))[:32]
        if not tokens:
            return ()
        fts_query = " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
        status_values = tuple(status.value for status in statuses)
        placeholders = ",".join("?" for _ in status_values)
        sql = f"""
            SELECT m.memory_id, bm25(memories_fts) AS rank, m.confidence
            FROM memories_fts JOIN memories m USING(memory_id)
            WHERE memories_fts MATCH ? AND m.status IN ({placeholders})
              AND (m.scope = 'user' OR m.project_id = ?)
            ORDER BY rank ASC, m.confidence DESC, m.updated_at DESC LIMIT ?
        """
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    sql, (fts_query, *status_values, project_id or "", limit)
                ).fetchall()
        except sqlite3.OperationalError:
            rows = ()
        indexed = tuple(
            MemorySearchResult(self.get(str(row["memory_id"])), float(-row["rank"])) for row in rows
        )
        if len(indexed) >= limit:
            return indexed

        # unicode61 does not segment unspaced CJK sentences reliably. A bounded
        # lexical supplement lets “我喜欢什么” recall “我喜欢 Python”.
        selected = {item.record.memory_id for item in indexed}
        query_terms = self._lexical_terms(query)
        supplemental: list[MemorySearchResult] = []
        for record in self.list(project_id=project_id):
            if record.memory_id in selected or record.status not in statuses:
                continue
            content_terms = self._lexical_terms(
                " ".join((record.title, record.summary, record.body, *record.tags))
            )
            overlap = query_terms & content_terms
            if not overlap:
                continue
            score = len(overlap) / max(len(query_terms), 1) + record.confidence * 0.1
            supplemental.append(MemorySearchResult(record, score))
        supplemental.sort(key=lambda item: (item.score, item.record.updated_at), reverse=True)
        return (*indexed, *supplemental[: limit - len(indexed)])

    @staticmethod
    def _lexical_terms(text: str) -> set[str]:
        normalized = text.casefold()
        terms = set(re.findall(r"[a-z0-9_-]{2,}", normalized))
        for run in re.findall(r"[\u3400-\u9fff]+", normalized):
            terms.update(run[index : index + 2] for index in range(max(0, len(run) - 1)))
        return terms

    def transition(self, memory_id: str, status: MemoryStatus) -> MemoryRecord:
        record = self.get(memory_id).transition(status)
        return self.save(record)

    def update(self, memory_id: str, **changes: Any) -> MemoryRecord:
        current = self.get(memory_id)
        allowed = {"title", "summary", "body", "tags", "evidence", "confidence"}
        if unknown := set(changes) - allowed:
            raise ValueError(f"unsupported memory fields: {', '.join(sorted(unknown))}")
        updated = replace(current, **changes, version=current.version + 1, updated_at=utc_now())
        return self.save(updated)

    def delete(self, memory_id: str) -> None:
        record = self.get(memory_id)
        path = self._path(record)
        with self._connect() as connection:
            connection.execute("DELETE FROM memories_fts WHERE memory_id = ?", (memory_id,))
            connection.execute("DELETE FROM memories WHERE memory_id = ?", (memory_id,))
        path.unlink(missing_ok=True)

    def rebuild(self) -> int:
        records: list[tuple[MemoryRecord, Path]] = []
        for path in sorted(self.records_dir.rglob("*.md")):
            if path.is_symlink() or not path.is_file():
                continue
            if not path.resolve().is_relative_to(self.records_dir):
                continue
            records.append((self._parse(path.read_text(encoding="utf-8")), path))
        with self._connect() as connection:
            connection.execute("DELETE FROM memories_fts")
            connection.execute("DELETE FROM memories")
            for record, path in records:
                self._index(connection, record, path)
        return len(records)

    def export_project(self, project_id: str, destination: Path) -> tuple[Path, ...]:
        destination = destination.expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        exported: list[Path] = []
        for record in self.list(status=MemoryStatus.ACTIVE, project_id=project_id):
            if record.scope is not MemoryScope.PROJECT:
                continue
            target = destination / f"{record.memory_id}.md"
            target.write_text(self._serialize(record), encoding="utf-8")
            exported.append(target)
        return tuple(exported)

    def record_outcome(self, memory_id: str, *, success: bool) -> MemoryRecord:
        current = self.get(memory_id)
        updated = replace(
            current,
            success_count=current.success_count + int(success),
            failure_count=current.failure_count + int(not success),
            last_verified_at=utc_now(),
            version=current.version + 1,
            updated_at=utc_now(),
        )
        return self.save(updated)

"""PostgreSQL-side knowledge tables (materials, concepts, prerequisites, aliases) against the REAL PostgreSQL from DATABASE_URL
(skipped, with a reason, when it is not reachable; the Docker evaluation runs them). Also pins the schema placement that a
clean `docker compose up` depends on: AI tables live in schema `ai`, never in Prisma's `public` schema."""
import asyncio
import os
import shutil
from pathlib import Path

import pytest

from app import knowledge
from .conftest import REAL_ENV

DB = REAL_ENV.get("DATABASE_URL", "")


def _reachable() -> bool:
    if not DB:
        return False
    try:
        import psycopg

        psycopg.connect(DB, connect_timeout=3).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="no reachable DATABASE_URL")


@pytest.fixture
def pg(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DB)
    loop = asyncio.new_event_loop()

    class Runner:
        def run(self, coro):
            return loop.run_until_complete(coro)

    yield Runner()
    loop.close()


async def _one(sql, *params):
    from app import db

    conn = await db.connect()
    try:
        cur = await conn.execute(sql, params)
        return await cur.fetchall()
    finally:
        await conn.close()


def test_ai_tables_live_in_their_own_schema_not_in_public(pg):
    """Prisma's `migrate deploy` fails with P3005 on a non-empty public schema it has not migrated; the AI service may start
    before the backend. Regression test for exactly that clean-start failure."""
    async def go():
        from app import db

        conn = await db.connect()
        try:
            await knowledge.ensure_schema(conn)
        finally:
            await conn.close()
        return await _one("SELECT table_schema, table_name FROM information_schema.tables WHERE table_name IN "
                          "('ai_learning_materials', 'ai_concepts', 'ai_concept_prereqs', 'ai_entity_aliases')")

    rows = pg.run(go())
    assert {r[1] for r in rows} == {"ai_learning_materials", "ai_concepts", "ai_concept_prereqs", "ai_entity_aliases"}
    assert {r[0] for r in rows} == {"ai"}


def test_ingestion_is_idempotent_and_versions_changes(pg, tmp_path):
    # a COPY of the real authored files plus one extra note, so nothing real is pruned from the shared database while the test runs
    mats = tmp_path / "materials"
    know = tmp_path / "knowledge"
    shutil.copytree(knowledge.MATERIALS_DIR, mats)
    shutil.copytree(knowledge.KNOWLEDGE_DIR, know)
    note = mats / "t-note.md"
    note.write_text("---\ntitle: Test note\ntags: alpha beta\nupdated: 2026-01-01\nconcepts: integer-overflow\nstatus: current\n---\nBody one.\n", encoding="utf-8")

    async def go():
        from app import db

        conn = await db.connect()
        try:
            await knowledge.ensure_schema(conn)
            await conn.execute("DELETE FROM ai_learning_materials WHERE id = 't-note'")
            first = await knowledge.ingest_files(conn, mats, know)
            second = await knowledge.ingest_files(conn, mats, know)  # unchanged input: nothing happens
            note.write_text(note.read_text(encoding="utf-8").replace("Body one.", "Body two."), encoding="utf-8")
            third = await knowledge.ingest_files(conn, mats, know)  # changed content: version + 1
            cur = await conn.execute("SELECT version, body FROM ai_learning_materials WHERE id = 't-note'")
            row = await cur.fetchone()
            note.unlink()
            await knowledge.ingest_files(conn, mats, know)  # a removed FILE note is pruned
            cur = await conn.execute("SELECT count(*) FROM ai_learning_materials WHERE id = 't-note'")
            gone = (await cur.fetchone())[0]
            return first, second, third, row, gone
        finally:
            await conn.close()

    first, second, third, row, gone = pg.run(go())
    assert first["materials_new"] == 1 and second == {"materials_new": 0, "materials_updated": 0, "concepts_new": 0, "concepts_updated": 0}
    assert third["materials_updated"] == 1 and row == (2, "Body two.") and gone == 0


def test_runtime_material_validation_and_lifecycle(pg):
    async def go():
        from app import db

        conn = await db.connect()
        try:
            await knowledge.ensure_schema(conn)
            await knowledge.ingest_files(conn)
            errors = []
            for bad in ({"id": "Bad Id!", "title": "t", "body": "b"}, {"id": "ok-id", "title": "", "body": "b"}, {"id": "ok-id", "title": "t", "body": "b", "status": "weird"},
                        {"id": "ok-id", "title": "t", "body": "b", "concepts": ["no-such-concept"]}, {"id": "ok-id", "title": "t", "body": "x" * 20001}):
                try:
                    await knowledge.upsert_material(conn, bad)
                    errors.append(None)
                except ValueError as e:
                    errors.append(str(e))
            first = await knowledge.upsert_material(conn, {"id": "t-runtime-note", "title": "Runtime", "body": "One.", "concepts": ["integer-overflow"]})
            second = await knowledge.upsert_material(conn, {"id": "t-runtime-note", "title": "Runtime", "body": "Two.", "concepts": ["integer-overflow"]})
            await knowledge.ingest_files(conn)  # ingestion never prunes runtime (source='db') notes
            docs = {d.id: d for d in await knowledge.load_materials_db(conn)}
            refused = await knowledge.delete_material(conn, "integer-overflow")  # authored notes cannot be deleted here
            deleted = await knowledge.delete_material(conn, "t-runtime-note")
            again = await knowledge.delete_material(conn, "t-runtime-note")
            return errors, first, second, docs, refused, deleted, again
        finally:
            await conn.close()

    errors, first, second, docs, refused, deleted, again = pg.run(go())
    assert all(errors) and any("unknown concept ids" in e for e in errors) and any("id must be" in e for e in errors)
    assert first == {"id": "t-runtime-note", "version": 1} and second["version"] == 2
    assert "t-runtime-note" in docs and docs["t-runtime-note"].concepts == ["integer-overflow"] and "integer-overflow" in docs  # both sources are loaded
    assert refused is False and deleted is True and again is False


def test_aliases_come_from_postgres_and_include_runtime_ones(pg):
    async def go():
        from app import db

        conn = await db.connect()
        try:
            await knowledge.ensure_schema(conn)
            await knowledge.ingest_files(conn)
            await knowledge.add_alias(conn, "problem", "t-problem-x", "Totally New Nickname")
            await knowledge.add_alias(conn, "problem", "t-problem-x", "totally   new nickname!")  # same normalised alias: no duplicate row
            al = await knowledge.load_aliases()
            await conn.execute("DELETE FROM ai_entity_aliases WHERE entity_id = 't-problem-x'")
            return al
        finally:
            await conn.close()

    al = pg.run(go())
    assert al["problem"]["t-problem-x"] == ["Totally New Nickname"]  # stored once
    assert "Add Two Numbers" in al["problem"]["44444444-4444-4444-8444-444444444441"]  # authored aliases are there too
    assert al["concept"]["integer-overflow"]

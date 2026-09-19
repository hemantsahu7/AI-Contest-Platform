"""AI-owned relational knowledge in PostgreSQL: versioned learning materials, the concept taxonomy with prerequisites, and
entity aliases. The authored files (ai/materials/*.md, ai/knowledge/*.json) are ingested idempotently; rows created another
way (source = 'db', e.g. through POST /ai/knowledge/materials) are never pruned by ingestion. Neo4j and pgvector are
derived from these tables and from the backend's tables."""
import hashlib
import json
import re
from pathlib import Path

from . import db
from .retrieval import MATERIALS_DIR, Material, load_materials, tokenize

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"

DDL = [
    "CREATE SCHEMA IF NOT EXISTS ai",
    """CREATE TABLE IF NOT EXISTS ai_learning_materials (
        id text PRIMARY KEY, title text NOT NULL, tags text[] NOT NULL DEFAULT '{}', body text NOT NULL,
        status text NOT NULL DEFAULT 'current', superseded_by text, updated text NOT NULL DEFAULT 'unknown',
        concepts text[] NOT NULL DEFAULT '{}', version integer NOT NULL DEFAULT 1, content_hash text NOT NULL,
        source text NOT NULL DEFAULT 'file', ingested_at timestamptz NOT NULL DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS ai_concepts (
        id text PRIMARY KEY, name text NOT NULL, description text NOT NULL DEFAULT '', keywords text[] NOT NULL DEFAULT '{}',
        version integer NOT NULL DEFAULT 1, content_hash text NOT NULL, source text NOT NULL DEFAULT 'file',
        updated_at timestamptz NOT NULL DEFAULT now())""",
    "CREATE TABLE IF NOT EXISTS ai_concept_prereqs (concept_id text NOT NULL, prereq_id text NOT NULL, PRIMARY KEY (concept_id, prereq_id))",
    """CREATE TABLE IF NOT EXISTS ai_entity_aliases (
        kind text NOT NULL, entity_id text NOT NULL, alias text NOT NULL, alias_norm text NOT NULL, source text NOT NULL DEFAULT 'file',
        PRIMARY KEY (kind, entity_id, alias_norm))""",
]


def norm(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", s.lower()))


def _hash(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def ensure_schema(conn) -> None:
    for stmt in DDL:
        await conn.execute(stmt)


async def add_alias(conn, kind: str, entity_id: str, alias: str, source: str = "db") -> None:
    n = norm(alias)
    if n:
        await conn.execute(
            "INSERT INTO ai_entity_aliases (kind, entity_id, alias, alias_norm, source) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (kind, entity_id, alias, n, source),
        )


async def ingest_files(conn=None, materials_dir: Path = MATERIALS_DIR, knowledge_dir: Path = KNOWLEDGE_DIR) -> dict:
    """Idempotent: unchanged entries are skipped, changed entries get version+1, removed FILE entries are pruned."""
    own = conn is None
    conn = conn or await db.connect()
    try:
        await ensure_schema(conn)
        counts = {"materials_new": 0, "materials_updated": 0, "concepts_new": 0, "concepts_updated": 0}
        docs = load_materials(materials_dir)
        for m in docs:
            h = _hash(m.title, m.tags, m.body, m.status, m.superseded_by, m.updated, m.concepts)
            cur = await conn.execute("SELECT content_hash, version FROM ai_learning_materials WHERE id = %s", (m.id,))
            row = await cur.fetchone()
            if row and row[0] == h:
                continue
            await conn.execute(
                """INSERT INTO ai_learning_materials (id, title, tags, body, status, superseded_by, updated, concepts, version, content_hash, source)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'file')
                   ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title, tags=EXCLUDED.tags, body=EXCLUDED.body, status=EXCLUDED.status,
                     superseded_by=EXCLUDED.superseded_by, updated=EXCLUDED.updated, concepts=EXCLUDED.concepts, version=EXCLUDED.version,
                     content_hash=EXCLUDED.content_hash, source='file', ingested_at=now()""",
                (m.id, m.title, m.tags, m.body, m.status, m.superseded_by, m.updated, m.concepts, (row[1] + 1) if row else 1, h),
            )
            counts["materials_updated" if row else "materials_new"] += 1
        await conn.execute("DELETE FROM ai_learning_materials WHERE source = 'file' AND NOT (id = ANY(%s))", ([m.id for m in docs],))

        cfile = knowledge_dir / "concepts.json"
        if cfile.exists():
            concepts = json.loads(cfile.read_text(encoding="utf-8"))["concepts"]
            for c in concepts:
                h = _hash(c["name"], c.get("description"), c.get("keywords"), c.get("requires"))
                cur = await conn.execute("SELECT content_hash, version FROM ai_concepts WHERE id = %s", (c["id"],))
                row = await cur.fetchone()
                if row and row[0] == h:
                    continue
                await conn.execute(
                    """INSERT INTO ai_concepts (id, name, description, keywords, version, content_hash, source) VALUES (%s,%s,%s,%s,%s,%s,'file')
                       ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, description=EXCLUDED.description, keywords=EXCLUDED.keywords,
                         version=EXCLUDED.version, content_hash=EXCLUDED.content_hash, source='file', updated_at=now()""",
                    (c["id"], c["name"], c.get("description", ""), c.get("keywords", []), (row[1] + 1) if row else 1, h),
                )
                counts["concepts_updated" if row else "concepts_new"] += 1
            ids = [c["id"] for c in concepts]
            await conn.execute("DELETE FROM ai_concepts WHERE source = 'file' AND NOT (id = ANY(%s))", (ids,))
            await conn.execute("DELETE FROM ai_concept_prereqs WHERE concept_id = ANY(%s)", (ids,))
            for c in concepts:
                for pre in c.get("requires", []):
                    await conn.execute("INSERT INTO ai_concept_prereqs (concept_id, prereq_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (c["id"], pre))
            # aliases from files are replaced wholesale; aliases created at runtime (source='db') are kept
            await conn.execute("DELETE FROM ai_entity_aliases WHERE source = 'file'")
            for c in concepts:
                for a in c.get("aliases", []):
                    await add_alias(conn, "concept", c["id"], a, "file")
        afile = knowledge_dir / "aliases.json"
        if afile.exists():
            data = json.loads(afile.read_text(encoding="utf-8"))
            for kind, table in (("problem", data.get("problems", {})), ("material", data.get("materials", {}))):
                for eid, aliases in table.items():
                    for a in aliases:
                        await add_alias(conn, kind, eid, a, "file")
        return counts
    finally:
        if own:
            await conn.close()


async def load_materials_db(conn=None) -> list[Material]:
    """Materials from PostgreSQL (the source of truth), as retrieval documents."""
    own = conn is None
    conn = conn or await db.connect()
    try:
        cur = await conn.execute("SELECT id, title, tags, body, status, superseded_by, updated, concepts FROM ai_learning_materials ORDER BY id")
        docs = []
        for id_, title, tags, body, status, sup, updated, concepts in await cur.fetchall():
            m = Material(id=id_, title=title, tags=list(tags), updated=updated, status=status, superseded_by=sup, body=body, concepts=list(concepts))
            m.tokens = tokenize(m.title + " " + " ".join(m.tags) * 2 + " " + m.body)
            docs.append(m)
        return docs
    finally:
        if own:
            await conn.close()


async def upsert_material(conn, data: dict) -> dict:
    """Insert/update one material created at runtime (source='db'). Validates the input; unknown concept ids are rejected."""
    mid = str(data.get("id", "")).strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,60}", mid):
        raise ValueError("id must be lowercase letters, digits and dashes (2-61 chars)")
    title, body = str(data.get("title", "")).strip(), str(data.get("body", "")).strip()
    if not title or not body:
        raise ValueError("title and body are required")
    if len(body) > 20000:
        raise ValueError("body too long (20000 chars max)")
    status = data.get("status", "current")
    if status not in ("current", "deprecated"):
        raise ValueError("status must be current or deprecated")
    concepts = [str(c) for c in data.get("concepts", [])]
    cur = await conn.execute("SELECT id FROM ai_concepts WHERE id = ANY(%s)", (concepts,))
    known = {r[0] for r in await cur.fetchall()}
    if set(concepts) - known:
        raise ValueError(f"unknown concept ids: {sorted(set(concepts) - known)}")
    tags = [str(t) for t in data.get("tags", [])]
    updated = str(data.get("updated", "unknown"))
    sup = data.get("superseded_by")
    h = _hash(title, tags, body, status, sup, updated, concepts)
    cur = await conn.execute("SELECT version FROM ai_learning_materials WHERE id = %s", (mid,))
    row = await cur.fetchone()
    version = (row[0] + 1) if row else 1
    await conn.execute(
        """INSERT INTO ai_learning_materials (id, title, tags, body, status, superseded_by, updated, concepts, version, content_hash, source)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'db')
           ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title, tags=EXCLUDED.tags, body=EXCLUDED.body, status=EXCLUDED.status,
             superseded_by=EXCLUDED.superseded_by, updated=EXCLUDED.updated, concepts=EXCLUDED.concepts, version=EXCLUDED.version,
             content_hash=EXCLUDED.content_hash, ingested_at=now()""",
        (mid, title, tags, body, status, sup, updated, concepts, version, h),
    )
    return {"id": mid, "version": version}


# ---------- read helpers for entity resolution ----------

def file_concepts(knowledge_dir: Path = KNOWLEDGE_DIR) -> list[dict]:
    """Authored concepts as entity dicts (id, title, aliases). Used when PostgreSQL/Neo4j are not reachable."""
    f = knowledge_dir / "concepts.json"
    if not f.exists():
        return []
    return [{"id": c["id"], "title": c["name"], "aliases": c.get("aliases", [])} for c in json.loads(f.read_text(encoding="utf-8"))["concepts"]]


def file_aliases(knowledge_dir: Path = KNOWLEDGE_DIR) -> dict[str, dict[str, list[str]]]:
    """{kind: {entity_id: [aliases]}} from the authored aliases.json (problems, materials) and concepts.json (concepts)."""
    out: dict[str, dict[str, list[str]]] = {"problem": {}, "material": {}, "concept": {}}
    f = knowledge_dir / "aliases.json"
    if f.exists():
        data = json.loads(f.read_text(encoding="utf-8"))
        out["problem"] = {k: list(v) for k, v in data.get("problems", {}).items()}
        out["material"] = {k: list(v) for k, v in data.get("materials", {}).items()}
    out["concept"] = {c["id"]: c["aliases"] for c in file_concepts(knowledge_dir) if c["aliases"]}
    return out


async def load_aliases() -> dict[str, dict[str, list[str]]]:
    """Aliases from PostgreSQL (includes ones added at runtime); the authored files when the database is not reachable."""
    if db.configured():
        try:
            conn = await db.connect()
            try:
                cur = await conn.execute("SELECT kind, entity_id, alias FROM ai_entity_aliases")
                out: dict[str, dict[str, list[str]]] = {"problem": {}, "material": {}, "concept": {}}
                for kind, eid, alias in await cur.fetchall():
                    out.setdefault(kind, {}).setdefault(eid, []).append(alias)
                if any(out.values()):
                    return out
            finally:
                await conn.close()
        except Exception:
            pass
    return file_aliases()


async def delete_material(conn, material_id: str) -> bool:
    """Remove a material that was added at runtime (source = 'db'). Authored files are managed in ai/materials, never here."""
    cur = await conn.execute("DELETE FROM ai_learning_materials WHERE id = %s AND source = 'db' RETURNING id", (material_id,))
    return await cur.fetchone() is not None

"""PostgreSQL access for the AI service (async psycopg). PostgreSQL is the relational source of truth.

The AI service's own tables (materials, concepts, prerequisites, aliases) live in the separate schema `ai`, never in `public`:
Prisma's `migrate deploy` refuses a non-empty `public` schema that it has not migrated (P3005), and the AI service may start
before the backend has run its migrations. Connections use `search_path = ai, public`, so the backend's tables ("Submission", ...)
and the AI tables are both reachable by their plain names."""
import os


def configured() -> bool:
    return bool(os.getenv("DATABASE_URL"))


async def connect():
    import psycopg

    return await psycopg.AsyncConnection.connect(os.getenv("DATABASE_URL", ""), autocommit=True, options="-c search_path=ai,public")

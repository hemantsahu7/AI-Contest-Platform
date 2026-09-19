"""Small retrieval layer: BM25 over learning materials + freshness rerank + problem entity resolution."""
import math
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

MATERIALS_DIR = Path(__file__).resolve().parent.parent / "materials"
STOP = {"the", "a", "an", "of", "to", "and", "is", "in", "for", "my", "me", "i", "it", "this", "that", "why", "what", "how", "problem", "two", "did", "does", "do", "can", "you", "on", "with", "was", "are"}


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t]


@dataclass
class Material:
    id: str
    title: str
    tags: list[str]
    updated: str
    status: str
    superseded_by: str | None
    body: str
    tokens: list[str] = field(default_factory=list)


def load_materials(directory: Path = MATERIALS_DIR) -> list[Material]:
    docs: list[Material] = []
    for path in sorted(directory.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        meta: dict[str, str] = {}
        body = raw
        if raw.startswith("---"):
            _, header, body = raw.split("---", 2)
            for line in header.strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
        m = Material(
            id=path.stem,
            title=meta.get("title", path.stem),
            tags=meta.get("tags", "").split(),
            updated=meta.get("updated", "unknown"),
            status=meta.get("status", "current"),
            superseded_by=meta.get("superseded_by"),
            body=body.strip(),
        )
        m.tokens = tokenize(m.title + " " + " ".join(m.tags) * 2 + " " + m.body)
        docs.append(m)
    return docs


class MaterialIndex:
    def __init__(self, docs: list[Material]):
        self.docs = docs
        self.avg_len = sum(len(d.tokens) for d in docs) / max(1, len(docs))
        self.df: dict[str, int] = {}
        for d in docs:
            for t in set(d.tokens):
                self.df[t] = self.df.get(t, 0) + 1

    def bm25(self, query_tokens: list[str], doc: Material, k1: float = 1.5, b: float = 0.75) -> float:
        n = len(self.docs)
        score = 0.0
        for t in set(query_tokens):
            f = doc.tokens.count(t)
            if not f:
                continue
            idf = math.log(1 + (n - self.df.get(t, 0) + 0.5) / (self.df.get(t, 0) + 0.5))
            score += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * len(doc.tokens) / self.avg_len))
        return score

    def search(self, query: str, top_k: int = 3, boost_tags: list[str] | None = None) -> list[tuple[Material, float]]:
        """BM25 candidates, then rerank: tag/title overlap boost, deprecated material demoted."""
        q = [t for t in tokenize(query) if t not in STOP]
        boost = set(tokenize(" ".join(boost_tags or [])))
        scored = []
        for d in self.docs:
            base = self.bm25(q, d)
            if base <= 0:
                continue
            base += 0.5 * len(boost & set(d.tokens)) if boost else 0
            base += 1.0 * len(set(q) & set(tokenize(d.title)))
            if d.status == "deprecated":
                base *= 0.4
            scored.append((d, round(base, 3)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


def _tok_match(q: str, t: str) -> bool:
    if q == t:
        return True
    if len(q) >= 3 and (t.startswith(q) or q.startswith(t)):
        return True
    return len(q) >= 4 and SequenceMatcher(None, q, t).ratio() >= 0.85


def resolve_problem(question: str, problems: list[dict]) -> tuple[dict | None, list[dict]]:
    """Entity resolution: map free text ('the sum problem', 'max one') to a contest problem.
    Returns (best, candidates). best is None when nothing matches or the match is ambiguous."""
    q_tokens = [t for t in tokenize(question) if t not in STOP]
    scored = []
    for p in problems:
        title_tokens = [t for t in tokenize(p["title"]) if t not in STOP]
        if not title_tokens:
            continue
        hits = sum(1 for tt in title_tokens if any(_tok_match(qt, tt) for qt in q_tokens))
        if hits:
            scored.append((hits / len(title_tokens), p))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored:
        return None, []
    top = scored[0][0]
    tied = [p for s, p in scored if abs(s - top) < 1e-9]
    if len(tied) > 1:
        return None, tied
    return scored[0][1], [scored[0][1]]

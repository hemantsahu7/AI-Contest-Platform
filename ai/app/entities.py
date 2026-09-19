"""Entity resolution: map free text to the stable PostgreSQL ids of problems, concepts and learning materials.

Signals, strongest first: the id itself, the normalised title as a phrase, an alias (old/alternative name) as a phrase, word
overlap (whole words, abbreviations such as "max" -> "maximum", plurals), fuzzy match on short word windows (typos, e.g.
"Maximun"). Whole-word rules mean "summarize" does NOT match "Sum". Scores are comparable within one call; two different
entities within AMBIGUITY_GAP of each other make the result ambiguous (the caller should ask for clarification)."""
import re
import uuid
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .retrieval import STOP, _tok_match, tokenize

AMBIGUITY_GAP = 0.05
MIN_SCORE = 0.5


@dataclass
class Candidate:
    kind: str
    id: str
    name: str
    score: float
    method: str
    contest_id: str | None = None
    contest: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class Resolution:
    query: str
    candidates: list[Candidate]
    ambiguous: bool = False

    @property
    def best(self) -> Candidate | None:
        return None if self.ambiguous or not self.candidates else self.candidates[0]


def normalize(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))


def _windows(tokens: list[str], sizes=(1, 2, 3)) -> list[str]:
    return [" ".join(tokens[i:i + n]) for n in sizes for i in range(0, len(tokens) - n + 1)]


def score_entity(query: str, e: dict) -> tuple[float, str]:
    """Score one entity dict {id, title, aliases?} against a question / name."""
    qn = normalize(query)
    if not qn:
        return 0.0, "none"
    if qn == normalize(e["id"]) or re.search(rf"\b{re.escape(e['id'].lower())}\b", query.lower()):
        return 1.0, "id"
    padded = f" {qn} "
    tn = normalize(e["title"])
    if tn and f" {tn} " in padded:
        return (1.0 if tn == qn else 0.98), "title"
    for a in e.get("aliases") or []:
        an = normalize(a)
        if an and f" {an} " in padded:
            return (0.97 if an == qn else 0.95), "alias"
    q_tokens = [t for t in tokenize(query) if t not in STOP]
    best, method = 0.0, "none"
    for name, base, m in [(e["title"], 0.9, "words")] + [(a, 0.85, "alias-words") for a in (e.get("aliases") or [])]:
        toks = [t for t in tokenize(name) if t not in STOP]
        if not toks:
            continue
        hits = sum(1 for tt in toks if any(_tok_match(qt, tt) for qt in q_tokens))
        if hits:
            s = base * hits / len(toks)
            if s > best:
                best, method = s, m
    if best < MIN_SCORE:
        for w in _windows(q_tokens):
            for name in [e["title"]] + list(e.get("aliases") or []):
                nn = normalize(name)
                if len(nn) >= 5 and len(w) >= 5:
                    r = SequenceMatcher(None, w, nn).ratio()
                    if r >= 0.85 and r * 0.9 > best:
                        best, method = r * 0.9, "fuzzy"
    return round(best, 3), method


def resolve(query: str, entities: list[dict], kind: str, prefer_contest: str | None = None, limit: int = 5) -> Resolution:
    """entities: dicts with id, title, aliases?, contest_id?, contest?. Ambiguity is judged among candidates of the preferred
    contest when one is given (a same-named problem in an archived contest is reported as a candidate, not as a rival)."""
    cands = []
    for e in entities:
        s, m = score_entity(query, e)
        if s >= MIN_SCORE:
            cands.append(Candidate(kind, e["id"], e["title"], s, m, e.get("contest_id"), e.get("contest"), {k: v for k, v in e.items() if k not in ("id", "title", "aliases", "contest_id", "contest")}))
    cands.sort(key=lambda c: (-c.score, c.name))
    cands = cands[:limit]
    scoped = [c for c in cands if prefer_contest is None or c.contest_id in (None, prefer_contest)]
    pool = scoped or cands
    ambiguous = len(pool) > 1 and pool[0].score - pool[1].score < AMBIGUITY_GAP and pool[0].id != pool[1].id
    if scoped and not ambiguous:
        cands = scoped + [c for c in cands if c not in scoped]  # preferred contest first
    return Resolution(query, cands, ambiguous)


def looks_like_id(s: str) -> bool:
    try:
        uuid.UUID(s.strip())
        return True
    except ValueError:
        return False

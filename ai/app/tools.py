"""Read-only tools for the bounded agent (see agent.py). Five tools, no others:

  resolve_entity            free text -> problem / concept / learning material ids (aliases, typos, similar names)
  search_learning_material  BM25 + pgvector retrieval, optionally expanded with graph concepts
  get_submission_history    the caller's own attempts (staff: per-learner attempts) for one problem
  get_judge_history         judge version, incidents (staff only) and identical-source/different-verdict conflicts
  traverse_graph            Neo4j multi-hop traversal: problem -> concept -> prerequisite -> material, learner failures ->
                            prerequisite gaps, contest-wide shared gaps (staff only)

Every tool works on data the asking user is already authorized to see: the backend calls that fed `ToolContext` used the
user's own JWT, and graph queries are the fixed parameterised statements of graph_store.READ_QUERIES scoped by org / contest /
user. Tools never write, never accept query text, never return source code, hidden tests or other learners' private data."""
import asyncio
import logging
import re
from dataclasses import dataclass, field

from . import entities, graph_store, graph_sync, knowledge, vectors

log = logging.getLogger("ai.agent")

MAX_ITEM_CHARS = 900
MAX_ROWS = 6
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class ToolError(Exception):
    """Bad arguments (unknown key, wrong type, too long): the call is rejected before anything runs."""


class ToolDenied(Exception):
    """The caller may not use this tool / object (role or ownership)."""


@dataclass
class ToolResult:
    status: str  # ok | empty
    summary: str
    evidence: list = field(default_factory=list)
    facts: list = field(default_factory=list)
    gaps: list = field(default_factory=list)


@dataclass
class ToolContext:
    ctx: object  # assistant.Ctx (evidence collected so far)
    me: dict
    contest: dict
    staff: bool
    subs: list  # submissions the backend returned to this user for this contest
    fresh: bool = False
    aliases: dict | None = None
    graph_gap_noted: bool = False
    resolved: dict = field(default_factory=dict)  # {"problem": dict|None, "candidates": [...], "other": Candidate|None, "concepts": [ids]}
    graph_materials: list = field(default_factory=list)  # ranked: {id, hops, concept, via, superseded_by}
    searched: list = field(default_factory=list)  # rankings produced by search_learning_material
    graph_concepts: list = field(default_factory=list)  # {id, name, role: direct|prerequisite}

    @property
    def org_id(self) -> str:
        return self.contest["organizationId"]

    @property
    def contest_id(self) -> str:
        return self.contest["id"]

    @property
    def user_id(self) -> str:
        return self.me["id"]

    def next_id(self, letter: str) -> str:
        used = [int(e.id[1:]) for e in self.ctx.evidence if e.id[:1] == letter and e.id[1:].isdigit()]
        return f"{letter}{max(used, default=0) + 1}"

    def ev(self, letter: str, kind: str, title: str, text: str, ref: str, updated: str | None = None):
        from .assistant import Ev

        return Ev(self.next_id(letter), kind, title, text[:MAX_ITEM_CHARS], ref, updated)

    def add(self, ev) -> None:
        self.ctx.evidence.append(ev)

    def authorized_problem_ids(self) -> set[str]:
        return {p["id"] for p in self.ctx.problems}


def _clip(s, n=200) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def _ts(v, n: int = 19) -> str:
    """ISO timestamp, cut to seconds (no ellipsis)."""
    return "" if v is None else str(v)[:n]


def _sid(v: str) -> str:
    """Short submission id that stays distinguishable when several ids share a prefix."""
    return f"{v[:8]}..{v[-4:]}" if len(v) > 14 else v


async def _graph(tc: ToolContext, name: str, **params) -> list[dict]:
    """One allowlisted graph read, after bringing the projection up to date once per request."""
    if not tc.fresh:
        tc.fresh = True
        try:
            await asyncio.wait_for(graph_sync.freshen(), 4)
        except Exception:
            pass
    return await graph_store.read(name, **params)


def _hops(row_materials: list[dict], concept_id: str, prereqs: list[dict]) -> list[dict]:
    """Rank a concept's materials: 0 = covers the concept itself, n = covers a prerequisite n hops away."""
    hop = {concept_id: 0, **{p["id"]: p["hops"] for p in prereqs if p.get("id")}}
    best: dict[str, dict] = {}
    for m in row_materials:
        if not m.get("id"):
            continue
        entry = {"id": m["id"], "hops": hop.get(m.get("covers"), 0), "concept": m.get("covers"), "status": m.get("status"),
                 "updated": m.get("updated"), "superseded_by": m.get("superseded_by")}
        if m["id"] not in best or entry["hops"] < best[m["id"]]["hops"]:  # a note covering several related concepts is listed once
            best[m["id"]] = entry
    return sorted(best.values(), key=lambda m: (m["hops"], m["id"]))


def _note_materials(tc: ToolContext, mats: list[dict], via_concept: str, via_name: str, role: str) -> None:
    seen = {m["id"]: m for m in tc.graph_materials}
    for m in mats:
        cur = seen.get(m["id"])
        entry = {**m, "via": via_name, "role": role}
        if cur is None or m["hops"] < cur["hops"]:
            seen[m["id"]] = entry
    tc.graph_materials = sorted(seen.values(), key=lambda m: (m["hops"], m.get("status") == "deprecated", -len(m.get("updated") or "")))


# ---------------- resolve_entity ----------------

async def resolve_entity(tc: ToolContext, args: dict) -> ToolResult:
    name, kind = args["name"], args.get("kind", "any")
    if tc.aliases is None:
        tc.aliases = await knowledge.load_aliases()
    lines, facts, gaps = [], [], []
    tc.resolved = {"problem": None, "candidates": [], "other": None, "concepts": []}
    found = 0
    ev = tc.ev("E", "resolution", "Entity resolution", "", "resolve_entity")

    if kind in ("problem", "any"):
        ents = [{"id": p["id"], "title": p["title"], "aliases": tc.aliases["problem"].get(p["id"], []), "contest_id": tc.contest_id, "contest": tc.contest["title"]} for p in tc.ctx.problems]
        try:
            known = {e["id"] for e in ents}
            for r in await _graph(tc, "entity_problems", org_id=tc.org_id, staff=tc.staff):  # same-organization problems of other contests (titles only)
                if r["id"] not in known:
                    ents.append({"id": r["id"], "title": r["title"], "aliases": r.get("aliases") or [], "contest_id": r["contest_id"], "contest": r["contest"]})
        except graph_store.GraphUnavailable:
            if graph_store.configured():
                gaps.append("Problems of other contests could not be checked (graph store unavailable).")
        res = entities.resolve(name, ents, "problem", prefer_contest=tc.contest_id)
        by_id = {p["id"]: p for p in tc.ctx.problems}
        for c in res.candidates[:4]:
            where = "this contest" if c.contest_id == tc.contest_id else f"contest '{c.contest}'"
            lines.append(f"problem '{c.name}' in {where} (id {c.id}, matched by {c.method}, score {c.score})")
        if res.ambiguous:
            tc.resolved["candidates"] = [by_id[c.id] for c in res.candidates if c.id in by_id][:4]
            gaps.append("The question matches more than one problem: " + ", ".join(f"'{c.name}'" for c in res.candidates[:4]) + ".")
            found += 1
        elif res.best:
            found += 1
            if res.best.id in by_id:
                tc.resolved["problem"] = by_id[res.best.id]
                facts.append({"text": f"'{res.query[:80]}' was resolved to the problem '{res.best.name}' (matched by {res.best.method}).", "kind": "observation", "ids": [ev.id], "mats": []})
            else:
                tc.resolved["other"] = res.best
                gaps.append(f"'{res.best.name}' is a problem of the contest '{res.best.contest}', not of this contest, so I cannot analyse its submissions here.")

    if kind in ("concept", "any"):
        concepts = knowledge.file_concepts()
        if graph_store.configured():
            try:
                rows = await _graph(tc, "entity_concepts")
                if rows:
                    concepts = [{"id": r["id"], "title": r["title"], "aliases": r.get("aliases") or []} for r in rows]
            except graph_store.GraphUnavailable:
                pass
        for c in concepts:
            c["aliases"] = list({*c.get("aliases", []), *tc.aliases["concept"].get(c["id"], [])})
        res = entities.resolve(name, concepts, "concept", limit=3)
        if res.candidates and not res.ambiguous or (res.ambiguous and res.candidates[0].score >= 0.9):
            for c in res.candidates[:3]:
                if c.score >= 0.85:
                    tc.resolved["concepts"].append(c.id)
                    lines.append(f"concept '{c.name}' (id {c.id}, matched by {c.method}, score {c.score})")
                    facts.append({"text": f"The question refers to the concept '{c.name}' (matched by {c.method}).", "kind": "observation", "ids": [ev.id], "mats": []})
                    found += 1

    if kind in ("material", "any"):
        from . import assistant

        ents = [{"id": d.id, "title": d.title, "aliases": tc.aliases["material"].get(d.id, [])} for d in assistant.INDEX.docs]
        res = entities.resolve(name, ents, "material", limit=3)
        titles = {d.id: d.title for d in assistant.INDEX.docs}
        docs = {d.id: d for d in assistant.INDEX.docs}
        ranked = []
        for c in res.candidates[:2]:
            if c.score >= 0.85:
                lines.append(f"learning material '{c.name}' (id {c.id}, matched by {c.method}, score {c.score})")
                found += 1
                d = docs[c.id]
                ranked.append(c.id)
                if d.status == "deprecated":
                    newer = titles.get(d.superseded_by or "")
                    facts.append({"text": f"The learning material '{d.title}' (updated {d.updated}) is marked deprecated" + (f" and superseded by '{newer}'; prefer the newer note." if newer else "; treat it as possibly outdated."), "kind": "observation", "ids": [ev.id], "mats": [d.id] + ([d.superseded_by] if newer else [])})
                else:
                    facts.append({"text": f"The learning material '{d.title}' is current (updated {d.updated}).", "kind": "observation", "ids": [ev.id], "mats": [d.id]})
        if ranked:
            tc.searched.append(ranked)

    if not found:
        return ToolResult("empty", f"nothing matched '{_clip(name, 60)}'", gaps=gaps)
    ev.text = ("Entities matched to the question: " + "; ".join(lines) + ".")[:MAX_ITEM_CHARS]
    tc.add(ev)
    return ToolResult("ok", f"{found} match(es): " + "; ".join(lines[:2]), [ev], facts, gaps)


# ---------------- search_learning_material ----------------

async def search_learning_material(tc: ToolContext, args: dict) -> ToolResult:
    from . import assistant

    query = args["query"]
    hits, retrieval = await assistant.hybrid_search(query, top_k=6)
    ids = [m.id for m, _ in hits]
    gaps = []
    for cid in args.get("concept_ids", [])[:3]:  # graph expansion: materials that cover the concept or its prerequisites
        if graph_store.configured():
            try:
                for r in await _graph(tc, "concept_graph", concept_id=cid, org_id=tc.org_id, staff=tc.staff):
                    _note_materials(tc, _hops(r["materials"], cid, r["prereqs"]), cid, r["name"], "direct")
            except graph_store.GraphUnavailable:
                gaps.append("Graph expansion of the search was unavailable.")
                break
    if not ids:
        return ToolResult("empty", f"no learning material matched '{_clip(query, 60)}'", gaps=gaps)
    tc.searched.append(ids)
    tc.ctx.retrieval = retrieval
    return ToolResult("ok", f"{len(ids)} materials: " + ", ".join(m.title for m, _ in hits[:3]), gaps=gaps)


# ---------------- get_submission_history ----------------

async def get_submission_history(tc: ToolContext, args: dict) -> ToolResult:
    pid = args.get("problem_id") or (tc.ctx.problem["id"] if tc.ctx.problem else None)
    if not pid:
        return ToolResult("empty", "no problem to look at", gaps=["No problem was identified, so there is no per-problem history to show."])
    if pid not in tc.authorized_problem_ids():
        raise ToolDenied("that problem is not part of this contest")
    title = next(p["title"] for p in tc.ctx.problems if p["id"] == pid)
    rows = [s for s in tc.subs if s["problemId"] == pid and (tc.staff or s["userId"] == tc.user_id)]  # a learner only ever sees their own attempts
    rows.sort(key=lambda s: s["submittedAt"])
    if not rows:
        return ToolResult("empty", "no submissions", gaps=[f"There are no {'recorded' if tc.staff else 'submissions of yours'} for '{title}'."])
    if tc.staff:
        by: dict[str, list[str]] = {}
        for s in rows:
            by.setdefault(s.get("username") or s["userId"][:8], []).append(f"{s['verdict']} ({s['submittedAt'][:16]})")
        text = f"Attempts on '{title}' per learner, oldest first: " + "; ".join(f"{u}: {' -> '.join(v[-5:])}" for u, v in sorted(by.items())[:8])
    else:
        text = f"Your attempts on '{title}', oldest first: " + " -> ".join(f"{s['verdict']} ({s['submittedAt'][:16]})" for s in rows[-8:])
    ev = tc.ev("U", "submission-history", f"Attempt history: {title}", text, f"GET /contests/{tc.contest_id}/submissions")
    tc.add(ev)
    return ToolResult("ok", f"{len(rows)} attempt(s) on '{title}'", [ev], [{"text": text, "kind": "observation", "ids": [ev.id], "mats": []}])


# ---------------- get_judge_history ----------------

async def get_judge_history(tc: ToolContext, args: dict) -> ToolResult:
    sub_id = args.get("submission_id") or (tc.ctx.submission["id"] if tc.ctx.submission else None)
    pid = args.get("problem_id") or (tc.ctx.problem["id"] if tc.ctx.problem else None)
    mine = {s["id"] for s in tc.subs if s["userId"] == tc.user_id}
    if sub_id and not tc.staff and sub_id not in mine:
        raise ToolDenied("that submission is not yours")
    if pid and pid not in tc.authorized_problem_ids():
        raise ToolDenied("that problem is not part of this contest")
    if not sub_id and not pid and not tc.staff:
        return ToolResult("empty", "no submission or problem to look at", gaps=["No submission was identified, so judge history cannot be checked."])
    base = dict(org_id=tc.org_id, contest_id=tc.contest_id, user_id=tc.user_id, staff=tc.staff)
    lines, notable, facts, evs = [], [], [], []
    ref = f"graph: judge history (contest {tc.contest_id[:8]})"

    if sub_id:
        rows = await _graph(tc, "judge_submission", submission_id=sub_id, **base)
        if rows:
            r = rows[0]
            if r.get("judge_version"):
                line = f"Submission {_sid(r['id'])} ({r['verdict']}) was judged by {r['judge_version']} (created {_ts(r['judge_version_created'], 25)}" + (f": {_clip(r['judge_description'], 140)}" if tc.staff and r.get("judge_description") else "") + f"); it was submitted {_ts(r['submitted_at'], 25)}."
                lines.append(line)
            if r.get("problem_updated_at") and r.get("submitted_at") and r["problem_updated_at"] > r["submitted_at"]:
                note = f"The statement of '{r['problem']}' was revised at {r['problem_updated_at'][:19]}, after this submission ({r['submitted_at'][:19]}): the current statement may differ from what was judged."
                notable.append(note)
            if tc.staff:
                inc = [i for i in r["incidents"] if i.get("type")]
                for i in inc[:3]:
                    notable.append(f"Judge incident {i['type']} recorded {_ts(i['created'], 25)}" + (f", resolved {_ts(i['resolved'], 25)}" if i.get("resolved") else ", unresolved") + (f": {_clip(i['message'], 200)}" if i.get("message") else "") + ".")
    conflicts = await _graph(tc, "judge_conflicts", problem_id=pid, **base)
    for c in conflicts[:3]:
        notable.append(
            f"CONFLICT for {c['learner'] if tc.staff else 'you'} on '{c['problem']}': submission {_sid(c['a_id'])} was {c['a_verdict']} at {_ts(c['a_at'], 19)}" + (f" under {c['a_judge']}" if c.get("a_judge") else "")
            + f", submission {_sid(c['b_id'])} was {c['b_verdict']} at {_ts(c['b_at'], 19)}" + (f" under {c['b_judge']}" if c.get("b_judge") else "")
            + " - the source text is identical (same hash), so the difference did not come from the code."
        )
    if tc.staff:
        versions = await _graph(tc, "judge_versions", org_id=tc.org_id, contest_id=tc.contest_id, staff=tc.staff)
        if versions:
            by: dict[str, list[str]] = {}
            for v in versions:
                by.setdefault(f"{v['version']} (created {_ts(v['created'], 10)})", []).append(f"{v['verdict']} x{v['n']}")
            notable.append("Outcomes by judge version: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in by.items()) + ".")
        incs = await _graph(tc, "judge_incidents", org_id=tc.org_id, contest_id=tc.contest_id, staff=tc.staff)
        if incs:
            notable.append(f"{len(incs)} judge incident(s) recorded in this contest; latest: {incs[-1]['type']} at {_ts(incs[-1]['created'], 19)} during {incs[-1].get('judge_version') or 'unknown version'}" + (f": {_clip(incs[-1]['message'], 160)}" if incs[-1].get("message") else "") + ".")
    if not lines and not notable:
        return ToolResult("empty", "no judge version or conflict on record")
    ev = tc.ev("J", "judge-history", "Judge version and history", " ".join(lines + notable), ref)
    tc.add(ev)
    evs.append(ev)
    lines = lines + notable
    for line in notable:  # the plain "judged by judge-vN" line is evidence only; conflicts, revisions and incidents become claims
        facts.append({"text": line, "kind": "observation", "ids": [ev.id], "mats": []})
    if conflicts:
        facts.append({"text": "The judge is authoritative and its recorded verdicts stand. Two different verdicts for identical source under different judge versions suggests a judge-side difference, but that cannot be confirmed from verdicts alone; ask an instructor to review or rejudge it.", "kind": "hypothesis", "ids": [ev.id], "mats": []})
    return ToolResult("ok", f"{len(lines)} finding(s): " + _clip(lines[0], 120), evs, facts)


# ---------------- traverse_graph ----------------

def _title(mid: str | None) -> str:
    from . import assistant

    d = next((d for d in assistant.INDEX.docs if d.id == mid), None)
    return d.title if d else str(mid)


def _mat_text(m: dict) -> str:
    flag = " [DEPRECATED]" if m.get("status") == "deprecated" else ""
    return f"'{_title(m['id'])}'{flag}"


async def traverse_graph(tc: ToolContext, args: dict) -> ToolResult:
    kind, sid = args["start_kind"], args.get("start_id")
    base = dict(org_id=tc.org_id)
    if kind == "problem":
        pid = sid or (tc.ctx.problem["id"] if tc.ctx.problem else None)
        if not pid or pid not in tc.authorized_problem_ids():
            raise ToolDenied("that problem is not part of this contest")
        title = next(p["title"] for p in tc.ctx.problems if p["id"] == pid)
        rows = await _graph(tc, "problem_graph", problem_id=pid, **base)
        similar = await _graph(tc, "similar_problems", problem_id=pid, staff=tc.staff, **base)
        lines, facts, mats_seen = [], [], []
        for r in rows[:MAX_ROWS]:
            rels = [x for x in r["rels"] if x.get("rel")]
            why = next((x["reason"] for x in rels if x.get("reason")), None)
            role = "REQUIRES" if any(x["rel"] == "REQUIRES" for x in rels) else "TAGGED_WITH"
            pre = sorted([p for p in r["prereqs"] if p.get("id")], key=lambda p: p["hops"])
            line = f"'{title}' -{role}-> {r['concept']}" + (f" ({why})" if why else "")
            for p in pre[:2]:
                line += f" <-PREREQUISITE_OF({p['hops']} hop{'s' if p['hops'] > 1 else ''})- {p['name']}"
            mats = _hops(r["materials"], r["concept_id"], r["prereqs"])
            if mats:
                line += " -> materials: " + ", ".join(_mat_text(m) for m in mats[:3])
            lines.append(line)
            tc.graph_concepts.append({"id": r["concept_id"], "name": r["concept"], "role": "direct"})
            tc.graph_concepts.extend({"id": p["id"], "name": p["name"], "role": "prerequisite"} for p in pre)
            _note_materials(tc, mats, r["concept_id"], r["concept"], role)
            mats_seen += [m["id"] for m in mats[:2]]
        for s in similar[:3]:
            lines.append(f"'{title}' -SIMILAR_TO-> '{s['title']}' in contest '{s['contest']}' ({s['kind']}, statement similarity {s['statement_score']})")
        if not lines:
            return ToolResult("empty", "no concept links for this problem", gaps=[f"The knowledge graph has no concept links for '{title}' yet."])
        ev = tc.ev("N", "graph", f"Knowledge graph paths from '{title}'", " | ".join(lines), f"graph: problem {pid[:8]} (Neo4j traversal, up to 3 hops)")
        tc.add(ev)
        linked = ", ".join(sorted({r["concept"] for r in rows[:MAX_ROWS]}))
        if rows:
            facts.append({"text": f"The knowledge graph links '{title}' to: {linked}.", "kind": "observation", "ids": [ev.id], "mats": []})
        for r in rows[:MAX_ROWS]:
            pre = sorted([p for p in r["prereqs"] if p.get("id")], key=lambda p: p["hops"])
            rels = [x for x in r["rels"] if x.get("reason")]
            if rels:
                facts.append({"text": f"Concept '{r['concept']}' applies because {rels[0]['reason']}.", "kind": "observation", "ids": [ev.id], "mats": []})
            if pre:
                facts.append({"text": f"To understand '{r['concept']}', review the prerequisite '{pre[0]['name']}' first.", "kind": "hypothesis", "ids": [ev.id], "mats": [m["id"] for m in _hops(r["materials"], r["concept_id"], r["prereqs"]) if m["hops"] == pre[0]["hops"]][:1]})
        for s in similar[:2]:
            facts.append({"text": f"Another problem, '{s['title']}' in contest '{s['contest']}', is {s['kind']} to this one (statement similarity {s['statement_score']}); they may be confused with each other.", "kind": "observation", "ids": [ev.id], "mats": []})
        return ToolResult("ok", f"{len(rows)} concept(s), {len(similar)} similar problem(s) from '{title}'", [ev], facts)

    if kind == "concept":
        if not sid:
            raise ToolError("start_id is required for a concept")
        rows = await _graph(tc, "concept_graph", concept_id=sid, staff=tc.staff, **base)
        if not rows or not rows[0].get("name"):
            return ToolResult("empty", "unknown concept")
        r = rows[0]
        mats = _hops(r["materials"], sid, r["prereqs"])
        _note_materials(tc, mats, sid, r["name"], "direct")
        pre = sorted([p for p in r["prereqs"] if p.get("id")], key=lambda p: p["hops"])
        probs = [p for p in r["problems"] if p.get("id")]
        text = f"Concept '{r['name']}'" + (f" requires (prerequisites): {', '.join(p['name'] for p in pre)}" if pre else " has no prerequisites") + "; " + (f"materials: {', '.join(_mat_text(m) for m in mats[:4])}" if mats else "no learning material covers it") + "; " + (f"problems that need it: {', '.join(p['title'] for p in probs[:4])}." if probs else "no problem of this organization needs it.")
        tc.graph_concepts.append({"id": sid, "name": r["name"], "role": "direct"})
        tc.graph_concepts.extend({"id": p["id"], "name": p["name"], "role": "prerequisite"} for p in pre)
        ev = tc.ev("N", "graph", f"Knowledge graph: concept '{r['name']}'", text, f"graph: concept {sid}")
        tc.add(ev)
        facts = [{"text": text, "kind": "observation", "ids": [ev.id], "mats": []}]
        for m in mats:
            if m.get("status") == "deprecated" and m.get("superseded_by"):
                facts.append({"text": f"The material '{_title(m['id'])}' covering this concept is deprecated (updated {m.get('updated')}) and superseded by '{_title(m['superseded_by'])}' in the graph; prefer the newer note.", "kind": "observation", "ids": [ev.id], "mats": [m["id"], m["superseded_by"]]})
        return ToolResult("ok", f"concept '{r['name']}': {len(pre)} prerequisite(s), {len(mats)} material(s)", [ev], facts)

    if kind == "learner":
        if sid and sid != tc.user_id:
            raise ToolDenied("only your own submissions can be traversed")
        rows = await _graph(tc, "learner_paths", user_id=tc.user_id, contest_id=tc.contest_id, failing=graph_store.FAILING, **base)
        return _learner_result(tc, rows)

    if kind == "contest":
        if not tc.staff:
            raise ToolDenied("contest-wide graph analysis is restricted to instructors")
        rows = await _graph(tc, "contest_gaps", contest_id=tc.contest_id, failing=graph_store.FAILING, staff=tc.staff, **base)
        return _gaps_result(tc, rows)
    raise ToolError("unknown start_kind")


def _learner_result(tc: ToolContext, rows: list[dict]) -> ToolResult:
    if not rows:
        return ToolResult("empty", "no failing problems with concept links", gaps=["No problem with repeated or unresolved failures was found in your history."])
    lines, facts, by_concept = [], [], {}
    for r in rows[:MAX_ROWS * 2]:
        pre = sorted([p for p in r["prereqs"] if p.get("id")], key=lambda p: p["hops"])
        mats = _hops(r["materials"], r["concept_id"], r["prereqs"])
        _note_materials(tc, mats, r["concept_id"], r["concept"], "direct")
        by_concept.setdefault((r["concept_id"], r["concept"]), []).append(r)
        line = f"'{r['problem']}' ({r['fails']} failing attempt(s): {', '.join(sorted(set(r['verdicts'])))}{'; later accepted' if r['accepted'] else ''}) -> {r['concept']}"
        if pre:
            line += " <- prerequisite " + pre[0]["name"]
        lines.append(line)
        tc.graph_concepts.append({"id": r["concept_id"], "name": r["concept"], "role": "direct"})
        tc.graph_concepts.extend({"id": p["id"], "name": p["name"], "role": "prerequisite"} for p in pre)
    ev = tc.ev("N", "graph", "Knowledge graph: concepts behind your failing problems", " | ".join(lines[:MAX_ROWS]), f"graph: your submissions in contest {tc.contest_id[:8]} (Neo4j traversal, up to 3 hops)")
    tc.add(ev)
    for (cid, cname), rs in by_concept.items():
        probs = sorted({r["problem"] for r in rs})
        pre = sorted([p for p in rs[0]["prereqs"] if p.get("id")], key=lambda p: p["hops"])
        mats = _hops(rs[0]["materials"], cid, rs[0]["prereqs"])
        text = f"Your failing problems {', '.join(repr(p) for p in probs)} all depend on the concept '{cname}'" + (f", which builds on '{pre[0]['name']}'" if pre else "") + ". A gap there is a possible common cause; this is a hypothesis, not a confirmed diagnosis."
        facts.append({"text": text if len(probs) > 1 else f"'{probs[0]}' depends on the concept '{cname}'" + (f", which builds on '{pre[0]['name']}'" if pre else "") + " (a possible area to review, not a confirmed cause).", "kind": "hypothesis", "ids": [ev.id], "mats": [m["id"] for m in mats[:1]]})
    return ToolResult("ok", f"{len(rows)} failing path(s) across {len(by_concept)} concept(s)", [ev], facts)


def _gaps_result(tc: ToolContext, rows: list[dict]) -> ToolResult:
    if not rows:
        return ToolResult("empty", "no failing submissions with concept links", gaps=["No failing submissions linked to concepts were found in this contest."])
    lines, facts = [], []
    for r in rows[:MAX_ROWS]:
        who = sorted(x for x in r["learners"] if x)
        via = [v for v in r["via_concepts"] if v != r["concept"]]
        lines.append(f"{r['concept']}" + (f" (reached through {', '.join(via[:2])})" if via else "") + f": learners {', '.join(who)}; problems {', '.join(sorted(r['problems'])[:3])}; {r['failing_submissions']} failing submission(s)")
        tc.graph_concepts.append({"id": r["concept_id"], "name": r["concept"], "role": "prerequisite" if via else "direct"})
    ev = tc.ev("N", "graph", "Knowledge graph: concepts shared by failing learners", " | ".join(lines), f"graph: contest {tc.contest_id[:8]} (Neo4j traversal, learners -> submissions -> problems -> concepts -> prerequisites)")
    tc.add(ev)
    ranked = sorted(rows[:MAX_ROWS], key=lambda r: (0 if [v for v in r["via_concepts"] if v != r["concept"]] else 1))  # prerequisite gaps first
    for r in ranked[:5]:
        who = sorted(x for x in r["learners"] if x)
        via = [v for v in r["via_concepts"] if v != r["concept"]]
        probs = ", ".join(sorted(r["problems"])[:3])
        if len(who) >= 2 and via:
            text = f"Candidate shared prerequisite gap - '{r['concept']}': failing submissions by {', '.join(who)} on {probs} trace back to it through {', '.join(via[:2])}. This is a hypothesis: the verdicts alone do not prove a shared misconception."
        elif len(who) >= 2:
            text = f"Shared concept - '{r['concept']}': failing submissions by {', '.join(who)} on {probs} all involve it. This is a hypothesis: the verdicts alone do not prove a shared misconception."
        else:
            text = f"Only {who[0] if who else 'one learner'} is affected by '{r['concept']}', so it is not a shared gap."
        facts.append({"text": text, "kind": "hypothesis", "ids": [ev.id], "mats": []})
    return ToolResult("ok", f"{len(rows)} concept(s); shared by 2+ learners: {sum(1 for r in rows if len(r['learners']) >= 2)}", [ev], facts)


# ---------------- registry ----------------

def _s(min_len=1, max_len=300):
    return ("str", min_len, max_len)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    spec: dict  # arg -> (type, ...)  ; required args are listed in `required`
    required: tuple
    fn: object


TOOLS: dict[str, Tool] = {
    "resolve_entity": Tool("resolve_entity", "Map a name or phrase to problem/concept/material ids (handles aliases, typos, similar names).", {"name": _s(), "kind": ("enum", {"any", "problem", "concept", "material"})}, ("name",), resolve_entity),
    "search_learning_material": Tool("search_learning_material", "BM25 + vector retrieval of learning material; optional concept_ids expand the search through the graph.", {"query": _s(1, 300), "concept_ids": ("idlist", 3)}, ("query",), search_learning_material),
    "get_submission_history": Tool("get_submission_history", "Attempt history for one problem: your own attempts (instructors: per learner).", {"problem_id": ("id",)}, (), get_submission_history),
    "get_judge_history": Tool("get_judge_history", "Judge version of a submission and conflicting verdicts for identical source; instructors also see incidents.", {"submission_id": ("id",), "problem_id": ("id",)}, (), get_judge_history),
    "traverse_graph": Tool("traverse_graph", "Multi-hop Neo4j traversal from a problem, concept, your own failures (learner) or the contest (instructors).", {"start_kind": ("enum", {"problem", "concept", "learner", "contest"}), "start_id": ("id",)}, ("start_kind",), traverse_graph),
}


def validate(tool: str, args) -> dict:
    """Reject anything that is not exactly the documented shape. Nothing here can carry query text."""
    if tool not in TOOLS:
        raise ToolError(f"unknown tool '{_clip(tool, 40)}'")
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object")
    t = TOOLS[tool]
    extra = set(args) - set(t.spec)
    if extra:
        raise ToolError(f"unexpected argument(s): {sorted(extra)}")
    out = {}
    for k in t.required:
        if k not in args:
            raise ToolError(f"missing argument '{k}'")
    for k, v in args.items():
        kind = t.spec[k]
        if v is None:
            continue
        if kind[0] == "str":
            if not isinstance(v, str) or not (kind[1] <= len(v.strip()) <= kind[2]):
                raise ToolError(f"'{k}' must be a string of {kind[1]}-{kind[2]} characters")
            out[k] = v.strip()
        elif kind[0] == "enum":
            if v not in kind[1]:
                raise ToolError(f"'{k}' must be one of {sorted(kind[1])}")
            out[k] = v
        elif kind[0] == "id":
            if not isinstance(v, str) or not _ID.fullmatch(v):
                raise ToolError(f"'{k}' is not a valid id")
            out[k] = v
        elif kind[0] == "idlist":
            if not isinstance(v, list) or len(v) > kind[1] or not all(isinstance(x, str) and _ID.fullmatch(x) for x in v):
                raise ToolError(f"'{k}' must be a list of up to {kind[1]} ids")
            out[k] = v
    return out


def catalogue() -> list[dict]:
    """What the (optional) LLM planner is told about the tools."""
    return [{"name": t.name, "description": t.description, "arguments": {k: v[0] if v[0] != "enum" else sorted(v[1]) for k, v in t.spec.items()}, "required": list(t.required)} for t in TOOLS.values()]


def fuse_materials(tc: ToolContext, base_hits: list, top_k: int = 4) -> tuple[list, str | None]:
    """Combine text retrieval (BM25 [+ pgvector], already reranked) with graph-derived and search-expanded rankings by reciprocal
    rank fusion, then apply the freshness rule: a deprecated note is demoted and, when a newer note supersedes it, that note is
    pulled in. Returns [(Material, provenance)] and a short note on what the graph contributed. With no graph/search rankings the
    base hits are returned unchanged."""
    from . import assistant

    by_id = {d.id: d for d in assistant.INDEX.docs}
    graph_rank = [m["id"] for m in tc.graph_materials if m["id"] in by_id]
    extra = [[i for i in r if i in by_id] for r in tc.searched]
    if not graph_rank and not any(extra):
        return [(m, "text retrieval") for m, _ in base_hits[:3]], None
    rankings = [[m.id for m, _ in base_hits]] + ([graph_rank] if graph_rank else []) + [r for r in extra if r]
    fused = vectors.rrf(rankings)
    for mid in list(fused):  # stale guidance -> its replacement (evidence the graph knows about via SUPERSEDED_BY)
        d = by_id[mid]
        if d.status == "deprecated" and d.superseded_by in by_id and d.superseded_by not in fused:
            fused[d.superseded_by] = fused[mid]
    ranked = sorted(fused.items(), key=lambda kv: kv[1] * (0.4 if by_id[kv[0]].status == "deprecated" else 1.0), reverse=True)[:top_k]
    g = {m["id"]: m for m in tc.graph_materials}
    text_ids = {m.id for m, _ in base_hits} | {i for r in extra for i in r}
    out = []
    for mid, _ in ranked:
        d = by_id[mid]
        parts = []
        if mid in text_ids:
            parts.append("text retrieval")
        if mid in g:
            m = g[mid]
            parts.append(f"knowledge graph: covers '{m['via']}'" + (f" (prerequisite, {m['hops']} hop{'s' if m['hops'] != 1 else ''})" if m["hops"] else ""))
        if not parts:
            parts.append("supersedes a deprecated note")
        out.append((d, " + ".join(parts)))
    contributed = [m for m, via in out if "knowledge graph" in via]
    note = None
    if contributed:
        only = [m.title for m, via in out if via.startswith("knowledge graph")]
        note = f"graph contributed {len(contributed)} material(s)" + (f", {len(only)} found only through the graph" if only else "")
    return out, note

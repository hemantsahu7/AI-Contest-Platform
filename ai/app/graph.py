"""Per-request relationship graph over ALREADY-AUTHORIZED data (the caller's own submissions, or all contest
submissions for staff). This is an in-memory typed-edge traversal, NOT a graph database:

  User -SUBMITTED-> Submission -FOR-> Problem -IN-> Contest
  Submission -RESULTED_IN-> Verdict          Problem -RELATED_TO-> LearningMaterial

Multi-hop questions ("which problems did I struggle with, what verdicts, what should I study?") are answered by
walking these edges, so each answer part is traceable to the path that produced it."""
from dataclasses import dataclass, field

FAILING = {"WRONG_ANSWER", "COMPILATION_ERROR", "RUNTIME_ERROR", "TIME_LIMIT_EXCEEDED"}  # JUDGE_ERROR is not the learner's fault


@dataclass
class Graph:
    edges: list[tuple[str, str, str]] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    props: dict[str, dict] = field(default_factory=dict)

    def add(self, src: str, rel: str, dst: str, src_label: str | None = None, dst_label: str | None = None, **props):
        self.edges.append((src, rel, dst))
        if src_label:
            self.labels[src] = src_label
        if dst_label:
            self.labels[dst] = dst_label
        for k, v in props.items():
            self.props.setdefault(dst, {})[k] = v

    def out(self, node: str, rel: str | None = None) -> list[str]:
        return [d for s, r, d in self.edges if s == node and (rel is None or r == rel)]

    def inc(self, node: str, rel: str | None = None) -> list[str]:
        return [s for s, r, d in self.edges if d == node and (rel is None or r == rel)]


def build(subs: list[dict], contest_id: str, contest_title: str) -> Graph:
    g = Graph()
    for s in sorted(subs, key=lambda x: x["submittedAt"]):
        user = f"user:{s['userId']}"
        sub, prob = f"submission:{s['id']}", f"problem:{s['problemId']}"
        who = s.get("username") or s["userId"][:8]
        g.add(user, "SUBMITTED", sub, who, f"submission {s['id'][:8]}", at=s["submittedAt"], source=s.get("sourceCode"))
        g.add(sub, "FOR", prob, None, (s.get("problem") or {}).get("title", s["problemId"][:8]))
        g.add(prob, "IN", f"contest:{contest_id}", None, contest_title)
        v = s["verdict"] if s["status"] != "INFRASTRUCTURE_ERROR" else "JUDGE_ERROR"
        g.add(sub, "RESULTED_IN", f"verdict:{v}", None, v)
    return g


def problem_history(g: Graph, user: str) -> dict[str, list[tuple[str, str]]]:
    """Two hops from a user: submissions, then the problem and verdict of each. problem node -> [(submission, verdict)]"""
    hist: dict[str, list[tuple[str, str]]] = {}
    for sub in g.out(user, "SUBMITTED"):
        verdict = g.out(sub, "RESULTED_IN")[0].split(":", 1)[1]
        for prob in g.out(sub, "FOR"):
            hist.setdefault(prob, []).append((sub, verdict))
    return hist


def struggled(hist: dict[str, list[tuple[str, str]]], staff: bool = False) -> dict[str, list[tuple[str, str]]]:
    """Problems never accepted, or accepted only after 2+ failing attempts (1+ when staff look for shared gaps).
    Judge errors do not count against the learner."""
    out = {}
    for prob, attempts in hist.items():
        fails = [a for a in attempts if a[1] in FAILING]
        accepted = any(v == "ACCEPTED" for _, v in attempts)
        if fails and (not accepted or len(fails) >= (1 if staff else 2)):
            out[prob] = attempts
    return out


def render_path(g: Graph, user: str, prob: str, attempts: list[tuple[str, str]], materials: list[str]) -> str:
    verdicts = " -> ".join(v for _, v in attempts)
    mats = "; ".join(materials) or "no matching material"
    return (f"{g.labels.get(user, user)} -SUBMITTED-> {len(attempts)} submission(s) -FOR-> '{g.labels.get(prob, prob)}' "
            f"[verdicts oldest to newest: {verdicts}] -RELATED_TO-> {mats}")

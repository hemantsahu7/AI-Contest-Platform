"""Entity resolution: free text -> stable ids, across names, aliases, typos, capitalization, similar and duplicate names.
Pure functions, no services."""
from app import entities, knowledge

P_SUM = {"id": "p-sum", "title": "Sum of Two Numbers", "aliases": ["Add Two Numbers", "A plus B"], "contest_id": "c1", "contest": "Open"}
P_MAX = {"id": "p-max", "title": "Maximum of Two", "aliases": ["Larger of Two"], "contest_id": "c1", "contest": "Open"}
P_DIFF = {"id": "p-diff", "title": "Absolute Difference", "aliases": [], "contest_id": "c1", "contest": "Open"}
P_DIGITS = {"id": "p-digits", "title": "Sum of Digits", "aliases": [], "contest_id": "c2", "contest": "Archive"}
P_DUP = {"id": "p-dup", "title": "A + B", "aliases": [], "contest_id": "c2", "contest": "Archive"}
PROBLEMS = [P_SUM, P_MAX, P_DIFF, P_DIGITS, P_DUP]


def best(q, ents=PROBLEMS, prefer="c1"):
    r = entities.resolve(q, ents, "problem", prefer_contest=prefer)
    return r.best.id if r.best else None, r


def test_exact_title_and_id():
    assert best("Sum of Two Numbers")[0] == "p-sum"
    assert best("what about problem p-max?")[0] == "p-max"  # a stable id in the text wins
    r = best("Sum of Two Numbers")[1]
    assert r.candidates[0].score == 1.0 and r.candidates[0].method == "title"


def test_capitalization_and_punctuation_do_not_matter():
    assert best("SUM OF TWO NUMBERS!!")[0] == "p-sum"
    assert best("sum   of two, numbers")[0] == "p-sum"


def test_alias_resolves_old_or_alternative_names():
    pid, r = best("Give me a hint for Add Two Numbers")
    assert pid == "p-sum" and r.candidates[0].method == "alias"
    assert best("help with larger of two")[0] == "p-max"


def test_abbreviation_plural_and_typo():
    assert best("the max one")[0] == "p-max"  # abbreviation of a title word
    assert best("Maximun of two")[0] == "p-max"  # typo, fuzzy match
    assert best("Absolute Differences problem")[0] == "p-diff"  # plural


def test_summarize_does_not_match_the_sum_problem():
    """Whole-word rules: 'summarize' is not 'Sum'."""
    assert best("Summarize my verdicts for this week")[0] is None
    assert entities.resolve("Summarize my verdicts", PROBLEMS, "problem").candidates == []


def test_ambiguous_names_ask_instead_of_guessing():
    """Two problems that answer to the same old name: the resolver must not pick one."""
    a = {"id": "p-a", "title": "Add Numbers", "aliases": ["Sum problem"], "contest_id": "c1", "contest": "Open"}
    b = {"id": "p-b", "title": "Add Integers", "aliases": ["Sum problem"], "contest_id": "c1", "contest": "Open"}
    pid, r = best("help with the sum problem", ents=[a, b, P_MAX], prefer="c1")
    assert r.ambiguous and pid is None
    assert {c.name for c in r.candidates} == {"Add Numbers", "Add Integers"}


def test_preferred_contest_breaks_ties_with_a_same_named_problem_elsewhere():
    twin = {"id": "p-sum-old", "title": "Sum of Two Numbers", "aliases": [], "contest_id": "c-archive", "contest": "Archive"}
    pid, r = best("Sum of Two Numbers", ents=[P_SUM, twin], prefer="c1")
    assert pid == "p-sum"  # ambiguity is judged inside the contest the user is asking about
    assert {c.id for c in r.candidates} == {"p-sum", "p-sum-old"}  # the archived twin is still reported as a candidate
    pid_none, r2 = best("Sum of Two Numbers", ents=[P_SUM, twin], prefer=None)
    assert r2.ambiguous and pid_none is None


def test_unrelated_text_matches_nothing():
    assert best("What is the weather today?")[0] is None
    assert best("")[0] is None


def test_concepts_and_materials_resolve_like_problems():
    concepts = knowledge.file_concepts()
    r = entities.resolve("why is int overflow a problem", concepts, "concept")
    assert r.best and r.best.id == "integer-overflow" and r.best.method in ("alias", "title", "words")
    r = entities.resolve("numbers too big for int", concepts, "concept")
    assert r.candidates and r.candidates[0].id == "integer-overflow"
    r = entities.resolve("I keep getting segmentation faults", concepts, "concept")
    assert any(c.id == "runtime-errors" for c in r.candidates)


def test_authored_aliases_cover_problems_and_materials():
    al = knowledge.file_aliases()
    assert "Add Two Numbers" in al["problem"]["44444444-4444-4444-8444-444444444441"]
    assert al["concept"]["integer-overflow"]  # concept aliases come from concepts.json
    assert "legacy-cpp-io" in al["material"]


def test_looks_like_id():
    assert entities.looks_like_id("44444444-4444-4444-8444-444444444441")
    assert not entities.looks_like_id("Sum of Two Numbers")

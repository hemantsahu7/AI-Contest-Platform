"""Facts derivable from a problem statement alone (shared by the code analysis and the graph projection)."""
import re

INT32_MAX = 2_147_483_647


def statement_bound(problem: dict) -> tuple[int | None, str]:
    """Largest magnitude the statement mentions (e.g. '-4000000000 <= A, B <= 4000000000', '4*10^9') and what the task
    does with it (sum / product / value)."""
    text = f"{problem.get('description', '')} {problem.get('inputFormat', '')}"
    nums = [int(n.replace(",", "")) for n in re.findall(r"(?<![\w.])(\d[\d,]{4,})(?![\w.])", text)]
    nums += [int(a) * 10 ** int(b) for a, b in re.findall(r"(\d+)\s*[x*\u00b7]\s*10\s*\^\s*(\d+)", text)]
    nums += [10 ** int(b) for b in re.findall(r"10\s*\^\s*(\d+)", text)]
    bound = max(nums) if nums else None
    if re.search(r"a\s*\*\s*b|product|multipl", text, re.I):
        return bound, "product"
    if re.search(r"a\s*\+\s*b|\bsum\b|\badd", text, re.I):
        return bound, "sum"
    return bound, "value"


def worst_case(bound: int, op: str) -> int:
    return bound * bound if op == "product" else 2 * bound if op == "sum" else bound

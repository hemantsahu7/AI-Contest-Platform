"""GraphRAG evaluation wrapper: BASELINE (text retrieval only) vs GRAPHRAG (entity resolution + Neo4j traversal + fused retrieval)
on the labelled questions in ai/eval/graph_queries.json. Runs inside the ai container against the running, seeded stack.
The model is never called (evidence-only answers), so this makes ZERO Gemini requests.

  python scripts/graph_eval.py                 # needs: docker compose up -d
  python scripts/graph_eval.py --json out.json

Details and methodology: ai/eval/graph_eval.py"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="also write the result JSON to this path")
    args = ap.parse_args()
    env = dict(os.environ, AI_MODEL_DISABLED="1")
    cmd = ["docker", "compose", "run", "--rm", "--no-deps", "-e", "AI_MODEL_DISABLED=1", "-v", f"{ROOT / 'ai' / 'eval'}:/srv/eval", "ai", "python", "-m", "eval.graph_eval"]
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    result = None
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT_JSON:"):
            result = json.loads(line[len("RESULT_JSON:"):])
        else:
            print(line)
    if proc.returncode != 0 or result is None:
        print(proc.stderr[-1500:], file=sys.stderr)
        return proc.returncode or 1
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Retrieval comparison (BM25 vs vector vs the production hybrid) on a small labelled query set.
Runs inside the ai container (it needs pgvector); this wrapper just launches it.

  python scripts/retrieval_eval.py              # cached real Gemini embeddings: makes ZERO Gemini calls
  python scripts/retrieval_eval.py --refresh    # re-embed corpus+queries: exactly 2 batched embedding requests (needs GEMINI_API_KEY)
  python scripts/retrieval_eval.py --json out.json

Details and methodology: ai/eval/retrieval_eval.py"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--json", help="also write the result JSON to this path")
    args = ap.parse_args()
    env = dict(os.environ)
    if args.refresh:
        env.pop("AI_MODEL_DISABLED", None)  # embeddings need the key enabled; generation is never called
    else:
        env["AI_MODEL_DISABLED"] = "1"
    cmd = ["docker", "compose", "run", "--rm", "--no-deps", "-v", f"{ROOT / 'ai' / 'eval'}:/srv/eval", "ai", "python", "-m", "eval.retrieval_eval"]
    if args.refresh:
        cmd.append("--refresh")
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

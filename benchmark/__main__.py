"""python -m benchmark [--llm] [--sandbox local|docker] [--case ID ...] [--out DIR]"""

import argparse
import logging
from pathlib import Path

from benchmark.runner import render_report, run_benchmark, to_json


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m benchmark", description=__doc__)
    p.add_argument(
        "--llm", action="store_true", help="also use the model (needs CRA_ANTHROPIC_API_KEY)"
    )
    p.add_argument("--sandbox", choices=["local", "docker"], default="local",
                   help="local runs the (trusted) corpus without isolation; docker isolates it")  # fmt: skip
    p.add_argument("--case", action="append", help="run only this case id (repeatable)")
    p.add_argument(
        "--out", default="benchmark/results", help="directory for report.md and results.json"
    )
    args = p.parse_args()

    logging.getLogger("code_review_agent").setLevel(logging.ERROR)
    results, summary = run_benchmark(use_llm=args.llm, sandbox_kind=args.sandbox, only=args.case)
    report = render_report(results, summary)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = "llm" if args.llm else "tools-only"
    (out / f"{stem}.md").write_text(report)
    (out / f"{stem}.json").write_text(to_json(results, summary))
    print(report)
    print(f"written to {out}/{stem}.md and .json")


if __name__ == "__main__":
    main()

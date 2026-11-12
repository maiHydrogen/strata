"""Chat with STRATA in the terminal to build a client brief, then its cited spec.

Run: python scripts/brief_chat.py [name]
Needs GEMINI_API_KEY in .env (optional STRATA_LLM_MODEL to pin the model). Writes
output/<name>.brief.json, output/<name>.spec.json and the prompt log output/logs/<name>.jsonl.
"""

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

from src.brief.chat import BriefSession  # noqa: E402
from src.brief.llm import GeminiClient  # noqa: E402
from src.inference.spec import build_spec  # noqa: E402


def main():
    load_dotenv(os.path.join(ROOT, ".env"))
    name = sys.argv[1] if len(sys.argv) > 1 else time.strftime("brief_%Y%m%d_%H%M%S")
    llm = GeminiClient()
    print(f"(model: {llm.model})")
    session = BriefSession(llm, log_path=os.path.join(ROOT, "output", "logs", f"{name}.jsonl"))
    print(f"STRATA: {session.start()}")
    while not session.done:
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nStopped; nothing written.")
            return
        if text:
            print(f"STRATA: {session.reply(text)}")
    brief = session.final_brief()
    out = os.path.join(ROOT, "output")
    with open(os.path.join(out, f"{name}.brief.json"), "w") as f:
        f.write(brief.model_dump_json(indent=2))
    spec = build_spec(brief)
    with open(os.path.join(out, f"{name}.spec.json"), "w") as f:
        json.dump(spec, f, indent=2)
    for c in spec["checks"]:
        print(f"{'PASS' if c['pass'] else 'FAIL'} {c['check']}: {c['actual']}")
    print(f"Wrote output/{name}.brief.json and output/{name}.spec.json")


if __name__ == "__main__":
    main()

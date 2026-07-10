"""Quick smoke test: run the agent directly (no HTTP server) and inspect results."""
import json
import sys

from app.agent import Agent

if __name__ == "__main__":
    request = " ".join(sys.argv[1:]) or "Create a project plan for launching a new mobile banking app"
    agent = Agent()
    result = agent.run(request)

    print("=" * 70)
    print("STATUS:", result["status"])
    print("DOCUMENT TYPE:", result["document_type"])
    print("TITLE:", result["title"])
    print("LLM PROVIDER USED:", result["llm_provider_used"])
    print("OUTPUT FILE:", result["output_path"])
    print("SUMMARY:", result["summary"])
    print("-" * 70)
    print("TASK PLAN / EXECUTION LOG:")
    for t in result["plan"]:
        print(f"  [{t.status.upper():^10}] #{t.id} {t.name}" + (f"  -> {t.detail}" if t.detail else ""))
    print("=" * 70)

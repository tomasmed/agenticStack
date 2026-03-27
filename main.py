import sys, os
from dotenv import load_dotenv
load_dotenv()
from flows import WebBuilderFlow
from pathlib import Path

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "build":
        args = [a for a in sys.argv[2:] if a != "--force"]
        request = " ".join(args)

        flow = WebBuilderFlow()
        flow.state["force_reindex"] = "--force" in sys.argv
        if not request:
            run_id_file = Path(os.getenv("TARGET_REPO_PATH", "")) / "workspace" / ".run_id"
            if run_id_file.exists():
                print(f"[main] Resuming existing run...")
                flow.kickoff()
            else:
                print("Usage: python main.py build <business request> [--force]")
                sys.exit(1)
        else:
            flow.state["business_request"] = request
            flow.kickoff()
    else:
        print("Usage:")
        print("  python main.py build")
        print("  python main.py schedule")
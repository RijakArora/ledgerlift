"""Ledgerlift daily automation: research + outreach drafting.

Runs unattended once a day and writes everything to digests/<date>.md
for review.

What it does NOT do, by design:
- It does not send anything. Outreach is drafted for a human to read,
  personalise and send.
- It does not collect anyone's contact details. Prospecting research
  targets *channels* (communities, directories, forums where the target
  customer gathers), never individual people's emails or numbers.

Both limits are deliberate. Automated unsolicited mail from the
operator's own Gmail - which is also the published support address on
the live site - risks that account being flagged, which would break the
support contact the Terms, Refund Policy and Privacy Policy all point
customers to.

Runs against the ai_business_platform API, so every draft still goes
through sales_agent and lands in that platform's audit log.
"""
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent.parent
PLATFORM_DIR = Path(
    r"C:\Users\arora\Downloads\files (3)\ai_business_platform_extracted\ai_business_platform"
)
ENV = dotenv_values(PLATFORM_DIR / ".env")
AGENT_API_KEY = ENV.get("AGENT_API_KEY", "")
API_BASE = "http://127.0.0.1:8000"

STATE_FILE = BASE_DIR / "scripts" / "cycle_state.json"
DIGEST_DIR = BASE_DIR / "digests"

PRODUCT_URL = "https://ledgerlift-wwbx.onrender.com"

# Rotating research queue. These are the open questions that actually
# matter for Ledgerlift right now - add to the list as new ones surface.
RESEARCH_TOPICS = [
    "online communities, forums, subreddits and professional directories where US freelance "
    "bookkeepers and small accounting practices gather and discuss tools - channels only, "
    "not individual contact details",

    "what accuracy level do bookkeepers consider acceptable for automated invoice and receipt "
    "data extraction before they stop double-checking every field",

    "common objections bookkeepers give for not switching away from Dext, Hubdoc or manual "
    "data entry - switching costs, trust, client workflow disruption",

    "what QuickBooks Online and Xero integration actually requires for a third-party app to "
    "push expense or bill data - API, app-store listing requirements, review process",

    "recent pricing or packaging changes at Dext, Nanonets, Docsumo, Hubdoc and AutoEntry - "
    "and whether any have moved away from per-seat pricing",

    "data privacy expectations of US bookkeepers when uploading client financial documents to "
    "a third-party AI tool - what disclosures or assurances they look for",

    "experiences of solo founders onboarding to Paddle or Lemon Squeezy as merchant of record - "
    "approval friction, timelines, common rejection reasons",
]

# Each cycle drafts one outreach variant against a different angle.
OUTREACH_ANGLES = [
    "the time-saved angle - lead with hours per week reclaimed from manual entry, not features",

    "the per-account-not-per-seat angle - aimed at a bookkeeper who shares access with clients "
    "or an accountant and is tired of per-seat pricing multiplying the bill",

    "the honest-limitations angle - lead with what it does NOT do well and why blank beats a "
    "guessed value, which is a trust play rather than a feature pitch",

    "a short follow-up for someone who tried it and went quiet - low pressure, asking for a "
    "blunt verdict rather than a favour",
]


def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"research_idx": 0, "outreach_idx": 0, "last_run_date": None}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _ensure_platform_up() -> bool:
    try:
        if httpx.get(f"{API_BASE}/health", timeout=3).status_code == 200:
            return True
    except Exception:
        pass

    venv_python = PLATFORM_DIR / ".venv" / "Scripts" / "python.exe"
    subprocess.Popen(
        [str(venv_python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=str(PLATFORM_DIR),
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
    )
    for _ in range(20):
        time.sleep(1.5)
        try:
            if httpx.get(f"{API_BASE}/health", timeout=3).status_code == 200:
                return True
        except Exception:
            continue
    return False


def _submit(payload: dict) -> dict:
    headers = {"x-api-key": AGENT_API_KEY, "Content-Type": "application/json"}
    r = httpx.post(f"{API_BASE}/tasks", headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    return r.json()


def run_cycle(force: bool = False) -> None:
    if not AGENT_API_KEY:
        print("AGENT_API_KEY not found - aborting.")
        return
    if not _ensure_platform_up():
        print("Could not reach or start the platform API - aborting this cycle.")
        return

    state = _load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("last_run_date") == today and not force:
        print(f"Already ran today ({today}) - skipping.")
        return

    lines = [f"# Ledgerlift daily cycle - {today}\n"]

    topic = RESEARCH_TOPICS[state["research_idx"] % len(RESEARCH_TOPICS)]
    try:
        res = _submit({"task_type": "research", "description": topic})
        lines += [f"## Research: {topic}\n", res.get("output", "(no output)"), ""]
    except Exception as e:
        lines += [f"## Research FAILED\n{topic}\n\nError: {e}\n"]
    state["research_idx"] += 1

    angle = OUTREACH_ANGLES[state["outreach_idx"] % len(OUTREACH_ANGLES)]
    try:
        res = _submit({"task_type": "sales", "description": (
            f"Draft pilot-recruitment outreach for Ledgerlift, live at {PRODUCT_URL} "
            "(use plain ASCII hyphens in the URL). Angle: " + angle + ". "
            "Write in FIRST PERSON singular - this is a solo founder, there is no team, "
            "never write 'we' or 'our team'. The ask is a free pilot and honest feedback, "
            "not a sale - there is no payment set up and the free tier is 150 documents/month. "
            "Be honest that extraction is automated and imperfect, and that unreadable fields "
            "come back blank rather than guessed. MUST state plainly that it currently runs on "
            "Google's unpaid Gemini API tier, so uploaded documents may be reviewed by Google "
            "and used to improve Google's products, and ask them to use non-sensitive or "
            "redacted documents. Disclose AI-assisted drafting and do not claim any prior "
            "human review happened. Leave [Name] and personal context as placeholders for a "
            "human to fill in."
        )})
        lines += [f"## Outreach draft (NOT SENT): {angle}\n",
                  f"Status: {res.get('status')}\n", res.get("output", "(no output)"), ""]
    except Exception as e:
        lines += [f"## Outreach draft FAILED\n{angle}\n\nError: {e}\n"]
    state["outreach_idx"] += 1

    state["last_run_date"] = today
    _save_state(state)

    DIGEST_DIR.mkdir(exist_ok=True)
    path = DIGEST_DIR / f"{today}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Cycle complete -> {path}")


if __name__ == "__main__":
    import sys
    run_cycle(force="--force" in sys.argv)

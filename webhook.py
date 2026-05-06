import json
import os
import re
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from twilio.request_validator import RequestValidator


load_dotenv()
app = Flask(__name__)
DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
INBOUND_FILE = DATA_DIR / "inbound_replies.json"
RUNTIME_STATE_FILE = DATA_DIR / "runtime_state.json"


def normalize_phone(value: str) -> str:
    return re.sub(r"[^0-9+]", "", value or "")


def load_entries():
    if not INBOUND_FILE.exists():
        return []
    with INBOUND_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_entries(entries):
    with INBOUND_FILE.open("w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


@app.post("/twilio/reply")
def twilio_reply():
    validator = RequestValidator(os.environ["TWILIO_AUTH_TOKEN"])
    signature = request.headers.get("X-Twilio-Signature", "")
    is_valid = validator.validate(request.url, request.form.to_dict(flat=True), signature)
    if not is_valid:
        return jsonify({"error": "invalid signature"}), 403

    from_number = normalize_phone(request.form.get("From", ""))
    expected = normalize_phone(os.getenv("TWILIO_EXPECTED_FROM_NUMBER", ""))
    if expected and from_number != expected:
        return jsonify({"error": "unexpected sender"}), 403

    body = (request.form.get("Body") or "").strip()
    run_id_match = re.search(r"\[([A-Z0-9]{8})\]", body)
    run_id = run_id_match.group(1) if run_id_match else ""
    if not run_id:
        if RUNTIME_STATE_FILE.exists():
            with RUNTIME_STATE_FILE.open("r", encoding="utf-8") as state_file:
                state = json.load(state_file)
            run_id = state.get("last_run_id", "")
        if not run_id:
            run_id = os.getenv("TWILIO_LAST_RUN_ID", "")

    entries = load_entries()
    entries.append(
        {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "from": request.form.get("From"),
            "body": body.replace(f"[{run_id}]", "").strip().upper(),
            "run_id": run_id,
        }
    )
    save_entries(entries)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8787)

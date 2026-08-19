"""BICT131 Code Tutor — Flask backend.

Vertical slice: one endpoint that takes a student's code + description of the
problem, asks Groq for a Socratic hint, and returns it as JSON.
"""

import os

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS

from detectors import detect_bugs
from prompts import (
    build_system_prompt,
    build_user_message,
    level_for_attempt,
    trim_blank_lines,
)

load_dotenv()

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Was llama-3.3-70b-versatile; Groq retired it (404 model_not_found) and serves
# no Llama chat model on the free tier any more. Swapped 2026-08-17 with sign-off.
GROQ_MODEL = "openai/gpt-oss-120b"

# Free tier has per-minute token limits, so keep replies short at the API level
# too, not just by asking nicely in the prompt. Sized for the longest reply we
# ever ask for — a Level 3 explanation plus a corrected snippet — so the fix
# never gets truncated mid-code-block. Levels 1 and 2 finish well under this.
MAX_TOKENS = 500
TEMPERATURE = 0.6
REQUEST_TIMEOUT = 30

# Guard rails on input size — a first-year exercise is not 50KB of code, and
# oversized requests just burn free-tier tokens.
MAX_CODE_CHARS = 8000
MAX_ISSUE_CHARS = 1000

app = Flask(__name__)

# The frontend is deployed separately (Netlify -> Render), so CORS is required
# in production, not just in dev. Override ALLOWED_ORIGINS on Render.
allowed_origins = os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173")
CORS(app, resources={r"/tutor": {"origins": [o.strip() for o in allowed_origins.split(",")]}})


@app.get("/health")
def health():
    """Cheap liveness check. Also reports whether the key is configured, so a
    misconfigured deploy is obvious without having to make a real request."""
    return jsonify({
        "status": "ok",
        "model": GROQ_MODEL,
        "groq_key_configured": bool(os.environ.get("GROQ_API_KEY")),
    })


@app.post("/tutor")
def tutor():
    data = request.get_json(silent=True) or {}

    # trim_blank_lines, not .strip(): the student's own indentation is part of
    # the bug report, including on line 1 of an indented fragment.
    code = trim_blank_lines(str(data.get("code") or ""))
    issue = str(data.get("issue") or "").strip()

    if not code:
        return jsonify({"error": "Paste some code first."}), 400
    if not issue:
        return jsonify({"error": "Describe what's going wrong first."}), 400
    if len(code) > MAX_CODE_CHARS:
        return jsonify({"error": f"That code is too long (limit {MAX_CODE_CHARS} characters)."}), 400
    if len(issue) > MAX_ISSUE_CHARS:
        return jsonify({"error": f"That description is too long (limit {MAX_ISSUE_CHARS} characters)."}), 400

    # The frontend sends its attempt count, but it is only ever a hint. Anything
    # unparseable becomes 0, and the server decides what it means — the client
    # must not be able to skip straight to the answer. The attempt -> level
    # decision itself belongs to prompts.level_for_attempt(); this is only
    # parsing, not policy.
    try:
        attempt = int(data.get("attempt", 0))
    except (TypeError, ValueError):
        attempt = 0
    attempt = max(0, attempt)

    level = level_for_attempt(attempt)

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return jsonify({
            "error": "GROQ_API_KEY is not set on the server. Copy backend/.env.example "
                     "to backend/.env and add your key."
        }), 500

    # Rule-based pass before the LLM. These are a lead for the model, never a
    # substitute for it and never shown to the student — note that `tags` is
    # deliberately absent from the response below. An empty list is the normal
    # case and produces exactly the prompt we sent before the detectors existed.
    tags = detect_bugs(code)

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": build_system_prompt(attempt, tags)},
            {"role": "user", "content": build_user_message(code, issue)},
        ],
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
    }

    try:
        response = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.Timeout:
        return jsonify({"error": "The tutor took too long to respond. Try again."}), 504
    except requests.RequestException as exc:
        return jsonify({"error": f"Could not reach the Groq API: {exc}"}), 502

    if response.status_code == 401:
        return jsonify({"error": "Groq rejected the API key. Check GROQ_API_KEY in backend/.env."}), 502
    if response.status_code == 429:
        return jsonify({"error": "Hit the Groq free-tier rate limit. Wait a moment and try again."}), 429
    if not response.ok:
        # Surface Groq's own message where possible — it is usually specific
        # (bad model name, malformed payload) and worth seeing during the build.
        detail = response.text[:300]
        return jsonify({"error": f"Groq API error ({response.status_code}): {detail}"}), 502

    try:
        reply = response.json()["choices"][0]["message"]["content"].strip()
    except (ValueError, KeyError, IndexError, AttributeError):
        return jsonify({"error": "Got an unexpected response shape from Groq."}), 502

    return jsonify({
        "reply": reply,
        "level": level,
        "attempt": attempt,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

# BICT131 Code Tutor

An AI-driven Socratic tutor for first-year programming students. The student pastes
buggy code and describes what's going wrong; the tutor replies with a guiding
**question** rather than the fix.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the architecture and the core Socratic principle.

## Current state: vertical slice

This is a thin end-to-end slice — frontend → backend → Groq → response — not the
finished product. What works today:

- `POST /tutor` accepts code + issue, calls Groq, returns a hint.
- A single-page React UI that submits to it and renders the reply.
- **Escalation logic** (Level 1 / 2 / 3), server-side in `backend/prompts.py`.
  The frontend sends an attempt count; `level_for_attempt()` clamps it and
  decides the level, so a client cannot skip the ladder.
- **Rule-based bug detectors** in `backend/detectors.py` — six checks that run
  before the Groq call and append a plain-English tag to the system prompt as
  extra context. The tag is a lead for the model, never shown to the student,
  and never changes the level.

Not built yet:

- The chat-style conversation panel and the "why this matters" strip.

## Layout

```
backend/     Flask API — the tutoring logic and the Groq call
frontend/    React (Vite + Tailwind) — the student-facing UI
```

## Prerequisites

- Python 3.10+
- Node.js 18+
- A free Groq API key — https://console.groq.com/keys

## Backend setup

From the repo root:

```bash
cd backend
python -m venv venv
```

Activate the virtualenv:

```bash
# Windows (PowerShell)
venv\Scripts\Activate.ps1

# macOS / Linux
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Add your API key — copy the example file and fill in the blank:

```bash
# Windows (PowerShell)
copy .env.example .env

# macOS / Linux
cp .env.example .env
```

Then open `backend/.env` and set your key:

```
GROQ_API_KEY=gsk_your_real_key_here
```

`.env` is gitignored. Never commit it, and never paste the key into source files.

Run the server:

```bash
python app.py
```

It listens on **http://localhost:5000**. Check it's healthy:

```bash
curl http://localhost:5000/health
```

`groq_key_configured` should be `true`. If it's `false`, the `.env` file isn't
being picked up.

## Frontend setup

In a **second terminal**, from the repo root:

```bash
cd frontend
npm install
npm run dev
```

Open the URL it prints — usually **http://localhost:5173**.

The frontend talks to `http://localhost:5000` by default. To point it elsewhere
(e.g. the deployed Render backend), copy `frontend/.env.example` to
`frontend/.env` and set `VITE_API_URL`.

Both servers need to be running at the same time.

## API

### `POST /tutor`

Request:

```json
{
  "code": "for i in range(1, 10):\n    print(i)",
  "issue": "it only prints up to 9, I wanted 10",
  "attempt": 0
}
```

Response:

```json
{
  "reply": "What does range(1, 10) actually stop at...?",
  "level": 1,
  "attempt": 0
}
```

`attempt` is the number of tries the student has already made. The server decides
what level of help that earns — the frontend cannot skip levels by lying about it.
`level` is currently always `1` until the escalation logic lands.

Errors come back as `{ "error": "..." }` with a 4xx/5xx status.

### `GET /health`

Liveness check. Reports the model in use and whether `GROQ_API_KEY` is configured.

## Secrets

The Groq key is read from the `GROQ_API_KEY` environment variable and is never
hardcoded. Locally that comes from `backend/.env`; on Render, set it as an
environment variable in the dashboard.

# BICT131 Code Tutor

## What this is
An AI-driven Socratic tutor for BICT131 (first-year programming) students. The student
pastes buggy code + a description of what's going wrong. The tutor asks a guiding QUESTION
first — it never hands over the fix immediately. It only gives the direct fix after the
student has genuinely attempted and is still stuck.

This is a group project for BICT332. Deadline: Friday 28 August 2026.

## Core principle (do not violate)
The tutor is Socratic. Level 1 = a guiding question. Level 2 = a narrower hint after one
failed attempt. Level 3 = direct explanation + fix, but ONLY after 3 attempts. The attempt
counter and escalation logic live SERVER-SIDE, not in the frontend — the frontend must not
be able to skip levels.

## Stack (decided — do not swap without asking)
- Frontend: React (Vite + Tailwind). Deployed on Vercel.
  (Was Netlify in the original plan — swapped to Vercel 19 Aug 2026 with
  sign-off. No code change; the Vite build deploys to either.)
- Backend: Flask (Python). Deployed on Render.
- LLM: Groq API, model openai/gpt-oss-120b. Free tier only.
  (Was llama-3.3-70b-versatile until 17 Aug 2026 — Groq retired it and now serves
  no Llama chat model on the free tier. Swapped with sign-off.)
- Bug detection: rule-based Python functions for common first-year bugs
  (off-by-one, indentation, scope confusion, missing return, wrong comparison operator).
  These run BEFORE the LLM call and pass a tag into the prompt as extra context.
- No local models. No paid APIs. Everything must stay on free tiers.

## Architecture flow
Student (browser) → React frontend → POST /tutor → Flask backend
→ rule-based checks tag known patterns → build prompt → Groq API → hint text
→ JSON back to frontend → rendered in chat panel.

## UI layout
- Left panel: code input box + "what's going wrong?" text field + "Ask the Tutor" button.
- Right panel: chat-style hint conversation (tutor questions left, student replies right).
- Bottom strip: "Why this matters" — one-line plain-English concept explainer, shown
  after the bug is resolved.
- No user accounts or persistence needed — session-based state is fine for the demo.

## Secrets
- Groq API key goes in an environment variable (GROQ_API_KEY), never hardcoded, never
  committed. Use a .env file locally and .gitignore it.

## Working style
- Build a thin end-to-end vertical slice FIRST (frontend → backend → Groq → response),
  then add features onto something that already runs.
- Write the rule-based bug checks as isolated, testable functions with test cases.
- Keep prompts lean — free tier has per-minute token limits.

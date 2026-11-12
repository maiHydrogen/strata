# STRATA web

The STRATA brief chat as a web app: Next.js for the page, one Python serverless
function (`api/brief.py`) for the chatbot. Styled after the "Web App UI Design"
Figma community template (lilac field, navy glass panels, cyan accents), with
Unbounded for headings and Comfortaa for body text.

## Run locally

```bash
cd web
npm install
npm run dev:api     # Python API on http://127.0.0.1:8001, reads GEMINI_API_KEY from ../.env
npm run dev         # in a second terminal: http://localhost:3000
```

`next dev` forwards `/api/*` to the Python API (see `next.config.mjs`).

## Deploy on Vercel

1. Import the GitHub repository in Vercel and set **Root Directory** to `web`.
   The framework preset is detected as Next.js.
2. In **Settings, Environment Variables** add:
   - `GEMINI_API_KEY`: your Google AI Studio key (never commit it)
   - `STRATA_ACCESS_CODE` (optional but recommended): any passphrase. Visitors are then
     asked for it before the chat calls Gemini, so strangers cannot use up the free tier.
   - `STRATA_LLM_MODEL` (optional): defaults to `gemma-4-26b-a4b-it`.
3. Deploy. `web/api/brief.py` becomes a Python function using `web/requirements.txt`
   (pydantic, google-genai, httpx only).

## How the API works

The function is stateless: the browser keeps the conversation state and sends it with
every message (`BriefSession.to_state` / `from_state`). The chatbot code itself lives in
`../src/brief`; `api/_lib/src/brief` is a committed copy so Vercel can bundle it.
`npm run dev` and `npm run build` refresh the copy, and `tests/test_web_sync.py` fails
if it is out of date.

Request: `POST /api/brief` with `{"state": ..., "message": "..."}` (both omitted to start).
Response: `reply`, `state`, `panel` (the live brief card), `summarised`, `done`, and the
confirmed `brief` JSON at the end.

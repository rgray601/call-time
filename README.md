# Call Time

A rehearsal room for pageant contestants 18 and older. Three rooms:

- **On-stage** times an answer and scores it. An interview coach drafts. A director is the only voice released.
- **News desk** turns live BBC and NPR headlines into judge questions, then grades the answer against those headlines.
- **Runway** measures a 12-second walk in the browser. The video never leaves the laptop. A coach may only talk about the numbers, and a checker replaces the note if it doesn't.

Every coach uses `gpt-6-luna` only. No other model is wired up.

## Key

The app looks for `OPENAI_API_KEY` in two places, in this order:

1. `.env` in this folder (the repo).
2. `.env` in the parent folder. That is where the teaching staff keeps the class key.

If `OPENAI_API_KEY` is missing and `PORTKEY_API_KEY` is set, it uses Portkey instead. Copy `.env.example` to `.env` only if you want the key inside the repo. Do not commit a real key.

```
OPENAI_API_KEY=your_key_here
```

## Run

Python 3.11 or newer.

Windows, from this folder:

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m app
```

macOS or Linux:

```
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m app
```

Open http://127.0.0.1:8000

Confirm you are 18 or older. If port 8000 is taken: `python -m app --port 8001`

The install is the slow part. The app itself starts in a few seconds. A full rehearsal is one or two model calls and usually finishes in under a minute.

## A rehearsal the staff can run without writing an answer

1. On-stage → **Load the sample** → **Ask the coach**. The sample clock is already 31 seconds on a 25-second question.
2. News desk → **Write judge questions**. When the questions appear, **Fill a vague sample**, then **Grade the answers**. The vague answer is supposed to get pushed.
3. Runway → **Use the sample walk** → **Ask the runway coach**. No camera required. The camera path is optional and keeps the video on the machine.
4. Backstage shows the tool calls, the audit, and the token receipt. The safety buttons there run the injection filter, the diet refusal, the budget lock, and the walk auditor.

Checks that do not spend a token:

```
.venv\Scripts\python -m app.selftest
```

## What the bill is

A receipt sits under every note: input tokens, cached input, output tokens, and dollars at the course gpt-6-luna rates ($0.10 / $0.50 per million, cache reads at $0.01). A session stops at $0.75. The whole app stops at $5.00 for the UTC day, and at 12 rehearsals an hour. Drawing a question, loading headlines, and previewing a walk do not call the model.

## News, if the network is down

Headlines come from three allowlisted feeds (BBC World, BBC US & Canada, NPR). They are cached for 30 minutes. If those feeds fail, the app uses `data/fallback_headlines.json` and labels it a practice set. If that file is empty too, the news desk refuses to invent headlines.

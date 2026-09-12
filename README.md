# realtime-dataqc

Realtime data-quality checks on drilling rig telemetry. One agent per well
reads the rig's latest reading once a second, runs it through a set of checks,
and writes out whatever is wrong. The checks live in editable JSON files, and a
small HTTP API is what wells are added to and what those files are edited
through — nothing needs a restart.

## Running it

```bash
uv sync
cp .env.example .env      # fill in DB_USERNAME / DB_PASSWORD
uv run python main.py
```

That starts both halves in one process: the config API in the foreground and
the agent manager on a background thread.

- **<http://localhost:8000>** — the dashboard
- **<http://localhost:8000/docs>** — the same endpoints as forms

No well is monitored until you add one. Use **Settings** on the dashboard, or:

```bash
curl -X POST http://localhost:8000/wells \
  -H 'Content-Type: application/json' \
  -d '{"database_name": "kj-16", "ip_address": "10.0.0.5"}'
```

The manager notices within five seconds and starts an agent. `DELETE
/wells/kj-16` stops it. The registry is in memory only — wells are gone on
restart and have to be added again.

## Layout

| File | What it is |
|---|---|
| `main.py` | Entry point. Starts the API and the agent manager together. |
| `config.py` | Every setting, read from `.env`. Paths resolve next to the app, so a PyInstaller `.exe` works the same. |
| `logger.py` | Console + hourly-rotating files under `logs/`, kept for `LOG_RETENTION_HOURS`. |
| `well_registry.py` | Which wells are monitored. Shared between the API and manager threads, so every access is locked. |
| `agent_manager.py` | Starts an agent for each well in the registry, stops the ones removed. |
| `well_agent.py` | One well's loop: read, skip if unchanged, validate, append alerts. |
| `mysql_client.py` | That well's connection. Read-only, reconnects if the link drops. |
| `column_mapper.py` | Turns this rig's column names into logical names. The only place that knows either. |
| `validation_realtime.py` | The checks themselves, and the state the change checks need. |
| `rule_files.py` | Reads and writes `data/*.json`, with the validation that keeps a bad edit off disk. |
| `config_api.py` | The HTTP surface over the registry and the rule files, and what serves the dashboard. |
| `frontend/` | The dashboard — plain HTML, CSS and JS, no build step and no CDN. |

Directories: `data/` holds the rule files (in git), `logs/` and `output/` are
runtime only (ignored).

## The dashboard

One card per well, in a grid sized so six wells land on one screen without
scrolling. Each card carries that well's own alerts, one per line:

```
17:40:57  TotalSPM dropped by 100.00% Where BD-93.16
```

The status dot and the card's border take the colour of the worst thing on it,
and each alert line is edged in its own, so a wall of cards reads at a glance.
The well's IP address is on the card title rather than the card, to keep the
header to one line — hover the name to see it.

Alerts repeat once a second for as long as a problem stands, so the card
groups them: identical messages collapse to one line with a `×N` count and the
reading that tripped it last. A well with 1,800 alerts on file usually shows
four or five lines.

**Settings** opens the add-well form. Removing a well is the `×` on its card,
which asks once before it stops the agent.

### Resizing a card

Drag a card's right edge, bottom edge or bottom-right corner. Height is free —
the alert list takes whatever it is dragged to, between 90px and 900px. Width
steps in whole grid columns (up to four), because a card that is some fraction
of a column wide either overflows its neighbour or leaves a ragged gap, and
stepping keeps the wall of cards lined up.

Double-click any handle to put that card back to the default size. Sizes are
per well and kept in the browser's local storage, so a refresh does not undo
the arranging. Widen a card on a large screen and open the page on a smaller
one and the span is re-clamped to the columns that screen actually has.

It is plain HTML, CSS and JS — no build step, no framework, no web fonts and no
icon CDN, because this is served off a rig server that may have no route out.
Colour is reserved entirely for status — green clear, amber warning, red
critical — and nothing else on the page is coloured, which is what makes a red
card findable in a wall of them.

`config_api.py` serves it at `/`, so the page and the endpoints share an
origin. **Do not open it with `python -m http.server`** from inside
`frontend/` — that server has no `/wells`, so every call 404s and `POST` comes
back 501. If you do want a separate dev server for live reload, it works: the
page notices it is not on the API's port and calls `localhost:8000` instead,
and the API allows the cross-origin request. Change `API_PORT` at the top of
`app.js` if you change `CONFIG_API_PORT`.

## Rules belong to a well

Every value a well is checked against — its ranges, its activity flags, its
change thresholds and what its rig calls each column — is entered in the
dashboard when the well is added, and applies to **that well only**. Two wells
on the same server can have quite different limits and quite different column
names, which is the point: they are different rigs.

A well's record lives in `data/wells/<name>.json`:

```json
{
    "database_name": "kj-16",
    "ip_address": "10.0.0.5",
    "rules": { "activity": {…}, "column_mapping": {…}, "conditions": {…}, "ranges": {…} }
}
```

Wells are saved as they are added, so monitoring **resumes by itself after a
restart** rather than every well having to be entered again.

### data/*.json is the template

The four files are still there and still mean what they meant, but they are
now what a new well's form *opens with* rather than the rules anything runs
on. Editing them changes what the next well starts from; it does not touch a
well already being monitored. `GET /rules/template` is what the form reads,
and the `/rules/*` endpoints still edit those files.

That is what makes a 117-field form workable: it arrives filled in, and most
wells need a handful of changes.

### The four blocks

Asked for in the order the data folder lists them.

- **`activity`** — per activity (`DRILLING`, `RIH`), ticked means the value
  must not be 0 during it.
- **`column_mapping`** — what this rig calls each parameter, as a
  comma-separated list. Matched case-insensitively, first match wins. This is
  the block that most often differs between rigs.
- **`conditions`** — how far a value must move, over how long. `TA_TG` and
  `HOOKLOAD` take a duration only; `SPP`, `SPM` and `ROP` need a percentage
  too. All five are required.
- **`ranges`** — `min`/`max` per parameter, with an optional `unit` for the
  alert text and an optional `factor`. **`factor` multiplies the stored
  reading before it is compared**, so limits can be written in the unit you
  think in.

### Checked before anything is saved

The four blocks are checked against each other, not against the files: a range
or an activity flag naming a parameter this well's mapping does not have is
refused, with a sentence naming the block and the parameter. Nothing is
written until it all agrees, so a half-valid rule set never reaches a running
agent.

Editing a well later — the pencil on its card — writes the same file. The
agent notices within a second and carries on with the new thresholds, keeping
the baselines its change checks are part-way through measuring.

## The checks

Seven run on every reading, in order:

1. **activity** — `DRILLING` if hole depth minus bit depth is at or below
   `DRILLING_CRITERIA`, else `RIH`. Then everything `activity.json` marks `1`
   must be above zero.
2. **ranges** — each parameter inside its `min`/`max`, after `factor`.
3. **TA > TG** — alert once TA has been above TG for the whole duration.
4. **SPP** — percentage move over the window, either direction.
5. **SPM** — the same, on total strokes per minute.
6. **ROP** — the same, but **increases only**: ROP dropping to zero is normal
   every time the bit comes off bottom.
7. **HOOKLOAD** — alert when the value has not moved *at all* for the
   duration, which means the feed has stalled rather than the rig being still.

## Alerts

Alerts are appended to `output/<database_name>/alerts.json`. Nothing is written
back to the rig's database — the connection is read-only.

The log is separate and deliberately quieter: the `qc.alerts` logger writes a
full block when the set of alerts *changes*, then a single line every 60s while
the same ones stay up. Every reading is still recorded in full at `DEBUG`.

## Configuration

All of it optional except the credentials — see `.env.example` for the full
list with defaults. `.env` is gitignored; it holds live credentials and must
never be committed.

# Real-Time Data QC

Checks live drilling data from each rig and shows the problems on a dashboard.

For every well, an agent reads the latest row from the rig's MySQL table once a
second, runs the checks, and saves any alerts. Nothing is written back to the
rig's database.

## Run it

```bash
uv sync
cp .env.example .env      # put in DB_USERNAME and DB_PASSWORD
uv run python main.py
```

Open **http://localhost:8000** for the dashboard (**/docs** for the API).

The rig databases only answer inside the company network.

## Using the dashboard

| To… | Do this |
|---|---|
| Add a well | **Settings → Add a well**, enter database name + IP, change any rules, **Start monitoring** |
| Edit a well's rules | ✎ pencil on its card, **Save rules** |
| Change what counts as drilling for one well | Its ✎ pencil → **Off-bottom margin**, **Save rules** |
| Stop a well | × on its card, then **Stop?** within 3 seconds |
| Rename parameters in alerts | **Settings → Display names**, **Save display names** |
| Resize a card | Drag its right edge, bottom edge or corner. Double-click to reset |

- Each card shows the well's activity next to its name: `(DRILLING)` or `(NON DRILLING)`.
- Alerts are listed newest first and leave the card after **30 minutes**.
- Card colour: green = clear, amber = warning, red = critical.
- Wells are saved, so they come back by themselves after a restart.
- A well name that is already monitored can't be added again. Use its pencil.
- **Stopping a well deletes its rules** (`data/wells/<well>.json`).

## Email digest

Every 10 minutes the alerts raised in that time are emailed to the people
responsible for each base region. A window with nothing in it sends nothing.

All of it lives in `mailer/`, except the settings, which are in `config.py`
with everything else configurable:

| | |
|---|---|
| `config.py` | `.env` settings **and** `data/email_config.json` — who each region emails |
| `mailer/digest.py` | which alerts, grouped how, worded how |
| `mailer/sender.py` | SMTP, and nothing else |
| `mailer/agent.py` | `EmailAgent` — the thread that decides when |

Not named `email/`. Python's own standard library has a package of that name,
and a folder called `email/` beside `main.py` is found first — which breaks
`smtplib`, whose first line is `import email.utils`.

`data/email_config.json` is the region, and under it whoever should be told
about that base:

```json
{
  "mumbai": {
    "base_head":        "someone@ofiindia.com",
    "operational_head": "someone.else@ofiindia.com"
  },
  "pune": { "base_head": "another@ofiindia.com" }
}
```

The roles are not fixed — add `"drilling_engineer"` to a region and they are
emailed too, with nothing in any file to change. A role left blank is someone
not appointed yet and is simply not written to; a region where nobody has an
address is refused. One mailbox holding two roles is addressed once, not sent
the same email twice.

Three things to set up, in this order:

1. **.env** — `EMAIL_ENABLED=true`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USE_TLS`
   or `SMTP_USE_SSL`, `SMTP_FROM`, and unless the relay is open,
   `SMTP_USERNAME` / `SMTP_PASSWORD`. Restart. Until this is done the tab
   below says so and nothing is sent.
2. **Settings → Email recipients** — one row per base region, each with its
   base coordinator and operational head. **Send test** proves the settings
   before waiting ten minutes to find out the password is wrong.
3. **Base region** on each well, beside its IP address. It is the name typed
   here that is looked up in the list above, so the box offers the regions
   already saved — a well whose region is spelt differently reaches nobody.

A well with no region is monitored exactly as before and appears in no email.
The log says which wells are in that state, and which regions have wells but
no entry.

| Setting | Default | |
|---|---|---|
| `EMAIL_ENABLED` | false | Nothing is sent unless this is on |
| `EMAIL_INTERVAL_SECONDS` | 600 | Seconds between passes |
| `SMTP_USE_TLS` | true | STARTTLS, for port 587 |
| `SMTP_USE_SSL` | false | TLS from the first byte, for port 465 |

### How the window works

Each region's window runs from **its own last successful send** up to now, not
from "now minus ten minutes". Two things follow:

- A pass that is late, or missed because the service was restarting, leaves no
  gap — the next window just starts where the last one ended and is longer.
- A send that fails does not move that region's watermark, so the window stays
  owed and grows until one gets through. A mail server being down delays these
  alerts; it never drops them.

Per region rather than one watermark for everything, so one region's mail
server refusing does not hold back the others. Where each has got to is in
`output/email_state.json`, so a restart resumes instead of skipping whatever
was raised while it was down. After a long outage the window is clamped to
`ALERT_RETENTION_HOURS`, since older alerts have already been swept out.

### What is in one

Alerts are grouped the way a card groups them, by the sentence with its
numbers taken out. A problem standing for the whole window is written to the
file once a second, so ten minutes of one stuck hookload is ~600 lines; sent
as-is it would bury everything else. The count is what carries that:

```
DK-1233
-------
  Please check for data Trans. Hookload has remained unchanged for 30 seconds
      14:20:03 to 14:29:58   raised 612x
  ROP increased by 80.00%, BD:2499.9406m
      14:24:11   raised 1x
```

## Activity

```
hole depth − bit depth ≤ off-bottom margin  →  DRILLING
otherwise                                   →  NON DRILLING
```

The margin belongs to the well, not to the app: it is the **Off-bottom margin**
box at the top of the well's form, and each well can have a different one. A rig
whose depth channels agree to the centimetre can run at 0.01 m; one that reads
half a metre apart while still on bottom needs 0.5, or every reading would be
called NON DRILLING and checked against the wrong activity rules.

A new well's form opens at 0.1 m. A well added before this setting existed keeps
being read at 0.1 m until a different figure is saved for it.

## Checks

1. **Activity**: parameters ticked for the current activity must not be 0.
2. **Ranges**: each value must be between its min and max.
3. **TA > TG**: TA has stayed above TG for the set time.
4. **SPP change**: SPP moved more than the set % (up or down).
5. **ROP change**: ROP went up more than the set %.
6. **HOOKLOAD stuck**: hookload hasn't changed at all for the set time (feed may be frozen).

The SPM % change check is currently switched off in the code.

## Logs

Two files in `logs/`, both rotating hourly and keeping 24 hours:

| File | What is in it |
|---|---|
| `app.log` | Everything - connections, reloads, errors, and the alerts |
| `alerts.log` | Only what the wells raised |

Every line is filed under the well it is about, so one well's whole story is
`grep KJ-16 logs/app.log`:

```
qc.alerts.KJ-16      that well's alerts
validation.KJ-16     that well's checks
well_agent.KJ-16     its loop - connecting, reading, retrying
```

When a well's alerts change, the block written names the values behind each
one and the rule it broke, so an alert can be settled without opening the
well's rules or going back to the rig's table:

```
Well         : MNDWO181HDB_1(M
Activity     : NON DRILLING  (off-bottom margin 0.01)
Depth        : 1430.28
...
Alerts       : 2
  1. PitSumVol1 cannot be 0 in NON DRILLING where BD:1430.28m, MD:1623.0m
       why : activity[NON DRILLING] requires PitSumVol1 above 0; read 0.0 from column PitSumVol1
  2. LEL : 10.36% above limit 1%  BD : 1430.28m
       why : ranges[LEL] is 0 to 1%; read 10.36 from column LEL, 10.36 x 1 = 10.36%, which is above the max of 1%
Readings     : DEPTH=1623.0m  SPP=0.0psi  HOOKLOAD=106.39kflb  WOB=0.0kflb  ...
```

While the same alerts stay up the block is not repeated - one line every
minute says they are still there. A rig that cannot be reached is one warning,
not a stack trace every few seconds, and a line saying how many passes it was
down for when it comes back.

### Is it working, or is it stuck?

A quiet log used to mean either. Every well now writes one line a minute
whatever is happening, so silence means the process is gone and nothing else:

```
alive | reading | DRILLING | 1823 read(s), 1450 check(s) | 0 alert(s) standing | last new row 1s ago
alive | cannot reach 43.241.39.241, retrying every 5s | ... | 62 failed pass(es), OperationalError
alive | connecting | CONNECTING | 0 read(s), 0 check(s) | 0 alert(s) standing
```

Two heartbeats side by side settle it: **read(s)** going up means the loop is
turning, **check(s)** going up means the rig is sending new values.

The state that used to be invisible is a rig that answers with the same row
over and over. The query works, so nothing failed - but the row never changes,
and checks only run on a row that has moved, so the well went completely quiet
while looking healthy. That is now a **warning**:

```
alive | STALE FEED - the rig is answering but timebaselastrecord has not
changed for 312s, so nothing has been checked in that time | ...
```

And if an agent's thread dies, the manager says so and restarts it on its next
pass, rather than leaving the well unchecked in silence.

## Alerts

- Saved to `output/<well>/alerts.json` and kept for **24 hours**.
- An alert is only saved when something is new. The same message where only the
  bit depth changed is not saved again. A new value (ROP 12% → 30%) is saved.
- If a problem clears and comes back later, it's alerted again.

## Where the settings live

| File | What it holds | Applies to |
|---|---|---|
| `data/wells/<well>.json` | That well's IP and rules | That well only |
| `data/activity.json`, `column_mapping.json`, `conditions.json`, `ranges.json` | Default rules a **new** well starts with | New wells |
| `data/display_name.json` | Names used in alerts, e.g. `"WOB": "Weight on bit"` | All wells |
| `.env` | Database login, timings, ports (see `.env.example`) | The whole app |

The rules for each well are:

- **drilling_criteria**: the off-bottom margin above, in metres.
- **activity**: which parameters must not be 0 while `DRILLING` / `NON DRILLING`.
- **column_mapping**: what the rig calls each parameter. The first column that exists is used.
- **conditions**: how much a value must change, and over how many seconds.
- **ranges**: min, max, unit, and an optional factor that converts the reading
  first. It multiplies (HOOKLOAD 2.268 daN → klbf), except for **ROP**, where it
  divides, because the column is minutes per metre and the limits are m/hr:
  `60 / 0.5 = 120 m/hr`. Blank compares the column exactly as stored.

Changes take effect within a second. No restart is needed.

**Display names:** only parameters listed in `display_name.json` appear in the
Display names tab. To add one, add it to the file and reopen Settings.

## `.env` settings

Every timing and limit the app runs on is here and nowhere else. Change one,
restart, and that is the whole procedure - including the dashboard's, which it
reads from this file through `GET /settings` rather than from `app.js`.

Each line is optional; leave it out and the default applies.

### Rig databases

| Setting | Default | Meaning |
|---|---|---|
| `DB_USERNAME`, `DB_PASSWORD`, `DB_PORT` | – , – , 3306 | Login for the rig databases |
| `TABLE_NAME` | timebaselastrecord | Table with the latest reading |

### How often each well is read

| Setting | Default | Meaning |
|---|---|---|
| `CHECK_INTERVAL` | 1 | Seconds between readings |
| `RETRY_INTERVAL` | 5 | Seconds before reconnecting after an error |
| `AGENT_POLL_INTERVAL` | 5 | Seconds before a newly added well starts, and before a dead agent is restarted |

### Knowing the agents are alive

| Setting | Default | Meaning |
|---|---|---|
| `HEARTBEAT_SECONDS` | 60 | Seconds between each well's `alive` line |
| `STALE_ROW_SECONDS` | 120 | Unchanged rows for this long is a `STALE FEED` warning |

### Drilling

| Setting | Default | Meaning |
|---|---|---|
| `DEFAULT_DRILLING_CRITERIA` | 0.1 | Metres off bottom a **new** well's form opens with. Each well then keeps its own |

### Alerts

| Setting | Default | Meaning |
|---|---|---|
| `ALERT_RETENTION_HOURS` | 24 | Hours an alert stays in `output/<well>/alerts.json` |
| `ALERT_PRUNE_INTERVAL` | 60 | Seconds between sweeps for alerts past that |
| `ALERT_PAGE_DEFAULT` | 200 | Alerts `GET /alerts/{well}` returns when not asked for a number |
| `ALERT_PAGE_MAX` | 5000 | The most it will return, however many are asked for |

### Logs

| Setting | Default | Meaning |
|---|---|---|
| `LOG_LEVEL` | INFO | What reaches the console. The files always take DEBUG |
| `LOG_RETENTION_HOURS` | 24 | How many rolled log files are kept |
| `LOG_ROTATE_WHEN` | H | When they roll: `H` hourly, `D` daily, `M` per minute |
| `LOG_ROTATE_INTERVAL` | 1 | How many of those between rolls |
| `LOG_REPEAT_SECONDS` | 60 | Seconds before unchanged alerts are written out again |

### The dashboard and its API

| Setting | Default | Meaning |
|---|---|---|
| `CONFIG_API_HOST`, `CONFIG_API_PORT` | 0.0.0.0 , 8000 | Where the dashboard and API are served |
| `DASHBOARD_POLL_SECONDS` | 5 | Seconds between the page's polls |
| `DASHBOARD_STARTING_SECONDS` | 8 | How long a new well says "starting" rather than "all clear" |
| `DASHBOARD_ALERT_MAX_AGE_MINUTES` | 30 | Minutes an alert stays on its card. It stays in the file until `ALERT_RETENTION_HOURS` either way |
| `DASHBOARD_ALERT_LIMIT` | 1000 | Most alerts one card will hold |
| `DASHBOARD_CARD_MIN_HEIGHT`, `DASHBOARD_CARD_MAX_HEIGHT` | 90 , 900 | Pixels a card's alert list can be dragged between |
| `DASHBOARD_CARD_MAX_COLUMNS` | 4 | Grid columns a card can be widened to |

The page falls back to its own built-in defaults if `GET /settings` fails, so a
server that is briefly down does not leave it blank.

Never commit `.env`. It holds the real password.

## Code files

| File | Job |
|---|---|
| `main.py` | Starts the dashboard/API and the agents |
| `agent_manager.py` | Starts and stops one agent per well |
| `well_agent.py` | One well's loop: read, check, save alerts |
| `mysql_client.py` | Connection to a rig database |
| `column_mapper.py` | Turns rig column names into parameter names |
| `validation_realtime.py` | The checks and the alert messages |
| `alert_log.py` | What the log says about each alert, and why |
| `rule_files.py`, `well_rules.py` | Load, check and save the JSON settings |
| `well_registry.py` | The list of monitored wells |
| `config_api.py` | The API and the dashboard server |
| `config.py`, `logger.py` | Settings from `.env`, and logging to `logs/` |

Two pages in `frontend/`, each with its own HTML, CSS and JavaScript:

| File | Job |
|---|---|
| `index.html`, `style.css`, `app.js` | The dashboard - the well cards and the settings form |
| `logs.html`, `logs.css`, `logs.js` | One well's whole alert history, opened by **Show Logs** |
| `shared.js` | What both pages need: where the API is, and how to read one alert |

The log page stands on its own: it takes the well from its own query string
(`/logs.html?well=<database_name>`), fetches its own alerts and polls for more,
so it can be reloaded, bookmarked, or left open on a second screen.

Neither page has a port or a hostname written into it. They are served by
`config_api.py` alongside the endpoints they call, so a relative path already
points at the right place - change `CONFIG_API_PORT` in `.env` and both pages
follow it, as do a reverse proxy, https and any hostname.

Working on the page from somewhere else - `python -m http.server 5500` inside
`frontend/`, VS Code Live Server, or opening the file directly - is the one
case where that is not true. Such a server hands back `index.html` and then
answers `GET /wells` with its own 404, so the page checks at load which kind of
origin it came from: it asks for `/health`, and falls back to `DEV_API_PORT` in
`shared.js` (8000) only when something answers that is not the API. A
deployment on any port is found by that check and never reads the constant.

To point the page at a rig on another machine, name the API on the URL. It is
read per load and never stored:

```
http://127.0.0.1:5500/index.html?api=http://10.0.0.5:8000
```


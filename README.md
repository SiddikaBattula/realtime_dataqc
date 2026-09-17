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
| Stop a well | × on its card, then **Stop?** within 3 seconds |
| Rename parameters in alerts | **Settings → Display names**, **Save display names** |
| Resize a card | Drag its right edge, bottom edge or corner. Double-click to reset |

- Each card shows the well's activity next to its name: `(DRILLING)` or `(NON DRILLING)`.
- Alerts are listed newest first and leave the card after **30 minutes**.
- Card colour: green = clear, amber = warning, red = critical.
- Wells are saved, so they come back by themselves after a restart.
- A well name that is already monitored can't be added again. Use its pencil.
- **Stopping a well deletes its rules** (`data/wells/<well>.json`).

## Activity

```
hole depth − bit depth ≤ DRILLING_CRITERIA (0.1 m)  →  DRILLING
otherwise                                           →  NON DRILLING
```

## Checks

1. **Activity**: parameters ticked for the current activity must not be 0.
2. **Ranges**: each value must be between its min and max.
3. **TA > TG**: TA has stayed above TG for the set time.
4. **SPP change**: SPP moved more than the set % (up or down).
5. **ROP change**: ROP went up more than the set %.
6. **HOOKLOAD stuck**: hookload hasn't changed at all for the set time (feed may be frozen).

The SPM % change check is currently switched off in the code.

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

- **activity**: which parameters must not be 0 while `DRILLING` / `NON DRILLING`.
- **column_mapping**: what the rig calls each parameter. The first column that exists is used.
- **conditions**: how much a value must change, and over how many seconds.
- **ranges**: min, max, unit, and an optional factor that multiplies the reading first.

Changes take effect within a second. No restart is needed.

**Display names:** only parameters listed in `display_name.json` appear in the
Display names tab. To add one, add it to the file and reopen Settings.

## Main `.env` settings

| Setting | Default | Meaning |
|---|---|---|
| `DB_USERNAME`, `DB_PASSWORD`, `DB_PORT` | – , – , 3306 | Login for the rig databases |
| `TABLE_NAME` | timebaselastrecord | Table with the latest reading |
| `CHECK_INTERVAL` | 1 | Seconds between readings |
| `DRILLING_CRITERIA` | 0.1 | Metres off bottom that still counts as drilling |
| `ALERT_RETENTION_HOURS` | 24 | How long alerts stay in `output/` |
| `LOG_RETENTION_HOURS` | 24 | How long log files stay in `logs/` |
| `CONFIG_API_PORT` | 8000 | Dashboard port (also change `API_PORT` in `frontend/app.js`) |

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
| `rule_files.py`, `well_rules.py` | Load, check and save the JSON settings |
| `well_registry.py` | The list of monitored wells |
| `config_api.py` | The API and the dashboard server |
| `config.py`, `logger.py` | Settings from `.env`, and logging to `logs/` |
| `frontend/` | The dashboard (HTML, CSS, JS) |

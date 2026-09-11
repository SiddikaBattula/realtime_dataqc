"""
The rule files, editable over HTTP instead of by hand.

Open http://localhost:8002/docs and every endpoint below is a form: fill the
fields in, press Execute, and the change is checked, backed up and written.
The agent notices the file changed and picks it up within a second - nothing
has to be restarted.

Two levels of endpoint, because they answer different questions:

    one value      PUT /rules/ranges/H2S           <- the everyday change
                   PUT /rules/activity/RIH/WOB
                   PUT /rules/conditions/SPP

    whole file     GET/PUT /rules/{name}           <- bulk edits, or a look at
                                                      the file as it stands

Run it with:  python config_api.py
"""

from contextlib import asynccontextmanager
from typing import Optional
from enum import Enum

from fastapi import Body, FastAPI, HTTPException, Path as PathParam, Request
from pydantic import BaseModel, Field
import uvicorn
import time

from config import Config
from logger import setup_logging, get_logger

setup_logging()

log = get_logger("config-api")

import rule_files
from rule_files import RuleFileError
from well_registry import ACTIVE_WELLS

class WellRequest(BaseModel):
    database_name: str
    ip_address: str

class RuleFile(str, Enum):
    """The four files, as a dropdown in the docs page."""

    activity = "activity"
    conditions = "conditions"
    ranges = "ranges"
    column_mapping = "column_mapping"


# ---------------------------------------------------------------------------
# What a single change looks like
# ---------------------------------------------------------------------------

class RangeLimits(BaseModel):
    """The allowed range for one parameter."""

    min: float = Field(..., description="Below this, an alert is raised", examples=[0])
    max: float = Field(..., description="Above this, an alert is raised", examples=[50])
    unit: Optional[str] = Field(None, description="Shown in the alert text", examples=["ppm"])
    factor: Optional[float] = Field(
        None,
        description="Converts the stored value before comparing - ROP is stored as "
                    "minutes per metre, so 60 turns it into metres per hour",
        examples=[None],
    )


class ActivityFlag(BaseModel):
    """Whether a parameter may sit at zero during an activity."""

    required: bool = Field(
        ...,
        description="true: alert when this parameter is 0 during the activity (1 in "
                    "the file). false: ignore it (0 in the file)",
        examples=[True],
    )


class ConditionBlock(str, Enum):
    """The four blocks the agent reads out of conditions.json."""

    TA_TG = "TA_TG"
    SPP = "SPP"
    SPM = "SPM"
    ROP = "ROP"


class ChangeCondition(BaseModel):
    """How much a value has to move, over how long, before it counts."""

    duration_seconds: float = Field(
        ...,
        ge=0,
        description="How long to wait before comparing against the earlier reading. "
                    "For TA_TG, how long TA may stay above TG before it is an alert.",
        examples=[5],
    )
    # No "greater than 0" rule here on purpose: TA_TG has no percentage at all,
    # and the docs page fills this in with 0 whether you want it or not. The
    # handler below ignores it for TA_TG and insists on it for the other three,
    # which gives a sentence explaining the problem instead of a schema error.
    percentage_change: Optional[float] = Field(
        None,
        description="How big the move has to be, in percent. Ignored for TA_TG, "
                    "which only has a duration - leave it at 0 or empty there.",
        examples=[1],
    )


# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Editing the rule files in %s", Config.DATA_DIR)

    for name in rule_files.FILES:
        try:
            rule_files.load(name)
        except RuleFileError as exc:
            log.error("%s cannot be read: %s", name, exc)

    # log.info(
    #     "Config API ready on http://%s:%s/docs",
    #     Config.CONFIG_API_HOST,
    #     Config.CONFIG_API_PORT,
    # )

    yield

    log.info("Config API stopped")


from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="DataQC rule files",
    lifespan=lifespan,
)

# Enable CORS for cross-origin frontend requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows requests from any origin/port
    allow_credentials=True,
    allow_methods=["*"],  # Allows GET, POST, DELETE, OPTIONS, etc.
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    started = time.perf_counter()

    response = await call_next(request)

    elapsed = (time.perf_counter() - started) * 1000

    emit = log.info if response.status_code < 400 else log.warning
    emit("%s %s  %s  %.1fms", request.method, request.url.path,
         response.status_code, elapsed)

    return response


def _saved(name, document, changed):
    return {
        "status": "saved",
        "file": f"{name}.json",
        "changed": changed,
        "note": "The agent reloads it on its next reading, within about a second.",
        "document": document,
    }


def _guard(action):
    """Turn a rule-file complaint into a 400 that says what to fix."""
    try:
        return action()

    except RuleFileError as exc:
        log.warning("Rejected: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))



# ---------------------------------------------------------------------------
# add wells and delete
# ---------------------------------------------------------------------------

@app.post("/wells")
def add_well(well: WellRequest):

    ACTIVE_WELLS[well.database_name] = {
        "database_name": well.database_name,
        "ip_address": well.ip_address
    }

    return {
        "message": f"{well.database_name} added",
        "status": "success"
    }


@app.get("/wells")
def get_wells():

    return {
        "count": len(ACTIVE_WELLS),
        "wells": ACTIVE_WELLS
    }


@app.delete("/wells/{database_name}")
def delete_well(database_name: str):

    if database_name in ACTIVE_WELLS:

        del ACTIVE_WELLS[database_name]

        return {
            "message": f"{database_name} removed"
        }

    return {
        "message": "Well not found"
    }




# ---------------------------------------------------------------------------
# One value at a time - the everyday edits
# ---------------------------------------------------------------------------

@app.put("/rules/ranges/{parameter}", summary="Set the allowed range for one parameter")
def set_range(
    limits: RangeLimits,
    parameter: str = PathParam(..., description="Logical name, e.g. H2S", examples=["H2S"]),
):
    """
    Change what counts as in range for one parameter, e.g. H2S 0-50 ppm.

    The parameter has to exist in column_mapping.json; everything else in
    ranges.json is left alone.
    """
    def write():
        document = rule_files.load("ranges")

        entry = {"min": limits.min, "max": limits.max}

        if limits.unit is not None:
            entry["unit"] = limits.unit

        if limits.factor is not None:
            entry["factor"] = limits.factor

        document[parameter] = entry

        saved = rule_files.save("ranges", document)

        # Echoed from the saved file, not from the form: whole numbers are
        # written as 50 rather than 50.0, and the reply should say so.
        return saved, saved[parameter]

    document, entry = _guard(write)

    return _saved("ranges", document, {parameter: entry})


@app.put(
    "/rules/activity/{activity}/{parameter}",
    summary="Say whether a parameter may be zero during an activity",
)
def set_activity_flag(
    flag: ActivityFlag,
    activity: str = PathParam(..., description="e.g. DRILLING or RIH", examples=["RIH"]),
    parameter: str = PathParam(..., description="Logical name, e.g. WOB", examples=["WOB"]),
):
    """
    Turn one check on or off - "WOB must not be 0 while drilling".

    A new activity name creates that block; the parameter has to exist in
    column_mapping.json.
    """
    def write():
        document = rule_files.load("activity")

        block = dict(document.get(activity, {}))
        block[parameter] = 1 if flag.required else 0
        document[activity] = block

        return rule_files.save("activity", document), block[parameter]

    document, value = _guard(write)

    return _saved("activity", document, {activity: {parameter: value}})


@app.put("/rules/conditions/{block}", summary="Set a change threshold")
def set_condition(
    condition: ChangeCondition,
    block: ConditionBlock = PathParam(
        ..., description="Which check to change", examples=["SPP"]
    ),
):
    """
    Change how far SPP, SPM or ROP has to move before it is an alert, or how
    long TA may sit above TG.

    **TA_TG takes a duration only** - it is not a percentage check, so whatever
    is in percentage_change is ignored there. The other three need both.
    """
    name = block.value
    ignored = None

    entry = {"duration_seconds": condition.duration_seconds}

    if name == "TA_TG":
        if condition.percentage_change:
            ignored = (
                "TA_TG is not a percentage check, so percentage_change "
                f"({condition.percentage_change}) was not written."
            )

    else:
        if not condition.percentage_change:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{name} needs a percentage_change above 0 - it is how far the "
                    "value has to move before it counts as an alert. Only TA_TG "
                    "goes without one."
                ),
            )

        entry["percentage_change"] = condition.percentage_change

    def write():
        document = rule_files.load("conditions")
        document[name] = entry

        return rule_files.save("conditions", document)

    document = _guard(write)

    saved = _saved("conditions", document, {name: entry})

    if ignored:
        saved["ignored"] = ignored

    return saved


@app.put(
    "/rules/column_mapping/{logical}",
    summary="Set the column names one logical name can appear as",
)
def set_column_names(
    columns: list[str] = Body(
        ...,
        description="Every name this parameter goes by. Only one has to exist in "
                    "the table; the first match wins.",
        examples=[["TOT_DPT_MD", "DEPTH", "TOTAL_DEPTH"]],
    ),
    logical: str = PathParam(..., description="e.g. DEPTH", examples=["DEPTH"]),
):
    """
    Point a logical name at the columns a rig actually uses. This is the file to
    edit when a new rig calls its depth column something new.
    """
    def write():
        document = rule_files.load("column_mapping")
        document[logical] = columns

        return rule_files.save("column_mapping", document), columns

    document, entry = _guard(write)

    return _saved("column_mapping", document, {logical: entry})


# ---------------------------------------------------------------------------
# Whole files
# ---------------------------------------------------------------------------

@app.get("/rules", summary="What the four files are and when each last changed")
def list_rules():
    listed = []

    for name, path in rule_files.FILES.items():
        changed = rule_files.changed_at(name)

        listed.append({
            "name": name,
            "file": path.name,
            "last_changed": time.strftime("%d-%m-%Y %H:%M:%S", time.localtime(changed))
            if changed else None,
            "has_backup": path.with_suffix(".json.bak").exists(),
        })

    return {"directory": str(Config.DATA_DIR), "files": listed}


@app.get("/rules/{name}", summary="Read one file as it stands")
def read_rules(name: RuleFile):
    return _guard(lambda: rule_files.load(name.value))


@app.put("/rules/{name}", summary="Replace one file completely")
def replace_rules(
    name: RuleFile,
    document: dict = Body(
        ...,
        description="The whole file. Anything not included is removed - use PATCH "
                    "to change part of it.",
        examples=[{"DRILLING": {"ROP": 1, "WOB": 1}, "RIH": {"HOOKLOAD": 1}}],
    ),
):
    saved = _guard(lambda: rule_files.save(name.value, document))

    return _saved(name.value, saved, "whole file replaced")



import json
from pathlib import Path
from fastapi import FastAPI, HTTPException

# Endpoint to fetch real alerts generated by WellAgent
@app.get("/alerts/{database_name}", summary="Get active alerts for a well")
def get_well_alerts(database_name: str):
    # Matches: Path("output") / self.database_name / "alerts.json"
    file_path = Path("output") / database_name / "alerts.json"
    
    if not file_path.exists():
        return {"status": "success", "alerts": []}
        
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            alerts = json.load(f)
        return {"status": "success", "alerts": alerts}
    except Exception as e:
        log.error("Failed to read alerts for %s: %s", database_name, e)
        raise HTTPException(status_code=500, detail="Could not read alert log file")

        

@app.get("/", summary="Health check")
def home():
    return {
        "message": "DataQC rule file API running",
        "docs": "/docs",
        "directory": str(Config.DATA_DIR),
    }






# if __name__ == "__main__":
#     uvicorn.run(
#         app,
#         host=Config.CONFIG_API_HOST,
#         port=Config.CONFIG_API_PORT,
#         log_config=None,
#     )

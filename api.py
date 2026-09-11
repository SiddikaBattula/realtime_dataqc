from fastapi import FastAPI
from pydantic import BaseModel

from well_registry import ACTIVE_WELLS

app = FastAPI(title="DataQC Well Manager")


class WellRequest(BaseModel):
    database_name: str
    ip_address: str


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

import logging
import uuid as PyUUID
import os
from typing import Optional
from sqlalchemy.orm import Session
from fastapi import FastAPI, HTTPException, Response, Depends, Path 
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from enum import Enum
from . import config, models
from .database import get_db, create_report_record, get_report_details, update_report_status
from .tasks import generate_report_task
from .ingestion import get_max_timestamp

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI()

MAX_TIMESTAMP_UTC = None

@app.on_event("startup")
async def startup_event():
    global MAX_TIMESTAMP_UTC
    logger.info("API Startup: Attempting to load MAX_TIMESTAMP_UTC...")
    MAX_TIMESTAMP_UTC = get_max_timestamp()
    if MAX_TIMESTAMP_UTC is None:
         logger.error("API Startup Warning: MAX_TIMESTAMP_UTC could not be determined. Ingestion script needs to run successfully first.")
    else:
         logger.info(f"API Startup: Using reference time (MAX_TIMESTAMP_UTC): {MAX_TIMESTAMP_UTC.isoformat()}")

class TriggerResponse(BaseModel):
    report_id: str = Field(..., description="Unique identifier for the generated report.")

class ReportStatusEnumAPI(str, Enum):
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"

class GetStatusResponse(BaseModel):
    status: ReportStatusEnumAPI = Field(..., description="Current status of the report generation.")


@app.post("/trigger_report/{store_id}",
          response_model=TriggerResponse,
          status_code=202,
          summary="Trigger Report Generation for a Specific Store")
async def trigger_report(store_id: PyUUID.UUID = Path(..., title="The UUID of the store to generate a report for")):
    if MAX_TIMESTAMP_UTC is None:
         logger.error("Trigger Report Failed: MAX_TIMESTAMP_UTC is not available.")
         raise HTTPException(status_code=500, detail="Server configuration error: Reference time not set.")

    logger.info(f"Trigger report request received for Store ID: {store_id}")
    report_id = str(PyUUID.uuid4()) 

    try:
        with get_db() as db_session: 
            logger.debug(f"Verifying existence for store {store_id} using db_session object: {type(db_session)}") 

            store = db_session.query(models.Store).filter(models.Store.id == store_id).first()
            if store is None:
                logger.warning(f"Store ID {store_id} not found for trigger request.")
                raise HTTPException(status_code=404, detail=f"Store with ID '{store_id}' not found.")
            logger.info(f"Store {store_id} found, proceeding with report trigger.")

            create_report_record(db_session, report_id=report_id, store_id=store_id, status='PENDING')

            logger.info(f"Dispatching Celery task for report_id: {report_id}, store_id: {store_id} with ref time: {MAX_TIMESTAMP_UTC.isoformat()}")
            generate_report_task.delay(
                report_id=report_id,
                store_id_str=str(store_id),
                reference_time_iso=MAX_TIMESTAMP_UTC.isoformat()
            )

        return TriggerResponse(report_id=report_id)

    except HTTPException:
        raise

    except Exception as e:
        logger.error(f"Failed to trigger report generation process for Report {report_id}, Store {store_id}: {e}")
        try:
            with get_db() as error_db:
                report = get_report_details(error_db, report_id)
                if report:
                     update_report_status(error_db, report_id, status='FAILED')
                else:
                     logger.warning(f"Report record {report_id} not found when trying to mark as FAILED after trigger error.")
        except Exception as db_err:
             logger.error(f"Failed to mark report {report_id} as FAILED after trigger error: {db_err}")

        raise HTTPException(status_code=500, detail=f"Failed to trigger report generation: {e}")


@app.get("/get_report/{report_id}",
         responses={
             200: {"description": "Report CSV file if status is 'complete'.", "content": {"text/csv": {}}},
             202: {"description": "Report is still processing.", "model": GetStatusResponse},
             404: {"description": "Report ID not found."},
             410: {"description": "Report generation failed.", "model": GetStatusResponse},
             500: {"description": "Internal server error."}
         },
         summary="Get Report Status or CSV File")
async def get_report(report_id: str):
    logger.debug(f"Received status/result request for report_id: {report_id}")
    try:
        with get_db() as db_session: 
            report = get_report_details(db_session, report_id) 

            if not report:
                raise HTTPException(status_code=404, detail="Report not found")

            logger.info(f"Report ID: {report_id}, Store: {report.store_id}, Status: {report.status}")

            status = report.status
            file_path = report.report_file_path 

        if status in ['PENDING', 'RUNNING']:
             return JSONResponse(status_code=202, content={"status": ReportStatusEnumAPI.RUNNING})
        elif status == 'FAILED':
             return JSONResponse(status_code=410, content={"status": ReportStatusEnumAPI.FAILED})
        elif status == 'COMPLETE':
            if file_path and os.path.isfile(file_path):
                logger.info(f"Report {report_id} is complete. Serving file: {file_path}")
                custom_headers = {"X-Report-Status": "Complete"}
                return FileResponse(
                    path=file_path,
                    filename=os.path.basename(file_path),
                    media_type='text/csv',
                    headers=custom_headers
                )
            else:
                logger.error(f"Report {report_id} status is COMPLETE, but file is missing or path invalid! Path: {file_path}")
                raise HTTPException(status_code=500, detail="Report generation complete, but the report file is missing or inaccessible.")
        else:
            logger.error(f"Report {report_id} has unknown status: {status}")
            raise HTTPException(status_code=500, detail="Unknown internal report status encountered.")

    except HTTPException:
         raise 
    except Exception as e:
         logger.error(f"Error retrieving report {report_id}: {e}")
         raise HTTPException(status_code=500, detail=f"Error retrieving report details: {e}")
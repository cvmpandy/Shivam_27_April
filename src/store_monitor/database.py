from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from sqlalchemy.exc import SQLAlchemyError
from contextlib import contextmanager
import logging
import datetime
import pytz
import uuid
from typing import Optional
from .config import DATABASE_URL
from . import models


logger = logging.getLogger(__name__)

try:
    engine = create_engine(DATABASE_URL, echo=False)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base = declarative_base()
    logger.info("Database engine and session maker created successfully.")

except SQLAlchemyError as e:
    logger.error(f"Error creating database engine: {e}")
    raise

@contextmanager
def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
        logger.debug("Database transaction committed.")
    except SQLAlchemyError as e:
        logger.error(f"Database transaction failed: {e}")
        db.rollback()
        raise
    finally:
        db.close()
        logger.debug("Database session closed.")

def init_db():
    try:
        logger.info("Initializing database schema...")
        Base.metadata.create_all(bind=engine)
        logger.info("Database schema initialized successfully.")
    except Exception as e:
        logger.error(f"Error initializing database schema: {e}")
        raise

# report related

def create_report_record(db: Session, report_id: str, store_id: uuid.UUID, status: str = 'PENDING'):
    try:
        new_report = models.Report(
            id=report_id,
            store_id=store_id,
            status=status
        )
        db.add(new_report)
        db.flush()
        logger.info(f"Created report record with ID: {report_id}, Store ID: {store_id}, Status: {status}")
        return new_report
    except SQLAlchemyError as e:
        logger.error(f"Failed to create report record for  Store ID {store_id}: {e}")
        raise

def update_report_status(db: Session, report_id: str, status: str, file_path: str = None):
    try:
        values_to_update = {"status": status}
        if status in ['COMPLETE', 'FAILED']:
            values_to_update["completed_at"] = datetime.datetime.now(pytz.utc)
        if file_path and status == 'COMPLETE':
            values_to_update["report_file_path"] = file_path

        stmt = (
            update(models.Report) 
            .where(models.Report.id == report_id)
            .values(**values_to_update)
            .execution_options(synchronize_session="fetch")
        )
        result = db.execute(stmt)
    except SQLAlchemyError as e:
        raise

def get_report_details(db: Session, report_id: str) -> Optional['models.Report']:
    report = db.query(models.Report).filter(models.Report.id == report_id).first()
    if report:
        logger.debug(f"Retrieved report details for ID: {report_id}")
    else:
        logger.debug(f"Report ID {report_id} not found.")
    return report
    
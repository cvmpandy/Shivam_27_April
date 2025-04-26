import pandas as pd
import logging
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert
import pytz
from datetime import datetime
import uuid
from typing import Optional, Set 
from . import models
from .database import get_db, init_db
from .config import (
    STATUS_CSV_PATH, BUSINESS_HOURS_CSV_PATH, TIMEZONE_CSV_PATH,
    DEFAULT_TIMEZONE
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

MAX_TIMESTAMP_UTC = None

def _parse_uuid(store_id_str: str, context: str = "") -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(store_id_str))
    except (ValueError, TypeError, AttributeError) as e:
        logger.warning(f"Invalid UUID format '{store_id_str}' encountered {context}. Skipping. Error: {e}")
        return None

def load_timezones(db: Session, filepath: str) -> Set[uuid.UUID]:
    processed_store_ids: Set[uuid.UUID] = set()
    try:
        df = pd.read_csv(filepath, dtype={'store_id': str})
        logger.info(f"Read {len(df)} rows from timezone CSV: {filepath}")
        stores_to_upsert = []
        for index, row in df.iterrows():
            store_id_uuid = _parse_uuid(row['store_id'], f"in timezone CSV row {index}")
            if store_id_uuid is None: continue

            timezone_str = row.get('timezone_str', DEFAULT_TIMEZONE)
            try:
                pytz.timezone(timezone_str)
            except pytz.UnknownTimeZoneError:
                logger.warning(f"Store ID {store_id_uuid}: Invalid timezone '{timezone_str}'. Using default '{DEFAULT_TIMEZONE}'.")
                timezone_str = DEFAULT_TIMEZONE

            stores_to_upsert.append({
                'id': store_id_uuid,
                'timezone_str': timezone_str
            })
            processed_store_ids.add(store_id_uuid)

        if stores_to_upsert:
            stmt = pg_insert(models.Store).values(stores_to_upsert)
            stmt = stmt.on_conflict_do_update(
                index_elements=['id'],
                set_=dict(
                    timezone_str=stmt.excluded.timezone_str
                 )
            )
            db.execute(stmt)
            logger.info(f"Upserted {len(stores_to_upsert)} store timezone records.")

    except FileNotFoundError:
        logger.error(f"Timezone file not found: {filepath}")
    except Exception as e:
        logger.error(f"Error processing timezones: {e}", exc_info=True)
        raise
    return processed_store_ids

def ensure_stores_exist(db: Session, store_ids_to_check: Set[uuid.UUID], all_known_store_ids: Set[uuid.UUID]):
    new_stores_to_add = []
    missing_ids = {sid for sid in store_ids_to_check if isinstance(sid, uuid.UUID)} - all_known_store_ids

    if not missing_ids:
        return all_known_store_ids

    for store_id_uuid in missing_ids:
        new_stores_to_add.append({'id': store_id_uuid, 'timezone_str': DEFAULT_TIMEZONE})

    if new_stores_to_add:
        try:
            stmt = pg_insert(models.Store).values(new_stores_to_add)
            stmt = stmt.on_conflict_do_nothing(index_elements=['id'])
            db.execute(stmt)
            logger.info(f"Created {len(new_stores_to_add)} missing store records with default timezone.")
            all_known_store_ids.update(missing_ids)
        except Exception as e:
            logger.error(f"Error creating missing store records: {e}", exc_info=True)

    return all_known_store_ids


def load_business_hours(db: Session, filepath: str, all_known_store_ids: Set[uuid.UUID]):
    try:
        df = pd.read_csv(filepath, dtype={'store_id': str})
        logger.info(f"Read {len(df)} rows from business hours CSV: {filepath}")
        df.columns = df.columns.str.strip()
        current_bh_store_ids_uuid = set()
        valid_rows = []
        for index, row in df.iterrows():
             store_id_uuid = _parse_uuid(row['store_id'], f"in business hours CSV row {index}")
             if store_id_uuid:
                  current_bh_store_ids_uuid.add(store_id_uuid)
                  valid_rows.append((index, store_id_uuid, row))

        all_known_store_ids = ensure_stores_exist(db, current_bh_store_ids_uuid, all_known_store_ids)

        stores_in_file_uuid = list(current_bh_store_ids_uuid)
        if stores_in_file_uuid:
            db.query(models.BusinessHour).filter(models.BusinessHour.store_id.in_(stores_in_file_uuid)).delete(synchronize_session=False)
            logger.info(f"Deleted existing business hours for {len(stores_in_file_uuid)} stores found in the CSV.")

        hours_to_add = []
        for index, store_id_uuid, row in valid_rows:
            try:
                day = int(row['dayOfWeek'])
                start_time = datetime.strptime(row['start_time_local'], '%H:%M:%S').time() if ':' in str(row['start_time_local']) else None
                end_time = datetime.strptime(row['end_time_local'], '%H:%M:%S').time() if ':' in str(row['end_time_local']) else None

                if start_time is None or end_time is None or not (0 <= day <= 6):
                    logger.warning(f"Invalid time or day format for Store ID {store_id_uuid}, Day {day}. Skipping row {index}.")
                    continue

                hours_to_add.append(models.BusinessHour(
                    store_id=store_id_uuid,
                    day_of_week=day,
                    start_time_local=start_time,
                    end_time_local=end_time
                ))
            except Exception as parse_error:
                 logger.warning(f"Error parsing business hour row {index}: {row.to_dict()}. Error: {parse_error}. Skipping.")
                 continue

        if hours_to_add:
            db.add_all(hours_to_add)
            logger.info(f"Added {len(hours_to_add)} new business hour records.")

    except FileNotFoundError:
        logger.error(f"Business hours file not found: {filepath}")
    except Exception as e:
        logger.error(f"Error processing business hours: {e}", exc_info=True)
        raise
    return all_known_store_ids

def load_status_polls(db: Session, filepath: str, all_known_store_ids: Set[uuid.UUID]):
    global MAX_TIMESTAMP_UTC
    max_ts_this_file = None
    try:
        chunk_size = 10000
        total_rows_processed = 0
        #this is done by me so that when i inject data once again all previous data get deleted
        logger.warning("Deleting existing poll records. before reingestion.")
        deleted_count = db.query(models.StoreStatusPoll).delete(synchronize_session=False)
        logger.info(f"Deleted {deleted_count} existing poll records.")

        for chunk in pd.read_csv(filepath, chunksize=chunk_size, parse_dates=['timestamp_utc'], dtype={'store_id': str}):
            logger.info(f"Processing chunk of {len(chunk)} status poll rows...")
            chunk.columns = chunk.columns.str.strip()
            current_poll_store_ids_uuid = set()
            valid_rows_chunk = []
            for index, row in chunk.iterrows():
                store_id_uuid = _parse_uuid(row['store_id'], f"in status poll CSV chunk row {index}")
                if store_id_uuid:
                    current_poll_store_ids_uuid.add(store_id_uuid)
                    valid_rows_chunk.append((index, store_id_uuid, row))

            all_known_store_ids = ensure_stores_exist(db, current_poll_store_ids_uuid, all_known_store_ids)

            if chunk['timestamp_utc'].dt.tz is None:
                 chunk['timestamp_utc'] = chunk['timestamp_utc'].dt.tz_localize('UTC')
            else:
                 chunk['timestamp_utc'] = chunk['timestamp_utc'].dt.tz_convert('UTC')

            chunk_max_ts = chunk['timestamp_utc'].max()
            if pd.notna(chunk_max_ts):
                 if max_ts_this_file is None or chunk_max_ts > max_ts_this_file:
                     max_ts_this_file = chunk_max_ts

            polls_to_add = []
            for index, store_id_uuid, row in valid_rows_chunk:
                status = str(row['status']).lower()
                timestamp = row['timestamp_utc']

                if status not in ['active', 'inactive'] or pd.isna(timestamp):
                    logger.warning(f"Invalid status or timestamp for Store ID {store_id_uuid}. Skipping chunk row {index}.")
                    continue

                polls_to_add.append(models.StoreStatusPoll(
                    store_id=store_id_uuid,
                    timestamp_utc=timestamp.to_pydatetime(),
                    status=status
                ))

            if polls_to_add:
                db.add_all(polls_to_add)
                db.flush()
                logger.info(f"Added {len(polls_to_add)} poll records from chunk.")
                total_rows_processed += len(polls_to_add)

        logger.info(f"Finished processing status polls. Total rows added: {total_rows_processed}")

        if max_ts_this_file:
            MAX_TIMESTAMP_UTC = max_ts_this_file
            if MAX_TIMESTAMP_UTC.tzinfo is None:
                 MAX_TIMESTAMP_UTC = pytz.utc.localize(MAX_TIMESTAMP_UTC)
            logger.info(f"Maximum timestamp found in status data: {MAX_TIMESTAMP_UTC.isoformat()}")
        else:
             logger.warning("No valid timestamps found in the status poll data.")

    except FileNotFoundError:
        logger.error(f"Status poll file not found: {filepath}")
    except Exception as e:
        logger.error(f"Error processing status polls: {e}")
        raise
    return all_known_store_ids

def run_full_ingestion():
    try:
        init_db() 
        with get_db() as db:
            logger.info("--- Loading Timezones ---")
            all_store_ids = load_timezones(db, TIMEZONE_CSV_PATH)

            logger.info("--- Loading Business Hours ---")
            all_store_ids = load_business_hours(db, BUSINESS_HOURS_CSV_PATH, all_store_ids)

            logger.info("--- Loading Status Polls ---")
            all_store_ids = load_status_polls(db, STATUS_CSV_PATH, all_store_ids)

        if MAX_TIMESTAMP_UTC:    
            with open("MAX_TIMESTAMP_UTC.txt", "w") as f:
                f.write(MAX_TIMESTAMP_UTC.isoformat())
                logger.info(f"Reference 'Current Time' (Max Timestamp): {MAX_TIMESTAMP_UTC.isoformat()}")    
        else:
            logger.error("MAX_TIMESTAMP_UTC is not set. Cannot save to file.")

    except Exception as e:
        logger.critical(f"Data ingestion failed: {e}")

def get_max_timestamp(): 
    try:
        with open("MAX_TIMESTAMP_UTC.txt", "r") as f:
            timestamp_str = f.read().strip()
            if not timestamp_str:
                logger.warning("MAX_TIMESTAMP_UTC has not been set. Run ingestion first.")
                return None
            else:
                return pd.to_datetime(timestamp_str, utc=True)
    except Exception as e:
        logger.error(f"Error reading MAX_TIMESTAMP_UTC from file: {e}")
        return None

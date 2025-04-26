import logging
from datetime import datetime, timedelta, time
import pytz
from sqlalchemy.orm import Session
from typing import List, Dict, Tuple, Optional
import uuid
from . import models
from .config import DEFAULT_STORE_STATUS, POLL_FETCH_BUFFER_HOURS, DEFAULT_TIMEZONE

logger = logging.getLogger(__name__)

def get_business_hours_for_store(db: Session, store_id: uuid.UUID) -> Dict[int, List[Tuple[time, time]]]:
    hours_query = db.query(models.BusinessHour).filter(models.BusinessHour.store_id == store_id).all()
    business_hours_map = {day: [] for day in range(7)} # 0=Monday, 6=Sunday

    if not hours_query:
        logger.debug(f"Store ID {store_id}: No business hours found, assuming 24/7.")
        for day in range(7):
            # 24 hours using min/max time objects
            business_hours_map[day].append((time.min, time.max))
    else:
        for bh in hours_query:
            business_hours_map[bh.day_of_week].append((bh.start_time_local, bh.end_time_local))
        logger.debug(f"Store ID {store_id}: Loaded business hours map: {business_hours_map}")

    return business_hours_map


def is_store_open(local_dt: datetime, business_hours_map: Dict[int, List[Tuple[time, time]]]) -> bool:
    day_of_week = local_dt.weekday() # Monday is 0, Sunday is 6
    current_time = local_dt.time()
    for start_time, end_time in business_hours_map.get(day_of_week, []):
        if start_time == time.min and end_time == time.max:
            return True
        if start_time <= end_time:
            if start_time <= current_time < end_time:
                return True
        else:
            if start_time <= current_time:
                return True

    prev_day_of_week = (day_of_week - 1 + 7) % 7
    for start_time, end_time in business_hours_map.get(prev_day_of_week, []):
        if start_time > end_time: 
            if current_time < end_time:
                return True

    return False


def get_status_at_time(target_utc_dt: datetime, polls: List[models.StoreStatusPoll]) -> str:
    last_known_status = DEFAULT_STORE_STATUS 
    for poll in polls:
        poll_time = poll.timestamp_utc
        if poll_time.tzinfo is None: 
             poll_time = pytz.utc.localize(poll_time)

        if poll_time <= target_utc_dt:
            last_known_status = poll.status
        else:
            break
    return last_known_status


def calculate_store_uptime_for_period(
    store_id: uuid.UUID, 
    store_timezone_str: str,
    business_hours_map: Dict[int, List[Tuple[time, time]]],
    interval_start_utc: datetime,
    interval_end_utc: datetime,
    polls: List[models.StoreStatusPoll] 
) -> Tuple[float, float]:
    uptime_minutes = 0.0
    downtime_minutes = 0.0

    if interval_start_utc.tzinfo is None: interval_start_utc = pytz.utc.localize(interval_start_utc)
    if interval_end_utc.tzinfo is None: interval_end_utc = pytz.utc.localize(interval_end_utc)

    try:
        store_tz = pytz.timezone(store_timezone_str)
    except pytz.UnknownTimeZoneError:
        logger.warning(f"Store ID {store_id}: Invalid timezone '{store_timezone_str}'. Using default {DEFAULT_TIMEZONE}.")
        store_tz = pytz.timezone(DEFAULT_TIMEZONE) 

    current_minute_utc = interval_start_utc
    while current_minute_utc < interval_end_utc:
        local_dt = current_minute_utc.astimezone(store_tz)

        is_open = is_store_open(local_dt, business_hours_map)

        if is_open:
            current_status = get_status_at_time(current_minute_utc, polls)
            if current_status == 'active':
                uptime_minutes += 1
            else: 
                downtime_minutes += 1

        current_minute_utc += timedelta(minutes=1)

    return uptime_minutes, downtime_minutes


def generate_report_data_for_store(
    db: Session,
    store: models.Store,
    reference_time_utc: datetime
) -> Dict:
    store_id = store.id
    store_timezone_str = store.timezone_str
    logger.info(f"Calculating report data for Store ID: {store_id}")

    if reference_time_utc.tzinfo is None:
        reference_time_utc = pytz.utc.localize(reference_time_utc)

    business_hours_map = get_business_hours_for_store(db, store_id)

    hour_ago = reference_time_utc - timedelta(hours=1)
    day_ago = reference_time_utc - timedelta(days=1)
    week_ago = reference_time_utc - timedelta(days=7)

    poll_query_start_time = week_ago - timedelta(hours=POLL_FETCH_BUFFER_HOURS)
    polls = db.query(models.StoreStatusPoll).filter(
        models.StoreStatusPoll.store_id == store_id,
        models.StoreStatusPoll.timestamp_utc >= poll_query_start_time,
        models.StoreStatusPoll.timestamp_utc <= reference_time_utc # Polls up to the reference time
    ).order_by(models.StoreStatusPoll.timestamp_utc.asc()).all()
    logger.info(f"Store ID {store_id}: Fetched {len(polls)} polls relevant for the last week.")
   

    uptime_last_hour, downtime_last_hour = calculate_store_uptime_for_period(
        store_id, store_timezone_str, business_hours_map, hour_ago, reference_time_utc, polls 
    )
    uptime_last_day, downtime_last_day = calculate_store_uptime_for_period(
        store_id, store_timezone_str, business_hours_map, day_ago, reference_time_utc, polls 
    )
    uptime_last_week, downtime_last_week = calculate_store_uptime_for_period(
        store_id, store_timezone_str, business_hours_map, week_ago, reference_time_utc, polls 
    )

    uptime_last_day_hr = round(uptime_last_day / 60.0, 2)
    downtime_last_day_hr = round(downtime_last_day / 60.0, 2)
    uptime_last_week_hr = round(uptime_last_week / 60.0, 2)
    downtime_last_week_hr = round(downtime_last_week / 60.0, 2)

    return {
        "store_id": store_id,
        "uptime_last_hour": int(round(uptime_last_hour)),
        "uptime_last_day": uptime_last_day_hr,
        "uptime_last_week": uptime_last_week_hr,
        "downtime_last_hour": int(round(downtime_last_hour)),
        "downtime_last_day": downtime_last_day_hr,
        "downtime_last_week": downtime_last_week_hr
    }
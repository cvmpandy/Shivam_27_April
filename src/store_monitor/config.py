import os
from dotenv import load_dotenv
import logging

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATABASE_URL = os.getenv("DATABASE_URL")
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND")

# Data Files Path
DATA_DIR = os.path.join(PROJECT_ROOT, "store-monitoring-data")
STATUS_CSV_PATH = os.path.join(DATA_DIR, "store_status.csv")
BUSINESS_HOURS_CSV_PATH = os.path.join(DATA_DIR, "menu_hours.csv")
TIMEZONE_CSV_PATH = os.path.join(DATA_DIR, "timezones.csv")

# Output Files Path 
REPORTS_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "reports_output")
os.makedirs(REPORTS_OUTPUT_DIR, exist_ok=True)
logging.info(f"Reports will be saved to: {REPORTS_OUTPUT_DIR}")

DEFAULT_TIMEZONE = "America/Chicago"
DEFAULT_STORE_STATUS = "inactive"

POLL_FETCH_BUFFER_HOURS = 1
# Store Uptime Monitoring System

## Overview

This project provides a backend system to monitor the uptime and downtime of retail stores based on periodic status polls. Store owners often need to know if their point-of-sale or other critical systems were online during their specified business hours. This system ingests store status data, business hours, and timezone information to generate reports detailing uptime and downtime over the last hour, day, and week.

The system exposes RESTful APIs to trigger report generation for specific stores and retrieve the generated reports or check their status.

## Features

*   **Asynchronous Report Generation:** Reports are generated in the background without blocking API responses.
*   **Store-Specific Reports:** Trigger reports for individual stores via API request.
*   **Status Polling:** Check if a report is `Running`, `Complete`, or `Failed`.
*   **CSV Report Output:** Completed reports are delivered as CSV files containing:
    *   `store_id`
    *   `uptime_last_hour` (minutes)
    *   `uptime_last_day` (hours)
    *   `uptime_last_week` (hours)
    *   `downtime_last_hour` (minutes)
    *   `downtime_last_day` (hours)
    *   `downtime_last_week` (hours)
*   **Business Hours Compliance:** Calculates uptime/downtime strictly within each store's specified local business hours.
*   **Timezone Awareness:** Correctly handles conversions between UTC poll data and local business hours using store-specific timezones.
*   **Data Extrapolation:** Assumes store status remains constant between polls.
*   **Default Assumptions:**
    *   If business hours are missing for a store, it assumes the store is open 24/7.
    *   If a timezone is missing for a store, it defaults to `America/Chicago`.

## Technology Stack

We chose the following technologies to build a robust and efficient system:

*   **Python (3.9+):** A versatile language with a rich ecosystem of libraries suitable for web development, data processing, and task queueing.
*   **FastAPI:** A modern, high-performance Python web framework for building APIs.
    *   *Why:* Excellent performance (built on Starlette & Pydantic), automatic data validation, interactive API documentation (Swagger UI/ReDoc), native asynchronous support.
*   **PostgreSQL:** A powerful, open-source object-relational database system.
    *   *Why:* Excellent support for date/time/timezone operations, robust, reliable, handles relational data (store schedules, reports) well, and supports UUID types efficiently.
*   **SQLAlchemy:** A comprehensive SQL toolkit and Object-Relational Mapper (ORM) for Python.
    *   *Why:* Simplifies database interactions, allows working with Python objects instead of raw SQL (mostly), provides connection pooling and session management.
*   **Celery:** A distributed task queue system for Python.
    *   *Why:* Handles long-running background tasks (like report generation) asynchronously, preventing API timeouts and allowing the system to scale worker processes independently.
*   **Redis:** An in-memory data structure store, often used as a message broker and result backend.
    *   *Why:* Fast and efficient broker/backend for Celery, simple to set up.
*   **Pandas:** A powerful data manipulation library.
    *   *Why:* Used primarily during the initial data ingestion phase to easily read and process CSV files.
*   **Pytz:** A standard library for accurate timezone calculations in Python.
    *   *Why:* Essential for correctly converting between UTC timestamps from polls and local times for business hours.
*   **Docker & Docker Compose:** Containerization tools.
    *   *Why:* Ensures a consistent development and deployment environment, simplifies setting up dependencies like PostgreSQL and Redis.

## Architecture & Project Flow

The system follows a standard asynchronous web service architecture:

1.  **Data Ingestion (Initial Setup):**
    *   A Python script (`scripts/run_ingestion.py`) reads static CSV files containing:
        *   Store status polls (`store_id, timestamp_utc, status`).
        *   Store business hours (`store_id, dayOfWeek, start_time_local, end_time_local`).
        *   Store timezones (`store_id, timezone_str`).
    *   The script parses this data, performs necessary conversions (e.g., string UUIDs to UUID objects), and loads it into the PostgreSQL database using SQLAlchemy models.
    *   It determines the maximum timestamp from the poll data (`MAX_TIMESTAMP_UTC`) to use as the fixed reference "current time" for reports.
2.  **API Request (Trigger):**
    *   A client sends a `POST` request to `/trigger_report/{store_id}`.
    *   The **FastAPI** application receives the request.
    *   It validates the `store_id` format (UUID).
    *   It checks if the `store_id` exists in the database using a **SQLAlchemy** session provided via `get_db`.
    *   It creates a new `Report` record in the **PostgreSQL** database with a unique `report_id` and status `PENDING`.
    *   It dispatches a background task (`generate_report_task`) message to the **Celery** queue via the **Redis** broker, passing the `report_id`, `store_id`, and the fixed `reference_time_utc`.
    *   The API immediately returns a `202 Accepted` response with the `report_id`.
3.  **Background Processing (Report Generation):**
    *   A **Celery Worker** process, listening to the **Redis** queue, picks up the `generate_report_task` message.
    *   The worker updates the `Report` status in **PostgreSQL** to `RUNNING`.
    *   It fetches the specific `Store` details and relevant `StoreStatusPoll` and `BusinessHour` data from **PostgreSQL**.
    *   It performs the core **Uptime/Downtime Calculation** logic (see below).
    *   It generates a **CSV** file containing the results.
    *   It saves the CSV file to the configured output directory (`reports_output/` by default).
    *   It updates the `Report` record in **PostgreSQL** with status `COMPLETE` and the path to the generated CSV file.
    *   (If any error occurs, it updates the status to `FAILED`).
4.  **API Request (Get Report):**
    *   A client sends a `GET` request to `/get_report/{report_id}`.
    *   **FastAPI** receives the request.
    *   It queries **PostgreSQL** (using `get_report_details`) for the `Report` record matching the `report_id`.
    *   If not found, it returns `404 Not Found`.
    *   If found, it checks the `status`:
        *   If `PENDING` or `RUNNING`, it returns a JSON response `{"status": "running"}` with status code `202 Accepted`.
        *   If `FAILED`, it returns a JSON response `{"status": "failed"}` with status code `410 Gone`.
        *   If `COMPLETE`, it checks if the associated CSV file exists.
            *   If the file exists, it returns a `FileResponse` containing the CSV data, setting `Content-Type: text/csv` and adding a custom `X-Report-Status: Complete` header. Status code `200 OK`.
            *   If the file is missing, it returns `500 Internal Server Error`.

## Core Logic: Uptime/Downtime Calculation

The calculation aims to determine, for a given store and reference time (`MAX_TIMESTAMP_UTC`), how much time the store was active (uptime) versus inactive (downtime) *only during its business hours* over three periods: the last hour, last day, and last week relative to the reference time.

The logic resides primarily in `src/store_monitor/calculation.py`.

**Key Steps:**

1.  **Inputs:** The function `generate_report_data_for_store` takes the target `Store` object and the `reference_time_utc`.
2.  **Fetch Data:**
    *   Retrieves the store's business hours rules using `get_business_hours_for_store`. This function handles the "24/7 default" if no specific hours are found in the DB.
    *   Fetches all relevant status polls (`StoreStatusPoll`) for the store from the database. To ensure accuracy, it fetches polls covering the entire last week plus a buffer period before that. This is done *once* per report generation.
3.  **Define Intervals:** Calculates the start times for the "last hour," "last day," and "last week" based on the `reference_time_utc`.
4.  **Iterate Minute-by-Minute:** The core calculation (`calculate_store_uptime_for_period`) iterates through *every minute* from the start of the last week (`week_ago`) up to the `reference_time_utc`.
5.  **For Each Minute:**
    *   **Convert Time:** The current minute (in UTC) is converted to the store's local time using its specific timezone (`store.timezone_str` and `pytz`).
    *   **Check Business Hours:** The `is_store_open` function checks if this local time falls within *any* of the defined business hour intervals for that specific day of the week. It correctly handles:
        *   Stores open 24/7 (represented by `time.min` to `time.max`).
        *   Intervals that cross midnight (e.g., Monday 20:00 to Tuesday 02:00).
    *   **Check Status (If Open):** If `is_store_open` returns `True`:
        *   The `get_status_at_time` function determines the store's status *at this specific UTC minute*. It does this by finding the *most recent poll timestamp* that is less than or equal to the current minute's timestamp.
        *   **Extrapolation:** The status found in that last poll (`active` or `inactive`) is assumed to be the store's status for the current minute. If no preceding poll is found, it defaults to `inactive` (configurable via `DEFAULT_STORE_STATUS`).
    *   **Increment Counters (If Open):** Based on the determined `current_status` (`active` or `inactive`), the corresponding uptime or downtime counter (in minutes) is incremented for the relevant period(s) (last hour, last day, last week) that the current minute falls within. If the store was determined to be closed at this minute, neither counter is incremented.
6.  **Format Results:** After iterating through all minutes in the last week, the accumulated uptime/downtime minutes for the "last day" and "last week" periods are converted to hours (float, rounded to 2 decimal places). The "last hour" values remain in minutes.
7.  **Return Dictionary:** The final results are compiled into a dictionary matching the required output CSV schema.

## Setup and Running Instructions

1.  **Prerequisites:**
    *   Git
    *   Python 3.9+ and Pip
    *   Docker and Docker Compose
2.  **Clone Repository:**
    ```bash
    git clone <your-repository-url>
    cd store-monitoring-system # Or your repo name
    ```
3.  **Create Virtual Environment:** (Recommended)
    ```bash
    python3 -m venv venv
    # Activate:
    # macOS/Linux:
    source venv/bin/activate
    # Windows (cmd): venv\Scripts\activate.bat
    # Windows (PowerShell): venv\Scripts\Activate.ps1
    ```
4.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
5.  **Configure Environment:**
    *   Copy or rename `.env.example` to `.env`.
    *   Edit the `.env` file and set your `DATABASE_URL` (matching the `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` in `docker-compose.yml`).
    *   **CRITICAL:** Add `.env` to your `.gitignore` file if it's not already there! `echo ".env" >> .gitignore`
    ```dotenv
    # .env Example Content
    DATABASE_URL=postgresql://myappuser:mysecretpassword@localhost:5432/store_monitor_db
    # CELERY_BROKER_URL=redis://localhost:6379/0
    # CELERY_RESULT_BACKEND=redis://localhost:6379/1
    # REPORTS_OUTPUT_DIR=/path/to/your/reports # Optional override
    ```
6.  **Prepare Data:** Place your input CSV files (`store_status.csv`, `store_business_hours.csv`, `store_timezones.csv`) into the `/data` directory. Ensure `store_id` columns contain valid UUIDs.
7.  **Start Services (Database & Cache):**
    ```bash
    docker-compose up -d
    ```
    *(Wait for the database and Redis containers to start fully)*
8.  **Run Data Ingestion:**
    ```bash
    python scripts/run_ingestion.py
    ```
    *(Check the output for success messages and the determined `MAX_TIMESTAMP_UTC`)*
9.  **Start Celery Worker:** (Open a new terminal in the project root, activate venv)
    ```bash
    celery -A src.store_monitor.celery_app worker --loglevel=INFO
    ```
    *(Leave this running)*
10. **Start API Server:** (Open another new terminal, activate venv)
    ```bash
    uvicorn src.store_monitor.api:app --host 0.0.0.0 --port 8000 --reload
    ```
    *(Leave this running)*
11. **Access API:**
    *   API Docs (Swagger UI): [http://localhost:8000/docs](http://localhost:8000/docs)
    *   Use the docs or tools like `curl`/Postman to interact with the endpoints.

## API Endpoints

*   **`POST /trigger_report/{store_id}`**
    *   **Input:** `store_id` (UUID string) in the URL path.
    *   **Action:** Checks if store exists, creates a pending report record, dispatches background generation task for that store.
    *   **Output:** `202 Accepted` with JSON body: `{"report_id": "unique-report-uuid"}`.
*   **`GET /get_report/{report_id}`**
    *   **Input:** `report_id` (string) from the trigger response in the URL path.
    *   **Action:** Checks the status of the report generation.
    *   **Output:**
        *   **If Running:** `202 Accepted` with JSON body: `{"status": "running"}`.
        *   **If Failed:** `410 Gone` with JSON body: `{"status": "failed"}`.
        *   **If Complete:** `200 OK` with the generated CSV file as the response body. Includes `Content-Type: text/csv` and `X-Report-Status: Complete` headers.
        *   **If Not Found:** `404 Not Found`.
        *   **If Complete but file missing:** `500 Internal Server Error`.

## Future Improvements

*   **Event-Driven Data Ingestion:** Instead of hourly CSV batch processing, implement an endpoint (or consume from a message queue like Kafka/RabbitMQ) where status "pings" or events for individual stores can be sent in near real-time. These events would directly insert/update `StoreStatusPoll` records, making data fresher and potentially simplifying calculation lookups.
*   **Real-time Status Updates:** Use WebSockets to push report status updates (`Running`, `Complete`, `Failed`) to clients instead of requiring polling.
*   **User Interface:** A simple web front-end for users to trigger reports and view results.
*   **Alerting:** Proactively notify store owners if downtime exceeds a certain threshold during business hours.
*   **Parameterized Reports:** Allow users to specify custom date ranges instead of just fixed "last hour/day/week". This would require changing the fixed `MAX_TIMESTAMP_UTC` logic.
*   **Owner Authentication & Multi-Tenancy:** Re-introduce user accounts and API keys so owners can only trigger/view reports for stores they own.
*   **Cloud Deployment:** Configure for deployment on platforms like AWS, GCP, or Azure, potentially using:
    *   Managed databases (RDS, Cloud SQL).
    *   Managed Redis (ElastiCache, Memorystore).
    *   Object storage (S3, GCS) for storing generated reports instead of the local filesystem.
    *   Container orchestration (ECS, EKS, GKE).
*   **Enhanced Extrapolation:** Implement more sophisticated methods for estimating status between polls if the current "last status holds" assumption is insufficient.
*   **Database Indexing & Optimization:** Analyze query performance for large datasets and add/tune database indexes as needed.


from celery import Celery
import os
from . import config

app = Celery('store_monitor_tasks',
             broker=config.CELERY_BROKER_URL,
             backend=config.CELERY_RESULT_BACKEND,
             include=['src.store_monitor.tasks'])

app.conf.update(
    task_serializer='json',
    accept_content=['json'],  
    result_serializer='json',
    timezone='UTC',         
    enable_utc=True
)

if __name__ == '__main__':
    app.start()
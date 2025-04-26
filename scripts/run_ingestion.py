import sys
import os
import logging

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src_path = os.path.join(project_root, "src")
sys.path.insert(0, src_path)

logging.basicConfig(level=logging.INFO, format='%(asctime)s  - %(message)s')

from store_monitor.ingestion import run_full_ingestion

if __name__ == "__main__":
    logging.info(" Data ingestion starts...")
    run_full_ingestion()
    logging.info("Data ingestion finished.")
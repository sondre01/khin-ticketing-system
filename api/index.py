import os
import sys
import logging
import traceback

logger = logging.getLogger("vercel.entrypoint")

# Ensure repository root is on sys.path so backend modules can be imported
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

try:
    from backend.main import app
except Exception as e:
    logger.error("CRITICAL: Failed to import FastAPI application in Vercel entrypoint:")
    traceback.print_exc()
    raise e

# Expose app for Vercel Serverless Function runtime
__all__ = ["app"]


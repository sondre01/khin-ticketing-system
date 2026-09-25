import os
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
import logging
from contextlib import contextmanager
from backend.config import DATABASE_URL

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ticketing_system.database")

_pool = None

def init_db_pool():
    global _pool
    if _pool is not None and not _pool.closed:
        return
    is_vercel = bool(os.environ.get("VERCEL"))
    if not DATABASE_URL or (is_vercel and ("localhost" in DATABASE_URL or "127.0.0.1" in DATABASE_URL)):
        err_msg = (
            "DATABASE_URL is not configured in Vercel Environment Variables. "
            "Please configure DATABASE_URL in your Vercel Project Settings -> Environment Variables."
        )
        logger.error(err_msg)
        raise psycopg2.OperationalError(err_msg)
    try:
        # Initialize a connection pool (min 1, max 4 connections for serverless resilience)
        _pool = psycopg2.pool.SimpleConnectionPool(
            1, 4,
            dsn=DATABASE_URL,
            connect_timeout=5
        )
        logger.info("PostgreSQL connection pool initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize PostgreSQL connection pool: {e}")
        logger.error("Please ensure PostgreSQL is accessible and DATABASE_URL is configured.")
        raise e

def close_db_pool():
    global _pool
    if _pool:
        try:
            _pool.closeall()
            logger.info("PostgreSQL connection pool closed.")
        except Exception as e:
            logger.warning(f"Warning closing database pool: {e}")
        finally:
            _pool = None

@contextmanager
def get_db_connection():
    global _pool
    if _pool is None or _pool.closed:
        init_db_pool()
    
    conn = None
    try:
        conn = _pool.getconn()
        # In serverless environments, verify the pooled connection is still alive
        if conn.closed != 0:
            _pool.putconn(conn, close=True)
            conn = _pool.getconn()
        yield conn
    except psycopg2.OperationalError as e:
        logger.warning(f"Database operational error encountered: {e}")
        if conn and _pool:
            try:
                _pool.putconn(conn, close=True)
                conn = None
            except Exception:
                pass
        raise e
    finally:
        if conn and _pool and conn.closed == 0:
            _pool.putconn(conn)


@contextmanager
def get_db_cursor(commit=True):
    with get_db_connection() as conn:
        # RealDictCursor returns rows as python dicts where keys are column names
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                yield cur
                if commit:
                    conn.commit()
            except Exception as e:
                conn.rollback()
                logger.error(f"Database transaction error (rolled back): {e}")
                raise e

def initialize_database():
    """Reads schema.sql and runs it to set up tables if they don't exist."""
    schema_path = os.path.join(os.path.dirname(__file__), "..", "schema.sql")
    if not os.path.exists(schema_path):
        logger.warning(f"schema.sql not found at {schema_path}, skipping tables initialization.")
        return
        
    logger.info("Applying database schema...")
    try:
        with open(schema_path, "r") as f:
            schema_sql = f.read()
            
        with get_db_cursor(commit=True) as cur:
            cur.execute(schema_sql)
            logger.info("Database schema applied successfully.")
    except Exception as e:
        logger.error(f"Failed to apply database schema: {e}")
        raise e

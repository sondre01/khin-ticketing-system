import os
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
import logging
from contextlib import contextmanager
from backend.config import DATABASE_URL as CONFIG_DATABASE_URL

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ticketing_system.database")

_pool = None

def get_database_url() -> str:
    """
    Retrieves and normalizes DATABASE_URL from os.getenv for Supabase / PostgreSQL.
    - Strips leading/trailing quotes and whitespace.
    - Normalizes postgres:// to postgresql://.
    - Ensures sslmode=require is appended for remote cloud hosts (Supabase, Neon, RDS).
    """
    raw_url = os.getenv("DATABASE_URL") or CONFIG_DATABASE_URL
    if not raw_url:
        return ""

    url = raw_url.strip().strip("'\"")

    # Normalize dialect schema for psycopg2 / SQLAlchemy compatibility
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]

    # Enforce sslmode=require for cloud-hosted databases (Supabase poolers require SSL)
    is_local = "localhost" in url or "127.0.0.1" in url
    if not is_local and "sslmode=" not in url:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}sslmode=require"

    # Enforce client_encoding=utf8 (prevents psycopg2 "server didn't return client encoding" with Supabase pooler)
    if not is_local and "client_encoding=" not in url:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}client_encoding=utf8"

    return url

def init_db_pool():
    global _pool
    if _pool is not None and not _pool.closed:
        return
    
    is_vercel = bool(os.environ.get("VERCEL"))
    dsn = get_database_url()

    if not dsn or (is_vercel and ("localhost" in dsn or "127.0.0.1" in dsn)):
        err_msg = (
            "DATABASE_URL is not configured in Vercel Environment Variables. "
            "Please configure DATABASE_URL in your Vercel Project Settings -> Environment Variables "
            "using your Supabase Transaction Pooler connection string (port 6543)."
        )
        logger.error(err_msg)
        raise psycopg2.OperationalError(err_msg)
    try:
        # Initialize a connection pool (min 1, max 4 connections for serverless resilience)
        # 10s connect timeout gives sufficient buffer for cloud cold-start handshakes
        _pool = psycopg2.pool.SimpleConnectionPool(
            1, 4,
            dsn=dsn,
            connect_timeout=10
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
        
        # Connection recycling: Supabase/serverless poolers terminate idle connections.
        # Test connection liveness via lightweight query; recycle if connection was dropped.
        is_alive = False
        if conn and conn.closed == 0:
            try:
                with conn.cursor() as test_cur:
                    test_cur.execute("SELECT 1;")
                is_alive = True
            except Exception:
                is_alive = False

        if not is_alive:
            logger.info("Recycling stale pooled connection dropped by database server...")
            if conn:
                try:
                    _pool.putconn(conn, close=True)
                except Exception:
                    pass
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

DEFAULT_DEPARTMENTS = [
    # Technology & Product
    ("Information Technology (IT)", "Manages networks, hardware, cloud servers, cybersecurity, and internal tech support."),
    ("Software Development / Engineering", "Writes code, builds software applications, maintains databases, and develops products."),
    ("Product Management", "Defines the product strategy, roadmaps, and features that developers need to build."),
    ("Data & Analytics", "Analyzes corporate and user data to guide business decisions and manage data pipelines."),
    # Revenue & Customer Growth
    ("Marketing", "Drives brand awareness, manages advertising campaigns, handles social media, and generates leads."),
    ("Sales", "Converts leads into paying clients, manages customer accounts, and directly drives revenue."),
    ("Customer Success / Support", "Helps clients use the product successfully and resolves their ongoing issues."),
    # Business Operations & Infrastructure
    ("Operations", "Oversees the daily machinery of the business, logistics, supply chain, and facilities."),
    ("Human Resources (HR)", "Handles recruitment, onboarding, payroll, employee benefits, and workplace culture."),
    ("Finance & Accounting", "Manages corporate budgets, financial forecasting, bookkeeping, and tax compliance."),
    ("Legal & Compliance", "Reviews contracts, protects intellectual property, and ensures adherence to industry regulations."),
    ("Procurement", "Sources and purchases the external goods, software licenses, and services the company needs."),
    # Strategy & Innovation
    ("Research & Development (R&D)", "Conducts scientific or technical research to create entirely new products or systems."),
    ("Corporate Strategy", "Focuses on long-term growth, mergers and acquisitions, and high-level partnerships.")
]

def initialize_database():
    """Reads schema.sql and runs it to set up tables if they don't exist, and seeds official departments."""
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
            
            # Ensure all 14 official departments are active
            for name, desc in DEFAULT_DEPARTMENTS:
                cur.execute(
                    """
                    INSERT INTO ticketing_system.departments (name, description, is_active)
                    VALUES (%s, %s, TRUE)
                    ON CONFLICT (name) DO UPDATE 
                    SET description = EXCLUDED.description, is_active = TRUE;
                    """,
                    (name, desc)
                )
            logger.info("Database schema applied and official departments verified successfully.")
    except Exception as e:
        logger.error(f"Failed to apply database schema: {e}")
        raise e

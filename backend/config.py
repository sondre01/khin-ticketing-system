import os
import logging
from dotenv import load_dotenv

logger = logging.getLogger("ticketing_system.config")

# Load .env file from parent directory
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    if os.environ.get("VERCEL"):
        logger.warning(
            "CRITICAL: DATABASE_URL is not set in Vercel Environment Variables! "
            "Please configure DATABASE_URL in your Vercel Project Settings -> Environment Variables."
        )
    DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/ticketing_db"

JWT_SECRET = os.getenv("JWT_SECRET", "c27df0a7bc01b4da523dfef86b840bc43f11da080788220ce6026a798a7281f6")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "5000"))

# Tech Team / Admin Access Passcode
TECH_ACCESS_KEY = os.getenv("TECH_ACCESS_KEY", "P@55w0rdKt!()")

# Gmail IMAP Configuration
GMAIL_USER = os.getenv("GMAIL_USER", "gamboa.khinandrei@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
GMAIL_IMAP_HOST = os.getenv("GMAIL_IMAP_HOST", "imap.gmail.com")
GMAIL_IMAP_PORT = int(os.getenv("GMAIL_IMAP_PORT", "993"))

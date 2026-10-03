import os
import sys
import logging
from contextlib import asynccontextmanager

# Ensure repository root is in sys.path when executed directly
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Automatically switch to project virtual environment if run directly with global python
venv_python = os.path.join(ROOT_DIR, ".venv", "Scripts", "python.exe")
if __name__ == "__main__" and os.path.exists(venv_python) and sys.executable.lower() != os.path.abspath(venv_python).lower():
    import subprocess
    result = subprocess.run([venv_python] + sys.argv)
    sys.exit(result.returncode)

import psycopg2
from fastapi import FastAPI, Depends, HTTPException, status, Request
import secrets
import json
from fastapi.responses import RedirectResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from backend.config import HOST, PORT, TECH_ACCESS_KEY, GMAIL_USER, GMAIL_APP_PASSWORD, OTP_EXPIRE_MINUTES
from backend.database import init_db_pool, close_db_pool, initialize_database, get_db_cursor
from backend.auth import hash_password, verify_password, create_access_token, decode_access_token
from backend.email_service import send_otp_email

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ticketing_system.main")

# FastAPI Lifespan Handler
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup tasks
    logger.info("Starting up backend server...")
    is_vercel = bool(os.environ.get("VERCEL"))
    if not is_vercel:
        try:
            init_db_pool()
            initialize_database()
        except Exception as e:
            logger.error(f"Startup database initialization failed: {e}")

    sync_task = None
    # Serverless runtimes like Vercel freeze background loops, causing timeouts and invocation failures.
    # Background polling is therefore enabled only in persistent local/server environments.
    if GMAIL_USER and GMAIL_APP_PASSWORD and not is_vercel:
        import asyncio
        from backend.email_service import sync_gmail_tickets

        async def background_gmail_worker():
            logger.info(f"Background Gmail ticket ingestion active (polling {GMAIL_USER} every 30s).")
            while True:
                try:
                    await asyncio.sleep(30)
                    await asyncio.to_thread(sync_gmail_tickets, limit=15, mark_as_read=True)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning(f"Background email sync iteration warning: {e}")

        sync_task = asyncio.create_task(background_gmail_worker())

    yield
    # Shutdown tasks
    if sync_task:
        sync_task.cancel()
    logger.info("Shutting down backend server...")
    if not is_vercel:
        close_db_pool()

app = FastAPI(
    title="Khin Ticket API",
    description="Python + PostgreSQL backend for Khin Ticket",
    version="1.0.0",
    lifespan=lifespan
)

# CORS Middleware (allows local HTML files to connect if opened directly)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic Schemas for Request/Response
class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=6, max_length=100)
    full_name: str = Field(..., min_length=1, max_length=100)
    department: str | None = Field(default="General", max_length=100)
    position: str | None = Field(default="Employee", max_length=100)
    role: str | None = Field(default="employee")

class SendOtpRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=6, max_length=100)
    full_name: str = Field(..., min_length=1, max_length=100)
    department: str | None = Field(default="General", max_length=100)
    position: str | None = Field(default="Employee", max_length=100)

class VerifyOtpRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    otp_code: str = Field(..., min_length=6, max_length=10)

class ResendOtpRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)

class LoginRequest(BaseModel):
    email: str
    password: str

class UserRoleUpdateRequest(BaseModel):
    role: str = Field(..., description="Role: super_admin, tech_member, dept_lead, dept_agent, employee")
    department: str | None = None
    position: str | None = None
    can_manage_departments: bool | None = None


class DepartmentCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: str = Field(default="")

class DepartmentRestrictionRequest(BaseModel):
    user_id: int
    reason: str | None = Field(default="Restricted by manager")

class TicketCreateRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=255)
    description: str = Field(default="")
    priority: str = Field(default="medium")
    department_id: int | None = None
    department_name: str | None = None
    requester_email: str | None = None
    requester_name: str | None = None

class TicketUpdateRequest(BaseModel):
    status: str | None = None
    priority: str | None = None
    assigned_to: int | None = None
    department_id: int | None = None

class CommentCreateRequest(BaseModel):
    comment_text: str = Field(..., min_length=1)
    is_internal: bool = True

# Token Security Dependency
security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token payload.",
        )
    
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT id, email, full_name, role, department, position, can_manage_departments, created_at 
                FROM ticketing_system.users 
                WHERE id = %s;
                """, 
                (user_id,)
            )
            user = cur.fetchone()
    except Exception as e:
        logger.error(f"Database error during token validation: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database connection error."
        )
        
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account no longer exists.",
        )
        
    if user.get("created_at"):
        user["created_at"] = user["created_at"].isoformat()
        
    return user

def is_super_admin(role: str) -> bool:
    return role in ("super_admin", "admin")

def is_dept_lead(user: dict) -> bool:
    """Returns True if user is a department admin lead or super admin."""
    role = user.get("role", "")
    if is_super_admin(role):
        return True
    return role in ("dept_lead", "admin_lead") or bool(user.get("can_manage_departments", False))

def is_staff(role: str) -> bool:
    """Returns True if user is an internal staff member (not regular employee requester)."""
    return role in ("super_admin", "admin", "tech_member", "agent", "dept_lead", "admin_lead", "dept_agent", "dept_member")

def is_tech_or_agent(role: str) -> bool:
    return is_staff(role)

def require_super_admin(current_user: dict = Depends(get_current_user)):
    if not is_super_admin(current_user.get("role", "")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super Admin / Developer Team Lead access required for this action."
        )
    return current_user

def require_tech_access(current_user: dict = Depends(get_current_user)):
    if not is_staff(current_user.get("role", "")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Staff access required (Super Admin, Tech Member, or Department Lead/Agent)."
        )
    return current_user

def user_has_department_access(user: dict, ticket_dept_id: int | None, ticket_dept_name: str | None) -> bool:
    """
    Strict department access check:
    - Super Admin: full access to all departments.
    - Developer Team Member: access to IT / Tech / DevOps tickets.
    - Department Lead / Staff: strictly isolated to their own department.
    """
    user_role = user.get("role", "")
    if is_super_admin(user_role):
        return True

    t_name = (ticket_dept_name or "").strip().lower()

    # Developer team members have access to Software Development / Engineering, IT, DevOps tickets
    if user_role == "tech_member":
        if any(tech_kw in t_name for tech_kw in ("software development", "software", "development", "engineering", "information technology", "tech", "devops", "it")):
            return True
        user_dept = (user.get("department") or "").strip().lower()
        clean_user_dept = user_dept.split('(')[0].strip()
        if clean_user_dept and (clean_user_dept in t_name or t_name in clean_user_dept):
            return True
        return False

    # Department Admin Lead or Department Staff: strict match to their department
    user_dept = (user.get("department") or "General").strip().lower()
    clean_user_dept = user_dept.split('(')[0].strip()
    if not clean_user_dept or not t_name:
        return False
    return clean_user_dept in t_name or t_name in clean_user_dept


def generate_ticket_code(cur) -> str:
    cur.execute("SELECT MAX(id) as max_id FROM ticketing_system.tickets;")
    res = cur.fetchone()
    next_id = (res["max_id"] or 0) + 1
    return f"TICK-{next_id:05d}"

# --- Exception Handlers ---

@app.exception_handler(psycopg2.OperationalError)
async def db_operational_exception_handler(request, exc):
    logger.error(f"PostgreSQL OperationalError during request to {request.url.path}: {exc}")
    return JSONResponse(
        status_code=503,
        content={
            "status": "database_error",
            "detail": "Database connection unavailable. Please ensure DATABASE_URL is properly configured in your Vercel Environment Variables.",
            "error": str(exc)
        }
    )

@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    logger.error(f"Unhandled error on {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "detail": f"Internal server error: {str(exc)}"
        }
    )


# --- System & Health Routes ---

@app.get("/", include_in_schema=False)
def root_index():
    for candidate in [
        os.path.join(ROOT_DIR, "public", "index.html"),
        os.path.join(ROOT_DIR, "frontend", "index.html"),
    ]:
        if os.path.exists(candidate):
            return FileResponse(candidate)
    return {"name": "Khin Ticket API", "status": "online", "docs": "/docs"}

@app.get("/portal.html", include_in_schema=False)
def portal_page():
    for candidate in [
        os.path.join(ROOT_DIR, "public", "portal.html"),
        os.path.join(ROOT_DIR, "frontend", "portal.html"),
    ]:
        if os.path.exists(candidate):
            return FileResponse(candidate)
    return RedirectResponse(url="/portal", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

@app.get("/dashboard.html", include_in_schema=False)
def dashboard_page():
    for candidate in [
        os.path.join(ROOT_DIR, "public", "dashboard.html"),
        os.path.join(ROOT_DIR, "frontend", "dashboard.html"),
    ]:
        if os.path.exists(candidate):
            return FileResponse(candidate)
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

@app.get("/api")
def api_root():
    return {
        "name": "Khin Ticket API",
        "status": "online",
        "docs": "/docs"
    }

@app.get("/api/health")
def health_check():
    db_status = "unknown"
    db_error = None
    try:
        with get_db_cursor() as cur:
            cur.execute("SELECT 1 as ping;")
            res = cur.fetchone()
            if res and res.get("ping") == 1:
                db_status = "connected"
    except Exception as e:
        db_status = "disconnected"
        db_error = str(e)
        logger.error(f"Health check database ping failed: {e}")

    return {
        "status": "ok" if db_status == "connected" else "degraded",
        "database": db_status,
        "database_error": db_error,
        "vercel_environment": bool(os.environ.get("VERCEL")),
        "database_configured": bool(os.environ.get("DATABASE_URL"))
    }

@app.get("/portal", include_in_schema=False)
def portal_redirect():
    return RedirectResponse(url="/portal.html", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

@app.get("/dashboard", include_in_schema=False)
def dashboard_redirect():
    return RedirectResponse(url="/dashboard.html", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

# --- Auth Routes ---


@app.post("/api/auth/send-registration-otp")
def send_registration_otp(req: SendOtpRequest):
    email = req.email.strip().lower()
    full_name = req.full_name.strip()
    department = (req.department or "General").strip()
    position = (req.position or "Employee").strip()
    
    if not full_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Full name is required.")
    if len(req.password) < 6:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 6 characters.")

    try:
        with get_db_cursor(commit=True) as cur:
            # Check if user already exists and is verified
            cur.execute("SELECT id, is_verified FROM ticketing_system.users WHERE email = %s;", (email,))
            existing = cur.fetchone()
            if existing and existing.get("is_verified", True):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="An account with this email address already exists. Please sign in."
                )

            # Throttle requests: 30 seconds cooldown between sending codes
            cur.execute(
                """
                SELECT created_at FROM ticketing_system.email_verifications
                WHERE email = %s AND purpose = 'registration' AND created_at > (NOW() - INTERVAL '30 seconds')
                ORDER BY created_at DESC LIMIT 1;
                """,
                (email,)
            )
            if cur.fetchone():
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Please wait 30 seconds before requesting another verification code."
                )

            otp_code = f"{secrets.randbelow(900000) + 100000}"
            pwd_hash = hash_password(req.password)

            payload = {
                "full_name": full_name,
                "department": department,
                "position": position,
                "password_hash": pwd_hash
            }

            # Delete any prior registration OTPs for this email address
            cur.execute(
                "DELETE FROM ticketing_system.email_verifications WHERE email = %s AND purpose = 'registration';",
                (email,)
            )

            cur.execute(
                """
                INSERT INTO ticketing_system.email_verifications 
                (email, otp_code, purpose, payload, attempts, expires_at)
                VALUES (%s, %s, 'registration', %s, 0, NOW() + INTERVAL '10 minutes');
                """,
                (email, otp_code, json.dumps(payload))
            )

        # Dispatch email via Gmail SMTP
        email_res = send_otp_email(to_email=email, recipient_name=full_name, otp_code=otp_code)
        
        response_payload = {
            "success": True,
            "message": f"Verification code sent to {email}. Please check your inbox.",
            "email": email,
            "expires_in_minutes": 10
        }
        if email_res.get("dev_code"):
            response_payload["dev_code"] = email_res["dev_code"]

        return response_payload
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Send registration OTP error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to send verification code: {str(e)}"
        )


@app.post("/api/auth/verify-registration-otp", status_code=status.HTTP_201_CREATED)
def verify_registration_otp(req: VerifyOtpRequest):
    email = req.email.strip().lower()
    otp_input = req.otp_code.strip().replace(" ", "")

    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                """
                SELECT id, otp_code, payload, attempts, expires_at 
                FROM ticketing_system.email_verifications 
                WHERE email = %s AND purpose = 'registration' 
                ORDER BY created_at DESC LIMIT 1;
                """,
                (email,)
            )
            record = cur.fetchone()
            if not record:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No pending verification code found for this email. Please request a new code."
                )

            # Check expiration
            cur.execute("SELECT NOW() > %s as is_expired;", (record["expires_at"],))
            exp_check = cur.fetchone()
            if exp_check and exp_check["is_expired"]:
                cur.execute("DELETE FROM ticketing_system.email_verifications WHERE id = %s;", (record["id"],))
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Verification code has expired. Please request a new code."
                )

            # Check attempt limit
            attempts = record.get("attempts") or 0
            if attempts >= 5:
                cur.execute("DELETE FROM ticketing_system.email_verifications WHERE id = %s;", (record["id"],))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many invalid attempts. Please request a new verification code."
                )

            if record["otp_code"].strip() != otp_input:
                cur.execute("UPDATE ticketing_system.email_verifications SET attempts = attempts + 1 WHERE id = %s;", (record["id"],))
                remaining = 4 - attempts
                if remaining <= 0:
                    cur.execute("DELETE FROM ticketing_system.email_verifications WHERE id = %s;", (record["id"],))
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Invalid code. Maximum attempts reached. Please request a new code."
                    )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Incorrect verification code. {remaining} attempt(s) remaining."
                )

            # Code verified! Unpack payload and create or activate user account
            cur.execute("SELECT id, email, full_name, role, department, position, can_manage_departments, is_verified, created_at FROM ticketing_system.users WHERE email = %s;", (email,))
            existing_user = cur.fetchone()

            raw_payload = record.get("payload")
            payload = json.loads(raw_payload) if isinstance(raw_payload, str) else (raw_payload or {})

            full_name = payload.get("full_name", "").strip() or (existing_user.get("full_name", "") if existing_user else "")
            department = payload.get("department", "General").strip() or (existing_user.get("department", "General") if existing_user else "General")
            position = payload.get("position", "Employee").strip() or (existing_user.get("position", "Employee") if existing_user else "Employee")
            pwd_hash = payload.get("password_hash")

            if existing_user:
                if pwd_hash:
                    cur.execute(
                        """
                        UPDATE ticketing_system.users 
                        SET is_verified = TRUE, password_hash = %s, full_name = %s, department = %s, position = %s
                        WHERE email = %s
                        RETURNING id, email, full_name, role, department, position, can_manage_departments, is_verified, created_at;
                        """,
                        (pwd_hash, full_name or existing_user["full_name"], department, position, email)
                    )
                else:
                    cur.execute(
                        """
                        UPDATE ticketing_system.users 
                        SET is_verified = TRUE
                        WHERE email = %s
                        RETURNING id, email, full_name, role, department, position, can_manage_departments, is_verified, created_at;
                        """,
                        (email,)
                    )
                new_user = cur.fetchone()
            else:
                if not pwd_hash:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Registration session expired or corrupted. Please fill out the registration form again."
                    )
                cur.execute(
                    """
                    INSERT INTO ticketing_system.users 
                    (email, password_hash, full_name, role, department, position, can_manage_departments, is_verified) 
                    VALUES (%s, %s, %s, 'employee', %s, %s, FALSE, TRUE)
                    ON CONFLICT (email) DO UPDATE 
                    SET password_hash = EXCLUDED.password_hash,
                        full_name = EXCLUDED.full_name,
                        department = EXCLUDED.department,
                        position = EXCLUDED.position,
                        is_verified = TRUE
                    RETURNING id, email, full_name, role, department, position, can_manage_departments, is_verified, created_at;
                    """,
                    (email, pwd_hash, full_name, department, position)
                )
                new_user = cur.fetchone()

            if new_user.get("created_at"):
                new_user["created_at"] = new_user["created_at"].isoformat()

            # Delete used verification records
            cur.execute("DELETE FROM ticketing_system.email_verifications WHERE email = %s;", (email,))

            # Issue JWT token for immediate seamless login
            token_data = {"sub": str(new_user["id"]), "email": new_user["email"], "role": new_user["role"]}
            access_token = create_access_token(data=token_data)

            return {
                "message": "Account email verified and registered successfully!",
                "access_token": access_token,
                "token_type": "bearer",
                "user": new_user
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Verify registration OTP error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Verification failed: {str(e)}"
        )


@app.post("/api/auth/resend-registration-otp")
def resend_registration_otp(req: ResendOtpRequest):
    email = req.email.strip().lower()

    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                """
                SELECT id, payload, created_at 
                FROM ticketing_system.email_verifications 
                WHERE email = %s
                ORDER BY created_at DESC LIMIT 1;
                """,
                (email,)
            )
            record = cur.fetchone()

            if not record:
                # Check if unverified user exists in ticketing_system.users
                cur.execute("SELECT id, full_name, is_verified FROM ticketing_system.users WHERE email = %s;", (email,))
                user = cur.fetchone()
                if not user:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="No pending registration found for this email. Please fill out the registration form."
                    )
                if user.get("is_verified", True):
                    return {"message": "Account is already verified. You can sign in immediately."}

                otp_code = f"{secrets.randbelow(900000) + 100000}"
                cur.execute(
                    """
                    INSERT INTO ticketing_system.email_verifications 
                    (email, otp_code, purpose, attempts, expires_at)
                    VALUES (%s, %s, 'registration', 0, NOW() + INTERVAL '10 minutes')
                    RETURNING id;
                    """,
                    (email, otp_code)
                )
                full_name = user["full_name"]
            else:
                # Check 30s cooldown
                cur.execute(
                    """
                    SELECT created_at FROM ticketing_system.email_verifications
                    WHERE id = %s AND created_at > (NOW() - INTERVAL '30 seconds');
                    """,
                    (record["id"],)
                )
                if cur.fetchone():
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="Please wait 30 seconds before requesting another code."
                    )

                otp_code = f"{secrets.randbelow(900000) + 100000}"
                cur.execute(
                    """
                    UPDATE ticketing_system.email_verifications 
                    SET otp_code = %s, attempts = 0, created_at = NOW(), expires_at = NOW() + INTERVAL '10 minutes'
                    WHERE id = %s;
                    """,
                    (otp_code, record["id"])
                )

                raw_payload = record.get("payload")
                payload = json.loads(raw_payload) if isinstance(raw_payload, str) else (raw_payload or {})
                full_name = payload.get("full_name") or "User"

        email_res = send_otp_email(to_email=email, recipient_name=full_name, otp_code=otp_code)
        
        response_payload = {
            "success": True,
            "message": f"A new verification code has been sent to {email}.",
            "email": email,
            "expires_in_minutes": 10
        }
        if email_res.get("dev_code"):
            response_payload["dev_code"] = email_res["dev_code"]

        return response_payload
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Resend registration OTP error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to resend verification code: {str(e)}"
        )


@app.post("/api/auth/register", status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest, request: Request):
    email = req.email.strip().lower()
    full_name = req.full_name.strip()
    department = (req.department or "General").strip()
    position = (req.position or "Employee").strip()
    final_role = "employee"
    can_manage_depts = False

    pwd_hash = hash_password(req.password)

    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute("SELECT id, is_verified FROM ticketing_system.users WHERE email = %s;", (email,))
            existing = cur.fetchone()
            if existing and existing.get("is_verified", True):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="An account with this email address already exists. Please sign in."
                )

            # Insert or update user account with is_verified = FALSE (must verify via email)
            cur.execute(
                """
                INSERT INTO ticketing_system.users 
                (email, password_hash, full_name, role, department, position, can_manage_departments, is_verified) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE) 
                ON CONFLICT (email) DO UPDATE 
                SET password_hash = EXCLUDED.password_hash,
                    full_name = EXCLUDED.full_name,
                    department = EXCLUDED.department,
                    position = EXCLUDED.position,
                    is_verified = FALSE
                RETURNING id, email, full_name, role, department, position, can_manage_departments, is_verified, created_at;
                """,
                (email, pwd_hash, full_name, final_role, department, position, can_manage_depts)
            )
            new_user = cur.fetchone()
            if new_user.get("created_at"):
                new_user["created_at"] = new_user["created_at"].isoformat()

            # Generate secure verification token and numeric OTP code
            token = secrets.token_urlsafe(32)
            otp_code = f"{secrets.randbelow(900000) + 100000}"

            cur.execute("DELETE FROM ticketing_system.email_verifications WHERE email = %s;", (email,))
            cur.execute(
                """
                INSERT INTO ticketing_system.email_verifications 
                (email, otp_code, token, purpose, attempts, expires_at)
                VALUES (%s, %s, %s, 'registration', 0, NOW() + INTERVAL '24 hours');
                """,
                (email, otp_code, token)
            )

        # Build verification URL based on host header
        client_host = request.headers.get("origin") or f"http://{request.headers.get('host', f'{HOST}:{PORT}')}"
        verification_url = f"{client_host}/api/auth/verify-email?token={token}"

        # Dispatch verification email via Gmail SMTP
        email_res = send_otp_email(
            to_email=email,
            recipient_name=full_name,
            otp_code=otp_code,
            verification_url=verification_url
        )

        return {
            "message": f"Account created! We've sent a verification link to {email}. Please verify your account in your email inbox before logging in.",
            "email": email,
            "is_verified": False,
            "user": new_user,
            "dev_code": email_res.get("dev_code")
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Registration error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to register user. Database error: {str(e)}"
        )


@app.get("/api/auth/verify-email")
def verify_email_via_link(token: str, request: Request):
    clean_token = token.strip()
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                """
                SELECT id, email, expires_at FROM ticketing_system.email_verifications 
                WHERE token = %s AND (purpose = 'registration' OR purpose IS NULL)
                ORDER BY created_at DESC LIMIT 1;
                """,
                (clean_token,)
            )
            record = cur.fetchone()
            if not record:
                return RedirectResponse(url="/index.html?verify_status=invalid", status_code=status.HTTP_303_SEE_OTHER)

            cur.execute("SELECT NOW() > %s as is_expired;", (record["expires_at"],))
            exp_check = cur.fetchone()
            if exp_check and exp_check["is_expired"]:
                return RedirectResponse(
                    url=f"/index.html?verify_status=expired&email={record['email']}", 
                    status_code=status.HTTP_303_SEE_OTHER
                )

            # Activate user account
            cur.execute("UPDATE ticketing_system.users SET is_verified = TRUE WHERE email = %s;", (record["email"],))
            cur.execute("DELETE FROM ticketing_system.email_verifications WHERE email = %s;", (record["email"],))

        return RedirectResponse(
            url=f"/index.html?verify_status=success&email={record['email']}", 
            status_code=status.HTTP_303_SEE_OTHER
        )
    except Exception as e:
        logger.error(f"Link verification error: {e}")
        return RedirectResponse(url="/index.html?verify_status=error", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/api/auth/resend-verification")
def resend_verification_email(req: ResendOtpRequest, request: Request):
    email = req.email.strip().lower()
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute("SELECT id, full_name, is_verified FROM ticketing_system.users WHERE email = %s;", (email,))
            user = cur.fetchone()
            if not user:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No account found with this email address.")
            if user.get("is_verified", True):
                return {"message": "Account is already verified. You can sign in immediately."}

            # Check 30s throttling
            cur.execute(
                """
                SELECT created_at FROM ticketing_system.email_verifications
                WHERE email = %s AND created_at > (NOW() - INTERVAL '30 seconds');
                """,
                (email,)
            )
            if cur.fetchone():
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Please wait 30 seconds before requesting another verification email."
                )

            token = secrets.token_urlsafe(32)
            otp_code = f"{secrets.randbelow(900000) + 100000}"

            cur.execute("DELETE FROM ticketing_system.email_verifications WHERE email = %s;", (email,))
            cur.execute(
                """
                INSERT INTO ticketing_system.email_verifications 
                (email, otp_code, token, purpose, attempts, expires_at)
                VALUES (%s, %s, %s, 'registration', 0, NOW() + INTERVAL '24 hours');
                """,
                (email, otp_code, token)
            )

        client_host = request.headers.get("origin") or f"http://{request.headers.get('host', f'{HOST}:{PORT}')}"
        verification_url = f"{client_host}/api/auth/verify-email?token={token}"

        email_res = send_otp_email(
            to_email=email,
            recipient_name=user["full_name"],
            otp_code=otp_code,
            verification_url=verification_url
        )

        return {
            "success": True,
            "message": f"A new verification link has been sent to {email}. Please check your email.",
            "email": email,
            "dev_code": email_res.get("dev_code")
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Resend verification error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to resend verification email: {str(e)}"
        )

@app.post("/api/auth/login")
def login(req: LoginRequest):
    email = req.email.strip().lower()

    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute("SELECT * FROM ticketing_system.users WHERE email = %s;", (email,))
            user = cur.fetchone()
    except Exception as e:
        logger.error(f"Login database error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database connection failed: {str(e)}"
        )


    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password."
        )

    if user.get("is_verified") is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account email address has not been verified yet. Please complete verification."
        )

    # Normalize legacy roles for active token session
    user_role = user["role"]
    if user_role == "admin":
        user_role = "super_admin"
    elif user_role == "agent":
        user_role = "tech_member"
    elif user_role in ("customer", "user"):
        user_role = "employee"

    token_data = {"sub": str(user["id"]), "email": user["email"], "role": user_role}
    access_token = create_access_token(data=token_data)

    user_data = {
        "id": user["id"],
        "email": user["email"],
        "full_name": user["full_name"],
        "role": user_role,
        "department": user.get("department", "General"),
        "position": user.get("position", "Employee"),
        "can_manage_departments": bool(user.get("can_manage_departments", False)),
        "is_verified": bool(user.get("is_verified", True)),
        "created_at": user["created_at"].isoformat() if user.get("created_at") else None
    }

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user_data
    }

@app.get("/api/auth/me")
def get_me(current_user: dict = Depends(get_current_user)):
    return {
        "user": current_user
    }

# --- Users & Hierarchy Management Endpoints (Super Admin / Manager Only) ---

@app.get("/api/users")
def list_users(current_user: dict = Depends(get_current_user)):
    """
    Returns users for hierarchy and permission management.
    - Super Admin: sees all users across the company.
    - Department Admin Lead: sees employees belonging to their department.
    """
    user_role = current_user.get("role", "employee")
    is_super = is_super_admin(user_role)
    is_lead = is_dept_lead(current_user)

    if not (is_super or is_lead):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Only Super Admin or Department Admin Leads can view the users directory."
        )

    try:
        with get_db_cursor(commit=False) as cur:
            if is_super:
                cur.execute(
                    """
                    SELECT id, email, full_name, role, department, position, can_manage_departments, created_at 
                    FROM ticketing_system.users 
                    ORDER BY id ASC;
                    """
                )
            else:
                user_dept = (current_user.get("department") or "").strip()
                dept_keyword = user_dept.split('(')[0].strip()
                cur.execute(
                    """
                    SELECT id, email, full_name, role, department, position, can_manage_departments, created_at 
                    FROM ticketing_system.users 
                    WHERE lower(department) LIKE lower(%s)
                    ORDER BY id ASC;
                    """,
                    (f"%{dept_keyword}%",)
                )
            users = cur.fetchall()
            for u in users:
                if u.get("created_at"):
                    u["created_at"] = u["created_at"].isoformat()
            return {"users": users}
    except Exception as e:
        logger.error(f"Failed to list users: {e}")
        raise HTTPException(status_code=500, detail="Failed to list users.")

@app.patch("/api/users/{user_id}/role")
def update_user_role(
    user_id: int, 
    req: UserRoleUpdateRequest, 
    current_user: dict = Depends(get_current_user)
):
    """
    Allows Super Admin / Manager to update hierarchy, promote tech members to super_admin,
    or adjust department management access.
    Allows Department Admin Leads to manage accessibility for employees within their department.
    """
    caller_role = current_user.get("role", "employee")
    is_super = is_super_admin(caller_role)
    is_lead = is_dept_lead(current_user)

    if not (is_super or is_lead):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Super Admin or Department Admin Leads can update user roles."
        )

    valid_roles = ("super_admin", "tech_member", "dept_lead", "admin_lead", "dept_agent", "dept_member", "employee", "admin", "agent", "customer")
    target_role = req.role.strip().lower()
    if target_role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {valid_roles}")

    if target_role == "admin":
        target_role = "super_admin"
    elif target_role in ("agent", "tech"):
        target_role = "tech_member"
    elif target_role == "admin_lead":
        target_role = "dept_lead"
    elif target_role in ("customer", "user"):
        target_role = "employee"

    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute("SELECT id, email, role, department, position, can_manage_departments FROM ticketing_system.users WHERE id = %s;", (user_id,))
            target = cur.fetchone()
            if not target:
                raise HTTPException(status_code=404, detail="User not found.")

            if not is_super:
                caller_dept = (current_user.get("department") or "").strip().lower().split('(')[0].strip()
                target_dept = (target.get("department") or "").strip().lower().split('(')[0].strip()
                if not caller_dept or (caller_dept not in target_dept and target_dept not in caller_dept):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Department Admin Leads can only manage employees belonging to their own department."
                    )
                if target_role in ("super_admin", "tech_member", "dept_lead"):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Only the Developer Team Lead (Super Admin) can assign Developer or Lead roles."
                    )
                can_manage = target.get("can_manage_departments", False)
                new_dept = target["department"]
            else:
                can_manage = req.can_manage_departments
                if target_role in ("super_admin", "dept_lead"):
                    can_manage = True
                elif can_manage is None:
                    can_manage = target.get("can_manage_departments", False)
                new_dept = req.department.strip() if req.department else target["department"]

            new_pos = req.position.strip() if req.position else target["position"]

            cur.execute(
                """
                UPDATE ticketing_system.users 
                SET role = %s, can_manage_departments = %s, department = %s, position = %s 
                WHERE id = %s 
                RETURNING id, email, full_name, role, department, position, can_manage_departments, created_at;
                """,
                (target_role, can_manage, new_dept, new_pos, user_id)
            )
            updated_user = cur.fetchone()
            if updated_user.get("created_at"):
                updated_user["created_at"] = updated_user["created_at"].isoformat()
            return {"message": "User hierarchy updated successfully", "user": updated_user}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update user role: {e}")
        raise HTTPException(status_code=500, detail="Failed to update user role.")

@app.patch("/api/departments/members/{user_id}")
def update_department_member_accessibility(
    user_id: int,
    req: UserRoleUpdateRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Allows a Department Admin Lead (or Super Admin) to assign/change accessibility
    for employees that are part of that specific department.
    """
    caller_role = current_user.get("role", "employee")
    is_super = is_super_admin(caller_role)
    is_lead = is_dept_lead(current_user)

    if not (is_super or is_lead):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Only the Super Admin or Department Admin Lead can manage department employee accessibility."
        )

    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute("SELECT id, email, role, department, position, can_manage_departments FROM ticketing_system.users WHERE id = %s;", (user_id,))
            target = cur.fetchone()
            if not target:
                raise HTTPException(status_code=404, detail="User not found.")

            # If Department Lead, verify target user belongs to their department
            if not is_super:
                caller_dept = (current_user.get("department") or "").strip().lower().split('(')[0].strip()
                target_dept = (target.get("department") or "").strip().lower().split('(')[0].strip()
                if not caller_dept or (caller_dept not in target_dept and target_dept not in caller_dept):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Department Admin Leads can only manage employees belonging to their own department."
                    )

                # Department leads cannot promote anyone to super_admin or tech_member
                if req.role and req.role in ("super_admin", "admin", "tech_member"):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Only the Developer Team Lead (Super Admin) can assign Developer or Super Admin roles."
                    )

            target_role = req.role.strip().lower() if req.role else target["role"]
            new_pos = req.position.strip() if req.position else target["position"]
            new_dept = req.department.strip() if (req.department and is_super) else target["department"]
            can_manage = req.can_manage_departments if is_super else target.get("can_manage_departments", False)

            cur.execute(
                """
                UPDATE ticketing_system.users 
                SET role = %s, department = %s, position = %s, can_manage_departments = %s 
                WHERE id = %s 
                RETURNING id, email, full_name, role, department, position, can_manage_departments, created_at;
                """,
                (target_role, new_dept, new_pos, can_manage, user_id)
            )
            updated_user = cur.fetchone()
            if updated_user.get("created_at"):
                updated_user["created_at"] = updated_user["created_at"].isoformat()
            return {"message": "Department member accessibility updated successfully", "user": updated_user}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update department member accessibility: {e}")
        raise HTTPException(status_code=500, detail="Failed to update department member.")


# --- Department Management Endpoints ---

@app.get("/api/departments")
def list_departments(current_user: dict = Depends(get_current_user)):
    """
    Returns list of departments.
    - Regular employees only see departments they have NOT been restricted from.
    - Tech and super admins see all departments with ticket counts.
    """
    user_role = current_user.get("role", "employee")
    user_id = current_user["id"]

    try:
        with get_db_cursor(commit=False) as cur:
            if not is_tech_or_agent(user_role):
                cur.execute(
                    """
                    SELECT d.id, d.name, d.description, d.is_active, d.created_at
                    FROM ticketing_system.departments d
                    WHERE d.is_active = TRUE
                      AND d.id NOT IN (
                          SELECT department_id FROM ticketing_system.department_restrictions WHERE user_id = %s
                      )
                    ORDER BY d.name ASC;
                    """,
                    (user_id,)
                )
            else:
                cur.execute(
                    """
                    SELECT 
                        d.id, d.name, d.description, d.is_active, d.created_at,
                        COUNT(DISTINCT t.id) as ticket_count,
                        COUNT(DISTINCT r.id) as restricted_count
                    FROM ticketing_system.departments d
                    LEFT JOIN ticketing_system.tickets t ON d.id = t.department_id
                    LEFT JOIN ticketing_system.department_restrictions r ON d.id = r.department_id
                    GROUP BY d.id
                    ORDER BY d.name ASC;
                    """
                )
            depts = cur.fetchall()
            for d in depts:
                if d.get("created_at"):
                    d["created_at"] = d["created_at"].isoformat()
            return {"departments": depts}
    except Exception as e:
        logger.error(f"Failed to list departments: {e}")
        raise HTTPException(status_code=500, detail="Failed to list departments.")

@app.post("/api/departments", status_code=status.HTTP_201_CREATED)
def create_department(req: DepartmentCreateRequest, current_user: dict = Depends(get_current_user)):
    """
    Creates a new department.
    Allowed for: Super Admin / Tech Manager, OR tech members with can_manage_departments = True.
    """
    user_role = current_user.get("role", "employee")
    can_manage = current_user.get("can_manage_departments", False)

    if not (is_super_admin(user_role) or can_manage):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied. Only Super Admin or authorized Tech Members can add departments."
        )

    dept_name = req.name.strip()
    if not dept_name:
        raise HTTPException(status_code=400, detail="Department name cannot be empty.")

    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute("SELECT id FROM ticketing_system.departments WHERE lower(name) = lower(%s);", (dept_name,))
            if cur.fetchone():
                raise HTTPException(status_code=400, detail="A department with this name already exists.")

            cur.execute(
                """
                INSERT INTO ticketing_system.departments (name, description)
                VALUES (%s, %s)
                RETURNING id, name, description, is_active, created_at;
                """,
                (dept_name, req.description.strip())
            )
            dept = cur.fetchone()
            if dept.get("created_at"):
                dept["created_at"] = dept["created_at"].isoformat()
            return {"message": "Department created successfully", "department": dept}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create department: {e}")
        raise HTTPException(status_code=500, detail="Failed to create department.")

@app.get("/api/departments/{dept_id}/restrictions")
def get_department_restrictions(dept_id: int, current_user: dict = Depends(require_super_admin)):
    """Super Admin: get all users restricted from raising tickets to this department."""
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT 
                    r.id, r.user_id, r.department_id, r.reason, r.created_at,
                    u.full_name, u.email, u.department as user_department, u.position
                FROM ticketing_system.department_restrictions r
                JOIN ticketing_system.users u ON r.user_id = u.id
                WHERE r.department_id = %s
                ORDER BY r.created_at DESC;
                """,
                (dept_id,)
            )
            restrictions = cur.fetchall()
            for r in restrictions:
                if r.get("created_at"):
                    r["created_at"] = r["created_at"].isoformat()
            return {"restrictions": restrictions}
    except Exception as e:
        logger.error(f"Failed to fetch restrictions: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch department restrictions.")

@app.post("/api/departments/{dept_id}/restrictions")
def add_department_restriction(
    dept_id: int, 
    req: DepartmentRestrictionRequest, 
    current_user: dict = Depends(require_super_admin)
):
    """Super Admin: restrict a user from raising tickets to this department."""
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute("SELECT id FROM ticketing_system.departments WHERE id = %s;", (dept_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Department not found.")

            cur.execute("SELECT id FROM ticketing_system.users WHERE id = %s;", (req.user_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="User not found.")

            cur.execute(
                """
                INSERT INTO ticketing_system.department_restrictions (user_id, department_id, restricted_by, reason)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, department_id) DO UPDATE SET reason = EXCLUDED.reason
                RETURNING id, user_id, department_id, reason, created_at;
                """,
                (req.user_id, dept_id, current_user["id"], req.reason or "Restricted by manager")
            )
            record = cur.fetchone()
            if record.get("created_at"):
                record["created_at"] = record["created_at"].isoformat()
            return {"message": "User restricted successfully from department", "restriction": record}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to restrict user: {e}")
        raise HTTPException(status_code=500, detail="Failed to restrict user.")

@app.delete("/api/departments/{dept_id}/restrictions/{user_id}")
def remove_department_restriction(dept_id: int, user_id: int, current_user: dict = Depends(require_super_admin)):
    """Super Admin: unrestrict a user from raising tickets to this department."""
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                "DELETE FROM ticketing_system.department_restrictions WHERE department_id = %s AND user_id = %s RETURNING id;",
                (dept_id, user_id)
            )
            deleted = cur.fetchone()
            if not deleted:
                raise HTTPException(status_code=404, detail="Restriction record not found.")
            return {"message": "Restriction lifted successfully."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to remove restriction: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove restriction.")

# --- Team Members Endpoint ---

@app.get("/api/team")
def get_team_members(dept_id: int | None = None, current_user: dict = Depends(get_current_user)):
    """
    Returns list of team members for ticket assignment.
    - Super Admin: sees all staff across departments (or filtered by dept_id).
    - Department Admin Lead & Department Staff: ONLY sees employees belonging to their department.
    - Developer Team Member: sees developer / IT team members.
    """
    user_role = current_user.get("role", "employee")
    if not is_staff(user_role):
        raise HTTPException(status_code=403, detail="Employees cannot view internal team members.")

    try:
        with get_db_cursor(commit=False) as cur:
            if is_super_admin(user_role):
                if dept_id:
                    cur.execute(
                        """
                        SELECT id, email, full_name, role, department, position, can_manage_departments, created_at 
                        FROM ticketing_system.users 
                        WHERE department = (SELECT name FROM ticketing_system.departments WHERE id = %s)
                           OR role IN ('super_admin', 'admin', 'tech_member', 'dept_lead', 'dept_agent', 'dept_member')
                        ORDER BY full_name ASC;
                        """,
                        (dept_id,)
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, email, full_name, role, department, position, can_manage_departments, created_at 
                        FROM ticketing_system.users 
                        WHERE role IN ('super_admin', 'admin', 'tech_member', 'agent', 'dept_lead', 'admin_lead', 'dept_agent', 'dept_member') 
                        ORDER BY full_name ASC;
                        """
                    )
            elif is_dept_lead(current_user) or user_role in ("dept_agent", "dept_member"):
                user_dept = (current_user.get("department") or "General").strip()
                dept_keyword = user_dept.split('(')[0].strip()
                dept_pattern = f"%{dept_keyword}%"
                cur.execute(
                    """
                    SELECT id, email, full_name, role, department, position, can_manage_departments, created_at 
                    FROM ticketing_system.users 
                    WHERE lower(department) LIKE lower(%s)
                    ORDER BY full_name ASC;
                    """,
                    (dept_pattern,)
                )
            else:
                cur.execute(
                    """
                    SELECT id, email, full_name, role, department, position, can_manage_departments, created_at 
                    FROM ticketing_system.users 
                    WHERE role IN ('super_admin', 'admin', 'tech_member')
                       OR lower(department) LIKE %s
                       OR lower(department) LIKE %s
                       OR lower(department) LIKE %s
                       OR lower(department) LIKE %s
                       OR lower(department) LIKE %s
                       OR lower(department) LIKE %s
                    ORDER BY full_name ASC;
                    """,
                    (
                        "%software%",
                        "%engineering%",
                        "%development%",
                        "%information technology%",
                        "%devops%",
                        "%tech%"
                    )
                )

            members = cur.fetchall()
            for m in members:
                if m.get("created_at"):
                    m["created_at"] = m["created_at"].isoformat()
            return {"members": members}
    except Exception as e:
        logger.error(f"Failed to fetch team members: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch team members.")

# --- Ticket Management Endpoints ---

@app.get("/api/tickets")
def list_tickets(
    status_filter: str | None = None,
    priority_filter: str | None = None,
    dept_filter: int | None = None,
    current_user: dict = Depends(get_current_user)
):
    query = """
        SELECT 
            t.id, t.ticket_code, t.title, t.description, t.requester_email, 
            t.requester_name, t.requester_id, t.department_id, t.department_name,
            t.source, t.status, t.priority, t.assigned_to,
            t.created_at, t.updated_at,
            u.full_name AS assignee_name, u.email AS assignee_email,
            d.name as resolved_dept_name
        FROM ticketing_system.tickets t
        LEFT JOIN ticketing_system.users u ON t.assigned_to = u.id
        LEFT JOIN ticketing_system.departments d ON t.department_id = d.id
        WHERE 1=1
    """
    params = []
    user_role = current_user.get("role", "employee")

    if is_super_admin(user_role):
        # Super Admin: Sees ALL tickets across ALL departments
        pass
    elif user_role == "tech_member":
        # Developer team member: ONLY see Software / IT / DevOps / Tech tickets, or tickets assigned to / raised by them
        user_dept = (current_user.get("department") or "Software Development / Engineering").strip()
        dept_keyword = user_dept.split('(')[0].strip()
        dept_pattern = f"%{dept_keyword}%"
        query += """ AND (
            t.assigned_to = %s 
            OR t.requester_id = %s 
            OR lower(t.requester_email) = lower(%s)
            OR lower(t.department_name) LIKE lower(%s)
            OR lower(d.name) LIKE lower(%s)
            OR lower(t.department_name) LIKE %s
            OR lower(t.department_name) LIKE %s
            OR lower(t.department_name) LIKE %s
            OR lower(t.department_name) LIKE %s
            OR lower(t.department_name) LIKE %s
            OR lower(t.department_name) LIKE %s
        )"""
        params.extend([
            current_user["id"],
            current_user["id"],
            current_user["email"],
            dept_pattern,
            dept_pattern,
            "%software%",
            "%engineering%",
            "%development%",
            "%information technology%",
            "%devops%",
            "%tech%"
        ])
    elif is_dept_lead(current_user) or user_role in ("dept_agent", "dept_member"):
        # Department Admin Lead or Department Staff: STRICTLY see tickets belonging to their own department, or assigned to/raised by them!
        user_dept = (current_user.get("department") or "General").strip()
        dept_keyword = user_dept.split('(')[0].strip()
        dept_pattern = f"%{dept_keyword}%"
        query += """ AND (
            t.assigned_to = %s 
            OR t.requester_id = %s 
            OR lower(t.requester_email) = lower(%s)
            OR lower(t.department_name) LIKE lower(%s)
            OR lower(d.name) LIKE lower(%s)
        )"""
        params.extend([current_user["id"], current_user["id"], current_user["email"], dept_pattern, dept_pattern])
    else:
        # Non-developer regular employee: ONLY see tickets they raised!
        query += " AND (t.requester_id = %s OR lower(t.requester_email) = lower(%s))"
        params.extend([current_user["id"], current_user["email"]])


    if status_filter:
        query += " AND t.status = %s"
        params.append(status_filter.lower())

    if priority_filter:
        query += " AND t.priority = %s"
        params.append(priority_filter.lower())

    if dept_filter:
        query += " AND t.department_id = %s"
        params.append(dept_filter)

    query += " ORDER BY t.created_at DESC;"

    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute(query, tuple(params))
            tickets = cur.fetchall()
            for item in tickets:
                if item.get("created_at"):
                    item["created_at"] = item["created_at"].isoformat()
                if item.get("updated_at"):
                    item["updated_at"] = item["updated_at"].isoformat()
                if item.get("resolved_dept_name"):
                    item["department_name"] = item["resolved_dept_name"]
            return {"tickets": tickets}
    except Exception as e:
        logger.error(f"Error fetching tickets: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve tickets.")

@app.post("/api/tickets", status_code=status.HTTP_201_CREATED)
def create_ticket(req: TicketCreateRequest, current_user: dict = Depends(get_current_user)):
    user_role = current_user.get("role", "employee")
    
    if not is_tech_or_agent(user_role):
        requester_email = current_user["email"]
        requester_name = current_user["full_name"]
        requester_id = current_user["id"]
    else:
        requester_email = (req.requester_email or current_user["email"]).strip().lower()
        requester_name = (req.requester_name or current_user["full_name"]).strip()
        requester_id = current_user["id"]

    dept_id = req.department_id
    dept_name = (req.department_name or "Information Technology").strip()

    try:
        with get_db_cursor(commit=True) as cur:
            if dept_id:
                cur.execute("SELECT id, name FROM ticketing_system.departments WHERE id = %s AND is_active = TRUE;", (dept_id,))
                drow = cur.fetchone()
                if not drow:
                    raise HTTPException(status_code=400, detail="Selected department is invalid or inactive.")
                dept_name = drow["name"]

                cur.execute(
                    "SELECT reason FROM ticketing_system.department_restrictions WHERE user_id = %s AND department_id = %s;",
                    (current_user["id"], dept_id)
                )
                restriction = cur.fetchone()
                if restriction:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"You are restricted from raising tickets to {dept_name}. Reason: {restriction.get('reason', 'Contact Manager')}"
                    )
            else:
                cur.execute("SELECT id, name FROM ticketing_system.departments WHERE lower(name) = lower(%s);", (dept_name,))
                drow = cur.fetchone()
                if drow:
                    dept_id = drow["id"]
                    dept_name = drow["name"]
                    cur.execute(
                        "SELECT reason FROM ticketing_system.department_restrictions WHERE user_id = %s AND department_id = %s;",
                        (current_user["id"], dept_id)
                    )
                    if cur.fetchone():
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail=f"You are restricted from raising tickets to {dept_name}."
                        )

            ticket_code = generate_ticket_code(cur)
            cur.execute(
                """
                INSERT INTO ticketing_system.tickets 
                (ticket_code, title, description, requester_email, requester_name, requester_id, department_id, department_name, source, priority, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'portal', %s, 'open')
                RETURNING id, ticket_code, title, description, requester_email, requester_name, requester_id, department_id, department_name, source, priority, status, created_at;
                """,
                (
                    ticket_code,
                    req.title.strip(),
                    req.description.strip(),
                    requester_email,
                    requester_name,
                    requester_id,
                    dept_id,
                    dept_name,
                    req.priority.lower()
                )
            )
            ticket = cur.fetchone()
            if ticket.get("created_at"):
                ticket["created_at"] = ticket["created_at"].isoformat()
            return {"message": "Ticket raised successfully", "ticket": ticket}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating ticket: {e}")
        raise HTTPException(status_code=500, detail="Failed to create ticket.")

@app.get("/api/tickets/{ticket_id}")
def get_ticket_details(ticket_id: int, current_user: dict = Depends(get_current_user)):
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT 
                    t.id, t.ticket_code, t.title, t.description, t.requester_email, 
                    t.requester_name, t.requester_id, t.department_id, t.department_name,
                    t.source, t.status, t.priority, t.assigned_to,
                    t.created_at, t.updated_at,
                    u.full_name AS assignee_name, u.email AS assignee_email,
                    d.name as resolved_dept_name
                FROM ticketing_system.tickets t
                LEFT JOIN ticketing_system.users u ON t.assigned_to = u.id
                LEFT JOIN ticketing_system.departments d ON t.department_id = d.id
                WHERE t.id = %s;
                """,
                (ticket_id,)
            )
            ticket = cur.fetchone()
            if not ticket:
                raise HTTPException(status_code=404, detail="Ticket not found.")
            
            user_role = current_user.get("role", "employee")

            # Check permissions
            if is_super_admin(user_role):
                pass
            elif is_staff(user_role):
                is_own = (ticket.get("requester_id") == current_user["id"] or (ticket.get("requester_email") or "").lower() == current_user["email"].lower())
                is_assignee = (ticket.get("assigned_to") == current_user["id"])
                has_access = user_has_department_access(current_user, ticket.get("department_id"), ticket.get("department_name") or ticket.get("resolved_dept_name"))
                if not (is_own or is_assignee or has_access):
                    raise HTTPException(status_code=403, detail="You do not have permission to view tickets outside your department.")
            else:
                if ticket.get("requester_id") != current_user["id"] and ticket.get("requester_email") != current_user["email"]:
                    raise HTTPException(status_code=403, detail="You can only view your own tickets.")

            if ticket.get("created_at"):
                ticket["created_at"] = ticket["created_at"].isoformat()
            if ticket.get("updated_at"):
                ticket["updated_at"] = ticket["updated_at"].isoformat()
            if ticket.get("resolved_dept_name"):
                ticket["department_name"] = ticket["resolved_dept_name"]

            # For employees, hide internal tech notes!
            internal_filter = "AND c.is_internal = FALSE" if not is_staff(user_role) else ""

            cur.execute(
                f"""
                SELECT 
                    c.id, c.comment_text, c.is_internal, c.created_at,
                    u.full_name AS author_name, u.email AS author_email, u.role AS author_role
                FROM ticketing_system.ticket_comments c
                LEFT JOIN ticketing_system.users u ON c.user_id = u.id
                WHERE c.ticket_id = %s {internal_filter}
                ORDER BY c.created_at ASC;
                """,
                (ticket_id,)
            )
            comments = cur.fetchall()
            for c in comments:
                if c.get("created_at"):
                    c["created_at"] = c["created_at"].isoformat()

            ticket["comments"] = comments
            return {"ticket": ticket}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching ticket details: {e}")
        raise HTTPException(status_code=500, detail="Failed to load ticket details.")

@app.patch("/api/tickets/{ticket_id}")
def update_ticket(
    ticket_id: int, 
    req: TicketUpdateRequest, 
    current_user: dict = Depends(get_current_user)
):
    user_role = current_user.get("role", "employee")
    user_id = current_user["id"]

    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute("SELECT * FROM ticketing_system.tickets WHERE id = %s;", (ticket_id,))
            ticket = cur.fetchone()
            if not ticket:
                raise HTTPException(status_code=404, detail="Ticket not found.")

            updates = []
            params = []

            # 1. Regular Employees: Can ONLY close or open their own tickets!
            if not is_staff(user_role):
                if ticket.get("requester_id") != user_id and ticket.get("requester_email") != current_user["email"]:
                    raise HTTPException(status_code=403, detail="You can only manage your own tickets.")

                if req.assigned_to is not None or req.priority is not None or req.department_id is not None:
                    raise HTTPException(status_code=403, detail="Employees can only close or reopen their tickets.")

                if req.status:
                    stat = req.status.lower()
                    if stat in ("open", "closed"):
                        updates.append("status = %s")
                        params.append(stat)
                    else:
                        raise HTTPException(status_code=400, detail="Employees can only set status to 'open' or 'closed'.")

            # 2. Staff Roles (Super Admin, Tech Member, Department Admin Lead, Department Staff):
            else:
                is_super = is_super_admin(user_role)
                is_lead = is_dept_lead(current_user)

                # Department permission check
                if not is_super:
                    is_own = (ticket.get("requester_id") == user_id or (ticket.get("requester_email") or "").lower() == current_user["email"].lower())
                    is_assignee = (ticket.get("assigned_to") == user_id)
                    has_access = user_has_department_access(current_user, ticket.get("department_id"), ticket.get("department_name"))
                    if not (is_own or is_assignee or has_access):
                        raise HTTPException(status_code=403, detail="You cannot modify tickets outside your department.")

                # Status update
                if req.status:
                    valid_statuses = ("open", "in_progress", "resolved", "closed")
                    if req.status.lower() in valid_statuses:
                        updates.append("status = %s")
                        params.append(req.status.lower())
                    else:
                        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {valid_statuses}")

                # Priority update
                if req.priority:
                    valid_priorities = ("low", "medium", "high", "urgent")
                    if req.priority.lower() in valid_priorities:
                        updates.append("priority = %s")
                        params.append(req.priority.lower())

                # Department re-routing (Super Admin only)
                if req.department_id is not None and is_super:
                    cur.execute("SELECT id, name FROM ticketing_system.departments WHERE id = %s;", (req.department_id,))
                    dept_row = cur.fetchone()
                    if dept_row:
                        updates.append("department_id = %s")
                        params.append(dept_row["id"])
                        updates.append("department_name = %s")
                        params.append(dept_row["name"])

                # Assignment update:
                if req.assigned_to is not None:
                    if is_super:
                        if req.assigned_to == 0:
                            updates.append("assigned_to = NULL")
                        else:
                            cur.execute("SELECT id FROM ticketing_system.users WHERE id = %s;", (req.assigned_to,))
                            if cur.fetchone():
                                updates.append("assigned_to = %s")
                                params.append(req.assigned_to)
                            else:
                                raise HTTPException(status_code=404, detail="Assignee user not found.")
                    elif is_lead:
                        if req.assigned_to == 0:
                            updates.append("assigned_to = NULL")
                        else:
                            cur.execute("SELECT id, full_name, department, role FROM ticketing_system.users WHERE id = %s;", (req.assigned_to,))
                            assignee = cur.fetchone()
                            if not assignee:
                                raise HTTPException(status_code=404, detail="Assignee user not found.")

                            # Ensure assignee belongs to this department!
                            t_dept = (ticket.get("department_name") or "").strip().lower().split('(')[0].strip()
                            u_dept = (assignee.get("department") or "").strip().lower().split('(')[0].strip()

                            if t_dept and u_dept and (t_dept not in u_dept and u_dept not in t_dept):
                                raise HTTPException(
                                    status_code=status.HTTP_403_FORBIDDEN,
                                    detail=f"Department Admin Leads can only assign tickets to members within their own department ({ticket.get('department_name')})."
                                )

                            updates.append("assigned_to = %s")
                            params.append(req.assigned_to)
                    else:
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the Super Admin or Department Admin Lead can assign tickets."
                        )

                    params.append(req.department_id)

            if not updates:
                return {"message": "No updates applied."}

            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(ticket_id)

            set_clause = ", ".join(updates)
            cur.execute(
                f"""
                UPDATE ticketing_system.tickets 
                SET {set_clause} 
                WHERE id = %s 
                RETURNING id, ticket_code, status, priority, assigned_to, department_id, department_name, updated_at;
                """, 
                tuple(params)
            )
            updated_ticket = cur.fetchone()
            if updated_ticket.get("updated_at"):
                updated_ticket["updated_at"] = updated_ticket["updated_at"].isoformat()

            return {"message": "Ticket updated successfully", "ticket": updated_ticket}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating ticket {ticket_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update ticket.")

@app.delete("/api/tickets/{ticket_id}")
def delete_ticket(ticket_id: int, current_user: dict = Depends(get_current_user)):
    """Enforces transparency: tickets cannot be deleted by employees or agents."""
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Tickets cannot be deleted. History remains preserved for transparency and audit trails."
    )

@app.post("/api/tickets/{ticket_id}/comments", status_code=status.HTTP_201_CREATED)
def add_comment(
    ticket_id: int, 
    req: CommentCreateRequest, 
    current_user: dict = Depends(get_current_user)
):
    user_role = current_user.get("role", "employee")
    
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute("SELECT id, requester_id, requester_email, assigned_to, department_id, department_name FROM ticketing_system.tickets WHERE id = %s;", (ticket_id,))
            ticket = cur.fetchone()
            if not ticket:
                raise HTTPException(status_code=404, detail="Ticket not found.")

            if not is_tech_or_agent(user_role):
                if ticket.get("requester_id") != current_user["id"] and ticket.get("requester_email") != current_user["email"]:
                    raise HTTPException(status_code=403, detail="You can only comment on your own tickets.")
                is_internal = False
            elif is_super_admin(user_role):
                is_internal = req.is_internal
            else:
                # Dept leads, dept staff, tech members
                is_own = (ticket.get("requester_id") == current_user["id"] or (ticket.get("requester_email") or "").lower() == current_user["email"].lower())
                is_assignee = (ticket.get("assigned_to") == current_user["id"])
                has_access = user_has_department_access(current_user, ticket.get("department_id"), ticket.get("department_name"))
                if not (is_own or is_assignee or has_access):
                    raise HTTPException(status_code=403, detail="You cannot comment on tickets outside your department.")
                is_internal = req.is_internal

            cur.execute(
                """
                INSERT INTO ticketing_system.ticket_comments (ticket_id, user_id, comment_text, is_internal)
                VALUES (%s, %s, %s, %s)
                RETURNING id, comment_text, is_internal, created_at;
                """,
                (ticket_id, current_user["id"], req.comment_text.strip(), is_internal)
            )
            new_comment = cur.fetchone()
            if new_comment.get("created_at"):
                new_comment["created_at"] = new_comment["created_at"].isoformat()
            new_comment["author_name"] = current_user["full_name"]
            new_comment["author_email"] = current_user["email"]
            new_comment["author_role"] = user_role

            return {"message": "Comment added successfully", "comment": new_comment}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding comment to ticket {ticket_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to add comment.")

@app.get("/api/dashboard/stats")
def get_dashboard_stats(current_user: dict = Depends(get_current_user)):
    try:
        with get_db_cursor(commit=False) as cur:
            user_role = current_user.get("role", "employee")
            
            if is_super_admin(user_role):
                cur.execute("SELECT COUNT(*) as total FROM ticketing_system.tickets;")
                total = cur.fetchone()["total"]
                cur.execute("SELECT COUNT(*) as open FROM ticketing_system.tickets WHERE status = 'open';")
                open_count = cur.fetchone()["open"]
                cur.execute("SELECT COUNT(*) as in_progress FROM ticketing_system.tickets WHERE status = 'in_progress';")
                in_progress = cur.fetchone()["in_progress"]
                cur.execute("SELECT COUNT(*) as resolved FROM ticketing_system.tickets WHERE status IN ('resolved', 'closed');")
                resolved = cur.fetchone()["resolved"]
                cur.execute("SELECT COUNT(*) as unassigned FROM ticketing_system.tickets WHERE assigned_to IS NULL;")
                unassigned = cur.fetchone()["unassigned"]

                cur.execute("SELECT COUNT(*) as team_count FROM ticketing_system.users WHERE role IN ('super_admin', 'admin', 'tech_member', 'dept_lead', 'admin_lead', 'dept_agent', 'dept_member');")
                team_count = cur.fetchone()["team_count"]

                cur.execute("SELECT COUNT(*) as dept_count FROM ticketing_system.departments WHERE is_active = TRUE;")
                dept_count = cur.fetchone()["dept_count"]
            elif user_role == "tech_member":
                user_dept = (current_user.get("department") or "Software Development / Engineering").strip()
                dept_keyword = user_dept.split('(')[0].strip()
                dept_pattern = f"%{dept_keyword}%"
                cur.execute(
                    """
                    SELECT 
                        COUNT(*) as total,
                        COUNT(CASE WHEN t.status = 'open' THEN 1 END) as open,
                        COUNT(CASE WHEN t.status = 'in_progress' THEN 1 END) as in_progress,
                        COUNT(CASE WHEN t.status IN ('resolved', 'closed') THEN 1 END) as resolved,
                        COUNT(CASE WHEN t.assigned_to IS NULL THEN 1 END) as unassigned
                    FROM ticketing_system.tickets t
                    LEFT JOIN ticketing_system.departments d ON t.department_id = d.id
                    WHERE t.assigned_to = %s 
                       OR t.requester_id = %s 
                       OR lower(t.requester_email) = lower(%s)
                       OR lower(t.department_name) LIKE lower(%s)
                       OR lower(d.name) LIKE lower(%s)
                       OR lower(t.department_name) LIKE %s
                       OR lower(t.department_name) LIKE %s
                       OR lower(t.department_name) LIKE %s
                       OR lower(t.department_name) LIKE %s
                       OR lower(t.department_name) LIKE %s
                       OR lower(t.department_name) LIKE %s;
                    """,
                    (
                        current_user["id"],
                        current_user["id"],
                        current_user["email"],
                        dept_pattern,
                        dept_pattern,
                        "%software%",
                        "%engineering%",
                        "%development%",
                        "%information technology%",
                        "%devops%",
                        "%tech%"
                    )
                )
                stats = cur.fetchone()
                total = stats["total"] or 0
                open_count = stats["open"] or 0
                in_progress = stats["in_progress"] or 0
                resolved = stats["resolved"] or 0
                unassigned = stats["unassigned"] or 0

                cur.execute("SELECT COUNT(*) as team_count FROM ticketing_system.users WHERE role IN ('super_admin', 'admin', 'tech_member');")
                team_count = cur.fetchone()["team_count"]
                dept_count = 1
            elif is_dept_lead(current_user) or user_role in ("dept_agent", "dept_member"):
                user_dept = (current_user.get("department") or "General").strip()
                dept_keyword = user_dept.split('(')[0].strip()
                dept_pattern = f"%{dept_keyword}%"
                cur.execute(
                    """
                    SELECT 
                        COUNT(*) as total,
                        COUNT(CASE WHEN t.status = 'open' THEN 1 END) as open,
                        COUNT(CASE WHEN t.status = 'in_progress' THEN 1 END) as in_progress,
                        COUNT(CASE WHEN t.status IN ('resolved', 'closed') THEN 1 END) as resolved,
                        COUNT(CASE WHEN t.assigned_to IS NULL THEN 1 END) as unassigned
                    FROM ticketing_system.tickets t
                    LEFT JOIN ticketing_system.departments d ON t.department_id = d.id
                    WHERE t.assigned_to = %s 
                       OR t.requester_id = %s 
                       OR lower(t.requester_email) = lower(%s)
                       OR lower(t.department_name) LIKE lower(%s)
                       OR lower(d.name) LIKE lower(%s);
                    """,
                    (current_user["id"], current_user["id"], current_user["email"], dept_pattern, dept_pattern)
                )
                stats = cur.fetchone()
                total = stats["total"] or 0
                open_count = stats["open"] or 0
                in_progress = stats["in_progress"] or 0
                resolved = stats["resolved"] or 0
                unassigned = stats["unassigned"] or 0

                cur.execute(
                    "SELECT COUNT(*) as team_count FROM ticketing_system.users WHERE lower(department) LIKE lower(%s);",
                    (dept_pattern,)
                )
                team_count = cur.fetchone()["team_count"]
                dept_count = 1
            else:
                user_id = current_user["id"]
                user_email = current_user["email"]
                cur.execute("SELECT COUNT(*) as total FROM ticketing_system.tickets WHERE requester_id = %s OR lower(requester_email) = lower(%s);", (user_id, user_email))
                total = cur.fetchone()["total"]
                cur.execute("SELECT COUNT(*) as open FROM ticketing_system.tickets WHERE (requester_id = %s OR lower(requester_email) = lower(%s)) AND status = 'open';", (user_id, user_email))
                open_count = cur.fetchone()["open"]
                cur.execute("SELECT COUNT(*) as in_progress FROM ticketing_system.tickets WHERE (requester_id = %s OR lower(requester_email) = lower(%s)) AND status = 'in_progress';", (user_id, user_email))
                in_progress = cur.fetchone()["in_progress"]
                cur.execute("SELECT COUNT(*) as resolved FROM ticketing_system.tickets WHERE (requester_id = %s OR lower(requester_email) = lower(%s)) AND status IN ('resolved', 'closed');", (user_id, user_email))
                resolved = cur.fetchone()["resolved"]
                unassigned = 0
                team_count = 0
                dept_count = 1

            return {
                "total": total,
                "open": open_count,
                "in_progress": in_progress,
                "resolved": resolved,
                "unassigned": unassigned,
                "team_members": team_count,
                "departments_count": dept_count,
                "user_role": user_role
            }
    except Exception as e:
        logger.error(f"Error fetching dashboard stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch dashboard metrics.")

@app.post("/api/tickets/sync-emails")
def sync_emails(current_user: dict = Depends(get_current_user)):
    user_role = current_user.get("role", "employee")
    if not is_tech_or_agent(user_role):
        raise HTTPException(status_code=403, detail="Employees cannot trigger email sync.")
    from backend.email_service import sync_gmail_tickets
    result = sync_gmail_tickets(mark_as_read=True)
    return result

# Serving frontend static files
for base_folder in ["public", "frontend"]:
    static_dir = os.path.join(ROOT_DIR, base_folder)
    if os.path.exists(static_dir):
        for sub in ["css", "js", "images"]:
            subpath = os.path.join(static_dir, sub)
            if os.path.exists(subpath):
                app.mount(f"/{sub}", StaticFiles(directory=subpath), name=f"{base_folder}_{sub}")
        if not os.environ.get("VERCEL"):
            app.mount("/", StaticFiles(directory=static_dir, html=True), name=f"{base_folder}_root")
        logger.info(f"Mounted static files from: {static_dir}")
        break

if __name__ == "__main__":
    import uvicorn
    print(f"[INFO] Starting Khin Ticket backend on http://{HOST}:{PORT}")
    print(f"[INFO] Frontend accessible at http://{HOST}:{PORT}")
    print(f"[INFO] API documentation at http://{HOST}:{PORT}/docs")
    uvicorn.run("backend.main:app", host=HOST, port=PORT, reload=True)

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
from datetime import datetime, timedelta, timezone
from fastapi.responses import RedirectResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from backend.config import HOST, PORT, TECH_ACCESS_KEY, GMAIL_USER, GMAIL_APP_PASSWORD, OTP_EXPIRE_MINUTES
from backend.database import init_db_pool, close_db_pool, initialize_database, get_db_cursor
from backend.auth import hash_password, verify_password, create_access_token, decode_access_token
from backend.email_service import send_otp_email, get_department_email

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
            logger.info(f"Background Gmail ticket ingestion active (polling {GMAIL_USER} continuously every 15s).")
            # Immediate check on startup
            try:
                await asyncio.to_thread(sync_gmail_tickets, limit=15, mark_as_read=True)
            except Exception as e:
                logger.warning(f"Initial startup email sync warning: {e}")

            while True:
                try:
                    await asyncio.sleep(15)
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

# CORS Middleware (allows local Go Live server, Live Server ports, and direct files to connect seamlessly)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:[0-9]+)?$",
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
    role: str = Field(..., description="Role: super_admin, tech_member, dept_lead, internal, external")
    department: str | None = None
    position: str | None = None
    can_manage_departments: bool | None = None


class DepartmentCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: str = Field(default="")

class DepartmentRestrictionRequest(BaseModel):
    user_id: int
    reason: str | None = Field(default="Restricted by manager")

class UserDepartmentAccessRequest(BaseModel):
    restricted_department_ids: list[int] = Field(default_factory=list)
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

# Employee Hub Pydantic Models
class AttendanceActionRequest(BaseModel):
    action: str = Field(..., pattern="^(clock_in|lunch_out|lunch_in|clock_out)$")
    notes: str | None = None

class TimeEditCreateRequest(BaseModel):
    target_date: str
    edit_type: str
    requested_clock_in: str | None = None
    requested_lunch_start: str | None = None
    requested_lunch_end: str | None = None
    requested_clock_out: str | None = None
    reason: str = Field(..., min_length=3)
    proof_attachment: str | None = None
    proof_file_name: str | None = None

class TimeEditUpdateRequest(BaseModel):
    status: str = Field(..., pattern="^(pending|approved|rejected|cancelled)$")
    reviewer_notes: str | None = None

class OvertimeCreateRequest(BaseModel):
    ot_date: str
    ot_type: str = Field(default="regular_day")
    start_time: str
    end_time: str
    total_hours: float
    task_purpose: str = Field(..., min_length=3)
    proof_attachment: str | None = None
    proof_file_name: str | None = None

class OvertimeUpdateRequest(BaseModel):
    status: str = Field(..., pattern="^(pending|approved|rejected|cancelled)$")
    reviewer_notes: str | None = None

class DocumentSubmitRequest(BaseModel):
    file_name: str | None = None
    file_data: str | None = None
    notes: str | None = None

class DocumentStatusUpdateRequest(BaseModel):
    status: str = Field(..., pattern="^(pending|under_review|verified|rejected)$")
    notes: str | None = None

class EmployeeProfileUpdateRequest(BaseModel):
    avatar_url: str | None = None
    full_name: str | None = None
    phone_number: str | None = None
    position: str | None = None
    work_location: str | None = None
    emergency_contact: str | None = None

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
            if str(user_id).isdigit():
                cur.execute(
                    """
                    SELECT id, email, full_name, role, department, position, can_manage_departments, created_at, avatar_url, employee_id,
                           phone_number, employment_type, work_location, date_hired, emergency_contact 
                    FROM ticketing_system.users 
                    WHERE id = %s;
                    """, 
                    (int(user_id),)
                )
            else:
                cur.execute(
                    """
                    SELECT id, email, full_name, role, department, position, can_manage_departments, created_at, avatar_url, employee_id,
                           phone_number, employment_type, work_location, date_hired, emergency_contact 
                    FROM ticketing_system.users 
                    WHERE lower(email) = lower(%s);
                    """, 
                    (str(user_id),)
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
        
    if user.get("date_hired"):
        user["date_hired"] = str(user["date_hired"])
    elif user.get("created_at"):
        try:
            user["date_hired"] = user["created_at"][:10]
        except Exception:
            user["date_hired"] = None

    if not user.get("employment_type"):
        user["employment_type"] = "Regular Full-Time"

    if not user.get("work_location"):
        user["work_location"] = "Main HQ / Hybrid"

    if not user.get("employee_id"):
        user["employee_id"] = f"EMP-{user['id']:05d}"
        
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
    """Returns True if user is an internal staff member (not regular external customer requester)."""
    return role in ("super_admin", "admin", "tech_member", "agent", "dept_lead", "admin_lead", "dept_agent", "dept_member", "internal", "internals")

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
    user_role = current_user.get("role", "")
    can_manage = bool(current_user.get("can_manage_departments", False))
    if not is_staff(user_role) and not can_manage:
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
@app.get("/index.html", include_in_schema=False)
def root_index():
    for candidate in [
        os.path.join(ROOT_DIR, "index.html"),
        os.path.join(ROOT_DIR, "public", "index.html"),
        os.path.join(ROOT_DIR, "frontend", "index.html"),
    ]:
        if os.path.exists(candidate):
            return FileResponse(candidate)
    return {"name": "Khin Ticket API", "status": "online", "docs": "/docs"}

@app.get("/portal", include_in_schema=False)
@app.get("/portal.html", include_in_schema=False)
def portal_page():
    for candidate in [
        os.path.join(ROOT_DIR, "portal.html"),
        os.path.join(ROOT_DIR, "public", "portal.html"),
        os.path.join(ROOT_DIR, "frontend", "portal.html"),
    ]:
        if os.path.exists(candidate):
            return FileResponse(candidate)
    raise HTTPException(status_code=404, detail="portal.html not found")

@app.get("/dashboard", include_in_schema=False)
@app.get("/dashboard.html", include_in_schema=False)
def dashboard_page():
    for candidate in [
        os.path.join(ROOT_DIR, "dashboard.html"),
        os.path.join(ROOT_DIR, "public", "dashboard.html"),
        os.path.join(ROOT_DIR, "frontend", "dashboard.html"),
    ]:
        if os.path.exists(candidate):
            return FileResponse(candidate)
    raise HTTPException(status_code=404, detail="dashboard.html not found")

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
        "created_at": user["created_at"].isoformat() if user.get("created_at") else None,
        "avatar_url": user.get("avatar_url"),
        "employee_id": user.get("employee_id") or f"EMP-{user['id']:05d}",
        "phone_number": user.get("phone_number"),
        "employment_type": user.get("employment_type") or "Regular Full-Time",
        "work_location": user.get("work_location") or "Main HQ / Hybrid",
        "date_hired": str(user.get("date_hired")) if user.get("date_hired") else (user["created_at"].date().isoformat() if user.get("created_at") else None),
        "emergency_contact": user.get("emergency_contact")
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

    valid_roles = (
        "super_admin", "tech_member", "dept_lead", "admin_lead",
        "internal", "internals", "dept_agent", "dept_member",
        "external", "externals", "employee", "admin", "agent", "customer", "user"
    )
    target_role = req.role.strip().lower()
    if target_role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {valid_roles}")

    if target_role == "admin":
        target_role = "super_admin"
    elif target_role in ("agent", "tech"):
        target_role = "tech_member"
    elif target_role == "admin_lead":
        target_role = "dept_lead"
    elif target_role in ("internal", "internals", "dept_agent", "dept_member"):
        target_role = "internal"
    elif target_role in ("external", "externals", "customer", "user"):
        target_role = "external"
    elif target_role == "employee":
        target_role = "internal"

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
                d["routing_email"] = get_department_email(d.get("name", ""))
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

@app.get("/api/department-restrictions")
def get_all_department_restrictions(current_user: dict = Depends(require_super_admin)):
    """Super Admin: get all user department restrictions across the company."""
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT r.id, r.user_id, r.department_id, r.reason, r.created_at,
                       d.name as department_name, u.full_name as user_name
                FROM ticketing_system.department_restrictions r
                LEFT JOIN ticketing_system.departments d ON r.department_id = d.id
                LEFT JOIN ticketing_system.users u ON r.user_id = u.id
                ORDER BY r.user_id, r.department_id;
                """
            )
            restrictions = cur.fetchall()
            for r in restrictions:
                if r.get("created_at"):
                    r["created_at"] = r["created_at"].isoformat()
            return {"restrictions": restrictions}
    except Exception as e:
        logger.error(f"Failed to fetch all restrictions: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch department restrictions.")

@app.put("/api/users/{user_id}/department-access")
def update_user_department_access(
    user_id: int,
    req: UserDepartmentAccessRequest,
    current_user: dict = Depends(require_super_admin)
):
    """Super Admin: batch update an employee's restricted departments."""
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute("SELECT id, full_name FROM ticketing_system.users WHERE id = %s;", (user_id,))
            user_row = cur.fetchone()
            if not user_row:
                raise HTTPException(status_code=404, detail="User not found.")

            # Remove existing restrictions for this user
            cur.execute("DELETE FROM ticketing_system.department_restrictions WHERE user_id = %s;", (user_id,))

            # Insert new restrictions if any
            valid_restricted_ids = []
            if req.restricted_department_ids:
                cur.execute("SELECT id FROM ticketing_system.departments WHERE id = ANY(%s);", (req.restricted_department_ids,))
                valid_ids_set = {r["id"] for r in cur.fetchall()}
                valid_restricted_ids = [d_id for d_id in req.restricted_department_ids if d_id in valid_ids_set]
                for dept_id in valid_restricted_ids:
                    cur.execute(
                        """
                        INSERT INTO ticketing_system.department_restrictions (user_id, department_id, restricted_by, reason)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (user_id, department_id) DO NOTHING;
                        """,
                        (user_id, dept_id, current_user["id"], req.reason or "Restricted by manager")
                    )
            return {
                "message": f"Updated department access for {user_row['full_name']}.",
                "user_id": user_id,
                "restricted_department_ids": valid_restricted_ids
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update department access for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update department access.")

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
                           OR role IN ('super_admin', 'admin', 'tech_member', 'dept_lead', 'dept_agent', 'dept_member', 'internal', 'internals')
                        ORDER BY full_name ASC;
                        """,
                        (dept_id,)
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, email, full_name, role, department, position, can_manage_departments, created_at 
                        FROM ticketing_system.users 
                        WHERE role IN ('super_admin', 'admin', 'tech_member', 'agent', 'dept_lead', 'admin_lead', 'dept_agent', 'dept_member', 'internal', 'internals') 
                        ORDER BY full_name ASC;
                        """
                    )
            elif is_dept_lead(current_user) or user_role in ("dept_agent", "dept_member", "internal", "internals"):
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
    raised_only: bool = False,
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

    if raised_only or user_role == "employee" or not is_staff(user_role):
        # In employee portal or for regular employee accounts:
        # STRICTLY retain ONLY tickets raised by that person/account!
        query += " AND (t.requester_id = %s OR lower(t.requester_email) = lower(%s))"
        params.extend([current_user["id"], current_user["email"]])
    elif is_super_admin(user_role):
        # Super Admin: Sees ALL tickets across ALL departments in tech dashboard
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
    elif is_dept_lead(current_user) or user_role in ("dept_agent", "dept_member", "staff", "internal", "internals"):
        # Department Members (Lead, Agent, Staff, or Member assigned to this department):
        # STRICTLY see tickets belonging to their own department, or assigned to / raised by them!
        user_dept = (current_user.get("department") or "").strip()
        if user_dept and user_dept.lower() != 'general':
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
            # If user has no specific department or General, only see tickets they raised or assigned to them
            query += " AND (t.assigned_to = %s OR t.requester_id = %s OR lower(t.requester_email) = lower(%s))"
            params.extend([current_user["id"], current_user["id"], current_user["email"]])
    else:
        # Fallback: ONLY see tickets they raised!
        query += " AND (t.requester_id = %s OR lower(t.requester_email) = lower(%s))"
        params.extend([current_user["id"], current_user["email"]])


    if status_filter:
        stat = status_filter.lower()
        if stat in ('waiting_on_us', 'in_progress'):
            query += " AND t.status IN ('waiting_on_us', 'in_progress')"
        elif stat in ('waiting_contact', 'waiting_on_them'):
            query += " AND t.status IN ('waiting_contact', 'waiting_on_them')"
        elif stat in ('closed', 'resolved'):
            query += " AND t.status IN ('closed', 'resolved')"
        else:
            query += " AND t.status = %s"
            params.append(stat)

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

            # 1. Regular Employees / Internals / Externals: Can update status of their own tickets!
            if not is_staff(user_role):
                if ticket.get("requester_id") != user_id and ticket.get("requester_email") != current_user["email"]:
                    raise HTTPException(status_code=403, detail="You can only manage your own tickets.")

                if req.assigned_to is not None or req.priority is not None or req.department_id is not None:
                    raise HTTPException(status_code=403, detail="Employees can only update status on their tickets.")

                if req.status:
                    stat = req.status.lower()
                    valid_emp_statuses = ("open", "in_progress", "waiting_on_us", "waiting_contact", "on_hold", "resolved", "closed")
                    if stat in valid_emp_statuses:
                        updates.append("status = %s")
                        params.append(stat)
                    else:
                        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {valid_emp_statuses}")

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
                    valid_statuses = ("open", "in_progress", "waiting_on_us", "waiting_contact", "on_hold", "resolved", "closed")
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
def get_dashboard_stats(
    raised_only: bool = False,
    current_user: dict = Depends(get_current_user)
):
    try:
        with get_db_cursor(commit=False) as cur:
            user_role = current_user.get("role", "employee")
            
            if raised_only or user_role == "employee" or not is_staff(user_role):
                user_id = current_user["id"]
                user_email = current_user["email"]
                cur.execute("SELECT COUNT(*) as total FROM ticketing_system.tickets WHERE requester_id = %s OR lower(requester_email) = lower(%s);", (user_id, user_email))
                total = cur.fetchone()["total"]
                cur.execute("SELECT COUNT(*) as open FROM ticketing_system.tickets WHERE (requester_id = %s OR lower(requester_email) = lower(%s)) AND status = 'open';", (user_id, user_email))
                open_count = cur.fetchone()["open"]
                cur.execute("SELECT COUNT(*) as in_progress FROM ticketing_system.tickets WHERE (requester_id = %s OR lower(requester_email) = lower(%s)) AND status IN ('in_progress', 'waiting_on_us', 'waiting_contact', 'on_hold');", (user_id, user_email))
                in_progress = cur.fetchone()["in_progress"]
                cur.execute("SELECT COUNT(*) as resolved FROM ticketing_system.tickets WHERE (requester_id = %s OR lower(requester_email) = lower(%s)) AND status IN ('resolved', 'closed');", (user_id, user_email))
                resolved = cur.fetchone()["resolved"]
                unassigned = 0
                team_count = 0
                dept_count = 1
            elif is_super_admin(user_role):
                cur.execute("SELECT COUNT(*) as total FROM ticketing_system.tickets;")
                total = cur.fetchone()["total"]
                cur.execute("SELECT COUNT(*) as open FROM ticketing_system.tickets WHERE status = 'open';")
                open_count = cur.fetchone()["open"]
                cur.execute("SELECT COUNT(*) as in_progress FROM ticketing_system.tickets WHERE status IN ('in_progress', 'waiting_on_us', 'waiting_contact', 'on_hold');")
                in_progress = cur.fetchone()["in_progress"]
                cur.execute("SELECT COUNT(*) as resolved FROM ticketing_system.tickets WHERE status IN ('resolved', 'closed');")
                resolved = cur.fetchone()["resolved"]
                cur.execute("SELECT COUNT(*) as unassigned FROM ticketing_system.tickets WHERE assigned_to IS NULL;")
                unassigned = cur.fetchone()["unassigned"]

                cur.execute("SELECT COUNT(*) as team_count FROM ticketing_system.users WHERE role IN ('super_admin', 'admin', 'tech_member', 'dept_lead', 'admin_lead', 'dept_agent', 'dept_member', 'internal', 'internals');")
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
                        COUNT(CASE WHEN t.status IN ('in_progress', 'waiting_on_us', 'waiting_contact', 'on_hold') THEN 1 END) as in_progress,
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
            elif is_dept_lead(current_user) or user_role in ("dept_agent", "dept_member", "internal", "internals"):
                user_dept = (current_user.get("department") or "General").strip()
                dept_keyword = user_dept.split('(')[0].strip()
                dept_pattern = f"%{dept_keyword}%"
                cur.execute(
                    """
                    SELECT 
                        COUNT(*) as total,
                        COUNT(CASE WHEN t.status = 'open' THEN 1 END) as open,
                        COUNT(CASE WHEN t.status IN ('in_progress', 'waiting_on_us', 'waiting_contact', 'on_hold') THEN 1 END) as in_progress,
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
                cur.execute("SELECT COUNT(*) as in_progress FROM ticketing_system.tickets WHERE (requester_id = %s OR lower(requester_email) = lower(%s)) AND status IN ('in_progress', 'waiting_on_us', 'waiting_contact', 'on_hold');", (user_id, user_email))
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

@app.get("/api/staff/performance")
def get_staff_performance(
    user_id: int | None = None,
    current_user: dict = Depends(get_current_user)
):
    user_role = current_user.get("role", "employee")
    if not is_tech_or_agent(user_role) and not current_user.get("can_manage_departments"):
        raise HTTPException(status_code=403, detail="Access denied. Staff members only.")

    target_user_id = current_user["id"]
    if user_id and is_super_admin(user_role):
        target_user_id = user_id

    try:
        with get_db_cursor(commit=False) as cur:
            # Fetch target user info
            cur.execute("SELECT id, full_name, email, role, department, position FROM ticketing_system.users WHERE id = %s;", (target_user_id,))
            target_user = cur.fetchone()
            if not target_user:
                raise HTTPException(status_code=404, detail="User not found.")

            # Handled tickets (tickets where this staff is assigned to support)
            cur.execute("""
                SELECT 
                    t.id, t.ticket_code, t.title, t.description, t.requester_email, 
                    t.requester_name, t.department_name, t.status, t.priority,
                    t.created_at, t.updated_at,
                    EXTRACT(EPOCH FROM (t.updated_at - t.created_at)) / 3600.0 AS resolution_hours
                FROM ticketing_system.tickets t
                WHERE t.assigned_to = %s
                ORDER BY t.created_at DESC;
            """, (target_user_id,))
            raw_handled = cur.fetchall() or []

            # Raised tickets (tickets created by this staff member - personal support history)
            cur.execute("""
                SELECT 
                    t.id, t.ticket_code, t.title, t.description, t.requester_email, 
                    t.requester_name, t.department_name, t.status, t.priority,
                    t.created_at, t.updated_at,
                    u.full_name AS assignee_name
                FROM ticketing_system.tickets t
                LEFT JOIN ticketing_system.users u ON t.assigned_to = u.id
                WHERE t.requester_id = %s OR lower(t.requester_email) = lower(%s)
                ORDER BY t.created_at DESC;
            """, (target_user_id, target_user["email"]))
            raw_raised = cur.fetchall() or []

            def serialize_ticket(t):
                row = dict(t)
                if row.get("created_at"):
                    row["created_at"] = row["created_at"].isoformat()
                if row.get("updated_at"):
                    row["updated_at"] = row["updated_at"].isoformat()
                if "resolution_hours" in row and row["resolution_hours"] is not None:
                    row["resolution_hours"] = round(float(row["resolution_hours"]), 2)
                return row

            handled_tickets = [serialize_ticket(t) for t in raw_handled]
            raised_tickets = [serialize_ticket(t) for t in raw_raised]

            total_handled = len(handled_tickets)
            resolved_count = sum(1 for t in handled_tickets if t["status"] in ("resolved", "closed"))
            in_progress_count = sum(1 for t in handled_tickets if t["status"] in ("in_progress", "waiting_on_us", "waiting_contact", "on_hold"))
            open_count = sum(1 for t in handled_tickets if t["status"] == "open")
            resolution_rate = round((resolved_count / total_handled * 100), 1) if total_handled > 0 else 0

            resolved_times = [
                t["resolution_hours"] for t in handled_tickets 
                if t["status"] in ("resolved", "closed") and t.get("resolution_hours") is not None and t["resolution_hours"] >= 0
            ]
            avg_resolution_hours = round(sum(resolved_times) / len(resolved_times), 1) if resolved_times else None

            status_counts = {
                "open": open_count,
                "in_progress": in_progress_count,
                "resolved": resolved_count,
                "closed": sum(1 for t in handled_tickets if t["status"] == "closed")
            }

            priority_counts = {
                "urgent": sum(1 for t in handled_tickets if (t.get("priority") or "").lower() == "urgent"),
                "high": sum(1 for t in handled_tickets if (t.get("priority") or "").lower() == "high"),
                "medium": sum(1 for t in handled_tickets if (t.get("priority") or "").lower() == "medium"),
                "low": sum(1 for t in handled_tickets if (t.get("priority") or "").lower() == "low")
            }

            dept_counts = {}
            for t in handled_tickets:
                dept_name = t.get("department_name") or "General"
                dept_counts[dept_name] = dept_counts.get(dept_name, 0) + 1

            today = datetime.now(timezone.utc).date()
            daily_activity = []
            for i in range(6, -1, -1):
                day = today - timedelta(days=i)
                day_str = day.strftime("%Y-%m-%d")
                day_label = day.strftime("%a")
                created_on_day = sum(1 for t in raw_handled if t.get("created_at") and t["created_at"].date() == day)
                resolved_on_day = sum(1 for t in raw_handled if t.get("updated_at") and t["updated_at"].date() == day and t["status"] in ("resolved", "closed"))
                daily_activity.append({
                    "date": day_str,
                    "label": day_label,
                    "created": created_on_day,
                    "resolved": resolved_on_day
                })

            return {
                "user": dict(target_user),
                "stats": {
                    "total_handled": total_handled,
                    "resolved_count": resolved_count,
                    "in_progress_count": in_progress_count,
                    "open_count": open_count,
                    "resolution_rate": resolution_rate,
                    "avg_resolution_hours": avg_resolution_hours,
                    "raised_count": len(raised_tickets)
                },
                "status_counts": status_counts,
                "priority_counts": priority_counts,
                "dept_counts": dept_counts,
                "daily_activity": daily_activity,
                "handled_tickets": handled_tickets,
                "raised_tickets": raised_tickets
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching staff performance: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch staff performance.")

@app.post("/api/tickets/sync-emails")
def sync_emails(current_user: dict = Depends(get_current_user)):
    user_role = current_user.get("role", "employee")
    if not is_tech_or_agent(user_role):
        raise HTTPException(status_code=403, detail="Employees cannot trigger email sync.")
    from backend.email_service import sync_gmail_tickets
    result = sync_gmail_tickets(mark_as_read=True)
    return result

# ============================================================================
# EMPLOYEE HUB: ATTENDANCE & SHIFT CLOCKING (9H WORKDAY WITH 1H LUNCH)
# ============================================================================

@app.get("/api/employee/attendance/today")
def get_today_attendance(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    today_str = datetime.now().date().isoformat()
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT id, user_id, work_date, clock_in, lunch_start, lunch_end, clock_out, total_hours, status, notes
                FROM ticketing_system.attendance_logs
                WHERE user_id = %s AND work_date = %s;
                """,
                (user_id, today_str)
            )
            row = cur.fetchone()
            
            if not row:
                return {
                    "work_date": today_str,
                    "status": "not_clocked_in",
                    "clock_in": None,
                    "lunch_start": None,
                    "lunch_end": None,
                    "clock_out": None,
                    "total_hours": 0.0,
                    "shift_duration_hours": 9.0,
                    "break_duration_hours": 1.0,
                    "notes": ""
                }
            
            return {
                "id": row["id"],
                "work_date": str(row["work_date"]),
                "status": row["status"] or "not_clocked_in",
                "clock_in": row["clock_in"].isoformat() if row.get("clock_in") else None,
                "lunch_start": row["lunch_start"].isoformat() if row.get("lunch_start") else None,
                "lunch_end": row["lunch_end"].isoformat() if row.get("lunch_end") else None,
                "clock_out": row["clock_out"].isoformat() if row.get("clock_out") else None,
                "total_hours": float(row["total_hours"] or 0.0),
                "shift_duration_hours": 9.0,
                "break_duration_hours": 1.0,
                "notes": row.get("notes") or ""
            }
    except Exception as e:
        logger.error(f"Error getting today's attendance: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch attendance.")

@app.post("/api/employee/attendance/action")
def attendance_action(req: AttendanceActionRequest, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    now = datetime.now(timezone.utc)
    today_date = datetime.now().date()
    action = req.action.lower()

    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                """
                SELECT * FROM ticketing_system.attendance_logs
                WHERE user_id = %s AND work_date = %s
                FOR UPDATE;
                """,
                (user_id, today_date)
            )
            record = cur.fetchone()

            if action == "clock_in":
                if record and record.get("clock_in"):
                    raise HTTPException(status_code=400, detail="You have already clocked in for today.")
                if not record:
                    cur.execute(
                        """
                        INSERT INTO ticketing_system.attendance_logs (user_id, work_date, clock_in, status, notes)
                        VALUES (%s, %s, %s, 'clocked_in', %s)
                        RETURNING *;
                        """,
                        (user_id, today_date, now, req.notes or "")
                    )
                else:
                    cur.execute(
                        """
                        UPDATE ticketing_system.attendance_logs
                        SET clock_in = %s, status = 'clocked_in', updated_at = %s, notes = COALESCE(%s, notes)
                        WHERE id = %s
                        RETURNING *;
                        """,
                        (now, now, req.notes, record["id"])
                    )

            elif action == "lunch_out":
                if not record or not record.get("clock_in"):
                    raise HTTPException(status_code=400, detail="You must clock in before taking lunch.")
                if record.get("lunch_start"):
                    raise HTTPException(status_code=400, detail="Lunch break has already been recorded.")
                if record.get("clock_out"):
                    raise HTTPException(status_code=400, detail="Shift already ended for today.")
                
                cur.execute(
                    """
                    UPDATE ticketing_system.attendance_logs
                    SET lunch_start = %s, status = 'on_lunch', updated_at = %s
                    WHERE id = %s
                    RETURNING *;
                    """,
                    (now, now, record["id"])
                )

            elif action == "lunch_in":
                if not record or not record.get("clock_in"):
                    raise HTTPException(status_code=400, detail="You must clock in first.")
                if not record.get("lunch_start"):
                    raise HTTPException(status_code=400, detail="Please tap Lunch Out before logging Lunch In.")
                if record.get("lunch_end"):
                    raise HTTPException(status_code=400, detail="Lunch break return has already been logged.")
                if record.get("clock_out"):
                    raise HTTPException(status_code=400, detail="Shift already ended for today.")
                
                cur.execute(
                    """
                    UPDATE ticketing_system.attendance_logs
                    SET lunch_end = %s, status = 'clocked_in', updated_at = %s
                    WHERE id = %s
                    RETURNING *;
                    """,
                    (now, now, record["id"])
                )

            elif action == "clock_out":
                if not record or not record.get("clock_in"):
                    raise HTTPException(status_code=400, detail="You have not clocked in today.")
                if record.get("clock_out"):
                    raise HTTPException(status_code=400, detail="You have already clocked out for today.")
                
                lunch_start = record.get("lunch_start")
                lunch_end = record.get("lunch_end")
                if lunch_start and not lunch_end:
                    lunch_end = now
                    cur.execute(
                        "UPDATE ticketing_system.attendance_logs SET lunch_end = %s WHERE id = %s;",
                        (now, record["id"])
                    )

                clock_in = record["clock_in"]
                total_seconds = (now - clock_in).total_seconds()
                
                if lunch_start and lunch_end:
                    lunch_seconds = (lunch_end - lunch_start).total_seconds()
                    worked_seconds = max(0, total_seconds - lunch_seconds)
                elif lunch_start and not lunch_end:
                    worked_seconds = max(0, total_seconds - 3600)
                else:
                    worked_seconds = total_seconds

                total_hrs = round(worked_seconds / 3600.0, 2)

                cur.execute(
                    """
                    UPDATE ticketing_system.attendance_logs
                    SET clock_out = %s, total_hours = %s, status = 'shift_ended', updated_at = %s
                    WHERE id = %s
                    RETURNING *;
                    """,
                    (now, total_hrs, now, record["id"])
                )

            cur.execute(
                """
                SELECT id, user_id, work_date, clock_in, lunch_start, lunch_end, clock_out, total_hours, status, notes
                FROM ticketing_system.attendance_logs
                WHERE user_id = %s AND work_date = %s;
                """,
                (user_id, today_date)
            )
            updated = cur.fetchone()

        return {
            "success": True,
            "message": f"Successfully performed {action.replace('_', ' ').title()}.",
            "attendance": {
                "id": updated["id"],
                "work_date": str(updated["work_date"]),
                "status": updated["status"],
                "clock_in": updated["clock_in"].isoformat() if updated.get("clock_in") else None,
                "lunch_start": updated["lunch_start"].isoformat() if updated.get("lunch_start") else None,
                "lunch_end": updated["lunch_end"].isoformat() if updated.get("lunch_end") else None,
                "clock_out": updated["clock_out"].isoformat() if updated.get("clock_out") else None,
                "total_hours": float(updated["total_hours"] or 0.0),
                "shift_duration_hours": 9.0,
                "break_duration_hours": 1.0,
                "notes": updated.get("notes") or ""
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Attendance action error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to record attendance: {str(e)}")

@app.get("/api/employee/attendance/history")
def get_attendance_history(limit: int = 15, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT id, user_id, work_date, clock_in, lunch_start, lunch_end, clock_out, total_hours, status, notes
                FROM ticketing_system.attendance_logs
                WHERE user_id = %s
                ORDER BY work_date DESC
                LIMIT %s;
                """,
                (user_id, limit)
            )
            rows = cur.fetchall()
            logs = []
            for r in rows:
                logs.append({
                    "id": r["id"],
                    "work_date": str(r["work_date"]),
                    "clock_in": r["clock_in"].isoformat() if r.get("clock_in") else None,
                    "lunch_start": r["lunch_start"].isoformat() if r.get("lunch_start") else None,
                    "lunch_end": r["lunch_end"].isoformat() if r.get("lunch_end") else None,
                    "clock_out": r["clock_out"].isoformat() if r.get("clock_out") else None,
                    "total_hours": float(r["total_hours"] or 0.0),
                    "status": r["status"]
                })
            return {"logs": logs}
    except Exception as e:
        logger.error(f"Error fetching attendance history: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch attendance history.")

# ============================================================================
# EMPLOYEE HUB: TIME EDIT REQUESTS (CORRECTION & ADJUSTMENT FILING)
# ============================================================================

@app.get("/api/employee/time-edit-requests")
def list_time_edit_requests(status_filter: str | None = None, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    try:
        with get_db_cursor(commit=False) as cur:
            query = """
                SELECT t.id, t.request_code, t.user_id, t.target_date, t.edit_type,
                       t.requested_clock_in, t.requested_lunch_start, t.requested_lunch_end, t.requested_clock_out,
                       t.reason, t.proof_attachment, t.proof_file_name, t.status,
                       t.reviewer_notes, t.reviewed_at, t.created_at,
                       u.full_name as employee_name, u.email as employee_email,
                       rev.full_name as reviewer_name
                FROM ticketing_system.time_edit_requests t
                JOIN ticketing_system.users u ON t.user_id = u.id
                LEFT JOIN ticketing_system.users rev ON t.reviewed_by = rev.id
                WHERE t.user_id = %s
            """
            params = [user_id]
            if status_filter:
                query += " AND t.status = %s"
                params.append(status_filter)
            query += " ORDER BY t.created_at DESC;"

            cur.execute(query, tuple(params))
            rows = cur.fetchall()
            requests = []
            for r in rows:
                requests.append({
                    "id": r["id"],
                    "request_code": r["request_code"],
                    "target_date": str(r["target_date"]),
                    "edit_type": r["edit_type"],
                    "requested_clock_in": r["requested_clock_in"],
                    "requested_lunch_start": r["requested_lunch_start"],
                    "requested_lunch_end": r["requested_lunch_end"],
                    "requested_clock_out": r["requested_clock_out"],
                    "reason": r["reason"],
                    "proof_attachment": r["proof_attachment"],
                    "proof_file_name": r["proof_file_name"],
                    "status": r["status"],
                    "reviewer_notes": r["reviewer_notes"],
                    "reviewer_name": r["reviewer_name"],
                    "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                    "reviewed_at": r["reviewed_at"].isoformat() if r.get("reviewed_at") else None
                })
            return {"requests": requests}
    except Exception as e:
        logger.error(f"Error listing time edit requests: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch time edit requests.")

@app.post("/api/employee/time-edit-requests", status_code=status.HTTP_201_CREATED)
def create_time_edit_request(req: TimeEditCreateRequest, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    try:
        target_date_obj = datetime.strptime(req.target_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid target date format. Must be YYYY-MM-DD.")

    req_code = f"TER-{secrets.token_hex(3).upper()}"

    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                """
                INSERT INTO ticketing_system.time_edit_requests
                (request_code, user_id, target_date, edit_type, requested_clock_in, requested_lunch_start,
                 requested_lunch_end, requested_clock_out, reason, proof_attachment, proof_file_name, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                RETURNING *;
                """,
                (
                    req_code, user_id, target_date_obj, req.edit_type,
                    req.requested_clock_in, req.requested_lunch_start,
                    req.requested_lunch_end, req.requested_clock_out,
                    req.reason, req.proof_attachment, req.proof_file_name
                )
            )
            created = cur.fetchone()

        return {
            "success": True,
            "message": f"Time edit request {req_code} submitted successfully.",
            "request": {
                "id": created["id"],
                "request_code": created["request_code"],
                "target_date": str(created["target_date"]),
                "edit_type": created["edit_type"],
                "status": created["status"],
                "created_at": created["created_at"].isoformat() if created.get("created_at") else None
            }
        }
    except Exception as e:
        logger.error(f"Error creating time edit request: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to submit time edit request: {str(e)}")

@app.patch("/api/employee/time-edit-requests/{req_id}")
def update_time_edit_request(req_id: int, req: TimeEditUpdateRequest, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    user_role = current_user.get("role", "employee")
    is_staff = is_super_admin(user_role) or is_tech_or_agent(user_role) or current_user.get("can_manage_departments")

    now = datetime.now(timezone.utc)
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute("SELECT * FROM ticketing_system.time_edit_requests WHERE id = %s;", (req_id,))
            record = cur.fetchone()
            if not record:
                raise HTTPException(status_code=404, detail="Time edit request not found.")

            if not is_staff and record["user_id"] != user_id:
                raise HTTPException(status_code=403, detail="Permission denied.")

            if not is_staff and req.status not in ["cancelled", "approved", "rejected", "pending"]:
                raise HTTPException(status_code=403, detail="Employees may only update or cancel their own requests.")

            cur.execute(
                """
                UPDATE ticketing_system.time_edit_requests
                SET status = %s, reviewer_notes = %s, reviewed_by = %s, reviewed_at = %s, updated_at = %s
                WHERE id = %s
                RETURNING *;
                """,
                (req.status, req.reviewer_notes or record.get("reviewer_notes"), user_id, now, now, req_id)
            )

            # If approved, update or insert attendance note
            if req.status == "approved":
                t_date = record["target_date"]
                cur.execute(
                    """
                    INSERT INTO ticketing_system.attendance_logs (user_id, work_date, status, notes)
                    VALUES (%s, %s, 'completed', 'Adjusted via Time Edit ' || %s)
                    ON CONFLICT (user_id, work_date) DO UPDATE
                    SET notes = COALESCE(ticketing_system.attendance_logs.notes, '') || ' [Adjusted via ' || %s || ']',
                        status = 'completed';
                    """,
                    (record["user_id"], t_date, record["request_code"], record["request_code"])
                )

        return {"success": True, "message": f"Time edit request updated to {req.status}."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating time edit request: {e}")
        raise HTTPException(status_code=500, detail="Failed to update request.")

# ============================================================================
# EMPLOYEE HUB: OVERTIME (OT) REQUESTS & AUTHORIZATION
# ============================================================================

@app.get("/api/employee/overtime-requests")
def list_overtime_requests(status_filter: str | None = None, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    try:
        with get_db_cursor(commit=False) as cur:
            query = """
                SELECT o.id, o.request_code, o.user_id, o.ot_date, o.ot_type,
                       o.start_time, o.end_time, o.total_hours, o.task_purpose,
                       o.proof_attachment, o.proof_file_name, o.status,
                       o.reviewer_notes, o.reviewed_at, o.created_at,
                       u.full_name as employee_name, rev.full_name as reviewer_name
                FROM ticketing_system.overtime_requests o
                JOIN ticketing_system.users u ON o.user_id = u.id
                LEFT JOIN ticketing_system.users rev ON o.reviewed_by = rev.id
                WHERE o.user_id = %s
            """
            params = [user_id]
            if status_filter:
                query += " AND o.status = %s"
                params.append(status_filter)
            query += " ORDER BY o.created_at DESC;"

            cur.execute(query, tuple(params))
            rows = cur.fetchall()
            requests = []
            for r in rows:
                requests.append({
                    "id": r["id"],
                    "request_code": r["request_code"],
                    "ot_date": str(r["ot_date"]),
                    "ot_type": r["ot_type"],
                    "start_time": r["start_time"],
                    "end_time": r["end_time"],
                    "total_hours": float(r["total_hours"] or 0.0),
                    "task_purpose": r["task_purpose"],
                    "proof_attachment": r["proof_attachment"],
                    "proof_file_name": r["proof_file_name"],
                    "status": r["status"],
                    "reviewer_notes": r["reviewer_notes"],
                    "reviewer_name": r["reviewer_name"],
                    "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                    "reviewed_at": r["reviewed_at"].isoformat() if r.get("reviewed_at") else None
                })
            return {"requests": requests}
    except Exception as e:
        logger.error(f"Error listing overtime requests: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch overtime requests.")

@app.post("/api/employee/overtime-requests", status_code=status.HTTP_201_CREATED)
def create_overtime_request(req: OvertimeCreateRequest, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    try:
        ot_date_obj = datetime.strptime(req.ot_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid OT date format. Must be YYYY-MM-DD.")

    if req.total_hours <= 0:
        raise HTTPException(status_code=400, detail="Overtime hours must be greater than 0.")

    req_code = f"OTR-{secrets.token_hex(3).upper()}"

    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                """
                INSERT INTO ticketing_system.overtime_requests
                (request_code, user_id, ot_date, ot_type, start_time, end_time, total_hours, task_purpose, proof_attachment, proof_file_name, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                RETURNING *;
                """,
                (
                    req_code, user_id, ot_date_obj, req.ot_type,
                    req.start_time, req.end_time, req.total_hours,
                    req.task_purpose, req.proof_attachment, req.proof_file_name
                )
            )
            created = cur.fetchone()

        return {
            "success": True,
            "message": f"Overtime request {req_code} submitted successfully.",
            "request": {
                "id": created["id"],
                "request_code": created["request_code"],
                "ot_date": str(created["ot_date"]),
                "total_hours": float(created["total_hours"]),
                "status": created["status"],
                "created_at": created["created_at"].isoformat() if created.get("created_at") else None
            }
        }
    except Exception as e:
        logger.error(f"Error creating overtime request: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to submit overtime request: {str(e)}")

@app.patch("/api/employee/overtime-requests/{req_id}")
def update_overtime_request(req_id: int, req: OvertimeUpdateRequest, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    user_role = current_user.get("role", "employee")
    is_staff = is_super_admin(user_role) or is_tech_or_agent(user_role) or current_user.get("can_manage_departments")

    now = datetime.now(timezone.utc)
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute("SELECT * FROM ticketing_system.overtime_requests WHERE id = %s;", (req_id,))
            record = cur.fetchone()
            if not record:
                raise HTTPException(status_code=404, detail="Overtime request not found.")

            if not is_staff and record["user_id"] != user_id:
                raise HTTPException(status_code=403, detail="Permission denied.")

            if not is_staff and req.status not in ["cancelled", "approved", "rejected", "pending"]:
                raise HTTPException(status_code=403, detail="Employees may only update or cancel their own requests.")

            cur.execute(
                """
                UPDATE ticketing_system.overtime_requests
                SET status = %s, reviewer_notes = %s, reviewed_by = %s, reviewed_at = %s, updated_at = %s
                WHERE id = %s
                RETURNING *;
                """,
                (req.status, req.reviewer_notes or record.get("reviewer_notes"), user_id, now, now, req_id)
            )

        return {"success": True, "message": f"Overtime request updated to {req.status}."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating overtime request: {e}")
        raise HTTPException(status_code=500, detail="Failed to update request.")

# ============================================================================
# EMPLOYEE HUB: REQUIRED DOCUMENTS (NEW HIRE & COMPLIANCE CHECKLIST)
# ============================================================================

DEFAULT_REQUIRED_DOCUMENTS = [
    ("gov_id", "Government-Issued Identification (Passport / UMID / Driver's License)"),
    ("bir_tin", "BIR Tax Identification Form (Form 1902 / 2316)"),
    ("sss_philhealth", "SSS, PhilHealth & Pag-IBIG Numbers Registration"),
    ("contract_nda", "Signed Employment Agreement & Non-Disclosure Contract"),
    ("medical_clearance", "Pre-Employment Medical Examination & Fit to Work Certificate"),
    ("bank_payroll", "Bank Account Direct Deposit Details for Payroll")
]

@app.get("/api/employee/documents")
def get_employee_documents(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    try:
        with get_db_cursor(commit=True) as cur:
            for key, name in DEFAULT_REQUIRED_DOCUMENTS:
                cur.execute(
                    """
                    INSERT INTO ticketing_system.employee_documents (user_id, doc_key, doc_name, status)
                    VALUES (%s, %s, %s, 'pending')
                    ON CONFLICT (user_id, doc_key) DO NOTHING;
                    """,
                    (user_id, key, name)
                )

            cur.execute(
                """
                SELECT id, doc_key, doc_name, status, file_name, notes, submitted_at, verified_at, updated_at
                FROM ticketing_system.employee_documents
                WHERE user_id = %s
                ORDER BY id ASC;
                """,
                (user_id,)
            )
            rows = cur.fetchall()

            docs = []
            verified_count = 0
            submitted_count = 0
            for r in rows:
                stat = r["status"]
                if stat == "verified":
                    verified_count += 1
                if stat in ("verified", "submitted", "under_review"):
                    submitted_count += 1

                docs.append({
                    "id": r["id"],
                    "doc_key": r["doc_key"],
                    "doc_name": r["doc_name"],
                    "status": r["status"],
                    "file_name": r.get("file_name") or "",
                    "notes": r.get("notes") or "",
                    "submitted_at": r["submitted_at"].isoformat() if r.get("submitted_at") else None,
                    "verified_at": r["verified_at"].isoformat() if r.get("verified_at") else None
                })

            total = len(docs)
            percent = round((submitted_count / total * 100)) if total > 0 else 0
            is_all_passed = (verified_count == total and total > 0)

            return {
                "documents": docs,
                "summary": {
                    "total": total,
                    "submitted_count": submitted_count,
                    "verified_count": verified_count,
                    "pending_count": total - submitted_count,
                    "completion_percent": percent,
                    "is_all_passed": is_all_passed,
                    "status_label": "All Requirements Complete" if is_all_passed else (f"{total - submitted_count} requirements remaining" if total - submitted_count > 0 else "Under HR Review")
                }
            }
    except Exception as e:
        logger.error(f"Error fetching employee documents: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch required documents.")

@app.post("/api/employee/documents/{doc_key}/submit")
def submit_employee_document(doc_key: str, req: DocumentSubmitRequest, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    now = datetime.now(timezone.utc)
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                """
                UPDATE ticketing_system.employee_documents
                SET status = 'under_review',
                    file_name = COALESCE(%s, file_name, 'uploaded_document.pdf'),
                    file_data = %s,
                    notes = %s,
                    submitted_at = %s,
                    updated_at = %s
                WHERE user_id = %s AND doc_key = %s
                RETURNING *;
                """,
                (req.file_name, req.file_data, req.notes, now, now, user_id, doc_key)
            )
            updated = cur.fetchone()
            if not updated:
                raise HTTPException(status_code=404, detail="Document requirement not found.")

        return {
            "success": True,
            "message": "Document submitted for HR compliance verification.",
            "document": {
                "doc_key": updated["doc_key"],
                "status": updated["status"],
                "file_name": updated["file_name"]
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error submitting document: {e}")
        raise HTTPException(status_code=500, detail="Failed to submit document.")

@app.patch("/api/employee/documents/{doc_key}/status")
def update_document_status(doc_key: str, req: DocumentStatusUpdateRequest, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    now = datetime.now(timezone.utc)
    verified_at = now if req.status == "verified" else None
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                """
                UPDATE ticketing_system.employee_documents
                SET status = %s,
                    notes = COALESCE(%s, notes),
                    verified_at = %s,
                    updated_at = %s
                WHERE user_id = %s AND doc_key = %s
                RETURNING *;
                """,
                (req.status, req.notes, verified_at, now, user_id, doc_key)
            )
            updated = cur.fetchone()
            if not updated:
                raise HTTPException(status_code=404, detail="Document requirement not found.")

        return {
            "success": True,
            "message": f"Document status updated to {req.status}.",
            "document": {
                "doc_key": updated["doc_key"],
                "status": updated["status"],
                "file_name": updated.get("file_name"),
                "verified_at": updated["verified_at"].isoformat() if updated.get("verified_at") else None
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating document status: {e}")
        raise HTTPException(status_code=500, detail="Failed to update document status.")

# ============================================================================
# EMPLOYEE HUB: PAYROLL SCHEDULE & CYCLE DATES
# ============================================================================

@app.get("/api/employee/payroll-schedule")
def get_payroll_schedule(current_user: dict = Depends(get_current_user)):
    now = datetime.now()
    year = now.year
    month = now.month
    day = now.day

    import calendar

    last_day_of_month = calendar.monthrange(year, month)[1]

    if day <= 15:
        period_start = datetime(year, month, 1)
        period_end = datetime(year, month, 15)
        filing_cutoff = datetime(year, month, 12, 23, 59, 59)
        approval_cutoff = datetime(year, month, 14, 17, 0, 0)
        payout_date = datetime(year, month, 15)
        cycle_name = f"{period_start.strftime('%B')} 1–15, {year}"
        
        next_cycle_start = datetime(year, month, 16)
        next_cycle_end = datetime(year, month, last_day_of_month)
        next_payout_month = month + 1 if month < 12 else 1
        next_payout_year = year if month < 12 else year + 1
        next_payout = datetime(next_payout_year, next_payout_month, 5)
    else:
        period_start = datetime(year, month, 16)
        period_end = datetime(year, month, last_day_of_month)
        filing_cutoff = datetime(year, month, min(27, last_day_of_month - 3), 23, 59, 59)
        approval_cutoff = datetime(year, month, min(29, last_day_of_month - 1), 17, 0, 0)
        next_month = month + 1 if month < 12 else 1
        next_year = year if month < 12 else year + 1
        payout_date = datetime(next_year, next_month, 5)
        cycle_name = f"{period_start.strftime('%B')} 16–{last_day_of_month}, {year}"
        
        next_cycle_start = datetime(next_year, next_month, 1)
        next_cycle_end = datetime(next_year, next_month, 15)
        next_payout = datetime(next_year, next_month, 15)

    days_to_filing_cutoff = (filing_cutoff.date() - now.date()).days
    filing_status = "Open" if days_to_filing_cutoff >= 0 else "Closed"

    return {
        "cycle_name": cycle_name,
        "pay_period": {
            "start": period_start.strftime("%b %d, %Y"),
            "end": period_end.strftime("%b %d, %Y"),
            "raw_start": period_start.date().isoformat(),
            "raw_end": period_end.date().isoformat()
        },
        "filing_cutoff": {
            "display": filing_cutoff.strftime("%b %d, %Y at %I:%M %p"),
            "date": filing_cutoff.strftime("%b %d, %Y"),
            "time": filing_cutoff.strftime("%I:%M %p"),
            "status": filing_status,
            "days_remaining": max(0, days_to_filing_cutoff),
            "is_past": days_to_filing_cutoff < 0
        },
        "approval_cutoff": {
            "display": approval_cutoff.strftime("%b %d, %Y at %I:%M %p"),
            "date": approval_cutoff.strftime("%b %d, %Y"),
            "time": approval_cutoff.strftime("%I:%M %p")
        },
        "payout_date": {
            "display": payout_date.strftime("%b %d, %Y"),
            "disbursement_type": "Direct Bank Deposit"
        },
        "upcoming_cycle": {
            "period": f"{next_cycle_start.strftime('%b %d')} – {next_cycle_end.strftime('%b %d, %Y')}",
            "payout": next_payout.strftime("%b %d, %Y")
        },
        "policy_notice": "All overtime entries and time edit requests must be submitted and supervisor-approved by the filing cutoff to ensure timely crediting in the corresponding payout."
    }

# ============================================================================
# EMPLOYEE HUB: PROFILE & COMPREHENSIVE HUB SUMMARY
# ============================================================================

@app.patch("/api/employee/profile")
def update_employee_profile(req: EmployeeProfileUpdateRequest, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                """
                UPDATE ticketing_system.users
                SET avatar_url = COALESCE(%s, avatar_url),
                    full_name = COALESCE(%s, full_name),
                    phone_number = COALESCE(%s, phone_number),
                    position = COALESCE(%s, position),
                    work_location = COALESCE(%s, work_location),
                    emergency_contact = COALESCE(%s, emergency_contact)
                WHERE id = %s
                RETURNING id, full_name, email, role, department, position, employee_id, avatar_url,
                          phone_number, employment_type, work_location, date_hired, emergency_contact, created_at;
                """,
                (req.avatar_url, req.full_name, req.phone_number, req.position, req.work_location, req.emergency_contact, user_id)
            )
            updated = cur.fetchone()
            if updated:
                if updated.get("date_hired"):
                    updated["date_hired"] = str(updated["date_hired"])
                elif updated.get("created_at"):
                    try:
                        updated["date_hired"] = updated["created_at"].date().isoformat()
                    except Exception:
                        pass
                if updated.get("created_at"):
                    updated["created_at"] = updated["created_at"].isoformat()
                if not updated.get("employee_id"):
                    updated["employee_id"] = f"EMP-{updated['id']:05d}"
                if not updated.get("employment_type"):
                    updated["employment_type"] = "Regular Full-Time"
                if not updated.get("work_location"):
                    updated["work_location"] = "Main HQ / Hybrid"
        return {"success": True, "user": updated}
    except Exception as e:
        logger.error(f"Error updating profile: {e}")
        raise HTTPException(status_code=500, detail="Failed to update profile.")

@app.get("/api/employee/hub-summary")
def get_hub_summary(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute("SELECT count(*) as count FROM ticketing_system.time_edit_requests WHERE user_id = %s AND status = 'pending';", (user_id,))
            pending_time_edits = cur.fetchone()["count"]

            cur.execute("SELECT count(*) as count FROM ticketing_system.time_edit_requests WHERE user_id = %s AND status = 'approved';", (user_id,))
            approved_time_edits = cur.fetchone()["count"]

            cur.execute("SELECT count(*) as count FROM ticketing_system.overtime_requests WHERE user_id = %s AND status = 'pending';", (user_id,))
            pending_overtimes = cur.fetchone()["count"]

            cur.execute("SELECT count(*) as count, COALESCE(SUM(total_hours), 0) as total_hours FROM ticketing_system.overtime_requests WHERE user_id = %s AND status = 'approved';", (user_id,))
            ot_row = cur.fetchone()
            approved_overtimes = ot_row["count"]
            total_approved_ot_hours = float(ot_row["total_hours"])

            cur.execute("SELECT count(*) as count FROM ticketing_system.tickets WHERE requester_id = %s AND status IN ('open', 'in_progress', 'waiting_on_us');", (user_id,))
            active_tickets = cur.fetchone()["count"]

            cur.execute(
                """
                (
                    SELECT id, 'time_edit' as req_kind, request_code, target_date as req_date, edit_type as title, reason as description, status, created_at
                    FROM ticketing_system.time_edit_requests
                    WHERE user_id = %s
                )
                UNION ALL
                (
                    SELECT id, 'overtime' as req_kind, request_code, ot_date as req_date, ot_type || ' (' || total_hours || ' hrs)' as title, task_purpose as description, status, created_at
                    FROM ticketing_system.overtime_requests
                    WHERE user_id = %s
                )
                ORDER BY created_at DESC
                LIMIT 8;
                """,
                (user_id, user_id)
            )
            recent_requests = [
                {
                    "id": r["id"],
                    "kind": r["req_kind"],
                    "request_code": r["request_code"],
                    "date": str(r["req_date"]),
                    "title": r["title"].replace('_', ' ').title(),
                    "description": r["description"],
                    "status": r["status"],
                    "created_at": r["created_at"].isoformat() if r.get("created_at") else None
                }
                for r in cur.fetchall()
            ]

            return {
                "pending_time_edits": pending_time_edits,
                "approved_time_edits": approved_time_edits,
                "pending_overtimes": pending_overtimes,
                "approved_overtimes": approved_overtimes,
                "total_approved_ot_hours": total_approved_ot_hours,
                "active_tickets": active_tickets,
                "recent_requests": recent_requests
            }
    except Exception as e:
        logger.error(f"Error fetching hub summary: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch hub summary.")

# Helper for serving frontend files
def _serve_frontend_file(filename: str):
    for base in ["public", "frontend", ""]:
        candidate = os.path.join(ROOT_DIR, base, filename) if base else os.path.join(ROOT_DIR, filename)
        if os.path.exists(candidate) and os.path.isfile(candidate):
            return FileResponse(candidate)
    raise HTTPException(status_code=404, detail=f"{filename} not found.")

@app.get("/portal", response_class=FileResponse, include_in_schema=False)
@app.get("/hub", response_class=FileResponse, include_in_schema=False)
@app.get("/requests", response_class=FileResponse, include_in_schema=False)
def route_portal_clean():
    return _serve_frontend_file("portal.html")

@app.get("/dashboard", response_class=FileResponse, include_in_schema=False)
def route_dashboard_clean():
    return _serve_frontend_file("dashboard.html")

@app.get("/login", response_class=FileResponse, include_in_schema=False)
@app.get("/signin", response_class=FileResponse, include_in_schema=False)
@app.get("/signup", response_class=FileResponse, include_in_schema=False)
def route_auth_clean():
    return _serve_frontend_file("index.html")

# Serving frontend static files
for base_folder in ["public", "frontend"]:
    static_dir = os.path.join(ROOT_DIR, base_folder)
    if os.path.exists(static_dir):
        for sub in ["css", "js", "images"]:
            subpath = os.path.join(static_dir, sub)
            if os.path.exists(subpath):
                app.mount(f"/{sub}", StaticFiles(directory=subpath), name=f"{base_folder}_{sub}")
        app.mount("/", StaticFiles(directory=static_dir, html=True), name=f"{base_folder}_root")
        logger.info(f"Mounted static files from: {static_dir}")
        break

if __name__ == "__main__":
    import uvicorn
    print(f"[INFO] Starting Khin Ticket backend on http://{HOST}:{PORT}")
    print(f"[INFO] Frontend accessible at http://{HOST}:{PORT}")
    print(f"[INFO] API documentation at http://{HOST}:{PORT}/docs")
    uvicorn.run("backend.main:app", host=HOST, port=PORT, reload=True)

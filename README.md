# Khin Ticket - Multi-Department Corporate Ticketing & Issue Tracking System

**Khin Ticket** is an enterprise-grade, multi-department ticketing and issue management platform designed to streamline corporate support, IT incident tracking, cross-departmental requests, and developer workflows. Built with a high-performance **Python (FastAPI)** backend, raw **PostgreSQL** relational database persistence, and a modern, responsive **Vanilla ES6+ Web** frontend, the system delivers zero-trust role-based access control (RBAC), automated Gmail IMAP ticket ingestion, granular department access restrictions, and dual-portal interfaces.

---

## 📋 Table of Contents

1. [System Architecture](#-system-architecture)
2. [Complete Technology Stack & Libraries](#-complete-technology-stack--libraries)
3. [Role Hierarchy & Access Control Matrix (RBAC)](#-role-hierarchy--access-control-matrix-rbac)
4. [End-to-End System Processes & Workflows](#-end-to-end-system-processes--workflows)
   - [Process 1: User Registration & Onboarding](#process-1-user-registration--onboarding-2-step-wizard)
   - [Process 2: Authentication & Token Session Lifecycle](#process-2-authentication--token-session-lifecycle)
   - [Process 3: Ticket Creation & Department Restriction Validation](#process-3-ticket-creation--department-restriction-validation)
   - [Process 4: Automated Gmail Ingestion & Deduplication](#process-4-automated-gmail-ingestion--deduplication)
   - [Process 5: Ticket Triage, Assignment & Department Transfers](#process-5-ticket-triage-assignment--department-transfers)
   - [Process 6: Ticket Status Lifecycle & Resolution Workflow](#process-6-ticket-status-lifecycle--resolution-workflow)
   - [Process 7: Activity Stream & Internal Communication](#process-7-activity-stream--internal-communication)
   - [Process 8: Department Management & User Access Restrictions](#process-8-department-management--user-access-restrictions)
   - [Process 9: User Directory & Hierarchy Promotion](#process-9-user-directory--hierarchy-promotion)
   - [Process 10: Non-Destructive Audit Trail Policy](#process-10-non-destructive-audit-trail-policy)
5. [Database Architecture & Schema Reference](#-database-architecture--schema-reference)
6. [Complete REST API Specification](#-complete-rest-api-specification)
7. [Directory Structure & Code Organization](#-directory-structure--code-organization)
8. [Prerequisites & System Requirements](#-prerequisites--system-requirements)
9. [Step-by-Step Installation & Setup Guide](#-step-by-step-installation--setup-guide)
10. [Environment Variables Reference](#-environment-variables-reference)
11. [Theme, UI & Design System](#-theme-ui--design-system)
12. [Security Architecture & Audit Integrity](#-security-architecture--audit-integrity)

---

## 🏛️ System Architecture

The application adopts a decoupled, client-server REST architecture with asynchronous background task processing and centralized relational database pooling:

```mermaid
flowchart TD
    subgraph Clients["Frontend Clients (Browser)"]
        A1["Requester Portal (portal.html)\n[Role: employee]"]
        A2["Staff Dashboard (dashboard.html)\n[Roles: super_admin, tech_member, dept_agent]"]
        A3["Authentication Page (index.html)\n[Sign In / 2-Step Sign Up]"]
    end

    subgraph External["External Services"]
        G1["Gmail Inbound Support Inbox\n(IMAP over SSL : 993)"]
    end

    subgraph Backend["Backend Application Server (Python / FastAPI)"]
        B1["Uvicorn ASGI Server (:5000)"]
        B2["CORS & Static File Middleware"]
        B3["FastAPI Application (backend/main.py)"]
        B4["Auth & Security Engine (backend/auth.py)\n[Bcrypt + PyJWT]"]
        B5["Email Ingestion Engine (backend/email_service.py)\n[imaplib + email]"]
        B6["Connection Pool Manager (backend/database.py)\n[psycopg2 SimpleConnectionPool]"]
    end

    subgraph Storage["Database Layer (PostgreSQL / Supabase)"]
        D1[("Schema: ticketing_system\n- users\n- departments\n- tickets\n- ticket_comments\n- department_restrictions")]
    end

    A1 -->|REST API with Bearer Token| B3
    A2 -->|REST API with Bearer Token| B3
    A3 -->|Auth Endpoints| B3
    G1 -->|Fetch Unread Messages| B5
    B3 --> B4
    B3 --> B5
    B3 --> B6
    B5 --> B6
    B6 -->|Parameterized SQL Queries| D1
    B1 --> B2 --> B3
```

---

## 🚀 Complete Technology Stack & Libraries

Every technology, third-party library, and module utilized within Khin Ticket has been chosen for performance, type safety, security, and zero unnecessary bloat:

### 1. Backend Core & Libraries

| Library / Tool | Exact Version | Module Location | Primary Role & Implementation Detail |
| :--- | :--- | :--- | :--- |
| **Python** | `3.11` - `3.13+` | Runtime | Core programming language powering all backend API endpoints and ingestion workers. |
| **FastAPI** | `0.111.0` | [`backend/main.py`](file:///C:/Users/gambo/repos/khin-ticketing-system/backend/main.py) | Modern, asynchronous Web REST API framework. Provides automatic OpenAPI/Swagger documentation (`/docs`), Pydantic request body validation, dependency injection (`Depends(get_current_user)`), and application lifespan handlers (`@asynccontextmanager`). |
| **Uvicorn** | `0.30.1` | [`run.py`](file:///C:/Users/gambo/repos/khin-ticketing-system/run.py) / [`backend/main.py`](file:///C:/Users/gambo/repos/khin-ticketing-system/backend/main.py) | Lightning-fast ASGI web server implementation with auto-reload capabilities during development. |
| **psycopg2-binary** | `2.9.12` | [`backend/database.py`](file:///C:/Users/gambo/repos/khin-ticketing-system/backend/database.py) | Native PostgreSQL database adapter. Implements `psycopg2.pool.SimpleConnectionPool` (1 to 10 connections) with `RealDictCursor` for dictionary row parsing and transaction rollback/commit context managers (`@contextmanager`). |
| **bcrypt** | `4.1.3` | [`backend/auth.py`](file:///C:/Users/gambo/repos/khin-ticketing-system/backend/auth.py) | Cryptographic hashing algorithm. Uses automatic salting via `bcrypt.gensalt()` to protect user credentials against rainbow table and brute-force attacks. |
| **PyJWT** | `2.8.0` | [`backend/auth.py`](file:///C:/Users/gambo/repos/khin-ticketing-system/backend/auth.py) | Stateless JSON Web Token generation and validation using `HS256` signatures and configurable expiration timestamps (`ACCESS_TOKEN_EXPIRE_MINUTES`). |
| **python-dotenv** | `1.0.1` | [`backend/config.py`](file:///C:/Users/gambo/repos/khin-ticketing-system/backend/config.py) | Reads environment variables from the root `.env` configuration file into `os.environ`. |
| **python-multipart** | `0.0.9` | Requirements | Enables parsing of multipart form-data requests and file streams in FastAPI endpoints. |

### 2. Standard Python Libraries & Ingestion Services

| Standard Module | Location | Purpose & Implementation |
| :--- | :--- | :--- |
| **`imaplib`** | [`backend/email_service.py`](file:///C:/Users/gambo/repos/khin-ticketing-system/backend/email_service.py) | Establishes secure SSL socket connections to Gmail (`imap.gmail.com:993`), logs in with Google App Passwords, searches for `UNSEEN` messages, and marks processed emails as `\Seen`. |
| **`email` / `email.header`** | [`backend/email_service.py`](file:///C:/Users/gambo/repos/khin-ticketing-system/backend/email_service.py) | Parses raw RFC822 email byte buffers, decodes internationalized MIME headers via `decode_header`, traverses multipart email payloads (`walk()`), extracts sender addresses (`parseaddr`), and handles fallback charsets (`utf-8`, `latin-1`). |
| **`contextlib`** | [`backend/database.py`](file:///C:/Users/gambo/repos/khin-ticketing-system/backend/database.py) | Provides `@contextmanager` for safe connection leasing, commit enforcement, and automatic transaction rollback on exceptions. |
| **`logging`** | Throughout backend | System-wide structured logging for SQL transactions, auth operations, and email sync cycles. |

### 3. Frontend Architecture & Design Assets

| Technology / Asset | Location | Details & Responsibilities |
| :--- | :--- | :--- |
| **Vanilla HTML5** | [`frontend/`](file:///C:/Users/gambo/repos/khin-ticketing-system/frontend/) | Clean, semantic structure (`<header>`, `<main>`, `<aside>`, `<section>`, modals) without heavy JS framework overhead. |
| **Vanilla CSS3** | [`frontend/css/style.css`](file:///C:/Users/gambo/repos/khin-ticketing-system/frontend/css/style.css) | Custom styling system using CSS custom properties (`:root`), glassmorphism cards (`backdrop-filter`), CSS Grid, Flexbox, custom scrollbars, and keyframe animations. |
| **Vanilla ES6+ JavaScript** | [`frontend/js/app.js`](file:///C:/Users/gambo/repos/khin-ticketing-system/frontend/js/app.js) & inline scripts | Asynchronous client utilizing the native `Fetch API`, DOM manipulation, local storage token management, form validation, dynamic password visibility toggling, and toast notification alerts. |
| **Google Fonts** | [`style.css`](file:///C:/Users/gambo/repos/khin-ticketing-system/frontend/css/style.css#L2) | **`Outfit`** (geometric sans-serif for headings, brand logo, and metrics) and **`Inter`** (clean sans-serif for forms, data tables, and activity logs). |
| **Brand Identity** | [`frontend/images/`](file:///C:/Users/gambo/repos/khin-ticketing-system/frontend/images/) | High-resolution brand logo ([`logo-box.png`](file:///C:/Users/gambo/repos/khin-ticketing-system/frontend/images/logo-box.png)) and custom browser icon ([`favicon.ico`](file:///C:/Users/gambo/repos/khin-ticketing-system/frontend/images/favicon.ico)). |

### 4. Database Layer

| Database Component | Implementation Detail |
| :--- | :--- |
| **PostgreSQL 14+** | Relational database hosted on **Supabase** (via AWS connection pooler on port `6543`) or locally. |
| **Scoping Schema** | All tables are isolated inside the `ticketing_system` schema namespace to prevent conflicts with Supabase default schemas. |
| **Raw Parameterized SQL** | Zero ORM overhead. All queries use explicit `%s` parameters to prevent SQL injection vulnerabilities while ensuring sub-millisecond execution speeds. |

---

## 👥 Role Hierarchy & Access Control Matrix (RBAC)

    Khin Ticket implements a **Zero-Trust Access Control** architecture. When a user registers publicly, they are assigned the `employee` role by default. Higher-tier roles and management permissions can only be granted by an authenticated Super Ad    min.

```mermaid
graph TD
    SuperAdmin["👑 Super Admin / Tech Manager\n(Role: super_admin)"]
    TechMember["🛠️ Tech Team Member\n(Role: tech_member)"]
    DeptAgent["🏢 Department Agent\n(Role: dept_agent)"]
    Employee["👤 Standard Employee\n(Role: employee)"]

    SuperAdmin -->|Promotes & Assigns| TechMember
    SuperAdmin -->|Assigns Dept Scope| DeptAgent
    SuperAdmin -->|Restricts / Manages| Employee
    TechMember -.->|Cross-Dept Support| DeptAgent
```

### Granular Permission Matrix

| Capability / Action | `employee` | `dept_agent` | `tech_member` | `super_admin` |
| :--- | :---: | :---: | :---: | :---: |
| **Interface Destination** | `portal.html` | `dashboard.html` | `dashboard.html` | `dashboard.html` |
| **Raise Support Tickets** | ✅ (Allowed depts) | ✅ | ✅ | ✅ |
| **View Ticket Queues** | Own tickets only | Own department tickets | All departments | All departments |
| **Update Ticket Status** | Reopen / Close own | Own dept (All statuses) | All depts (All statuses) | All depts (All statuses) |
| **Update Priority & Department** | ❌ | Own dept only | All departments | All departments |
| **Assign Tickets to Staff** | ❌ | ❌ | ❌ | ✅ |
| **Internal Tech Notes** | ❌ (Hidden) | ✅ (View & Post) | ✅ (View & Post) | ✅ (View & Post) |
| **Public Comments / Activity** | ✅ (Own tickets) | ✅ | ✅ | ✅ |
| **Create New Departments** | ❌ | ❌ | ✅ (If delegated) | ✅ |
| **Manage Department Restrictions** | ❌ | ❌ | ❌ | ✅ |
| **Users & Hierarchy Directory** | ❌ | ❌ | ❌ | ✅ |
| **Promote Roles & Delegate Permissions** | ❌ | ❌ | ❌ | ✅ |
| **Trigger Gmail IMAP Ingestion** | ❌ | ❌ | ✅ | ✅ |
| **Delete Tickets** | ❌ (Forbidden) | ❌ (Forbidden) | ❌ (Forbidden) | ❌ (Forbidden) |

---

## 🔄 End-to-End System Processes & Workflows

### Process 1: User Registration & Onboarding (2-Step Wizard)

To prevent registration drop-offs while collecting organizational details, registration follows a guided 2-step wizard on [`frontend/index.html`](file:///C:/Users/gambo/repos/khin-ticketing-system/frontend/index.html):

```mermaid
sequenceDiagram
    autonumber
    actor User as New User
    participant UI as Frontend (index.html)
    participant API as FastAPI (/api/auth/register)
    participant DB as PostgreSQL (ticketing_system.users)

    User->>UI: Enter Full Name, Email, Department & Position
    UI->>UI: Validate email regex & non-empty fields
    User->>UI: Clicks "Continue to Password →"
    UI->>UI: Switches to Step 2 Card (smooth transition)
    User->>UI: Enter Password & Confirm Password (min. 6 chars)
    User->>UI: Clicks "Create Account"
    UI->>API: POST /api/auth/register (payload with sanitized strings)
    API->>DB: Check if email already exists
    alt Email Exists
        DB-->>API: Duplicate found
        API-->>UI: 400 Bad Request ("Account with this email exists")
        UI-->>User: Red Toast Notification
    else Email Unique
        API->>API: Hash password with bcrypt.gensalt()
        API->>DB: INSERT INTO ticketing_system.users (role='employee')
        DB-->>API: Returns new user record
        API->>API: Generate PyJWT access token (sub=user.id, role='employee')
        API-->>UI: 201 Created (access_token, user object)
        UI->>UI: Store access_token & profile in localStorage
        UI-->>User: Green Toast Notification & auto-redirect to portal.html
    end
```

### Process 2: Authentication & Token Session Lifecycle

Authentication is stateless and powered by JSON Web Tokens:

1. **User Sign In**: The user enters their registered email and password on [`frontend/index.html`](file:///C:/Users/gambo/repos/khin-ticketing-system/frontend/index.html).
2. **Hash Verification**: `POST /api/auth/login` fetches the user's `password_hash` from PostgreSQL and validates it via `bcrypt.checkpw()`.
3. **Token Issuance**: If valid, a JWT token is signed with `JWT_SECRET` (`HS256`) containing `sub` (user ID), `email`, and `role`, expiring in 24 hours (1440 minutes).
4. **Role Routing**:
   - Staff (`super_admin`, `tech_member`, `dept_agent`) are redirected to [`frontend/dashboard.html`](file:///C:/Users/gambo/repos/khin-ticketing-system/frontend/dashboard.html).
   - Requesters (`employee`) are redirected to [`frontend/portal.html`](file:///C:/Users/gambo/repos/khin-ticketing-system/frontend/portal.html).
5. **Flash of Unauthenticated Content (FOUC) Shield**: Both `dashboard.html` and `portal.html` execute a synchronous inline `<script>` in the `<head>` before HTML rendering. If `access_token` is missing or the role does not match, the browser immediately redirects to `index.html`.
6. **Session Expiration**: If an API request returns `401 Unauthorized`, `localStorage` is purged and the user is redirected to `index.html?expired=true` with an informative toast message.

### Process 3: Ticket Creation & Department Restriction Validation

Users can create tickets through the interactive modal in [`frontend/portal.html`](file:///C:/Users/gambo/repos/khin-ticketing-system/frontend/portal.html):

```mermaid
sequenceDiagram
    autonumber
    actor Emp as Employee
    participant Portal as Customer Portal (portal.html)
    participant API as FastAPI (/api/tickets)
    participant DB as PostgreSQL

    Emp->>Portal: Opens "Raise a Ticket" Modal
    Portal->>API: GET /api/departments
    API->>DB: SELECT active departments WHERE user NOT IN restrictions
    DB-->>API: Filtered department list
    API-->>Portal: Departments displayed in dropdown
    Emp->>Portal: Selects Department, Priority, Title, Description
    Emp->>Portal: Submits Ticket
    Portal->>API: POST /api/tickets (Bearer Token)
    API->>API: Verify token & extract current_user.id
    API->>DB: Check ticketing_system.department_restrictions
    alt User is Restricted
        DB-->>API: Restriction record found with reason
        API-->>Portal: 403 Forbidden ("You are restricted from raising tickets to [Dept]")
        Portal-->>Emp: Displays restriction banner with reason
    else User Not Restricted
        API->>DB: Compute next ticket code (e.g. KT-1042)
        API->>DB: INSERT INTO ticketing_system.tickets (source='portal', status='open')
        DB-->>API: Created ticket record
        API-->>Portal: 201 Created (Ticket details)
        Portal-->>Emp: Success Toast & Ticket prepended to table
    end
```

### Process 4: Automated Gmail Ingestion & Deduplication

Support tickets can be submitted automatically via email without requiring manual data entry:

```mermaid
flowchart TD
    A["User sends email to corporate support\n(e.g., gamboa.khinandrei@gmail.com)"] --> B["Gmail Server Inbox"]
    C["Staff clicks 'Sync Gmail' or automated task\nPOST /api/tickets/sync-emails"] --> D["backend/email_service.py\n(sync_gmail_tickets)"]
    D --> E["Connect via imaplib.IMAP4_SSL\n(imap.gmail.com:993)"]
    E --> F["Login using GMAIL_USER &\nGMAIL_APP_PASSWORD"]
    F --> G["Search for 'UNSEEN' messages"]
    G --> H{"Unread emails found?"}
    H -- No --> I["Return: 'No new unread emails'"]
    H -- Yes --> J["Fetch RFC822 raw email stream"]
    J --> K["Extract RFC822 Message-ID"]
    K --> L{"Message-ID exists in\ntickets.email_message_id?"}
    L -- Yes (Duplicate) --> M["Skip to avoid duplicate tickets"]
    L -- No (Unique) --> N["Decode MIME words for Subject & Sender"]
    N --> O["Extract plain text or HTML body payload"]
    O --> P{"Sender email matches\nexisting user account?"}
    P -- Yes --> Q["Associate requester_id with user.id"]
    P -- No --> R["Set requester_id = NULL\nStore sender email & name"]
    Q --> S["Generate ticket code (KT-XXXX)\nINSERT INTO tickets (source='email', status='open')"]
    R --> S
    S --> T["Flag email as \\Seen in Gmail"]
    T --> U["Return sync summary count to Dashboard"]
```

### Process 5: Ticket Triage, Assignment & Department Transfers

Staff members review and manage incoming tickets from [`frontend/dashboard.html`](file:///C:/Users/gambo/repos/khin-ticketing-system/frontend/dashboard.html):

1. **Filtering & Searching**: Staff can filter tickets by status (`open`, `in_progress`, `resolved`, `closed`), department dropdown, or live search query (matching ticket code, title, requester email, or requester name).
2. **Scoped Viewing**:
   - `super_admin` and `tech_member` see all organizational tickets.
   - `dept_agent` only sees tickets belonging to their assigned department or explicitly assigned to them.
3. **Ticket Assignment**:
   - In accordance with organizational hierarchy, **only Super Admins** can reassign tickets to tech team members (`PATCH /api/tickets/{id}`).
   - Department agents and tech members attempting to assign tickets receive a `403 Forbidden` response.
4. **Department Transfers**: If a ticket was routed to the wrong department, staff can reassign the `department_id`, routing it immediately into the target department's queue.

### Process 6: Ticket Status Lifecycle & Resolution Workflow

Tickets follow a standardized status state machine:

```mermaid
stateDiagram-v2
    [*] --> open: Created via Portal or Ingested via Email
    open --> in_progress: Staff picks up ticket / investigates
    in_progress --> resolved: Issue fixed & solution communicated
    resolved --> closed: Requester or Staff confirms resolution
    open --> closed: Canceled / duplicate closed
    closed --> open: Requester or Staff reopens ticket if issue recurs
    resolved --> in_progress: Issue not resolved / follow-up required
```

- **Employee Privileges**: Requesters can only transition their own tickets between `open` and `closed` (e.g., closing when satisfied, or reopening if unresolved).
- **Staff Privileges**: Staff can transition tickets through all four states (`open`, `in_progress`, `resolved`, `closed`).
- **Priority Escalation**: Staff can adjust priority between `low`, `medium`, `high`, and `urgent` based on business impact.

### Process 7: Activity Stream & Internal Communication

The ticket details modal features an integrated communication and activity thread:

1. **Public Comments (`is_internal = false`)**: Visible to both staff and the requester. Used for troubleshooting questions, requesting screenshots, providing status updates, and communicating solutions.
2. **Internal Tech Notes (`is_internal = true`)**:
   - Tagged with an amber **Internal Note** badge and highlighted border.
   - **Completely invisible to employees**: Filtered at the database query level (`AND c.is_internal = FALSE`) in `GET /api/tickets/{id}`.
   - Used by tech members and agents to discuss root causes, share diagnostic commands, and coordinate handoffs without confusing the customer.

### Process 8: Department Management & User Access Restrictions

Super Admins and authorized Tech Members can configure organizational departments in [`frontend/dashboard.html`](file:///C:/Users/gambo/repos/khin-ticketing-system/frontend/dashboard.html):

1. **Creating Departments**:
   - Super Admins or Tech Members with `can_manage_departments = true` can create departments with custom descriptions.
   - Default seeded departments: *Information Technology*, *Human Resources*, *Finance & Accounting*, *Operations & Facilities*, and *General / Administrative*.
2. **Restricting Access**:
   - When specific employees should not submit tickets to certain departments (e.g., preventing contractor accounts from submitting internal IT hardware procurement requests), Super Admins can add a restriction with an audit reason.
   - Restrictions are enforced at both frontend dropdown rendering and backend ticket creation.
   - Restrictions can be reviewed and revoked at any time.

### Process 9: User Directory & Hierarchy Promotion

Super Admins manage corporate access via the **Users & Hierarchy** dashboard view:

1. **Directory Inspection**: View all registered accounts, emails, departments, titles, roles, and creation timestamps.
2. **Role Promotion**: Elevate employees to `dept_agent`, `tech_member`, or `super_admin`.
3. **Department Management Delegation**: Toggle the `can_manage_departments` boolean flag for trusted tech members without granting them full Super Admin status.

### Process 10: Non-Destructive Audit Trail Policy

Khin Ticket enforces enterprise audit transparency:
- The endpoint `DELETE /api/tickets/{ticket_id}` explicitly throws a `403 Forbidden` error with the message:
  > *"Tickets cannot be deleted. History remains preserved for transparency and audit trails."*
- Resolved or duplicate tickets must be set to `closed` rather than deleted, guaranteeing an immutable historical record for compliance reviews.

---

## 🗄️ Database Architecture & Schema Reference

The database is built on **PostgreSQL** inside the isolated `ticketing_system` schema.

### Entity-Relationship Diagram

```mermaid
erDiagram
    users ||--o{ tickets : "requester_id"
    users ||--o{ tickets : "assigned_to"
    users ||--o{ ticket_comments : "user_id"
    users ||--o{ department_restrictions : "user_id"
    users ||--o{ department_restrictions : "restricted_by"
    departments ||--o{ tickets : "department_id"
    departments ||--o{ department_restrictions : "department_id"
    tickets ||--o{ ticket_comments : "ticket_id"

    users {
        int id PK
        string email UK
        string password_hash
        string full_name
        string role
        string department
        string position
        boolean can_manage_departments
        timestamp created_at
    }

    departments {
        int id PK
        string name UK
        text description
        boolean is_active
        timestamp created_at
    }

    tickets {
        int id PK
        string ticket_code UK
        string title
        text description
        string requester_email
        string requester_name
        int requester_id FK
        int department_id FK
        string department_name
        string source
        string status
        string priority
        int assigned_to FK
        string email_message_id UK
        timestamp created_at
        timestamp updated_at
    }

    ticket_comments {
        int id PK
        int ticket_id FK
        int user_id FK
        text comment_text
        boolean is_internal
        timestamp created_at
    }

    department_restrictions {
        int id PK
        int user_id FK
        int department_id FK
        int restricted_by FK
        text reason
        timestamp created_at
    }
```

### Table Definitions & Indexing

#### 1. `ticketing_system.users`
Stores user identities, hashed passwords, roles, and administrative permissions.
- `id`: `SERIAL PRIMARY KEY`
- `email`: `VARCHAR(255) UNIQUE NOT NULL` (Indexed via `idx_users_email` for sub-millisecond login queries)
- `password_hash`: `VARCHAR(255) NOT NULL` (Salted bcrypt hash)
- `full_name`: `VARCHAR(100) NOT NULL`
- `role`: `VARCHAR(30) DEFAULT 'employee'` (`'super_admin'`, `'tech_member'`, `'dept_agent'`, `'employee'`)
- `department`: `VARCHAR(100) DEFAULT 'General'`
- `position`: `VARCHAR(100) DEFAULT 'Employee'`
- `can_manage_departments`: `BOOLEAN DEFAULT FALSE`
- `created_at`: `TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP`

#### 2. `ticketing_system.departments`
Maintains operational department queues.
- `id`: `SERIAL PRIMARY KEY`
- `name`: `VARCHAR(100) UNIQUE NOT NULL`
- `description`: `TEXT`
- `is_active`: `BOOLEAN DEFAULT TRUE`
- `created_at`: `TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP`

#### 3. `ticketing_system.tickets`
Core issue records submitted via portal or email.
- `id`: `SERIAL PRIMARY KEY`
- `ticket_code`: `VARCHAR(20) UNIQUE NOT NULL` (e.g. `KT-1001`, indexed)
- `title`: `VARCHAR(255) NOT NULL`
- `description`: `TEXT`
- `requester_email`: `VARCHAR(255) NOT NULL`
- `requester_name`: `VARCHAR(100)`
- `requester_id`: `INTEGER REFERENCES users(id) ON DELETE SET NULL` (indexed)
- `department_id`: `INTEGER REFERENCES departments(id) ON DELETE SET NULL` (indexed)
- `department_name`: `VARCHAR(100) DEFAULT 'Information Technology'`
- `source`: `VARCHAR(50) DEFAULT 'portal'` (`'portal'` or `'email'`)
- `status`: `VARCHAR(50) DEFAULT 'open'` (`'open'`, `'in_progress'`, `'resolved'`, `'closed'`, indexed)
- `priority`: `VARCHAR(20) DEFAULT 'medium'` (`'low'`, `'medium'`, `'high'`, `'urgent'`)
- `assigned_to`: `INTEGER REFERENCES users(id) ON DELETE SET NULL` (indexed)
- `email_message_id`: `VARCHAR(255) UNIQUE` (RFC822 Message-ID deduplication key)
- `created_at` / `updated_at`: `TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP` (indexed by created_at DESC)

#### 4. `ticketing_system.ticket_comments`
Chronological activity history and collaboration notes.
- `id`: `SERIAL PRIMARY KEY`
- `ticket_id`: `INTEGER REFERENCES tickets(id) ON DELETE CASCADE` (indexed)
- `user_id`: `INTEGER REFERENCES users(id) ON DELETE SET NULL`
- `comment_text`: `TEXT NOT NULL`
- `is_internal`: `BOOLEAN DEFAULT FALSE` (Flag separating private tech notes from customer view)
- `created_at`: `TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP`

#### 5. `ticketing_system.department_restrictions`
Access control entries restricting users from submitting tickets to specific departments.
- `id`: `SERIAL PRIMARY KEY`
- `user_id`: `INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE` (indexed)
- `department_id`: `INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE` (indexed)
- `restricted_by`: `INTEGER REFERENCES users(id) ON DELETE SET NULL`
- `reason`: `TEXT`
- `created_at`: `TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP`
- `UNIQUE(user_id, department_id)`: Prevents duplicate restriction rows.

---

## 📡 Complete REST API Specification

All protected endpoints require the HTTP Header:
`Authorization: Bearer <access_token>`

| Method | Endpoint | Required Role | Request Body / Query Params | Success Code & Response Description |
| :--- | :--- | :--- | :--- | :--- |
| **POST** | `/api/auth/register` | Public | JSON: `email`, `password`, `full_name`, `department`, `position` | `201 Created`: Returns `access_token`, `token_type`, and sanitized `user` object. |
| **POST** | `/api/auth/login` | Public | JSON: `email`, `password` | `200 OK`: Returns signed JWT `access_token` and `user` profile. |
| **GET** | `/api/auth/me` | Authenticated | None (Bearer Token) | `200 OK`: Returns authenticated user's current session credentials and role. |
| **GET** | `/api/users` | `super_admin` | None | `200 OK`: Returns all corporate user accounts for hierarchy management. |
| **PATCH** | `/api/users/{user_id}/role` | `super_admin` | JSON: `role`, `can_manage_departments` | `200 OK`: Updates target user's role and permission flags. |
| **GET** | `/api/departments` | Authenticated | None | `200 OK`: Returns active departments (filtered for employees against restrictions; enriched with counts for staff). |
| **POST** | `/api/departments` | Super Admin / Authorized Tech | JSON: `name`, `description` | `201 Created`: Creates a new department queue. |
| **GET** | `/api/departments/{dept_id}/restrictions` | `super_admin` | None | `200 OK`: Returns list of users restricted from this department. |
| **POST** | `/api/departments/{dept_id}/restrictions` | `super_admin` | JSON: `user_id`, `reason` | `200 OK`: Restricts user from submitting tickets to department. |
| **DELETE**| `/api/departments/{dept_id}/restrictions/{user_id}` | `super_admin` | None | `200 OK`: Removes restriction and restores user submission rights. |
| **GET** | `/api/team` | Staff only | None | `200 OK`: Returns tech and agent staff available for ticket assignment. |
| **GET** | `/api/tickets` | Authenticated | Query: `status_filter`, `priority_filter`, `dept_filter` | `200 OK`: Returns scoped tickets (employees see only own tickets; dept agents see department tickets; tech/admin see all). |
| **POST** | `/api/tickets` | Authenticated | JSON: `title`, `description`, `priority`, `department_id`, `department_name` | `201 Created`: Validates restrictions, creates ticket with code `KT-XXXX`. |
| **GET** | `/api/tickets/{ticket_id}` | Authenticated | None | `200 OK`: Returns ticket details and comments (internal notes hidden from employees). |
| **PATCH** | `/api/tickets/{ticket_id}` | Authenticated | JSON: `status`, `priority`, `assigned_to`, `department_id` | `200 OK`: Applies updates based on role permissions (only Super Admin can assign). |
| **DELETE**| `/api/tickets/{ticket_id}` | Anyone | None | `403 Forbidden`: Blocked to preserve transparency and audit trail. |
| **POST** | `/api/tickets/{ticket_id}/comments` | Authenticated | JSON: `comment_text`, `is_internal` | `201 Created`: Adds activity comment or internal tech note. |
| **GET** | `/api/dashboard/stats` | Authenticated | None | `200 OK`: Returns scoped metric counters (Total, Open, In Progress, Resolved, Unassigned, Staff Count). |
| **POST** | `/api/tickets/sync-emails` | Staff only | None | `200 OK`: Polls Gmail IMAP, ingests unread support emails into tickets. |

Interactive API documentation and schema explorer are accessible at:
- **Swagger UI**: `http://127.0.0.1:5000/docs`
- **ReDoc**: `http://127.0.0.1:5000/redoc`

---

## 📁 Directory Structure & Code Organization

```
khin-ticketing-system/
├── backend/
│   ├── __init__.py              # Python package marker
│   ├── auth.py                  # Cryptographic password hashing (bcrypt) & JWT token utilities (PyJWT)
│   ├── config.py                # Environment configuration loader (.env parser)
│   ├── database.py              # PostgreSQL connection pool manager (psycopg2) & schema runner
│   ├── email_service.py         # Gmail IMAP SSL ingestion worker & MIME header parser
│   ├── main.py                  # FastAPI REST endpoints, RBAC dependencies, and static file mount
│   └── requirements.txt         # Pinned Python package dependencies
├── frontend/
│   ├── css/
│   │   └── style.css            # Centralized stylesheet (CSS variables, glassmorphism, responsive grid)
│   ├── images/
│   │   ├── .gitkeep             # Git keepfile
│   │   ├── favicon.ico          # Custom browser favicon icon
│   │   └── logo-box.png         # High-resolution brand logo asset
│   ├── js/
│   │   └── app.js               # Frontend controller (auth forms, 2-step signup, fetch client, toasts)
│   ├── dashboard.html           # Staff & Admin dashboard (ticket queues, departments, users & hierarchy)
│   ├── index.html               # Sign In & Sign Up landing page
│   └── portal.html              # Dedicated Requester / Employee ticket portal
├── .env                         # Local environment configuration & credentials (gitignored)
├── .env.example                 # Template environment variables file
├── .gitignore                   # Git exclusion rules
├── index.html                   # Root redirection entrypoint to frontend/index.html
├── README.md                    # System documentation and operational manual (this file)
├── run.py                       # Unified application launcher with auto-venv detection
└── schema.sql                   # Database DDL schema, indexes, and initial seed records
```

---

## ⚙️ Prerequisites & System Requirements

### 1. Hardware & Operating System
- **Operating System**: Windows 10/11, macOS (Intel/Apple Silicon), or Linux (Ubuntu, Debian, Fedora, Arch).
- **RAM**: Minimum 2 GB (4 GB recommended).
- **Disk Space**: ~150 MB for repository, virtual environment, and dependencies.

### 2. Software Requirements
- **Python**: Version `3.11`, `3.12`, or `3.13+` installed and added to your system `PATH`.
- **PostgreSQL**: Version `14` or higher (either a cloud instance like **Supabase** or local PostgreSQL server).
- **Modern Web Browser**: Google Chrome, Mozilla Firefox, Microsoft Edge, or Safari with JavaScript enabled.
- **Git**: For version control.

### 3. Optional Email Ingestion Requirements
- **Google / Gmail Account**: To ingest support tickets via incoming emails.
- **Google App Password**: Generated via Google Account → Security → 2-Step Verification → App Passwords (16-character token).

---

## 🛠️ Step-by-Step Installation & Setup Guide

### Step 1: Clone the Repository
Open your terminal (or PowerShell on Windows) and clone the repository:
```bash
git clone https://github.com/your-username/khin-ticketing-system.git
cd khin-ticketing-system
```

### Step 2: Initialize Python Virtual Environment
Create and activate an isolated Python virtual environment:

- **Windows (PowerShell)**:
  ```powershell
  python -m venv .venv
  .venv\Scripts\Activate.ps1
  ```
- **Windows (Command Prompt)**:
  ```cmd
  python -m venv .venv
  .venv\Scripts\activate.bat
  ```
- **macOS / Linux**:
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  ```

### Step 3: Install Backend Dependencies
Install the required packages using `pip`:
```bash
pip install -r backend/requirements.txt
```

### Step 4: Provision PostgreSQL Database (Supabase Recommended)
1. Sign up or log in to [Supabase](https://supabase.com).
2. Create a new project (e.g., `Khin Ticket`).
3. Once provisioned, navigate to **Project Settings** (gear icon) → **Database**.
4. In the **Connection string** section, select the **URI** tab.
5. Copy the connection string. Using the AWS connection pooler on port **`6543`** is strongly recommended:
   ```
   postgresql://postgres.[your-project-ref]:[your-password]@aws-0-[region].pooler.supabase.com:6543/postgres?sslmode=require
   ```

*(Note: If using local PostgreSQL, your URI will look like `postgresql://postgres:password@localhost:5432/ticketing_db`).*

### Step 5: Configure Environment Variables
Create your local `.env` file by copying the provided template:
```bash
# Windows PowerShell
cp .env.example .env

# macOS / Linux
cp .env.example .env
```

Open `.env` and fill in your values:
```ini
# PostgreSQL Database Connection URI
DATABASE_URL=postgresql://postgres.exampleprojectref:my_actual_password@aws-0-us-west-1.pooler.supabase.com:6543/postgres?sslmode=require

# JWT Secret Key (Generate a random 32-byte hex string)
JWT_SECRET=c27df0a7bc01b4da523dfef86b840bc43f11da080788220ce6026a798a7281f6
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# Server Binding
HOST=127.0.0.1
PORT=5000

# Gmail IMAP Configuration (Optional for email ticket ingestion)
GMAIL_USER=your_email@gmail.com
GMAIL_APP_PASSWORD=your_16_char_app_password
GMAIL_IMAP_HOST=imap.gmail.com
GMAIL_IMAP_PORT=993
```

> [!NOTE]
> On startup, the backend automatically reads and applies [`schema.sql`](file:///C:/Users/gambo/repos/khin-ticketing-system/schema.sql) to your database, creating the `ticketing_system` schema, tables, indexes, constraints, and default departments without requiring manual SQL execution.

### Step 6: Launch the Application

You can launch the entire system using the provided runner script:

```bash
python run.py
```

The launcher will verify the virtual environment and start Uvicorn:
```
============================================================
Khin Ticket System Starting...
Server URL: http://127.0.0.1:5000
API Docs:   http://127.0.0.1:5000/docs
============================================================
INFO:     Started server process
INFO:     PostgreSQL connection pool initialized successfully.
INFO:     Applying database schema...
INFO:     Database schema applied successfully.
INFO:     Uvicorn running on http://127.0.0.1:5000 (Press CTRL+C to quit)
```

Open your browser and navigate to:
**[http://127.0.0.1:5000](http://127.0.0.1:5000)**

### Step 7: Initial Super Admin Account Setup
1. Open [http://127.0.0.1:5000](http://127.0.0.1:5000) and click **"Create one here"**.
2. Complete Step 1 and Step 2 of registration.
3. According to [`schema.sql`](file:///C:/Users/gambo/repos/khin-ticketing-system/schema.sql#L26), the first registered account (`id = 1`) or any account marked with `role = 'admin'` is automatically designated as **`super_admin`** with full management permissions.
4. If you register additional accounts, log into your first account to access the **Users & Hierarchy** dashboard tab and promote team members to `tech_member` or `dept_agent`.

---

## 🔧 Environment Variables Reference

| Variable Name | Required | Default Value | Description & Security Guidance |
| :--- | :---: | :--- | :--- |
| `DATABASE_URL` | **Yes** | `postgresql://postgres:postgres@localhost:5432/ticketing_db` | Full PostgreSQL connection URI. Use port `6543` with `?sslmode=require` for Supabase pooler connections. |
| `JWT_SECRET` | **Yes** | None | Secret key used to cryptographically sign JWT tokens. In production, generate using `openssl rand -hex 32` or Python: `import secrets; secrets.token_hex(32)`. |
| `JWT_ALGORITHM` | No | `HS256` | Cryptographic algorithm for JWT encoding and decoding. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | `1440` (24 Hours) | Lifespan of access tokens before re-authentication is required. |
| `HOST` | No | `127.0.0.1` | Network interface IP address for the ASGI web server. |
| `PORT` | No | `5000` | Port on which FastAPI serves both API routes and static frontend files. |
| `GMAIL_USER` | No | Empty | Gmail address monitored for incoming support tickets. |
| `GMAIL_APP_PASSWORD` | No | Empty | 16-character Google App Password (spaces stripped automatically). |
| `GMAIL_IMAP_HOST` | No | `imap.gmail.com` | Hostname of Gmail's IMAP server. |
| `GMAIL_IMAP_PORT` | No | `993` | Port for secure IMAP SSL communication. |

---

## 🎨 Theme, UI & Design System

The application features a tailored dark glassmorphic design system configured in [`frontend/css/style.css`](file:///C:/Users/gambo/repos/khin-ticketing-system/frontend/css/style.css):

### Color Palette

| Name | Hex Code | CSS Variable | Application in UI |
| :--- | :---: | :--- | :--- |
| **Pitch Black** | `#0a0a0a` | `--bg-pitch-black` | Top headers, outer boundary containers, select inputs |
| **Near Black** | `#121212` | `--bg-near-black` | Main dashboard body background, auth page canvas |
| **Deep Charcoal**| `#1c1c1c` | `--bg-deep-charcoal`| Sidebar navigation, card containers, dialog modals |
| **Warm Yellow** | `#facc15` | `--accent-yellow` | Primary buttons, brand accents, active filters, open status badges |
| **Amber Hover** | `#eab308` | `--accent-yellow-hover`| Hover and focus states on controls and action items |
| **Success Green**| `#4ade80` | `--success-green` | Resolved tickets, successful toast alerts |
| **Error Red** | `#f87171` | `--error-red` | Urgent priority tags, restriction warnings, error toasts |
| **Text Primary** | `#ffffff` | `--text-primary` | High-contrast headings, card titles, and active text |
| **Text Muted** | `#6b7280` | `--text-muted` | Metadata labels, helper notes, timestamps |

### Dual Frontend Portals

1. **Requester Portal ([`frontend/portal.html`](file:///C:/Users/gambo/repos/khin-ticketing-system/frontend/portal.html))**:
   - Targeted at standard corporate employees.
   - Clean, focused interface displaying personal ticket statistics (Total, Open, In Progress, Resolved).
   - "Raise a Ticket" workflow with live department restriction checking.
   - Public activity stream only; internal IT chatter is completely omitted.
2. **Staff Dashboard ([`frontend/dashboard.html`](file:///C:/Users/gambo/repos/khin-ticketing-system/frontend/dashboard.html))**:
   - Targeted at Tech Members, Department Agents, and Super Admins.
   - Comprehensive multi-view layout:
     - **Overview & Tickets**: Global filter toolbar, status tabs, department filters, search, and Gmail sync trigger.
     - **Departments Management**: Add department queues and configure employee restriction rules.
     - **Users & Hierarchy**: Manage roles, promote Super Admins, and delegate department management permissions.
   - Ticket details drawer with assignee dropdown, status/priority controls, internal notes vs public replies, and department transfer controls.

---

## 🔒 Security Architecture & Audit Integrity

1. **Zero-Trust Role Enforcement**: Public registrations cannot escalate privileges via client-side payload manipulation; the backend hardcodes `role = 'employee'` and `can_manage_departments = false` during registration.
2. **Bcrypt Password Salting**: Passwords are never stored in plaintext. Adaptive salt rounds ensure resistance against specialized hardware attacks.
3. **Stateless JWT Authorization**: Bearer tokens carry cryptographic signatures verified on every API request.
4. **SQL Injection Immunity**: All database transactions utilize parameterized SQL cursors (`%s`), completely isolating input strings from SQL commands.
5. **Data Isolation**: Department agents cannot inspect tickets belonging to other divisions; employees cannot view tickets submitted by other users.
6. **Internal Notes Confidentiality**: Database queries explicitly filter `AND c.is_internal = FALSE` when the requesting user is an employee, preventing accidental leakage of sensitive technical troubleshooting.
7. **Immutable Audit Trail**: The application strictly prohibits ticket deletion (`DELETE /api/tickets/{id}` returns `403 Forbidden`). Ticket codes (`KT-XXXX`) and email message IDs (`Message-ID`) are uniquely constrained, ensuring complete traceability.

---

## 📄 License & Attribution

Developed as an enterprise-grade ticketing and workflow system. Designed for scalable corporate deployment and technical evaluation. All rights reserved.

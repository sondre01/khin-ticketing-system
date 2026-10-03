-- Database schema for the Khin Ticket System

-- Create Schema for scoping the Ticketing System
CREATE SCHEMA IF NOT EXISTS ticketing_system;

-- Users table within the custom schema
CREATE TABLE IF NOT EXISTS ticketing_system.users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(100) NOT NULL,
    role VARCHAR(30) DEFAULT 'employee' NOT NULL, -- 'super_admin', 'tech_member', 'dept_agent', 'employee'
    department VARCHAR(100) DEFAULT 'General',
    position VARCHAR(100) DEFAULT 'Employee',
    can_manage_departments BOOLEAN DEFAULT FALSE,
    is_verified BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Ensure columns exist if table was already created
ALTER TABLE ticketing_system.users ADD COLUMN IF NOT EXISTS role VARCHAR(30) DEFAULT 'employee';
ALTER TABLE ticketing_system.users ADD COLUMN IF NOT EXISTS department VARCHAR(100) DEFAULT 'General';
ALTER TABLE ticketing_system.users ADD COLUMN IF NOT EXISTS position VARCHAR(100) DEFAULT 'Employee';
ALTER TABLE ticketing_system.users ADD COLUMN IF NOT EXISTS can_manage_departments BOOLEAN DEFAULT FALSE;
ALTER TABLE ticketing_system.users ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT TRUE;

-- Email Verifications table for registration verification
CREATE TABLE IF NOT EXISTS ticketing_system.email_verifications (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    otp_code VARCHAR(10) NOT NULL,
    token VARCHAR(255),
    purpose VARCHAR(30) DEFAULT 'registration' NOT NULL,
    payload JSONB,
    attempts INTEGER DEFAULT 0,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_email_verifications_email ON ticketing_system.email_verifications(email);
CREATE INDEX IF NOT EXISTS idx_email_verifications_token ON ticketing_system.email_verifications(token);
CREATE INDEX IF NOT EXISTS idx_email_verifications_expires_at ON ticketing_system.email_verifications(expires_at);

-- Default first registered user or admin to super_admin (Developer Team Lead in Software Development / Engineering)
UPDATE ticketing_system.users 
SET role = 'super_admin', 
    department = 'Software Development / Engineering', 
    position = 'Developer Team Lead', 
    can_manage_departments = TRUE 
WHERE id = 1 OR role = 'admin' OR email = 'gamboa.khinandrei@gmail.com';

UPDATE ticketing_system.users SET role = 'tech_member' WHERE role = 'agent';
UPDATE ticketing_system.users SET role = 'employee' WHERE role IN ('customer', 'user');

-- Index for faster login lookups by email
CREATE INDEX IF NOT EXISTS idx_users_email ON ticketing_system.users(email);

-- Departments table
CREATE TABLE IF NOT EXISTS ticketing_system.departments (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Seed default departments if not present
INSERT INTO ticketing_system.departments (name, description)
VALUES 
    -- Technology & Product
    ('Information Technology (IT)', 'Manages networks, hardware, cloud servers, cybersecurity, and internal tech support.'),
    ('Software Development / Engineering', 'Writes code, builds software applications, maintains databases, and develops products.'),
    ('Product Management', 'Defines the product strategy, roadmaps, and features that developers need to build.'),
    ('Data & Analytics', 'Analyzes corporate and user data to guide business decisions and manage data pipelines.'),
    -- Revenue & Customer Growth
    ('Marketing', 'Drives brand awareness, manages advertising campaigns, handles social media, and generates leads.'),
    ('Sales', 'Converts leads into paying clients, manages customer accounts, and directly drives revenue.'),
    ('Customer Success / Support', 'Helps clients use the product successfully and resolves their ongoing issues.'),
    -- Business Operations & Infrastructure
    ('Operations', 'Oversees the daily machinery of the business, logistics, supply chain, and facilities.'),
    ('Human Resources (HR)', 'Handles recruitment, onboarding, payroll, employee benefits, and workplace culture.'),
    ('Finance & Accounting', 'Manages corporate budgets, financial forecasting, bookkeeping, and tax compliance.'),
    ('Legal & Compliance', 'Reviews contracts, protects intellectual property, and ensures adherence to industry regulations.'),
    ('Procurement', 'Sources and purchases the external goods, software licenses, and services the company needs.'),
    -- Strategy & Innovation
    ('Research & Development (R&D)', 'Conducts scientific or technical research to create entirely new products or systems.'),
    ('Corporate Strategy', 'Focuses on long-term growth, mergers and acquisitions, and high-level partnerships.')
ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description, is_active = TRUE;

-- Tickets table
CREATE TABLE IF NOT EXISTS ticketing_system.tickets (
    id SERIAL PRIMARY KEY,
    ticket_code VARCHAR(20) UNIQUE NOT NULL,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    requester_email VARCHAR(255) NOT NULL,
    requester_name VARCHAR(100),
    requester_id INTEGER REFERENCES ticketing_system.users(id) ON DELETE SET NULL,
    department_id INTEGER REFERENCES ticketing_system.departments(id) ON DELETE SET NULL,
    department_name VARCHAR(100) DEFAULT 'Information Technology',
    source VARCHAR(50) DEFAULT 'portal',           -- 'email' or 'portal'
    status VARCHAR(50) DEFAULT 'open',            -- 'open', 'in_progress', 'resolved', 'closed'
    priority VARCHAR(20) DEFAULT 'medium',        -- 'low', 'medium', 'high', 'urgent'
    assigned_to INTEGER REFERENCES ticketing_system.users(id) ON DELETE SET NULL,
    email_message_id VARCHAR(255) UNIQUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Ensure department columns exist on tickets
ALTER TABLE ticketing_system.tickets ADD COLUMN IF NOT EXISTS department_id INTEGER REFERENCES ticketing_system.departments(id) ON DELETE SET NULL;
ALTER TABLE ticketing_system.tickets ADD COLUMN IF NOT EXISTS department_name VARCHAR(100) DEFAULT 'Information Technology';
ALTER TABLE ticketing_system.tickets ADD COLUMN IF NOT EXISTS requester_id INTEGER REFERENCES ticketing_system.users(id) ON DELETE SET NULL;

-- Indexes for efficient ticket searching, filtering and sorting
CREATE INDEX IF NOT EXISTS idx_tickets_status ON ticketing_system.tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_assigned_to ON ticketing_system.tickets(assigned_to);
CREATE INDEX IF NOT EXISTS idx_tickets_requester_id ON ticketing_system.tickets(requester_id);
CREATE INDEX IF NOT EXISTS idx_tickets_dept_id ON ticketing_system.tickets(department_id);
CREATE INDEX IF NOT EXISTS idx_tickets_ticket_code ON ticketing_system.tickets(ticket_code);
CREATE INDEX IF NOT EXISTS idx_tickets_created_at ON ticketing_system.tickets(created_at DESC);

-- Ticket Comments / Activity table
CREATE TABLE IF NOT EXISTS ticketing_system.ticket_comments (
    id SERIAL PRIMARY KEY,
    ticket_id INTEGER REFERENCES ticketing_system.tickets(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES ticketing_system.users(id) ON DELETE SET NULL,
    comment_text TEXT NOT NULL,
    is_internal BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_comments_ticket_id ON ticketing_system.ticket_comments(ticket_id);

-- Department Access Restrictions table
-- Allows managers to restrict specific users from raising tickets to certain departments
CREATE TABLE IF NOT EXISTS ticketing_system.department_restrictions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES ticketing_system.users(id) ON DELETE CASCADE,
    department_id INTEGER NOT NULL REFERENCES ticketing_system.departments(id) ON DELETE CASCADE,
    restricted_by INTEGER REFERENCES ticketing_system.users(id) ON DELETE SET NULL,
    reason TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, department_id)
);

CREATE INDEX IF NOT EXISTS idx_dept_restrictions_user_id ON ticketing_system.department_restrictions(user_id);
CREATE INDEX IF NOT EXISTS idx_dept_restrictions_dept_id ON ticketing_system.department_restrictions(department_id);


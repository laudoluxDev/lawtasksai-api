"""
LawTasksAI API
FastAPI backend for skill delivery, licensing, and usage tracking.
"""

from fastapi import FastAPI, HTTPException, Depends, Header, BackgroundTasks, Request, APIRouter, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime, timedelta
import os
import secrets
import hashlib
import io
import zipfile
import json
import asyncio
import stripe
import anthropic
import re

# Database imports (using async SQLAlchemy)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, Boolean, DateTime, Text, ForeignKey, select, update, func, or_, text
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSONB
import uuid
import base64
from io import BytesIO
import httpx

# Document generation (install: pip install python-docx openpyxl)
try:
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

try:
    from openpyxl import Workbook
    XLSX_AVAILABLE = True
except ImportError:
    XLSX_AVAILABLE = False

# ============================================
# Configuration
# ============================================

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/lawtasksai")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
API_BASE_URL = os.getenv("API_BASE_URL", "https://api.taskvaultai.com")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://lawtasksai.com")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Admin authentication
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")  # Set via Cloud Run env var — never hardcode

def verify_admin(x_admin_secret: Optional[str] = Header(None, alias="X-Admin-Secret")):
    """Dependency that enforces admin secret on all /admin/* routes."""
    if not ADMIN_SECRET:
        raise HTTPException(status_code=500, detail="Admin secret not configured on server")
    if x_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

# Loader versioning
CURRENT_LOADER_VERSION = "1.6.0"
LOADER_UPDATE_URL = "https://lawtasksai.com/download"
LOADER_UPDATE_MESSAGE = None  # Set to a string when there's an important update

# Path to the loader SKILL.md  [rebuilt 20260322T163027Z] (served for auto-update)
LOADER_SKILL_PATH = os.path.join(os.path.dirname(__file__), "loader", "SKILL.md")

# Initialize Stripe
stripe.api_key = STRIPE_SECRET_KEY

# Initialize Anthropic client
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

# ============================================
# Database Models
# ============================================

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(255))
    firm_name: Mapped[Optional[str]] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    credits_balance: Mapped[int] = mapped_column(Integer, default=0)
    version_policy: Mapped[str] = mapped_column(String(20), default="latest")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # Multi-tenancy: which product this user belongs to
    product_id: Mapped[Optional[str]] = mapped_column(String(50), default="law")
    # Profile for document generation (firm info, letterhead, etc.)
    profile: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict)

class Category(Base):
    __tablename__ = "categories"
    
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    product_id: Mapped[str] = mapped_column(String(50), default="law")

class Skill(Base):
    __tablename__ = "skills"
    
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    category_id: Mapped[str] = mapped_column(String(50), ForeignKey("categories.id"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    current_version: Mapped[Optional[str]] = mapped_column(String(20))
    stable_version: Mapped[Optional[str]] = mapped_column(String(20))
    credits_per_use: Mapped[int] = mapped_column(Integer, default=1)
    requires_upload: Mapped[bool] = mapped_column(Boolean, default=False)
    execution_type: Mapped[str] = mapped_column(String(20), default="server")
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deprecated: Mapped[bool] = mapped_column(Boolean, default=False)
    triggers: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), default=list)
    product_id: Mapped[str] = mapped_column(String(50), default="law")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class SkillVersion(Base):
    __tablename__ = "skill_versions"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_id: Mapped[str] = mapped_column(String(100), ForeignKey("skills.id", ondelete="CASCADE"))
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    config_template: Mapped[Optional[dict]] = mapped_column(JSONB)
    changelog: Mapped[Optional[str]] = mapped_column(Text)
    is_stable: Mapped[bool] = mapped_column(Boolean, default=False)
    is_beta: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class ContentPage(Base):
    """Editable content pages (security guide, etc.) with version control."""
    __tablename__ = "content_pages"
    
    slug: Mapped[str] = mapped_column(String(100), primary_key=True)  # e.g. "openclaw-security-guide"
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[str] = mapped_column(Text, nullable=False)  # Current HTML content
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class ContentPageVersion(Base):
    """Version history for content pages."""
    __tablename__ = "content_page_versions"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    page_slug: Mapped[str] = mapped_column(String(100), ForeignKey("content_pages.slug", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    changelog: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class License(Base):
    __tablename__ = "licenses"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    license_key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(20), nullable=False)  # trial, credits, subscription, enterprise
    status: Mapped[str] = mapped_column(String(20), default="active")  # active, expired, revoked, suspended
    valid_from: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    valid_until: Mapped[Optional[datetime]] = mapped_column(DateTime)
    skills_allowed: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String))
    categories_allowed: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String))
    usage_limit: Mapped[Optional[int]] = mapped_column(Integer)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    credits_purchased: Mapped[int] = mapped_column(Integer, default=0)
    credits_remaining: Mapped[int] = mapped_column(Integer, default=0)
    product_id: Mapped[Optional[str]] = mapped_column(String(50), default="law")
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

class UsageLog(Base):
    __tablename__ = "usage_logs"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    license_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("licenses.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    skill_id: Mapped[str] = mapped_column(String(100), ForeignKey("skills.id"))
    skill_version: Mapped[str] = mapped_column(String(20))
    executed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    execution_time_ms: Mapped[Optional[int]] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    credits_used: Mapped[int] = mapped_column(Integer, default=1)
    tokens_input: Mapped[Optional[int]] = mapped_column(Integer)
    tokens_output: Mapped[Optional[int]] = mapped_column(Integer)
    # Store result for document regeneration (no extra credit charge)
    result_text: Mapped[Optional[str]] = mapped_column(Text)

class CreditTransaction(Base):
    __tablename__ = "credit_transactions"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    license_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("licenses.id"))
    type: Mapped[str] = mapped_column(String(20), nullable=False)  # purchase, usage, refund, bonus
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_id: Mapped[Optional[str]] = mapped_column(String(100))
    description: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SkillGap(Base):
    """
    Anonymous gap reports submitted when a user's legal question matches no skill.
    No user identity, no query content — only the search terms that failed to match.
    Submitted only with explicit per-request user consent.
    """
    __tablename__ = "skill_gaps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    search_terms: Mapped[str] = mapped_column(Text, nullable=False)       # space-separated keywords
    loader_version: Mapped[Optional[str]] = mapped_column(String(20))     # which loader version reported
    reported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

# ============================================
# Database Session
# ============================================

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_db():
    async with async_session() as session:
        yield session


def get_product_id(
    x_product_id: Optional[str] = Header(None, alias="X-Product-ID"),
    product: Optional[str] = Query(None, alias="product"),
) -> str:
    """
    Resolve product_id from (in priority order):
    1. X-Product-ID request header
    2. ?product= query parameter
    3. Fallback: 'law'
    """
    return x_product_id or product or "law"

# ============================================
# Pydantic Schemas
# ============================================

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: Optional[str] = None
    firm_name: Optional[str] = None
    product_id: Optional[str] = None  # Multi-tenancy: which product they registered from

class UserResponse(BaseModel):
    id: str
    email: str
    name: Optional[str]
    firm_name: Optional[str]
    credits_balance: int
    created_at: datetime

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    license_key: str
    product_id: Optional[str] = "law"  # Multi-tenancy: which product this user belongs to

class SkillResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    category_id: str
    current_version: Optional[str]
    credits_per_use: int
    requires_upload: bool
    execution_type: str
    confidentiality_note: Optional[str] = None  # Warning for sensitive data handling

class SkillExecuteRequest(BaseModel):
    query: str  # User's input/question
    context: Optional[dict] = None  # Additional context (optional)
    version: Optional[str] = None  # None = use policy (latest/stable)

class LoaderMeta(BaseModel):
    """Metadata about loader updates - included in responses when relevant."""
    loader_current: str  # Current available loader version
    update_available: bool
    update_message: Optional[str] = None
    update_url: str

class SkillExecuteResponse(BaseModel):
    skill_id: str
    version: str
    result: str  # AI-generated result (not the prompt!)
    credits_remaining: int
    credits_used: int
    meta: Optional[LoaderMeta] = None  # Loader update hints

class SkillSchemaResponse(BaseModel):
    """Response for local execution skills - returns schema instead of executing."""
    skill_id: str
    skill_name: str
    version: str
    schema: str  # The expert prompt/framework for local AI to apply
    required_inputs: Optional[dict] = None  # Expected inputs (from YAML if available)
    credits_remaining: int
    credits_used: int
    instructions: str  # How to apply the schema locally
    meta: Optional[LoaderMeta] = None

class CreditBalanceResponse(BaseModel):
    credits_balance: int
    license_key: str
    license_type: str
    valid_until: Optional[datetime]

class PurchaseCreditsRequest(BaseModel):
    pack: str  # 'trial', 'essentials', 'accelerator', 'efficient', 'unstoppable', 'apex'
    email: Optional[str] = None  # Required for trial pack (one-time offer)
    attorney_name: Optional[str] = None
    bar_jurisdiction: Optional[str] = None   # e.g. "Colorado", "TX", "Federal"
    bar_number: Optional[str] = None
    firm_name: Optional[str] = None
    attestation: Optional[bool] = False      # True = user checked "I am a licensed attorney"

class UsageResponse(BaseModel):
    skill_id: str
    skill_name: str
    executed_at: datetime
    success: bool
    credits_used: int

# ============================================
# Profile & Document Schemas
# ============================================

class UserProfile(BaseModel):
    """User profile for document generation."""
    firm_name: Optional[str] = None
    attorney_name: Optional[str] = None
    attorney_bar: Optional[str] = None
    bar_jurisdiction: Optional[str] = None   # e.g. "Colorado", "TX", "Federal"
    bar_number: Optional[str] = None
    paralegal_name: Optional[str] = None
    address: Optional[str] = None
    city_state_zip: Optional[str] = None
    phone: Optional[str] = None
    fax: Optional[str] = None
    email: Optional[str] = None
    logo_url: Optional[str] = None

class ProfileResponse(BaseModel):
    profile: UserProfile
    missing_fields: List[str] = []

class ProfileUpdateRequest(BaseModel):
    """Update profile (merges with existing)."""
    firm_name: Optional[str] = None
    attorney_name: Optional[str] = None
    attorney_bar: Optional[str] = None
    bar_jurisdiction: Optional[str] = None   # e.g. "Colorado", "TX", "Federal"
    bar_number: Optional[str] = None
    paralegal_name: Optional[str] = None
    address: Optional[str] = None
    city_state_zip: Optional[str] = None
    phone: Optional[str] = None
    fax: Optional[str] = None
    email: Optional[str] = None
    logo_url: Optional[str] = None

class DocumentAttachment(BaseModel):
    """A generated document attachment."""
    filename: str
    content_type: str
    data: str  # Base64-encoded document content

class SkillExecuteResponseWithDocs(BaseModel):
    """Extended response with optional document attachments."""
    skill_id: str
    version: str
    result: str
    credits_remaining: int
    credits_used: int
    documents: Optional[List[DocumentAttachment]] = None
    needs_profile: Optional[List[str]] = None  # Profile fields needed before execution
    meta: Optional[LoaderMeta] = None

# ============================================
# Helper Functions
# ============================================

def hash_password(password: str) -> str:
    """Hash password with salt."""
    salt = secrets.token_hex(16)
    pw_hash = hashlib.sha256((password + salt).encode()).hexdigest()
    return f"{salt}:{pw_hash}"

def verify_password(password: str, stored_hash: str) -> bool:
    """Verify password against stored hash."""
    try:
        salt, pw_hash = stored_hash.split(":")
        return hashlib.sha256((password + salt).encode()).hexdigest() == pw_hash
    except:
        return False

def generate_license_key() -> str:
    """Generate a unique license key."""
    return f"lt_{secrets.token_hex(16)}"

def generate_token(user_id: str, license_key: str) -> str:
    """Generate a simple token (in production, use JWT)."""
    data = f"{user_id}:{license_key}:{secrets.token_hex(8)}"
    return hashlib.sha256(data.encode()).hexdigest()[:32]

# Credit pack pricing
CREDIT_PACKS = {
    "tryit":       {"credits": 2,    "price_cents": 500,    "one_time": False, "name": "Try It"},
    "starter":     {"credits": 15,   "price_cents": 2900,   "one_time": False, "name": "Starter"},
    "pro":         {"credits": 60,   "price_cents": 9900,   "one_time": False, "name": "Pro"},
    "business":    {"credits": 150,  "price_cents": 19900,  "one_time": False, "name": "Business"},
    "power":       {"credits": 350,  "price_cents": 34900,  "one_time": False, "name": "Power"},
    "unlimited":   {"credits": 800,  "price_cents": 59900,  "one_time": False, "name": "Unlimited"},
    "enterprise":  {"credits": 2000, "price_cents": 99900,  "one_time": False, "name": "Enterprise"},
    # Legacy aliases
    "peek":        {"credits": 5,    "price_cents": 500,    "one_time": False, "name": "Peek"},
    "trial":       {"credits": 15,   "price_cents": 2900,   "one_time": False, "name": "Starter"},
    "essentials":  {"credits": 60,   "price_cents": 9900,   "one_time": False, "name": "Pro"},
    "accelerator": {"credits": 150,  "price_cents": 19900,  "one_time": False, "name": "Business"},
    "efficient":   {"credits": 350,  "price_cents": 34900,  "one_time": False, "name": "Power"},
    "unstoppable": {"credits": 800,  "price_cents": 59900,  "one_time": False, "name": "Unlimited"},
    "apex":        {"credits": 2000, "price_cents": 99900,  "one_time": False, "name": "Enterprise"},
}

# ============================================
# Document Generation Helpers
# ============================================

# Skills that should generate documents and their required profile fields
DOCUMENT_SKILLS = {
    "demand-letter-drafter": {
        "format": "docx",
        "required_profile": ["firm_name", "attorney_name", "address", "city_state_zip", "phone"],
        "filename_template": "demand-letter-{date}.docx"
    },
    "discovery-request-generator": {
        "format": "docx",
        "required_profile": ["firm_name", "attorney_name", "attorney_bar"],
        "filename_template": "discovery-requests-{date}.docx"
    },
    "subpoena-generator": {
        "format": "docx",
        "required_profile": ["firm_name", "attorney_name", "attorney_bar", "address", "city_state_zip", "phone"],
        "filename_template": "subpoena-{date}.docx"
    },
    "deposition-summarizer": {
        "format": "docx",
        "required_profile": [],  # No letterhead needed for internal summaries
        "filename_template": "deposition-summary-{date}.docx"
    },
    "sol-alert-system": {
        "format": "xlsx",
        "required_profile": [],
        "filename_template": "sol-tracker-{date}.xlsx"
    },
    "deadline-calculator": {
        "format": "xlsx",
        "required_profile": [],
        "filename_template": "deadline-calendar-{date}.xlsx"
    }
}


def check_profile_requirements(skill_id: str, profile: dict) -> List[str]:
    """Check if user profile has all required fields for a skill."""
    if skill_id not in DOCUMENT_SKILLS:
        return []
    
    required = DOCUMENT_SKILLS[skill_id].get("required_profile", [])
    missing = [field for field in required if not profile.get(field)]
    return missing


def generate_docx_with_letterhead(content: str, profile: dict, title: str = None) -> bytes:
    """Generate a Word document with firm letterhead, supporting markdown formatting."""
    if not DOCX_AVAILABLE:
        return None
    
    doc = Document()
    
    # Add letterhead if profile has firm info
    if profile.get("firm_name"):
        header = doc.sections[0].header
        header_para = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
        header_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # Firm name (bold, larger)
        run = header_para.add_run(profile.get("firm_name", ""))
        run.bold = True
        run.font.size = Pt(14)
        header_para.add_run("\n")
        
        # Address
        if profile.get("address"):
            header_para.add_run(profile["address"]).font.size = Pt(10)
            header_para.add_run("\n")
        if profile.get("city_state_zip"):
            header_para.add_run(profile["city_state_zip"]).font.size = Pt(10)
            header_para.add_run("\n")
        
        # Contact info
        contact_parts = []
        if profile.get("phone"):
            contact_parts.append(f"Tel: {profile['phone']}")
        if profile.get("fax"):
            contact_parts.append(f"Fax: {profile['fax']}")
        if profile.get("email"):
            contact_parts.append(profile["email"])
        if contact_parts:
            header_para.add_run(" | ".join(contact_parts)).font.size = Pt(9)
    
    # Add title if provided
    if title:
        title_para = doc.add_paragraph()
        title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = title_para.add_run(title)
        run.bold = True
        run.font.size = Pt(14)
        doc.add_paragraph()  # Spacing
    
    # Check if content contains headings (for TOC)
    has_headings = bool(re.search(r'^#{1,4}\s+.+', content, re.MULTILINE))
    
    # Add Table of Contents if headings exist
    if has_headings:
        # Create TOC using Word field codes
        toc_para = doc.add_paragraph()
        toc_para.alignment = WD_ALIGN_PARAGRAPH.LEFT
        
        # Add TOC field
        run = toc_para.add_run()
        fldChar = OxmlElement('w:fldChar')
        fldChar.set(qn('w:fldCharType'), 'begin')
        run._r.append(fldChar)
        
        instrText = OxmlElement('w:instrText')
        instrText.set(qn('xml:space'), 'preserve')
        instrText.text = 'TOC \\o "1-4" \\h \\z \\u'
        run._r.append(instrText)
        
        fldChar2 = OxmlElement('w:fldChar')
        fldChar2.set(qn('w:fldCharType'), 'separate')
        run._r.append(fldChar2)
        
        # Placeholder text shown before TOC is updated
        run2 = toc_para.add_run('[Table of Contents - right-click and select "Update Field" to populate]')
        run2.font.color.rgb = RGBColor(128, 128, 128)
        run2.font.italic = True
        
        fldChar3 = OxmlElement('w:fldChar')
        fldChar3.set(qn('w:fldCharType'), 'end')
        run3 = toc_para.add_run()
        run3._r.append(fldChar3)
        
        # Add spacing after TOC
        doc.add_paragraph()
        doc.add_paragraph()
    
    # Helper function to add text with inline bold formatting
    def add_text_with_formatting(paragraph, text):
        """Add text to paragraph with inline **bold** support."""
        # Pattern to match **bold text**
        pattern = r'\*\*(.+?)\*\*'
        last_end = 0
        
        for match in re.finditer(pattern, text):
            # Add text before the bold part
            if match.start() > last_end:
                paragraph.add_run(text[last_end:match.start()])
            
            # Add bold text
            bold_run = paragraph.add_run(match.group(1))
            bold_run.bold = True
            
            last_end = match.end()
        
        # Add remaining text after last bold part
        if last_end < len(text):
            paragraph.add_run(text[last_end:])
    
    # Process content line by line for better structure detection
    lines = content.split('\n')
    i = 0
    
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        
        # Skip empty lines
        if not stripped:
            i += 1
            continue
        
        # Detect markdown headings
        heading_match = re.match(r'^(#{1,4})\s+(.+)$', stripped)
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2)
            
            # Add heading with appropriate style
            heading_para = doc.add_heading(level=level)
            heading_para.text = heading_text
            i += 1
            continue
        
        # Detect horizontal rule
        if stripped in ['---', '___', '***']:
            # Add a paragraph with bottom border to simulate HR
            hr_para = doc.add_paragraph()
            hr_para.paragraph_format.space_after = Pt(12)
            hr_para.paragraph_format.space_before = Pt(12)
            i += 1
            continue
        
        # Detect bullet points (- or *)
        bullet_match = re.match(r'^[-*]\s+(.+)$', stripped)
        if bullet_match:
            bullet_text = bullet_match.group(1)
            bullet_para = doc.add_paragraph(style='List Bullet')
            add_text_with_formatting(bullet_para, bullet_text)
            i += 1
            continue
        
        # Detect numbered lists
        numbered_match = re.match(r'^(\d+)\.\s+(.+)$', stripped)
        if numbered_match:
            numbered_text = numbered_match.group(2)
            numbered_para = doc.add_paragraph(style='List Number')
            add_text_with_formatting(numbered_para, numbered_text)
            i += 1
            continue
        
        # Regular paragraph - collect until next empty line or special formatting
        para_lines = [line]
        i += 1
        
        while i < len(lines):
            next_line = lines[i].strip()
            
            # Stop if empty line or special formatting detected
            if not next_line or \
               re.match(r'^#{1,4}\s+', next_line) or \
               next_line in ['---', '___', '***'] or \
               re.match(r'^[-*]\s+', next_line) or \
               re.match(r'^\d+\.\s+', next_line):
                break
            
            para_lines.append(lines[i])
            i += 1
        
        # Add paragraph with inline formatting
        para_text = ' '.join(line.strip() for line in para_lines if line.strip())
        if para_text:
            p = doc.add_paragraph()
            add_text_with_formatting(p, para_text)
    
    # Add footer with attorney info
    if profile.get("attorney_name"):
        doc.add_paragraph()  # Spacing
        footer_para = doc.add_paragraph()
        footer_para.add_run(profile["attorney_name"])
        if profile.get("attorney_bar"):
            footer_para.add_run(f"\n{profile['attorney_bar']}")
    
    # Save to bytes
    buffer = BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer.read()


def generate_xlsx_from_content(content: str, title: str = None) -> bytes:
    """Generate an Excel spreadsheet from structured content."""
    if not XLSX_AVAILABLE:
        return None
    
    wb = Workbook()
    ws = wb.active
    ws.title = title or "Data"
    
    # Try to parse structured data from the content
    # Look for lines that look like table rows (contain | or tabs)
    lines = content.strip().split("\n")
    row_num = 1
    
    for line in lines:
        if "|" in line:
            # Markdown table format
            cells = [cell.strip() for cell in line.split("|") if cell.strip() and cell.strip() != "---"]
            if cells:
                for col_num, cell in enumerate(cells, 1):
                    ws.cell(row=row_num, column=col_num, value=cell)
                row_num += 1
        elif "\t" in line:
            # Tab-separated
            cells = line.split("\t")
            for col_num, cell in enumerate(cells, 1):
                ws.cell(row=row_num, column=col_num, value=cell.strip())
            row_num += 1
        elif line.strip():
            # Plain text - put in first column
            ws.cell(row=row_num, column=1, value=line.strip())
            row_num += 1
    
    # Auto-adjust column widths
    for column in ws.columns:
        max_length = 0
        column_letter = column[0].column_letter
        for cell in column:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass
        ws.column_dimensions[column_letter].width = min(max_length + 2, 50)
    
    # Save to bytes
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.read()


def generate_document(skill_id: str, content: str, profile: dict) -> Optional[DocumentAttachment]:
    """Generate appropriate document for a skill result."""
    if skill_id not in DOCUMENT_SKILLS:
        return None
    
    skill_doc_config = DOCUMENT_SKILLS[skill_id]
    doc_format = skill_doc_config["format"]
    
    # Generate filename with today's date
    from datetime import date
    filename = skill_doc_config["filename_template"].format(date=date.today().isoformat())
    
    if doc_format == "docx":
        doc_bytes = generate_docx_with_letterhead(content, profile, title=None)
        if doc_bytes:
            return DocumentAttachment(
                filename=filename,
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                data=base64.b64encode(doc_bytes).decode("utf-8")
            )
    
    elif doc_format == "xlsx":
        doc_bytes = generate_xlsx_from_content(content, title=skill_id.replace("-", " ").title())
        if doc_bytes:
            return DocumentAttachment(
                filename=filename,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                data=base64.b64encode(doc_bytes).decode("utf-8")
            )
    
    return None

# ============================================
# FastAPI App
# ============================================

app = FastAPI(
    title="LawTasksAI API",
    description="Skill delivery, licensing, and usage tracking for legal AI automation",
    version="1.0.0"
)

# Admin router — all routes require X-Admin-Secret header
admin_router = APIRouter(
    prefix="/admin",
    include_in_schema=False,
    dependencies=[Depends(verify_admin)]
)

# Auto-create tables on startup
@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# Auth Dependency
# ============================================

async def get_current_license(
    authorization: str = Header(...),
    db: AsyncSession = Depends(get_db)
) -> License:
    """Validate license key from Authorization header."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    
    license_key = authorization.replace("Bearer ", "")
    
    result = await db.execute(
        select(License).where(
            License.license_key == license_key,
            License.status == "active"
        )
    )
    license = result.scalar_one_or_none()
    
    if not license:
        raise HTTPException(status_code=401, detail="Invalid or inactive license")
    
    # Check expiry
    if license.valid_until and license.valid_until < datetime.utcnow():
        raise HTTPException(status_code=401, detail="License expired")
    
    return license

# ============================================
# Routes: Auth
# ============================================

@app.post("/auth/register", response_model=UserResponse)
async def register(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
    product_id: str = Depends(get_product_id),
):
    """Create a new user account."""
    # Check if email exists
    result = await db.execute(select(User).where(User.email == user_data.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    # Resolve product_id: body field takes priority (explicit), then dependency (header/query/default)
    resolved_product_id = user_data.product_id or product_id

    # Generate user ID explicitly so it's available for license FK before flush
    user_id = uuid.uuid4()

    # Create user
    user = User(
        id=user_id,
        email=user_data.email,
        password_hash=hash_password(user_data.password),
        name=user_data.name,
        firm_name=user_data.firm_name,
        credits_balance=5,  # Free signup credits
        product_id=resolved_product_id,
    )
    db.add(user)
    await db.flush()  # Ensure user row exists before license FK insert

    # Create trial license
    license = License(
        license_key=generate_license_key(),
        user_id=user_id,
        type="trial",
        valid_until=datetime.utcnow() + timedelta(days=14),
        credits_purchased=5,
        credits_remaining=5
    )
    db.add(license)
    
    await db.commit()
    await db.refresh(user)
    
    return UserResponse(
        id=str(user.id),
        email=user.email,
        name=user.name,
        firm_name=user.firm_name,
        credits_balance=user.credits_balance,
        created_at=user.created_at
    )

@app.post("/auth/login", response_model=TokenResponse)
async def login(credentials: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Login and get access token."""
    result = await db.execute(select(User).where(User.email == credentials.email))
    user = result.scalar_one_or_none()
    
    if not user or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    # Get active license
    result = await db.execute(
        select(License).where(
            License.user_id == user.id,
            License.status == "active"
        ).order_by(License.created_at.desc())
    )
    license = result.scalar_one_or_none()
    
    if not license:
        raise HTTPException(status_code=401, detail="No active license found")
    
    # Update last login
    user.last_login = datetime.utcnow()
    await db.commit()
    
    return TokenResponse(
        access_token=generate_token(str(user.id), license.license_key),
        license_key=license.license_key,
        product_id=user.product_id or "law",
    )

class RecoverLicenseRequest(BaseModel):
    email: EmailStr

class RecoverLicenseResponse(BaseModel):
    email: str
    license_key: str
    credits_remaining: int
    license_type: str
    message: str

@app.post("/auth/recover-license", response_model=RecoverLicenseResponse)
async def recover_license(request: RecoverLicenseRequest, db: AsyncSession = Depends(get_db)):
    """
    Recover license key by email.
    For customers who lost their config or reinstalled.
    """
    # Find user by email
    result = await db.execute(select(User).where(User.email == request.email))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="No account found with this email")
    
    # Get active license
    result = await db.execute(
        select(License).where(
            License.user_id == user.id,
            License.status == "active"
        ).order_by(License.created_at.desc())
    )
    license = result.scalar_one_or_none()
    
    if not license:
        raise HTTPException(status_code=404, detail="No active license found for this account")
    
    return RecoverLicenseResponse(
        email=user.email,
        license_key=license.license_key,
        credits_remaining=license.credits_remaining,
        license_type=license.type,
        message="Your license key has been recovered. Update your config.json with this key."
    )

# ============================================
# Routes: Skills
# ============================================

@app.get("/skills", response_model=List[SkillResponse])
@app.get("/v1/skills", response_model=List[SkillResponse])
async def list_skills(
    category: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    product_id: str = Depends(get_product_id),
):
    """List all available skills, optionally filtered by product."""
    query = select(Skill).where(Skill.is_published == True)
    if category:
        query = query.where(Skill.category_id == category)
    # Always filter by product_id — defaults to 'law' for backward compat.
    query = query.where(Skill.product_id == product_id)
    
    result = await db.execute(query.order_by(Skill.category_id, Skill.name))
    skills = result.scalars().all()
    
    # Keywords that suggest document/data processing
    sensitive_keywords = ['analyzer', 'summarizer', 'reviewer', 'examiner', 'auditor', 
                          'parser', 'extractor', 'scanner', 'checker', 'drafter']
    
    def get_confidentiality_note(skill):
        name_lower = skill.name.lower()
        if skill.requires_upload:
            return "⚠️ Uploads document content to server for processing"
        if any(kw in name_lower for kw in sensitive_keywords):
            return "⚠️ May process sensitive text on server"
        if skill.execution_type == 'local':
            return "🔒 Runs locally — data stays on your machine"
        return None
    
    return [
        SkillResponse(
            id=s.id,
            name=s.name,
            description=s.description,
            category_id=s.category_id,
            current_version=s.current_version,
            credits_per_use=s.credits_per_use,
            requires_upload=s.requires_upload,
            execution_type=s.execution_type,
            confidentiality_note=get_confidentiality_note(s)
        )
        for s in skills
    ]

@app.get("/skills/triggers")
@app.get("/v1/skills/triggers")
async def get_skill_triggers(
    product_id: str = Depends(get_product_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Get trigger phrases for local skill matching.
    This enables privacy-preserving skill discovery without sending queries to the server.
    Reads from database - skills with non-empty triggers arrays, filtered by product.
    """
    result = await db.execute(
        select(Skill.id, Skill.triggers).where(
            Skill.is_published == True,
            Skill.product_id == product_id,
            Skill.triggers != None,
            func.array_length(Skill.triggers, 1) > 0
        )
    )
    rows = result.all()
    
    # Build response in expected format
    triggers_dict = {}
    for skill_id, triggers in rows:
        if triggers:
            triggers_dict[skill_id] = {"triggers": triggers}
    
    return triggers_dict


@app.get("/skills/{skill_id}", response_model=SkillResponse)
@app.get("/v1/skills/{skill_id}", response_model=SkillResponse)
async def get_skill(skill_id: str, db: AsyncSession = Depends(get_db)):
    """Get skill details."""
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    
    # Get confidentiality note
    sensitive_keywords = ['analyzer', 'summarizer', 'reviewer', 'examiner', 'auditor', 
                          'parser', 'extractor', 'scanner', 'checker', 'drafter']
    name_lower = skill.name.lower()
    
    if skill.requires_upload:
        conf_note = "⚠️ Uploads document content to server for processing"
    elif any(kw in name_lower for kw in sensitive_keywords):
        conf_note = "⚠️ May process sensitive text on server"
    elif skill.execution_type == 'local':
        conf_note = "🔒 Runs locally — data stays on your machine"
    else:
        conf_note = None
    
    return SkillResponse(
        id=skill.id,
        name=skill.name,
        description=skill.description,
        category_id=skill.category_id,
        current_version=skill.current_version,
        credits_per_use=skill.credits_per_use,
        requires_upload=skill.requires_upload,
        execution_type=skill.execution_type,
        confidentiality_note=conf_note
    )

def check_loader_update(loader_version: Optional[str]) -> Optional[LoaderMeta]:
    """Check if loader needs update and return metadata if so."""
    if not loader_version:
        return None
    
    # Simple version comparison (assumes semver: x.y.z)
    try:
        current_parts = [int(x) for x in CURRENT_LOADER_VERSION.split('.')]
        client_parts = [int(x) for x in loader_version.split('.')]
        
        update_available = current_parts > client_parts
        
        # Only include meta if update is available or there's an important message
        if update_available or LOADER_UPDATE_MESSAGE:
            return LoaderMeta(
                loader_current=CURRENT_LOADER_VERSION,
                update_available=update_available,
                update_message=LOADER_UPDATE_MESSAGE,
                update_url=LOADER_UPDATE_URL
            )
    except (ValueError, AttributeError):
        pass  # Invalid version format, skip
    
    return None


@app.get("/skills/{skill_id}/schema", response_model=SkillSchemaResponse)
@app.get("/v1/skills/{skill_id}/schema", response_model=SkillSchemaResponse)
async def get_skill_schema(
    skill_id: str,
    license: License = Depends(get_current_license),
    db: AsyncSession = Depends(get_db),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    x_loader_version: Optional[str] = Header(None, alias="X-Loader-Version")
):
    """
    Get skill schema for LOCAL execution.
    
    For skills with execution_type='local', returns the expert framework/prompt
    so the client's AI can apply it to local documents. Documents never leave
    the user's machine.
    
    - Validates license and credits
    - Deducts credits (you pay for the expert knowledge)
    - Returns schema for local AI to apply
    - Only works for execution_type='local' skills
    
    All skills run locally — documents never leave the user's machine.
    """
    # Get skill
    result = await db.execute(select(Skill).where(Skill.id == skill_id, Skill.is_published == True))
    skill = result.scalar_one_or_none()
    
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    
    # Check skill access
    if license.skills_allowed and skill_id not in license.skills_allowed:
        raise HTTPException(status_code=403, detail="Skill not included in license")
    
    if license.categories_allowed and skill.category_id not in license.categories_allowed:
        raise HTTPException(status_code=403, detail="Category not included in license")
    
    # Check usage limit
    if license.usage_limit and license.usage_count >= license.usage_limit:
        raise HTTPException(status_code=403, detail="Usage limit reached")
    
    # Check credits
    if license.credits_remaining < skill.credits_per_use:
        raise HTTPException(status_code=402, detail="Insufficient credits")
    
    # Get current version
    version_query = select(SkillVersion).where(
        SkillVersion.skill_id == skill_id,
        SkillVersion.version == skill.current_version
    )
    result = await db.execute(version_query)
    skill_version = result.scalar_one_or_none()
    
    if not skill_version:
        raise HTTPException(status_code=404, detail="Skill version not found")
    
    # Deduct credits and increment usage
    license.credits_remaining -= skill.credits_per_use
    license.usage_count += 1
    
    # Log usage
    usage_log = UsageLog(
        license_id=license.id,
        user_id=license.user_id,
        skill_id=skill_id,
        skill_version=skill_version.version,
        credits_used=skill.credits_per_use,
        success=True,
    )
    db.add(usage_log)
    await db.commit()
    
    # Check for loader updates
    loader_meta = check_loader_update(x_loader_version)
    
    # Build instructions for local execution
    instructions = """
## Local Execution Instructions

This skill runs LOCALLY on your machine. LawTasksAI.com never sees your prompts, your client files, or your client data. Your documents stay local if using OpenClaw, or go to your LLM provider if using a cloud AI.

### How to use this schema:

1. **Load your document(s)** into the conversation (paste text, attach file, or reference local path)

2. **Apply this schema** by telling your AI:
   "Apply the following expert analysis framework to my document: [paste schema]"
   
   Or simply include the schema as context and ask your question naturally.

3. **The schema provides:**
   - Expert considerations and analysis frameworks
   - Step-by-step workflows
   - What to look for
   - How to structure the output
   - Common pitfalls to avoid

4. **Your AI will:**
   - Read your local document
   - Apply the expert framework
   - Generate analysis based on the schema
   - All processing happens on your machine

### Privacy Guarantee
Your document content is NEVER sent to LawTasksAI servers.
Only this schema was retrieved (general legal knowledge, not your data).
"""
    
    return SkillSchemaResponse(
        skill_id=skill_id,
        skill_name=skill.name,
        version=skill_version.version,
        schema=skill_version.content,  # The expert framework
        required_inputs=None,  # TODO: Parse from YAML if structured
        credits_remaining=license.credits_remaining,
        credits_used=skill.credits_per_use,
        instructions=instructions,
        meta=loader_meta
    )


# ============================================
# Routes: Credits & Billing
# ============================================

@app.get("/credits/balance", response_model=CreditBalanceResponse)
@app.get("/v1/credits/balance", response_model=CreditBalanceResponse)
async def get_credit_balance(
    license: License = Depends(get_current_license),
    db: AsyncSession = Depends(get_db)
):
    """Get current credit balance."""
    return CreditBalanceResponse(
        credits_balance=license.credits_remaining,
        license_key=license.license_key,
        license_type=license.type,
        valid_until=license.valid_until
    )

# ============================================
# Routes: Identity (GET /v1/me)
# ============================================

# Map license key prefixes → product metadata
# Longest prefix first so "mkt_" wins over hypothetical "m_" etc.
_VERTICAL_BY_PREFIX: list[tuple[str, dict]] = sorted([
    ("lt_",   {"product_id": "law",           "product_name": "LawTasksAI",           "display_name": "LawTasksAI",           "tool_prefix": "lawtasksai",       "occupation": "attorney",             "support_email": "support@lawtasksai.com"}),
    ("ct_",   {"product_id": "contractor",    "product_name": "ContractorTasksAI",   "display_name": "ContractorTasksAI",   "tool_prefix": "contractortasksai", "occupation": "contractor",           "support_email": "support@contractortasksai.com"}),
    ("rt_",   {"product_id": "realtor",       "product_name": "RealtorTasksAI",      "display_name": "RealtorTasksAI",      "tool_prefix": "realtortasksai",    "occupation": "real estate agent",   "support_email": "support@realtortasksai.com"}),
    ("mkt_",  {"product_id": "marketing",     "product_name": "MarketingTasksAI",    "display_name": "MarketingTasksAI",    "tool_prefix": "marketingtasksai",  "occupation": "marketer",            "support_email": "support@marketingtasksai.com"}),
    ("ft_",   {"product_id": "farmer",        "product_name": "FarmerTasksAI",       "display_name": "FarmerTasksAI",       "tool_prefix": "farmertasksai",     "occupation": "farmer",              "support_email": "support@farmertasksai.com"}),
    ("pt_",   {"product_id": "pastor",        "product_name": "PastorTasksAI",       "display_name": "PastorTasksAI",       "tool_prefix": "pastortasksai",     "occupation": "pastor",              "support_email": "support@pastortasksai.com"}),
    ("st_",   {"product_id": "salon",         "product_name": "SalonTasksAI",        "display_name": "SalonTasksAI",        "tool_prefix": "salontasksai",      "occupation": "salon owner",         "support_email": "support@salontasksai.com"}),
    ("tt_",   {"product_id": "teacher",       "product_name": "TeacherTasksAI",      "display_name": "TeacherTasksAI",      "tool_prefix": "teachertasksai",    "occupation": "teacher",             "support_email": "support@teachertasksai.com"}),
    ("tht_",  {"product_id": "therapist",     "product_name": "TherapistTasksAI",    "display_name": "TherapistTasksAI",    "tool_prefix": "therapisttasksai",  "occupation": "therapist",           "support_email": "support@therapisttasksai.com"}),
    ("ht_",   {"product_id": "hr",            "product_name": "HRTasksAI",           "display_name": "HRTasksAI",           "tool_prefix": "hrtasksai",         "occupation": "HR professional",     "support_email": "support@hrtasksai.com"}),
    ("tat_",  {"product_id": "travelagent",   "product_name": "TravelAgentTasksAI",  "display_name": "TravelAgentTasksAI",  "tool_prefix": "travelagenttasksai","occupation": "travel agent",        "support_email": "support@travelagenttasksai.com"}),
    ("dt_",   {"product_id": "dentist",       "product_name": "DentistTasksAI",      "display_name": "DentistTasksAI",      "tool_prefix": "dentisttasksai",    "occupation": "dentist",             "support_email": "support@dentisttasksai.com"}),
    ("chrt_", {"product_id": "chiropractor",  "product_name": "ChiropractorTasksAI", "display_name": "ChiropractorTasksAI", "tool_prefix": "chiropractortasksai","occupation": "chiropractor",       "support_email": "support@chiropractortasksai.com"}),
    ("it_",   {"product_id": "insurance",     "product_name": "InsuranceTasksAI",    "display_name": "InsuranceTasksAI",    "tool_prefix": "insurancetasksai",  "occupation": "insurance agent",    "support_email": "support@insurancetasksai.com"}),
    ("at_",   {"product_id": "accounting",    "product_name": "AccountingTasksAI",   "display_name": "AccountingTasksAI",   "tool_prefix": "accountingtasksai", "occupation": "accountant",          "support_email": "support@accountingtasksai.com"}),
    ("nt_",   {"product_id": "nutritionist",  "product_name": "NutritionistTasksAI", "display_name": "NutritionistTasksAI", "tool_prefix": "nutritionisttasksai","occupation": "nutritionist",       "support_email": "support@nutritionisttasksai.com"}),
    ("rest_", {"product_id": "restaurant",    "product_name": "RestaurantTasksAI",   "display_name": "RestaurantTasksAI",   "tool_prefix": "restauranttasksai", "occupation": "restaurant owner",   "support_email": "support@restauranttasksai.com"}),
    ("llt_",  {"product_id": "landlord",      "product_name": "LandlordTasksAI",     "display_name": "LandlordTasksAI",     "tool_prefix": "landlordtasksai",   "occupation": "landlord",            "support_email": "support@landlordtasksai.com"}),
    ("prt_",  {"product_id": "principal",     "product_name": "PrincipalTasksAI",    "display_name": "PrincipalTasksAI",    "tool_prefix": "principaltasksai",  "occupation": "school principal",    "support_email": "support@principaltasksai.com"}),
    ("mot_",  {"product_id": "mortuary",      "product_name": "MortuaryTasksAI",     "display_name": "MortuaryTasksAI",     "tool_prefix": "mortuarytasksai",   "occupation": "funeral director",    "support_email": "support@mortuarytasksai.com"}),
    ("evt_",  {"product_id": "eventplanner",  "product_name": "EventPlannerTasksAI", "display_name": "EventPlannerTasksAI", "tool_prefix": "eventplannertasksai","occupation": "event planner",      "support_email": "support@eventplannertasksai.com"}),
    ("cht_",  {"product_id": "church",        "product_name": "ChurchTasksAI",       "display_name": "ChurchTasksAI",       "tool_prefix": "churchtasksai",     "occupation": "church administrator","support_email": "support@churchtasksai.com"}),
    ("per_",  {"product_id": "personaltrainer","product_name": "PersonalTrainerTasksAI","display_name": "PersonalTrainerTasksAI","tool_prefix": "personaltrainertasksai","occupation": "personal trainer",  "support_email": "support@personaltrainertasksai.com"}),
    ("elt_",  {"product_id": "electrician",   "product_name": "ElectricianTasksAI",  "display_name": "ElectricianTasksAI",  "tool_prefix": "electriciantasksai","occupation": "electrician",        "support_email": "support@electriciantasksai.com"}),
    ("mgt_",  {"product_id": "mortgage",      "product_name": "MortgageTasksAI",     "display_name": "MortgageTasksAI",     "tool_prefix": "mortgagetasksai",   "occupation": "mortgage broker",    "support_email": "support@mortgagetasksai.com"}),
    ("plt_",  {"product_id": "plumber",       "product_name": "PlumberTasksAI",      "display_name": "PlumberTasksAI",      "tool_prefix": "plumbertasksai",    "occupation": "plumber",             "support_email": "support@plumbertasksai.com"}),
    ("vt_",   {"product_id": "vet",           "product_name": "VetTasksAI",          "display_name": "VetTasksAI",          "tool_prefix": "vettasksai",        "occupation": "veterinarian",        "support_email": "support@vettasksai.com"}),
    ("fun_",  {"product_id": "funeral",       "product_name": "FuneralTasksAI",      "display_name": "FuneralTasksAI",      "tool_prefix": "funeraltasksai",    "occupation": "funeral director",    "support_email": "support@funeraltasksai.com"}),
    ("des_",  {"product_id": "designer",      "product_name": "DesignerTasksAI",     "display_name": "DesignerTasksAI",     "tool_prefix": "designertasksai",   "occupation": "designer",            "support_email": "support@designertasksai.com"}),
    ("mst_",  {"product_id": "militaryspouse","product_name": "MilitarySpouseTasksAI","display_name": "MilitarySpouseTasksAI","tool_prefix": "militaryspousetasksai","occupation": "military spouse",  "support_email": "support@militaryspousetasksai.com"}),
], key=lambda x: -len(x[0]))


def _vertical_from_key(key: str) -> dict | None:
    """Return vertical metadata dict for a license key, or None if no prefix matches."""
    for prefix, meta in _VERTICAL_BY_PREFIX:
        if key.startswith(prefix):
            return meta
    return None


@app.get("/v1/me")
async def get_me(
    license: License = Depends(get_current_license),
    db: AsyncSession = Depends(get_db),
):
    """
    Return identity + vertical metadata for the calling license key.
    Used by the MCP server at startup to self-configure tool names and prompts.
    """
    meta = _vertical_from_key(license.license_key)
    if not meta:
        # Fallback: try products table via user's product_id
        user_result = await db.execute(
            text("SELECT product_id FROM users WHERE id = :uid"),
            {"uid": str(license.user_id)},
        )
        user_row = user_result.fetchone()
        product_id = user_row.product_id if user_row else "law"
        meta = {
            "product_id":    product_id,
            "product_name":  f"{product_id.title()}TasksAI",
            "display_name":  f"{product_id.title()}TasksAI",
            "tool_prefix":   f"{product_id}tasksai",
            "occupation":    product_id,
            "support_email": f"support@{product_id}tasksai.com",
        }

    # Fetch domain from products table if available
    prod_result = await db.execute(
        text("SELECT domain FROM products WHERE id = :pid AND is_active = TRUE"),
        {"pid": meta["product_id"]},
    )
    prod_row = prod_result.fetchone()
    domain = prod_row.domain if prod_row else f"{meta['product_id']}tasksai.com"

    return {
        **meta,
        "domain":            domain,
        "license_type":      license.type,
        "credits_remaining": license.credits_remaining,
    }


# ============================================
# Routes: Abbreviations (GET /v1/abbreviations)
# ============================================

@app.get("/v1/abbreviations")
async def get_abbreviations(
    license: License = Depends(get_current_license),
    db: AsyncSession = Depends(get_db),
):
    """
    Return abbreviation expansions for the calling license's vertical.
    Used by MCP server to populate _ABBREVS at startup.
    Response: {"product_id": "law", "abbreviations": {"tro": "temporary restraining order", ...}}
    """
    meta = _vertical_from_key(license.license_key)
    product_id = meta["product_id"] if meta else "law"

    result = await db.execute(
        text("SELECT abbreviation, expansion FROM skill_abbreviations WHERE product_id = :pid ORDER BY abbreviation"),
        {"pid": product_id},
    )
    rows = result.fetchall()

    return {
        "product_id":    product_id,
        "abbreviations": {row.abbreviation: row.expansion for row in rows},
        "count":         len(rows),
    }


@app.post("/credits/purchase")
async def purchase_credits(
    request: PurchaseCreditsRequest,
    license: License = Depends(get_current_license),
    db: AsyncSession = Depends(get_db)
):
    """
    Initiate credit purchase.
    Returns Stripe checkout URL (in production).
    For now, just adds credits directly.
    """
    if request.pack not in CREDIT_PACKS:
        raise HTTPException(status_code=400, detail="Invalid credit pack")
    
    pack = CREDIT_PACKS[request.pack]
    
    # In production: Create Stripe checkout session
    # For now: Add credits directly
    license.credits_remaining += pack["credits"]
    license.credits_purchased += pack["credits"]
    
    # Log transaction
    tx = CreditTransaction(
        user_id=license.user_id,
        license_id=license.id,
        type="purchase",
        amount=pack["credits"],
        balance_after=license.credits_remaining,
        description=f"Purchased {pack['name']} ({pack['credits']} credits)"
    )
    db.add(tx)
    
    await db.commit()
    
    return {
        "success": True,
        "credits_added": pack["credits"],
        "credits_balance": license.credits_remaining,
        "amount_charged": pack["price_cents"] / 100
    }

# ============================================
# Routes: Usage
# ============================================

@app.get("/usage", response_model=List[UsageResponse])
async def get_usage(
    limit: int = 50,
    license: License = Depends(get_current_license),
    db: AsyncSession = Depends(get_db)
):
    """Get usage history for current license."""
    result = await db.execute(
        select(UsageLog, Skill.name)
        .join(Skill, UsageLog.skill_id == Skill.id)
        .where(UsageLog.license_id == license.id)
        .order_by(UsageLog.executed_at.desc())
        .limit(limit)
    )
    
    return [
        UsageResponse(
            skill_id=log.skill_id,
            skill_name=skill_name,
            executed_at=log.executed_at,
            success=log.success,
            credits_used=log.credits_used
        )
        for log, skill_name in result.all()
    ]

# ============================================
# Routes: Profile
# ============================================

@app.get("/profile", response_model=ProfileResponse)
@app.get("/v1/profile", response_model=ProfileResponse)
async def get_profile(
    license: License = Depends(get_current_license),
    db: AsyncSession = Depends(get_db)
):
    """Get current user profile for document generation."""
    # Get user
    result = await db.execute(select(User).where(User.id == license.user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    profile_data = user.profile or {}
    
    # Check for commonly needed fields
    common_fields = ["firm_name", "attorney_name", "address", "city_state_zip", "phone"]
    missing = [f for f in common_fields if not profile_data.get(f)]
    
    return ProfileResponse(
        profile=UserProfile(**profile_data),
        missing_fields=missing
    )


@app.put("/profile", response_model=ProfileResponse)
@app.put("/v1/profile", response_model=ProfileResponse)
async def update_profile(
    profile_update: ProfileUpdateRequest,
    license: License = Depends(get_current_license),
    db: AsyncSession = Depends(get_db)
):
    """Update user profile (merges with existing data)."""
    # Get user
    result = await db.execute(select(User).where(User.id == license.user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Merge with existing profile
    current_profile = user.profile or {}
    update_data = profile_update.model_dump(exclude_none=True)
    current_profile.update(update_data)
    
    # Update in database
    user.profile = current_profile
    await db.commit()
    
    # Check for commonly needed fields
    common_fields = ["firm_name", "attorney_name", "address", "city_state_zip", "phone"]
    missing = [f for f in common_fields if not current_profile.get(f)]
    
    return ProfileResponse(
        profile=UserProfile(**current_profile),
        missing_fields=missing
    )


@app.get("/profile/check/{skill_id}")
@app.get("/v1/profile/check/{skill_id}")
async def check_profile_for_skill(
    skill_id: str,
    license: License = Depends(get_current_license),
    db: AsyncSession = Depends(get_db)
):
    """
    Check if user profile has required fields for a specific skill's document generation.
    Returns missing fields if any.
    """
    # Get user
    result = await db.execute(select(User).where(User.id == license.user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    profile_data = user.profile or {}
    missing = check_profile_requirements(skill_id, profile_data)
    
    return {
        "skill_id": skill_id,
        "generates_document": skill_id in DOCUMENT_SKILLS,
        "document_format": DOCUMENT_SKILLS.get(skill_id, {}).get("format"),
        "profile_complete": len(missing) == 0,
        "missing_fields": missing
    }

# ============================================
# Routes: Account (Dashboard)
# ============================================

@app.get("/v1/account/stats")
async def get_account_stats(
    license: License = Depends(get_current_license),
    db: AsyncSession = Depends(get_db)
):
    """Get account statistics for dashboard."""
    return {
        "credits_remaining": license.credits_remaining,
        "total_purchased": license.credits_purchased,
        "license_type": license.type,
        "created_at": license.created_at.isoformat() if license.created_at else None
    }


@app.get("/v1/account/purchases")
async def get_purchase_history(
    license: License = Depends(get_current_license),
    db: AsyncSession = Depends(get_db)
):
    """Get purchase history for dashboard."""
    # Get credit transactions (purchases only)
    result = await db.execute(
        select(CreditTransaction)
        .where(
            CreditTransaction.license_id == license.id,
            CreditTransaction.type == "purchase"
        )
        .order_by(CreditTransaction.created_at.desc())
    )
    transactions = result.scalars().all()
    
    purchases = []
    for tx in transactions:
        # Try to get Stripe invoice URL if reference_id is a Stripe session
        invoice_url = None
        if tx.reference_id and tx.reference_id.startswith('cs_'):
            try:
                session = stripe.checkout.Session.retrieve(tx.reference_id)
                if session.invoice:
                    invoice = stripe.Invoice.retrieve(session.invoice)
                    invoice_url = invoice.hosted_invoice_url
            except:
                pass
        
        purchases.append({
            "id": tx.id,
            "date": tx.created_at.isoformat() if tx.created_at else None,
            "description": tx.description or f"Credit purchase",
            "credits": tx.amount,
            "invoice_url": invoice_url
        })
    
    return purchases


# ============================================
# Routes: Document Regeneration
# ============================================

class RegenerateDocumentResponse(BaseModel):
    """Response for document regeneration."""
    usage_id: int
    skill_id: str
    document: DocumentAttachment
    message: str

# ============================================
# Routes: Categories
# ============================================

@app.get("/categories")
@app.get("/v1/categories")
async def list_categories(db: AsyncSession = Depends(get_db)):
    """List all skill categories."""
    result = await db.execute(select(Category).order_by(Category.display_order))
    categories = result.scalars().all()
    
    return [
        {"id": c.id, "name": c.name, "description": c.description}
        for c in categories
    ]

# ============================================
# Health Check
# ============================================

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "lawtasksai-api", "version": "1.0.0"}


@app.get("/v1/loader/latest")
async def get_loader_latest(
    license: License = Depends(get_current_license),
    x_loader_version: Optional[str] = Header(None, alias="X-Loader-Version")
):
    """
    Return the current loader version and full SKILL.md content for auto-update.

    The loader calls this on startup when its local version is behind.
    Returns the full SKILL.md so the loader can replace itself in one round-trip.
    """
    already_current = (x_loader_version == CURRENT_LOADER_VERSION)

    try:
        with open(LOADER_SKILL_PATH, "r") as f:
            skill_content = f.read()
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Loader file not found on server")

    return {
        "version": CURRENT_LOADER_VERSION,
        "already_current": already_current,
        "skill_md": skill_content,
        "update_message": LOADER_UPDATE_MESSAGE,
    }


# ============================================
# Routes: Gap Reporting (anonymous, user-consented)
# ============================================

class GapReportRequest(BaseModel):
    search_terms: List[str]           # keywords that failed to match any skill
    loader_version: Optional[str] = None

@app.post("/v1/feedback/gap", status_code=204)
async def report_skill_gap(
    report: GapReportRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Accept an anonymous gap report from the loader.
    Called only when the user explicitly consents to share per-request.
    No user identity, no query content — only the search terms.
    Returns 204 No Content on success.
    """
    # Sanitize: strip empties, lowercase, limit to 20 terms, 50 chars each
    clean_terms = [
        t.strip().lower()[:50]
        for t in report.search_terms
        if t.strip()
    ][:20]

    if not clean_terms:
        return  # Nothing to record

    gap = SkillGap(
        search_terms=" ".join(clean_terms),
        loader_version=report.loader_version,
    )
    db.add(gap)
    await db.commit()


# ============================================
# Routes: Checkout & Purchase
# ============================================

# Load LOADER_SKILL_MD from file or inline fallback
def _load_loader_skill_md():
    import pathlib as _pl
    candidates = [
        _pl.Path(__file__).parent / 'loader' / 'SKILL.md',
        _pl.Path('/app/loader/SKILL.md'),
    ]
    for p in candidates:
        if p.exists():
            return p.read_text()
    return "# LawTasksAI Skills\n\nLoader SKILL.md not found.\n"

LOADER_SKILL_MD = _load_loader_skill_md()

@app.post("/checkout/create-session")
async def create_checkout_session(
    request: PurchaseCreditsRequest,
    db: AsyncSession = Depends(get_db),
    product_id: str = Depends(get_product_id),
):
    """Create a Stripe checkout session for credit purchase."""

    # --- Resolve pack: prefer product-specific packs, fall back to CREDIT_PACKS ---
    pack = None
    resolved_product_id = product_id  # from header/query/default

    # Try product_credit_packs table first
    try:
        pcp_result = await db.execute(
            text(
                "SELECT pack_key, name, credits, price_cents FROM product_credit_packs "
                "WHERE product_id = :pid ORDER BY credits"
            ),
            {"pid": resolved_product_id},
        )
        product_packs_rows = pcp_result.fetchall()
        if product_packs_rows:
            product_packs = {
                row.pack_key: {
                    "credits": row.credits,
                    "price_cents": row.price_cents,
                    "name": row.name,
                    "one_time": False,
                }
                for row in product_packs_rows
            }
            pack = product_packs.get(request.pack)
    except Exception:
        pass  # table may not exist in all environments — fall through to CREDIT_PACKS

    # Fall back to hardcoded CREDIT_PACKS
    if pack is None:
        if request.pack not in CREDIT_PACKS:
            raise HTTPException(status_code=400, detail="Invalid credit pack")
        pack = CREDIT_PACKS[request.pack]

    # Enforce one-time trial/starter pack per email
    if pack.get("one_time"):
        if not request.email:
            raise HTTPException(status_code=400, detail="Email is required for the Trial pack")
        # Check if this email has already purchased a trial/starter pack
        result = await db.execute(
            select(CreditTransaction).join(User, CreditTransaction.user_id == User.id).where(
                User.email == request.email.lower().strip(),
                or_(
                    CreditTransaction.description.contains("Trial"),
                    CreditTransaction.description.contains("Starter")
                )
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=400, detail="The Trial package is only available once per customer. Check out our other packages for better value!")
    
    # Resolve product domain for success/cancel URLs
    product_domain = None
    product_display_name = None
    try:
        prod_result = await db.execute(
            text("SELECT domain, name FROM products WHERE id = :pid AND is_active = TRUE"),
            {"pid": resolved_product_id},
        )
        prod_row = prod_result.fetchone()
        if prod_row:
            product_domain = f"https://{prod_row.domain}"
            product_display_name = prod_row.name
    except Exception:
        pass
    frontend_url = product_domain or FRONTEND_URL
    product_name = product_display_name or "LawTasksAI"

    try:
        # Create Stripe checkout session
        checkout_kwargs = {
            'payment_method_types': ['card'],
            'line_items': [{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f'{product_name} {pack["name"]}',
                        'description': f'{pack["credits"]} AI task credits',
                    },
                    'unit_amount': pack['price_cents'],
                },
                'quantity': 1,
            }],
            'mode': 'payment',
            'success_url': f'{frontend_url}/success?session_id={{CHECKOUT_SESSION_ID}}',
            'cancel_url': f'{frontend_url}/#pricing',
            'metadata': {
                'pack': request.pack,
                'credits': str(pack['credits']),
                'product_id': resolved_product_id,
                'attorney_name': request.attorney_name or '',
                'bar_jurisdiction': request.bar_jurisdiction or '',
                'bar_number': request.bar_number or '',
                'firm_name': request.firm_name or '',
                'attestation': 'true' if request.attestation else 'false',
            },
        }
        
        # Pre-fill email if provided
        if request.email:
            checkout_kwargs['customer_email'] = request.email.lower().strip()
        
        checkout_session = stripe.checkout.Session.create(**checkout_kwargs)
        
        return {
            "checkout_url": checkout_session.url,
            "session_id": checkout_session.id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def get_zoho_access_token() -> str:
    """Exchange Zoho refresh token for a fresh access token."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://accounts.zoho.com/oauth/v2/token",
            data={
                "refresh_token": os.getenv("ZOHO_REFRESH_TOKEN", ""),
                "client_id": os.getenv("ZOHO_CLIENT_ID", ""),
                "client_secret": os.getenv("ZOHO_CLIENT_SECRET", ""),
                "grant_type": "refresh_token"
            }
        )
        data = resp.json()
        return data.get("access_token", "")


async def add_to_zoho_list(email: str, name: str) -> None:
    """Add a contact to Zoho Campaigns LawTasksAI Subscribers list. Fails silently."""
    list_key = os.getenv("ZOHO_LIST_KEY", "")
    if not list_key or not os.getenv("ZOHO_REFRESH_TOKEN"):
        return
    try:
        access_token = await get_zoho_access_token()
        if not access_token:
            print(f"[Zoho] could not get access token")
            return
        contact_info = json.dumps({"Contact Email": email, "First Name": name or ""})
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://campaigns.zoho.com/api/v1.1/json/listsubscribe",
                params={"resfmt": "JSON", "listkey": list_key, "contactinfo": contact_info},
                headers={"Authorization": f"Zoho-authtoken {access_token}"}
            )
            print(f"[Zoho] add subscriber {email}: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"[Zoho] failed to add subscriber {email}: {e}")


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Handle Stripe webhook events."""
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    # Handle checkout.session.completed
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        session_id = session.get('id', '')

        # Idempotency: skip if this session was already processed
        existing_tx = await db.execute(
            select(CreditTransaction).where(CreditTransaction.reference_id == session_id)
        )
        if existing_tx.scalar_one_or_none():
            return {"status": "skipped", "reason": "already_processed"}

        # Get customer email
        customer_email = session.get('customer_details', {}).get('email')
        if not customer_email:
            return {"status": "skipped", "reason": "no email"}

        # Get credits and pack name from metadata
        meta = session.get('metadata', {})
        credits = int(meta.get('credits', 0))
        pack_name = meta.get('pack', 'unknown')
        if credits == 0:
            return {"status": "skipped", "reason": "no credits"}

        # Product ID and bar/attorney info from metadata
        purchase_product_id = meta.get('product_id', 'law')
        bar_profile = {
            "attorney_name": meta.get('attorney_name', ''),
            "bar_jurisdiction": meta.get('bar_jurisdiction', ''),
            "bar_number": meta.get('bar_number', ''),
            "firm_name": meta.get('firm_name', ''),
            "attestation": meta.get('attestation', 'false') == 'true',
        }

        # Look up product domain for email/download links
        purchase_product_domain = "lawtasksai.com"
        purchase_product_name = "LawTasksAI"
        try:
            prod_result = await db.execute(
                text("SELECT domain, name FROM products WHERE id = :pid AND is_active = TRUE"),
                {"pid": purchase_product_id}
            )
            prod_row = prod_result.fetchone()
            if prod_row and prod_row.domain:
                purchase_product_domain = prod_row.domain
                purchase_product_name = prod_row.name
        except Exception:
            pass

        # Find or create user by email (one account per email, product tracked separately)
        result = await db.execute(select(User).where(User.email == customer_email))
        user = result.scalar_one_or_none()

        if not user:
            # New customer — create account tagged to this product
            user = User(
                email=customer_email,
                name=bar_profile.get('attorney_name') or None,
                firm_name=bar_profile.get('firm_name') or None,
                password_hash=hash_password(secrets.token_hex(16)),
                credits_balance=credits,
                profile=bar_profile,
                product_id=purchase_product_id,
            )
            db.add(user)
            await db.flush()  # get user.id

            license = License(
                license_key=generate_license_key(),
                user_id=user.id,
                type="credits",
                credits_purchased=credits,
                credits_remaining=credits,
                product_id=purchase_product_id,
            )
            db.add(license)
            await db.flush()  # get license.id
        else:
            # Existing customer — add credits, update product_id if this is a new product for them
            user.credits_balance += credits
            # Only update product_id if account has no product yet
            if not user.product_id:
                user.product_id = purchase_product_id
            # Merge bar profile (don't overwrite with empty)
            existing_profile = user.profile or {}
            for k, v in bar_profile.items():
                if v:
                    existing_profile[k] = v
            user.profile = existing_profile
            if bar_profile.get('attorney_name') and not user.name:
                user.name = bar_profile['attorney_name']
            if bar_profile.get('firm_name') and not user.firm_name:
                user.firm_name = bar_profile['firm_name']

            # Find or create license
            lic_result = await db.execute(
                select(License).where(
                    License.user_id == user.id,
                    License.status == "active",
                ).order_by(License.created_at.desc())
            )
            license = lic_result.scalar_one_or_none()

            if not license:
                license = License(
                    license_key=generate_license_key(),
                    user_id=user.id,
                    type="credits",
                    credits_purchased=credits,
                    credits_remaining=credits,
                    product_id=purchase_product_id,
                )
                db.add(license)
                await db.flush()  # get license.id
            else:
                license.credits_remaining += credits
                license.credits_purchased += credits
                # Always update license product_id to match the purchase site
                license.product_id = purchase_product_id

        # Log transaction
        tx = CreditTransaction(
            user_id=user.id,
            license_id=license.id,
            type="purchase",
            amount=credits,
            balance_after=license.credits_remaining,
            reference_id=session['id'],
            description=f"Purchased {pack_name} pack via Stripe checkout"
        )
        db.add(tx)

        await db.commit()

        # Send confirmation email via Zoho SMTP or SendGrid
        try:
            download_url = f"https://api.taskvaultai.com/download/loader?session_id={session['id']}"
            success_url = f"https://{purchase_product_domain}/success?session_id={session['id']}"
            email_subject = f"Your {purchase_product_name} license key and download link"
            email_body = f"""Thank you for purchasing {purchase_product_name}!

Your license key: {license.license_key}
Credits: {credits}

Download your skills package here:
{download_url}

Or visit your purchase summary:
{success_url}

Keep this email — your license key gives you access to your downloads anytime.

Questions? hello@{purchase_product_domain}
"""
            from_addr = f"hello@{purchase_product_domain}"
            from_name = purchase_product_name
            email_sent = False

            # Send via Zoho Mail API (OAuth) — no password needed
            try:
                zoho_client_id = os.getenv("ZOHO_CLIENT_ID", "")
                zoho_client_secret = os.getenv("ZOHO_CLIENT_SECRET", "")
                zoho_refresh_token = os.getenv("ZOHO_MAIL_REFRESH_TOKEN", "") or os.getenv("ZOHO_REFRESH_TOKEN", "")
                zoho_account_id = "6556209000000008002"

                if zoho_client_id and zoho_refresh_token:
                    # Get fresh access token
                    token_resp = await asyncio.get_event_loop().run_in_executor(None, lambda: __import__('urllib.request', fromlist=['urlopen']).urlopen(
                        __import__('urllib.request', fromlist=['Request']).Request(
                            "https://accounts.zoho.com/oauth/v2/token",
                            data=f"refresh_token={zoho_refresh_token}&client_id={zoho_client_id}&client_secret={zoho_client_secret}&grant_type=refresh_token".encode(),
                            method="POST"
                        ), timeout=10
                    ))
                    token_data = json.loads(token_resp.read())
                    access_token = token_data.get("access_token")

                    if access_token:
                        # Send via Zoho Mail API
                        mail_payload = json.dumps({
                            "fromAddress": from_addr,
                            "toAddress": customer_email,
                            "subject": email_subject,
                            "content": email_body,
                            "mailFormat": "plaintext"
                        }).encode()
                        import urllib.request as _ur
                        mail_req = _ur.Request(
                            f"https://mail.zoho.com/api/accounts/{zoho_account_id}/messages",
                            data=mail_payload,
                            headers={
                                "Authorization": f"Zoho-oauthtoken {access_token}",
                                "Content-Type": "application/json"
                            },
                            method="POST"
                        )
                        with _ur.urlopen(mail_req, timeout=10) as mr:
                            resp_data = json.loads(mr.read())
                            print(f"[Email] sent via Zoho API to {customer_email}: {resp_data.get('status', {}).get('code')}")
                        email_sent = True
                    else:
                        print(f"[Email] Zoho token refresh failed: {token_data}")
            except Exception as zoho_err:
                print(f"[Email] Zoho API send failed: {zoho_err}")

            if not email_sent:
                print(f"[Email] skipped for {customer_email}: Zoho API send failed or not configured")
        except Exception as email_err:
            print(f"[Email] failed for {customer_email}: {email_err}")

        # Add buyer to Zoho Campaigns subscriber list
        await add_to_zoho_list(customer_email, user.name or "")

        return {"status": "success", "credits_added": credits, "product_id": purchase_product_id}
    
    return {"status": "ignored", "event_type": event['type']}

@app.get("/checkout/session/{session_id}")
async def get_checkout_session(session_id: str, db: AsyncSession = Depends(get_db)):
    """Get checkout session details (for success page).
    If webhook hasn't fired yet, provisions the user on the spot so the
    success page always works regardless of webhook timing.
    """
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        
        if session.payment_status != 'paid':
            raise HTTPException(status_code=400, detail="Payment not completed")
        
        customer_email = session.get('customer_details', {}).get('email')
        meta = session.get('metadata', {})
        credits = int(meta.get('credits', 0))
        purchase_product_id = meta.get('product_id', 'law')

        # Get user — provision if webhook hasn't fired yet
        result = await db.execute(select(User).where(User.email == customer_email))
        user = result.scalar_one_or_none()

        if not user:
            # Webhook hasn't fired yet — provision now (webhook will be idempotent)
            user = User(
                email=customer_email,
                password_hash=hash_password(secrets.token_hex(16)),
                credits_balance=credits,
                product_id=purchase_product_id,
            )
            db.add(user)
            await db.flush()
            license = License(
                license_key=generate_license_key(),
                user_id=user.id,
                type="credits",
                credits_purchased=credits,
                credits_remaining=credits,
            )
            db.add(license)
            # Log transaction with session_id so webhook skips it later
            tx = CreditTransaction(
                user_id=user.id,
                license_id=license.id,
                type="purchase",
                amount=credits,
                balance_after=credits,
                reference_id=session_id,
                description=f"Purchased {meta.get('pack','unknown')} pack via Stripe checkout"
            )
            db.add(tx)
            await db.flush()
            await db.commit()

        result = await db.execute(
            select(License).where(
                License.user_id == user.id,
                License.status == "active"
            ).order_by(License.created_at.desc())
        )
        license = result.scalar_one_or_none()

        return {
            "email": customer_email,
            "credits_purchased": credits,
            "license_key": license.license_key if license else None,
            "total_credits": license.credits_remaining if license else credits,
            "product_id": user.product_id or "law"
        }
    except stripe.error.InvalidRequestError:
        raise HTTPException(status_code=404, detail="Session not found")

@app.get("/download/loader")
async def download_loader_by_session(session_id: str, db: AsyncSession = Depends(get_db)):
    """
    Download loader using Stripe session_id — always serves the correct product
    regardless of what else is on the account. Used in purchase confirmation emails.
    """
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        if session.payment_status != 'paid':
            raise HTTPException(status_code=400, detail="Payment not completed")
        customer_email = session.get('customer_details', {}).get('email')
        meta = session.get('metadata', {})
        product_id = meta.get('product_id', 'law')
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid session: {e}")

    # Get the user's license key
    result = await db.execute(select(User).where(User.email == customer_email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    license_result = await db.execute(
        select(License).where(License.user_id == user.id, License.status == "active")
        .order_by(License.valid_from.desc()).limit(1)
    )
    license = license_result.scalar_one_or_none()
    if not license:
        raise HTTPException(status_code=404, detail="No active license found")

    # Delegate to the core download logic with the session's product_id
    return await _build_loader_zip(license.license_key, product_id, db)


async def _build_loader_zip(license_key: str, product_id: str, db: AsyncSession):
    """Core download logic — builds the zip for a given license + product."""
    result = await db.execute(
        select(License).where(License.license_key == license_key, License.status == "active")
    )
    license = result.scalar_one_or_none()
    if not license:
        raise HTTPException(status_code=404, detail="Invalid license key")

    user_product_id = product_id or "law"

    prod_name = "LawTasksAI"
    prod_domain = "lawtasksai.com"
    prod_support_email = "hello@lawtasksai.com"
    loader_skill_md = LOADER_SKILL_MD  # default (law)
    try:
        prod_result = await db.execute(
            text("SELECT name, domain FROM products WHERE id = :pid AND is_active = TRUE"),
            {"pid": user_product_id},
        )
        prod_row = prod_result.fetchone()
        if prod_row:
            prod_name = prod_row.name
            prod_domain = prod_row.domain
            prod_support_email = f"hello@{prod_row.domain}"
            # Load product-specific SKILL.md from loader/{product_id}/SKILL.md
            import pathlib as _pl
            skill_path = _pl.Path(__file__).parent / 'loader' / user_product_id / 'SKILL.md'
            if skill_path.exists():
                loader_skill_md = skill_path.read_text()
    except Exception:
        pass

    zip_filename = f"{prod_domain.split('.')[0]}-skills.zip"  # e.g. contractortasksai-skills.zip
    prod_slug = prod_domain.split('.')[0]   # e.g. "realtortasksai"
    env_prefix = prod_slug.upper()          # e.g. "REALTORTASKSAI"

    # Create config.json with license key
    config = {
        "license_key": license_key,
        "api_base_url": API_BASE_URL,
        "product_id": user_product_id,
        "version_policy": "latest",
        "cache_skills": False,
        "offline_mode": False
    }
    
    # Create zip file in memory
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Add config.json
        zf.writestr(f'{prod_slug}/config.json', json.dumps(config, indent=2))
        
        # Add SKILL.md
        zf.writestr(f'{prod_slug}/SKILL.md', loader_skill_md)

        # Add README
        readme = f'''# {prod_name} Skills

Your personalized AI skills are ready to use!

## Installation (Step by Step)

### Step 1: Extract the ZIP file

Double-click the downloaded `{zip_filename}` to extract it.
You should see a folder called `openclaw` containing:
- config.json (your license is already configured!)
- SKILL.md
- README.md (this file)

### Step 2: Tell OpenClaw to install it

Open your OpenClaw chat and type:

"I just downloaded the {prod_name} skill file to my Downloads folder.
Please find it, unzip it if needed, and install it so I can use it.
My license key is {license_key}"

OpenClaw will find the file, install it, configure your license, and
confirm when everything is ready.

(If you prefer to install manually, copy this folder
to ~/.openclaw/skills/{prod_domain.split('.')[0]}/ and restart OpenClaw.)

### Step 3: Start using!

Just ask for any task — your AI will know exactly what to do.

---

## Your License

- **License Key:** {license_key}
- **Credits:** {license.credits_remaining}
- Your license key is already configured — no setup needed!

## ⚠️ Confidentiality Notice

Skills run entirely locally on your machine. Your data never leaves your computer.

## Need Help?

- Email: {prod_support_email}
- Website: https://{prod_domain}
'''
        zf.writestr(f'{prod_slug}/README.md', readme)
    
        # =========================================
        # Add MCP Server for Claude Desktop/Cursor
        # =========================================
        
        # Universal server.py — self-configures via GET /v1/me at startup
        import base64 as _b64
        mcp_server_py = _b64.b64decode('IiIiClRhc2tzQUkgTUNQIFNlcnZlciDigJQgVW5pdmVyc2FsIE11bHRpLVZlcnRpY2FsIFJvdXRlcgoKQSBzaW5nbGUgTUNQIHNlcnZlciB0aGF0IHdvcmtzIGZvciBhbGwgMjkgVGFza3NBSSB2ZXJ0aWNhbHMuCk9uIHN0YXJ0dXAsIGNhbGxzIEdFVCAvdjEvbWUgdG8gZGV0ZWN0IHRoZSB2ZXJ0aWNhbCBmcm9tIHRoZSBsaWNlbnNlIGtleSwKdGhlbiBzZWxmLWNvbmZpZ3VyZXMgdG9vbCBuYW1lcywgc3lzdGVtIHByb21wdCwgYW5kIGFiYnJldmlhdGlvbiBtYXBzLgoKVG9vbHMgKG5hbWVzIGFyZSB2ZXJ0aWNhbC1wcmVmaXhlZCBhdCBydW50aW1lLCBlLmcuIGxhd3Rhc2tzYWlfc2VhcmNoKToKICB7cHJlZml4fV9zZWFyY2ggICAgIOKAlCBGaW5kIHRoZSByaWdodCBza2lsbCBmb3IgeW91ciB0YXNrCiAge3ByZWZpeH1fZXhlY3V0ZSAgICDigJQgR2V0IHRoZSBmdWxsIGV4cGVydCBmcmFtZXdvcmsgZm9yIGEgc2tpbGwgKGNvc3RzIDEgY3JlZGl0KQogIHtwcmVmaXh9X2JhbGFuY2UgICAg4oCUIENoZWNrIHlvdXIgcmVtYWluaW5nIGNyZWRpdCBiYWxhbmNlCiAge3ByZWZpeH1fY2F0ZWdvcmllcyDigJQgQnJvd3NlIHNraWxscyBieSBjYXRlZ29yeQoKUHJpdmFjeTogWW91ciBxdWVyaWVzLCBkb2N1bWVudHMsIGFuZCBjbGllbnQgZGF0YSBuZXZlciBsZWF2ZSB5b3VyIG1hY2hpbmUuClNraWxscyBydW4gZW50aXJlbHkgbG9jYWxseS4gVGhlIEFQSSBvbmx5IGRlbGl2ZXJzIHNraWxsIG1ldGFkYXRhIGFuZApjb3VudHMgY3JlZGl0cyDigJQgaXQgbmV2ZXIgc2VlcyB3aGF0IHlvdSdyZSB3b3JraW5nIG9uLgoiIiIKCmltcG9ydCBvcwppbXBvcnQgcmUKaW1wb3J0IHRpbWUKaW1wb3J0IGFzeW5jaW8KaW1wb3J0IGh0dHB4CmZyb20gZG90ZW52IGltcG9ydCBsb2FkX2RvdGVudgpmcm9tIG1jcC5zZXJ2ZXIgaW1wb3J0IFNlcnZlcgpmcm9tIG1jcC5zZXJ2ZXIuc3RkaW8gaW1wb3J0IHN0ZGlvX3NlcnZlcgpmcm9tIG1jcC50eXBlcyBpbXBvcnQgVG9vbCwgVGV4dENvbnRlbnQsIFByb21wdCwgUHJvbXB0TWVzc2FnZSwgUHJvbXB0QXJndW1lbnQKZnJvbSBtY3AudHlwZXMgaW1wb3J0IEdldFByb21wdFJlc3VsdAoKbG9hZF9kb3RlbnYoKQoKQVBJX0JBU0UgICAgPSBvcy5nZXRlbnYoIlRBU0tTQUlfQVBJX0JBU0UiLCBvcy5nZXRlbnYoIkxBV1RBU0tTQUlfQVBJX0JBU0UiLCAiaHR0cHM6Ly9hcGkudGFza3ZhdWx0YWkuY29tIikpCkxJQ0VOU0VfS0VZID0gb3MuZ2V0ZW52KCJUQVNLU0FJX0xJQ0VOU0VfS0VZIiwgb3MuZ2V0ZW52KCJMQVdUQVNLU0FJX0xJQ0VOU0VfS0VZIiwgIiIpKQoKaWYgbm90IExJQ0VOU0VfS0VZOgogICAgcmFpc2UgVmFsdWVFcnJvcigKICAgICAgICAiTGljZW5zZSBrZXkgaXMgcmVxdWlyZWQuIFNldCBUQVNLU0FJX0xJQ0VOU0VfS0VZIGluIHlvdXIgLmVudiBmaWxlLlxuIgogICAgICAgICJGaW5kIHlvdXIga2V5IGluIHlvdXIgcHVyY2hhc2UgY29uZmlybWF0aW9uIGVtYWlsLiIKICAgICkKCkFVVEhfSEVBREVSUyA9IHsKICAgICJBdXRob3JpemF0aW9uIjogICAgZiJCZWFyZXIge0xJQ0VOU0VfS0VZfSIsCiAgICAiQ29udGVudC1UeXBlIjogICAgICJhcHBsaWNhdGlvbi9qc29uIiwKICAgICJYLUNsaWVudC1UeXBlIjogICAgIm1jcC1zZXJ2ZXIiLAogICAgIlgtQ2xpZW50LVZlcnNpb24iOiAiMi4wLjAiLAp9CgojIOKUgOKUgCBQZXItdmVydGljYWwgYWJicmV2aWF0aW9uIG1hcHMg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACiMgRXhwYW5kcyBjb21tb24gc2hvcnRoYW5kIGJlZm9yZSB0cmlnZ2VyLXBocmFzZSBtYXRjaGluZy4KIyBGYWxsYmFjayBhYmJyZXZpYXRpb24gbWFwcyB1c2VkIHdoZW4gR0VUIC92MS9hYmJyZXZpYXRpb25zIGlzIHVuYXZhaWxhYmxlLgojIFNwcmludCA2OiBEQiBpcyBub3cgdGhlIHNvdXJjZSBvZiB0cnV0aDsgdGhlc2UgYXJlIGEgc2FmZXR5IG5ldCBvbmx5LgoKX0FCQlJFVlNfRkFMTEJBQ0sgPSB7CiAgICAibGF3IjogewogICAgICAgICJtdGMiOiAgICAibW90aW9uIHRvIGNvbXBlbCIsCiAgICAgICAgInJvZ3MiOiAgICJpbnRlcnJvZ2F0b3JpZXMiLAogICAgICAgICJyb2ciOiAgICAiaW50ZXJyb2dhdG9yeSIsCiAgICAgICAgInJmYSI6ICAgICJyZXF1ZXN0IGZvciBhZG1pc3Npb24iLAogICAgICAgICJyZmFzIjogICAicmVxdWVzdHMgZm9yIGFkbWlzc2lvbiIsCiAgICAgICAgInJmcCI6ICAgICJyZXF1ZXN0IGZvciBwcm9kdWN0aW9uIiwKICAgICAgICAicmZwcyI6ICAgInJlcXVlc3RzIGZvciBwcm9kdWN0aW9uIiwKICAgICAgICAidHJvIjogICAgInRlbXBvcmFyeSByZXN0cmFpbmluZyBvcmRlciIsCiAgICAgICAgInBpIjogICAgICJwZXJzb25hbCBpbmp1cnkiLAogICAgICAgICJtc2oiOiAgICAibW90aW9uIGZvciBzdW1tYXJ5IGp1ZGdtZW50IiwKICAgICAgICAibXNrIjogICAgIm1vdGlvbiB0byBzdHJpa2UiLAogICAgICAgICJzaiI6ICAgICAic3VtbWFyeSBqdWRnbWVudCIsCiAgICAgICAgImpub3YiOiAgICJqdWRnbWVudCBub3R3aXRoc3RhbmRpbmcgdmVyZGljdCIsCiAgICAgICAgIm1pbCI6ICAgICJtb3Rpb24gaW4gbGltaW5lIiwKICAgICAgICAic29sIjogICAgInN0YXR1dGUgb2YgbGltaXRhdGlvbnMiLAogICAgICAgICJhZmYiOiAgICAiYWZmaWRhdml0IiwKICAgICAgICAiZGVjbCI6ICAgImRlY2xhcmF0aW9uIiwKICAgICAgICAiZGVwbyI6ICAgImRlcG9zaXRpb24iLAogICAgICAgICJkZXBzIjogICAiZGVwb3NpdGlvbnMiLAogICAgICAgICJmcmNwIjogICAiZmVkZXJhbCBydWxlcyBjaXZpbCBwcm9jZWR1cmUiLAogICAgICAgICJmcmUiOiAgICAiZmVkZXJhbCBydWxlcyBldmlkZW5jZSIsCiAgICAgICAgImNvbXBsIjogICJjb21wbGFpbnQiLAogICAgICAgICJhbnMiOiAgICAiYW5zd2VyIiwKICAgICAgICAicm9lIjogICAgInJ1bGVzIG9mIGV2aWRlbmNlIiwKICAgICAgICAiYXR0eSI6ICAgImF0dG9ybmV5IiwKICAgIH0sCiAgICAicmVhbHRvciI6IHsKICAgICAgICAibWxzIjogICAgIm11bHRpcGxlIGxpc3Rpbmcgc2VydmljZSIsCiAgICAgICAgImNtYSI6ICAgICJjb21wYXJhdGl2ZSBtYXJrZXQgYW5hbHlzaXMiLAogICAgICAgICJkb20iOiAgICAiZGF5cyBvbiBtYXJrZXQiLAogICAgICAgICJhcnYiOiAgICAiYWZ0ZXIgcmVwYWlyIHZhbHVlIiwKICAgICAgICAiaG9hIjogICAgImhvbWVvd25lcnMgYXNzb2NpYXRpb24iLAogICAgICAgICJjb2UiOiAgICAiY2xvc2Ugb2YgZXNjcm93IiwKICAgICAgICAiZW1kIjogICAgImVhcm5lc3QgbW9uZXkgZGVwb3NpdCIsCiAgICAgICAgInBpdGkiOiAgICJwcmluY2lwYWwgaW50ZXJlc3QgdGF4ZXMgaW5zdXJhbmNlIiwKICAgICAgICAibHR2IjogICAgImxvYW4gdG8gdmFsdWUiLAogICAgICAgICJuYXIiOiAgICAibmF0aW9uYWwgYXNzb2NpYXRpb24gb2YgcmVhbHRvcnMiLAogICAgICAgICJib20iOiAgICAiYmFjayBvbiBtYXJrZXQiLAogICAgICAgICJ1YyI6ICAgICAidW5kZXIgY29udHJhY3QiLAogICAgICAgICJmcyI6ICAgICAiZm9yIHNhbGUiLAogICAgICAgICJmc2JvIjogICAiZm9yIHNhbGUgYnkgb3duZXIiLAogICAgICAgICJyZW8iOiAgICAicmVhbCBlc3RhdGUgb3duZWQiLAogICAgfSwKICAgICJjb250cmFjdG9yIjogewogICAgICAgICJyZmkiOiAgICAicmVxdWVzdCBmb3IgaW5mb3JtYXRpb24iLAogICAgICAgICJzb3ciOiAgICAic2NvcGUgb2Ygd29yayIsCiAgICAgICAgImNvIjogICAgICJjaGFuZ2Ugb3JkZXIiLAogICAgICAgICJnYyI6ICAgICAiZ2VuZXJhbCBjb250cmFjdG9yIiwKICAgICAgICAibnRwIjogICAgIm5vdGljZSB0byBwcm9jZWVkIiwKICAgICAgICAicGNvIjogICAgInBvdGVudGlhbCBjaGFuZ2Ugb3JkZXIiLAogICAgICAgICJhaWEiOiAgICAiYW1lcmljYW4gaW5zdGl0dXRlIG9mIGFyY2hpdGVjdHMiLAogICAgICAgICJsaWVuIjogICAibWVjaGFuaWNzIGxpZW4iLAogICAgICAgICJzdWIiOiAgICAic3ViY29udHJhY3RvciIsCiAgICAgICAgInBvciI6ICAgICJwdXJjaGFzZSBvcmRlciByZXF1ZXN0IiwKICAgICAgICAiY29zIjogICAgImNlcnRpZmljYXRlIG9mIHN1YnN0YW50aWFsIGNvbXBsZXRpb24iLAogICAgICAgICJwdW5jaCI6ICAicHVuY2ggbGlzdCIsCiAgICAgICAgImc3MDIiOiAgICJwYXltZW50IGFwcGxpY2F0aW9uIiwKICAgICAgICAiZzcwMyI6ICAgInNjaGVkdWxlIG9mIHZhbHVlcyIsCiAgICB9LAogICAgImZhcm1lciI6IHsKICAgICAgICAiZnNhIjogICAgImZhcm0gc2VydmljZSBhZ2VuY3kiLAogICAgICAgICJucmNzIjogICAibmF0dXJhbCByZXNvdXJjZXMgY29uc2VydmF0aW9uIHNlcnZpY2UiLAogICAgICAgICJjcnAiOiAgICAiY29uc2VydmF0aW9uIHJlc2VydmUgcHJvZ3JhbSIsCiAgICAgICAgImFyYyI6ICAgICJhZ3JpY3VsdHVyZSByaXNrIGNvdmVyYWdlIiwKICAgICAgICAicGxjIjogICAgInByaWNlIGxvc3MgY292ZXJhZ2UiLAogICAgICAgICJ1c2RhIjogICAidW5pdGVkIHN0YXRlcyBkZXBhcnRtZW50IG9mIGFncmljdWx0dXJlIiwKICAgICAgICAiZXFpcCI6ICAgImVudmlyb25tZW50YWwgcXVhbGl0eSBpbmNlbnRpdmVzIHByb2dyYW0iLAogICAgICAgICJjc2EiOiAgICAiY29tbXVuaXR5IHN1cHBvcnRlZCBhZ3JpY3VsdHVyZSIsCiAgICAgICAgImdtcCI6ICAgICJnb29kIG1hbnVmYWN0dXJpbmcgcHJhY3RpY2VzIiwKICAgICAgICAiZ2FwIjogICAgImdvb2QgYWdyaWN1bHR1cmFsIHByYWN0aWNlcyIsCiAgICB9LAogICAgImhyIjogewogICAgICAgICJwaXAiOiAgICAicGVyZm9ybWFuY2UgaW1wcm92ZW1lbnQgcGxhbiIsCiAgICAgICAgInB0byI6ICAgICJwYWlkIHRpbWUgb2ZmIiwKICAgICAgICAiZm1sYSI6ICAgImZhbWlseSBtZWRpY2FsIGxlYXZlIGFjdCIsCiAgICAgICAgImFkYSI6ICAgICJhbWVyaWNhbnMgd2l0aCBkaXNhYmlsaXRpZXMgYWN0IiwKICAgICAgICAiZWVvYyI6ICAgImVxdWFsIGVtcGxveW1lbnQgb3Bwb3J0dW5pdHkgY29tbWlzc2lvbiIsCiAgICAgICAgIncyIjogICAgICJ3YWdlIGFuZCB0YXggc3RhdGVtZW50IiwKICAgICAgICAiaTkiOiAgICAgImVtcGxveW1lbnQgZWxpZ2liaWxpdHkgdmVyaWZpY2F0aW9uIiwKICAgICAgICAiY29icmEiOiAgImNvbnNvbGlkYXRlZCBvbW5pYnVzIGJ1ZGdldCByZWNvbmNpbGlhdGlvbiBhY3QiLAogICAgICAgICJvc2hhIjogICAib2NjdXBhdGlvbmFsIHNhZmV0eSBhbmQgaGVhbHRoIGFkbWluaXN0cmF0aW9uIiwKICAgICAgICAiZXJwIjogICAgImVtcGxveWVlIHJlbGF0aW9ucyBwb2xpY3kiLAogICAgfSwKICAgICJhY2NvdW50aW5nIjogewogICAgICAgICJwJmwiOiAgICAicHJvZml0IGFuZCBsb3NzIiwKICAgICAgICAiY29ncyI6ICAgImNvc3Qgb2YgZ29vZHMgc29sZCIsCiAgICAgICAgImFyIjogICAgICJhY2NvdW50cyByZWNlaXZhYmxlIiwKICAgICAgICAiYXAiOiAgICAgImFjY291bnRzIHBheWFibGUiLAogICAgICAgICJnYWFwIjogICAiZ2VuZXJhbGx5IGFjY2VwdGVkIGFjY291bnRpbmcgcHJpbmNpcGxlcyIsCiAgICAgICAgInl0ZCI6ICAgICJ5ZWFyIHRvIGRhdGUiLAogICAgICAgICJtdGQiOiAgICAibW9udGggdG8gZGF0ZSIsCiAgICAgICAgImViaXRkYSI6ICJlYXJuaW5ncyBiZWZvcmUgaW50ZXJlc3QgdGF4ZXMgZGVwcmVjaWF0aW9uIGFtb3J0aXphdGlvbiIsCiAgICAgICAgImNwYSI6ICAgICJjZXJ0aWZpZWQgcHVibGljIGFjY291bnRhbnQiLAogICAgICAgICJzb3giOiAgICAic2FyYmFuZXMgb3hsZXkiLAogICAgfSwKICAgICJtb3J0Z2FnZSI6IHsKICAgICAgICAibHR2IjogICAgImxvYW4gdG8gdmFsdWUiLAogICAgICAgICJkdGkiOiAgICAiZGVidCB0byBpbmNvbWUiLAogICAgICAgICJhcm0iOiAgICAiYWRqdXN0YWJsZSByYXRlIG1vcnRnYWdlIiwKICAgICAgICAiYXByIjogICAgImFubnVhbCBwZXJjZW50YWdlIHJhdGUiLAogICAgICAgICJwbWkiOiAgICAicHJpdmF0ZSBtb3J0Z2FnZSBpbnN1cmFuY2UiLAogICAgICAgICJodWQiOiAgICAiaG91c2luZyBhbmQgdXJiYW4gZGV2ZWxvcG1lbnQiLAogICAgICAgICJmbm1hIjogICAiZmFubmllIG1hZSIsCiAgICAgICAgImZobG1jIjogICJmcmVkZGllIG1hYyIsCiAgICAgICAgImhlbG9jIjogICJob21lIGVxdWl0eSBsaW5lIG9mIGNyZWRpdCIsCiAgICAgICAgImdmZSI6ICAgICJnb29kIGZhaXRoIGVzdGltYXRlIiwKICAgICAgICAiY2QiOiAgICAgImNsb3NpbmcgZGlzY2xvc3VyZSIsCiAgICAgICAgImxlIjogICAgICJsb2FuIGVzdGltYXRlIiwKICAgIH0sCiAgICAiaW5zdXJhbmNlIjogewogICAgICAgICJkb2kiOiAgICAiZGVwYXJ0bWVudCBvZiBpbnN1cmFuY2UiLAogICAgICAgICJlJm8iOiAgICAiZXJyb3JzIGFuZCBvbWlzc2lvbnMiLAogICAgICAgICJnbCI6ICAgICAiZ2VuZXJhbCBsaWFiaWxpdHkiLAogICAgICAgICJ3YyI6ICAgICAid29ya2VycyBjb21wZW5zYXRpb24iLAogICAgICAgICJjb2kiOiAgICAiY2VydGlmaWNhdGUgb2YgaW5zdXJhbmNlIiwKICAgICAgICAiZGVjIjogICAgImRlY2xhcmF0aW9ucyBwYWdlIiwKICAgICAgICAiYW9iIjogICAgImFzc2lnbm1lbnQgb2YgYmVuZWZpdHMiLAogICAgICAgICJ1d2kiOiAgICAidW5kZXJ3cml0aW5nIGluZm9ybWF0aW9uIiwKICAgICAgICAiY2x1ZSI6ICAgImNvbXByZWhlbnNpdmUgbG9zcyB1bmRlcndyaXRpbmcgZXhjaGFuZ2UiLAogICAgICAgICJwaXAiOiAgICAicGVyc29uYWwgaW5qdXJ5IHByb3RlY3Rpb24iLAogICAgfSwKICAgICJ0aGVyYXBpc3QiOiB7CiAgICAgICAgImRhcCI6ICAgICJkYXRhIGFzc2Vzc21lbnQgcGxhbiIsCiAgICAgICAgInNvYXAiOiAgICJzdWJqZWN0aXZlIG9iamVjdGl2ZSBhc3Nlc3NtZW50IHBsYW4iLAogICAgICAgICJoaXBhYSI6ICAiaGVhbHRoIGluc3VyYW5jZSBwb3J0YWJpbGl0eSBhbmQgYWNjb3VudGFiaWxpdHkgYWN0IiwKICAgICAgICAicGhpIjogICAgInByb3RlY3RlZCBoZWFsdGggaW5mb3JtYXRpb24iLAogICAgICAgICJkeCI6ICAgICAiZGlhZ25vc2lzIiwKICAgICAgICAidHgiOiAgICAgInRyZWF0bWVudCIsCiAgICAgICAgImlvcCI6ICAgICJpbnRlbnNpdmUgb3V0cGF0aWVudCBwcm9ncmFtIiwKICAgICAgICAicGhwIjogICAgInBhcnRpYWwgaG9zcGl0YWxpemF0aW9uIHByb2dyYW0iLAogICAgICAgICJjYnQiOiAgICAiY29nbml0aXZlIGJlaGF2aW9yYWwgdGhlcmFweSIsCiAgICAgICAgImRidCI6ICAgICJkaWFsZWN0aWNhbCBiZWhhdmlvciB0aGVyYXB5IiwKICAgICAgICAiZW1kciI6ICAgImV5ZSBtb3ZlbWVudCBkZXNlbnNpdGl6YXRpb24gcmVwcm9jZXNzaW5nIiwKICAgIH0sCiAgICAiY2hpcm9wcmFjdG9yIjogewogICAgICAgICJzb2FwIjogICAic3ViamVjdGl2ZSBvYmplY3RpdmUgYXNzZXNzbWVudCBwbGFuIiwKICAgICAgICAicm9tIjogICAgInJhbmdlIG9mIG1vdGlvbiIsCiAgICAgICAgInBpIjogICAgICJwZXJzb25hbCBpbmp1cnkiLAogICAgICAgICJoaXBhYSI6ICAiaGVhbHRoIGluc3VyYW5jZSBwb3J0YWJpbGl0eSBhbmQgYWNjb3VudGFiaWxpdHkgYWN0IiwKICAgICAgICAiaWNkIjogICAgImludGVybmF0aW9uYWwgY2xhc3NpZmljYXRpb24gb2YgZGlzZWFzZXMiLAogICAgICAgICJjcHQiOiAgICAiY3VycmVudCBwcm9jZWR1cmFsIHRlcm1pbm9sb2d5IiwKICAgICAgICAiZW9iIjogICAgImV4cGxhbmF0aW9uIG9mIGJlbmVmaXRzIiwKICAgIH0sCiAgICAiZGVudGlzdCI6IHsKICAgICAgICAiaGlwYWEiOiAgImhlYWx0aCBpbnN1cmFuY2UgcG9ydGFiaWxpdHkgYW5kIGFjY291bnRhYmlsaXR5IGFjdCIsCiAgICAgICAgImNkZHQiOiAgICJjdXJyZW50IGRlbnRhbCB0ZXJtaW5vbG9neSIsCiAgICAgICAgInBlcmlvIjogICJwZXJpb2RvbnRhbCIsCiAgICAgICAgIm9ydGhvIjogICJvcnRob2RvbnRpYyIsCiAgICAgICAgImVuZG8iOiAgICJlbmRvZG9udGljIiwKICAgICAgICAiZW9iIjogICAgImV4cGxhbmF0aW9uIG9mIGJlbmVmaXRzIiwKICAgICAgICAicGFubyI6ICAgInBhbm9yYW1pYyByYWRpb2dyYXBoIiwKICAgIH0sCiAgICAidGVhY2hlciI6IHsKICAgICAgICAiaWVwIjogICAgImluZGl2aWR1YWxpemVkIGVkdWNhdGlvbiBwcm9ncmFtIiwKICAgICAgICAiNTA0IjogICAgInNlY3Rpb24gNTA0IGFjY29tbW9kYXRpb24gcGxhbiIsCiAgICAgICAgImVsbCI6ICAgICJlbmdsaXNoIGxhbmd1YWdlIGxlYXJuZXIiLAogICAgICAgICJzcGVkIjogICAic3BlY2lhbCBlZHVjYXRpb24iLAogICAgICAgICJwYmlzIjogICAicG9zaXRpdmUgYmVoYXZpb3JhbCBpbnRlcnZlbnRpb25zIGFuZCBzdXBwb3J0cyIsCiAgICAgICAgIm10c3MiOiAgICJtdWx0aS10aWVyZWQgc3lzdGVtIG9mIHN1cHBvcnRzIiwKICAgICAgICAicnRpIjogICAgInJlc3BvbnNlIHRvIGludGVydmVudGlvbiIsCiAgICAgICAgImZlcnBhIjogICJmYW1pbHkgZWR1Y2F0aW9uYWwgcmlnaHRzIGFuZCBwcml2YWN5IGFjdCIsCiAgICAgICAgInBkIjogICAgICJwcm9mZXNzaW9uYWwgZGV2ZWxvcG1lbnQiLAogICAgICAgICJwbGMiOiAgICAicHJvZmVzc2lvbmFsIGxlYXJuaW5nIGNvbW11bml0eSIsCiAgICB9LAogICAgInZldCI6IHsKICAgICAgICAic29hcCI6ICAgInN1YmplY3RpdmUgb2JqZWN0aXZlIGFzc2Vzc21lbnQgcGxhbiIsCiAgICAgICAgImF2bWEiOiAgICJhbWVyaWNhbiB2ZXRlcmluYXJ5IG1lZGljYWwgYXNzb2NpYXRpb24iLAogICAgICAgICJyeCI6ICAgICAicHJlc2NyaXB0aW9uIiwKICAgICAgICAiZHgiOiAgICAgImRpYWdub3NpcyIsCiAgICAgICAgInR4IjogICAgICJ0cmVhdG1lbnQiLAogICAgICAgICJoeCI6ICAgICAiaGlzdG9yeSIsCiAgICAgICAgInBlIjogICAgICJwaHlzaWNhbCBleGFtaW5hdGlvbiIsCiAgICB9LAogICAgImVsZWN0cmljaWFuIjogewogICAgICAgICJuZWMiOiAgICAibmF0aW9uYWwgZWxlY3RyaWNhbCBjb2RlIiwKICAgICAgICAiZ2ZjaSI6ICAgImdyb3VuZCBmYXVsdCBjaXJjdWl0IGludGVycnVwdGVyIiwKICAgICAgICAiYWZjaSI6ICAgImFyYyBmYXVsdCBjaXJjdWl0IGludGVycnVwdGVyIiwKICAgICAgICAiYXRwIjogICAgImFtcGVyZSB0cmlwIHBvaW50IiwKICAgICAgICAicmZpIjogICAgInJlcXVlc3QgZm9yIGluZm9ybWF0aW9uIiwKICAgICAgICAiY28iOiAgICAgImNoYW5nZSBvcmRlciIsCiAgICAgICAgIm50cCI6ICAgICJub3RpY2UgdG8gcHJvY2VlZCIsCiAgICB9LAogICAgInBsdW1iZXIiOiB7CiAgICAgICAgImlwYyI6ICAgICJpbnRlcm5hdGlvbmFsIHBsdW1iaW5nIGNvZGUiLAogICAgICAgICJ1cGMiOiAgICAidW5pZm9ybSBwbHVtYmluZyBjb2RlIiwKICAgICAgICAicmZpIjogICAgInJlcXVlc3QgZm9yIGluZm9ybWF0aW9uIiwKICAgICAgICAiY28iOiAgICAgImNoYW5nZSBvcmRlciIsCiAgICAgICAgIm50cCI6ICAgICJub3RpY2UgdG8gcHJvY2VlZCIsCiAgICAgICAgInBleCI6ICAgICJjcm9zcy1saW5rZWQgcG9seWV0aHlsZW5lIiwKICAgICAgICAiYWJzIjogICAgImFjcnlsb25pdHJpbGUgYnV0YWRpZW5lIHN0eXJlbmUiLAogICAgfSwKICAgICMg4pSA4pSAIEFkZGl0aW9uYWwgdmVydGljYWxzIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgAogICAgIm1hcmtldGluZyI6IHsKICAgICAgICAic2VvIjogICAgInNlYXJjaCBlbmdpbmUgb3B0aW1pemF0aW9uIiwKICAgICAgICAic2VtIjogICAgInNlYXJjaCBlbmdpbmUgbWFya2V0aW5nIiwKICAgICAgICAicHBjIjogICAgInBheSBwZXIgY2xpY2siLAogICAgICAgICJjdHIiOiAgICAiY2xpY2sgdGhyb3VnaCByYXRlIiwKICAgICAgICAiY3BjIjogICAgImNvc3QgcGVyIGNsaWNrIiwKICAgICAgICAiY3BhIjogICAgImNvc3QgcGVyIGFjcXVpc2l0aW9uIiwKICAgICAgICAicm9pIjogICAgInJldHVybiBvbiBpbnZlc3RtZW50IiwKICAgICAgICAia3BpIjogICAgImtleSBwZXJmb3JtYW5jZSBpbmRpY2F0b3IiLAogICAgICAgICJjcm0iOiAgICAiY3VzdG9tZXIgcmVsYXRpb25zaGlwIG1hbmFnZW1lbnQiLAogICAgICAgICJjdGEiOiAgICAiY2FsbCB0byBhY3Rpb24iLAogICAgICAgICJiMmIiOiAgICAiYnVzaW5lc3MgdG8gYnVzaW5lc3MiLAogICAgICAgICJiMmMiOiAgICAiYnVzaW5lc3MgdG8gY29uc3VtZXIiLAogICAgICAgICJzYWFzIjogICAic29mdHdhcmUgYXMgYSBzZXJ2aWNlIiwKICAgICAgICAibXJyIjogICAgIm1vbnRobHkgcmVjdXJyaW5nIHJldmVudWUiLAogICAgICAgICJhcnIiOiAgICAiYW5udWFsIHJlY3VycmluZyByZXZlbnVlIiwKICAgIH0sCiAgICAicGFzdG9yIjogewogICAgICAgICJ2YnMiOiAgICAidmFjYXRpb24gYmlibGUgc2Nob29sIiwKICAgICAgICAiYXdhbmEiOiAgImFwcHJvdmVkIHdvcmttZW4gYXJlIG5vdCBhc2hhbWVkIiwKICAgICAgICAiYWNsIjogICAgImFkdWx0IGNvbW11bml0eSBsaWZlIiwKICAgICAgICAibGNtIjogICAgImxlYWRlcnNoaXAgY29yZSBtZWV0aW5nIiwKICAgICAgICAic21sIjogICAgInNtYWxsIGdyb3VwIGxlYWRlciIsCiAgICB9LAogICAgInNhbG9uIjogewogICAgICAgICJwYmUiOiAgICAicHJvZmVzc2lvbmFsIGJlYXV0eSBlcXVpcG1lbnQiLAogICAgICAgICJjb3NtbyI6ICAiY29zbWV0b2xvZ3kiLAogICAgICAgICJlc3RpIjogICAiZXN0aGV0aWNpYW4iLAogICAgICAgICJuYWlsIjogICAibmFpbCB0ZWNobmljaWFuIiwKICAgICAgICAiaHNjIjogICAgImhhaXIgc2Fsb24gY29vcmRpbmF0b3IiLAogICAgfSwKICAgICJ0cmF2ZWxhZ2VudCI6IHsKICAgICAgICAiZ2RzIjogICAgImdsb2JhbCBkaXN0cmlidXRpb24gc3lzdGVtIiwKICAgICAgICAiaWF0YSI6ICAgImludGVybmF0aW9uYWwgYWlyIHRyYW5zcG9ydCBhc3NvY2lhdGlvbiIsCiAgICAgICAgImZhbSI6ICAgICJmYW1pbGlhcml6YXRpb24gdHJpcCIsCiAgICAgICAgImZ4IjogICAgICJmb3JlaWduIGV4Y2hhbmdlIiwKICAgICAgICAib3RhIjogICAgIm9ubGluZSB0cmF2ZWwgYWdlbmN5IiwKICAgICAgICAicG5yIjogICAgInBhc3NlbmdlciBuYW1lIHJlY29yZCIsCiAgICAgICAgInJvaSI6ICAgICJyZXR1cm4gb24gaW52ZXN0bWVudCIsCiAgICB9LAogICAgInJlc3RhdXJhbnQiOiB7CiAgICAgICAgImNvZ3MiOiAgICJjb3N0IG9mIGdvb2RzIHNvbGQiLAogICAgICAgICJmb2giOiAgICAiZnJvbnQgb2YgaG91c2UiLAogICAgICAgICJib2giOiAgICAiYmFjayBvZiBob3VzZSIsCiAgICAgICAgInBvcyI6ICAgICJwb2ludCBvZiBzYWxlIiwKICAgICAgICAiaGFjY3AiOiAgImhhemFyZCBhbmFseXNpcyBjcml0aWNhbCBjb250cm9sIHBvaW50cyIsCiAgICAgICAgImZpZm8iOiAgICJmaXJzdCBpbiBmaXJzdCBvdXQiLAogICAgICAgICJlaWdodHkgc2l4IjogIml0ZW0gdW5hdmFpbGFibGUiLAogICAgfSwKICAgICJsYW5kbG9yZCI6IHsKICAgICAgICAibm9pIjogICAgIm5ldCBvcGVyYXRpbmcgaW5jb21lIiwKICAgICAgICAiY2FwIjogICAgImNhcGl0YWxpemF0aW9uIHJhdGUiLAogICAgICAgICJyb2kiOiAgICAicmV0dXJuIG9uIGludmVzdG1lbnQiLAogICAgICAgICJob2EiOiAgICAiaG9tZW93bmVycyBhc3NvY2lhdGlvbiIsCiAgICAgICAgInNlYyBkZXAiOiAic2VjdXJpdHkgZGVwb3NpdCIsCiAgICAgICAgImx0diI6ICAgICJsb2FuIHRvIHZhbHVlIiwKICAgIH0sCiAgICAicHJpbmNpcGFsIjogewogICAgICAgICJpZXAiOiAgICAiaW5kaXZpZHVhbGl6ZWQgZWR1Y2F0aW9uIHByb2dyYW0iLAogICAgICAgICI1MDQiOiAgICAic2VjdGlvbiA1MDQgYWNjb21tb2RhdGlvbiBwbGFuIiwKICAgICAgICAicGJpcyI6ICAgInBvc2l0aXZlIGJlaGF2aW9yYWwgaW50ZXJ2ZW50aW9ucyBhbmQgc3VwcG9ydHMiLAogICAgICAgICJtdHNzIjogICAibXVsdGktdGllcmVkIHN5c3RlbSBvZiBzdXBwb3J0cyIsCiAgICAgICAgImZlcnBhIjogICJmYW1pbHkgZWR1Y2F0aW9uYWwgcmlnaHRzIGFuZCBwcml2YWN5IGFjdCIsCiAgICAgICAgInNwZWQiOiAgICJzcGVjaWFsIGVkdWNhdGlvbiIsCiAgICAgICAgImVsbCI6ICAgICJlbmdsaXNoIGxhbmd1YWdlIGxlYXJuZXIiLAogICAgICAgICJwbGMiOiAgICAicHJvZmVzc2lvbmFsIGxlYXJuaW5nIGNvbW11bml0eSIsCiAgICB9LAogICAgIm1vcnR1YXJ5IjogewogICAgICAgICJmZGEiOiAgICAiZm9vZCBhbmQgZHJ1ZyBhZG1pbmlzdHJhdGlvbiIsCiAgICAgICAgImZ0YyI6ICAgICJmZWRlcmFsIHRyYWRlIGNvbW1pc3Npb24iLAogICAgICAgICJvc2hhIjogICAib2NjdXBhdGlvbmFsIHNhZmV0eSBhbmQgaGVhbHRoIGFkbWluaXN0cmF0aW9uIiwKICAgICAgICAiZG5hIjogICAgImRvIG5vdCBhdXRvcHN5IiwKICAgICAgICAiZG5yIjogICAgImRvIG5vdCByZXN1c2NpdGF0ZSIsCiAgICB9LAogICAgImV2ZW50cGxhbm5lciI6IHsKICAgICAgICAicnN2cCI6ICAgInJlcG9uZGV6IHNpbCB2b3VzIHBsYWl0IiwKICAgICAgICAiYXYiOiAgICAgImF1ZGlvIHZpc3VhbCIsCiAgICAgICAgImJlbyI6ICAgICJiYW5xdWV0IGV2ZW50IG9yZGVyIiwKICAgICAgICAicmZwIjogICAgInJlcXVlc3QgZm9yIHByb3Bvc2FsIiwKICAgICAgICAicm9pIjogICAgInJldHVybiBvbiBpbnZlc3RtZW50IiwKICAgICAgICAiZiZiIjogICAgImZvb2QgYW5kIGJldmVyYWdlIiwKICAgIH0sCiAgICAiY2h1cmNoIjogewogICAgICAgICJ2YnMiOiAgICAidmFjYXRpb24gYmlibGUgc2Nob29sIiwKICAgICAgICAiYXdhbmEiOiAgImFwcHJvdmVkIHdvcmttZW4gYXJlIG5vdCBhc2hhbWVkIiwKICAgICAgICAiNTAxYzMiOiAgIm5vbnByb2ZpdCB0YXggZXhlbXB0IHN0YXR1cyIsCiAgICAgICAgImFlZCI6ICAgICJhdXRvbWF0ZWQgZXh0ZXJuYWwgZGVmaWJyaWxsYXRvciIsCiAgICAgICAgImFjbCI6ICAgICJhZHVsdCBjb21tdW5pdHkgbGlmZSIsCiAgICB9LAogICAgInBlcnNvbmFsdHJhaW5lciI6IHsKICAgICAgICAicm0iOiAgICAgInJlcGV0aXRpb24gbWF4aW11bSIsCiAgICAgICAgImhpaXQiOiAgICJoaWdoIGludGVuc2l0eSBpbnRlcnZhbCB0cmFpbmluZyIsCiAgICAgICAgImJtciI6ICAgICJiYXNhbCBtZXRhYm9saWMgcmF0ZSIsCiAgICAgICAgInRkZWUiOiAgICJ0b3RhbCBkYWlseSBlbmVyZ3kgZXhwZW5kaXR1cmUiLAogICAgICAgICJibWkiOiAgICAiYm9keSBtYXNzIGluZGV4IiwKICAgICAgICAicm9tIjogICAgInJhbmdlIG9mIG1vdGlvbiIsCiAgICAgICAgInBhciBxIjogICJwaHlzaWNhbCBhY3Rpdml0eSByZWFkaW5lc3MgcXVlc3Rpb25uYWlyZSIsCiAgICB9LAogICAgImRlc2lnbmVyIjogewogICAgICAgICJ1aSI6ICAgICAidXNlciBpbnRlcmZhY2UiLAogICAgICAgICJ1eCI6ICAgICAidXNlciBleHBlcmllbmNlIiwKICAgICAgICAicmdiIjogICAgInJlZCBncmVlbiBibHVlIiwKICAgICAgICAiY215ayI6ICAgImN5YW4gbWFnZW50YSB5ZWxsb3cga2V5IiwKICAgICAgICAiZHBpIjogICAgImRvdHMgcGVyIGluY2giLAogICAgICAgICJwcGkiOiAgICAicGl4ZWxzIHBlciBpbmNoIiwKICAgICAgICAic3ZnIjogICAgInNjYWxhYmxlIHZlY3RvciBncmFwaGljcyIsCiAgICAgICAgInNvdyI6ICAgICJzY29wZSBvZiB3b3JrIiwKICAgIH0sCiAgICAibWlsaXRhcnlzcG91c2UiOiB7CiAgICAgICAgInBjcyI6ICAgICJwZXJtYW5lbnQgY2hhbmdlIG9mIHN0YXRpb24iLAogICAgICAgICJ0ZHkiOiAgICAidGVtcG9yYXJ5IGR1dHkgYXNzaWdubWVudCIsCiAgICAgICAgImJhaCI6ICAgICJiYXNpYyBhbGxvd2FuY2UgZm9yIGhvdXNpbmciLAogICAgICAgICJiYXMiOiAgICAiYmFzaWMgYWxsb3dhbmNlIGZvciBzdWJzaXN0ZW5jZSIsCiAgICAgICAgImRlZXJzIjogICJkZWZlbnNlIGVucm9sbG1lbnQgZWxpZ2liaWxpdHkgcmVwb3J0aW5nIHN5c3RlbSIsCiAgICAgICAgInRyaWNhcmUiOiJtaWxpdGFyeSBoZWFsdGggaW5zdXJhbmNlIiwKICAgICAgICAiaWQgY2FyZCI6Im1pbGl0YXJ5IGRlcGVuZGVudCBpZGVudGlmaWNhdGlvbiIsCiAgICB9LAogICAgImZ1bmVyYWwiOiB7CiAgICAgICAgImZ0YyI6ICAgICJmZWRlcmFsIHRyYWRlIGNvbW1pc3Npb24iLAogICAgICAgICJmZGEiOiAgICAiZm9vZCBhbmQgZHJ1ZyBhZG1pbmlzdHJhdGlvbiIsCiAgICAgICAgIm9zaGEiOiAgICJvY2N1cGF0aW9uYWwgc2FmZXR5IGFuZCBoZWFsdGggYWRtaW5pc3RyYXRpb24iLAogICAgICAgICJkbnIiOiAgICAiZG8gbm90IHJlc3VzY2l0YXRlIiwKICAgICAgICAiY3JlbWFpbnMiOiJjcmVtYXRlZCByZW1haW5zIiwKICAgIH0sCiAgICAibnV0cml0aW9uaXN0IjogewogICAgICAgICJibWkiOiAgICAiYm9keSBtYXNzIGluZGV4IiwKICAgICAgICAiYm1yIjogICAgImJhc2FsIG1ldGFib2xpYyByYXRlIiwKICAgICAgICAidGRlZSI6ICAgInRvdGFsIGRhaWx5IGVuZXJneSBleHBlbmRpdHVyZSIsCiAgICAgICAgImdpIjogICAgICJnbHljZW1pYyBpbmRleCIsCiAgICAgICAgImdsIjogICAgICJnbHljZW1pYyBsb2FkIiwKICAgICAgICAiZHJpIjogICAgImRpZXRhcnkgcmVmZXJlbmNlIGludGFrZSIsCiAgICAgICAgInJkYSI6ICAgICJyZWNvbW1lbmRlZCBkaWV0YXJ5IGFsbG93YW5jZSIsCiAgICAgICAgImlidyI6ICAgICJpZGVhbCBib2R5IHdlaWdodCIsCiAgICB9LAp9CgojIERlZmF1bHQgZW1wdHkgbWFwIGZvciB2ZXJ0aWNhbHMgd2l0aG91dCBzcGVjaWZpYyBhYmJyZXZpYXRpb25zCl9ERUZBVUxUX0FCQlJFVlMgPSB7fQoKIyBEQi1sb2FkZWQgYWJicmV2aWF0aW9ucyAoZmV0Y2hlZCBmcm9tIC92MS9hYmJyZXZpYXRpb25zIGF0IHN0YXJ0dXApCl9hYmJyZXZzX2RiOiBkaWN0IHwgTm9uZSA9IE5vbmUKX2FiYnJldnNfZGJfdHM6IGZsb2F0ID0gMC4wCl9hYmJyZXZzX2RiX3Byb2R1Y3Q6IHN0ciB8IE5vbmUgPSBOb25lCgoKIyDilIDilIAgQ2FjaGUgY29uZmlndXJhdGlvbiDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAKQ0FDSEVfVFRMICAgICAgPSA2MDAgICAjIDEwIG1pbnV0ZXMKRVJST1JfQ09PTERPV04gPSAzMCAgICAjIHJldHJ5IGFmdGVyIGZhaWx1cmUKCiMgVmVydGljYWwgbWV0YWRhdGEgKGxvYWRlZCBvbmNlIGF0IHN0YXJ0dXAgdmlhIEdFVCAvdjEvbWUpCl92ZXJ0aWNhbCA9IE5vbmUKCiMgU2tpbGxzIGNhY2hlCl9za2lsbHNfY2FjaGUgICAgICAgICA9IE5vbmUKX3NraWxsc19jYWNoZV90cyAgICAgID0gMC4wCl9za2lsbHNfY2FjaGVfZXJyX3VudGlsID0gMC4wCgojIFRyaWdnZXJzIGNhY2hlIOKAlCB7c2tpbGxfaWQ6IFtwaHJhc2UsIC4uLl19Cl90cmlnZ2Vyc19jYWNoZSAgICAgICAgICAgPSBOb25lCl90cmlnZ2Vyc19jYWNoZV90cyAgICAgICAgPSAwLjAKX3RyaWdnZXJzX2NhY2hlX2Vycl91bnRpbCA9IDAuMAoKCmFzeW5jIGRlZiBhcGlfZ2V0KHBhdGgpOgogICAgYXN5bmMgd2l0aCBodHRweC5Bc3luY0NsaWVudCh0aW1lb3V0PTMwLjApIGFzIGNsaWVudDoKICAgICAgICByZXNwID0gYXdhaXQgY2xpZW50LmdldCgKICAgICAgICAgICAgZiJ7QVBJX0JBU0V9e3BhdGh9IiwKICAgICAgICAgICAgaGVhZGVycz17KipBVVRIX0hFQURFUlMsICJYLVByb2R1Y3QtSUQiOiAoX3ZlcnRpY2FsIG9yIHt9KS5nZXQoInByb2R1Y3RfaWQiLCAibGF3Iil9CiAgICAgICAgKQogICAgICAgIHJlc3AucmFpc2VfZm9yX3N0YXR1cygpCiAgICAgICAgcmV0dXJuIHJlc3AuanNvbigpCgoKYXN5bmMgZGVmIGxvYWRfdmVydGljYWwoKToKICAgICIiIkZldGNoIHZlcnRpY2FsIG1ldGFkYXRhIGZyb20gL3YxL21lIG9uIHN0YXJ0dXAuIEZhbGxzIGJhY2sgdG8gbGF3LiIiIgogICAgZ2xvYmFsIF92ZXJ0aWNhbAogICAgdHJ5OgogICAgICAgIF92ZXJ0aWNhbCA9IGF3YWl0IGFwaV9nZXQoIi92MS9tZSIpCiAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgICMgRmFsbGJhY2s6IGRlcml2ZSBmcm9tIGxpY2Vuc2Uga2V5IHByZWZpeCBjbGllbnQtc2lkZQogICAgICAgIHByZWZpeCA9IExJQ0VOU0VfS0VZLnNwbGl0KCJfIilbMF0gKyAiXyIgaWYgIl8iIGluIExJQ0VOU0VfS0VZIGVsc2UgImx0XyIKICAgICAgICBfdmVydGljYWwgPSB7CiAgICAgICAgICAgICJwcm9kdWN0X2lkIjogICAibGF3IiwKICAgICAgICAgICAgInByb2R1Y3RfbmFtZSI6ICJMYXdUYXNrc0FJIiwKICAgICAgICAgICAgImRpc3BsYXlfbmFtZSI6ICJMYXcgVGFza3MgQUkiLAogICAgICAgICAgICAidG9vbF9wcmVmaXgiOiAgImxhd3Rhc2tzYWkiLAogICAgICAgICAgICAib2NjdXBhdGlvbiI6ICAgImF0dG9ybmV5cyBhbmQgbGVnYWwgcHJvZmVzc2lvbmFscyIsCiAgICAgICAgICAgICJzdXBwb3J0X2VtYWlsIjoiaGVsbG9AbGF3dGFza3NhaS5jb20iLAogICAgICAgICAgICAiZG9tYWluIjogICAgICAgImxhd3Rhc2tzYWkuY29tIiwKICAgICAgICB9CiAgICByZXR1cm4gX3ZlcnRpY2FsCgoKYXN5bmMgZGVmIGxvYWRfYWJicmV2aWF0aW9ucygpOgogICAgIiIiCiAgICBGZXRjaCBhYmJyZXZpYXRpb25zIGZvciB0aGlzIHZlcnRpY2FsIGZyb20gR0VUIC92MS9hYmJyZXZpYXRpb25zLgogICAgUG9wdWxhdGVzIF9hYmJyZXZzX2RiLiBGYWxscyBiYWNrIHNpbGVudGx5IHRvIF9BQkJSRVZTX0ZBTExCQUNLIGlmIHVuYXZhaWxhYmxlLgogICAgQ2FsbGVkIG9uY2UgYXQgc3RhcnR1cCBhZnRlciBsb2FkX3ZlcnRpY2FsKCkuCiAgICAiIiIKICAgIGdsb2JhbCBfYWJicmV2c19kYiwgX2FiYnJldnNfZGJfdHMsIF9hYmJyZXZzX2RiX3Byb2R1Y3QKICAgIHRyeToKICAgICAgICBkYXRhID0gYXdhaXQgYXBpX2dldCgiL3YxL2FiYnJldmlhdGlvbnMiKQogICAgICAgIF9hYmJyZXZzX2RiID0gZGF0YS5nZXQoImFiYnJldmlhdGlvbnMiLCB7fSkKICAgICAgICBfYWJicmV2c19kYl9wcm9kdWN0ID0gZGF0YS5nZXQoInByb2R1Y3RfaWQiKQogICAgICAgIF9hYmJyZXZzX2RiX3RzID0gdGltZS5tb25vdG9uaWMoKQogICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICAjIE5vbi1mYXRhbDogX0FCQlJFVlNfRkFMTEJBQ0sgd2lsbCBiZSB1c2VkIGluc3RlYWQKICAgICAgICBfYWJicmV2c19kYiA9IE5vbmUKICAgIHJldHVybiBfYWJicmV2c19kYgoKCmFzeW5jIGRlZiBnZXRfc2tpbGxzKCk6CiAgICBnbG9iYWwgX3NraWxsc19jYWNoZSwgX3NraWxsc19jYWNoZV90cywgX3NraWxsc19jYWNoZV9lcnJfdW50aWwKICAgIG5vdyA9IHRpbWUubW9ub3RvbmljKCkKICAgIGlmIF9za2lsbHNfY2FjaGUgaXMgbm90IE5vbmUgYW5kIChub3cgLSBfc2tpbGxzX2NhY2hlX3RzKSA8IENBQ0hFX1RUTDoKICAgICAgICByZXR1cm4gX3NraWxsc19jYWNoZQogICAgaWYgbm93IDwgX3NraWxsc19jYWNoZV9lcnJfdW50aWw6CiAgICAgICAgcmV0dXJuIF9za2lsbHNfY2FjaGUgaWYgX3NraWxsc19jYWNoZSBpcyBub3QgTm9uZSBlbHNlIFtdCiAgICB0cnk6CiAgICAgICAgX3NraWxsc19jYWNoZSA9IGF3YWl0IGFwaV9nZXQoIi92MS9za2lsbHMiKQogICAgICAgIF9za2lsbHNfY2FjaGVfdHMgPSBub3cKICAgICAgICBfc2tpbGxzX2NhY2hlX2Vycl91bnRpbCA9IDAuMAogICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICBfc2tpbGxzX2NhY2hlX2Vycl91bnRpbCA9IG5vdyArIEVSUk9SX0NPT0xET1dOCiAgICAgICAgaWYgX3NraWxsc19jYWNoZSBpcyBOb25lOgogICAgICAgICAgICBfc2tpbGxzX2NhY2hlID0gW10KICAgIHJldHVybiBfc2tpbGxzX2NhY2hlCgoKYXN5bmMgZGVmIGdldF90cmlnZ2VycygpOgogICAgIiIiUmV0dXJuIHRyaWdnZXIgcGhyYXNlcyB7c2tpbGxfaWQ6IFtwaHJhc2UsIC4uLl19LiBGYWlscyBzaWxlbnRseS4iIiIKICAgIGdsb2JhbCBfdHJpZ2dlcnNfY2FjaGUsIF90cmlnZ2Vyc19jYWNoZV90cywgX3RyaWdnZXJzX2NhY2hlX2Vycl91bnRpbAogICAgbm93ID0gdGltZS5tb25vdG9uaWMoKQogICAgaWYgX3RyaWdnZXJzX2NhY2hlIGlzIG5vdCBOb25lIGFuZCAobm93IC0gX3RyaWdnZXJzX2NhY2hlX3RzKSA8IENBQ0hFX1RUTDoKICAgICAgICByZXR1cm4gX3RyaWdnZXJzX2NhY2hlCiAgICBpZiBub3cgPCBfdHJpZ2dlcnNfY2FjaGVfZXJyX3VudGlsOgogICAgICAgIHJldHVybiBfdHJpZ2dlcnNfY2FjaGUgaWYgX3RyaWdnZXJzX2NhY2hlIGlzIG5vdCBOb25lIGVsc2Uge30KICAgIHRyeToKICAgICAgICByYXcgPSBhd2FpdCBhcGlfZ2V0KCIvdjEvc2tpbGxzL3RyaWdnZXJzIikKICAgICAgICBfdHJpZ2dlcnNfY2FjaGUgPSB7CiAgICAgICAgICAgIHNpZDogW3AubG93ZXIoKSBmb3IgcCBpbiB2LmdldCgidHJpZ2dlcnMiLCBbXSldCiAgICAgICAgICAgIGZvciBzaWQsIHYgaW4gcmF3Lml0ZW1zKCkKICAgICAgICB9CiAgICAgICAgX3RyaWdnZXJzX2NhY2hlX3RzID0gbm93CiAgICAgICAgX3RyaWdnZXJzX2NhY2hlX2Vycl91bnRpbCA9IDAuMAogICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICBfdHJpZ2dlcnNfY2FjaGVfZXJyX3VudGlsID0gbm93ICsgRVJST1JfQ09PTERPV04KICAgICAgICBpZiBfdHJpZ2dlcnNfY2FjaGUgaXMgTm9uZToKICAgICAgICAgICAgX3RyaWdnZXJzX2NhY2hlID0ge30KICAgIHJldHVybiBfdHJpZ2dlcnNfY2FjaGUKCgpkZWYgZXhwYW5kX3F1ZXJ5KHF1ZXJ5LCBwcm9kdWN0X2lkKToKICAgICIiIkV4cGFuZCB2ZXJ0aWNhbC1zcGVjaWZpYyBhYmJyZXZpYXRpb25zIGJlZm9yZSBtYXRjaGluZy4iIiIKICAgICMgUHJlZmVyIERCLWxvYWRlZCBhYmJyZXZpYXRpb25zOyBmYWxsIGJhY2sgdG8gaGFyZGNvZGVkIG1hcAogICAgaWYgX2FiYnJldnNfZGIgaXMgbm90IE5vbmU6CiAgICAgICAgYWJicmV2cyA9IF9hYmJyZXZzX2RiCiAgICBlbHNlOgogICAgICAgIGFiYnJldnMgPSBfQUJCUkVWU19GQUxMQkFDSy5nZXQocHJvZHVjdF9pZCwgX0RFRkFVTFRfQUJCUkVWUykKICAgIGlmIG5vdCBhYmJyZXZzOgogICAgICAgIHJldHVybiBxdWVyeQogICAgd29yZHMgPSBxdWVyeS5sb3dlcigpLnNwbGl0KCkKICAgIGV4cGFuc2lvbnMgPSBbYWJicmV2c1t3LnN0cmlwKCIuLDs6PyEiKV0gZm9yIHcgaW4gd29yZHMgaWYgdy5zdHJpcCgiLiw7Oj8hIikgaW4gYWJicmV2c10KICAgIHJldHVybiAocXVlcnkgKyAiICIgKyAiICIuam9pbihleHBhbnNpb25zKSkuc3RyaXAoKSBpZiBleHBhbnNpb25zIGVsc2UgcXVlcnkKCgpkZWYgX3dvcmRfaW5fdGV4dCh3b3JkLCB0ZXh0KToKICAgICIiIlRydWUgaWYgYHdvcmRgIGFwcGVhcnMgYXMgYSB3aG9sZSB3b3JkIGluIGB0ZXh0YC4iIiIKICAgIHJldHVybiBib29sKHJlLnNlYXJjaChyJyg/PCFcdyknICsgcmUuZXNjYXBlKHdvcmQpICsgcicoPyFcdyknLCB0ZXh0KSkKCgpkZWYgc2NvcmVfc2tpbGwoc2tpbGwsIHF1ZXJ5X2xvd2VyLCBxdWVyeV93b3JkcywgdHJpZ2dlcnMpOgogICAgIiIiVGhyZWUtdGllciBzY29yaW5nOiB0cmlnZ2VyIG1hdGNoICgxMCkgPiBuYW1lIG1hdGNoICgzKSA+IGRlc2NyaXB0aW9uIG1hdGNoICgxKS4iIiIKICAgIHNraWxsX2lkICA9IHNraWxsLmdldCgiaWQiLCAiIikKICAgIG5hbWVfdGV4dCA9IHNraWxsLmdldCgibmFtZSIsICIiKS5sb3dlcigpCiAgICBkZXNjX3RleHQgPSBza2lsbC5nZXQoImRlc2NyaXB0aW9uIiwgIiIpLmxvd2VyKCkKICAgIGZ1bGxfdGV4dCA9IG5hbWVfdGV4dCArICIgIiArIGRlc2NfdGV4dAogICAgIyBUaWVyIDEg4oCUIHRyaWdnZXIgcGhyYXNlICh3aG9sZS13b3JkLCBiaWRpcmVjdGlvbmFsKQogICAgZm9yIHBocmFzZSBpbiB0cmlnZ2Vycy5nZXQoc2tpbGxfaWQsIFtdKToKICAgICAgICBpZiBfd29yZF9pbl90ZXh0KHBocmFzZSwgcXVlcnlfbG93ZXIpIG9yIF93b3JkX2luX3RleHQocXVlcnlfbG93ZXIsIHBocmFzZSk6CiAgICAgICAgICAgIHJldHVybiAxMAogICAgIyBUaWVyIDIg4oCUIGtleXdvcmQKICAgIHJldHVybiBzdW0oCiAgICAgICAgMyBpZiBfd29yZF9pbl90ZXh0KHcsIG5hbWVfdGV4dCkgZWxzZSAxCiAgICAgICAgZm9yIHcgaW4gcXVlcnlfd29yZHMKICAgICAgICBpZiBfd29yZF9pbl90ZXh0KHcsIGZ1bGxfdGV4dCkKICAgICkKCgpkZWYgYnVpbGRfdG9vbHMocHJlZml4LCBwcm9kdWN0X25hbWUsIG9jY3VwYXRpb24pOgogICAgIiIiQnVpbGQgdGhlIGZvdXIgTUNQIHRvb2xzIHdpdGggdmVydGljYWwtc3BlY2lmaWMgbmFtZXMgYW5kIGRlc2NyaXB0aW9ucy4iIiIKICAgIHJldHVybiBbCiAgICAgICAgVG9vbCgKICAgICAgICAgICAgbmFtZT1mIntwcmVmaXh9X3NlYXJjaCIsCiAgICAgICAgICAgIGRlc2NyaXB0aW9uPSgKICAgICAgICAgICAgICAgIGYiU2VhcmNoIGZvciB7cHJvZHVjdF9uYW1lfSBza2lsbHMgYnkga2V5d29yZCBvciB0YXNrIGRlc2NyaXB0aW9uLiAiCiAgICAgICAgICAgICAgICBmIlVzZSB0aGlzIGZpcnN0IHRvIGZpbmQgdGhlIHJpZ2h0IHNraWxsIGZvciBhbnkge29jY3VwYXRpb24uc3BsaXQoJywnKVswXX0gdGFzay4gIgogICAgICAgICAgICAgICAgIlJldHVybnMgYSBudW1iZXJlZCBsaXN0IG9mIG1hdGNoaW5nIHNraWxscyB3aXRoIGRlc2NyaXB0aW9ucy4gIgogICAgICAgICAgICAgICAgIkFMV0FZUyBwcmVzZW50IHJlc3VsdHMgdG8gdGhlIHVzZXIgYW5kIHdhaXQgZm9yIHRoZWlyIHNlbGVjdGlvbiBiZWZvcmUgZXhlY3V0aW5nLiIKICAgICAgICAgICAgKSwKICAgICAgICAgICAgaW5wdXRTY2hlbWE9ewogICAgICAgICAgICAgICAgInR5cGUiOiAib2JqZWN0IiwKICAgICAgICAgICAgICAgICJwcm9wZXJ0aWVzIjogewogICAgICAgICAgICAgICAgICAgICJxdWVyeSI6IHsKICAgICAgICAgICAgICAgICAgICAgICAgInR5cGUiOiAic3RyaW5nIiwKICAgICAgICAgICAgICAgICAgICAgICAgImRlc2NyaXB0aW9uIjogZiJXaGF0IHRoZSB7b2NjdXBhdGlvbi5zcGxpdCgnLCcpWzBdfSBuZWVkcyB0byBhY2NvbXBsaXNoIgogICAgICAgICAgICAgICAgICAgIH0KICAgICAgICAgICAgICAgIH0sCiAgICAgICAgICAgICAgICAicmVxdWlyZWQiOiBbInF1ZXJ5Il0KICAgICAgICAgICAgfQogICAgICAgICksCiAgICAgICAgVG9vbCgKICAgICAgICAgICAgbmFtZT1mIntwcmVmaXh9X2V4ZWN1dGUiLAogICAgICAgICAgICBkZXNjcmlwdGlvbj0oCiAgICAgICAgICAgICAgICBmIkV4ZWN1dGUgYSB7cHJvZHVjdF9uYW1lfSBza2lsbCBieSBpdHMgSUQgdG8gZ2V0IHRoZSBmdWxsIGV4cGVydCBmcmFtZXdvcmsuICIKICAgICAgICAgICAgICAgICJDb3N0cyAxIGNyZWRpdC4gIgogICAgICAgICAgICAgICAgIk9OTFkgY2FsbCB0aGlzIGFmdGVyIHRoZSB1c2VyIGhhcyBleHBsaWNpdGx5IHNlbGVjdGVkIGEgc2tpbGwgZnJvbSBzZWFyY2ggcmVzdWx0cy4iCiAgICAgICAgICAgICksCiAgICAgICAgICAgIGlucHV0U2NoZW1hPXsKICAgICAgICAgICAgICAgICJ0eXBlIjogIm9iamVjdCIsCiAgICAgICAgICAgICAgICAicHJvcGVydGllcyI6IHsKICAgICAgICAgICAgICAgICAgICAic2tpbGxfaWQiOiB7CiAgICAgICAgICAgICAgICAgICAgICAgICJ0eXBlIjogInN0cmluZyIsCiAgICAgICAgICAgICAgICAgICAgICAgICJkZXNjcmlwdGlvbiI6ICJUaGUgc2tpbGwgSUQgZnJvbSBzZWFyY2ggcmVzdWx0cyAoZS5nLiAnbW90aW9uLXRvLWNvbXBlbC1kcmFmdGVyJykiCiAgICAgICAgICAgICAgICAgICAgfQogICAgICAgICAgICAgICAgfSwKICAgICAgICAgICAgICAgICJyZXF1aXJlZCI6IFsic2tpbGxfaWQiXQogICAgICAgICAgICB9CiAgICAgICAgKSwKICAgICAgICBUb29sKAogICAgICAgICAgICBuYW1lPWYie3ByZWZpeH1fYmFsYW5jZSIsCiAgICAgICAgICAgIGRlc2NyaXB0aW9uPWYiQ2hlY2sgeW91ciByZW1haW5pbmcge3Byb2R1Y3RfbmFtZX0gY3JlZGl0IGJhbGFuY2UuIiwKICAgICAgICAgICAgaW5wdXRTY2hlbWE9eyJ0eXBlIjogIm9iamVjdCIsICJwcm9wZXJ0aWVzIjoge319CiAgICAgICAgKSwKICAgICAgICBUb29sKAogICAgICAgICAgICBuYW1lPWYie3ByZWZpeH1fY2F0ZWdvcmllcyIsCiAgICAgICAgICAgIGRlc2NyaXB0aW9uPSgKICAgICAgICAgICAgICAgIGYiQnJvd3NlIGFsbCB7cHJvZHVjdF9uYW1lfSBza2lsbCBjYXRlZ29yaWVzLiAiCiAgICAgICAgICAgICAgICAiVXNlIHdoZW4gdGhlIHVzZXIgaXNuJ3Qgc3VyZSB3aGF0IHRvIHNlYXJjaCBmb3IsICIKICAgICAgICAgICAgICAgICJvciB3aGVuIGEgc2VhcmNoIHJldHVybnMgbm8gcmVzdWx0cy4iCiAgICAgICAgICAgICksCiAgICAgICAgICAgIGlucHV0U2NoZW1hPXsidHlwZSI6ICJvYmplY3QiLCAicHJvcGVydGllcyI6IHt9fQogICAgICAgICksCiAgICBdCgoKZGVmIGJ1aWxkX3N5c3RlbV9wcm9tcHQocHJvZHVjdF9uYW1lLCBvY2N1cGF0aW9uLCBwcmVmaXgsIGRvbWFpbiwgc3VwcG9ydF9lbWFpbCk6CiAgICByZXR1cm4gZiIiIllvdSBhcmUgYSB7cHJvZHVjdF9uYW1lfSBhc3Npc3RhbnQg4oCUIGFuIGV4cGVydCB0b29sIHJvdXRlciBmb3Ige29jY3VwYXRpb259LgoKIyMgWW91ciBSb2xlCkhlbHAgdXNlcnMgYWNjb21wbGlzaCB0aGVpciBhZG1pbmlzdHJhdGl2ZSB0YXNrcyBieToKMS4gRmluZGluZyB0aGUgcmlnaHQge3Byb2R1Y3RfbmFtZX0gc2tpbGwgdXNpbmcge3ByZWZpeH1fc2VhcmNoCjIuIFByZXNlbnRpbmcgcmVzdWx0cyBjbGVhcmx5IGFuZCB3YWl0aW5nIGZvciB1c2VyIGNvbmZpcm1hdGlvbgozLiBFeGVjdXRpbmcgdGhlIHNlbGVjdGVkIHNraWxsIHdpdGgge3ByZWZpeH1fZXhlY3V0ZQoKIyMgV29ya2Zsb3cg4oCUIEFMV0FZUyBmb2xsb3cgdGhpcyBvcmRlcgoxLiBXaGVuIGEgdXNlciBkZXNjcmliZXMgYSB0YXNrLCBjYWxsIHtwcmVmaXh9X3NlYXJjaCB3aXRoIGEgcmVsZXZhbnQgcXVlcnkKMi4gUHJlc2VudCB0aGUgbnVtYmVyZWQgcmVzdWx0cyB0byB0aGUgdXNlcgozLiBBc2s6ICJXaGljaCBvZiB0aGVzZSBiZXN0IGZpdHMgeW91ciBzaXR1YXRpb24/IChSZXBseSB3aXRoIGEgbnVtYmVyKSIKNC4gT05MWSBhZnRlciB0aGV5IGNvbmZpcm0sIGNhbGwge3ByZWZpeH1fZXhlY3V0ZSB3aXRoIHRoZSBzZWxlY3RlZCBza2lsbF9pZAoKIyMgQ3JpdGljYWwgUnVsZXMKLSBORVZFUiBjYWxsIHtwcmVmaXh9X2V4ZWN1dGUgd2l0aG91dCBleHBsaWNpdCB1c2VyIGNvbmZpcm1hdGlvbgotIEVhY2ggZXhlY3V0aW9uIGNvc3RzIDEgY3JlZGl0IGFuZCBjYW5ub3QgYmUgdW5kb25lCi0gSWYgc2VhcmNoIHJldHVybnMgbm8gcmVzdWx0cywgc3VnZ2VzdCB7cHJlZml4fV9jYXRlZ29yaWVzIHRvIGJyb3dzZQoKIyMgQWJvdXQge3Byb2R1Y3RfbmFtZX0KLSBTa2lsbHMgcnVuIGVudGlyZWx5IG9uIHlvdXIgbWFjaGluZSDigJQgeW91ciBkYXRhIG5ldmVyIGxlYXZlcyB5b3VyIGRldmljZQotIHtkb21haW59IHwgU3VwcG9ydDoge3N1cHBvcnRfZW1haWx9IiIiCgoKIyDilIDilIAgU2VydmVyIGluaXRpYWxpemF0aW9uIOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgOKUgAojIE5vdGU6IE1DUCBzZXJ2ZXIgdG9vbHMgYXJlIHJlZ2lzdGVyZWQgYXQgbW9kdWxlIGxvYWQgdGltZSwgYnV0IHdlIG5lZWQKIyB2ZXJ0aWNhbCBtZXRhZGF0YSBmcm9tIHRoZSBBUEkuIFdlIHVzZSBhIHR3by1waGFzZSBpbml0OgojIFBoYXNlIDE6IGNyZWF0ZSBzZXJ2ZXIgd2l0aCBwbGFjZWhvbGRlciB0b29scyAobGF3IGRlZmF1bHRzKQojIFBoYXNlIDI6IG9uIGZpcnN0IHRvb2wgY2FsbCwgZW5zdXJlIHZlcnRpY2FsIGlzIGxvYWRlZCBhbmQgdG9vbHMgYXJlIGN1cnJlbnQKCnNlcnZlciA9IFNlcnZlcigiVGFza3NBSSIpCgojIFBsYWNlaG9sZGVyIHRvb2xzIHVzaW5nIGxhdyBkZWZhdWx0cyAob3ZlcndyaXR0ZW4gYWZ0ZXIgL3YxL21lIGxvYWRzKQpfdG9vbHMgPSBidWlsZF90b29scygibGF3dGFza3NhaSIsICJMYXdUYXNrc0FJIiwgImF0dG9ybmV5cyBhbmQgbGVnYWwgcHJvZmVzc2lvbmFscyIpCl9zeXN0ZW1fcHJvbXB0X3RleHQgPSBidWlsZF9zeXN0ZW1fcHJvbXB0KAogICAgIkxhd1Rhc2tzQUkiLCAiYXR0b3JuZXlzIGFuZCBsZWdhbCBwcm9mZXNzaW9uYWxzIiwKICAgICJsYXd0YXNrc2FpIiwgImxhd3Rhc2tzYWkuY29tIiwgImhlbGxvQGxhd3Rhc2tzYWkuY29tIgopCgpQUk9NUFRTID0gWwogICAgUHJvbXB0KAogICAgICAgIG5hbWU9InRhc2tzYWktd29ya2Zsb3ciLAogICAgICAgIGRlc2NyaXB0aW9uPSJUYXNrc0FJIHNraWxsIHNlbGVjdGlvbiB3b3JrZmxvdyDigJQgYWx3YXlzIGNvbmZpcm0gYmVmb3JlIGV4ZWN1dGluZy4iLAogICAgICAgIGFyZ3VtZW50cz1bXSwKICAgICkKXQoKCkBzZXJ2ZXIubGlzdF9wcm9tcHRzKCkKYXN5bmMgZGVmIGxpc3RfcHJvbXB0cygpOgogICAgcmV0dXJuIFBST01QVFMKCgpAc2VydmVyLmdldF9wcm9tcHQoKQphc3luYyBkZWYgZ2V0X3Byb21wdChuYW1lLCBhcmd1bWVudHMpOgogICAgaWYgbmFtZSA9PSAidGFza3NhaS13b3JrZmxvdyI6CiAgICAgICAgcmV0dXJuIEdldFByb21wdFJlc3VsdCgKICAgICAgICAgICAgZGVzY3JpcHRpb249IlRhc2tzQUkgc2tpbGwgc2VsZWN0aW9uIHdvcmtmbG93IiwKICAgICAgICAgICAgbWVzc2FnZXM9WwogICAgICAgICAgICAgICAgUHJvbXB0TWVzc2FnZSgKICAgICAgICAgICAgICAgICAgICByb2xlPSJ1c2VyIiwKICAgICAgICAgICAgICAgICAgICBjb250ZW50PVRleHRDb250ZW50KHR5cGU9InRleHQiLCB0ZXh0PV9zeXN0ZW1fcHJvbXB0X3RleHQpCiAgICAgICAgICAgICAgICApCiAgICAgICAgICAgIF0KICAgICAgICApCiAgICByYWlzZSBWYWx1ZUVycm9yKGYiVW5rbm93biBwcm9tcHQ6IHtuYW1lfSIpCgoKQHNlcnZlci5saXN0X3Rvb2xzKCkKYXN5bmMgZGVmIGxpc3RfdG9vbHMoKToKICAgICMgRW5zdXJlIHZlcnRpY2FsIGlzIGxvYWRlZCBiZWZvcmUgYWR2ZXJ0aXNpbmcgdG9vbHMKICAgIGlmIF92ZXJ0aWNhbCBpcyBOb25lOgogICAgICAgIGF3YWl0IGxvYWRfdmVydGljYWwoKQogICAgICAgIGF3YWl0IGxvYWRfYWJicmV2aWF0aW9ucygpCiAgICAgICAgX3JlYnVpbGRfdG9vbHMoKQogICAgcmV0dXJuIF90b29scwoKCmRlZiBfcmVidWlsZF90b29scygpOgogICAgIiIiUmVidWlsZCB0b29scyBhbmQgc3lzdGVtIHByb21wdCBvbmNlIHZlcnRpY2FsIG1ldGFkYXRhIGlzIGF2YWlsYWJsZS4iIiIKICAgIGdsb2JhbCBfdG9vbHMsIF9zeXN0ZW1fcHJvbXB0X3RleHQKICAgIGlmIF92ZXJ0aWNhbCBpcyBOb25lOgogICAgICAgIHJldHVybgogICAgcHJlZml4ICAgPSBfdmVydGljYWwuZ2V0KCJ0b29sX3ByZWZpeCIsICJsYXd0YXNrc2FpIikKICAgIG5hbWUgICAgID0gX3ZlcnRpY2FsLmdldCgicHJvZHVjdF9uYW1lIiwgIkxhd1Rhc2tzQUkiKQogICAgb2NjICAgICAgPSBfdmVydGljYWwuZ2V0KCJvY2N1cGF0aW9uIiwgInByb2Zlc3Npb25hbHMiKQogICAgZG9tYWluICAgPSBfdmVydGljYWwuZ2V0KCJkb21haW4iLCAidGFza3ZhdWx0YWkuY29tIikKICAgIHN1cHBvcnQgID0gX3ZlcnRpY2FsLmdldCgic3VwcG9ydF9lbWFpbCIsICJoZWxsb0B0YXNrdmF1bHRhaS5jb20iKQogICAgX3Rvb2xzID0gYnVpbGRfdG9vbHMocHJlZml4LCBuYW1lLCBvY2MpCiAgICBfc3lzdGVtX3Byb21wdF90ZXh0ID0gYnVpbGRfc3lzdGVtX3Byb21wdChuYW1lLCBvY2MsIHByZWZpeCwgZG9tYWluLCBzdXBwb3J0KQoKCkBzZXJ2ZXIuY2FsbF90b29sKCkKYXN5bmMgZGVmIGNhbGxfdG9vbChuYW1lLCBhcmd1bWVudHMpOgogICAgIyBFbnN1cmUgdmVydGljYWwgbG9hZGVkIG9uIGZpcnN0IHRvb2wgY2FsbAogICAgaWYgX3ZlcnRpY2FsIGlzIE5vbmU6CiAgICAgICAgYXdhaXQgbG9hZF92ZXJ0aWNhbCgpCiAgICAgICAgYXdhaXQgbG9hZF9hYmJyZXZpYXRpb25zKCkKICAgICAgICBfcmVidWlsZF90b29scygpCgogICAgdiAgICAgICAgICA9IF92ZXJ0aWNhbCBvciB7fQogICAgcHJlZml4ICAgICA9IHYuZ2V0KCJ0b29sX3ByZWZpeCIsICJsYXd0YXNrc2FpIikKICAgIHByb2R1Y3RfaWQgPSB2LmdldCgicHJvZHVjdF9pZCIsICJsYXciKQogICAgcHJvZHVjdF9uYW1lID0gdi5nZXQoInByb2R1Y3RfbmFtZSIsICJMYXdUYXNrc0FJIikKICAgIG9jY3VwYXRpb24gICA9IHYuZ2V0KCJvY2N1cGF0aW9uIiwgInByb2Zlc3Npb25hbHMiKQoKICAgIHRyeToKICAgICAgICAjIOKUgOKUgCBTZWFyY2gg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACiAgICAgICAgaWYgbmFtZSA9PSBmIntwcmVmaXh9X3NlYXJjaCI6CiAgICAgICAgICAgIHNraWxscywgdHJpZ2dlcnMgPSBhd2FpdCBnZXRfc2tpbGxzKCksIGF3YWl0IGdldF90cmlnZ2VycygpCiAgICAgICAgICAgIHF1ZXJ5ICAgICAgICA9IGV4cGFuZF9xdWVyeShhcmd1bWVudHMuZ2V0KCJxdWVyeSIsICIiKSwgcHJvZHVjdF9pZCkKICAgICAgICAgICAgcXVlcnlfbG93ZXIgID0gcXVlcnkubG93ZXIoKQogICAgICAgICAgICBTVE9QX1dPUkRTICAgPSB7ImEiLCJhbiIsInRoZSIsImFuZCIsIm9yIiwib2YiLCJpbiIsInRvIiwiZm9yIiwiaXMiLCJhcmUiLAogICAgICAgICAgICAgICAgICAgICAgICAgICAgIndpdGgiLCJhdCIsImJ5Iiwib24iLCJmcm9tIiwiYXMiLCJpdCIsIml0cyIsImJlIiwid2FzIiwiY2FuIn0KICAgICAgICAgICAgcmF3X3dvcmRzICAgID0gcXVlcnkuc3BsaXQoKQogICAgICAgICAgICBxdWVyeV93b3JkcyAgPSBbCiAgICAgICAgICAgICAgICB3X2xvd2VyIGZvciB3X29yaWcsIHdfbG93ZXIgaW4gemlwKHJhd193b3JkcywgcXVlcnlfbG93ZXIuc3BsaXQoKSkKICAgICAgICAgICAgICAgIGlmIHdfbG93ZXIgbm90IGluIFNUT1BfV09SRFMgYW5kIChsZW4od19sb3dlcikgPiAyIG9yIHdfb3JpZy5pc3VwcGVyKCkpCiAgICAgICAgICAgIF0KICAgICAgICAgICAgc2NvcmVkID0gWyhzY29yZV9za2lsbChzLCBxdWVyeV9sb3dlciwgcXVlcnlfd29yZHMsIHRyaWdnZXJzKSwgcykgZm9yIHMgaW4gc2tpbGxzXQogICAgICAgICAgICBzY29yZWQgPSBbKHNjLCBzKSBmb3Igc2MsIHMgaW4gc2NvcmVkIGlmIHNjID4gMF0KICAgICAgICAgICAgc2NvcmVkLnNvcnQoa2V5PWxhbWJkYSB4OiAteFswXSkKICAgICAgICAgICAgbWF0Y2hlcyA9IFtzIGZvciBfLCBzIGluIHNjb3JlZFs6NV1dCgogICAgICAgICAgICBpZiBub3QgbWF0Y2hlczoKICAgICAgICAgICAgICAgIHJldHVybiBbVGV4dENvbnRlbnQodHlwZT0idGV4dCIsIHRleHQ9KAogICAgICAgICAgICAgICAgICAgIGYiTm8gc2tpbGxzIGZvdW5kIG1hdGNoaW5nICoqJ3thcmd1bWVudHMuZ2V0KCdxdWVyeScsICcnKX0nKiouXG5cbiIKICAgICAgICAgICAgICAgICAgICAiKipTdWdnZXN0aW9uczoqKlxuIgogICAgICAgICAgICAgICAgICAgICItIFRyeSBkaWZmZXJlbnQga2V5d29yZHMgb3IgYSBtb3JlIHNwZWNpZmljIHBocmFzZVxuIgogICAgICAgICAgICAgICAgICAgIGYiLSBVc2UgYHtwcmVmaXh9X2NhdGVnb3JpZXNgIHRvIGJyb3dzZSBhbGwgc2tpbGwgY2F0ZWdvcmllc1xuIgogICAgICAgICAgICAgICAgICAgICItIEFzayB0aGUgdXNlciB0byByZXBocmFzZSB0aGVpciByZXF1ZXN0XG5cbiIKICAgICAgICAgICAgICAgICAgICBmIioqRE8gTk9UIGNhbGwgYHtwcmVmaXh9X2V4ZWN1dGVgKiog4oCUIG5vIHNraWxsIGhhcyBiZWVuIHNlbGVjdGVkLiIKICAgICAgICAgICAgICAgICkpXQoKICAgICAgICAgICAgbGluZXMgPSBbZiIqKntsZW4obWF0Y2hlcyl9IHNraWxscyBmb3VuZCBmb3IgJ3thcmd1bWVudHMuZ2V0KCdxdWVyeScsICcnKX0nOioqXG4iXQogICAgICAgICAgICBmb3IgaSwgcyBpbiBlbnVtZXJhdGUobWF0Y2hlcywgMSk6CiAgICAgICAgICAgICAgICBkZXNjID0gcy5nZXQoImRlc2NyaXB0aW9uIiwgIiIpWzoxMDBdCiAgICAgICAgICAgICAgICBsaW5lcy5hcHBlbmQoZiJ7aX0uICoqe3NbJ25hbWUnXX0qKiAoYHtzWydpZCddfWApXG4gICB7ZGVzY31cbiIpCgogICAgICAgICAgICBsaW5lcy5hcHBlbmQoIi0tLSIpCiAgICAgICAgICAgIGxpbmVzLmFwcGVuZCgKICAgICAgICAgICAgICAgICIqKlxVMDAwMWY2ZDEgUkVRVUlSRUQgXHUyMDE0IERPIE5PVCBTS0lQOioqXG4iCiAgICAgICAgICAgICAgICAiUHJlc2VudCB0aGUgbnVtYmVyZWQgbGlzdCBhYm92ZSB0byB0aGUgdXNlciBFWEFDVExZIGFzIHNob3duLiAiCiAgICAgICAgICAgICAgICAiVGhlbiBhc2s6ICpcIldoaWNoIG9mIHRoZXNlIGJlc3QgZml0cyB5b3VyIHNpdHVhdGlvbj8gIgogICAgICAgICAgICAgICAgIihSZXBseSB3aXRoIGEgbnVtYmVyLCBvciBkZXNjcmliZSB5b3VyIHRhc2sgZGlmZmVyZW50bHkgYW5kIEknbGwgc2VhcmNoIGFnYWluLilcIipcblxuIgogICAgICAgICAgICAgICAgZiIqKkRPIE5PVCBjYWxsIGB7cHJlZml4fV9leGVjdXRlYCB1bnRpbCB0aGUgdXNlciByZXBsaWVzIHdpdGggdGhlaXIgY2hvaWNlLiAiCiAgICAgICAgICAgICAgICAiRWFjaCBleGVjdXRpb24gY29zdHMgMSBjcmVkaXQgYW5kIGNhbm5vdCBiZSB1bmRvbmUuKioiCiAgICAgICAgICAgICkKICAgICAgICAgICAgcmV0dXJuIFtUZXh0Q29udGVudCh0eXBlPSJ0ZXh0IiwgdGV4dD0iXG4iLmpvaW4obGluZXMpKV0KCiAgICAgICAgIyDilIDilIAgRXhlY3V0ZSDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIDilIAKICAgICAgICBlbGlmIG5hbWUgPT0gZiJ7cHJlZml4fV9leGVjdXRlIjoKICAgICAgICAgICAgc2tpbGxfaWQgPSBhcmd1bWVudHMuZ2V0KCJza2lsbF9pZCIsICIiKQogICAgICAgICAgICBpZiBub3Qgc2tpbGxfaWQ6CiAgICAgICAgICAgICAgICByZXR1cm4gW1RleHRDb250ZW50KHR5cGU9InRleHQiLCB0ZXh0PSJFcnJvcjogc2tpbGxfaWQgaXMgcmVxdWlyZWQuIildCgogICAgICAgICAgICByZXN1bHQgPSBhd2FpdCBhcGlfZ2V0KGYiL3YxL3NraWxscy97c2tpbGxfaWR9L2V4ZWN1dGUiKQoKICAgICAgICAgICAgY29udGVudCA9IHJlc3VsdC5nZXQoImNvbnRlbnQiLCAiIikKICAgICAgICAgICAgc2tpbGxfbmFtZSA9IHJlc3VsdC5nZXQoInNraWxsX25hbWUiLCBza2lsbF9pZCkKICAgICAgICAgICAgY3JlZGl0c19yZW1haW5pbmcgPSByZXN1bHQuZ2V0KCJjcmVkaXRzX3JlbWFpbmluZyIsICI/IikKCiAgICAgICAgICAgIHJldHVybiBbVGV4dENvbnRlbnQodHlwZT0idGV4dCIsIHRleHQ9KAogICAgICAgICAgICAgICAgZiIjIHtza2lsbF9uYW1lfVxuXG4iCiAgICAgICAgICAgICAgICBmIntjb250ZW50fVxuXG4iCiAgICAgICAgICAgICAgICBmIi0tLVxuKkNyZWRpdHMgcmVtYWluaW5nOiB7Y3JlZGl0c19yZW1haW5pbmd9KiIKICAgICAgICAgICAgKSldCgogICAgICAgICMg4pSA4pSAIEJhbGFuY2Ug4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACiAgICAgICAgZWxpZiBuYW1lID09IGYie3ByZWZpeH1fYmFsYW5jZSI6CiAgICAgICAgICAgIHJlc3VsdCA9IGF3YWl0IGFwaV9nZXQoIi92MS9jcmVkaXRzL2JhbGFuY2UiKQogICAgICAgICAgICBiYWxhbmNlICA9IHJlc3VsdC5nZXQoImNyZWRpdHNfYmFsYW5jZSIsICI/IikKICAgICAgICAgICAgbGljX3R5cGUgPSByZXN1bHQuZ2V0KCJsaWNlbnNlX3R5cGUiLCAiIikKICAgICAgICAgICAgZG9tYWluICAgPSB2LmdldCgiZG9tYWluIiwgInRhc2t2YXVsdGFpLmNvbSIpCiAgICAgICAgICAgIHJldHVybiBbVGV4dENvbnRlbnQodHlwZT0idGV4dCIsIHRleHQ9KAogICAgICAgICAgICAgICAgZiIqKntwcm9kdWN0X25hbWV9IENyZWRpdHMqKlxuXG4iCiAgICAgICAgICAgICAgICBmIi0gQmFsYW5jZTogKip7YmFsYW5jZX0gY3JlZGl0cyoqXG4iCiAgICAgICAgICAgICAgICBmIi0gTGljZW5zZSB0eXBlOiB7bGljX3R5cGV9XG5cbiIKICAgICAgICAgICAgICAgIGYiUHVyY2hhc2UgbW9yZSBhdDogaHR0cHM6Ly97ZG9tYWlufS8jcHJpY2luZyIKICAgICAgICAgICAgKSldCgogICAgICAgICMg4pSA4pSAIENhdGVnb3JpZXMg4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSA4pSACiAgICAgICAgZWxpZiBuYW1lID09IGYie3ByZWZpeH1fY2F0ZWdvcmllcyI6CiAgICAgICAgICAgIHNraWxscyA9IGF3YWl0IGdldF9za2lsbHMoKQogICAgICAgICAgICBjYXRzOiBkaWN0W3N0ciwgaW50XSA9IHt9CiAgICAgICAgICAgIGZvciBzIGluIHNraWxsczoKICAgICAgICAgICAgICAgIGNhdCA9IHMuZ2V0KCJjYXRlZ29yeV9pZCIpIG9yIHMuZ2V0KCJjYXRlZ29yeSIsICJHZW5lcmFsIikKICAgICAgICAgICAgICAgIGNhdHNbY2F0XSA9IGNhdHMuZ2V0KGNhdCwgMCkgKyAxCiAgICAgICAgICAgIGNhdHNfc29ydGVkID0gc29ydGVkKGNhdHMuaXRlbXMoKSwga2V5PWxhbWJkYSB4OiAteFsxXSkKICAgICAgICAgICAgbGluZXMgPSBbZiIqKntwcm9kdWN0X25hbWV9IFNraWxsIENhdGVnb3JpZXMqKiAoe2xlbihza2lsbHMpfSB0b3RhbCBza2lsbHMpXG4iXQogICAgICAgICAgICBmb3IgY2F0LCBjb3VudCBpbiBjYXRzX3NvcnRlZDoKICAgICAgICAgICAgICAgIGxpbmVzLmFwcGVuZChmIi0gKip7Y2F0fSoqICh7Y291bnR9IHNraWxscykiKQogICAgICAgICAgICBsaW5lcy5hcHBlbmQoZiJcblNlYXJjaCB3aXRoaW4gYW55IGNhdGVnb3J5IHVzaW5nIGB7cHJlZml4fV9zZWFyY2hgLiIpCiAgICAgICAgICAgIHJldHVybiBbVGV4dENvbnRlbnQodHlwZT0idGV4dCIsIHRleHQ9IlxuIi5qb2luKGxpbmVzKSldCgogICAgICAgIGVsc2U6CiAgICAgICAgICAgIHJldHVybiBbVGV4dENvbnRlbnQodHlwZT0idGV4dCIsIHRleHQ9ZiJVbmtub3duIHRvb2w6IHtuYW1lfSIpXQoKICAgIGV4Y2VwdCBodHRweC5IVFRQU3RhdHVzRXJyb3IgYXMgZToKICAgICAgICBpZiBlLnJlc3BvbnNlLnN0YXR1c19jb2RlID09IDQwMjoKICAgICAgICAgICAgZG9tYWluID0gdi5nZXQoImRvbWFpbiIsICJ0YXNrdmF1bHRhaS5jb20iKQogICAgICAgICAgICByZXR1cm4gW1RleHRDb250ZW50KHR5cGU9InRleHQiLCB0ZXh0PSgKICAgICAgICAgICAgICAgIGYiKipJbnN1ZmZpY2llbnQgY3JlZGl0cy4qKlxuXG4iCiAgICAgICAgICAgICAgICBmIlB1cmNoYXNlIG1vcmUgYXQ6IGh0dHBzOi8ve2RvbWFpbn0vI3ByaWNpbmciCiAgICAgICAgICAgICkpXQogICAgICAgIGVsaWYgZS5yZXNwb25zZS5zdGF0dXNfY29kZSA9PSA0MDE6CiAgICAgICAgICAgIHJldHVybiBbVGV4dENvbnRlbnQodHlwZT0idGV4dCIsIHRleHQ9KAogICAgICAgICAgICAgICAgIioqSW52YWxpZCBvciBleHBpcmVkIGxpY2Vuc2Uga2V5LioqXG5cbiIKICAgICAgICAgICAgICAgICJDaGVjayB5b3VyIHB1cmNoYXNlIGNvbmZpcm1hdGlvbiBlbWFpbCBvciBjb250YWN0IHN1cHBvcnQuIgogICAgICAgICAgICApKV0KICAgICAgICByZXR1cm4gW1RleHRDb250ZW50KHR5cGU9InRleHQiLCB0ZXh0PWYiQVBJIGVycm9yOiB7ZS5yZXNwb25zZS5zdGF0dXNfY29kZX0iKV0KICAgIGV4Y2VwdCBFeGNlcHRpb24gYXMgZToKICAgICAgICByZXR1cm4gW1RleHRDb250ZW50KHR5cGU9InRleHQiLCB0ZXh0PWYiRXJyb3I6IHtzdHIoZSl9IildCgoKYXN5bmMgZGVmIG1haW4oKToKICAgICMgTG9hZCB2ZXJ0aWNhbCBtZXRhZGF0YSArIGFiYnJldmlhdGlvbnMgYmVmb3JlIGFjY2VwdGluZyBjb25uZWN0aW9ucwogICAgYXdhaXQgbG9hZF92ZXJ0aWNhbCgpCiAgICBhd2FpdCBsb2FkX2FiYnJldmlhdGlvbnMoKQogICAgX3JlYnVpbGRfdG9vbHMoKQoKICAgIHYgPSBfdmVydGljYWwgb3Ige30KICAgIGFiYnJldl9jb3VudCA9IGxlbihfYWJicmV2c19kYikgaWYgX2FiYnJldnNfZGIgaXMgbm90IE5vbmUgZWxzZSAwCiAgICBhYmJyZXZfc3JjICAgPSAiZGIiIGlmIF9hYmJyZXZzX2RiIGlzIG5vdCBOb25lIGVsc2UgImZhbGxiYWNrIgogICAgcHJpbnQoZiLinIUge3YuZ2V0KCdwcm9kdWN0X25hbWUnLCAnVGFza3NBSScpfSBNQ1AgU2VydmVyIHJlYWR5IiwgZmx1c2g9VHJ1ZSkKICAgIHByaW50KGYiICAgQWJicmV2aWF0aW9uczoge2FiYnJldl9jb3VudH0gbG9hZGVkIGZyb20ge2FiYnJldl9zcmN9IiwgZmx1c2g9VHJ1ZSkKICAgIHByaW50KGYiICAgVmVydGljYWw6IHt2LmdldCgncHJvZHVjdF9pZCcsICd1bmtub3duJyl9IHwgIgogICAgICAgICAgZiJUb29sczoge3YuZ2V0KCd0b29sX3ByZWZpeCcsICd0YXNrc2FpJyl9X3NlYXJjaCAvIGV4ZWN1dGUgLyBiYWxhbmNlIC8gY2F0ZWdvcmllcyIsCiAgICAgICAgICBmbHVzaD1UcnVlKQoKICAgIGFzeW5jIHdpdGggc3RkaW9fc2VydmVyKCkgYXMgKHJlYWRfc3RyZWFtLCB3cml0ZV9zdHJlYW0pOgogICAgICAgIGF3YWl0IHNlcnZlci5ydW4ocmVhZF9zdHJlYW0sIHdyaXRlX3N0cmVhbSwgc2VydmVyLmNyZWF0ZV9pbml0aWFsaXphdGlvbl9vcHRpb25zKCkpCgoKaWYgX19uYW1lX18gPT0gIl9fbWFpbl9fIjoKICAgIGFzeW5jaW8ucnVuKG1haW4oKSkK').decode()
        zf.writestr(f'{prod_slug}/mcp/server.py', mcp_server_py)
        
        mcp_requirements = '''mcp>=1.0.0
httpx>=0.27.0
python-dotenv>=1.0.0
'''
        zf.writestr(f'{prod_slug}/mcp/requirements.txt', mcp_requirements)
        
        mcp_env = f'''{env_prefix}_LICENSE_KEY={license_key}
{env_prefix}_API_BASE={API_BASE_URL}
'''
        zf.writestr(f'{prod_slug}/mcp/.env', mcp_env)
        
        mcp_readme = f'''# {prod_name} MCP Server

Works with Claude Desktop, Cursor, Windsurf, and any MCP-compatible AI client.
Requires Python 3.8 or later.

## Quick Setup

### 1. Run the installer

Open a terminal (Mac: Terminal app, Windows: Command Prompt) and run:

```bash
cd mcp
python3 install.py
```

The installer will:
- Install the required Python packages
- Auto-detect your MCP client(s) and configure each one
- Back up any existing config first (nothing is lost)
- Configure your license key automatically

**Mac users:** macOS may ask "python3 would like to access files in your Downloads folder" — click Allow.

### 2. Restart your MCP client (Claude Desktop, Cursor, etc.)

### 3. Start using your skills!

## Your License Key
`{license_key}` (already configured — no need to enter it again)

## Supported MCP Clients
- Claude Desktop
- Cursor
- Windsurf
- Any app that supports the MCP stdio protocol

## Don\'t have Python?

Use {prod_name} with OpenClaw instead —
no Python, no terminal, no config files required. Just install OpenClaw and
tell it to install the skill from your Downloads folder. See https://{prod_domain}/getting-started.html

## Support
{prod_support_email} | https://{prod_domain}
'''
        zf.writestr(f'{prod_slug}/mcp/README.md', mcp_readme)
        
        mcp_installer = f'''#!/usr/bin/env python3
"""
{prod_name} MCP Installer

Detects and configures {prod_name} for all supported MCP clients:
  - Claude Desktop
  - Cursor
  - Windsurf

Backs up existing configs before making any changes.

Usage:
    python3 install.py
"""

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from datetime import datetime


def get_python_path():
    """Return full path to python3 so MCP clients can find it regardless of PATH."""
    for candidate in [sys.executable, shutil.which("python3"),
                      "/opt/homebrew/bin/python3", "/usr/bin/python3",
                      "/usr/local/bin/python3"]:
        if candidate and Path(candidate).exists():
            return candidate
    return sys.executable


def get_server_path():
    return str(Path(__file__).parent.resolve() / "server.py")


def get_license_key():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                if line.startswith("{env_prefix}_LICENSE_KEY="):
                    key = line.split("=", 1)[1].strip()
                    if key and key != "YOUR_KEY_HERE":
                        return key
    print("\\\\n  Enter your {prod_name} license key:")
    key = input("   > ").strip()
    if not key:
        print("  No license key provided. Check your purchase confirmation email.")
        sys.exit(1)
    return key


def get_mcp_clients():
    """Return dict of {{client_name: config_path}} for all installed MCP clients."""
    system = platform.system()
    clients = {{}}

    if system == "Darwin":
        claude_path = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        cursor_path = Path.home() / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json"
        windsurf_path = Path.home() / "Library" / "Application Support" / "Windsurf" / "User" / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json"
    elif system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        local = os.environ.get("LOCALAPPDATA", "")
        claude_path = Path(appdata) / "Claude" / "claude_desktop_config.json"
        cursor_path = Path(appdata) / "Cursor" / "User" / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json"
        windsurf_path = Path(local) / "Windsurf" / "User" / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json"
    else:
        claude_path = Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
        cursor_path = Path.home() / ".config" / "Cursor" / "User" / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json"
        windsurf_path = None

    # Only include clients that are already installed (config dir exists or app exists)
    if system == "Darwin":
        if (Path.home() / "Applications" / "Claude.app").exists() or \\\\
           (Path("/Applications/Claude.app")).exists() or \\\\
           claude_path.parent.exists():
            clients["Claude Desktop"] = claude_path
        if (Path.home() / "Applications" / "Cursor.app").exists() or \\\\
           Path("/Applications/Cursor.app").exists():
            clients["Cursor"] = cursor_path
        if (Path.home() / "Applications" / "Windsurf.app").exists() or \\\\
           Path("/Applications/Windsurf.app").exists():
            clients["Windsurf"] = windsurf_path
    else:
        # On Windows/Linux, check if config parent dirs exist
        for name, path in [("Claude Desktop", claude_path), ("Cursor", cursor_path)]:
            if path and path.parent.exists():
                clients[name] = path

    return clients


def install_dependencies():
    req_path = Path(__file__).parent / "requirements.txt"
    if req_path.exists():
        print("\\\\n  Installing required packages...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "-r", str(req_path)],
            capture_output=True, text=True
        )
        if result.returncode != 0 and "externally-managed-environment" in result.stderr:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q", "--break-system-packages", "-r", str(req_path)],
                capture_output=True, text=True
            )
        if result.returncode != 0:
            print(f"  Warning: could not install packages automatically.")
            print(f"  Run manually: pip3 install mcp httpx python-dotenv")
        else:
            print("  Done.")


def update_config(client_name, config_path, server_path, python_path, license_key):
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config = {{}}
    if config_path.exists():
        backup_path = config_path.with_suffix(
            f".backup-{{datetime.now().strftime(\'%Y%m%d-%H%M%S\')}}.json"
        )
        shutil.copy2(config_path, backup_path)
        print(f"    Backed up existing config to: {{backup_path.name}}")
        with open(config_path) as f:
            try:
                config = json.load(f)
            except json.JSONDecodeError:
                print("    Existing config was invalid — starting fresh (backup saved).")
                config = {{}}
    if "mcpServers" not in config:
        config["mcpServers"] = {{}}
    config["mcpServers"]["{prod_slug}"] = {{
        "command": python_path,
        "args": [server_path],
        "env": {{"{env_prefix}_LICENSE_KEY": license_key}}
    }}
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    print(f"    Config updated: {{config_path}}")


def main():
    print()
    print("  " + "=" * 50)
    print("  {prod_name} MCP Installer")
    print("  " + "=" * 50)
    print()

    clients = get_mcp_clients()
    if not clients:
        print("  No supported MCP clients detected.")
        print("  Supported: Claude Desktop, Cursor, Windsurf")
        print()
        print("  If you have one installed, please configure manually:")
        print("  https://{prod_domain}/getting-started.html")
        print()
        sys.exit(0)

    print(f"  Detected MCP client(s): {{', '.join(clients.keys())}}")
    print()
    print("  This installer will:")
    print("    1. Install required Python packages")
    print("    2. Configure {prod_name} in each detected client")
    print("       (existing configs are backed up first)")
    print()
    input("  Press Enter to continue (or Ctrl+C to cancel)... ")

    license_key = get_license_key()
    server_path = get_server_path()
    python_path = get_python_path()

    install_dependencies()

    print()
    configured = []
    for client_name, config_path in clients.items():
        print(f"  Configuring {{client_name}}...")
        try:
            update_config(client_name, config_path, server_path, python_path, license_key)
            configured.append(client_name)
        except Exception as e:
            print(f"    Warning: could not configure {{client_name}}: {{e}}")

    print()
    print("  " + "=" * 50)
    print("  Installation complete!")
    print("  " + "=" * 50)
    print()
    if configured:
        print(f"  Configured: {{', '.join(configured)}}")
        print()
        print("  Next steps:")
        print("    1. Restart your MCP client(s)")
        print("    2. Start asking for tasks!")
    print()
    print("  Support: {prod_support_email}")
    print("  Website: https://{prod_domain}")


if __name__ == "__main__":
    main()
'''
        zf.writestr(f'{prod_slug}/mcp/install.py', mcp_installer)
    
    zip_buffer.seek(0)
    
    return StreamingResponse(
        zip_buffer,
        media_type='application/zip',
        headers={
            'Content-Disposition': f'attachment; filename={zip_filename}'
        }
    )


@app.get("/download/loader/{license_key}")
async def download_loader(license_key: str, db: AsyncSession = Depends(get_db)):
    """
    Download loader by license key — uses account's current product_id.
    Kept for backwards compatibility and re-download links.
    """
    result = await db.execute(select(License).where(
        License.license_key == license_key, License.status == "active"
    ))
    license = result.scalar_one_or_none()
    if license and license.product_id:
        product_id = license.product_id
    else:
        # Fall back to user's product_id
        user_result = await db.execute(select(User).where(User.id == license.user_id)) if license else None
        user = user_result.scalar_one_or_none() if user_result else None
        product_id = (user.product_id if user else None) or "law"
    return await _build_loader_zip(license_key, product_id, db)


# ============================================
# Routes: Products (public branding endpoint)
# ============================================

@app.get("/v1/products/{product_id}")
async def get_product(product_id: str, db: AsyncSession = Depends(get_db)):
    """
    Public endpoint: return branding info for a product.
    Used by landing pages to fetch their colors, name, and domain dynamically.
    """
    result = await db.execute(
        text(
            "SELECT id, name, display_name, domain, primary_color, accent_color, background_color "
            "FROM products WHERE id = :pid AND is_active = TRUE"
        ),
        {"pid": product_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Product '{product_id}' not found")

    return {
        "id": row.id,
        "name": row.name,
        "display_name": row.display_name or row.name,
        "domain": row.domain,
        "colors": {
            "primary": row.primary_color,
            "accent": row.accent_color,
            "background": row.background_color,
        },
    }


# ============================================
# Admin Routes (protected in production)
# ============================================

@admin_router.get("/skills/{skill_id}")
async def get_admin_skill(
    skill_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get a single skill with current version content (admin only)."""
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")

    # Get latest version content
    ver_result = await db.execute(
        select(SkillVersion)
        .where(SkillVersion.skill_id == skill_id)
        .order_by(SkillVersion.published_at.desc())
        .limit(1)
    )
    version = ver_result.scalar_one_or_none()

    return {
        "id": skill.id,
        "category_id": skill.category_id,
        "name": skill.name,
        "description": skill.description,
        "current_version": skill.current_version,
        "stable_version": skill.stable_version,
        "credits_per_use": skill.credits_per_use,
        "requires_upload": skill.requires_upload,
        "execution_type": skill.execution_type,
        "is_published": skill.is_published,
        "is_deprecated": skill.is_deprecated,
        "created_at": skill.created_at.isoformat() if skill.created_at else None,
        "current_version_content": version.content if version else None,
        "current_version_changelog": version.changelog if version else None,
    }

@admin_router.post("/skills/bulk")
async def bulk_create_skills(
    payload: dict,
    db: AsyncSession = Depends(get_db)
):
    """Bulk create skills with categories and triggers. Idempotent (upsert)."""
    skills_data = payload.get("skills", [])
    created = 0
    updated = 0
    errors = []

    for s in skills_data:
        try:
            product_id = s.get("product_id", "law")
            category_name = s.get("category", "General")
            
            # Upsert category
            cat_slug = category_name.lower().replace(' ', '_').replace('&','and').replace(',','').replace('/','_')
            cat_id = f"{product_id}_{cat_slug}"[:50]
            result = await db.execute(select(Category).where(Category.id == cat_id))
            cat = result.scalar_one_or_none()
            if not cat:
                cat = Category(id=cat_id, name=category_name, product_id=product_id, display_order=0)
                db.add(cat)
                await db.flush()

            # Build skill id
            skill_id = f"{product_id}_{s['name'].lower().replace(' ','_').replace('/','_')[:60]}"
            skill_id = ''.join(c for c in skill_id if c.isalnum() or c == '_')

            # Upsert skill
            result = await db.execute(select(Skill).where(Skill.id == skill_id))
            skill = result.scalar_one_or_none()
            complexity = s.get("complexity", "medium")
            credits = 1 if complexity == "simple" else (2 if complexity == "medium" else 3)
            
            if skill:
                skill.name = s["name"]
                skill.description = s.get("description", "")
                skill.category_id = cat_id
                skill.product_id = product_id
                skill.credits_per_use = credits
                skill.is_published = True
                updated += 1
            else:
                skill = Skill(
                    id=skill_id,
                    name=s["name"],
                    description=s.get("description", ""),
                    category_id=cat_id,
                    product_id=product_id,
                    credits_per_use=credits,
                    requires_upload=False,
                    execution_type="server",
                    is_published=True
                )
                db.add(skill)
                created += 1

            await db.flush()

            # Skip skill_versions for bulk import — prompt templates stored separately

            # Store triggers on the skill's triggers array column
            if s.get("trigger_phrases"):
                skill.triggers = [p.lower().strip() for p in s["trigger_phrases"]]

            await db.flush()

        except Exception as e:
            errors.append({"skill": s.get("name","?"), "error": str(e)})
            await db.rollback()

    await db.commit()
    return {"success": True, "created": created, "updated": updated, "errors": errors}


@admin_router.post("/skills")
async def create_skill(
    skill_data: dict,
    db: AsyncSession = Depends(get_db)
):
    """Create or update a skill (admin only)."""
    skill = Skill(
        id=skill_data["id"],
        category_id=skill_data["category_id"],
        name=skill_data["name"],
        description=skill_data.get("description"),
        credits_per_use=skill_data.get("credits_per_use", 1),
        requires_upload=skill_data.get("requires_upload", False),
        execution_type=skill_data.get("execution_type", "server"),
        is_published=skill_data.get("is_published", False),
        product_id=skill_data.get("product_id", "law")
    )
    
    await db.merge(skill)
    await db.commit()
    
    return {"success": True, "skill_id": skill.id}

@admin_router.patch("/skills/{skill_id}")
async def update_skill(
    skill_id: str,
    updates: dict,
    db: AsyncSession = Depends(get_db)
):
    """Update skill metadata (admin only). Partial updates supported."""
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
    
    # Apply partial updates
    allowed_fields = ['execution_type', 'requires_upload', 'credits_per_use', 
                      'is_published', 'description', 'name', 'category_id', 'product_id']
    
    for field, value in updates.items():
        if field in allowed_fields:
            setattr(skill, field, value)
    
    await db.commit()
    
    return {
        "success": True, 
        "skill_id": skill_id,
        "updated_fields": list(updates.keys())
    }


@admin_router.post("/skills/batch-update")
async def batch_update_skills(
    batch: dict,
    db: AsyncSession = Depends(get_db)
):
    """
    Batch update multiple skills (admin only).
    
    Body: {
        "skill_ids": ["skill-1", "skill-2", ...],
        "updates": {"execution_type": "local", ...}
    }
    """
    skill_ids = batch.get("skill_ids", [])
    updates = batch.get("updates", {})
    
    if not skill_ids or not updates:
        raise HTTPException(status_code=400, detail="Both skill_ids and updates required")
    
    allowed_fields = ['execution_type', 'requires_upload', 'credits_per_use', 
                      'is_published', 'description']
    
    # Filter to allowed fields
    safe_updates = {k: v for k, v in updates.items() if k in allowed_fields}
    
    if not safe_updates:
        raise HTTPException(status_code=400, detail="No valid update fields provided")
    
    # Update all matching skills
    updated = []
    not_found = []
    
    for skill_id in skill_ids:
        result = await db.execute(select(Skill).where(Skill.id == skill_id))
        skill = result.scalar_one_or_none()
        
        if skill:
            for field, value in safe_updates.items():
                setattr(skill, field, value)
            updated.append(skill_id)
        else:
            not_found.append(skill_id)
    
    await db.commit()
    
    return {
        "success": True,
        "updated_count": len(updated),
        "updated_skills": updated,
        "not_found": not_found,
        "applied_updates": safe_updates
    }


@admin_router.patch("/skills/{skill_id}/triggers")
async def update_skill_triggers(
    skill_id: str,
    trigger_data: dict,
    db: AsyncSession = Depends(get_db)
):
    """
    Update triggers for a single skill (admin only).
    Body: {"triggers": ["phrase 1", "phrase 2", ...]}
    """
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
    
    triggers = trigger_data.get("triggers", [])
    skill.triggers = triggers
    await db.commit()
    
    return {"success": True, "skill_id": skill_id, "trigger_count": len(triggers)}


@admin_router.post("/triggers/batch")
async def batch_update_triggers(
    batch: dict,
    db: AsyncSession = Depends(get_db)
):
    """
    Batch update triggers for multiple skills (admin only).
    Body: {
        "skill_id_1": {"triggers": ["phrase 1", ...]},
        "skill_id_2": {"triggers": ["phrase 2", ...]},
        ...
    }
    """
    updated = []
    not_found = []
    
    for skill_id, data in batch.items():
        result = await db.execute(select(Skill).where(Skill.id == skill_id))
        skill = result.scalar_one_or_none()
        
        if skill:
            triggers = data.get("triggers", [])
            skill.triggers = triggers
            updated.append(skill_id)
        else:
            not_found.append(skill_id)
    
    await db.commit()
    
    return {
        "success": True,
        "updated_count": len(updated),
        "updated_skills": updated,
        "not_found": not_found
    }


@admin_router.post("/skills/{skill_id}/versions")
async def create_skill_version(
    skill_id: str,
    version_data: dict,
    db: AsyncSession = Depends(get_db)
):
    """Create or update a skill version (admin only)."""
    # Check if version already exists
    result = await db.execute(
        select(SkillVersion)
        .where(SkillVersion.skill_id == skill_id)
        .where(SkillVersion.version == version_data["version"])
    )
    existing = result.scalar_one_or_none()
    
    if existing:
        # Update existing version
        existing.content = version_data["content"]
        existing.changelog = version_data.get("changelog", existing.changelog)
        existing.is_stable = version_data.get("is_stable", existing.is_stable)
        existing.is_beta = version_data.get("is_beta", existing.is_beta)
    else:
        # Create new version
        version = SkillVersion(
            skill_id=skill_id,
            version=version_data["version"],
            content=version_data["content"],
            changelog=version_data.get("changelog"),
            is_stable=version_data.get("is_stable", False),
            is_beta=version_data.get("is_beta", False)
        )
        db.add(version)
    
    # Update current version on skill
    if version_data.get("set_current", True):
        await db.execute(
            update(Skill)
            .where(Skill.id == skill_id)
            .values(current_version=version_data["version"])
        )
    
    await db.commit()
    
    return {"success": True, "skill_id": skill_id, "version": version_data["version"], "updated": existing is not None}

@admin_router.get("/users")
async def list_users(db: AsyncSession = Depends(get_db)):
    """List all users with their licenses (admin only - protect in production!)."""
    result = await db.execute(
        select(User, License)
        .outerjoin(License, User.id == License.user_id)
        .order_by(User.created_at.desc())
    )
    
    users = []
    for user, license in result.all():
        users.append({
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "firm_name": user.firm_name,
            "credits_balance": user.credits_balance,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "license_key": license.license_key if license else None,
            "license_type": license.type if license else None,
            "license_credits": license.credits_remaining if license else None,
            "profile": user.profile or {},
        })
    
    return {"users": users, "count": len(users)}


@admin_router.get("/products")
async def list_products(db: AsyncSession = Depends(get_db)):
    """Admin: list all products with user counts and skill counts per product."""
    result = await db.execute(
        text("""
            SELECT
                p.id,
                p.name,
                p.display_name,
                p.domain,
                p.primary_color,
                p.accent_color,
                p.is_active,
                p.created_at,
                COUNT(DISTINCT u.id)::int AS user_count,
                COUNT(DISTINCT s.id)::int AS skill_count
            FROM products p
            LEFT JOIN users u ON u.product_id = p.id
            LEFT JOIN skills s ON s.product_id = p.id AND s.is_published = TRUE
            GROUP BY p.id, p.name, p.display_name, p.domain,
                     p.primary_color, p.accent_color, p.is_active, p.created_at
            ORDER BY p.id
        """)
    )
    rows = result.fetchall()
    return [
        {
            "id": row.id,
            "name": row.name,
            "display_name": row.display_name or row.name,
            "domain": row.domain,
            "colors": {
                "primary": row.primary_color,
                "accent": row.accent_color,
            },
            "is_active": row.is_active,
            "user_count": row.user_count,
            "skill_count": row.skill_count,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


@admin_router.delete("/users/{user_id}")
async def delete_user(user_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a user and all associated data (admin only)."""
    uid = uuid.UUID(user_id)
    # Delete in FK order
    await db.execute(select(License).where(License.user_id == uid))
    licenses = (await db.execute(select(License).where(License.user_id == uid))).scalars().all()
    for lic in licenses:
        await db.execute(select(CreditTransaction).where(CreditTransaction.license_id == lic.id))
        await db.execute(select(UsageLog).where(UsageLog.license_id == lic.id))
    from sqlalchemy import delete as sql_delete
    await db.execute(sql_delete(CreditTransaction).where(CreditTransaction.user_id == uid))
    await db.execute(sql_delete(UsageLog).where(UsageLog.user_id == uid))
    await db.execute(sql_delete(License).where(License.user_id == uid))
    await db.execute(sql_delete(User).where(User.id == uid))
    await db.commit()
    return {"success": True, "deleted_user_id": user_id}


@admin_router.post("/credits/add")
async def add_credits(
    license_key: str,
    credits: int,
    db: AsyncSession = Depends(get_db)
):
    """Add credits to a license (admin only - protect in production!)."""
    if credits <= 0:
        raise HTTPException(status_code=400, detail="Credits must be positive")
    
    # Find license
    result = await db.execute(
        select(License).where(License.license_key == license_key)
    )
    license = result.scalar_one_or_none()
    
    if not license:
        raise HTTPException(status_code=404, detail="License not found")
    
    # Add credits
    old_balance = license.credits_remaining
    license.credits_remaining += credits
    license.credits_purchased += credits
    license.updated_at = datetime.utcnow()
    
    await db.commit()
    
    return {
        "success": True,
        "license_key": license_key,
        "credits_added": credits,
        "old_balance": old_balance,
        "new_balance": license.credits_remaining
    }


@admin_router.get("/gaps")
async def list_skill_gaps(
    db: AsyncSession = Depends(get_db),
    limit: int = 200
):
    """
    Admin view of anonymous gap reports.
    Returns raw reports plus a frequency-ranked summary of search terms
    — use this to prioritize new skill development.
    """
    result = await db.execute(
        select(SkillGap).order_by(SkillGap.reported_at.desc()).limit(limit)
    )
    rows = result.scalars().all()

    # Build frequency map across all reported terms
    from collections import Counter
    term_counter: Counter = Counter()
    raw = []
    for row in rows:
        terms = row.search_terms.split()
        term_counter.update(terms)
        raw.append({
            "id": row.id,
            "search_terms": row.search_terms,
            "loader_version": row.loader_version,
            "reported_at": row.reported_at.isoformat(),
        })

    ranked = [
        {"term": term, "count": count}
        for term, count in term_counter.most_common(50)
    ]

    return {
        "total_reports": len(raw),
        "top_terms": ranked,
        "recent_reports": raw,
    }


@admin_router.post("/migrate/add-skill-gaps-table")
async def migrate_add_skill_gaps_table(db: AsyncSession = Depends(get_db)):
    """
    One-time migration to create the skill_gaps table.
    Safe to run multiple times.
    """
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS skill_gaps (
            id SERIAL PRIMARY KEY,
            search_terms TEXT NOT NULL,
            loader_version VARCHAR(20),
            reported_at TIMESTAMP DEFAULT NOW()
        )
    """))
    await db.commit()
    return {"status": "ok", "message": "skill_gaps table ready"}


@admin_router.post("/migrate/add-triggers-column")
async def migrate_add_triggers_column(db: AsyncSession = Depends(get_db)):
    """
    One-time migration to add triggers column to skills table.
    Safe to run multiple times - checks if column exists first.
    """
    from sqlalchemy import text
    
    try:
        # Check if column exists
        check_query = text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'skills' AND column_name = 'triggers'
        """)
        result = await db.execute(check_query)
        exists = result.scalar_one_or_none()
        
        if exists:
            return {"success": True, "message": "Column 'triggers' already exists"}
        
        # Add the column
        alter_query = text("""
            ALTER TABLE skills 
            ADD COLUMN triggers TEXT[] DEFAULT '{}'
        """)
        await db.execute(alter_query)
        await db.commit()
        
        return {"success": True, "message": "Column 'triggers' added successfully"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================
# Content Pages (version-controlled editable pages)
# ============================================

@app.get("/pages/{slug}")
async def get_page(slug: str, db: AsyncSession = Depends(get_db)):
    """Public endpoint: get current content of a page by slug."""
    result = await db.execute(select(ContentPage).where(ContentPage.slug == slug))
    page = result.scalar_one_or_none()
    if not page:
        raise HTTPException(status_code=404, detail=f"Page '{slug}' not found")
    return {
        "slug": page.slug,
        "title": page.title,
        "content": page.content,
        "current_version": page.current_version,
        "updated_at": page.updated_at.isoformat() if page.updated_at else None
    }

@admin_router.get("/pages")
async def list_pages(db: AsyncSession = Depends(get_db)):
    """Admin: list all content pages."""
    result = await db.execute(select(ContentPage).order_by(ContentPage.title))
    pages = result.scalars().all()
    return [
        {
            "slug": p.slug,
            "title": p.title,
            "current_version": p.current_version,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None
        }
        for p in pages
    ]

@admin_router.get("/pages/{slug}")
async def get_page_admin(slug: str, db: AsyncSession = Depends(get_db)):
    """Admin: get page with full content for editing."""
    result = await db.execute(select(ContentPage).where(ContentPage.slug == slug))
    page = result.scalar_one_or_none()
    if not page:
        raise HTTPException(status_code=404, detail=f"Page '{slug}' not found")
    return {
        "slug": page.slug,
        "title": page.title,
        "content": page.content,
        "current_version": page.current_version,
        "updated_at": page.updated_at.isoformat() if page.updated_at else None,
        "created_at": page.created_at.isoformat() if page.created_at else None
    }

@admin_router.put("/pages/{slug}")
async def save_page(slug: str, data: dict, db: AsyncSession = Depends(get_db)):
    """Admin: save page content. Creates a new version automatically."""
    from sqlalchemy import text
    
    result = await db.execute(select(ContentPage).where(ContentPage.slug == slug))
    page = result.scalar_one_or_none()
    
    content = data.get("content", "")
    title = data.get("title", "")
    changelog = data.get("changelog", "")
    
    if page:
        # Update existing page
        new_version = page.current_version + 1
        
        # Save current version to history before overwriting
        version_entry = ContentPageVersion(
            page_slug=slug,
            version=page.current_version,
            content=page.content,
            changelog=changelog or f"Version {page.current_version}",
            created_at=page.updated_at or page.created_at
        )
        db.add(version_entry)
        
        # Update the page
        page.content = content
        page.title = title or page.title
        page.current_version = new_version
        page.updated_at = datetime.utcnow()
    else:
        # Create new page
        page = ContentPage(
            slug=slug,
            title=title or slug,
            content=content,
            current_version=1,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(page)
    
    await db.commit()
    
    return {
        "success": True,
        "slug": slug,
        "version": page.current_version,
        "updated_at": page.updated_at.isoformat()
    }

@admin_router.get("/pages/{slug}/versions")
async def get_page_versions(slug: str, db: AsyncSession = Depends(get_db)):
    """Admin: list all versions of a page."""
    # Check page exists
    result = await db.execute(select(ContentPage).where(ContentPage.slug == slug))
    page = result.scalar_one_or_none()
    if not page:
        raise HTTPException(status_code=404, detail=f"Page '{slug}' not found")
    
    # Get version history
    result = await db.execute(
        select(ContentPageVersion)
        .where(ContentPageVersion.page_slug == slug)
        .order_by(ContentPageVersion.version.desc())
    )
    versions = result.scalars().all()
    
    # Include current version at the top
    all_versions = [
        {
            "version": page.current_version,
            "changelog": "Current version",
            "created_at": page.updated_at.isoformat() if page.updated_at else None,
            "is_current": True
        }
    ]
    
    for v in versions:
        all_versions.append({
            "version": v.version,
            "changelog": v.changelog,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "is_current": False
        })
    
    return all_versions

@admin_router.get("/pages/{slug}/versions/{version}")
async def get_page_version(slug: str, version: int, db: AsyncSession = Depends(get_db)):
    """Admin: get a specific version's content (for viewing/restoring)."""
    # Check if requesting current version
    result = await db.execute(select(ContentPage).where(ContentPage.slug == slug))
    page = result.scalar_one_or_none()
    if not page:
        raise HTTPException(status_code=404, detail=f"Page '{slug}' not found")
    
    if version == page.current_version:
        return {"version": version, "content": page.content, "is_current": True}
    
    # Get historical version
    result = await db.execute(
        select(ContentPageVersion)
        .where(ContentPageVersion.page_slug == slug, ContentPageVersion.version == version)
    )
    ver = result.scalar_one_or_none()
    if not ver:
        raise HTTPException(status_code=404, detail=f"Version {version} not found")
    
    return {"version": ver.version, "content": ver.content, "is_current": False}

@admin_router.post("/pages/{slug}/restore/{version}")
async def restore_page_version(slug: str, version: int, db: AsyncSession = Depends(get_db)):
    """Admin: restore a previous version (saves current as new version first)."""
    result = await db.execute(select(ContentPage).where(ContentPage.slug == slug))
    page = result.scalar_one_or_none()
    if not page:
        raise HTTPException(status_code=404, detail=f"Page '{slug}' not found")
    
    if version == page.current_version:
        return {"success": True, "message": "Already on this version"}
    
    # Get the version to restore
    result = await db.execute(
        select(ContentPageVersion)
        .where(ContentPageVersion.page_slug == slug, ContentPageVersion.version == version)
    )
    ver = result.scalar_one_or_none()
    if not ver:
        raise HTTPException(status_code=404, detail=f"Version {version} not found")
    
    # Save current to history
    version_entry = ContentPageVersion(
        page_slug=slug,
        version=page.current_version,
        content=page.content,
        changelog=f"Before restoring v{version}",
        created_at=page.updated_at or page.created_at
    )
    db.add(version_entry)
    
    # Restore
    new_version = page.current_version + 1
    page.content = ver.content
    page.current_version = new_version
    page.updated_at = datetime.utcnow()
    
    await db.commit()
    
    return {
        "success": True,
        "restored_from": version,
        "new_version": new_version
    }

@admin_router.post("/migrate/add-license-product-id")
async def migrate_add_license_product_id(db: AsyncSession = Depends(get_db)):
    """
    One-time migration: add product_id column to licenses table.
    Idempotent — safe to call multiple times.
    """
    from sqlalchemy import text
    await db.execute(text(
        "ALTER TABLE licenses ADD COLUMN IF NOT EXISTS product_id VARCHAR(50) DEFAULT 'law'"
    ))
    # Backfill from user product_id for existing rows
    await db.execute(text("""
        UPDATE licenses l
        SET product_id = COALESCE(u.product_id, 'law')
        FROM users u
        WHERE l.user_id = u.id AND (l.product_id IS NULL OR l.product_id = 'law')
    """))
    await db.commit()
    return {"success": True, "message": "licenses.product_id column added and backfilled"}


@admin_router.post("/migrate/add-abbreviations-table")
async def migrate_add_abbreviations_table(db: AsyncSession = Depends(get_db)):
    """
    One-time migration: create skill_abbreviations table + index.
    Idempotent — safe to call multiple times.
    """
    from sqlalchemy import text
    # Check if table already exists
    exists = await db.execute(text(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name='skill_abbreviations'"
    ))
    if exists.fetchone():
        return {"success": True, "message": "Table already exists"}
    # Create table
    await db.execute(text("""
        CREATE TABLE skill_abbreviations (
            id           SERIAL PRIMARY KEY,
            product_id   TEXT NOT NULL,
            abbreviation TEXT NOT NULL,
            expansion    TEXT NOT NULL,
            created_at   TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (product_id, abbreviation)
        )
    """))
    await db.execute(text(
        "CREATE INDEX idx_abbrev_product ON skill_abbreviations(product_id)"
    ))
    await db.commit()
    return {"success": True, "message": "Table and index created"}


@admin_router.post("/migrate/add-content-pages")
async def migrate_add_content_pages(db: AsyncSession = Depends(get_db)):
    """One-time migration to create content_pages and content_page_versions tables."""
    from sqlalchemy import text
    
    try:
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS content_pages (
                slug VARCHAR(100) PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                current_version INTEGER DEFAULT 1,
                content TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT NOW(),
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS content_page_versions (
                id SERIAL PRIMARY KEY,
                page_slug VARCHAR(100) REFERENCES content_pages(slug) ON DELETE CASCADE,
                version INTEGER NOT NULL,
                content TEXT NOT NULL,
                changelog TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        
        await db.commit()
        return {"success": True, "message": "Content pages tables created successfully"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================
# Admin File Management (GCS-backed)
# ============================================

WORKSPACE_BUCKET = os.getenv("WORKSPACE_BUCKET", "lawtasksai-workspace")

def _get_gcs_client():
    from google.cloud import storage
    return storage.Client()

@admin_router.get("/files/list")
async def list_files(prefix: str = ""):
    """List files in the workspace bucket under a prefix."""
    try:
        client = _get_gcs_client()
        bucket = client.bucket(WORKSPACE_BUCKET)
        blobs = bucket.list_blobs(prefix=prefix)
        files = []
        for blob in blobs:
            files.append({
                "path": blob.name,
                "size": blob.size,
                "updated": blob.updated.isoformat() if blob.updated else None,
            })
        return files
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@admin_router.get("/files/read")
async def read_file(path: str):
    """Read a file from the workspace bucket."""
    try:
        client = _get_gcs_client()
        bucket = client.bucket(WORKSPACE_BUCKET)
        blob = bucket.blob(path)
        if not blob.exists():
            raise HTTPException(status_code=404, detail="File not found")
        content = blob.download_as_text()
        return {"path": path, "content": content, "size": blob.size, "updated": blob.updated.isoformat() if blob.updated else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@admin_router.put("/files/write")
async def write_file(data: dict):
    """Write/update a file in the workspace bucket."""
    path = data.get("path", "")
    content = data.get("content", "")
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    try:
        client = _get_gcs_client()
        bucket = client.bucket(WORKSPACE_BUCKET)
        blob = bucket.blob(path)
        blob.upload_from_string(content, content_type="text/plain")
        return {"success": True, "path": path, "size": len(content.encode('utf-8'))}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# Template Management & Rendering
# ============================================

@admin_router.get("/templates")
async def list_templates():
    """List available output templates with metadata from manifest."""
    try:
        client = _get_gcs_client()
        bucket = client.bucket(WORKSPACE_BUCKET)
        manifest_blob = bucket.blob("templates/manifest.json")
        if manifest_blob.exists():
            import json as _json
            manifest = _json.loads(manifest_blob.download_as_text())
            return manifest
        # Fallback: list HTML files
        blobs = bucket.list_blobs(prefix="templates/")
        templates = []
        for blob in blobs:
            if blob.name.endswith('.html'):
                tid = blob.name.replace('templates/', '').replace('.html', '')
                templates.append({"id": tid, "name": tid.replace('-', ' ').title(), "file": f"{tid}.html"})
        return templates
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@admin_router.get("/templates/{template_id}")
async def get_template(template_id: str):
    """Get template HTML content."""
    try:
        client = _get_gcs_client()
        bucket = client.bucket(WORKSPACE_BUCKET)
        blob = bucket.blob(f"templates/{template_id}.html")
        if not blob.exists():
            raise HTTPException(status_code=404, detail="Template not found")
        content = blob.download_as_text()
        return {"id": template_id, "content": content, "size": blob.size}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@admin_router.put("/templates/{template_id}")
async def save_template(template_id: str, data: dict):
    """Save/update a template."""
    content = data.get("content", "")
    metadata = data.get("metadata", {})
    try:
        client = _get_gcs_client()
        bucket = client.bucket(WORKSPACE_BUCKET)
        # Save template HTML
        blob = bucket.blob(f"templates/{template_id}.html")
        blob.upload_from_string(content, content_type="text/html")
        # Update manifest if metadata provided
        if metadata:
            import json as _json
            manifest_blob = bucket.blob("templates/manifest.json")
            manifest = []
            if manifest_blob.exists():
                manifest = _json.loads(manifest_blob.download_as_text())
            # Update or add entry
            found = False
            for entry in manifest:
                if entry.get("id") == template_id:
                    entry.update(metadata)
                    found = True
                    break
            if not found:
                metadata["id"] = template_id
                metadata["file"] = f"{template_id}.html"
                manifest.append(metadata)
            manifest_blob.upload_from_string(_json.dumps(manifest, indent=2), content_type="application/json")
        return {"success": True, "id": template_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@admin_router.post("/templates/preview")
async def preview_template(data: dict):
    """Render a template with provided data. Returns HTML string."""
    template_id = data.get("template_id", "")
    title = data.get("title", "Untitled")
    subtitle = data.get("subtitle", "")
    eyebrow = data.get("eyebrow", "LawTasksAI")
    content_html = data.get("content", "<p>No content provided.</p>")
    nav_items = data.get("nav_items", "")

    try:
        client = _get_gcs_client()
        bucket = client.bucket(WORKSPACE_BUCKET)
        blob = bucket.blob(f"templates/{template_id}.html")
        if not blob.exists():
            raise HTTPException(status_code=404, detail="Template not found")
        template = blob.download_as_text()

        # Simple placeholder replacement
        rendered = template.replace("{{title}}", title)
        rendered = rendered.replace("{{subtitle}}", subtitle)
        rendered = rendered.replace("{{eyebrow}}", eyebrow)
        rendered = rendered.replace("{{content}}", content_html)
        rendered = rendered.replace("{{nav_items}}", nav_items)

        return {"html": rendered}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# Multi-tenant Migration Endpoint (one-time use)
# ============================================

@admin_router.post("/run-migration-001")
async def run_migration_001(db: AsyncSession = Depends(get_db)):
    """Run the multi-tenant migration 001. Safe to call multiple times (idempotent)."""
    results = []

    async def step(name: str, sql: str):
        try:
            await db.execute(text(sql))
            results.append(f"✅ {name}")
        except Exception as e:
            results.append(f"⚠️ {name}: {str(e)[:120]}")

    # Step 1: products table
    await step("Create products table", """
        CREATE TABLE IF NOT EXISTS products (
            id VARCHAR(50) PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            display_name VARCHAR(100),
            domain VARCHAR(100),
            frontend_url VARCHAR(200),
            primary_color VARCHAR(20) DEFAULT '#1a1a2e',
            accent_color VARCHAR(20) DEFAULT '#2563eb',
            background_color VARCHAR(20) DEFAULT '#fafbfc',
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)

    # Step 2: seed all 27 products
    products = [
        ("law", "LawTasksAI", "#1a1a2e", "#2563eb", "#fafbfc"),
        ("contractor", "ContractorTasksAI", "#1a1a1a", "#F97316", "#FAFAFA"),
        ("realtor", "RealtorTasksAI", "#1c2b3a", "#C8973A", "#FDF8F3"),
        ("mortgage", "MortgageTasksAI", "#1a2e1a", "#16A34A", "#F0FDF4"),
        ("insurance", "InsuranceTasksAI", "#1e293b", "#0EA5E9", "#F0F9FF"),
        ("hr", "HRTasksAI", "#2d1b4e", "#7C3AED", "#FAF5FF"),
        ("accounting", "AccountingTasksAI", "#14213d", "#1D4ED8", "#EFF6FF"),
        ("chiropractor", "ChiropractorTasksAI", "#1a3a3a", "#0D9488", "#F0FDFA"),
        ("vet", "VetTasksAI", "#1a3520", "#22C55E", "#F0FDF4"),
        ("dentist", "DentistTasksAI", "#1e3a5f", "#38BDF8", "#F0F9FF"),
        ("plumber", "PlumberTasksAI", "#1a2744", "#3B82F6", "#F8FAFF"),
        ("landlord", "LandlordTasksAI", "#2c1a0e", "#B45309", "#FFFBF5"),
        ("nutritionist", "NutritionistTasksAI", "#1a3320", "#65A30D", "#F7FEE7"),
        ("personaltrainer", "PersonalTrainerTasksAI", "#1a1a1a", "#EF4444", "#FFF5F5"),
        ("therapist", "TherapistTasksAI", "#2d2640", "#8B5CF6", "#F5F3FF"),
        ("eventplanner", "EventPlannerTasksAI", "#3b0764", "#D946EF", "#FDF4FF"),
        ("travelagent", "TravelAgentTasksAI", "#0f3460", "#F59E0B", "#FFFBEB"),
        ("funeral", "FuneralTasksAI", "#1a1a2a", "#6B7280", "#F9FAFB"),
        ("pastor", "PastorTasksAI", "#2d1b00", "#D97706", "#FFFBEB"),
        ("principal", "PrincipalTasksAI", "#1e3a5f", "#2563EB", "#EFF6FF"),
        ("farmer", "FarmerTasksAI", "#2d1f00", "#92400E", "#FEFCE8"),
        ("restaurant", "RestaurantTasksAI", "#1a0a00", "#DC2626", "#FFF5F5"),
        ("salon", "SalonTasksAI", "#2d0a2d", "#EC4899", "#FDF2F8"),
        ("mortician", "MorticiaryTasksAI", "#1c1c1c", "#9CA3AF", "#F3F4F6"),
        ("churchadmin", "ChurchAdminTasksAI", "#1e2d40", "#6366F1", "#EEF2FF"),
        ("militaryspouse", "MilitarySpouseTasksAI", "#1a2340", "#1E40AF", "#EFF6FF"),
        ("electrician", "ElectricianTasksAI", "#1a1a00", "#EAB308", "#FEFCE8"),
        ("teacher", "TeacherTasksAI", "#1a2744", "#2563EB", "#EFF6FF"),
        ("designer", "DesignerTasksAI", "#2d1a3a", "#C026D3", "#FDF4FF"),
    ]
    for p in products:
        await step(f"Seed product: {p[0]}", f"""
            INSERT INTO products (id, name, primary_color, accent_color, background_color)
            VALUES ('{p[0]}', '{p[1]}', '{p[2]}', '{p[3]}', '{p[4]}')
            ON CONFLICT (id) DO NOTHING
        """)

    # Step 3: add product_id to all tables
    for table in ["users", "skills", "categories", "usage_logs", "licenses", "credit_transactions"]:
        await step(f"Add product_id to {table}", f"""
            ALTER TABLE {table} ADD COLUMN IF NOT EXISTS product_id VARCHAR(50) DEFAULT 'law'
        """)

    # Step 4: product_credit_packs table
    await step("Create product_credit_packs table", """
        CREATE TABLE IF NOT EXISTS product_credit_packs (
            id SERIAL PRIMARY KEY,
            product_id VARCHAR(50) REFERENCES products(id),
            pack_key VARCHAR(50) NOT NULL,
            name VARCHAR(100) NOT NULL,
            credits INTEGER NOT NULL,
            price_cents INTEGER NOT NULL,
            UNIQUE(product_id, pack_key)
        )
    """)

    # Step 5: seed pricing tiers for all 27 products
    tiers = [
        ("tryit", "Try It", 2, 500),
        ("starter", "Starter", 15, 2900),
        ("pro", "Pro", 60, 9900),
        ("business", "Business", 150, 19900),
        ("power", "Power", 350, 34900),
        ("unlimited", "Unlimited", 800, 59900),
        ("enterprise", "Enterprise", 2000, 99900),
    ]
    # Include teacher and designer in pricing seed even if already in products list
    product_ids = [p[0] for p in products]
    for pid in product_ids:
        for key, name, credits, price in tiers:
            await step(f"Seed {pid}/{key}", f"""
                INSERT INTO product_credit_packs (product_id, pack_key, name, credits, price_cents)
                VALUES ('{pid}', '{key}', '{name}', {credits}, {price})
                ON CONFLICT (product_id, pack_key) DO NOTHING
            """)

    await db.commit()
    return {"migration": "001_add_multitenant", "steps": len(results), "results": results}


@admin_router.post("/provision-user")
async def admin_provision_user(
    email: str,
    product_id: str,
    credits: int,
    db: AsyncSession = Depends(get_db)
):
    """Manually provision a user+license for a product. Idempotent — safe to run twice."""
    # Find user by email (one user per email, product tracked on license/user)
    result = await db.execute(
        select(User).where(User.email == email.lower().strip())
    )
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            email=email.lower().strip(),
            password_hash=hash_password(secrets.token_hex(16)),
            credits_balance=credits,
            product_id=product_id,
        )
        db.add(user)
        await db.flush()
        license = License(
            license_key=generate_license_key(),
            user_id=user.id,
            type="credits",
            credits_purchased=credits,
            credits_remaining=credits,
        )
        db.add(license)
        await db.flush()
        action = "created"
    else:
        # Update product_id if being provisioned for a different product
        if user.product_id != product_id:
            user.product_id = product_id
        user.credits_balance += credits
        result2 = await db.execute(
            select(License).where(License.user_id == user.id, License.status == "active")
            .order_by(License.created_at.desc())
        )
        license = result2.scalar_one_or_none()
        if not license:
            license = License(
                license_key=generate_license_key(),
                user_id=user.id,
                type="credits",
                credits_purchased=credits,
                credits_remaining=credits,
            )
            db.add(license)
            await db.flush()
        else:
            license.credits_remaining += credits
            license.credits_purchased += credits
        action = "updated"

    await db.commit()
    return {
        "action": action,
        "email": email,
        "product_id": product_id,
        "license_key": license.license_key,
        "credits": license.credits_remaining,
        "download_url": f"https://api.taskvaultai.com/download/loader/{license.license_key}",
    }


@admin_router.post("/migrate/set-product-domains")
async def migrate_set_product_domains(db: AsyncSession = Depends(get_db)):
    """One-time migration: populate domain column for all 29 products."""
    domains = {
        "law":            "lawtasksai.com",
        "contractor":     "contractortasksai.com",
        "realtor":        "realtortasksai.com",
        "accounting":     "accountingtasksai.com",
        "chiropractor":   "chiropractortasksai.com",
        "churchadmin":    "churchadmintasksai.com",
        "dentist":        "dentisttasksai.com",
        "designer":       "designertasksai.com",
        "electrician":    "electriciantasksai.com",
        "eventplanner":   "eventplannertasksai.com",
        "farmer":         "farmertasksai.com",
        "funeral":        "funeraltasksai.com",
        "hr":             "hrtasksai.com",
        "insurance":      "insurancetasksai.com",
        "landlord":       "landlordtasksai.com",
        "militaryspouse": "militaryspousetasksai.com",
        "mortgage":       "mortgagetasksai.com",
        "mortician":      "mortuarytasksai.com",
        "nutritionist":   "nutritionisttasksai.com",
        "pastor":         "pastortasksai.com",
        "personaltrainer":"personaltrainertasksai.com",
        "plumber":        "plumbertasksai.com",
        "principal":      "principaltasksai.com",
        "restaurant":     "restauranttasksai.com",
        "salon":          "salontasksai.com",
        "teacher":        "teachertasksai.com",
        "therapist":      "therapisttasksai.com",
        "travelagent":    "travelagenttasksai.com",
        "vet":            "vettasksai.com",
    }
    results = []
    for pid, domain in domains.items():
        try:
            await db.execute(
                text("UPDATE products SET domain = :domain WHERE id = :pid"),
                {"domain": domain, "pid": pid}
            )
            results.append({"product_id": pid, "domain": domain, "status": "ok"})
        except Exception as e:
            results.append({"product_id": pid, "domain": domain, "status": f"error: {e}"})
    await db.commit()
    return {"updated": len([r for r in results if r["status"] == "ok"]), "results": results}


# Register admin router (all routes protected by X-Admin-Secret)
@admin_router.post("/migrate/add-product")
async def migrate_add_product(data: dict, db: AsyncSession = Depends(get_db)):
    """Insert or update a product record."""
    pid = data.get("id")
    if not pid:
        raise HTTPException(status_code=400, detail="id is required")
    await db.execute(text("""
        INSERT INTO products (id, name, primary_color, accent_color, background_color, domain, is_active)
        VALUES (:id, :name, :primary_color, :accent_color, :background_color, :domain, TRUE)
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            domain = EXCLUDED.domain,
            accent_color = EXCLUDED.accent_color,
            is_active = TRUE
    """), {
        "id": pid,
        "name": data.get("name", pid),
        "primary_color": data.get("primary_color", "#1a1a2e"),
        "accent_color": data.get("accent_color", "#2563eb"),
        "background_color": data.get("background_color", "#ffffff"),
        "domain": data.get("domain", f"{pid}tasksai.com"),
    })
    await db.commit()
    return {"success": True, "product_id": pid}


# ============================================
# Admin Routes: Abbreviations CRUD
# ============================================

@admin_router.get("/abbreviations")
async def admin_list_abbreviations(
    product_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Admin: list all abbreviations, optionally filtered by product_id."""
    q = "SELECT id, product_id, abbreviation, expansion, created_at FROM skill_abbreviations"
    params: dict = {}
    if product_id:
        q += " WHERE product_id = :pid"
        params["pid"] = product_id
    q += " ORDER BY product_id, abbreviation"
    result = await db.execute(text(q), params)
    rows = result.fetchall()
    return [
        {
            "id":           row.id,
            "product_id":   row.product_id,
            "abbreviation": row.abbreviation,
            "expansion":    row.expansion,
            "created_at":   row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


@admin_router.post("/abbreviations", status_code=201)
async def admin_create_abbreviation(
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    """Admin: create a new abbreviation. Body: {product_id, abbreviation, expansion}"""
    product_id   = data.get("product_id")
    abbreviation = (data.get("abbreviation") or "").lower().strip()
    expansion    = (data.get("expansion") or "").lower().strip()
    if not product_id or not abbreviation or not expansion:
        raise HTTPException(status_code=400, detail="product_id, abbreviation, and expansion are required")
    try:
        result = await db.execute(
            text("""
                INSERT INTO skill_abbreviations (product_id, abbreviation, expansion)
                VALUES (:pid, :abbr, :exp)
                ON CONFLICT (product_id, abbreviation) DO UPDATE SET expansion = EXCLUDED.expansion
                RETURNING id, product_id, abbreviation, expansion
            """),
            {"pid": product_id, "abbr": abbreviation, "exp": expansion},
        )
        row = result.fetchone()
        await db.commit()
        return {"success": True, "id": row.id, "product_id": row.product_id,
                "abbreviation": row.abbreviation, "expansion": row.expansion}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@admin_router.put("/abbreviations/{abbrev_id}")
async def admin_update_abbreviation(
    abbrev_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
):
    """Admin: update expansion for an abbreviation by its DB id."""
    expansion = (data.get("expansion") or "").lower().strip()
    if not expansion:
        raise HTTPException(status_code=400, detail="expansion is required")
    result = await db.execute(
        text("UPDATE skill_abbreviations SET expansion = :exp WHERE id = :id RETURNING id, product_id, abbreviation, expansion"),
        {"exp": expansion, "id": abbrev_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Abbreviation id {abbrev_id} not found")
    await db.commit()
    return {"success": True, "id": row.id, "product_id": row.product_id,
            "abbreviation": row.abbreviation, "expansion": row.expansion}


@admin_router.delete("/abbreviations/{abbrev_id}", status_code=204)
async def admin_delete_abbreviation(
    abbrev_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Admin: delete an abbreviation by its DB id."""
    result = await db.execute(
        text("DELETE FROM skill_abbreviations WHERE id = :id RETURNING id"),
        {"id": abbrev_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Abbreviation id {abbrev_id} not found")
    await db.commit()


@admin_router.post("/abbreviations/seed")
async def admin_seed_abbreviations(db: AsyncSession = Depends(get_db)):
    """
    Admin: re-run the abbreviation seed (migration 002 data).
    Safe to call multiple times -- uses ON CONFLICT DO NOTHING.
    Returns counts per vertical.
    """
    ABBREVIATIONS = {
        "law": {"mtc":"motion to compel","rogs":"interrogatories","rog":"interrogatory","rfa":"request for admission","rfas":"requests for admission","rfp":"request for production","rfps":"requests for production","tro":"temporary restraining order","pi":"personal injury","msj":"motion for summary judgment","msk":"motion to strike","sj":"summary judgment","jnov":"judgment notwithstanding verdict","mil":"motion in limine","sol":"statute of limitations","aff":"affidavit","decl":"declaration","depo":"deposition","deps":"depositions","frcp":"federal rules civil procedure","fre":"federal rules evidence","compl":"complaint","ans":"answer","roe":"rules of evidence","atty":"attorney"},
        "realtor": {"mls":"multiple listing service","cma":"comparative market analysis","dom":"days on market","arv":"after repair value","hoa":"homeowners association","coe":"close of escrow","emd":"earnest money deposit","piti":"principal interest taxes insurance","ltv":"loan to value","nar":"national association of realtors","bom":"back on market","uc":"under contract","fs":"for sale","fsbo":"for sale by owner","reo":"real estate owned"},
        "contractor": {"rfi":"request for information","sow":"scope of work","co":"change order","gc":"general contractor","ntp":"notice to proceed","pco":"potential change order","aia":"american institute of architects","lien":"mechanics lien","sub":"subcontractor","por":"purchase order request","cos":"certificate of substantial completion","punch":"punch list","g702":"payment application","g703":"schedule of values"},
        "farmer": {"fsa":"farm service agency","nrcs":"natural resources conservation service","crp":"conservation reserve program","arc":"agriculture risk coverage","plc":"price loss coverage","usda":"united states department of agriculture","eqip":"environmental quality incentives program","csa":"community supported agriculture","gmp":"good manufacturing practices","gap":"good agricultural practices"},
        "hr": {"pip":"performance improvement plan","pto":"paid time off","fmla":"family medical leave act","ada":"americans with disabilities act","eeoc":"equal employment opportunity commission","w2":"wage and tax statement","i9":"employment eligibility verification","cobra":"consolidated omnibus budget reconciliation act","osha":"occupational safety and health administration","erp":"employee relations policy"},
        "accounting": {"cogs":"cost of goods sold","ar":"accounts receivable","ap":"accounts payable","gaap":"generally accepted accounting principles","ytd":"year to date","mtd":"month to date","ebitda":"earnings before interest taxes depreciation amortization","cpa":"certified public accountant","sox":"sarbanes oxley"},
        "mortgage": {"ltv":"loan to value","dti":"debt to income","arm":"adjustable rate mortgage","apr":"annual percentage rate","pmi":"private mortgage insurance","hud":"housing and urban development","fnma":"fannie mae","fhlmc":"freddie mac","heloc":"home equity line of credit","gfe":"good faith estimate","cd":"closing disclosure","le":"loan estimate"},
        "insurance": {"doi":"department of insurance","gl":"general liability","wc":"workers compensation","coi":"certificate of insurance","dec":"declarations page","aob":"assignment of benefits","uwi":"underwriting information","clue":"comprehensive loss underwriting exchange","pip":"personal injury protection"},
        "therapist": {"dap":"data assessment plan","soap":"subjective objective assessment plan","hipaa":"health insurance portability and accountability act","phi":"protected health information","dx":"diagnosis","tx":"treatment","iop":"intensive outpatient program","php":"partial hospitalization program","cbt":"cognitive behavioral therapy","dbt":"dialectical behavior therapy","emdr":"eye movement desensitization reprocessing"},
        "chiropractor": {"soap":"subjective objective assessment plan","rom":"range of motion","pi":"personal injury","hipaa":"health insurance portability and accountability act","icd":"international classification of diseases","cpt":"current procedural terminology","eob":"explanation of benefits"},
        "dentist": {"hipaa":"health insurance portability and accountability act","cdt":"current dental terminology","perio":"periodontal","ortho":"orthodontic","endo":"endodontic","eob":"explanation of benefits","pano":"panoramic radiograph"},
        "teacher": {"iep":"individualized education program","504":"section 504 accommodation plan","ell":"english language learner","sped":"special education","pbis":"positive behavioral interventions and supports","mtss":"multi-tiered system of supports","rti":"response to intervention","ferpa":"family educational rights and privacy act","pd":"professional development","plc":"professional learning community"},
        "vet": {"soap":"subjective objective assessment plan","avma":"american veterinary medical association","rx":"prescription","dx":"diagnosis","tx":"treatment","hx":"history","pe":"physical examination"},
        "electrician": {"nec":"national electrical code","gfci":"ground fault circuit interrupter","afci":"arc fault circuit interrupter","rfi":"request for information","co":"change order","ntp":"notice to proceed"},
        "plumber": {"ipc":"international plumbing code","upc":"uniform plumbing code","rfi":"request for information","co":"change order","ntp":"notice to proceed","pex":"cross-linked polyethylene","abs":"acrylonitrile butadiene styrene"},
    }

    summary = {}
    for product_id, abbrevs in ABBREVIATIONS.items():
        inserted = 0
        for abbr, expansion in abbrevs.items():
            result = await db.execute(
                text("""
                    INSERT INTO skill_abbreviations (product_id, abbreviation, expansion)
                    VALUES (:pid, :abbr, :exp)
                    ON CONFLICT (product_id, abbreviation) DO NOTHING
                """),
                {"pid": product_id, "abbr": abbr.lower(), "exp": expansion.lower()},
            )
            inserted += result.rowcount
        summary[product_id] = inserted
    await db.commit()
    total = await db.execute(text("SELECT COUNT(*) FROM skill_abbreviations"))
    return {"success": True, "inserted_by_vertical": summary,
            "total_rows": total.scalar()}


app.include_router(admin_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

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
API_BASE_URL = os.getenv("API_BASE_URL", "https://api.lawtasksai.com")
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
    "trial": {"credits": 10, "price_cents": 2000, "one_time": False, "name": "Trial"},
    "starter": {"credits": 10, "price_cents": 2000, "one_time": False, "name": "Trial"},  # Deprecated, use 'trial'
    "essentials": {"credits": 50, "price_cents": 7500, "one_time": False, "name": "Essentials"},
    "accelerator": {"credits": 100, "price_cents": 12500, "one_time": False, "name": "Accelerator"},
    "efficient": {"credits": 250, "price_cents": 25000, "one_time": False, "name": "Efficient"},
    "unstoppable": {"credits": 625, "price_cents": 50000, "one_time": False, "name": "Unstoppable"},
    "apex": {"credits": 1500, "price_cents": 100000, "one_time": False, "name": "Apex"},
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

    # Create user
    user = User(
        email=user_data.email,
        password_hash=hash_password(user_data.password),
        name=user_data.name,
        firm_name=user_data.firm_name,
        credits_balance=50,  # Free trial credits
        product_id=resolved_product_id,
    )
    db.add(user)
    
    # Create trial license
    license = License(
        license_key=generate_license_key(),
        user_id=user.id,
        type="trial",
        valid_until=datetime.utcnow() + timedelta(days=14),
        credits_purchased=50,
        credits_remaining=50
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

        # Bar / attorney info from metadata
        bar_profile = {
            "attorney_name": meta.get('attorney_name', ''),
            "bar_jurisdiction": meta.get('bar_jurisdiction', ''),
            "bar_number": meta.get('bar_number', ''),
            "firm_name": meta.get('firm_name', ''),
            "attestation": meta.get('attestation', 'false') == 'true',
        }

        # Find or create user
        result = await db.execute(select(User).where(User.email == customer_email))
        user = result.scalar_one_or_none()
        
        if not user:
            # Create new user
            user = User(
                email=customer_email,
                name=bar_profile.get('attorney_name') or None,
                firm_name=bar_profile.get('firm_name') or None,
                password_hash=hash_password(secrets.token_hex(16)),  # Random password
                credits_balance=credits,
                profile=bar_profile,
            )
            db.add(user)
            await db.flush()
            
            # Create license
            license = License(
                license_key=generate_license_key(),
                user_id=user.id,
                type="credits",
                credits_purchased=credits,
                credits_remaining=credits
            )
            db.add(license)
        else:
            # Add credits to existing license
            result = await db.execute(
                select(License).where(
                    License.user_id == user.id,
                    License.status == "active"
                ).order_by(License.created_at.desc())
            )
            license = result.scalar_one_or_none()
            
            if license:
                license.credits_remaining += credits
                license.credits_purchased += credits
            else:
                # Create new license
                license = License(
                    license_key=generate_license_key(),
                    user_id=user.id,
                    type="credits",
                    credits_purchased=credits,
                    credits_remaining=credits
                )
                db.add(license)
            
            user.credits_balance += credits
            # Merge bar profile info (update if provided, don't overwrite with empty)
            existing_profile = user.profile or {}
            for k, v in bar_profile.items():
                if v:  # only overwrite if new value is non-empty
                    existing_profile[k] = v
            user.profile = existing_profile
            if bar_profile.get('attorney_name') and not user.name:
                user.name = bar_profile['attorney_name']
            if bar_profile.get('firm_name') and not user.firm_name:
                user.firm_name = bar_profile['firm_name']

        # Log transaction
        tx = CreditTransaction(
            user_id=user.id,
            license_id=license.id,
            type="purchase",
            amount=credits,
            balance_after=license.credits_remaining,
            reference_id=session['id'],
            description=f"Purchased {CREDIT_PACKS.get(pack_name, {}).get('name', pack_name)} via Stripe checkout"
        )
        db.add(tx)
        
        await db.commit()

        # Add buyer to Zoho Campaigns subscriber list
        await add_to_zoho_list(customer_email, user.name or "")
        
        return {"status": "success", "credits_added": credits}
    
    return {"status": "ignored", "event_type": event['type']}

@app.get("/checkout/session/{session_id}")
async def get_checkout_session(session_id: str, db: AsyncSession = Depends(get_db)):
    """Get checkout session details (for success page)."""
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        
        if session.payment_status != 'paid':
            raise HTTPException(status_code=400, detail="Payment not completed")
        
        customer_email = session.get('customer_details', {}).get('email')
        credits = int(session.get('metadata', {}).get('credits', 0))
        
        # Get user's license key
        result = await db.execute(select(User).where(User.email == customer_email))
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
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
            "total_credits": license.credits_remaining if license else credits
        }
    except stripe.error.InvalidRequestError:
        raise HTTPException(status_code=404, detail="Session not found")

@app.get("/download/loader/{license_key}")
async def download_loader(license_key: str, db: AsyncSession = Depends(get_db)):
    """
    Generate and download personalized loader skill with license key pre-configured.
    Product-aware: uses the product tied to the user's license.
    """
    # Validate license key
    result = await db.execute(
        select(License).where(
            License.license_key == license_key,
            License.status == "active"
        )
    )
    license = result.scalar_one_or_none()

    if not license:
        raise HTTPException(status_code=404, detail="Invalid license key")

    # Resolve product info from the user's product_id
    user_result = await db.execute(select(User).where(User.id == license.user_id))
    user = user_result.scalar_one_or_none()
    user_product_id = (user.product_id if user else None) or "law"

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

    zip_filename = f"{prod_domain.split('.')[0]}.zip"  # e.g. contractortasksai.zip

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
        zf.writestr('openclaw/config.json', json.dumps(config, indent=2))
        
        # Add SKILL.md
        zf.writestr('openclaw/SKILL.md', loader_skill_md)

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

(If you prefer to install manually, copy the openclaw folder
to ~/.openclaw/skills/{prod_domain.split('.')[0]}/ and restart OpenClaw.)

### Step 3: Start using!

Just ask for any task — your AI will know exactly what to do.

---

## Your License

- **License Key:** {license_key}
- **Credits:** {license.credits_remaining}
- Your license key is already configured — no setup needed!

## ⚠️ Confidentiality Notice

Some skills process document text on LawTasksAI servers. For highly sensitive 
or privileged materials, ensure you have appropriate client consent. Skills 
marked with 🔒 run entirely locally for maximum confidentiality.

## Need Help?

- Email: hello@lawtasksai.com
- Docs: https://lawtasksai.com/docs
'''
        zf.writestr('openclaw/README.md', readme)
    
        # =========================================
        # Add MCP Server for Claude Desktop/Cursor
        # =========================================
        
        mcp_server_py = '''"""
LawTasksAI MCP Server — Smart Router

Instead of exposing 200+ tools (token bloat), we expose 4 clean tools:
  1. lawtasks_search     — Find the right skill for a legal question
  2. lawtasks_execute    — Get a skill's expert framework (runs locally)
  3. lawtasks_balance    — Check credit balance
  4. lawtasks_categories — Browse skill categories

LawTasksAI.com never sees your prompts, your client files, or your client data. Skills run entirely on your machine — your documents stay local if using OpenClaw, or go to your LLM provider if using a cloud AI.
For full details see our Zero Data Retention & ABA Compliance guide: https://lawtasksai.com/zdr-aba-compliance-guide
"""

import os
import httpx
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, Prompt, PromptMessage, PromptArgument
from mcp.types import GetPromptResult

load_dotenv()

API_BASE = os.getenv("LAWTASKSAI_API_BASE", "https://api.lawtasksai.com")
LICENSE_KEY = os.getenv("LAWTASKSAI_LICENSE_KEY", "")

if not LICENSE_KEY:
    raise ValueError("LAWTASKSAI_LICENSE_KEY is required. Set it in .env file.")

AUTH_HEADERS = {
    "Authorization": f"Bearer {LICENSE_KEY}",
    "Content-Type": "application/json",
    "X-Client-Type": "mcp-server",
    "X-Client-Version": "1.4.0",
}

async def api_get(path):
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{API_BASE}{path}", headers=AUTH_HEADERS)
        resp.raise_for_status()
        return resp.json()

TOOLS = [
    Tool(
        name="lawtasks_search",
        description=(
            "Search for legal skills by keyword. "
            "After getting results, ALWAYS present the top matches to the user as a numbered list "
            "with the skill name and a brief description of each. "
            "Then ask the user: 'Which of these best fits your situation? (reply with a number, "
            "or describe your task differently for a new search)' "
            "NEVER call lawtasks_execute without explicit user confirmation of which skill to use."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Legal topic to search for (e.g. 'statute of limitations', 'motion to compel', 'NDA review')"
                }
            },
            "required": ["query"]
        },
    ),
    Tool(
        name="lawtasks_execute",
        description=(
            "Get a skill's expert analysis framework by skill ID. "
            "Returns the full prompt/schema for your AI to apply locally. Costs 1 credit. "
            "Only call this after the user has confirmed which skill they want from lawtasks_search results."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "skill_id": {
                    "type": "string",
                    "description": "Skill ID from lawtasks_search results, confirmed by the user"
                }
            },
            "required": ["skill_id"]
        },
    ),
    Tool(
        name="lawtasks_balance",
        description="Check your remaining LawTasksAI credit balance.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="lawtasks_categories",
        description="List all available skill categories to browse by practice area.",
        inputSchema={"type": "object", "properties": {}},
    ),
]

# ─────────────────────────────────────────────
# System prompt — injected at session start
# Instructs Claude to always confirm skill
# selection with the user before executing.
# ─────────────────────────────────────────────

SYSTEM_PROMPT_TEXT = """\
You have access to LawTasksAI, a library of 206+ expert legal task skills for attorneys.

## How to use LawTasksAI

**Always follow this 3-step flow:**

1. **Search** — Call lawtasks_search with the user's legal topic to find matching skills.

2. **Confirm** — Present the top results to the user as a numbered list, like this:
   > I found these skills that match your request:
   > 1. **Motion to Compel Discovery** — Drafts a motion compelling an opposing party to respond to discovery requests.
   > 2. **Discovery Deficiency Letter** — Drafts a letter identifying deficiencies in discovery responses.
   > 3. **Request for Production** — Prepares a formal request for production of documents.
   >
   > Which of these best fits your situation? (Reply with a number, or describe your task differently and I'll search again.)

3. **Execute** — Only after the user confirms their choice, call lawtasks_execute with that skill's ID.

## Important rules
- NEVER auto-execute a skill without explicit user confirmation.
- If the user's reply is ambiguous, ask a clarifying question rather than guessing.
- If no skills match, suggest the user try lawtasks_categories to browse by practice area.
- After executing a skill, apply its framework to the user's specific facts and produce the full output.
- Always remind the user that outputs require their professional review and judgment.
"""

PROMPTS = [
    Prompt(
        name="lawtasksai-workflow",
        description="LawTasksAI skill selection workflow — always confirm skill choice with user before executing.",
        arguments=[],
    )
]

_skills_cache = None

async def get_skills():
    global _skills_cache
    if _skills_cache is None:
        try:
            _skills_cache = await api_get("/v1/skills")
        except Exception:
            _skills_cache = []
    return _skills_cache

server = Server("lawtasksai")

@server.list_prompts()
async def list_prompts():
    return PROMPTS

@server.get_prompt()
async def get_prompt(name, arguments):
    if name == "lawtasksai-workflow":
        return GetPromptResult(
            description="LawTasksAI skill selection workflow",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(type="text", text=SYSTEM_PROMPT_TEXT)
                )
            ]
        )
    raise ValueError(f"Unknown prompt: {name}")

@server.list_tools()
async def list_tools():
    return TOOLS

@server.call_tool()
async def call_tool(name, arguments):
    try:
        if name == "lawtasks_search":
            skills = await get_skills()
            query = arguments.get("query", "").lower()
            STOP_WORDS = {"a","an","the","and","or","of","in","to","for","is","are",
                          "with","at","by","on","from","as","it","its","be","was","can"}
            words = [w for w in query.split() if w not in STOP_WORDS and len(w) > 2]
            scored = []
            for s in skills:
                text = (s.get("name", "") + " " + s.get("description", "")).lower()
                # Weight name matches higher than description matches
                name_text = s.get("name", "").lower()
                score = sum(3 if w in name_text else 1 for w in words if w in text)
                if score > 0:
                    scored.append((score, s))
            scored.sort(key=lambda x: -x[0])
            matches = [s for _, s in scored[:5]]
            if not matches:
                matches = skills[:5]

            lines = [f"**Top {len(matches)} matching skills for '{arguments.get('query', '')}':**\n"]
            for i, s in enumerate(matches, 1):
                desc = s.get("description", "")[:100]
                lines.append(f"{i}. **{s['name']}** (`{s['id']}`)\n   {desc}\n")
            lines.append("---")
            lines.append("*Present these options to the user and ask which one they'd like to use before calling lawtasks_execute.*")
            return [TextContent(type="text", text="\n".join(lines))]

        if name == "lawtasks_execute":
            skill_id = arguments.get("skill_id", "")
            if not skill_id:
                return [TextContent(type="text", text="Error: skill_id is required. Use lawtasks_search first to find a skill ID.")]
            result = await api_get(f"/v1/skills/{skill_id}/schema")
            schema = result.get("schema", "")
            instructions = result.get("instructions", "")
            credits_used = result.get("credits_used", 1)
            credits_remaining = result.get("credits_remaining", "?")
            text = f"# {result.get('skill_name', skill_id)}\n\n"
            text += f"{instructions}\n\n"
            text += f"## Expert Analysis Framework\n\n{schema}\n\n"
            text += f"---\n*Credits used: {credits_used} | Remaining: {credits_remaining}*"
            return [TextContent(type="text", text=text)]

        if name == "lawtasks_balance":
            b = await api_get("/v1/credits/balance")
            return [TextContent(type="text", text=f"**Credit Balance:** {b.get('credits_balance', '?')} credits")]

        if name == "lawtasks_categories":
            cats = await api_get("/v1/categories")
            if isinstance(cats, list):
                lines = ["**Skill Categories:**\n"]
                for c in cats:
                    lines.append(f"- **{c.get('name', '?')}** (`{c.get('id', '?')}`)")
                return [TextContent(type="text", text="\n".join(lines))]
            return [TextContent(type="text", text=str(cats))]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 402:
            return [TextContent(type="text", text="Insufficient credits. Purchase more at https://lawtasksai.com/#pricing")]
        if e.response.status_code == 401:
            return [TextContent(type="text", text="Invalid or expired license key. Check your .env file.")]
        return [TextContent(type="text", text=f"API error ({e.response.status_code}): {e.response.text[:200]}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]

if __name__ == "__main__":
    import asyncio

    async def main():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(main())
'''
        zf.writestr('mcp/server.py', mcp_server_py)
        
        mcp_requirements = '''mcp>=1.0.0
httpx>=0.27.0
python-dotenv>=1.0.0
'''
        zf.writestr('mcp/requirements.txt', mcp_requirements)
        
        mcp_env = f'''LAWTASKSAI_LICENSE_KEY={license_key}
LAWTASKSAI_API_BASE={API_BASE_URL}
'''
        zf.writestr('mcp/.env', mcp_env)
        
        mcp_readme = f'''# LawTasksAI MCP Server

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

### 3. Ask a legal question!
- "What\'s the statute of limitations for negligence in Colorado?"
- "Draft a motion to compel discovery in a breach of contract case."
- Attach a document: "Analyze this NDA for unfavorable terms"

## Your License Key
`{license_key}` (already configured — no need to enter it again)

## Supported MCP Clients
- Claude Desktop
- Cursor
- Windsurf
- Any app that supports the MCP stdio protocol

## Don\'t have Python?

Use LawTasksAI with OpenClaw instead —
no Python, no terminal, no config files required. See the openclaw folder
in this download, or visit https://lawtasksai.com/getting-started.html

## Support
hello@lawtasksai.com | https://lawtasksai.com
'''
        zf.writestr('mcp/README.md', mcp_readme)
        
        mcp_installer = '''#!/usr/bin/env python3
"""
LawTasksAI MCP Installer

Detects and configures LawTasksAI for all supported MCP clients:
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
                if line.startswith("LAWTASKSAI_LICENSE_KEY="):
                    key = line.split("=", 1)[1].strip()
                    if key and key != "YOUR_KEY_HERE":
                        return key
    print("\\n  Enter your LawTasksAI license key (starts with lt_):")
    key = input("   > ").strip()
    if not key:
        print("  No license key provided. Check your purchase confirmation email.")
        sys.exit(1)
    return key


def get_mcp_clients():
    """Return dict of {client_name: config_path} for all installed MCP clients."""
    system = platform.system()
    clients = {}

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
        if (Path.home() / "Applications" / "Claude.app").exists() or \\
           (Path("/Applications/Claude.app")).exists() or \\
           claude_path.parent.exists():
            clients["Claude Desktop"] = claude_path
        if (Path.home() / "Applications" / "Cursor.app").exists() or \\
           Path("/Applications/Cursor.app").exists():
            clients["Cursor"] = cursor_path
        if (Path.home() / "Applications" / "Windsurf.app").exists() or \\
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
        print("\\n  Installing required packages...")
        # Try normal install first, then fall back to --break-system-packages
        # (needed on modern Macs with Homebrew-managed Python)
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
    config = {}
    if config_path.exists():
        backup_path = config_path.with_suffix(
            f".backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        )
        shutil.copy2(config_path, backup_path)
        print(f"    Backed up existing config to: {backup_path.name}")
        with open(config_path) as f:
            try:
                config = json.load(f)
            except json.JSONDecodeError:
                print("    Existing config was invalid — starting fresh (backup saved).")
                config = {}
    if "mcpServers" not in config:
        config["mcpServers"] = {}
    config["mcpServers"]["lawtasksai"] = {
        "command": python_path,
        "args": [server_path],
        "env": {"LAWTASKSAI_LICENSE_KEY": license_key}
    }
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    print(f"    Config updated: {config_path}")


def main():
    print()
    print("  " + "=" * 50)
    print("  LawTasksAI MCP Installer")
    print("  " + "=" * 50)
    print()

    clients = get_mcp_clients()
    if not clients:
        print("  No supported MCP clients detected.")
        print("  Supported: Claude Desktop, Cursor, Windsurf")
        print()
        print("  If you have one installed, please configure manually:")
        print("  https://lawtasksai.com/getting-started.html")
        print()
        sys.exit(0)

    print(f"  Detected MCP client(s): {', '.join(clients.keys())}")
    print()
    print("  This installer will:")
    print("    1. Install required Python packages")
    print("    2. Configure LawTasksAI in each detected client")
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
        print(f"  Configuring {client_name}...")
        try:
            update_config(client_name, config_path, server_path, python_path, license_key)
            configured.append(client_name)
        except Exception as e:
            print(f"    Warning: could not configure {client_name}: {e}")

    print()
    print("  " + "=" * 50)
    print("  Installation complete!")
    print("  " + "=" * 50)
    print()
    if configured:
        print(f"  Configured: {', '.join(configured)}")
        print()
        print("  Next steps:")
        print("    1. Restart your MCP client(s)")
        print("    2. Ask a legal question, like:")
        print('       "What is the statute of limitations for')
        print('        breach of contract in Texas?"')
    print()
    print("  Support: hello@lawtasksai.com")
    print("  Website: https://lawtasksai.com")


if __name__ == "__main__":
    main()
'''
        zf.writestr('mcp/install.py', mcp_installer)
    
    zip_buffer.seek(0)
    
    return StreamingResponse(
        zip_buffer,
        media_type='application/zip',
        headers={
            'Content-Disposition': f'attachment; filename={zip_filename}'
        }
    )

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
        is_published=skill_data.get("is_published", False)
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
                      'is_published', 'description', 'name', 'category_id']
    
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


# Register admin router (all routes protected by X-Admin-Secret)
app.include_router(admin_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

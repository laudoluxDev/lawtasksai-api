"""
LawTasksAI API
FastAPI backend for skill delivery, licensing, and usage tracking.
"""

from fastapi import FastAPI, HTTPException, Depends, Header, BackgroundTasks, Request
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
from sqlalchemy import String, Integer, Boolean, DateTime, Text, ForeignKey, select, update, func, or_
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
API_BASE_URL = os.getenv("API_BASE_URL", "https://lawtasksai-api-10437713249.us-central1.run.app")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://lawtasksai.com")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Loader versioning
CURRENT_LOADER_VERSION = "1.1.0"
LOADER_UPDATE_URL = "https://lawtasksai.com/download"
LOADER_UPDATE_MESSAGE = None  # Set to a string when there's an important update

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
    # Profile for document generation (firm info, letterhead, etc.)
    profile: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict)

class Category(Base):
    __tablename__ = "categories"
    
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    display_order: Mapped[int] = mapped_column(Integer, default=0)

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

# ============================================
# Database Session
# ============================================

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_db():
    async with async_session() as session:
        yield session

# ============================================
# Pydantic Schemas
# ============================================

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: Optional[str] = None
    firm_name: Optional[str] = None

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
async def register(user_data: UserCreate, db: AsyncSession = Depends(get_db)):
    """Create a new user account."""
    # Check if email exists
    result = await db.execute(select(User).where(User.email == user_data.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Create user
    user = User(
        email=user_data.email,
        password_hash=hash_password(user_data.password),
        name=user_data.name,
        firm_name=user_data.firm_name,
        credits_balance=50  # Free trial credits
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
        license_key=license.license_key
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
    db: AsyncSession = Depends(get_db)
):
    """List all available skills."""
    query = select(Skill).where(Skill.is_published == True)
    if category:
        query = query.where(Skill.category_id == category)
    
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
async def get_skill_triggers(db: AsyncSession = Depends(get_db)):
    """
    Get trigger phrases for local skill matching.
    This enables privacy-preserving skill discovery without sending queries to the server.
    Reads from database - skills with non-empty triggers arrays.
    """
    result = await db.execute(
        select(Skill.id, Skill.triggers).where(
            Skill.is_published == True,
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


@app.post("/skills/{skill_id}/execute", response_model=SkillExecuteResponseWithDocs)
@app.post("/v1/skills/{skill_id}/execute", response_model=SkillExecuteResponseWithDocs)
async def execute_skill(
    skill_id: str,
    request: SkillExecuteRequest,
    license: License = Depends(get_current_license),
    db: AsyncSession = Depends(get_db),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    x_loader_version: Optional[str] = Header(None, alias="X-Loader-Version"),
    x_skip_document: Optional[bool] = Header(False, alias="X-Skip-Document")
):
    """
    Execute a skill - validates license, runs AI server-side, returns RESULTS only.
    The skill prompt/logic never leaves the server.
    
    Accepts X-Loader-Version header to provide update hints.
    Accepts X-Skip-Document header to skip document generation.
    
    If skill requires profile fields for document generation and they're missing,
    returns needs_profile list (execution still happens, document not generated).
    """
    # Validate Anthropic client
    if not anthropic_client:
        raise HTTPException(status_code=503, detail="AI service not configured")
    
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
    
    # Get user profile for document generation
    user_result = await db.execute(select(User).where(User.id == license.user_id))
    user = user_result.scalar_one_or_none()
    user_profile = user.profile if user else {}
    
    # Check if profile is complete for this skill's document generation
    missing_profile_fields = check_profile_requirements(skill_id, user_profile)
    
    # Determine version to use
    requested_version = request.version if request else None
    
    if requested_version:
        version_query = select(SkillVersion).where(
            SkillVersion.skill_id == skill_id,
            SkillVersion.version == requested_version
        )
    else:
        version_query = select(SkillVersion).where(
            SkillVersion.skill_id == skill_id,
            SkillVersion.version == skill.current_version
        )
    
    result = await db.execute(version_query)
    skill_version = result.scalar_one_or_none()
    
    if not skill_version:
        raise HTTPException(status_code=404, detail="Skill version not found")
    
    # =========================================
    # SERVER-SIDE AI EXECUTION
    # The skill content is used as system prompt
    # User's query is the user message
    # Results returned, NOT the prompt
    # =========================================
    
    try:
        # Build context from request if provided
        context_str = ""
        if request.context:
            context_str = f"\n\nAdditional context:\n{json.dumps(request.context, indent=2)}"
        
        # Call Anthropic Claude
        message = anthropic_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=skill_version.content,  # Skill prompt as system message (NEVER returned to user)
            messages=[
                {
                    "role": "user",
                    "content": f"{request.query}{context_str}"
                }
            ]
        )
        
        # Extract result
        ai_result = message.content[0].text
        
    except anthropic.APIError as e:
        raise HTTPException(status_code=502, detail=f"AI service error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Execution error: {str(e)}")
    
    # Deduct credits and increment usage
    license.credits_remaining -= skill.credits_per_use
    license.usage_count += 1
    
    # Log usage (success) - store result for document regeneration
    usage_log = UsageLog(
        license_id=license.id,
        user_id=license.user_id,
        skill_id=skill_id,
        skill_version=skill_version.version,
        credits_used=skill.credits_per_use,
        success=True,
        result_text=ai_result  # Store for free document regeneration
    )
    db.add(usage_log)
    
    await db.commit()
    
    # Check if loader update is available
    loader_meta = check_loader_update(x_loader_version)
    
    # Generate document if applicable and not skipped
    documents = None
    if not x_skip_document and skill_id in DOCUMENT_SKILLS and not missing_profile_fields:
        doc = generate_document(skill_id, ai_result, user_profile)
        if doc:
            documents = [doc]
    
    return SkillExecuteResponseWithDocs(
        skill_id=skill_id,
        version=skill_version.version,
        result=ai_result,  # Return RESULTS, not the prompt!
        credits_remaining=license.credits_remaining,
        credits_used=skill.credits_per_use,
        documents=documents,
        needs_profile=missing_profile_fields if missing_profile_fields else None,
        meta=loader_meta  # Include update hints if available
    )


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
    
    For server-side skills, use /execute instead.
    """
    # Get skill
    result = await db.execute(select(Skill).where(Skill.id == skill_id, Skill.is_published == True))
    skill = result.scalar_one_or_none()
    
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    
    # Verify this is a local execution skill
    if skill.execution_type != 'local':
        raise HTTPException(
            status_code=400, 
            detail=f"Skill '{skill_id}' requires server-side execution. Use /execute endpoint instead."
        )
    
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
        result_text="[LOCAL EXECUTION - Schema delivered]"
    )
    db.add(usage_log)
    await db.commit()
    
    # Check for loader updates
    loader_meta = check_loader_update(x_loader_version)
    
    # Build instructions for local execution
    instructions = """
## Local Execution Instructions

This skill runs LOCALLY on your machine. Your documents never leave your computer.

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

@app.post("/usage/{usage_id}/document", response_model=RegenerateDocumentResponse)
@app.post("/v1/usage/{usage_id}/document", response_model=RegenerateDocumentResponse)
async def regenerate_document(
    usage_id: int,
    license: License = Depends(get_current_license),
    db: AsyncSession = Depends(get_db)
):
    """
    Regenerate a document from a previous skill execution.
    FREE - no credits charged. Uses stored result from usage log.
    
    Use this when:
    - User set up profile AFTER running a skill
    - User wants to re-download a document
    - Document generation failed initially but profile is now complete
    """
    # Get usage log
    result = await db.execute(
        select(UsageLog).where(
            UsageLog.id == usage_id,
            UsageLog.license_id == license.id  # Security: only own usage
        )
    )
    usage_log = result.scalar_one_or_none()
    
    if not usage_log:
        raise HTTPException(status_code=404, detail="Usage record not found")
    
    if not usage_log.result_text:
        raise HTTPException(status_code=400, detail="No result stored for this execution")
    
    if usage_log.skill_id not in DOCUMENT_SKILLS:
        raise HTTPException(status_code=400, detail="This skill does not generate documents")
    
    # Get user profile
    user_result = await db.execute(select(User).where(User.id == license.user_id))
    user = user_result.scalar_one_or_none()
    user_profile = user.profile if user else {}
    
    # Check profile requirements
    missing = check_profile_requirements(usage_log.skill_id, user_profile)
    if missing:
        raise HTTPException(
            status_code=400, 
            detail=f"Profile incomplete. Missing fields: {', '.join(missing)}"
        )
    
    # Generate document from stored result
    doc = generate_document(usage_log.skill_id, usage_log.result_text, user_profile)
    
    if not doc:
        raise HTTPException(status_code=500, detail="Document generation failed")
    
    return RegenerateDocumentResponse(
        usage_id=usage_id,
        skill_id=usage_log.skill_id,
        document=doc,
        message="Document regenerated from previous result (no credits charged)"
    )


@app.get("/usage/recent")
@app.get("/v1/usage/recent")
async def get_recent_usage_for_documents(
    limit: int = 10,
    license: License = Depends(get_current_license),
    db: AsyncSession = Depends(get_db)
):
    """
    Get recent usage that can have documents regenerated.
    Only returns executions for document-generating skills that have stored results.
    """
    document_skill_ids = list(DOCUMENT_SKILLS.keys())
    
    result = await db.execute(
        select(UsageLog, Skill.name)
        .join(Skill, UsageLog.skill_id == Skill.id)
        .where(
            UsageLog.license_id == license.id,
            UsageLog.success == True,
            UsageLog.result_text.isnot(None),
            UsageLog.skill_id.in_(document_skill_ids)
        )
        .order_by(UsageLog.executed_at.desc())
        .limit(limit)
    )
    
    return [
        {
            "usage_id": log.id,
            "skill_id": log.skill_id,
            "skill_name": skill_name,
            "executed_at": log.executed_at.isoformat(),
            "document_format": DOCUMENT_SKILLS[log.skill_id]["format"],
            "can_regenerate": True
        }
        for log, skill_name in result.all()
    ]

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

# ============================================
# Routes: Checkout & Purchase
# ============================================

LOADER_SKILL_MD = '''# LawTasksAI Skills

Universal legal skill loader — access 200 AI-powered legal automation skills.

## License Resolution (CRITICAL - Do This First!)

Before making ANY API calls, you must resolve the license key. Check in this order:

### Step 1: Check persistent credentials file
```bash
cat ~/.lawtasksai/credentials.json
```

If this file exists and contains a valid `license_key`, use it and skip to "Confirmation Flow".

### Step 2: Fall back to skill config
If `~/.lawtasksai/credentials.json` doesn't exist, check the skill's config.json:
- Look for `license_key` in the skill's config.json
- If found and NOT "YOUR_LICENSE_KEY_HERE", use it

### Step 3: No license found — prompt user
If no valid license key is found, ask the user:

> **LawTasksAI Setup Required**
> 
> I don't have a license key configured. Please provide one of the following:
> 1. Your license key (starts with `lt_`)
> 2. The email you used to purchase LawTasksAI (I'll recover your license)
> 3. Or visit **lawtasksai.com** to purchase credits
>
> Which would you like to do?

**If user provides a license key (starts with `lt_`):**
- Validate it by calling the balance endpoint
- If valid, save it (see Step 4)

**If user provides an email:**
```
POST https://lawtasksai-api-10437713249.us-central1.run.app/auth/recover-license
Content-Type: application/json

{"email": "[user's email]"}
```
Response contains `license_key` — save it (see Step 4)

### Step 4: Save credentials (IMPORTANT!)
Once you have a valid license key, **always save it** to the persistent location:

```bash
mkdir -p ~/.lawtasksai
cat > ~/.lawtasksai/credentials.json << 'EOF'
{
  "license_key": "[THE_LICENSE_KEY]",
  "api_base_url": "https://lawtasksai-api-10437713249.us-central1.run.app"
}
EOF
```

This ensures the license survives OpenClaw restarts and skill reinstalls.

---

## IMPORTANT: User Confirmation Required

**Before EVERY LawTasksAI skill execution, you MUST ask the user for confirmation.**

This is a paid service. Never execute a skill without explicit user approval.

## Confirmation Flow

When the user asks for something that LawTasksAI can help with:

### Step 1: Check credit balance
```
GET {api_base_url}/credits/balance
Authorization: Bearer {license_key}
```

### Step 2: Find matching skills
```
GET {api_base_url}/skills
```
Search the skills list for relevant matches based on the user's request.

### Step 3: Offer skill options
If multiple skills could help, present choices:

> I found these **LawTasksAI skills** that could help:
>
> 1. **SOL Calculator** (1 credit) — Calculate statute of limitations
> 2. **Deadline Calculator** (1 credit) — Calculate filing deadlines  
> 3. **Personal Injury Assistant** (5 credits) — Comprehensive PI case analysis
>
> You have **48 credits** remaining.
> Which would you like to use? (1, 2, 3, or none)

If only one skill matches well, proceed to Step 4.

### Step 4: Ask for confirmation
Once user selects a skill (or if only one match), present:

> I can help with this using **LawTasksAI [Skill Name]** (**[cost] credits**).
> You have **[balance] credits** remaining.
>
> This will send your query to LawTasksAI servers for processing.
> Proceed? (yes/no)

**Important:** Show the ACTUAL cost from `credits_per_use`, not always "1 credit".
- Simple lookups: 1 credit
- Document analysis: 5-30 credits  
- Complex research: 10-50 credits

### Step 5: Check if user can afford it
If `credits_per_use > credits_remaining`:
> This skill costs **30 credits** but you only have **18 credits**.
> Would you like to purchase more credits at lawtasksai.com?

### Step 6: Wait for explicit approval
- If user says **yes/proceed/ok/sure**: Execute the skill
- If user says **no/cancel/skip**: Do NOT execute, offer alternatives
- If unclear: Ask again for clarification

### Step 7: Execute and report
After execution, always show:
> **LawTasksAI Result:**
> [The result from the API]
>
> *— [credits_used] credit(s) used, [credits_remaining] remaining*

Use the values from the API response (`credits_used` and `credits_remaining`).

## Example Interactions

### Example 1: First-time setup

**User:** "What's the statute of limitations for personal injury in Texas?"

**You:** [Check ~/.lawtasksai/credentials.json — not found]
[Check config.json — has placeholder]

**LawTasksAI Setup Required**

I don't have a license key configured. Please provide one of the following:
1. Your license key (starts with `lt_`)
2. The email you used to purchase LawTasksAI (I'll recover your license)
3. Or visit **lawtasksai.com** to purchase credits

Which would you like to do?

**User:** "my email is kent@example.com"

**You:** [Call /auth/recover-license with email]
[Save to ~/.lawtasksai/credentials.json]

Found your license! You have **47 credits** remaining. I've saved your credentials so you won't need to do this again.

Now, I can help with your question using **LawTasksAI SOL Alert System** (**1 credit**).
Proceed? (yes/no)

### Example 2: Normal flow (credentials exist)

**User:** "What's the statute of limitations for personal injury in Texas?"

**You:** [Read ~/.lawtasksai/credentials.json — found license key]
[Check balance — 48 credits]

I can help with this using **LawTasksAI SOL Alert System** (**1 credit**).
You have **48 credits** remaining.

This will send your query to LawTasksAI servers for processing.
Proceed? (yes/no)

**User:** "Yes"

**You:** [Execute API call, then display:]

**LawTasksAI Result:**

Under Texas Civil Practice & Remedies Code § 16.003, the statute of limitations for personal injury claims is **2 years** from the date of injury.

*— 1 credit used, 47 remaining*

**Want case law interpreting this statute?** (5 credits)

---

## Upsell Flow: Statutory → Case Law

After returning a statutory/rule-based answer (SOL, deadlines, court rules), offer case law research:

> **Want case law interpreting this statute?** (5 credits)

If user says **yes**:
```
POST {api_base_url}/skills/case-law-research/execute
Authorization: Bearer {license_key}
Content-Type: application/json

{
  "query": "[original question + statute reference]",
  "context": {"statute": "[the statute from the original answer]"}
}
```

---

## If User Declines

When the user declines a LawTasksAI skill, **answer using general knowledge**:

**User:** "No"

**You:** No problem. From my general knowledge:

[Answer the question]

*This is general knowledge, not verified legal research.*
**Upgrade this answer with LawTasksAI?** (1 credit, 48 available)

---

## API Endpoints

**Base URL:** `https://lawtasksai-api-10437713249.us-central1.run.app`

### Execute a Skill
```
POST {api_base_url}/skills/{skill_id}/execute
Authorization: Bearer {license_key}
Content-Type: application/json

{
  "query": "[user's question or request]",
  "context": { ... optional additional context ... }
}
```

### Check Balance
```
GET {api_base_url}/credits/balance
Authorization: Bearer {license_key}
```

### List Available Skills
```
GET {api_base_url}/skills
```

### Recover License (for setup)
```
POST {api_base_url}/auth/recover-license
Content-Type: application/json

{"email": "[user's email]"}
```

## Skill Categories

- **Contract & Transactional** — Clause analysis, lease review, NDA checks
- **Litigation & Discovery** — Deadline calculators, deposition summaries, timeline builders
- **Case Management** — Conflict checks, SOL tracking, intake triage
- **Research** — Precedent finding, statute updates, judge research
- **Billing & Finance** — Invoice auditing, collections, LEDES formatting
- **Compliance & Ethics** — Ethical walls, trust accounting, bar rules
- **Court & Government** — E-filing, fee calculations, records requests
- And more... (200 total skills)

## Important: Data Privacy

**⚠️ CONFIDENTIALITY NOTICE:**
- Queries are sent to LawTasksAI servers for AI processing
- Do NOT include privileged attorney-client communications without client consent
- LawTasksAI does not store query content after processing

## Support

- Email: hello@lawtasksai.com
- Docs: https://lawtasksai.com/docs

---
*LawTasksAI — Automate the busywork. Focus on the law.*
'''

@app.post("/checkout/create-session")
async def create_checkout_session(
    request: PurchaseCreditsRequest,
    db: AsyncSession = Depends(get_db)
):
    """Create a Stripe checkout session for credit purchase."""
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
    
    try:
        # Create Stripe checkout session
        checkout_kwargs = {
            'payment_method_types': ['card'],
            'line_items': [{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f'LawTasksAI {pack["name"]}',
                        'description': f'{pack["credits"]} tasks for legal AI automation',
                    },
                    'unit_amount': pack['price_cents'],
                },
                'quantity': 1,
            }],
            'mode': 'payment',
            'success_url': f'{FRONTEND_URL}/success?session_id={{CHECKOUT_SESSION_ID}}',
            'cancel_url': f'{FRONTEND_URL}/#pricing',
            'metadata': {
                'pack': request.pack,
                'credits': str(pack['credits']),
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
        credits = int(session.get('metadata', {}).get('credits', 0))
        pack_name = session.get('metadata', {}).get('pack', 'unknown')
        if credits == 0:
            return {"status": "skipped", "reason": "no credits"}
        
        # Find or create user
        result = await db.execute(select(User).where(User.email == customer_email))
        user = result.scalar_one_or_none()
        
        if not user:
            # Create new user
            user = User(
                email=customer_email,
                password_hash=hash_password(secrets.token_hex(16)),  # Random password
                credits_balance=credits
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
    
    # Create config.json with license key
    config = {
        "license_key": license_key,
        "api_base_url": API_BASE_URL,
        "version_policy": "latest",
        "cache_skills": False,
        "offline_mode": False
    }
    
    # Create zip file in memory
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Add config.json
        zf.writestr('lawtasksai-skills/config.json', json.dumps(config, indent=2))
        
        # Add SKILL.md
        zf.writestr('lawtasksai-skills/SKILL.md', LOADER_SKILL_MD)
        
        # Add README
        readme = f'''# LawTasksAI Skills

Your personalized legal AI skills are ready to use!

## Installation (Step by Step)

### Step 1: Extract the ZIP file

Double-click the downloaded `lawtasksai-skills.zip` to extract it.
You should see a folder called `lawtasksai-skills` containing:
- config.json (your license is already configured!)
- SKILL.md
- README.md (this file)

### Step 2: Copy to OpenClaw skills folder

Copy the entire `lawtasksai-skills` folder to your OpenClaw skills directory:

**macOS:**
```
/Users/YOUR_USERNAME/.openclaw/skills/
```
Example: /Users/john/.openclaw/skills/lawtasksai-skills/

**Linux:**
```
/home/YOUR_USERNAME/.openclaw/skills/
```
Example: /home/john/.openclaw/skills/lawtasksai-skills/

**Windows:**
```
C:\\Users\\YOUR_USERNAME\\.openclaw\\skills\\
```
Example: C:\\Users\\John\\.openclaw\\skills\\lawtasksai-skills\\

### Step 3: Restart OpenClaw

Restart OpenClaw or run `/reload` to load the new skill.

### Step 4: Start using!

Just ask for any legal task:
- "Calculate the statute of limitations for a personal injury case in Colorado"
- "What are the deadlines for responding to a federal complaint?"
- "List available billing skills"

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
        zf.writestr('lawtasksai-skills/README.md', readme)
    
        # =========================================
        # Add MCP Server for Claude Desktop/Cursor
        # =========================================
        
        mcp_server_py = '''"""
LawTasksAI MCP Server — Smart Router

Instead of exposing 200+ tools (token bloat), we expose 4 clean tools:
  1. lawtasks_search   — Find the right skill for a question
  2. lawtasks_execute   — Run any skill by ID
  3. lawtasks_balance   — Check credit balance
  4. lawtasks_categories — Browse skill categories

Skill prompts never leave the LawTasksAI server.
"""

import os
import httpx
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import run_server
from mcp.types import Tool, TextContent

load_dotenv()

API_BASE = os.getenv("LAWTASKSAI_API_BASE", "https://lawtasksai-api-10437713249.us-central1.run.app")
LICENSE_KEY = os.getenv("LAWTASKSAI_LICENSE_KEY", "")

if not LICENSE_KEY:
    raise ValueError("LAWTASKSAI_LICENSE_KEY is required. Set it in .env file.")

AUTH_HEADERS = {
    "Authorization": f"Bearer {LICENSE_KEY}",
    "Content-Type": "application/json",
    "X-Client-Type": "mcp-server",
    "X-Client-Version": "1.2.0",
}

async def api_get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{API_BASE}{path}", headers=AUTH_HEADERS)
        resp.raise_for_status()
        return resp.json()

async def api_post(path: str, data: dict) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{API_BASE}{path}", headers=AUTH_HEADERS, json=data)
        resp.raise_for_status()
        return resp.json()

TOOLS = [
    Tool(name="lawtasks_search", description="Search for legal skills. Use FIRST to find the right skill.", 
         inputSchema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}),
    Tool(name="lawtasks_execute", description="Execute a legal skill by ID.",
         inputSchema={"type": "object", "properties": {"skill_id": {"type": "string"}, "query": {"type": "string"}}, "required": ["skill_id", "query"]}),
    Tool(name="lawtasks_balance", description="Check credit balance.", inputSchema={"type": "object", "properties": {}}),
    Tool(name="lawtasks_categories", description="List skill categories.", inputSchema={"type": "object", "properties": {}}),
]

_skills_cache = None

async def get_skills():
    global _skills_cache
    if _skills_cache is None:
        try: _skills_cache = await api_get("/v1/skills")
        except: _skills_cache = []
    return _skills_cache

server = Server("lawtasksai")

@server.list_tools()
async def list_tools(): return TOOLS

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        if name == "lawtasks_search":
            skills = await get_skills()
            query = arguments.get("query", "").lower()
            matches = [s for s in skills if query in s.get("name","").lower() or query in s.get("description","").lower()][:10]
            if not matches: matches = skills[:10]
            lines = ["**Matching skills:**\\n"]
            for s in matches:
                mode = "🔒 LOCAL" if s.get("execution_type") == "local" else "☁️ SERVER"
                lines.append(f"- **{s['name']}** [{mode}] (`{s['id']}`) — {s.get('credits_per_use',1)} credits")
            return [TextContent(type="text", text="\\n".join(lines))]
        
        if name == "lawtasks_execute":
            skill_id, query = arguments.get("skill_id",""), arguments.get("query","")
            skills = await get_skills()
            skill = next((s for s in skills if s["id"] == skill_id), None)
            if skill and skill.get("execution_type") == "local":
                result = await api_get(f"/v1/skills/{skill_id}/schema")
                return [TextContent(type="text", text=f"# 🔒 Local Execution\\n\\n{result.get('instructions','')}\\n\\n## Schema\\n\\n{result.get('schema','')}\\n\\n*Credits: {result.get('credits_used')} used, {result.get('credits_remaining')} remaining*")]
            result = await api_post(f"/v1/skills/{skill_id}/execute", {"query": query})
            return [TextContent(type="text", text=f"{result.get('result','')}\\n\\n*Credits: {result.get('credits_used')} used, {result.get('credits_remaining')} remaining*")]
        
        if name == "lawtasks_balance":
            b = await api_get("/v1/credits/balance")
            return [TextContent(type="text", text=f"**Balance:** {b['credits_balance']} credits")]
        
        if name == "lawtasks_categories":
            cats = await api_get("/v1/categories")
            return [TextContent(type="text", text="\\n".join([f"- {c['name']} ({c['id']})" for c in cats]))]
        
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]

if __name__ == "__main__":
    import asyncio
    asyncio.run(run_server(server))
'''
        zf.writestr('lawtasksai-mcp/server.py', mcp_server_py)
        
        mcp_requirements = '''mcp>=1.0.0
httpx>=0.27.0
python-dotenv>=1.0.0
'''
        zf.writestr('lawtasksai-mcp/requirements.txt', mcp_requirements)
        
        mcp_env = f'''LAWTASKSAI_LICENSE_KEY={license_key}
LAWTASKSAI_API_BASE=https://lawtasksai-api-10437713249.us-central1.run.app
'''
        zf.writestr('lawtasksai-mcp/.env', mcp_env)
        
        mcp_readme = f'''# LawTasksAI MCP Server

For Claude Desktop, Cursor, and other MCP-compatible AI clients.

## Quick Setup (Claude Desktop)

### 1. Install dependencies
```bash
cd lawtasksai-mcp
pip install -r requirements.txt
```

### 2. Configure Claude Desktop

**Mac:** Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** Edit `%APPDATA%\\Claude\\claude_desktop_config.json`

Add:
```json
{{
  "mcpServers": {{
    "lawtasksai": {{
      "command": "python",
      "args": ["/FULL/PATH/TO/lawtasksai-mcp/server.py"],
      "env": {{
        "LAWTASKSAI_LICENSE_KEY": "{license_key}"
      }}
    }}
  }}
}}
```

### 3. Restart Claude Desktop

### 4. Test it!
- "What's the statute of limitations for negligence in Colorado?"
- Attach a document: "Analyze this NDA for unfavorable terms"

## Your License Key
`{license_key}`

## Support
hello@lawtasksai.com
'''
        zf.writestr('lawtasksai-mcp/README.md', mcp_readme)
    
    zip_buffer.seek(0)
    
    return StreamingResponse(
        zip_buffer,
        media_type='application/zip',
        headers={
            'Content-Disposition': 'attachment; filename=lawtasksai-skills.zip'
        }
    )

# ============================================
# Admin Routes (protected in production)
# ============================================

@app.post("/admin/skills", include_in_schema=False)
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

@app.patch("/admin/skills/{skill_id}", include_in_schema=False)
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


@app.post("/admin/skills/batch-update", include_in_schema=False)
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


@app.patch("/admin/skills/{skill_id}/triggers", include_in_schema=False)
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


@app.post("/admin/triggers/batch", include_in_schema=False)
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


@app.post("/admin/skills/{skill_id}/versions", include_in_schema=False)
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

@app.get("/admin/users", include_in_schema=False)
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
            "credits_balance": user.credits_balance,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "license_key": license.license_key if license else None,
            "license_type": license.type if license else None,
            "license_credits": license.credits_remaining if license else None,
        })
    
    return {"users": users, "count": len(users)}


@app.post("/admin/credits/add", include_in_schema=False)
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


@app.post("/admin/migrate/add-triggers-column", include_in_schema=False)
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

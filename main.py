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
CURRENT_LOADER_VERSION = "1.3.0"
LOADER_UPDATE_URL = "https://lawtasksai.com/download"
LOADER_UPDATE_MESSAGE = None  # Set to a string when there's an important update

# Initialize Stripe
stripe.api_key = STRIPE_SECRET_KEY

# Initialize Anthropic client

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

# ============================================
# FastAPI App
# ============================================

app = FastAPI(
    title="LawTasksAI API",
    description="Skill delivery, licensing, and usage tracking for LawTasksAI.",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

@app.get("/admin/skills/{skill_id}", include_in_schema=False)
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
            "firm_name": user.firm_name,
            "credits_balance": user.credits_balance,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "license_key": license.license_key if license else None,
            "license_type": license.type if license else None,
            "license_credits": license.credits_remaining if license else None,
            "profile": user.profile or {},
        })
    
    return {"users": users, "count": len(users)}


@app.delete("/admin/users/{user_id}", include_in_schema=False)
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

@app.get("/admin/pages", include_in_schema=False)
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

@app.get("/admin/pages/{slug}", include_in_schema=False)
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

@app.put("/admin/pages/{slug}", include_in_schema=False)
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

@app.get("/admin/pages/{slug}/versions", include_in_schema=False)
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

@app.get("/admin/pages/{slug}/versions/{version}", include_in_schema=False)
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

@app.post("/admin/pages/{slug}/restore/{version}", include_in_schema=False)
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

@app.post("/admin/migrate/add-content-pages", include_in_schema=False)
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

@app.get("/admin/files/list", include_in_schema=False)
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

@app.get("/admin/files/read", include_in_schema=False)
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

@app.put("/admin/files/write", include_in_schema=False)
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

@app.get("/admin/templates", include_in_schema=False)
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

@app.get("/admin/templates/{template_id}", include_in_schema=False)
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

@app.put("/admin/templates/{template_id}", include_in_schema=False)
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

@app.post("/admin/templates/preview", include_in_schema=False)
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

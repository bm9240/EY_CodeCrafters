"""
Session management module for a multi-channel retail AI system.

This module implements an in-memory session store and three HTTP endpoints:
- POST /session/start   -> create a new session and return a session token
- GET  /session/restore -> retrieve a session using the `X-Session-Token` header
- POST /session/update  -> update session state based on an action

All data is kept in memory (no database). This file is a standalone FastAPI app
so it can be run directly with `uvicorn backend.session_manager:app`.

Every function and major step contains inline comments and docstrings.
"""

# Standard library imports
import uuid
import logging
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime
from pathlib import Path

# FastAPI imports
from fastapi import FastAPI, Header, HTTPException, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import uvicorn
import pandas as pd

from db.repositories.customer_repo import ensure_customer, ensure_customer_record

# Configure a simple logger for this module
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ==========================================
# PERSUASION ENGINE HELPERS
# ==========================================

def _detect_last_action_from_message(message: str) -> Optional[str]:
    """Detect user intent from message using keyword matching."""
    message_lower = message.lower()
    
    # Purchase intent keywords
    if any(kw in message_lower for kw in ["buy", "purchase", "checkout", "order", "payment", "pay"]):
        return "purchase_intent"
    
    # Cart update keywords
    if any(kw in message_lower for kw in ["add to cart", "add cart", "cart"]):
        return "cart_update"
    
    # Browsing keywords
    if any(kw in message_lower for kw in ["show", "recommend", "looking", "want", "need", "find", "search"]):
        return "browsing"
    
    return None


def _generate_simple_summary(chat_context: List[Dict[str, Any]]) -> str:
    """Generate a 2-3 line conversation summary using simple heuristics."""
    if not chat_context or len(chat_context) < 2:
        return ""
    
    # Take last 10 messages
    recent = chat_context[-10:]
    user_msgs = [msg["message"].lower() for msg in recent if msg.get("sender") == "user"]
    
    if not user_msgs:
        return ""
    
    # Detect products mentioned
    product_kws = ["shoe", "shirt", "pant", "jacket", "sneaker", "tshirt", "jeans", "hoodie", "running", "casual", "formal"]
    products = []
    for msg in user_msgs:
        for kw in product_kws:
            if kw in msg and kw not in products:
                products.append(kw)
    
    # Detect stage
    stage = "browsing"
    if any(kw in " ".join(user_msgs[-3:]) for kw in ["buy", "purchase", "order", "checkout", "cart", "payment"]):
        stage = "ready to purchase"
    elif any(kw in " ".join(user_msgs[-3:]) for kw in ["compare", "difference", "between", "better"]):
        stage = "comparing options"
    
    product_text = ", ".join(products[:3]) + "s" if products else "products"
    return f"Customer is {stage} - interested in {product_text}. {len(user_msgs)} interactions so far."


def _should_generate_summary(chat_context: List[Dict[str, Any]]) -> bool:
    """Check if it's time to generate summary (every 6 messages)."""
    return len(chat_context) % 6 == 0 and len(chat_context) >= 6


def _update_metadata_to_supabase(session_token: str, session: Dict[str, Any]) -> bool:
    """Update only the metadata field in Supabase (lightweight update)."""
    if not is_supabase_enabled():
        return False
    
    try:
        import requests
        from db.supabase_client import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
        from datetime import datetime
        
        session_id = session.get("session_id")
        if not session_id:
            return False
        
        now = datetime.utcnow().isoformat() + "Z"
        
        url = f"{SUPABASE_URL}/rest/v1/sessions"
        headers = {
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal"
        }
        
        params = {"session_token": f"eq.{session_token}"}
        
        # Update metadata and last_activity
        update_payload = {
            "metadata": session.get("data", {}),
            "last_activity": now
        }
        
        response = requests.patch(url, json=update_payload, headers=headers, params=params, timeout=5)
        
        if response.status_code in [200, 204]:
            logger.debug(f"[METADATA] ✅ Updated metadata for session {session_id[:12]}...")
            return True
        else:
            logger.warning(f"[METADATA] ⚠️ Failed to update: {response.status_code}")
            return False
            
    except Exception as e:
        logger.debug(f"[METADATA] Update failed: {e}")
        return False

# Initialize FastAPI app
app = FastAPI(title="Session Manager", version="1.0.0")

# Add CORS middleware for frontend integration
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    """Lightweight health endpoint used by orchestrators and frontend probes."""
    try:
        # Basic sanity checks
        samples_loaded = len(PHONE_TO_CUSTOMER) >= 0
        return JSONResponse(status_code=200, content={"status": "healthy", "samples": samples_loaded})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "unhealthy", "error": str(e)})


@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    logger.error(f"Unhandled error in session_manager: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"status": "error", "message": "Internal server error"})

# In-memory session store (mapping session_token -> session dict)
# This satisfies the "no database" requirement; all data is lost on process restart.
SESSIONS: Dict[str, Dict[str, Any]] = {}

# Phone-based mappings
# phone_number -> session_token (for quick lookup)
PHONE_SESSIONS: Dict[str, str] = {}
# phone_number -> persistent session_id (ensures one ID per phone across channels)
PHONE_SESSION_IDS: Dict[str, str] = {}
# phone_number -> customer_id mapping from CSV
PHONE_TO_CUSTOMER: Dict[str, str] = {}

# Telegram-based mappings
# telegram_chat_id -> session_token (for quick lookup)
TELEGRAM_SESSIONS: Dict[str, str] = {}
# telegram_chat_id -> phone_number mapping (for customer lookup)
TELEGRAM_TO_PHONE: Dict[str, str] = {}

# Load customers.csv to map phone numbers to customer IDs
try:
    data_path = Path(__file__).parent / "data" / "customers.csv"
    customers_df = pd.read_csv(data_path)
    for _, row in customers_df.iterrows():
        phone = str(row['phone_number'])
        customer_id = str(row['customer_id'])
        PHONE_TO_CUSTOMER[phone] = customer_id
        digits_only = "".join(ch for ch in phone if ch.isdigit())
        if digits_only:
            PHONE_TO_CUSTOMER[digits_only] = customer_id
    logger.info(f"Loaded {len(PHONE_TO_CUSTOMER)} phone-to-customer mappings")
except Exception as e:
    logger.error(f"Failed to load customers.csv: {e}")
    PHONE_TO_CUSTOMER = {}

# No expiry: sessions remain active unless explicitly ended

# ================================
# PHASE 2: Supabase Integration
# ================================

def is_supabase_enabled():
    """Check if Supabase persistence is enabled."""
    try:
        from db.supabase_client import FEATURE_SUPABASE_WRITE, SUPABASE_URL
        return FEATURE_SUPABASE_WRITE and SUPABASE_URL
    except:
        return False


def save_session_to_supabase(session_token: str, session: Dict[str, Any]) -> bool:
    """Persist session to Supabase table 'sessions' (MATCHING YOUR SCHEMA).
    
    UPSERT LOGIC: Checks if active session exists for phone/customer and updates it,
    otherwise inserts new record. This ensures one active session per customer.
    
    Schema (VERIFIED):
    - session_id: UUID (generated by Supabase)
    - customer_id: TEXT NOT NULL (matches customers.customer_id TEXT)
    - phone: TEXT NOT NULL
    - channel: TEXT (whatsapp/kiosk/website)
    - session_token: TEXT UNIQUE NOT NULL
    - status: TEXT DEFAULT 'active' (active/expired/logged_out)
    - created_at: TIMESTAMPTZ DEFAULT now()
    - last_activity: TIMESTAMPTZ DEFAULT now()
    - expires_at: TIMESTAMPTZ DEFAULT (now + 7 days)
    - metadata: JSONB DEFAULT '{}'::jsonb
    
    Returns True if successful, False otherwise.
    """
    if not is_supabase_enabled():
        logger.debug("[SUPABASE] Persistence disabled - skipping save")
        return False
    
    try:
        import requests
        from db.supabase_client import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
        from datetime import datetime, timedelta
        
        customer_id = session.get("customer_id")
        phone = session.get("phone")
        
        # Validate required fields
        if not customer_id or not phone:
            logger.error(f"[SUPABASE] ❌ SAVE FAILED: Missing required fields. customer_id={customer_id}, phone={phone}")
            return False
        
        # Keep customer_id as STRING (not int) - schema is TEXT
        customer_id_str = str(customer_id).strip()
        phone_str = str(phone).strip()
        
        if not customer_id_str or not phone_str:
            logger.error(f"[SUPABASE] ❌ SAVE FAILED: Empty customer_id or phone after strip")
            return False
        
        # Calculate expiry (7 days from now)
        now = datetime.utcnow().isoformat() + "Z"
        expires_at = (datetime.utcnow() + timedelta(days=7)).isoformat() + "Z"
        
        session_record = {
            "customer_id": customer_id_str,  # TEXT - must be string
            "phone": phone_str,  # TEXT
            "session_token": session_token,  # TEXT UNIQUE
            "channel": str(session.get("channel", "whatsapp")),  # TEXT
            "status": "active",  # TEXT
            "created_at": session.get("created_at", now),  # TIMESTAMPTZ
            "last_activity": now,  # TIMESTAMPTZ
            "expires_at": expires_at,  # TIMESTAMPTZ
            "metadata": session.get("data", {})  # JSONB - session data
        }
        
        # Validate session_record before sending
        if not session_record.get("session_token"):
            logger.error(f"[SUPABASE] ❌ SAVE FAILED: Missing session_token")
            return False
        
        url = f"{SUPABASE_URL}/rest/v1/sessions"
        headers = {
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "Content-Type": "application/json"
        }
        
        # UPSERT: First check if active session exists for this phone
        check_params = {
            "phone": f"eq.{phone_str}",
            "status": "eq.active",
            "limit": "1"
        }
        check_response = requests.get(url, headers=headers, params=check_params, timeout=5)
        
        if check_response.status_code == 200 and check_response.json():
            # UPDATE existing session
            existing = check_response.json()[0]
            existing_id = existing.get("session_id")
            
            update_params = {"session_id": f"eq.{existing_id}"}
            update_headers = {**headers, "Prefer": "return=minimal"}
            
            update_response = requests.patch(url, json=session_record, headers=update_headers, params=update_params, timeout=5)
            
            if update_response.status_code in [200, 204]:
                logger.info(f"[SUPABASE] ✅ Session UPDATED: customer_id={customer_id_str}, phone={phone_str}")
                return True
            else:
                logger.error(f"[SUPABASE] ❌ UPDATE FAILED: Status={update_response.status_code}, Body={update_response.text}")
                return False
        else:
            # INSERT new session
            insert_headers = {**headers, "Prefer": "return=minimal"}
            insert_response = requests.post(url, json=session_record, headers=insert_headers, timeout=5)
            
            if insert_response.status_code in [200, 201]:
                logger.info(f"[SUPABASE] ✅ Session INSERTED: customer_id={customer_id_str}, phone={phone_str}")
                return True
            else:
                logger.error(f"[SUPABASE] ❌ INSERT FAILED: Status={insert_response.status_code}, Body={insert_response.text}")
                return False
        
    except Exception as e:
        logger.error(f"[SUPABASE] ❌ Exception during save: {type(e).__name__}: {e}")
        return False


def restore_session_from_supabase(phone: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Restore active session from Supabase by phone (MATCHING YOUR SCHEMA).
    
    Looks up the most recent active session and verifies expiry.
    Returns None if not found or expired.
    Also restores session_id into PHONE_SESSION_IDS for continuity.
    """
    if not is_supabase_enabled() or not phone:
        logger.debug("[SUPABASE] Persistence disabled or no phone - skipping restore")
        return None
    
    try:
        import requests
        from db.supabase_client import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
        from datetime import datetime
        
        # Query for active, non-expired sessions
        url = f"{SUPABASE_URL}/rest/v1/sessions"
        headers = {
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "Content-Type": "application/json"
        }
        
        # Filter by phone and active status, order by last_activity DESC
        params = {
            "phone": f"eq.{phone}",
            "status": "eq.active",
            "order": "last_activity.desc",
            "limit": "1"
        }
        
        response = requests.get(url, headers=headers, params=params, timeout=5)
        
        if response.status_code == 200 and response.json():
            session_record = response.json()[0]
            
            # Check expiry
            expires_at = session_record.get("expires_at")
            if expires_at:
                expiry_time = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                if datetime.utcnow().replace(tzinfo=None) > expiry_time.replace(tzinfo=None):
                    logger.info(f"[SUPABASE] Session expired: {session_record['session_id']}")
                    return None
            
            logger.info(f"[SUPABASE] ✅ Session restored: {str(session_record['session_id'])[:12]}...")
            
            # Initialize default data structure if metadata is empty/incomplete
            metadata = session_record.get("metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
            
            # Ensure all required data fields are present
            default_data = {
                "cart": [],
                "recent": [],
                "chat_context": [],
                "last_action": None,
                "channels": [session_record.get("channel", "whatsapp")],
                "conversation_summary": "",
                "last_recommended_skus": [],
            }
            
            # Merge metadata with defaults (metadata takes precedence)
            merged_data = {**default_data, **metadata}
            
            # Ensure critical arrays are arrays
            if not isinstance(merged_data.get("chat_context"), list):
                merged_data["chat_context"] = []
            if not isinstance(merged_data.get("cart"), list):
                merged_data["cart"] = []
            if not isinstance(merged_data.get("recent"), list):
                merged_data["recent"] = []
            
            # CRITICAL: Restore session_id mapping for persistence across restarts
            restored_session_id = str(session_record["session_id"])
            PHONE_SESSION_IDS[phone] = restored_session_id
            logger.info(f"[SUPABASE] Restored session_id mapping: {phone} -> {restored_session_id[:12]}...")
            
            # Convert to in-memory format (reconstruct full session object)
            return {
                "session_id": restored_session_id,
                "phone": session_record["phone"],
                "channel": session_record["channel"],
                "customer_id": session_record["customer_id"],
                "session_token": session_record["session_token"],
                "created_at": session_record["created_at"],
                "updated_at": session_record["last_activity"],
                "is_active": session_record["status"] == "active",
                "expires_at": session_record["expires_at"],
                "data": merged_data,
                "metadata": merged_data
            }
        else:
            logger.info(f"[SUPABASE] No active session found for phone: {phone}")
            return None
        
    except Exception as e:
        logger.warning(f"[SUPABASE] Exception during restore: {e}")
        return None


def update_session_expiry_in_supabase(session_id: str) -> bool:
    """Update session expiry to extend 7-day window (sliding expiry).
    
    Called on every session activity to maintain the sliding window.
    """
    if not is_supabase_enabled() or not session_id:
        return False
    
    try:
        import requests
        from db.supabase_client import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
        from datetime import datetime, timedelta
        
        now = datetime.utcnow().isoformat() + "Z"
        expires_at = (datetime.utcnow() + timedelta(days=7)).isoformat() + "Z"
        
        url = f"{SUPABASE_URL}/rest/v1/sessions"
        headers = {
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal"
        }
        
        params = {"session_id": f"eq.{session_id}"}
        
        response = requests.patch(
            url,
            json={"last_activity": now, "expires_at": expires_at},
            headers=headers,
            params=params,
            timeout=5
        )
        
        if response.status_code in [200, 204]:
            logger.info(f"[SUPABASE] ✅ Session expiry updated: {str(session_id)[:12]}...")
            return True
        else:
            logger.warning(f"[SUPABASE] Failed to update expiry: {response.status_code} {response.text}")
            return False
        
    except Exception as e:
        logger.warning(f"[SUPABASE] Exception during expiry update: {e}")
        return False


def delete_session_from_supabase(session_id: str) -> bool:
    """Soft-delete session by marking status=logged_out."""
    if not is_supabase_enabled() or not session_id:
        return False
    
    try:
        import requests
        from db.supabase_client import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
        from datetime import datetime
        
        now = datetime.utcnow().isoformat() + "Z"
        
        url = f"{SUPABASE_URL}/rest/v1/sessions"
        headers = {
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal"
        }
        
        params = {"session_id": f"eq.{session_id}"}
        
        response = requests.patch(
            url,
            json={"status": "logged_out", "last_activity": now},
            headers=headers,
            params=params,
            timeout=5
        )
        
        if response.status_code in [200, 204]:
            logger.info(f"[SUPABASE] ✅ Session deleted: {str(session_id)[:12]}...")
            return True
        else:
            logger.warning(f"[SUPABASE] Failed to delete session: {response.status_code} {response.text}")
            return False
        
    except Exception as e:
        logger.warning(f"[SUPABASE] Exception during delete: {e}")
        return False


# -----------------------------------
# Pydantic models for requests
# -----------------------------------

class StartSessionRequest(BaseModel):
    """Request model for starting a session.

    Requires either phone number or telegram_chat_id for session continuity across channels.
    ALL fields are optional for maximum compatibility.
    """
    phone: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    channel: str = "whatsapp"
    user_id: Optional[str] = None
    customer_id: Optional[str] = None
    
    class Config:
        extra = "allow"  # Allow extra fields without failing validation


class StartSessionResponse(BaseModel):
    """Response model returned after creating a session."""
    session_token: str
    session: Dict[str, Any]


class UpdateSessionRequest(BaseModel):
    """Request model for updating session state.

    `action` must be one of: `add_to_cart`, `view_product`, `chat_message`.
    `payload` is an arbitrary dict that contains data required by the action.
    """
    action: str = Field(..., description="Action type to apply to the session")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Action-specific data payload")


class RestoreSessionResponse(BaseModel):
    """Response model returned when restoring a session."""
    session_token: str
    session: Dict[str, Any]


class UpdateSessionResponse(BaseModel):
    """Response model returned after successfully updating a session."""
    session: Dict[str, Any]


class LoginRequest(BaseModel):
    """Request body for logging in customers via the web frontend."""
    customer_id: Optional[str] = Field(default=None, description="Unique customer identifier if already known")
    name: str = Field(..., description="Customer display name")
    age: Optional[int] = Field(default=None, description="Age in years")
    gender: Optional[str] = Field(default=None, description="Gender information")
    phone_number: str = Field(..., description="Primary phone number")
    city: Optional[str] = Field(default=None, description="City or locality")
    channel: str = Field(default="web", description="Channel initiating the login")

# -----------------------------
# Helper functions
# -----------------------------

def _now_iso() -> str:
    """Return the current UTC time as an ISO-formatted string.

    This helps keep timestamps consistent across session objects.
    """
    return datetime.utcnow().isoformat() + "Z"


def _register_phone_mapping(phone: Optional[str], customer_id: Optional[str]) -> None:
    """Store convenient lookups for phone -> customer id."""
    if not phone or not customer_id:
        return

    phone_str = str(phone)
    customer_str = str(customer_id)

    PHONE_TO_CUSTOMER[phone_str] = customer_str
    digits_only = "".join(ch for ch in phone_str if ch.isdigit())
    if digits_only:
        PHONE_TO_CUSTOMER[digits_only] = customer_str


def _sanitize_shipping_address(raw: Any) -> Dict[str, str]:
    """Normalize shipping address payloads coming from chat metadata."""
    if not isinstance(raw, dict):
        return {}

    cleaned: Dict[str, str] = {}
    for key in ("city", "landmark", "building", "building_name"):
        value = raw.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        normalized_key = "building" if key in {"building", "building_name"} else key
        if normalized_key == "building" and "building" in cleaned:
            continue
        cleaned[normalized_key] = text
    return cleaned


def _sync_shipping_address(session: Dict[str, Any], address: Dict[str, str]) -> None:
    """Persist the latest shipping address to Supabase for the active customer."""
    if not address:
        return

    logger.info("Syncing shipping address to Supabase: %s", address)

    phone = session.get("phone") or session.get("customer_profile", {}).get("phone_number")
    if not phone:
        return

    profile_name = None
    profile = session.get("customer_profile")
    if isinstance(profile, dict):
        profile_name = profile.get("name")

    attribute_payload: Dict[str, Any] = {}
    city_value = address.get("city")
    if city_value:
        attribute_payload["city"] = city_value

    try:
        ensure_customer_record(
            phone,
            name=profile_name,
            attributes=attribute_payload or None,
            address=address,
        )
        logger.info("Shipping address sync complete for phone=%s", phone)
    except Exception:
        logger.warning("Failed to sync shipping address for phone=%s", phone, exc_info=True)


def generate_session_token() -> str:
    """Generate a secure session token.

    Uses UUID4 hex representation to create a reasonably unique token.
    Returns:
        A string token suitable for use in headers and storage keys.
    """
    # Create a random UUID and return the hex string (32 chars, lower-case)
    token = uuid.uuid4().hex
    return token


def create_session(
    phone: Optional[str] = None,
    telegram_chat_id: Optional[str] = None,
    channel: str = "whatsapp",
    user_id: Optional[str] = None,
    customer_id: Optional[str] = None,
    customer_profile: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Create a new session object and store it in the in-memory store.

    The session schema follows the user's requirements:
    - session_id: uuid (persistent for the phone number)
    - phone: phone number as primary identifier (can be None for Telegram-only sessions)
    - telegram_chat_id: Telegram chat ID (can be None for phone-only sessions)
    - channel: whatsapp, kiosk, or telegram
    - user_id: can be `None` initially
    - customer_id: CRITICAL - must not be None for proper session linking
    - data: { cart: [], recent: [], chat_context: [], channels: [channel], conversation_summary: "", last_recommended_skus: [] }
    - metadata: { channels: [channel], ... }
    - created_at: session creation timestamp
    - updated_at: last activity timestamp
    - is_active: session status
    - expires_at: 7-day expiry for sliding window

    Args:
        phone: Phone number as primary identifier (optional)
        telegram_chat_id: Telegram chat ID (optional)
        channel: Channel type (whatsapp, kiosk, telegram)
        user_id: Optional user identifier to attach to the session.
        customer_id: Customer ID - should be provided for all authenticated sessions
        customer_profile: Full customer record for omni-channel continuity

    Returns:
        Tuple of (session_token, session_dict)
    """
    # Validate that at least one identifier is provided
    if not phone and not telegram_chat_id:
        logger.error("[CREATE_SESSION] VALIDATION FAILED: No phone or telegram_chat_id provided")
        raise ValueError("Either phone or telegram_chat_id must be provided")

    # Determine the primary identifier for session lookup
    primary_id = phone or telegram_chat_id
    logger.info(f"[CREATE_SESSION] Starting for primary_id={primary_id}, channel={channel}, customer_id={customer_id}")
    
    # PHASE 3: Check for existing session in memory first, then Supabase
    existing_token = None
    existing_session = None
    
    # 1. Check in-memory first
    if phone and phone in PHONE_SESSIONS:
        existing_token = PHONE_SESSIONS[phone]
        if existing_token in SESSIONS:
            existing_session = SESSIONS[existing_token]
    elif telegram_chat_id and telegram_chat_id in TELEGRAM_SESSIONS:
        existing_token = TELEGRAM_SESSIONS[telegram_chat_id]
        if existing_token in SESSIONS:
            existing_session = SESSIONS[existing_token]
    
    # 2. If not in memory but phone provided, check Supabase for persistence
    if not existing_session and phone and is_supabase_enabled():
        try:
            supabase_session = restore_session_from_supabase(phone=phone)
            if supabase_session:
                # Verify it's still active and not expired
                if supabase_session.get("is_active") and supabase_session.get("expires_at"):
                    from datetime import datetime
                    expiry_time = datetime.fromisoformat(supabase_session["expires_at"].replace('Z', '+00:00'))
                    if datetime.utcnow().replace(tzinfo=None) <= expiry_time.replace(tzinfo=None):
                        existing_token = supabase_session.get("session_token")
                        existing_session = supabase_session
                        logger.info(f"[CREATE_SESSION] ✅ Recovered session from Supabase: token={existing_token[:20]}...")
        except Exception as e:
            logger.debug(f"[CREATE_SESSION] Supabase lookup failed (continuing): {e}")

    if existing_session:
        # existing_session is either from memory or from Supabase
        # Safe to use as-is, but reload into memory if needed
        if existing_token and existing_token not in SESSIONS:
            logger.info(f"[CREATE_SESSION] Reloading session into memory after restart: token={existing_token[:20]}...")
            SESSIONS[existing_token] = existing_session
        
        logger.info(f"[CREATE_SESSION] Found existing session: token={existing_token}, old_channel={existing_session.get('channel')}, new_channel={channel}")
        
        # Restore and update existing session for omni-channel continuity
        existing_session["channel"] = channel
        existing_session["is_active"] = True
        existing_session["updated_at"] = _now_iso()
        
        # Update expires_at to 7 days from now (sliding expiry)
        from datetime import datetime, timedelta
        existing_session["expires_at"] = (datetime.utcnow() + timedelta(days=7)).isoformat()
        
        # Add channel to metadata if not present
        if "metadata" not in existing_session:
            existing_session["metadata"] = {"channels": []}
        if isinstance(existing_session["metadata"], dict) and "channels" in existing_session["metadata"]:
            if channel not in existing_session["metadata"]["channels"]:
                existing_session["metadata"]["channels"].append(channel)
                logger.info(f"[CREATE_SESSION] Added channel {channel} to metadata.channels")
        
        # Update telegram_chat_id if provided and different
        if telegram_chat_id and existing_session.get("telegram_chat_id") != telegram_chat_id:
            existing_session["telegram_chat_id"] = telegram_chat_id
            TELEGRAM_SESSIONS[telegram_chat_id] = existing_token
            if phone:
                TELEGRAM_TO_PHONE[telegram_chat_id] = phone
            logger.info(f"[CREATE_SESSION] Updated telegram_chat_id in existing session")
        
        if customer_profile:
            existing_session["customer_profile"] = customer_profile
            profile_customer_id = None
            if isinstance(customer_profile, dict):
                profile_customer_id = customer_profile.get("customer_id")
            if profile_customer_id:
                existing_session["customer_id"] = str(profile_customer_id)
                if not existing_session.get("user_id"):
                    existing_session["user_id"] = str(profile_customer_id)
                if phone:
                    _register_phone_mapping(phone, profile_customer_id)
                logger.info(f"[CREATE_SESSION] Updated customer_profile: customer_id={profile_customer_id}")
        
        # Update customer_id if provided
        if customer_id:
            existing_session["customer_id"] = str(customer_id)
            if not existing_session.get("user_id"):
                existing_session["user_id"] = str(customer_id)
            logger.info(f"[CREATE_SESSION] Set customer_id={customer_id} in existing session")
        
        SESSIONS[existing_token] = existing_session
        
        # Ensure phone mappings are restored (in case of server restart)
        if phone and phone not in PHONE_SESSIONS:
            PHONE_SESSIONS[phone] = existing_token
            logger.info(f"[CREATE_SESSION] Restored phone mapping: {phone} → token")
        
        # CRITICAL: Save updated session back to Supabase (channel, expires_at, etc.)
        try:
            save_session_to_supabase(existing_token, existing_session)
        except Exception as save_err:
            logger.warning(f"[CREATE_SESSION] Failed to save restored session to Supabase: {save_err}")
        
        logger.info(f"[CREATE_SESSION] ✅ Restored session: token={existing_token[:20]}..., session_id={existing_session['session_id']}, customer_id={existing_session.get('customer_id')}")
        return existing_token, existing_session

    # Generate new session token
    token = generate_session_token()
    logger.info(f"[CREATE_SESSION] Generated new token: {token[:20]}...")

    # For phone-based sessions, use persistent session_id per phone
    # For Telegram-only sessions, create a new session_id
    session_id = None
    if phone:
        session_id = PHONE_SESSION_IDS.get(phone) or str(uuid.uuid4())
        PHONE_SESSION_IDS[phone] = session_id
    else:
        session_id = str(uuid.uuid4())

    if customer_profile and not customer_id:
        profile_customer_id = None
        if isinstance(customer_profile, dict):
            profile_customer_id = customer_profile.get("customer_id")
        if profile_customer_id:
            customer_id = str(profile_customer_id)
            logger.info(f"[CREATE_SESSION] Extracted customer_id from profile: {customer_id}")

    # Map to customer_id from CSV (only if phone is provided and customer_id not provided)
    if not customer_id and phone:
        customer_id = PHONE_TO_CUSTOMER.get(phone)
        if not customer_id:
            digits_only = "".join(ch for ch in str(phone) if ch.isdigit())
            if digits_only:
                customer_id = PHONE_TO_CUSTOMER.get(digits_only)

        if customer_id:
            logger.info(f"[CREATE_SESSION] ✅ Mapped phone {phone} to customer_id {customer_id} from CSV")
            _register_phone_mapping(phone, customer_id)
        else:
            logger.warning(f"[CREATE_SESSION] ⚠️ Phone {phone} not found in customers.csv; ensuring Supabase record")
            try:
                ensured = ensure_customer_record(phone)
            except Exception as exc:
                logger.warning(f"[CREATE_SESSION] Failed to ensure Supabase customer for phone {phone}: {exc}")
                ensured = None

            if ensured:
                ensured_id = ensured.get("customer_id")
                customer_id = str(ensured_id) if ensured_id is not None else None
                if customer_id:
                    ensured_phone = str(ensured.get("phone_number") or phone)
                    _register_phone_mapping(ensured_phone, customer_id)
                    _register_phone_mapping(phone, customer_id)
                    logger.info(f"[CREATE_SESSION] ✅ Created Supabase customer {customer_id} for phone {ensured_phone}")
    
    # Build complete session payload with required structure
    resolved_user_id = user_id or customer_id
    
    # Calculate 7-day expiry (sliding window)
    from datetime import datetime, timedelta
    expires_at = (datetime.utcnow() + timedelta(days=7)).isoformat()
    
    session = {
        "session_id": session_id,  # persistent id for the phone (or unique for Telegram-only)
        "phone": phone,  # primary identifier (can be None)
        "telegram_chat_id": telegram_chat_id,  # Telegram chat ID (can be None)
        "channel": channel,  # current channel
        "user_id": resolved_user_id,  # can be None
        "customer_id": customer_id,  # CRITICAL: must not be None for authenticated sessions
        "data": {
            "cart": [],  # list of cart items
            "recent": [],  # recently viewed products
            "chat_context": [],  # chat history between user and agent
            "last_action": None,  # track last action performed
            "channels": [channel],  # channels user is logged into
            "conversation_summary": "",  # summary of conversation
            "last_recommended_skus": [],  # track last recommendations for context
        },
        "metadata": {
            "channels": [channel],
            "chat_context": [],
            "conversation_summary": "",
            "last_recommended_skus": [],
        },
        "created_at": _now_iso(),
        "updated_at": _now_iso(),  # timestamp for last update
        "is_active": True,  # session status
        "expires_at": expires_at,  # 7-day expiry window
    }

    if customer_profile:
        session["customer_profile"] = customer_profile

    # Store session in the global in-memory dict
    SESSIONS[token] = session

    # Update mappings
    if phone:
        PHONE_SESSIONS[phone] = token
        if customer_id:
            _register_phone_mapping(phone, customer_id)
    if telegram_chat_id:
        TELEGRAM_SESSIONS[telegram_chat_id] = token
        if phone:
            TELEGRAM_TO_PHONE[telegram_chat_id] = phone

    # Log for visibility - CRITICAL for debugging
    logger.info(f"[CREATE_SESSION] ✅ SUCCESS (Memory): token={token[:20]}..., session_id={session_id}, phone={phone}, customer_id={customer_id}, channel={channel}, expires_at={expires_at}")

    # PHASE 3: Persist to Supabase (async, non-blocking)
    try:
        supabase_success = save_session_to_supabase(token, session)
        if supabase_success:
            logger.info(f"[CREATE_SESSION] ✅ Supabase persistence successful for session_id={session_id}")
        else:
            logger.warning(f"[CREATE_SESSION] ⚠️ Supabase persistence returned False for session_id={session_id} - check logs above for save_session_to_supabase errors")
    except Exception as e:
        logger.exception(f"[CREATE_SESSION] ❌ Unexpected error during Supabase save: {e}")

    return token, session


def get_session(token: str) -> Dict[str, Any]:
    """Retrieve a session by its token from the in-memory store.

    Args:
        token: Session token provided by the client.

    Raises:
        HTTPException 404 if session not found.

    Returns:
        The session dictionary if found.
    """
    # Attempt to fetch the session from the global store
    session = SESSIONS.get(token)
    if session is None:
        # Return an HTTP error if the token is unknown
        logger.warning(f"Session token not found: {token}")
        raise HTTPException(status_code=404, detail="Session not found")

    return session


def update_session_state(token: str, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Apply an action to a session's state and persist it in memory.

    Supported actions:
    - add_to_cart: expects `payload['item']` -> item is appended to `data['cart']`
    - view_product: expects `payload['product_id']` -> appended to `data['recent']`
    - chat_message: expects `payload['message']` -> appended to `data['chat_context']` with timestamp

    Args:
        token: Session token identifying which session to update.
        action: Action string describing the update.
        payload: Action-specific data.

    Returns:
        The updated session dictionary.

    Raises:
        HTTPException 400 for unknown actions or missing payload data.
    """
    # Retrieve the session or raise 404
    session = get_session(token)

    # Shortcut reference to the mutable data sub-dictionary
    data = session["data"]

    # Handle each supported action explicitly
    if action == "add_to_cart":
        # Validate payload contains the expected 'item'
        item = payload.get("item")
        if item is None:
            raise HTTPException(status_code=400, detail="'item' is required for add_to_cart action")

        # Append the item to the cart list
        data["cart"].append(item)
        data["last_action"] = {
            "type": "add_to_cart",
            "item": item,
            "timestamp": _now_iso()
        }

        logger.info(f"Added item to cart for token={token}: {item}")

    elif action == "view_product":
        # Validate payload contains 'product_id' or 'product'
        product = payload.get("product") or payload.get("product_id")
        if product is None:
            raise HTTPException(status_code=400, detail="'product' or 'product_id' is required for view_product action")

        # Keep recent as a simple list; prepend newest views
        data["recent"].insert(0, {"product": product, "viewed_at": _now_iso()})

        # Optionally cap recent history length to avoid memory blowup
        if len(data["recent"]) > 50:
            data["recent"] = data["recent"][:50]

        data["last_action"] = {"type": "view_product", "product": product, "timestamp": _now_iso()}

        logger.info(f"Recorded product view for token={token}: {product}")

    elif action == "chat_message":
        # Validate payload contains 'message'
        message = payload.get("message")
        if message is None:
            raise HTTPException(status_code=400, detail="'message' is required for chat_message action")

        # Record the chat message with a timestamp and optional sender (defaults to 'user')
        sender = payload.get("sender", "user")
        # Allow optional metadata to be stored alongside chat messages
        metadata = payload.get("metadata")

        # Prevent accidental duplicate consecutive messages (same sender + text)
        last_entry = data["chat_context"][-1] if data["chat_context"] else None
        if last_entry and last_entry.get("sender") == sender and last_entry.get("message") == message:
            # Update timestamp on the existing entry instead of appending duplicate
            last_entry["timestamp"] = _now_iso()
            if metadata is not None:
                last_entry["metadata"] = metadata
            logger.debug("Skipped appending duplicate chat message; refreshed timestamp instead.")
        else:
            chat_entry = {"sender": sender, "message": message, "timestamp": _now_iso()}
            if metadata is not None:
                chat_entry["metadata"] = metadata
            data["chat_context"].append(chat_entry)

        # ENHANCEMENT 1: Detect last_action from user messages
        if sender == "user" and message:
            detected_action = _detect_last_action_from_message(message)
            if detected_action:
                data["last_action"] = detected_action
                logger.debug(f"🔍 Detected last_action: {detected_action}")
        
        # ENHANCEMENT 2: Extract SKUs from agent messages with cards
        if sender == "agent" and isinstance(metadata, dict):
            cards = metadata.get("cards", [])
            if cards:
                skus = []
                for card in cards:
                    if isinstance(card, dict):
                        sku = card.get("sku") or card.get("SKU") or card.get("product_id")
                        if sku:
                            skus.append(str(sku))
                
                if skus:
                    # Update last_recommended_skus
                    data["last_recommended_skus"] = skus
                    
                    # Add unique SKUs to recent (max 10)
                    for sku in skus:
                        if sku not in data["recent"]:
                            data["recent"].append(sku)
                    data["recent"] = data["recent"][-10:]  # Keep last 10
                    
                    logger.debug(f"🏷️ Updated last_recommended_skus: {len(skus)} SKUs")
        
        # ENHANCEMENT 3: Generate conversation summary every 6 messages
        if _should_generate_summary(data["chat_context"]):
            summary = _generate_simple_summary(data["chat_context"])
            if summary:
                data["conversation_summary"] = summary
                logger.info(f"📝 Generated conversation summary: {summary[:80]}...")

        if isinstance(metadata, dict):
            shipping_address = _sanitize_shipping_address(metadata.get("shipping_address"))
            if shipping_address:
                data["shipping_address"] = shipping_address
                _sync_shipping_address(session, shipping_address)

        # Update last_action timestamp
        if not isinstance(data.get("last_action"), dict):
            data["last_action"] = {"type": "chat_message", "sender": sender, "timestamp": _now_iso()}

        logger.info(f"Appended chat message for token={token}: sender={sender}")
        
        # ENHANCEMENT 4: Persist metadata to Supabase immediately
        try:
            _update_metadata_to_supabase(token, session)
        except Exception as persist_error:
            logger.warning(f"⚠️ Metadata persistence failed: {persist_error}")

    else:
        # Unknown action type -> client error
        logger.error(f"Unknown action attempted: {action}")
        # Support a lightweight 'set_user' action to set user_id on the session
        if action == "set_user":
            user_id = payload.get("user_id")
            if not user_id:
                raise HTTPException(status_code=400, detail="'user_id' is required for set_user action")

            session["user_id"] = user_id
            session["updated_at"] = _now_iso()
            SESSIONS[token] = session
            logger.info(f"Set user_id for token={token}: {user_id}")
            return session

        raise HTTPException(status_code=400, detail=f"Unsupported action: {action}")

    # Update the session-level timestamp to reflect mutation
    session["updated_at"] = _now_iso()

    # Persist change back to the global store (not strictly necessary for mutable dicts)
    SESSIONS[token] = session

    # Return the updated session
    return session


def _dedupe_chat_context(session: Dict[str, Any]) -> None:
    """Remove consecutive duplicate chat entries (in-place) to keep history clean."""
    ctx = session.get("data", {}).get("chat_context")
    if not ctx or len(ctx) < 2:
        return

    cleaned = [ctx[0]]
    for entry in ctx[1:]:
        last = cleaned[-1]
        if entry.get("sender") == last.get("sender") and entry.get("message") == last.get("message"):
            # skip duplicate
            continue
        cleaned.append(entry)

    session["data"]["chat_context"] = cleaned

# -----------------------------
# API Endpoints
# -----------------------------

@app.post("/session/login")
async def session_login(request: LoginRequest):
    """Create or update a customer record and return an active session."""
    customer_id = (request.customer_id or "").strip() or None

    phone = (request.phone_number or "").strip()
    if not phone:
        raise HTTPException(status_code=400, detail="phone_number is required")

    name = (request.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    customer_payload = {
        "name": name,
        "age": request.age if request.age is not None else "",
        "gender": (request.gender or "").strip(),
        "phone_number": phone,
        "city": (request.city or "").strip(),
    }

    if customer_id:
        customer_payload["customer_id"] = customer_id

    try:
        customer_record = ensure_customer(customer_payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to persist customer record")
        raise HTTPException(status_code=500, detail="Unable to save customer") from exc

    stored_phone = customer_record.get("phone_number") or phone
    stored_customer_id = customer_record.get("customer_id") or customer_id

    _register_phone_mapping(stored_phone, stored_customer_id)

    try:
        attributes = {
            "age": int(customer_record.get("age")) if customer_record.get("age") else None,
            "gender": customer_record.get("gender") or None,
            "city": customer_record.get("city") or None,
        }
        ensure_customer_record(stored_phone, name=customer_record.get("name"), attributes=attributes)
    except Exception as exc:
        logger.debug("Supabase ensure_customer_record skipped: %s", exc)

    token, session = create_session(
        phone=stored_phone,
        channel=request.channel or "web",
        user_id=str(stored_customer_id),
        customer_id=str(stored_customer_id),
        customer_profile=customer_record,
    )

    try:
        _dedupe_chat_context(session)
    except Exception:
        logger.debug("Failed to dedupe chat context on login", exc_info=True)

    return JSONResponse(status_code=200, content={
        "session_token": token,
        "session": session,
        "customer": customer_record,
    })


@app.post("/session/start")
async def session_start(request: dict = Body(...)):
    """Start a new session or restore existing session for a phone number or Telegram chat.

    The endpoint accepts phone, telegram_chat_id, channel, and optional user_id/customer_id.
    Returns the session_token and full session object.
    If identifier already has an active session, restores it with updated channel.
    
    PHASE 3 IMPROVEMENTS:
    - Better error handling and logging for kiosk "Failed to create session" debugging
    - Explicit customer_id resolution from CSV/Supabase
    - Ensures no null customer_id for authenticated sessions
    """
    logger.info(f"[SESSION_START] Received raw request: {request}")
    
    try:
        # Extract fields from request dict
        phone = request.get('phone')
        telegram_chat_id = request.get('telegram_chat_id')
        channel = request.get('channel', 'whatsapp')
        user_id = request.get('user_id')
        customer_id = request.get('customer_id')
        
        logger.info(f"[SESSION_START] Parsed: phone={phone}, channel={channel}, customer_id={customer_id}, telegram_chat_id={telegram_chat_id}")
        
        # Validate minimum requirements
        if not phone and not telegram_chat_id:
            logger.error(f"[SESSION_START] ❌ VALIDATION FAILED: No phone or telegram_chat_id provided")
            return JSONResponse(status_code=400, content={"error": "Either phone or telegram_chat_id is required"})
        
        # If no customer_id provided, try to resolve it
        if not customer_id and phone:
            # First check CSV mapping
            if phone in PHONE_TO_CUSTOMER:
                customer_id = PHONE_TO_CUSTOMER[phone]
                logger.info(f"[SESSION_START] Resolved customer_id from CSV mapping: {customer_id}")
            else:
                # Try to load from CSV
                import pandas as pd
                try:
                    df = pd.read_csv(Path(__file__).parent / "data" / "customers.csv")
                    matches = df[df['phone_number'].astype(str) == str(phone)]
                    if len(matches) > 0:
                        customer_id = str(matches.iloc[0]['customer_id'])
                        _register_phone_mapping(phone, customer_id)
                        logger.info(f"[SESSION_START] Loaded customer_id from CSV: {customer_id}")
                except Exception as csv_err:
                    logger.debug(f"[SESSION_START] CSV lookup failed: {csv_err}")
        
        logger.info(f"[SESSION_START] Final customer_id: {customer_id}, has_phone={bool(phone)}")
        
        # Create or restore session using helper
        try:
            token, session = create_session(
                phone=phone,
                telegram_chat_id=telegram_chat_id,
                channel=channel,
                user_id=user_id,
                customer_id=customer_id
            )
        except ValueError as create_err:
            logger.error(f"[SESSION_START] ❌ create_session ValueError: {create_err}")
            return JSONResponse(status_code=400, content={"error": str(create_err)})
        except Exception as create_err:
            logger.exception(f"[SESSION_START] ❌ create_session Exception: {create_err}")
            return JSONResponse(status_code=500, content={"error": f"Failed to create session: {str(create_err)}"})
        
        logger.info(f"[SESSION_START] ✅ Session created/restored: token={token[:20]}..., customer_id={session.get('customer_id')}, session_id={session.get('session_id')}")

        # Clean up any accidental duplicate entries in chat history before returning
        try:
            _dedupe_chat_context(session)
        except Exception as dedup_err:
            logger.warning(f"[SESSION_START] Failed to dedupe chat context: {dedup_err}")

        # Verify session has expected fields before returning
        if not session.get('session_token'):
            session['session_token'] = token
        
        response_data = {
            "session_token": token,
            "session": session,
            "success": True
        }
        
        logger.info(f"[SESSION_START] ✅ Returning session token and complete session object. token={token[:20]}..., session_id={session.get('session_id')}")
        return JSONResponse(status_code=200, content=response_data)
        
    except Exception as e:
        logger.exception(f"[SESSION_START] ❌ UNEXPECTED ERROR: {e}")
        return JSONResponse(status_code=500, content={"error": f"Internal server error: {str(e)}", "type": type(e).__name__})


@app.get("/session/restore", response_model=RestoreSessionResponse)
async def session_restore(
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
    x_phone: Optional[str] = Header(None, alias="X-Phone")
):
    """Restore a session using the `X-Session-Token` header or `X-Phone` header.

    PHASE 2: First checks in-memory, then falls back to Supabase for persistent recovery with sliding 7-day expiry.
    """
    logger.info(f"[SESSION_RESTORE] Received: token={x_session_token[:20] if x_session_token else None}..., phone={x_phone}")
    
    # Try to restore by session token first (in-memory)
    if x_session_token:
        try:
            session = get_session(x_session_token)
            # Refresh expiry (sliding window)
            session_id = session.get("session_id")
            if session_id:
                update_session_expiry_in_supabase(session_id)
            logger.info(f"[SESSION_RESTORE] ✅ Found session by token: customer_id={session.get('customer_id')}")
            return JSONResponse(status_code=200, content={"session_token": x_session_token, "session": session})
        except HTTPException:
            # Not in memory - try Supabase as failover
            logger.debug(f"[SESSION_RESTORE] Token not in memory, checking Supabase...")
            if x_phone:
                try:
                    supabase_session = restore_session_from_supabase(phone=x_phone)
                    if supabase_session and supabase_session.get("session_token") == x_session_token:
                        # Reload into memory
                        SESSIONS[x_session_token] = supabase_session
                        logger.info(f"[SESSION_RESTORE] ✅ Recovered from Supabase by token+phone")
                        return JSONResponse(status_code=200, content={"session_token": x_session_token, "session": supabase_session})
                except Exception as e:
                    logger.debug(f"[SESSION_RESTORE] Supabase fallback failed: {e}")
            logger.warning(f"[SESSION_RESTORE] ⚠️ Session token not found: {x_session_token[:20]}...")
            raise HTTPException(status_code=404, detail="Session not found")

    # Try to restore by phone (omni-channel recovery)
    if x_phone:
        # Check memory first
        if x_phone in PHONE_SESSIONS:
            token = PHONE_SESSIONS[x_phone]
            try:
                session = get_session(token)
                session_id = session.get("session_id")
                if session_id:
                    update_session_expiry_in_supabase(session_id)
                logger.info(f"[SESSION_RESTORE] ✅ Found session by phone (memory): customer_id={session.get('customer_id')}")
                return JSONResponse(status_code=200, content={"session_token": token, "session": session})
            except HTTPException:
                pass
        
        # Try Supabase as fallback
        try:
            supabase_session = restore_session_from_supabase(phone=x_phone)
            if supabase_session:
                token = supabase_session.get("session_token")
                PHONE_SESSIONS[x_phone] = token
                SESSIONS[token] = supabase_session
                logger.info(f"[SESSION_RESTORE] ✅ Recovered from Supabase by phone")
                return JSONResponse(status_code=200, content={"session_token": token, "session": supabase_session})
        except Exception as e:
            logger.debug(f"[SESSION_RESTORE] Supabase phone lookup failed: {e}")
        
        logger.warning(f"[SESSION_RESTORE] ⚠️ No session for phone: {x_phone}")
        raise HTTPException(status_code=404, detail="Session not found for this phone number")

    # No valid identifier provided
    logger.error(f"[SESSION_RESTORE] ❌ No valid header provided (missing token or phone)")
    raise HTTPException(status_code=400, detail="Missing X-Session-Token or X-Phone header")


@app.post("/session/update", response_model=UpdateSessionResponse)
async def session_update(
    request: UpdateSessionRequest,
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token")
):
    """Update a session's state based on an action and payload.

    The client must provide the `X-Session-Token` header to identify the session,
    and the request body must contain `action` and a `payload` dict.
    
    PHASE 2: Automatically refreshes 7-day expiry on every activity (sliding window).
    """
    # Validate header presence
    if not x_session_token:
        raise HTTPException(status_code=400, detail="Missing X-Session-Token header")

    # Apply the requested update using our helper function
    updated_session = update_session_state(x_session_token, request.action, request.payload)

    # PHASE 2: Refresh expiry on every activity (sliding 7-day window)
    session_id = updated_session.get("session_id")
    if session_id:
        update_session_expiry_in_supabase(session_id)

    # Return the updated session
    return JSONResponse(status_code=200, content={"session": updated_session})


@app.post("/session/end")
async def session_end(x_session_token: Optional[str] = Header(None, alias="X-Session-Token")):
    """End a session by marking it as inactive.

    The session_id persists, and the same phone can restore it later.
    Session remains in memory but is marked inactive.
    
    PHASE 2: Also marks session as inactive in Supabase.
    """
    # Validate header presence
    if not x_session_token:
        raise HTTPException(status_code=400, detail="Missing X-Session-Token header")

    # Retrieve the session
    session = get_session(x_session_token)

    # Mark session as inactive (but keep it restorable)
    session["is_active"] = False
    session["updated_at"] = _now_iso()
    SESSIONS[x_session_token] = session

    # PHASE 2: Also mark as inactive in Supabase
    session_id = session.get("session_id")
    if session_id:
        delete_session_from_supabase(session_id)

    logger.info(f"Session ended: token={x_session_token}, session_id={session['session_id']}")

    return JSONResponse(status_code=200, content={
        "message": "Session ended successfully",
        "session_id": session["session_id"],
        "phone": session["phone"]
    })


# ===================================================================
# Authentication Endpoints (New - for multi-channel auth with passwords)
# ===================================================================

# Import auth manager for password-based authentication
try:
    from auth_manager import (
        create_customer,
        validate_login,
        generate_qr_token,
        verify_qr_token,
    )
    AUTH_ENABLED = True
    logger.info("Auth manager loaded successfully - password authentication enabled")
except ImportError as e:
    AUTH_ENABLED = False
    logger.warning(f"Auth manager not available: {e}")


class SignupRequest(BaseModel):
    """Request model for customer signup with password."""
    name: str = Field(..., description="Customer name")
    phone_number: str = Field(..., description="Phone number (must be unique)")
    password: str = Field(..., min_length=6, description="Password (minimum 6 characters)")
    age: Optional[int] = Field(default=None, ge=0, le=150, description="Age in years")
    gender: Optional[str] = Field(default=None, description="Gender")
    city: Optional[str] = Field(default=None, description="City")
    building_name: Optional[str] = Field(default=None, description="Building name")
    address_landmark: Optional[str] = Field(default=None, description="Address landmark")
    channel: str = Field(default="web", description="Channel: web, whatsapp, kiosk")


class PasswordLoginRequest(BaseModel):
    """Request model for password-based login."""
    phone_number: str = Field(..., description="Phone number")
    password: str = Field(..., description="Password")
    channel: str = Field(default="web", description="Channel: web, whatsapp, kiosk")


class QRInitRequest(BaseModel):
    """Request model for QR code initialization."""
    phone_number: str = Field(..., description="Phone number of logged-in user")


class QRVerifyRequest(BaseModel):
    """Request model for QR code verification."""
    qr_token: str = Field(..., description="QR token from QR code")
    channel: str = Field(default="kiosk", description="Channel: typically kiosk")


@app.post("/auth/signup")
async def auth_signup(request: SignupRequest):
    """Create a new customer account with password.
    
    This endpoint:
    1. Validates that phone number is unique
    2. Creates customer record in CSV with password hash
    3. Creates active session
    4. Returns session token and customer record
    
    Dual-channel support:
    - Web users: Must provide password
    - WhatsApp users: Can still use /session/login (password-less flow)
    """
    if not AUTH_ENABLED:
        raise HTTPException(
            status_code=501,
            detail="Password authentication not available. Auth manager not loaded."
        )
    
    # Validate required fields
    phone = (request.phone_number or "").strip()
    password = (request.password or "").strip()
    name = (request.name or "").strip()
    
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number is required")
    if not password:
        raise HTTPException(status_code=400, detail="Password is required")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    
    try:
        # Create customer with password
        customer_record = create_customer(
            name=name,
            phone=phone,
            password=password,
            age=request.age,
            gender=request.gender,
            city=request.city,
            building_name=request.building_name,
            address_landmark=request.address_landmark,
        )
        
        customer_id = customer_record.get('customer_id')
        
        # Register phone mapping
        _register_phone_mapping(phone, customer_id)
        
        # Create session
        token, session = create_session(
            phone=phone,
            channel=request.channel,
            user_id=customer_id,
            customer_id=customer_id,
            customer_profile=customer_record,
        )
        
        logger.info(f"Signup successful: customer_id={customer_id}, phone={phone}")
        
        return JSONResponse(status_code=201, content={
            "success": True,
            "message": "Account created successfully",
            "session_token": token,
            "session": session,
            "customer": customer_record,
        })
        
    except ValueError as e:
        # Customer already exists or validation error
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Signup failed")
        raise HTTPException(status_code=500, detail=f"Signup failed: {str(e)}")


@app.post("/auth/login")
async def auth_login(request: PasswordLoginRequest):
    """Authenticate with phone number and password.
    
    This endpoint:
    1. Validates phone + password credentials
    2. Retrieves customer record
    3. Creates or restores session
    4. Returns session token
    
    Note: WhatsApp users without passwords should continue using /session/login
    """
    if not AUTH_ENABLED:
        raise HTTPException(
            status_code=501,
            detail="Password authentication not available. Auth manager not loaded."
        )
    
    phone = (request.phone_number or "").strip()
    password = (request.password or "").strip()
    
    if not phone or not password:
        raise HTTPException(status_code=400, detail="Phone and password are required")
    
    try:
        # Validate credentials
        success, customer = validate_login(phone, password)
        
        if not success:
            raise HTTPException(status_code=401, detail="Invalid phone number or password")
        
        customer_id = customer.get('customer_id')
        
        # Register phone mapping
        _register_phone_mapping(phone, customer_id)
        
        # Create or restore session
        token, session = create_session(
            phone=phone,
            channel=request.channel,
            user_id=customer_id,
            customer_id=customer_id,
            customer_profile=customer,
        )
        
        logger.info(f"Login successful: customer_id={customer_id}, phone={phone}")
        
        return JSONResponse(status_code=200, content={
            "success": True,
            "message": "Login successful",
            "session_token": token,
            "session": session,
            "customer": customer,
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Login failed")
        raise HTTPException(status_code=500, detail=f"Login failed: {str(e)}")


@app.post("/auth/logout")
async def auth_logout(x_session_token: Optional[str] = Header(None, alias="X-Session-Token")):
    """Logout by invalidating the current session token.
    
    This endpoint:
    1. Validates session token
    2. Marks session as inactive
    3. Removes token from lookup tables
    4. Returns success message
    
    Note: Session data is preserved in memory but marked inactive.
    User must login again to get a new token.
    """
    if not x_session_token:
        raise HTTPException(status_code=400, detail="Missing X-Session-Token header")
    
    # Check if session exists
    if x_session_token not in SESSIONS:
        raise HTTPException(status_code=404, detail="Session not found or already logged out")
    
    session = SESSIONS[x_session_token]
    
    # Mark session as inactive
    session["is_active"] = False
    session["updated_at"] = _now_iso()
    
    # Remove from phone lookup
    phone = session.get("phone")
    if phone and PHONE_SESSIONS.get(phone) == x_session_token:
        del PHONE_SESSIONS[phone]
    
    # Remove from telegram lookup if exists
    telegram_chat_id = session.get("telegram_chat_id")
    if telegram_chat_id and TELEGRAM_SESSIONS.get(telegram_chat_id) == x_session_token:
        del TELEGRAM_SESSIONS[telegram_chat_id]
    
    logger.info(f"Logout successful: token={x_session_token}, phone={phone}")
    
    return JSONResponse(status_code=200, content={
        "success": True,
        "message": "Logged out successfully",
        "session_id": session["session_id"],
    })


@app.post("/auth/qr-init")
async def auth_qr_init(
    request: QRInitRequest,
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token")
):
    """Generate a QR code token for kiosk authentication.
    
    This endpoint:
    1. Validates user is logged in (via session token)
    2. Generates a secure QR token (expires in 15 minutes)
    3. Returns token for QR code generation
    
    Usage flow:
    1. User logs in on website
    2. User requests QR code
    3. QR code displays token
    4. Kiosk scans QR code
    5. Kiosk calls /auth/qr-verify with token
    """
    if not AUTH_ENABLED:
        raise HTTPException(
            status_code=501,
            detail="QR authentication not available. Auth manager not loaded."
        )
    
    if not x_session_token:
        raise HTTPException(status_code=401, detail="Must be logged in to generate QR code")
    
    # Verify session exists and is active
    if x_session_token not in SESSIONS:
        raise HTTPException(status_code=401, detail="Invalid session")
    
    session = SESSIONS[x_session_token]
    
    if not session.get("is_active"):
        raise HTTPException(status_code=401, detail="Session is not active")
    
    phone = request.phone_number.strip()
    customer_id = session.get("customer_id") or session.get("user_id")
    
    if not customer_id:
        raise HTTPException(status_code=400, detail="Customer ID not found in session")
    
    try:
        # Generate QR token
        qr_token = generate_qr_token(phone, customer_id)
        
        logger.info(f"QR token generated for customer {customer_id}")
        
        return JSONResponse(status_code=200, content={
            "success": True,
            "qr_token": qr_token,
            "customer_id": customer_id,
        })
        
    except Exception as e:
        logger.exception("QR token generation failed")
        raise HTTPException(status_code=500, detail=f"Failed to generate QR token: {str(e)}")


@app.post("/auth/qr-verify")
async def auth_qr_verify(request: QRVerifyRequest):
    """Verify a QR code token and create a kiosk session.
    
    This endpoint:
    1. Validates QR token
    2. Retrieves customer info from token
    3. Creates new kiosk session
    4. Returns session token for kiosk
    
    Usage flow:
    1. Kiosk scans QR code
    2. Kiosk extracts token from QR
    3. Kiosk calls this endpoint
    4. Kiosk receives session token
    5. Kiosk can now make authenticated requests
    """
    if not AUTH_ENABLED:
        raise HTTPException(
            status_code=501,
            detail="QR authentication not available. Auth manager not loaded."
        )
    
    qr_token = (request.qr_token or "").strip()
    
    if not qr_token:
        raise HTTPException(status_code=400, detail="QR token is required")
    
    try:
        # Verify QR token
        valid, customer_info = verify_qr_token(qr_token)
        
        if not valid:
            raise HTTPException(status_code=401, detail="Invalid QR token")
        
        phone = customer_info['phone']
        customer_id = customer_info['customer_id']
        
        # Retrieve customer record
        from db.repositories.customer_repo import get_customer_by_phone
        try:
            customer_record = get_customer_by_phone(phone)
        except:
            # Fallback: load from CSV
            import pandas as pd
            df = pd.read_csv(Path(__file__).parent / "data" / "customers.csv")
            matches = df[df['phone_number'].astype(str) == str(phone)]
            if len(matches) > 0:
                customer_record = matches.iloc[0].to_dict()
            else:
                customer_record = {"customer_id": customer_id, "phone_number": phone}
        
        # Create kiosk session
        token, session = create_session(
            phone=phone,
            channel=request.channel,
            user_id=customer_id,
            customer_id=customer_id,
            customer_profile=customer_record,
        )
        
        logger.info(f"QR verification successful: customer_id={customer_id}, channel={request.channel}")
        
        return JSONResponse(status_code=200, content={
            "success": True,
            "message": "QR authentication successful",
            "session_token": token,
            "session": session,
            "customer": customer_record,
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("QR verification failed")
        raise HTTPException(status_code=500, detail=f"QR verification failed: {str(e)}")


# ===================================================================
# Sales Agent Memory Endpoints (Phase 3 - for contextual selling)
# ===================================================================

@app.get("/session/{session_id}/context")
async def get_session_context(session_id: str):
    """Retrieve previous chat context for a session.
    
    Used by sales agent to:
    - Know what products customer looked at before
    - Know what customer asked about
    - Provide contextual follow-ups
    
    Returns:
    - chat_context: List of all messages in this session
    - conversation_summary: High-level summary
    - last_recommended_skus: Products previously recommended
    """
    try:
        # Find session by session_id
        session = None
        for token, sess in SESSIONS.items():
            if sess.get("session_id") == session_id:
                session = sess
                break
        
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        data = session.get("data", {})
        
        return {
            "session_id": session_id,
            "customer_id": session.get("customer_id"),
            "phone": session.get("phone"),
            "channel": session.get("channel"),
            "chat_context": data.get("chat_context", []),
            "conversation_summary": data.get("conversation_summary", ""),
            "last_recommended_skus": data.get("last_recommended_skus", []),
            "created_at": session.get("created_at"),
            "updated_at": session.get("updated_at"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get session context: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/session/{session_id}/summary")
async def get_session_summary(session_id: str):
    """Retrieve conversation summary for a session.
    
    Used by sales agent to quickly understand:
    - What customer bought/wants
    - What objections exist
    - What follow-up needed
    """
    try:
        session = None
        for token, sess in SESSIONS.items():
            if sess.get("session_id") == session_id:
                session = sess
                break
        
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        data = session.get("data", {})
        
        return {
            "session_id": session_id,
            "customer_id": session.get("customer_id"),
            "summary": data.get("conversation_summary", ""),
            "total_messages": len(data.get("chat_context", [])),
            "last_updated": session.get("updated_at"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get session summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/session/{session_id}/recommendations")
async def get_previous_recommendations(session_id: str):
    """Retrieve previously recommended products for a session.
    
    Used by sales agent to:
    - Avoid repeating recommendations
    - Build on previous interest
    - Suggest complementary products
    """
    try:
        session = None
        for token, sess in SESSIONS.items():
            if sess.get("session_id") == session_id:
                session = sess
                break
        
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        data = session.get("data", {})
        
        return {
            "session_id": session_id,
            "customer_id": session.get("customer_id"),
            "last_recommended_skus": data.get("last_recommended_skus", []),
            "total_recommendations": len(data.get("last_recommended_skus", [])),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get recommendations: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/session/{session_id}/cart")
async def get_session_cart(session_id: str):
    """Retrieve current cart for a session.
    
    Used by sales agent to:
    - Know what customer is considering
    - Make relevant suggestions
    - Reference items in conversation
    """
    try:
        session = None
        for token, sess in SESSIONS.items():
            if sess.get("session_id") == session_id:
                session = sess
                break
        
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        data = session.get("data", {})
        
        return {
            "session_id": session_id,
            "customer_id": session.get("customer_id"),
            "cart": data.get("cart", []),
            "cart_size": len(data.get("cart", [])),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get cart: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/session/{session_id}/summary")
async def update_session_summary(session_id: str, summary: str = Body(..., embed=True)):
    """Update conversation summary after selling interaction.
    
    Called by sales agent after every 5 messages to
    update the conversation summary for future reference.
    """
    try:
        session = None
        for token, sess in SESSIONS.items():
            if sess.get("session_id") == session_id:
                session = sess
                break
        
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        if "data" not in session:
            session["data"] = {}
        
        session["data"]["conversation_summary"] = summary
        session["updated_at"] = _now_iso()
        
        # Also update Supabase if enabled
        try:
            update_session_expiry_in_supabase(session_id)
        except Exception as supabase_err:
            logger.debug(f"Supabase update failed (continuing): {supabase_err}")
        
        logger.info(f"Updated summary for session {session_id}")
        
        return {
            "success": True,
            "session_id": session_id,
            "summary": summary,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Allow this module to be executed directly for local development
if __name__ == "__main__":
    # Run uvicorn with this module's `app` object
    # Use "__main__:app" when running directly, not "backend.session_manager:app"
    uvicorn.run("__main__:app", host="0.0.0.0", port=8000, reload=True)

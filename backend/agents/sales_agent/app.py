"""
Sales Agent FastAPI Application with LangGraph + Vertex AI

A production-ready sales agent that uses:
- Vertex AI (Gemini) for intelligent intent detection
- LangGraph for workflow orchestration
- Microservice architecture for business logic

"""

import logging
import os
import uuid
from typing import Optional, Dict, Any, List
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request, status, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import requests
import httpx
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
import uvicorn
from dotenv import load_dotenv
import redis
import sys

# Load environment variables from .env file
load_dotenv(Path(__file__).parent / '.env')

# Add backend to path for Supabase client
backend_path = Path(__file__).resolve().parent.parent.parent
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

# Import Supabase client
from db import supabase_client

# Import LangGraph Sales Agent (absolute import for direct uvicorn execution)
from sales_graph import process_message as process_with_langgraph

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Initialize Redis client for reservation locks (after logger is configured)
redis_client = None
REDIS_URL = os.getenv("REDIS_URL")
if REDIS_URL:
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=5)
        redis_client.ping()
        logger.info("✓ Redis connected for reservation locks")
    except Exception as e:
        logger.warning(f"⚠ Redis connection failed: {e} - reservations will fail")
        redis_client = None

PAYMENT_SERVICE_URL = os.getenv("PAYMENT_URL", "http://localhost:8003")

# Initialize FastAPI app
app = FastAPI(
    title="Sales Agent API with LangGraph + Vertex AI",
    description="Intelligent sales agent powered by Vertex AI intent detection and LangGraph workflow",
    version="2.0.0"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    """Health endpoint to verify the Sales Agent service is up."""
    try:
        # Lightweight checks: langgraph module availability
        ready = True
        return JSONResponse(status_code=200, content={"status": "healthy", "ready": ready})
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(status_code=500, content={"status": "unhealthy", "error": str(e)})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    logger.warning(f"Validation error: {exc}")
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"status": "error", "message": str(exc)})


@app.exception_handler(Exception)
async def generic_error_handler(request, exc):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"status": "error", "message": "Internal server error"})


# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class MessageRequest(BaseModel):
    """Request model for user messages."""
    message: str = Field(..., min_length=1, description="User message to the sales agent")
    session_token: Optional[str] = Field(None, description="Session token for conversation continuity")
    metadata: dict = Field(default_factory=dict, description="Additional metadata")

    class Config:
        json_schema_extra = {
            "example": {
                "message": "Show me running shoes under 3000",
                "session_token": "abc123-def456",
                "metadata": {"user_id": "user_001", "source": "web"}
            }
        }


class AgentResponse(BaseModel):
    """Response model with intent information and cards."""
    reply: str = Field(..., description="Agent's response message")
    session_token: str = Field(..., description="Session token for tracking conversation")
    timestamp: str = Field(..., description="Response timestamp")
    metadata: dict = Field(default_factory=dict, description="Additional response metadata")
    intent_info: Optional[dict] = Field(None, description="Intent detection information")
    cards: List[dict] = Field(default_factory=list, description="Product cards or visual elements")


class PostPaymentRequest(BaseModel):
    """Request model for post-payment processing after successful Razorpay payment."""
    order_id: str = Field(..., description="Order ID that was successfully paid")
    customer_id: str = Field(..., description="Customer ID who made the payment")
    session_token: str = Field(..., description="Session token for conversation continuity")
    amount_paid: float = Field(..., description="Amount that was successfully paid")
    payment_id: str = Field(..., description="Razorpay payment ID")
    transaction_id: Optional[str] = Field(None, description="Transaction ID from payment agent")

    class Config:
        json_schema_extra = {
            "example": {
                "order_id": "ORD000961",
                "customer_id": "user_001",
                "session_token": "abc123-def456",
                "amount_paid": 2999.00,
                "payment_id": "pay_1234567890",
                "transaction_id": "txn_1234567890"
            }
        }


# ============================================================================
# MIDDLEWARE
# ============================================================================

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log incoming requests and responses."""
    logger.info(f"📨 {request.method} {request.url.path}")
    
    if request.method in ["POST", "PUT", "PATCH"]:
        try:
            body = await request.body()
            content_type = request.headers.get("content-type", "")
            if content_type.startswith("multipart/form-data"):
                logger.debug("Request body: <multipart/form-data>")
            else:
                logger.debug(f"Request body: {body.decode('utf-8', errors='ignore')[:500]}")
            
            # Store body for route handler
            async def receive():
                return {"type": "http.request", "body": body}
            request._receive = receive
        except Exception as e:
            logger.error(f"Error reading request body: {e}")
    
    response = await call_next(request)
    logger.info(f"✅ Response: {response.status_code}")
    
    return response


# ============================================================================
# EXCEPTION HANDLERS
# ============================================================================

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle validation errors."""
    logger.error(f"❌ Validation error: {exc.errors()}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "Validation Error",
            "detail": exc.errors(),
            "message": "Invalid request payload"
        }
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle unexpected errors."""
    logger.error(f"❌ Unhandled exception: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Internal Server Error",
            "message": "An unexpected error occurred. Please try again later."
        }
    )


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/")
async def root():
    """Root endpoint - service info."""
    return {
        "status": "running",
        "service": "Sales Agent with LangGraph + Vertex AI",
        "version": "2.0.0",
        "features": ["Vertex AI Intent Detection", "LangGraph Workflow", "Microservice Integration"]
    }


@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring."""
    return {
        "status": "healthy",
        "service": "Sales Agent with LangGraph + Vertex AI",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/api/chat-summary")
async def get_chat_summary(session_token: str, mode: str = "whatsapp"):
    """
    Generate AI-powered chat summary using Groq for WhatsApp/Kiosk channels.
    Matches Telegram's warm, engaging summary style.
    
    Args:
        session_token: Session token to fetch context for
        mode: 'whatsapp' (second person) or 'kiosk' (third person)
        
    Returns:
        AI-generated summary of customer's shopping journey
    """
    logger.info(f"💬 Generating {mode} chat summary for token: {session_token[:20]}...")
    
    try:
        import httpx
        
        # Fetch session data
        sess_resp = requests.get(
            "http://localhost:8000/session/restore",
            headers={"X-Session-Token": session_token},
            timeout=8
        )
        
        if sess_resp.status_code != 200:
            return JSONResponse(
                status_code=404,
                content={"error": "Session not found", "has_summary": False}
            )
        
        sess = sess_resp.json().get("session", {})
        session_data = sess.get("data", {})
        conversation_history = session_data.get("chat_context", [])
        cart = session_data.get("cart", [])
        summary = session_data.get("conversation_summary", "")
        
        # Extract customer name from multiple possible locations
        customer_name = (
            sess.get("customer_name") or 
            session_data.get("customer_profile", {}).get("name") or
            session_data.get("customer_name") or
            "Customer"
        )
        customer_id = sess.get("customer_id") or session_data.get("customer_id")
        
        # Fetch REAL loyalty data from Loyalty Service
        loyalty_tier = "Member"
        loyalty_points = 0
        if customer_id:
            try:
                logger.info(f"🏆 Fetching real loyalty data for customer {customer_id}...")
                loyalty_resp = requests.get(
                    f"http://localhost:8002/loyalty/tier/{customer_id}",
                    timeout=5
                )
                if loyalty_resp.status_code == 200:
                    loyalty_data = loyalty_resp.json()
                    loyalty_tier = loyalty_data.get("tier", "Member")
                    loyalty_points = loyalty_data.get("points", 0)
                    logger.info(f"✅ Loyalty data fetched: {loyalty_tier}, {loyalty_points} points")
                else:
                    logger.warning(f"⚠️ Loyalty service returned {loyalty_resp.status_code}, using defaults")
            except Exception as loyalty_err:
                logger.warning(f"⚠️ Failed to fetch loyalty data: {loyalty_err}, using defaults")
        
        # Check if there's meaningful context
        has_context = bool(summary or cart or len(conversation_history) > 0)
        
        if not has_context:
            return {
                "has_summary": False,
                "message": "No previous interaction found"
            }
        
        # Keep last 6 turns only
        recent_history = conversation_history[-6:]
        
        # Extract product names from chat history (from cards metadata)
        viewed_products = []
        for msg in conversation_history:
            if msg.get("metadata") and msg["metadata"].get("cards"):
                for card in msg["metadata"]["cards"]:
                    product_name = card.get("name")
                    if product_name and product_name not in viewed_products:
                        viewed_products.append(product_name)
        
        # Limit to last 5 unique products
        viewed_products = viewed_products[-5:]
        products_viewed_text = ", ".join(viewed_products) if viewed_products else "various products"
        
        # Build cart summary
        cart_summary = []
        for item in cart:
            cart_summary.append(
                f"{item.get('name')} (₹{item.get('price')} x {item.get('quantity', 1)})"
            )
        cart_text = ", ".join(cart_summary) if cart_summary else "No items in cart"
        
        # Build POV-specific prompts (matching Telegram's style)
        if mode == "kiosk":
            # Third person for kiosk (sales staff view)
            system_prompt = """
You are a retail intelligence assistant for sales staff at a premium fashion brand.
Provide THIRD PERSON summaries of customer shopping behavior.
Be professional, actionable, and insightful.
Speak about "the customer" or "they" or use their name.
Keep it under 120 words.
Focus on what staff should know to provide excellent service.
"""
            user_prompt = f"""
Customer Name: {customer_name}

Products They Viewed: {products_viewed_text}

Conversation Summary: {summary}

Recent Chat History:
{recent_history}

Cart Items:
{cart_text}

Loyalty Tier: {loyalty_tier}
Loyalty Points: {loyalty_points}

Create a third-person customer intelligence summary for sales staff.
Mention:
- SPECIFIC product names they were exploring (use exact names from "Products They Viewed")
- Current cart status (if any items)
- Their loyalty tier and points balance
- Key preferences or patterns noticed
- Recommended approach to assist them

Use a professional but warm tone. Make it actionable for staff.
"""
        else:
            # Second person for WhatsApp (customer view - EXACT SAME AS TELEGRAM)
            system_prompt = """
You are a premium fashion sales assistant for a luxury brand.
Your tone is warm, elegant, empathetic, and persuasive.
Speak in SECOND PERSON.
Be welcoming, stylish, and emotionally engaging.
Focus on making the customer feel valued and excited about their shopping journey.

"""
            user_prompt = f"""
Customer Name: {customer_name}

Products They Viewed: {products_viewed_text}

Conversation Summary: {summary}

Recent Chat History:
{recent_history}

Cart Items:
{cart_text}

Loyalty Tier: {loyalty_tier}
Loyalty Points: {loyalty_points}

Create a personalized welcome-back summary.
Mention:
- SPECIFIC product names they were exploring (use exact names from "Products They Viewed")
- Cart reminder (if any)
- Their loyalty tier
- Encourage continuation
- Ask a soft engaging question

IMPORTANT: Use the actual product names from "Products They Viewed" list. Be specific like "blue track pants" or "Women Boat-Neck Wildberry Sweatshirt".
"""
        
        # Call Groq API
        groq_api_key = os.getenv("GROQ_API_KEY")
        if not groq_api_key:
            # Fallback to simple summary
            fallback = f"Welcome back {customer_name}! " if mode == "whatsapp" else f"Customer {customer_name} "
            if cart:
                fallback += f"has {len(cart)} item(s) in cart. "
            if summary:
                fallback += summary[:100]
            return {
                "has_summary": True,
                "summary": fallback,
                "mode": mode
            }
        
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {groq_api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.7
                }
            )
            
            response.raise_for_status()
            result = response.json()
            summary_text = result["choices"][0]["message"]["content"].strip()
            
            logger.info(f"✅ Generated {mode} summary using Groq")
            return {
                "has_summary": True,
                "summary": summary_text,
                "mode": mode,
                "cart_count": len(cart),
                "loyalty_tier": loyalty_tier,
                "loyalty_points": loyalty_points
            }
            
    except Exception as e:
        logger.error(f"❌ Failed to generate chat summary: {e}")
        # Fallback
        fallback = "Welcome back!" if mode == "whatsapp" else "Customer has previous interaction."
        return {
            "has_summary": True,
            "summary": fallback,
            "mode": mode,
            "error": str(e)
        }


@app.get("/api/customer-context")
async def get_customer_context(session_token: str):
    """
    Get customer context summary for Kiosk display.
    Sales staff can call this to see customer history before interaction.
    
    Args:
        session_token: Session token to fetch context for
        
    Returns:
        Customer context including summary, cart, last actions
    """
    logger.info(f"📋 Fetching customer context for token: {session_token[:20]}...")
    
    try:
        # Fetch session data
        sess_resp = requests.get(
            "http://localhost:8000/session/restore",
            headers={"X-Session-Token": session_token},
            timeout=8
        )
        
        if sess_resp.status_code != 200:
            return JSONResponse(
                status_code=404,
                content={"error": "Session not found", "has_context": False}
            )
        
        sess = sess_resp.json().get("session", {})
        session_data = sess.get("data", {})
        conversation_history = session_data.get("chat_context", [])
        
        # Extract metadata
        summary = session_data.get("conversation_summary", "")
        cart = session_data.get("cart", [])
        last_action = session_data.get("last_action")
        last_skus = session_data.get("last_recommended_skus", [])
        channels = session_data.get("channels", [])
        
        # Check if there's meaningful context
        has_context = bool(summary or cart or len(conversation_history) > 0)
        
        if not has_context:
            return {
                "has_context": False,
                "message": "No previous interaction found"
            }
        
        # Build context response
        context = {
            "has_context": True,
            "summary": {
                "text": summary or "Customer is just starting their shopping journey.",
                "cart_items": len(cart),
                "interactions": len(conversation_history),
                "last_action": last_action or "browsing",
                "previous_channels": channels
            },
            "cart": cart[:10],  # First 10 items
            "last_products_viewed": last_skus[:10],
            "customer_info": {
                "phone": sess.get("phone"),
                "customer_id": sess.get("customer_id"),
                "session_duration": len(conversation_history),
                "active_since": sess.get("created_at", "unknown")
            },
            "recommendations": {
                "should_upsell": len(cart) > 0,
                "should_complete_checkout": len(cart) > 2,
                "interests": session_data.get("recent", [])[:5]
            }
        }
        
        logger.info(f"✅ Context fetched: {len(cart)} items, {len(conversation_history)} messages")
        return context
        
    except Exception as e:
        logger.error(f"❌ Failed to fetch customer context: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to fetch context", "has_context": False}
        )


@app.post("/api/visual-search")
async def visual_search(image: UploadFile = File(...)):
    """
    Proxy visual search uploads to Ambient Commerce agent.
    Expects a multipart form field named "image".
    """
    ambient_url = os.getenv("AMBIENT_COMMERCE_URL", "http://localhost:8017")

    try:
        image_bytes = await image.read()
        files = {
            "file": (image.filename, image_bytes, image.content_type or "application/octet-stream")
        }

        response = requests.post(
            f"{ambient_url}/search/upload",
            files=files,
            timeout=60
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"❌ Visual search failed: {e}")
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={
                "success": False,
                "message": "Visual search failed. Please try again.",
                "error": str(e)
            }
        )


class CheckoutRequest(BaseModel):
    """Request model for checkout."""
    customer_id: str = Field(..., description="Customer ID")
    items: List[Dict[str, Any]] = Field(..., description="List of items to purchase")
    payment_method: Dict[str, Any] = Field(..., description="Payment method details")
    shipping_address: Dict[str, Any] = Field(..., description="Shipping address")
    session_token: Optional[str] = Field(None, description="Session token")


@app.post("/api/checkout")
async def handle_checkout(request: CheckoutRequest):
    """
    Complete checkout flow: inventory -> payment -> fulfillment.
    
    This endpoint orchestrates the full purchase flow across all microservices:
    1. Verify inventory availability
    2. Create inventory holds
    3. Process payment
    4. Start fulfillment
    5. Persist order record
    
    Args:
        request: CheckoutRequest with customer, items, payment, and shipping info
        
    Returns:
        Order completion status with order_id and fulfillment details
    """
    logger.info(f"🛒 Checkout initiated for customer: {request.customer_id}")
    
    try:
        # Import the agent client
        from agent_client import SalesAgentClient
        
        # Create agent instance
        agent = SalesAgentClient()
        
        # Execute complete purchase flow
        result = await agent.complete_purchase_flow(
            customer_id=request.customer_id,
            items=request.items,
            payment_method=request.payment_method,
            shipping_address=request.shipping_address
        )
        
        logger.info(f"✅ Checkout completed: {result['status']} - Order: {result.get('order_id')}")
        
        # Return formatted response
        return {
            "status": result['status'],
            "order_id": result.get('order_id'),
            "steps": result.get('steps', {}),
            "message": "Order placed successfully" if result['status'] == 'completed' else f"Order {result['status']}"
        }
        
    except Exception as e:
        logger.error(f"❌ Checkout failed: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "error": str(e),
                "message": "Failed to process checkout"
            }
        )


@app.post("/api/post-payment")
async def handle_post_payment(request: PostPaymentRequest):
    """
    Handle post-payment processing after successful Razorpay payment verification.

    This endpoint is called by the frontend after successful payment verification
    to trigger the full post-payment agent workflow:
    1. Start fulfillment processing
    2. Notify post-purchase agent
    3. Trigger stylist analysis
    4. Update inventory

    Note: Payment success message is generated and sent by the Payment Agent separately

    Args:
        request: PostPaymentRequest with verified payment details

    Returns:
        Processing status and agent responses
    """
    logger.info(f"💰 Post-payment processing for order: {request.order_id}")

    try:
        # Create a mock state for the fulfillment worker
        from sales_graph import SalesAgentState

        state = SalesAgentState(
            message=f"Payment completed for order {request.order_id}",
            session_token=request.session_token,
            metadata={
                "order_id": request.order_id,
                "customer_id": request.customer_id,
                "source": "post_payment",
                "action": "start_processing",
                "trigger_agents": ["fulfillment", "post_purchase", "stylist", "inventory"],
                "amount_paid": request.amount_paid,
                "payment_id": request.payment_id,
                "transaction_id": request.transaction_id
            },
            intent="support",  # Routes to fulfillment_worker
            confidence=1.0,
            entities={
                "order_id": request.order_id,
                "customer_id": request.customer_id,
                "source": "post_payment",
                "action": "start_processing",
                "trigger_agents": ["fulfillment", "post_purchase", "stylist", "inventory"]
            },
            intent_method="post_payment_trigger",
            response="",
            cards=[],
            worker_service="",
            worker_url="",
            error=""
        )

        # Import and call the fulfillment worker directly
        from sales_graph import call_fulfillment_worker
        result_state = await call_fulfillment_worker(state)

        logger.info(f"✅ Post-payment processing completed for order {request.order_id}")

        response_message = ""
        if isinstance(result_state, dict):
            response_message = (
                result_state.get("response")
                or result_state.get("message")
                or "Order processing started"
            )
        else:  # Fallback for TypedDict-like objects
            response_message = getattr(result_state, "response", "Order processing started")

        return {
            "status": "success",
            "order_id": request.order_id,
            "message": response_message,
            "processing_started": True,
            "agents_triggered": ["fulfillment", "post_purchase", "stylist", "inventory"]
        }

    except Exception as e:
        logger.error(f"❌ Post-payment processing failed: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "error": str(e),
                "message": "Failed to process post-payment workflow"
            }
        )


@app.api_route("/api/payment/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_payment_requests(path: str, request: Request):
    """Proxy all payment requests through the Sales Agent."""
    target_url = f"{PAYMENT_SERVICE_URL}/payment/{path}"
    try:
        body = await request.body()
        params = dict(request.query_params)
        headers = {}
        content_type = request.headers.get("content-type")
        if content_type:
            headers["content-type"] = content_type

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(
                request.method,
                target_url,
                params=params,
                content=body if body else None,
                headers=headers,
            )

        try:
            payload = response.json()
            return JSONResponse(status_code=response.status_code, content=payload)
        except ValueError:
            return JSONResponse(
                status_code=response.status_code,
                content={"raw": response.text},
            )
    except httpx.TimeoutException:
        return JSONResponse(
            status_code=504,
            content={"error": "Payment service timeout"},
        )
    except httpx.ConnectError:
        return JSONResponse(
            status_code=503,
            content={"error": "Cannot connect to payment service"},
        )
    except Exception as e:
        logger.error(f"❌ Payment proxy failed: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": "Payment proxy failed", "detail": str(e)},
        )


@app.post("/api/message", response_model=AgentResponse)
async def handle_message(request: MessageRequest):
    """
    Handle incoming user messages using LangGraph + Vertex AI workflow.
    
    Flow:
        1. User message received
        2. Fetch conversation history from session
        3. Run LangGraph workflow:
           - Intent Detection (Vertex AI)
           - Router (based on intent)
           - Worker Microservice Call
        4. Return structured response to frontend
    
    Args:
        request: MessageRequest with user message and metadata
        
    Returns:
        AgentResponse with reply, intent info, and product cards
    """
    logger.info(f"📨 Message: '{request.message[:100]}...'" )

    # Generate or reuse session token
    session_token = request.session_token or str(uuid.uuid4())
    
    # Fetch conversation history and session data for context
    conversation_history = []
    enhanced_metadata = request.metadata.copy() if request.metadata else {}
    session_metadata = {}
    has_previous_summary = False
    
    if request.session_token:
        try:
            sess_resp = requests.get(
                "http://localhost:8000/session/restore",
                headers={"X-Session-Token": request.session_token},
                timeout=8
            )
            if sess_resp.status_code == 200:
                sess = sess_resp.json().get("session", {})
                conversation_history = sess.get("data", {}).get("chat_context", [])
                
                # Extract session metadata for persuasion engine
                session_data = sess.get("data", {})
                session_metadata = {
                    "conversation_summary": session_data.get("conversation_summary", ""),
                    "last_action": session_data.get("last_action"),
                    "last_recommended_skus": session_data.get("last_recommended_skus", []),
                    "cart": session_data.get("cart", []),
                    "recent": session_data.get("recent", []),
                    "channels": session_data.get("channels", []),
                    "has_summary": bool(session_data.get("conversation_summary"))
                }
                has_previous_summary = bool(session_data.get("conversation_summary"))
                
                # Enhance metadata with session data
                enhanced_metadata["phone"] = sess.get("phone")
                enhanced_metadata["user_id"] = sess.get("user_id")
                enhanced_metadata["session_id"] = sess.get("session_id")
                enhanced_metadata["customer_id"] = sess.get("customer_id")
                enhanced_metadata["session_metadata"] = session_metadata

                # Restore recommendation clarification context from last agent message
                for msg in reversed(conversation_history):
                    if msg.get("sender") == "agent":
                        msg_meta = msg.get("metadata") or {}
                        if "recommendation_context" in msg_meta:
                            enhanced_metadata["recommendation_context"] = msg_meta["recommendation_context"]
                            enhanced_metadata["awaiting_recommendation_clarification"] = msg_meta.get("awaiting_recommendation_clarification", False)
                            logger.info(f"🔄 Restored recommendation_context (mode={msg_meta['recommendation_context'].get('mode')}, awaiting={msg_meta.get('awaiting_recommendation_clarification')})")
                        break
                
                logger.info(f"📚 Retrieved {len(conversation_history)} conversation turns")
                logger.info(f"📞 Session phone: {sess.get('phone')}")
                
                # Get channel from session
                channel = sess.get("channel", "web")
                enhanced_metadata["channel"] = channel
                
                if has_previous_summary:
                    previous_channels = session_metadata.get("channels", [])
                    summary = session_metadata.get("conversation_summary", "")
                    
                    logger.info(f"🔄 Session restored with summary: {summary[:80]}...")
                    logger.info(f"📱 Current channel: '{channel}', Previous channels: {previous_channels}")
                    logger.info(f"💾 Has summary: {bool(summary)}, Conv history length: {len(conversation_history)}")
                    
                    # For KIOSK channel with summary, show restoration on first kiosk interaction
                    # Only trigger if this looks like a cross-channel restoration
                    if channel == "kiosk" and summary:
                        logger.info(f"🖥️  Kiosk with summary detected - checking if restoration needed")
                        
                        # Show restoration if: coming from another channel OR very few messages on kiosk
                        should_restore = (
                            (previous_channels and previous_channels[-1] != "kiosk") or  # Different channel last time
                            len(conversation_history) <= 8  # Still early in conversation
                        )
                        
                        if should_restore:
                            logger.info(f"✅ Kiosk restoration will be triggered")
                            enhanced_metadata["is_kiosk_restoration"] = True
                        else:
                            logger.info(f"⏭️  Skipping restoration (already done or too many messages)")
        except Exception as e:
            logger.warning(f"⚠️  Could not fetch conversation history: {e}")
    
    try:
        # Execute LangGraph workflow
        logger.info("🔄 Running LangGraph workflow...")
        result = await process_with_langgraph(
            message=request.message,
            session_token=session_token,
            metadata=enhanced_metadata,
            conversation_history=conversation_history
        )
        
        # For Kiosk channel, prepare summary section for sales staff
        final_response = result["response"]
        kiosk_summary_section = None
        
        current_channel = enhanced_metadata.get("channel", "web")
        if current_channel == "kiosk" and has_previous_summary:
            logger.info("🖥️  Preparing Kiosk summary section for sales staff...")
            
            summary = session_metadata.get("conversation_summary", "")
            cart_items = session_metadata.get("cart", [])
            last_action = session_metadata.get("last_action", "browsing")
            last_skus = session_metadata.get("last_recommended_skus", [])
            previous_channels = session_metadata.get("channels", [])
            
            # Build structured summary for Kiosk display
            kiosk_summary_section = {
                "type": "customer_context",
                "title": "Customer Context",
                "summary": summary,
                "details": {
                    "cart_items": len(cart_items),
                    "last_action": last_action,
                    "products_viewed": len(last_skus),
                    "previous_channels": previous_channels,
                    "interaction_count": len(conversation_history)
                },
                "cart": cart_items[:5],  # First 5 cart items
                "last_recommended": last_skus[:5]  # First 5 SKUs
            }
            
            logger.info(f"✅ Kiosk summary prepared: {len(cart_items)} items, {last_action} action")
            
            # Only prepend welcome message on first kiosk interaction
            if enhanced_metadata.get("is_kiosk_restoration"):
                logger.info("🖥️  Generating Kiosk welcome message with Groq...")
                try:
                    from engine import generate_response
                    
                    restoration_prompt = f"""You are greeting a returning customer on a Kiosk screen.

Previous Interaction Summary:
{summary}

Cart Status: {len(cart_items)} items
Last Action: {last_action}

Generate a BRIEF (1-2 sentences) friendly welcome-back message that:
1. Acknowledges their previous interaction naturally
2. Mentions specific interests from summary
3. Sounds warm and consultative

Example: "Welcome back! I see you were exploring running shoes earlier."

Generate ONLY the welcome message (no extra text):"""
                    
                    # Generate restoration message using Groq
                    restoration_msg = generate_response(
                        user_message=restoration_prompt,
                        conversation_history=[],
                        session_metadata={}
                    )
                    
                    # Prepend restoration message to actual response
                    final_response = f"{restoration_msg}\n\n{result['response']}"
                    logger.info(f"✅ Kiosk welcome message prepended")
                    
                except Exception as e:
                    logger.error(f"❌ Failed to generate welcome message: {e}")
        
        # Format response for frontend
        response_metadata = {
            "processed": True,
            "worker": result["worker"],
            "original_metadata": request.metadata,
            "has_previous_summary": has_previous_summary,
            "channel": enhanced_metadata.get("channel", "web")
        }
        
        # Add summary for Kiosk channel
        if kiosk_summary_section:
            response_metadata["kiosk_summary"] = kiosk_summary_section
            response_metadata["summary"] = session_metadata.get("conversation_summary", "")
        
        response = AgentResponse(
            reply=final_response,
            session_token=session_token,
            timestamp=result["timestamp"],
            metadata=response_metadata,
            intent_info={
                "intent": result["intent"],
                "confidence": result["confidence"],
                "entities": result["entities"],
                "method": result["method"]
            },
            cards=result.get("cards", [])
        )
        
        logger.info(
            f"✅ Response generated via {result['worker']} "
            f"(intent: {result['intent']}, confidence: {result['confidence']:.2f})"
        )
        
        # Save to session if available
        if request.session_token:
            try:
                base_headers = {"X-Session-Token": request.session_token}

                requests.post(
                    "http://localhost:8000/session/update",
                    headers=base_headers,
                    json={
                        "action": "chat_message",
                        "payload": {
                            "sender": "user",
                            "message": request.message,
                            "metadata": {"intent": result["intent"]}
                        }
                    },
                    timeout=6
                )

                # Pass cards in metadata for SKU extraction
                agent_metadata = {
                    "intent": result["intent"],
                    "confidence": result["confidence"],
                    "method": result["method"],
                    "cards": result.get("cards", []),  # Include cards for SKU tracking
                    "recommendation_context": result.get("recommendation_context"),
                    "awaiting_recommendation_clarification": result.get("awaiting_recommendation_clarification", False),
                }
                
                requests.post(
                    "http://localhost:8000/session/update",
                    headers=base_headers,
                    json={
                        "action": "chat_message",
                        "payload": {
                            "sender": "agent",
                            "message": result["response"],
                            "metadata": agent_metadata
                        }
                    },
                    timeout=6
                )
            except Exception as e:
                logger.warning(f"⚠️  Could not save to session: {e}")
        
        return response
        
    except Exception as e:
        logger.error(f"❌ LangGraph workflow failed: {e}", exc_info=True)
        
        # Fallback response
        return AgentResponse(
            reply="I'm having trouble processing your request right now. Please try again.",
            session_token=session_token,
            timestamp=datetime.utcnow().isoformat(),
            metadata={"error": str(e), "processed": False},
            intent_info={
                "intent": "error",
                "confidence": 0.0,
                "entities": {},
                "method": "error_fallback"
            },
            cards=[]
        )


# ============================================================================
# INVENTORY & STORE PROXIES (through Sales Agent)
# ============================================================================

@app.get("/api/stores")
async def list_stores():
    """
    Proxy endpoint to fetch all stores from Supabase via inventory service.
    All store queries go through the sales agent for proper orchestration.
    """
    try:
        logger.info("🏬 Fetching all stores via inventory service")
        
        # Call inventory service
        response = requests.get(
            "http://localhost:8001/stores",
            timeout=5
        )
        response.raise_for_status()
        
        stores_data = response.json()
        logger.info(f"✅ Fetched {len(stores_data.get('stores', []))} stores")
        
        return stores_data
        
    except Exception as e:
        logger.error(f"❌ Failed to fetch stores: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch stores: {str(e)}")


@app.get("/api/products/{sku}")
async def get_product_details(sku: str):
    """
    Get product details by SKU.
    Used by Reservation Service to fetch product name and description.
    
    Args:
        sku: Product SKU
        
    Returns:
        Product details including name, description, price, category, image_url
    """
    try:
        from db.repositories.products_repo import get_product_by_sku
        
        logger.info(f"📦 Fetching product details for SKU={sku}")
        product = get_product_by_sku(sku)
        
        if product:
            logger.info(f"✅ Found product: {product.get('name', 'Unknown')}")
            return {
                "sku": sku,
                "name": product.get("name", f"Product {sku}"),
                "description": product.get("description", ""),
                "price": product.get("price", "N/A"),
                "category": product.get("category", ""),
                "image_url": product.get("image_url", product.get("image", ""))
            }
        else:
            logger.warning(f"Product not found for SKU={sku}")
            raise HTTPException(status_code=404, detail=f"Product not found for SKU {sku}")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Failed to fetch product details: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch product: {str(e)}")


@app.get("/api/stores/{store_location}/inventory/{sku}")
async def check_store_inventory(store_location: str, sku: str):
    """
    Proxy endpoint to check inventory for a product at a specific store.
    Fetches current stock from Supabase inventory table via inventory service.
    
    Args:
        store_location: Store location ID (e.g., 'STORE_MUMBAI')
        sku: Product SKU
        
    Returns:
        Inventory details including available stock and can_reserve status
    """
    try:
        logger.info(f"📦 Checking inventory: SKU={sku} at {store_location}")
        
        # Call inventory service
        response = requests.get(
            f"http://localhost:8001/stores/{store_location}/inventory/{sku}",
            timeout=5
        )
        response.raise_for_status()
        
        inventory_data = response.json()
        logger.info(f"✅ Inventory check: {inventory_data['available_stock']} units available")
        
        return inventory_data
        
    except Exception as e:
        logger.error(f"❌ Failed to check inventory: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to check inventory: {str(e)}")


class ReserveInStoreRequest(BaseModel):
    """Request to reserve product in store."""
    customer_id: str
    sku: str
    quantity: int = Field(..., gt=0)
    store_location: str
    session_token: Optional[str] = None


@app.post("/api/reserve-in-store")
async def reserve_in_store(request: ReserveInStoreRequest):
    """
    Reserve a product in a specific store.
    
    EXECUTION ORDER (MUST NOT CHANGE):
    1. Validate request payload
    2. Fetch inventory from Supabase (source of truth)
    3. Check Redis reservation lock
    4. Create reservation in Supabase
    5. Create Redis lock
    6. Return success
    
    Args:
        request: ReserveInStoreRequest with customer, SKU, quantity, and store
        
    Returns:
        Reservation confirmation with reservation_id and expiry
    """
    try:
        logger.info(
            f"🏪 [RESERVE-IN-STORE] customer={request.customer_id}, "
            f"sku={request.sku}, qty={request.quantity}, store={request.store_location}"
        )
        
        # ====================================================================
        # STEP 1: VALIDATE REQUEST PAYLOAD
        # ====================================================================
        if not request.sku or not request.store_location:
            logger.error(f"❌ Missing required fields: sku={request.sku}, store={request.store_location}")
            return JSONResponse(
                status_code=400,
                content={
                    "status": "validation_error",
                    "message": "sku and store_location are required"
                }
            )
        
        # Normalize inputs
        sku_normalized = request.sku.strip().upper()
        store_normalized = request.store_location.strip().upper()
        
        logger.info(f"📋 Normalized: sku={sku_normalized}, store={store_normalized}")
        
        # ====================================================================
        # STEP 2: FETCH INVENTORY FROM SUPABASE (SOURCE OF TRUTH)
        # ====================================================================
        logger.info(f"📊 [STEP 2] Querying Supabase for store-level inventory...")
        
        try:
            # Query Supabase inventory table directly
            inventory_rows = supabase_client.select(
                'inventory',
                params=f"sku=eq.{sku_normalized}&store_id=eq.{store_normalized}",
                columns="sku,store_id,quantity"
            )
            
            if not inventory_rows or len(inventory_rows) == 0:
                logger.warning(f"⚠️ No inventory record found in Supabase for sku={sku_normalized}, store={store_normalized}")
                db_stock = 0
            else:
                db_stock = int(inventory_rows[0].get('quantity', 0))
                logger.info(f"✅ Supabase inventory: {db_stock} units")
            
        except Exception as e:
            logger.error(f"❌ Supabase query failed: {e}")
            return JSONResponse(
                status_code=503,
                content={
                    "status": "database_unavailable",
                    "message": "Failed to check inventory database"
                }
            )
        
        # Check if sufficient stock exists in Supabase
        if db_stock <= 0:
            logger.warning(
                f"🚫 [RETURN 409] Insufficient stock in Supabase\n"
                f"   sku={sku_normalized}\n"
                f"   store={store_normalized}\n"
                f"   dbStock={db_stock}\n"
                f"   requested={request.quantity}"
            )
            return JSONResponse(
                status_code=409,
                content={
                    "status": "insufficient_stock",
                    "sku": sku_normalized,
                    "store": store_normalized,
                    "available": db_stock,
                    "requested": request.quantity,
                    "reason": "Supabase inventory quantity <= 0"
                }
            )
        
        if db_stock < request.quantity:
            logger.warning(
                f"🚫 [RETURN 409] Insufficient stock for requested quantity\n"
                f"   sku={sku_normalized}\n"
                f"   store={store_normalized}\n"
                f"   dbStock={db_stock}\n"
                f"   requested={request.quantity}"
            )
            return JSONResponse(
                status_code=409,
                content={
                    "status": "insufficient_stock",
                    "sku": sku_normalized,
                    "store": store_normalized,
                    "available": db_stock,
                    "requested": request.quantity,
                    "reason": f"Available: {db_stock}, Requested: {request.quantity}"
                }
            )
        
        # ====================================================================
        # STEP 3: CHECK REDIS RESERVATION LOCK
        # ====================================================================
        logger.info(f"🔐 [STEP 3] Checking Redis reservation lock...")
        
        if not redis_client:
            logger.error("❌ Redis not available - cannot create reservation locks")
            return JSONResponse(
                status_code=503,
                content={
                    "status": "redis_unavailable",
                    "message": "Reservation locking service unavailable"
                }
            )
        
        # Redis key format: reserve:{storeId}:{sku}
        reservation_lock_key = f"reserve:{store_normalized}:{sku_normalized}"
        
        try:
            redis_lock_exists = redis_client.exists(reservation_lock_key)
            
            if redis_lock_exists:
                logger.warning(
                    f"🚫 [RETURN 409] Redis reservation lock exists\n"
                    f"   sku={sku_normalized}\n"
                    f"   store={store_normalized}\n"
                    f"   dbStock={db_stock}\n"
                    f"   redisLockKey={reservation_lock_key}\n"
                    f"   redisLockExists=True"
                )
                return JSONResponse(
                    status_code=409,
                    content={
                        "status": "already_reserved",
                        "sku": sku_normalized,
                        "store": store_normalized,
                        "message": "This item is already reserved at this store",
                        "reason": "Redis reservation lock exists"
                    }
                )
            
            logger.info(f"✅ No reservation lock found - proceeding with reservation")
            
        except Exception as e:
            logger.error(f"❌ Redis lock check failed: {e}")
            return JSONResponse(
                status_code=503,
                content={
                    "status": "redis_error",
                    "message": "Failed to check reservation lock"
                }
            )
        
        # ====================================================================
        # STEP 4: CREATE RESERVATION IN SUPABASE
        # ====================================================================
        logger.info(f"💾 [STEP 4] Creating reservation in Supabase...")
        
        try:
            reservation_response = requests.post(
                "http://localhost:8012/reservations",
                json={
                    "customer_id": request.customer_id,
                    "sku": sku_normalized,
                    "quantity": request.quantity,
                    "store_location": store_normalized,
                    "hold_id": f"reserve-{uuid.uuid4()}"
                },
                timeout=5
            )
            
            if reservation_response.status_code != 200:
                logger.error(f"❌ Reservation service failed: {reservation_response.status_code}")
                return JSONResponse(
                    status_code=500,
                    content={
                        "status": "reservation_failed",
                        "message": "Failed to create reservation record"
                    }
                )
            
            reservation_data = reservation_response.json()
            reservation_id = reservation_data.get('reservation_id')
            expires_at = reservation_data.get('expires_at')
            
            logger.info(f"✅ Reservation created: {reservation_id}")
            
        except Exception as e:
            logger.error(f"❌ Failed to create reservation: {e}")
            return JSONResponse(
                status_code=500,
                content={
                    "status": "reservation_error",
                    "message": str(e)
                }
            )
        
        # ====================================================================
        # STEP 5: CREATE REDIS LOCK
        # ====================================================================
        logger.info(f"🔒 [STEP 5] Creating Redis reservation lock...")
        
        try:
            # TTL = 24 hours (matching reservation expiry)
            ttl_seconds = 24 * 60 * 60
            redis_client.setex(reservation_lock_key, ttl_seconds, reservation_id)
            logger.info(f"✅ Redis lock created: {reservation_lock_key} (TTL={ttl_seconds}s)")
            
        except Exception as e:
            logger.warning(f"⚠️ Redis lock creation failed (reservation still valid): {e}")
        
        # ====================================================================
        # STEP 6: RETURN SUCCESS
        # ====================================================================
        logger.info(f"✅ [SUCCESS] Reservation complete: {reservation_id}")
        
        return {
            "status": "reserved",
            "reservation_id": reservation_id,
            "sku": sku_normalized,
            "quantity": request.quantity,
            "store_location": store_normalized,
            "expires_at": expires_at,
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Unexpected error in reserve_in_store: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "error": str(e),
                "message": "Failed to create reservation"
            }
        )


@app.post("/api/hold/{hold_id}/release")
async def release_hold(hold_id: str):
    """
    Release a reserved hold through the inventory service.
    
    Args:
        hold_id: The ID of the hold to release
        
    Returns:
        Release confirmation
    """
    try:
        logger.info(f"🔓 Releasing hold: {hold_id}")
        
        # Release through inventory service (uses POST /release)
        response = requests.post(
            "http://localhost:8001/release",
            json={"hold_id": hold_id},
            timeout=5
        ).json()
        
        logger.info(f"✅ Hold released: {hold_id}")
        return {
            "status": "released",
            "hold_id": hold_id,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"❌ Failed to release hold {hold_id}: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "error": str(e),
                "message": "Failed to release hold"
            }
        )


# ============================================================================
# ADMIN RESERVATION ENDPOINTS (through Sales Agent)
# ============================================================================

@app.get("/api/admin/reservations")
async def list_admin_reservations(store: str = None):
    """
    List all reservations for a store (admin view).
    
    Args:
        store: Store location ID (e.g., 'STORE_MUMBAI')
        
    Returns:
        List of reservations for the store
    """
    try:
        if not store:
            raise HTTPException(status_code=400, detail="Store parameter required")
        
        logger.info(f"📋 Admin: Listing reservations for store={store}")
        
        # Call reservation service to get store reservations
        url = f"http://localhost:8012/admin/reservations?store={store}"
        logger.debug(f"🔗 Calling Reservation Service: {url}")
        
        response = requests.get(url, timeout=5)
        
        logger.debug(f"🔗 Reservation Service response status: {response.status_code}")
        if response.status_code != 200:
            logger.error(f"❌ Reservation Service returned {response.status_code}: {response.text}")
            response.raise_for_status()
        
        reservations = response.json()
        logger.debug(f"📖 Response body: {reservations}")
        logger.info(f"✅ Fetched {len(reservations.get('reservations', []))} admin reservations for {store}")
        
        return reservations
        
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Request error calling Reservation Service: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Reservation Service error: {str(e)}")
    except Exception as e:
        logger.error(f"❌ Failed to list admin reservations: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch reservations: {str(e)}")


@app.put("/api/admin/reservations/{reservation_id}/confirm")
async def confirm_admin_reservation(reservation_id: str, store: str = None):
    """
    Confirm a reservation (mark item as kept aside).
    
    Args:
        reservation_id: Reservation ID to confirm
        store: Store location ID for verification
        
    Returns:
        Confirmation result
    """
    try:
        if not store:
            raise HTTPException(status_code=400, detail="Store parameter required")
        
        logger.info(f"✅ Admin: Confirming reservation {reservation_id} at {store}")
        
        # Call reservation service to confirm
        response = requests.put(
            f"http://localhost:8012/admin/reservations/{reservation_id}/confirm?store={store}",
            timeout=5
        )
        response.raise_for_status()
        
        result = response.json()
        logger.info(f"✅ Reservation confirmed: {reservation_id}")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Failed to confirm reservation: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to confirm reservation: {str(e)}")


@app.put("/api/admin/reservations/{reservation_id}/convert")
async def convert_admin_reservation(reservation_id: str, store: str = None, order_id: str = None):
    """
    Convert a reservation to a purchase.
    
    Args:
        reservation_id: Reservation ID to convert
        store: Store location ID for verification
        order_id: Optional order ID if converting to an existing order
        
    Returns:
        Conversion result
    """
    try:
        if not store:
            raise HTTPException(status_code=400, detail="Store parameter required")
        
        logger.info(f"🔄 Admin: Converting reservation {reservation_id} to sale at {store}")
        
        # Call reservation service to convert
        url = f"http://localhost:8012/admin/reservations/{reservation_id}/convert?store={store}"
        if order_id:
            url += f"&order_id={order_id}"
        
        response = requests.put(url, timeout=5)
        response.raise_for_status()
        
        result = response.json()
        logger.info(f"✅ Reservation converted to sale: {reservation_id}")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Failed to convert reservation: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to convert reservation: {str(e)}")


# ============================================================================
# CUSTOMER RESERVATION ENDPOINTS (through Sales Agent)
# ============================================================================

class ReservationCreateRequest(BaseModel):
    """Request to create a reservation."""
    customer_id: str
    sku: str
    quantity: int = 1
    store_location: str
    hold_id: str


@app.post("/api/reservations")
async def create_customer_reservation(request: ReservationCreateRequest):
    """
    Create a new reservation (customer).
    
    Args:
        request: Reservation creation details
        
    Returns:
        Reservation confirmation
    """
    try:
        logger.info(f"📝 Customer: Creating reservation for {request.customer_id}, SKU={request.sku}")
        
        response = requests.post(
            "http://localhost:8012/reservations",
            json=request.dict(),
            timeout=5
        )
        response.raise_for_status()
        
        result = response.json()
        logger.info(f"✅ Reservation created: {result.get('reservation_id')}")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Failed to create reservation: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create reservation: {str(e)}")


@app.get("/api/reservations")
async def list_customer_reservations(customer_id: str = None):
    """
    List all reservations for a customer.
    
    Args:
        customer_id: Customer ID to fetch reservations for
        
    Returns:
        List of customer's reservations
    """
    try:
        if not customer_id:
            raise HTTPException(status_code=400, detail="customer_id parameter required")
        
        logger.info(f"📋 Listing reservations for customer={customer_id}")
        
        response = requests.get(
            f"http://localhost:8012/reservations?customer_id={customer_id}",
            timeout=5
        )
        response.raise_for_status()
        
        reservations = response.json()
        logger.info(f"✅ Fetched {len(reservations.get('reservations', []))} reservations for {customer_id}")
        
        return reservations
        
    except Exception as e:
        logger.error(f"❌ Failed to list customer reservations: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch reservations: {str(e)}")


@app.get("/api/reservations/{reservation_id}")
async def get_customer_reservation(reservation_id: str):
    """
    Get a specific reservation by ID.
    
    Args:
        reservation_id: Reservation ID to fetch
        
    Returns:
        Reservation details
    """
    try:
        logger.info(f"📖 Fetching reservation: {reservation_id}")
        
        response = requests.get(
            f"http://localhost:8012/reservations/{reservation_id}",
            timeout=5
        )
        response.raise_for_status()
        
        reservation = response.json()
        logger.info(f"✅ Retrieved reservation: {reservation_id}")
        
        return reservation
        
    except Exception as e:
        logger.error(f"❌ Failed to fetch reservation: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch reservation: {str(e)}")


class ReservationStatusRequest(BaseModel):
    """Request to update reservation status."""
    status: str
    notes: str = None


@app.put("/api/reservations/{reservation_id}/status")
async def update_customer_reservation_status(reservation_id: str, request: ReservationStatusRequest):
    """
    Update a reservation's status.
    
    Args:
        reservation_id: Reservation ID to update
        request: New status and optional notes
        
    Returns:
        Updated reservation
    """
    try:
        logger.info(f"🔄 Updating reservation {reservation_id} status to {request.status}")
        
        response = requests.put(
            f"http://localhost:8012/reservations/{reservation_id}/status",
            json=request.dict(),
            timeout=5
        )
        response.raise_for_status()
        
        result = response.json()
        logger.info(f"✅ Reservation status updated: {reservation_id}")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Failed to update reservation status: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update reservation: {str(e)}")


@app.delete("/api/reservations/{reservation_id}")
async def cancel_customer_reservation(reservation_id: str):
    """
    Cancel a reservation.
    
    Args:
        reservation_id: Reservation ID to cancel
        
    Returns:
        Cancellation confirmation
    """
    try:
        logger.info(f"❌ Cancelling reservation: {reservation_id}")
        
        response = requests.delete(
            f"http://localhost:8012/reservations/{reservation_id}",
            timeout=5
        )
        response.raise_for_status()
        
        result = response.json()
        logger.info(f"✅ Reservation cancelled: {reservation_id}")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Failed to cancel reservation: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to cancel reservation: {str(e)}")


# ============================================================================

@app.get("/api/reservations/{reservation_id}/insights")
async def get_reservation_insights(reservation_id: str):
    """
    Get comprehensive customer insights for a reservation.
    Orchestrates calls to the Reservation Service to fetch insights.
    
    Args:
        reservation_id: Reservation ID to get insights for
        
    Returns:
        Reservation insights with product details, customer profile, and AI-generated summary
    """
    try:
        logger.info(f"📊 Fetching insights for reservation {reservation_id} via Sales Agent")
        
        # Call Reservation Service insights endpoint with extended timeout
        response = requests.get(
            f"http://localhost:8012/admin/reservations/{reservation_id}/insights",
            timeout=30
        )
        
        if response.status_code == 200:
            insights = response.json()
            logger.info(f"✅ Retrieved insights for {reservation_id}")
            return insights
        else:
            logger.warning(f"Reservation Service returned {response.status_code}")
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Failed to fetch insights: {response.text[:200]}"
            )
            
    except requests.exceptions.Timeout:
        logger.error("Timeout calling Reservation Service")
        raise HTTPException(status_code=504, detail="Reservation Service timeout")
    except requests.exceptions.ConnectionError:
        logger.error("Connection error to Reservation Service")
        raise HTTPException(status_code=503, detail="Reservation Service unavailable")
    except Exception as e:
        logger.error(f"Error fetching insights: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch insights: {str(e)}")


# ============================================================================
# STARTUP
# ============================================================================

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8010,
        reload=True,
        log_level="info"
    )

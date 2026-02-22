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

from fastapi import FastAPI, Request, status, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import requests
import httpx
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
import uvicorn
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv(Path(__file__).parent / '.env')

# Import LangGraph Sales Agent (absolute import for direct uvicorn execution)
from sales_graph import process_message as process_with_langgraph

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

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

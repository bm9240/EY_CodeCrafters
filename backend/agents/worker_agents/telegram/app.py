# Telegram Agent - FastAPI Server
# Handles Telegram bot interactions and forwards to Sales Agent
# Architecture: Telegram → Sales Agent → Worker Agents
# 
# Telegram ONLY forwards messages and displays responses.
# All business logic is handled by Sales Agent.

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn
import requests
import logging
import os
import sys
from typing import Optional, Dict, Any, List
import json
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables from .env file
load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")

# Add backend to path for imports
BACKEND_DIR = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# Import Supabase customer repository
from db.repositories.customer_repo import get_customer_by_phone

app = FastAPI(
    title="Telegram Agent",
    description="Telegram message forwarder - all logic handled by Sales Agent",
    version="2.0.0"
)

# In-memory storage for pending phone authentication
# chat_id -> {"session_token": str, "customer_id": str, "phone": str, "awaiting_phone": bool}
chat_state = {}

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SALES_AGENT_URL = os.getenv("SALES_AGENT_URL", "http://localhost:8010")
SESSION_MANAGER_URL = os.getenv("SESSION_MANAGER_URL", "http://localhost:8000")

# Telegram API base URL
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# ==========================================
# REQUEST/RESPONSE MODELS
# ==========================================

class TelegramMessage(BaseModel):
    """Telegram message structure"""
    class Config:
        extra = "ignore"

    message_id: Optional[int] = None
    from_user: Optional[Dict[str, Any]] = Field(default=None, alias="from")
    chat: Optional[Dict[str, Any]] = None
    date: Optional[int] = None
    text: Optional[str] = None

class TelegramUpdate(BaseModel):
    """Telegram update structure"""
    class Config:
        extra = "ignore"

    update_id: Optional[int] = None
    message: Optional[TelegramMessage] = None
    callback_query: Optional[Dict[str, Any]] = None

# ==========================================
# TELEGRAM API HELPERS
# ==========================================

async def send_telegram_message(chat_id: int, text: str, reply_markup: Optional[Dict] = None) -> bool:
    """Send a text message to Telegram chat"""
    try:
        # Validate inputs to prevent 400 errors
        if not text or not isinstance(text, str):
            logger.error(f"❌ Invalid text for chat {chat_id}: {text}")
            return False
        
        # Ensure text is not empty and is a string
        text = str(text).strip()
        if not text:
            logger.error(f"❌ Empty text for chat {chat_id}")
            return False
        
        url = f"{TELEGRAM_API_URL}/sendMessage"
        payload = {
            "chat_id": int(chat_id),  # Ensure int
            "text": text,
            "parse_mode": "Markdown"
        }
        
        if reply_markup:
            payload["reply_markup"] = reply_markup

        response = requests.post(url, json=payload, timeout=10)
        
        # Log error details if failed
        if response.status_code != 200:
            error_data = response.json() if response.text else {}
            logger.error(f"❌ Telegram API error {response.status_code}: {error_data}")
            return False
        
        response.raise_for_status()
        logger.info(f"✅ Sent message to Telegram chat {chat_id}")
        return True

    except Exception as e:
        logger.error(f"❌ Failed to send Telegram message: {e}")
        return False

async def send_telegram_photo(chat_id: int, photo_url: str, caption: Optional[str] = None) -> bool:
    """Send a photo to Telegram chat"""
    try:
        # Validate photo URL (must be HTTPS for Telegram)
        if not photo_url or not isinstance(photo_url, str):
            logger.error(f"❌ Invalid photo URL for chat {chat_id}: {photo_url}")
            return False
        
        photo_url = str(photo_url).strip()
        
        # Ensure HTTPS for Telegram compatibility
        if not photo_url.startswith(('https://', 'http://')):
            logger.error(f"❌ Photo URL must be HTTP(S): {photo_url}")
            return False
        
        url = f"{TELEGRAM_API_URL}/sendPhoto"
        payload = {
            "chat_id": int(chat_id),  # Ensure int
            "photo": photo_url,
            "parse_mode": "Markdown"
        }
        
        if caption:
            # Validate caption
            caption = str(caption).strip()
            if caption:
                payload["caption"] = caption

        response = requests.post(url, json=payload, timeout=10)
        
        # Log error details if failed
        if response.status_code != 200:
            error_data = response.json() if response.text else {}
            logger.error(f"❌ Telegram sendPhoto error {response.status_code}: {error_data}")
            return False
        
        response.raise_for_status()
        logger.info(f"✅ Sent photo to Telegram chat {chat_id}")
        return True

    except Exception as e:
        logger.error(f"❌ Failed to send Telegram photo: {e}")
        return False

async def send_inline_keyboard(chat_id: int, text: str, buttons: List[List[Dict]]) -> bool:
    """Send a message with inline keyboard buttons"""
    try:
        reply_markup = {
            "inline_keyboard": buttons
        }
        
        return await send_telegram_message(chat_id, text, reply_markup)

    except Exception as e:
        logger.error(f"❌ Failed to send inline keyboard: {e}")
        return False

# ==========================================
# CUSTOMER & SESSION MANAGEMENT
# ==========================================

async def get_or_create_session(chat_id: int, customer_id: Optional[str] = None, phone: Optional[str] = None) -> Optional[str]:
    """Get existing session or create new one for Telegram chat"""
    try:
        # Try to restore existing session by customer_id
        if customer_id:
            restore_url = f"{SESSION_MANAGER_URL}/session/restore"
            headers = {"X-Customer-Id": str(customer_id)}
            
            response = requests.get(restore_url, headers=headers, timeout=5)
            if response.status_code == 200:
                session_data = response.json()
                session_token = session_data.get("session_token") or session_data.get("session", {}).get("session_token")
                logger.info(f"✅ Restored session for customer {customer_id}: {session_token}")
                return session_token

        # Create new session
        start_url = f"{SESSION_MANAGER_URL}/session/start"
        payload = {
            "channel": "telegram",
            "telegram_chat_id": str(chat_id)
        }
        
        if phone:
            payload["phone"] = phone
        if customer_id:
            payload["customer_id"] = str(customer_id)

        response = requests.post(start_url, json=payload, timeout=5)
        response.raise_for_status()

        session_data = response.json()
        session_token = session_data.get("session_token")

        logger.info(f"✅ Created new session for Telegram chat {chat_id}: {session_token}")
        return session_token

    except Exception as e:
        logger.error(f"❌ Failed to get/create session: {e}")
        return None

async def get_session_data(session_token: str) -> Optional[Dict[str, Any]]:
    """Fetch full session data from Session Manager"""
    try:
        restore_url = f"{SESSION_MANAGER_URL}/session/restore"
        headers = {"X-Session-Token": session_token}
        
        response = requests.get(restore_url, headers=headers, timeout=5)
        if response.status_code == 200:
            session_data = response.json()
            return session_data.get("session")
        
        return None
    except Exception as e:
        logger.error(f"❌ Failed to fetch session data: {e}")
        return None

import httpx
import os

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = "llama-3.1-8b-instant"  # or your preferred model

async def generate_ai_summary_with_groq(session_data: Dict[str, Any], customer_name: str) -> str:
    """
    Generate AI-powered persuasive summary using Groq
    based on REAL session history + cart + loyalty data.
    """

    try:
        data = session_data.get("data", {})
        chat_history = data.get("chat_context", [])
        cart = data.get("cart", [])
        conversation_summary = data.get("conversation_summary", "")
        loyalty_tier = data.get("loyalty_tier", "Member")
        loyalty_points = data.get("loyalty_points", 0)

        # Keep last 6 turns only (avoid token overflow)
        recent_history = chat_history[-6:]

        cart_summary = []
        for item in cart:
            cart_summary.append(
                f"{item.get('name')} (₹{item.get('price')} x {item.get('quantity',1)})"
            )

        cart_text = ", ".join(cart_summary) if cart_summary else "No items in cart"

        system_prompt = """
You are a premium fashion sales assistant for a luxury brand.
Your tone is warm, elegant, empathetic, and persuasive.
Speak in SECOND PERSON.
Be welcoming, stylish, and emotionally engaging.
Keep it under 150 words.
"""

        user_prompt = f"""
Customer Name: {customer_name}

Conversation Summary: {conversation_summary}

Recent Chat History:
{recent_history}

Cart Items:
{cart_text}

Loyalty Tier: {loyalty_tier}
Loyalty Points: {loyalty_points}

Create a personalized welcome-back summary.
Mention:
- What they were exploring
- Cart reminder (if any)
- Their loyalty tier
- Encourage continuation
- Ask a soft engaging question
"""

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": GROQ_MODEL,
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

            return summary_text

    except Exception as e:
        logger.error(f"❌ Groq summary generation failed: {e}")
        return (
            f"✨ Welcome back {customer_name}! 😊\n\n"
            "I'm so glad you're here again.\n"
            "What would you love to explore today?"
        )
# ==========================================
# MESSAGE PROCESSING
# ==========================================

async def process_telegram_message(telegram_message: TelegramMessage) -> bool:
    """
    Process incoming Telegram message and forward to Sales Agent.
    
    Flow:
        1. /start → Ask for phone (NO forward to Sales Agent)
        2. Phone number → Validate from Supabase → Restore session → Show summary
        3. User message → Forward to Sales Agent → Display structured response
    """
    try:
        if not telegram_message.chat or not telegram_message.text:
            return True

        # IMPORTANT: chat_id MUST be int for Telegram API
        chat_id = int(telegram_message.chat["id"])
        text = telegram_message.text.strip()

        if not text:
            return True

        logger.info(f"📨 Telegram message from chat {chat_id}: '{text[:50]}...'")

        # ======================
        # HANDLE /start COMMAND (DO NOT forward to Sales Agent)
        # ======================
        if text.lower() == "/start":
            chat_state[str(chat_id)] = {"awaiting_phone": True}
            await send_telegram_message(
                chat_id,
                "👋 Welcome to EY CodeCrafters Shopping!\n\n"
                "📱 Please share your phone number to continue:"
            )
            return True

        # ======================
        # HANDLE PHONE NUMBER INPUT
        # ======================
        state = chat_state.get(str(chat_id), {})
        
        if state.get("awaiting_phone"):
            # Validate phone number (10 digits)
            if text.isdigit() and len(text) == 10:
                phone = text
                
                # Lookup customer from SUPABASE (not CSV)
                logger.info(f"🔍 Looking up phone {phone} in Supabase...")
                customer = get_customer_by_phone(phone)
                
                if not customer:
                    await send_telegram_message(
                        chat_id,
                        "❌ Phone number not found in our records.\n\n"
                        "Please check and try again, or contact support."
                    )
                    return True

                customer_id = str(customer.get('customer_id'))
                customer_name = customer.get('name', 'Customer')
                
                logger.info(f"✅ Found customer: {customer_name} (ID: {customer_id})")
                
                # Create or restore session from Redis
                session_token = await get_or_create_session(chat_id, customer_id, phone)
                
                if not session_token:
                    await send_telegram_message(chat_id, "❌ Failed to start session. Please try /start again.")
                    return True

                # Fetch full session data to check if returning user
                session_data = await get_session_data(session_token)
                
                # Store session info in state
                chat_state[str(chat_id)] = {
                    "session_token": session_token,
                    "customer_id": customer_id,
                    "phone": phone,
                    "customer_name": customer_name,
                    "awaiting_phone": False
                }

                # Check if user has chat history (returning user)
                if session_data:
                    conversation_history = session_data.get("data", {}).get("chat_context", [])
                    cart = session_data.get("data", {}).get("cart", [])
                    
                    # If returning user with history, show summary
                    if len(conversation_history) > 0 or len(cart) > 0:
                        logger.info(f"📚 Returning user detected: {len(conversation_history)} messages, {len(cart)} cart items")
                        
                        # Generate personalized summary
                        summary = await generate_ai_summary_with_groq(session_data, customer_name)
                        await send_telegram_message(chat_id, summary)
                        
                        logger.info(f"✅ Sent chat history summary to chat {chat_id}")
                        return True

                # New user or no history - send welcome message
                await send_telegram_message(
                    chat_id,
                    f"✅ Welcome {customer_name}!\n\n"
                    f"How can I help you today?\n\n"
                    f"💡 Try asking:\n"
                    f"• Show me running shoes\n"
                    f"• I need a t-shirt under ₹1000\n"
                    f"• What's in my cart?"
                )
                
                logger.info(f"✅ Authenticated customer {customer_id} for chat {chat_id}")
                return True
            else:
                await send_telegram_message(
                    chat_id,
                    "❌ Invalid phone number.\n\n"
                    "Please enter a valid 10-digit phone number:"
                )
                return True

        # ======================
        # CHECK AUTHENTICATION
        # ======================
        if not state.get("session_token"):
            await send_telegram_message(
                chat_id,
                "⚠️ Session not found.\n\n"
                "Please use /start to begin."
            )
            return True

        session_token = state["session_token"]
        customer_id = state["customer_id"]

        # ======================
        # FORWARD TO SALES AGENT
        # ======================
        logger.info(f"🔄 Forwarding to Sales Agent: {text[:50]}...")
        
        # Fetch current session state
        session_data = await get_session_data(session_token)
        
        # Prepare request payload
        sales_payload = {
            "message": text,
            "session_token": session_token,
            "session_state": session_data,  # Include full session state
            "metadata": {
                "channel": "telegram",
                "telegram_chat_id": str(chat_id),
                "customer_id": customer_id,
                "source": "telegram"
            }
        }

        # Call Sales Agent /api/message (ALL business logic happens here)
        try:
            sales_response = requests.post(
                f"{SALES_AGENT_URL}/api/message",
                json=sales_payload,
                timeout=30
            )
            sales_response.raise_for_status()
            sales_data = sales_response.json()
            
            logger.info(f"✅ Sales Agent response received")

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Failed to call Sales Agent: {e}")
            await send_telegram_message(
                chat_id,
                "❌ Sorry, I'm experiencing technical difficulties.\n\n"
                "Please try again in a moment."
            )
            return False

        # ======================
        # DISPLAY STRUCTURED RESPONSE
        # ======================
        
        logger.info(f"📦 Sales Agent response keys: {list(sales_data.keys())}")
        
        # 1. Extract and send chat message (ALWAYS a string, never raw JSON)
        chat_message = sales_data.get("reply", "")
        
        # Log the raw response for debugging
        logger.info(f"💬 Raw chat_message: '{chat_message[:100]}...'")
        
        # Ensure it's a string
        if chat_message and isinstance(chat_message, str):
            chat_message = chat_message.strip()
            if chat_message:
                logger.info(f"✅ Sending chat message to Telegram")
                await send_telegram_message(chat_id, chat_message)
            else:
                logger.warning(f"⚠️ Empty chat message after stripping")
        else:
            logger.error(f"❌ Invalid chat message type: {type(chat_message)}")

        # 2. Send product images (if any) - AFTER text message
        cards = sales_data.get("cards", [])
        if cards and isinstance(cards, list) and len(cards) > 0:
            logger.info(f"📦 Sending {len(cards)} product cards...")
            
            for card in cards[:5]:  # Limit to 5 images
                product_name = card.get("name", "Product")
                product_price = card.get("price", "N/A")
                image_url = card.get("image")
                sku = card.get("sku", card.get("id"))
                
                # Get recommendation text/description
                description = card.get("personalized_reason") or card.get("description") or card.get("gift_message") or ""
                
                # Build product URL for website
                product_url = f"http://localhost:5173/products/{sku}" if sku else ""
                
                if image_url:
                    # Validate HTTPS before sending
                    if image_url.startswith(('https://', 'http://')):
                        # Build caption with name, price, description, and link
                        caption_parts = [f"*{product_name}*", f"💰 ₹{product_price}"]
                        
                        if description:
                            # Limit description to 200 chars to stay within Telegram caption limits
                            description_text = description[:200] + "..." if len(description) > 200 else description
                            caption_parts.append(f"\n{description_text}")
                        
                        if product_url:
                            caption_parts.append(f"\n🔗 [View Product]({product_url})")
                        
                        caption = "\n".join(caption_parts)
                        logger.info(f"🖼️ Sending product image with description: {image_url[:50]}...")
                        await send_telegram_photo(chat_id, image_url, caption)
                    else:
                        logger.warning(f"⚠️ Skipping invalid image URL: {image_url}")
        else:
            logger.info(f"📦 No product cards to send")

        # 3. Handle payment link or checkout buttons
        metadata = sales_data.get("metadata", {})
        payment_link = metadata.get("payment_link")
        
        if payment_link:
            logger.info(f"💳 Sending payment link button...")
            
            # Create inline keyboard with payment button
            buttons = [[{
                "text": "💳 Complete Payment",
                "url": payment_link
            }]]
            
            await send_inline_keyboard(
                chat_id,
                "Click the button below to complete your payment:",
                buttons
            )
        
        # 4. Product links are now included in photo captions above
        # No need for separate action buttons - removed as per requirement
        
        # Add checkout button if cart exists (kept for cart management)
        session_data = await get_session_data(session_token)
        cart = session_data.get("data", {}).get("cart", []) if session_data else []
        if cart:
            await send_inline_keyboard(
                chat_id,
                "Quick actions:",
                [[{
                    "text": "🛒 Checkout",
                    "callback_data": "CHECKOUT"
                }]]
            )

        logger.info(f"✅ Processed Telegram message from chat {chat_id}")
        return True

    except Exception as e:
        logger.error(f"❌ Failed to process Telegram message: {e}", exc_info=True)
        try:
            await send_telegram_message(
                int(telegram_message.chat["id"]),
                "Sorry, I'm having trouble processing your message. Please try again."
            )
        except:
            pass
        return False

# ==========================================
# CALLBACK QUERY PROCESSING
# ==========================================

async def process_callback_query(callback_query: Dict[str, Any]) -> bool:
    """
    Process inline button callback queries.
    
    Supported actions:
    - ADD_TO_CART|SKU|QTY
    - BUY_NOW|SKU|QTY  
    - CHECKOUT
    """
    try:
        query_id = callback_query.get("id")
        data = callback_query.get("data", "")
        from_user = callback_query.get("from", {})
        chat_id = from_user.get("id")
        
        if not chat_id or not data:
            logger.error(f"❌ Invalid callback query: {callback_query}")
            return False
        
        chat_id = int(chat_id)
        logger.info(f"🔘 Callback query from chat {chat_id}: {data}")
        
        # Parse callback data
        parts = data.split("|")
        action = parts[0] if len(parts) > 0 else ""
        
        # Get user session
        state = chat_state.get(str(chat_id), {})
        if not state.get("session_token"):
            await answer_callback_query(query_id, "Session expired. Please use /start again.")
            return False
        
        session_token = state["session_token"]
        customer_id = state["customer_id"]
        
        # Handle different actions
        if action == "ADD_TO_CART":
            if len(parts) < 3:
                await answer_callback_query(query_id, "Invalid cart data")
                return False
            
            sku = parts[1]
            qty = int(parts[2]) if len(parts) > 2 else 1
            
            # Update cart directly via Session Manager (don't call Sales Agent)
            try:
                cart_payload = {
                    "action": "add_to_cart",
                    "sku": sku,
                    "quantity": qty
                }
                
                cart_response = requests.post(
                    f"{SESSION_MANAGER_URL}/session/update",
                    json=cart_payload,
                    headers={"X-Session-Token": session_token},
                    timeout=10
                )
                cart_response.raise_for_status()
                
                await answer_callback_query(query_id, f"🛒 Added to cart successfully!")
                await send_telegram_message(chat_id, f"🛒 *{sku}* added to your cart!")
                
                logger.info(f"✅ Added {sku} to cart for customer {customer_id}")
                return True
                
            except Exception as e:
                logger.error(f"❌ Failed to add to cart: {e}")
                await answer_callback_query(query_id, "Failed to add to cart")
                return False
        
        elif action == "BUY_NOW":
            if len(parts) < 3:
                await answer_callback_query(query_id, "Invalid buy data")
                return False
            
            sku = parts[1]
            qty = int(parts[2]) if len(parts) > 2 else 1
            
            # Add to cart first, then trigger checkout
            try:
                cart_payload = {
                    "action": "add_to_cart",
                    "sku": sku,
                    "quantity": qty
                }
                
                cart_response = requests.post(
                    f"{SESSION_MANAGER_URL}/session/update",
                    json=cart_payload,
                    headers={"X-Session-Token": session_token},
                    timeout=10
                )
                cart_response.raise_for_status()
                
                # Now trigger checkout
                checkout_payload = {
                    "message": "__CHECKOUT__",
                    "session_token": session_token,
                    "metadata": {
                        "channel": "telegram",
                        "telegram_chat_id": str(chat_id),
                        "customer_id": customer_id,
                        "source": "telegram_checkout"
                    }
                }
                
                await answer_callback_query(query_id, "Processing payment...")
                
                # Call Sales Agent for checkout
                sales_response = requests.post(
                    f"{SALES_AGENT_URL}/api/message",
                    json=checkout_payload,
                    timeout=30
                )
                sales_response.raise_for_status()
                sales_data = sales_response.json()
                
                # Extract and send response
                chat_message = sales_data.get("reply", "")
                if chat_message and isinstance(chat_message, str):
                    chat_message = chat_message.strip()
                    if chat_message:
                        await send_telegram_message(chat_id, chat_message)
                
                logger.info(f"✅ Buy now completed for {sku}")
                return True
                
            except Exception as e:
                logger.error(f"❌ Failed to buy now: {e}")
                await answer_callback_query(query_id, "Failed to process purchase")
                return False
        
        elif action == "CHECKOUT":
            # Trigger checkout process
            try:
                checkout_payload = {
                    "message": "__CHECKOUT__",
                    "session_token": session_token,
                    "metadata": {
                        "channel": "telegram",
                        "telegram_chat_id": str(chat_id),
                        "customer_id": customer_id,
                        "source": "telegram_checkout"
                    }
                }
                
                await answer_callback_query(query_id, "Processing payment...")
                
                # Call Sales Agent for checkout
                sales_response = requests.post(
                    f"{SALES_AGENT_URL}/api/message",
                    json=checkout_payload,
                    timeout=30
                )
                sales_response.raise_for_status()
                sales_data = sales_response.json()
                
                # Extract and send response
                chat_message = sales_data.get("reply", "")
                if chat_message and isinstance(chat_message, str):
                    chat_message = chat_message.strip()
                    if chat_message:
                        await send_telegram_message(chat_id, chat_message)
                
                # Clear cart if success (check for success indicators)
                if "success" in chat_message.lower() or "completed" in chat_message.lower():
                    # Optionally clear cart - but Sales Agent should handle this
                    pass
                
                logger.info(f"✅ Checkout processed")
                return True
                
            except Exception as e:
                logger.error(f"❌ Failed to checkout: {e}")
                await answer_callback_query(query_id, "Failed to process checkout")
                return False
        
        else:
            await answer_callback_query(query_id, "Unknown action")
            return False
            
    except Exception as e:
        logger.error(f"❌ Failed to process callback query: {e}", exc_info=True)
        try:
            await answer_callback_query(callback_query.get("id"), "Error processing request")
        except:
            pass
        return False

async def answer_callback_query(query_id: str, text: str = "", show_alert: bool = False) -> bool:
    """Answer a callback query to remove loading state"""
    try:
        url = f"{TELEGRAM_API_URL}/answerCallbackQuery"
        payload = {
            "callback_query_id": query_id,
            "text": text,
            "show_alert": show_alert
        }
        
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to answer callback query: {e}")
        return False

# ==========================================
# API ENDPOINTS
# ==========================================

@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "service": "Telegram Agent",
        "status": "running",
        "version": "2.0.0",
        "architecture": "Telegram → Sales Agent → Worker Agents",
        "telegram_configured": bool(TELEGRAM_BOT_TOKEN)
    }

@app.post("/telegram/webhook")
async def telegram_webhook(update: TelegramUpdate):
    """
    Handle incoming Telegram updates via webhook.
    
    This is the main entry point for all Telegram messages.
    All business logic is delegated to Sales Agent.
    """
    try:
        if update.message:
            # Process regular message
            success = await process_telegram_message(update.message)
            return {"status": "processed" if success else "failed"}
        
        elif update.callback_query:
            # Process callback query (button clicks)
            success = await process_callback_query(update.callback_query)
            return {"status": "callback_processed" if success else "callback_failed"}
        
        else:
            # Ignore other update types
            return {"status": "ignored"}

    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/telegram/set-webhook")
async def set_webhook(webhook_url: str):
    """Set Telegram webhook URL"""
    try:
        if not TELEGRAM_BOT_TOKEN:
            raise HTTPException(status_code=500, detail="Telegram bot token not configured")

        url = f"{TELEGRAM_API_URL}/setWebhook"
        payload = {
            "url": webhook_url,
            "allowed_updates": ["message", "callback_query"]
        }

        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()

        result = response.json()
        logger.info(f"✅ Webhook set to: {webhook_url}")
        return result

    except Exception as e:
        logger.error(f"❌ Failed to set webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/telegram/webhook-info")
async def get_webhook_info():
    """Get current webhook information"""
    try:
        if not TELEGRAM_BOT_TOKEN:
            raise HTTPException(status_code=500, detail="Telegram bot token not configured")

        url = f"{TELEGRAM_API_URL}/getWebhookInfo"

        response = requests.get(url, timeout=10)
        response.raise_for_status()

        return response.json()

    except Exception as e:
        logger.error(f"❌ Failed to get webhook info: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# STARTUP
# ==========================================

if __name__ == "__main__":
    logger.info("🚀 Starting Telegram Agent...")
    logger.info(f"📡 Sales Agent URL: {SALES_AGENT_URL}")
    logger.info(f"🔐 Session Manager URL: {SESSION_MANAGER_URL}")
    logger.info(f"🤖 Telegram Bot Configured: {bool(TELEGRAM_BOT_TOKEN)}")
    
    uvicorn.run(app, host="0.0.0.0", port=8011)
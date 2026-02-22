"""
LangGraph-based Sales Agent with Vertex AI Intent Detection

This module defines a LangGraph workflow that:
1. Detects user intent using Vertex AI
2. Routes to appropriate microservice based on intent
3. Returns structured response to frontend

Architecture:
    User Message → Intent Detection (Vertex AI) → Router → Worker Microservice → Response
"""

import logging
import os
from typing import TypedDict, Literal, Optional, List, Dict, Any
from datetime import datetime
import requests
from pathlib import Path
import pandas as pd
import csv
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import orders_repository
import json
import sys

from langgraph.graph import StateGraph, END
from dotenv import load_dotenv

# Import intent detector (absolute import for direct execution)
from vertex_intent_detector import detect_intent as vertex_detect_intent
# Agent client (async unified client for workers)
from agent_client import call_agent
# Orders repository for thread-safe CSV persistence

# ── Recommendation Flow Manager (progressive clarification) ──────────────
try:
    from services.recommendation_flow_manager import (
        detect_mode as _rfm_detect_mode,
        absorb_message as _rfm_absorb,
        init_context as _rfm_init,
        is_ready as _rfm_is_ready,
        next_question as _rfm_next_question,
        build_payload as _rfm_build_payload,
    )
    _RFM_AVAILABLE = True
except ImportError as _rfm_err:
    _RFM_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning(f"⚠️  Recommendation flow manager unavailable: {_rfm_err}")
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import orders_repository
    # ...existing code...

# Load environment
load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)

    # ...existing code...

# Load customer phone-to-ID mapping
_customer_phone_map = {}
_product_name_to_sku = {}
try:
    customers_csv = Path(__file__).parent.parent.parent.parent / 'backend' / 'data' / 'customers.csv'
    if customers_csv.exists():
        customers_df = pd.read_csv(customers_csv)
        _customer_phone_map = dict(zip(
            customers_df['phone_number'].astype(str), 
            customers_df['customer_id'].astype(str)
        ))
        logger.info(f"✅ Loaded {len(_customer_phone_map)} customer phone mappings")
    else:
        logger.warning("⚠️  customers.csv not found, will use fallback customer ID")
except Exception as e:
    logger.warning(f"⚠️  Could not load customer mappings: {e}")

# Load product name-to-SKU mapping
try:
    products_csv = Path(__file__).parent.parent.parent.parent / 'backend' / 'data' / 'products.csv'
    if products_csv.exists():
        products_df = pd.read_csv(products_csv)
        # Normalize column names for new CSV schema
        if "product_display_name" in products_df.columns and "ProductDisplayName" not in products_df.columns:
            products_df = products_df.rename(columns={"product_display_name": "ProductDisplayName"})
        if "sub_category" in products_df.columns and "subcategory" not in products_df.columns:
            products_df = products_df.rename(columns={"sub_category": "subcategory"})
        
        # Ensure product URLs are set for Kiosk/WhatsApp UI
        if 'image_url' not in products_df.columns and 'image' in products_df.columns:
            products_df['image_url'] = products_df['image']
        if 'product_url' not in products_df.columns:
            products_df['product_url'] = '/products/' + products_df['sku'].astype(str)
        
        # Create lowercase name → SKU mapping for case-insensitive lookup
        _product_name_to_sku = dict(zip(
            products_df['ProductDisplayName'].str.lower(), 
            products_df['sku']
        ))
        logger.info(f"✅ Loaded {len(_product_name_to_sku)} product name mappings")
    else:
        logger.warning("⚠️  products.csv not found, SKU resolution will fail")
except Exception as e:
    logger.warning(f"⚠️  Could not load product mappings: {e}")

# Microservice URLs
WORKER_SERVICES = {
    "recommendation": "http://localhost:8008",  # Matches recommendation/app.py uvicorn port
    "inventory": "http://localhost:8001",
    "payment": "http://localhost:8003",
    "loyalty": "http://localhost:8002",
    "fulfillment": "http://localhost:8004",
    "post_purchase": "http://localhost:8005",
    "stylist": "http://localhost:8006",
    "virtual_circles": "http://localhost:8009",  # Virtual Circles (Community Chat)
    "ambient_commerce": os.getenv("AMBIENT_COMMERCE_URL", "http://localhost:8017"),
}

WORKER_TIMEOUT_SECONDS = int(os.getenv("SALES_AGENT_WORKER_TIMEOUT", "25"))

# ---------------------------------------------------------------------------
# In-process store for recommendation clarification state.
# Keyed by session_token so each conversation has its own context.
# This is far more reliable than storing/restoring via chat-message metadata.
# ---------------------------------------------------------------------------
_REC_CTX_STORE: Dict[str, Dict[str, Any]] = {}   # session_token → recommendation_context
_REC_AWAITING_STORE: Dict[str, bool] = {}         # session_token → awaiting clarification?


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def infer_gender_from_relation(relation: str) -> str:
    """
    Infer recipient gender from relationship.
    
    Args:
        relation: Relationship string (e.g., 'mother', 'father', 'sister')
        
    Returns:
        'male', 'female', or 'unisex'
    """
    relation_lower = relation.lower().strip()
    
    # Female relations
    female_relations = {
        'mother', 'mom', 'mama', 'mum', 'mummy', 'grandmother', 'grandma', 'grandmom',
        'sister', 'sis', 'daughter', 'wife', 'girlfriend', 'gf', 'aunt', 'aunty',
        'niece', 'cousin'  # cousin can be either, but often female in gifting context
    }
    
    # Male relations
    male_relations = {
        'father', 'dad', 'papa', 'grandfather', 'grandpa', 'granddad',
        'brother', 'bro', 'son', 'husband', 'boyfriend', 'bf', 'uncle',
        'nephew'
    }
    
    if relation_lower in female_relations:
        return 'female'
    elif relation_lower in male_relations:
        return 'male'
    else:
        return 'unisex'

def resolve_product_to_sku(product_identifier: str) -> Optional[str]:
    """
    Resolve product name or SKU to actual SKU.
    
    Args:
        product_identifier: Product name or SKU
        
    Returns:
        SKU string if found, None otherwise
    """
    # If it's already a SKU format, return as-is
    if product_identifier.upper().startswith('SKU'):
        return product_identifier.upper()
    
    # Try to find by product name (case-insensitive)
    product_lower = product_identifier.lower()
    
    # Exact match first (highest priority)
    if product_lower in _product_name_to_sku:
        sku = _product_name_to_sku[product_lower]
        logger.info(f"📦 Exact match: '{product_identifier}' → {sku}")
        return sku
    
    # Partial match - collect all matches and score them
    matches = []
    search_words = set(product_lower.split())
    
    for name, sku in _product_name_to_sku.items():
        name_words = set(name.split())
        
        # Calculate match score
        if product_lower in name:
            # User query is substring of product name
            common_words = search_words & name_words
            score = len(common_words) * 2 + len(product_lower)  # Prefer more word matches
            matches.append((score, name, sku, len(name)))
        elif name in product_lower:
            # Product name is substring of user query
            common_words = search_words & name_words
            score = len(common_words) * 2
            matches.append((score, name, sku, len(name)))
    
    if matches:
        # Sort by score (desc), then by name length (asc) - prefer better matches with shorter names
        matches.sort(key=lambda x: (-x[0], x[3]))
        best_score, best_name, best_sku, _ = matches[0]
        logger.info(f"📦 Best match: '{product_identifier}' → {best_sku} ('{best_name}', score: {best_score})")
        
        # Log other candidates for debugging
        if len(matches) > 1:
            logger.debug(f"   Other matches: {[(m[1], m[2]) for m in matches[1:4]]}")
        
        return best_sku
    
    logger.warning(f"⚠️  Could not resolve '{product_identifier}' to SKU")
    return None


# =====================
# Orchestrator helpers
# =====================
async def fallback_recommendations(intent: Dict[str, Any], limit: int = 5) -> List[Dict[str, Any]]:
    """Return simple CSV-based recommendations as a fallback when workers are unavailable."""
    try:
        if 'products_df' not in globals() or products_df is None:
            return []

        df = products_df.copy()

        # Filter by product_type if provided
        ptype = intent.get('product_type')
        if ptype:
            if ptype == 'footwear':
                df = df[df.apply(lambda r: 'shoe' in str(r.get('ProductDisplayName','')).lower() or 'footwear' in str(r.get('ProductDisplayName','')).lower(), axis=1)]
            elif ptype == 'apparel':
                df = df[df.apply(lambda r: any(w in str(r.get('ProductDisplayName','')).lower() for w in ['shirt','tshirt','jacket','top','coat']), axis=1)]

        # Price filter
        max_price = intent.get('max_price') or intent.get('budget')
        if max_price:
            try:
                maxp = float(max_price)
                # try common price columns
                price_col = None
                for c in ['price','mrp','MRP','Price']:
                    if c in df.columns:
                        price_col = c
                        break
                if price_col:
                    df = df[pd.to_numeric(df[price_col], errors='coerce') <= maxp]
            except Exception:
                pass

        if df.empty:
            return []

        # Take top N results (simple deterministic ordering)
        df = df.head(limit)

        results: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            results.append({
                'sku': row.get('sku') or row.get('SKU') or row.get('Sku'),
                'name': row.get('ProductDisplayName') or row.get('name') or '',
                'price': float(row.get('price') or row.get('MRP') or 0),
                'personalized_reason': 'Recommended based on your query',
                # ...existing code...
            })

        return results
    except Exception as e:
        logger.warning(f"Fallback recommendations failed: {e}")
        return []


class SalesOrchestrator:
    """Lightweight orchestrator facade embedded into `sales_graph`.

    Provides the minimal async methods other modules expect from the original
    `orchestrator.py` so you can safely remove that file and keep this as
    the single orchestrator surface.
    """

    def __init__(self):
        # Use the CSVs already loaded (products_df / customers_df)
        self.products = globals().get('products_df', None)
        self.customers = globals().get('customers_df', None)

    async def get_recommendations(self, user_id: str, intent: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Prefer calling the recommendation worker; fall back to CSV recommendations
        try:
            payload = {**intent, **(context or {})}
            # Try agent client (mock or real)
            resp = await call_agent('recommendation', payload)
            # Expect worker to return a list under common keys
            recs = resp.get('recommended_products') or resp.get('recommendations') or resp.get('results') or []
            if recs:
                return recs
        except Exception:
            logger.debug('Recommendation worker call failed; using fallback CSV')

        # CSV fallback
        return await fallback_recommendations(intent, limit=context.get('limit', 5) if context else 5)

    async def verify_inventory(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Check availability for a list of items using the inventory agent."""
        results = {'all_available': True, 'items': [], 'low_stock_alerts': []}
        for item in items:
            sku = item.get('sku') or resolve_product_to_sku(item.get('product_name',''))
            qty = int(item.get('quantity', 1))
            try:
                inv = await call_agent('inventory', {'sku': sku})
                available = False
                if isinstance(inv, dict):
                    if 'available' in inv:
                        available = bool(inv.get('available'))
                    else:
                        # try numeric stock fields
                        total = inv.get('total_stock') or inv.get('online_stock') or 0
                        available = int(total) >= qty

                results['items'].append({'sku': sku, 'requested': qty, 'available': available})
                if not available:
                    results['all_available'] = False
                # low-stock heuristic
                try:
                    total_stock = int(inv.get('total_stock', 0) or inv.get('online_stock', 0) or 0)
                    if total_stock > 0 and total_stock < 5:
                        results['low_stock_alerts'].append({'sku': sku, 'stock': total_stock})
                except Exception:
                    pass
            except Exception as e:
                results['items'].append({'sku': sku, 'requested': qty, 'available': False, 'error': str(e)})
                results['all_available'] = False

        return results

    async def create_inventory_holds(self, items: List[Dict[str, Any]], session_id: str) -> List[Dict[str, Any]]:
        holds = []
        for item in items:
            sku = item.get('sku')
            qty = int(item.get('quantity', 1))
            try:
                # Best-effort: ask inventory agent to create a hold
                resp = await call_agent('inventory', {'action': 'hold', 'sku': sku, 'quantity': qty, 'session_id': session_id})
                holds.append({'sku': sku, 'hold': resp})
            except Exception as e:
                holds.append({'sku': sku, 'error': str(e)})
        return holds

    async def process_payment(self, customer_id: str, order_total: float, payment_method: Dict[str, Any]) -> Dict[str, Any]:
        try:
            resp = await call_agent('payment', {'action': 'process', 'customer_id': customer_id, 'amount': order_total, 'payment_method': payment_method})
            return resp
        except Exception as e:
            return {'status': 'failed', 'error': str(e)}

    async def handle_return_exchange(self, order_id: str, items: List[Dict[str, Any]], reason: str, action: str) -> Dict[str, Any]:
        try:
            resp = await call_agent('post_purchase', {'action': action, 'order_id': order_id, 'items': items, 'reason': reason})
            return resp
        except Exception as e:
            return {'status': 'failed', 'error': str(e)}

    async def complete_purchase_flow(self, customer_id: str, items: List[Dict[str, Any]], payment_method: Dict[str, Any], shipping_address: Dict[str, Any]) -> Dict[str, Any]:
        """Minimal end-to-end flow: verify inventory -> create holds -> process payment -> start fulfillment."""
        flow = {'status': 'initiated', 'steps': {}, 'order_id': f"ORD-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{customer_id[:8]}"}
        # 1) verify
        ver = await self.verify_inventory(items)
        flow['steps']['verify_inventory'] = ver
        if not ver.get('all_available'):
            flow['status'] = 'failed'
            return flow

        # 2) create holds
        holds = await self.create_inventory_holds(items, session_id=flow['order_id'])
        flow['steps']['holds'] = holds

        # 3) calculate discounted total with loyalty and coupons
        original_total = sum([float(i.get('price', 0)) * int(i.get('quantity', 1)) for i in items])
        
        # Apply automatic discounts via loyalty service
        try:
            discount_url = f"{WORKER_SERVICES['loyalty']}/loyalty/calculate-discounts"
            discount_payload = {
                "user_id": customer_id,
                "cart_total": original_total
            }
            discount_response = requests.post(discount_url, json=discount_payload, timeout=5)
            discount_response.raise_for_status()
            discount_data = discount_response.json()
            
            discounted_total = discount_data.get('final_total', original_total)
            applied_discounts = discount_data.get('message', 'No discounts applied')
        except Exception as e:
            logger.warning(f"⚠️  Failed to calculate discounts: {e}")
            discounted_total = original_total
            applied_discounts = 'Discount calculation failed'
        
        # 4) process payment with discounted amount
        payment_resp = await self.process_payment(customer_id, discounted_total, payment_method)
        # Extract first successful hold_id if available
        inventory_hold_id = None
        for h in holds:
            hold_obj = h.get('hold') if isinstance(h, dict) else None
            if isinstance(hold_obj, dict):
                inventory_hold_id = hold_obj.get('hold_id') or hold_obj.get('id')
            if inventory_hold_id:
                break

        # 3) process payment
        total = sum([float(i.get('price', 0)) * int(i.get('quantity', 1)) for i in items])
        payment_resp = await self.process_payment(customer_id, total, payment_method)
        flow['steps']['payment'] = payment_resp
        flow['steps']['discounts'] = {
            'original_total': original_total,
            'discounted_total': discounted_total,
            'applied_discounts': applied_discounts
        }
        if payment_resp.get('status') in ('failed', False):
            flow['status'] = 'payment_failed'
            return flow

        payment_txn = payment_resp.get('transaction_id') or payment_resp.get('gateway_txn_id')

        # 3.5) persist order record after successful payment using thread-safe repository
        try:
            # Build items payload matching orders.csv schema
            csv_items: List[Dict[str, Any]] = []
            for it in items:
                sku = it.get('sku') or it.get('product_sku') or it.get('id')
                qty = int(it.get('quantity', 1))
                unit_price = float(it.get('price', 0))
                csv_items.append({
                    'sku': sku,
                    'qty': qty,
                    'unit_price': unit_price,
                    'line_total': round(unit_price * qty, 2)
                })

            status = 'placed'
            created_at = datetime.utcnow().isoformat()

            # Upsert to CSV safely with thread lock
            orders_repository.upsert_order_record({
                'order_id': flow['order_id'],
                'customer_id': str(customer_id),
                'items': csv_items,
                'total_amount': round(total, 2),
                'status': status,
                'created_at': created_at
            })
            flow['steps']['orders_csv'] = {'status': 'success', 'message': 'Order persisted'}
        except Exception as e:
            logger.warning(f"⚠️  Failed to persist order to orders.csv: {e}")
            flow['steps']['orders_csv'] = {'status': 'failed', 'error': str(e)}

        # 4) start fulfillment
        try:
            fulfill = await call_agent('fulfillment', {
                'action': 'start',
                'order_id': flow['order_id'],
                'customer_id': customer_id,
                'items': items,
                'shipping_address': shipping_address,
                'inventory_status': 'RESERVED',
                'payment_status': 'SUCCESS',
                'amount': total,
                'inventory_hold_id': inventory_hold_id,
                'payment_transaction_id': payment_txn,
            })
            flow['steps']['fulfillment'] = fulfill
            flow['status'] = 'completed'
        except Exception as e:
            flow['steps']['fulfillment'] = {'status': 'failed', 'error': str(e)}
            flow['status'] = 'fulfillment_failed'

        return flow

    async def close(self):
        return


# Global orchestrator instance (compat shim for removed file)
orchestrator = SalesOrchestrator()


# ============================================================================
# STATE DEFINITION
# ============================================================================

class SalesAgentState(TypedDict):
    """
    Shared state for the sales agent workflow.
    Tracks user message, intent, routing, and responses.
    """
    # Input
    message: str
    session_token: str
    metadata: Dict[str, Any]
    conversation_history: List[Dict[str, str]]
    
    # Intent detection
    intent: str
    confidence: float
    entities: Dict[str, Any]
    intent_method: str  # "vertex_ai" or "rule_based"
    
    # Routing
    worker_service: str
    worker_url: str
    
    # Response
    response: str
    cards: List[Dict[str, Any]]
    error: Optional[str]
    
    # Metadata
    timestamp: str


# ============================================================================
# NODE 1: INTENT DETECTION (VERTEX AI)
# ============================================================================

async def detect_intent_node(state: SalesAgentState) -> SalesAgentState:
    """
    First node: Detect user intent using Vertex AI.
    
    Args:
        state: Current workflow state with user message
        
    Returns:
        Updated state with detected intent and entities
    """
    logger.info(f"🤖 Detecting intent for: '{state['message'][:100]}...'")

    # ── NEW: Recommendation clarification already in progress ────────────
    # If the previous turn asked a clarification question for recommendations,
    # skip Vertex AI entirely and route straight back to clarifying_questions.
    metadata = state.get("metadata", {})
    session_token = state.get("session_token", "")
    # Check in-process store first; fall back to restored metadata flag
    if _REC_AWAITING_STORE.get(session_token) or metadata.get("awaiting_recommendation_clarification"):
        logger.info("🔁 Continuing recommendation clarification — skipping intent detection")
        state["intent"] = "recommendation"
        state["confidence"] = 1.0
        state["entities"] = {}
        state["intent_method"] = "recommendation_clarification_continuation"
        return state

    # Check if we're awaiting clarification from previous interaction
    if metadata.get("awaiting_clarification"):
        logger.info("📝 Processing clarifying question answers...")
        
        # Parse user's answers to clarifying questions
        message = state["message"].lower().strip()
        preferences = {}
        
        # Extract usage context
        if any(word in message for word in ["running", "sports", "jogging", "marathon", "gym", "workout"]):
            preferences["usage"] = "running_sports"
        elif any(word in message for word in ["office", "work", "formal", "business", "corporate"]):
            preferences["usage"] = "office_formal"
        elif any(word in message for word in ["party", "celebration", "event", "special", "wedding"]):
            preferences["usage"] = "party_special"
        elif any(word in message for word in ["casual", "everyday", "daily", "home", "relaxed"]):
            preferences["usage"] = "casual_everyday"
        
        # Extract budget
        if "under" in message or "below" in message:
            import re
            budget_match = re.search(r'₹?(\d+)', message)
            if budget_match:
                preferences["budget_max"] = int(budget_match.group(1))
        elif "2000" in message or "₹2000" in message:
            preferences["budget_max"] = 2000
        elif "5000" in message or "₹5000" in message:
            preferences["budget_max"] = 5000
        elif "10000" in message or "₹10000" in message:
            preferences["budget_max"] = 10000
        
        # Extract colors
        color_map = {
            "black": "black", "white": "white", "blue": "blue", "red": "red",
            "green": "green", "grey": "grey", "gray": "gray", "yellow": "yellow",
            "purple": "purple", "pink": "pink", "orange": "orange", "brown": "brown"
        }
        for color_name, color_value in color_map.items():
            if color_name in message:
                preferences["color"] = color_value
                break
        
        # Store preferences in session
        if preferences:
            from datetime import datetime
            preferences["last_updated"] = datetime.now().isoformat()
            
            # Update session with preferences
            session_token = state.get("session_token")
            if session_token:
                try:
                    update_payload = {
                        "action": "update_preferences",
                        "preferences": preferences
                    }
                    update_response = requests.post(
                        f"{os.getenv('SESSION_MANAGER_URL', 'http://localhost:8000')}/session/update",
                        json=update_payload,
                        headers={"X-Session-Token": session_token},
                        timeout=5
                    )
                    if update_response.status_code == 200:
                        logger.info(f"✅ Stored preferences: {preferences}")
                    else:
                        logger.warning(f"⚠️ Failed to store preferences: {update_response.status_code}")
                except Exception as e:
                    logger.error(f"❌ Error storing preferences: {e}")
        
        # Clear awaiting_clarification flag and route to recommendations
        metadata["awaiting_clarification"] = False
        state["intent"] = "recommendation"
        state["confidence"] = 1.0
        state["entities"] = preferences  # Use preferences as entities
        state["intent_method"] = "clarification_response"
        
        logger.info(f"✅ Processed clarification response, routing to recommendations")
        return state
    
    # Special handling for __CHECKOUT__ command from web UI
    if state["message"].strip() == "__CHECKOUT__":
        logger.info("🛒 Detected __CHECKOUT__ command from web UI")
        
        # Extract cart data from metadata
        cart = state.get("metadata", {}).get("cart", [])
        source = state.get("metadata", {}).get("source", "unknown")
        customer_id = state.get("metadata", {}).get("customer_id") or state.get("metadata", {}).get("user_id")
        
        if not cart:
            state["intent"] = "fallback"
            state["confidence"] = 1.0
            state["entities"] = {}
            state["intent_method"] = "checkout_empty_cart"
            state["response"] = "Your cart is empty. Please add items before checkout."
            logger.warning("⚠️ Checkout attempted with empty cart")
            return state
        
        if not customer_id:
            state["intent"] = "fallback"
            state["confidence"] = 1.0
            state["entities"] = {}
            state["intent_method"] = "checkout_no_customer"
            state["response"] = "Session expired. Please log in again to complete checkout."
            logger.warning("⚠️ Checkout attempted without customer_id")
            return state
        
        # Set payment intent with cart entities
        state["intent"] = "payment"
        state["confidence"] = 1.0
        state["entities"] = {
            "cart": cart,
            "source": source,
            "checkout_type": "cart_checkout",
            "customer_id": customer_id,
            "payment_method": "card"  # Use card method (processed via simulated gateway)
        }
        state["intent_method"] = "web_checkout"
        
        logger.info(f"✅ Checkout intent set with {len(cart)} items from {source} for customer {customer_id}")
        return state
    
    # Special handling for post-payment processing trigger from web UI
    if state.get("metadata", {}).get("source") == "post_payment_processing":
        logger.info("🚀 Detected post-payment processing trigger from web UI")
        
        # Extract order_id from metadata
        order_id = state.get("metadata", {}).get("order_id")
        
        if not order_id:
            state["intent"] = "fallback"
            state["confidence"] = 1.0
            state["entities"] = {}
            state["intent_method"] = "post_payment_no_order"
            state["response"] = "I couldn't find the order ID to process. Please check your order details."
            logger.warning("⚠️ Post-payment processing triggered without order_id")
            return state
        
        # Set fulfillment intent to start post-payment processing
        state["intent"] = "support"  # This routes to fulfillment_worker
        state["confidence"] = 1.0
        state["entities"] = {
            "order_id": order_id,
            "source": "post_payment",
            "action": "start_processing",
            "trigger_agents": ["fulfillment", "post_purchase", "stylist", "inventory"]
        }
        state["intent_method"] = "post_payment_trigger"
        
        logger.info(f"✅ Post-payment processing intent set for order {order_id}")
        return state
    
    try:
        # Call Vertex AI intent detector
        result = await vertex_detect_intent(
            user_message=state["message"],
            conversation_history=state.get("conversation_history", []),
            metadata=state.get("metadata", {})
        )
        
        # Update state with intent detection results
        state["intent"] = result["intent"]
        state["confidence"] = result["confidence"]
        state["entities"] = result["entities"]
        state["intent_method"] = result["method"]
        
        logger.info(
            f"✅ Intent: {state['intent']} "
            f"(confidence: {state['confidence']:.2f}, method: {state['intent_method']})"
        )
        logger.info(f"📦 Entities: {state['entities']}")
        
    except Exception as e:
        logger.error(f"❌ Intent detection failed: {e}")
        # Fallback to generic intent
        state["intent"] = "fallback"
        state["confidence"] = 0.5
        state["entities"] = {}
        state["intent_method"] = "error_fallback"
        state["error"] = str(e)
    
    return state


# ============================================================================
# NODE 2: ROUTER (BASED ON INTENT)
# ============================================================================

def route_by_intent(state: SalesAgentState) -> Literal[
    "clarifying_questions",
    "recommendation_worker",
    "inventory_worker",
    "payment_worker",
    "loyalty_worker",
    "fulfillment_worker",
    "post_purchase_worker",
    "stylist_worker",
    "ambient_commerce_worker",
    "comparison_worker",
    "trend_worker",
    "support_worker",
    "virtual_circles_worker",
    "fallback_worker"
]:
    """
    Router: Determines which worker microservice to call based on intent.
    
    Args:
        state: Current state with detected intent
        
    Returns:
        Node name to route to
    """
    intent = state["intent"]
    logger.info(f"🔀 Routing intent '{intent}' to worker...")
    
    # Intent to worker mapping
    intent_mapping = {
        "recommendation": "clarifying_questions",  # Route to clarifying questions first
        "gifting": "clarifying_questions",  # Gifting uses recommendation service
        "inventory": "inventory_worker",
        "payment": "payment_worker",
        "loyalty": "loyalty_worker",  # Loyalty points and coupons
        "comparison": "clarifying_questions",  # Comparison uses recommendation
        "trend": "recommendation_worker",  # Trends go directly to recommendation
        "ambient_commerce": "ambient_commerce_worker",
        # Route order tracking and support to fulfillment (not post-purchase)
        "support": "fulfillment_worker",
        "social_validation": "virtual_circles_worker",  # Community chat & insights
        "community": "virtual_circles_worker",  # Community features
        "fallback": "fallback_worker",
    }
    
    worker = intent_mapping.get(intent, "fallback_worker")
    logger.info(f"✅ Routing to: {worker}")
    
    return worker


# ============================================================================
# WORKER NODES: CALL MICROSERVICES
# ============================================================================

async def clarifying_questions_node(state: SalesAgentState) -> SalesAgentState:
    """
    Progressive clarification node powered by RecommendationFlowManager.

    Behaviour
    ---------
    1. Detect or restore the recommendation mode (normal / gifting_genius / trendseer).
    2. Absorb the current user message into the accumulated recommendation_context.
    3. If more required fields are missing AND we haven't hit the attempt cap:
       – Ask the next natural, single-sentence question.
       – Set ``_skip_recommendation=True`` so call_recommendation_worker is a no-op.
       – Persist recommendation_context + awaiting flag in metadata.
    4. Once ready (all required fields collected, or attempt/vague cap reached):
       – Clear the awaiting flag.
       – Let the graph edge call call_recommendation_worker which will use the
         structured payload built by the flow manager.

    Backward-compat: falls back to the legacy entity-based flow when the
    recommendation flow manager is not available.
    """
    logger.info("🤔 Clarifying questions node — recommendation flow manager check...")

    metadata = state.get("metadata", {}) or {}
    message = state.get("message", "")
    entities = state.get("entities", {}) or {}

    # ── Fallback: flow manager not installed ─────────────────────────────
    if not _RFM_AVAILABLE:
        logger.warning("⚠️  Flow manager unavailable, using legacy clarify logic")
        # Legacy: if entities have enough context, go straight to recommendation
        has_usage = entities.get("usage") or entities.get("occasion") or entities.get("category")
        has_budget = entities.get("price_max") or entities.get("price_min")
        if has_usage or has_budget:
            return state  # recommendation_worker will be called by graph edge
        state["response"] = (
            "I'd love to help! 😊 What kind of products are you looking for? "
            "(e.g. shoes, clothing, accessories)"
        )
        state["cards"] = []
        state["metadata"]["_skip_recommendation"] = True
        state["metadata"]["awaiting_recommendation_clarification"] = True
        return state

    # ── Restore or initialise recommendation_context ──────────────────────
    # Primary source: in-process store (survives across turns reliably)
    # Fallback: metadata restored from session chat history
    session_token = state.get("session_token", "")
    ctx: dict = dict(
        _REC_CTX_STORE.get(session_token)
        or metadata.get("recommendation_context")
        or {}
    )
    if not ctx:
        ctx = _rfm_init()

    # ── Detect mode (only on first turn or when not yet set) ──────────────
    if not ctx.get("mode"):
        ctx["mode"] = _rfm_detect_mode(message, entities)
        # Seed context from Vertex AI entities on the first turn
        if entities.get("recipient_relation") and not ctx.get("recipient_relation"):
            ctx["recipient_relation"] = entities["recipient_relation"]
            # Auto-infer gender from the seeded relation
            if not ctx.get("recipient_gender"):
                from services.recommendation_flow_manager import GENDER_FROM_RELATION as _GFR
                ctx["recipient_gender"] = _GFR.get(entities["recipient_relation"], "")
        if entities.get("occasion") and not ctx.get("occasion"):
            ctx["occasion"] = entities["occasion"]
        if entities.get("category") and not ctx.get("category"):
            ctx["category"] = entities["category"]

    mode = ctx["mode"]
    logger.info(f"📊 Recommendation mode: {mode} | clarification_attempts: {ctx.get('clarification_attempts', 0)}")

    # ── TrendSeer: no clarification needed, proceed immediately ──────────
    if mode == "trendseer":
        logger.info("🔮 TrendSeer mode — calling recommendation API immediately")
        _REC_CTX_STORE[session_token] = ctx
        _REC_AWAITING_STORE[session_token] = False
        state["metadata"]["recommendation_context"] = ctx
        state["metadata"]["_skip_recommendation"] = False
        state["metadata"]["awaiting_recommendation_clarification"] = False
        return state

    # ── Absorb the user's current message into context ────────────────────
    ctx = _rfm_absorb(message, ctx)

    # ── Check readiness ───────────────────────────────────────────────────
    if _rfm_is_ready(ctx):
        logger.info("✅ Sufficient context collected — proceeding to recommendation API")
        ctx["clarification_stage"] = "complete"
        _REC_CTX_STORE[session_token] = ctx
        _REC_AWAITING_STORE[session_token] = False
        state["metadata"]["recommendation_context"] = ctx
        state["metadata"]["_skip_recommendation"] = False
        state["metadata"]["awaiting_recommendation_clarification"] = False
        return state

    # ── Still missing required fields — ask the next question ────────────
    question = _rfm_next_question(ctx)
    ctx["clarification_attempts"] = ctx.get("clarification_attempts", 0) + 1

    if not question:
        # Safety net: flow manager couldn't generate a question but not ready
        logger.warning("⚠️  No question generated but context incomplete — proceeding")
        ctx["clarification_stage"] = "complete"
        _REC_CTX_STORE[session_token] = ctx
        _REC_AWAITING_STORE[session_token] = False
        state["metadata"]["recommendation_context"] = ctx
        state["metadata"]["_skip_recommendation"] = False
        state["metadata"]["awaiting_recommendation_clarification"] = False
        return state

    logger.info(f"❓ Asking clarification question (attempt {ctx['clarification_attempts']}): {question[:80]}...")

    state["response"] = question
    state["cards"] = []
    _REC_CTX_STORE[session_token] = ctx
    _REC_AWAITING_STORE[session_token] = True
    state["metadata"]["recommendation_context"] = ctx
    state["metadata"]["_skip_recommendation"] = True          # tell recommendation_worker to skip
    state["metadata"]["awaiting_recommendation_clarification"] = True  # tell detect_intent_node next turn
    return state

async def call_recommendation_worker(state: SalesAgentState) -> SalesAgentState:
    """Call recommendation microservice with structured payload."""
    logger.info("📞 Calling Recommendation Worker...")

    state["worker_service"] = "recommendation"
    state["worker_url"] = WORKER_SERVICES["recommendation"]
    session_token = state.get("session_token", "")

    # ── Skip if clarifying_questions_node is still collecting context ─────
    if state.get("metadata", {}).get("_skip_recommendation"):
        logger.info("⏸️  Recommendation API skipped — clarification still in progress")
        return state

    try:
        # Extract customer_id dynamically from phone number or metadata
        customer_id = state["metadata"].get("customer_id") or state["metadata"].get("user_id")

        # If no user_id, try to resolve from phone number in session
        if not customer_id:
            phone = state["metadata"].get("phone")
            if phone and str(phone) in _customer_phone_map:
                customer_id = _customer_phone_map[str(phone)]
                logger.info(f"📞 Resolved customer ID {customer_id} from phone {phone}")
            else:
                customer_id = next(iter(_customer_phone_map.values())) if _customer_phone_map else "101"
                logger.warning(f"⚠️  No phone mapping found, using fallback customer ID: {customer_id}")

        cart_skus = state["metadata"].get("cart_skus", [])
        entities = state.get("entities", {}) or {}

        # ── Prefer structured payload from flow manager ───────────────────
        # Use in-process store as primary source; fall back to state metadata
        rec_ctx = _REC_CTX_STORE.get(session_token) or state.get("metadata", {}).get("recommendation_context")
        if _RFM_AVAILABLE and rec_ctx and rec_ctx.get("mode"):
            payload = _rfm_build_payload(
                ctx=rec_ctx,
                customer_id=str(customer_id),
                cart_skus=cart_skus,
                entities=entities,
            )
            logger.info(f"📦 Using flow-manager payload (mode={rec_ctx.get('mode')})")
        else:
            # ── Legacy payload construction (backward-compat) ─────────────
            payload = {
                "customer_id": str(customer_id),
                "mode": "normal",
                "intent": entities,
                "current_cart_skus": cart_skus,
                "limit": 5,
            }

            if state["intent"] == "gifting" or entities.get("occasion") in ["birthday", "gift", "anniversary"]:
                payload["mode"] = "gifting_genius"
                payload["recipient_relation"] = entities.get("recipient_relation", "friend")
                recipient_relation = entities.get("recipient_relation", "")
                explicit_gender = entities.get("gender")
                payload["recipient_gender"] = explicit_gender or infer_gender_from_relation(recipient_relation) or "unisex"
                payload["occasion"] = entities.get("occasion", "gift")
                logger.info(f"🎁 Legacy gifting mode: relation={recipient_relation}")
            elif state["intent"] == "trend":
                payload["mode"] = "trendseer"

            # Add budget filters if present
            if "price_max" in entities:
                payload.setdefault("intent", {})
                if isinstance(payload.get("intent"), dict):
                    payload["intent"]["budget_max"] = entities["price_max"]
            if "price_min" in entities:
                payload.setdefault("intent", {})
                if isinstance(payload.get("intent"), dict):
                    payload["intent"]["budget_min"] = entities["price_min"]
        
        # Single endpoint for all modes
        endpoint = f"{state['worker_url']}/recommend"

        # Debug logging
        logger.info(f"🔍 Recommendation payload: {payload}")
        logger.info(f"⏳ Recommendation worker timeout: {WORKER_TIMEOUT_SECONDS}s")

        # Call microservice
        response = requests.post(endpoint, json=payload, timeout=WORKER_TIMEOUT_SECONDS)
        response.raise_for_status()
        
        data = response.json()
        logger.info(f"📥 Recommendation response: {len(data.get('recommended_products', []))} products")
        
        # Format response - CHECK THE CORRECT KEY NAME
        recommendations = data.get("recommended_products", [])
        if recommendations:
            mode_used = rec_ctx.get("mode", "normal") if rec_ctx else payload.get("mode", "normal")
            if mode_used == "gifting_genius":
                intro = f"Here are {len(recommendations)} thoughtful gift ideas I found! 🎁"
            elif mode_used == "trendseer":
                intro = f"Here are {len(recommendations)} trending picks for you! 🔮"
            else:
                intro = f"I found {len(recommendations)} great options for you! ✨"

            # Clear recommendation clarification state — flow is complete
            _REC_CTX_STORE.pop(session_token, None)
            _REC_AWAITING_STORE.pop(session_token, None)
            state["metadata"].pop("recommendation_context", None)
            state["metadata"].pop("awaiting_recommendation_clarification", None)
            state["metadata"].pop("_skip_recommendation", None)

            state["response"] = intro
            state["cards"] = [
                {
                    "type": "product",
                    "sku": item.get("sku"),
                    "name": item.get("name"),
                    "price": item.get("price"),
                    "image": item.get("image_url") or item.get("image", ""),
                    "description": item.get("personalized_reason", ""),
                    "personalized_reason": item.get("personalized_reason", ""),
                    "gift_message": item.get("gift_message") if isinstance(item, dict) else None,
                    "gift_suitability": item.get("gift_suitability") if isinstance(item, dict) else None
                }
                for item in recommendations
            ]
        else:
            state["response"] = "I couldn't find any matches right now. Can you try different criteria?"
            state["cards"] = []
        
        logger.info(f"✅ Got {len(recommendations)} recommendations")
        
    except Exception as e:
        logger.error(f"❌ Recommendation worker failed: {e}")
        state["response"] = "I'm having trouble fetching recommendations right now. Please try again."
        state["error"] = str(e)
        state["cards"] = []
    
    return state


async def call_inventory_worker(state: SalesAgentState) -> SalesAgentState:
    """Call inventory microservice."""
    logger.info("📞 Calling Inventory Worker...")
    
    state["worker_service"] = "inventory"
    state["worker_url"] = WORKER_SERVICES["inventory"]
    
    try:
        # Get product identifier from entities (could be name or SKU)
        product_identifier = state["entities"].get("sku") or state["entities"].get("product_name")
        
        if not product_identifier:
            state["response"] = "Please tell me which product you'd like to check. You can use the product name or SKU."
            return state
        
        # Resolve product name to SKU
        sku = resolve_product_to_sku(product_identifier)
        
        if not sku:
            state["response"] = f"I couldn't find a product matching '{product_identifier}'. Could you try a different name or provide the SKU?"
            state["cards"] = []
            return state
        
        logger.info(f"🔍 Checking inventory for SKU: {sku}")
        
        # Check stock
        response = requests.get(
            f"{state['worker_url']}/inventory/{sku}",
            timeout=5
        )
        response.raise_for_status()
        
        data = response.json()
        
        # Response format: {sku, online_stock, store_stock, total_stock}
        total_stock = data.get("total_stock", 0)
        online_stock = data.get("online_stock", 0)
        
        if total_stock > 0:
            state["response"] = f"✅ Great news! '{product_identifier}' ({sku}) is in stock! We have {total_stock} units available ({online_stock} online)."
        else:
            state["response"] = f"❌ Sorry, '{product_identifier}' ({sku}) is currently out of stock."
        
        state["cards"] = []
        logger.info(f"✅ Stock check complete for {sku}: {total_stock} units")
        
    except Exception as e:
        logger.error(f"❌ Inventory worker failed: {e}")
        state["response"] = "I'm having trouble checking inventory right now. Please try again."
        state["error"] = str(e)
        state["cards"] = []
    
    return state


async def call_ambient_commerce_worker(state: SalesAgentState) -> SalesAgentState:
    """Call ambient commerce (visual search) microservice."""
    logger.info("📞 Calling Ambient Commerce Worker...")

    state["worker_service"] = "ambient_commerce"
    state["worker_url"] = WORKER_SERVICES["ambient_commerce"]

    try:
        metadata = state.get("metadata", {})
        image_path = metadata.get("image_path")
        image_url = metadata.get("image_url")

        if not image_path and not image_url:
            state["response"] = (
                "To run visual search, please upload an image in the visual search flow. "
                "Once uploaded, I can find similar products for you."
            )
            state["cards"] = []
            return state

        file_bytes = None
        filename = "query.jpg"

        if image_path:
            image_file = Path(image_path)
            if not image_file.exists():
                state["response"] = "I couldn't find that image locally. Please upload it again."
                state["cards"] = []
                return state
            filename = image_file.name
            file_bytes = image_file.read_bytes()
        else:
            response = requests.get(image_url, timeout=10)
            response.raise_for_status()
            file_bytes = response.content

        files = {
            "file": (filename, file_bytes, "application/octet-stream")
        }

        response = requests.post(
            f"{state['worker_url']}/search/upload",
            files=files,
            timeout=WORKER_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        data = response.json()

        if not data.get("success"):
            state["response"] = data.get("message", "I couldn't find a close visual match.")
            state["cards"] = []
            return state

        best_match = data.get("best_match")
        alternatives = data.get("alternative_matches", [])

        if best_match:
            state["response"] = f"I found a strong visual match: {best_match.get('brand', '')} {best_match.get('product_name', '')}."
        else:
            state["response"] = "I found some similar options based on your image."

        cards = []
        if best_match:
            cards.append({
                "type": "product",
                "sku": best_match.get("matched_product_id"),
                "name": best_match.get("product_name"),
                "price": best_match.get("price"),
                "image": best_match.get("image_url", ""),
                "description": best_match.get("reasoning", ""),
            })

        for match in alternatives:
            cards.append({
                "type": "product",
                "sku": match.get("matched_product_id"),
                "name": match.get("product_name"),
                "price": match.get("price"),
                "image": match.get("image_url", ""),
                "description": match.get("reasoning", ""),
            })

        state["cards"] = cards

    except Exception as e:
        logger.error(f"❌ Ambient commerce worker failed: {e}")
        state["response"] = "I'm having trouble running visual search right now. Please try again."
        state["error"] = str(e)
        state["cards"] = []

    return state


async def call_payment_worker(state: SalesAgentState) -> SalesAgentState:
    """Call payment microservice and register order on successful payment."""
    logger.info("📞 Calling Payment Worker...")
    
    state["worker_service"] = "payment"
    state["worker_url"] = WORKER_SERVICES["payment"]
    
    try:
        # Extract payment details from entities/metadata
        entities = state.get("entities", {})
        metadata = state.get("metadata", {})
        
        customer_id = (
            entities.get("customer_id")
            or metadata.get("customer_id")
            or metadata.get("user_id")
        )
        
        # Handle cart from web checkout or items from other sources
        items = (
            entities.get("cart")  # From __CHECKOUT__ command
            or entities.get("items")
            or metadata.get("cart_items", [])
        )
        
        payment_method = entities.get("payment_method") or metadata.get("payment_method") or "card"
        shipping_address = entities.get("shipping_address") or metadata.get("shipping_address")
        
        logger.info(f"💳 Payment details: customer_id={customer_id}, items={len(items) if items else 0}")
        
        # If we have complete payment data, process it
        if customer_id and items and payment_method:
            # Calculate total amount (handle both cart format and items format)
            total_amount = sum([
                float(i.get('unit_price') or i.get('price', 0)) * 
                int(i.get('qty') or i.get('quantity', 1))
                for i in items
            ])
            
            # Call payment agent to process payment
            payment_resp = await call_agent('payment', {
                'action': 'process',
                'customer_id': customer_id,
                'amount': total_amount,
                'payment_method': payment_method,
                'order_id': orders_repository.generate_next_order_id()
            })
            
            # Check if payment was successful
            if payment_resp.get('success') or payment_resp.get('status') == 'success':
                logger.info(f"✅ Payment successful: {payment_resp.get('transaction_id')}")
                
                # Use the order_id from payment response or generate one
                order_id = payment_resp.get('order_id') or orders_repository.generate_next_order_id()
                
                # 3.5) Register order to orders.csv after successful payment
                try:
                    csv_items: List[Dict[str, Any]] = []
                    for it in items:
                        # Handle both cart format (sku, qty, unit_price) and items format (id, quantity, price)
                        sku = it.get('sku') or it.get('product_sku') or it.get('id')
                        qty = int(it.get('qty') or it.get('quantity', 1))
                        unit_price = float(it.get('unit_price') or it.get('price', 0))
                        csv_items.append({
                            'sku': sku,
                            'qty': qty,
                            'unit_price': unit_price,
                            'line_total': round(unit_price * qty, 2)
                        })
                    
                    orders_repository.upsert_order_record({
                        'order_id': order_id,
                        'customer_id': str(customer_id),
                        'items': csv_items,
                        'total_amount': round(total_amount, 2),
                        'status': 'placed',
                        'created_at': datetime.utcnow().isoformat()
                    })
                    logger.info(f"✅ Order registered: {order_id}")
                    
                    state["response"] = (
                        f"🎉 Payment successful! Your order {order_id} has been placed. "
                        f"Thank you for your purchase! You'll receive updates on your order soon."
                    )
                except Exception as e:
                    logger.warning(f"⚠️  Failed to register order to CSV: {e}")
                    state["response"] = (
                        f"Payment successful, but there was an issue registering your order. "
                        f"Transaction ID: {payment_resp.get('transaction_id')}"
                    )
                
                state["cards"] = []
            else:
                logger.error(f"❌ Payment failed: {payment_resp}")
                state["response"] = (
                    f"❌ Payment failed. {payment_resp.get('message', 'Please try again or use a different payment method.')}"
                )
                state["cards"] = []
        else:
            # No complete payment data, request checkout
            state["response"] = (
                "Ready to checkout? I'll help you complete your purchase securely. "
                "Please confirm your cart and I'll guide you through payment."
            )
            state["cards"] = []
            logger.info("⚠️  Incomplete payment data, awaiting user confirmation")
        
    except Exception as e:
        logger.error(f"❌ Payment worker failed: {e}", exc_info=True)
        state["response"] = "I'm having trouble with the payment service. Please try again."
        state["error"] = str(e)
        state["cards"] = []
    
    return state


async def call_loyalty_worker(state: SalesAgentState) -> SalesAgentState:
    """Call loyalty microservice for points and offers."""
    logger.info("📞 Calling Loyalty Worker...")
    
    state["worker_service"] = "loyalty"
    state["worker_url"] = WORKER_SERVICES["loyalty"]
    
    try:
        # Extract customer ID from metadata
        customer_id = None
        phone = state.get("metadata", {}).get("phone")
        
        if phone and phone in _customer_phone_map:
            customer_id = _customer_phone_map[phone]
            logger.info(f"✅ Resolved phone {phone} to customer_id {customer_id}")
        
        if not customer_id:
            customer_id = state.get("metadata", {}).get("customer_id") or state.get("metadata", {}).get("user_id", "101")
            logger.warning(f"⚠️  Using fallback customer_id: {customer_id}")
        
        # Get user's complete tier information (points + tier + benefits)
        url = f"{WORKER_SERVICES['loyalty']}/loyalty/tier/{customer_id}"
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        
        tier_data = response.json()
        points = tier_data.get("points", 0)
        tier = tier_data.get("tier", "Bronze")
        benefits = tier_data.get("benefits", {})
        next_tier = tier_data.get("next_tier")
        points_to_next = tier_data.get("points_to_next", 0)
        
        # Tier emojis
        tier_emoji = {"Bronze": "🥉", "Silver": "🥈", "Gold": "🥇", "Platinum": "💎"}
        
        # Check for active promotions
        cart_total = state.get("metadata", {}).get("cart_total", 0)
        if cart_total > 0:
            promo_url = f"{WORKER_SERVICES['loyalty']}/loyalty/check-promotions"
            promo_payload = {
                "user_id": customer_id,
                "cart_total": cart_total
            }
            promo_response = requests.post(promo_url, json=promo_payload, timeout=5)
            promo_response.raise_for_status()
            promo_data = promo_response.json()
            
            # Build response with promotions
            if promo_data.get("applicable_promotions"):
                best_promo = promo_data.get("best_promotion", {})
                state["response"] = (
                    f"{tier_emoji.get(tier, '🏅')} {tier} Tier Member\n\n"
                    f"💰 You have {points} loyalty points (₹" + str(points) + " value)\n"
                    f"🎁 Tier Discount: {benefits.get('discount_percent', 0)}% off all purchases\n\n"
                    f"🎉 Active Offer: {best_promo.get('name', 'N/A')}\n"
                    f"💸 Save {best_promo.get('discount', 0)}% on purchases above ₹" + str(best_promo.get('min_purchase', 0)) + "\n\n"
                    f"{f'🚀 {points_to_next} points to {next_tier} tier!' if next_tier else '⭐ Maximum tier reached!'}"
                )
            else:
                state["response"] = (
                    f"{tier_emoji.get(tier, '🏅')} {tier} Tier Member\n\n"
                    f"💰 You have {points} points (₹" + str(points) + " value)\n"
                    f"🎁 Tier Discount: {benefits.get('discount_percent', 0)}% off\n"
                    f"{'🚀 Free Shipping Enabled!' if benefits.get('free_shipping') else ''}\n\n"
                    f"{f'🚀 {points_to_next} points to {next_tier}!' if next_tier else '⭐ Maximum tier!'}\n\n"
                    f"💡 Use points or coupons at checkout!\n"
                    f"Coupons: ABFRL10, ABFRL20, WELCOME25"
                )
        else:
            # No cart total, just show tier status
            state["response"] = (
                f"{tier_emoji.get(tier, '🏅')} {tier} Tier Loyalty Member\n\n"
                f"💰 Points Balance: {points} (₹" + str(points) + " value)\n"
                f"🎁 Tier Benefits:\n"
                f"  • {benefits.get('discount_percent', 0)}% discount on all purchases\n"
                f"  • {'✅' if benefits.get('free_shipping') else '❌'} Free Shipping\n"
                f"  • Birthday Bonus: {benefits.get('birthday_bonus', 0)} points\n"
                f"  • Points Multiplier: {benefits.get('points_multiplier', 1.0)}x\n\n" +
                ("🚀 Earn " + str(points_to_next) + " more points to reach " + str(next_tier) + " tier!" if next_tier else "⭐ You're at the highest tier!") + "\n\n"
                "📦 Earn 1 point per ₹10 spent\n"
                "💡 Points never expire!\n\n"
                "Available Coupons:\n"
                "• ABFRL10 - 10% off on ₹500+\n"
                "• ABFRL20 - 20% off on ₹1000+\n"
                "• WELCOME25 - 25% off on ₹1500+"
            )
        
        state["cards"] = []
        state["metadata"]["loyalty_points"] = points
        state["metadata"]["loyalty_tier"] = tier
        logger.info(f"✅ Loyalty status retrieved: {tier} tier, {points} points")
        
    except Exception as e:
        logger.error(f"❌ Loyalty worker failed: {e}")
        state["response"] = "I'm having trouble fetching your loyalty details right now. Please try again."
    
    return state
async def call_fulfillment_worker(state: SalesAgentState) -> SalesAgentState:
    """Check order fulfillment status and tracking."""
    logger.info("📞 Calling Fulfillment Worker...")
    
    state["worker_service"] = "fulfillment"
    state["worker_url"] = WORKER_SERVICES["fulfillment"]

    # Incoming state can be either the TypedDict or a legacy dict from the
    # post-payment trigger. Normalise keys for downstream access.
    if "metadata" in state and "entities" not in state:
        metadata = state.get("metadata", {})
        state["entities"] = {
            "order_id": metadata.get("order_id"),
            "customer_id": metadata.get("customer_id"),
            "source": metadata.get("source"),
            "action": metadata.get("action"),
            "trigger_agents": metadata.get("trigger_agents", []),
        }
    if "intent" not in state and "metadata" in state:
        state["intent"] = state.get("metadata", {}).get("intent", "support")
    if "confidence" not in state:
        state["confidence"] = 1.0
    if "intent_method" not in state:
        state["intent_method"] = state.get("metadata", {}).get("intent_method", "post_payment_trigger")
    
    try:
        # Extract order_id from entities or message
        entities = state.get("entities", {})
        order_id = entities.get("order_id")
        source = entities.get("source")
        trigger_agents = entities.get("trigger_agents", [])
        action = entities.get("action")
        
        # Check if this is a post-payment trigger to start processing
        if source == "post_payment" and action == "start_processing":
            logger.info(f"🚀 Starting post-payment processing for order {order_id}")
            
            # Start fulfillment process
            try:
                fulfillment_response = await call_agent('fulfillment', {
                    'action': 'start',
                    'order_id': order_id,
                    'inventory_status': 'RESERVED',
                    'payment_status': 'SUCCESS'
                })
                logger.info(f"✅ Fulfillment started: {fulfillment_response}")
            except Exception as e:
                logger.error(f"❌ Failed to start fulfillment: {e}")
            
            # Trigger post-purchase agent
            if 'post_purchase' in trigger_agents:
                try:
                    post_purchase_response = await call_agent('post_purchase', {
                        'action': 'order_placed',
                        'order_id': order_id,
                        'customer_id': entities.get("customer_id")
                    })
                    logger.info(f"✅ Post-purchase notified: {post_purchase_response}")
                except Exception as e:
                    logger.error(f"❌ Failed to notify post-purchase: {e}")
            
            # Trigger stylist agent for personalized recommendations
            if 'stylist' in trigger_agents:
                try:
                    stylist_response = await call_agent('stylist', {
                        'action': 'order_analysis',
                        'order_id': order_id,
                        'customer_id': entities.get("customer_id")
                    })
                    logger.info(f"✅ Stylist notified: {stylist_response}")
                except Exception as e:
                    logger.error(f"❌ Failed to notify stylist: {e}")
            
            # Trigger inventory agent for stock updates
            if 'inventory' in trigger_agents:
                try:
                    # Get order items to update inventory
                    order_data = orders_repository.get_order_by_id(order_id)
                    if order_data and 'items' in order_data:
                        inventory_response = await call_agent('inventory', {
                            'action': 'update_stock',
                            'order_id': order_id,
                            'items': order_data['items']
                        })
                        logger.info(f"✅ Inventory updated: {inventory_response}")
                except Exception as e:
                    logger.error(f"❌ Failed to update inventory: {e}")
            
            # Set success response
            state["response"] = (
                f"🎉 Order {order_id} processing started!\n\n"
                f"📦 Fulfillment: Order is being prepared for shipment\n"
                f"👔 Stylist: Analyzing your purchase for personalized recommendations\n"
                f"📊 Inventory: Stock levels updated\n"
                f"💝 Post-Purchase: You'll receive follow-up care soon\n\n"
                f"You'll receive updates on your order status. Track your order anytime!"
            )
            state["cards"] = []
            return state
        
        # If no order_id in entities, try to find it in the message
        if not order_id:
            message = state.get("message", "")
            logger.info(f"📝 Searching for order ID in message: {message}")
            import re
            # Look for patterns like ORD000936, ORD-20260131, ORD_123, etc.
            # Match ORD followed by digits/hyphens/underscores (at least 3 chars after ORD)
            match = re.search(r'\b(ORD[-_]?\w{3,})\b', message, re.IGNORECASE)
            if match:
                order_id = match.group(1).upper()
                logger.info(f"✅ Found order ID via regex: {order_id}")
            else:
                logger.warning(f"⚠️  No order ID pattern found in message")
        
        # Validate order_id (must be more than just "ORDER" or "ORD")
        if order_id and len(order_id) <= 3:
            logger.warning(f"⚠️  Invalid order_id '{order_id}' - too short")
            order_id = None
        
        if not order_id:
            state["response"] = (
                "I can help you track your order! Please share your order ID "
                "(it looks like ORD-XXXXXXXX) so I can check the status."
            )
            state["cards"] = []
            logger.info("⚠️  No order ID provided, requesting from user")
            return state
        
        logger.info(f"🔍 Using order_id: {order_id}")
        
        # Call fulfillment agent to get status
        try:
            response = await call_agent('fulfillment', {
                'action': 'status',
                'order_id': order_id
            })
            
            logger.info(f"📥 Fulfillment response: {response}")
            
            # Fulfillment API returns { "data": { "fulfillment": {...} } } or direct fulfillment object
            fulfillment = None
            if response.get('data') and response['data'].get('fulfillment'):
                fulfillment = response['data']['fulfillment']
            elif response.get('fulfillment'):
                fulfillment = response['fulfillment']
            elif 'current_status' in response:
                # Direct fulfillment object
                fulfillment = response
            
            if fulfillment:
                status = fulfillment.get('current_status', 'UNKNOWN')
                tracking_id = fulfillment.get('tracking_id', 'N/A')
                courier = fulfillment.get('courier_partner', 'N/A')
                eta = fulfillment.get('eta', 'N/A')
                
                # Format user-friendly status message
                status_messages = {
                    'PROCESSING': '📦 Your order is being processed and packed.',
                    'PACKED': '✅ Your order has been packed and is ready for shipment.',
                    'SHIPPED': '🚚 Your order has been shipped!',
                    'OUT_FOR_DELIVERY': '🏃 Your order is out for delivery!',
                    'DELIVERED': '🎉 Your order has been delivered!'
                }
                
                status_msg = status_messages.get(status, f"Status: {status}")
                
                # Build the response message
                response_msg = (
                    f"Order {order_id}:\n\n"
                    f"{status_msg}\n\n"
                    f"📍 Tracking ID: {tracking_id}\n"
                    f"🚛 Courier: {courier}\n"
                    f"📅 ETA: {eta}"
                )
                
                # Add delivery details if OUT_FOR_DELIVERY
                if status == 'OUT_FOR_DELIVERY':
                    delivery_boy = fulfillment.get('delivery_boy_name', '')
                    delivery_phone = fulfillment.get('delivery_boy_phone', '')
                    delivery_otp = fulfillment.get('delivery_otp', '')
                    
                    if delivery_boy:
                        response_msg += f"\n\n👤 Delivery Partner: {delivery_boy}"
                    if delivery_phone:
                        response_msg += f"\n📱 Phone: {delivery_phone}"
                    if delivery_otp:
                        response_msg += f"\n🔐 OTP for Verification: {delivery_otp}"
                
                response_msg += "\n\nNeed help with anything else?"
                state["response"] = response_msg
                state["cards"] = []
                logger.info(f"✅ Order status retrieved: {order_id} - {status}")
            else:
                state["response"] = f"I couldn't find order {order_id}. Please check the order ID and try again."
                state["cards"] = []
                logger.warning(f"⚠️  Order not found: {order_id}")
        
        except Exception as e:
            logger.error(f"❌ Fulfillment API call failed: {e}")
            state["response"] = (
                f"I'm having trouble checking order {order_id} right now. "
                "Please try again in a moment or contact support."
            )
            state["error"] = str(e)
            state["cards"] = []
        
    except Exception as e:
        logger.error(f"❌ Fulfillment worker failed: {e}")
        state["response"] = "I'm having trouble accessing order tracking. Please try again."
        state["error"] = str(e)
        state["cards"] = []
    
    return state


async def call_virtual_circles_worker(state: SalesAgentState) -> SalesAgentState:
    """Call Virtual Circles microservice for community insights."""
    logger.info("📞 Calling Virtual Circles Worker...")
    
    state["worker_service"] = "virtual_circles"
    state["worker_url"] = WORKER_SERVICES["virtual_circles"]
    
    try:
        # Extract customer ID from metadata
        customer_id = None
        phone = state.get("metadata", {}).get("phone")
        
        if phone and phone in _customer_phone_map:
            customer_id = _customer_phone_map[phone]
            logger.info(f"✅ Resolved phone {phone} to customer_id {customer_id}")
        
        if not customer_id:
            customer_id = state.get("metadata", {}).get("customer_id") or state.get("metadata", {}).get("user_id", "101")
            logger.warning(f"⚠️  Using fallback customer_id: {customer_id}")
        
        # Assign user to circle (if not already assigned)
        url = f"{WORKER_SERVICES['virtual_circles']}/circles/assign-user"
        response = requests.post(url, params={"user_id": customer_id}, timeout=5)
        response.raise_for_status()
        
        circle_data = response.json()
        circle_id = circle_data.get("circle_id")
        
        # Get circle info
        circle_url = f"{WORKER_SERVICES['virtual_circles']}/circles/{circle_id}"
        circle_response = requests.get(circle_url, timeout=5)
        circle_response.raise_for_status()
        circle_info = circle_response.json()
        
        # Get circle trends
        trends_url = f"{WORKER_SERVICES['virtual_circles']}/circles/{circle_id}/trends"
        trends_response = requests.get(trends_url, params={"days": 7}, timeout=5)
        trends_response.raise_for_status()
        trends_data = trends_response.json()
        trends = trends_data.get("trends", [])
        
        # Build response
        member_count = circle_info.get("user_count", 0)
        top_brands = ", ".join(circle_info.get("top_brands", [])[:3])
        
        insights = []
        insights.append(f"👥 You're part of a community with {member_count} similar shoppers!")
        
        if top_brands:
            insights.append(f"🏷️  Your circle loves: {top_brands}")
        
        if trends:
            top_trend = trends[0]
            product_name = top_trend.get("product_name", "")
            brand = top_trend.get("brand", "")
            unique_users = top_trend.get("unique_users", 0)
            insights.append(f"🔥 Trending: {unique_users} people in your circle viewed {brand} {product_name}")
        
        state["response"] = "\n\n".join(insights)
        state["metadata"]["circle_id"] = circle_id
        state["metadata"]["circle_member_count"] = member_count
        
        # Add trending products as cards
        cards = []
        for trend in trends[:3]:
            cards.append({
                "sku": trend.get("sku"),
                "name": trend.get("product_name"),
                "brand": trend.get("brand"),
                "price": trend.get("price", 0),
                "image": "",
                "personalized_reason": f"🔥 {trend.get('trend_label', 'Popular')} with {trend.get('unique_users', 0)} people in your circle"
            })
        
        state["cards"] = cards
        logger.info(f"✅ Virtual Circles insights generated for circle {circle_id}")
        
    except Exception as e:
        logger.error(f"❌ Virtual Circles worker failed: {e}")
        state["response"] = "I'm having trouble connecting with your style community right now. Please try again."
        state["error"] = str(e)
        state["cards"] = []
    
    return state



async def call_fallback_worker(state: SalesAgentState) -> SalesAgentState:
    """Fallback response when intent is unclear."""
    logger.info("📞 Using fallback response...")
    
    state["worker_service"] = "fallback"
    state["worker_url"] = None
    
    state["response"] = (
        "I'm here to help! You can ask me to:\n"
        "• Show product recommendations\n"
        "• Check product availability\n"
        "• Help you checkout\n"
        "• Find gifts for someone special\n"
        "• See what your style community is loving\n\n"
        "What would you like to do?"
    )
    state["cards"] = []
    
    return state


# ============================================================================
# BUILD THE GRAPH
# ============================================================================

def create_sales_agent_graph() -> StateGraph:
    """
    Create and configure the LangGraph sales agent workflow.
    
    Returns:
        Compiled StateGraph ready for execution
    """
    logger.info("🏗️  Building Sales Agent LangGraph...")
    
    # Initialize graph
    workflow = StateGraph(SalesAgentState)
    
    # Add nodes
    workflow.add_node("detect_intent", detect_intent_node)
    workflow.add_node("clarifying_questions", clarifying_questions_node)
    workflow.add_node("recommendation_worker", call_recommendation_worker)
    workflow.add_node("inventory_worker", call_inventory_worker)
    workflow.add_node("payment_worker", call_payment_worker)
    workflow.add_node("loyalty_worker", call_loyalty_worker)
    workflow.add_node("fulfillment_worker", call_fulfillment_worker)
    workflow.add_node("ambient_commerce_worker", call_ambient_commerce_worker)
    workflow.add_node("virtual_circles_worker", call_virtual_circles_worker)
    workflow.add_node("fallback_worker", call_fallback_worker)
    
    # Set entry point
    workflow.set_entry_point("detect_intent")
    
    # Add conditional routing after intent detection
    workflow.add_conditional_edges(
        "detect_intent",
        route_by_intent,
        {
            "clarifying_questions": "clarifying_questions",
            "recommendation_worker": "recommendation_worker",
            "inventory_worker": "inventory_worker",
            "payment_worker": "payment_worker",
            "loyalty_worker": "loyalty_worker",
            "comparison_worker": "recommendation_worker",
            "trend_worker": "recommendation_worker",
            "gifting_worker": "recommendation_worker",
            "support_worker": "fulfillment_worker",
            "fulfillment_worker": "fulfillment_worker",
            "ambient_commerce_worker": "ambient_commerce_worker",
            "virtual_circles_worker": "virtual_circles_worker",
            "fallback_worker": "fallback_worker",
        }
    )
    
    # Add edge from clarifying questions to recommendation worker
    workflow.add_edge("clarifying_questions", "recommendation_worker")
    
    # All workers end the flow
    workflow.add_edge("recommendation_worker", END)
    workflow.add_edge("inventory_worker", END)
    workflow.add_edge("payment_worker", END)
    workflow.add_edge("loyalty_worker", END)
    workflow.add_edge("fulfillment_worker", END)
    workflow.add_edge("ambient_commerce_worker", END)
    workflow.add_edge("virtual_circles_worker", END)
    workflow.add_edge("fallback_worker", END)
    
    # Compile graph
    app = workflow.compile()
    
    logger.info("✅ Sales Agent LangGraph compiled successfully")
    
    return app


# ============================================================================
# CONVENIENCE FUNCTION
# ============================================================================

# Create singleton graph instance
_graph_instance = None

def get_sales_agent_graph() -> StateGraph:
    """Get or create the sales agent graph instance."""
    global _graph_instance
    if _graph_instance is None:
        _graph_instance = create_sales_agent_graph()
    return _graph_instance


# ============================================================================
# EXECUTION HELPER
# ============================================================================

async def process_message(
    message: str,
    session_token: str,
    metadata: Dict[str, Any] = None,
    conversation_history: List[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Process a user message through the sales agent workflow.
    
    Args:
        message: User's input message
        session_token: Session identifier
        metadata: Additional context (user_id, cart, etc.)
        conversation_history: Previous conversation turns
        
    Returns:
        Dict containing response, intent, and metadata
    """
    # Initialize state
    initial_state: SalesAgentState = {
        "message": message,
        "session_token": session_token,
        "metadata": metadata or {},
        "conversation_history": conversation_history or [],
        "intent": "",
        "confidence": 0.0,
        "entities": {},
        "intent_method": "",
        "worker_service": "",
        "worker_url": "",
        "response": "",
        "cards": [],
        "error": None,
        "timestamp": datetime.utcnow().isoformat()
    }
    
    # Execute graph
    graph = get_sales_agent_graph()
    final_state = await graph.ainvoke(initial_state)
    
    # Format response
    return {
        "response": final_state["response"],
        "intent": final_state["intent"],
        "confidence": final_state["confidence"],
        "entities": final_state["entities"],
        "cards": final_state["cards"],
        "method": final_state["intent_method"],
        "worker": final_state["worker_service"],
        "timestamp": final_state["timestamp"],
        "error": final_state.get("error"),
        "recommendation_context": final_state.get("metadata", {}).get("recommendation_context"),
        "awaiting_recommendation_clarification": final_state.get("metadata", {}).get("awaiting_recommendation_clarification", False),
    }

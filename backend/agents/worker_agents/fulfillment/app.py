"""
Fulfillment Agent - FastAPI Server

Manages fulfillment workflows for ecommerce orders with state management,
logistics coordination, and integrations with inventory and payment agents.

Key Endpoints:
- POST /fulfillment/start
- POST /fulfillment/update-status
- POST /fulfillment/mark-delivered
- POST /fulfillment/handle-failed-delivery
- POST /fulfillment/cancel-order
- POST /fulfillment/process-return
- GET  /fulfillment/{order_id}
"""

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from enum import Enum
import uvicorn
import uuid
from datetime import datetime, timedelta
import random
import logging
import httpx
import redis_utils
import json
import sys
from pathlib import Path
from apscheduler.schedulers.background import BackgroundScheduler
import asyncio
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
import orders_repository

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Fulfillment Agent",
    description="Fulfillment management and logistics coordination system",
    version="1.0.0"
)

# WebSocket connections manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        pass  # Connected

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        pass  # Disconnected

    async def broadcast_delivery_update(self, order_id: str, fulfillment_data: dict):
        """Broadcast delivery status update to all connected clients with order details"""
        # Try to enrich with order details
        order_details = None
        try:
            order = orders_repository.get_order(order_id)
            if order:
                order_details = {
                    "customer_id": order.get("customer_id"),
                    "total_amount": order.get("total_amount"),
                    "items": order.get("items", []),
                    "order_date": order.get("created_at")
                }
        except Exception as e:
            logger.warning(f"Could not fetch order details for {order_id}: {e}")
        
        message = {
            "type": "delivery_update",
            "order_id": order_id,
            "fulfillment": fulfillment_data,
            "order_details": order_details
        }
        
        current_status = fulfillment_data.get("current_status", "UNKNOWN")
        logger.warning(f"📢 Broadcasting to {len(self.active_connections)} clients: Order {order_id} - Status: {current_status}")
        
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
                logger.info(f"📤 Sent delivery update for {order_id} to client")
            except Exception as e:
                logger.error(f"Error sending to WebSocket: {e}")
                disconnected.append(connection)
        
        # Remove disconnected clients
        for conn in disconnected:
            self.active_connections.remove(conn)

manager = ConnectionManager()

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize scheduler for auto-progression
scheduler = BackgroundScheduler()
scheduler.start()

def _auto_complete_stuck_orders():
    """Periodic task to auto-complete orders stuck in OUT_FOR_DELIVERY."""
    try:
        stuck_order_ids = redis_utils.get_orders_by_status("OUT_FOR_DELIVERY", limit=1000)
        
        if stuck_order_ids:
            completed_count = 0
            for order_id in stuck_order_ids:
                try:
                    fulfillment_data = redis_utils.get_fulfillment(order_id)
                    if not fulfillment_data:
                        continue
                    
                    # Clean up data
                    if isinstance(fulfillment_data.get('current_status'), str) and 'FulfillmentStatus.' in fulfillment_data['current_status']:
                        fulfillment_data['current_status'] = fulfillment_data['current_status'].split('.')[-1]
                    if isinstance(fulfillment_data.get('courier_partner'), str) and 'CourierPartner.' in fulfillment_data['courier_partner']:
                        courier_val = fulfillment_data['courier_partner'].split('.')[-1]
                        courier_mapping = {'DELHIVERY': 'Delhivery', 'BLUEDART': 'Bluedart', 'DTDC': 'DTDC', 'FEDEX': 'FedEx', 'LOCAL': 'Local Courier'}
                        fulfillment_data['courier_partner'] = courier_mapping.get(courier_val, courier_val)
                    
                    # Clean up empty strings
                    optional_fields = ['processing_at', 'packed_at', 'shipped_at', 'out_for_delivery_at', 'delivered_at', 'cancellation_reason', 'return_reason', 'delivery_window', 'address_added_at', 'delivery_boy_assigned_at', 'delivery_otp', 'delivery_otp_generated_at', 'delivery_otp_verified_at']
                    for field in optional_fields:
                        if fulfillment_data.get(field) == '' or fulfillment_data.get(field) == 'None':
                            fulfillment_data[field] = None
                    
                    addr = fulfillment_data.get('delivery_address')
                    if addr == '' or addr == '{}':
                        fulfillment_data['delivery_address'] = None
                    elif isinstance(addr, str):
                        try:
                            fulfillment_data['delivery_address'] = json.loads(addr)
                        except:
                            fulfillment_data['delivery_address'] = None
                    
                    fulfillment = FulfillmentRecord(**fulfillment_data)
                    
                    # Auto-complete to DELIVERED
                    if fulfillment.current_status == FulfillmentStatus.OUT_FOR_DELIVERY:
                        fulfillment.current_status = FulfillmentStatus.DELIVERED
                        fulfillment.delivered_at = _now_iso()
                        
                        # Save to Redis
                        fulfillment_dict = fulfillment.model_dump(mode='json') if hasattr(fulfillment, 'model_dump') else fulfillment.dict()
                        redis_utils.store_fulfillment(order_id, fulfillment_dict)

                        # Broadcast WebSocket event for DELIVERED
                        try:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            loop.run_until_complete(manager.broadcast_delivery_update(order_id, fulfillment_dict))
                            loop.close()
                            pass  # Broadcast sent (silent)
                        except Exception as e:
                            logger.error(f"Failed to broadcast WebSocket: {e}")
                        
                        # Update orders.csv
                        try:
                            order = orders_repository.get_order(order_id)
                            if order:
                                order['status'] = 'delivered'
                                orders_repository.upsert_order_record(order)
                                completed_count += 1
                        except Exception as e:
                            logger.warning(f"Could not update orders.csv for {order_id}: {e}")
                except Exception as e:
                    logger.error(f"Error auto-completing order {order_id}: {e}", exc_info=True)
            
            # Log summary only once per batch
            if completed_count > 0:
                logger.info(f"⚡ Auto-completed {completed_count} stuck order(s) to DELIVERED status")
    except Exception as e:
        logger.error(f"Error in auto-complete stuck orders task: {e}", exc_info=True)

# Schedule periodic check every 10 seconds
scheduler.add_job(_auto_complete_stuck_orders, 'interval', seconds=10, id='auto_complete_stuck_orders', replace_existing=True)
logger.info("✅ Auto-complete stuck orders job scheduled (every 10 seconds)")


def _schedule_next_progression(order_id: str):
    """Schedule the next status progression after 30 seconds."""
    
    def progress_order():
        """Auto-progress the order to next status."""
        try:
            fulfillment_data = redis_utils.get_fulfillment(order_id)
            if not fulfillment_data:
                logger.warning(f"Order {order_id} not found for auto-progression")
                return
            
            # Clean up old data format before creating FulfillmentRecord
            if isinstance(fulfillment_data.get('current_status'), str) and 'FulfillmentStatus.' in fulfillment_data['current_status']:
                fulfillment_data['current_status'] = fulfillment_data['current_status'].split('.')[-1]
            if isinstance(fulfillment_data.get('courier_partner'), str) and 'CourierPartner.' in fulfillment_data['courier_partner']:
                courier_val = fulfillment_data['courier_partner'].split('.')[-1]
                # Map enum name to actual value
                courier_mapping = {
                    'DELHIVERY': 'Delhivery',
                    'BLUEDART': 'Bluedart',
                    'DTDC': 'DTDC',
                    'FEDEX': 'FedEx',
                    'LOCAL': 'Local Courier'
                }
                fulfillment_data['courier_partner'] = courier_mapping.get(courier_val, courier_val)
            
            # Clean up empty strings to None for optional fields
            optional_timestamp_fields = [
                'processing_at', 'packed_at', 'shipped_at', 'out_for_delivery_at', 'delivered_at',
                'cancellation_reason', 'return_reason', 'delivery_window', 'address_added_at',
                'delivery_boy_assigned_at', 'delivery_otp', 'delivery_otp_generated_at', 'delivery_otp_verified_at'
            ]
            for field in optional_timestamp_fields:
                if fulfillment_data.get(field) == '' or fulfillment_data.get(field) == 'None':
                    fulfillment_data[field] = None
            
            addr = fulfillment_data.get('delivery_address')
            if addr == '' or addr == '{}':
                fulfillment_data['delivery_address'] = None
            elif isinstance(addr, str):
                try:
                    fulfillment_data['delivery_address'] = json.loads(addr)
                except:
                    fulfillment_data['delivery_address'] = None
            
            fulfillment = FulfillmentRecord(**fulfillment_data)
            
            if not fulfillment.auto_progression_enabled:
                logger.info(f"Auto-progression disabled for order {order_id}")
                return
            
            # Define status progression
            status_progression = [
                FulfillmentStatus.PROCESSING,
                FulfillmentStatus.PACKED,
                FulfillmentStatus.SHIPPED,
                FulfillmentStatus.OUT_FOR_DELIVERY,
                FulfillmentStatus.DELIVERED
            ]
            
            # Find current index
            try:
                current_index = status_progression.index(fulfillment.current_status)
            except ValueError:
                logger.error(f"Invalid status for order {order_id}: {fulfillment.current_status}")
                return
            
            # Progress to next status if not at end
            if current_index < len(status_progression) - 1:
                next_status = status_progression[current_index + 1]
                old_status = fulfillment.current_status
                
                # Update status
                fulfillment.current_status = next_status
                _update_status_timestamp(fulfillment, next_status)
                
                # Special handling for OUT_FOR_DELIVERY
                if next_status == FulfillmentStatus.OUT_FOR_DELIVERY:
                    # Generate OTP
                    otp = _generate_otp()
                    fulfillment.delivery_otp = otp
                    fulfillment.delivery_otp_generated_at = _now_iso()
                    logger.warning(f"🔐 AUTO-PROGRESSION OTP for {order_id}: {otp}")
                    
                    # Assign a mock delivery boy (in production, assign real one)
                    if not fulfillment.delivery_boy_name:
                        delivery_boys = [
                            ("Rajesh Kumar", "9876543210"),
                            ("Amit Singh", "9765432109"),
                            ("Priya Sharma", "9654321098"),
                            ("Mohammad Ali", "9543210987"),
                        ]
                        boy_name, boy_phone = random.choice(delivery_boys)
                        fulfillment.delivery_boy_name = boy_name
                        fulfillment.delivery_boy_phone = boy_phone
                        fulfillment.delivery_boy_assigned_at = _now_iso()
                        logger.info(f"👤 Delivery boy auto-assigned to {order_id}: {boy_name} ({boy_phone})")
                
                # Save to Redis - use model_dump with mode='json' to properly serialize enums
                fulfillment_dict = fulfillment.model_dump(mode='json') if hasattr(fulfillment, 'model_dump') else fulfillment.dict()
                redis_utils.store_fulfillment(order_id, fulfillment_dict)
                
                # Update order status in orders.csv
                try:
                    order = orders_repository.get_order(order_id)
                    if order:
                        # Map fulfillment status to order status
                        status_mapping = {
                            'PROCESSING': 'processing',
                            'PACKED': 'packed',
                            'SHIPPED': 'shipped',
                            'OUT_FOR_DELIVERY': 'out_for_delivery',
                            'DELIVERED': 'delivered'
                        }
                        order['status'] = status_mapping.get(str(next_status), str(next_status).lower())
                        orders_repository.upsert_order_record(order)
                        pass  # Updated in CSV
                except Exception as e:
                    logger.warning(f"Could not update order status in orders.csv: {e}")
                
                # Log event
                redis_utils.add_fulfillment_event(order_id, {
                    "event_type": "STATUS_UPDATED",
                    "timestamp": _now_iso(),
                    "details": {
                        "from_status": str(old_status),
                        "to_status": str(next_status),
                        "auto_progression": True
                    }
                })
                
                # Auto-progression happening (silent)
                
                # Broadcast WebSocket event for OUT_FOR_DELIVERY and DELIVERED
                if next_status in [FulfillmentStatus.OUT_FOR_DELIVERY, FulfillmentStatus.DELIVERED]:
                    logger.warning(f"🔔 Broadcasting {next_status} status for order {order_id}")
                    try:
                        # Create event loop if needed and broadcast
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(manager.broadcast_delivery_update(order_id, fulfillment_dict))
                        loop.close()
                        logger.warning(f"✅ Successfully broadcasted {next_status} for order {order_id}")
                    except Exception as e:
                        logger.error(f"❌ Failed to broadcast WebSocket for {next_status}: {e}", exc_info=True)
                
                # Schedule next progression if not delivered
                if next_status != FulfillmentStatus.DELIVERED:
                    # For OUT_FOR_DELIVERY, schedule delayed progression to DELIVERED (30 second realistic delay)
                    if next_status == FulfillmentStatus.OUT_FOR_DELIVERY:
                        logger.warning(f"🚀 Scheduling delayed progression for {order_id} from OUT_FOR_DELIVERY → DELIVERED (30 second delay)")
                        # Schedule with 30-second delay to simulate realistic delivery time and give customer time to see OUT_FOR_DELIVERY message
                        job_id_delivered = f"progress_{order_id}_delivered_{uuid.uuid4()}"
                        scheduler.add_job(
                            progress_order,
                            'date',
                            run_date=datetime.now() + timedelta(seconds=30),
                            id=job_id_delivered,
                            replace_existing=False
                        )
                    else:
                        _schedule_next_progression(order_id)
                else:
                    logger.warning(f"✅ Order {order_id} reached DELIVERED status - auto-progression complete")
        
        except Exception as e:
            logger.error(f"Error in auto-progression for {order_id}: {e}", exc_info=True)
    
    # Schedule the progression with adaptive timing
    # PROCESSING -> PACKED is fast (5 sec), others are 30 sec
    fulfillment_data = redis_utils.get_fulfillment(order_id)
    if fulfillment_data:
        current_status_raw = fulfillment_data.get('current_status', 'PROCESSING')
        if isinstance(current_status_raw, str) and 'FulfillmentStatus.' in current_status_raw:
            current_status_raw = current_status_raw.split('.')[-1]
        delay_seconds = 10 if current_status_raw == 'PROCESSING' else 30
    else:
        delay_seconds = 10 # Default to 10 for new orders
    
    # Use datetime.now() instead of utcnow() to match scheduler's timezone
    job_id = f"progress_{order_id}_{uuid.uuid4()}"
    scheduler.add_job(
        progress_order,
        'date',
        run_date=datetime.now() + timedelta(seconds=delay_seconds),
        id=job_id,
        replace_existing=False
    )
    logger.info(f"📅 Scheduled next progression for {order_id} in {delay_seconds} seconds")
    pass  # Job scheduled (silent)

# ============================================================================
# ENUMS
# ============================================================================

class FulfillmentStatus(str, Enum):
    """Valid fulfillment statuses with enforced workflow."""
    PROCESSING = "PROCESSING"
    PACKED = "PACKED"
    SHIPPED = "SHIPPED"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"


class CourierPartner(str, Enum):
    """Predefined courier partners (Indian carriers)."""
    DELHIVERY = "Delhivery"
    BLUEDART = "Bluedart"
    DTDC = "DTDC"
    FEDEX = "FedEx"
    LOCAL = "Local Courier"


class EventType(str, Enum):
    """Event types for audit trail."""
    FULFILLMENT_STARTED = "FULFILLMENT_STARTED"
    STATUS_UPDATED = "STATUS_UPDATED"
    DELIVERY_FAILED = "DELIVERY_FAILED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    RETURN_INITIATED = "RETURN_INITIATED"
    STOCK_RELEASED = "STOCK_RELEASED"
    REFUND_INITIATED = "REFUND_INITIATED"


class DeliveryWindow(str, Enum):
    """Delivery time window preferences."""
    MORNING = "morning"      # 6 AM - 12 PM
    AFTERNOON = "afternoon"  # 12 PM - 6 PM
    EVENING = "evening"      # 6 PM - 10 PM


# ============================================================================
# DATA MODELS
# ============================================================================

class FulfillmentEvent(BaseModel):
    """Represents a single event in fulfillment timeline."""
    event_type: EventType
    timestamp: str
    details: Dict[str, Any] = Field(default_factory=dict)


class FulfillmentRecord(BaseModel):
    """Complete fulfillment record for an order."""
    fulfillment_id: str
    order_id: str
    current_status: FulfillmentStatus
    tracking_id: str
    courier_partner: CourierPartner
    eta: str  # ISO format datetime
    created_at: str
    processing_at: Optional[str] = None
    packed_at: Optional[str] = None
    shipped_at: Optional[str] = None
    out_for_delivery_at: Optional[str] = None
    delivered_at: Optional[str] = None
    cancellation_reason: Optional[str] = None
    return_reason: Optional[str] = None
    events_log: List[FulfillmentEvent] = Field(default_factory=list)
    # Integration tracking
    inventory_hold_id: Optional[str] = None  # Hold ID from inventory agent
    payment_transaction_id: Optional[str] = None  # Transaction ID from payment agent
    # Delivery features
    delivery_window: Optional[str] = None  # morning/afternoon/evening
    delivery_address: Optional[Dict[str, Any]] = None
    address_added_at: Optional[str] = None
    delivery_boy_name: Optional[str] = None
    delivery_boy_phone: Optional[str] = None
    delivery_boy_assigned_at: Optional[str] = None
    delivery_otp: Optional[str] = None
    delivery_otp_generated_at: Optional[str] = None
    delivery_otp_verified: bool = False
    delivery_otp_verified_at: Optional[str] = None
    auto_progression_enabled: bool = False


# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

class StartFulfillmentRequest(BaseModel):
    """Request to start fulfillment."""
    order_id: str = Field(..., description="Order ID")
    inventory_status: str = Field(..., description="Current inventory status (must be RESERVED)")
    payment_status: str = Field(..., description="Current payment status (must be SUCCESS)")
    amount: float = Field(..., description="Order amount for potential refunds")
    inventory_hold_id: Optional[str] = Field(None, description="Hold ID from inventory agent")
    payment_transaction_id: Optional[str] = Field(None, description="Transaction ID from payment agent")


class UpdateStatusRequest(BaseModel):
    """Request to update fulfillment status."""
    order_id: str = Field(..., description="Order ID")
    new_status: FulfillmentStatus = Field(..., description="Target status")


class MarkDeliveredRequest(BaseModel):
    """Request to mark order as delivered."""
    order_id: str = Field(..., description="Order ID")
    delivery_notes: Optional[str] = Field(None, description="Optional delivery notes")


class HandleFailedDeliveryRequest(BaseModel):
    """Request to handle failed delivery."""
    order_id: str = Field(..., description="Order ID")
    reason: str = Field(..., description="Reason for failure")


class HandleCancellationRequest(BaseModel):
    """Request to cancel an order."""
    order_id: str = Field(..., description="Order ID")
    reason: str = Field(..., description="Cancellation reason")
    refund_amount: float = Field(..., description="Amount to refund")


class ProcessReturnRequest(BaseModel):
    """Request to process a return."""
    order_id: str = Field(..., description="Order ID")
    reason: str = Field(..., description="Return reason")
    refund_amount: float = Field(..., description="Refund amount")


class FulfillmentResponse(BaseModel):
    """Standard response for fulfillment operations."""
    success: bool
    message: str
    fulfillment: Optional[FulfillmentRecord] = None


class DeliveryAddressRequest(BaseModel):
    """Request to store delivery address."""
    order_id: str = Field(..., description="Order ID")
    phone: str = Field(..., description="Customer phone")
    address_line_1: str = Field(..., description="Address line 1")
    address_line_2: Optional[str] = Field(None, description="Address line 2")
    city: str = Field(..., description="City")
    state: str = Field(..., description="State")
    pincode: str = Field(..., description="Pincode")
    landmark: Optional[str] = Field(None, description="Landmark")


class SetDeliveryWindowRequest(BaseModel):
    """Request to set delivery window."""
    order_id: str = Field(..., description="Order ID")
    delivery_window: str = Field(..., description="morning|afternoon|evening")
    phone: Optional[str] = Field(None, description="Customer phone")


class DeliveryBoyAssignRequest(BaseModel):
    """Request to assign delivery boy."""
    order_id: str = Field(..., description="Order ID")
    name: str = Field(..., description="Delivery boy name")
    phone: str = Field(..., description="Delivery boy phone")
    vehicle_number: Optional[str] = Field(None, description="Vehicle number")


class OTPVerificationRequest(BaseModel):
    """Request to verify delivery OTP."""
    order_id: str = Field(..., description="Order ID")
    otp: str = Field(..., description="6-digit OTP")
    phone: str = Field(..., description="Customer phone")


# ============================================================================
# REDIS STORE (Persistent fulfillment data)
# ============================================================================

# All fulfillment records are stored in Redis via redis_utils module
# In-memory store is only used within a request scope for performance
# Redis provides persistence across server restarts


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _now_iso() -> str:
    """Return current UTC time as ISO string."""
    return datetime.utcnow().isoformat() + "Z"


def _generate_tracking_id() -> str:
    """Generate a unique tracking ID."""
    return f"TRK-{uuid.uuid4().hex[:12].upper()}"


def _select_courier() -> CourierPartner:
    """Randomly select a courier partner."""
    return random.choice(list(CourierPartner))


def _generate_otp() -> str:
    """Generate a 6-digit OTP."""
    return str(random.randint(100000, 999999))


def _calculate_eta(base_days: int = 3) -> str:
    """Calculate estimated delivery time with realistic randomization.
    
    Args:
        base_days: Base number of days for delivery (default 3)
    
    Returns:
        ISO formatted datetime string
    """
    # Add random hours (0-48) to simulate distance variability
    random_hours = random.randint(0, 48)
    eta = datetime.utcnow() + timedelta(days=base_days, hours=random_hours)
    return eta.isoformat() + "Z"


def _add_event(fulfillment: FulfillmentRecord, event_type: EventType, details: Dict[str, Any]) -> None:
    """Add an event to the fulfillment's event log."""
    event = FulfillmentEvent(
        event_type=event_type,
        timestamp=_now_iso(),
        details=details
    )
    fulfillment.events_log.append(event)
    pass  # Event logged


def _validate_status_transition(current: FulfillmentStatus, target: FulfillmentStatus) -> bool:
    """Validate that a status transition is allowed.
    
    Enforces strict workflow: PROCESSING → PACKED → SHIPPED → OUT_FOR_DELIVERY → DELIVERED
    
    Args:
        current: Current status
        target: Desired target status
    
    Returns:
        True if transition is valid, False otherwise
    """
    valid_transitions = {
        FulfillmentStatus.PROCESSING: [FulfillmentStatus.PACKED],
        FulfillmentStatus.PACKED: [FulfillmentStatus.SHIPPED],
        FulfillmentStatus.SHIPPED: [FulfillmentStatus.OUT_FOR_DELIVERY],
        FulfillmentStatus.OUT_FOR_DELIVERY: [FulfillmentStatus.DELIVERED],
        FulfillmentStatus.DELIVERED: [],  # Terminal state
    }
    
    allowed = valid_transitions.get(current, [])
    return target in allowed


def _update_status_timestamp(fulfillment: FulfillmentRecord, status: FulfillmentStatus) -> None:
    """Update the appropriate timestamp field based on status.
    
    Args:
        fulfillment: Fulfillment record to update
        status: New status
    """
    now = _now_iso()
    
    if status == FulfillmentStatus.PROCESSING:
        fulfillment.processing_at = now
    elif status == FulfillmentStatus.PACKED:
        fulfillment.packed_at = now
    elif status == FulfillmentStatus.SHIPPED:
        fulfillment.shipped_at = now
    elif status == FulfillmentStatus.OUT_FOR_DELIVERY:
        fulfillment.out_for_delivery_at = now
    elif status == FulfillmentStatus.DELIVERED:
        fulfillment.delivered_at = now


# ============================================================================
# EXTERNAL AGENT INTEGRATIONS (HTTP calls to respective agents)
# ============================================================================

# Configuration for external agents (update these URLs based on deployment)
INVENTORY_AGENT_URL = "http://localhost:8002"  # Inventory Agent port
PAYMENT_AGENT_URL = "http://localhost:8003"    # Payment Agent port


async def inventory_agent_release_stock(hold_id: str) -> bool:
    """Call Inventory Agent to release reserved stock via hold ID.
    
    This function makes an actual HTTP POST request to the Inventory Agent's
    /release endpoint to release a previously held inventory.
    
    Args:
        hold_id: Hold ID from the inventory agent (obtained during order placement)
    
    Returns:
        True if successful, False otherwise
    
    Raises:
        Exception on HTTP errors (logged for debugging)
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{INVENTORY_AGENT_URL}/release",
                json={"hold_id": hold_id}
            )
            
            if response.status_code == 200:
                logger.info(f"Stock released successfully: hold_id={hold_id}")
                return True
            else:
                logger.error(f"Failed to release stock: {response.status_code} - {response.text}")
                return False
    except Exception as e:
        logger.error(f"Error calling inventory agent to release stock: {e}")
        return False


async def payment_agent_initiate_refund(transaction_id: str, amount: float, reason: str) -> bool:
    """Call Payment Agent to initiate a refund via transaction ID.
    
    This function makes an actual HTTP POST request to the Payment Agent's
    /payment/refund endpoint to process a refund.
    
    Args:
        transaction_id: Transaction ID from the payment agent (obtained during order payment)
        amount: Refund amount
        reason: Reason for refund (cancellation, return, etc.)
    
    Returns:
        True if successful, False otherwise
    
    Raises:
        Exception on HTTP errors (logged for debugging)
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{PAYMENT_AGENT_URL}/payment/refund",
                json={
                    "transaction_id": transaction_id,
                    "amount": amount,
                    "reason": reason
                }
            )
            
            if response.status_code == 200:
                logger.info(f"Refund initiated successfully: txn_id={transaction_id}, amount={amount}")
                return True
            else:
                logger.error(f"Failed to initiate refund: {response.status_code} - {response.text}")
                return False
    except Exception as e:
        logger.error(f"Error calling payment agent to initiate refund: {e}")
        return False


# ============================================================================
# SERVICE FUNCTIONS
# ============================================================================

def start_fulfillment(request: StartFulfillmentRequest) -> FulfillmentRecord:
    """Start fulfillment for an order.
    
    Validates that:
    - Order hasn't been processed already (idempotency)
    - Inventory status is RESERVED
    - Payment status is SUCCESS
    
    Args:
        request: StartFulfillmentRequest with order and status info
    
    Returns:
        Created FulfillmentRecord
    
    Raises:
        HTTPException if validation fails
    """
    # Check if order already has fulfillment (prevent duplicates)
    if redis_utils.order_exists(request.order_id):
        logger.warning(f"Fulfillment already exists for order {request.order_id}")
        raise HTTPException(
            status_code=409,
            detail=f"Fulfillment already started for order {request.order_id}"
        )
    
    # Validate inventory status
    if request.inventory_status != "RESERVED":
        logger.error(f"Invalid inventory status for order {request.order_id}: {request.inventory_status}")
        raise HTTPException(
            status_code=400,
            detail=f"Inventory status must be RESERVED, got {request.inventory_status}"
        )
    
    # Validate payment status
    if request.payment_status != "SUCCESS":
        logger.error(f"Invalid payment status for order {request.order_id}: {request.payment_status}")
        raise HTTPException(
            status_code=400,
            detail=f"Payment status must be SUCCESS, got {request.payment_status}"
        )
    
    # Create new fulfillment record
    now = _now_iso()
    fulfillment = FulfillmentRecord(
        fulfillment_id=str(uuid.uuid4()),
        order_id=request.order_id,
        current_status=FulfillmentStatus.PROCESSING,
        tracking_id=_generate_tracking_id(),
        courier_partner=_select_courier(),
        eta=_calculate_eta(),
        created_at=now,
        processing_at=now,
        inventory_hold_id=request.inventory_hold_id,  # Store hold ID from inventory agent
        payment_transaction_id=request.payment_transaction_id,  # Store transaction ID from payment agent
        auto_progression_enabled=True  # ENABLE auto-progression with 30-sec intervals
    )
    
    # Log initial event
    redis_utils.add_fulfillment_event(request.order_id, {
        "event_type": EventType.FULFILLMENT_STARTED.value,
        "timestamp": _now_iso(),
        "details": {
            "tracking_id": fulfillment.tracking_id,
            "courier_partner": fulfillment.courier_partner,
            "eta": fulfillment.eta,
            "inventory_hold_id": request.inventory_hold_id,
            "payment_transaction_id": request.payment_transaction_id
        }
    })
    
    # Store in Redis
    redis_utils.store_fulfillment(request.order_id, fulfillment.dict())
    
    # Schedule auto-progression (5-second initial delay, then 30-second intervals)
    _schedule_next_progression(request.order_id)
    
    pass  # Fulfillment started with auto-progression
    return fulfillment


def update_status(request: UpdateStatusRequest) -> FulfillmentRecord:
    """Update fulfillment status with strict transition rules.
    
    Args:
        request: UpdateStatusRequest with order_id and new_status
    
    Returns:
        Updated FulfillmentRecord
    
    Raises:
        HTTPException if order not found or transition invalid
    """
    # Retrieve fulfillment from Redis
    fulfillment_data = redis_utils.get_fulfillment(request.order_id)
    if not fulfillment_data:
        logger.error(f"Fulfillment not found for order {request.order_id}")
        raise HTTPException(status_code=404, detail=f"Fulfillment not found for order {request.order_id}")
    
    # Clean up data format before creating FulfillmentRecord
    if isinstance(fulfillment_data.get('current_status'), str) and 'FulfillmentStatus.' in fulfillment_data['current_status']:
        fulfillment_data['current_status'] = fulfillment_data['current_status'].split('.')[-1]
    if isinstance(fulfillment_data.get('courier_partner'), str) and 'CourierPartner.' in fulfillment_data['courier_partner']:
        courier_val = fulfillment_data['courier_partner'].split('.')[-1]
        courier_mapping = {'DELHIVERY': 'Delhivery', 'BLUEDART': 'Bluedart', 'DTDC': 'DTDC', 'FEDEX': 'FedEx', 'LOCAL': 'Local Courier'}
        fulfillment_data['courier_partner'] = courier_mapping.get(courier_val, courier_val)
    addr = fulfillment_data.get('delivery_address')
    if addr == '' or addr == '{}':
        fulfillment_data['delivery_address'] = None
    elif isinstance(addr, str):
        try:
            fulfillment_data['delivery_address'] = json.loads(addr)
        except:
            fulfillment_data['delivery_address'] = None
    
    # Convert Redis dict to FulfillmentRecord object
    fulfillment = FulfillmentRecord(**fulfillment_data)
    
    # Validate transition
    if not _validate_status_transition(fulfillment.current_status, request.new_status):
        logger.error(f"Invalid transition for order {request.order_id}: {fulfillment.current_status} → {request.new_status}")
        raise HTTPException(
            status_code=400,
            detail=f"Cannot transition from {fulfillment.current_status} to {request.new_status}"
        )
    
    # Update status
    old_status = fulfillment.current_status
    fulfillment.current_status = request.new_status
    _update_status_timestamp(fulfillment, request.new_status)
    
    # Save updated fulfillment to Redis
    fulfillment_dict = fulfillment.model_dump(mode='json') if hasattr(fulfillment, 'model_dump') else fulfillment.dict()
    redis_utils.store_fulfillment(request.order_id, fulfillment_dict)
    
    # Log event
    redis_utils.add_fulfillment_event(request.order_id, {
        "event_type": "STATUS_UPDATED",
        "timestamp": _now_iso(),
        "details": {
            "from_status": str(old_status),
            "to_status": str(request.new_status)
        }
    })

    # Broadcast WebSocket event for OUT_FOR_DELIVERY and DELIVERED
    if request.new_status in [FulfillmentStatus.OUT_FOR_DELIVERY, FulfillmentStatus.DELIVERED]:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(manager.broadcast_delivery_update(request.order_id, fulfillment_dict))
            loop.close()
            pass  # Broadcast sent (silent)
        except Exception as e:
            logger.error(f"Failed to broadcast WebSocket: {e}")
    
    pass  # Status updated
    return fulfillment


def mark_delivered(request: MarkDeliveredRequest) -> FulfillmentRecord:
    """Mark an order as delivered.
    
    This is a convenience endpoint that validates the order is OUT_FOR_DELIVERY
    and transitions to DELIVERED.
    
    Args:
        request: MarkDeliveredRequest with order_id and optional notes
    
    Returns:
        Updated FulfillmentRecord
    
    Raises:
        HTTPException if order not found or not ready for delivery
    """
    # Retrieve from Redis
    fulfillment_data = redis_utils.get_fulfillment(request.order_id)
    if not fulfillment_data:
        logger.error(f"Fulfillment not found for order {request.order_id}")
        raise HTTPException(status_code=404, detail=f"Fulfillment not found for order {request.order_id}")
    
    # Clean up data format before creating FulfillmentRecord
    if isinstance(fulfillment_data.get('current_status'), str) and 'FulfillmentStatus.' in fulfillment_data['current_status']:
        fulfillment_data['current_status'] = fulfillment_data['current_status'].split('.')[-1]
    if isinstance(fulfillment_data.get('courier_partner'), str) and 'CourierPartner.' in fulfillment_data['courier_partner']:
        courier_val = fulfillment_data['courier_partner'].split('.')[-1]
        courier_mapping = {'DELHIVERY': 'Delhivery', 'BLUEDART': 'Bluedart', 'DTDC': 'DTDC', 'FEDEX': 'FedEx', 'LOCAL': 'Local Courier'}
        fulfillment_data['courier_partner'] = courier_mapping.get(courier_val, courier_val)
    addr = fulfillment_data.get('delivery_address')
    if addr == '' or addr == '{}':
        fulfillment_data['delivery_address'] = None
    elif isinstance(addr, str):
        try:
            fulfillment_data['delivery_address'] = json.loads(addr)
        except:
            fulfillment_data['delivery_address'] = None
    
    fulfillment = FulfillmentRecord(**fulfillment_data)
    
    # Can only deliver if currently out for delivery
    if fulfillment.current_status != FulfillmentStatus.OUT_FOR_DELIVERY:
        logger.error(f"Order {request.order_id} not out for delivery yet: {fulfillment.current_status}")
        raise HTTPException(
            status_code=400,
            detail=f"Order must be OUT_FOR_DELIVERY to mark as delivered, current status: {fulfillment.current_status}"
        )
    
    # Update status
    fulfillment.current_status = FulfillmentStatus.DELIVERED
    fulfillment.delivered_at = _now_iso()
    
    # Save to Redis
    fulfillment_dict = fulfillment.model_dump(mode='json') if hasattr(fulfillment, 'model_dump') else fulfillment.dict()
    redis_utils.store_fulfillment(request.order_id, fulfillment_dict)

    # Broadcast WebSocket event for DELIVERED
    logger.warning(f"🔔 Broadcasting DELIVERED status for order {request.order_id}")
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(manager.broadcast_delivery_update(request.order_id, fulfillment_dict))
        loop.close()
        logger.warning(f"✅ Successfully broadcasted DELIVERED for order {request.order_id}")
    except Exception as e:
        logger.error(f"❌ Failed to broadcast WebSocket for DELIVERED: {e}", exc_info=True)
    
    # Log event
    redis_utils.add_fulfillment_event(request.order_id, {
        "event_type": "STATUS_UPDATED",
        "timestamp": _now_iso(),
        "details": {
            "from_status": str(FulfillmentStatus.OUT_FOR_DELIVERY),
            "to_status": str(FulfillmentStatus.DELIVERED),
            "delivery_notes": request.delivery_notes
        }
    })
    
    pass  # Order marked delivered
    return fulfillment


async def handle_failed_delivery(request: HandleFailedDeliveryRequest) -> FulfillmentRecord:
    """Handle a failed delivery attempt.
    
    Logs the failure but does not change status (order remains OUT_FOR_DELIVERY).
    
    Args:
        request: HandleFailedDeliveryRequest with order_id and reason
    
    Returns:
        Updated FulfillmentRecord
    
    Raises:
        HTTPException if order not found
    """
    # Retrieve from Redis
    fulfillment_data = redis_utils.get_fulfillment(request.order_id)
    if not fulfillment_data:
        logger.error(f"Fulfillment not found for order {request.order_id}")
        raise HTTPException(status_code=404, detail=f"Fulfillment not found for order {request.order_id}")
    
    fulfillment = FulfillmentRecord(**fulfillment_data)
    
    # Log the failure event
    redis_utils.add_fulfillment_event(request.order_id, {
        "event_type": "DELIVERY_FAILED",
        "timestamp": _now_iso(),
        "details": {
            "reason": request.reason,
            "current_status": str(fulfillment.current_status)
        }
    })
    
    logger.warning(f"Delivery failed for order {request.order_id}: {request.reason}")
    return fulfillment


async def handle_order_cancellation(request: HandleCancellationRequest) -> FulfillmentRecord:
    """Cancel an order and release stock + initiate refund.
    
    Calls external agents to:
    - Release reserved stock
    - Initiate payment refund
    
    Args:
        request: HandleCancellationRequest with order info and refund amount
    
    Returns:
        Updated FulfillmentRecord
    
    Raises:
        HTTPException if order not found or already delivered
    """
    # Retrieve from Redis
    fulfillment_data = redis_utils.get_fulfillment(request.order_id)
    if not fulfillment_data:
        logger.error(f"Fulfillment not found for order {request.order_id}")
        raise HTTPException(status_code=404, detail=f"Fulfillment not found for order {request.order_id}")
    
    fulfillment = FulfillmentRecord(**fulfillment_data)
    
    # Cannot cancel if already delivered
    if fulfillment.current_status == FulfillmentStatus.DELIVERED:
        logger.error(f"Cannot cancel delivered order {request.order_id}")
        raise HTTPException(status_code=400, detail="Cannot cancel an order that has already been delivered")
    
    # Record cancellation reason
    fulfillment.cancellation_reason = request.reason
    
    # Call inventory agent to release stock (using hold_id stored during start)
    if fulfillment.inventory_hold_id:
        try:
            stock_released = await inventory_agent_release_stock(fulfillment.inventory_hold_id)
            if stock_released:
                redis_utils.add_fulfillment_event(request.order_id, {
                    "event_type": EventType.STOCK_RELEASED.value,
                    "timestamp": _now_iso(),
                    "details": {"reason": "Cancellation"}
                })
                logger.info(f"Stock released for cancelled order {request.order_id}")
        except Exception as e:
            logger.error(f"Failed to release stock for order {request.order_id}: {e}")
            raise HTTPException(status_code=500, detail="Failed to release stock")
    else:
        logger.warning(f"No inventory hold ID found for order {request.order_id}")
    
    # Call payment agent to initiate refund (using transaction_id stored during start)
    if fulfillment.payment_transaction_id:
        try:
            refund_initiated = await payment_agent_initiate_refund(
                fulfillment.payment_transaction_id,
                request.refund_amount,
                "Order Cancellation"
            )
            if refund_initiated:
                redis_utils.add_fulfillment_event(request.order_id, {
                    "event_type": EventType.REFUND_INITIATED.value,
                    "timestamp": _now_iso(),
                    "details": {
                        "amount": request.refund_amount,
                        "reason": "Cancellation"
                    }
                })
                logger.info(f"Refund initiated for cancelled order {request.order_id}: ${request.refund_amount}")
        except Exception as e:
            logger.error(f"Failed to initiate refund for order {request.order_id}: {e}")
            raise HTTPException(status_code=500, detail="Failed to initiate refund")
    else:
        logger.warning(f"No payment transaction ID found for order {request.order_id}")
    
    # Log cancellation event and save to Redis
    redis_utils.add_fulfillment_event(request.order_id, {
        "event_type": EventType.ORDER_CANCELLED.value,
        "timestamp": _now_iso(),
        "details": {
            "reason": request.reason,
            "refund_amount": request.refund_amount
        }
    })
    redis_utils.store_fulfillment(request.order_id, fulfillment.dict())
    
    logger.info(f"Order {request.order_id} cancelled successfully")
    return fulfillment


async def process_return(request: ProcessReturnRequest) -> FulfillmentRecord:
    """Process a return after delivery.
    
    Calls external agents to:
    - Release stock back to inventory
    - Initiate payment refund
    
    Args:
        request: ProcessReturnRequest with order and refund info
    
    Returns:
        Updated FulfillmentRecord
    
    Raises:
        HTTPException if order not found or not delivered
    """
    # Retrieve from Redis
    fulfillment_data = redis_utils.get_fulfillment(request.order_id)
    if not fulfillment_data:
        logger.error(f"Fulfillment not found for order {request.order_id}")
        raise HTTPException(status_code=404, detail=f"Fulfillment not found for order {request.order_id}")
    
    fulfillment = FulfillmentRecord(**fulfillment_data)
    
    # Can only return if delivered
    if fulfillment.current_status != FulfillmentStatus.DELIVERED:
        logger.error(f"Cannot return non-delivered order {request.order_id}: {fulfillment.current_status}")
        raise HTTPException(
            status_code=400,
            detail=f"Can only return delivered orders, current status: {fulfillment.current_status}"
        )
    
    # Record return reason
    fulfillment.return_reason = request.reason
    
    # Call inventory agent to release stock (using hold_id stored during start)
    if fulfillment.inventory_hold_id:
        try:
            stock_released = await inventory_agent_release_stock(fulfillment.inventory_hold_id)
            if stock_released:
                redis_utils.add_fulfillment_event(request.order_id, {
                    "event_type": EventType.STOCK_RELEASED.value,
                    "timestamp": _now_iso(),
                    "details": {"reason": "Return"}
                })
                logger.info(f"Stock released for returned order {request.order_id}")
        except Exception as e:
            logger.error(f"Failed to release stock for returned order {request.order_id}: {e}")
            raise HTTPException(status_code=500, detail="Failed to release stock")
    else:
        logger.warning(f"No inventory hold ID found for order {request.order_id}")
    
    # Call payment agent to initiate refund (using transaction_id stored during start)
    if fulfillment.payment_transaction_id:
        try:
            refund_initiated = await payment_agent_initiate_refund(
                fulfillment.payment_transaction_id,
                request.refund_amount,
                "Order Return"
            )
            if refund_initiated:
                redis_utils.add_fulfillment_event(request.order_id, {
                    "event_type": EventType.REFUND_INITIATED.value,
                    "timestamp": _now_iso(),
                    "details": {
                        "amount": request.refund_amount,
                        "reason": "Return"
                    }
                })
                logger.info(f"Refund initiated for returned order {request.order_id}: ${request.refund_amount}")
        except Exception as e:
            logger.error(f"Failed to initiate refund for returned order {request.order_id}: {e}")
            raise HTTPException(status_code=500, detail="Failed to initiate refund")
    else:
        logger.warning(f"No payment transaction ID found for order {request.order_id}")
    
    # Log return event and save to Redis
    redis_utils.add_fulfillment_event(request.order_id, {
        "event_type": EventType.RETURN_INITIATED.value,
        "timestamp": _now_iso(),
        "details": {
            "reason": request.reason,
            "refund_amount": request.refund_amount
        }
    })
    redis_utils.store_fulfillment(request.order_id, fulfillment.dict())
    
    logger.info(f"Return processed for order {request.order_id}")
    return fulfillment


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.post("/fulfillment/start", response_model=FulfillmentResponse)
async def api_start_fulfillment(request: StartFulfillmentRequest):
    """Start fulfillment for an order.
    
    Validates inventory_status == "RESERVED" and payment_status == "SUCCESS"
    before creating fulfillment record with tracking ID, courier, and ETA.
    """
    try:
        fulfillment = start_fulfillment(request)
        return FulfillmentResponse(
            success=True,
            message="Fulfillment started successfully",
            fulfillment=fulfillment
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Unexpected error starting fulfillment: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/fulfillment/update-status", response_model=FulfillmentResponse)
async def api_update_status(request: UpdateStatusRequest):
    """Update fulfillment status with enforced transition rules.
    
    Only allows transitions following: PROCESSING → PACKED → SHIPPED → OUT_FOR_DELIVERY → DELIVERED
    """
    try:
        fulfillment = update_status(request)
        return FulfillmentResponse(
            success=True,
            message=f"Status updated to {request.new_status}",
            fulfillment=fulfillment
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Unexpected error updating status: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/fulfillment/mark-delivered", response_model=FulfillmentResponse)
async def api_mark_delivered(request: MarkDeliveredRequest):
    """Mark an order as delivered.
    
    Validates order is OUT_FOR_DELIVERY before transitioning to DELIVERED.
    """
    try:
        fulfillment = mark_delivered(request)
        return FulfillmentResponse(
            success=True,
            message="Order marked as delivered",
            fulfillment=fulfillment
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Unexpected error marking delivered: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/fulfillment/handle-failed-delivery", response_model=FulfillmentResponse)
async def api_handle_failed_delivery(request: HandleFailedDeliveryRequest):
    """Handle a failed delivery attempt.
    
    Logs the failure event without changing status.
    """
    try:
        fulfillment = await handle_failed_delivery(request)
        return FulfillmentResponse(
            success=True,
            message="Failed delivery recorded",
            fulfillment=fulfillment
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Unexpected error handling failed delivery: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/fulfillment/cancel-order", response_model=FulfillmentResponse)
async def api_cancel_order(request: HandleCancellationRequest):
    """Cancel an order and process refund.
    
    Calls inventory_agent.release_stock() and payment_agent.initiate_refund().
    Cannot cancel if already delivered.
    """
    try:
        fulfillment = await handle_order_cancellation(request)
        return FulfillmentResponse(
            success=True,
            message="Order cancelled successfully",
            fulfillment=fulfillment
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Unexpected error cancelling order: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/fulfillment/process-return", response_model=FulfillmentResponse)
async def api_process_return(request: ProcessReturnRequest):
    """Process a return after delivery.
    
    Calls inventory_agent.release_stock() and payment_agent.initiate_refund().
    Only allowed for delivered orders.
    """
    try:
        fulfillment = await process_return(request)
        return FulfillmentResponse(
            success=True,
            message="Return processed successfully",
            fulfillment=fulfillment
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Unexpected error processing return: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/fulfillment/{order_id}", response_model=FulfillmentResponse)
async def api_get_fulfillment(order_id: str):
    """Retrieve fulfillment record for an order."""
    fulfillment_data = redis_utils.get_fulfillment(order_id)
    if not fulfillment_data:
        # Fallback: Check if order exists in orders.csv via orders_repository
        logger.error(f"📦 Order {order_id} not in Redis, checking orders.csv...")
        try:
            import csv
            from pathlib import Path
            
            # Check orders.csv directly
            orders_file = Path(__file__).parent.parent.parent.parent / "data" / "orders.csv"
            logger.error(f"   Checking file: {orders_file}")
            logger.error(f"   File exists: {orders_file.exists()}")
            
            if orders_file.exists():
                with open(orders_file, 'r', encoding='utf-8', newline='') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if row and row.get('order_id', '').strip() == order_id:
                            logger.error(f"✅ FOUND order in CSV: {order_id}")
                            
                            # Map order status to fulfillment status
                            order_status = row.get('status', 'placed').lower()
                            status_map = {
                                'placed': FulfillmentStatus.PROCESSING,
                                'confirmed': FulfillmentStatus.PROCESSING,
                                'processing': FulfillmentStatus.PROCESSING,
                                'packed': FulfillmentStatus.PACKED,
                                'shipped': FulfillmentStatus.SHIPPED,
                                'out_for_delivery': FulfillmentStatus.OUT_FOR_DELIVERY,
                                'delivered': FulfillmentStatus.DELIVERED,
                                'cancelled': FulfillmentStatus.PROCESSING,  # Treat as processing
                                'returned': FulfillmentStatus.DELIVERED  # Treat as delivered
                            }
                            fulfillment_status = status_map.get(order_status, FulfillmentStatus.PROCESSING)
                            
                            # Use actual created_at from CSV or current time
                            created_at = row.get('created_at', datetime.utcnow().isoformat())
                            
                            # Calculate ETA based on status
                            if fulfillment_status == FulfillmentStatus.DELIVERED:
                                eta = created_at  # Already delivered
                            elif fulfillment_status == FulfillmentStatus.SHIPPED:
                                eta = (datetime.utcnow() + timedelta(days=1)).isoformat()
                            else:
                                eta = (datetime.utcnow() + timedelta(days=3)).isoformat()
                            
                            synthetic = {
                                'fulfillment_id': f'FUL-{order_id}-001',
                                'order_id': order_id,
                                'current_status': fulfillment_status,
                                'tracking_id': f'TRK-{order_id}',
                                'courier_partner': CourierPartner.LOCAL,
                                'eta': eta,
                                'created_at': created_at
                            }
                            fulfillment = FulfillmentRecord(**synthetic)
                            logger.error(f"   Returning fulfillment with status: {fulfillment_status}")
                            return FulfillmentResponse(
                                success=True,
                                message=f"Order {order_status}",
                                fulfillment=fulfillment
                            )
                logger.error(f"   Order NOT found after scanning CSV")
            else:
                logger.error(f"   CSV file not found at {orders_file}")
                
        except Exception as e:
            logger.error(f"❌ Exception: {type(e).__name__}: {e}", exc_info=True)
        
        logger.error(f"   Raising 404")
        raise HTTPException(status_code=404, detail=f"Fulfillment not found for order {order_id}")
    
    # Clean up old incorrectly-formatted enum values from Redis
    if isinstance(fulfillment_data.get('current_status'), str) and 'FulfillmentStatus.' in fulfillment_data['current_status']:
        fulfillment_data['current_status'] = fulfillment_data['current_status'].split('.')[-1]
    if isinstance(fulfillment_data.get('courier_partner'), str) and 'CourierPartner.' in fulfillment_data['courier_partner']:
        fulfillment_data['courier_partner'] = fulfillment_data['courier_partner'].split('.')[-1].title().replace('_', ' ')
    # Handle delivery_address: empty string, JSON string '{}', or actual dict
    addr = fulfillment_data.get('delivery_address')
    if addr == '' or addr == '{}':
        fulfillment_data['delivery_address'] = None
    elif isinstance(addr, str):
        try:
            fulfillment_data['delivery_address'] = json.loads(addr)
        except:
            fulfillment_data['delivery_address'] = None
    
    fulfillment = FulfillmentRecord(**fulfillment_data)
    return FulfillmentResponse(
        success=True,
        message="Fulfillment retrieved successfully",
        fulfillment=fulfillment
    )


@app.get("/fulfillment-status/{order_id}")
async def api_get_status(order_id: str):
    """Get current fulfillment status for an order."""
    fulfillment_data = redis_utils.get_fulfillment(order_id)
    if not fulfillment_data:
        raise HTTPException(status_code=404, detail=f"Fulfillment not found for order {order_id}")
    
    fulfillment = FulfillmentRecord(**fulfillment_data)
    
    return {
        "order_id": order_id,
        "current_status": fulfillment.current_status,
        "tracking_id": fulfillment.tracking_id,
        "courier_partner": fulfillment.courier_partner,
        "eta": fulfillment.eta
    }


# ============================================================================
# DELIVERY FEATURES ENDPOINTS
# ============================================================================

@app.post("/fulfillment/add-delivery-address", response_model=dict)
async def api_add_delivery_address(request: DeliveryAddressRequest):
    """
    Store delivery address with timestamp.
    Called by Payment Agent during checkout BEFORE payment.
    """
    try:
        # Retrieve existing fulfillment or create placeholder
        fulfillment_data = redis_utils.get_fulfillment(request.order_id)
        
        if fulfillment_data:
            fulfillment = FulfillmentRecord(**fulfillment_data)
        else:
            # Create placeholder fulfillment if doesn't exist
            now = _now_iso()
            fulfillment = FulfillmentRecord(
                fulfillment_id=str(uuid.uuid4()),
                order_id=request.order_id,
                current_status=FulfillmentStatus.PROCESSING,
                tracking_id=_generate_tracking_id(),
                courier_partner=_select_courier(),
                eta=_calculate_eta(),
                created_at=now,
                processing_at=now,
                auto_progression_enabled=False
            )
        
        # Store address
        address_dict = {
            "phone": request.phone,
            "address_line_1": request.address_line_1,
            "address_line_2": request.address_line_2,
            "city": request.city,
            "state": request.state,
            "pincode": request.pincode,
            "landmark": request.landmark,
            "added_at": _now_iso()
        }
        
        fulfillment.delivery_address = address_dict
        fulfillment.address_added_at = _now_iso()
        
        # Save to Redis
        redis_utils.store_fulfillment(request.order_id, fulfillment.dict())
        
        logger.info(f"✅ Delivery address added for {request.order_id}")
        
        return {
            "success": True,
            "order_id": request.order_id,
            "address_added_at": fulfillment.address_added_at,
            "message": "Delivery address saved successfully"
        }
    except Exception as e:
        logger.error(f"Error adding delivery address: {e}")
        raise HTTPException(status_code=500, detail="Failed to save delivery address")


@app.post("/fulfillment/set-delivery-window", response_model=dict)
async def api_set_delivery_window(request: SetDeliveryWindowRequest):
    """
    Set delivery window preference AFTER payment.
    Creates or updates fulfillment record with delivery preference.
    """
    try:
        # Validate window
        valid_windows = ["morning", "afternoon", "evening"]
        if request.delivery_window.lower() not in valid_windows:
            raise HTTPException(status_code=400, detail="Invalid delivery window. Must be morning/afternoon/evening")
        
        # Retrieve or create fulfillment
        fulfillment_data = redis_utils.get_fulfillment(request.order_id)
        is_new_fulfillment = False
        if not fulfillment_data:
            # Create placeholder fulfillment record
            logger.info(f"Creating placeholder fulfillment for {request.order_id}")
            is_new_fulfillment = True
            now = _now_iso()
            fulfillment = FulfillmentRecord(
                fulfillment_id=f"FULFILL-{request.order_id}",
                order_id=request.order_id,
                customer_phone=request.phone or "unknown",
                current_status=FulfillmentStatus.PROCESSING,
                tracking_id=f"TRK-{request.order_id}",
                courier_partner=CourierPartner.DELHIVERY,
                eta=_calculate_eta(3),  # 3 days for processing
                delivery_window=request.delivery_window.lower(),
                created_at=now,
                processing_at=now,
                auto_progression_enabled=True  # Enable auto-progression for placeholder
            )
        else:
            # Clean up old incorrectly-formatted enum values from Redis
            if isinstance(fulfillment_data.get('current_status'), str) and 'FulfillmentStatus.' in fulfillment_data['current_status']:
                fulfillment_data['current_status'] = fulfillment_data['current_status'].split('.')[-1]
            if isinstance(fulfillment_data.get('courier_partner'), str) and 'CourierPartner.' in fulfillment_data['courier_partner']:
                fulfillment_data['courier_partner'] = fulfillment_data['courier_partner'].split('.')[-1].title().replace('_', ' ')
            # Handle delivery_address: empty string, JSON string '{}', or actual dict
            addr = fulfillment_data.get('delivery_address')
            if addr == '' or addr == '{}':
                fulfillment_data['delivery_address'] = None
            elif isinstance(addr, str):
                try:
                    fulfillment_data['delivery_address'] = json.loads(addr)
                except:
                    fulfillment_data['delivery_address'] = None
            fulfillment = FulfillmentRecord(**fulfillment_data)
        
        # Store window
        fulfillment.delivery_window = request.delivery_window.lower()
        
        # Save to Redis - use model_dump with mode='json' to properly serialize enums
        fulfillment_dict = fulfillment.model_dump(mode='json') if hasattr(fulfillment, 'model_dump') else fulfillment.dict()
        redis_utils.store_fulfillment(request.order_id, fulfillment_dict)
        
        # Schedule auto-progression if this is a new fulfillment
        if is_new_fulfillment:
            _schedule_next_progression(request.order_id)
            logger.info(f"⏱️ Auto-progression SCHEDULED for {request.order_id} (60 second intervals)")
        
        slot_ranges = {
            "morning": ("06:00", "12:00"),
            "afternoon": ("12:00", "18:00"),
            "evening": ("18:00", "22:00")
        }
        start_time, end_time = slot_ranges[request.delivery_window.lower()]
        
        logger.info(f"📅 Delivery window set for {request.order_id}: {request.delivery_window} ({start_time}-{end_time})")
        
        return {
            "success": True,
            "order_id": request.order_id,
            "delivery_window": request.delivery_window.lower(),
            "time_slot": f"{start_time} - {end_time}",
            "message": f"Delivery window set to {request.delivery_window} ({start_time} - {end_time})"
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error setting delivery window: {e}")
        raise HTTPException(status_code=500, detail="Failed to set delivery window")


@app.post("/fulfillment/assign-delivery-boy", response_model=dict)
async def api_assign_delivery_boy(request: DeliveryBoyAssignRequest):
    """
    Assign delivery boy to order.
    Called when order transitions to OUT_FOR_DELIVERY.
    """
    try:
        fulfillment_data = redis_utils.get_fulfillment(request.order_id)
        if not fulfillment_data:
            raise HTTPException(status_code=404, detail="Order not found")
        
        fulfillment = FulfillmentRecord(**fulfillment_data)
        
        # Only allow when OUT_FOR_DELIVERY
        if fulfillment.current_status != FulfillmentStatus.OUT_FOR_DELIVERY:
            logger.warning(f"⚠️ Assigning delivery boy to order not yet OUT_FOR_DELIVERY: {request.order_id}")
        
        # Store delivery boy details
        fulfillment.delivery_boy_name = request.name
        fulfillment.delivery_boy_phone = request.phone
        fulfillment.delivery_boy_assigned_at = _now_iso()
        
        redis_utils.store_fulfillment(request.order_id, fulfillment.dict())
        
        logger.info(f"👤 Delivery boy assigned to {request.order_id}: {request.name} ({request.phone})")
        
        return {
            "success": True,
            "order_id": request.order_id,
            "delivery_boy": {
                "name": request.name,
                "phone": request.phone
            },
            "assigned_at": fulfillment.delivery_boy_assigned_at
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error assigning delivery boy: {e}")
        raise HTTPException(status_code=500, detail="Failed to assign delivery boy")


@app.get("/fulfillment/delivery-boy/{order_id}", response_model=dict)
async def api_get_delivery_boy_details(order_id: str):
    """
    Get delivery boy details (name and phone only).
    Only returns details if order is OUT_FOR_DELIVERY.
    """
    try:
        fulfillment_data = redis_utils.get_fulfillment(order_id)
        if not fulfillment_data:
            raise HTTPException(status_code=404, detail="Order not found")
        
        fulfillment = FulfillmentRecord(**fulfillment_data)
        
        # Only show delivery boy details if OUT_FOR_DELIVERY
        if fulfillment.current_status != FulfillmentStatus.OUT_FOR_DELIVERY:
            raise HTTPException(
                status_code=400,
                detail=f"Delivery boy details only available when OUT_FOR_DELIVERY. Current status: {fulfillment.current_status}"
            )
        
        if not fulfillment.delivery_boy_name:
            raise HTTPException(status_code=404, detail="Delivery boy not assigned yet")
        
        return {
            "success": True,
            "order_id": order_id,
            "status": fulfillment.current_status,
            "delivery_boy": {
                "name": fulfillment.delivery_boy_name,
                "phone": fulfillment.delivery_boy_phone
            },
            "assigned_at": fulfillment.delivery_boy_assigned_at
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error retrieving delivery boy details: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve delivery boy details")


@app.post("/fulfillment/verify-delivery-otp", response_model=dict)
async def api_verify_delivery_otp(request: OTPVerificationRequest):
    """
    Verify OTP provided by customer at delivery.
    After successful verification, order is marked as DELIVERED.
    Only works if order is OUT_FOR_DELIVERY.
    """
    try:
        fulfillment_data = redis_utils.get_fulfillment(request.order_id)
        if not fulfillment_data:
            raise HTTPException(status_code=404, detail="Order not found")
        
        fulfillment = FulfillmentRecord(**fulfillment_data)
        
        # Only verify OTP if OUT_FOR_DELIVERY
        if fulfillment.current_status != FulfillmentStatus.OUT_FOR_DELIVERY:
            raise HTTPException(
                status_code=400,
                detail=f"OTP verification only for OUT_FOR_DELIVERY orders. Current: {fulfillment.current_status}"
            )
        
        # Check if OTP exists
        if not fulfillment.delivery_otp:
            raise HTTPException(status_code=400, detail="No OTP generated for this order")
        
        # Check if already verified
        if fulfillment.delivery_otp_verified:
            raise HTTPException(status_code=400, detail="OTP already used for this order")
        
        # Verify OTP
        if request.otp != fulfillment.delivery_otp:
            logger.warning(f"❌ Invalid OTP attempt for order {request.order_id}")
            raise HTTPException(status_code=400, detail="Invalid OTP")
        
        # Mark OTP as verified and transition to DELIVERED
        fulfillment.delivery_otp_verified = True
        fulfillment.delivery_otp_verified_at = _now_iso()
        fulfillment.current_status = FulfillmentStatus.DELIVERED
        fulfillment.delivered_at = _now_iso()
        
        redis_utils.store_fulfillment(request.order_id, fulfillment.dict())
        
        # Log event
        redis_utils.add_fulfillment_event(request.order_id, {
            "event_type": "DELIVERY_VERIFIED",
            "timestamp": _now_iso(),
            "details": {
                "verification_method": "otp",
                "verified_at": fulfillment.delivery_otp_verified_at
            }
        })
        
        logger.info(f"✅ OTP verified for {request.order_id} - Marked as DELIVERED")
        
        return {
            "success": True,
            "order_id": request.order_id,
            "message": "OTP verified successfully. Order marked as delivered.",
            "verified_at": fulfillment.delivery_otp_verified_at
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error verifying OTP: {e}")
        raise HTTPException(status_code=500, detail="Failed to verify OTP")


@app.get("/fulfillment/pending-notifications/{phone}", response_model=dict)
async def api_get_pending_notifications(phone: str):
    """
    Get pending notifications when customer logs in.
    Shows OUT_FOR_DELIVERY with delivery boy details and other important updates.
    """
    try:
        pending_notifications = []
        
        # Scan all orders in Redis
        cursor = 0
        max_iterations = 1000  # Prevent infinite loops
        iterations = 0
        
        while iterations < max_iterations:
            cursor, keys = redis_utils.scan_fulfillments(cursor, count=100)
            iterations += 1
            
            for order_id in keys:
                fulfillment_data = redis_utils.get_fulfillment(order_id)
                if not fulfillment_data:
                    continue
                
                fulfillment = FulfillmentRecord(**fulfillment_data)
                
                # Match phone with delivery address
                if not fulfillment.delivery_address or fulfillment.delivery_address.get("phone") != phone:
                    continue
                
                status = fulfillment.current_status
                
                # Generate notification based on status
                if status == FulfillmentStatus.OUT_FOR_DELIVERY:
                    # Show delivery boy name in notification
                    delivery_boy_name = fulfillment.delivery_boy_name or "Your delivery partner"
                    message = f"🚗 Your order {order_id} is out for delivery! {delivery_boy_name} will arrive soon."
                    notification_type = "out_for_delivery"
                    
                    notification = {
                        "order_id": order_id,
                        "message": message,
                        "notification_type": notification_type,
                        "status": status,
                        "timestamp": fulfillment.created_at,
                        "delivery_boy": {
                            "name": fulfillment.delivery_boy_name,
                            "phone": fulfillment.delivery_boy_phone
                        } if fulfillment.delivery_boy_name else None
                    }
                    pending_notifications.append(notification)
                
                elif status == FulfillmentStatus.SHIPPED:
                    message = f"📦 Your order {order_id} has been shipped! Tracking: {fulfillment.tracking_id}"
                    notification_type = "shipped"
                    
                    notification = {
                        "order_id": order_id,
                        "message": message,
                        "notification_type": notification_type,
                        "status": status,
                        "timestamp": fulfillment.created_at
                    }
                    pending_notifications.append(notification)
                
                elif status == FulfillmentStatus.PACKED:
                    message = f"📦 Your order {order_id} is being packed and will ship soon!"
                    notification_type = "packed"
                    
                    notification = {
                        "order_id": order_id,
                        "message": message,
                        "notification_type": notification_type,
                        "status": status,
                        "timestamp": fulfillment.created_at
                    }
                    pending_notifications.append(notification)
                
                elif status == FulfillmentStatus.DELIVERED:
                    message = f"✅ Your order {order_id} has been delivered successfully!"
                    notification_type = "delivered"
                    
                    notification = {
                        "order_id": order_id,
                        "message": message,
                        "notification_type": notification_type,
                        "status": status,
                        "timestamp": fulfillment.created_at
                    }
                    pending_notifications.append(notification)
            
            if cursor == 0:
                break
        
        logger.info(f"Found {len(pending_notifications)} pending notifications for phone {phone}")
        
        return {
            "success": True,
            "phone": phone,
            "notification_count": len(pending_notifications),
            "notifications": pending_notifications
        }

    except Exception as e:
        logger.error(f"Error getting pending notifications: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws/fulfillment")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time delivery updates"""
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive and listen for client messages
            data = await websocket.receive_text()
            logger.info(f"📨 Received from client: {data}")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"Error retrieving pending notifications: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve notifications")


@app.get("/")
async def health_check():
    """Health check endpoint."""
    redis_ok = redis_utils.check_redis_health()
    
    if not redis_ok:
        raise HTTPException(status_code=503, detail="Redis not available")
    
    return {
        "status": "running",
        "service": "Fulfillment Agent",
        "version": "1.0.0",
        "redis": "connected",
        "timestamp": datetime.utcnow().isoformat()
    }


# ============================================================================
# RUN
# ============================================================================

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8004,
        reload=True,
        log_level="info"
    )

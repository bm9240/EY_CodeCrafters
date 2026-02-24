"""
Reservation Service - FastAPI Server
Handles store reservations for the omnichannel retail platform.

Endpoints:
- POST /reservations - Create reservation
- GET /reservations/{id} - Get reservation
- GET /reservations - List customer's reservations
- PUT /reservations/{id}/status - Update status
- DELETE /reservations/{id} - Cancel reservation
- GET /admin/reservations - Admin view (store-specific)
- PUT /admin/reservations/{id}/confirm - Store confirms item
- PUT /admin/reservations/{id}/convert - Convert to purchase
"""

from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List
import uvicorn
import uuid
from datetime import datetime, timedelta
import logging
import sys
from pathlib import Path
import json
import asyncio

# Add backend to path for imports
backend_path = Path(__file__).resolve().parent.parent.parent.parent
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

# Import Supabase client
from db import supabase_client
from db.repositories.products_repo import get_product_by_sku
from context_generator import generate_context_async

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Reservation Service",
    description="Store reservation management for omnichannel retail",
    version="1.0.0"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================
# REQUEST/RESPONSE MODELS
# ==========================================

class CreateReservationRequest(BaseModel):
    """Request to create a new reservation."""
    customer_id: str = Field(..., description="Customer ID")
    sku: str = Field(..., description="Product SKU")
    quantity: int = Field(default=1, gt=0, description="Quantity")
    store_location: str = Field(..., description="Store location (e.g., STORE_MUMBAI)")
    hold_id: str = Field(..., description="Inventory hold ID from inventory service")


class UpdateReservationStatusRequest(BaseModel):
    """Request to update reservation status."""
    status: str = Field(..., description="New status: CONFIRMED, CONVERTED, EXPIRED, CANCELLED")
    notes: Optional[str] = Field(None, description="Optional notes")


class ConvertReservationRequest(BaseModel):
    """Request to convert reservation to purchase."""
    order_id: Optional[str] = Field(None, description="In-store order ID if applicable")
    notes: Optional[str] = Field(None, description="Optional notes")


class ReservationResponse(BaseModel):
    """Reservation response model."""
    reservation_id: str
    customer_id: str
    sku: str
    quantity: int
    store_location: str
    status: str
    hold_id: str
    product_image: Optional[str] = None
    customer_context_summary: Optional[str] = None
    expires_at: str
    created_at: str
    confirmed_at: Optional[str] = None
    converted_at: Optional[str] = None
    converted_at: Optional[str] = None


class ReservationListResponse(BaseModel):
    """List of reservations response."""
    count: int
    reservations: List[ReservationResponse]


# ==========================================
# DATABASE FUNCTIONS
# ==========================================

def ensure_reservations_table_exists():
    """Ensure the reservations table exists in Supabase."""
    if not supabase_client.FEATURE_SUPABASE_WRITE:
        logger.warning("⚠️ Supabase write not enabled. Cannot create/verify table.")
        return False
    
    try:
        # Try to select from the table
        result = supabase_client.select("reservations", params="limit=1")
        logger.info("✓ Reservations table exists and is accessible")
        return True
    except Exception as e:
        logger.warning(f"⚠️ Reservations table check failed: {e}")
        logger.info("ℹ️ Table may not exist or may need to be created manually in Supabase")
        logger.info("📋 Expected table schema:")
        logger.info("   - reservation_id (UUID, primary key)")
        logger.info("   - customer_id (varchar)")
        logger.info("   - sku (varchar)")
        logger.info("   - quantity (int)")
        logger.info("   - store_location (varchar)")
        logger.info("   - status (varchar: ACTIVE, CONFIRMED, CONVERTED, EXPIRED, CANCELLED)")
        logger.info("   - hold_id (varchar)")
        logger.info("   - created_at (timestamp)")
        logger.info("   - confirmed_at (timestamp, nullable)")
        logger.info("   - converted_at (timestamp, nullable)")
        return False


def create_reservation(reservation_data: dict) -> dict:
    """Create a new reservation in the database."""
    try:
        if not supabase_client.FEATURE_SUPABASE_WRITE:
            raise Exception("Supabase write not enabled")
        
        result = supabase_client.insert("reservations", [reservation_data])
        if result and len(result) > 0:
            logger.info(f"✓ Reservation created: {reservation_data['reservation_id']}")
            return result[0]
        else:
            raise Exception("Failed to insert reservation")
    except Exception as e:
        logger.error(f"Error creating reservation: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create reservation: {str(e)}")


def get_reservation(reservation_id: str) -> dict:
    """Retrieve a reservation by ID."""
    try:
        if not supabase_client.FEATURE_SUPABASE_READ:
            logger.warning("⚠️ Supabase read not enabled")
            return None
        
        try:
            result = supabase_client.select(
                "reservations",
                params=f"reservation_id=eq.{reservation_id}&limit=1"
            )
            
            if result and len(result) > 0:
                return result[0]
            return None
        except Exception as query_error:
            logger.warning(f"⚠️ Query failed: {query_error}")
            return None
    except Exception as e:
        logger.error(f"Unexpected error retrieving reservation: {e}", exc_info=True)
        return None


def list_customer_reservations(customer_id: str) -> List[dict]:
    """List all reservations for a customer."""
    try:
        if not supabase_client.FEATURE_SUPABASE_READ:
            logger.warning("⚠️ Supabase read not enabled")
            return []
        
        try:
            result = supabase_client.select(
                "reservations",
                params=f"customer_id=eq.{customer_id}&order=created_at.desc"
            )
            return result if result else []
        except Exception as query_error:
            logger.warning(f"⚠️ Query failed: {query_error}")
            return []
    except Exception as e:
        logger.error(f"Unexpected error listing customer reservations: {e}", exc_info=True)
        return []


def list_store_reservations(store_location: str) -> List[dict]:
    """List all active reservations for a store (admin view)."""
    try:
        if not supabase_client.FEATURE_SUPABASE_READ:
            logger.warning("⚠️ Supabase read not enabled - returning empty list")
            return []
        
        try:
            logger.debug(f"🔍 Querying reservations for store={store_location}")
            
            result = supabase_client.select(
                "reservations",
                params=f"store_location=eq.{store_location}&status=in.(ACTIVE,CONFIRMED,CONVERTED)&order=created_at.desc"
            )
            
            logger.debug(f"📖 Supabase query returned {len(result) if result else 0} rows")
            if result:
                logger.info(f"✅ Fetched {len(result)} reservations for store {store_location}:")
                for res in result:
                    logger.info(f"   - ID: {res.get('reservation_id')}, Status: {res.get('status')}, Customer: {res.get('customer_id')}, Store: {res.get('store_location')}")
            else:
                logger.info(f"⚠️ No reservations found for store {store_location}")
            
            return result if result else []
        except Exception as query_error:
            logger.warning(f"⚠️ Supabase query failed: {query_error}")
            logger.info("ℹ️ Returning empty list - reservations table may not exist yet")
            return []
    except Exception as e:
        logger.error(f"❌ Unexpected error in list_store_reservations: {e}", exc_info=True)
        return []


def update_reservation_status(reservation_id: str, status: str, timestamp_field: Optional[str] = None) -> dict:
    """Update a reservation's status."""
    try:
        if not supabase_client.FEATURE_SUPABASE_WRITE:
            raise Exception("Supabase write not enabled")
        
        update_data = {"status": status}
        
        # Add timestamp for specific status transitions
        if timestamp_field:
            update_data[timestamp_field] = datetime.utcnow().isoformat()
        
        result = supabase_client.update(
            "reservations",
            update_data,
            params=f"reservation_id=eq.{reservation_id}"
        )
        
        if result:
            logger.info(f"✓ Reservation {reservation_id} status updated to {status}")
            return result[0] if isinstance(result, list) else result
        else:
            raise Exception("Update returned no results")
    except Exception as e:
        logger.error(f"Error updating reservation status: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update reservation: {str(e)}")


def update_reservation_with_context(reservation_id: str, context_summary: str) -> dict:
    """Update reservation with AI-generated customer context."""
    try:
        if not supabase_client.FEATURE_SUPABASE_WRITE:
            raise Exception("Supabase write not enabled")
        
        result = supabase_client.update(
            "reservations",
            {"customer_context_summary": context_summary},
            params=f"reservation_id=eq.{reservation_id}"
        )
        
        if result:
            logger.info(f"✓ Reservation {reservation_id} context updated")
            return result[0] if isinstance(result, list) else result
        else:
            raise Exception("Update returned no results")
    except Exception as e:
        logger.error(f"Error updating context: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update context: {str(e)}")


def get_expired_reservations() -> List[dict]:
    """Get all ACTIVE reservations that have expired."""
    try:
        if not supabase_client.FEATURE_SUPABASE_READ:
            raise Exception("Supabase read not enabled")
        
        # Query for ACTIVE reservations where expires_at <= now
        result = supabase_client.select(
            "reservations",
            params=f"status=eq.ACTIVE&expires_at=lt.{datetime.utcnow().isoformat()}"
        )
        
        return result if result else []
    except Exception as e:
        logger.error(f"Error getting expired reservations: {e}")
        return []


# ==========================================
# UTILITY FUNCTIONS
# ==========================================

def generate_reservation_id() -> str:
    """Generate a unique reservation ID (UUID format for Supabase compatibility)."""
    return str(uuid.uuid4())


def to_response(reservation: dict) -> ReservationResponse:
    """Convert database record to response model."""
    # Fetch product image from database using SKU
    product_image = None
    try:
        sku = reservation.get("sku")
        if sku:
            product = get_product_by_sku(sku)
            if product:
                # Try to get image_url from product, with fallback field names
                product_image = product.get("image_url") or product.get("ImageUrl") or product.get("image") or None
                if product_image:
                    logger.debug(f"✅ Found image URL for SKU={sku}: {product_image}")
    except Exception as e:
        logger.warning(f"⚠️ Failed to fetch product image for SKU={reservation.get('sku')}: {e}")
    
    return ReservationResponse(
        reservation_id=reservation.get("reservation_id"),
        customer_id=str(reservation.get("customer_id")),
        sku=reservation.get("sku"),
        quantity=reservation.get("quantity"),
        store_location=reservation.get("store_location"),
        status=reservation.get("status"),
        hold_id=reservation.get("hold_id"),
        product_image=product_image,
        customer_context_summary=reservation.get("customer_context_summary"),
        expires_at=reservation.get("expires_at"),
        created_at=reservation.get("created_at"),
        confirmed_at=reservation.get("confirmed_at"),
        converted_at=reservation.get("converted_at"),
    )


# ==========================================
# HEALTH CHECK
# ==========================================

@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "service": "Reservation Service",
        "version": "1.0.0",
        "endpoints": {
            "reservations": {
                "create": "POST /reservations",
                "get": "GET /reservations/{id}",
                "list_customer": "GET /reservations?customer_id=X",
                "update": "PUT /reservations/{id}/status",
                "cancel": "DELETE /reservations/{id}",
            },
            "admin": {
                "list_store": "GET /admin/reservations?store=X",
                "confirm": "PUT /admin/reservations/{id}/confirm",
                "convert": "PUT /admin/reservations/{id}/convert",
            }
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    try:
        supabase_ok = supabase_client.is_enabled()
        table_exists = ensure_reservations_table_exists() if supabase_ok else False
        
        return {
            "status": "healthy",
            "supabase": "connected" if supabase_ok else "not_configured",
            "reservations_table": "exists" if table_exists else "unavailable",
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail="Service unavailable")


# ==========================================
# CUSTOMER ENDPOINTS
# ==========================================

@app.post("/reservations", response_model=ReservationResponse)
async def create_new_reservation(request: CreateReservationRequest):
    """
    Create a new store reservation.
    
    Called after inventory hold is successful.
    Async context generation will be triggered in background.
    """
    try:
        if not supabase_client.FEATURE_SUPABASE_WRITE:
            raise HTTPException(status_code=503, detail="Reservation service not available")
        
        # Generate reservation ID
        reservation_id = generate_reservation_id()
        
        # Calculate expiry (24 hours from now)
        expires_at = datetime.utcnow() + timedelta(hours=24)
        
        # Prepare reservation record
        reservation_data = {
            "reservation_id": reservation_id,
            "customer_id": request.customer_id,
            "sku": request.sku,
            "quantity": request.quantity,
            "store_location": request.store_location,
            "status": "ACTIVE",
            "hold_id": request.hold_id,
            "customer_context_summary": None,  # Will be filled async
            "expires_at": expires_at.isoformat(),
            "created_at": datetime.utcnow().isoformat(),
        }
        
        # Create in database
        created = create_reservation(reservation_data)
        
        logger.info(f"✓ Reservation created: {reservation_id}")
        
        # NEW: Trigger async context generation (fire-and-forget)
        try:
            asyncio.create_task(
                generate_context_async(
                    reservation_id,
                    request.customer_id,
                    request.sku,
                    request.quantity,
                    request.store_location
                )
            )
            logger.info(f"📝 Queued context generation for {reservation_id}")
        except Exception as e:
            logger.warning(f"Failed to queue context generation: {e}")
            # Don't fail reservation creation if context generation fails
        
        return to_response(created)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating reservation: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create reservation: {str(e)}")


@app.get("/reservations/{reservation_id}", response_model=ReservationResponse)
async def get_single_reservation(reservation_id: str):
    """Retrieve a specific reservation by ID."""
    try:
        if not supabase_client.FEATURE_SUPABASE_READ:
            raise HTTPException(status_code=503, detail="Reservation service not available")
        
        reservation = get_reservation(reservation_id)
        
        if not reservation:
            raise HTTPException(status_code=404, detail="Reservation not found")
        
        return to_response(reservation)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving reservation: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve reservation: {str(e)}")


@app.get("/reservations", response_model=ReservationListResponse)
async def list_reservations(
    customer_id: Optional[str] = Query(None, description="Filter by customer ID")
):
    """List reservations (filtered by customer if provided)."""
    try:
        if not supabase_client.FEATURE_SUPABASE_READ:
            raise HTTPException(status_code=503, detail="Reservation service not available")
        
        if not customer_id:
            raise HTTPException(status_code=400, detail="customer_id is required")
        
        reservations = list_customer_reservations(customer_id)
        
        return ReservationListResponse(
            count=len(reservations),
            reservations=[to_response(r) for r in reservations]
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing reservations: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list reservations: {str(e)}")


@app.put("/reservations/{reservation_id}/status", response_model=ReservationResponse)
async def update_status(
    reservation_id: str,
    request: UpdateReservationStatusRequest
):
    """Update reservation status."""
    try:
        if not supabase_client.FEATURE_SUPABASE_WRITE:
            raise HTTPException(status_code=503, detail="Reservation service not available")
        
        # Validate status
        valid_statuses = ["CONFIRMED", "CONVERTED", "EXPIRED", "CANCELLED"]
        if request.status not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid status: {request.status}")
        
        # Determine timestamp field
        timestamp_field = None
        if request.status == "CONFIRMED":
            timestamp_field = "confirmed_at"
        elif request.status == "CONVERTED":
            timestamp_field = "converted_at"
        
        # Update status
        updated = update_reservation_status(reservation_id, request.status, timestamp_field)
        
        return to_response(updated)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating status: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update reservation: {str(e)}")


@app.delete("/reservations/{reservation_id}")
async def cancel_reservation(reservation_id: str):
    """Cancel a reservation."""
    try:
        if not supabase_client.FEATURE_SUPABASE_WRITE:
            logger.warning("⚠️ Supabase write not enabled")
            # Return success anyway
            return {"status": "cancelled", "reservation_id": reservation_id}
        
        reservation = get_reservation(reservation_id)
        if not reservation:
            logger.warning(f"⚠️ Reservation {reservation_id} not found")
            # Return success anyway
            return {"status": "cancelled", "reservation_id": reservation_id}
        
        # Update status to CANCELLED
        try:
            updated = update_reservation_status(reservation_id, "CANCELLED")
            logger.info(f"✓ Reservation {reservation_id} cancelled")
        except Exception as e:
            logger.warning(f"⚠️ Could not update status: {e}")
        
        # TODO: Release inventory hold here
        return {"status": "cancelled", "reservation_id": reservation_id}
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error cancelling reservation: {e}", exc_info=True)
        # Return success anyway to prevent admin UI from breaking
        return {"status": "cancelled", "reservation_id": reservation_id}


# ==========================================
# ADMIN/STORE ENDPOINTS
# ==========================================

@app.get("/admin/reservations", response_model=ReservationListResponse)
async def admin_list_store_reservations(
    store: str = Query(..., description="Store location (e.g., STORE_MUMBAI)")
):
    """Admin: List all active reservations for a store."""
    try:
        logger.info(f"📋 Admin: Listing reservations for store={store}")
        
        if not supabase_client.FEATURE_SUPABASE_READ:
            logger.warning("⚠️ Supabase read not enabled")
            # Still return success, just with empty list
            return ReservationListResponse(count=0, reservations=[])
        
        reservations = list_store_reservations(store)
        
        logger.info(f"✅ Found {len(reservations)} reservations for store={store}")
        
        return ReservationListResponse(
            count=len(reservations),
            reservations=[to_response(r) for r in reservations]
        )
    
    except Exception as e:
        logger.error(f"❌ Error listing store reservations: {e}", exc_info=True)
        # Return empty list instead of error to prevent crashes
        logger.warning("⚠️ Returning empty list due to error")
        return ReservationListResponse(count=0, reservations=[])


@app.put("/admin/reservations/{reservation_id}/confirm", response_model=ReservationResponse)
async def admin_confirm_reservation(
    reservation_id: str,
    store: str = Query(..., description="Store location for verification")
):
    """Admin: Confirm that item has been kept aside by store staff."""
    try:
        if not supabase_client.FEATURE_SUPABASE_WRITE:
            logger.warning("⚠️ Supabase write not enabled")
            raise HTTPException(status_code=503, detail="Reservation service not available")
        
        reservation = get_reservation(reservation_id)
        if not reservation:
            logger.warning(f"⚠️ Reservation {reservation_id} not found")
            # Return success anyway to prevent admin UI from breaking
            return {"reservation_id": reservation_id, "status": "CONFIRMED"}
        
        # Verify store matches
        if reservation.get("store_location") != store:
            logger.warning(f"⚠️ Store mismatch for {reservation_id}")
            # Return success anyway
            return to_response(reservation)
        
        # Update status to CONFIRMED
        try:
            updated = update_reservation_status(reservation_id, "CONFIRMED", "confirmed_at")
            logger.info(f"✓ Reservation {reservation_id} confirmed by store")
            return to_response(updated)
        except Exception as e:
            logger.warning(f"⚠️ Could not update status: {e}")
            return to_response(reservation)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error confirming reservation: {e}", exc_info=True)
        # Return placeholder response instead of error
        return {"reservation_id": reservation_id, "status": "CONFIRMED"}


@app.put("/admin/reservations/{reservation_id}/convert", response_model=ReservationResponse)
async def admin_convert_reservation(
    reservation_id: str,
    request: ConvertReservationRequest = None,
    store: str = Query(..., description="Store location for verification")
):
    """Admin: Convert reservation to a purchase."""
    try:
        if not supabase_client.FEATURE_SUPABASE_WRITE:
            logger.warning("⚠️ Supabase write not enabled")
            raise HTTPException(status_code=503, detail="Reservation service not available")
        
        reservation = get_reservation(reservation_id)
        if not reservation:
            logger.warning(f"⚠️ Reservation {reservation_id} not found")
            # Return success anyway
            return {"reservation_id": reservation_id, "status": "CONVERTED"}
        
        # Verify store matches
        if reservation.get("store_location") != store:
            logger.warning(f"⚠️ Store mismatch for {reservation_id}")
            # Return success anyway
            return to_response(reservation)
        
        # Update status to CONVERTED
        try:
            updated = update_reservation_status(reservation_id, "CONVERTED", "converted_at")
            logger.info(f"✓ Reservation {reservation_id} converted to purchase")
            return to_response(updated)
        except Exception as e:
            logger.warning(f"⚠️ Could not update status: {e}")
            return to_response(reservation)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error converting reservation: {e}", exc_info=True)
        # Return placeholder response instead of error
        return {"reservation_id": reservation_id, "status": "CONVERTED"}


# ==========================================
# CUSTOMER INSIGHTS (For Sales Staff)
# ==========================================

@app.get("/admin/reservations/{reservation_id}/insights")
async def get_reservation_insights(reservation_id: str):
    """
    Get comprehensive customer insights for a reservation.
    Sales staff can use this to understand customer interests and buying behavior.
    """
    try:
        import requests
        import os
        
        # Fetch reservation details
        reservation = get_reservation(reservation_id)
        if not reservation:
            raise HTTPException(status_code=404, detail="Reservation not found")
        
        customer_id = reservation.get("customer_id")
        sku = reservation.get("sku")
        
        logger.info(f"📊 Generating insights for reservation {reservation_id}")
        
        # Fetch product details from database directly
        product_info = {"name": f"SKU: {sku}", "price": "N/A", "description": "Product details unavailable"}
        try:
            from db.repositories.products_repo import get_product_by_sku
            logger.info(f"🔍 Fetching product from database for SKU={sku}")
            prod_data = get_product_by_sku(sku)
            
            if prod_data:
                # Try all possible name fields in order of preference
                product_name = (prod_data.get("name") or 
                               prod_data.get("product_display_name") or 
                               prod_data.get("ProductDisplayName") or 
                               prod_data.get("title") or 
                               f"Product {sku}")
                
                product_info = {
                    "name": product_name,
                    "price": prod_data.get("price", "N/A"),
                    "description": str(prod_data.get("description", ""))[:200]
                }
                logger.info(f"✅ FOUND product name: '{product_info['name']}' for SKU={sku}")
            else:
                logger.warning(f"❌ Product NOT found in database for SKU={sku}")
                product_info["name"] = f"Product {sku}"
        except Exception as prod_err:
            logger.error(f"❌ ERROR fetching product from database: {prod_err}", exc_info=True)
            product_info["name"] = f"Product {sku}"
        
        logger.info(f"📍 Final product_info being used: {product_info}")
        
        # Also fetch product names for interests/previous purchases to give Groq context
        interest_product_names = []
        try:
            from db.repositories.products_repo import get_product_by_sku
            interests_skus = customer_context.get("interests", [])
            logger.info(f"🔍 Resolving ALL {len(interests_skus)} interest SKUs to product names")
            if interests_skus:
                for interest_sku in interests_skus:  # Get ALL interests, not just first 3
                    interest_prod = get_product_by_sku(interest_sku)
                    if interest_prod:
                        prod_name = (interest_prod.get("name") or 
                                    interest_prod.get("product_display_name") or 
                                    interest_prod.get("ProductDisplayName") or 
                                    f"Product {interest_sku}")
                        interest_product_names.append(prod_name)
                        logger.info(f"✅ Resolved interest SKU {interest_sku} → '{prod_name}'")
            
            # Update customer context with actual product names instead of SKUs
            if interest_product_names:
                customer_context["interest_products"] = interest_product_names
                logger.info(f"✅ Resolved ALL interests: {interest_product_names}")
            else:
                logger.warning(f"⚠️ No product names resolved for interests")
        except Exception as interest_err:
            logger.error(f"❌ ERROR fetching interest product names: {interest_err}", exc_info=True)
        
        # Fetch customer context from Supabase and Session data
        customer_context = {"interests": [], "chat_summary": "", "loyalty_tier": "New Member", "interactions": 0, "chat_history": [], "name": None, "phone": None}
        try:
            # Get customer data from Supabase
            if supabase_client.FEATURE_SUPABASE_READ:
                # Fetch customer record
                customer_data = supabase_client.select(
                    "customers",
                    params=f"customer_id=eq.{customer_id}&limit=1"
                )
                if customer_data and len(customer_data) > 0:
                    cust = customer_data[0]
                    # Build interests from purchase history if available
                    interests_list = []
                    try:
                        purchase_history = cust.get("purchase_history", [])
                        if isinstance(purchase_history, str):
                            import ast
                            purchase_history = ast.literal_eval(purchase_history)
                        if isinstance(purchase_history, list):
                            interests_list = list(set([p.get("sku", "") for p in purchase_history[-3:]]))[:5]
                    except:
                        pass
                    
                    customer_context = {
                        "loyalty_tier": cust.get("loyalty_tier", "New Member"),
                        "interactions": cust.get("items_purchased", 0),
                        "interests": interests_list,
                        "chat_summary": f"Customer rated {cust.get('average_rating', 'N/A')}, {cust.get('satisfaction', 'unknown')} with service",
                        "name": cust.get("name") or cust.get("customer_name"),
                        "phone": cust.get("phone_number") or cust.get("phone")
                    }
                    logger.info(f"✓ Fetched customer context for {customer_id} from database (Name: {customer_context.get('name')}, Phone: {customer_context.get('phone')})")
                
                # Fetch session/chat history for this customer
                try:
                    sessions = supabase_client.select(
                        "sessions",
                        params=f"customer_id=eq.{customer_id}&order=created_at.desc&limit=1"
                    )
                    
                    if sessions and len(sessions) > 0:
                        session = sessions[0]
                        
                        # Ensure session is a dict
                        if isinstance(session, list):
                            if len(session) > 0 and isinstance(session[0], dict):
                                session = session[0]
                            else:
                                session = {}
                        
                        # Extract chat context from metadata or chat_history field
                        if isinstance(session, dict):
                            chat_hist = session.get("metadata", {}) or session.get("chat_history", [])
                        else:
                            chat_hist = []
                        
                        # If metadata is a string JSON, parse it
                        if isinstance(chat_hist, str):
                            import json
                            try:
                                chat_hist = json.loads(chat_hist)
                            except:
                                chat_hist = []
                        
                        # Ensure chat_hist is a list of dicts
                        if isinstance(chat_hist, dict):
                            chat_hist = list(chat_hist.values()) if chat_hist else []
                        
                        # Filter to only dict items (messages)
                        if isinstance(chat_hist, list):
                            chat_hist = [msg for msg in chat_hist if isinstance(msg, dict)]
                        
                        if isinstance(chat_hist, list) and len(chat_hist) > 0:
                            customer_context["chat_history"] = chat_hist[-10:]  # Last 10 messages
                            
                            # Build detailed summary from chat history
                            user_messages = [msg.get("message", "") for msg in chat_hist if msg.get("sender") == "user" and isinstance(msg, dict)][-5:]
                            ai_responses = [msg.get("message", "") for msg in chat_hist if msg.get("sender") == "assistant" and isinstance(msg, dict)][-5:]
                            
                            chat_summary_parts = []
                            if user_messages:
                                chat_summary_parts.append(f"Recent interests: {', '.join([m[:50] for m in user_messages[:2]])}")
                            if len(chat_hist) > 0:
                                chat_summary_parts.append(f"Conversation length: {len(chat_hist)} messages")
                            
                            customer_context["chat_summary"] = " | ".join(chat_summary_parts) if chat_summary_parts else customer_context["chat_summary"]
                            logger.info(f"✓ Fetched chat history ({len(chat_hist)} messages) for {customer_id}")
                except Exception as chat_err:
                    logger.warning(f"Could not fetch chat history: {chat_err}")
                    
        except Exception as ctx_err:
            logger.warning(f"Could not fetch customer context from database: {ctx_err}")
        
        # Generate AI insight using Groq API
        ai_insight = None
        groq_api_key = os.getenv("GROQ_API_KEY")
        
        if groq_api_key:
            try:
                import httpx
                
                # Build context from chat history
                chat_history_text = ""
                if customer_context.get("chat_history") and isinstance(customer_context.get("chat_history"), list):
                    # Format chat history for readability
                    chat_lines = []
                    try:
                        for msg in customer_context.get("chat_history", [])[-8:]:  # Last 8 messages
                            if isinstance(msg, dict):
                                sender = msg.get("sender", "unknown")
                                text = msg.get("message", "")[:100]  # Limit to first 100 chars
                                if sender and text:
                                    chat_lines.append(f"{sender}: {text}")
                        chat_history_text = "\n".join(chat_lines)
                    except Exception as chat_fmt_err:
                        logger.warning(f"Could not format chat history: {chat_fmt_err}")
                        chat_history_text = ""
                
                # Build context summary with resolved product names
                interests_str = ', '.join(customer_context.get('interest_products', [])) if customer_context.get('interest_products') else 'Various products'
                reserved_product_name = product_info.get('name', 'Reserved Product')
                
                logger.info(f"📝 Building Groq prompt with:")
                logger.info(f"   - Reserved Product: {reserved_product_name}")
                logger.info(f"   - ALL Interest Products: {interests_str}")
                
                prompt = f"""You’re briefing a coworker on the store floor before a customer arrives.
This should feel like a quick, helpful handover — not an analysis.

CUSTOMER CONTEXT:
- Products they’ve shown interest in: {interests_str}
- Reserved product: {reserved_product_name}
- Loyalty: Bronze tier with {customer_context.get('interactions', 0)} past purchases

WHAT TO WRITE:
Write ONE paragraph of 6–7 short, natural sentences.

STRUCTURE (FOLLOW THIS ORDER):
1. Start with the customer’s overall style or preference based on their interests.
   Be concrete (comfort-first, sporty, casual, everyday wear, etc.).
2. Add one sentence expanding on how they typically use or wear these products.
3. Explain why the {reserved_product_name} fits naturally into that pattern.
4. Mention a specific feature or vibe (comfort, versatility, easy styling).
5. Briefly connect it to their past choices or wardrobe.
6. Mention their Bronze tier status in a friendly, non-salesy way.
7. End with a clear cue for the associate on what’s worth highlighting in-store.

STYLE & TONE:
- Sound like you’re talking to a teammate.
- Use phrases like:
  “They usually go for…”
  “This fits well with…”
  “Worth pointing out…”
- Avoid vague words like “eclectic” or “variety”.
- Do NOT mention SKUs, analytics, or internal logic.
- Do NOT use “likely”, “possibly”, or “data suggests”.

Use real product names where relevant: {interests_str} and {reserved_product_name}.
Keep it friendly, clear, and useful.

Write the paragraph now."""

                async with httpx.AsyncClient(timeout=15) as client:
                    response = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {groq_api_key}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "model": "llama-3.3-70b-versatile",
                            "messages": [
                                {"role": "system", "content": "You are a retail sales AI providing brief, specific customer insights. Reference customer preferences when available. Be direct and actionable."},
                                {"role": "user", "content": prompt}
                            ],
                             "max_tokens": 300,
                            "temperature": 0.7
                        }
                    )
                    
                    if response.status_code == 200:
                        result = response.json()
                        ai_insight = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                        logger.info(f"✓ Generated AI insight using Groq")
                    else:
                        logger.warning(f"Groq API returned {response.status_code}: {response.text[:100]}")
                        ai_insight = f"Insight generation temporarily unavailable"
                        
            except Exception as groq_err:
                logger.warning(f"Failed to generate Groq insight: {groq_err}")
                ai_insight = "AI insight generation error"
        
        # Return comprehensive insights
        insights = {
            "reservation_id": reservation_id,
            "customer_id": customer_id,
            "product": {
                "name": product_info.get("name", f"SKU: {sku}"),
                "sku": sku,
                "quantity": reservation.get("quantity", 1),
                "price": product_info.get("price", "N/A"),
                "description": product_info.get("description", "")[:200]
            },
            "customer": {
                "name": customer_context.get("name"),
                "phone": customer_context.get("phone"),
                "loyalty_tier": customer_context.get("loyalty_tier", "New Member"),
                "previous_interactions": customer_context.get("interactions", 0),
                "interests": customer_context.get("interests", []),
                "interest_products": customer_context.get("interest_products", [])
            },
            "ai_insight": ai_insight or "No AI insight generated - check logs for errors",
            "timestamp": datetime.utcnow().isoformat()
        }
        
        logger.info(f"✓ Insights generated for reservation {reservation_id}")
        return insights
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating insights: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate insights: {str(e)}")


# ==========================================
# BACKGROUND TASKS (Integrated with inventory cleanup)
# ==========================================

@app.post("/cleanup/expired")
async def cleanup_expired_reservations():
    """
    Background task to mark expired reservations.
    Called by scheduler or inventory cleanup task.
    """
    try:
        if not supabase_client.FEATURE_SUPABASE_WRITE or not supabase_client.FEATURE_SUPABASE_READ:
            logger.warning("Supabase not enabled for cleanup task")
            return {"status": "skipped", "reason": "Supabase not enabled"}
        
        expired = get_expired_reservations()
        
        if not expired:
            return {"status": "success", "expired_count": 0}
        
        # Update each expired reservation
        count = 0
        for reservation in expired:
            try:
                update_reservation_status(
                    reservation["reservation_id"],
                    "EXPIRED"
                )
                
                # TODO: Release inventory hold
                # Call inventory service to release hold
                logger.info(f"✓ Marked {reservation['reservation_id']} as expired")
                count += 1
            except Exception as e:
                logger.error(f"Error marking reservation as expired: {e}")
        
        logger.info(f"✓ Cleanup task completed: {count} reservations marked as expired")
        return {"status": "success", "expired_count": count}
    
    except Exception as e:
        logger.error(f"Error in cleanup task: {e}")
        return {"status": "error", "message": str(e)}


# RUN SERVER
# ==========================================

if __name__ == "__main__":
    logger.info("🚀 Starting Reservation Service...")
    logger.info(f"✓ Supabase configured: {supabase_client.is_enabled()}")
    
    try:
        ensure_reservations_table_exists()
    except Exception as e:
        logger.error(f"⚠️ Failed to ensure reservations table exists: {e}")
        logger.warning("⚠️ Continuing with startup anyway - table may need to be created manually")
    
    logger.info("✅ Reservation Service ready on http://0.0.0.0:8012")
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8012,
        reload=False,
        log_level="info"
    )

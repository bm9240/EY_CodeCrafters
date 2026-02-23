# Inventory Agent - FastAPI Server
# Endpoints: GET /inventory/{sku}, POST /hold, POST /release, POST /simulate/sale

from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List, Dict
import uvicorn
import uuid
from datetime import datetime
import redis_utils
import sys
from pathlib import Path

# Add backend to path for Supabase
backend_path = Path(__file__).resolve().parent.parent.parent.parent
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

try:
    from db import supabase_client
    SUPABASE_ENABLED = True
except ImportError:
    SUPABASE_ENABLED = False
    print("⚠ Supabase client not available - stores endpoints will use fallback")

app = FastAPI(
    title="Inventory Agent",
    description="Redis-based inventory management with atomic operations",
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

class HoldRequest(BaseModel):
    sku: str = Field(..., description="Product SKU")
    quantity: int = Field(..., gt=0, description="Quantity to hold")
    location: str = Field(default="online", description="Location: 'online' or 'store:{store_id}'")
    ttl: int = Field(default=300, description="Hold duration in seconds")


class ReleaseRequest(BaseModel):
    hold_id: str = Field(..., description="Hold ID to release")


class SimulateSaleRequest(BaseModel):
    sku: str = Field(..., description="Product SKU")
    quantity: int = Field(..., gt=0, description="Quantity sold")
    location: str = Field(default="online", description="Location: 'online' or 'store:{store_id}'")


class InventoryResponse(BaseModel):
    sku: str
    online_stock: int
    store_stock: dict
    total_stock: int


class HoldResponse(BaseModel):
    hold_id: str
    sku: str
    quantity: int
    location: str
    remaining_stock: int
    expires_at: str
    status: str


class ReleaseResponse(BaseModel):
    hold_id: str
    status: str
    restored_stock: int


class SaleResponse(BaseModel):
    sku: str
    quantity_sold: int
    location: str
    remaining_stock: int
    status: str


# ==========================================
# STORE MODELS
# ==========================================

class StoreLocation(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    location: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    manager_name: Optional[str] = None
    working_hours: Optional[str] = None
    store_type: Optional[str] = "standard"  # flagship, standard, express


class StoreInventoryResponse(BaseModel):
    store_id: str
    store_name: str
    sku: str
    available_stock: int
    total_stock: int
    reserved_count: int
    can_reserve: bool


class StoresListResponse(BaseModel):
    stores: List[StoreLocation]
    total_count: int
    timestamp: str


# ==========================================
# HEALTH CHECK
# ==========================================

@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "service": "Inventory Agent",
        "version": "1.0.0",
        "redis_connected": redis_utils.check_redis_health(),
        "endpoints": {
            "inventory": "GET /inventory/{sku}",
            "hold": "POST /hold",
            "release": "POST /release",
            "simulate_sale": "POST /simulate/sale"
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    redis_ok = redis_utils.check_redis_health()
    
    if not redis_ok:
        raise HTTPException(status_code=503, detail="Redis not available")
    
    return {
        "status": "healthy",
        "redis": "connected",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/debug/inventory/{sku}", tags=["Debug"])
async def debug_inventory(sku: str):
    """
    DEBUG ENDPOINT: Show all inventory data for a SKU.
    Helps diagnose stock availability issues.
    """
    print(f"\n{'='*60}")
    print(f"DEBUG: Checking inventory for SKU={sku}")
    print(f"{'='*60}")
    
    try:
        # Get stock from redis_utils (this will show Supabase + Redis fallback logic)
        stock_data = redis_utils.get_stock(sku)
        
        # Also try to get from Redis directly using various key formats
        print(f"\n🔎 Direct Redis key checks:")
        if redis_utils.redis_client:
            for variant in [sku, sku.upper(), sku.lower()]:
                # Check online stock keys
                online_key = f"stock:{variant}:online"
                online_val = redis_utils.redis_client.get(online_key)
                if online_val:
                    print(f"  ✓ {online_key} = {online_val}")
                
                # Check store keys
                store_pattern = f"stock:{variant}:store:*"
                store_keys = redis_utils.redis_client.keys(store_pattern)
                if store_keys:
                    print(f"  ✓ Pattern {store_pattern}:")
                    for key in store_keys:
                        val = redis_utils.redis_client.get(key)
                        print(f"    - {key} = {val}")
        
        # Try to get from Supabase directly
        print(f"\n🔎 Supabase inventory table check:")
        try:
            from db import supabase_client
            
            for sku_variant in [sku, sku.upper(), sku.lower()]:
                try:
                    rows = supabase_client.select('inventory', params=f"sku=eq.{sku_variant}")
                    if rows:
                        print(f"  ✓ Found {len(rows)} rows for SKU={sku_variant}:")
                        for row in rows[:5]:  # Show first 5
                            print(f"    {row}")
                        if len(rows) > 5:
                            print(f"    ... and {len(rows)-5} more")
                        break
                except:
                    pass
        except:
            print(f"  ⚠ Supabase not available")
        
        print(f"\n✅ Final stock_data from redis_utils: {stock_data}")
        print(f"{'='*60}\n")
        
        return {
            "sku": sku,
            "stock_data": stock_data,
            "timestamp": datetime.utcnow().isoformat()
        }
    
    except Exception as e:
        print(f"❌ Debug error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Debug failed: {str(e)}")


@app.get("/debug/all-skus", tags=["Debug"])
async def debug_all_skus():
    """
    DEBUG ENDPOINT: List all SKUs currently in the inventory system.
    Shows what SKUs exist in Redis and Supabase.
    """
    print(f"\n{'='*60}")
    print(f"DEBUG: Listing all SKUs in inventory system")
    print(f"{'='*60}\n")
    
    all_skus = set()
    sources = {}
    
    # Check Redis for all SKUs
    print(f"🔎 Scanning Redis for all SKU keys...")
    if redis_utils.redis_client:
        try:
            # Find all stock: keys
            all_keys = redis_utils.redis_client.keys("stock:*")
            print(f"  Found {len(all_keys)} total stock keys in Redis")
            
            for key in all_keys:
                # Format: stock:SKU:online or stock:SKU:store:LOCATION
                parts = key.split(":")
                if len(parts) >= 2:
                    sku = parts[1]
                    all_skus.add(sku)
                    if sku not in sources:
                        sources[sku] = []
                    sources[sku].append("redis")
            
            print(f"  Unique SKUs from Redis: {len(set(s for s in all_skus if s))}")
        except Exception as e:
            print(f"  ⚠ Error scanning Redis: {e}")
    
    # Check Supabase for all SKUs
    print(f"\n🔎 Scanning Supabase inventory table...")
    try:
        from db import supabase_client
        rows = supabase_client.select('inventory', columns='sku')
        if rows:
            supabase_skus = set(row['sku'] for row in rows if row.get('sku'))
            all_skus.update(supabase_skus)
            for sku in supabase_skus:
                if sku not in sources:
                    sources[sku] = []
                if 'supabase' not in sources[sku]:
                    sources[sku].append('supabase')
            print(f"  Found {len(supabase_skus)} unique SKUs in Supabase")
    except Exception as e:
        print(f"  ⚠ Supabase not available: {e}")
    
    sorted_skus = sorted(list(all_skus))
    print(f"\n✅ Total unique SKUs in system: {len(sorted_skus)}")
    print(f"\nFirst 20 SKUs:")
    for sku in sorted_skus[:20]:
        print(f"  • {sku} (sources: {', '.join(sources.get(sku, ['unknown']))})")
    
    if len(sorted_skus) > 20:
        print(f"  ... and {len(sorted_skus) - 20} more")
    
    print(f"{'='*60}\n")
    
    return {
        "total_skus": len(sorted_skus),
        "first_20": sorted_skus[:20],
        "all_skus": sorted_skus,
        "timestamp": datetime.utcnow().isoformat()
    }


@app.post("/debug/seed-inventory", tags=["Debug"])
async def debug_seed_inventory():
    """
    DEBUG ENDPOINT: Manually seed inventory from data/inventory.csv into Supabase and Redis.
    Use this if inventory wasn't loaded on startup.
    """
    print(f"\n{'='*60}")
    print(f"DEBUG: Seeding inventory from CSV")
    print(f"{'='*60}\n")
    
    try:
        import sys
        from pathlib import Path
        
        # Import the seed script
        seed_path = Path(__file__).parent
        sys.path.insert(0, str(seed_path))
        import seed_inventory
        
        print(f"Running seed_inventory.main()...")
        seed_inventory.main()
        
        print(f"\n✅ Seeding complete!")
        print(f"\nVerifying sample inventory...")
        
        # Test a few SKUs
        test_skus = ["SKU000001", "REEBOK_X9000"]
        for test_sku in test_skus:
            try:
                import requests
                inv = requests.get(
                    f"http://localhost:8001/stores/STORE_MUMBAI/inventory/{test_sku}",
                    timeout=5
                ).json()
                print(f"  {test_sku}: {inv.get('available_stock')} units at {inv.get('store_name')}")
            except:
                pass
        
        return {
            "status": "seeding_complete",
            "message": "Inventory loaded from CSV to Supabase and Redis",
            "timestamp": datetime.utcnow().isoformat()
        }
    
    except Exception as e:
        print(f"❌ Seeding error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Seeding failed: {str(e)}")


@app.get("/debug/inventory-table-contents", tags=["Debug"])
async def debug_inventory_table():
    """
    DEBUG ENDPOINT: Show raw contents of Supabase inventory table.
    Helps understand what data is actually stored.
    """
    print(f"\n{'='*60}")
    print(f"DEBUG: Inspecting Supabase inventory table")
    print(f"{'='*60}\n")
    
    try:
        from db import supabase_client
        
        # Get first 200 rows from inventory table
        rows = supabase_client.select('inventory', columns='*')
        
        if not rows:
            print(f"❌ Supabase inventory table is EMPTY!")
            return {
                "status": "empty",
                "message": "Supabase inventory table contains no data",
                "total_rows": 0
            }
        
        print(f"✅ Found {len(rows)} total rows in Supabase inventory table\n")
        
        # Show first 20 rows
        print(f"First 20 rows:")
        for i, row in enumerate(rows[:20]):
            print(f"  {i+1}. {row}")
        
        # Group by SKU and store_id to see structure
        skus_per_store = {}
        stores_seen = set()
        
        for row in rows:
            sku = row.get('sku')
            store_id = row.get('store_id')
            stores_seen.add(store_id)
            
            if sku not in skus_per_store:
                skus_per_store[sku] = []
            skus_per_store[sku].append(store_id)
        
        print(f"\n📊 Statistics:")
        print(f"  Total rows: {len(rows)}")
        print(f"  Unique SKUs: {len(skus_per_store)}")
        print(f"  Unique stores: {len(stores_seen)}")
        print(f"  Stores: {sorted(stores_seen)}")
        
        # Show sample SKUs and their stores
        sample_skus = list(skus_per_store.keys())[:5]
        print(f"\n📋 Sample SKUs and their stores:")
        for sku in sample_skus:
            store_list = skus_per_store[sku]
            print(f"  {sku}: {store_list}")
        
        # Check for specific SKU
        if 'SKU000068' in skus_per_store:
            print(f"\n✅ SKU000068 found in stores: {skus_per_store['SKU000068']}")
        else:
            print(f"\n❌ SKU000068 NOT FOUND in inventory table")
            print(f"   Available SKUs: {sample_skus}...")
        
        # Check if STORE_MUMBAI has any inventory
        mumbai_rows = [r for r in rows if r.get('store_id') == 'STORE_MUMBAI']
        print(f"\n🏪 Inventory at STORE_MUMBAI: {len(mumbai_rows)} rows")
        if mumbai_rows:
            for row in mumbai_rows[:5]:
                print(f"    {row.get('sku')}: {row.get('quantity')} units")
        else:
            print(f"   ⚠️  STORE_MUMBAI has NO inventory rows")
        
        print(f"{'='*60}\n")
        
        return {
            "status": "ok",
            "total_rows": len(rows),
            "unique_skus": len(skus_per_store),
            "unique_stores": len(stores_seen),
            "stores": sorted(stores_seen),
            "first_20_rows": rows[:20],
            "sample_sku_stores": {sku: skus_per_store[sku] for sku in sample_skus},
            "mumbai_count": len(mumbai_rows),
            "mumbai_sample": mumbai_rows[:5] if mumbai_rows else []
        }
        
    except Exception as e:
        print(f"❌ Error reading inventory table: {e}")
        import traceback
        traceback.print_exc()
        return {
            "status": "error",
            "error": str(e)
        }


# ==========================================
# STORE ENDPOINTS
# ==========================================

@app.get("/stores", response_model=StoresListResponse)
async def list_stores():
    """
    Fetch all store locations directly from Supabase stores table.
    
    Returns store details including location, contact, and hours.
    Used by frontend to populate store selection dropdown.
    """
    try:
        if not SUPABASE_ENABLED:
            raise Exception("Supabase not enabled - cannot fetch stores")
        
        # Fetch stores directly from Supabase using the select function
        stores_data = supabase_client.select('stores')
        
        print(f"\n🔍 [DEBUG] Raw Supabase response (first store): {stores_data[0] if stores_data else 'None'}")
        print(f"🔍 [DEBUG] Total stores: {len(stores_data) if stores_data else 0}")
        
        if not stores_data:
            raise Exception("No stores found in Supabase")
        
        stores = [
            StoreLocation(
                id=item.get('store_id'),  # Map from store_id
                name=item.get('store_name') or item.get('store_id', 'Store'),  # Use store_name
                location=item.get('store_id'),  # Use store_id as location
                address=item.get('mall_or_area'),  # Map from mall_or_area
                city=item.get('city'),
                state=item.get('state'),
                pincode=str(item.get('pincode', '')) if item.get('pincode') else None,
                phone=item.get('contact_phone'),  # Map from contact_phone
                email=item.get('contact_email'),  # Map from contact_email
                manager_name=item.get('manager_name'),
                working_hours=f"{item.get('opening_time', '10:00')} - {item.get('closing_time', '22:00')}",  # Combine opening/closing
                store_type=item.get('store_type', 'standard')
            )
            for item in stores_data
        ]
        
        print(f"✅ Loaded {len(stores)} stores from Supabase")
        print(f"🔍 [DEBUG] First processed store: {stores[0].dict() if stores else 'None'}\n")
        
        return StoresListResponse(
            stores=stores,
            total_count=len(stores),
            timestamp=datetime.utcnow().isoformat()
        )
    
    except Exception as e:
        print(f"❌ Failed to fetch stores from Supabase: {str(e)}")
        raise HTTPException(
            status_code=503,
            detail=f"Failed to fetch stores: {str(e)}"
        )


@app.get("/stores/{store_location}/inventory/{sku}", response_model=StoreInventoryResponse)
async def get_store_inventory(store_location: str, sku: str):
    """
    Check inventory for a specific product at a specific store.
    
    Priority:
    1. Check Supabase inventory table (source of truth)
    2. Fall back to Redis if Supabase is empty or unavailable
    
    Args:
        store_location: Store location ID (e.g., 'STORE_MUMBAI')
        sku: Product SKU
    
    Returns:
        Available stock, total stock, and reservation status
    """
    try:
        print(f"\n📦 [CHECK INVENTORY] store={store_location}, sku={sku}")
        
        # Normalize inputs
        sku_normalized = sku.strip().upper()
        store_normalized = store_location.strip().upper()
        
        available_stock = 0
        source = None
        
        # Try Supabase first (source of truth)
        if SUPABASE_ENABLED:
            try:
                print(f"   📊 Querying Supabase for inventory...")
                print(f"   🔍 Query params: sku=eq.'{sku_normalized}' AND store_id=eq.'{store_normalized}'")
                inventory_rows = supabase_client.select(
                    'inventory',
                    params=f"sku=eq.'{sku_normalized}'&store_id=eq.'{store_normalized}'",
                    columns="sku,store_id,quantity"
                )
                
                print(f"   📋 Supabase returned {len(inventory_rows) if inventory_rows else 0} rows: {inventory_rows}")
                
                if inventory_rows and len(inventory_rows) > 0:
                    available_stock = int(inventory_rows[0].get('quantity', 0))
                    source = "supabase"
                    print(f"   ✅ Found in Supabase: {available_stock} units")
                else:
                    print(f"   ⚠️  Supabase returned empty result for {sku_normalized} at {store_normalized}")
            except Exception as e:
                print(f"   ⚠️  Supabase query failed: {e}")
        
        # Fall back to Redis if Supabase didn't find stock
        if available_stock == 0 and redis_utils.redis_client:
            try:
                print(f"   📊 Falling back to Redis...")
                redis_key = f"stock:{sku_normalized}:store:{store_normalized}"
                redis_val = redis_utils.redis_client.get(redis_key)
                
                if redis_val:
                    available_stock = int(redis_val)
                    source = "redis"
                    print(f"   ✅ Found in Redis: {available_stock} units")
            except Exception as e:
                print(f"   ⚠️  Redis fallback failed: {e}")
        
        # Get store name from stores table
        store_name = store_location.replace('_', ' ').title()
        if SUPABASE_ENABLED:
            try:
                store_data = supabase_client.select(
                    'stores',
                    params=f"store_id=eq.{store_location}",
                    columns="store_name"
                )
                if store_data and len(store_data) > 0:
                    store_name = store_data[0].get('store_name', store_name)
            except Exception as e:
                print(f"   ⚠️  Could not fetch store name: {e}")
        
        can_reserve = available_stock > 0
        store_id = store_location.lower()
        
        print(f"   ✅ Response: stock={available_stock}, can_reserve={can_reserve}, source={source}\n")
        
        return StoreInventoryResponse(
            store_id=store_id,
            store_name=store_name,
            sku=sku,
            available_stock=available_stock,
            total_stock=available_stock,
            reserved_count=0,
            can_reserve=can_reserve
        )
    
    except Exception as e:
        print(f"❌ Exception in get_store_inventory: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching store inventory: {str(e)}"
        )


# ==========================================
# INVENTORY ENDPOINTS
# ==========================================

@app.get("/inventory/{sku}", response_model=InventoryResponse)
async def get_inventory(sku: str):
    """
    Get stock levels for a SKU across all locations.
    
    Returns online and store-specific stock.
    """
    try:
        stock_data = redis_utils.get_stock(sku)
        
        total = stock_data["online"] + sum(stock_data["stores"].values())
        
        return InventoryResponse(
            sku=sku,
            online_stock=stock_data["online"],
            store_stock=stock_data["stores"],
            total_stock=total
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching inventory: {str(e)}")


@app.post("/hold", response_model=HoldResponse)
async def create_hold(
    request: HoldRequest,
    idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key")
):
    """
    Create an inventory hold (atomic decrement).
    
    - Atomically decrements stock
    - Creates hold with TTL
    - Supports idempotency
    
    Headers:
        X-Idempotency-Key: Optional key for idempotent requests
    """
    try:
        # Check idempotency
        if idempotency_key:
            cached_response = redis_utils.check_idempotency(idempotency_key)
            if cached_response:
                return HoldResponse(**cached_response)
        
        # Generate hold ID
        hold_id = f"hold-{uuid.uuid4()}"
        
        # Atomic stock decrement
        remaining = redis_utils.hold_stock_atomic(
            request.sku,
            request.quantity,
            request.location
        )
        
        if remaining < 0:
            raise HTTPException(
                status_code=409,
                detail=f"Insufficient stock for {request.sku} at {request.location}"
            )
        
        # Calculate expiry time
        import time
        expiry_timestamp = time.time() + request.ttl
        expires_at = datetime.fromtimestamp(expiry_timestamp).isoformat()
        
        # Create hold with TTL
        hold_data = {
            "hold_id": hold_id,
            "sku": request.sku,
            "quantity": request.quantity,
            "location": request.location,
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": expires_at,
            "status": "active"
        }
        
        redis_utils.create_hold(hold_id, hold_data, request.ttl)
        
        # Build response
        response = HoldResponse(
            hold_id=hold_id,
            sku=request.sku,
            quantity=request.quantity,
            location=request.location,
            remaining_stock=remaining,
            expires_at=expires_at,
            status="active"
        )
        
        # Save for idempotency
        if idempotency_key:
            redis_utils.save_idempotency(idempotency_key, response.dict())
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating hold: {str(e)}")


@app.post("/release", response_model=ReleaseResponse)
async def release_hold(request: ReleaseRequest):
    """
    Release an inventory hold (restore stock).
    
    - Restores stock atomically
    - Marks hold as released
    """
    try:
        # Get hold data
        hold = redis_utils.get_hold(request.hold_id)
        
        if not hold:
            raise HTTPException(
                status_code=404,
                detail=f"Hold {request.hold_id} not found or already expired"
            )
        
        # Restore stock atomically
        new_stock = redis_utils.release_stock_atomic(
            hold["sku"],
            hold["quantity"],
            hold.get("location", "online")
        )
        
        # Release hold
        redis_utils.release_hold(request.hold_id)
        
        return ReleaseResponse(
            hold_id=request.hold_id,
            status="released",
            restored_stock=new_stock
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error releasing hold: {str(e)}")


@app.post("/simulate/sale", response_model=SaleResponse)
async def simulate_sale(request: SimulateSaleRequest):
    """
    Simulate a sale by decrementing stock.
    
    For demo/testing purposes. Bypasses hold mechanism.
    """
    try:
        # Atomic stock decrement
        remaining = redis_utils.hold_stock_atomic(
            request.sku,
            request.quantity,
            request.location
        )
        
        if remaining < 0:
            raise HTTPException(
                status_code=409,
                detail=f"Insufficient stock for {request.sku} at {request.location}"
            )
        
        return SaleResponse(
            sku=request.sku,
            quantity_sold=request.quantity,
            location=request.location,
            remaining_stock=remaining,
            status="sold"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error simulating sale: {str(e)}")


# ==========================================
# BACKGROUND TASKS
# ==========================================

import asyncio
from contextlib import asynccontextmanager

async def cleanup_expired_holds_task():
    """Background task to cleanup expired holds every 10 seconds."""
    while True:
        try:
            expired_holds = redis_utils.get_expired_holds()
            
            for hold_id in expired_holds:
                redis_utils.cleanup_expired_hold(hold_id)
                print(f"✓ Cleaned up expired hold: {hold_id}")
            
            if expired_holds:
                print(f"✓ Cleaned up {len(expired_holds)} expired holds")
                
        except Exception as e:
            print(f"⚠ Error in cleanup task: {e}")
        
        await asyncio.sleep(10)  # Run every 10 seconds


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown events."""
    # Startup
    print("🚀 Starting Inventory Agent...")
    print(f"✓ Redis connected: {redis_utils.check_redis_health()}")
    # Print Supabase status (if configured)
    try:
        import sys
        from pathlib import Path
        backend_path = Path(__file__).resolve().parent.parent.parent.parent
        if str(backend_path) not in sys.path:
            sys.path.insert(0, str(backend_path))

        from db import supabase_client

        print(f"✓ Supabase URL present: {bool(supabase_client.SUPABASE_URL)}")
        print(f"✓ Supabase read enabled: {supabase_client.FEATURE_SUPABASE_READ}")
        print(f"✓ Supabase write enabled: {supabase_client.FEATURE_SUPABASE_WRITE}")

        # Attempt a light read to verify connectivity
        if supabase_client.is_enabled():
            try:
                _ = supabase_client.select("inventory", params="limit=1", columns="sku")
                print("✓ Supabase read test OK")
            except Exception as e:
                print(f"⚠ Supabase read test failed: {e}")
        else:
            print("ℹ️ Supabase reads not enabled or misconfigured")

    except Exception as e:
        print(f"ℹ️ Supabase not configured or unavailable: {e}")
    
    # Start background task
    cleanup_task = asyncio.create_task(cleanup_expired_holds_task())
    print("✓ Background hold cleanup task started")
    
    yield
    
    # Shutdown
    cleanup_task.cancel()
    print("👋 Inventory Agent shutting down")


app.router.lifespan_context = lifespan


# ==========================================
# RUN SERVER
# ==========================================

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8001,
        reload=True,
        log_level="info"
    )

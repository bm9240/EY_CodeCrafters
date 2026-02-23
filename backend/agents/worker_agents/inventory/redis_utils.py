# Redis connection and Lua scripts for atomic inventory operations

import os
import threading
import time
from typing import Optional

import redis
from dotenv import load_dotenv

load_dotenv()

# Redis Configuration
REDIS_URL = os.getenv("REDIS_URL")
HOLD_TTL = 300  # 5 minutes default hold time

# Initialize Redis client
redis_client = None
if REDIS_URL:
    try:
        redis_client = redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5
        )
    except Exception as exc:
        print(f"⚠ Redis connection failed ({exc}); falling back to in-memory store")
        redis_client = None

# Validate connectivity immediately; if ping fails, fall back to in-memory store.
if redis_client:
    try:
        redis_client.ping()
    except Exception as exc:
        print(f"⚠ Redis ping failed ({exc}); falling back to in-memory store")
        redis_client = None

# In-memory fallback when Redis is unavailable
_IN_MEMORY_STOCK: dict[str, int] = {}
_IN_MEMORY_HOLDS: dict[str, dict] = {}
_IN_MEMORY_IDEMPOTENCY: dict[str, dict] = {}
_IN_MEMORY_HOLDS_EXPIRY: dict[str, float] = {}
_LOCK = threading.Lock()


# ==========================================
# LUA SCRIPTS FOR ATOMIC OPERATIONS
# ==========================================

# Atomic stock decrement (hold inventory)
LUA_HOLD_STOCK = """
local sku = ARGV[1]
local quantity = tonumber(ARGV[2])
local location = ARGV[3]  -- 'online' or 'store:{store_id}'

local key
if location == 'online' then
    key = 'stock:' .. sku .. ':online'
else
    key = 'stock:' .. sku .. ':' .. location
end

local current = tonumber(redis.call('GET', key) or 0)

if current >= quantity then
    redis.call('DECRBY', key, quantity)
    return current - quantity
else
    return -1  -- Insufficient stock
end
"""

# Atomic stock increment (release hold)
LUA_RELEASE_STOCK = """
local sku = ARGV[1]
local quantity = tonumber(ARGV[2])
local location = ARGV[3]

local key
if location == 'online' then
    key = 'stock:' .. sku .. ':online'
else
    key = 'stock:' .. sku .. ':' .. location
end

redis.call('INCRBY', key, quantity)
return tonumber(redis.call('GET', key))
"""

# Register Lua scripts
hold_stock_script = None
release_stock_script = None

if redis_client:
    try:
        hold_stock_script = redis_client.register_script(LUA_HOLD_STOCK)
        release_stock_script = redis_client.register_script(LUA_RELEASE_STOCK)
        print("✓ Lua scripts registered successfully")
    except Exception as e:
        print(f"⚠ Failed to register Lua scripts: {e}")
elif REDIS_URL:
    print("⚠ Redis URL provided but connection unavailable - using in-memory inventory store")
else:
    print("ℹ️ REDIS_URL not set - using in-memory inventory store")


# ==========================================
# REDIS KEY PATTERNS
# ==========================================

def get_stock_key(sku: str, location: str = "online") -> str:
    """Generate Redis key for stock."""
    if location == "online":
        return f"stock:{sku}:online"
    if location.startswith("store:"):
        return f"stock:{sku}:{location}"
    return f"stock:{sku}:store:{location}"


def get_hold_key(hold_id: str) -> str:
    """Generate Redis key for hold."""
    return f"hold:{hold_id}"


def get_idempotency_key(key: str) -> str:
    """Generate Redis key for idempotency."""
    return f"idemp:{key}"


# ==========================================
# STOCK OPERATIONS
# ==========================================

def get_stock(sku: str) -> dict:
    """
    Get stock levels for a SKU across all locations.
    
    Tries Supabase first (if FEATURE_SUPABASE_READ=true), then falls back to Redis.
    
    Returns:
        {
            "online": int,
            "stores": {"store_id": int, ...}
        }
    """
    # Normalize SKU to uppercase for consistency
    sku_normalized = sku.upper()
    print(f"\n🔍 [DEBUG] get_stock called with sku={sku}, normalized={sku_normalized}")
    
    # Try Supabase first (when enabled)
    try:
        import sys
        from pathlib import Path
        # Add backend to path so db package is importable
        backend_path = Path(__file__).resolve().parent.parent.parent.parent
        if str(backend_path) not in sys.path:
            sys.path.insert(0, str(backend_path))
        
        from db.repositories import inventory_repo
        
        # Try with normalized SKU
        supabase_result = inventory_repo.get_stock(sku_normalized)
        if supabase_result is not None:
            print(f"✅ Source: SUPABASE for SKU={sku_normalized}, stores={supabase_result.get('stores', {})}")
            return {
                "online": supabase_result.get("online", 0),
                "stores": supabase_result.get("stores", {})
            }
        else:
            # If normalized didn't work, try original
            supabase_result = inventory_repo.get_stock(sku)
            if supabase_result is not None:
                print(f"✅ Source: SUPABASE (original case) for SKU={sku}, stores={supabase_result.get('stores', {})}")
                return {
                    "online": supabase_result.get("online", 0),
                    "stores": supabase_result.get("stores", {})
                }
            print(f"⚠️ Supabase returned None for both {sku_normalized} and {sku}, falling back to Redis/CSV")
    except Exception as e:
        print(f"⚠️ Supabase read failed for SKU={sku}: {e}, falling back to Redis/CSV")
    
    # Fallback to Redis
    print(f"📦 Source: REDIS/CSV fallback for SKU={sku}")
    
    result = {"online": 0, "stores": {}}

    if redis_client:
        # Try both normalized and original SKU
        for sku_variant in [sku_normalized, sku]:
            online_stock = redis_client.get(get_stock_key(sku_variant, "online"))
            if online_stock:
                result["online"] = int(online_stock)
                print(f"  Found online stock using sku={sku_variant}: {result['online']}")
                break
        
        if result["online"] == 0:
            online_stock = redis_client.get(get_stock_key(sku, "online"))
            result["online"] = int(online_stock) if online_stock else 0
            print(f"  Redis online stock: {result['online']}")

        # Search for store keys with both variants
        for sku_variant in [sku_normalized, sku]:
            pattern = f"stock:{sku_variant}:store:*"
            print(f"  Searching Redis pattern: {pattern}")
            store_keys = redis_client.keys(pattern)
            if store_keys:
                print(f"  Found Redis keys: {store_keys}")
                for key in store_keys:
                    store_id = key.split(":")[-1]
                    stock = redis_client.get(key)
                    result["stores"][store_id] = int(stock) if stock else 0
                    print(f"    {store_id}: {result['stores'][store_id]}")
                if result["stores"]:
                    break
        
        if result["stores"]:
            print(f"✓ Found store stock from Redis")
        else:
            print(f"⚠️ No store stock found in Redis")
        return result

    # In-memory fallback
    print(f"  Using in-memory fallback")
    with _LOCK:
        for sku_variant in [sku_normalized, sku]:
            online_key = get_stock_key(sku_variant, "online")
            result["online"] = int(_IN_MEMORY_STOCK.get(online_key, 0))
            if result["online"] > 0:
                print(f"    Online (variant {sku_variant}): {result['online']}")
                break
        
        if result["online"] == 0:
            result["online"] = int(_IN_MEMORY_STOCK.get(get_stock_key(sku, "online"), 0))

        for sku_variant in [sku_normalized, sku]:
            prefix = f"stock:{sku_variant}:store:"
            for key, value in _IN_MEMORY_STOCK.items():
                if key.startswith(prefix):
                    store_id = key[len(prefix):]
                    result["stores"][store_id] = int(value)
                    print(f"    {store_id} (variant {sku_variant}): {result['stores'][store_id]}")
            if result["stores"]:
                break
    
    print(f"Final result: online={result['online']}, stores={result['stores']}\n")
    return result


def hold_stock_atomic(sku: str, quantity: int, location: str = "online") -> int:
    """
    Atomically hold stock in Redis.
    
    Flow:
    1. Query Supabase (source of truth with all 4000 products) for available stock
    2. Validate enough stock is available in Supabase
    3. Initialize Redis with Supabase value if not already there
    4. Create hold in Redis (atomic decrement via Lua script)
    5. Return remaining stock in Redis
    
    Note: Redis is used for TEMPORARY HOLDS ONLY (TTL-based)
          Supabase is the source of truth for product availability
    
    Returns:
        Remaining stock in Redis if successful, -1 if insufficient stock in Supabase
    """
    # Normalize SKU (trim and uppercase for consistency)
    sku_normalized = sku.strip().upper()
    print(f"✓ Normalized SKU in hold_stock_atomic: {sku} → {sku_normalized}")
    
    # Extract store ID from location if it's a store location
    store_id = None
    location_for_redis = location
    if location.startswith("store:"):
        store_id = location.split(":", 1)[1].strip().upper()
    
    # STEP 1: Fetch current stock from Supabase (source of truth - has all 4000 products)
    supabase_stock = 0
    try:
        from db.repositories import inventory_repo
        stock_data = inventory_repo.get_stock(sku_normalized)
        
        print(f"✓ Stock data from Supabase: {stock_data}")
        
        if location_for_redis == "online":
            supabase_stock = stock_data.get("online", 0) if stock_data else 0
        elif store_id and stock_data and stock_data.get("stores"):
            supabase_stock = stock_data["stores"].get(store_id, 0)
        
        print(f"✓ Supabase stock for {sku_normalized} at {location_for_redis}: {supabase_stock} units")
    except Exception as e:
        print(f"❌ CRITICAL: Could not fetch from Supabase: {e}")
        return -1
    
    # STEP 2: Validate sufficient stock in Supabase
    if supabase_stock < quantity:
        print(f"❌ Insufficient stock in Supabase: requested {quantity}, available {supabase_stock}")
        return -1
    
    # STEP 3: Initialize Redis with Supabase value if not already there
    stock_key = get_stock_key(sku_normalized, location_for_redis)
    
    if redis_client:
        try:
            redis_current = redis_client.get(stock_key)
            if not redis_current:
                # Initialize Redis from Supabase value
                redis_client.set(stock_key, supabase_stock)
                print(f"✓ Redis initialized from Supabase: {stock_key} = {supabase_stock}")
        except Exception as e:
            print(f"⚠ Could not initialize Redis: {e}")
    
    # STEP 4: Create hold in Redis using Lua script
    if redis_client and hold_stock_script:
        try:
            result = hold_stock_script(args=[sku_normalized, quantity, location_for_redis])
            remaining = int(result)
            if remaining >= 0:
                print(f"✓ Redis hold created: {sku_normalized} at {location_for_redis}, remaining in Redis={remaining}")
                return remaining
            else:
                print(f"❌ Hold failed in Redis: {sku_normalized} at {location_for_redis} (no stock left in Redis)")
                return -1
        except Exception as e:
            print(f"❌ Error creating hold in Redis: {e}")
            return -1
    
    # FALLBACK: In-memory storage if Redis unavailable
    with _LOCK:
        current = int(_IN_MEMORY_STOCK.get(stock_key, supabase_stock))
        if current >= quantity:
            _IN_MEMORY_STOCK[stock_key] = current - quantity
            print(f"✓ In-memory hold created: {sku_normalized} at {location_for_redis}, remaining={current - quantity}")
            return current - quantity
        print(f"❌ Insufficient stock in memory fallback")
        return -1


def release_stock_atomic(sku: str, quantity: int, location: str = "online") -> int:
    """
    Atomically increment stock using Lua script.
    
    Returns:
        New stock level
    """
    # Normalize SKU (trim and uppercase for consistency)
    sku_normalized = sku.strip().upper()
    
    if redis_client and release_stock_script:
        try:
            result = release_stock_script(args=[sku_normalized, quantity, location])
            new_stock = int(result)
            # If Supabase write enabled, attempt to increment Supabase as well
            try:
                from db.repositories import inventory_repo
                inventory_repo.increment_stock(sku_normalized, location, quantity)
            except Exception as e:
                print(f"⚠ Failed to update Supabase stock after release: {e}")
            return new_stock
        except Exception as e:
            print(f"Error releasing stock: {e}")
            return 0

    stock_key = get_stock_key(sku_normalized, location)
    with _LOCK:
        current = int(_IN_MEMORY_STOCK.get(stock_key, 0))
        new_value = current + quantity
        _IN_MEMORY_STOCK[stock_key] = new_value
        return new_value


def set_stock(sku: str, quantity: int, location: str = "online") -> bool:
    """
    Set stock level for a SKU at a location.
    Used primarily for seeding data.
    """
    key = get_stock_key(sku, location)

    if redis_client:
        try:
            redis_client.set(key, quantity)
            return True
        except Exception as e:
            print(f"Error setting stock: {e}")
            return False

    with _LOCK:
        _IN_MEMORY_STOCK[key] = int(quantity)
    return True


# ==========================================
# HOLD OPERATIONS
# ==========================================

def create_hold(hold_id: str, hold_data: dict, ttl: int = HOLD_TTL) -> bool:
    """
    Create a hold with TTL.
    
    Args:
        hold_id: Unique hold identifier
        hold_data: Hold information (sku, quantity, location, etc.)
        ttl: Time to live in seconds
    
    Returns:
        True if successful
    """
    import json

    if redis_client:
        try:
            key = get_hold_key(hold_id)
            redis_client.setex(key, ttl, json.dumps(hold_data))
            expiry_time = redis_client.time()[0] + ttl
            redis_client.zadd("holds_by_expiry", {hold_id: expiry_time})
            return True
        except Exception as e:
            print(f"Error creating hold: {e}")
            return False

    with _LOCK:
        _IN_MEMORY_HOLDS[hold_id] = hold_data
        _IN_MEMORY_HOLDS_EXPIRY[hold_id] = time.time() + ttl
    return True


def get_hold(hold_id: str) -> Optional[dict]:
    """Get hold data if it exists."""
    import json

    if redis_client:
        try:
            key = get_hold_key(hold_id)
            data = redis_client.get(key)
            return json.loads(data) if data else None
        except Exception as e:
            print(f"Error getting hold: {e}")
            return None

    with _LOCK:
        data = _IN_MEMORY_HOLDS.get(hold_id)
        return data.copy() if data else None


def release_hold(hold_id: str) -> bool:
    """Mark hold as released and remove it."""
    if redis_client:
        try:
            key = get_hold_key(hold_id)
            redis_client.delete(key)
            redis_client.zrem("holds_by_expiry", hold_id)
            return True
        except Exception as e:
            print(f"Error releasing hold: {e}")
            return False

    with _LOCK:
        _IN_MEMORY_HOLDS.pop(hold_id, None)
        _IN_MEMORY_HOLDS_EXPIRY.pop(hold_id, None)
    return True


# ==========================================
# IDEMPOTENCY
# ==========================================

def check_idempotency(key: str) -> Optional[dict]:
    """Check if request was already processed."""
    import json

    if redis_client:
        try:
            idemp_key = get_idempotency_key(key)
            data = redis_client.get(idemp_key)
            return json.loads(data) if data else None
        except Exception as e:
            print(f"Error checking idempotency: {e}")
            return None

    with _LOCK:
        data = _IN_MEMORY_IDEMPOTENCY.get(key)
        return json.loads(json.dumps(data)) if data else None


def save_idempotency(key: str, response: dict, ttl: int = 3600) -> bool:
    """Save response for idempotency check (1 hour TTL)."""
    import json

    if redis_client:
        try:
            idemp_key = get_idempotency_key(key)
            redis_client.setex(idemp_key, ttl, json.dumps(response))
            return True
        except Exception as e:
            print(f"Error saving idempotency: {e}")
            return False

    with _LOCK:
        _IN_MEMORY_IDEMPOTENCY[key] = json.loads(json.dumps(response))
    return True


# ==========================================
# EXPIRY MANAGEMENT
# ==========================================

def get_expired_holds() -> list:
    """Get all holds that have expired."""
    if redis_client:
        try:
            current_time = redis_client.time()[0]
            expired = redis_client.zrangebyscore("holds_by_expiry", 0, current_time)
            return expired
        except Exception as e:
            print(f"Error getting expired holds: {e}")
            return []

    now = time.time()
    with _LOCK:
        return [hold_id for hold_id, expiry in list(_IN_MEMORY_HOLDS_EXPIRY.items()) if expiry <= now]


def cleanup_expired_hold(hold_id: str) -> bool:
    """
    Cleanup an expired hold by restoring stock.
    
    Returns:
        True if successful
    """
    if redis_client:
        try:
            hold = get_hold(hold_id)

            if hold:
                release_stock_atomic(
                    hold["sku"],
                    hold["quantity"],
                    hold.get("location", "online")
                )

            redis_client.zrem("holds_by_expiry", hold_id)
            return True
        except Exception as e:
            print(f"Error cleaning up expired hold: {e}")
            return False

    with _LOCK:
        hold = _IN_MEMORY_HOLDS.pop(hold_id, None)
        _IN_MEMORY_HOLDS_EXPIRY.pop(hold_id, None)

    if hold:
        release_stock_atomic(hold["sku"], hold["quantity"], hold.get("location", "online"))
    return True


# ==========================================
# HEALTH CHECK
# ==========================================

def check_redis_health() -> bool:
    """Check if Redis is available."""
    if redis_client:
        try:
            redis_client.ping()
            return True
        except Exception:
            return False
    # In-memory fallback treated as healthy so dependent services continue
    return True


print(f"✓ Redis utils loaded (connected: {redis_client is not None})")

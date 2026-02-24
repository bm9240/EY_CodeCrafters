"""
Inventory repository: reads stock from Supabase.

Expects an 'inventory' table with columns: sku, store_id, quantity
"""
from typing import Dict, Any, Optional
from ..supabase_client import supabase, is_enabled


def _aggregate_rows(rows) -> Dict[str, Any]:
    """
    Aggregate inventory rows into {online, stores, total} format.
    
    Rows should have: sku, store_id, quantity
    store_id='ONLINE' or similar → online stock
    store_id='STORE_MUMBAI' etc → store stock
    """
    result = {"online": 0, "stores": {}, "total": 0}
    
    for row in rows:
        try:
            qty = int(row.get("quantity") or row.get("qty") or 0)
        except (ValueError, TypeError):
            qty = 0
        
        store_id = str(row.get("store_id", "")).upper()
        
        if store_id == "ONLINE":
            result["online"] += qty
        else:
            result["stores"][store_id] = result["stores"].get(store_id, 0) + qty
        
        result["total"] += qty
    
    return result


def get_stock(sku: str) -> Optional[Dict[str, Any]]:
    """
    Get stock for a SKU from Supabase.
    
    Returns:
        {"online": int, "stores": {store_id: qty}, "total": int}
        or None on error/empty
    """
    if not is_enabled() or supabase is None:
        print("[inventory_repo] Supabase not enabled or client not available")
        return None
    
    # Normalize SKU before querying
    normalized_sku = str(sku).strip().upper()
    
    # Log what we are querying
    print(f"[inventory_repo] Querying Supabase for SKU={normalized_sku}")
    
    try:
        # Use correct Supabase query syntax
        response = supabase.table("inventory").select("*").eq("sku", normalized_sku).execute()
        
        # Log raw response
        print("Supabase raw response:", response)
        
        # Fix response checking
        if response.data and len(response.data) > 0:
            result = _aggregate_rows(response.data)
            print(f"[inventory_repo] Supabase returned {len(response.data)} rows for SKU={normalized_sku}, result={result}")
            return result
        else:
            print(f"[inventory_repo] No data in response for SKU={normalized_sku}")
            return None
            
    except Exception as e:
        print(f"[inventory_repo] Error querying SKU={normalized_sku}: {e}")
        return None


def get_total_stock(sku: str) -> Optional[int]:
    """Get total stock quantity for a SKU."""
    stock = get_stock(sku)
    if stock is None:
        return None
    return stock.get("total", 0)


def _normalize_store_id(location: str) -> str:
    """Normalize location string to store_id used in Supabase inventory rows."""
    if not location:
        return "ONLINE"
    if isinstance(location, str) and location.lower() == "online":
        return "ONLINE"
    # location might be 'store:STORE_MUMBAI' or 'STORE_MUMBAI'
    if isinstance(location, str) and location.startswith("store:"):
        return location.split(":", 1)[1].upper()
    return str(location).upper()


def decrement_stock(sku: str, location: str, amount: int) -> bool:
    """Decrease stock in Supabase for the given sku and location by amount.

    Returns True on success, False otherwise.
    Requires FEATURE_SUPABASE_WRITE to be enabled in supabase_client.
    """
    try:
        from ..supabase_client import is_write_enabled, select_one, update

        if not is_write_enabled():
            # Write not enabled; skip
            return False

        # Normalize SKU
        sku = sku.strip().upper()
        store_id = _normalize_store_id(location)
        # Find existing row quantity (don't assume an 'id' column exists)
        row = select_one("inventory", params=f"sku=eq.'{sku}'&store_id=eq.'{store_id}'", columns="quantity")
        if not row:
            # No existing row to decrement
            return False
        try:
            current = int(row.get("quantity") or 0)
        except Exception:
            current = 0
        new_qty = max(0, current - int(amount))
        # Update row using composite filter (sku + store_id) to avoid requiring an 'id' column
        update("inventory", {"quantity": new_qty}, params=f"sku=eq.'{sku}'&store_id=eq.'{store_id}'")
        return True
    except Exception as e:
        print(f"[inventory_repo] Failed to decrement stock for {sku} at {location}: {e}")
        return False


def increment_stock(sku: str, location: str, amount: int) -> bool:
    """Increase stock in Supabase for the given sku and location by amount."""
    try:
        from ..supabase_client import is_write_enabled, select_one, update

        if not is_write_enabled():
            return False

        store_id = _normalize_store_id(location)
        # Find existing row quantity (don't assume an 'id' column exists)
        row = select_one("inventory", params=f"sku=eq.'{sku}'&store_id=eq.'{store_id}'", columns="quantity")
        if not row:
            # No existing row; cannot increment
            return False
        try:
            current = int(row.get("quantity") or 0)
        except Exception:
            current = 0
        new_qty = current + int(amount)
        # Update row using composite filter (sku + store_id)
        update("inventory", {"quantity": new_qty}, params=f"sku=eq.'{sku}'&store_id=eq.'{store_id}'")
        return True
    except Exception as e:
        print(f"[inventory_repo] Failed to increment stock for {sku} at {location}: {e}")
        return False


def upsert_stock(sku: str, location: str, quantity: int) -> bool:
    """Insert or update a Supabase inventory row for given sku and store_id.

    This is a best-effort helper used when no existing row is found.
    Returns True on success, False on failure.
    """
    try:
        from ..supabase_client import is_write_enabled, upsert

        if not is_write_enabled():
            return False

        store_id = _normalize_store_id(location)
        
        # Payload without inventory_id - let Supabase auto-generate if needed
        # Focus on the composite key: sku + store_id
        payload = {
            "sku": sku,
            "store_id": store_id,
            "quantity": int(quantity)
        }

        # Upsert on sku+store_id composite key
        upsert("inventory", payload, conflict_column="sku,store_id")
        return True
    except Exception as e:
        print(f"[inventory_repo] Failed to upsert stock for {sku} at {location}: {e}")
        return False

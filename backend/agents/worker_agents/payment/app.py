# Payment Agent - FastAPI Server
# Endpoints: POST /payment/process, GET /payment/transaction/{txn_id}, GET /payment/user-transactions/{user_id}

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional, List, Tuple
import uvicorn
import uuid
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
import json
import logging
import os
import requests
import csv
import re
import math
print(">>> Razorpay router loaded")
try:
    import razorpay
except ImportError:  # pragma: no cover - dependency managed via requirements
    razorpay = None

import redis_utils
import payment_repository
import sys
from pathlib import Path
from dotenv import load_dotenv
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
import orders_repository
from db import supabase_client


# Load environment variables
load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")

app = FastAPI(
    title="Payment Agent",
    description="Payment processing and transaction management system",
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


logger = logging.getLogger(__name__)

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")
LOYALTY_SERVICE_URL = os.getenv("LOYALTY_SERVICE_URL", "http://localhost:8002")
def _clean_store_identifier(value: Optional[Any]) -> Optional[str]:
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.lower().startswith("store:"):
            candidate = candidate.split(":", 1)[1]
        candidate = candidate.replace("-", "_").upper()
        if candidate in {"ONLINE"}:
            return "ONLINE"
        if candidate.startswith("STORE_"):
            return candidate
        return f"STORE_{candidate}"
    return None


DEFAULT_STORE_ID = _clean_store_identifier(os.getenv("DEFAULT_STORE_ID")) or "STORE_MUMBAI"


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_token(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    sanitized = re.sub(r"[^a-z0-9\s]", " ", text)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized or None


CITY_ALIAS_OVERRIDES: Dict[str, str] = {
    "bombay": "mumbai",
    "navi mumbai": "mumbai",
    "thane": "mumbai",
    "delhi": "new delhi",
    "delhi ncr": "new delhi",
    "ncr": "new delhi",
    "gurgaon": "new delhi",
    "gurugram": "new delhi",
    "noida": "new delhi",
    "ghaziabad": "new delhi",
    "bangalore": "bengaluru",
    "bengalore": "bengaluru",
    "bengaluru": "bengaluru",
    "bengaluru urban": "bengaluru",
    "bengaluru city": "bengaluru",
    "pune": "pune",
    "poona": "pune",
    "chennai": "chennai",
    "madras": "chennai",
}


STATE_ALIAS_OVERRIDES: Dict[str, str] = {
    "maharashtra": "mumbai",
    "delhi": "new delhi",
    "nct of delhi": "new delhi",
    "karnataka": "bengaluru",
    "tamil nadu": "chennai",
}


def _load_store_catalog() -> tuple[Dict[str, Dict[str, Any]], Dict[str, str], Dict[str, str], Dict[str, str]]:
    store_by_id: Dict[str, Dict[str, Any]] = {}
    city_map: Dict[str, str] = {}
    state_map: Dict[str, str] = {}
    pin_map: Dict[str, str] = {}
    try:
        stores_path = Path(__file__).resolve().parents[3] / "data" / "stores.csv"
        with stores_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                store_id = _clean_store_identifier(row.get("store_id"))
                if not store_id:
                    continue

                city_norm = _normalize_token(row.get("city"))
                state_norm = _normalize_token(row.get("state"))
                lat = _to_float(row.get("latitude"))
                lon = _to_float(row.get("longitude"))

                store_by_id[store_id] = {
                    "city": city_norm,
                    "state": state_norm,
                    "latitude": lat,
                    "longitude": lon,
                }

                if city_norm and city_norm not in city_map:
                    city_map[city_norm] = store_id
                if state_norm and state_norm not in state_map:
                    state_map[state_norm] = store_id

                pincode = str(row.get("pincode") or "").strip()
                digits = "".join(ch for ch in pincode if ch.isdigit())
                for length in range(3, len(digits) + 1):
                    prefix = digits[:length]
                    if prefix:
                        pin_map.setdefault(prefix, store_id)
    except FileNotFoundError:
        logger.warning("⚠️  stores.csv not found; store proximity defaults will use fallback")
    except Exception as exc:
        logger.warning(f"⚠️  Failed to load store catalog: {exc}")

    return store_by_id, city_map, state_map, pin_map


STORE_CATALOG, STORE_CITY_MAP, STORE_STATE_MAP, STORE_PIN_PREFIX_MAP = _load_store_catalog()


def _match_store_by_city_name(value: Any) -> Optional[str]:
    token = _normalize_token(value)
    if not token:
        return None

    canonical = CITY_ALIAS_OVERRIDES.get(token, token)
    if canonical in STORE_CITY_MAP:
        return STORE_CITY_MAP[canonical]

    for alias, target in CITY_ALIAS_OVERRIDES.items():
        if alias in token and target in STORE_CITY_MAP:
            return STORE_CITY_MAP[target]

    for city_key, store_id in STORE_CITY_MAP.items():
        if city_key in token:
            return store_id

    return None


def _match_store_by_state(value: Any) -> Optional[str]:
    token = _normalize_token(value)
    if not token:
        return None

    canonical = STATE_ALIAS_OVERRIDES.get(token)
    if canonical:
        return STORE_CITY_MAP.get(canonical) or STORE_STATE_MAP.get(canonical)

    if token in STORE_STATE_MAP:
        return STORE_STATE_MAP[token]

    for alias, target in STATE_ALIAS_OVERRIDES.items():
        if alias in token:
            return STORE_CITY_MAP.get(target) or STORE_STATE_MAP.get(target)

    return None


def _match_store_by_pincode(value: Any) -> Optional[str]:
    if value is None:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        return None

    for length in range(len(digits), 2, -1):
        prefix = digits[:length]
        if prefix in STORE_PIN_PREFIX_MAP:
            return STORE_PIN_PREFIX_MAP[prefix]

    prefix3 = digits[:3]
    return STORE_PIN_PREFIX_MAP.get(prefix3)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def _match_store_by_coordinates(lat: Optional[float], lon: Optional[float]) -> Optional[str]:
    if lat is None or lon is None:
        return None

    closest_store: Optional[str] = None
    closest_distance: Optional[float] = None

    for store_id, info in STORE_CATALOG.items():
        store_lat = info.get("latitude")
        store_lon = info.get("longitude")
        if store_lat is None or store_lon is None:
            continue

        distance = _haversine_km(lat, lon, store_lat, store_lon)
        if closest_distance is None or distance < closest_distance:
            closest_distance = distance
            closest_store = store_id

    return closest_store


def _infer_store_from_address(address: Optional[Any]) -> Optional[str]:
    # Heuristic: resolve city/state/pincode hints (or coordinates when present) to a known store.
    if not address:
        return None

    if isinstance(address, str):
        try:
            parsed = json.loads(address)
        except (json.JSONDecodeError, TypeError):
            parsed = None

        if isinstance(parsed, dict):
            return _infer_store_from_address(parsed)

        store = _match_store_by_city_name(address)
        if store:
            return store
        return _match_store_by_pincode(address)

    if isinstance(address, dict):
        for key in ("store_id", "store", "preferred_store", "pickup_store", "fulfillment_store"):
            store = _clean_store_identifier(address.get(key))
            if store:
                return store

        store = _clean_store_identifier(address.get("location"))
        if store:
            return store

        lat = _to_float(address.get("lat") or address.get("latitude"))
        lon = _to_float(address.get("lng") or address.get("longitude") or address.get("lon"))
        coord_store = _match_store_by_coordinates(lat, lon)
        if coord_store:
            return coord_store

        for key in ("city", "town", "district", "region", "area", "locality", "village", "metro", "customer_city", "shipping_city"):
            store = _match_store_by_city_name(address.get(key))
            if store:
                return store

        for key in ("state", "state_name", "province", "state_code", "shipping_state"):
            store = _match_store_by_state(address.get(key))
            if store:
                return store

        for key in ("pincode", "postal_code", "zip", "zipcode", "post_code"):
            store = _match_store_by_pincode(address.get(key))
            if store:
                return store

        for value in address.values():
            if isinstance(value, dict):
                store = _infer_store_from_address(value)
                if store:
                    return store
            elif isinstance(value, str):
                store = _match_store_by_city_name(value)
                if store:
                    return store
                store = _match_store_by_pincode(value)
                if store:
                    return store

        return None

    return None


def _extract_store_from_metadata(metadata: Optional[Any]) -> Optional[str]:
    if not metadata:
        return None

    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            return _match_store_by_city_name(metadata) or _match_store_by_pincode(metadata)

    if not isinstance(metadata, dict):
        return None

    for key in ("store_id", "store", "location", "fulfillment_store", "pickup_store", "channel_store_id"):
        store = _clean_store_identifier(metadata.get(key))
        if store:
            return store

    for key in ("shipping_address", "delivery_address", "address", "customer_address", "billing_address"):
        store = _infer_store_from_address(metadata.get(key))
        if store:
            return store

    for key in ("shipping_city", "delivery_city", "customer_city", "city"):
        store = _match_store_by_city_name(metadata.get(key))
        if store:
            return store

    for key in ("address_city", "address_town", "address_area"):
        store = _match_store_by_city_name(metadata.get(key))
        if store:
            return store

    for key in ("shipping_state", "delivery_state", "state"):
        store = _match_store_by_state(metadata.get(key))
        if store:
            return store

    for key in ("address_state", "address_region", "address_province"):
        store = _match_store_by_state(metadata.get(key))
        if store:
            return store

    for key in ("shipping_pincode", "delivery_pincode", "pincode", "postal_code"):
        store = _match_store_by_pincode(metadata.get(key))
        if store:
            return store

    for key in ("address_pincode", "address_zip", "address_postal_code", "address_zipcode"):
        store = _match_store_by_pincode(metadata.get(key))
        if store:
            return store

    items = metadata.get("items")
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except json.JSONDecodeError:
            items = []
    if isinstance(items, list):
        for raw in items:
            if isinstance(raw, dict):
                direct_store = _clean_store_identifier(raw.get("store_id") or raw.get("location"))
                if direct_store:
                    return direct_store
                inferred_store = _infer_store_from_address(raw)
                if inferred_store:
                    return inferred_store

    return _match_store_by_city_name(metadata) or _match_store_by_pincode(metadata)

razorpay_client = None
razorpay_router = APIRouter(prefix="/payment/razorpay", tags=["Razorpay"])


def _load_customer_mappings() -> tuple[Dict[str, str], Dict[str, str]]:
    phone_to_id: Dict[str, str] = {}
    ids: Dict[str, str] = {}
    try:
        customers_path = Path(__file__).resolve().parents[3] / "data" / "customers.csv"
        with customers_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                cid = str(row.get("customer_id", "")).strip()
                phone = str(row.get("phone_number", "")).strip()
                if cid:
                    ids[cid] = cid
                if cid and phone:
                    phone_to_id[phone] = cid
    except Exception as exc:
        logger.warning(f"⚠️  Could not load customer mappings: {exc}")
    return phone_to_id, ids


PHONE_TO_CUSTOMER_ID, CUSTOMER_IDS = _load_customer_mappings()


def _resolve_customer_id(user_id: Optional[str], metadata: Optional[Dict[str, Any]] = None) -> Optional[str]:
    candidate: Optional[str] = None

    if metadata:
        cid = metadata.get("customer_id")
        if cid and str(cid) in CUSTOMER_IDS:
            candidate = str(cid)
        else:
            phone = metadata.get("phone") or metadata.get("phone_number")
            if phone and str(phone) in PHONE_TO_CUSTOMER_ID:
                candidate = PHONE_TO_CUSTOMER_ID[str(phone)]

    if not candidate and user_id:
        uid = str(user_id)
        if uid in CUSTOMER_IDS:
            candidate = uid
        elif uid in PHONE_TO_CUSTOMER_ID:
            candidate = PHONE_TO_CUSTOMER_ID[uid]

    return candidate


def _ensure_razorpay_client():
    """Return an initialised Razorpay client or raise an HTTP error."""
    global razorpay_client

    if razorpay is None:
        raise HTTPException(
            status_code=500,
            detail="Razorpay SDK not installed. Run pip install razorpay.",
        )

    if not (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET):
        raise HTTPException(
            status_code=503,
            detail="Razorpay credentials not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET.",
        )

    if razorpay_client is None:
        logger.info("Initialising Razorpay test client")
        razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

    return razorpay_client


def _quantize(value: Decimal) -> str:
    """Format Decimal values with two decimal places for CSV storage."""
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _store_transaction_safely(transaction_id: str, data: Dict[str, str], user_key: str, amount: Decimal) -> None:
    """Persist Razorpay transactions to Redis, ignoring missing client errors."""
    try:
        redis_utils.store_transaction(transaction_id, data)
        redis_utils.store_payment_attempt(user_key, {
            "transaction_id": transaction_id,
            "amount": float(amount),
            "status": data.get("status", "success"),
        })
    except RuntimeError:
        logger.debug("Redis not configured; skipped caching Razorpay payment")


def _normalize_order_items(items: List[Dict[str, Any]], default_store: Optional[str] = None) -> List[Dict[str, Any]]:
    """Normalize raw cart items into a consistent structure."""
    normalized: List[Dict[str, Any]] = []
    fallback_store = _clean_store_identifier(default_store) or DEFAULT_STORE_ID

    for raw in items or []:
        if not isinstance(raw, dict):
            continue

        normalized_item = dict(raw)

        try:
            qty_value = int(raw.get("qty") or raw.get("quantity") or raw.get("count") or 0)
        except (TypeError, ValueError):
            qty_value = 0
        if qty_value <= 0:
            qty_value = 1

        price_source = raw.get("unit_price", raw.get("price"))
        if price_source is None:
            price_source = raw.get("line_total")
            if price_source is not None:
                try:
                    price_source = float(price_source) / qty_value if qty_value else 0.0
                except (TypeError, ValueError, ZeroDivisionError):
                    price_source = 0.0

        try:
            unit_price = float(price_source)
        except (TypeError, ValueError):
            unit_price = 0.0

        line_total_source = raw.get("line_total")
        if line_total_source is None:
            line_total_source = unit_price * qty_value
        try:
            line_total = float(line_total_source)
        except (TypeError, ValueError):
            line_total = unit_price * qty_value

        location_hint = raw.get("location")
        store_hint = raw.get("store_id") or raw.get("store") or raw.get("fulfillment_store") or raw.get("pickup_store")
        store_from_location = _clean_store_identifier(location_hint)
        store_id = (
            _clean_store_identifier(store_hint)
            or store_from_location
            or fallback_store
        )

        if store_id == "ONLINE":
            location = "online"
        else:
            location = f"store:{store_id}"

        if store_from_location:
            location = "online" if store_from_location == "ONLINE" else f"store:{store_from_location}"

        sku_value = raw.get("sku") or raw.get("product_id")
        sku = str(sku_value).strip() if sku_value is not None else None

        # Convert prices to integers (keep in rupees, just remove decimals for Supabase bigint)
        unit_price_int = int(round(unit_price))
        line_total_int = int(round(line_total))

        normalized_item.update({
            "sku": sku,
            "qty": qty_value,
            "quantity": qty_value,
            "unit_price": unit_price_int,
            "line_total": line_total_int,
            "store_id": store_id,
            "location": location,
        })

        normalized.append(normalized_item)

    return normalized


def _update_customer_records(
    customer_id: Optional[str],
    normalized_items: List[Dict[str, Any]],
    payment_amount: float,
    timestamp: str,
    response_message: str,
    order_id: Optional[str],
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Update customer records and return loyalty details."""
    if not customer_id:
        return response_message, None
    if not supabase_client.is_enabled() or not supabase_client.is_write_enabled():
        return response_message, None

    try:
        customer = supabase_client.select_one('customers', f'customer_id=eq.{customer_id}')
    except Exception as exc:
        logger.warning(f"⚠️  Supabase customer lookup failed: {exc}")
        return response_message, None

    if not customer:
        return response_message, None

    purchase_history = customer.get('purchase_history') or []
    if isinstance(purchase_history, str):
        try:
            purchase_history = json.loads(purchase_history)
        except Exception:
            purchase_history = []
    if not isinstance(purchase_history, list):
        purchase_history = []

    for item in normalized_items:
        purchase_history.append({
            'order_id': order_id,
            'sku': item.get('sku'),
            'qty': item.get('qty'),
            'unit_price': item.get('unit_price'),
            'amount': item.get('line_total'),
            'date': timestamp,
        })

    try:
        previous_total = Decimal(str(customer.get('total_spend') or 0))
    except (InvalidOperation, TypeError, ValueError):
        previous_total = Decimal("0")
    try:
        previous_items = int(customer.get('items_purchased') or 0)
    except (TypeError, ValueError):
        previous_items = 0
    try:
        previous_points = Decimal(str(customer.get('loyalty_points') or 0))
    except (InvalidOperation, TypeError, ValueError):
        previous_points = Decimal("0")
    old_tier = customer.get('loyalty_tier') or 'bronze'

    try:
        amount_decimal = Decimal(str(payment_amount))
    except (InvalidOperation, TypeError, ValueError):
        amount_decimal = Decimal("0")
    earned_points = (amount_decimal * Decimal("0.02")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    loyalty_points = previous_points + earned_points
    previous_points_int = int(previous_points.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    loyalty_points_int = int(loyalty_points.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    total_spend = (previous_total + amount_decimal).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    def _safe_qty(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    items_purchased = previous_items + sum(_safe_qty(item.get('qty')) for item in normalized_items)

    # Tier calculation with new rules
    points_int = int(loyalty_points.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if points_int >= 2000:
        tier = 'platinum'
    elif points_int >= 1000:
        tier = 'gold'
    elif points_int >= 500:
        tier = 'silver'
    else:
        tier = 'bronze'

    try:
        supabase_client.upsert('customers', {
            'customer_id': customer_id,
            'purchase_history': purchase_history,
            'loyalty_points': loyalty_points_int,
            'loyalty_tier': tier,
            'total_spend': float(total_spend),
            'items_purchased': items_purchased,
        }, conflict_column='customer_id')
    except Exception as exc:
        logger.warning(f"⚠️  Failed to upsert customer metrics: {exc}")
        return response_message, None

    suffix = ""
    earned_points_display = loyalty_points_int - previous_points_int
    if earned_points_display > 0:
        suffix += f" | Earned {earned_points_display} loyalty points!"
    if tier != old_tier:
        suffix += f" 🏆 Upgraded to {tier.capitalize()}!"

    # Calculate loyalty details for response
    loyalty_details = _calculate_loyalty_details(
        earned_points=earned_points_display,
        total_points=loyalty_points_int,
        old_tier=old_tier,
        new_tier=tier
    )

    return response_message + suffix, loyalty_details


def _calculate_loyalty_details(earned_points: int, total_points: int, old_tier: str, new_tier: str) -> Dict[str, Any]:
    """Calculate loyalty details including points needed for next tier."""
    tier_upgraded = old_tier != new_tier
    
    # Calculate points needed for next tier
    if total_points >= 2000:
        points_needed = 0  # Already at max tier
        next_tier = "platinum"
    elif total_points >= 1000:
        points_needed = 2000 - total_points
        next_tier = "platinum"
    elif total_points >= 500:
        points_needed = 1000 - total_points
        next_tier = "gold"
    else:
        points_needed = 500 - total_points
        next_tier = "silver"
    
    return {
        "earned_points": earned_points,
        "total_points": total_points,
        "current_tier": new_tier,
        "previous_tier": old_tier,
        "tier_upgraded": tier_upgraded,
        "points_needed": points_needed if points_needed > 0 else 0,
        "next_tier": next_tier
    }


def _update_inventory(normalized_items: List[Dict[str, Any]], default_store: Optional[str] = None) -> None:
    if not normalized_items:
        return

    fallback_store = _clean_store_identifier(default_store) or DEFAULT_STORE_ID
    supabase_enabled = supabase_client.is_enabled() and supabase_client.is_write_enabled()
    redis_client = getattr(redis_utils, "redis_client", None)

    adjustments: Dict[tuple[str, str], int] = {}

    for item in normalized_items:
        if not isinstance(item, dict):
            continue

        sku_value = item.get("sku") or item.get("product_id")
        sku = str(sku_value).strip() if sku_value else ""
        if not sku:
            continue

        try:
            qty_value = int(item.get("qty") or item.get("quantity") or 0)
        except (TypeError, ValueError):
            qty_value = 0
        if qty_value <= 0:
            continue

        store_id = None
        for candidate in (
            item.get("store_id"),
            item.get("store"),
            item.get("location"),
            default_store,
        ):
            store_id = _clean_store_identifier(candidate)
            if store_id:
                break

        store_id = store_id or fallback_store

        key = (sku, store_id)
        adjustments[key] = adjustments.get(key, 0) + qty_value

    if not adjustments:
        return

    timestamp_now = datetime.now().isoformat()

    for (sku, store_id), total_qty in adjustments.items():
        if supabase_enabled:
            try:
                inventory = supabase_client.select_one(
                    'inventory',
                    f'sku=eq.{sku}&store_id=eq.{store_id}'
                )
            except Exception as exc:
                logger.warning(f"⚠️  Supabase inventory lookup failed for {sku}/{store_id}: {exc}")
                inventory = None

            if inventory:
                try:
                    current_qty = int(inventory.get('quantity') or 0)
                except (TypeError, ValueError):
                    current_qty = 0
                new_qty = max(0, current_qty - total_qty)

                update_payload = {
                    'quantity': new_qty,
                }
                if 'updated_at' in inventory:
                    update_payload['updated_at'] = timestamp_now

                try:
                    supabase_client.update(
                        'inventory',
                        update_payload,
                        f'sku=eq.{sku}&store_id=eq.{store_id}'
                    )
                except Exception as exc:
                    logger.warning(f"⚠️  Failed to update inventory for {sku}/{store_id}: {exc}")
            else:
                logger.info(f"ℹ️  Inventory row missing for {sku}/{store_id}; skipped Supabase decrement")

        if redis_client:
            redis_location = 'online' if store_id == 'ONLINE' else store_id
            try:
                redis_key = redis_utils.get_stock_key(sku, redis_location)
                current_stock_raw = redis_client.get(redis_key)
                if current_stock_raw is None:
                    continue
                try:
                    current_stock = int(current_stock_raw)
                except (TypeError, ValueError):
                    current_stock = 0
                new_stock = max(0, current_stock - total_qty)
                redis_client.set(redis_key, new_stock)
            except Exception as exc:
                logger.warning(f"⚠️  Failed to update Redis inventory for {sku}/{store_id}: {exc}")


def _generate_tracking_id() -> str:
    """Generate a unique tracking ID for the order."""
    return f"TRK-{uuid.uuid4().hex[:12].upper()}"


def _format_payment_success_message(items: Optional[List[Dict[str, Any]]], amount: float) -> str:
    """Format a detailed payment success message with items list.
    
    Args:
        items: List of items in the order
        amount: Total amount paid
        
    Returns:
        Formatted message string
    """
    if not items:
        return f"✅ Payment successful! Order amount: ₹{amount:.2f}"
    
    # Build items summary
    items_list = []
    for item in items:
        if isinstance(item, dict):
            product_name = item.get("name") or item.get("product_name") or item.get("title") or "Item"
            qty = item.get("quantity", item.get("qty", 1))
            size = item.get("size") or ""
            color = item.get("color") or ""
            
            size_color = []
            if size:
                size_color.append(f"Size: {size}")
            if color:
                size_color.append(f"Color: {color}")
            
            variant_info = f" ({', '.join(size_color)})" if size_color else ""
            items_list.append(f"• {product_name} x{qty}{variant_info}")
    
    items_text = "\n".join(items_list) if items_list else "Items: Processing..."
    message = f"""✅ **Payment Successful!**

Your payment of ₹{amount:.2f} has been confirmed.

**Order Items:**
{items_text}

Your order is being processed and will be dispatched shortly!"""
    
    return message


def _notify_session_manager_of_payment(
    metadata: Dict[str, Any], 
    user_id: Optional[str], 
    payment_id: str, 
    order_id: str,
    items: Optional[List[Dict[str, Any]]] = None,
    amount: float = 0.0
) -> Tuple[Optional[str], str]:
    """Attempt to notify the Session Manager to append a chat message about a successful payment.

    This is best-effort and will not raise on error.
    
    Args:
        metadata: Payment metadata
        user_id: User/customer ID
        payment_id: Payment transaction ID
        order_id: Order ID
        items: List of items in the order
        amount: Total amount paid
        
    Returns:
        Tuple of (tracking_id, message) - tracking_id is None if generation failed
    """
    tracking_id = None
    try:
        session_mgr = os.getenv("SESSION_MANAGER_URL", "http://localhost:8000").rstrip('/')

        # Generate tracking ID
        tracking_id = _generate_tracking_id()

        # Prefer an explicit session token if provided in metadata
        session_token = None
        if isinstance(metadata, dict):
            session_token = metadata.get("session_token") or metadata.get("sessionToken")

        # Determine a phone hint if available
        phone = None
        if isinstance(metadata, dict):
            phone = metadata.get("phone") or metadata.get("phone_number") or metadata.get("phoneNumber")
        if not phone and user_id:
            phone = str(user_id)

        # Format detailed message with items
        detailed_message = _format_payment_success_message(items, amount)
        simple_message = f"✅ Payment successful for order {order_id}"

        # If we don't have a session token, try to recover one from the session/restore endpoint using phone
        if not session_token and phone:
            try:
                resp = requests.get(f"{session_mgr}/session/restore", headers={"X-Phone": phone}, timeout=3)
                if resp.status_code == 200:
                    data = resp.json()
                    session_token = data.get("session_token") or (data.get("session") or {}).get("session_token")
            except Exception:
                logger.debug("Could not restore session by phone; continuing without session token")

        # If we have a session token, post an update action
        if session_token:
            try:
                update_url = f"{session_mgr}/session/update"
                payload = {
                    "action": "chat_message",
                    "payload": {
                        "sender": "agent",
                        "message": detailed_message,
                        "metadata": {
                            "payment_id": payment_id, 
                            "order_id": order_id,
                            "tracking_id": tracking_id,
                            "amount": amount,
                            "items_count": len(items) if items else 0
                        }
                    }
                }
                headers = {"X-Session-Token": session_token, "Content-Type": "application/json"}
                resp = requests.post(update_url, json=payload, headers=headers, timeout=3)
                if resp.status_code != 200:
                    logger.warning(f"[payment] Session update returned {resp.status_code}: {resp.text}")
                else:
                    logger.info(f"[payment] Notified session manager for payment {payment_id} with tracking {tracking_id}")
            except Exception as e:
                logger.warning(f"[payment] Failed to notify session manager: {e}")
        else:
            logger.debug("No session token or phone available; skipping session notify")
    except Exception as e:
        logger.warning(f"[payment] Unexpected error notifying session manager: {e}")
    
    return tracking_id, f"✅ Payment successful"


# ==========================================
# REQUEST/RESPONSE MODELS
# ==========================================

class PaymentRequest(BaseModel):
    user_id: str = Field(..., description="User ID")
    amount: float = Field(..., gt=0, description="Payment amount")
    payment_method: str = Field(..., description="Payment method: upi, card, wallet, netbanking, cod")
    order_id: Optional[str] = Field(None, description="Associated order ID")
    metadata: dict = Field(default_factory=dict, description="Additional payment metadata")


class PaymentResponse(BaseModel):
    success: bool
    transaction_id: str
    amount: float
    payment_method: str
    gateway_txn_id: Optional[str]
    cashback: float
    message: str
    timestamp: str
    order_id: Optional[str]


class TransactionResponse(BaseModel):
    transaction_id: str
    user_id: str
    amount: str
    payment_method: str
    status: str
    gateway_txn_id: Optional[str]
    timestamp: str
    order_id: Optional[str]


class RefundRequest(BaseModel):
    transaction_id: str = Field(..., description="Transaction ID to refund")
    amount: Optional[float] = Field(None, description="Refund amount (partial or full)")
    reason: str = Field(..., description="Reason for refund")


class RefundResponse(BaseModel):
    success: bool
    refund_id: str
    transaction_id: str
    refund_amount: float
    message: str
    timestamp: str


class AuthorizationRequest(BaseModel):
    user_id: str = Field(..., description="User ID")
    amount: float = Field(..., gt=0, description="Amount to authorize")
    payment_method: str = Field(..., description="Payment method")
    order_id: str = Field(..., description="Order ID")


class AuthorizationResponse(BaseModel):
    success: bool
    authorization_id: str
    amount: float
    status: str  # authorized, declined
    message: str
    timestamp: str


class CaptureRequest(BaseModel):
    authorization_id: str = Field(..., description="Authorization ID to capture")
    amount: Optional[float] = Field(None, description="Amount to capture (partial or full)")


class CaptureResponse(BaseModel):
    success: bool
    transaction_id: str
    authorization_id: str
    captured_amount: float
    message: str
    timestamp: str


class POSRequest(BaseModel):
    store_id: str = Field(..., description="Store ID")
    terminal_id: str = Field(..., description="POS terminal ID")
    barcode: str = Field(..., description="Product barcode scanned")
    payment_method: str = Field(..., description="Payment method: card, upi, cash")
    amount: float = Field(..., gt=0, description="Transaction amount")


class POSResponse(BaseModel):
    success: bool
    transaction_id: str
    store_id: str
    terminal_id: str
    barcode: str
    amount: float
    payment_method: str
    message: str
    timestamp: str


class RazorpayCreateOrderRequest(BaseModel):
    amount_rupees: float = Field(..., gt=0, description="Amount to collect via Razorpay test order")
    currency: str = Field("INR", description="Three letter currency code")
    receipt: Optional[str] = Field(None, description="Optional receipt identifier shown in Razorpay dashboard")
    notes: Dict[str, str] = Field(default_factory=dict, description="Additional metadata forwarded to Razorpay")
    items: Optional[list] = Field(default_factory=list)  


class RazorpayVerifyRequest(BaseModel):
    razorpay_payment_id: str = Field(..., description="Payment ID returned by Razorpay Checkout")
    razorpay_order_id: str = Field(..., description="Order ID returned during create-order")
    razorpay_signature: Optional[str] = Field(None, description="Signature for server-side verification")
    amount_rupees: Optional[float] = Field(None, gt=0, description="Captured amount in rupees")
    method: Optional[str] = Field(None, description="Payment method recorded for this transaction")
    discount_applied: Optional[float] = Field(0.0, ge=0, description="Applied discount value")
    gst: Optional[float] = Field(None, ge=0, description="GST amount for record keeping")
    idempotency_key: Optional[str] = Field(None, description="Client supplied idempotency key")
    user_id: Optional[str] = Field(None, description="User reference for auditing")
    order_id: Optional[str] = Field(None, description="Preferred canonical order identifier")


# ==========================================
# ROUTES
# ==========================================

@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "service": "Payment Agent",
        "status": "running",
        "version": "1.0.0",
        "timestamp": datetime.now().isoformat()
    }


@razorpay_router.post("/create-order")
async def create_razorpay_order(payload: RazorpayCreateOrderRequest):
    print("🟢 CREATE ORDER ITEMS FROM FRONTEND:", payload.items)
    """Create a Razorpay order in test mode (no real charge)."""
    client = _ensure_razorpay_client()

    amount_rupees = Decimal(str(payload.amount_rupees))
    if amount_rupees <= 0:
        raise HTTPException(status_code=400, detail="amount_rupees must be greater than zero")

    amount_rupees = amount_rupees.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    amount_paise = int((amount_rupees * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    currency = (payload.currency or "INR").upper()

    notes_dict = dict(payload.notes or {})

    store_id_hint = _extract_store_from_metadata(notes_dict)
    if not store_id_hint:
        store_id_hint = _extract_store_from_metadata({"items": payload.items})

    if store_id_hint and "store_id" not in notes_dict:
        notes_dict["store_id"] = store_id_hint

    order_payload = {
        "amount": amount_paise,
        "currency": currency,
        "payment_capture": 1,
    }
    if payload.receipt:
        order_payload["receipt"] = payload.receipt
    if notes_dict:
        order_payload["notes"] = notes_dict

    # Generate or reuse canonical order ID for display and downstream flows
    if payload.receipt and orders_repository.is_valid_order_id(payload.receipt):
        app_order_id = payload.receipt
    else:
        app_order_id = orders_repository.generate_next_order_id()
    order_payload["receipt"] = app_order_id

    order = client.order.create(order_payload)

    # Validate items are provided
    if not payload.items:
        raise HTTPException(status_code=400, detail="items are required")

    normalized_items = _normalize_order_items(payload.items, default_store=store_id_hint)

    orders_repository.upsert_order_record({
        "order_id": app_order_id,                 # EXISTS HERE
        "customer_id": notes_dict.get("customer_id"),
        "status": "created",                      # ORDER NOT PAID YET
        "items": normalized_items,                   # SOURCE OF TRUTH
        "created_at": datetime.now().isoformat(),
    })
    return {
        "order": order,
        "razorpay_key_id": RAZORPAY_KEY_ID,
        "amount_rupees": _quantize(amount_rupees),
        "order_id": app_order_id,
    }


@app.get("/payment/next-order-id")
async def get_next_order_id():
    """Reserve and return the next canonical order identifier."""
    return {"order_id": orders_repository.generate_next_order_id()}


@razorpay_router.post("/verify-payment")
async def verify_razorpay_payment(payload: RazorpayVerifyRequest):
    print("🔥🔥🔥 VERIFY PAYMENT HIT 🔥🔥🔥", flush=True)
    """Record a Razorpay test payment as successful and persist it."""
    if not payload.razorpay_payment_id or not payload.razorpay_order_id:
        raise HTTPException(status_code=400, detail="razorpay_payment_id and razorpay_order_id are required")

    amount_rupees = Decimal(str(payload.amount_rupees)) if payload.amount_rupees is not None else Decimal("0")
    if amount_rupees < 0:
        raise HTTPException(status_code=400, detail="amount_rupees cannot be negative")

    amount_rupees = amount_rupees.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    discount_value = Decimal(str(payload.discount_applied)) if payload.discount_applied is not None else Decimal("0")
    discount_value = discount_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if payload.gst is not None:
        gst_value = Decimal(str(payload.gst)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    elif amount_rupees:
        gst_value = (amount_rupees * Decimal("0.18")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        gst_value = Decimal("0.00")

    method = (payload.method or "upi").lower()
    timestamp = datetime.now().isoformat()


    customer_id = _resolve_customer_id(payload.user_id, None)

    response_message = "Payment captured successfully"

    if not payload.order_id:
        raise HTTPException(
            status_code=400,
            detail="order_id (ORDxxxx) is required"
        )

    if not orders_repository.is_valid_order_id(payload.order_id):
        raise HTTPException(status_code=400, detail="Invalid order_id format")

    canonical_order_id = payload.order_id
    

    logger.warning(f"🟡 CANONICAL ORDER ID: {canonical_order_id}")


    order = supabase_client.select_one(
    "orders",
    f"order_id=eq.{canonical_order_id}"
    )

    if not order:
        raise HTTPException(status_code=404, detail="Order not found in Supabase")

    logger.warning(f"🟡 ORDER FROM SUPABASE: {order}")

    if not customer_id:
        order_customer = order.get("customer_id") if isinstance(order, dict) else None
        if order_customer:
            customer_id = str(order_customer)

    # Fallback to orders_repository if Supabase returns None
    if not order:
        logger.warning(f"⚠️  Order {canonical_order_id} not found in Supabase, checking local repository...")
        try:
            order = orders_repository.get_order(canonical_order_id)
            if order:
                logger.info(f"✅ Found order in local repository: {canonical_order_id}")
            else:
                logger.error(f"❌ Order {canonical_order_id} not found in local repository either")
                raise HTTPException(status_code=404, detail=f"Order {canonical_order_id} not found")
        except Exception as e:
            logger.error(f"❌ Error fetching order from repository: {e}")
            raise HTTPException(status_code=404, detail=f"Order {canonical_order_id} not found")

    items_payload = order.get("items", []) if order else []
    if isinstance(items_payload, str):
        try:
            items_payload = json.loads(items_payload)
        except json.JSONDecodeError:
            items_payload = []

    if not items_payload:
        raise HTTPException(status_code=400, detail="Order items missing")

    logger.warning(f"🟢 ITEMS FROM ORDER: {items_payload}")

    normalized_items = _normalize_order_items(items_payload)
    logger.warning(f"🟣 NORMALIZED ITEMS: {normalized_items}")
    store_id_hint = normalized_items[0].get("store_id") if normalized_items else None


    # Idempotency for webhook
    idempotency_key = payload.idempotency_key or f"idemp_{payload.razorpay_payment_id}"

    try:
        orders_repository.upsert_order_record(
                {
                    "order_id": canonical_order_id,
                    "customer_id": customer_id,
                    "total_amount": float(amount_rupees),
                    "status": "paid",
                    "items": normalized_items,
                    "created_at": timestamp,
                }
        )
    except Exception as exc:
        logger.warning(f"⚠️  Failed to ensure order {canonical_order_id}: {exc}")

    # Update customer purchase history, metrics, and inventory on payment success
    loyalty_details = None
    try:
        response_message, loyalty_details = _update_customer_records(
            customer_id,
            normalized_items,
            float(amount_rupees),
            timestamp,
            response_message,
            canonical_order_id,
        )
        _update_inventory(normalized_items, default_store=store_id_hint)
    except Exception as exc:
        logger.warning(f"⚠️  Failed to update customer/inventory for order {canonical_order_id}: {exc}")

    payment_id = payment_repository.generate_next_payment_id()
    payment_id = payment_repository.upsert_payment_record(
        {
            "payment_id": payment_id,
            "order_id": canonical_order_id,
            "status": "success",
            "amount_rupees": _quantize(amount_rupees),
            "discount_applied": _quantize(discount_value),
            "gst": _quantize(gst_value),
            "method": method,
            "gateway_ref": payload.razorpay_payment_id,
            "idempotency_key": idempotency_key,
            "created_at": timestamp,
        }
    )

    metadata = {
        "idempotency_key": idempotency_key,
        "razorpay_signature": payload.razorpay_signature,
        "gateway_payment_id": payload.razorpay_payment_id,
    }
    if customer_id:
        metadata["customer_id"] = customer_id

    transaction_payload = {
        "transaction_id": payment_id,
        "user_id": payload.user_id or "",
        "amount": _quantize(amount_rupees),
        "payment_method": method,
        "status": "success",
        "gateway_txn_id": payload.razorpay_payment_id,
        "cashback": "0",
        "timestamp": timestamp,
        "order_id": canonical_order_id,
        "metadata": json.dumps({**metadata, "customer_id": customer_id} if customer_id else metadata),
    }

    user_key = payload.user_id or payment_id
    _store_transaction_safely(payment_id, transaction_payload, user_key, amount_rupees)
    try:
        _notify_session_manager_of_payment(
            metadata, 
            payload.user_id or customer_id, 
            payment_id, 
            canonical_order_id,
            items=normalized_items,
            amount=float(amount_rupees)
        )
    except Exception:
        logger.debug("Notification to session manager failed (non-fatal)")

    response = {
        "status": "ok",
        "payment_id": payment_id,
        "order_id": canonical_order_id,
        "gateway_payment_id": payload.razorpay_payment_id,
    }
    
    # Include loyalty details if available
    if loyalty_details:
        response["loyalty"] = loyalty_details
    
    return response


app.include_router(razorpay_router)



@app.post("/payment/process", response_model=PaymentResponse)
async def process_payment(request: PaymentRequest):
    """
    Process a payment transaction
    Steps:
    1. Validate payment method
    2. Simulate payment gateway call
    3. Calculate cashback
    4. Store transaction details
    5. Return response
    """
    try:
        # Generate timestamp for transaction
        timestamp = datetime.utcnow().isoformat()
        
        # Step 1: Validate payment method
        if not redis_utils.validate_payment_method(request.payment_method):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid payment method: {request.payment_method}. Supported: upi, card, wallet, netbanking, cod"
            )
        
        # Step 2: Generate canonical payment and order identifiers
        payment_id = payment_repository.generate_next_payment_id()
        original_order_reference = request.order_id or ""
        if orders_repository.is_valid_order_id(original_order_reference):
            canonical_order_id = original_order_reference
        else:
            canonical_order_id = orders_repository.generate_next_order_id()
        
        
        customer_id = _resolve_customer_id(
            request.user_id,
            request.metadata if isinstance(request.metadata, dict) else None,
        )
        
        # Step 3: Simulate payment gateway
        gateway_response = redis_utils.simulate_payment_gateway(
            request.payment_method,
            request.amount
        )
        
        if not gateway_response["success"]:
            raise HTTPException(status_code=400, detail=gateway_response["message"])
        
        # Step 4: Calculate cashback
        cashback = redis_utils.calculate_cashback(request.amount, request.payment_method)
        
        # Step 5: Store transaction
        metadata_payload = {
            "source": "process_payment",
            "original_order_reference": original_order_reference,
        }
        metadata_dict = request.metadata if isinstance(request.metadata, dict) else {}
        if metadata_dict:
            metadata_payload["request_metadata"] = metadata_dict
        if customer_id:
            metadata_payload["customer_id"] = customer_id
        store_id_hint = _extract_store_from_metadata(metadata_dict)

        transaction_data = {
            "transaction_id": payment_id,
            "user_id": request.user_id,
            "amount": str(request.amount),
            "payment_method": request.payment_method,
            "status": "success",
            "gateway_txn_id": gateway_response["gateway_txn_id"],
            "cashback": str(cashback),
            "timestamp": timestamp,
            "order_id": canonical_order_id,
            "metadata": json.dumps(metadata_payload),
        }
        
        # Step 6: Log payment attempt
        redis_utils.store_payment_attempt(request.user_id, {
            "transaction_id": payment_id,
            "amount": request.amount,
            "status": "success"
        })
        # Step 7: Award loyalty points after successful payment
        # Removed: now done directly in Supabase
        
        # Build response message
        response_message = f"{gateway_response['message']}. Cashback: ₹{cashback}"
        
        # Get items from metadata for updates
        items_payload: List[Dict[str, Any]] = []
        items_payload = metadata_dict.get('items', []) or []
        if isinstance(items_payload, str):
            try:
                items_payload = json.loads(items_payload)
            except json.JSONDecodeError:
                items_payload = []
        if not store_id_hint:
            store_id_hint = _extract_store_from_metadata({"items": items_payload})
        normalized_items = _normalize_order_items(items_payload, default_store=store_id_hint)
        if not store_id_hint and normalized_items:
            store_id_hint = _clean_store_identifier(normalized_items[0].get("store_id"))
        if store_id_hint:
            metadata_payload["store_id"] = store_id_hint
            transaction_data["metadata"] = json.dumps(metadata_payload)
        
        redis_utils.store_transaction(payment_id, transaction_data)
        
        # Register / update order to ensure Supabase FK integrity
        try:
            order_record = {
                'order_id': canonical_order_id,
                'customer_id': customer_id,
                'total_amount': round(request.amount, 2),
                'status': 'paid',
                'created_at': timestamp
            }
            order_record['items'] = normalized_items

            orders_repository.upsert_order_record(order_record)
            logger.info(f"✅ Order registered: {canonical_order_id} (payment: {payment_id})")
        except Exception as e:
            logger.warning(f"⚠️  Failed to register order {canonical_order_id}: {e}")

        # Persist payment record to shared dataset / Supabase
        try:
            amount_decimal = Decimal(str(request.amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            try:
                discount_source = metadata_dict.get("discount_applied", 0)
                discount_value = Decimal(str(discount_source))
            except (InvalidOperation, TypeError, ValueError):
                discount_value = Decimal("0")
            discount_value = discount_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            if isinstance(request.metadata, dict) and "gst" in request.metadata:
                try:
                    gst_source = request.metadata.get("gst", 0)
                    gst_value = Decimal(str(gst_source))
                except (InvalidOperation, TypeError, ValueError):
                    gst_value = amount_decimal * Decimal("0.18")
            else:
                gst_value = amount_decimal * Decimal("0.18")
            gst_value = gst_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            payment_id = payment_repository.upsert_payment_record(
                {
                    "payment_id": payment_id,
                    "order_id": canonical_order_id,
                    "status": "success",
                    "amount_rupees": _quantize(amount_decimal),
                    "discount_applied": _quantize(discount_value),
                    "gst": _quantize(gst_value),
                    "method": request.payment_method,
                    "gateway_ref": gateway_response["gateway_txn_id"],
                    "idempotency_key": metadata_dict.get("idempotency_key", payment_id),
                    "created_at": timestamp,
                }
            )
        except Exception as exc:
            logger.warning(f"⚠️  Failed to persist payment record {payment_id}: {exc}")
        
        # Perform post-payment updates
        try:
            response_message = _update_customer_records(
                customer_id,
                normalized_items,
                request.amount,
                timestamp,
                response_message,
                canonical_order_id,
            )
            _update_inventory(normalized_items, default_store=store_id_hint)

            response_dict = {
                "success": True,
                "transaction_id": payment_id,
                "amount": request.amount,
                "payment_method": request.payment_method,
                "gateway_txn_id": gateway_response["gateway_txn_id"],
                "cashback": cashback,
                "message": response_message,
                "timestamp": timestamp,
                "order_id": canonical_order_id
            }

            try:
                _notify_session_manager_of_payment(
                    metadata_dict, 
                    request.user_id or customer_id, 
                    payment_id, 
                    canonical_order_id,
                    items=normalized_items,
                    amount=request.amount
                )
            except Exception:
                logger.debug("Notification to session manager failed (non-fatal)")

        except Exception as e:
            logger.error(f"Post-payment update failed: {e}")
            # Do not mark as completed
        
        return PaymentResponse(
            success=True,
            transaction_id=payment_id,
            amount=request.amount,
            payment_method=request.payment_method,
            gateway_txn_id=gateway_response["gateway_txn_id"],
            cashback=cashback,
            message=response_message,
            timestamp=timestamp,
            order_id=canonical_order_id
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/payment/transaction/{transaction_id}", response_model=TransactionResponse)
async def get_transaction(transaction_id: str):
    """Retrieve transaction details by transaction ID"""
    try:
        transaction = redis_utils.get_transaction(transaction_id)
        
        if not transaction:
            raise HTTPException(status_code=404, detail="Transaction not found")
        
        return TransactionResponse(
            transaction_id=transaction["transaction_id"],
            user_id=transaction["user_id"],
            amount=transaction["amount"],
            payment_method=transaction["payment_method"],
            status=transaction["status"],
            gateway_txn_id=transaction.get("gateway_txn_id"),
            timestamp=transaction["timestamp"],
            order_id=transaction.get("order_id")
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/payment/user-transactions/{user_id}")
async def get_user_transactions(user_id: str, limit: int = 10):
    """Get recent transactions for a user"""
    try:
        transactions = redis_utils.get_user_transactions(user_id, limit)
        
        return {
            "user_id": user_id,
            "transaction_count": len(transactions),
            "transactions": transactions
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/payment/refund", response_model=RefundResponse)
async def process_refund(request: RefundRequest):
    """
    Process a refund for a transaction
    """
    try:
        # Retrieve original transaction
        transaction = redis_utils.get_transaction(request.transaction_id)
        
        if not transaction:
            raise HTTPException(status_code=404, detail="Transaction not found")
        
        # Get original amount
        original_amount = float(transaction["amount"])
        
        # Determine refund amount
        refund_amount = request.amount if request.amount else original_amount
        
        if refund_amount > original_amount:
            raise HTTPException(
                status_code=400,
                detail=f"Refund amount cannot exceed original amount of ₹{original_amount}"
            )
        
        # Generate refund ID
        refund_id = f"REFUND_{uuid.uuid4().hex[:12].upper()}"
        
        # Store refund transaction
        refund_data = {
            "refund_id": refund_id,
            "transaction_id": request.transaction_id,
            "user_id": transaction["user_id"],
            "refund_amount": str(refund_amount),
            "original_amount": str(original_amount),
            "reason": request.reason,
            "status": "processed",
            "timestamp": datetime.now().isoformat()
        }
        
        redis_utils.store_transaction(refund_id, refund_data)
        
        return RefundResponse(
            success=True,
            refund_id=refund_id,
            transaction_id=request.transaction_id,
            refund_amount=refund_amount,
            message=f"Refund of ₹{refund_amount} processed successfully",
            timestamp=datetime.now().isoformat()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/payment/methods")
async def get_payment_methods():
    """Get list of supported payment methods"""
    return {
        "supported_methods": [
            {
                "id": "upi",
                "name": "UPI",
                "cashback": "1%",
                "processing_time": "Instant"
            },
            {
                "id": "card",
                "name": "Credit/Debit Card",
                "cashback": "2%",
                "processing_time": "Instant"
            },
            {
                "id": "wallet",
                "name": "Digital Wallet",
                "cashback": "1.5%",
                "processing_time": "Instant"
            },
            {
                "id": "netbanking",
                "name": "Net Banking",
                "cashback": "0.5%",
                "processing_time": "2-3 minutes"
            },
            {
                "id": "cod",
                "name": "Cash on Delivery",
                "cashback": "0%",
                "processing_time": "On delivery"
            }
        ]
    }


@app.post("/payment/authorize", response_model=AuthorizationResponse)
async def authorize_payment(request: AuthorizationRequest):
    """
    Payment Gateway Stub: Authorize payment (pre-authorization)
    This simulates authorization without capturing funds
    Can be declined based on random simulation
    """
    try:
        import random
        
        # Validate payment method
        if not redis_utils.validate_payment_method(request.payment_method):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid payment method: {request.payment_method}"
            )
        
        # Generate authorization ID
        auth_id = f"AUTH_{uuid.uuid4().hex[:12].upper()}"
        
        # Simulate authorization with 10% decline rate
        is_declined = random.random() < 0.1
        
        if is_declined:
            # Store declined authorization
            auth_data = {
                "authorization_id": auth_id,
                "user_id": request.user_id,
                "amount": str(request.amount),
                "payment_method": request.payment_method,
                "order_id": request.order_id,
                "status": "declined",
                "reason": "Insufficient funds or card declined",
                "timestamp": datetime.now().isoformat()
            }
            redis_utils.store_transaction(auth_id, auth_data)
            
            return AuthorizationResponse(
                success=False,
                authorization_id=auth_id,
                amount=request.amount,
                status="declined",
                message="Payment authorization declined. Please try a different payment method.",
                timestamp=datetime.now().isoformat()
            )
        
        # Store successful authorization
        auth_data = {
            "authorization_id": auth_id,
            "user_id": request.user_id,
            "amount": str(request.amount),
            "payment_method": request.payment_method,
            "order_id": request.order_id,
            "status": "authorized",
            "timestamp": datetime.now().isoformat(),
            "captured": "false"
        }
        redis_utils.store_transaction(auth_id, auth_data)
        
        return AuthorizationResponse(
            success=True,
            authorization_id=auth_id,
            amount=request.amount,
            status="authorized",
            message=f"Payment of ₹{request.amount} authorized successfully",
            timestamp=datetime.now().isoformat()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/payment/capture", response_model=CaptureResponse)
async def capture_payment(request: CaptureRequest):
    """
    Payment Gateway Stub: Capture authorized payment
    This finalizes the transaction and transfers funds
    """
    try:
        # Retrieve authorization
        auth_data = redis_utils.get_transaction(request.authorization_id)
        
        if not auth_data:
            raise HTTPException(status_code=404, detail="Authorization not found")
        
        if auth_data.get("status") != "authorized":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot capture. Authorization status: {auth_data.get('status')}"
            )
        
        if auth_data.get("captured") == "true":
            raise HTTPException(status_code=400, detail="Authorization already captured")
        
        # Determine capture amount
        authorized_amount = float(auth_data["amount"])
        capture_amount = request.amount if request.amount else authorized_amount
        
        if capture_amount > authorized_amount:
            raise HTTPException(
                status_code=400,
                detail=f"Capture amount cannot exceed authorized amount of ₹{authorized_amount}"
            )
        
        # Generate transaction ID
        txn_id = f"TXN_{uuid.uuid4().hex[:12].upper()}"
        
        # Calculate cashback
        cashback = redis_utils.calculate_cashback(capture_amount, auth_data["payment_method"])
        
        # Store captured transaction
        txn_data = {
            "transaction_id": txn_id,
            "authorization_id": request.authorization_id,
            "user_id": auth_data["user_id"],
            "amount": str(capture_amount),
            "payment_method": auth_data["payment_method"],
            "status": "captured",
            "cashback": str(cashback),
            "timestamp": datetime.now().isoformat(),
            "order_id": auth_data.get("order_id", "")
        }
        redis_utils.store_transaction(txn_id, txn_data)
        
        # Update authorization as captured
        auth_data["captured"] = "true"
        auth_data["capture_txn_id"] = txn_id
        redis_utils.store_transaction(request.authorization_id, auth_data)
        
        return CaptureResponse(
            success=True,
            transaction_id=txn_id,
            authorization_id=request.authorization_id,
            captured_amount=capture_amount,
            message=f"Payment of ₹{capture_amount} captured successfully. Cashback: ₹{cashback}",
            timestamp=datetime.now().isoformat()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/payment/pos", response_model=POSResponse)
async def process_pos_payment(request: POSRequest):
    """
    POS Integration: Simulated in-store terminal interactions
    Handles barcode scan and payment processing at physical stores
    """
    try:
        # Validate store and terminal
        if not request.store_id or not request.terminal_id:
            raise HTTPException(status_code=400, detail="Invalid store or terminal ID")
        
        # Validate barcode (simulate product lookup)
        if len(request.barcode) < 8:
            raise HTTPException(status_code=400, detail="Invalid barcode format")
        
        # Validate payment method for POS
        valid_pos_methods = ["card", "upi", "cash"]
        if request.payment_method not in valid_pos_methods:
            raise HTTPException(
                status_code=400,
                detail=f"Payment method not supported for POS. Use: {', '.join(valid_pos_methods)}"
            )
        
        # Generate POS transaction ID
        pos_txn_id = f"POS_{request.store_id}_{uuid.uuid4().hex[:8].upper()}"
        
        # Simulate payment processing
        import random
        is_success = random.random() > 0.05  # 95% success rate
        
        if not is_success:
            raise HTTPException(
                status_code=400,
                detail="POS transaction failed. Please retry or use alternative payment method."
            )
        
        # Store POS transaction
        pos_data = {
            "transaction_id": pos_txn_id,
            "type": "pos",
            "store_id": request.store_id,
            "terminal_id": request.terminal_id,
            "barcode": request.barcode,
            "amount": str(request.amount),
            "payment_method": request.payment_method,
            "status": "success",
            "timestamp": datetime.now().isoformat()
        }
        redis_utils.store_transaction(pos_txn_id, pos_data)
        
        return POSResponse(
            success=True,
            transaction_id=pos_txn_id,
            store_id=request.store_id,
            terminal_id=request.terminal_id,
            barcode=request.barcode,
            amount=request.amount,
            payment_method=request.payment_method,
            message=f"POS payment of ₹{request.amount} processed successfully",
            timestamp=datetime.now().isoformat()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8003)

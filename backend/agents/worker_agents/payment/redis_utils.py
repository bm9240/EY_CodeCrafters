# Redis utilities for Payment Agent

import redis
import os
from typing import Optional
from dotenv import load_dotenv
import uuid
from datetime import datetime

load_dotenv()

# Redis Configuration
REDIS_URL = os.getenv("REDIS_URL")

# Initialize Redis client
redis_client = redis.from_url(
    REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=5
) if REDIS_URL else None


def store_transaction(transaction_id: str, transaction_data: dict, expiry_days: int = 90) -> bool:
    """
    Store transaction details in Redis
    Expires after specified days for compliance
    """
    if not redis_client:
        raise RuntimeError("Redis client not initialized")
    
    key = f"transaction:{transaction_id}"
    
    # Add timestamp
    transaction_data["stored_at"] = datetime.now().isoformat()
    
    # Store as hash
    redis_client.hset(key, mapping=transaction_data)
    
    # Set expiry
    redis_client.expire(key, expiry_days * 24 * 3600)
    
    return True


def get_transaction(transaction_id: str) -> Optional[dict]:
    """Retrieve transaction details from Redis"""
    if not redis_client:
        raise RuntimeError("Redis client not initialized")
    
    key = f"transaction:{transaction_id}"
    
    if redis_client.exists(key):
        return redis_client.hgetall(key)
    
    return None


def store_payment_attempt(user_id: str, attempt_data: dict) -> bool:
    """Store payment attempt for fraud detection"""
    if not redis_client:
        raise RuntimeError("Redis client not initialized")
    
    key = f"payment_attempts:{user_id}"
    timestamp = datetime.now().timestamp()
    
    # Store attempt with numeric score so Redis ZADD accepts it
    redis_client.zadd(key, {str(attempt_data): timestamp})
    
    # Keep only last 100 attempts
    redis_client.zremrangebyrank(key, 0, -101)
    
    # Set expiry (30 days)
    redis_client.expire(key, 30 * 24 * 3600)
    
    return True


def get_user_transactions(user_id: str, limit: int = 10) -> list:
    """Get recent transactions for a user"""
    if not redis_client:
        raise RuntimeError("Redis client not initialized")
    
    # Search for transactions by user_id pattern
    pattern = f"transaction:*"
    transactions = []
    
    for key in redis_client.scan_iter(match=pattern, count=100):
        tx_data = redis_client.hgetall(key)
        if tx_data.get("user_id") == user_id:
            transactions.append(tx_data)
            if len(transactions) >= limit:
                break
    
    return transactions


def simulate_payment_gateway(payment_method: str, amount: float) -> dict:
    """
    Simulate payment gateway response
    In production, this would call actual payment gateway APIs
    """
    
    # Simulate different payment methods
    if payment_method == "upi":
        return {
            "success": True,
            "gateway": "UPI",
            "gateway_txn_id": f"UPI{uuid.uuid4().hex[:10].upper()}",
            "message": "Payment successful via UPI"
        }
    
    elif payment_method == "card":
        return {
            "success": True,
            "gateway": "Card",
            "gateway_txn_id": f"CARD{uuid.uuid4().hex[:10].upper()}",
            "message": "Payment successful via Card"
        }
    
    elif payment_method == "wallet":
        return {
            "success": True,
            "gateway": "Wallet",
            "gateway_txn_id": f"WALLET{uuid.uuid4().hex[:10].upper()}",
            "message": "Payment successful via Digital Wallet"
        }
    
    elif payment_method == "netbanking":
        return {
            "success": True,
            "gateway": "NetBanking",
            "gateway_txn_id": f"NB{uuid.uuid4().hex[:10].upper()}",
            "message": "Payment successful via Net Banking"
        }
    
    elif payment_method == "cod":
        return {
            "success": True,
            "gateway": "COD",
            "gateway_txn_id": f"COD{uuid.uuid4().hex[:10].upper()}",
            "message": "Cash on Delivery confirmed"
        }
    
    else:
        return {
            "success": False,
            "gateway": "Unknown",
            "gateway_txn_id": None,
            "message": "Invalid payment method"
        }


def validate_payment_method(payment_method: str) -> bool:
    """Validate if payment method is supported"""
    valid_methods = ["upi", "card", "wallet", "netbanking", "cod"]
    return payment_method.lower() in valid_methods


def calculate_cashback(amount: float, payment_method: str) -> float:
    """Calculate cashback based on payment method"""
    cashback_rates = {
        "upi": 0.01,      # 1% cashback
        "card": 0.02,     # 2% cashback
        "wallet": 0.015,  # 1.5% cashback
        "netbanking": 0.005,  # 0.5% cashback
        "cod": 0.0        # No cashback
    }
    
    rate = cashback_rates.get(payment_method.lower(), 0.0)
    return round(amount * rate, 2)


def get_stock_key(sku: str, location: str) -> str:
    """Generate Redis key for inventory stock.
    
    Args:
        sku: Product SKU
        location: Store location/identifier (e.g., 'STORE_MUMBAI' or 'online')
        
    Returns:
        Redis key for inventory lookup
    """
    # Normalize location
    if location.startswith("store:"):
        location = location.split(":", 1)[1]
    
    location = location.replace("-", "_").upper()
    sku = sku.upper()
    
    return f"inventory:stock:{sku}:{location}"

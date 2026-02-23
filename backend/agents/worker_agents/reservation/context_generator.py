"""
Context Generation Service - Generates AI-powered customer context summaries
integrating with the Sales Agent and customer data.

This module provides utilities for:
1. Retrieving customer purchase history
2. Generating context summaries via Sales Agent
3. Storing summaries in the reservation database
"""

import logging
import requests
import sys
from pathlib import Path
from typing import Optional, Dict

# Add backend to path
backend_path = Path(__file__).resolve().parent.parent.parent.parent
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

from db import supabase_client

logger = logging.getLogger(__name__)

SALES_AGENT_URL = "http://localhost:8000"  # Adjust based on config


def get_customer_profile(customer_id: str) -> Optional[Dict]:
    """
    Retrieve customer profile and purchase history.
    
    Args:
        customer_id: The customer ID
        
    Returns:
        Dictionary with customer info and purchase history, or None
    """
    try:
        if not supabase_client.FEATURE_SUPABASE_READ:
            logger.warning("Supabase read not enabled")
            return None
        
        # Get customer info
        customers = supabase_client.select(
            "customers",
            params=f"id=eq.{customer_id}&limit=1"
        )
        
        if not customers:
            logger.warning(f"Customer not found: {customer_id}")
            return None
        
        customer = customers[0]
        
        # Try to get order history
        orders = []
        try:
            orders = supabase_client.select(
                "orders",
                params=f"customer_id=eq.{customer_id}&order=created_at.desc&limit=5"
            )
        except Exception as e:
            logger.debug(f"Could not fetch order history: {e}")
        
        return {
            "customer_id": customer_id,
            "name": customer.get("name"),
            "email": customer.get("email"),
            "phone": customer.get("phone"),
            "order_count": len(orders) if orders else 0,
            "recent_orders": orders[:3] if orders else []
        }
    
    except Exception as e:
        logger.error(f"Error getting customer profile: {e}")
        return None


def generate_context_summary(
    customer_id: str,
    sku: str,
    quantity: int,
    store_location: str
) -> Optional[str]:
    """
    Generate an AI-powered context summary for a reservation.
    
    Uses the Sales Agent to create a 1-paragraph summary about the customer's
    occasion, preferences, and helpful selling cues.
    
    Args:
        customer_id: Customer ID
        sku: Product SKU
        quantity: Quantity reserved
        store_location: Store location
        
    Returns:
        Context summary string, or None if generation fails
    """
    try:
        # Get customer profile
        profile = get_customer_profile(customer_id)
        if not profile:
            logger.warning(f"Could not generate context for customer {customer_id}")
            return None
        
        # Prepare context message for Sales Agent
        context_prompt = f"""
        Generate a brief 1-paragraph customer context summary for a store associate.
        
        Customer: {profile.get('name', 'Valued Customer')}
        Previous Orders: {profile.get('order_count', 0)}
        Product SKU: {sku}
        Quantity: {quantity}
        Store: {store_location}
        Recent Order Details: {str(profile.get('recent_orders', [])[:1])}
        
        Create a helpful, professional summary that includes:
        1. Likely occasion (e.g., trip, gifting, professional wear)
        2. Style preferences based on their history
        3. One useful selling cue for the associate
        
        Keep it friendly but professional. No AI mentions. Max 100 words.
        """
        
        # Call Sales Agent
        try:
            response = requests.post(
                f"{SALES_AGENT_URL}/chat",
                json={
                    "message": context_prompt,
                    "session_token": f"context_gen_{customer_id}",
                    "metadata": {
                        "type": "context_generation",
                        "sku": sku,
                        "store": store_location
                    }
                },
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                # Extract summary from agent response
                agent_message = data.get("response", "")
                
                # Clean up response
                summary = agent_message.strip()
                if len(summary) > 500:
                    summary = summary[:500] + "..."
                
                logger.info(f"✓ Context generated for {customer_id}")
                return summary
            else:
                logger.warning(f"Sales Agent returned {response.status_code}")
                return None
        
        except requests.exceptions.RequestException as e:
            logger.warning(f"Could not reach Sales Agent: {e}")
            # Generate a fallback summary
            return generate_fallback_summary(profile, sku)
    
    except Exception as e:
        logger.error(f"Error generating context: {e}")
        return None


def generate_fallback_summary(profile: Dict, sku: str) -> Optional[str]:
    """
    Generate a fallback summary if Sales Agent is unavailable.
    
    Args:
        profile: Customer profile dict
        sku: Product SKU
        
    Returns:
        Simple context summary
    """
    try:
        order_history = f"This repeat customer has made {profile.get('order_count', 0)} previous purchases."
        
        if profile.get('order_count', 0) == 0:
            order_history = "New customer - first purchase likely."
        
        return f"Customer {profile.get('name', 'Profile')} is reserving item {sku}. {order_history} Please provide personalized assistance based on fit and style preferences."
    
    except Exception as e:
        logger.error(f"Error generating fallback summary: {e}")
        return None


def update_reservation_context(reservation_id: str, context_summary: str) -> bool:
    """
    Update a reservation with the generated context summary.
    
    Args:
        reservation_id: Reservation ID
        context_summary: AI-generated context summary
        
    Returns:
        True if successful, False otherwise
    """
    try:
        if not supabase_client.FEATURE_SUPABASE_WRITE:
            logger.warning("Supabase write not enabled")
            return False
        
        result = supabase_client.update(
            "reservations",
            {"customer_context_summary": context_summary},
            params=f"reservation_id=eq.{reservation_id}"
        )
        
        if result:
            logger.info(f"✓ Updated context for {reservation_id}")
            return True
        else:
            logger.warning(f"Update returned no results for {reservation_id}")
            return False
    
    except Exception as e:
        logger.error(f"Error updating context: {e}")
        return False


# ==========================================
# ASYNC TASK FOR CONTEXT GENERATION
# ==========================================

async def generate_context_async(
    reservation_id: str,
    customer_id: str,
    sku: str,
    quantity: int,
    store_location: str
) -> bool:
    """
    Async task to generate and store context summary.
    
    This should be called as a background task after reservation creation.
    
    Args:
        reservation_id: Reservation ID
        customer_id: Customer ID
        sku: Product SKU
        quantity: Quantity
        store_location: Store location
        
    Returns:
        True if successful
    """
    try:
        logger.info(f"Generating context for reservation {reservation_id}...")
        
        # Generate context
        summary = generate_context_summary(customer_id, sku, quantity, store_location)
        
        if summary:
            # Update reservation
            success = update_reservation_context(reservation_id, summary)
            return success
        else:
            logger.warning(f"No context generated for {reservation_id}")
            return False
    
    except Exception as e:
        logger.error(f"Error in async context generation: {e}")
        return False

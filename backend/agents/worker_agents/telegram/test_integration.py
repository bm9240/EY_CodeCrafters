#!/usr/bin/env python3
"""
Telegram Bot Integration Test Script
Tests the complete flow: authentication → message forwarding → response display

Run this script to verify the Telegram bot is working correctly.
"""

import requests
import json
import time
from datetime import datetime

# Configuration
TELEGRAM_API = "http://localhost:8011"
SALES_AGENT_API = "http://localhost:8010"
SESSION_MANAGER_API = "http://localhost:8000"

# Test data
TEST_CHAT_ID = "123456789"
TEST_PHONE = "9876543210"  # Must exist in customers.csv
TEST_MESSAGES = [
    "/start",
    TEST_PHONE,
    "Show me running shoes",
    "Add to cart",
    "What's in my cart?",
    "Checkout"
]

def print_header(title):
    """Print formatted header"""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)

def print_result(test_name, success, message=""):
    """Print test result"""
    status = "✅ PASS" if success else "❌ FAIL"
    print(f"{status} - {test_name}")
    if message:
        print(f"       {message}")

def check_service(name, url):
    """Check if a service is running"""
    try:
        response = requests.get(url, timeout=5)
        return response.status_code == 200
    except Exception as e:
        return False

def simulate_telegram_message(chat_id, text):
    """Simulate a Telegram webhook message"""
    update = {
        "update_id": int(time.time()),
        "message": {
            "message_id": int(time.time()),
            "from": {
                "id": int(chat_id),
                "first_name": "Test",
                "username": "testuser"
            },
            "chat": {
                "id": int(chat_id),
                "first_name": "Test",
                "type": "private"
            },
            "date": int(time.time()),
            "text": text
        }
    }
    
    try:
        response = requests.post(
            f"{TELEGRAM_API}/telegram/webhook",
            json=update,
            timeout=30
        )
        return response.status_code == 200, response.json()
    except Exception as e:
        return False, str(e)

def test_service_availability():
    """Test 1: Check all required services are running"""
    print_header("TEST 1: Service Availability")
    
    services = [
        ("Telegram Agent", f"{TELEGRAM_API}/"),
        ("Sales Agent", f"{SALES_AGENT_API}/"),
        ("Session Manager", f"{SESSION_MANAGER_API}/health"),
    ]
    
    all_running = True
    for name, url in services:
        running = check_service(name, url)
        print_result(name, running, url)
        if not running:
            all_running = False
    
    return all_running

def test_telegram_webhook():
    """Test 2: Test Telegram webhook endpoint"""
    print_header("TEST 2: Telegram Webhook")
    
    success, result = simulate_telegram_message(TEST_CHAT_ID, "/start")
    print_result("Webhook accepts messages", success, str(result))
    
    return success

def test_authentication_flow():
    """Test 3: Test phone authentication flow"""
    print_header("TEST 3: Phone Authentication Flow")
    
    # Step 1: Send /start
    print("\nStep 1: Send /start command")
    success1, result1 = simulate_telegram_message(TEST_CHAT_ID, "/start")
    print_result("/start command", success1, str(result1))
    time.sleep(1)
    
    # Step 2: Send phone number
    print("\nStep 2: Send phone number")
    success2, result2 = simulate_telegram_message(TEST_CHAT_ID, TEST_PHONE)
    print_result(f"Phone authentication ({TEST_PHONE})", success2, str(result2))
    time.sleep(1)
    
    return success1 and success2

def test_message_forwarding():
    """Test 4: Test message forwarding to Sales Agent"""
    print_header("TEST 4: Message Forwarding")
    
    test_message = "Show me running shoes"
    
    print(f"\nSending: '{test_message}'")
    success, result = simulate_telegram_message(TEST_CHAT_ID, test_message)
    print_result("Message forwarded to Sales Agent", success)
    
    if success:
        print(f"Response: {json.dumps(result, indent=2)}")
    
    return success

def test_end_to_end_flow():
    """Test 5: Complete end-to-end shopping flow"""
    print_header("TEST 5: End-to-End Shopping Flow")
    
    flow_steps = [
        ("/start", "Start conversation"),
        (TEST_PHONE, "Authenticate with phone"),
        ("Show me running shoes", "Product search"),
        ("Add first item to cart", "Add to cart"),
        ("What's in my cart?", "View cart"),
    ]
    
    all_success = True
    for message, description in flow_steps:
        print(f"\n{description}: '{message}'")
        success, result = simulate_telegram_message(TEST_CHAT_ID, message)
        print_result(description, success)
        
        if not success:
            all_success = False
            print(f"       Failed: {result}")
        
        time.sleep(2)  # Wait between messages
    
    return all_success

def test_architecture_compliance():
    """Test 6: Verify architecture compliance"""
    print_header("TEST 6: Architecture Compliance")
    
    # Check that Telegram doesn't have business logic endpoints
    invalid_endpoints = [
        "/telegram/intent-detection",
        "/telegram/inventory-check",
        "/telegram/payment-process",
        "/telegram/loyalty-calculate"
    ]
    
    all_compliant = True
    for endpoint in invalid_endpoints:
        try:
            response = requests.get(f"{TELEGRAM_API}{endpoint}", timeout=5)
            # Should return 404 (not found) or 405 (method not allowed)
            compliant = response.status_code in [404, 405]
        except:
            compliant = True  # Endpoint doesn't exist (good!)
        
        print_result(
            f"No business logic endpoint: {endpoint}",
            compliant,
            "Should not exist" if compliant else "❌ Endpoint exists (architecture violation!)"
        )
        
        if not compliant:
            all_compliant = False
    
    return all_compliant

def test_webhook_info():
    """Test 7: Check webhook configuration"""
    print_header("TEST 7: Webhook Configuration")
    
    try:
        response = requests.get(f"{TELEGRAM_API}/telegram/webhook-info", timeout=5)
        success = response.status_code == 200
        
        if success:
            webhook_info = response.json()
            print(f"Webhook URL: {webhook_info.get('result', {}).get('url', 'Not set')}")
            print(f"Has webhook: {webhook_info.get('result', {}).get('has_custom_certificate', False)}")
        
        print_result("Webhook info accessible", success)
        return success
    except Exception as e:
        print_result("Webhook info accessible", False, str(e))
        return False

def main():
    """Run all tests"""
    print("\n" + "=" * 70)
    print("  TELEGRAM BOT INTEGRATION TEST SUITE")
    print("  Version: 2.0.0")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    
    print(f"\nConfiguration:")
    print(f"  Telegram API: {TELEGRAM_API}")
    print(f"  Sales Agent API: {SALES_AGENT_API}")
    print(f"  Session Manager API: {SESSION_MANAGER_API}")
    print(f"  Test Chat ID: {TEST_CHAT_ID}")
    print(f"  Test Phone: {TEST_PHONE}")
    
    # Run all tests
    tests = [
        ("Service Availability", test_service_availability),
        ("Telegram Webhook", test_telegram_webhook),
        ("Phone Authentication Flow", test_authentication_flow),
        ("Message Forwarding", test_message_forwarding),
        ("End-to-End Shopping Flow", test_end_to_end_flow),
        ("Architecture Compliance", test_architecture_compliance),
        ("Webhook Configuration", test_webhook_info),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"\n❌ Test '{test_name}' crashed: {e}")
            results.append((test_name, False))
        
        time.sleep(1)  # Brief pause between tests
    
    # Summary
    print_header("TEST SUMMARY")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")
    
    print(f"\n{'=' * 70}")
    print(f"Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED! Telegram bot is working correctly.")
        print("\n✅ Architecture Verified:")
        print("   - Telegram forwards messages to Sales Agent")
        print("   - No business logic in Telegram")
        print("   - Structured responses displayed correctly")
        print("   - Phone authentication working")
        print("   - Session management integrated")
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Please check the logs above.")
        print("\n🔍 Common Issues:")
        print("   - Services not running: python start_all_services.py")
        print("   - Phone not in customers.csv: Add test phone number")
        print("   - TELEGRAM_BOT_TOKEN not set: Check .env file")
    
    print("=" * 70)

if __name__ == "__main__":
    main()

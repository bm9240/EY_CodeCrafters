#!/usr/bin/env python3
"""
Start all backend services in parallel
Run: python start_all_services.py
"""

import subprocess
import sys
import os
from pathlib import Path

# Get the backend directory
BACKEND_DIR = Path(__file__).parent

# Service configurations: (name, file_path, port)
# Port assignments MUST match the ports hardcoded in each service's app.py or uvicorn.run()
SERVICES = [
    ("Session Manager", "session_manager.py", 8000),  # Must run first for Chat to work
    ("Inventory", "agents/worker_agents/inventory", 8001),
    ("Loyalty", "agents/worker_agents/loyalty", 8002),
    ("Payment", "agents/worker_agents/payment", 8003),
    ("Fulfillment", "agents/worker_agents/fulfillment", 8004),
    ("Post-Purchase", "agents/worker_agents/post_purchase", 8005),
    ("Stylist", "agents/worker_agents/stylist", 8006),
    ("Data API", "data_api.py", 8007),
    ("Virtual Circles", "agents/worker_agents/virtual_circles", 8009),
    ("Sales Agent", "agents/sales_agent", 8010),
    ("Telegram", "agents/worker_agents/telegram", 8011),
    ("Reservation", "agents/worker_agents/reservation", 8012),
    ("Ambient Commerce", "agents/worker_agents/ambient_commerce", 8017),
]

def start_service(name, path, port, env_vars=None):
    """Start a single service"""
    # Handle both direct .py files and directories
    if path.endswith('.py'):
        app_file = BACKEND_DIR / path
        service_path = app_file.parent
        script_name = app_file.name
    else:
        service_path = BACKEND_DIR / path
        app_file = service_path / "app.py"
        script_name = "app.py"
    
    if not app_file.exists():
        print(f"❌ {name}: {script_name} not found at {app_file}")
        return None
    
    print(f"🚀 Starting {name} on port {port}...")
    
    # Set up environment variables
    env = os.environ.copy()
    if env_vars:
        env.update(env_vars)
    
    try:
        process = subprocess.Popen(
            [sys.executable, script_name],
            cwd=service_path,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        return process
    except Exception as e:
        print(f"❌ Failed to start {name}: {e}")
        return None

def main():
    print("=" * 60)
    print("🎯 Starting All Backend Services")
    print("=" * 60)
    
    processes = []
    
    # Start all services
    for name, directory, port in SERVICES:
        # Set USE_REAL_AGENTS=true for Sales Agent
        env_vars = {"USE_REAL_AGENTS": "true"} if name == "Sales Agent" else None
        process = start_service(name, directory, port, env_vars)
        if process:
            processes.append((name, process, port))
    
    print("\n" + "=" * 60)
    print(f"✅ Started {len(processes)} services")
    print("=" * 60)
    
    for name, _, port in processes:
        print(f"✓ {name:20s} → http://localhost:{port}")
    
    print("\n" + "=" * 60)
    print("📝 Press Ctrl+C to stop all services")
    print("=" * 60)
    
    try:
        # Keep script running
        for name, process, port in processes:
            process.wait()
    except KeyboardInterrupt:
        print("\n\n🛑 Stopping all services...")
        for name, process, port in processes:
            process.terminate()
            print(f"✓ Stopped {name}")
        print("\n✅ All services stopped")

if __name__ == "__main__":
    main()

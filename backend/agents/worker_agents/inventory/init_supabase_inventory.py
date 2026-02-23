#!/usr/bin/env python3
"""
Initialize Supabase inventory table with data from CSV files.
Run this once to populate the inventory table in Supabase.
"""

import sys
import csv
from pathlib import Path

# Add paths
backend_path = Path(__file__).resolve().parent.parent.parent
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

from db import supabase_client

def seed_supabase_inventory():
    """Seed store inventory from CSV to Supabase."""
    
    base = Path(__file__).parent.parent.parent.parent / "data"
    csv_full = base / "inventory_full.csv"
    csv_default = base / "inventory.csv"
    
    csv_path = csv_full if csv_full.exists() else csv_default
    
    if not csv_path.exists():
        print(f"❌ CSV file not found: {csv_path}")
        return False
    
    print(f"\n{'='*70}")
    print(f"🌱 SUPABASE INVENTORY SEEDING")
    print(f"{'='*70}\n")
    print(f"📊 Reading from: {csv_path.name}")
    
    rows_to_insert = []
    count = 0
    stores_seen = set()
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        
        for row in reader:
            sku = row.get('sku')
            store_id = row.get('store_id')
            quantity_raw = (
                row.get('qty')
                or row.get('quantity')
                or row.get('Quantity')
                or row.get('QTY')
            )
            
            if not sku or not store_id:
                continue
            
            try:
                qty = int(quantity_raw)
            except (TypeError, ValueError):
                qty = 0
            
            # Normalize
            sku_upper = sku.strip().upper()
            store_upper = store_id.strip().upper()
            
            rows_to_insert.append({
                "sku": sku_upper,
                "store_id": store_upper,
                "quantity": qty
            })
            
            count += 1
            stores_seen.add(store_upper)
            
            if count % 500 == 0:
                print(f"  ✓ Loaded {count} entries...")
    
    print(f"\n📤 Inserting {count} inventory entries into Supabase...")
    
    try:
        # Clear existing data first (optional - comment out if you want to keep existing data)
        # print(f"   Clearing existing inventory...")
        # supabase_client.delete('inventory')
        
        # Insert new data
        result = supabase_client.insert("inventory", rows_to_insert)
        
        print(f"✅ Successfully inserted {count} entries")
        print(f"   Stores: {', '.join(sorted(stores_seen))}")
        
        # Verify
        print(f"\n🔍 Verifying sample data...")
        for store in sorted(stores_seen):
            inv = supabase_client.select(
                'inventory',
                params=f"store_id=eq.{store}&limit=1",
                columns="sku,store_id,quantity"
            )
            if inv:
                print(f"   ✓ {store}: Found {len(inv)} entries")
        
        print(f"\n{'='*70}")
        print(f"✅ SEEDING COMPLETE!")
        print(f"{'='*70}\n")
        return True
        
    except Exception as e:
        print(f"❌ Error inserting to Supabase: {e}")
        print(f"   Make sure SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set")
        return False


if __name__ == "__main__":
    success = seed_supabase_inventory()
    sys.exit(0 if success else 1)

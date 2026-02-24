import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useCart } from '@/contexts/CartContext.jsx';
import { ShoppingCart, Trash2, Plus, Minus, ArrowLeft } from 'lucide-react';
import storeService from '@/services/storeService';
import sessionStore from '@/lib/session';
import Navbar from '@/components/Navbar.jsx';

const CartPage = () => {
  const navigate = useNavigate();
  const {
    cartItems,
    removeFromCart,
    updateQuantity,
    getCartTotal,
    getCartCount,
    updateItemMetadata,
  } = useCart();

  const [reserveLoading, setReserveLoading] = useState({});
  const [reserveFeedback, setReserveFeedback] = useState({});
  const [stores, setStores] = useState([]);
  const [storesLoading, setStoresLoading] = useState(true);
  const [selectedStore, setSelectedStore] = useState({});
  // Address management (stored in localStorage under 'ey_addresses')
  const [addresses, setAddresses] = useState(() => {
    try {
      const raw = localStorage.getItem('ey_addresses');
      return raw ? JSON.parse(raw) : [];
    } catch (e) {
      return [];
    }
  });
  const [addressModalOpen, setAddressModalOpen] = useState(false);
  const [editingAddress, setEditingAddress] = useState(null);
  const [selectAddressModalOpen, setSelectAddressModalOpen] = useState(false);
  const [selectedAddressId, setSelectedAddressId] = useState(null);

  // Load stores from Supabase on component mount
  useEffect(() => {
    const loadStores = async () => {
      try {
        setStoresLoading(true);
        console.log('📦 Loading stores from Sales Agent...');
        const storesData = await storeService.getStores();
        console.log('✅ Raw stores response:', storesData);
        
        // Handle both array and wrapped response
        let storesArray = [];
        if (Array.isArray(storesData)) {
          storesArray = storesData;
        } else if (storesData?.stores && Array.isArray(storesData.stores)) {
          storesArray = storesData.stores;
        } else {
          console.warn('⚠️ Unexpected stores format:', storesData);
          storesArray = [];
        }
        
        // Log each store to see what fields are present
        console.log(`📍 Total stores: ${storesArray.length}`);
        storesArray.forEach((store, idx) => {
          console.log(`  Store ${idx}:`, {
            id: store.id,
            name: store.name,
            location: store.location,
            city: store.city,
            state: store.state,
            raw: store
          });
        });
        
        setStores(storesArray);
      } catch (error) {
        console.error('❌ Failed to load stores from Supabase:', error);
        setStores([]);
      } finally {
        setStoresLoading(false);
      }
    };
    
    loadStores();
  }, []);

  const saveAddresses = (next) => {
    setAddresses(next);
    try {
      localStorage.setItem('ey_addresses', JSON.stringify(next));
    } catch (e) {
      console.error('Failed to save addresses', e);
    }
  };

  const formatINR = (amount) => {
    if (amount === undefined || amount === null) return '₹0';
    return parseFloat(amount).toLocaleString('en-IN', {
      style: 'currency',
      currency: 'INR',
      minimumFractionDigits: 0,
    });
  };

  const resetReservationMetadata = (id) => {
    updateItemMetadata(id, {
      reservationStatus: 'idle',
      reservationHoldId: null,
      reservationExpiresAt: null,
      reservationLocation: null,
      reservedQuantity: 0,
    });
  };

  const handleReleaseReservation = async (item) => {
    if (!item?.reservationHoldId) {
      resetReservationMetadata(item.id);
      return;
    }

    try {
      // Call Sales Agent to release the hold
      await storeService.releaseReservation(item.reservationHoldId);
      setReserveFeedback((prev) => ({
        ...prev,
        [item.id]: 'Reservation released',
      }));
    } catch (error) {
      console.error('Failed to release reservation:', error);
      setReserveFeedback((prev) => ({
        ...prev,
        [item.id]: 'Could not release reservation. Please try again.',
      }));
    } finally {
      resetReservationMetadata(item.id);
    }
  };

  const handleReserveInStore = async (item) => {
    if (!item || item.qty <= 0) return;

    setReserveLoading((prev) => ({ ...prev, [item.id]: true }));
    
    try {
      // Get selected store or first available store
      let selectedStoreLocation = selectedStore[item.sku];
      
      if (!selectedStoreLocation) {
        if (stores.length === 0) {
          throw new Error('No stores available for reservation');
        }
        selectedStoreLocation = stores[0].location;
        setSelectedStore((prev) => ({ ...prev, [item.sku]: selectedStoreLocation }));
      }

      // Get customer ID
      const customerId = sessionStore.getCustomerId();
      if (!customerId) {
        throw new Error('Customer not authenticated');
      }

      // Call Sales Agent reservation endpoint (complete orchestration)
      const reservation = await storeService.reserveInStore({
        customer_id: String(customerId),  // Convert to string
        sku: item.sku,
        quantity: item.qty,
        store_location: selectedStoreLocation
      });

      // Check if reservation was successful
      if (reservation.status !== 'reserved') {
        const errorMsg = reservation.message || `Reservation failed: ${reservation.status}`;
        setReserveFeedback((prev) => ({
          ...prev,
          [item.id]: errorMsg,
        }));
        setReserveLoading((prev) => ({ ...prev, [item.id]: false }));
        return;
      }

      // Update item with reservation metadata
      updateItemMetadata(item.id, {
        reservationStatus: 'reserved',
        reservationHoldId: reservation.hold_id,
        reservationExpiresAt: reservation.expires_at,
        reservationLocation: `store:${selectedStoreLocation}`,
        reservedQuantity: item.qty,
      });

      // Store reservation in localStorage for "My Reservations" view
      const storedReservations = JSON.parse(localStorage.getItem('ey_reservations') || '[]');
      storedReservations.push({
        reservation_id: reservation.reservation_id,
        customer_id: customerId,
        sku: item.sku,
        quantity: item.qty,
        store_location: selectedStoreLocation,
        hold_id: reservation.hold_id,
        status: 'confirmed',
        created_at: new Date().toISOString(),
        expires_at: reservation.expires_at,
        product_name: item.name,
        product_image: item.image,
        store_name: reservation.store_name,
      });
      localStorage.setItem('ey_reservations', JSON.stringify(storedReservations));

      // Remove item from active cart after successful reservation
      removeFromCart(item.id);

      setReserveFeedback((prev) => ({
        ...prev,
        [item.id]: `✓ Reserved at ${reservation.store_name}! Confirmation: ${reservation.reservation_id}`,
      }));

      // Brief delay before clearing feedback
      setTimeout(() => {
        setReserveFeedback((prev) => {
          const next = { ...prev };
          delete next[item.id];
          return next;
        });
      }, 3000);
    } catch (error) {
      console.error('Reservation failed:', error);

      const errorMessage = error?.message || 'Unable to reserve product. Please try again.';
      setReserveFeedback((prev) => ({
        ...prev,
        [item.id]: errorMessage,
      }));

      resetReservationMetadata(item.id);
    } finally {
      setReserveLoading((prev) => ({ ...prev, [item.id]: false }));
    }
  };

  const handleQuantityChange = async (item, newQty) => {
    if (newQty <= 0) {
      await handleRemove(item);
      return;
    }

    if (item.reservationHoldId) {
      await handleReleaseReservation(item);
    }

    updateQuantity(item.id, newQty);
  };

  useEffect(() => {
    let mounted = true;
    const fetchOptions = async () => {
      const skus = cartItems.map((c) => c.sku);
      const nextOptions = {};
      const nextSelected = {};

      await Promise.all(
        skus.map(async (sku) => {
          try {
            const inv = await inventoryService.getInventory(sku);
            const stores = inv.store_stock ? Object.keys(inv.store_stock) : [];
            nextOptions[sku] = stores;
            if (stores.includes('STORE_MUMBAI')) nextSelected[sku] = 'STORE_MUMBAI';
            else if (stores.length > 0) nextSelected[sku] = stores[0];
            else nextSelected[sku] = inv.online_stock > 0 ? 'online' : 'STORE_MUMBAI';
          } catch (e) {
            // ignore per-sku failures
          }
        })
      );

      if (!mounted) return;
      // Use nextSelected to initialize store selection but don't need storeOptions
      setSelectedStore((prev) => ({ ...prev, ...nextSelected }));
    };

    if (cartItems.length > 0) fetchOptions();
    return () => {
      mounted = false;
    };
  }, [cartItems]);

  const handleRemove = async (item) => {
    if (item.reservationHoldId) {
      await handleReleaseReservation(item);
    }
    removeFromCart(item.id);
  };

  const handleCheckout = () => {
    if (cartItems.length === 0) return;

    if (addresses.length === 0) {
      // No address -> prompt add
      setEditingAddress(null);
      setAddressModalOpen(true);
      return;
    }

    if (addresses.length === 1) {
      // set selected and navigate
      const addr = addresses[0];
      localStorage.setItem('ey_selected_address', JSON.stringify(addr));
      navigate('/checkout');
      return;
    }

    // multiple addresses -> ask user to select
    setSelectAddressModalOpen(true);
  };

  const isReservationFresh = (item) => {
    return (
      item.reservationStatus === 'reserved' &&
      item.reservationHoldId &&
      item.reservedQuantity === item.qty
    );
  };

  // Address helpers
  const handleAddOrUpdateAddress = (addr) => {
    if (!addr || !addr.id) {
      addr.id = `addr-${Date.now().toString(36)}`;
    }
    const next = [...addresses];
    const idx = next.findIndex((a) => a.id === addr.id);
    if (idx >= 0) next[idx] = addr;
    else next.push(addr);
    saveAddresses(next);
    setAddressModalOpen(false);
    setEditingAddress(null);
  };

  const handleEditAddress = (addr) => {
    setEditingAddress(addr);
    setAddressModalOpen(true);
  };

  const handleDeleteAddress = (id) => {
    const next = addresses.filter((a) => a.id !== id);
    saveAddresses(next);
  };

  const confirmSelectAddress = () => {
    const addr = addresses.find((a) => a.id === selectedAddressId);
    if (addr) {
      localStorage.setItem('ey_selected_address', JSON.stringify(addr));
      setSelectAddressModalOpen(false);
      navigate('/checkout');
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-orange-50 via-yellow-50 to-red-50">
      <Navbar />

      {/* Header */}
      <div className="pt-32 pb-8">
        <div className="max-w-4xl mx-auto px-4 py-6 bg-gradient-to-r from-red-600 to-orange-600 text-white shadow-md rounded-lg">
          <div className="flex items-center gap-4">
            <button
              onClick={() => navigate(-1)}
              className="hover:bg-white/10 p-2 rounded-full transition-colors"
            >
              <ArrowLeft className="w-6 h-6" />
            </button>
            <div className="flex items-center gap-3">
              <ShoppingCart className="w-8 h-8" />
              <div>
                <h1 className="text-2xl font-bold">Your Cart</h1>
                <p className="text-sm text-orange-100">
                  {getCartCount()} {getCartCount() === 1 ? 'item' : 'items'}
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Cart Content */}
      <div className="max-w-4xl mx-auto px-4 pb-16">
        {cartItems.length === 0 ? (
          <div className="text-center py-16 bg-white rounded-lg shadow-md">
            <ShoppingCart className="w-24 h-24 mx-auto text-gray-300 mb-4" />
            <h2 className="text-2xl font-semibold text-gray-700 mb-2">Your cart is empty</h2>
            <p className="text-gray-500 mb-6">Add some products to get started!</p>
            <button
              onClick={() => navigate('/products')}
              className="bg-gradient-to-r from-red-600 to-orange-600 text-white px-6 py-3 rounded-lg font-semibold hover:from-red-700 hover:to-orange-700 transition-all"
            >
              Continue Shopping
            </button>
          </div>
        ) : (
          <div className="space-y-6">
            {/* Cart Items */}
            <div className="bg-white rounded-xl shadow-md overflow-hidden">
              {cartItems.map((item, idx) => (
                <div
                  key={item.id}
                  className={`p-6 flex gap-4 ${idx !== 0 ? 'border-t border-gray-200' : ''}`}
                >
                  {/* Product Image */}
                  {item.image && (
                    <img
                      src={item.image}
                      alt={item.name}
                      className="w-24 h-24 object-cover rounded-lg"
                      onError={(e) => (e.target.style.display = 'none')}
                    />
                  )}

                  {/* Product Details */}
                  <div className="flex-1">
                    <h3 className="font-semibold text-lg text-gray-900">{item.name}</h3>
                    <p className="text-sm text-gray-500 mt-1">SKU: {item.sku}</p>
                    <p className="textont-bold text-green-600 mt-2">{formatINR(item.unit_price)}</p>

                    {/* Quantity Controls */}
                    <div className="flex items-center gap-4 mt-4">
                      <div className="flex items-center gap-2 border border-gray-300 rounded-lg">
                        <button
                          onClick={() => handleQuantityChange(item, item.qty - 1)}
                          className="p-2 hover:bg-gray-100 transition-colors rounded-l-lg"
                        >
                          <Minus className="w-4 h-4" />
                        </button>
                        <span className="px-4 font-semibold">{item.qty}</span>
                        <button
                          onClick={() => handleQuantityChange(item, item.qty + 1)}
                          className="p-2 hover:bg-gray-100 transition-colors rounded-r-lg"
                        >
                          <Plus className="w-4 h-4" />
                        </button>
                      </div>

                      <button
                        onClick={() => handleRemove(item)}
                        className="text-red-600 hover:text-red-700 p-2 rounded-lg hover:bg-red-50 transition-colors"
                      >
                        <Trash2 className="w-5 h-5" />
                      </button>
                    </div>

                    <div className="mt-4 space-y-3">
                      {/* Store selector for reservation */}
                      <div className="flex items-center gap-3">
                        <label className="text-sm font-semibold text-gray-800 min-w-fit">Store:</label>
                        <select
                          value={selectedStore[item.sku] || ''}
                          onChange={(e) => {
                            console.log('📍 Store selected:', e.target.value, 'for SKU:', item.sku);
                            setSelectedStore((prev) => ({ ...prev, [item.sku]: e.target.value }));
                          }}
                          disabled={storesLoading || stores.length === 0}
                          className="flex-1 border-2 border-gray-300 px-3 py-2 rounded-lg text-sm bg-white hover:border-orange-400 focus:border-orange-500 focus:outline-none disabled:bg-gray-100 disabled:cursor-not-allowed font-medium"
                        >
                          <option value="">
                            {storesLoading ? 'Loading stores...' : stores.length === 0 ? 'No stores available' : 'Select a store'}
                          </option>
                          {stores.map((store) => {
                            console.log('🏬 Rendering store option:', store);
                            const displayName = store.name || store.location || store.id || 'Unknown Store';
                            const storeValue = store.location || store.id;
                            return (
                              <option key={storeValue} value={storeValue}>
                                {displayName}
                              </option>
                            );
                          })}
                        </select>
                      </div>

                      <button
                        onClick={() => handleReserveInStore(item)}
                        disabled={reserveLoading[item.id] || isReservationFresh(item)}
                        className={`w-full px-4 py-2 rounded-lg font-semibold transition-colors ${
                          isReservationFresh(item) ? 'bg-gradient-to-r from-red-100 to-orange-100 text-red-700 cursor-default font-bold border-2 border-red-400' : 'bg-gradient-to-r from-red-600 to-orange-600 text-white hover:from-red-700 hover:to-orange-700'
                        } ${reserveLoading[item.id] ? 'opacity-70 cursor-wait' : ''}`}
                      >
                        {isReservationFresh(item) ? '✓ Reserved in Store' : reserveLoading[item.id] ? 'Reserving...' : 'Reserve in Store'}
                      </button>

                      {reserveFeedback[item.id] && <p className="text-sm text-gray-600">{reserveFeedback[item.id]}</p>}

                      {item.reservationStatus === 'reserved' && !isReservationFresh(item) && (
                        <p className="text-sm text-orange-600">
                          Reservation covers {item.reservedQuantity} item(s). Update reservation to match current quantity.
                        </p>
                      )}
                    </div>
                  </div>

                  {/* Item Total */}
                  <div className="text-right">
                    <p className="text-sm text-gray-500">Total</p>
                    <p className="text-xl font-bold text-gray-900">{formatINR(item.unit_price * item.qty)}</p>
                  </div>
                </div>
              ))}
            </div>

            {/* Cart Summary */}
            <div className="bg-white rounded-xl shadow-md p-6">
              <h2 className="text-xl font-bold text-gray-900 mb-4">Order Summary</h2>

              <div className="space-y-3 mb-6">
                {/* Address block */}
                <div className="mb-4">
                  <h3 className="font-semibold">Shipping Address</h3>
                  {addresses.length === 0 ? (
                    <div className="mt-2">
                      <p className="text-sm text-gray-500">No address added yet.</p>
                      <button
                        onClick={() => { setEditingAddress(null); setAddressModalOpen(true); }}
                        className="mt-2 inline-block bg-blue-600 text-white px-3 py-2 rounded-md text-sm"
                      >
                        Add Address
                      </button>
                    </div>
                  ) : (
                    <div className="mt-2 bg-gray-50 p-3 rounded-md">
                      <div className="flex justify-between items-start">
                        <div>
                          <p className="text-sm font-medium">{addresses[0].name}</p>
                          <p className="text-sm text-gray-600">{addresses[0].line1}{addresses[0].line2 ? ', ' + addresses[0].line2 : ''}</p>
                          <p className="text-sm text-gray-600">{addresses[0].city} {addresses[0].state} - {addresses[0].pincode}</p>
                          <p className="text-sm text-gray-600">{addresses[0].phone}</p>
                        </div>
                        <div className="flex flex-col gap-2">
                          <button
                            onClick={() => handleEditAddress(addresses[0])}
                            className="text-sm text-blue-600"
                          >
                            Edit
                          </button>
                          <button
                            onClick={() => { setEditingAddress(null); setAddressModalOpen(true); }}
                            className="text-sm text-green-600"
                          >
                            Add New
                          </button>
                        </div>
                      </div>
                    </div>
                  )}
                </div>

                <div className="flex justify-between text-gray-700">
                  <span>Subtotal ({getCartCount()} items)</span>
                  <span className="font-semibold">{formatINR(getCartTotal())}</span>
                </div>
                <div className="border-t border-gray-200 pt-3">
                  <div className="flex justify-between text-lg font-bold text-gray-900">
                    <span>Total</span>
                    <span className="text-green-600">{formatINR(getCartTotal())}</span>
                  </div>
                </div>
              </div>

              <button
                onClick={handleCheckout}
                className="w-full bg-gradient-to-r from-red-600 to-orange-600 text-white py-4 rounded-lg font-bold text-lg hover:from-red-700 hover:to-orange-700 transition-all shadow-lg hover:shadow-xl"
              >
                Proceed to Checkout
              </button>
            </div>
          </div>
        )}
      </div>

      {addressModalOpen && (
        <AddressModal
          open={addressModalOpen}
          initial={editingAddress}
          onClose={() => { setAddressModalOpen(false); setEditingAddress(null); }}
          onSave={(form) => handleAddOrUpdateAddress(form)}
        />
      )}

      {selectAddressModalOpen && (
        <SelectAddressModal
          open={selectAddressModalOpen}
          addresses={addresses}
          selectedId={selectedAddressId}
          onSelect={(id) => setSelectedAddressId(id)}
          onProceed={confirmSelectAddress}
          onClose={() => setSelectAddressModalOpen(false)}
          onAddNew={() => { setEditingAddress(null); setAddressModalOpen(true); }}
          onEdit={(a) => handleEditAddress(a)}
          onDelete={(id) => handleDeleteAddress(id)}
        />
      )}
    </div>
  );
};

export default CartPage;

// Address modal component (rendered at bottom of this file via conditional)

// Render address modals via simple portal-less markup
/** Note: kept inline for simplicity; could be refactored into separate components */
const AddressModal = ({ open, onClose, onSave, initial }) => {
  if (!open) return null;
  const [form, setForm] = useState(() => ({
    id: initial?.id || null,
    name: initial?.name || '',
    line1: initial?.line1 || '',
    line2: initial?.line2 || '',
    city: initial?.city || '',
    state: initial?.state || '',
    pincode: initial?.pincode || '',
    phone: initial?.phone || '',
  }));

  const handleChange = (k, v) => setForm((s) => ({ ...s, [k]: v }));

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg p-6 w-full max-w-md">
        <h3 className="text-lg font-semibold mb-3">{form.id ? 'Edit Address' : 'Add Address'}</h3>
        <div className="space-y-2">
          <input className="w-full border px-3 py-2 rounded" placeholder="Full name" value={form.name} onChange={(e)=>handleChange('name', e.target.value)} />
          <input className="w-full border px-3 py-2 rounded" placeholder="Line 1" value={form.line1} onChange={(e)=>handleChange('line1', e.target.value)} />
          <input className="w-full border px-3 py-2 rounded" placeholder="Line 2" value={form.line2} onChange={(e)=>handleChange('line2', e.target.value)} />
          <div className="flex gap-2">
            <input className="flex-1 border px-3 py-2 rounded" placeholder="City" value={form.city} onChange={(e)=>handleChange('city', e.target.value)} />
            <input className="w-32 border px-3 py-2 rounded" placeholder="Pincode" value={form.pincode} onChange={(e)=>handleChange('pincode', e.target.value)} />
          </div>
          <input className="w-full border px-3 py-2 rounded" placeholder="State" value={form.state} onChange={(e)=>handleChange('state', e.target.value)} />
          <input className="w-full border px-3 py-2 rounded" placeholder="Phone" value={form.phone} onChange={(e)=>handleChange('phone', e.target.value)} />
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button className="px-4 py-2" onClick={onClose}>Cancel</button>
          <button
            className="px-4 py-2 bg-blue-600 text-white rounded"
            onClick={() => onSave(form)}
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
};

const SelectAddressModal = ({ open, addresses, selectedId, onSelect, onClose, onAddNew, onEdit, onDelete, onProceed }) => {
  if (!open) return null;
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg p-6 w-full max-w-lg">
        <h3 className="text-lg font-semibold mb-3">Select Shipping Address</h3>
        <div className="space-y-2 max-h-72 overflow-y-auto">
          {addresses.map((a) => (
            <div key={a.id} className="p-3 border rounded flex justify-between items-center">
              <label className="flex items-center gap-3">
                <input type="radio" name="seladdr" checked={selectedId===a.id} onChange={()=>onSelect(a.id)} />
                <div>
                  <div className="font-medium">{a.name}</div>
                  <div className="text-sm text-gray-600">{a.line1}{a.line2? ', '+a.line2:''}</div>
                  <div className="text-sm text-gray-600">{a.city} {a.state} - {a.pincode}</div>
                </div>
              </label>
              <div className="flex flex-col gap-1 text-sm">
                <button onClick={()=>onEdit(a)} className="text-blue-600">Edit</button>
                <button onClick={()=>onDelete(a.id)} className="text-red-600">Delete</button>
              </div>
            </div>
          ))}
        </div>
        <div className="mt-4 flex justify-between items-center">
          <button className="text-green-600" onClick={onAddNew}>Add New Address</button>
          <div className="flex gap-2">
            <button className="px-4 py-2" onClick={onClose}>Cancel</button>
            <button className="px-4 py-2 bg-blue-600 text-white rounded" onClick={onProceed}>Proceed</button>
          </div>
        </div>
      </div>
    </div>
  );
};

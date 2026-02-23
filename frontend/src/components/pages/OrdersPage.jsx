import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import dataService from '@/services/dataService';
import { getFulfillmentStatus } from '@/services/fulfillmentService';
import sessionStore from '@/lib/session';
import Navbar from '@/components/Navbar.jsx';
import { resolveImageUrl } from '@/lib/utils.js';
import { Package, Truck, CheckCircle, Clock, MessageCircle, Sparkles } from 'lucide-react';

const OrdersPage = () => {
  const navigate = useNavigate();
  const [orders, setOrders] = useState([]);
  const [loading, setLoading] = useState(false);
  const [fulfillmentData, setFulfillmentData] = useState({});
  const [errorMessage, setErrorMessage] = useState('');
  const customerId = sessionStore.getCustomerId();
  const customerPhone = sessionStore.getPhone();
  const POLL_MS = 30000;

  const fulfillmentStages = [
    { key: 'processing', label: 'Processing', short: 'Proc' },
    { key: 'packed', label: 'Packed', short: 'Pack' },
    { key: 'shipped', label: 'Shipped', short: 'Ship' },
    { key: 'out_for_delivery', label: 'Out for Delivery', short: 'Out' },
    { key: 'delivered', label: 'Delivered', short: 'Del' },
  ];

  const awaitLabel = 'Awaiting Fulfillment';

  const getStageIndex = (statusValue) => {
    const normalized = String(statusValue || '').toLowerCase().trim();
    // Map common status variations to stage keys
    const statusMap = {
      'processing': 'processing',
      'fulfillmentstatus.processing': 'processing',
      'packed': 'packed',
      'fulfillmentstatus.packed': 'packed',
      'shipped': 'shipped',
      'fulfillmentstatus.shipped': 'shipped',
      'out_for_delivery': 'out_for_delivery',
      'fulfillmentstatus.out_for_delivery': 'out_for_delivery',
      'outfordelivery': 'out_for_delivery',
      'out for delivery': 'out_for_delivery',
      'delivered': 'delivered',
      'fulfillmentstatus.delivered': 'delivered',
      'paid': 'processing',
      'created': 'processing',
    };
    const mapped = statusMap[normalized] || normalized;
    const index = fulfillmentStages.findIndex((stage) => stage.key === mapped);
    const resultIndex = index >= 0 ? index : -1;
    if (resultIndex === -1) {
      console.warn(`Unknown status: "${statusValue}" (normalized: "${normalized}")`);
    }
    return resultIndex >= 0 ? resultIndex : 0;
  };

  useEffect(() => {
    let mounted = true;
    const fetchOrders = async () => {
      setLoading(true);
      setErrorMessage('');
      try {
        const normalizeCustomerId = (value) => {
          if (value == null) return '';
          const text = String(value).trim();
          if (!text) return '';
          const numeric = Number(text);
          if (Number.isFinite(numeric)) {
            return String(Math.trunc(numeric));
          }
          return text;
        };

        let resolvedCustomerId = normalizeCustomerId(customerId);

        if (!resolvedCustomerId && customerPhone) {
          const phoneDigits = String(customerPhone).replace(/\D/g, '');
          if (phoneDigits) {
            const customersRes = await dataService.getCustomers(10000);
            const customers = customersRes.customers || [];
            const matched = customers.find((c) => {
              const customerPhoneRaw = String(c.phone_number || '').replace(/\D/g, '');
              return customerPhoneRaw && customerPhoneRaw === phoneDigits;
            });
            if (matched?.customer_id != null) {
              resolvedCustomerId = normalizeCustomerId(matched.customer_id);
            }
          }
        }

        if (!resolvedCustomerId) {
          setOrders([]);
          return;
        }

        let ordersList = [];
        let source = 'customer-filter';

        try {
          const res = await dataService.getOrders({ customer_id: resolvedCustomerId, limit: 10000 });
          if (!mounted) return;
          ordersList = res.orders || [];
        } catch (innerError) {
          console.error('Failed to fetch customer orders', innerError);
          setErrorMessage(innerError?.message || 'Failed to fetch orders.');
        }

        if (!ordersList || ordersList.length === 0) {
          const allRes = await dataService.getOrders({ limit: 10000 });
          const allOrders = allRes.orders || [];
          ordersList = allOrders.filter((order) => {
            const orderCustomerId = normalizeCustomerId(order.customer_id);
            return orderCustomerId && orderCustomerId === resolvedCustomerId;
          });
          source = 'client-filter';
        }

        if (Array.isArray(ordersList)) {
          ordersList = [...ordersList].sort((a, b) => {
            const aTime = a?.created_at ? new Date(a.created_at).getTime() : 0;
            const bTime = b?.created_at ? new Date(b.created_at).getTime() : 0;
            return bTime - aTime;
          });
        }

        setOrders(ordersList || []);
        // Fetch fulfillment data for each order
        const fulfillmentPromises = ordersList.map(async (order) => {
          try {
            const fulfillment = await getFulfillmentStatus(order.order_id || order.id);
            return { orderId: order.order_id || order.id, data: fulfillment };
          } catch (e) {
            console.error(`Failed to fetch fulfillment for ${order.order_id}:`, e);
            return { orderId: order.order_id || order.id, data: null };
          }
        });

        const fulfillmentResults = await Promise.all(fulfillmentPromises);
        const fulfillmentMap = {};
        fulfillmentResults.forEach(({ orderId, data }) => {
          if (data) fulfillmentMap[orderId] = data;
        });
        
        if (mounted) setFulfillmentData(fulfillmentMap);
      } catch (e) {
        console.error('Failed to fetch orders', e);
        setErrorMessage(e?.message || 'Failed to fetch orders.');
      } finally {
        if (mounted) setLoading(false);
      }
    };
    fetchOrders();
    const intervalId = setInterval(fetchOrders, POLL_MS);
    return () => { 
      mounted = false; 
      clearInterval(intervalId);
    };
  }, [customerId, customerPhone]);

  const current = orders.filter(o => !['delivered', 'cancelled', 'returned'].includes((o.status || '').toLowerCase()));
  const delivered = orders.filter(o => (o.status || '').toLowerCase() === 'delivered');
  const cancelled = orders.filter(o => ['cancelled', 'returned'].includes((o.status || '').toLowerCase()));

  const getFirstItem = (order) => {
    const items = order.items || [];
    return items[0] || null;
  };

  const getItemCount = (order) => {
    const items = order.items || [];
    return items.length;
  };

  const formatDate = (dateStr) => {
    if (!dateStr) return '';
    try {
      return new Date(dateStr).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
    } catch {
      return dateStr;
    }
  };

  const OrderCard = ({ order }) => {
    const firstItem = getFirstItem(order);
    const itemCount = getItemCount(order);
    const orderId = order.order_id || order.id;
    const fulfillment = fulfillmentData[orderId];
    let displayStatus = fulfillment?.current_status ? String(fulfillment.current_status).toLowerCase().trim() : null;
    
    // Handle enum format like "FulfillmentStatus.DELIVERED" or enum string values
    if (displayStatus && displayStatus.includes('.')) {
      displayStatus = displayStatus.split('.').pop().toLowerCase().trim();
    }
    
    const stageIndex = getStageIndex(displayStatus);
    
    const statusColors = {
      processing: 'bg-yellow-100 text-yellow-800 border-yellow-200',
      packed: 'bg-blue-100 text-blue-800 border-blue-200',
      shipped: 'bg-indigo-100 text-indigo-800 border-indigo-200',
      out_for_delivery: 'bg-purple-100 text-purple-800 border-purple-200',
      delivered: 'bg-green-100 text-green-800 border-green-200',
      cancelled: 'bg-red-100 text-red-800 border-red-200',
      returned: 'bg-gray-100 text-gray-800 border-gray-200',
      paid: 'bg-orange-100 text-orange-800 border-orange-200',
      created: 'bg-gray-100 text-gray-600 border-gray-200',
    };

    const statusIcons = {
      processing: <Clock className="w-4 h-4" />,
      packed: <Package className="w-4 h-4" />,
      shipped: <Truck className="w-4 h-4" />,
      out_for_delivery: <Truck className="w-4 h-4" />,
      delivered: <CheckCircle className="w-4 h-4" />,
      paid: <Clock className="w-4 h-4" />,
    };

    return (
      <div className="bg-white rounded-xl shadow-md border border-gray-200 hover:shadow-lg transition-all overflow-hidden group">
        <div className="p-5">
          <div className="flex gap-4">
            {/* Product Image */}
            <div className="flex-shrink-0 w-24 h-24 bg-gradient-to-br from-orange-50 to-red-50 rounded-lg overflow-hidden border-2 border-red-100">
              {firstItem?.image ? (
                <img 
                  src={resolveImageUrl(firstItem.image)} 
                  alt={firstItem.name || 'Product'} 
                  className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                  onError={(e) => { e.target.style.display = 'none'; }}
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center text-red-300">
                  <Package className="w-10 h-10" />
                </div>
              )}
            </div>

            {/* Order Details */}
            <div className="flex-1 min-w-0">
              <div className="flex items-start justify-between gap-2 mb-2">
                <div className="flex-1">
                  <p className="font-semibold text-gray-900 truncate">
                    {firstItem?.name || firstItem?.sku || 'Order Item'}
                  </p>
                  {firstItem?.brand && (
                    <p className="text-xs text-gray-500">{firstItem.brand}</p>
                  )}
                  {itemCount > 1 && (
                    <p className="text-xs text-orange-600 mt-0.5 font-medium">+{itemCount - 1} more item{itemCount > 2 ? 's' : ''}</p>
                  )}
                </div>
                <span className={`px-3 py-1 text-xs font-semibold rounded-full flex items-center gap-1 border ${statusColors[displayStatus] || 'bg-gray-100 text-gray-800 border-gray-200'}`}>
                  {statusIcons[displayStatus] || <Clock className="w-4 h-4" />}
                  {displayStatus ? displayStatus.replace(/_/g, ' ') : awaitLabel}
                </span>
              </div>
              
              <div className="mt-3">
                <div className="flex items-center">
                  {fulfillmentStages.map((stage, index) => (
                    <div key={stage.key} className="flex items-center flex-1">
                      <div className={`h-2.5 w-2.5 rounded-full ${index <= stageIndex ? 'bg-gradient-to-r from-red-600 to-orange-600' : 'bg-gray-300'}`}></div>
                      {index < fulfillmentStages.length - 1 && (
                        <div className={`flex-1 h-0.5 ${index < stageIndex ? 'bg-gradient-to-r from-red-600 to-orange-600' : 'bg-gray-200'}`}></div>
                      )}
                    </div>
                  ))}
                </div>
                <div className="flex justify-between text-[10px] text-gray-500 mt-1">
                  {fulfillmentStages.map((stage) => (
                    <span key={stage.key}>{stage.short}</span>
                  ))}
                </div>
                {!displayStatus && (
                  <p className="text-[10px] text-gray-400 mt-1">Waiting for fulfillment agent to start.</p>
                )}
              </div>
              
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-medium text-gray-600">Order {orderId}</p>
                  {order.created_at && (
                    <p className="text-xs text-gray-400">{formatDate(order.created_at)}</p>
                  )}
                </div>
                {order.total_amount && (
                  <p className="text-lg font-bold bg-gradient-to-r from-red-600 to-orange-600 bg-clip-text text-transparent">
                    ₹{Number(order.total_amount).toLocaleString('en-IN')}
                  </p>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Action Buttons */}
        <div className="grid grid-cols-3 gap-2 p-4 bg-gradient-to-r from-orange-50 to-red-50 border-t border-orange-200">
          <button 
            onClick={() => navigate(`/orders/${orderId}`)} 
            className="px-4 py-3 bg-gradient-to-r from-red-600 to-orange-600 text-white text-xs font-bold hover:from-red-700 hover:to-orange-700 transition-all rounded-lg flex items-center justify-center gap-2 shadow-md hover:shadow-lg border border-orange-400"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
            <span className="hidden sm:inline">Details</span>
          </button>

          <button 
            onClick={() => navigate('/chat', {
              state: {
                postPurchaseRequest: true,
                orderId: orderId,
                orderItems: order.items || [],
                userId: sessionStore.getCustomerId()
              }
            })}
            className="px-4 py-3 bg-gradient-to-r from-red-700 via-orange-600 to-red-600 text-white text-xs font-bold hover:from-red-800 hover:via-orange-700 hover:to-red-700 transition-all rounded-lg flex items-center justify-center gap-2 shadow-md hover:shadow-lg border border-orange-500"
          >
            <MessageCircle className="w-4 h-4" />
            <span className="hidden sm:inline">Support</span>
          </button>

          <button 
            onClick={() => navigate('/chat', {
              state: {
                stylistRequest: true,
                orderId: orderId,
                product: getFirstItem(order)
              }
            })}
            className="px-4 py-3 bg-gradient-to-br from-red-600 via-red-700 to-orange-700 text-white text-xs font-bold hover:from-red-700 hover:via-red-800 hover:to-orange-800 transition-all rounded-lg flex items-center justify-center gap-2 shadow-md hover:shadow-lg border border-orange-500"
          >
            <Sparkles className="w-4 h-4" />
            <span className="hidden sm:inline">Stylist</span>
          </button>
        </div>
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-orange-50 via-yellow-50 to-red-50">
      <Navbar />
      <div className="max-w-5xl mx-auto px-4 pt-28 pb-12">
        <div className="mb-8">
          <h1 className="text-4xl font-bold text-red-700 mb-2">Your Orders</h1>
          <p className="text-gray-600">Track and manage all your orders in one place</p>
        </div>
        {loading ? (
          <div className="flex flex-col items-center justify-center py-16">
            <div className="animate-spin rounded-full h-12 w-12 border-4 border-red-200 border-t-red-600 mb-4"></div>
            <p className="text-gray-600 font-medium">Loading your orders...</p>
          </div>
        ) : orders.length === 0 ? (
          <div className="text-center py-16 bg-white/80 backdrop-blur rounded-2xl shadow-lg border border-red-100">
            <Package className="mx-auto h-16 w-16 text-red-300 mb-4" />
            <h2 className="text-2xl font-bold text-gray-800 mb-2">No orders yet</h2>
            <p className="text-gray-600 mb-6">Start shopping to see your orders here!</p>
            {errorMessage && (
              <p className="text-sm text-red-600 mb-4">{errorMessage}</p>
            )}
            <button 
              onClick={() => navigate('/products')} 
              className="px-8 py-3 bg-gradient-to-r from-red-600 to-orange-600 text-white rounded-lg font-semibold hover:from-red-700 hover:to-orange-700 transition-all shadow-md hover:shadow-lg"
            >
              Start Shopping
            </button>
          </div>
        ) : (
          <div className="space-y-8">
            {current.length > 0 && (
              <section>
                <div className="flex items-center gap-3 mb-4">
                  <div className="w-1 h-8 bg-gradient-to-b from-red-600 to-orange-600 rounded-full"></div>
                  <h2 className="text-2xl font-bold text-red-700">Current Orders ({current.length})</h2>
                </div>
                <div className="space-y-4">
                  {current.map((o) => (
                    <OrderCard key={o.order_id || o.id} order={o} />
                  ))}
                </div>
              </section>
            )}

            {delivered.length > 0 && (
              <section>
                <div className="flex items-center gap-3 mb-4">
                  <div className="w-1 h-8 bg-gradient-to-b from-green-600 to-emerald-600 rounded-full"></div>
                  <h2 className="text-2xl font-bold text-green-700">Delivered ({delivered.length})</h2>
                </div>
                <div className="space-y-4">
                  {delivered.map((o) => (
                    <OrderCard key={o.order_id || o.id} order={o} />
                  ))}
                </div>
              </section>
            )}

            {cancelled.length > 0 && (
              <section>
                <div className="flex items-center gap-3 mb-4">
                  <div className="w-1 h-8 bg-gradient-to-b from-gray-600 to-gray-400 rounded-full"></div>
                  <h2 className="text-2xl font-bold text-gray-700">Cancelled / Returned ({cancelled.length})</h2>
                </div>
                <div className="space-y-4">
                  {cancelled.map((o) => (
                    <OrderCard key={o.order_id || o.id} order={o} />
                  ))}
                </div>
              </section>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default OrdersPage;

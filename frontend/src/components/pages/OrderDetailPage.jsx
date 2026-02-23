import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import dataService from '@/services/dataService';
import { getFulfillmentStatus } from '@/services/fulfillmentService';
import sessionStore from '@/lib/session';
import Navbar from '@/components/Navbar.jsx';
import { resolveImageUrl } from '@/lib/utils.js';
import { Package, Truck, CheckCircle, Clock } from 'lucide-react';

const OrderDetailPage = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const [order, setOrder] = useState(null);
  const [fulfillment, setFulfillment] = useState(null);
  const [loading, setLoading] = useState(false);
  const customerId = sessionStore.getCustomerId();
  const POLL_MS = 30000;

  useEffect(() => {
    let mounted = true;
    const fetchOrder = async () => {
      setLoading(true);
      try {
        const res = await dataService.getOrder(id);
        const found = res || null;

        // Fetch fulfillment data
        if (found) {
          try {
            const fulfillmentData = await getFulfillmentStatus(id);
            if (mounted) setFulfillment(fulfillmentData);
          } catch (e) {
            console.error('Failed to fetch fulfillment data:', e);
          }
        }
        if (mounted) setOrder(found || { order_id: id, items: [] });
      } catch (e) {
        console.error('Failed to fetch order', e);
      } finally {
        if (mounted) setLoading(false);
      }
    };
    const fetchFulfillment = async () => {
      try {
        const fulfillmentData = await getFulfillmentStatus(id);
        if (mounted) setFulfillment(fulfillmentData);
      } catch (e) {
        console.error('Failed to fetch fulfillment data:', e);
      }
    };

    fetchOrder();
    fetchFulfillment();
    const intervalId = setInterval(fetchFulfillment, POLL_MS);
    return () => { 
      mounted = false; 
      clearInterval(intervalId);
    };
  }, [id, customerId]);

  const formatPrice = (price) => {
    if (!price) return '';
    return `₹${Number(price).toLocaleString('en-IN')}`;
  };

  const statusColors = {
    processing: 'bg-yellow-100 text-yellow-800 border-yellow-200',
    packed: 'bg-blue-100 text-blue-800 border-blue-200',
    shipped: 'bg-indigo-100 text-indigo-800 border-indigo-200',
    out_for_delivery: 'bg-purple-100 text-purple-800 border-purple-200',
    delivered: 'bg-green-100 text-green-800 border-green-200',
    cancelled: 'bg-red-100 text-red-800 border-red-200',
    returned: 'bg-gray-100 text-gray-800 border-gray-200',
    paid: 'bg-orange-100 text-orange-800 border-orange-200',
  };

  const status = (() => {
    if (!fulfillment?.current_status) return null;
    let s = String(fulfillment.current_status).toLowerCase().trim();
    // Handle enum format like "FulfillmentStatus.DELIVERED"
    if (s.includes('.')) {
      s = s.split('.').pop().toLowerCase().trim();
    }
    return s;
  })();
  const awaitLabel = 'Awaiting fulfillment';

  // Fulfillment Timeline Component
  const FulfillmentTimeline = () => {
    const currentStatus = (() => {
      if (!fulfillment?.current_status) return '';
      let s = String(fulfillment.current_status).toLowerCase().trim();
      // Handle enum format
      if (s.includes('.')) {
        s = s.split('.').pop().toLowerCase().trim();
      }
      return s;
    })();

    const stages = [
      { key: 'processing', label: 'Processing', icon: Clock },
      { key: 'packed', label: 'Packed', icon: Package },
      { key: 'shipped', label: 'Shipped', icon: Truck },
      { key: 'out_for_delivery', label: 'Out for Delivery', icon: Truck },
      { key: 'delivered', label: 'Delivered', icon: CheckCircle },
    ];

    const currentIndex = stages.findIndex(s => s.key === currentStatus);

    return (
      <div className="bg-gradient-to-br from-orange-50 to-red-50 rounded-xl p-6 border-2 border-red-100 shadow-md mb-6">
        <h3 className="text-xl font-bold text-red-700 mb-6">Order Tracking</h3>
        {!fulfillment && (
          <p className="text-sm text-gray-600 mb-4">Waiting for fulfillment agent to start.</p>
        )}
        
        {/* Timeline */}
        <div className="relative">
          {/* Progress Line */}
          <div className="absolute top-6 left-0 w-full h-1 bg-gray-200 rounded-full"></div>
          <div 
            className="absolute top-6 left-0 h-1 bg-gradient-to-r from-red-600 to-orange-600 rounded-full transition-all duration-500"
            style={{ width: `${currentIndex >= 0 ? (currentIndex / (stages.length - 1)) * 100 : 0}%` }}
          ></div>

          {/* Stages */}
          <div className="relative flex justify-between">
            {stages.map((stage, index) => {
              const Icon = stage.icon;
              const isCompleted = currentIndex >= 0 && index <= currentIndex;
              const isCurrent = currentIndex >= 0 && index === currentIndex;
              const stageTimestamp = fulfillment?.[`${stage.key}_at`];

              return (
                <div key={stage.key} className="flex flex-col items-center" style={{ flex: 1 }}>
                  <div className={`w-12 h-12 rounded-full flex items-center justify-center mb-2 border-2 transition-all ${
                    isCompleted 
                      ? 'bg-gradient-to-r from-red-600 to-orange-600 border-red-600 text-white shadow-lg' 
                      : 'bg-white border-gray-300 text-gray-400'
                  } ${isCurrent ? 'animate-pulse' : ''}`}>
                    <Icon className="w-6 h-6" />
                  </div>
                  <p className={`text-xs font-semibold text-center ${isCompleted ? 'text-red-700' : 'text-gray-500'}`}>
                    {stage.label}
                  </p>
                  {stageTimestamp && isCompleted && (
                    <p className="text-xs text-gray-500 mt-1">
                      {new Date(stageTimestamp).toLocaleDateString('en-IN', { 
                        day: 'numeric', 
                        month: 'short',
                        hour: '2-digit',
                        minute: '2-digit'
                      })}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </div>

      </div>
    );
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-orange-50 via-yellow-50 to-red-50">
      <Navbar />
      <div className="max-w-5xl mx-auto px-4 pt-28 pb-12">
        <button onClick={() => navigate('/orders')} className="flex items-center gap-2 text-sm font-medium text-red-600 hover:text-red-800 mb-6 transition-colors">
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
          Back to all orders
        </button>
        
        {loading ? (
          <div className="flex flex-col items-center justify-center py-16">
            <div className="animate-spin rounded-full h-12 w-12 border-4 border-red-200 border-t-red-600 mb-4"></div>
            <p className="text-gray-600 font-medium">Loading order details...</p>
          </div>
        ) : (
          <div>
            {/* Order Header */}
            <div className="bg-white/95 backdrop-blur rounded-2xl p-6 shadow-lg border border-red-100 mb-6">
              <div className="flex items-start justify-between mb-4">
                <div>
                  <h2 className="text-3xl font-bold bg-gradient-to-r from-red-700 to-orange-700 bg-clip-text text-transparent">
                    Order {order?.order_id || id}
                  </h2>
                  {order?.created_at && (
                    <p className="text-sm text-gray-600 mt-2">
                      Placed on {new Date(order.created_at).toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' })}
                    </p>
                  )}
                </div>
                <span className={`px-4 py-2 text-sm font-bold rounded-full border-2 ${statusColors[status] || 'bg-gray-100 text-gray-800 border-gray-200'}`}>
                  {status ? status.replace(/_/g, ' ') : awaitLabel}
                </span>
              </div>
            </div>

            {/* Fulfillment Timeline */}
            <FulfillmentTimeline />

            {/* Order Items */}
            <div className="bg-white/95 backdrop-blur rounded-2xl p-6 shadow-lg border border-red-100 mb-6">
              <h3 className="text-xl font-bold text-red-700 mb-6">Order Items ({(order?.items || []).length})</h3>
              <div className="space-y-4">
                {(order?.items || []).map((it, idx) => (
                  <div key={it.sku || idx} className="flex gap-4 p-4 bg-gradient-to-r from-orange-50 to-red-50 rounded-xl border border-red-100">
                    {/* Product Image */}
                    <div className="flex-shrink-0 w-24 h-24 bg-white rounded-lg overflow-hidden border-2 border-red-200 shadow-sm">
                      {it.image ? (
                        <img 
                          src={resolveImageUrl(it.image)} 
                          alt={it.name || 'Product'} 
                          className="w-full h-full object-cover"
                          onError={(e) => { e.target.style.display = 'none'; }}
                        />
                      ) : (
                        <div className="w-full h-full flex items-center justify-center text-red-300">
                          <Package className="w-10 h-10" />
                        </div>
                      )}
                    </div>

                    {/* Product Details */}
                    <div className="flex-1 min-w-0">
                      <p className="font-bold text-gray-900">{it.name || it.sku}</p>
                      {it.brand && <p className="text-sm text-gray-600 font-medium">{it.brand}</p>}
                      <div className="flex items-center gap-3 mt-1 text-sm text-gray-600">
                        {it.category && <span className="bg-white px-2 py-1 rounded text-xs font-medium">{it.category}</span>}
                        {it.color && <span className="text-xs">• {it.color}</span>}
                      </div>
                      <p className="text-xs text-gray-400 mt-1">SKU: {it.sku}</p>
                    </div>

                    {/* Quantity & Price */}
                    <div className="text-right">
                      <p className="text-xl font-bold bg-gradient-to-r from-red-600 to-orange-600 bg-clip-text text-transparent">
                        {formatPrice(it.line_total || it.unit_price)}
                      </p>
                      <p className="text-sm text-gray-600 font-medium">Qty: {it.qty || 1}</p>
                      {it.unit_price && <p className="text-xs text-gray-500">{formatPrice(it.unit_price)} each</p>}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Order Total */}
            {order?.total_amount && (
              <div className="flex justify-between items-center py-5 px-6 bg-gradient-to-r from-red-100 to-orange-100 rounded-xl border-2 border-red-200 mb-6">
                <span className="text-xl font-bold text-red-800">Total Amount</span>
                <span className="text-3xl font-bold bg-gradient-to-r from-red-700 to-orange-700 bg-clip-text text-transparent">
                  {formatPrice(order.total_amount)}
                </span>
              </div>
            )}

          </div>
        )}
      </div>
    </div>
  );
};

export default OrderDetailPage;

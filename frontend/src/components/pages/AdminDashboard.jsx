import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Clock, CheckCircle, Package, AlertCircle, LogOut, MapPin, Lightbulb, ChevronDown, ChevronUp } from 'lucide-react';
import reservationService from '@/services/reservationService';

const AdminDashboard = () => {
  const navigate = useNavigate();
  const [store, setStore] = useState(() => localStorage.getItem('ey_store_location') || 'STORE_MUMBAI');
  const [reservations, setReservations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [confirmingId, setConfirmingId] = useState(null);
  const [convertingId, setConvertingId] = useState(null);
  const [currentTime, setCurrentTime] = useState(new Date());
  const [expandedIds, setExpandedIds] = useState({});
  const [insights, setInsights] = useState({});
  const [insightsLoading, setInsightsLoading] = useState({});

  // Update current time every second for countdown
  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  // Load reservations when store changes
  useEffect(() => {
    loadReservations();
  }, [store]);

  const loadReservations = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await reservationService.listStoreReservations(store);
      setReservations(data.reservations || []);
    } catch (err) {
      console.error('Failed to load reservations:', err);
      setError('Failed to load reservations. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const fetchInsights = async (reservationId) => {
    if (insights[reservationId]) {
      // Toggle expansion
      setExpandedIds(prev => ({
        ...prev,
        [reservationId]: !prev[reservationId]
      }));
      return;
    }

    setInsightsLoading(prev => ({ ...prev, [reservationId]: true }));
    try {
      const SALES_AGENT_API = 'http://localhost:8010';
      const response = await fetch(`${SALES_AGENT_API}/api/reservations/${reservationId}/insights`);
      
      if (response.ok) {
        const data = await response.json();
        setInsights(prev => ({ ...prev, [reservationId]: data }));
        setExpandedIds(prev => ({ ...prev, [reservationId]: true }));
      } else {
        console.error('Failed to fetch insights:', response.statusText);
      }
    } catch (err) {
      console.error('Error fetching insights:', err);
    } finally {
      setInsightsLoading(prev => ({ ...prev, [reservationId]: false }));
    }
  };

  const handleConfirmReservation = async (reservationId) => {
    setConfirmingId(reservationId);
    try {
      await reservationService.confirmReservation(reservationId, store);
      setReservations((prev) =>
        prev.map((r) =>
          r.reservation_id === reservationId
            ? { ...r, status: 'CONFIRMED', confirmed_at: new Date().toISOString() }
            : r
        )
      );
      alert('✓ Reservation confirmed! Item marked as kept aside.');
    } catch (err) {
      console.error('Failed to confirm reservation:', err);
      alert('Failed to confirm reservation. Please try again.');
    } finally {
      setConfirmingId(null);
    }
  };

  const handleConvertToSale = async (reservationId) => {
    setConvertingId(reservationId);
    try {
      await reservationService.convertReservation(reservationId, store);
      setReservations((prev) =>
        prev.map((r) =>
          r.reservation_id === reservationId
            ? { ...r, status: 'CONVERTED', converted_at: new Date().toISOString() }
            : r
        )
      );
      alert('✓ Reservation converted to purchase!');
    } catch (err) {
      console.error('Failed to convert reservation:', err);
      alert('Failed to convert reservation. Please try again.');
    } finally {
      setConvertingId(null);
    }
  };

  const handleLogout = () => {
    localStorage.removeItem('ey_store_location');
    navigate('/');
  };

  const formatTime = (isoString) => {
    if (!isoString) return '';
    const date = new Date(isoString);
    return date.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
  };

  const formatDate = (isoString) => {
    if (!isoString) return '';
    const date = new Date(isoString);
    return date.toLocaleDateString('en-IN');
  };

  const getTimeRemaining = (expiresAt) => {
    const expiry = new Date(expiresAt);
    const diff = expiry - currentTime;
    
    if (diff < 0) return 'Expired';
    
    const hours = Math.floor(diff / 3600000);
    const minutes = Math.floor((diff % 3600000) / 60000);
    
    return `${hours}h ${minutes}m`;
  };

  const getStatusColor = (status) => {
    switch (status) {
      case 'ACTIVE':
        return 'bg-slate-50 border-slate-200';
      case 'CONFIRMED':
        return 'bg-red-50 border-red-200';
      case 'CONVERTED':
        return 'bg-teal-50 border-teal-200';
      default:
        return 'bg-gray-50 border-gray-200';
    }
  };

  const getStatusBadge = (status) => {
    switch (status) {
      case 'ACTIVE':
        return <span className="inline-flex items-center gap-1 px-3 py-1 bg-slate-100 text-slate-700 rounded-full text-sm font-medium"><Clock className="w-4 h-4" />Awaiting Confirmation</span>;
      case 'CONFIRMED':
        return <span className="inline-flex items-center gap-1 px-3 py-1 bg-red-100 text-red-700 rounded-full text-sm font-medium"><CheckCircle className="w-4 h-4" />Kept Aside</span>;
      case 'CONVERTED':
        return <span className="inline-flex items-center gap-1 px-3 py-1 bg-teal-100 text-teal-700 rounded-full text-sm font-medium"><Package className="w-4 h-4" />Purchased</span>;
      default:
        return <span className="inline-flex items-center gap-1 px-3 py-1 bg-gray-100 text-gray-700 rounded-full text-sm font-medium">{status}</span>;
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-orange-50 via-yellow-50 to-red-50">
      {/* Premium Header - Organized & Professional */}
      <div className="bg-gradient-to-r from-red-600 to-orange-600 shadow-2xl">
        {/* Top Navigation Bar */}
        <div className="max-w-7xl mx-auto px-6">
          <div className="py-6 flex items-center justify-between border-b border-red-500/30">
            <div className="flex items-center gap-3">
              <div className="w-12 h-12 bg-white/20 rounded-xl flex items-center justify-center backdrop-blur-sm">
                <Package className="w-6 h-6 text-white" />
              </div>
              <div>
                <h1 className="text-2xl font-bold text-white">Store Dashboard</h1>
                <p className="text-sm text-orange-100">Reservation Management System</p>
              </div>
            </div>
            <div className="text-right">
              <p className="text-orange-100 text-sm">Store Management</p>
              <p className="text-white font-semibold">{new Date().toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}</p>
            </div>
          </div>

          {/* Bottom Control Bar */}
          <div className="py-4 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <MapPin className="w-5 h-5 text-orange-100" />
              <div>
                <span className="text-orange-100 text-sm">Active Store</span>
                <div className="flex items-center gap-2">
                  <select
                    value={store}
                    onChange={(e) => {
                      setStore(e.target.value);
                      localStorage.setItem('ey_store_location', e.target.value);
                    }}
                    className="px-4 py-2 rounded-lg bg-white text-red-700 font-semibold hover:bg-orange-50 transition-colors text-sm"
                  >
                    <option value="STORE_MUMBAI">📍 Mumbai Store</option>
                    <option value="STORE_DELHI">📍 Delhi Store</option>
                    <option value="STORE_BANGALORE">📍 Bangalore Store</option>
                    <option value="STORE_PUNE">📍 Pune Store</option>
                  </select>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-3">
              <button
                onClick={handleLogout}
                className="flex items-center gap-2 px-5 py-2 bg-white/20 backdrop-blur-sm text-white rounded-lg font-semibold hover:bg-white/30 transition-all border border-white/30 hover:border-white/50 text-sm"
              >
                <LogOut className="w-4 h-4" />
                Logout
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="max-w-7xl mx-auto px-4 py-12">
        {/* Status Overview - Premium Cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-12">
          <div className="bg-white rounded-2xl shadow-lg p-8 hover:shadow-2xl transition-all border border-slate-100 hover:border-slate-300">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-gray-600 text-sm font-semibold uppercase tracking-wider">Awaiting Confirmation</p>
                <p className="text-5xl font-bold text-slate-600 mt-3">
                  {reservations.filter((r) => r.status === 'ACTIVE').length}
                </p>
              </div>
              <Clock className="w-16 h-16 text-slate-200" />
            </div>
          </div>

          <div className="bg-white rounded-2xl shadow-lg p-8 hover:shadow-2xl transition-all border border-red-100 hover:border-red-300">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-gray-600 text-sm font-semibold uppercase tracking-wider">Kept Aside</p>
                <p className="text-5xl font-bold text-red-600 mt-3">
                  {reservations.filter((r) => r.status === 'CONFIRMED').length}
                </p>
              </div>
              <CheckCircle className="w-16 h-16 text-red-200" />
            </div>
          </div>

          <div className="bg-white rounded-2xl shadow-lg p-8 hover:shadow-2xl transition-all border border-teal-100 hover:border-teal-300">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-gray-600 text-sm font-semibold uppercase tracking-wider">Converted to Sales</p>
                <p className="text-5xl font-bold text-teal-600 mt-3">
                  {reservations.filter((r) => r.status === 'CONVERTED').length}
                </p>
              </div>
              <Package className="w-16 h-16 text-teal-200" />
            </div>
          </div>
        </div>

        {/* Error Message */}
        {error && (
          <div className="mb-8 p-6 bg-red-50 border-2 border-red-200 rounded-2xl flex items-center gap-4">
            <AlertCircle className="w-6 h-6 text-red-600 flex-shrink-0" />
            <p className="text-red-700 font-semibold flex-grow">{error}</p>
            <button
              onClick={loadReservations}
              className="px-6 py-2 bg-red-600 text-white rounded-lg font-medium hover:bg-red-700 transition-colors whitespace-nowrap"
            >
              Retry
            </button>
          </div>
        )}

        {/* Reservations Grid - Premium Layout */}
        {loading ? (
          <div className="flex flex-col items-center justify-center py-20">
            <div className="inline-block animate-spin rounded-full h-12 w-12 border-4 border-orange-600 border-t-transparent"></div>
            <p className="mt-4 text-gray-600 font-semibold text-lg">Loading reservations...</p>
          </div>
        ) : reservations.length === 0 ? (
          <div className="text-center py-20 bg-white rounded-2xl shadow-lg border border-gray-200">
            <AlertCircle className="w-20 h-20 text-gray-400 mx-auto mb-4" />
            <p className="text-gray-600 font-semibold text-lg">No reservations at this store</p>
            <p className="text-gray-500 mt-1">Reservations will appear here as customers make them</p>
          </div>
        ) : (
          <div className="space-y-6">
            {reservations.map((res) => (
              <div key={res.reservation_id}>
                {/* Premium Reservation Card */}
                <div className={`rounded-2xl shadow-lg overflow-hidden transition-all hover:shadow-2xl border-2 ${
                  res.status === 'ACTIVE' 
                    ? 'border-orange-200 bg-orange-50/50' 
                    : res.status === 'CONFIRMED' 
                    ? 'border-red-200 bg-red-50/50' 
                    : 'border-purple-200 bg-purple-50/50'
                }`}>
                  <div className="p-8">
                    {/* Top Section: Product Card with Image */}
                    <div className="flex gap-8 mb-8 pb-8 border-b-2 border-current border-opacity-20">
                      {/* Product Image - Left Side */}
                      <div className="flex-shrink-0">
                        {res.product_image ? (
                          <img
                            src={res.product_image}
                            alt={res.sku}
                            className="w-40 h-40 object-cover rounded-xl shadow-lg"
                            onError={(e) => {
                              e.target.style.display = 'none';
                              if (e.target.nextElementSibling) {
                                e.target.nextElementSibling.style.display = 'flex';
                              }
                            }}
                          />
                        ) : null}
                        {(!res.product_image || res.product_image) && (
                          <div style={res.product_image ? { display: 'none' } : {}}>
                            <div className="w-40 h-40 rounded-xl bg-gradient-to-br from-orange-200 to-red-200 flex items-center justify-center shadow-lg">
                              <Package className="w-20 h-20 text-white opacity-50" />
                            </div>
                          </div>
                        )}
                      </div>

                      {/* Product & Reservation Info - Right Side */}
                      <div className="flex-grow">
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                          {/* ID & Date */}
                          <div>
                            <p className="text-xs text-gray-600 uppercase tracking-wider font-bold mb-2">Reservation ID</p>
                            <p className="text-2xl font-mono font-bold text-gray-900 mb-4">{res.reservation_id}</p>
                            <p className="text-xs text-gray-600">
                              Created: {formatDate(res.created_at)} at {formatTime(res.created_at)}
                            </p>
                          </div>

                          {/* Status Badge */}
                          <div>
                            <p className="text-xs text-gray-600 uppercase tracking-wider font-bold mb-2">Status</p>
                            <div className="mb-4">
                              {getStatusBadge(res.status)}
                            </div>
                            {res.confirmed_at && (
                              <p className="text-xs text-gray-600">
                                Confirmed: {formatDate(res.confirmed_at)}
                              </p>
                            )}
                          </div>
                        </div>

                        {/* Product Details */}
                        <div className="mt-6 pt-6 border-t border-gray-300">
                          <p className="text-xs text-gray-600 uppercase tracking-wider font-bold mb-3">Product Details</p>
                          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                            {res.product_name && (
                              <div>
                                <p className="text-xs text-gray-500">Product Name</p>
                                <p className="font-semibold text-gray-900">{res.product_name}</p>
                              </div>
                            )}
                            <div>
                              <p className="text-xs text-gray-500">SKU</p>
                              <p className="font-semibold text-gray-900">{res.sku}</p>
                            </div>
                            <div>
                              <p className="text-xs text-gray-500">Quantity</p>
                              <p className="font-semibold text-gray-900">{res.quantity}</p>
                            </div>
                            <div>
                              <p className="text-xs text-gray-500">Expires In</p>
                              <p className={`font-bold text-lg ${
                                res.status === 'ACTIVE' && new Date(res.expires_at) - currentTime < 3600000 ? 'text-red-600' : 'text-green-600'
                              }`}>
                                {getTimeRemaining(res.expires_at)}
                              </p>
                            </div>
                            <div>
                              <p className="text-xs text-gray-500">Expires At</p>
                              <p className="text-sm text-gray-900">{formatDate(res.expires_at)} {formatTime(res.expires_at)}</p>
                            </div>
                          </div>
                        </div>

                        {/* Customer Details */}
                        <div className="mt-6 pt-6 border-t border-gray-300">
                          <p className="text-xs text-gray-600 uppercase tracking-wider font-bold mb-3">Customer Details</p>
                          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                            <div>
                              <p className="text-xs text-gray-500">Customer ID</p>
                              <p className="font-semibold text-gray-900">{res.customer_id}</p>
                            </div>
                            {res.customer_name && (
                              <div>
                                <p className="text-xs text-gray-500">Name</p>
                                <p className="font-semibold text-gray-900">{res.customer_name}</p>
                              </div>
                            )}
                            {res.customer_phone && (
                              <div>
                                <p className="text-xs text-gray-500">Phone</p>
                                <p className="font-semibold text-gray-900">{res.customer_phone}</p>
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>

                    {/* Bottom: Actions */}
                    <div className="flex flex-wrap gap-3 justify-end">
                      <button
                        onClick={() => fetchInsights(res.reservation_id)}
                        disabled={insightsLoading[res.reservation_id]}
                        className="flex items-center gap-2 px-6 py-3 bg-gradient-to-r from-slate-600 to-slate-700 text-white rounded-lg font-semibold hover:from-slate-700 hover:to-slate-800 disabled:opacity-50 transition-all shadow-md"
                      >
                        <Lightbulb className="w-5 h-5" />
                        {expandedIds[res.reservation_id] ? 'Hide' : 'Show'} Insights
                      </button>

                      {res.status === 'ACTIVE' && (
                        <button
                          onClick={() => handleConfirmReservation(res.reservation_id)}
                          disabled={confirmingId === res.reservation_id}
                          className="px-6 py-3 bg-gradient-to-r from-orange-500 to-orange-600 text-white rounded-lg font-semibold hover:from-orange-600 hover:to-orange-700 disabled:opacity-50 disabled:cursor-not-allowed transition-all shadow-md"
                        >
                          {confirmingId === res.reservation_id ? 'Confirming...' : 'Keep Aside'}
                        </button>
                      )}

                      {(res.status === 'CONFIRMED' || res.status === 'ACTIVE') && (
                        <button
                          onClick={() => handleConvertToSale(res.reservation_id)}
                          disabled={convertingId === res.reservation_id}
                          className="px-6 py-3 bg-gradient-to-r from-teal-600 to-teal-700 text-white rounded-lg font-semibold hover:from-teal-700 hover:to-teal-800 disabled:opacity-50 disabled:cursor-not-allowed transition-all shadow-md"
                        >
                          {convertingId === res.reservation_id ? 'Converting...' : 'Convert to Sale'}
                        </button>
                      )}

                      {res.status === 'CONVERTED' && (
                        <div className="px-6 py-3 bg-gradient-to-r from-green-500 to-green-600 text-white rounded-lg font-semibold text-center shadow-md">
                          ✓ Purchased
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                {/* Expanded Insights Section - Premium */}
                {expandedIds[res.reservation_id] && insights[res.reservation_id] && (
                  <div className="mt-4 rounded-2xl bg-gradient-to-br from-slate-50 to-slate-100 border-2 border-slate-200 shadow-lg overflow-hidden">
                    <div className="p-8">
                      <h3 className="text-xl font-bold text-gray-900 mb-6 flex items-center gap-3">
                        <Lightbulb className="w-6 h-6 text-slate-700" />
                        Customer Insights & Details
                      </h3>

                      {/* Product Details Box */}
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
                        <div className="bg-white rounded-xl shadow-md p-6 border-l-4 border-slate-600">
                          <p className="text-xs text-gray-600 uppercase tracking-wider font-bold mb-4">Product Details</p>
                          <p className="text-lg font-bold text-gray-900 mb-3">{insights[res.reservation_id].product.name}</p>
                          <div className="space-y-2">
                            <p className="text-sm text-gray-700"><span className="font-semibold text-gray-900">SKU:</span> {insights[res.reservation_id].product.sku}</p>
                            <p className="text-sm text-gray-700"><span className="font-semibold text-gray-900">Price:</span> ₹{insights[res.reservation_id].product.price}</p>
                            <p className="text-sm text-gray-700"><span className="font-semibold text-gray-900">Qty:</span> {insights[res.reservation_id].product.quantity}</p>
                          </div>
                        </div>

                        <div className="bg-white rounded-xl shadow-md p-6 border-l-4 border-green-500">
                          <p className="text-xs text-gray-600 uppercase tracking-wider font-bold mb-4">Customer Profile</p>
                          <div className="space-y-2">
                            {insights[res.reservation_id].customer.name && (
                              <p className="text-sm text-gray-700"><span className="font-semibold text-gray-900">Name:</span> {insights[res.reservation_id].customer.name}</p>
                            )}
                            {insights[res.reservation_id].customer.phone && (
                              <p className="text-sm text-gray-700"><span className="font-semibold text-gray-900">Phone:</span> {insights[res.reservation_id].customer.phone}</p>
                            )}
                            <p className="text-sm text-gray-700"><span className="font-semibold text-gray-900">Loyalty Tier:</span> {insights[res.reservation_id].customer.loyalty_tier}</p>
                            <p className="text-sm text-gray-700"><span className="font-semibold text-gray-900">Interactions:</span> {insights[res.reservation_id].customer.previous_interactions} messages</p>
                            <p className="text-sm text-gray-700"><span className="font-semibold text-gray-900">Interests:</span> {insights[res.reservation_id].customer.interests.length > 0 ? insights[res.reservation_id].customer.interests.join(', ') : 'New customer'}</p>
                          </div>
                        </div>
                      </div>

                      {/* AI Insight Box */}
                      <div className="bg-white rounded-xl shadow-md p-6 border-l-4 border-teal-600">
                        <p className="text-xs text-gray-600 uppercase tracking-wider font-bold mb-4 flex items-center gap-2">
                          <span className="text-lg">🤖</span> AI-Powered Customer Insight
                        </p>
                        <p className="text-base leading-relaxed text-gray-800">{insights[res.reservation_id].ai_insight}</p>
                      </div>
                    </div>
                  </div>
                )}

                {/* Loading Insights */}
                {expandedIds[res.reservation_id] && insightsLoading[res.reservation_id] && (
                  <div className="mt-4 rounded-2xl bg-slate-50 border-2 border-slate-200 p-6 flex items-center gap-4">
                    <div className="inline-block animate-spin rounded-full h-5 w-5 border-2 border-slate-600 border-t-transparent"></div>
                    <p className="text-gray-600 font-semibold">Loading customer insights...</p>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

      </div>

      {/* Footer */}
      <div className="mt-16 py-8 text-center text-gray-600 font-medium border-t border-gray-200">
        <p>Last updated: {currentTime.toLocaleTimeString('en-IN')}</p>
      </div>
    </div>
  );
};

export default AdminDashboard;

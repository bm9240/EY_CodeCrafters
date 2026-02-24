import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, Clock, CheckCircle, Package, AlertCircle, MapPin, Calendar } from 'lucide-react';
import reservationService from '@/services/reservationService';
import sessionStore from '@/lib/session';
import Navbar from '@/components/Navbar.jsx';

const MyReservations = () => {
  const navigate = useNavigate();
  const [reservations, setReservations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [currentTime, setCurrentTime] = useState(new Date());

  // Update current time every second for countdown
  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  // Load reservations on mount
  useEffect(() => {
    loadReservations();
  }, []);

  const loadReservations = async () => {
    setLoading(true);
    setError(null);
    try {
      const customerId = sessionStore.getCustomerId();
      
      if (!customerId) {
        setError('Please log in to view your reservations');
        setLoading(false);
        return;
      }

      // First try API
      try {
        const data = await reservationService.listCustomerReservations(customerId);
        setReservations(data.reservations || []);
      } catch (apiError) {
        console.warn('API call failed, checking localStorage:', apiError);
        // Fallback to localStorage
        const stored = JSON.parse(localStorage.getItem('ey_reservations') || '[]');
        setReservations(stored);
      }
    } catch (err) {
      console.error('Failed to load reservations:', err);
      setError('Failed to load reservations. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleCancelReservation = async (reservationId) => {
    if (!window.confirm('Are you sure you want to cancel this reservation?')) return;

    try {
      await reservationService.cancelReservation(reservationId);
      setReservations((prev) =>
        prev.filter((r) => r.reservation_id !== reservationId)
      );
      alert('✓ Reservation cancelled');
    } catch (err) {
      console.error('Failed to cancel:', err);
      alert('Failed to cancel reservation. Please try again.');
    }
  };

  const formatTime = (isoString) => {
    if (!isoString) return '';
    const date = new Date(isoString);
    return date.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
  };

  const formatDate = (isoString) => {
    if (!isoString) return '';
    const date = new Date(isoString);
    return date.toLocaleDateString('en-IN', { weekday: 'short', month: 'short', day: 'numeric' });
  };

  const getTimeRemaining = (expiresAt) => {
    const expiry = new Date(expiresAt);
    const diff = expiry - currentTime;
    
    if (diff < 0) return 'Expired';
    
    const hours = Math.floor(diff / 3600000);
    const minutes = Math.floor((diff % 3600000) / 60000);
    
    if (hours === 0) {
      return `${minutes}m remaining`;
    }
    return `${hours}h ${minutes}m remaining`;
  };

  const getStatusIcon = (status) => {
    switch (status) {
      case 'ACTIVE':
        return <Clock className="w-6 h-6 text-slate-600" />;
      case 'CONFIRMED':
        return <CheckCircle className="w-6 h-6 text-red-600" />;
      case 'CONVERTED':
        return <Package className="w-6 h-6 text-teal-700" />;
      default:
        return <AlertCircle className="w-6 h-6 text-gray-400" />;
    }
  };

  const getStatusLabel = (status) => {
    switch (status) {
      case 'ACTIVE':
        return 'Awaiting Confirmation';
      case 'CONFIRMED':
        return 'Item Kept Aside';
      case 'CONVERTED':
        return 'Purchased';
      case 'EXPIRED':
        return 'Expired';
      default:
        return status;
    }
  };

  const getStatusColor = (status) => {
    switch (status) {
      case 'ACTIVE':
        return 'bg-slate-50 border-slate-200';
      case 'CONFIRMED':
        return 'bg-red-50 border-red-200';
      case 'CONVERTED':
        return 'bg-teal-50 border-teal-200';
      case 'EXPIRED':
        return 'bg-gray-50 border-gray-200';
      default:
        return 'bg-gray-50 border-gray-200';
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-orange-50 via-yellow-50 to-red-50">
      <Navbar />

      {/* Header */}
      <div className="pt-32 pb-8">
        <div className="max-w-5xl mx-auto px-4 py-6 bg-gradient-to-r from-red-600 to-orange-600 text-white shadow-md rounded-lg">
          <div className="flex items-center gap-4">
            <button
              onClick={() => navigate(-1)}
              className="hover:bg-white/10 p-2 rounded-full transition-colors"
            >
              <ArrowLeft className="w-6 h-6" />
            </button>
            <div className="flex items-center gap-3">
              <Package className="w-8 h-8" />
              <div>
                <h1 className="text-2xl font-bold">My Reservations</h1>
                <p className="text-sm text-orange-100">
                  View and manage your store reservations
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="max-w-5xl mx-auto px-4 pb-16">
        {/* Error Message */}
        {error && (
          <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-lg flex items-center gap-3">
            <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0" />
            <p className="text-red-700 font-semibold">{error}</p>
          </div>
        )}

        {/* Loading State */}
        {loading ? (
          <div className="text-center py-16 bg-white rounded-lg shadow-md">
            <div className="inline-block animate-spin rounded-full h-8 w-8 border border-red-600 border-t-transparent"></div>
            <p className="text-gray-600 mt-3 font-semibold">Loading your reservations...</p>
          </div>
        ) : reservations.length === 0 ? (
          <div className="text-center py-16 bg-white rounded-lg shadow-md">
            <Package className="w-24 h-24 mx-auto text-gray-300 mb-4" />
            <h2 className="text-2xl font-bold text-gray-800 mb-2">No Reservations Yet</h2>
            <p className="text-gray-600 mb-6">You haven't made any store reservations</p>
            <button
              onClick={() => navigate('/cart')}
              className="bg-gradient-to-r from-red-600 to-orange-600 text-white px-6 py-3 rounded-lg font-semibold hover:from-red-700 hover:to-orange-700 transition-all"
            >
              Browse Products
            </button>
          </div>
        ) : (
          <div className="space-y-4">
            {reservations.map((res) => (
              <div
                key={res.reservation_id}
                className={`border-2 rounded-lg shadow-sm overflow-hidden transition-all hover:shadow-md ${getStatusColor(res.status)}`}
              >
                <div className="p-6">
                  {/* Top: ID and Status Badge */}
                  <div className="flex items-start justify-between mb-6 pb-4 border-b border-current border-opacity-20">
                    <div>
                      <p className="text-xs text-gray-600 uppercase tracking-widest font-bold mb-1">ID</p>
                      <p className="text-lg font-mono font-bold text-gray-900">{res.reservation_id}</p>
                    </div>
                    <div className="flex items-center gap-3">
                      <div className="text-right">
                        <div className="flex items-center gap-2 mb-1">
                          {getStatusIcon(res.status)}
                          <span className="text-sm font-bold text-gray-900">{getStatusLabel(res.status)}</span>
                        </div>
                        <p className="text-xs text-gray-600">
                          {res.confirmed_at
                            ? `Confirmed ${formatDate(res.confirmed_at)}`
                            : `Made ${formatDate(res.created_at)}`}
                        </p>
                      </div>
                    </div>
                  </div>

                  {/* Product, Store, Expiry Grid */}
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-6 pb-6 border-b border-current border-opacity-20">
                    {/* Product Section */}
                    <div>
                      <p className="text-xs text-gray-600 uppercase tracking-widest font-bold mb-3">Product</p>
                      {res.product_image && (
                        <img
                          src={res.product_image}
                          alt={res.product_name}
                          className="w-28 h-28 object-cover rounded-lg mb-3 shadow-sm"
                          onError={(e) => (e.target.style.display = 'none')}
                        />
                      )}
                      {res.product_name && (
                        <p className="text-sm font-bold text-gray-900 mb-1">{res.product_name}</p>
                      )}
                      <p className="text-sm text-gray-700 mb-1"><span className="font-semibold">SKU:</span> {res.sku}</p>
                      <p className="text-sm text-gray-700"><span className="font-semibold">Qty:</span> {res.quantity}</p>
                    </div>

                    {/* Store Location */}
                    <div>
                      <p className="text-xs text-gray-600 uppercase tracking-widest font-bold mb-3">Store Location</p>
                      <div className="flex items-start gap-2 mb-3">
                        <MapPin className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
                        <span className="text-lg font-bold text-gray-900">{res.store_location}</span>
                      </div>
                      <p className="text-xs text-gray-600">
                        Made: {formatDate(res.created_at)}
                      </p>
                      <p className="text-xs text-gray-600">{formatTime(res.created_at)}</p>
                    </div>

                    {/* Expiry Countdown */}
                    <div>
                      <p className="text-xs text-gray-600 uppercase tracking-widest font-bold mb-3">Expiry Countdown</p>
                      <p className={`text-2xl font-bold ${
                        res.status === 'ACTIVE' && new Date(res.expires_at) - currentTime < 3600000
                          ? 'text-red-600'
                          : 'text-green-600'
                      }`}>
                        {res.status === 'ACTIVE' ? getTimeRemaining(res.expires_at) : 'N/A'}
                      </p>
                      {res.status === 'ACTIVE' && (
                        <p className="text-xs text-gray-600 mt-2">
                          Until: {formatDate(res.expires_at)} {formatTime(res.expires_at)}
                        </p>
                      )}
                    </div>
                  </div>

                  {/* Customer Context (if available) */}
                  {res.customer_context_summary && (
                    <div className="mb-6 p-4 bg-orange-100 rounded-lg border border-orange-300">
                      <p className="text-xs font-bold text-orange-900 mb-2">📝 Store's Notes</p>
                      <p className="text-sm text-orange-900 leading-relaxed">{res.customer_context_summary}</p>
                    </div>
                  )}

                  {/* Actions */}
                  <div className="flex gap-3 justify-end">
                    {res.status === 'ACTIVE' && (
                      <button
                        onClick={() => handleCancelReservation(res.reservation_id)}
                        className="px-6 py-2 bg-red-100 text-red-700 rounded-lg font-semibold hover:bg-red-200 transition-colors text-sm"
                      >
                        Cancel
                      </button>
                    )}
                    <button
                      onClick={() => navigate('/cart')}
                      className="px-6 py-2 bg-gradient-to-r from-red-600 to-orange-600 text-white rounded-lg font-semibold hover:from-red-700 hover:to-orange-700 transition-all text-sm"
                    >
                      Browse More
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default MyReservations;

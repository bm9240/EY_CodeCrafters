import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Clock, CheckCircle, Package, AlertCircle, LogOut } from 'lucide-react';
import reservationService from '@/services/reservationService';
import Navbar from '@/components/Navbar.jsx';

const AdminDashboard = () => {
  const navigate = useNavigate();
  const [store, setStore] = useState(() => localStorage.getItem('ey_store_location') || 'STORE_MUMBAI');
  const [reservations, setReservations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [confirmingId, setConfirmingId] = useState(null);
  const [convertingId, setConvertingId] = useState(null);
  const [currentTime, setCurrentTime] = useState(new Date());

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
        return 'bg-blue-50 border-blue-200';
      case 'CONFIRMED':
        return 'bg-green-50 border-green-200';
      case 'CONVERTED':
        return 'bg-gray-50 border-gray-200';
      default:
        return 'bg-gray-50 border-gray-200';
    }
  };

  const getStatusBadge = (status) => {
    switch (status) {
      case 'ACTIVE':
        return <span className="inline-flex items-center gap-1 px-3 py-1 bg-blue-100 text-blue-700 rounded-full text-sm font-medium"><Clock className="w-4 h-4" />Incoming</span>;
      case 'CONFIRMED':
        return <span className="inline-flex items-center gap-1 px-3 py-1 bg-green-100 text-green-700 rounded-full text-sm font-medium"><CheckCircle className="w-4 h-4" />Confirmed</span>;
      case 'CONVERTED':
        return <span className="inline-flex items-center gap-1 px-3 py-1 bg-gray-100 text-gray-700 rounded-full text-sm font-medium"><Package className="w-4 h-4" />Purchased</span>;
      default:
        return <span className="inline-flex items-center gap-1 px-3 py-1 bg-gray-100 text-gray-700 rounded-full text-sm font-medium">{status}</span>;
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-100 to-slate-200">
      {/* Header */}
      <div className="bg-gradient-to-r from-blue-600 to-blue-700 text-white shadow-lg">
        <div className="max-w-7xl mx-auto px-4 py-6">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-bold">Store Dashboard</h1>
              <p className="text-blue-100 mt-1">Manage customer reservations</p>
            </div>
            <div className="flex items-center gap-4">
              <select
                value={store}
                onChange={(e) => {
                  setStore(e.target.value);
                  localStorage.setItem('ey_store_location', e.target.value);
                }}
                className="px-4 py-2 rounded-lg bg-blue-600 border border-blue-500 text-white font-medium"
              >
                <option value="STORE_MUMBAI">Mumbai Store</option>
                <option value="STORE_DELHI">Delhi Store</option>
                <option value="STORE_BANGALORE">Bangalore Store</option>
                <option value="STORE_PUNE">Pune Store</option>
              </select>
              <button
                onClick={handleLogout}
                className="flex items-center gap-2 px-4 py-2 bg-red-500 hover:bg-red-600 rounded-lg font-medium transition-colors"
              >
                <LogOut className="w-4 h-4" />
                Logout
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="max-w-7xl mx-auto px-4 py-8">
        {/* Status Overview */}
        <div className="grid grid-cols-3 gap-4 mb-8">
          <div className="bg-white rounded-lg shadow p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-gray-500 text-sm">Incoming Reservations</p>
                <p className="text-3xl font-bold text-blue-600">
                  {reservations.filter((r) => r.status === 'ACTIVE').length}
                </p>
              </div>
              <Clock className="w-12 h-12 text-blue-200" />
            </div>
          </div>

          <div className="bg-white rounded-lg shadow p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-gray-500 text-sm">Confirmed</p>
                <p className="text-3xl font-bold text-green-600">
                  {reservations.filter((r) => r.status === 'CONFIRMED').length}
                </p>
              </div>
              <CheckCircle className="w-12 h-12 text-green-200" />
            </div>
          </div>

          <div className="bg-white rounded-lg shadow p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-gray-500 text-sm">Converted to Sales</p>
                <p className="text-3xl font-bold text-purple-600">
                  {reservations.filter((r) => r.status === 'CONVERTED').length}
                </p>
              </div>
              <Package className="w-12 h-12 text-purple-200" />
            </div>
          </div>
        </div>

        {/* Error Message */}
        {error && (
          <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-lg flex items-center gap-3">
            <AlertCircle className="w-5 h-5 text-red-600" />
            <p className="text-red-700">{error}</p>
            <button
              onClick={loadReservations}
              className="ml-auto px-4 py-1 bg-red-600 text-white rounded text-sm font-medium hover:bg-red-700"
            >
              Retry
            </button>
          </div>
        )}

        {/* Reservations List */}
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <div className="px-6 py-4 bg-gray-50 border-b">
            <h2 className="text-lg font-semibold text-gray-800">Incoming Reservations</h2>
            <p className="text-sm text-gray-500 mt-1">
              {loading ? 'Loading...' : `${reservations.length} total reservations`}
            </p>
          </div>

          {loading ? (
            <div className="p-12 text-center">
              <div className="inline-block animate-spin rounded-full h-8 w-8 border border-blue-600 border-t-transparent"></div>
              <p className="text-gray-500 mt-2">Loading reservations...</p>
            </div>
          ) : reservations.length === 0 ? (
            <div className="p-12 text-center">
              <Package className="w-16 h-16 text-gray-300 mx-auto mb-4" />
              <p className="text-gray-500 text-lg">No reservations for {store}</p>
              <p className="text-gray-400 text-sm mt-1">Reservations will appear here when customers make them</p>
            </div>
          ) : (
            <div className="divide-y">
              {reservations.map((res) => (
                <div
                  key={res.reservation_id}
                  className={`p-6 border-l-4 transition-colors ${
                    res.status === 'ACTIVE' ? 'border-l-blue-600' : 'border-l-gray-300'
                  } hover:bg-gray-50`}
                >
                  <div className="grid grid-cols-4 gap-6">
                    {/* Reservation Info */}
                    <div>
                      <p className="text-xs text-gray-500 uppercase tracking-wider">Reservation ID</p>
                      <p className="text-lg font-bold text-gray-900 mt-1">{res.reservation_id}</p>
                      <p className="text-xs text-gray-500 mt-2">
                        Created: {formatDate(res.created_at)} {formatTime(res.created_at)}
                      </p>
                    </div>

                    {/* Product & Customer Info */}
                    <div>
                      <p className="text-xs text-gray-500 uppercase tracking-wider">Expires In</p>
                      <p className={`text-lg font-bold mt-1 ${
                        new Date(res.expires_at) - currentTime < 3600000 ? 'text-red-600' : 'text-green-600'
                      }`}>
                        {getTimeRemaining(res.expires_at)}
                      </p>
                      <p className="text-xs text-gray-500 mt-2">
                        Until: {formatDate(res.expires_at)} {formatTime(res.expires_at)}
                      </p>
                    </div>

                    {/* Product Details */}
                    <div>
                      <p className="text-xs text-gray-500 uppercase tracking-wider">Product</p>
                      <p className="text-sm font-medium text-gray-900 mt-1">SKU: {res.sku}</p>
                      <p className="text-sm text-gray-600">Qty: {res.quantity}</p>
                      {res.customer_context_summary && (
                        <div className="mt-3 p-3 bg-blue-50 rounded border border-blue-200">
                          <p className="text-xs font-medium text-blue-900 mb-1">Customer Context</p>
                          <p className="text-xs text-blue-800 leading-relaxed">{res.customer_context_summary}</p>
                        </div>
                      )}
                    </div>

                    {/* Status & Actions */}
                    <div className="flex flex-col justify-between">
                      <div>
                        <p className="text-xs text-gray-500 uppercase tracking-wider mb-2">Status</p>
                        {getStatusBadge(res.status)}
                      </div>

                      {res.status === 'ACTIVE' && (
                        <div className="flex flex-col gap-2 mt-4">
                          <button
                            onClick={() => handleConfirmReservation(res.reservation_id)}
                            disabled={confirmingId === res.reservation_id}
                            className="px-4 py-2 bg-green-600 text-white rounded font-medium hover:bg-green-700 disabled:opacity-50 text-sm transition-colors"
                          >
                            {confirmingId === res.reservation_id ? 'Confirming...' : 'Confirm - Kept Aside'}
                          </button>
                        </div>
                      )}

                      {(res.status === 'CONFIRMED' || res.status === 'ACTIVE') && (
                        <div className="flex flex-col gap-2 mt-2">
                          <button
                            onClick={() => handleConvertToSale(res.reservation_id)}
                            disabled={convertingId === res.reservation_id}
                            className="px-4 py-2 bg-purple-600 text-white rounded font-medium hover:bg-purple-700 disabled:opacity-50 text-sm transition-colors"
                          >
                            {convertingId === res.reservation_id ? 'Converting...' : 'Convert to Purchase'}
                          </button>
                        </div>
                      )}

                      {res.status === 'CONVERTED' && (
                        <div className="text-xs text-gray-500 mt-2">
                          Converted: {formatTime(res.converted_at)}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="mt-8 text-center text-gray-500 text-sm">
          <p>Store Dashboard • Last updated: {currentTime.toLocaleTimeString('en-IN')}</p>
        </div>
      </div>
    </div>
  );
};

export default AdminDashboard;

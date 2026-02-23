import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { User, Phone, MapPin, Award, TrendingUp, Calendar } from 'lucide-react';
import Navbar from '@/components/Navbar.jsx';
import QRAuth from '@/components/QRAuth.jsx';
import sessionStore from '@/lib/session';
import { getTierInfo } from '../../services/loyaltyService';

const ProfilePage = () => {
  const navigate = useNavigate();
  const [profile, setProfile] = useState(null);
  const [phone, setPhone] = useState('');
  const [realLoyaltyData, setRealLoyaltyData] = useState(null);
  const [loyaltyLoading, setLoyaltyLoading] = useState(false);

  const fetchRealLoyaltyData = async (customerId) => {
    if (!customerId) return;
    
    setLoyaltyLoading(true);
    try {
      const loyaltyInfo = await getTierInfo(customerId);
      setRealLoyaltyData(loyaltyInfo);
    } catch (error) {
      console.error('Failed to fetch real loyalty data:', error);
      // Keep the existing profile data as fallback
    } finally {
      setLoyaltyLoading(false);
    }
  };

  useEffect(() => {
    // Load profile from session store
    const storedProfile = sessionStore.getProfile();
    const storedPhone = sessionStore.getPhone();

    if (!storedProfile || !storedPhone) {
      // Not logged in - redirect to login
      navigate('/login', { state: { redirectTo: '/profile' } });
      return;
    }

    setProfile(storedProfile);
    setPhone(storedPhone);
    
    // Fetch real loyalty data
    const customerId = storedProfile.customer_id || storedProfile.customerId;
    if (customerId) {
      fetchRealLoyaltyData(customerId);
    }
  }, [navigate]);

  if (!profile) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-orange-50 via-yellow-50 to-red-50">
        <Navbar />
        <div className="pt-32 flex justify-center">
          <p className="text-gray-600">Loading profile...</p>
        </div>
      </div>
    );
  }

  const {
    name = 'Guest',
    customer_id = '--',
    customerId,
    age,
    gender,
    city,
    loyalty_tier = 'Bronze',
    loyaltyTier,
    loyalty_points = 0,
    loyaltyPoints,
    total_spend = 0,
    totalSpend,
    items_purchased = 0,
    itemsPurchased,
    average_rating = 0,
    averageRating,
    days_since_last_purchase,
    daysSinceLastPurchase,
    building_name,
    buildingName,
    address_landmark,
    addressLandmark,
  } = profile;

  const displayCustomerId = customer_id || customerId || '--';
  const displayLoyaltyTier = realLoyaltyData?.tier || profile?.loyalty_tier || profile?.loyaltyTier || 'Bronze';
  const displayLoyaltyPoints = realLoyaltyData?.points ?? profile?.loyalty_points ?? profile?.loyaltyPoints ?? 0;
  const displayTotalSpend = total_spend || totalSpend || 0;
  const displayItemsPurchased = items_purchased || itemsPurchased || 0;
  const displayAverageRating = average_rating || averageRating || 0;
  const displayDaysSinceLastPurchase = days_since_last_purchase || daysSinceLastPurchase || '--';
  const displayBuildingName = building_name || buildingName || '';
  const displayAddressLandmark = address_landmark || addressLandmark || '';

  return (
    <div className="min-h-screen bg-gradient-to-br from-orange-50 via-yellow-50 to-red-50">
      <Navbar />
      
      <div className="pt-24 pb-12 px-4 max-w-5xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-4xl font-bold text-red-700 mb-2">My Profile</h1>
          <p className="text-gray-600">Manage your account and preferences</p>
        </div>

        <div className="grid lg:grid-cols-3 gap-6">
          {/* Left Column - Profile Info */}
          <div className="lg:col-span-2 space-y-6">
            {/* Basic Info Card */}
            <div className="bg-white/95 backdrop-blur rounded-2xl shadow-lg border border-red-100 p-6">
              <div className="flex items-center gap-3 mb-6">
                <div className="w-12 h-12 bg-gradient-to-r from-red-600 to-orange-500 rounded-full flex items-center justify-center">
                  <User className="w-6 h-6 text-white" />
                </div>
                <div>
                  <h2 className="text-xl font-semibold text-red-700">Basic Information</h2>
                  <p className="text-sm text-gray-600">Your personal details</p>
                </div>
              </div>

              <div className="grid md:grid-cols-2 gap-4">
                <div className="p-4 bg-red-50 rounded-lg">
                  <p className="text-xs text-gray-600 mb-1">Full Name</p>
                  <p className="font-semibold text-red-900">{name}</p>
                </div>
                <div className="p-4 bg-red-50 rounded-lg">
                  <p className="text-xs text-gray-600 mb-1">Customer ID</p>
                  <p className="font-semibold text-red-900">{displayCustomerId}</p>
                </div>
                <div className="p-4 bg-red-50 rounded-lg">
                  <div className="flex items-center gap-2 mb-1">
                    <Phone className="w-3 h-3 text-gray-600" />
                    <p className="text-xs text-gray-600">Phone Number</p>
                  </div>
                  <p className="font-semibold text-red-900">{phone}</p>
                </div>
                {age && (
                  <div className="p-4 bg-red-50 rounded-lg">
                    <p className="text-xs text-gray-600 mb-1">Age</p>
                    <p className="font-semibold text-red-900">{age} years</p>
                  </div>
                )}
                {gender && (
                  <div className="p-4 bg-red-50 rounded-lg">
                    <p className="text-xs text-gray-600 mb-1">Gender</p>
                    <p className="font-semibold text-red-900">{gender}</p>
                  </div>
                )}
                {city && (
                  <div className="p-4 bg-red-50 rounded-lg">
                    <div className="flex items-center gap-2 mb-1">
                      <MapPin className="w-3 h-3 text-gray-600" />
                      <p className="text-xs text-gray-600">City</p>
                    </div>
                    <p className="font-semibold text-red-900">{city}</p>
                  </div>
                )}
              </div>

              {(displayBuildingName || displayAddressLandmark) && (
                <div className="mt-4 p-4 bg-orange-50 rounded-lg border border-orange-200">
                  <p className="text-xs text-gray-600 mb-2">Address Details</p>
                  {displayBuildingName && (
                    <p className="text-sm text-gray-700">Building: <span className="font-medium">{displayBuildingName}</span></p>
                  )}
                  {displayAddressLandmark && (
                    <p className="text-sm text-gray-700">Landmark: <span className="font-medium">{displayAddressLandmark}</span></p>
                  )}
                </div>
              )}
            </div>

            {/* Purchase Stats Card */}
            <div className="bg-white/95 backdrop-blur rounded-2xl shadow-lg border border-red-100 p-6">
              <div className="flex items-center gap-3 mb-6">
                <div className="w-12 h-12 bg-gradient-to-r from-orange-500 to-yellow-500 rounded-full flex items-center justify-center">
                  <TrendingUp className="w-6 h-6 text-white" />
                </div>
                <div>
                  <h2 className="text-xl font-semibold text-red-700">Purchase Statistics</h2>
                  <p className="text-sm text-gray-600">Your shopping activity</p>
                </div>
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div className="p-4 bg-gradient-to-br from-red-50 to-orange-50 rounded-lg text-center">
                  <p className="text-2xl font-bold text-red-700">₹{Number(displayTotalSpend).toFixed(2)}</p>
                  <p className="text-xs text-gray-600 mt-1">Total Spend</p>
                </div>
                <div className="p-4 bg-gradient-to-br from-orange-50 to-yellow-50 rounded-lg text-center">
                  <p className="text-2xl font-bold text-orange-700">{displayItemsPurchased}</p>
                  <p className="text-xs text-gray-600 mt-1">Items Purchased</p>
                </div>
                <div className="p-4 bg-gradient-to-br from-yellow-50 to-red-50 rounded-lg text-center">
                  <p className="text-2xl font-bold text-yellow-700">{Number(displayAverageRating).toFixed(1)}</p>
                  <p className="text-xs text-gray-600 mt-1">Avg Rating</p>
                </div>
                <div className="p-4 bg-gradient-to-br from-red-50 to-pink-50 rounded-lg text-center flex flex-col items-center justify-center">
                  <Calendar className="w-5 h-5 text-gray-600 mb-1" />
                  <p className="text-lg font-bold text-red-700">{displayDaysSinceLastPurchase}</p>
                  <p className="text-xs text-gray-600">Days Since Last</p>
                </div>
              </div>
            </div>
          </div>

          {/* Right Column - Loyalty & QR */}
          <div className="space-y-6">
            {/* Loyalty Card */}
            <div className="bg-gradient-to-br from-red-600 to-orange-500 rounded-2xl shadow-lg p-6 text-white">
              <div className="flex items-center gap-3 mb-4">
                <Award className="w-8 h-8" />
                <div>
                  <h3 className="text-lg font-semibold">Loyalty Status</h3>
                  <p className="text-sm text-orange-100">
                    {loyaltyLoading ? 'Updating...' : 'Real-time Rewards & Benefits'}
                  </p>
                </div>
              </div>
              
              <div className="space-y-4">
                <div className="bg-white/10 backdrop-blur rounded-lg p-4">
                  <p className="text-sm text-orange-100 mb-1">Current Tier</p>
                  <p className="text-2xl font-bold">
                    {loyaltyLoading ? '...' : displayLoyaltyTier}
                  </p>
                </div>
                
                <div className="bg-white/10 backdrop-blur rounded-lg p-4">
                  <p className="text-sm text-orange-100 mb-1">Loyalty Points</p>
                  <p className="text-2xl font-bold">
                    {loyaltyLoading ? '...' : displayLoyaltyPoints.toLocaleString()}
                  </p>
                </div>
                
                {realLoyaltyData && realLoyaltyData.next_tier && (
                  <div className="bg-white/10 backdrop-blur rounded-lg p-4">
                    <p className="text-sm text-orange-100 mb-1">Next Tier: {realLoyaltyData.next_tier}</p>
                    <p className="text-sm text-orange-200">
                      {realLoyaltyData.points_to_next_tier?.toLocaleString() || 0} points to go
                    </p>
                  </div>
                )}
              </div>

              <button
                onClick={() => navigate('/orders')}
                className="mt-4 w-full bg-white text-red-600 py-2 rounded-lg font-semibold hover:bg-orange-50 transition"
              >
                View Orders
              </button>
            </div>

            {/* QR Code Card */}
            <div className="bg-white/95 backdrop-blur rounded-2xl shadow-lg border border-red-100 p-6">
              <h3 className="text-lg font-semibold text-red-700 mb-2">Kiosk Access</h3>
              <p className="text-sm text-gray-600 mb-4">
                Generate a QR code to quickly login on kiosk devices
              </p>
              
              <QRAuth />

              <div className="mt-4 p-3 bg-blue-50 border border-blue-200 rounded-lg">
                <p className="text-xs text-blue-800">
                  <strong>Tip:</strong> Use this QR code at in-store kiosks for instant authentication without typing.
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ProfilePage;

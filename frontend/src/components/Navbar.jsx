import { useNavigate } from 'react-router-dom';
import { ShoppingCart, User, Heart } from 'lucide-react';
import { useCart } from '@/contexts/CartContext.jsx';
import { useWishlist } from '@/contexts/WishlistContext.jsx';
import { useRef, useState, useEffect } from 'react';
import sessionStore from '@/lib/session';
import authService from '@/services/authService';

const Navbar = () => {
  const navigate = useNavigate();
  const { getCartCount } = useCart();
  const { items: wishlistItems } = useWishlist();
  const [profile, setProfile] = useState(() => sessionStore.getProfile());
  const [customerPhone, setCustomerPhone] = useState(() => sessionStore.getPhone());
  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  const profileButtonRef = useRef(null);
  const profileMenuRef = useRef(null);

  const displayName = profile?.name || 'Guest';
  const customerId = profile?.customer_id || profile?.customerId || '--';
  const loyaltyTier = profile?.loyalty_tier || profile?.loyaltyTier || 'Bronze';
  const loyaltyPoints = profile?.loyalty_points || profile?.loyaltyPoints || '0';
  const displayCity = profile?.city || '';

  useEffect(() => {
    const syncProfile = () => {
      setProfile(sessionStore.getProfile());
      setCustomerPhone(sessionStore.getPhone());
    };
    syncProfile();
    const handleStorage = (event) => {
      if (!event.key || event.key.startsWith('ey_session_')) {
        syncProfile();
      }
    };
    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, []);

  useEffect(() => {
    if (!profileMenuOpen) return;
    const handleClickAway = (event) => {
      if (
        profileButtonRef.current &&
        profileMenuRef.current &&
        !profileButtonRef.current.contains(event.target) &&
        !profileMenuRef.current.contains(event.target)
      ) {
        setProfileMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickAway);
    return () => document.removeEventListener('mousedown', handleClickAway);
  }, [profileMenuOpen]);

  const handleLogout = async () => {
    const sessionToken = sessionStore.getSessionToken();
    
    // Close menu
    setProfileMenuOpen(false);
    
    // Clear local session data immediately
    sessionStore.clearAll();
    
    // Call backend logout endpoint if we have a session token
    if (sessionToken) {
      try {
        await authService.logout(sessionToken);
      } catch (error) {
        console.error('Logout API call failed:', error);
        // Continue with logout even if API fails
      }
    }
    
    // Navigate to login page
    navigate('/login');
  };

  return (
    <nav className="fixed top-0 left-0 right-0 w-full z-50 bg-gradient-to-r from-red-600 to-orange-600 backdrop-blur-sm shadow-sm">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-20">
          {/* Left - Logo and Navigation */}
          <div className="flex items-center space-x-8 lg:space-x-12">
            {/* Logo */}
            <button 
              onClick={() => navigate('/')}
              className="flex flex-col items-center flex-shrink-0 hover:opacity-90 transition"
            >
              <div className="w-10 h-10 sm:w-12 sm:h-12 border-2 border-yellow-300 flex items-center justify-center mb-1">
                <div
                  className="w-5 h-5 sm:w-6 sm:h-6 border border-yellow-300"
                  style={{
                    clipPath: "polygon(50% 0%, 100% 50%, 50% 100%, 0% 50%)",
                  }}
                ></div>
              </div>
              <span className="text-xs sm:text-sm font-semibold tracking-widest text-yellow-100">
                EDGE
              </span>
            </button>

            {/* Desktop Navigation */}
            <div className="hidden lg:flex items-center space-x-8">
              <button
                onClick={() => navigate('/')}
                className="text-xs font-medium text-yellow-100 hover:text-yellow-200 transition-colors tracking-wider cursor-pointer"
              >
                HOME
              </button>
              <a
                href="/#categories"
                className="text-xs font-medium text-yellow-100 hover:text-yellow-200 transition-colors tracking-wider"
              >
                FEATURES
              </a>
              <button
                onClick={() => navigate('/products')}
                className="text-xs font-medium text-yellow-100 hover:text-yellow-200 transition-colors tracking-wider cursor-pointer"
              >
                PRODUCTS
              </button>
              <a
                href="/#contact"
                className="text-xs font-medium text-yellow-100 hover:text-yellow-200 transition-colors tracking-wider"
              >
                CONTACT
              </a>
            </div>
          </div>

          {/* Right - Auth, Cart & Wishlist */}
          <div className="hidden md:flex items-center space-x-4 relative">
            {/* Auth Section */}
            {profile ? (
              <>
                {/* Profile Button with Dropdown */}
                <button
                  ref={profileButtonRef}
                  onClick={() => setProfileMenuOpen((open) => !open)}
                  className="relative flex items-center gap-2 px-4 py-2 rounded-lg bg-white/15 hover:bg-white/20 text-yellow-100 transition-all border border-yellow-300/30"
                >
                  <User className="w-4 h-4" />
                  <span className="text-xs font-semibold tracking-wider">{displayName}</span>
                </button>
                
                {/* Direct Profile Link */}
                <button
                  onClick={() => navigate('/profile')}
                  className="text-xs font-medium text-yellow-100 hover:text-yellow-200 transition-colors tracking-wider px-3 py-2 rounded hover:bg-white/10"
                  title="My Profile"
                >
                  PROFILE
                </button>
                
                {/* Direct Logout Button */}
                <button
                  onClick={handleLogout}
                  className="text-xs font-medium text-yellow-100 hover:text-red-200 transition-colors tracking-wider px-3 py-2 rounded hover:bg-red-500/20 border border-red-300/30"
                  title="Logout"
                >
                  LOGOUT
                </button>
              </>
            ) : (
              <>
                {/* Login Button - More Prominent */}
                <button
                  onClick={() => navigate('/login')}
                  className="text-xs font-semibold text-yellow-100 hover:text-white transition-colors tracking-wider px-5 py-2 rounded-lg bg-white/15 hover:bg-white/25 border border-yellow-300/40"
                >
                  LOGIN / SIGNUP
                </button>
              </>
            )}
            
            {/* Divider */}
            <div className="h-8 w-px bg-yellow-300/30"></div>
            
            {/* Cart */}
            <button 
              onClick={() => navigate('/cart')}
              className="relative p-2 text-yellow-100 hover:text-yellow-200 transition-colors hover:bg-white/10 rounded" 
              aria-label="Shopping cart"
              title="Shopping Cart"
            >
              <ShoppingCart className="w-5 h-5" />
              {getCartCount() > 0 && (
                <span className="absolute -top-1 -right-1 w-5 h-5 bg-yellow-400 text-red-700 text-xs rounded-full flex items-center justify-center font-semibold">
                  {getCartCount()}
                </span>
              )}
            </button>

            {/* Wishlist */}
            <button
              onClick={() => navigate('/wishlist')}
              className="relative p-2 text-yellow-100 hover:text-yellow-200 transition-colors hover:bg-white/10 rounded"
              aria-label="Wishlist"
              title="Wishlist"
            >
              <Heart className="w-5 h-5" />
              {wishlistItems.length > 0 && (
                <span className="absolute -top-1 -right-1 w-5 h-5 bg-yellow-400 text-red-700 text-xs rounded-full flex items-center justify-center font-semibold">
                  {wishlistItems.length}
                </span>
              )}
            </button>

            {profile && profileMenuOpen && (
              <div
                ref={profileMenuRef}
                className="absolute right-0 top-14 w-72 bg-white text-red-900 rounded-2xl shadow-2xl border border-red-100/70 overflow-hidden z-50"
              >
                <div className="bg-gradient-to-r from-red-700 to-orange-500 px-4 py-3 text-white">
                  <p className="text-sm uppercase tracking-wide font-semibold">Account Overview</p>
                  <p className="text-lg font-bold">{displayName}</p>
                  <p className="text-xs text-orange-100/90">Customer ID: {customerId}</p>
                </div>
                <div className="px-4 py-3 space-y-3 text-sm">
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-red-800">Loyalty Tier</span>
                    <span className="text-red-600 font-semibold">{loyaltyTier}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-red-800">Loyalty Points</span>
                    <span className="text-red-600 font-semibold">{loyaltyPoints}</span>
                  </div>
                  {customerPhone && (
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-red-800">Phone</span>
                      <span className="text-red-600">{customerPhone}</span>
                    </div>
                  )}
                  {displayCity && (
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-red-800">City</span>
                      <span className="text-red-600">{displayCity}</span>
                    </div>
                  )}
                  <div className="pt-2 border-t border-red-100/40">
                    <button
                      onClick={() => { setProfileMenuOpen(false); navigate('/profile'); }}
                      className="w-full text-left px-3 py-2 rounded hover:bg-red-50"
                    >
                      My Profile
                    </button>
                    <button
                      onClick={() => { setProfileMenuOpen(false); navigate('/orders'); }}
                      className="w-full text-left px-3 py-2 rounded hover:bg-red-50"
                    >
                      Orders
                    </button>                    <button
                      onClick={() => { setProfileMenuOpen(false); navigate('/reservations'); }}
                      className="w-full text-left px-3 py-2 rounded hover:bg-red-50"
                    >
                      My Reservations
                    </button>                    <button
                      onClick={handleLogout}
                      className="w-full text-left px-3 py-2 rounded text-red-700 hover:bg-red-50 font-medium"
                    >
                      Logout
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </nav>
  );
};

export default Navbar;

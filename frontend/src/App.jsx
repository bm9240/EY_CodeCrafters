import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { CartProvider } from '@/contexts/CartContext.jsx';
import { WishlistProvider } from '@/contexts/WishlistContext.jsx';
import MainApp from './components/MainApp';
import KioskChat from './components/KioskChat';
import LandingPage from './components/pages/LandingPage';
import LoginPage from './components/pages/LoginPage';
import ProfilePage from './components/pages/ProfilePage';
import CartPage from './components/pages/CartPage';
import CheckoutPage from './components/pages/CheckoutPage';
import ProductCatalog from './components/pages/ProductCatalog';
import ProductDetail from './components/pages/ProductDetail';
import Wishlist from './components/pages/Wishlist';
import OrdersPage from './components/pages/OrdersPage';
import OrderDetailPage from './components/pages/OrderDetailPage';
import AdminDashboard from './components/pages/AdminDashboard';
import MyReservations from './components/pages/MyReservations';

function App() {
  return (
    <CartProvider>
      <WishlistProvider>
        <Router>
        <div className="min-h-screen bg-background">
          <Routes>
            <Route path="/" element={<LandingPage />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/profile" element={<ProfilePage />} />
            <Route path="/chat" element={<MainApp />} />
            <Route path="/kiosk" element={<KioskChat />} />
            <Route path="/products" element={<ProductCatalog />} />
            <Route path="/products/:sku" element={<ProductDetail />} />
            <Route path="/wishlist" element={<Wishlist />} />
            <Route path="/cart" element={<CartPage />} />
            <Route path="/checkout" element={<CheckoutPage />} />
            <Route path="/orders" element={<OrdersPage />} />
            <Route path="/orders/:id" element={<OrderDetailPage />} />
            <Route path="/reservations" element={<MyReservations />} />
            <Route path="/admin" element={<AdminDashboard />} />
          </Routes>
        </div>
        </Router>
      </WishlistProvider>
    </CartProvider>
  );
}

export default App;

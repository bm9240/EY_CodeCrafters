import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useCart } from '@/contexts/CartContext';
import { ArrowLeft, CheckCircle, AlertCircle, Loader, Minus, Plus, Trash2 } from 'lucide-react';
import sessionStore from '@/lib/session';
import salesAgentService from '@/services/salesAgentService';
import { createRazorpayOrder, verifyRazorpayPayment } from '@/services/paymentService';
import Navbar from '@/components/Navbar.jsx';

const CheckoutPage = () => {
  const navigate = useNavigate();
  const { cartItems, clearCart, getCartTotal, getCartCount, updateQuantity, removeFromCart } = useCart();
  const [isProcessing, setIsProcessing] = useState(false);
  const [paymentStatus, setPaymentStatus] = useState(null); // 'success' | 'error' | null
  const [statusMessage, setStatusMessage] = useState('');
  const [loyaltyData, setLoyaltyData] = useState(null);

  useEffect(() => {
    const fetchLoyaltyDiscount = async () => {
      const userId = String(sessionStore.getCustomerId());
      const cartTotal = parseFloat(getCartTotal());
      console.log("Fetching loyalty with userId:", userId, "cartTotal:", cartTotal);
      if (!userId || userId === 'null' || userId === 'undefined' || cartTotal <= 0) {
        console.log("Skipping loyalty fetch: invalid userId or cartTotal");
        setLoyaltyData(null);
        return;
      }
      try {
        const response = await fetch('http://localhost:8002/loyalty/calculate-discounts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: userId, cart_total: cartTotal })
        });
        if (!response.ok) {
          console.error("Loyalty API error:", response.status, response.statusText);
          const errorData = await response.json();
          console.error("Error details:", errorData);
          setLoyaltyData(null);
          return;
        }
        const data = await response.json();
        console.log("Loyalty response:", data);
        setLoyaltyData(data);
      } catch (error) {
        console.error('Failed to fetch loyalty discount:', error);
        setLoyaltyData(null);
      }
    };
    fetchLoyaltyDiscount();
  }, [cartItems, getCartTotal]);

  const formatINR = (amount) => {
    return amount.toLocaleString('en-IN', {
      style: 'currency',
      currency: 'INR',
      minimumFractionDigits: 0,
    });
  };

  const handleProceedToPayment = async () => {
    if (cartItems.length === 0) {
      setPaymentStatus('error');
      setStatusMessage('Your cart is empty. Please add items before checkout.');
      return;
    }

    const sessionToken = sessionStore.getSessionToken();
    const userId = sessionStore.getCustomerId();

    if (!sessionToken || !userId) {
      setPaymentStatus('error');
      setStatusMessage('Session expired. Please log in again.');
      setTimeout(() => navigate('/login'), 2000);
      return;
    }

    setIsProcessing(true);
    setPaymentStatus(null);
    setStatusMessage('');

    try {
      // Calculate total amount
      const totalAmount = Number(loyaltyData?.final_total || getCartTotal());
      if (!Number.isFinite(totalAmount) || totalAmount <= 0) {
        throw new Error('Invalid cart total. Please review your cart items.');
      }

      // Prepare cart data for order creation
      const cart = cartItems.map((item) => ({
        sku: item.sku,
        qty: Number(item.qty) || 1,
        unit_price: Number(item.unit_price || 0),
      }));

      const safeNotes = {};
      if (userId) safeNotes.customer_id = String(userId);
      if (sessionToken) safeNotes.session_token = String(sessionToken);
      safeNotes.source = 'web_checkout';

      // Create Razorpay order
      const orderData = {
        amount_rupees: totalAmount,
        currency: 'INR',
        notes: safeNotes,
        items: cart,
      };

      const razorpayOrder = await createRazorpayOrder(orderData);

      // Configure Razorpay checkout
      const options = {
        key: razorpayOrder.razorpay_key_id,
        amount: razorpayOrder.order.amount,
        currency: razorpayOrder.order.currency,
        name: 'EY CodeCrafters Store',
        description: `Order ${razorpayOrder.order_id}`,
        order_id: razorpayOrder.order.id,
        handler: async function (response) {
          console.log('Payment successful:', response);

          try {
            const razorpayPaymentId = response?.razorpay_payment_id;
            const razorpayOrderId = response?.razorpay_order_id;
            const canonicalOrderId = String(razorpayOrder?.order_id || '').trim();
            const amountRupees = Number(razorpayOrder?.amount_rupees);

            if (!razorpayPaymentId || !razorpayOrderId) {
              throw new Error('Razorpay did not return payment identifiers.');
            }
            if (!canonicalOrderId) {
              throw new Error('Order ID is missing for payment verification.');
            }
            if (!Number.isFinite(amountRupees) || amountRupees <= 0) {
              throw new Error('Invalid payment amount for verification.');
            }

            // Verify payment with backend
            const verificationData = {
              razorpay_payment_id: razorpayPaymentId,
              razorpay_order_id: razorpayOrderId,
              razorpay_signature: response.razorpay_signature,
              amount_rupees: amountRupees,
              user_id: userId ? String(userId) : undefined,
              order_id: canonicalOrderId,
            };

            const verificationResult = await verifyRazorpayPayment(verificationData);

            setPaymentStatus('success');
            setStatusMessage(`🎉 Payment successful! Your order ${razorpayOrder.order_id} has been placed. Thank you for your purchase! You'll receive updates on your order soon.`);
            clearCart();

            // Trigger post-payment processing by calling Sales Agent's post-payment endpoint
            try {
              console.log('Triggering post-payment processing...');
              const postPaymentResult = await salesAgentService.triggerPostPayment(
                razorpayOrder.order_id,
                userId,
                sessionToken,
                razorpayOrder.amount_rupees,
                response.razorpay_payment_id,
                verificationResult.transaction_id || response.razorpay_payment_id
              );
              console.log('Post-payment processing triggered successfully:', postPaymentResult);
            } catch (postPaymentError) {
              console.error('Failed to trigger post-payment processing:', postPaymentError);
              // Don't fail the payment for this - it's not critical
            }

            // Redirect to orders page with payment success message after 1.5 seconds
            setTimeout(() => {
              const oid = razorpayOrder.order_id || razorpayOrder.order?.id || razorpayOrder.order_id;
              navigate('/orders', { 
                state: { 
                  paymentSuccess: true,
                  message: `🎉 Payment successful! Order ID: ${oid}, Payment ID: ${response.razorpay_payment_id}. Your order is being processed.`,
                  orderId: oid,
                  paymentId: response.razorpay_payment_id
                } 
              });
            }, 1000);

          } catch (verificationError) {
            console.error('Payment verification failed:', verificationError);
            setPaymentStatus('error');
            setStatusMessage('Payment verification failed. Please contact support with your payment ID: ' + response.razorpay_payment_id);
          }
        },
        prefill: {
          name: 'Customer',
          email: 'customer@example.com',
          contact: '9999999999',
        },
        theme: {
          color: '#3399cc',
        },
        modal: {
          ondismiss: function() {
            console.log('Payment modal dismissed');
            setIsProcessing(false);
            setPaymentStatus('error');
            setStatusMessage('Payment was cancelled. Please try again.');
          }
        }
      };

      // Open Razorpay checkout
      const rzp = new window.Razorpay(options);
      rzp.open();

    } catch (error) {
      console.error('Payment initialization error:', error);
      setIsProcessing(false);
      setPaymentStatus('error');
      setStatusMessage(
        error.message || 'Failed to initialize payment. Please try again.'
      );
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-orange-50 via-yellow-50 to-red-50">
      <Navbar />
      
      {/* Header */}
      <div className="mt-20 bg-gradient-to-r from-red-600 to-orange-600 text-white shadow-md">
        <div className="max-w-4xl mx-auto px-4 py-6">
          <div className="flex items-center gap-4">
            <button
              onClick={() => navigate('/cart')}
              className="hover:bg-white/10 p-2 rounded-full transition-colors"
              disabled={isProcessing}
            >
              <ArrowLeft className="w-6 h-6" />
            </button>
            <div>
              <h1 className="text-2xl font-bold">Checkout</h1>
              <p className="text-sm text-orange-100">Review and confirm your order</p>
            </div>
          </div>
        </div>
      </div>

      {/* Checkout Content */}
      <div className="max-w-4xl mx-auto px-4 py-8">
        {cartItems.length === 0 ? (
          <div className="text-center py-16">
            <AlertCircle className="w-24 h-24 mx-auto text-orange-300 mb-4" />
            <h2 className="text-2xl font-semibold text-gray-700 mb-2">No items to checkout</h2>
            <p className="text-gray-500 mb-6">Your cart is empty!</p>
            <button
              onClick={() => navigate('/chat')}
              className="bg-gradient-to-r from-red-600 to-orange-600 text-white px-6 py-3 rounded-lg font-semibold hover:from-red-700 hover:to-orange-700 transition-all"
            >
              Start Shopping
            </button>
          </div>
        ) : (
          <div className="space-y-6">
            {/* Order Items */}
            <div className="bg-white rounded-xl shadow-md p-6">
              <h2 className="text-xl font-bold text-gray-900 mb-4">Order Items</h2>
              <div className="space-y-4">
                {cartItems.map((item) => (
                  <div key={item.id} className="flex gap-4 items-center p-4 border border-gray-200 rounded-lg">
                    {item.image && (
                      <img
                        src={item.image}
                        alt={item.name}
                        className="w-16 h-16 object-cover rounded-lg"
                        onError={(e) => (e.target.style.display = 'none')}
                      />
                    )}
                    <div className="flex-1">
                      <h3 className="font-semibold text-gray-900">{item.name}</h3>
                      <p className="text-sm text-gray-500">SKU: {item.sku}</p>
                      <p className="text-sm text-gray-700">
                        Price: <span className="font-semibold">{formatINR(item.unit_price)}</span>
                      </p>
                      
                      {/* Quantity Controls */}
                      <div className="flex items-center gap-4 mt-2">
                        <div className="flex items-center gap-2 border border-gray-300 rounded-lg">
                          <button
                            onClick={() => updateQuantity(item.id, item.qty - 1)}
                            className="p-1 hover:bg-gray-100 transition-colors rounded-l-lg"
                            disabled={item.qty <= 1}
                          >
                            <Minus className="w-4 h-4" />
                          </button>
                          <span className="px-3 font-semibold">{item.qty}</span>
                          <button
                            onClick={() => updateQuantity(item.id, item.qty + 1)}
                            className="p-1 hover:bg-gray-100 transition-colors rounded-r-lg"
                          >
                            <Plus className="w-4 h-4" />
                          </button>
                        </div>

                        <button
                          onClick={() => removeFromCart(item.id)}
                          className="text-red-600 hover:text-red-700 p-1 rounded-lg hover:bg-red-50 transition-colors"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                    <div className="text-right">
                      <p className="text-lg font-bold text-gray-900">
                        {formatINR(item.unit_price * item.qty)}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Order Summary */}
            <div className="bg-white rounded-xl shadow-md p-6">
              <h2 className="text-xl font-bold text-gray-900 mb-4">Order Summary</h2>
              
              <div className="space-y-3 mb-6">
                <div className="flex justify-between text-gray-700">
                  <span>Subtotal ({getCartCount()} items)</span>
                  <span className="font-semibold">{formatINR(loyaltyData?.original_total || getCartTotal())}</span>
                </div>
                {loyaltyData && loyaltyData.total_discount_amount > 0 && (
                  <div className="flex justify-between text-gray-700">
                    <span>Loyalty Discount</span>
                    <span className="font-semibold text-green-600">-{formatINR(loyaltyData.total_discount_amount)}</span>
                  </div>
                )}
                <div className="border-t border-gray-200 pt-3">
                  <div className="flex justify-between text-lg font-bold text-gray-900">
                    <span>Total Amount</span>
                    <span className="text-green-600">{formatINR(loyaltyData?.final_total || getCartTotal())}</span>
                  </div>
                </div>
              </div>

              {/* Payment Status Messages */}
              {paymentStatus === 'success' && (
                <div className="mb-6 p-4 bg-green-50 border border-green-200 rounded-lg flex items-start gap-3">
                  <CheckCircle className="w-6 h-6 text-green-600 flex-shrink-0 mt-0.5" />
                  <div>
                    <h3 className="font-semibold text-green-900">Payment Successful!</h3>
                    <p className="text-sm text-green-700 mt-1">{statusMessage}</p>
                    <p className="text-xs text-green-600 mt-2">Redirecting to chat...</p>
                  </div>
                </div>
              )}

              {paymentStatus === 'error' && (
                <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-lg flex items-start gap-3">
                  <AlertCircle className="w-6 h-6 text-red-600 flex-shrink-0 mt-0.5" />
                  <div>
                    <h3 className="font-semibold text-red-900">Payment Failed</h3>
                    <p className="text-sm text-red-700 mt-1">{statusMessage}</p>
                  </div>
                </div>
              )}

              {/* Proceed to Payment Button */}
              <button
                onClick={handleProceedToPayment}
                disabled={isProcessing || paymentStatus === 'success'}
                className={`w-full py-4 rounded-lg font-bold text-lg transition-all shadow-lg flex items-center justify-center gap-3 ${
                  isProcessing || paymentStatus === 'success'
                    ? 'bg-gray-400 cursor-not-allowed'
                    : 'bg-gradient-to-r from-red-600 to-orange-600 text-white hover:from-red-700 hover:to-orange-700 hover:shadow-xl'
                }`}
              >
                {isProcessing && <Loader className="w-6 h-6 animate-spin" />}
                {isProcessing
                  ? 'Processing Payment...'
                  : paymentStatus === 'success'
                  ? 'Payment Complete'
                  : 'Pay with Razorpay'}
              </button>

              {/* Info Text */}
              <p className="text-xs text-gray-500 text-center mt-4">
                Payment will be processed through our secure Sales Agent
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default CheckoutPage;

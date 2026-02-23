import { useState, useRef, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';

import {
  Send,
  Check,
  CheckCheck,
  Phone,
  Video,
  Mic,
  MicOff,
  User,
  X,
  CreditCard,
  LifeBuoy,
  CheckCircle,
  ImagePlus,
  ShoppingCart,
  Package,
} from 'lucide-react';

import { useCart } from '@/contexts/CartContext.jsx';
import { resolveImageUrl } from '@/lib/utils.js';

import {
  createRazorpayOrder,
  verifyRazorpayPayment,
  getNextOrderId,
} from '../services/paymentService';

import { getTierInfo } from '../services/loyaltyService';
import { setDeliveryWindow } from '../services/fulfillmentService';
import API_ENDPOINTS from '../config/api';
import sessionStore from '../lib/session';
import salesAgentService from '../services/salesAgentService';

import {
  getReturnReasons,
  getIssueTypes,
  initiateReturn,
  initiateExchange,
  raiseComplaint,
  submitFeedback,
  registerPostPurchaseOrder,
} from '../services/postPurchaseService';

const SESSION_API = API_ENDPOINTS.SESSION_MANAGER;
const SALES_API = API_ENDPOINTS.SALES_AGENT;

const parsePriceToNumber = (value) => {
  if (value === null || value === undefined) {
    return null;
  }

  const numeric = parseFloat(String(value).replace(/[^0-9.]/g, ''));
  if (!Number.isFinite(numeric)) {
    return null;
  }

  return Number(Math.round(numeric * 100) / 100);
};

const formatINR = (amount) => {
  if (!Number.isFinite(amount)) {
    return '₹0';
  }

  return amount.toLocaleString('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: amount % 1 === 0 ? 0 : 2,
  });
};

const buildCheckoutOrderId = (sku = '') => {
  const safeSku = sku ? String(sku).replace(/[^A-Za-z0-9]/g, '').toUpperCase() : 'ITEM';
  return `ORDER-${safeSku}-${Date.now()}`;
};

const fetchCanonicalOrderId = async (sku = '') => {
  try {
    const reservedId = await getNextOrderId();
    if (reservedId) {
      return reservedId;
    }
  } catch (error) {
    console.warn('Falling back to local order id generation:', error);
  }
  return buildCheckoutOrderId(sku);
};

const extractCardAttribute = (card, key) => {
  if (!card) return '';
  if (card[key]) return card[key];

  const { attributes } = card;
  if (!attributes) return '';

  if (typeof attributes === 'object' && attributes !== null && attributes[key]) {
    return attributes[key];
  }

  if (typeof attributes === 'string') {
    try {
      const parsed = JSON.parse(attributes);
      if (parsed && typeof parsed === 'object' && parsed[key]) {
        return parsed[key];
      }
    } catch (error) {
      try {
        const normalized = attributes.replace(/'/g, '"');
        const parsed = JSON.parse(normalized);
        if (parsed && typeof parsed === 'object' && parsed[key]) {
          return parsed[key];
        }
      } catch (secondaryError) {
        console.warn('Failed to parse card attributes', secondaryError);
      }
    }
  }

  return '';
};

const SUPPORT_TITLES = {
  menu: 'Post-Purchase Support',
  return: 'Start a Return',
  exchange: 'Request an Exchange',
  complaint: 'Raise a Complaint',
  feedback: 'Share Feedback',
};

const Chat = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { addToCart, clearCart, cartItems } = useCart();
  const [toast, setToast] = useState({ show: false, message: '' });

  // Session state
  const [sessionInfo, setSessionInfo] = useState(null);
  const [sessionToken, setSessionToken] = useState(null);
  const [customerProfile, setCustomerProfile] = useState(() => sessionStore.getProfile());
  const [isInitializing, setIsInitializing] = useState(true);
  const [isLoadingSession, setIsLoadingSession] = useState(false);
  const [loyaltyPoints, setLoyaltyPoints] = useState(0);
  const [loyaltyTier, setLoyaltyTier] = useState('Bronze');
  const [userId, setUserId] = useState(null);

  const [messages, setMessages] = useState([]);
  const [inputText, setInputText] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [expandedMessages, setExpandedMessages] = useState(new Set());
  const [expandedCards, setExpandedCards] = useState(new Set());
  const [isRazorpayReady, setIsRazorpayReady] = useState(false);
  const [isPaymentProcessing, setIsPaymentProcessing] = useState(false);
  const [pendingCheckoutItem, setPendingCheckoutItem] = useState(null);
  const [awaitingConfirmation, setAwaitingConfirmation] = useState(false);
  const [lastCompletedOrder, setLastCompletedOrder] = useState(null);
  const [showSupportPanel, setShowSupportPanel] = useState(false);
  const [activeSupportMode, setActiveSupportMode] = useState(null);
  const [supportForm, setSupportForm] = useState({});
  const [supportContext, setSupportContext] = useState({});
  const [returnReasons, setReturnReasons] = useState([]);
  const [issueTypes, setIssueTypes] = useState([]);
  const [panelInitializing, setPanelInitializing] = useState(false);
  const [supportLoading, setSupportLoading] = useState(false);
  const [supportResult, setSupportResult] = useState(null);
  const [supportError, setSupportError] = useState('');
  const [showAddressModal, setShowAddressModal] = useState(false);
  const [addressForm, setAddressForm] = useState({ city: '', landmark: '', building: '' });
  const [addressError, setAddressError] = useState('');
  const [savedAddress, setSavedAddress] = useState(null);
  const [pendingPaymentDetails, setPendingPaymentDetails] = useState(null);
  const [showDeliveryModal, setShowDeliveryModal] = useState(false);
  const [deliveryOrderId, setDeliveryOrderId] = useState(null);
  const [selectedDeliveryWindow, setSelectedDeliveryWindow] = useState(null);
  const [lastTrackedOrderId, setLastTrackedOrderId] = useState(null);
  const [lastOrderStatus, setLastOrderStatus] = useState(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [isImageSearching, setIsImageSearching] = useState(false);
  const messagesEndRef = useRef(null);
  const recognitionRef = useRef(null);
  const transcriptRef = useRef('');
  const paymentInFlightRef = useRef(false);
  const statusPollingRef = useRef(null);
  const websocketRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const imageInputRef = useRef(null);

  const supportActions = [
    { key: 'return', label: 'Start Return', caption: 'Schedule pickup and refund', emoji: '📦' },
    { key: 'exchange', label: 'Exchange Size', caption: 'Swap for a better fit', emoji: '🔁' },
    { key: 'complaint', label: 'Raise Complaint', caption: 'Escalate delivery or product issues', emoji: '📝' },
    { key: 'feedback', label: 'Share Feedback', caption: 'Tell us how we did', emoji: '💬' },
  ];

  useEffect(() => {
    const storedAddress = sessionStore.getAddress?.();
    if (storedAddress) {
      setSavedAddress(storedAddress);
      setAddressForm({
        city: storedAddress.city || '',
        landmark: storedAddress.landmark || '',
        building: storedAddress.building || '',
      });
    }
  }, []);

  // Track if payment message was already handled
  const paymentMessageHandledRef = useRef(false);
  // Track if post-purchase/stylist requests were already handled
  const postPurchaseHandledRef = useRef(false);
  const stylistHandledRef = useRef(false);

  // Payment success message is now handled directly in startOrRestoreSession()
  // to ensure it's added AFTER session messages are restored

  useEffect(() => {
    if (!sessionToken) {
      return undefined;
    }

    const baseUrl = API_ENDPOINTS.FULFILLMENT;
    const wsUrl = `${baseUrl.replace(/^http/, 'ws')}/ws/fulfillment`;

    const connectWebSocket = () => {
      if (websocketRef.current) {
        websocketRef.current.close();
      }

      const socket = new WebSocket(wsUrl);
      websocketRef.current = socket;

      socket.onopen = () => {
        setWsConnected(true);
      };

      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload?.type !== 'delivery_update') {
            return;
          }

          const orderId = payload.order_id;
          const fulfillment = payload.fulfillment || {};
          const rawStatus = fulfillment.current_status || 'UNKNOWN';
          const status = String(rawStatus).replace('FulfillmentStatus.', '').toUpperCase();

          const statusMessages = {
            PROCESSING: '📦 Your order is being carefully prepared with utmost care.\n\nOur team is picking and packing your items to ensure they arrive in perfect condition!',
            PACKED: '✅ Great news! Your order has been packed and sealed.\n\nIt\'s now in our logistics network and will be picked up soon for shipment!',
            SHIPPED: '🚚 Your order is on the move!\n\nYour package is now with our carrier and heading towards your doorstep. Exciting times ahead! 📍',
            OUT_FOR_DELIVERY: '🏃🎯 Your delivery partner is on the way!\n\nYour order is out for delivery today. Please keep your phone handy for the delivery partner\'s call.',
            DELIVERED: '🎉🌟 Success! Your order has been delivered!\n\nThank you for shopping with us! We hope you love your new purchase. Don\'t forget to share your photos and feedback with our community! 💝',
          };

          let responseText = `Order ${orderId}:\n\n${statusMessages[status] || `Status: ${status}`}`;

          if (status === 'OUT_FOR_DELIVERY') {
            if (fulfillment.delivery_boy_name) {
              responseText += `\n\n👤 Your Delivery Partner: ${fulfillment.delivery_boy_name}`;
            }
            if (fulfillment.delivery_boy_phone) {
              responseText += `\n📱 Contact: ${fulfillment.delivery_boy_phone} (Ready to assist)`;
            }
            if (fulfillment.delivery_otp) {
              responseText += `\n🔐 Verification OTP: ${fulfillment.delivery_otp}\n\n💡 Share this OTP only with your delivery partner for verification.`;
            }
          } else if (status === 'DELIVERED') {
            responseText += `\n\n⭐ We'd love your feedback! Rate and review your purchase.`;
          }

          appendAgentMessage(responseText);
          setLastTrackedOrderId(orderId);
          setLastOrderStatus(status);
        } catch (error) {
          console.error('Failed to parse fulfillment websocket message:', error);
        }
      };

      socket.onerror = (error) => {
        console.error('Fulfillment websocket error:', error);
      };

      socket.onclose = () => {
        setWsConnected(false);
        if (reconnectTimeoutRef.current) {
          clearTimeout(reconnectTimeoutRef.current);
        }
        reconnectTimeoutRef.current = setTimeout(connectWebSocket, 3000);
      };
    };

    connectWebSocket();

    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (websocketRef.current) {
        websocketRef.current.close();
        websocketRef.current = null;
      }
    };
  }, [sessionToken]);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    const profile = sessionStore.getProfile();
    const phone = sessionStore.getPhone();

    if (!profile || !phone) {
      setIsInitializing(false);
      navigate('/login');
      return;
    }

    setCustomerProfile(profile);
    if (profile.customer_id || profile.customerId) {
      setUserId(profile.customer_id || profile.customerId);
    }

    startOrRestoreSession();
  }, [navigate]);

  // Effect to fetch stylist suggestions when a message has stylistPending: true
  useEffect(() => {
    const pendingMessage = messages.find(msg => msg.stylistPending && msg.stylistProduct);
    if (!pendingMessage) return;

    const fetchStylistSuggestions = async () => {
      try {
        const product = pendingMessage.stylistProduct;
        const stylistPayload = {
          user_id: userId || sessionStore.getCustomerId() || 'guest',
          product_sku: product.sku || '',
          product_name: product.name || 'Purchased item',
          category: product.category || 'Apparel',
          color: product.color || '',
          brand: product.brand || '',
        };

        const stylistResponse = await salesAgentService.getStylistSuggestions(stylistPayload);
        const stylistBundle = stylistResponse?.recommendations || {};
        const recommendedProducts = Array.isArray(stylistBundle?.recommended_products)
          ? stylistBundle.recommended_products
              .map((item) => ({
                sku: item?.sku || '',
                name: item?.name || '',
                reason: item?.reason || '',
                image_url: item?.image_url || item?.image || '',
                price: item?.price || 0,
                brand: item?.brand || '',
                rating: item?.rating || 0,
                personalized_reason: item?.personalized_reason || item?.reason || '',
              }))
              .filter((item) => item.sku || item.name || item.reason)
          : [];
        const stylingTips = Array.isArray(stylistBundle?.styling_tips)
          ? stylistBundle.styling_tips.filter((tip) => typeof tip === 'string' && tip.trim())
          : [];

        // Update the message with stylist recommendations
        setMessages(prev => prev.map(msg => {
          if (msg.id === pendingMessage.id) {
            return {
              ...msg,
              text: stylistResponse?.success && (recommendedProducts.length || stylingTips.length)
                ? `👗 Our stylist has styling suggestions for your ${product.name || 'purchase'}!`
                : `👗 Sorry, no styling suggestions available at this time.`,
              stylistPending: false,
              stylistRecommendations: (stylistResponse?.success && (recommendedProducts.length || stylingTips.length))
                ? {
                    purchasedProduct: stylistResponse.purchased_product || stylistPayload,
                    recommendedProducts,
                    stylingTips,
                    recommendationId: stylistResponse.recommendation_id || '',
                  }
                : null
            };
          }
          return msg;
        }));
      } catch (error) {
        console.error('Failed to fetch stylist suggestions:', error);
        setMessages(prev => prev.map(msg => {
          if (msg.id === pendingMessage.id) {
            return {
              ...msg,
              text: `👗 Sorry, couldn't fetch styling suggestions. Please try again later.`,
              stylistPending: false
            };
          }
          return msg;
        }));
      }
    };

    fetchStylistSuggestions();
  }, [messages, userId]);

  // Helper to normalize surrounding quotes from messages so we only add one pair
  const normalizeQuotes = (text) => {
    if (!text) return '';
    return String(text).replace(/^\s*["'“”]+|["'“”]+\s*$/g, '').trim();
  };

  const renderMessageText = (text, metadata = {}) => {
    if (!text) return null;
    
    // Special formatting for payment success messages
    if (metadata?.type === 'payment_success') {
      const lines = String(text).split('\n');
      return (
        <div className="space-y-2">
          {lines.map((line, idx) => {
            if (line.includes('Order ID:') || line.includes('Payment ID:') || line.includes('Amount:')) {
              const [label, value] = line.split(':');
              return (
                <div key={`payment-line-${idx}`} className="flex items-start gap-2">
                  <span className="font-semibold text-orange-600">{label}:</span>
                  <span className="font-mono text-gray-700">{value}</span>
                </div>
              );
            }
            return (
              <div key={`payment-text-${idx}`} className={`${line.trim() === '' ? 'h-2' : ''}`}>
                {line.trim() && renderMessageText(line)}
              </div>
            );
          })}
        </div>
      );
    }
    
    const parts = String(text).split(/\*\*(.*?)\*\*/g);
    return parts.map((part, index) => (
      index % 2 === 1
        ? <strong key={`bold-${index}`} className="font-semibold">{part}</strong>
        : <span key={`text-${index}`}>{part}</span>
    ));
  };

  // Loyalty Management
  const fetchLoyaltyPoints = async (user_id) => {
    try {
      console.log('🔍 Fetching loyalty points for user:', user_id);
      const result = await getTierInfo(user_id);
      console.log('📊 Loyalty data received:', result);
      
      if (result && result.points !== undefined) {
        const points = parseInt(result.points) || 0;
        const tier = result.tier || 'Bronze';
        const tierCapitalized = tier.charAt(0).toUpperCase() + tier.slice(1);
        
        console.log(`✅ Setting loyalty state - Points: ${points}, Tier: ${tierCapitalized}, Source: ${result.source || 'unknown'}`);
        
        setLoyaltyPoints(points);
        setLoyaltyTier(tierCapitalized);
      } else {
        console.warn('⚠️ No loyalty data in response, setting defaults');
        setLoyaltyPoints(0);
        setLoyaltyTier('Bronze');
      }
    } catch (error) {
      console.error('❌ Failed to fetch loyalty info:', error);
      console.error('Error details:', {
        message: error.message,
        response: error.response,
        stack: error.stack
      });
      // Gracefully handle error - set defaults so UI doesn't break
      setLoyaltyPoints(0);
      setLoyaltyTier('Bronze');
    }
  };

  // Session Management Functions
  const fetchAndDisplayChatSummary = async (sessionToken) => {
    try {
      const summaryResp = await fetch(`${SALES_API}/api/chat-summary?session_token=${sessionToken}&mode=whatsapp`);
      if (summaryResp.ok) {
        const summaryData = await summaryResp.json();
        if (summaryData.has_summary && summaryData.summary) {
          // Add summary as first agent message
          const summaryMessage = {
            id: 0, // Special ID to keep it at top
            text: summaryData.summary,
            sender: 'agent',
            timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
            status: 'delivered',
            metadata: { type: 'chat_summary' }
          };
          return summaryMessage;
        }
      }
    } catch (err) {
      console.warn('Failed to fetch chat summary:', err);
    }
    return null;
  };

  const startOrRestoreSession = async () => {
    setIsLoadingSession(true);
    try {
      const phone = sessionStore.getPhone();
      const profile = sessionStore.getProfile();
      const storedToken = sessionStore.getSessionToken();

      if (!phone || !profile) {
        sessionStore.clearAll();
        navigate('/login');
        return;
      }

      setCustomerProfile(profile);

      if (storedToken) {
        try {
          const restoreResp = await fetch(`${SESSION_API}/session/restore`, {
            method: 'GET',
            headers: { 'X-Session-Token': storedToken }
          });

          if (restoreResp.ok) {
            const restoreData = await restoreResp.json();
            setSessionToken(storedToken);
            setSessionInfo(restoreData.session);
            sessionStore.setPhone(phone);

            const resolvedUserId = restoreData.session.customer_id || restoreData.session.data?.user_id || restoreData.session.user_id || profile.customer_id;
            if (resolvedUserId) {
              setUserId(resolvedUserId);
              await fetchLoyaltyPoints(resolvedUserId);
            }

            // Restore chat history for returning users
            const hasHistory = restoreData.session.data?.chat_context?.length > 0;
            const hasCart = restoreData.session.data?.cart?.length > 0;
            
            if (hasHistory) {
              console.log('📚 Restoring chat history...');
              const chatMessages = restoreData.session.data.chat_context.map((msg, idx) => ({
                id: idx + 1,
                text: msg.message,
                sender: msg.sender === 'user' ? 'user' : 'agent',
                timestamp: new Date(msg.timestamp).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
                status: 'read',
                cards: msg.metadata?.cards || []
              }));
              
              // Add AI-powered welcome summary as the LAST message for returning users
              if (hasHistory || hasCart) {
                console.log('📨 Fetching AI summary to append as last message...');
                const summaryMessage = await fetchAndDisplayChatSummary(storedToken);
                if (summaryMessage) {
                  chatMessages.push({
                    ...summaryMessage,
                    id: chatMessages.length + 1
                  });
                  console.log('✅ Chat history restored + summary appended as last message');
                }
              }
              
              setMessages(chatMessages);
            } else {
              // New user - show welcome message
              setMessages([{
                id: 1,
                text: `Welcome to WhatsApp Shopping! I'm your personal shopping assistant. How can I help you today?`,
                sender: 'agent',
                timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
                status: 'delivered'
              }]);
            }
            
            // Handle special navigation states (payment success, post-purchase, stylist)
            const currentMessages = [];
            
            // Check for payment success message from navigation state
            if (location.state?.paymentSuccess && location.state?.message && !paymentMessageHandledRef.current) {
              paymentMessageHandledRef.current = true;
              currentMessages.push({
                id: Date.now(),
                text: location.state.message,
                sender: 'agent',
                timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
                status: 'delivered',
                metadata: { type: 'payment_success', orderId: location.state.orderId, paymentId: location.state.paymentId }
              });
              navigate(location.pathname, { replace: true, state: {} });
            }
            
            // Check for post-purchase request
            if (location.state?.postPurchaseRequest && !postPurchaseHandledRef.current) {
              postPurchaseHandledRef.current = true;
              currentMessages.push({
                id: Date.now() + 1,
                text: `📦 Post-Purchase Support for Order ${location.state.orderId}\n\nHow can I help you today? Please select an option:`,
                sender: 'agent',
                timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
                status: 'delivered',
                postPurchaseOptions: {
                  orderId: location.state.orderId,
                  orderItems: location.state.orderItems,
                  userId: location.state.userId,
                  productName: location.state.orderItems?.[0]?.name || null
                }
              });
              navigate(location.pathname, { replace: true, state: {} });
            }
            
            // Check for stylist request
            if (location.state?.stylistRequest && !stylistHandledRef.current) {
              stylistHandledRef.current = true;
              currentMessages.push({
                id: Date.now() + 2,
                text: `👗 Fetching personalized styling suggestions for your order...`,
                sender: 'agent',
                timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
                status: 'delivered',
                metadata: { 
                  type: 'stylist_request',
                  orderId: location.state.orderId,
                  product: location.state.product
                },
                stylistPending: true,
                stylistProduct: location.state.product
              });
              navigate(location.pathname, { replace: true, state: {} });
            }
            
            // Append special state messages if any
            if (currentMessages.length > 0) {
              setMessages(prev => [...prev, ...currentMessages]);
            }
            
            return;
          }
        } catch (err) {
          console.warn('Stored session restore failed, attempting fresh session', err);
        }
      }

      const response = await fetch(`${SESSION_API}/session/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          phone,
          channel: 'whatsapp',
          customer_id: profile.customer_id || profile.customerId || undefined
        })
      });

      if (!response.ok) {
        throw new Error('Failed to start session');
      }

      const data = await response.json();

      setSessionToken(data.session_token);
      sessionStore.setSessionToken(data.session_token);
      sessionStore.setPhone(phone);
      setSessionInfo(data.session);

      const resolvedUserId = data.session.customer_id || data.session.data?.user_id || data.session.user_id || profile.customer_id;
      if (resolvedUserId) {
        setUserId(resolvedUserId);
        await fetchLoyaltyPoints(resolvedUserId);
      }

      if (data.session.data.chat_context && data.session.data.chat_context.length > 0) {
        const chatMessages = data.session.data.chat_context.map((msg, idx) => ({
          id: idx + 1,
          text: msg.message,
          sender: msg.sender === 'user' ? 'user' : 'agent',
          timestamp: new Date(msg.timestamp).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
          status: 'read',
          cards: msg.metadata?.cards || []
        }));
        
        // Check for pending payment success message from navigation state
        if (location.state?.paymentSuccess && location.state?.message && !paymentMessageHandledRef.current) {
          paymentMessageHandledRef.current = true;
          const paymentMessage = {
            id: chatMessages.length + 1,
            text: location.state.message,
            sender: 'agent',
            timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
            status: 'delivered',
            metadata: { type: 'payment_success', orderId: location.state.orderId, paymentId: location.state.paymentId }
          };
          chatMessages.push(paymentMessage);
          navigate(location.pathname, { replace: true, state: {} });
        }
        
        // Check for post-purchase request
        if (location.state?.postPurchaseRequest && !postPurchaseHandledRef.current) {
          postPurchaseHandledRef.current = true;
          chatMessages.push({
            id: chatMessages.length + 1,
            text: `📦 Post-Purchase Support for Order ${location.state.orderId}\n\nHow can I help you today? Please select an option:`,
            sender: 'agent',
            timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
            status: 'delivered',
            postPurchaseOptions: {
              orderId: location.state.orderId,
              orderItems: location.state.orderItems,
              userId: location.state.userId,
              productName: location.state.orderItems?.[0]?.name || null
            }
          });
          navigate(location.pathname, { replace: true, state: {} });
        }
        
        // Check for stylist request
        if (location.state?.stylistRequest && !stylistHandledRef.current) {
          stylistHandledRef.current = true;
          chatMessages.push({
            id: chatMessages.length + 1,
            text: `👗 Fetching personalized styling suggestions for your order...`,
            sender: 'agent',
            timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
            status: 'delivered',
            metadata: { 
              type: 'stylist_request',
              orderId: location.state.orderId,
              product: location.state.product
            },
            stylistPending: true,
            stylistProduct: location.state.product
          });
          navigate(location.pathname, { replace: true, state: {} });
        }
        
        setMessages(chatMessages);
      } else {
        // Check for pending messages when starting fresh session
        let initialMessages = [];
        if (location.state?.paymentSuccess && location.state?.message && !paymentMessageHandledRef.current) {
          paymentMessageHandledRef.current = true;
          initialMessages.push({
            id: 1,
            text: location.state.message,
            sender: 'agent',
            timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
            status: 'delivered',
            metadata: { type: 'payment_success', orderId: location.state.orderId, paymentId: location.state.paymentId }
          });
          navigate(location.pathname, { replace: true, state: {} });
        } else if (location.state?.postPurchaseRequest && !postPurchaseHandledRef.current) {
          postPurchaseHandledRef.current = true;
          initialMessages.push({
            id: 1,
            text: `📦 Post-Purchase Support for Order ${location.state.orderId}\n\nHow can I help you today? Please select an option:`,
            sender: 'agent',
            timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
            status: 'delivered',
            postPurchaseOptions: {
              orderId: location.state.orderId,
              orderItems: location.state.orderItems,
              userId: location.state.userId,
              productName: location.state.orderItems?.[0]?.name || null
            }
          });
          navigate(location.pathname, { replace: true, state: {} });
        } else if (location.state?.stylistRequest && !stylistHandledRef.current) {
          stylistHandledRef.current = true;
          initialMessages.push({
            id: 1,
            text: `👗 Fetching personalized styling suggestions for your order...`,
            sender: 'agent',
            timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
            status: 'delivered',
            metadata: { 
              type: 'stylist_request',
              orderId: location.state.orderId,
              product: location.state.product
            },
            stylistPending: true,
            stylistProduct: location.state.product
          });
          navigate(location.pathname, { replace: true, state: {} });
        } else {
          initialMessages.push({
            id: 1,
            text: "Hello! I'm your AI Sales Agent. How can I help you today?",
            sender: 'agent',
            timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
            status: 'read'
          });
        }
        setMessages(initialMessages);
      }
    } catch (error) {
      console.error('Session error:', error);
      sessionStore.clearAll();
      alert('We could not create your session. Please log in again.');
      navigate('/login');
    } finally {
      setIsLoadingSession(false);
      setIsInitializing(false);
    }
  };

  const endSession = async () => {
    if (!sessionToken) return;

    try {
      await fetch(`${SESSION_API}/session/end`, {
        method: 'POST',
        headers: { 'X-Session-Token': sessionToken }
      });

      // Reset UI
      setSessionInfo(null);
      setSessionToken(null);
      setCustomerProfile(null);
      sessionStore.clearAll();
      setMessages([]);
      setIsInitializing(true);
      navigate('/login');
    } catch (error) {
      console.error('End session error:', error);
    }
  };

  const saveChatMessage = async (sender, message, metadata = null) => {
    if (!sessionToken) return;

    try {
      const payload = { sender, message };
      if (metadata) payload.metadata = metadata;

      await fetch(`${SESSION_API}/session/update`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Session-Token': sessionToken
        },
        body: JSON.stringify({
          action: 'chat_message',
          payload
        })
      });
    } catch (error) {
      console.error('Failed to save chat message:', error);
    }
  };

  const appendAgentMessage = async (text, { metadata = null, messageProps = {} } = {}) => {
    const agentMessage = {
      id: Date.now() + Math.random(),
      text,
      sender: 'agent',
      timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
      status: 'read',
      ...messageProps,
    };

    setMessages((prev) => [...prev, agentMessage]);
    await saveChatMessage('agent', text, metadata);
    return agentMessage;
  };

  const handleAddressInputChange = (field, value) => {
    setAddressForm((prev) => ({
      ...prev,
      [field]: value,
    }));
  };

  const openAddressModalForPayment = (paymentConfig) => {
    if (!paymentConfig || !paymentConfig.amount) {
      alert('Payment details are missing. Please try again.');
      return;
    }

    const existing = savedAddress || sessionStore.getAddress?.() || {};
    setAddressForm({
      city: existing.city || '',
      landmark: existing.landmark || '',
      building: existing.building || '',
    });
    setAddressError('');
    setPendingPaymentDetails(paymentConfig);
    setShowAddressModal(true);
  };

  const closeAddressModal = () => {
    setShowAddressModal(false);
    setAddressError('');
    setPendingPaymentDetails(null);
    if (savedAddress) {
      setAddressForm({
        city: savedAddress.city || '',
        landmark: savedAddress.landmark || '',
        building: savedAddress.building || '',
      });
    }
  };

  const submitAddressForm = async (event) => {
    if (event && typeof event.preventDefault === 'function') {
      event.preventDefault();
    }

    const trimmedCity = (addressForm.city || '').trim();
    const trimmedLandmark = (addressForm.landmark || '').trim();
    const trimmedBuilding = (addressForm.building || '').trim();

    if (!trimmedCity || !trimmedLandmark || !trimmedBuilding) {
      setAddressError('City, landmark, and building are required.');
      return;
    }

    const normalizedAddress = {
      city: trimmedCity,
      landmark: trimmedLandmark,
      building: trimmedBuilding,
    };

    try {
      if (typeof sessionStore.setAddress === 'function') {
        sessionStore.setAddress(normalizedAddress);
      }
      setSavedAddress(normalizedAddress);
      setAddressForm(normalizedAddress);
      setSessionInfo((prev) => (
        prev
          ? {
              ...prev,
              data: {
                ...(prev.data || {}),
                shipping_address: normalizedAddress,
              },
            }
          : prev
      ));
      await saveChatMessage('user', 'Shared delivery address for this order.', {
        shipping_address: normalizedAddress,
      });
    } catch (storageError) {
      console.error('Failed to persist address:', storageError);
    }

    const paymentConfig = pendingPaymentDetails;
    setShowAddressModal(false);
    setPendingPaymentDetails(null);
    setAddressError('');

    if (!paymentConfig) {
      return;
    }

    await appendAgentMessage('📍 Address saved. Opening payment gateway now...');
    await initiateRazorpayPayment(paymentConfig.amount, {
      ...paymentConfig.details,
      address: normalizedAddress,
    });
  };

  const updateSupportForm = (field, value) => {
    setSupportForm((prev) => ({
      ...prev,
      [field]: value,
    }));
  };

  const submitSupportForm = async (event) => {
    if (event && typeof event.preventDefault === 'function') {
      event.preventDefault();
    }

    if (!activeSupportMode || activeSupportMode === 'menu') {
      return;
    }

    setSupportLoading(true);
    setSupportError('');

    try {
      let result = null;
      let summaryText = '';

      switch (activeSupportMode) {
        case 'return': {
          if (!supportForm.user_id || !supportForm.order_id || !supportForm.product_sku || !supportForm.reason_code) {
            setSupportError('User, order, product, and reason are required.');
            setSupportLoading(false);
            return;
          }

          result = await initiateReturn({
            user_id: supportForm.user_id,
            order_id: supportForm.order_id,
            product_sku: supportForm.product_sku,
            reason_code: supportForm.reason_code,
            additional_comments: supportForm.additional_comments || '',
          });

          summaryText = `📦 Return ${result.return_id} created for order ${supportForm.order_id}. Pickup ${result.pickup_date || 'will be scheduled soon'} and refund will trigger after inspection.`;
          await appendAgentMessage(summaryText, {
            metadata: {
              post_purchase: {
                stage: 'return',
                return_id: result.return_id,
                order_id: supportForm.order_id,
                product_sku: supportForm.product_sku,
              },
            },
          });
          break;
        }
        case 'exchange': {
          if (!supportForm.user_id || !supportForm.order_id || !supportForm.product_sku || !supportForm.requested_size) {
            setSupportError('User, order, product, and requested size are required.');
            setSupportLoading(false);
            return;
          }

          result = await initiateExchange({
            user_id: supportForm.user_id,
            order_id: supportForm.order_id,
            product_sku: supportForm.product_sku,
            current_size: supportForm.current_size || '',
            requested_size: supportForm.requested_size,
            reason: supportForm.reason || '',
          });

          summaryText = `🔁 Exchange ${result.exchange_id} started for ${supportForm.product_sku}. New size arrives by ${result.delivery_date || 'the promised date'}.`;
          await appendAgentMessage(summaryText, {
            metadata: {
              post_purchase: {
                stage: 'exchange',
                exchange_id: result.exchange_id,
                order_id: supportForm.order_id,
                product_sku: supportForm.product_sku,
              },
            },
          });
          break;
        }
        case 'complaint': {
          if (!supportForm.user_id || !supportForm.issue_type || !supportForm.description) {
            setSupportError('Issue type and description are required.');
            setSupportLoading(false);
            return;
          }

          result = await raiseComplaint({
            user_id: supportForm.user_id,
            order_id: supportForm.order_id || '',
            issue_type: supportForm.issue_type,
            description: supportForm.description,
            priority: supportForm.priority || 'medium',
          });

          summaryText = `📝 Complaint ticket ${result.ticket_number} logged (${supportForm.issue_type}). We will reach out soon.`;
          await appendAgentMessage(summaryText, {
            metadata: {
              post_purchase: {
                stage: 'complaint',
                complaint_id: result.complaint_id,
                ticket_number: result.ticket_number,
              },
            },
          });
          break;
        }
        case 'feedback': {
          if (!supportForm.product_sku || !supportForm.size_purchased) {
            setSupportError('Please select an item and enter the size purchased.');
            setSupportLoading(false);
            return;
          }

          result = await submitFeedback({
            user_id: supportForm.user_id,
            product_sku: supportForm.product_sku,
            size_purchased: supportForm.size_purchased,
            fit_rating: supportForm.fit_rating || 'perfect',
            length_feedback: supportForm.length_feedback || 'not_specified',
            comments: supportForm.comments || '',
          });

          summaryText = result.message
            ? `💬 ${result.message}`
            : '💬 Feedback saved. Thanks for helping us improve your next fit.';
          await appendAgentMessage(summaryText, {
            metadata: {
              post_purchase: {
                stage: 'feedback',
                product_sku: supportForm.product_sku,
              },
            },
          });
          break;
        }
        default:
          setSupportError('Please pick a support option.');
          setSupportLoading(false);
          return;
      }

      if (result) {
        setSupportResult({ type: activeSupportMode, data: result, summary: summaryText });
      }
    } catch (error) {
      console.error('Post-purchase request failed:', error);
      setSupportError(error.message || 'Action failed. Please try again.');
    } finally {
      setSupportLoading(false);
    }
  };

  // Auto-scroll to bottom when messages change
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isTyping]);

  // Initialize Speech Recognition
  useEffect(() => {
    if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
      const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      recognitionRef.current = new SpeechRecognition();
      recognitionRef.current.continuous = false;
      recognitionRef.current.interimResults = true;
      recognitionRef.current.lang = 'en-US';

      recognitionRef.current.onresult = (event) => {
        let finalTranscript = '';

        for (let i = 0; i < event.results.length; i++) {
          const transcript = event.results[i][0].transcript;
          if (event.results[i].isFinal) {
            finalTranscript += transcript + ' ';
          }
        }

        if (finalTranscript) {
          transcriptRef.current += finalTranscript;
          setInputText(transcriptRef.current);
        }
      };

      recognitionRef.current.onend = () => {
        setIsRecording(false);
      };

      recognitionRef.current.onerror = (event) => {
        console.error('Speech recognition error:', event.error);
        setIsRecording(false);
      };
    }
  }, []);

  useEffect(() => {
    if (window.Razorpay) {
      setIsRazorpayReady(true);
      return;
    }

    const script = document.createElement('script');
    script.src = 'https://checkout.razorpay.com/v1/checkout.js';
    script.async = true;
    script.onload = () => setIsRazorpayReady(true);
    script.onerror = () => {
      console.error('Failed to load Razorpay checkout script');
      setIsRazorpayReady(false);
    };

    document.body.appendChild(script);

    return () => {
      if (script.parentNode) {
        script.parentNode.removeChild(script);
      }
    };
  }, []);

  // Mock agent responses
  const mockAgentResponses = [
    "Sure! Let me check the best options for you.",
    "I found some great products that match your preferences!",
    "Would you like me to show you our top recommendations?",
    "I can help you with that. What's your budget?",
    "Great choice! Let me find similar items for you.",
    "I'm checking our inventory for you...",
    "Based on your preferences, I have some perfect options!"
  ];

  const initiateRazorpayPayment = async (amount, detailsArg = null) => {
    if (!sessionToken) {
      alert('Start a chat session before initiating payment.');
      return;
    }

    if (!window.Razorpay || !isRazorpayReady) {
      alert('Razorpay checkout is still loading. Please wait a moment and try again.');
      return;
    }

    const parsedAmount = parsePriceToNumber(amount);
    if (!Number.isFinite(parsedAmount) || parsedAmount <= 0) {
      alert('Unable to determine payment amount. Please verify the price and try again.');
      return;
    }

    setIsPaymentProcessing(true);
    paymentInFlightRef.current = true;

    try {
      const normalizedDetails = (detailsArg && typeof detailsArg === 'object') ? detailsArg : {};
      const productDetails = normalizedDetails.product || (normalizedDetails.name || normalizedDetails.sku ? normalizedDetails : null) || pendingCheckoutItem;
      const shippingAddress = normalizedDetails.address || savedAddress;
      const notes = {
        session_id: sessionInfo?.session_id || '',
        phone: sessionInfo?.phone || customerProfile?.phone_number || '',
      };

      if (sessionInfo?.data?.customer_id) {
        notes.customer_id = sessionInfo.data.customer_id;
      }
      if (productDetails?.sku) {
        notes.product_sku = productDetails.sku;
      }
      if (productDetails?.name) {
        notes.product_name = productDetails.name;
      }
      if (normalizedDetails.source) {
        notes.checkout_source = normalizedDetails.source;
      }
      if (shippingAddress) {
        notes.address_city = shippingAddress.city || '';
        notes.address_landmark = shippingAddress.landmark || '';
        notes.address_building = shippingAddress.building || '';
      }

      if (!productDetails) {
        alert('No product selected for payment.');
        setIsPaymentProcessing(false);
        paymentInFlightRef.current = false;
        return;
      }

      const quantity = Number(productDetails.quantity) > 0 ? Number(productDetails.quantity) : 1;
      const unitPrice = Number(productDetails.price) || parsedAmount / quantity;

      const orderPayload = {
        amount_rupees: parsedAmount,
        currency: 'INR',
        notes,
        items: [{
          sku: productDetails.sku,
          qty: quantity,
          unit_price: unitPrice
        }],
      };

      if (normalizedDetails.orderId) {
        orderPayload.receipt = normalizedDetails.orderId;
      }

      const orderResponse = await createRazorpayOrder(orderPayload);

      const options = {
        key: orderResponse.razorpay_key_id,
        amount: orderResponse.order.amount,
        currency: orderResponse.order.currency,
        name: 'EY CodeCrafters',
        description: productDetails?.name ? `Order for ${productDetails.name}` : 'AI Sales Agent Order',
        order_id: orderResponse.order.id,
        prefill: {
          name: sessionInfo?.data?.customer_name || customerProfile?.name || sessionInfo?.phone || 'Customer',
          email: sessionInfo?.data?.email || 'test@example.com',
          contact: sessionInfo?.phone || customerProfile?.phone_number || '',
        },
        theme: { color: '#008069' },
        modal: {
          ondismiss: async () => {
            if (paymentInFlightRef.current) {
              const infoText = 'ℹ️ Razorpay checkout was closed before completing the payment.';
              const infoMessage = {
                id: Date.now() + Math.random(),
                text: infoText,
                sender: 'agent',
                timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
                status: 'read',
              };
              setMessages((prev) => [...prev, infoMessage]);
              await saveChatMessage('agent', infoText);
            }
            paymentInFlightRef.current = false;
            setIsPaymentProcessing(false);
          },
        },
        handler: async (response) => {
          console.log('💳 Razorpay payment handler triggered:', response);
          try {
            // Get user ID for loyalty updates
            const currentUserId = sessionInfo?.data?.customer_id || sessionInfo?.customer_id || customerProfile?.customer_id || sessionInfo?.phone || customerProfile?.phone_number;
            console.log('👤 Current User ID:', currentUserId);
            
            const verificationResult = await verifyRazorpayPayment({
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_order_id: response.razorpay_order_id,
              razorpay_signature: response.razorpay_signature,
              amount_rupees: parsedAmount,
              user_id: currentUserId,
              method: 'razorpay',
              order_id: orderResponse.order_id,
            });
            
            console.log('✅ Verification Result:', verificationResult);

            const canonicalOrderId =
              verificationResult?.order_id || normalizedDetails.orderId || response.razorpay_order_id;
            const canonicalPaymentId = verificationResult?.payment_id || response.razorpay_payment_id;
            const gatewayPaymentId =
              verificationResult?.gateway_payment_id || response.razorpay_payment_id;

            // Extract loyalty details from verification result
            const loyaltyData = verificationResult?.loyalty;
            
            // Update loyalty state directly from verification result
            if (loyaltyData) {
              setLoyaltyPoints(loyaltyData.total_points || 0);
              setLoyaltyTier(loyaltyData.current_tier ? loyaltyData.current_tier.charAt(0).toUpperCase() + loyaltyData.current_tier.slice(1) : 'Bronze');
            }
            
            // Also refresh from backend to ensure sync
            if (currentUserId) {
              await fetchLoyaltyPoints(currentUserId);
            }

            // Show delivery window selection modal
            setDeliveryOrderId(canonicalOrderId);
            setShowDeliveryModal(true);

            let successText =
              `✅ Payment of ₹${parsedAmount} received!\nOrder ID: ${canonicalOrderId}\nPayment ID: ${canonicalPaymentId}`;
            
            // Add loyalty message if available
            if (loyaltyData) {
              if (loyaltyData.tier_upgraded) {
                successText += `\n\n🎉 Congratulations!\nYou've been upgraded to ${loyaltyData.current_tier.charAt(0).toUpperCase() + loyaltyData.current_tier.slice(1)} tier 🏆\nYou earned ${loyaltyData.earned_points} loyalty points!`;
              } else {
                successText += `\n\nYou earned ${loyaltyData.earned_points} loyalty points 🎉\nYou now have ${loyaltyData.total_points} points.`;
                if (loyaltyData.points_needed > 0) {
                  successText += `\nYou are just ${loyaltyData.points_needed} points away from ${loyaltyData.next_tier.charAt(0).toUpperCase() + loyaltyData.next_tier.slice(1)} tier.`;
                }
              }
            }
            
            // Add product-specific message
            if (productDetails) {
              successText += `\n\n🎉 Purchase Complete!\n📦 ${productDetails.name || 'Selected item'}\n💰 Amount Paid: ₹${parsedAmount}`;
            }

            const successMessage = {
              id: Date.now() + Math.random(),
              text: successText,
              sender: 'agent',
              timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
              status: 'read',
            };
            
            // Display success message in chat
            setMessages((prev) => [...prev, successMessage]);
            await saveChatMessage('agent', successText);
            
            // Scroll to bottom to show the success message
            setTimeout(() => {
              messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
            }, 100);
            
            // Log for debugging
            console.log('💳 Payment Success:', {
              orderId: canonicalOrderId,
              paymentId: canonicalPaymentId,
              loyaltyData,
              successText,
              messageAdded: true
            });
            const displayName = productDetails?.name || '';
            const productLine = displayName ? ` for ${displayName}` : '';
            await appendAgentMessage(
              `✅ Payment of ${formatINR(parsedAmount)} received${productLine}!\nOrder ID: ${canonicalOrderId}\nPayment ID: ${canonicalPaymentId}`
            );

            const orderId = canonicalOrderId;
            const recordedAddress = normalizedDetails.address || savedAddress || null;

            if (productDetails) {
              const orderOwner = sessionInfo?.data?.customer_id || sessionInfo?.customer_id || customerProfile?.customer_id || sessionInfo?.phone || customerProfile?.phone_number || '';
              const quantity = Number(productDetails.quantity) > 0 ? Number(productDetails.quantity) : 1;
              const unitPrice = Number(productDetails.price) || parsedAmount / quantity;
              const orderPayload = {
                order_id: orderId,
                user_id: orderOwner,
                amount: parsedAmount,
                status: 'completed',
                created_at: new Date().toISOString(),
                shipping_address: recordedAddress || {},
                metadata: {
                  payment_id: canonicalPaymentId,
                  gateway_payment_id: gatewayPaymentId,
                  razorpay_order_id: response.razorpay_order_id,
                  checkout_source: normalizedDetails.source || '',
                  session_id: sessionInfo?.session_id || '',
                },
                items: [
                  {
                    sku: productDetails.sku || 'UNKNOWN',
                    name: productDetails.name || 'Purchased item',
                    brand: productDetails.brand || '',
                    category: productDetails.category || productDetails.productType || '',
                    quantity,
                    unit_price: unitPrice,
                    line_total: unitPrice * quantity,
                  },
                ],
              };

              try {
                await registerPostPurchaseOrder(orderPayload);
              } catch (registerError) {
                console.error('Failed to register order for post-purchase:', registerError);
              }

              setLastCompletedOrder({
                orderId,
                amount: parsedAmount,
                paymentId: canonicalPaymentId,
                gatewayPaymentId,
                product: productDetails || undefined,
                address: recordedAddress,
              });

              if (productDetails?.sku) {
                try {
                  const stylistPayload = {
                    user_id: orderOwner || '',
                    product_sku: productDetails.sku || '',
                    product_name: productDetails.name || 'Purchased item',
                    category: productDetails.category || productDetails.productType || 'Apparel',
                    color: productDetails.color || '',
                    brand: productDetails.brand || '',
                  };

                  const stylistResponse = await salesAgentService.getStylistSuggestions(stylistPayload);
                  const stylistBundle = stylistResponse?.recommendations || {};
                  const recommendedProducts = Array.isArray(stylistBundle?.recommended_products)
                    ? stylistBundle.recommended_products
                        .map((item) => ({
                          sku: item?.sku || '',
                          name: item?.name || '',
                          reason: item?.reason || '',
                          image_url: item?.image_url || item?.image || '',
                          price: item?.price || 0,
                          brand: item?.brand || '',
                          rating: item?.rating || 0,
                          personalized_reason: item?.personalized_reason || item?.reason || '',
                        }))
                        .filter((item) => item.sku || item.name || item.reason)
                    : [];
                  const stylingTips = Array.isArray(stylistBundle?.styling_tips)
                    ? stylistBundle.styling_tips
                        .filter((tip) => typeof tip === 'string' && tip.trim())
                    : [];

                  if (stylistResponse?.success && (recommendedProducts.length || stylingTips.length)) {
                    await appendAgentMessage(
                      `👗 Our stylist just walked in with looks for your ${productDetails.name || 'new purchase'}!`,
                      {
                        metadata: {
                          stylist: {
                            stage: 'post_purchase',
                            product_sku: productDetails.sku || '',
                            recommendation_id: stylistResponse.recommendation_id || '',
                          },
                        },
                        messageProps: {
                          stylistRecommendations: {
                            purchasedProduct: stylistResponse.purchased_product || stylistPayload,
                            recommendedProducts,
                            stylingTips,
                            recommendationId: stylistResponse.recommendation_id || '',
                          },
                        },
                      }
                    );
                  }
                } catch (stylistError) {
                  console.error('Failed to fetch stylist suggestions:', stylistError);
                }
              }

              await appendAgentMessage(
                `Need any help after buying ${productDetails.name || 'this item'}? Choose a support option below.`,
                {
                  metadata: {
                    post_purchase: {
                      stage: 'cta',
                      order_id: orderId,
                      product_sku: productDetails.sku || '',
                      amount: parsedAmount,
                      address: recordedAddress,
                    },
                  },
                  messageProps: {
                    postPurchaseOptions: {
                      orderId,
                      productName: productDetails.name || '',
                      productSku: productDetails.sku || '',
                      amount: parsedAmount,
                      brand: productDetails.brand || '',
                      productCategory: productDetails.category || '',
                      productColor: productDetails.color || '',
                      productMaterial: productDetails.material || '',
                      productType: productDetails.productType || productDetails.category || '',
                      deliveryAddress: recordedAddress || undefined,
                    },
                  },
                }
              );
            }

            setPendingCheckoutItem(null);
            setAwaitingConfirmation(false);
          } catch (verifyError) {
            console.error('❌ Payment verification failed:', verifyError);
            console.error('Error details:', {
              message: verifyError.message,
              response: verifyError.response,
              stack: verifyError.stack
            });
            
            const failureText = `⚠️ Payment captured but verification failed. Please contact support. (${verifyError.message || 'Unknown error'})`;
            const failMessage = {
              id: Date.now() + Math.random(),
              text: failureText,
              sender: 'agent',
              timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
              status: 'read',
            };
            setMessages((prev) => [...prev, failMessage]);
            await saveChatMessage('agent', failureText);
          } finally {
            paymentInFlightRef.current = false;
            setIsPaymentProcessing(false);
          }
        },
      };

      const razorpayInstance = new window.Razorpay(options);
      razorpayInstance.on('payment.failed', async (failure) => {
        console.error('Razorpay payment failed:', failure);
        const failureText = `❌ Payment failed: ${failure.error?.description || 'Unknown error'}`;
        const failMessage = {
          id: Date.now() + Math.random(),
          text: failureText,
          sender: 'agent',
          timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
          status: 'read',
        };
        setMessages((prev) => [...prev, failMessage]);
        await saveChatMessage('agent', failureText);
        paymentInFlightRef.current = false;
        setIsPaymentProcessing(false);
      });

      razorpayInstance.open();
    } catch (error) {
      console.error('Failed to initiate Razorpay payment:', error);
      const errorText = `❌ Unable to start Razorpay checkout: ${error.message || 'Unknown error'}`;
      const failMessage = {
        id: Date.now() + Math.random(),
        text: errorText,
        sender: 'agent',
        timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
        status: 'read',
      };
      setMessages((prev) => [...prev, failMessage]);
      await saveChatMessage('agent', errorText);
      paymentInFlightRef.current = false;
      setIsPaymentProcessing(false);
    }
  };

  const handleProductPurchase = async (product) => {
    if (!sessionToken || !userId) {
      alert('Please start a chat session first.');
      return;
    }

    if (isPaymentProcessing) {
      return;
    }

    setIsPaymentProcessing(true);

    try {
      // Step 1: Calculate discounts
      const discountResponse = await fetch(`${API_ENDPOINTS.LOYALTY}/loyalty/calculate-discounts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id: userId,
          cart_total: parseFloat(product.price)
        })
      });

      if (!discountResponse.ok) {
        throw new Error('Failed to calculate discounts');
      }

      const discountData = await discountResponse.json();

      // Step 2: Show discount breakdown to user
      const originalPrice = discountData.original_total;
      const finalPrice = discountData.final_total;
      const savings = originalPrice - finalPrice;

      const discountMessage = `🛒 **Purchase Summary**\n\n` +
        `📦 Product: ${product.name}\n` +
        `💰 Original Price: ₹${originalPrice}\n` +
        `${discountData.message}\n` +
        `💸 You Save: ₹${savings.toFixed(2)}\n` +
        `✅ Final Price: ₹${finalPrice.toFixed(2)}\n\n` +
        `🎁 Your Loyalty Status:\n` +
        `🏅 Tier: ${loyaltyTier}\n` +
        `💎 Points: ${loyaltyPoints}\n\n` +
        `Ready to proceed with payment?`;

      // Add discount summary message
      const discountMsg = {
        id: Date.now(),
        text: discountMessage,
        sender: 'agent',
        timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
      };
      setMessages(prev => [...prev, discountMsg]);
      await saveChatMessage('agent', discountMessage);

      const payableAmount = parsePriceToNumber(finalPrice);
      const safeAmount = Number.isFinite(payableAmount) && payableAmount > 0
        ? payableAmount
        : parsePriceToNumber(product.price);

      if (!Number.isFinite(safeAmount) || safeAmount <= 0) {
        throw new Error('Unable to determine payable amount');
      }

      const normalizedQuantity = Number(product.quantity) > 0 ? Number(product.quantity) : 1;
      const orderId = await fetchCanonicalOrderId(product.sku || 'ITEM');
      const normalizedProduct = {
        ...product,
        price: safeAmount,
        rawPrice: product.price,
        quantity: normalizedQuantity,
        orderId,
      };

      setPendingCheckoutItem(normalizedProduct);

      await storeSelectedItemInSession({
        sku: normalizedProduct.sku,
        name: normalizedProduct.name,
        price: safeAmount,
        quantity: normalizedQuantity,
      });

      const checkoutPayload = {
        product: normalizedProduct,
        amount: safeAmount,
        orderId,
        quantity: normalizedQuantity,
      };

      setIsPaymentProcessing(false);

      const paymentPromptText = `Great! Your payable amount is ${formatINR(safeAmount)}. Tap "Please Pay Here" to add your delivery address and pay securely via Razorpay.`;
      const paymentPromptMessage = {
        id: Date.now() + Math.random(),
        text: paymentPromptText,
        sender: 'agent',
        timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
        status: 'read',
        checkout: checkoutPayload,
      };

      setMessages((prev) => [...prev, paymentPromptMessage]);
      await saveChatMessage('agent', paymentPromptText, { checkout: checkoutPayload });

    } catch (error) {
      console.error('Purchase error:', error);
      alert('Failed to process purchase. Please try again.');
    } finally {
      setIsPaymentProcessing(false);
    }
  };

  const handlePaymentClick = async () => {
    // You can implement a generic payment action here if needed.
    // For now, it does nothing.
  };

  const handleDeliveryWindowSelection = async (window) => {
    try {
      console.log('🚚 Setting delivery window:', window, 'for order:', deliveryOrderId);
      setSelectedDeliveryWindow(window);
      
      if (!deliveryOrderId) {
        console.error('No order ID available for delivery window');
        await appendAgentMessage(
          `⚠️ Order ID not found. Please contact support.`
        );
        return;
      }
      
      const response = await setDeliveryWindow({
        order_id: deliveryOrderId,
        delivery_window: window
      });
      
      console.log('✅ Delivery window response:', response);
      
      if (response && (response.success || response.delivery_window || response.status === 'success')) {
        // Success - start tracking this order for delivery updates
        setLastTrackedOrderId(deliveryOrderId);
        setLastOrderStatus(null);
        
        // Close modal and show confirmation
        setShowDeliveryModal(false);
        await appendAgentMessage(
          `🚚 Perfect! Your delivery is scheduled for the ${window} slot (${window === 'morning' ? '6AM-12PM' : window === 'afternoon' ? '12PM-6PM' : '6PM-10PM'}). We'll send you updates soon!`
        );
      } else {
        // Unexpected response but don't auto-close
        console.warn('Unexpected delivery window response:', response);
        await appendAgentMessage(
          `⚠️ Unable to confirm delivery window. Please try selecting again or skip for now.`
        );
      }
    } catch (error) {
      console.error('❌ Delivery window error:', error);
      // Don't close modal on error - let user try again or skip
      await appendAgentMessage(
        `⚠️ Connection error. Please try selecting your delivery window again, or click Skip to continue.`
      );
    }
  };

  const sendMessageToAgent = async (messageText, { skipBackend = false } = {}) => {
    if (!messageText.trim() || !sessionToken) {
      return;
    }

    const messageId = Date.now();
    const timestamp = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });

    const userMessage = {
      id: messageId,
      text: messageText,
      sender: 'user',
      timestamp,
      status: skipBackend ? 'read' : 'sent',
    };

    setMessages((prev) => [...prev, userMessage]);
    await saveChatMessage('user', messageText);

    if (skipBackend) {
      return;
    }

    setTimeout(() => {
      setMessages((prev) => prev.map((msg) =>
        msg.id === messageId ? { ...msg, status: 'delivered' } : msg
      ));
    }, 500);

    setTimeout(() => {
      setMessages((prev) => prev.map((msg) =>
        msg.id === messageId ? { ...msg, status: 'read' } : msg
      ));
    }, 1000);

    setIsTyping(true);

    try {
      const payload = {
        message: messageText,
        session_token: sessionToken,
        metadata: { user_id: sessionInfo?.customer_id || customerProfile?.customer_id || sessionInfo?.phone || customerProfile?.phone_number }
      };

      const resp = await fetch(`${SALES_API}/api/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      setIsTyping(false);

      if (!resp.ok) {
        throw new Error('Agent error');
      }

      const data = await resp.json();
      const agentText = data.reply || 'Sorry, I could not process that.';

      if (data.session_token) {
        setSessionToken(data.session_token);
      }

      const agentMessage = {
        id: Date.now() + Math.random(),
        text: agentText,
        sender: 'agent',
        timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
        status: 'read',
        cards: data.cards || [],
      };

      if (agentText.toLowerCase().includes('please confirm your cart')) {
        setAwaitingConfirmation(true);
      }

      setMessages((prev) => [...prev, agentMessage]);
      await saveChatMessage('agent', agentText, { cards: agentMessage.cards || [] });
    } catch (error) {
      setIsTyping(false);
      console.error('Agent call failed:', error);
      const failMsg = {
        id: Date.now() + Math.random(),
        text: 'Sorry, I could not reach the agent. Please try again later.',
        sender: 'agent',
        timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
        status: 'read',
      };
      setMessages((prev) => [...prev, failMsg]);
      await saveChatMessage('agent', failMsg.text);
    }
  };

  const handleCheckoutConfirmation = async (messageText) => {
    await sendMessageToAgent(messageText, { skipBackend: true });

    if (!pendingCheckoutItem) {
      const infoText = 'I do not see any item in your cart yet. Please choose a product before confirming checkout.';
      const infoMessage = {
        id: Date.now() + Math.random(),
        text: infoText,
        sender: 'agent',
        timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
        status: 'read',
      };
      setMessages((prev) => [...prev, infoMessage]);
      await saveChatMessage('agent', infoText);
      setAwaitingConfirmation(false);
      return;
    }

    const amount = parsePriceToNumber(pendingCheckoutItem.price ?? pendingCheckoutItem.rawPrice);
    if (!Number.isFinite(amount) || amount <= 0) {
      const warnText = 'Price information is missing for the selected item. Please ask the agent for the latest price before checking out.';
      const warnMessage = {
        id: Date.now() + Math.random(),
        text: warnText,
        sender: 'agent',
        timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
        status: 'read',
      };
      setMessages((prev) => [...prev, warnMessage]);
      await saveChatMessage('agent', warnText);
      setAwaitingConfirmation(false);
      return;
    }

    const orderId = buildCheckoutOrderId(pendingCheckoutItem.sku || 'ITEM');
    const checkoutPayload = {
      product: { ...pendingCheckoutItem },
      amount,
      orderId,
      quantity: pendingCheckoutItem.quantity || 1,
    };

    setPendingCheckoutItem((prev) => (prev ? { ...prev, orderId } : prev));

    const agentText = `Great choice! Here's your checkout summary for ${pendingCheckoutItem.name || 'your selected product'}${pendingCheckoutItem.sku ? ` (SKU ${pendingCheckoutItem.sku})` : ''}. Total payable ${formatINR(amount)}. Tap "Please Pay Here" to finish the purchase.`;
    const checkoutMessage = {
      id: Date.now() + Math.random(),
      text: agentText,
      sender: 'agent',
      timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
      status: 'read',
      checkout: checkoutPayload,
    };

    setMessages((prev) => [...prev, checkoutMessage]);
    await saveChatMessage('agent', agentText, { checkout: checkoutPayload });
    setAwaitingConfirmation(false);
  };

  const handleSendMessage = async () => {
    if (!sessionToken) {
      return;
    }

    const messageText = inputText.trim();
    if (!messageText) {
      return;
    }

    setInputText('');

    if (awaitingConfirmation && messageText.toLowerCase() === 'confirm') {
      await handleCheckoutConfirmation(messageText);
      return;
    }

    await sendMessageToAgent(messageText);
  };

  const handleImageUploadClick = () => {
    if (imageInputRef.current) {
      imageInputRef.current.value = '';
      imageInputRef.current.click();
    }
  };

  const handleImageUpload = async (event) => {
    const file = event.target.files?.[0];
    if (!file || isImageSearching) {
      return;
    }

    setIsImageSearching(true);

    const uploadPreviewUrl = URL.createObjectURL(file);
    setMessages((prev) => [
      ...prev,
      {
        id: Date.now() + Math.random(),
        text: '📸 Uploaded an image for visual search.',
        sender: 'user',
        timestamp: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
        status: 'read',
        imagePreview: uploadPreviewUrl,
      },
    ]);

    await appendAgentMessage('📸 Got your image! Searching for visually similar products...');

    try {
      const response = await salesAgentService.visualSearch(file);

      if (!response || response.success === false) {
        await appendAgentMessage(response?.message || 'I could not find similar products. Please try another image.');
        return;
      }

      const bestMatch = response.best_match;
      const alternatives = response.alternative_matches || [];
      const cards = [];

      if (bestMatch) {
        cards.push({
          type: 'product',
          sku: bestMatch.matched_product_id,
          name: bestMatch.product_name,
          price: bestMatch.price,
          image: bestMatch.image_url || '',
          description: bestMatch.reasoning || '',
        });
      }

      alternatives.forEach((match) => {
        cards.push({
          type: 'product',
          sku: match.matched_product_id,
          name: match.product_name,
          price: match.price,
          image: match.image_url || '',
          description: match.reasoning || '',
        });
      });

      const intro = bestMatch
        ? `✨ **Perfect Match!** I found this stunning ${bestMatch.brand || ''} piece: **${bestMatch.product_name || ''}**. Check out the full details below along with other curated options.`
        : '✨ **Visual Search Complete!** I have handpicked these premium pieces based on your image. Each one is curated to match your style preferences.';

      await appendAgentMessage(intro, { metadata: { cards }, messageProps: { cards } });
    } catch (error) {
      console.error('Visual search failed:', error);
      await appendAgentMessage('Something went wrong while searching by image. Please try again.');
    } finally {
      setIsImageSearching(false);
    }
  };

  const handleCheckoutPayment = (checkout) => {
    if (!checkout) {
      return;
    }

    const amount = parsePriceToNumber(checkout.amount);
    if (!Number.isFinite(amount) || amount <= 0) {
      alert('Unable to determine payment amount. Please ask the agent for assistance.');
      return;
    }

    openAddressModalForPayment({
      amount,
      details: {
        orderId: checkout.orderId,
        sku: checkout.product?.sku,
        name: checkout.product?.name,
        source: 'guided-checkout',
        product: checkout.product,
      },
    });
  };

  const storeSelectedItemInSession = async (item) => {
    if (!sessionToken || !item) {
      return;
    }

    try {
      await fetch(`${SESSION_API}/session/update`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Session-Token': sessionToken,
        },
        body: JSON.stringify({
          action: 'add_to_cart',
          payload: { item },
        }),
      });
    } catch (error) {
      console.error('Failed to persist cart item in session:', error);
    }
  };

  const showToast = (message) => {
    setToast({ show: true, message });
    setTimeout(() => setToast({ show: false, message: '' }), 3000);
  };

  const handleAddToCart = (card) => {
    const price = parsePriceToNumber(card.price ?? card.rawPrice);
    if (!Number.isFinite(price) || price <= 0) {
      showToast('Unable to add item: price information missing');
      return;
    }

    addToCart({
      sku: card.sku,
      name: card.name,
      unit_price: price,
      qty: 1,
      image: card.image,
    });
    showToast(`${card.name} added to cart!`);
  };

  const handleBuyNow = (card) => {
    const price = parsePriceToNumber(card.price ?? card.rawPrice);
    if (!Number.isFinite(price) || price <= 0) {
      showToast('Unable to purchase: price information missing');
      return;
    }

    // Clear cart and add this single item
    clearCart();
    addToCart({
      sku: card.sku,
      name: card.name,
      unit_price: price,
      qty: 1,
      image: card.image,
    });

    // Navigate to checkout
    navigate('/checkout');
  };

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  const toggleVoiceRecording = () => {
    if (!recognitionRef.current) {
      alert('Speech recognition is not supported in your browser. Please use Chrome or Edge.');
      return;
    }

    if (isRecording) {
      recognitionRef.current.stop();
      setIsRecording(false);
    } else {
      transcriptRef.current = '';
      setInputText('');
      recognitionRef.current.start();
      setIsRecording(true);
    }
  };

  const getMessageStatusIcon = (status) => {
    switch(status) {
      case 'sent':
        return <Check className="w-4 h-4 text-gray-400" />;
      case 'delivered':
        return <CheckCheck className="w-4 h-4 text-gray-400" />;
      case 'read':
        return <CheckCheck className="w-4 h-4 text-blue-500" />;
      default:
        return null;
    }
  };

  const toggleExpandMessage = (id) => {
    setExpandedMessages(prev => {
      const s = new Set(prev);
      if (s.has(id)) s.delete(id); else s.add(id);
      return s;
    });
  };

  const toggleExpandCard = (id) => {
    setExpandedCards(prev => {
      const s = new Set(prev);
      if (s.has(id)) s.delete(id); else s.add(id);
      return s;
    });
  };

  const closeSupportPanel = () => {
    setShowSupportPanel(false);
    setActiveSupportMode(null);
    setSupportForm({});
    setSupportContext({});
    setSupportResult(null);
    setSupportError('');
    setPanelInitializing(false);
  };

  const openSupportPanel = async (mode, context = {}) => {
    if (!sessionToken) {
      alert('Start the chat session before using post-purchase services.');
      return;
    }

    setSupportResult(null);
    setSupportError('');
    setSupportContext(context || {});
    setShowSupportPanel(true);

    if (mode === 'menu') {
      setActiveSupportMode('menu');
      return;
    }

    setPanelInitializing(true);
    setActiveSupportMode(mode);

    // Detailed initialization logic will run below
    const baseUserId = sessionInfo?.data?.customer_id
      ? String(sessionInfo.data.customer_id)
      : (sessionInfo?.customer_id || customerProfile?.customer_id || sessionInfo?.phone || customerProfile?.phone_number || '');
    const defaults = {
      user_id: baseUserId,
      order_id: context.orderId || context.order_id || lastCompletedOrder?.orderId || '',
      product_sku: context.productSku || context.product_sku || context.sku || lastCompletedOrder?.product?.sku || '',
      product_name: context.productName || context.product_name || lastCompletedOrder?.product?.name || '',
      category: context.productCategory || context.category || lastCompletedOrder?.product?.category || '',
      brand: context.brand || lastCompletedOrder?.product?.brand || '',
      color: context.productColor || context.color || lastCompletedOrder?.product?.color || '',
      material: context.productMaterial || context.material || lastCompletedOrder?.product?.material || '',
      product_type: context.productType || context.product_type || lastCompletedOrder?.product?.productType || lastCompletedOrder?.product?.category || '',
    };

    try {
      switch (mode) {
        case 'return': {
          let reasons = returnReasons;
          if (!reasons.length) {
            const reasonResp = await getReturnReasons();
            reasons = reasonResp.return_reasons || [];
            setReturnReasons(reasons);
          }
          setSupportForm({
            user_id: defaults.user_id,
            order_id: defaults.order_id,
            product_sku: defaults.product_sku,
            reason_code: (reasons[0]?.code || returnReasons[0]?.code) || '',
            additional_comments: '',
            images: '',
          });
          break;
        }
        case 'exchange': {
          setSupportForm({
            user_id: defaults.user_id,
            order_id: defaults.order_id,
            product_sku: defaults.product_sku,
            current_size: context.current_size || '',
            requested_size: '',
            reason: '',
          });
          break;
        }
        case 'complaint': {
          let issues = issueTypes;
          if (!issues.length) {
            const issueResp = await getIssueTypes();
            issues = issueResp.issue_types || [];
            setIssueTypes(issues);
          }
          setSupportForm({
            user_id: defaults.user_id,
            order_id: defaults.order_id,
            issue_type: issues[0] || '',
            description: '',
            priority: 'medium',
          });
          break;
        }
        case 'feedback': {
          setSupportForm({
            user_id: defaults.user_id,
            product_sku: defaults.product_sku,
            size_purchased: context.size_purchased || '',
            fit_rating: 'perfect',
            length_feedback: 'not_specified',
            comments: '',
          });
          break;
        }
        default: {
          setSupportForm(defaults);
        }
      }
    } catch (error) {
      console.error('Failed to prepare post-purchase panel:', error);
      setSupportError(error.message || 'Failed to load data for this action.');
    } finally {
      setPanelInitializing(false);
    }
  };

  if (isInitializing) {
    return (
      <div className="flex items-center justify-center h-screen bg-[#efeae2]">
        <div className="bg-white/90 rounded-xl shadow-lg px-8 py-6 text-center">
          <p className="text-lg font-semibold text-[#008069]">Preparing your session...</p>
          <p className="text-sm text-gray-600 mt-2">
            {isLoadingSession ? 'Connecting you with the AI agent.' : 'Verifying your login details.'}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full min-h-0 bg-[#efeae2]">
      {/* Header - WhatsApp style */}
      <div className="bg-[#008069] text-white px-4 py-3 flex items-center justify-between shadow-md">
        <div className="flex items-center gap-3">
          <div className="relative">
            <div className="w-10 h-10 rounded-full bg-[#d9d9d9] flex items-center justify-center text-[#128c7e] font-semibold text-lg">
              AI
            </div>
            <div className="absolute bottom-0 right-0 w-3 h-3 bg-[#25d366] rounded-full border-2 border-[#008069]"></div>
          </div>
          <div>
            <h1 className="font-semibold text-base">AI Sales Agent</h1>
            <p className="text-xs text-[#d9d9d9]">online</p>
          </div>
        </div>
        <div className="flex items-center gap-4">
          {/* Loyalty Points & Tier Badge */}
          {loyaltyPoints > 0 && (
            <div className="flex items-center gap-2 bg-gradient-to-r from-yellow-500/20 to-orange-500/20 px-3 py-1.5 rounded-full border border-yellow-500/50">
              <span className="text-xl">
                {loyaltyTier === 'Platinum' ? '💎' : loyaltyTier === 'Gold' ? '🥇' : loyaltyTier === 'Silver' ? '🥈' : '🥉'}
              </span>
              <div className="text-sm">
                <div className="flex items-center gap-1">
                  <span className="font-bold text-white">{loyaltyPoints}</span>
                  <span className="text-xs text-yellow-200">pts</span>
                </div>
                <div className="text-[10px] text-yellow-300 -mt-0.5 font-semibold">{loyaltyTier}</div>
              </div>
            </div>
          )}
          <button className="hover:bg-[#017561] p-2 rounded-full transition-colors">
            <Video className="w-5 h-5" />
          </button>
          <button
            onClick={() => openSupportPanel('menu')}
            className="hover:bg-[#017561] p-2 rounded-full transition-colors"
            title="Post-purchase support"
          >
            <LifeBuoy className="w-5 h-5" />
          </button>
          <button
            onClick={() => navigate('/cart')}
            className="relative hover:bg-[#017561] p-2 rounded-full transition-colors"
            title="View Cart"
          >
            <ShoppingCart className="w-5 h-5" />
            {cartItems.length > 0 && (
              <span className="absolute -top-1 -right-1 w-4 h-4 bg-yellow-400 text-red-700 text-[10px] rounded-full flex items-center justify-center font-bold">
                {cartItems.reduce((sum, item) => sum + item.qty, 0)}
              </span>
            )}
          </button>
          <button
            onClick={() => navigate('/orders')}
            className="hover:bg-[#017561] p-2 rounded-full transition-colors"
            title="Your Orders"
          >
            <Package className="w-5 h-5" />
          </button>
          <button className="hover:bg-[#017561] p-2 rounded-full transition-colors">
            <Phone className="w-5 h-5" />
          </button>
          <button 
            onClick={endSession}
            className="hover:bg-[#c0392b] p-2 rounded-full transition-colors"
            title="End Session"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* Session Continuity Banner */}
      {sessionInfo && (
        <div className="bg-gradient-to-r from-[#dcf8c6] to-[#d0f4de] border-b-2 border-[#25d366] px-4 py-3 shadow-sm">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-2 h-2 bg-[#25d366] rounded-full animate-pulse"></div>
              <div className="text-sm">
                <span className="font-semibold text-[#075e54]">Session Active:</span>
                <span className="ml-2 text-[#128c7e] font-mono text-xs">{sessionInfo.session_id}</span>
              </div>
            </div>
            <div className="flex items-center gap-4 text-xs text-[#075e54]">
              <div className="flex items-center gap-1">
                <User className="w-3 h-3" />
                <span className="font-medium">{sessionInfo.phone}</span>
              </div>
              <div className="px-2 py-1 bg-white/50 rounded-full">
                <span className="font-semibold">📱 WhatsApp</span>
              </div>
              {sessionInfo.data?.chat_context?.length > 1 && (
                <div className="text-[#00796b] font-medium">
                  ↻ Session restored ({sessionInfo.data.chat_context.length} messages)
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Chat Messages Area */}
      <div 
        className="flex-1 overflow-y-auto px-4 py-6 space-y-3 min-h-0"
        style={{
          backgroundImage: 'url("data:image/svg+xml,%3Csvg width=\'100\' height=\'100\' xmlns=\'http://www.w3.org/2000/svg\'%3E%3Cg fill=\'%23d9d9d9\' fill-opacity=\'0.05\'%3E%3Cpath d=\'M0 0h50v50H0zM50 50h50v50H50z\'/%3E%3C/g%3E%3C/svg%3E")',
        }}
      >
        {messages.map((message) => (
          <div
            key={message.id}
            className={`flex ${message.sender === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[75%] md:max-w-[65%] rounded-lg px-4 py-2 shadow-sm ${
                message.sender === 'user'
                  ? 'bg-[#d9fdd3] text-gray-900'
                  : 'bg-white text-gray-900'
              }`}
            >
              <div className="text-sm leading-relaxed whitespace-pre-wrap break-words">
                {message.text && message.text.length > 800 && !expandedMessages.has(message.id)
                  ? renderMessageText(`${message.text.slice(0, 380)}... `, message.metadata)
                  : renderMessageText(message.text, message.metadata)}
                {message.text && message.text.length > 800 && (
                  <button
                    onClick={() => toggleExpandMessage(message.id)}
                    className="ml-1 text-xs text-[#00796b] font-medium hover:underline"
                  >
                    {expandedMessages.has(message.id) ? 'Show less' : 'Show more'}
                  </button>
                )}
              </div>

              {message.imagePreview && (
                <div className="mt-4 relative group">
                  <div className="relative overflow-hidden rounded-xl shadow-lg border-2 border-indigo-200 bg-gradient-to-br from-indigo-50 to-purple-50 p-2">
                    <img
                      src={message.imagePreview}
                      alt="Your uploaded image"
                      className="w-full max-w-xs mx-auto object-cover rounded-lg shadow-md transform transition-transform duration-300 group-hover:scale-105"
                    />
                    <div className="absolute top-3 right-3 bg-white/90 backdrop-blur-sm px-3 py-1 rounded-full text-xs font-semibold text-indigo-700 shadow-md">
                      📸 Your Image
                    </div>
                  </div>
                  <div className="mt-2 text-center text-xs text-gray-500 italic">
                    Searching our collection for similar styles...
                  </div>
                </div>
              )}
              
              {/* Product Cards */}
              {message.cards && message.cards.length > 0 && (
                <div className="mt-3 space-y-3">
                  {message.cards.map((card, idx) => (
                    <div key={idx} className="border border-gray-300 rounded-xl overflow-hidden bg-gradient-to-br from-white to-gray-50 hover:shadow-xl transition-all duration-300 transform hover:-translate-y-1">
                      <div className="flex gap-4 p-4">
                        {/* Premium Product Image */}
                        {card.image && (
                          <div className="flex-shrink-0 w-32 h-32 rounded-lg overflow-hidden bg-white shadow-md">
                            <img
                              src={resolveImageUrl(card.image)}
                              alt={card.name}
                              className="w-full h-full object-cover hover:scale-110 transition-transform duration-300"
                              onError={(e) => {
                                e.target.style.display = 'none';
                              }}
                            />
                          </div>
                        )}
                        <div className="flex-1 min-w-0">
                          <div className="flex items-start justify-between gap-2">
                            <div className="flex-1">
                              <a 
                                href={`/products/${card.sku}`} 
                                className="font-bold text-base text-blue-600 hover:text-blue-800 leading-tight underline cursor-pointer"
                              >
                                {card.name}
                              </a>
                              <p className="text-xs text-gray-500 mt-1 font-mono">{card.sku}</p>
                            </div>
                            {card.price && (
                              <div className="text-right">
                                <p className="text-lg font-bold text-emerald-600">₹{card.price.toLocaleString()}</p>
                                <p className="text-[10px] text-gray-500">Inclusive of taxes</p>
                              </div>
                            )}
                          </div>
                          
                          {/* Show personalized reason AND gift message (if present). Fall back to description only if neither exists. */}
                          {(card.personalized_reason || card.gift_message || card.description) && (
                            <div className="mt-3 text-sm text-gray-700 leading-relaxed">
                              {/* Personalized reason (primary) */}
                              {card.personalized_reason && (
                                <div className="mb-3 italic bg-indigo-50/50 border-l-4 border-indigo-400 px-3 py-2 rounded-r-lg">
                                  {card.personalized_reason.length > 240 && !expandedCards.has(`${message.id}-${idx}-pr`)
                                    ? renderMessageText(`${card.personalized_reason.slice(0, 220)}... `)
                                    : renderMessageText(card.personalized_reason)}
                                  {card.personalized_reason.length > 240 && (
                                    <button
                                      onClick={() => toggleExpandCard(`${message.id}-${idx}-pr`)}
                                      className="ml-1 text-xs text-indigo-600 font-semibold hover:underline"
                                    >
                                      {expandedCards.has(`${message.id}-${idx}-pr`) ? 'Show less' : 'Read more'}
                                    </button>
                                  )}
                                </div>
                              )}

                              {/* Gift message heading + message (italic, green, same size as description) */}
                              {card.gift_message && (
                                <div className="mb-3">
                                  <div className="text-xs font-semibold text-emerald-700 mb-1.5 uppercase tracking-wide">🎁 Gift Message</div>
                                  <div className="italic text-sm text-emerald-800 bg-emerald-50/50 border-l-4 border-emerald-400 px-3 py-2 rounded-r-lg">
                                    {(() => {
                                      const gm = normalizeQuotes(card.gift_message);
                                      if (!gm) return null;
                                      const short = gm.length > 240 && !expandedCards.has(`${message.id}-${idx}-gift`);
                                      return (
                                        <>
                                          {short ? `"${gm.slice(0,220)}..." ` : `"${gm}"`}
                                          {gm.length > 240 && (
                                            <button
                                              onClick={() => toggleExpandCard(`${message.id}-${idx}-gift`)}
                                              className="ml-1 text-xs text-emerald-600 font-semibold hover:underline"
                                            >
                                              {expandedCards.has(`${message.id}-${idx}-gift`) ? 'Show less' : 'Read more'}
                                            </button>
                                          )}
                                        </>
                                      );
                                    })()}
                                  </div>
                                </div>
                              )}

                              {/* If neither personalized nor gift message exist, show description with expand */}
                              {(!card.personalized_reason && !card.gift_message && card.description) && (
                                <div className="bg-gray-50/50 border-l-4 border-gray-400 px-3 py-2 rounded-r-lg">
                                  {card.description.length > 240 && !expandedCards.has(`${message.id}-${idx}`)
                                    ? renderMessageText(`${card.description.slice(0, 220)}... `)
                                    : renderMessageText(card.description)}
                                  {card.description.length > 240 && (
                                    <button
                                      onClick={() => toggleExpandCard(`${message.id}-${idx}`)}
                                      className="ml-1 text-xs text-gray-600 font-semibold hover:underline"
                                    >
                                      {expandedCards.has(`${message.id}-${idx}`) ? 'Show less' : 'Read more'}
                                    </button>
                                  )}
                                </div>
                              )}

                              {/* Gift suitability tag */}
                              {card.gift_suitability && (
                                <div className="mt-2 inline-block bg-amber-100 text-amber-900 px-3 py-1 rounded-full text-xs font-semibold shadow-sm">
                                  🎁 {card.gift_suitability}
                                </div>
                              )}
                            </div>
                          )}
                          
                          {/* Premium Purchase Buttons */}
                          <div className="mt-4 flex justify-end gap-2">
                            <button
                              onClick={() => handleAddToCart(card)}
                              className="bg-gradient-to-r from-orange-500 to-orange-600 hover:from-orange-600 hover:to-orange-700 text-white text-sm font-semibold px-4 py-2 rounded-lg transition-all duration-200 flex items-center gap-2 shadow-md hover:shadow-lg transform hover:scale-105"
                            >
                              <ShoppingCart className="w-4 h-4" />
                              Add to Cart
                            </button>
                            <button
                              onClick={() => handleBuyNow(card)}
                              disabled={isPaymentProcessing}
                              className="bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-700 hover:to-teal-700 disabled:from-gray-400 disabled:to-gray-500 text-white text-sm font-semibold px-4 py-2 rounded-lg transition-all duration-200 flex items-center gap-2 shadow-md hover:shadow-lg transform hover:scale-105 disabled:transform-none disabled:cursor-not-allowed"
                            >
                              <CreditCard className="w-4 h-4" />
                              Buy Now
                            </button>
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {message.checkout && (
                <div className="mt-3 border border-green-200 bg-green-50 rounded-lg p-3">
                  <div className="text-xs font-semibold uppercase tracking-wide text-green-700 mb-2">Checkout Summary</div>
                  <div className="text-sm text-gray-800 space-y-1">
                    <div className="font-medium">{message.checkout.product?.name || 'Selected product'}</div>
                    {message.checkout.product?.sku && (
                      <div className="text-xs text-gray-600">SKU: {message.checkout.product.sku}</div>
                    )}
                    <div className="text-sm font-semibold text-green-700">
                      Total: {formatINR(message.checkout.amount)}
                    </div>
                    {message.checkout.orderId && (
                      <div className="text-xs text-gray-600">Order ID: {message.checkout.orderId}</div>
                    )}
                  </div>
                  <button
                    onClick={() => handleCheckoutPayment(message.checkout)}
                    className="mt-3 inline-flex items-center justify-center gap-2 w-full px-3 py-2 bg-[#128c7e] text-white text-sm font-semibold rounded-lg hover:bg-[#0a6258] transition-colors disabled:cursor-not-allowed disabled:opacity-70"
                    disabled={isPaymentProcessing}
                  >
                    <CreditCard className="w-4 h-4" />
                    <span>Please Pay Here</span>
                  </button>
                  {isPaymentProcessing && (
                    <p className="mt-2 text-xs text-[#075e54]">Opening Razorpay checkout...</p>
                  )}
                </div>
              )}

              {message.stylistRecommendations && (
                <div className="mt-3 space-y-3">
                  {/* Header */}
                  <div className="border-l-4 border-[#25d366] bg-[#f0f9ff] rounded-r-lg p-3 mb-2">
                    <div className="text-xs font-semibold uppercase tracking-wide text-[#128c7e]">Stylist Picks</div>
                    <p className="text-sm text-[#075e54] mt-1">
                      {message.stylistRecommendations.purchasedProduct?.name
                        ? `Ideas to style your ${message.stylistRecommendations.purchasedProduct.name}.`
                        : 'Ideas to style your new purchase.'}
                    </p>
                  </div>

                  {/* Recommendation Product Cards - WhatsApp style */}
                  {message.stylistRecommendations.recommendedProducts?.length > 0 && (
                    <div className="space-y-2">
                      {message.stylistRecommendations.recommendedProducts.map((item, idx) => (
                        <div key={`${message.id}-stylist-${idx}`} className="bg-[#e8f5e9] rounded-2xl overflow-hidden hover:shadow-lg transition-shadow duration-200 border border-[#a5d6a7]">
                          <div className="flex gap-3 p-3">
                            {/* Premium Product Image */}
                            {item.image_url && (
                              <div className="flex-shrink-0 w-24 h-24 rounded-lg overflow-hidden bg-white shadow-sm border border-[#c8e6c9]">
                                <img 
                                  src={resolveImageUrl(item.image_url)} 
                                  alt={item.name}
                                  className="w-full h-full object-cover hover:scale-105 transition-transform duration-300"
                                  onError={(e) => {
                                    e.target.style.display = 'none';
                                  }}
                                />
                              </div>
                            )}
                            
                            {/* Product Info */}
                            <div className="flex-1 min-w-0">
                              <div className="flex items-start justify-between gap-2">
                                <div className="flex-1">
                                  <h4 className="font-bold text-sm text-[#075e54] leading-tight">
                                    {item.name || `Recommendation ${idx + 1}`}
                                  </h4>
                                  {item.brand && (
                                    <p className="text-xs text-[#566573] mt-1 font-medium">{item.brand}</p>
                                  )}
                                </div>
                                {item.price && (
                                  <div className="text-right flex-shrink-0">
                                    <p className="text-base font-bold text-[#25d366]">₹{item.price.toLocaleString()}</p>
                                    <p className="text-[10px] text-[#566573]">incl. tax</p>
                                  </div>
                                )}
                              </div>
                              
                              {/* Rating Badge */}
                              {item.rating && (
                                <div className="mt-2">
                                  <span className="inline-block bg-[#fff9c4] text-[#f57f17] text-xs font-semibold px-2 py-1 rounded-full">
                                    ⭐ {item.rating}
                                  </span>
                                </div>
                              )}
                              
                              {/* Personalized Reason */}
                              {(item.personalized_reason || item.gift_message) && (
                                <p className="mt-2 text-xs text-[#556571] italic leading-relaxed">
                                  {(item.personalized_reason || item.gift_message).length > 100 
                                    ? `${(item.personalized_reason || item.gift_message).slice(0, 95)}...` 
                                    : (item.personalized_reason || item.gift_message)}
                                </p>
                              )}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Styling Tips - if present */}
                  {message.stylistRecommendations.stylingTips?.length > 0 && (
                    <div className="border-l-4 border-[#25d366] bg-[#f0f9ff] rounded-r-lg p-3 mt-2">
                      <div className="text-xs font-semibold text-[#128c7e] uppercase tracking-wide">Styling Tips</div>
                      <ul className="mt-2 list-disc list-inside text-xs text-[#075e54] space-y-1">
                        {message.stylistRecommendations.stylingTips.map((tip, idx) => (
                          <li key={`${message.id}-stylist-tip-${idx}`}>{tip}</li>
                        ))}
                      </ul>
                    </div>

                  )}
                </div>
              )}

              {message.postPurchaseOptions && (
                <div className="mt-3 border-l-4 border-[#25d366] bg-[#f0f9ff] rounded-r-lg p-3">
                  <div className="text-xs font-semibold uppercase tracking-wide text-[#128c7e]">Post-Purchase Support</div>
                  <p className="text-sm text-[#075e54] mt-1">
                    {message.postPurchaseOptions.productName
                      ? `Need help with ${message.postPurchaseOptions.productName}? Choose an option below.`
                      : 'Need help after your purchase? Pick an option below.'}
                  </p>
                  <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {supportActions.map((action) => (
                      <button
                        key={action.key}
                        type="button"
                        onClick={() => openSupportPanel(action.key, message.postPurchaseOptions)}
                        className="text-left px-3 py-2 bg-white border border-[#a5d6a7] rounded-lg hover:bg-[#e8f5e9] transition-colors"
                      >
                        <div className="text-sm font-semibold text-[#128c7e]">{`${action.emoji} ${action.label}`}</div>
                        <div className="text-xs text-[#556571] mt-1">{action.caption}</div>
                      </button>
                    ))}
                  </div>
                </div>
              )}
              
              <div className={`flex items-center gap-1 justify-end mt-1 ${
                message.sender === 'user' ? 'text-gray-600' : 'text-gray-500'
              }`}>
                <span className="text-[10px]">{message.timestamp}</span>
                {message.sender === 'user' && getMessageStatusIcon(message.status)}
              </div>
            </div>
          </div>
        ))}

        {/* Typing Indicator */}
        {isTyping && (
          <div className="flex justify-start">
            <div className="bg-white rounded-lg px-4 py-3 shadow-sm">
              <div className="flex items-center gap-1">
                <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></div>
                <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></div>
                <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></div>
              </div>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input Area - WhatsApp style */}
      <div className="bg-[#f0f2f5] px-4 py-3 border-t border-gray-200">
        <div className="flex items-end gap-2">
          <div className="flex-1 bg-white rounded-full px-4 py-2 flex items-center gap-2 shadow-sm">
            <button className="text-gray-500 hover:text-gray-700">
              <svg className="w-6 h-6" fill="currentColor" viewBox="0 0 24 24">
                <path d="M9.153 11.603c.795 0 1.439-.879 1.439-1.962s-.644-1.962-1.439-1.962-1.439.879-1.439 1.962.644 1.962 1.439 1.962zm-3.204 1.362c-.026-.307-.131 5.218 6.063 5.551 6.066-.25 6.066-5.551 6.066-5.551-6.078 1.416-12.129 0-12.129 0zm11.363 1.108s-.669 1.959-5.051 1.959c-3.505 0-5.388-1.164-5.607-1.959 0 0 5.912 1.055 10.658 0zM11.804 1.011C5.609 1.011.978 6.033.978 12.228s4.826 10.761 11.021 10.761S23.02 18.423 23.02 12.228c.001-6.195-5.021-11.217-11.216-11.217zM12 21.354c-5.273 0-9.381-3.886-9.381-9.159s3.942-9.548 9.215-9.548 9.548 4.275 9.548 9.548c-.001 5.272-4.109 9.159-9.382 9.159zm3.108-9.751c.795 0 1.439-.879 1.439-1.962s-.644-1.962-1.439-1.962-1.439.879-1.439 1.962.644 1.962 1.439 1.962z"/>
              </svg>
            </button>
            <button
              type="button"
              onClick={handleImageUploadClick}
              disabled={isImageSearching}
              className={`transition-colors ${isImageSearching ? 'text-gray-300 cursor-not-allowed' : 'text-gray-500 hover:text-gray-700'}`}
              title={isImageSearching ? 'Searching by image...' : 'Upload image for visual search'}
            >
              <ImagePlus className="w-6 h-6" />
            </button>
            <input
              type="text"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onKeyPress={handleKeyPress}
              placeholder="Type a message"
              className="flex-1 bg-transparent outline-none text-sm placeholder:text-gray-500"
            />
            <button
              onClick={handlePaymentClick}
              disabled={!sessionToken || !isRazorpayReady || isPaymentProcessing}
              className={`transition-colors ${(!sessionToken || !isRazorpayReady || isPaymentProcessing) ? 'text-gray-300 cursor-not-allowed' : 'text-gray-500 hover:text-gray-700'}`}
              title={isPaymentProcessing ? 'Processing payment...' : 'Collect payment via Razorpay'}
            >
              <CreditCard className="w-6 h-6" />
            </button>
            <button 
              onClick={toggleVoiceRecording}
              className={`transition-colors ${
                isRecording 
                  ? 'text-red-500 hover:text-red-700 animate-pulse' 
                  : 'text-gray-500 hover:text-gray-700'
              }`}
              title={isRecording ? 'Stop recording' : 'Start voice input'}
            >
              {isRecording ? <MicOff className="w-6 h-6" /> : <Mic className="w-6 h-6" />}
            </button>
          </div>
          <button
            onClick={handleSendMessage}
            disabled={!inputText.trim()}
            className={`rounded-full p-3 transition-all ${
              inputText.trim()
                ? 'bg-[#008069] hover:bg-[#017561] text-white'
                : 'bg-gray-300 text-gray-500 cursor-not-allowed'
            }`}
          >
            <Send className="w-5 h-5" />
          </button>
        </div>
        <input
          ref={imageInputRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={handleImageUpload}
        />
      </div>

      {showAddressModal && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 px-4 py-6">
          <div className="bg-white w-full max-w-md rounded-2xl shadow-2xl overflow-hidden">
            <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
              <div>
                <h2 className="text-lg font-semibold text-gray-900">Add Delivery Address</h2>
                {pendingPaymentDetails?.details?.name && (
                  <p className="text-xs text-gray-500 mt-1">
                    For {pendingPaymentDetails.details.name}
                  </p>
                )}
              </div>
              <button
                type="button"
                onClick={closeAddressModal}
                className="p-2 rounded-full hover:bg-gray-100 text-gray-500"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <form onSubmit={submitAddressForm} className="px-6 py-4 space-y-4">
              {addressError && (
                <div className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">
                  {addressError}
                </div>
              )}
              <label className="block text-xs font-medium text-gray-600 uppercase">
                City
                <input
                  type="text"
                  value={addressForm.city}
                  onChange={(e) => handleAddressInputChange('city', e.target.value)}
                  className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none"
                  required
                  placeholder="e.g., Mumbai"
                />
              </label>
              <label className="block text-xs font-medium text-gray-600 uppercase">
                Landmark
                <input
                  type="text"
                  value={addressForm.landmark}
                  onChange={(e) => handleAddressInputChange('landmark', e.target.value)}
                  className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none"
                  required
                  placeholder="e.g., Near City Mall"
                />
              </label>
              <label className="block text-xs font-medium text-gray-600 uppercase">
                Building / House Name
                <input
                  type="text"
                  value={addressForm.building}
                  onChange={(e) => handleAddressInputChange('building', e.target.value)}
                  className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none"
                  required
                  placeholder="e.g., Sunrise Apartments"
                />
              </label>
              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={closeAddressModal}
                  className="px-4 py-2 text-sm font-medium text-gray-600 hover:text-gray-800"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 text-sm font-semibold text-white bg-[#128c7e] rounded-lg hover:bg-[#0a6258] transition-colors disabled:cursor-not-allowed disabled:opacity-70"
                  disabled={isPaymentProcessing}
                >
                  {isPaymentProcessing ? 'Processing...' : 'Continue to Payment'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {showSupportPanel && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4 py-6">
          <div className="bg-white w-full max-w-3xl rounded-2xl shadow-2xl overflow-hidden">
            <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
              <div>
                <h2 className="text-lg font-semibold text-gray-900">
                  {SUPPORT_TITLES[activeSupportMode] || SUPPORT_TITLES.menu}
                </h2>
                {activeSupportMode && activeSupportMode !== 'menu' && (supportContext.productName || lastCompletedOrder?.product?.name) && (
                  <p className="text-xs text-gray-500 mt-1">
                    For {supportContext.productName || lastCompletedOrder?.product?.name}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-2">
                {activeSupportMode !== 'menu' && (
                  <button
                    type="button"
                    onClick={() => openSupportPanel('menu', supportContext)}
                    className="px-3 py-1 text-xs font-medium text-emerald-700 border border-emerald-200 rounded-full hover:bg-emerald-50"
                  >
                    All options
                  </button>
                )}
                <button
                  type="button"
                  onClick={closeSupportPanel}
                  className="p-2 rounded-full hover:bg-gray-100 text-gray-500"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            </div>
            <div className="px-6 py-4 max-h-[70vh] overflow-y-auto">
              {panelInitializing ? (
                <p className="py-6 text-center text-sm text-gray-500">Loading details...</p>
              ) : activeSupportMode === 'menu' ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {supportActions.map((action) => (
                    <button
                      key={action.key}
                      type="button"
                      onClick={() => openSupportPanel(action.key, supportContext)}
                      className="text-left px-3 py-3 bg-emerald-50 border border-emerald-100 rounded-xl hover:bg-emerald-100 transition-colors"
                    >
                      <div className="text-sm font-semibold text-emerald-800">{`${action.emoji} ${action.label}`}</div>
                      <div className="text-xs text-emerald-700 mt-1">{action.caption}</div>
                    </button>
                  ))}
                </div>
              ) : (
                <form onSubmit={submitSupportForm} className="space-y-4">
                  {supportError && (
                    <div className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">
                      {supportError}
                    </div>
                  )}

                  {activeSupportMode === 'return' && (
                    <div className="grid gap-3">
                      <label className="block text-xs font-medium text-gray-600 uppercase">User ID
                        <input
                          type="text"
                          value={supportForm.user_id || ''}
                          readOnly
                          className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm bg-gray-100"
                        />
                      </label>
                      <label className="block text-xs font-medium text-gray-600 uppercase">Order ID
                        <input
                          type="text"
                          value={supportForm.order_id || ''}
                          readOnly
                          className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm bg-gray-100"
                        />
                      </label>
                      <label className="block text-xs font-medium text-gray-600 uppercase">Select Item to Return
                        <select
                          value={supportForm.product_sku || ''}
                          onChange={(e) => {
                            const selectedItem = (supportContext.orderItems || []).find(it => it.sku === e.target.value);
                            updateSupportForm('product_sku', e.target.value);
                            if (selectedItem) {
                              updateSupportForm('product_name', selectedItem.name || selectedItem.sku);
                            }
                          }}
                          className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none"
                          required
                        >
                          <option value="" disabled>Select an item from your order</option>
                          {(supportContext.orderItems || []).map((item) => (
                            <option key={item.sku} value={item.sku}>
                              {item.name || item.sku} {item.brand ? `(${item.brand})` : ''}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="block text-xs font-medium text-gray-600 uppercase">Return Reason
                        <select
                          value={supportForm.reason_code || ''}
                          onChange={(e) => updateSupportForm('reason_code', e.target.value)}
                          className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none"
                          required
                        >
                          <option value="" disabled>Select reason</option>
                          {(returnReasons.length ? returnReasons : [{ code: supportForm.reason_code, label: supportForm.reason_code }]).map((reason) => (
                            <option key={reason.code} value={reason.code}>
                              {reason.label || reason.code}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="block text-xs font-medium text-gray-600 uppercase">Additional Comments
                        <textarea
                          value={supportForm.additional_comments || ''}
                          onChange={(e) => updateSupportForm('additional_comments', e.target.value)}
                          className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none"
                          rows={3}
                          placeholder="Share any details for the pickup team"
                        />
                      </label>
                      {/* Image URLs input removed */}
                    </div>
                  )}

                  {activeSupportMode === 'exchange' && (
                    <div className="grid gap-3">
                      <label className="block text-xs font-medium text-gray-600 uppercase">User ID
                        <input
                          type="text"
                          value={supportForm.user_id || ''}
                          readOnly
                          className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm bg-gray-100"
                        />
                      </label>
                      <label className="block text-xs font-medium text-gray-600 uppercase">Order ID
                        <input
                          type="text"
                          value={supportForm.order_id || ''}
                          readOnly
                          className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm bg-gray-100"
                        />
                      </label>
                      <label className="block text-xs font-medium text-gray-600 uppercase">Select Item
                        <select
                          value={supportForm.product_sku || ''}
                          onChange={(e) => {
                            const selectedItem = (supportContext.orderItems || []).find(it => it.sku === e.target.value);
                            updateSupportForm('product_sku', e.target.value);
                            if (selectedItem) {
                              updateSupportForm('product_name', selectedItem.name || selectedItem.sku);
                            }
                          }}
                          className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none"
                          required
                        >
                          <option value="" disabled>Select an item from your order</option>
                          {(supportContext.orderItems || []).map((item) => (
                            <option key={item.sku} value={item.sku}>
                              {item.name || item.sku} {item.brand ? `(${item.brand})` : ''}
                            </option>
                          ))}
                        </select>
                      </label>
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <label className="block text-xs font-medium text-gray-600 uppercase">Current Size
                          <input
                            type="text"
                            value={supportForm.current_size || ''}
                            onChange={(e) => updateSupportForm('current_size', e.target.value)}
                            className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none"
                          />
                        </label>
                        <label className="block text-xs font-medium text-gray-600 uppercase">Requested Size
                          <input
                            type="text"
                            value={supportForm.requested_size || ''}
                            onChange={(e) => updateSupportForm('requested_size', e.target.value)}
                            className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none"
                            required
                          />
                        </label>
                      </div>
                      <label className="block text-xs font-medium text-gray-600 uppercase">Reason (optional)
                        <textarea
                          value={supportForm.reason || ''}
                          onChange={(e) => updateSupportForm('reason', e.target.value)}
                          className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none"
                          rows={2}
                        />
                      </label>
                    </div>
                  )}

                  {activeSupportMode === 'complaint' && (
                    <div className="grid gap-3">
                      <label className="block text-xs font-medium text-gray-600 uppercase">User ID
                        <input
                          type="text"
                          value={supportForm.user_id || ''}
                          readOnly
                          className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm bg-gray-100"
                        />
                      </label>
                      <label className="block text-xs font-medium text-gray-600 uppercase">Order ID
                        <input
                          type="text"
                          value={supportForm.order_id || ''}
                          readOnly
                          className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm bg-gray-100"
                        />
                      </label>
                      <label className="block text-xs font-medium text-gray-600 uppercase">Issue Type
                        <select
                          value={supportForm.issue_type || ''}
                          onChange={(e) => updateSupportForm('issue_type', e.target.value)}
                          className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none"
                          required
                        >
                          <option value="" disabled>Select issue</option>
                          {(issueTypes.length ? issueTypes : [supportForm.issue_type]).map((issue) => (
                            <option key={issue} value={issue}>
                              {issue}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="block text-xs font-medium text-gray-600 uppercase">Priority
                        <select
                          value={supportForm.priority || 'medium'}
                          onChange={(e) => updateSupportForm('priority', e.target.value)}
                          className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none"
                        >
                          <option value="low">Low</option>
                          <option value="medium">Medium</option>
                          <option value="high">High</option>
                        </select>
                      </label>
                      <label className="block text-xs font-medium text-gray-600 uppercase">Description
                        <textarea
                          value={supportForm.description || ''}
                          onChange={(e) => updateSupportForm('description', e.target.value)}
                          className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none"
                          rows={3}
                          required
                        />
                      </label>
                    </div>
                  )}


                  {activeSupportMode === 'feedback' && (
                    <div className="grid gap-3">
                      <label className="block text-xs font-medium text-gray-600 uppercase">User ID
                        <input
                          type="text"
                          value={supportForm.user_id || ''}
                          readOnly
                          className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm bg-gray-100"
                        />
                      </label>
                      <label className="block text-xs font-medium text-gray-600 uppercase">Select Item
                        <select
                          value={supportForm.product_sku || ''}
                          onChange={(e) => {
                            const selectedItem = (supportContext.orderItems || []).find(it => it.sku === e.target.value);
                            updateSupportForm('product_sku', e.target.value);
                            if (selectedItem) {
                              updateSupportForm('product_name', selectedItem.name || selectedItem.sku);
                            }
                          }}
                          className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none"
                          required
                        >
                          <option value="" disabled>Select an item from your order</option>
                          {(supportContext.orderItems || []).map((item) => (
                            <option key={item.sku} value={item.sku}>
                              {item.name || item.sku} {item.brand ? `(${item.brand})` : ''}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="block text-xs font-medium text-gray-600 uppercase">Size Purchased
                        <input
                          type="text"
                          value={supportForm.size_purchased || ''}
                          onChange={(e) => updateSupportForm('size_purchased', e.target.value)}
                          className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none"
                          required
                        />
                      </label>
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <label className="block text-xs font-medium text-gray-600 uppercase">Fit Rating
                          <select
                            value={supportForm.fit_rating || 'perfect'}
                            onChange={(e) => updateSupportForm('fit_rating', e.target.value)}
                            className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none"
                          >
                            <option value="too_tight">Too tight</option>
                            <option value="perfect">Perfect</option>
                            <option value="too_loose">Too loose</option>
                          </select>
                        </label>
                        <label className="block text-xs font-medium text-gray-600 uppercase">Length Feedback
                          <select
                            value={supportForm.length_feedback || 'not_specified'}
                            onChange={(e) => updateSupportForm('length_feedback', e.target.value)}
                            className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none"
                          >
                            <option value="not_specified">Not specified</option>
                            <option value="too_short">Too short</option>
                            <option value="perfect">Perfect</option>
                            <option value="too_long">Too long</option>
                          </select>
                        </label>
                      </div>
                      <label className="block text-xs font-medium text-gray-600 uppercase">Comments (optional)
                        <textarea
                          value={supportForm.comments || ''}
                          onChange={(e) => updateSupportForm('comments', e.target.value)}
                          className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none"
                          rows={3}
                        />
                      </label>
                    </div>
                  )}

                  {supportResult && supportResult.type === activeSupportMode && supportResult.summary && (
                    <div className="rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2 text-sm text-emerald-800 whitespace-pre-wrap">
                      {supportResult.summary}
                    </div>
                  )}

                  <div className="flex justify-end gap-2 pt-2">
                    <button
                      type="button"
                      onClick={closeSupportPanel}
                      className="px-4 py-2 rounded-lg border border-gray-200 text-sm text-gray-600 hover:bg-gray-50"
                    >
                      Close
                    </button>
                    <button
                      type="submit"
                      disabled={supportLoading}
                      className="px-4 py-2 rounded-lg bg-emerald-600 text-white text-sm font-semibold hover:bg-emerald-700 disabled:opacity-60"
                    >
                      {supportLoading ? 'Submitting...' : 'Submit'}
                    </button>
                  </div>
                </form>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Delivery Window Selection Modal */}
      {showDeliveryModal && deliveryOrderId && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full mx-4">
            {/* Modal Header */}
            <div className="bg-gradient-to-r from-blue-600 to-blue-700 text-white p-6">
              <h2 className="text-2xl font-bold">🚚 Select Your Delivery Slot</h2>
              <p className="text-blue-100 mt-2">Choose your preferred delivery time window</p>
            </div>

            {/* Modal Body */}
            <div className="p-8">
              <p className="text-gray-600 mb-6">
                Order ID: <span className="font-semibold text-gray-900">{deliveryOrderId}</span>
              </p>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
                {[
                  { id: 'morning', label: '🌅 Morning', time: '06:00 AM - 12:00 PM', description: 'Early bird delivery' },
                  { id: 'afternoon', label: '☀️ Afternoon', time: '12:00 PM - 06:00 PM', description: 'Standard delivery' },
                  { id: 'evening', label: '🌆 Evening', time: '06:00 PM - 10:00 PM', description: 'Late delivery' }
                ].map((window) => (
                  <button
                    key={window.id}
                    onClick={() => handleDeliveryWindowSelection(window.id)}
                    className={`p-4 rounded-lg border-2 transition-all ${
                      selectedDeliveryWindow === window.id
                        ? 'border-blue-600 bg-blue-50 ring-2 ring-blue-300'
                        : 'border-gray-200 hover:border-blue-400'
                    }`}
                  >
                    <div className="text-2xl mb-2">{window.label.split(' ')[0]}</div>
                    <div className="font-semibold text-gray-900">{window.label.split(' ').slice(1).join(' ')}</div>
                    <div className="text-sm text-gray-600 mt-1">{window.time}</div>
                    <div className="text-xs text-gray-500 mt-2">{window.description}</div>
                    {selectedDeliveryWindow === window.id && (
                      <div className="mt-3 flex items-center justify-center">
                        <CheckCircle className="w-5 h-5 text-blue-600" />
                      </div>
                    )}
                  </button>
                ))}
              </div>

              {/* Additional Options */}
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-6">
                <div className="flex items-start gap-3">
                  <div className="flex-shrink-0 w-6 h-6 rounded-full bg-blue-200 flex items-center justify-center text-xs font-bold text-blue-600">
                    ℹ️
                  </div>
                  <div>
                    <p className="font-semibold text-gray-900">💡 Tips for smooth delivery:</p>
                    <ul className="text-sm text-gray-700 mt-2 space-y-1 list-disc list-inside">
                      <li>Please keep your phone accessible during the delivery window</li>
                      <li>You'll receive an OTP on your registered number before delivery</li>
                      <li>Our delivery partner will contact you 30 minutes before arrival</li>
                    </ul>
                  </div>
                </div>
              </div>

              {/* Skip Button */}
              <div className="flex gap-3">
                <button
                  onClick={() => setShowDeliveryModal(false)}
                  className="flex-1 py-3 rounded-lg font-semibold bg-gray-300 text-gray-700 hover:bg-gray-400 transition-all"
                >
                  Skip for Now
                </button>
              </div>

              {selectedDeliveryWindow && (
                <p className="text-center text-sm text-green-600 mt-4">
                  ✓ Click on your preferred slot to confirm
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Toast Notification */}
      {toast.show && (
        <div className="fixed bottom-24 left-1/2 transform -translate-x-1/2 z-50 animate-fade-in-up">
          <div className="bg-gray-900 text-white px-6 py-3 rounded-lg shadow-lg flex items-center gap-2">
            <CheckCircle className="w-5 h-5 text-green-400" />
            <span className="font-medium">{toast.message}</span>
          </div>
        </div>
      )}
    </div>
  );
};

export default Chat;

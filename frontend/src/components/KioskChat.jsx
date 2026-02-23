import { useState, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Send, Mic, MicOff, Package, MapPin, User, ShoppingBag, X, Award } from 'lucide-react';
import API_ENDPOINTS from '../config/api';
import { getTierInfo } from '../services/loyaltyService';
import sessionStore from '../lib/session';
import { resolveImageUrl } from '../lib/utils.js';

const SESSION_API = API_ENDPOINTS.SESSION_MANAGER;
const SALES_API = API_ENDPOINTS.SALES_AGENT;

// Resolve card images - returns full URL for image display
const resolveCardImage = (card) => {
  if (!card) return null;
  if (card.image) return resolveImageUrl(card.image);
  if (card.primary_image) return resolveImageUrl(card.primary_image);
  if (card.image_url) return resolveImageUrl(card.image_url);
  if (Array.isArray(card.image_urls) && card.image_urls.length > 0) {
    return resolveImageUrl(card.image_urls[0]);
  }
  return null;
};

const KioskChat = () => {
  const navigate = useNavigate();

  // Session state
  const [sessionInfo, setSessionInfo] = useState(null);
  const [sessionToken, setSessionToken] = useState(null);
  const [customerProfile, setCustomerProfile] = useState(() => sessionStore.getProfile());
  const [isInitializing, setIsInitializing] = useState(true);
  const [isLoadingSession, setIsLoadingSession] = useState(false);
  const [loyaltyPoints, setLoyaltyPoints] = useState(0);
  const [loyaltyTier, setLoyaltyTier] = useState('Bronze');

  const [messages, setMessages] = useState([]);
  const [inputText, setInputText] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const messagesEndRef = useRef(null);
  const recognitionRef = useRef(null);
  const transcriptRef = useRef('');
  const [expandedMessages, setExpandedMessages] = useState(new Set());
  const [expandedCards, setExpandedCards] = useState(new Set());
  const [kioskSummary, setKioskSummary] = useState(null);
  const [showFeatured, setShowFeatured] = useState(true);

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

  const fetchLoyaltyDetails = async (userId) => {
    if (!userId) {
      setLoyaltyPoints(0);
      setLoyaltyTier('Bronze');
      return;
    }

    try {
      const result = await getTierInfo(userId);
      const points = Number(result?.points);
      setLoyaltyPoints(Number.isFinite(points) ? points : 0);
      setLoyaltyTier(result?.tier || 'Bronze');
    } catch (error) {
      console.error('Failed to fetch loyalty info:', error);
      setLoyaltyPoints(0);
      setLoyaltyTier('Bronze');
    }
  };

  const findFeaturedCard = () => {
    // Check most recent messages first
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m?.cards && m.cards.length > 0) return m.cards[0];
    }

    // Fallback: check sessionInfo.chat_context metadata
    const ctx = sessionInfo?.data?.chat_context || [];
    for (let i = ctx.length - 1; i >= 0; i--) {
      const m = ctx[i];
      if (m?.metadata?.cards && m.metadata.cards.length > 0) return m.metadata.cards[0];
    }

    return null;
  };

  const FeaturedProductBlock = ({ messages: _msgs, sessionInfo: _session }) => {
    try {
      const card = findFeaturedCard();
      if (!card || !showFeatured) return null;
      
      // Defensive checks for card object
      if (typeof card !== 'object' || !card.name) {
        console.warn('[FeaturedProductBlock] Invalid card object:', card);
        return null;
      }
      
      const featuredImage = resolveCardImage(card);

      return (
        <div className="mb-6 px-4">
          <div className="relative bg-gradient-to-br from-white to-gray-50 rounded-2xl shadow-lg border border-gray-200 p-4 max-w-md">
            <button onClick={() => setShowFeatured(false)} className="absolute top-3 right-3 p-1 rounded hover:bg-gray-100">
              <X className="w-4 h-4 text-gray-500" />
            </button>
            <p className="text-sm font-semibold text-gray-600 mb-3">FEATURED PRODUCT</p>
            <div className="flex gap-4 items-center">
              {featuredImage ? (
                <img src={featuredImage} alt={card.name || 'Product'} className="w-28 h-28 object-cover rounded-lg" onError={(e)=>e.target.style.display='none'} />
              ) : (
                <div className="w-28 h-28 bg-gradient-to-br from-gray-100 to-gray-200 rounded-lg flex items-center justify-center">
                  <ShoppingBag className="w-10 h-10 text-gray-400" />
                </div>
              )}
              <div className="flex-1">
                <a href={`/products/${card.sku || ''}`} className="font-semibold text-sm text-blue-600 hover:text-blue-800 underline cursor-pointer">
                  {card.name || 'Product'}
                </a>
                {card.price && <p className="text-sm font-bold text-green-600 mt-1">₹{card.price}</p>}
                <div className="mt-2 text-xs text-gray-500">
                  {card.personalized_reason ? (
                    <>
                      {card.personalized_reason.length > 120 && !expandedCards.has('featured')
                        ? `${card.personalized_reason.slice(0,110)}... `
                        : card.personalized_reason}
                      {card.personalized_reason.length > 120 && (
                        <button
                          onClick={(e) => {
                            e.preventDefault();
                            toggleExpandCard('featured');
                          }}
                          className="ml-1 text-xs text-[#00796b] font-medium hover:underline"
                        >
                          {expandedCards.has('featured') ? 'Show less' : 'Show more'}
                        </button>
                      )}
                    </>
                  ) : card.description ? (
                    <>
                      {card.description.length > 120 && !expandedCards.has('featured')
                        ? `${card.description.slice(0,110)}... `
                        : card.description}
                      {card.description.length > 120 && (
                        <button
                          onClick={(e) => {
                            e.preventDefault();
                            toggleExpandCard('featured');
                          }}
                          className="ml-1 text-xs text-[#00796b] font-medium hover:underline"
                        >
                          {expandedCards.has('featured') ? 'Show less' : 'Show more'}
                        </button>
                      )}
                    </>
                  ) : (
                    <span className="text-gray-400">Top trending for you</span>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      );
    } catch (error) {
      console.error('[FeaturedProductBlock] Uncaught error:', error);
      return null;
    }
  };

  // Helper to normalize surrounding quotes from messages so we only add one pair
  const normalizeQuotes = (text) => {
    if (!text) return '';
    return String(text).replace(/^["'“”\s]+|["'“”\s]+$/g, '').trim();
  };

  // Session Management Functions
  const startOrRestoreSession = async () => {
    setIsLoadingSession(true);
    try {
      const phone = sessionStore.getPhone();
      const profile = sessionStore.getProfile();
      const storedToken = sessionStore.getSessionToken();

      console.log('[KioskChat] Starting session init:', { phone, profile: profile?.customer_id, hasToken: !!storedToken });

      // If no login session exists, redirect to login (but don't clear other sessions)
      if (!phone || !profile) {
        console.warn('[KioskChat] No phone or profile found, redirecting to login');
        setIsLoadingSession(false);
        setIsInitializing(false);
        navigate('/login', { state: { redirectTo: '/kiosk' } });
        return;
      }

      setCustomerProfile(profile);

      const applyServerProfile = (serverProfile) => {
        if (!serverProfile || typeof serverProfile !== 'object') {
          return;
        }
        setCustomerProfile(serverProfile);
        sessionStore.setProfile(serverProfile);
      };

      const resolveUserId = (session, fallbackProfile) => (
        session?.customer_id
        || session?.data?.user_id
        || session?.user_id
        || session?.data?.customer_profile?.customer_id
        || session?.data?.customer_profile?.customerId
        || fallbackProfile?.customer_id
        || fallbackProfile?.customerId
        || null
      );

      if (storedToken) {
        try {
          console.log('[KioskChat] Attempting to restore session with token:', storedToken.substring(0, 20) + '...');
          const restoreResp = await fetch(`${SESSION_API}/session/restore`, {
            method: 'GET',
            headers: { 
              'X-Session-Token': storedToken,
              'X-Phone': phone || ''
            }
          });

          if (restoreResp.ok) {
            const restoreData = await restoreResp.json();
            const restoredSession = restoreData.session || {};
            
            // Ensure session always has proper data structure
            if (!restoredSession.data || typeof restoredSession.data !== 'object') {
              restoredSession.data = {
                cart: [],
                recent: [],
                chat_context: [],
                last_action: null,
                channels: [restoredSession.channel || 'kiosk'],
                conversation_summary: '',
                last_recommended_skus: []
              };
            }
            
            console.log('[KioskChat] ✅ Session restored successfully:', { 
              customer_id: restoredSession?.customer_id,
              hasData: !!restoredSession?.data,
              chatContextLength: restoredSession?.data?.chat_context?.length || 0,
              hasSummary: !!restoredSession?.data?.conversation_summary,
              channel: restoredSession?.channel
            });
            
            setSessionToken(storedToken);
            setSessionInfo(restoredSession);
            sessionStore.setPhone(phone);

            // Fetch AI-powered customer summary for Kiosk channel (third person POV)
            if (restoredSession?.data?.chat_context?.length > 0 || restoredSession?.data?.cart?.length > 0) {
              try {
                console.log('[KioskChat] 🖥️ Fetching AI-powered customer summary for Kiosk...');
                const summaryResp = await fetch(`${SALES_API}/api/chat-summary?session_token=${storedToken}&mode=kiosk`);
                if (summaryResp.ok) {
                  const summaryData = await summaryResp.json();
                  if (summaryData.has_summary && summaryData.summary) {
                    // Build kiosk_summary from Groq API
                    const kioskCtx = {
                      type: 'ai_summary',
                      title: 'Customer Intelligence',
                      summary: summaryData.summary,
                      details: {
                        cart_items: summaryData.cart_count || 0,
                        loyalty_tier: summaryData.loyalty_tier || 'Member',
                        loyalty_points: summaryData.loyalty_points || 0,
                        mode: summaryData.mode
                      },
                      cart: restoredSession?.data?.cart || [],
                      last_recommended: restoredSession?.data?.last_recommended_skus || []
                    };
                    setKioskSummary(kioskCtx);
                    console.log('[KioskChat] ✅ AI-powered customer summary loaded:', kioskCtx);
                  }
                }
              } catch (ctxErr) {
                console.warn('[KioskChat] Could not fetch AI summary:', ctxErr);
              }
            }

            applyServerProfile(restoredSession?.data?.customer_profile);

            const userId = resolveUserId(restoredSession, profile);
            try {
              await fetchLoyaltyDetails(userId);
            } catch (loyaltyErr) {
              console.warn('[KioskChat] Loyalty fetch failed (non-blocking):', loyaltyErr);
            }

            if (Array.isArray(restoredSession.data?.chat_context) && restoredSession.data.chat_context.length) {
              try {
                const chatMessages = restoredSession.data.chat_context.map((msg, idx) => ({
                  id: idx + 1,
                  text: msg.message || msg.text || '',
                  sender: msg.sender === 'user' ? 'user' : 'agent',
                  timestamp: msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString('en-US', {
                    hour: '2-digit',
                    minute: '2-digit',
                    hour12: true,
                  }) : new Date().toLocaleTimeString('en-US', {
                    hour: '2-digit',
                    minute: '2-digit',
                    hour12: true,
                  }),
                  cards: (msg.metadata?.cards && Array.isArray(msg.metadata.cards)) ? msg.metadata.cards : [],
                }));
                setMessages(chatMessages);
              } catch (mapErr) {
                console.warn('[KioskChat] Failed to map chat_context to messages:', mapErr);
                setMessages([]);
              }
            }
            return;
          } else {
            console.warn('[KioskChat] Failed to restore session:', restoreResp.status, await restoreResp.text());
          }
        } catch (err) {
          console.warn('[KioskChat] Stored session restore failed, attempting fresh session', err);
        }
      }

      console.log('[KioskChat] Creating new session with:', { phone, channel: 'kiosk', customer_id: profile.customer_id || profile.customerId });
      const response = await fetch(`${SESSION_API}/session/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          phone,
          channel: 'kiosk',
          customer_id: profile.customer_id || profile.customerId || undefined,
        })
      });

      console.log('[KioskChat] Session start response:', response.status);

      if (!response.ok) {
        const errorText = await response.text();
        console.error('[KioskChat] ❌ Session creation failed:', response.status, errorText);
        throw new Error(`Failed to create session: ${response.status} ${errorText}`);
      }

      const data = await response.json();
      console.log('[KioskChat] ✅ New session created:', { 
        token: data.session_token?.substring(0, 20) + '...', 
        customer_id: data.session?.customer_id 
      });
      
      setSessionToken(data.session_token);
      sessionStore.setSessionToken(data.session_token);
      sessionStore.setPhone(phone);
      setSessionInfo(data.session);

      applyServerProfile(data.session?.data?.customer_profile);

      const userId = resolveUserId(data.session, profile);
      try {
        await fetchLoyaltyDetails(userId);
      } catch (loyaltyErr) {
        console.warn('[KioskChat] Loyalty fetch failed (non-blocking):', loyaltyErr);
      }

      if (data.session.data.chat_context && data.session.data.chat_context.length > 0) {
        const chatMessages = data.session.data.chat_context.map((msg, idx) => ({
          id: idx + 1,
          text: msg.message,
          sender: msg.sender === 'user' ? 'user' : 'agent',
          timestamp: new Date(msg.timestamp).toLocaleTimeString('en-US', {
            hour: '2-digit',
            minute: '2-digit',
            hour12: true,
          }),
          cards: msg.metadata?.cards || [],
        }));
        setMessages(chatMessages);
      } else {
        setMessages([{
          id: 1,
          text: "Welcome to Aditya Birla Fashion & Retail! I'm your in-store digital assistant. How may I help you today?",
          sender: 'agent',
          timestamp: new Date().toLocaleTimeString('en-US', {
            hour: '2-digit',
            minute: '2-digit',
            hour12: true,
          }),
        }]);
      }
    } catch (error) {
      console.error('[KioskChat] ❌ Session error:', error.message, error);
      console.error('[KioskChat] Error stack:', error.stack);
      sessionStore.clearAll();
      setIsLoadingSession(false);
      setIsInitializing(false);
      alert(`We could not create your session. Please log in again.\n\nError: ${error.message}`);
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
      setLoyaltyPoints(0);
      setLoyaltyTier('Bronze');
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

  // Add initialization timeout to prevent infinite loading
  useEffect(() => {
    const timer = setTimeout(() => {
      if (isInitializing) {
        console.warn('[KioskChat] Session initialization timeout - forcing fallback');
        setIsInitializing(false);
        setIsLoadingSession(false);
      }
    }, 15000); // 15 second timeout
    
    return () => clearTimeout(timer);
  }, [isInitializing]);

  // Auto-scroll to bottom
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isTyping]);

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
    startOrRestoreSession();
  }, [navigate]);

  // Fetch customer context for Kiosk when session is ready
  useEffect(() => {
    const fetchKioskContext = async () => {
      if (!sessionToken || !sessionInfo) return;
      if (sessionInfo.channel !== 'kiosk') return;
      if (!sessionInfo.data?.conversation_summary) return;
      if (kioskSummary) return; // Already have summary

      try {
        console.log('[KioskChat] 📊 Fetching customer context via useEffect...');
        const resp = await fetch(`${SALES_API}/api/customer-context?session_token=${sessionToken}`);
        if (resp.ok) {
          const data = await resp.json();
          if (data.has_context && data.summary) {
            const ctx = {
              type: 'customer_context',
              title: 'Customer Context',
              summary: data.summary.text,
              details: {
                cart_items: data.summary.cart_items || 0,
                last_action: data.summary.last_action || 'browsing',
                products_viewed: data.last_products_viewed?.length || 0,
                previous_channels: data.summary.previous_channels || [],
                interaction_count: data.summary.interactions || 0
              },
              cart: data.cart || [],
              last_recommended: data.last_products_viewed || []
            };
            setKioskSummary(ctx);
            console.log('[KioskChat] ✅ Context loaded via useEffect:', ctx);
          }
        }
      } catch (err) {
        console.warn('[KioskChat] Failed to fetch context via useEffect:', err);
      }
    };

    fetchKioskContext();
  }, [sessionToken, sessionInfo, kioskSummary]);

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

  // Mock bot responses
  const mockBotResponses = [
    "I'd be happy to help you with that. Let me check our latest collection for you.",
    "That's a great choice! We have several options that match your preference.",
    "Our premium collection features top brands like Van Heusen, Allen Solly, and Louis Philippe.",
    "Would you like to see items available in your size and preferred color?",
    "I can help you find the perfect match. What's your budget range?",
    "Excellent! Let me find the best options for you from our in-store inventory."
  ];

  const handleSendMessage = async () => {
    if (!inputText.trim() || !sessionToken) return;

    const messageText = inputText;
    setInputText('');

    // Add user message
    const userMessage = {
      id: Date.now(),
      text: messageText,
      sender: 'user',
      timestamp: new Date().toLocaleTimeString('en-US', { 
        hour: '2-digit', 
        minute: '2-digit',
        hour12: true 
      })
    };

    setMessages(prev => [...prev, userMessage]);

    // Save to backend
    await saveChatMessage('user', messageText);

    // Show typing indicator
    setIsTyping(true);

    // Call Sales Agent API
    try {
      const payload = {
        message: messageText,
        session_token: sessionToken,
        metadata: { user_id: sessionInfo?.data?.customer_id || sessionInfo?.phone }
      };

      const resp = await fetch(`${SALES_API}/api/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      setIsTyping(false);

      if (!resp.ok) throw new Error('Agent error');

      const data = await resp.json();
      const botText = data.reply || 'Sorry, I could not process that.';

      console.log('[KioskChat] 📨 Agent response received:', {
        hasMetadata: !!data.metadata,
        hasKioskSummary: !!data.metadata?.kiosk_summary,
        channel: data.metadata?.channel,
        metadata: data.metadata
      });

      // Extract Kiosk summary if available
      if (data.metadata?.kiosk_summary) {
        setKioskSummary(data.metadata.kiosk_summary);
        console.log('[KioskChat] 📋 Customer context updated:', data.metadata.kiosk_summary);
      } else {
        console.log('[KioskChat] ⚠️ No kiosk_summary in response metadata');
      }

      // Update session token if returned
      if (data.session_token) setSessionToken(data.session_token);

      const agentMessage = {
        id: Date.now() + 1,
        text: botText,
        sender: 'agent',
        timestamp: new Date().toLocaleTimeString('en-US', { 
          hour: '2-digit', 
          minute: '2-digit',
          hour12: true 
        }),
        cards: data.cards || []
      };

      setMessages(prev => [...prev, agentMessage]);
      await saveChatMessage('agent', botText, { cards: agentMessage.cards });
    } catch (error) {
      setIsTyping(false);
      console.error('Agent call failed:', error);
      const failMsg = {
        id: Date.now() + 2,
        text: 'Sorry, I could not reach the assistant. Please try again later.',
        sender: 'agent',
        timestamp: new Date().toLocaleTimeString('en-US', { 
          hour: '2-digit', 
          minute: '2-digit',
          hour12: true 
        })
      };
      setMessages(prev => [...prev, failMsg]);
      await saveChatMessage('agent', failMsg.text);
    }
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

  const handleQuickAction = (action) => {
    const quickMessages = {
      browse: "Show me your latest products",
      track: "I want to track my order",
      availability: "Check product availability in store",
      expert: "I'd like to speak with a sales expert"
    };

    const message = quickMessages[action];
    setInputText(message);
  };

  // Previous phone input UI removed in favor of login-first flow

  const activeProfile = customerProfile || sessionInfo?.data?.customer_profile || null;
  
  const displayName = activeProfile?.name?.trim() || 'Guest';
  const firstName = activeProfile?.name ? displayName.split(' ')[0] || displayName : 'Guest';
  const displayCity = activeProfile?.city?.trim() || '';
  const normalizedPoints = Number.isFinite(loyaltyPoints) ? loyaltyPoints : Number(loyaltyPoints) || 0;
  const loyaltyTierLabel = loyaltyTier || 'Bronze';
  const loyaltyPointsLabel = new Intl.NumberFormat('en-IN').format(Math.max(0, Math.round(normalizedPoints)));

  if (isInitializing) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-[#f9e6d3] via-[#fbe9e7] to-[#f1f8e9] flex items-center justify-center">
        <div className="bg-white shadow-2xl rounded-3xl p-10 max-w-lg w-full border border-[#ffe0b2] text-center">
          <div className="inline-flex items-center justify-center w-24 h-24 rounded-full bg-gradient-to-r from-[#bf360c] to-[#f57c00] text-white shadow-xl">
            <ShoppingBag className="w-12 h-12" />
          </div>
          <h2 className="mt-6 text-3xl font-bold text-[#4e342e]">Preparing kiosk session...</h2>
          <p className="mt-3 text-sm text-[#6d4c41]">
            {isLoadingSession ? 'Linking your profile across channels.' : 'Verifying your login details.'}
          </p>
          <p className="mt-4 text-xs text-[#8d6e63]">If this takes too long, please refresh the page.</p>
        </div>
      </div>
    );
  }

  // Fallback: if we somehow got past initialization but have no session, show error
  if (!sessionInfo || !sessionToken) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-red-50 to-orange-50 flex items-center justify-center p-4">
        <div className="bg-white shadow-2xl rounded-3xl p-10 max-w-lg w-full border border-red-200 text-center">
          <div className="inline-flex items-center justify-center w-24 h-24 rounded-full bg-red-100 text-red-600 shadow-xl">
            <X className="w-12 h-12" />
          </div>
          <h2 className="mt-6 text-2xl font-bold text-red-700">Session Error</h2>
          <p className="mt-3 text-sm text-gray-600">We couldn't load your kiosk session. Please log in again.</p>
          <button
            onClick={() => {
              sessionStore.clearAll();
              navigate('/login', { replace: true });
            }}
            className="mt-6 bg-red-600 hover:bg-red-700 text-white font-bold py-3 px-6 rounded-lg transition-colors"
          >
            Return to Login
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen bg-gradient-to-br from-gray-50 to-gray-100">
      {/* Header Bar - ABFRL Branding */}
      {/* Header Bar - ABFRL Branding */}
      <div className="bg-gradient-to-r from-[#8B1538] to-[#A91D3A] text-white px-8 py-6 shadow-lg">
        <div className="flex items-center justify-between max-w-[1600px] mx-auto">
          <div className="flex items-center gap-6">
            <div className="flex items-center gap-3">
              <div className="w-16 h-16 bg-white rounded-lg flex items-center justify-center shadow-md">
                <span className="text-3xl font-bold bg-gradient-to-br from-[#8B1538] to-[#D4AF37] bg-clip-text text-transparent">
                  AB
                </span>
              </div>
              <div>
                <h1 className="text-2xl font-bold tracking-wide">Aditya Birla Fashion & Retail</h1>
                <p className="text-sm text-gray-200 font-light">In-Store Digital Assistant</p>
                {activeProfile?.name && (
                  <p className="text-xs text-[#fef3c7] mt-1">Welcome back, {firstName}!</p>
                )}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="px-4 py-2 bg-white/10 rounded-full backdrop-blur-sm">
              <span className="text-sm font-medium">Store Open: 10 AM - 9 PM</span>
            </div>
            {sessionInfo && (
              <button
                onClick={endSession}
                className="px-4 py-2 bg-red-600 hover:bg-red-700 rounded-full backdrop-blur-sm transition-colors"
                title="End Session"
              >
                <span className="text-sm font-medium flex items-center gap-2">
                  <X className="w-4 h-4" />
                  End Session
                </span>
              </button>
            )}
          </div>
        </div>
        <div className="h-1 bg-gradient-to-r from-[#D4AF37] via-[#F5DEB3] to-[#D4AF37] mt-4 rounded-full opacity-80"></div>
      </div>

      {/* Session Continuity Banner */}
      {sessionInfo && (
        <div className="bg-gradient-to-r from-[#f0f9ff] to-[#e0f2fe] border-b-2 border-[#3b82f6] px-8 py-4 shadow-md max-w-[1600px] mx-auto w-full">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <div className="w-3 h-3 bg-[#3b82f6] rounded-full animate-pulse"></div>
              <div className="text-sm">
                <span className="font-semibold text-[#1e40af]">Active Session:</span>
                <span className="ml-2 text-[#3b82f6] font-mono text-xs bg-white px-2 py-1 rounded">{sessionInfo.session_id}</span>
              </div>
            </div>
            <div className="flex items-center gap-6 text-sm">
              <div className="flex items-center gap-3 bg-white px-3 py-2 rounded-lg shadow-sm">
                <User className="w-4 h-4 text-[#1e40af]" />
                <div className="leading-tight">
                  <span className="font-semibold text-[#1e40af] block">{displayName}</span>
                  {(sessionInfo.phone || displayCity) && (
                    <span className="text-[11px] text-[#475569]">
                      {sessionInfo.phone}
                      {sessionInfo.phone && displayCity ? ' • ' : ''}
                      {displayCity}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2 bg-white px-3 py-2 rounded-lg shadow-sm border border-[#facc15]/40">
                <Award className="w-4 h-4 text-[#b45309]" />
                <div className="leading-tight">
                  <span className="font-semibold text-[#92400e] block">{loyaltyTierLabel} Member</span>
                  <span className="text-[11px] text-[#b45309]">{loyaltyPointsLabel} pts balance</span>
                </div>
              </div>
              <div className="px-3 py-2 bg-gradient-to-r from-[#8B1538] to-[#A91D3A] text-white rounded-lg shadow-sm">
                <span className="font-semibold">🏬 In-Store Kiosk</span>
              </div>
              {sessionInfo.data?.chat_context?.length > 1 && (
                <div className="flex items-center gap-2 text-[#059669] font-semibold bg-white px-3 py-2 rounded-lg shadow-sm">
                  <span className="text-lg">↻</span>
                  <span>Session restored ({sessionInfo.data.chat_context.length} messages)</span>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col max-w-[1600px] mx-auto w-full px-8 py-6">
        
        {/* Customer Intelligence Summary (Primary View for Sales Staff) */}
        {kioskSummary ? (
          <div className="mb-6 bg-gradient-to-r from-red-50 via-rose-50 to-pink-50 border-2 border-[#8B1538] rounded-2xl p-8 shadow-2xl animate-fadeIn">
            <div className="flex items-start justify-between">
              <div className="flex-1">
                <div className="flex items-center gap-4 mb-5">
                  <div className="w-14 h-14 bg-gradient-to-br from-[#8B1538] to-[#A91D3A] rounded-full flex items-center justify-center shadow-lg">
                    <User className="w-7 h-7 text-white" />
                  </div>
                  <div className="flex-1">
                    <h3 className="text-2xl font-bold text-[#8B1538] mb-1">
                      {kioskSummary.title || 'Customer Intelligence'}
                    </h3>
                    <div className="flex items-center gap-3">
                      <span className="text-sm bg-gradient-to-r from-[#8B1538] to-[#A91D3A] text-white px-4 py-1.5 rounded-full font-semibold shadow-md">
                        🤖 AI-Powered Insights
                      </span>
                      {kioskSummary.details?.loyalty_tier && (
                        <span className="text-sm bg-gradient-to-r from-[#D4AF37] to-[#F5DEB3] text-[#4e342e] px-4 py-1.5 rounded-full font-semibold shadow-md">
                          ⭐ {kioskSummary.details.loyalty_tier} Member
                        </span>
                      )}
                    </div>
                  </div>
                </div>
                
                <div className="bg-white rounded-xl p-6 shadow-md border-2 border-[#ffe0b2] mb-5">
                  <p className="text-lg text-gray-800 leading-relaxed font-medium whitespace-pre-wrap">
                    {(() => {
                      const cleanSummary = (kioskSummary.summary || '').replace(/\*\*/g, '');

                      // If name already present, don't duplicate
                      if (cleanSummary.toLowerCase().includes(firstName.toLowerCase())) {
                        return cleanSummary;
                      }

                      return `👋 ${firstName} is back in-store today.\n\n${cleanSummary}`;
                    })()}
                  </p>
                </div>

                <div className="flex flex-wrap gap-3 mb-5">
                  {kioskSummary.details?.cart_items > 0 && (
                    <div className="flex items-center gap-2 bg-gradient-to-r from-green-100 to-emerald-100 px-4 py-2.5 rounded-xl shadow-md border-2 border-green-400">
                      <ShoppingBag className="w-5 h-5 text-green-700" />
                      <span className="text-sm font-bold text-green-800">
                        {kioskSummary.details.cart_items} {kioskSummary.details.cart_items === 1 ? 'item' : 'items'} in cart
                      </span>
                    </div>
                  )}
                  
                  {kioskSummary.details?.loyalty_points > 0 && (
                    <div className="flex items-center gap-2 bg-gradient-to-r from-[#fef3c7] to-[#fde68a] px-4 py-2.5 rounded-xl shadow-md border-2 border-[#D4AF37]">
                      <Award className="w-5 h-5 text-[#b45309]" />
                      <span className="text-sm font-bold text-[#92400e]">
                        {new Intl.NumberFormat('en-IN').format(kioskSummary.details.loyalty_points)} points available
                      </span>
                    </div>
                  )}
                  
                  {kioskSummary.details?.interaction_count > 0 && (
                    <div className="flex items-center gap-2 bg-gradient-to-r from-[#fce7f3] to-[#fbcfe8] px-4 py-2.5 rounded-xl shadow-md border-2 border-[#A91D3A]">
                      <div className="w-5 h-5 bg-[#8B1538] rounded-full"></div>
                      <span className="text-sm font-bold text-[#831843]">
                        {kioskSummary.details.interaction_count} previous interactions
                      </span>
                    </div>
                  )}

                  {kioskSummary.details?.previous_channels?.length > 0 && (
                    <div className="flex items-center gap-2 bg-gradient-to-r from-blue-100 to-sky-100 px-4 py-2.5 rounded-xl shadow-md border-2 border-blue-400">
                      <span className="text-lg">📱</span>
                      <span className="text-sm font-bold text-blue-800">
                        Used: {kioskSummary.details.previous_channels.join(', ')}
                      </span>
                    </div>
                  )}

                  {kioskSummary.details?.last_action && (
                    <div className="flex items-center gap-2 bg-gradient-to-r from-orange-100 to-red-100 px-4 py-2.5 rounded-xl shadow-md border-2 border-orange-400">
                      <span className="text-lg">🏷️</span>
                      <span className="text-sm font-bold text-orange-800">
                        Last action: {kioskSummary.details.last_action.replace('_', ' ')}
                      </span>
                    </div>
                  )}
                </div>

                {kioskSummary.cart && kioskSummary.cart.length > 0 && (
                  <div className="bg-white rounded-xl p-5 border-2 border-green-300 shadow-md mb-4">
                    <p className="text-sm font-bold text-green-900 mb-3 flex items-center gap-2">
                      <ShoppingBag className="w-4 h-4" />
                      Cart Items:
                    </p>
                    <div className="space-y-2">
                      {kioskSummary.cart.slice(0, 3).map((item, idx) => (
                        <div key={idx} className="flex items-center justify-between text-sm bg-gradient-to-r from-gray-50 to-green-50 rounded-lg p-3 border border-green-200">
                          <span className="text-gray-700 font-medium flex-1">• {item.name || item.sku}</span>
                          <span className="text-green-700 font-bold">₹{item.price}</span>
                          {item.quantity > 1 && (
                            <span className="text-xs text-gray-500 ml-2 bg-white px-2 py-1 rounded-full">x{item.quantity}</span>
                          )}
                        </div>
                      ))}
                      {kioskSummary.cart.length > 3 && (
                        <p className="text-sm text-[#8B1538] italic mt-2 font-medium">
                          +{kioskSummary.cart.length - 3} more items in cart
                        </p>
                      )}
                    </div>
                  </div>
                )}

                {kioskSummary.details?.cart_items > 0 && (
                  <div className="inline-flex items-center gap-3 bg-gradient-to-r from-green-500 to-emerald-600 px-5 py-3 rounded-full border-2 border-green-400 shadow-lg">
                    <div className="w-3 h-3 bg-white rounded-full animate-pulse"></div>
                    <span className="text-sm font-bold text-white">✅ Customer Ready to Checkout</span>
                  </div>
                )}
              </div>

            </div>
          </div>
        ) : (
          <div className="mb-6 bg-gradient-to-r from-gray-50 to-gray-100 border-2 border-gray-300 rounded-xl p-6 shadow-md">
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 bg-gray-400 rounded-full flex items-center justify-center">
                <User className="w-5 h-5 text-white" />
              </div>
              <h3 className="text-lg font-bold text-gray-700">No Customer Context Available</h3>
            </div>
            <p className="text-sm text-gray-600 leading-relaxed">
              This customer hasn't interacted with our platform before, or their session data is not available. You can start by asking what they're looking for today.
            </p>
          </div>
        )}
        
        {/* Chat Messages Area - HIDDEN FOR KIOSK (Summary-Only Mode) */}
        <div className="hidden flex-1 overflow-y-auto mb-6 px-4 space-y-4 scrollbar-thin scrollbar-thumb-gray-300 scrollbar-track-transparent">
          {messages.map((message) => (
            <div
              key={message.id}
              className={`flex ${message.sender === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-[60%] rounded-2xl px-6 py-4 shadow-md ${
                  message.sender === 'user'
                    ? 'bg-gradient-to-br from-[#8B1538] to-[#A91D3A] text-white'
                    : 'bg-white text-gray-800 border border-gray-100'
                }`}
              >
                <p className="text-base leading-relaxed mb-2">{message.text}</p>

                {/* Product cards (if any) */}
                {message.cards && message.cards.length > 0 && (
                  <div className="mt-3 space-y-3">
                    {message.cards.map((card, idx) => {
                      const cardImage = resolveCardImage(card);
                      return (
                        <div 
                          key={idx} 
                          className="border border-gray-200 rounded-lg p-3 bg-gray-50 hover:shadow-md hover:bg-gray-100 transition-all"
                        >
                          <div className="flex gap-3">
                            {cardImage && (
                              <img src={cardImage} alt={card.name} className="w-20 h-20 object-cover rounded" onError={(e)=>e.target.style.display='none'} />
                            )}
                            <div className="flex-1">
                            <a 
                              href={`/products/${card.sku}`}
                              className="font-semibold text-sm text-blue-600 hover:text-blue-800 underline cursor-pointer"
                            >
                              {card.name}
                            </a>
                            <p className="text-xs text-gray-600 mt-1">{card.sku}</p>
                            {card.price && (<p className="text-sm font-bold text-green-600 mt-1">₹{card.price}</p>)}

                            {(card.personalized_reason || card.gift_message || card.description) && (
                              <div className="mt-2 text-xs text-gray-500">
                                {card.personalized_reason && (
                                  <div className="mb-2">
                                    {card.personalized_reason.length > 240 && !expandedCards.has(`${message.id}-${idx}-pr`)
                                      ? `${card.personalized_reason.slice(0,220)}... `
                                      : card.personalized_reason}
                                    {card.personalized_reason.length > 240 && (
                                      <button
                                        onClick={(e) => {
                                          e.preventDefault();
                                          toggleExpandCard(`${message.id}-${idx}-pr`);
                                        }}
                                        className="ml-1 text-xs text-[#00796b] font-medium hover:underline"
                                      >
                                        {expandedCards.has(`${message.id}-${idx}-pr`) ? 'Show less' : 'Show more'}
                                      </button>
                                    )}
                                  </div>
                                )}

                                {card.gift_message && (
                                  <div className="mb-2">
                                    <div className="text-xs font-medium text-[#075e54] mb-1">Gift message to attach:</div>
                                    <div className="italic text-xs text-[#0b6655]">
                                      {(() => {
                                        const gm = normalizeQuotes(card.gift_message);
                                        if (!gm) return null;
                                        const short = gm.length > 240 && !expandedCards.has(`${message.id}-${idx}-gift`);
                                        return (
                                          <>
                                            {short ? `"${gm.slice(0,220)}..." ` : `"${gm}"`}
                                            {gm.length > 240 && (
                                              <button
                                                onClick={(e) => {
                                                  e.preventDefault();
                                                  toggleExpandCard(`${message.id}-${idx}-gift`);
                                                }}
                                                className="ml-1 text-xs text-[#00796b] font-medium hover:underline"
                                              >
                                                {expandedCards.has(`${message.id}-${idx}-gift`) ? 'Show less' : 'Show more'}
                                              </button>
                                            )}
                                          </>
                                        );
                                      })()}
                                    </div>
                                  </div>
                                )}

                                {(!card.personalized_reason && !card.gift_message && card.description) && (
                                  <>
                                    {card.description.length > 240 && !expandedCards.has(`${message.id}-${idx}`)
                                      ? `${card.description.slice(0,220)}... `
                                      : card.description}
                                    {card.description.length > 240 && (
                                      <button
                                        onClick={(e) => {
                                          e.preventDefault();
                                          toggleExpandCard(`${message.id}-${idx}`);
                                        }}
                                        className="ml-1 text-xs text-[#00796b] font-medium hover:underline"
                                      >
                                        {expandedCards.has(`${message.id}-${idx}`) ? 'Show less' : 'Show more'}
                                      </button>
                                    )}
                                  </>
                                )}

                                {card.gift_suitability && (
                                  <div className="mt-1 inline-block bg-yellow-50 text-yellow-800 px-2 py-0.5 rounded-full text-[11px] font-medium">
                                    🎁 {card.gift_suitability}
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                      );
                    })}
                  </div>
                )}

                <div className={`text-xs ${
                  message.sender === 'user' ? 'text-gray-200' : 'text-gray-400'
                }`}>
                  {message.timestamp}
                </div>
              </div>
            </div>
          ))}

          {/* Typing Indicator */}
          {isTyping && (
            <div className="flex justify-start">
              <div className="bg-white rounded-2xl px-6 py-4 shadow-md border border-gray-100">
                <div className="flex items-center gap-2">
                  <div className="w-2.5 h-2.5 bg-gradient-to-br from-[#8B1538] to-[#D4AF37] rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></div>
                  <div className="w-2.5 h-2.5 bg-gradient-to-br from-[#8B1538] to-[#D4AF37] rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></div>
                  <div className="w-2.5 h-2.5 bg-gradient-to-br from-[#8B1538] to-[#D4AF37] rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></div>
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Featured Product: show customer's top trending product when available */}
        {typeof window !== 'undefined' && (
          <FeaturedProductBlock messages={messages} sessionInfo={sessionInfo} />
        )}

        {/* Quick Action Buttons */}
        <div className="mb-4 px-4">
          <p className="text-sm font-semibold text-gray-600 mb-3">QUICK ACTIONS</p>
          <div className="grid grid-cols-4 gap-3">
            <button
              onClick={() => handleQuickAction('browse')}
              className="bg-gradient-to-br from-[#8B1538] via-[#D2691E] to-[#D4AF37] hover:from-[#7A1230] hover:via-[#C25A15] hover:to-[#C4A037] text-white font-semibold py-4 px-6 rounded-xl shadow-md hover:shadow-xl transition-all duration-300 transform hover:scale-105 flex items-center justify-center gap-2"
            >
              <ShoppingBag className="w-5 h-5" />
              <span>Top Picks</span>
            </button>
            <button
              onClick={() => handleQuickAction('track')}
              className="bg-gradient-to-br from-[#8B1538] via-[#D2691E] to-[#D4AF37] hover:from-[#7A1230] hover:via-[#C25A15] hover:to-[#C4A037] text-white font-semibold py-4 px-6 rounded-xl shadow-md hover:shadow-xl transition-all duration-300 transform hover:scale-105 flex items-center justify-center gap-2"
            >
              <Package className="w-5 h-5" />
              <span>Track Order</span>
            </button>
            <button
              onClick={() => handleQuickAction('availability')}
              className="bg-gradient-to-br from-[#8B1538] via-[#D2691E] to-[#D4AF37] hover:from-[#7A1230] hover:via-[#C25A15] hover:to-[#C4A037] text-white font-semibold py-4 px-6 rounded-xl shadow-md hover:shadow-xl transition-all duration-300 transform hover:scale-105 flex items-center justify-center gap-2"
            >
              <MapPin className="w-5 h-5" />
              <span>Store Availability</span>
            </button>
            <button
              onClick={() => handleQuickAction('expert')}
              className="bg-gradient-to-br from-[#8B1538] via-[#D2691E] to-[#D4AF37] hover:from-[#7A1230] hover:via-[#C25A15] hover:to-[#C4A037] text-white font-semibold py-4 px-6 rounded-xl shadow-md hover:shadow-xl transition-all duration-300 transform hover:scale-105 flex items-center justify-center gap-2"
            >
              <User className="w-5 h-5" />
              <span>Sales Expert</span>
            </button>
          </div>
        </div>

        {/* Input Area */}
        <div className="px-4">
          <div className="bg-white rounded-2xl shadow-lg border border-gray-200 p-4">
            <div className="flex items-center gap-4">
              <input
                type="text"
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                onKeyPress={handleKeyPress}
                placeholder="Type your message here..."
                className="flex-1 bg-gray-50 rounded-xl px-6 py-4 outline-none text-base border border-gray-200 focus:border-[#D4AF37] focus:ring-2 focus:ring-[#D4AF37]/20 transition-all"
              />
              <button
                onClick={toggleVoiceRecording}
                className={`p-4 rounded-xl transition-all duration-300 ${
                  isRecording 
                    ? 'bg-red-100 hover:bg-red-200 text-red-600 animate-pulse' 
                    : 'bg-gray-100 hover:bg-gray-200 text-gray-600'
                }`}
                title={isRecording ? 'Stop recording' : 'Start voice input'}
              >
                {isRecording ? <MicOff className="w-6 h-6" /> : <Mic className="w-6 h-6" />}
              </button>
              <button
                onClick={handleSendMessage}
                disabled={!inputText.trim()}
                className={`bg-gradient-to-r from-[#8B1538] via-[#D2691E] to-[#D4AF37] text-white font-bold py-4 px-8 rounded-xl shadow-md transition-all duration-300 flex items-center gap-2 ${
                  inputText.trim()
                    ? 'hover:shadow-xl transform hover:scale-105 cursor-pointer hover:from-[#7A1230] hover:via-[#C25A15] hover:to-[#C4A037]'
                    : 'opacity-50 cursor-not-allowed'
                }`}
              >
                <Send className="w-5 h-5" />
                <span className="font-semibold">Send</span>
              </button>
            </div>
          </div>
        </div>

        {/* Footer Info */}
        <div className="mt-4 text-center">
          <p className="text-xs text-gray-500">
            Need assistance? Our store staff is always here to help you • Powered by AI Sales Agent
          </p>
        </div>
      </div>
    </div>
  );
};

export default KioskChat;
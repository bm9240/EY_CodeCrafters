import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import MainApp from './MainApp';
import sessionStore from '../lib/session';

const sanitizePhone = (value) => value.replace(/[^0-9]/g, '');

const WhatsAppGate = () => {
  const navigate = useNavigate();
  const [isChecking, setIsChecking] = useState(true);
  const [phoneInput, setPhoneInput] = useState('');
  const [error, setError] = useState('');
  const [isReady, setIsReady] = useState(false);
  const [chatKey, setChatKey] = useState('');

  useEffect(() => {
    const profile = sessionStore.getProfile();
    if (!profile) {
      navigate('/login', { replace: true, state: { redirectTo: '/chat' } });
      return;
    }

    const previousPhone = sessionStore.getPhone() || '';
    setPhoneInput(previousPhone);
    setIsChecking(false);
  }, [navigate]);

  const handleSubmit = (event) => {
    event.preventDefault();
    const digits = sanitizePhone(phoneInput);

    if (!digits) {
      setError('Please enter your phone number.');
      return;
    }
    if (digits.length < 10) {
      setError('Phone number should include at least 10 digits.');
      return;
    }

    const existingPhone = sessionStore.getPhone();
    const existingToken = sessionStore.getSessionToken();
    if (!existingToken || existingPhone !== digits) {
      sessionStore.clearSessionToken();
    }
    sessionStore.setPhone(digits);
    setChatKey(`chat-${digits}-${Date.now()}`);
    setIsReady(true);
  };

  if (isChecking) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-orange-50 via-yellow-50 to-red-50">
        <div className="text-center text-red-700 font-medium">Preparing WhatsApp experience…</div>
      </div>
    );
  }

  if (isReady) {
    return <MainApp key={chatKey} />;
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-orange-50 via-yellow-50 to-red-50 px-4">
      <div className="w-full max-w-lg bg-white/95 backdrop-blur shadow-xl rounded-2xl border border-red-100 p-8">
        <h1 className="text-2xl font-semibold text-red-700 text-center mb-4">Enter Phone Number</h1>
        <p className="text-sm text-gray-600 text-center mb-6">
          To continue to the WhatsApp experience please confirm the phone number you wish to use for this session.
        </p>
        <form onSubmit={handleSubmit} className="space-y-4">
          <label className="flex flex-col gap-2 text-sm font-medium text-gray-700">
            Phone Number
            <input
              value={phoneInput}
              onChange={(event) => {
                setPhoneInput(sanitizePhone(event.target.value));
                setError('');
              }}
              placeholder="Digits only"
              className="border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-red-400"
            />
          </label>
          {error && <p className="text-sm text-red-600 text-center">{error}</p>}
          <button
            type="submit"
            className="w-full bg-gradient-to-r from-red-600 to-orange-500 text-white py-3 rounded-lg font-semibold tracking-wide shadow-lg hover:from-red-500 hover:to-orange-400"
          >
            Continue to WhatsApp
          </button>
        </form>
        <p className="text-xs text-gray-500 text-center mt-4">
          You will be asked for your phone number every time you access the WhatsApp channel.
        </p>
      </div>
    </div>
  );
};

export default WhatsAppGate;

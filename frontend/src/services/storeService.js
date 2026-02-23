// Store Service - Fetch stores and inventory through Sales Agent
// All agent communication goes through the Sales Agent for proper orchestration

import { apiCall } from '../config/api';

const SALES_AGENT_URL = 'http://localhost:8010';

/**
 * Get all available stores directly from Supabase via Sales Agent
 * @returns {Promise<Array>} Array of store objects
 */
export const getStores = async () => {
  try {
    // All store queries go through sales agent
    const response = await apiCall(`${SALES_AGENT_URL}/api/stores`);
    console.log('📍 Store service response:', response);
    
    // Extract stores array from response (handle both wrapped and direct)
    if (response?.stores && Array.isArray(response.stores)) {
      return response.stores;
    } else if (Array.isArray(response)) {
      return response;
    } else {
      console.warn('⚠️ Unexpected stores response format:', response);
      return [];
    }
  } catch (error) {
    console.error('Failed to fetch stores:', error);
    throw error;  // No fallback - force proper error handling
  }
};

/**
 * Get store details by location
 * @param {string} storeLocation - Store location ID (e.g., 'STORE_MUMBAI')
 */
export const getStoreDetails = async (storeLocation) => {
  return apiCall(`${SALES_AGENT_URL}/api/stores/${storeLocation}`);
};

/**
 * Get inventory for a product at a specific store
 * Fetches directly from Supabase inventory table via Sales Agent
 * @param {string} sku - Product SKU
 * @param {string} storeLocation - Store location ID (e.g., 'STORE_MUMBAI')
 * @returns {Promise<Object>} Inventory details including available_stock and can_reserve
 */
export const getStoreInventory = async (sku, storeLocation) => {
  try {
    const response = await apiCall(
      `${SALES_AGENT_URL}/api/stores/${storeLocation}/inventory/${sku}`
    );
    return response;
  } catch (error) {
    console.error(`Failed to fetch inventory for ${sku} at ${storeLocation}:`, error);
    throw error;
  }
};

/**
 * Reserve a product in a store - complete orchestration through Sales Agent
 * @param {Object} reservationData - { customer_id, sku, quantity, store_location }
 * @returns {Promise<Object>} Reservation confirmation
 */
export const reserveInStore = async (reservationData) => {
  try {
    const response = await apiCall(
      `${SALES_AGENT_URL}/api/reserve-in-store`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(reservationData)
      }
    );
    return response;
  } catch (error) {
    console.error('Failed to reserve in store:', error);
    throw error;
  }
};

/**
 * Release a reservation hold - through Sales Agent
 * @param {string} holdId - Hold ID to release
 * @returns {Promise<Object>} Release confirmation
 */
export const releaseReservation = async (holdId) => {
  try {
    const response = await apiCall(
      `${SALES_AGENT_URL}/api/hold/${holdId}/release`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      }
    );
    return response;
  } catch (error) {
    console.error('Failed to release reservation:', error);
    throw error;
  }
};

export default {
  getStores,
  getStoreDetails,
  getStoreInventory,
  reserveInStore,
  releaseReservation,
};

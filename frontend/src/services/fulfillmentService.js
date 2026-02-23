// Fulfillment Service - Handles order fulfillment and tracking

import { API_ENDPOINTS, apiCall } from '../config/api';

/**
 * Start fulfillment for an order
 * @param {Object} fulfillmentData - Fulfillment data
 * @param {string} fulfillmentData.order_id - Order ID
 * @param {string} fulfillmentData.inventory_status - Inventory status (must be RESERVED)
 * @param {string} fulfillmentData.payment_status - Payment status (must be SUCCESS)
 * @param {number} fulfillmentData.amount - Order amount
 * @param {string} fulfillmentData.inventory_hold_id - Hold ID from inventory (optional)
 * @param {string} fulfillmentData.payment_transaction_id - Transaction ID from payment (optional)
 * @returns {Promise<Object>} Fulfillment response
 */
export const startFulfillment = async (fulfillmentData) => {
  return apiCall(API_ENDPOINTS.FULFILLMENT_START, {
    method: 'POST',
    body: JSON.stringify(fulfillmentData),
  });
};

/**
 * Get fulfillment status for an order
 * @param {string} orderId - Order ID
 * @returns {Promise<Object>} Fulfillment details
 */
export const getFulfillmentStatus = async (orderId) => {
  const response = await apiCall(`${API_ENDPOINTS.FULFILLMENT_STATUS}/${orderId}`);
  // Unwrap the fulfillment object from the API response
  return response?.fulfillment || response;
};

/**
 * Update fulfillment status
 * @param {Object} updateData - Update data
 * @param {string} updateData.order_id - Order ID
 * @param {string} updateData.new_status - New status (PROCESSING, PACKED, SHIPPED, OUT_FOR_DELIVERY, DELIVERED)
 * @returns {Promise<Object>} Updated fulfillment
 */
export const updateFulfillmentStatus = async (updateData) => {
  return apiCall(API_ENDPOINTS.FULFILLMENT_UPDATE, {
    method: 'POST',
    body: JSON.stringify(updateData),
  });
};

/**
 * Mark order as delivered
 * @param {Object} deliveryData - Delivery data
 * @param {string} deliveryData.order_id - Order ID
 * @param {string} deliveryData.delivery_notes - Delivery notes (optional)
 * @returns {Promise<Object>} Delivery confirmation
 */
export const markAsDelivered = async (deliveryData) => {
  return apiCall(API_ENDPOINTS.FULFILLMENT_DELIVERED, {
    method: 'POST',
    body: JSON.stringify(deliveryData),
  });
};

/**
 * Cancel an order
 * @param {Object} cancelData - Cancellation data
 * @param {string} cancelData.order_id - Order ID
 * @param {string} cancelData.reason - Cancellation reason
 * @returns {Promise<Object>} Cancellation response
 */
export const cancelOrder = async (cancelData) => {
  return apiCall(API_ENDPOINTS.FULFILLMENT_CANCEL, {
    method: 'POST',
    body: JSON.stringify(cancelData),
  });
};

/**
 * Set delivery window for an order
 * @param {Object} windowData - Delivery window data
 * @param {string} windowData.order_id - Order ID
 * @param {string} windowData.delivery_window - Delivery window (morning|afternoon|evening)
 * @returns {Promise<Object>} Delivery window confirmation
 */
export const setDeliveryWindow = async (windowData) => {
  return apiCall(API_ENDPOINTS.FULFILLMENT_SET_DELIVERY_WINDOW, {
    method: 'POST',
    body: JSON.stringify(windowData),
  });
};

export default {
  startFulfillment,
  getFulfillmentStatus,
  updateFulfillmentStatus,
  markAsDelivered,
  cancelOrder,
  setDeliveryWindow,
};

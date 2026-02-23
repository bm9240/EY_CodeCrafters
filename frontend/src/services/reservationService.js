// Reservation Service - Client-side API calls

import { API_ENDPOINTS, apiCall } from '../config/api';

const SALES_AGENT_URL = 'http://localhost:8010';

/**
 * Create a new reservation after inventory hold is successful
 */
export const createReservation = async (data) => {
  return apiCall(`${SALES_AGENT_URL}/api/reservations`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
};

/**
 * Get a specific reservation by ID
 */
export const getReservation = async (reservationId) => {
  return apiCall(`${SALES_AGENT_URL}/api/reservations/${reservationId}`);
};

/**
 * List all reservations for a customer
 */
export const listCustomerReservations = async (customerId) => {
  return apiCall(`${SALES_AGENT_URL}/api/reservations?customer_id=${customerId}`);
};

/**
 * Update reservation status
 */
export const updateReservationStatus = async (reservationId, status, notes = null) => {
  return apiCall(`${SALES_AGENT_URL}/api/reservations/${reservationId}/status`, {
    method: 'PUT',
    body: JSON.stringify({ status, notes }),
  });
};

/**
 * Cancel a reservation
 */
export const cancelReservation = async (reservationId) => {
  return apiCall(`${SALES_AGENT_URL}/api/reservations/${reservationId}`, {
    method: 'DELETE',
  });
};

/**
 * Admin: List reservations for a store
 */
export const listStoreReservations = async (store) => {
  return apiCall(`${SALES_AGENT_URL}/api/admin/reservations?store=${store}`);
};

/**
 * Admin: Confirm reservation (item kept aside)
 */
export const confirmReservation = async (reservationId, store) => {
  return apiCall(`${SALES_AGENT_URL}/api/admin/reservations/${reservationId}/confirm?store=${store}`, {
    method: 'PUT',
  });
};

/**
 * Admin: Convert reservation to purchase
 */
export const convertReservation = async (reservationId, store, orderId = null) => {
  return apiCall(`${SALES_AGENT_URL}/api/admin/reservations/${reservationId}/convert?store=${store}`, {
    method: 'PUT',
    body: JSON.stringify({ order_id: orderId }),
  });
};

export default {
  createReservation,
  getReservation,
  listCustomerReservations,
  updateReservationStatus,
  cancelReservation,
  listStoreReservations,
  confirmReservation,
  convertReservation,
};

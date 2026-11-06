import axiosClient from './axiosClient';

const BASE = '/api/assistant';
// Planner plus tools can take long; only assistant calls wait this long, the shared axios client keeps its default.
export const ASSISTANT_TIMEOUT_MS = 120000;
const OPTIONS = { timeout: ASSISTANT_TIMEOUT_MS };

export const createConversation = () =>
  axiosClient.post(`${BASE}/conversations/`, {}, OPTIONS).then((response) => response.data);

export const listConversations = (page = 1) =>
  axiosClient.get(`${BASE}/conversations/`, { ...OPTIONS, params: { page } }).then((response) => response.data);

export const listTurns = (id, page = 1) =>
  axiosClient.get(`${BASE}/conversations/${id}/turns/`, { ...OPTIONS, params: { page } }).then((response) => response.data);

export const askQuestion = (id, { client_request_id, question }) =>
  axiosClient.post(`${BASE}/conversations/${id}/turns/`, { client_request_id, question }, OPTIONS).then((response) => response.data);

export const getTurn = (id) =>
  axiosClient.get(`${BASE}/turns/${id}/`, OPTIONS).then((response) => response.data);

export const escalateTurn = (id) =>
  axiosClient.post(`${BASE}/turns/${id}/escalate/`, {}, OPTIONS).then((response) => response.data);

export const getProcedure = (topic) =>
  axiosClient.get(`${BASE}/procedures/${topic}/`, OPTIONS).then((response) => response.data);

export const listSupportTickets = ({ view = 'inbox', status, page = 1 } = {}) =>
  axiosClient.get(`${BASE}/support-tickets/`, { ...OPTIONS, params: { view, status, page } }).then((response) => response.data);

export const getSupportTicket = (id) =>
  axiosClient.get(`${BASE}/support-tickets/${id}/`, OPTIONS).then((response) => response.data);

export const replySupportTicket = (id, { client_request_id, expected_version, reply }) =>
  axiosClient
    .post(`${BASE}/support-tickets/${id}/reply/`, { client_request_id, expected_version, reply }, OPTIONS)
    .then((response) => response.data);

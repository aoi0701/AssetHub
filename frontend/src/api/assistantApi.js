import axiosClient from './axiosClient';

const BASE = '/api/assistant';

export const createConversation = () =>
  axiosClient.post(`${BASE}/conversations/`, {}).then((response) => response.data);

export const listConversations = (page = 1) =>
  axiosClient.get(`${BASE}/conversations/`, { params: { page } }).then((response) => response.data);

export const listTurns = (id, page = 1) =>
  axiosClient.get(`${BASE}/conversations/${id}/turns/`, { params: { page } }).then((response) => response.data);

export const askQuestion = (id, { client_request_id, question }) =>
  axiosClient.post(`${BASE}/conversations/${id}/turns/`, { client_request_id, question }).then((response) => response.data);

export const getTurn = (id) =>
  axiosClient.get(`${BASE}/turns/${id}/`).then((response) => response.data);

export const escalateTurn = (id) =>
  axiosClient.post(`${BASE}/turns/${id}/escalate/`, {}).then((response) => response.data);

export const getProcedure = (topic) =>
  axiosClient.get(`${BASE}/procedures/${topic}/`).then((response) => response.data);

import axios from 'axios';

let accessToken = null;
let refreshToken = null;

let isRefreshing = false;
let failedQueue = [];

let authFailureListeners = [];

export const onAuthFailure = (callback) => {
  authFailureListeners.push(callback);
  return () => {
    authFailureListeners = authFailureListeners.filter((cb) => cb !== callback);
  };
};

const notifyAuthFailure = () => {
  authFailureListeners.forEach((cb) => {
    try {
      cb();
    } catch {
      // ignore
    }
  });
};

export const setTokens = ({ access, refresh }) => {
  if (access !== undefined) accessToken = access;
  if (refresh !== undefined) refreshToken = refresh;
};

export const getAccessToken = () => accessToken;
export const getRefreshToken = () => refreshToken;

export const clearTokens = () => {
  accessToken = null;
  refreshToken = null;
};

const processQueue = (error, token = null) => {
  failedQueue.forEach((prom) => {
    if (error) {
      prom.reject(error);
    } else {
      prom.resolve(token);
    }
  });
  failedQueue = [];
};

const axiosClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  headers: {
    'Content-Type': 'application/json',
  },
});

axiosClient.interceptors.request.use(
  (config) => {
    const token = getAccessToken();
    if (token) {
      config.headers = config.headers || {};
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

axiosClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;

    if (!originalRequest || !error.response) {
      return Promise.reject(error);
    }

    const isAuthEndpoint =
      originalRequest.url?.includes('/api/auth/login/') ||
      originalRequest.url?.includes('/api/auth/refresh/');

    if (error.response.status === 401 && !originalRequest._retry && !isAuthEndpoint) {
      if (isRefreshing) {
        return new Promise((resolve, reject) => {
          failedQueue.push({ resolve, reject });
        })
          .then((token) => {
            originalRequest.headers.Authorization = `Bearer ${token}`;
            return axiosClient(originalRequest);
          })
          .catch((err) => Promise.reject(err));
      }

      originalRequest._retry = true;
      isRefreshing = true;

      const currentRefresh = getRefreshToken();
      if (!currentRefresh) {
        clearTokens();
        notifyAuthFailure();
        isRefreshing = false;
        return Promise.reject(error);
      }

      try {
        const response = await axios.post(
          `${import.meta.env.VITE_API_BASE_URL || ''}/api/auth/refresh/`,
          { refresh: currentRefresh }
        );
        const newAccess = response.data.access;
        setTokens({ access: newAccess, refresh: currentRefresh });

        processQueue(null, newAccess);
        originalRequest.headers.Authorization = `Bearer ${newAccess}`;
        return axiosClient(originalRequest);
      } catch (refreshErr) {
        processQueue(refreshErr, null);
        clearTokens();
        notifyAuthFailure();
        return Promise.reject(refreshErr);
      } finally {
        isRefreshing = false;
      }
    }

    return Promise.reject(error);
  }
);

export default axiosClient;

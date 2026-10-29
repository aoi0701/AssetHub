import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import axiosClient, {
  setTokens,
  clearTokens,
  getAccessToken,
  onAuthFailure,
} from '../api/axiosClient';

const AuthContext = createContext(null);

export const AuthProvider = ({ children, initialUser = null, initialToken = null }) => {
  const [user, setUser] = useState(initialUser);
  const [token, setToken] = useState(initialToken);
  const [isAuthenticated, setIsAuthenticated] = useState(!!initialUser || !!initialToken);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (initialToken) {
      setTokens({ access: initialToken });
    }
  }, [initialToken]);

  const logout = useCallback(async () => {
    try {
      if (getAccessToken()) {
        await axiosClient.post('/api/auth/logout/');
      }
    } catch {
      // ignore network/auth errors during logout
    } finally {
      clearTokens();
      setUser(null);
      setToken(null);
      setIsAuthenticated(false);
    }
  }, []);

  useEffect(() => {
    const unsubscribe = onAuthFailure(() => {
      clearTokens();
      setUser(null);
      setToken(null);
      setIsAuthenticated(false);
    });
    return unsubscribe;
  }, []);

  const login = async (username, password) => {
    setIsLoading(true);
    try {
      const response = await axiosClient.post('/api/auth/login/', {
        username,
        password,
      });
      const { access, refresh, user: userData } = response.data;
      setTokens({ access, refresh });
      setUser(userData);
      setToken(access);
      setIsAuthenticated(true);
      return userData;
    } finally {
      setIsLoading(false);
    }
  };

  const register = async ({
    username,
    email,
    full_name,
    phone,
    password,
    password_confirm,
  }) => {
    if (password !== password_confirm) {
      throw new Error('Mật khẩu xác nhận không khớp.');
    }
    setIsLoading(true);
    try {
      const response = await axiosClient.post('/api/auth/register/', {
        username,
        email,
        full_name,
        phone,
        password,
      });
      return response.data;
    } finally {
      setIsLoading(false);
    }
  };

  const updateProfile = async (data) => {
    setIsLoading(true);
    try {
      const response = await axiosClient.patch('/api/auth/profile/', data);
      setUser((prev) => ({
        ...prev,
        ...response.data,
      }));
      return response.data;
    } finally {
      setIsLoading(false);
    }
  };

  const changePassword = async (oldPassword, newPassword, newPasswordConfirm) => {
    if (newPassword !== newPasswordConfirm) {
      throw new Error('Mật khẩu mới xác nhận không khớp.');
    }
    setIsLoading(true);
    try {
      await axiosClient.post('/api/auth/change-password/', {
        old_password: oldPassword,
        new_password: newPassword,
      });
    } finally {
      setIsLoading(false);
    }
  };

  const value = {
    user,
    token,
    isAuthenticated,
    isLoading,
    login,
    register,
    logout,
    updateProfile,
    changePassword,
    setUser,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

export default AuthContext;

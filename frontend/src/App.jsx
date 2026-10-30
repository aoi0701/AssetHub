import React from 'react';
import { Routes, Route, Navigate, Link, useLocation } from 'react-router-dom';
import { useAuth } from './context/AuthContext';
import Navbar from './components/Navbar';
import Sidebar from './components/Sidebar';
import ProtectedRoute from './components/ProtectedRoute';

import LoginPage from './pages/LoginPage';
import RegisterPage from './pages/RegisterPage';
import ProfilePage from './pages/ProfilePage';
import UserAdminPage from './pages/UserAdminPage';
import DepartmentPage from './pages/DepartmentPage';
import CategoryPage from './pages/CategoryPage';

import DashboardPage from './pages/DashboardPage';
import AssetListPage from './pages/AssetListPage';
import AssetDetailPage from './pages/AssetDetailPage';
import UserPortalPage from './pages/UserPortalPage';
import PersonalHistoryPage from './pages/PersonalHistoryPage';
import RequestInboxPage from './pages/RequestInboxPage';

const HomeRedirect = () => {
  const { isAuthenticated, user } = useAuth();
  if (!isAuthenticated || !user) {
    return <Navigate to="/login" replace />;
  }
  if (user.role === 'USER') {
    return <Navigate to="/my-assets" replace />;
  }
  return <Navigate to="/dashboard" replace />;
};

function App() {
  const { isAuthenticated, user } = useAuth();
  const location = useLocation();
  const isGuestRoute = location.pathname === '/login' || location.pathname === '/register';
  const showSidebar = Boolean(isAuthenticated && user && !isGuestRoute);

  const routes = (
    <Routes>
      <Route path="/" element={<HomeRedirect />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />

      {/* Authenticated user routes */}
      <Route
        path="/profile"
        element={
          <ProtectedRoute>
            <ProfilePage />
          </ProtectedRoute>
        }
      />

      {/* User routes */}
      <Route
        path="/my-assets"
        element={
          <ProtectedRoute allowedRoles={['USER']}>
            <UserPortalPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/my-history"
        element={
          <ProtectedRoute allowedRoles={['USER']}>
            <PersonalHistoryPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/my-requests"
        element={
          <ProtectedRoute allowedRoles={['USER']}>
            <RequestInboxPage />
          </ProtectedRoute>
        }
      />

      {/* Admin & Manager shared routes */}
      <Route
        path="/dashboard"
        element={
          <ProtectedRoute allowedRoles={['ADMIN', 'MANAGER']}>
            <DashboardPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/assets"
        element={
          <ProtectedRoute allowedRoles={['ADMIN', 'MANAGER']}>
            <AssetListPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/assets/:id"
        element={
          <ProtectedRoute allowedRoles={['ADMIN', 'MANAGER']}>
            <AssetDetailPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/requests"
        element={
          <ProtectedRoute allowedRoles={['ADMIN', 'MANAGER']}>
            <RequestInboxPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/users"
        element={
          <ProtectedRoute allowedRoles={['ADMIN', 'MANAGER']}>
            <UserAdminPage />
          </ProtectedRoute>
        }
      />

      {/* Admin only routes */}
      <Route
        path="/departments"
        element={
          <ProtectedRoute allowedRoles={['ADMIN']}>
            <DepartmentPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/categories"
        element={
          <ProtectedRoute allowedRoles={['ADMIN']}>
            <CategoryPage />
          </ProtectedRoute>
        }
      />

      {/* 404 Not Found */}
      <Route
        path="*"
        element={
          <div className="min-h-[60vh] flex items-center justify-center p-4">
            <div className="text-center">
              <h1 className="text-6xl font-black text-gray-300">404</h1>
              <p className="text-lg font-semibold text-gray-700 mt-2">Trang không tồn tại</p>
              <p className="text-sm text-gray-500 mt-1 mb-6">
                Đường dẫn bạn truy cập không tồn tại hoặc đã bị di chuyển.
              </p>
              <Link
                to="/"
                className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
              >
                Về trang chủ
              </Link>
            </div>
          </div>
        }
      />
    </Routes>
  );

  if (showSidebar) {
    return (
      <div className="flex h-screen w-full overflow-hidden bg-gray-50 text-gray-900">
        <Sidebar />
        <main className="flex-1 min-w-0 h-screen overflow-y-auto">
          {routes}
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex flex-col bg-gray-50 text-gray-900">
      <Navbar />
      <main className="flex-1">
        {routes}
      </main>
    </div>
  );
}

export default App;

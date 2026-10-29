import React from 'react';
import { Routes, Route, Navigate, Link } from 'react-router-dom';
import { useAuth } from './context/AuthContext';
import Navbar from './components/Navbar';
import ProtectedRoute from './components/ProtectedRoute';

import LoginPage from './pages/LoginPage';
import RegisterPage from './pages/RegisterPage';
import ProfilePage from './pages/ProfilePage';
import UserAdminPage from './pages/UserAdminPage';
import DepartmentPage from './pages/DepartmentPage';
import CategoryPage from './pages/CategoryPage';

const PlaceholderPage = ({ title, description }) => (
  <div className="max-w-7xl mx-auto py-12 px-4 sm:px-6 lg:px-8">
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-8 text-center">
      <h1 className="text-2xl font-bold text-gray-900 mb-2">{title}</h1>
      <p className="text-gray-500 max-w-md mx-auto">{description}</p>
    </div>
  </div>
);

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
  return (
    <div className="min-h-screen flex flex-col bg-gray-50 text-gray-900">
      <Navbar />
      <main className="flex-1">
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
                <PlaceholderPage
                  title="Tài sản của tôi"
                  description="Danh sách tài sản doanh nghiệp đang được bàn giao cho bạn."
                />
              </ProtectedRoute>
            }
          />
          <Route
            path="/my-history"
            element={
              <ProtectedRoute allowedRoles={['USER']}>
                <PlaceholderPage
                  title="Lịch sử tài sản"
                  description="Nhật ký bàn giao, thu hồi và luân chuyển tài sản cá nhân."
                />
              </ProtectedRoute>
            }
          />
          <Route
            path="/my-requests"
            element={
              <ProtectedRoute allowedRoles={['USER']}>
                <PlaceholderPage
                  title="Yêu cầu của tôi"
                  description="Danh sách các yêu cầu cấp phát, thu hồi hoặc sửa chữa tài sản."
                />
              </ProtectedRoute>
            }
          />

          {/* Admin & Manager shared routes */}
          <Route
            path="/dashboard"
            element={
              <ProtectedRoute allowedRoles={['ADMIN', 'MANAGER']}>
                <PlaceholderPage
                  title="Bảng điều khiển (Dashboard)"
                  description="Tổng quan số liệu tài sản, phòng ban và yêu cầu cần xử lý."
                />
              </ProtectedRoute>
            }
          />
          <Route
            path="/assets"
            element={
              <ProtectedRoute allowedRoles={['ADMIN', 'MANAGER']}>
                <PlaceholderPage
                  title="Quản lý tài sản"
                  description="Danh sách và thông tin chi tiết tài sản doanh nghiệp."
                />
              </ProtectedRoute>
            }
          />
          <Route
            path="/requests"
            element={
              <ProtectedRoute allowedRoles={['ADMIN', 'MANAGER']}>
                <PlaceholderPage
                  title="Quản lý yêu cầu"
                  description="Duyệt và xử lý các yêu cầu tài sản từ nhân viên."
                />
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
      </main>
    </div>
  );
}

export default App;

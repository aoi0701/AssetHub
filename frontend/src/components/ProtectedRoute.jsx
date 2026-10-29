import React from 'react';
import { Navigate, useLocation, Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { ShieldAlert } from 'lucide-react';

const ProtectedRoute = ({ children, allowedRoles }) => {
  const { user, isAuthenticated, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  if (!isAuthenticated || !user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  if (allowedRoles && allowedRoles.length > 0 && !allowedRoles.includes(user.role)) {
    return (
      <div className="min-h-[70vh] flex items-center justify-center p-4">
        <div className="max-w-md w-full bg-white shadow-lg rounded-xl p-8 text-center border border-gray-100">
          <div className="w-16 h-16 bg-red-100 rounded-full flex items-center justify-center mx-auto mb-4 text-red-600">
            <ShieldAlert size={36} />
          </div>
          <h1 className="text-2xl font-bold text-gray-900 mb-2">403 - Truy cập bị từ chối</h1>
          <p className="text-gray-600 mb-6">
            Bạn không có quyền truy cập vào trang này với vai trò hiện tại (<strong>{user.role}</strong>).
          </p>
          <Link
            to={user.role === 'USER' ? '/my-assets' : '/dashboard'}
            className="inline-block px-5 py-2.5 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 transition-colors"
          >
            Quay về trang chính
          </Link>
        </div>
      </div>
    );
  }

  return children;
};

export default ProtectedRoute;

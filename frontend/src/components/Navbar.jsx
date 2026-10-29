import React from 'react';
import { NavLink, useNavigate, Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { LogOut, User as UserIcon, Building2, Package, Layers, Users, Home, FileText, History } from 'lucide-react';

const Navbar = () => {
  const { user, isAuthenticated, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  if (!isAuthenticated || !user) {
    return (
      <header className="bg-white border-b border-gray-200">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <Link to="/" className="flex items-center space-x-2">
            <Package className="h-6 w-6 text-blue-600" />
            <span className="font-bold text-xl text-gray-900 tracking-tight">AssetHub</span>
          </Link>
          <div className="flex items-center space-x-4">
            <Link to="/login" className="text-sm font-medium text-gray-700 hover:text-blue-600">
              Đăng nhập
            </Link>
            <Link
              to="/register"
              className="text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 px-3 py-1.5 rounded-lg"
            >
              Đăng ký
            </Link>
          </div>
        </div>
      </header>
    );
  }

  const role = user.role;

  const roleBadgeColors = {
    ADMIN: 'bg-purple-100 text-purple-800 border-purple-200',
    MANAGER: 'bg-blue-100 text-blue-800 border-blue-200',
    USER: 'bg-green-100 text-green-800 border-green-200',
  };

  const navClass = ({ isActive }) =>
    `inline-flex items-center px-3 py-2 text-sm font-medium rounded-md transition-colors ${
      isActive
        ? 'text-blue-600 bg-blue-50'
        : 'text-gray-700 hover:text-blue-600 hover:bg-gray-50'
    }`;

  return (
    <header className="bg-white border-b border-gray-200 sticky top-0 z-30">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16">
          <div className="flex items-center space-x-8">
            <Link
              to={role === 'USER' ? '/my-assets' : '/dashboard'}
              className="flex items-center space-x-2"
            >
              <Package className="h-7 w-7 text-blue-600" />
              <span className="font-bold text-xl text-gray-900 tracking-tight">AssetHub</span>
            </Link>

            <nav className="hidden md:flex space-x-1" aria-label="Main Navigation">
              {role === 'ADMIN' && (
                <>
                  <NavLink to="/dashboard" className={navClass}>
                    <Home className="h-4 w-4 mr-1.5" />
                    Dashboard
                  </NavLink>
                  <NavLink to="/assets" className={navClass}>
                    <Package className="h-4 w-4 mr-1.5" />
                    Tài sản
                  </NavLink>
                  <NavLink to="/requests" className={navClass}>
                    <FileText className="h-4 w-4 mr-1.5" />
                    Yêu cầu
                  </NavLink>
                  <NavLink to="/users" className={navClass}>
                    <Users className="h-4 w-4 mr-1.5" />
                    Người dùng
                  </NavLink>
                  <NavLink to="/departments" className={navClass}>
                    <Building2 className="h-4 w-4 mr-1.5" />
                    Phòng ban
                  </NavLink>
                  <NavLink to="/categories" className={navClass}>
                    <Layers className="h-4 w-4 mr-1.5" />
                    Danh mục
                  </NavLink>
                </>
              )}

              {role === 'MANAGER' && (
                <>
                  <NavLink to="/dashboard" className={navClass}>
                    <Home className="h-4 w-4 mr-1.5" />
                    Dashboard
                  </NavLink>
                  <NavLink to="/assets" className={navClass}>
                    <Package className="h-4 w-4 mr-1.5" />
                    Tài sản
                  </NavLink>
                  <NavLink to="/requests" className={navClass}>
                    <FileText className="h-4 w-4 mr-1.5" />
                    Yêu cầu
                  </NavLink>
                  <NavLink to="/users" className={navClass}>
                    <Users className="h-4 w-4 mr-1.5" />
                    Nhân viên
                  </NavLink>
                </>
              )}

              {role === 'USER' && (
                <>
                  <NavLink to="/my-assets" className={navClass}>
                    <Package className="h-4 w-4 mr-1.5" />
                    Tài sản của tôi
                  </NavLink>
                  <NavLink to="/my-history" className={navClass}>
                    <History className="h-4 w-4 mr-1.5" />
                    Lịch sử
                  </NavLink>
                  <NavLink to="/my-requests" className={navClass}>
                    <FileText className="h-4 w-4 mr-1.5" />
                    Yêu cầu
                  </NavLink>
                </>
              )}
            </nav>
          </div>

          <div className="flex items-center space-x-3">
            <span
              className={`text-xs px-2.5 py-0.5 rounded-full font-semibold border ${
                roleBadgeColors[role] || 'bg-gray-100 text-gray-800'
              }`}
            >
              {role}
            </span>

            <NavLink
              to="/profile"
              className={({ isActive }) =>
                `flex items-center space-x-1.5 text-sm font-medium px-2.5 py-1.5 rounded-lg border ${
                  isActive
                    ? 'border-blue-500 bg-blue-50 text-blue-600'
                    : 'border-gray-200 text-gray-700 hover:bg-gray-50'
                }`
              }
              title="Thông tin tài khoản"
            >
              <UserIcon className="h-4 w-4" />
              <span className="hidden sm:inline">
                {user.full_name || user.username}
              </span>
            </NavLink>

            <button
              onClick={handleLogout}
              className="p-2 text-gray-500 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
              title="Đăng xuất"
              aria-label="Đăng xuất"
            >
              <LogOut className="h-5 w-5" />
            </button>
          </div>
        </div>
      </div>
    </header>
  );
};

export default Navbar;

import React, { useState } from 'react';
import { NavLink, useNavigate, Link } from 'react-router-dom';
import {
  Package,
  ChevronLeft,
  ChevronRight,
  Home,
  FileText,
  Users,
  Building2,
  Layers,
  History,
  LogOut,
  User as UserIcon,
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';

const STORAGE_KEY = 'assethub_sidebar_collapsed';

const getInitialCollapsed = () => {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored !== null) {
      return stored === 'true';
    }
    // Default collapsed on smaller screens (< 1024px) to preserve data table space
    if (typeof window !== 'undefined' && window.innerWidth < 1024) {
      return true;
    }
    return false;
  } catch {
    return false;
  }
};

const setCollapsedStorage = (value) => {
  try {
    localStorage.setItem(STORAGE_KEY, String(value));
  } catch {
    // Gracefully ignore storage write failures (SecurityError, quota exceeded, private mode)
  }
};

const roleBadgeColors = {
  ADMIN: 'bg-purple-50 text-purple-700 border-purple-200',
  MANAGER: 'bg-blue-50 text-blue-700 border-blue-200',
  USER: 'bg-emerald-50 text-emerald-700 border-emerald-200',
};

const ADMIN_NAV_ITEMS = [
  { to: '/dashboard', label: 'Dashboard', icon: Home },
  { to: '/assets', label: 'Tài sản', icon: Package },
  { to: '/requests', label: 'Yêu cầu', icon: FileText },
  { to: '/users', label: 'Người dùng', icon: Users },
  { to: '/departments', label: 'Phòng ban', icon: Building2 },
  { to: '/categories', label: 'Danh mục', icon: Layers },
];

const MANAGER_NAV_ITEMS = [
  { to: '/dashboard', label: 'Dashboard', icon: Home },
  { to: '/assets', label: 'Tài sản', icon: Package },
  { to: '/requests', label: 'Yêu cầu', icon: FileText },
  { to: '/users', label: 'Nhân viên', icon: Users },
];

const USER_NAV_ITEMS = [
  { to: '/my-assets', label: 'Tài sản của tôi', icon: Package },
  { to: '/my-history', label: 'Lịch sử', icon: History },
  { to: '/my-requests', label: 'Yêu cầu', icon: FileText },
];

const NAV_ITEMS_BY_ROLE = {
  ADMIN: ADMIN_NAV_ITEMS,
  MANAGER: MANAGER_NAV_ITEMS,
  USER: USER_NAV_ITEMS,
};

const Sidebar = () => {
  const { user, isAuthenticated, logout } = useAuth();
  const navigate = useNavigate();
  const [isCollapsed, setIsCollapsed] = useState(getInitialCollapsed);

  if (!isAuthenticated || !user) {
    return null;
  }

  const role = user.role;
  const displayName = user.full_name || user.username || 'Tài khoản';

  const handleToggleCollapse = () => {
    setIsCollapsed((prev) => {
      const next = !prev;
      setCollapsedStorage(next);
      return next;
    });
  };

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  const navItems = NAV_ITEMS_BY_ROLE[role] || [];

  return (
    <aside
      className={`h-screen shrink-0 bg-slate-50 border-r border-slate-200/90 text-slate-700 flex flex-col transition-all duration-200 ease-in-out ${
        isCollapsed ? 'w-20' : 'w-64'
      }`}
      aria-label="Thanh điều hướng chính"
    >
      {/* Sidebar Header */}
      <div
        className={`border-b border-slate-200/90 flex transition-all duration-200 ${
          isCollapsed
            ? 'flex-col items-center justify-center py-3 gap-2'
            : 'h-16 items-center justify-between px-3 gap-2'
        }`}
      >
        <Link
          to={role === 'USER' ? '/my-assets' : '/dashboard'}
          className={`flex items-center min-w-0 focus:outline-none focus:ring-2 focus:ring-blue-500 rounded-lg p-1 ${
            isCollapsed ? 'justify-center' : 'space-x-2'
          }`}
          title="AssetHub"
          aria-label="AssetHub"
        >
          <Package className="h-7 w-7 text-blue-600 shrink-0" />
          {!isCollapsed ? (
            <span className="font-bold text-xl text-slate-900 tracking-tight truncate">
              AssetHub
            </span>
          ) : (
            <span className="sr-only">AssetHub</span>
          )}
        </Link>

        <button
          type="button"
          onClick={handleToggleCollapse}
          className="p-1.5 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-200/70 focus:outline-none focus:ring-2 focus:ring-blue-500 transition-colors shrink-0"
          aria-expanded={!isCollapsed}
          aria-label={isCollapsed ? 'Mở rộng thanh điều hướng' : 'Thu gọn thanh điều hướng'}
          title={isCollapsed ? 'Mở rộng thanh điều hướng' : 'Thu gọn thanh điều hướng'}
        >
          {isCollapsed ? (
            <ChevronRight className="h-5 w-5" />
          ) : (
            <ChevronLeft className="h-5 w-5" />
          )}
        </button>
      </div>

      {/* Navigation Menu */}
      <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-1" aria-label="Menu chính">
        {navItems.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              title={item.label}
              aria-label={item.label}
              className={({ isActive }) =>
                `flex items-center px-3 py-2.5 text-sm font-medium rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 ${
                  isCollapsed ? 'justify-center' : 'space-x-3'
                } ${
                  isActive
                    ? 'text-blue-700 bg-blue-50 font-semibold border border-blue-200/80 shadow-xs'
                    : 'text-slate-600 hover:text-slate-900 hover:bg-slate-100/80'
                }`
              }
            >
              <Icon className="h-5 w-5 shrink-0" />
              {!isCollapsed ? (
                <span className="truncate">{item.label}</span>
              ) : (
                <span className="sr-only">{item.label}</span>
              )}
            </NavLink>
          );
        })}
      </nav>

      {/* Sidebar Footer */}
      <div className="border-t border-slate-200/90 p-3 space-y-2">
        {/* Role Badge */}
        <div className="flex items-center justify-center">
          <span
            className={`text-xs px-2.5 py-0.5 rounded-full font-semibold border ${
              roleBadgeColors[role] || 'bg-slate-100 text-slate-700 border-slate-200'
            }`}
            title={`Vai trò: ${role}`}
            aria-label={`Vai trò: ${role}`}
          >
            {isCollapsed ? (
              <>
                <span className="sr-only">{role}</span>
                <span aria-hidden="true">{role.slice(0, 3)}</span>
              </>
            ) : (
              role
            )}
          </span>
        </div>

        {/* Profile Link */}
        <NavLink
          to="/profile"
          className={({ isActive }) =>
            `flex items-center px-3 py-2 text-sm font-medium rounded-lg transition-colors border focus:outline-none focus:ring-2 focus:ring-blue-500 ${
              isCollapsed ? 'justify-center' : 'space-x-2'
            } ${
              isActive
                ? 'border-blue-300 bg-blue-50 text-blue-700 font-semibold'
                : 'border-slate-200/80 text-slate-700 hover:bg-slate-100/80 hover:text-slate-900'
            }`
          }
          title={displayName}
          aria-label={displayName}
        >
          <UserIcon className="h-5 w-5 shrink-0 text-slate-400" />
          {!isCollapsed ? (
            <span className="truncate">{displayName}</span>
          ) : (
            <span className="sr-only">{displayName}</span>
          )}
        </NavLink>

        {/* Logout Button */}
        <button
          type="button"
          onClick={handleLogout}
          className={`w-full flex items-center px-3 py-2 text-sm font-medium text-slate-600 hover:text-rose-600 hover:bg-rose-50/80 rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-rose-500 ${
            isCollapsed ? 'justify-center' : 'space-x-2'
          }`}
          title="Đăng xuất"
          aria-label="Đăng xuất"
        >
          <LogOut className="h-5 w-5 shrink-0" />
          {!isCollapsed ? (
            <span>Đăng xuất</span>
          ) : (
            <span className="sr-only">Đăng xuất</span>
          )}
        </button>
      </div>
    </aside>
  );
};

export default Sidebar;

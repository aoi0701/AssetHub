import React from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { Package } from 'lucide-react';

const Navbar = () => {
  const { user, isAuthenticated } = useAuth();

  if (isAuthenticated && user) {
    return null;
  }

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
};

export default Navbar;

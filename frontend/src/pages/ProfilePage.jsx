import React, { useState, useEffect } from 'react';
import { useAuth } from '../context/AuthContext';
import { User, Lock, Mail, Phone, Shield, Building2, CheckCircle, AlertCircle } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

const ProfilePage = () => {
  const { user, updateProfile, changePassword } = useAuth();
  const navigate = useNavigate();

  const [activeTab, setActiveTab] = useState('profile'); // 'profile' | 'password'

  // Profile Form state
  const [profileData, setProfileData] = useState({
    full_name: '',
    email: '',
    phone: '',
  });
  const [profileSuccess, setProfileSuccess] = useState('');
  const [profileError, setProfileError] = useState('');
  const [profileLoading, setProfileLoading] = useState(false);

  // Password Form state
  const [passwordData, setPasswordData] = useState({
    old_password: '',
    new_password: '',
    new_password_confirm: '',
  });
  const [passwordSuccess, setPasswordSuccess] = useState('');
  const [passwordError, setPasswordError] = useState('');
  const [passwordLoading, setPasswordLoading] = useState(false);

  useEffect(() => {
    if (user) {
      setProfileData({
        full_name: user.full_name || '',
        email: user.email || '',
        phone: user.phone || '',
      });
    }
  }, [user]);

  const handleProfileSubmit = async (e) => {
    e.preventDefault();
    setProfileSuccess('');
    setProfileError('');
    setProfileLoading(true);

    try {
      await updateProfile({
        full_name: profileData.full_name.trim(),
        email: profileData.email.trim(),
        phone: profileData.phone.trim(),
      });
      setProfileSuccess('Cập nhật thông tin cá nhân thành công!');
    } catch (err) {
      if (err.response?.data) {
        const data = err.response.data;
        const msg = typeof data === 'object' ? Object.values(data).flat().join(' ') : String(data);
        setProfileError(msg);
      } else {
        setProfileError(err.message || 'Cập nhật thất bại.');
      }
    } finally {
      setProfileLoading(false);
    }
  };

  const handlePasswordSubmit = async (e) => {
    e.preventDefault();
    setPasswordSuccess('');
    setPasswordError('');

    if (passwordData.new_password !== passwordData.new_password_confirm) {
      setPasswordError('Mật khẩu mới xác nhận không khớp.');
      return;
    }

    setPasswordLoading(true);
    try {
      await changePassword(
        passwordData.old_password,
        passwordData.new_password,
        passwordData.new_password_confirm
      );
      setPasswordSuccess('Đổi mật khẩu thành công! Bạn sẽ được chuyển tới trang đăng nhập.');
      setTimeout(() => {
        navigate('/login');
      }, 1500);
    } catch (err) {
      if (err.response?.data) {
        const data = err.response.data;
        const msg = typeof data === 'object' ? Object.values(data).flat().join(' ') : String(data);
        setPasswordError(msg);
      } else {
        setPasswordError(err.message || 'Đổi mật khẩu thất bại.');
      }
    } finally {
      setPasswordLoading(false);
    }
  };

  if (!user) return null;

  return (
    <div className="w-full py-6 px-4 sm:px-6 lg:px-8">
      <div className="max-w-3xl mx-auto">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-gray-900">Hồ sơ cá nhân</h1>
          <p className="text-sm text-gray-500 mt-1">
            Quản lý thông tin tài khoản và bảo mật mật khẩu
          </p>
        </div>

        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
        {/* Navigation Tabs */}
        <div className="flex border-b border-gray-200 bg-gray-50/50">
          <button
            onClick={() => {
              setActiveTab('profile');
              setProfileSuccess('');
              setProfileError('');
            }}
            className={`flex items-center space-x-2 px-6 py-3.5 text-sm font-medium border-b-2 transition-colors ${
              activeTab === 'profile'
                ? 'border-blue-600 text-blue-600 bg-white'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-100/50'
            }`}
          >
            <User size={18} />
            <span>Thông tin cá nhân</span>
          </button>
          <button
            onClick={() => {
              setActiveTab('password');
              setPasswordSuccess('');
              setPasswordError('');
            }}
            className={`flex items-center space-x-2 px-6 py-3.5 text-sm font-medium border-b-2 transition-colors ${
              activeTab === 'password'
                ? 'border-blue-600 text-blue-600 bg-white'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-100/50'
            }`}
          >
            <Lock size={18} />
            <span>Đổi mật khẩu</span>
          </button>
        </div>

        <div className="p-6">
          {activeTab === 'profile' && (
            <div className="max-w-xl">
              {/* Readonly Overview Cards */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6 p-4 bg-gray-50 rounded-lg border border-gray-100">
                <div className="flex items-center space-x-3">
                  <div className="p-2 bg-blue-100 text-blue-600 rounded-lg">
                    <User size={18} />
                  </div>
                  <div>
                    <div className="text-xs text-gray-500 font-medium">Tên đăng nhập</div>
                    <div className="text-sm font-semibold text-gray-900">{user.username}</div>
                  </div>
                </div>

                <div className="flex items-center space-x-3">
                  <div className="p-2 bg-purple-100 text-purple-600 rounded-lg">
                    <Shield size={18} />
                  </div>
                  <div>
                    <div className="text-xs text-gray-500 font-medium">Vai trò</div>
                    <div className="text-sm font-semibold text-gray-900">{user.role}</div>
                  </div>
                </div>

                {user.department_name && (
                  <div className="flex items-center space-x-3 sm:col-span-2">
                    <div className="p-2 bg-emerald-100 text-emerald-600 rounded-lg">
                      <Building2 size={18} />
                    </div>
                    <div>
                      <div className="text-xs text-gray-500 font-medium">Phòng ban</div>
                      <div className="text-sm font-semibold text-gray-900">
                        {user.department_name} ({user.department_code || 'ID: ' + user.department_id})
                      </div>
                    </div>
                  </div>
                )}
              </div>

              {profileSuccess && (
                <div
                  role="status"
                  className="mb-5 p-4 bg-green-50 border-l-4 border-green-500 rounded-r-lg flex items-center space-x-3 text-green-700 text-sm"
                >
                  <CheckCircle className="w-5 h-5 flex-shrink-0" />
                  <div>{profileSuccess}</div>
                </div>
              )}

              {profileError && (
                <div
                  role="alert"
                  className="mb-5 p-4 bg-red-50 border-l-4 border-red-500 rounded-r-lg flex items-start space-x-3 text-red-700 text-sm"
                >
                  <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
                  <div>{profileError}</div>
                </div>
              )}

              <form onSubmit={handleProfileSubmit} className="space-y-4">
                <div>
                  <label
                    htmlFor="profile_full_name"
                    className="block text-sm font-medium text-gray-700 mb-1"
                  >
                    Họ và tên
                  </label>
                  <input
                    id="profile_full_name"
                    type="text"
                    value={profileData.full_name}
                    onChange={(e) =>
                      setProfileData((prev) => ({ ...prev, full_name: e.target.value }))
                    }
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    placeholder="Nguyễn Văn A"
                  />
                </div>

                <div>
                  <label
                    htmlFor="profile_email"
                    className="block text-sm font-medium text-gray-700 mb-1"
                  >
                    Email
                  </label>
                  <div className="relative">
                    <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-gray-400">
                      <Mail size={16} />
                    </div>
                    <input
                      id="profile_email"
                      type="email"
                      required
                      value={profileData.email}
                      onChange={(e) =>
                        setProfileData((prev) => ({ ...prev, email: e.target.value }))
                      }
                      className="w-full pl-9 pr-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                      placeholder="email@example.com"
                    />
                  </div>
                </div>

                <div>
                  <label
                    htmlFor="profile_phone"
                    className="block text-sm font-medium text-gray-700 mb-1"
                  >
                    Số điện thoại
                  </label>
                  <div className="relative">
                    <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-gray-400">
                      <Phone size={16} />
                    </div>
                    <input
                      id="profile_phone"
                      type="tel"
                      value={profileData.phone}
                      onChange={(e) =>
                        setProfileData((prev) => ({ ...prev, phone: e.target.value }))
                      }
                      className="w-full pl-9 pr-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                      placeholder="0912345678"
                    />
                  </div>
                </div>

                <div className="pt-2">
                  <button
                    type="submit"
                    disabled={profileLoading}
                    className="px-5 py-2.5 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-lg text-sm transition-colors shadow-sm disabled:opacity-50"
                  >
                    {profileLoading ? 'Đang cập nhật...' : 'Lưu thay đổi'}
                  </button>
                </div>
              </form>
            </div>
          )}

          {activeTab === 'password' && (
            <div className="max-w-xl">
              {passwordSuccess && (
                <div
                  role="status"
                  className="mb-5 p-4 bg-green-50 border-l-4 border-green-500 rounded-r-lg flex items-center space-x-3 text-green-700 text-sm"
                >
                  <CheckCircle className="w-5 h-5 flex-shrink-0" />
                  <div>{passwordSuccess}</div>
                </div>
              )}

              {passwordError && (
                <div
                  role="alert"
                  className="mb-5 p-4 bg-red-50 border-l-4 border-red-500 rounded-r-lg flex items-start space-x-3 text-red-700 text-sm"
                >
                  <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
                  <div>{passwordError}</div>
                </div>
              )}

              <form onSubmit={handlePasswordSubmit} className="space-y-4">
                <div>
                  <label
                    htmlFor="old_password"
                    className="block text-sm font-medium text-gray-700 mb-1"
                  >
                    Mật khẩu hiện tại <span className="text-red-500">*</span>
                  </label>
                  <input
                    id="old_password"
                    type="password"
                    required
                    value={passwordData.old_password}
                    onChange={(e) =>
                      setPasswordData((prev) => ({ ...prev, old_password: e.target.value }))
                    }
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    placeholder="••••••••"
                  />
                </div>

                <div>
                  <label
                    htmlFor="new_password"
                    className="block text-sm font-medium text-gray-700 mb-1"
                  >
                    Mật khẩu mới <span className="text-red-500">*</span>
                  </label>
                  <input
                    id="new_password"
                    type="password"
                    required
                    value={passwordData.new_password}
                    onChange={(e) =>
                      setPasswordData((prev) => ({ ...prev, new_password: e.target.value }))
                    }
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    placeholder="••••••••"
                  />
                  <p className="text-xs text-gray-500 mt-1">
                    Mật khẩu mới tối thiểu 8 ký tự, không quá phổ biến hoặc hoàn toàn là số.
                  </p>
                </div>

                <div>
                  <label
                    htmlFor="new_password_confirm"
                    className="block text-sm font-medium text-gray-700 mb-1"
                  >
                    Xác nhận mật khẩu mới <span className="text-red-500">*</span>
                  </label>
                  <input
                    id="new_password_confirm"
                    type="password"
                    required
                    value={passwordData.new_password_confirm}
                    onChange={(e) =>
                      setPasswordData((prev) => ({
                        ...prev,
                        new_password_confirm: e.target.value,
                      }))
                    }
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    placeholder="••••••••"
                  />
                </div>

                <div className="pt-2">
                  <button
                    type="submit"
                    disabled={passwordLoading}
                    className="px-5 py-2.5 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-lg text-sm transition-colors shadow-sm disabled:opacity-50"
                  >
                    {passwordLoading ? 'Đang đổi mật khẩu...' : 'Xác nhận đổi mật khẩu'}
                  </button>
                </div>
              </form>
            </div>
          )}
        </div>
      </div>
      </div>
    </div>
  );
};

export default ProfilePage;

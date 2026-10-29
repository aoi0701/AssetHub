import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import axiosClient from '../api/axiosClient';
import { useAuth } from '../context/AuthContext';
import AssetStatusBadge from '../components/AssetStatusBadge';
import AssetCreateModal from '../components/AssetCreateModal';
import {
  Package,
  Search,
  Plus,
  RefreshCw,
  AlertCircle,
  ExternalLink,
} from 'lucide-react';

const AssetListPage = () => {
  const { user } = useAuth();
  const isAdmin = user?.role === 'ADMIN';
  const navigate = useNavigate();

  const [assets, setAssets] = useState([]);
  const [categories, setCategories] = useState([]);
  const [departments, setDepartments] = useState([]);
  const [loading, setLoading] = useState(false);
  const [errorAlert, setErrorAlert] = useState('');

  // Filters
  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [departmentFilter, setDepartmentFilter] = useState('');

  // Create Modal
  const [isCreateOpen, setIsCreateOpen] = useState(false);

  const fetchDropdowns = useCallback(async () => {
    try {
      const catRes = await axiosClient.get('/api/categories/');
      setCategories(Array.isArray(catRes.data) ? catRes.data : catRes.data.results || []);
    } catch {
      // ignore
    }

    if (isAdmin) {
      try {
        const deptRes = await axiosClient.get('/api/departments/');
        setDepartments(Array.isArray(deptRes.data) ? deptRes.data : deptRes.data.results || []);
      } catch {
        // ignore
      }
    }
  }, [isAdmin]);

  const fetchAssets = useCallback(async () => {
    setLoading(true);
    setErrorAlert('');
    try {
      const params = {};
      if (searchTerm.trim()) params.search = searchTerm.trim();
      if (statusFilter) params.status = statusFilter;
      if (categoryFilter) params.category_id = categoryFilter;
      if (isAdmin && departmentFilter) params.department_id = departmentFilter;

      const res = await axiosClient.get('/api/assets/', { params });
      setAssets(Array.isArray(res.data) ? res.data : res.data.results || []);
    } catch {
      setErrorAlert('Không thể tải danh sách tài sản. Vui lòng thử lại.');
    } finally {
      setLoading(false);
    }
  }, [searchTerm, statusFilter, categoryFilter, departmentFilter, isAdmin]);

  useEffect(() => {
    fetchDropdowns();
  }, [fetchDropdowns]);

  useEffect(() => {
    fetchAssets();
  }, [fetchAssets]);

  return (
    <div className="max-w-7xl mx-auto py-8 px-4 sm:px-6 lg:px-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between pb-6 border-b border-gray-200 gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <Package className="h-7 w-7 text-blue-600" />
            Quản lý tài sản
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            {isAdmin
              ? 'Toàn quyền theo dõi, khởi tạo, điều chuyển và quản lý vòng đời tài sản công ty.'
              : `Theo dõi và quản lý tài sản thuộc phạm vi ${user?.department_name || 'phòng ban'}.`}
          </p>
        </div>

        <div className="flex items-center space-x-3 self-start sm:self-auto">
          <button
            onClick={fetchAssets}
            className="inline-flex items-center px-3 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 transition-colors"
          >
            <RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
            Làm mới
          </button>

          {isAdmin && (
            <button
              onClick={() => setIsCreateOpen(true)}
              className="inline-flex items-center px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium transition-colors shadow-sm"
            >
              <Plus className="h-4 w-4 mr-1.5" />
              Tạo tài sản mới
            </button>
          )}
        </div>
      </div>

      {/* Alerts */}
      {errorAlert && (
        <div
          role="alert"
          className="my-4 p-4 bg-red-50 border-l-4 border-red-500 rounded-r-lg flex items-start space-x-3 text-red-700 text-sm"
        >
          <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
          <div className="flex-1">{errorAlert}</div>
          <button
            onClick={() => setErrorAlert('')}
            className="text-red-500 hover:text-red-700 font-bold ml-2"
          >
            ×
          </button>
        </div>
      )}

      {/* Filters */}
      <div className="mt-6 bg-white p-4 rounded-xl border border-gray-200 shadow-sm grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {/* Search */}
        <div className="relative">
          <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-gray-400">
            <Search size={16} />
          </div>
          <input
            type="text"
            placeholder="Tìm theo mã, tên, serial..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full pl-9 pr-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>

        {/* Status Filter */}
        <div>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 text-gray-700"
            aria-label="Lọc theo trạng thái"
          >
            <option value="">Tất cả trạng thái</option>
            <option value="AVAILABLE">Sẵn sàng (AVAILABLE)</option>
            <option value="IN_USE">Đang sử dụng (IN_USE)</option>
            <option value="DAMAGED_PENDING_REPAIR">Hỏng chờ sửa</option>
            <option value="UNDER_MAINTENANCE">Đang sửa chữa</option>
            <option value="PENDING_DISPOSAL">Chờ thanh lý</option>
            <option value="DISPOSED">Đã thanh lý</option>
          </select>
        </div>

        {/* Category Filter */}
        <div>
          <select
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 text-gray-700"
            aria-label="Lọc theo danh mục"
          >
            <option value="">Tất cả danh mục</option>
            {categories.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>

        {/* Department Filter (Admin only) */}
        {isAdmin && (
          <div>
            <select
              value={departmentFilter}
              onChange={(e) => setDepartmentFilter(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 text-gray-700"
              aria-label="Lọc theo phòng ban"
            >
              <option value="">Tất cả phòng ban</option>
              <option value="unallocated">-- Kho công ty (Chưa gán) --</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name} ({d.code})
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {/* Asset Table */}
      <div className="mt-6 bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 text-left text-sm">
            <thead className="bg-gray-50 text-gray-700 font-semibold">
              <tr>
                <th className="px-6 py-3">Mã tài sản</th>
                <th className="px-6 py-3">Tên tài sản</th>
                <th className="px-6 py-3">Danh mục</th>
                <th className="px-6 py-3">Phòng ban</th>
                <th className="px-6 py-3">Người đang giữ</th>
                <th className="px-6 py-3">Trạng thái</th>
                <th className="px-6 py-3 text-right">Chi tiết</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 bg-white">
              {assets.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-6 py-12 text-center text-gray-500">
                    {loading ? 'Đang tải dữ liệu...' : 'Không tìm thấy tài sản nào phù hợp.'}
                  </td>
                </tr>
              ) : (
                assets.map((asset) => (
                  <tr
                    key={asset.id}
                    onClick={() => navigate(`/assets/${asset.id}`)}
                    className="hover:bg-gray-50/80 cursor-pointer transition-colors"
                  >
                    <td className="px-6 py-4 whitespace-nowrap font-mono font-semibold text-blue-600">
                      {asset.asset_code}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="font-medium text-gray-900">{asset.name}</div>
                      {asset.serial_number && (
                        <div className="text-xs text-gray-400">SN: {asset.serial_number}</div>
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-gray-700">
                      {asset.category_name || '-'}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-gray-700">
                      {asset.department_name ? (
                        <span>
                          {asset.department_name}{' '}
                          <span className="text-xs text-gray-400">({asset.department_code})</span>
                        </span>
                      ) : (
                        <span className="text-xs italic text-gray-400">Kho công ty</span>
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-gray-700">
                      {asset.assigned_to_user_name ? (
                        <div>
                          <span className="font-medium">{asset.assigned_to_user_name}</span>
                          <span className="text-xs text-gray-400 block">
                            @{asset.assigned_to_user_username}
                          </span>
                        </div>
                      ) : (
                        <span className="text-xs text-gray-400 italic">Chưa bàn giao</span>
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <AssetStatusBadge status={asset.status} />
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-right">
                      <Link
                        to={`/assets/${asset.id}`}
                        onClick={(e) => e.stopPropagation()}
                        className="inline-flex items-center text-xs font-medium text-blue-600 hover:text-blue-800"
                      >
                        Xem <ExternalLink className="w-3.5 h-3.5 ml-1" />
                      </Link>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Create Modal (Admin only) */}
      {isAdmin && (
        <AssetCreateModal
          isOpen={isCreateOpen}
          onClose={() => setIsCreateOpen(false)}
          onSuccess={() => {
            fetchAssets();
          }}
          categories={categories}
          departments={departments}
        />
      )}
    </div>
  );
};

export default AssetListPage;

import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import axiosClient from '../api/axiosClient';
import { useAuth } from '../context/AuthContext';
import AssetStatusBadge from '../components/AssetStatusBadge';
import AssetCreateModal from '../components/AssetCreateModal';
import Pagination from '../components/Pagination';
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

  // Pagination State
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize] = useState(15);
  const [totalCount, setTotalCount] = useState(0);
  const [totalPages, setTotalPages] = useState(1);

  // Filters
  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [departmentFilter, setDepartmentFilter] = useState('');

  // Create Modal
  const [isCreateOpen, setIsCreateOpen] = useState(false);

  const fetchDropdowns = useCallback(async () => {
    try {
      const catRes = await axiosClient.get('/api/categories/', { params: { all: 'true' } });
      setCategories(Array.isArray(catRes.data) ? catRes.data : catRes.data.results || []);
    } catch {
      // ignore
    }

    if (isAdmin) {
      try {
        const deptRes = await axiosClient.get('/api/departments/', { params: { all: 'true' } });
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
      const params = {
        page: currentPage,
        page_size: pageSize,
      };
      if (searchTerm.trim()) params.search = searchTerm.trim();
      if (statusFilter) params.status = statusFilter;
      if (categoryFilter) params.category_id = categoryFilter;
      if (isAdmin && departmentFilter) params.department_id = departmentFilter;

      const res = await axiosClient.get('/api/assets/', { params });
      if (res.data && Array.isArray(res.data.results)) {
        setAssets(res.data.results);
        setTotalCount(res.data.count || 0);
        setTotalPages(res.data.total_pages || Math.ceil((res.data.count || 0) / pageSize) || 1);
      } else if (Array.isArray(res.data)) {
        setAssets(res.data);
        setTotalCount(res.data.length);
        setTotalPages(1);
      } else {
        setAssets([]);
        setTotalCount(0);
        setTotalPages(1);
      }
    } catch {
      setErrorAlert('Không thể tải danh sách tài sản. Vui lòng thử lại.');
    } finally {
      setLoading(false);
    }
  }, [currentPage, pageSize, searchTerm, statusFilter, categoryFilter, departmentFilter, isAdmin]);

  useEffect(() => {
    fetchDropdowns();
  }, [fetchDropdowns]);

  useEffect(() => {
    fetchAssets();
  }, [fetchAssets]);

  return (
    <div className="w-full py-6 px-4 sm:px-6 lg:px-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between pb-6 border-b border-slate-200/90 gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2">
            <Package className="h-7 w-7 text-blue-600" />
            Quản lý tài sản
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            {isAdmin
              ? 'Toàn quyền theo dõi, khởi tạo, điều chuyển và quản lý vòng đời tài sản công ty.'
              : `Theo dõi và quản lý tài sản thuộc phạm vi ${user?.department_name || 'phòng ban'}.`}
          </p>
        </div>

        <div className="flex items-center space-x-3 self-start sm:self-auto">
          <button
            onClick={fetchAssets}
            className="inline-flex items-center px-3 py-2 border border-slate-300 rounded-lg text-sm font-medium text-slate-700 bg-white hover:bg-slate-50 transition-colors shadow-sm"
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
      <div className="mt-6 bg-white p-4 rounded-xl border border-slate-200/90 shadow-sm grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {/* Search */}
        <div className="relative">
          <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-slate-400">
            <Search size={16} />
          </div>
          <input
            type="text"
            placeholder="Tìm theo mã, tên, serial..."
            value={searchTerm}
            onChange={(e) => {
              setSearchTerm(e.target.value);
              setCurrentPage(1);
            }}
            className="w-full pl-9 pr-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 text-slate-800"
          />
        </div>

        {/* Status Filter */}
        <div>
          <select
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value);
              setCurrentPage(1);
            }}
            className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 text-slate-700 bg-white"
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
            onChange={(e) => {
              setCategoryFilter(e.target.value);
              setCurrentPage(1);
            }}
            className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 text-slate-700 bg-white"
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
              onChange={(e) => {
                setDepartmentFilter(e.target.value);
                setCurrentPage(1);
              }}
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 text-slate-700 bg-white"
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
      <div className="mt-6 bg-white border border-slate-200/90 rounded-xl shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
            <thead className="bg-slate-50 text-slate-600 font-semibold">
              <tr>
                <th className="px-4 py-3 whitespace-nowrap">Mã tài sản</th>
                <th className="px-4 py-3 whitespace-nowrap">Tên tài sản</th>
                <th className="px-4 py-3 whitespace-nowrap">Danh mục</th>
                <th className="px-4 py-3 whitespace-nowrap">Phòng ban</th>
                <th className="px-4 py-3 whitespace-nowrap">Người đang giữ</th>
                <th className="px-4 py-3 whitespace-nowrap">Trạng thái</th>
                <th className="sticky right-0 bg-slate-50 px-4 py-3 text-right whitespace-nowrap min-w-[100px] shadow-[-6px_0_10px_-4px_rgba(0,0,0,0.06)] z-10">
                  Chi tiết
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {assets.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-4 py-12 text-center text-slate-400">
                    {loading ? 'Đang tải dữ liệu...' : 'Không tìm thấy tài sản nào phù hợp.'}
                  </td>
                </tr>
              ) : (
                assets.map((asset) => (
                  <tr
                    key={asset.id}
                    onClick={() => navigate(`/assets/${asset.id}`)}
                    className="group hover:bg-slate-50/80 cursor-pointer transition-colors"
                  >
                    <td className="px-4 py-3.5 whitespace-nowrap font-mono font-semibold text-blue-600">
                      {asset.asset_code}
                    </td>
                    <td className="px-4 py-3.5 whitespace-nowrap">
                      <div className="font-medium text-slate-900">{asset.name}</div>
                      {asset.serial_number && (
                        <div className="text-xs text-slate-400">SN: {asset.serial_number}</div>
                      )}
                    </td>
                    <td className="px-4 py-3.5 whitespace-nowrap text-slate-600">
                      {asset.category_name || '-'}
                    </td>
                    <td className="px-4 py-3.5 whitespace-nowrap text-slate-600">
                      {asset.department_name ? (
                        <span>
                          {asset.department_name}{' '}
                          <span className="text-xs text-slate-400">({asset.department_code})</span>
                        </span>
                      ) : (
                        <span className="text-xs italic text-slate-400">Kho công ty</span>
                      )}
                    </td>
                    <td className="px-4 py-3.5 whitespace-nowrap text-slate-600">
                      {asset.assigned_to_user_name ? (
                        <div>
                          <span className="font-medium text-slate-800">{asset.assigned_to_user_name}</span>
                          <span className="text-xs text-slate-400 block">
                            @{asset.assigned_to_user_username}
                          </span>
                        </div>
                      ) : (
                        <span className="text-xs text-slate-400 italic">Chưa bàn giao</span>
                      )}
                    </td>
                    <td className="px-4 py-3.5 whitespace-nowrap">
                      <AssetStatusBadge status={asset.status} />
                    </td>
                    <td className="sticky right-0 bg-white group-hover:bg-slate-50/90 px-4 py-3.5 whitespace-nowrap text-right min-w-[100px] shadow-[-6px_0_10px_-4px_rgba(0,0,0,0.06)] z-10 transition-colors">
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

        <Pagination
          currentPage={currentPage}
          totalPages={totalPages}
          totalCount={totalCount}
          pageSize={pageSize}
          onPageChange={(page) => setCurrentPage(page)}
          loading={loading}
        />
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

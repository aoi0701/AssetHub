import React, { useState, useEffect, useCallback, useRef } from 'react';
import axiosClient from '../api/axiosClient';
import { useAuth } from '../context/AuthContext';
import Pagination from '../components/Pagination';
import { Users, Search, Filter, Edit, CheckCircle, XCircle, AlertCircle, RefreshCw, X } from 'lucide-react';

const UserAdminPage = () => {
  const { user: currentUser } = useAuth();
  const isAdmin = currentUser?.role === 'ADMIN';

  const [users, setUsers] = useState([]);
  const [departments, setDepartments] = useState([]);
  const [loading, setLoading] = useState(false);
  const [errorAlert, setErrorAlert] = useState('');
  const [successAlert, setSuccessAlert] = useState('');

  // Pagination State
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize] = useState(15);
  const [totalCount, setTotalCount] = useState(0);
  const [totalPages, setTotalPages] = useState(1);

  // Filters
  const [searchTerm, setSearchTerm] = useState('');
  const [roleFilter, setRoleFilter] = useState('');
  const [deptFilter, setDeptFilter] = useState('');
  const [activeFilter, setActiveFilter] = useState('');

  // Edit Modal
  const [editingUser, setEditingUser] = useState(null);
  const [modalRole, setModalRole] = useState('USER');
  const [modalDeptId, setModalDeptId] = useState('');
  const [modalLoading, setModalLoading] = useState(false);
  const [modalError, setModalError] = useState('');

  const reqIdRef = useRef(0);

  const fetchDepartments = useCallback(async () => {
    try {
      const res = await axiosClient.get('/api/departments/', { params: { all: 'true' } });
      setDepartments(Array.isArray(res.data) ? res.data : res.data.results || []);
    } catch {
      // departments might fail for unprivileged, ignore
    }
  }, []);

  const fetchUsers = useCallback(async (isCancelled = () => false) => {
    const isAborted = typeof isCancelled === 'function' ? isCancelled : () => false;
    const currentReqId = ++reqIdRef.current;
    setLoading(true);
    setErrorAlert('');
    try {
      const params = {
        page: currentPage,
        page_size: pageSize,
      };
      if (searchTerm.trim()) params.search = searchTerm.trim();
      if (roleFilter) params.role = roleFilter;
      if (deptFilter) params.department_id = deptFilter;
      if (activeFilter !== '') params.is_active = activeFilter;

      const res = await axiosClient.get('/api/users/', { params });
      if (isAborted() || currentReqId !== reqIdRef.current) return;
      if (res.data && Array.isArray(res.data.results)) {
        setUsers(res.data.results);
        const count = res.data.count || 0;
        const total = res.data.total_pages || Math.ceil(count / pageSize) || 1;
        setTotalCount(count);
        setTotalPages(total);
        if (currentPage > total && total > 0) {
          setCurrentPage(total);
        }
      } else if (Array.isArray(res.data)) {
        setUsers(res.data);
        setTotalCount(res.data.length);
        setTotalPages(1);
      } else {
        setUsers([]);
        setTotalCount(0);
        setTotalPages(1);
      }
    } catch {
      if (isAborted() || currentReqId !== reqIdRef.current) return;
      setErrorAlert('Không thể tải danh sách người dùng. Vui lòng thử lại.');
    } finally {
      if (!isAborted() && currentReqId === reqIdRef.current) {
        setLoading(false);
      }
    }
  }, [currentPage, pageSize, searchTerm, roleFilter, deptFilter, activeFilter]);

  useEffect(() => {
    fetchDepartments();
  }, [fetchDepartments]);

  useEffect(() => {
    let active = true;
    fetchUsers(() => !active);
    return () => {
      active = false;
    };
  }, [fetchUsers]);

  useEffect(() => {
    if (currentPage > totalPages && totalPages > 0) {
      setCurrentPage(totalPages);
    }
  }, [currentPage, totalPages]);

  const handleToggleActive = async (targetUser) => {
    if (!isAdmin) return;
    setErrorAlert('');
    setSuccessAlert('');

    const newActive = !targetUser.is_active;
    try {
      await axiosClient.patch(`/api/users/${targetUser.id}/`, {
        is_active: newActive,
      });
      setSuccessAlert(
        `Đã ${newActive ? 'kích hoạt' : 'vô hiệu hóa'} tài khoản ${targetUser.username} thành công.`
      );
      fetchUsers();
    } catch (err) {
      if (err.response?.status === 409) {
        setErrorAlert(
          err.response.data.detail ||
            'Tài khoản đang giữ tài sản hoặc có phiếu chờ xử lý, không thể đổi phòng ban hoặc vô hiệu hóa.'
        );
      } else if (err.response?.data?.detail) {
        setErrorAlert(err.response.data.detail);
      } else {
        const errorMsg =
          err.response?.data && typeof err.response.data === 'object'
            ? Object.values(err.response.data).flat().join(' ')
            : 'Thao tác không thành công.';
        setErrorAlert(errorMsg);
      }
    }
  };

  const handleOpenEditModal = (targetUser) => {
    setEditingUser(targetUser);
    setModalRole(targetUser.role);
    setModalDeptId(targetUser.department_id ? String(targetUser.department_id) : '');
    setModalError('');
  };

  const handleSaveUserModal = async (e) => {
    e.preventDefault();
    if (!editingUser) return;
    setModalError('');
    setModalLoading(true);

    try {
      const payload = {
        role: modalRole,
        department_id: modalDeptId ? parseInt(modalDeptId, 10) : null,
      };

      await axiosClient.patch(`/api/users/${editingUser.id}/`, payload);
      setSuccessAlert(`Cập nhật tài khoản ${editingUser.username} thành công.`);
      setEditingUser(null);
      fetchUsers();
    } catch (err) {
      if (err.response?.status === 409) {
        setModalError(
          err.response.data.detail ||
            'Tài khoản đang giữ tài sản hoặc có phiếu chờ xử lý, không thể đổi phòng ban hoặc vô hiệu hóa.'
        );
      } else if (err.response?.data) {
        const data = err.response.data;
        const msg =
          typeof data === 'object' ? Object.values(data).flat().join(' ') : String(data);
        setModalError(msg);
      } else {
        setModalError('Lỗi cập nhật người dùng.');
      }
    } finally {
      setModalLoading(false);
    }
  };

  return (
    <div className="w-full py-6 px-4 sm:px-6 lg:px-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between pb-6 border-b border-gray-200 gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <Users className="h-7 w-7 text-blue-600" />
            {isAdmin ? 'Quản lý người dùng' : 'Danh sách nhân viên phòng ban'}
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            {isAdmin
              ? 'Xem và quản lý tất cả tài khoản, vai trò và trạng thái kích hoạt.'
              : 'Xem danh sách nhân viên trong phòng ban của bạn (chế độ chỉ đọc).'}
          </p>
        </div>

        <button
          onClick={() => fetchUsers()}
          className="inline-flex items-center px-3 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 transition-colors self-start sm:self-auto"
        >
          <RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
          Làm mới
        </button>
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

      {successAlert && (
        <div
          role="status"
          className="my-4 p-4 bg-green-50 border-l-4 border-green-500 rounded-r-lg flex items-center space-x-3 text-green-700 text-sm"
        >
          <CheckCircle className="w-5 h-5 flex-shrink-0" />
          <div className="flex-1">{successAlert}</div>
          <button
            onClick={() => setSuccessAlert('')}
            className="text-green-500 hover:text-green-700 font-bold ml-2"
          >
            ×
          </button>
        </div>
      )}

      {/* Filters (Admin & Manager) */}
      <div className="mt-6 bg-white p-4 rounded-xl border border-gray-200 shadow-sm grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {/* Search */}
        <div className="relative">
          <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-gray-400">
            <Search size={16} />
          </div>
          <input
            type="text"
            placeholder="Tìm theo tên, username, email..."
            value={searchTerm}
            onChange={(e) => {
              setSearchTerm(e.target.value);
              setCurrentPage(1);
            }}
            className="w-full pl-9 pr-8 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          {searchTerm && (
            <button
              onClick={() => {
                setSearchTerm('');
                setCurrentPage(1);
              }}
              className="absolute inset-y-0 right-0 pr-2.5 flex items-center text-gray-400 hover:text-gray-600"
              title="Xóa tìm kiếm"
              aria-label="Xóa tìm kiếm"
            >
              <X size={14} />
            </button>
          )}
        </div>

        {/* Role Filter */}
        <div>
          <select
            value={roleFilter}
            onChange={(e) => {
              setRoleFilter(e.target.value);
              setCurrentPage(1);
            }}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 text-gray-700"
            aria-label="Lọc theo vai trò"
          >
            <option value="">Tất cả vai trò</option>
            <option value="ADMIN">ADMIN (Quản trị viên)</option>
            <option value="MANAGER">MANAGER (Quản lý)</option>
            <option value="USER">USER (Nhân viên)</option>
          </select>
        </div>

        {/* Department Filter (Admin view) */}
        {isAdmin && (
          <div>
            <select
              value={deptFilter}
              onChange={(e) => {
                setDeptFilter(e.target.value);
                setCurrentPage(1);
              }}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 text-gray-700"
              aria-label="Lọc theo phòng ban"
            >
              <option value="">Tất cả phòng ban</option>
              {departments.map((dept) => (
                <option key={dept.id} value={dept.id}>
                  {dept.name} ({dept.code})
                </option>
              ))}
            </select>
          </div>
        )}

        {/* Status Filter */}
        <div>
          <select
            value={activeFilter}
            onChange={(e) => {
              setActiveFilter(e.target.value);
              setCurrentPage(1);
            }}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 text-gray-700"
            aria-label="Lọc theo trạng thái hoạt động"
          >
            <option value="">Tất cả trạng thái</option>
            <option value="true">Đang hoạt động</option>
            <option value="false">Đã vô hiệu hóa / Chờ duyệt</option>
          </select>
        </div>
      </div>

      {/* Users Table */}
      <div className="mt-6 bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 text-left text-sm">
            <thead className="bg-gray-50 text-gray-700 font-semibold">
              <tr>
                <th className="px-6 py-3">Người dùng</th>
                <th className="px-6 py-3">Liên hệ</th>
                <th className="px-6 py-3">Vai trò</th>
                <th className="px-6 py-3">Phòng ban</th>
                <th className="px-6 py-3">Trạng thái</th>
                {isAdmin && <th className="px-6 py-3 text-right">Thao tác</th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 bg-white">
              {users.length === 0 ? (
                <tr>
                  <td
                    colSpan={isAdmin ? 6 : 5}
                    className="px-6 py-12 text-center text-gray-500"
                  >
                    {loading ? 'Đang tải dữ liệu...' : 'Không tìm thấy người dùng nào phù hợp.'}
                  </td>
                </tr>
              ) : (
                users.map((u) => (
                  <tr key={u.id} className="hover:bg-gray-50/75 transition-colors">
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="font-medium text-gray-900">{u.full_name || u.username}</div>
                      <div className="text-xs text-gray-500">@{u.username}</div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="text-gray-900">{u.email}</div>
                      {u.phone && <div className="text-xs text-gray-500">{u.phone}</div>}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span
                        className={`inline-block px-2.5 py-0.5 rounded-full text-xs font-semibold ${
                          u.role === 'ADMIN'
                            ? 'bg-purple-100 text-purple-800'
                            : u.role === 'MANAGER'
                            ? 'bg-blue-100 text-blue-800'
                            : 'bg-green-100 text-green-800'
                        }`}
                      >
                        {u.role}
                      </span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-gray-700">
                      {u.department_name ? (
                        <span>
                          {u.department_name}{' '}
                          <span className="text-xs text-gray-400">({u.department_code})</span>
                        </span>
                      ) : (
                        <span className="text-xs italic text-gray-400">Chưa gán</span>
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      {u.is_active ? (
                        <span className="inline-flex items-center text-xs font-medium text-emerald-700 bg-emerald-50 px-2.5 py-0.5 rounded-full border border-emerald-200">
                          <CheckCircle className="w-3.5 h-3.5 mr-1 text-emerald-600" />
                          Hoạt động
                        </span>
                      ) : (
                        <span className="inline-flex items-center text-xs font-medium text-gray-600 bg-gray-100 px-2.5 py-0.5 rounded-full border border-gray-200">
                          <XCircle className="w-3.5 h-3.5 mr-1 text-gray-500" />
                          Vô hiệu hóa
                        </span>
                      )}
                    </td>
                    {isAdmin && (
                      <td className="px-6 py-4 whitespace-nowrap text-right space-x-2">
                        <button
                          onClick={() => handleOpenEditModal(u)}
                          className="inline-flex items-center px-2.5 py-1 text-xs font-medium text-blue-700 bg-blue-50 hover:bg-blue-100 rounded border border-blue-200 transition-colors"
                          title="Sửa vai trò và phòng ban"
                        >
                          <Edit className="w-3.5 h-3.5 mr-1" />
                          Sửa
                        </button>

                        <button
                          onClick={() => handleToggleActive(u)}
                          className={`inline-flex items-center px-2.5 py-1 text-xs font-medium rounded border transition-colors ${
                            u.is_active
                              ? 'text-amber-700 bg-amber-50 hover:bg-amber-100 border-amber-200'
                              : 'text-emerald-700 bg-emerald-50 hover:bg-emerald-100 border-emerald-200'
                          }`}
                        >
                          {u.is_active ? 'Khóa' : 'Kích hoạt'}
                        </button>
                      </td>
                    )}
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

      {/* Edit Role & Department Modal (Admin only, NO DELETE BUTTON) */}
      {isAdmin && editingUser && (
        <div
          role="dialog"
          aria-modal="true"
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm"
        >
          <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-6 border border-gray-100">
            <h3 className="text-lg font-bold text-gray-900 mb-1">
              Phân quyền & Phòng ban
            </h3>
            <p className="text-xs text-gray-500 mb-4">
              Cập nhật vai trò và phòng ban cho người dùng: <strong>{editingUser.username}</strong>
            </p>

            {modalError && (
              <div
                role="alert"
                className="mb-4 p-3 bg-red-50 border-l-4 border-red-500 rounded-r-lg text-xs text-red-700"
              >
                {modalError}
              </div>
            )}

            <form onSubmit={handleSaveUserModal} className="space-y-4">
              <div>
                <label
                  htmlFor="modal_role"
                  className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
                >
                  Vai trò
                </label>
                <select
                  id="modal_role"
                  value={modalRole}
                  onChange={(e) => setModalRole(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="ADMIN">ADMIN (Quản trị hệ thống)</option>
                  <option value="MANAGER">MANAGER (Quản lý phòng ban)</option>
                  <option value="USER">USER (Nhân viên)</option>
                </select>
              </div>

              <div>
                <label
                  htmlFor="modal_department"
                  className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
                >
                  Phòng ban
                </label>
                <select
                  id="modal_department"
                  value={modalDeptId}
                  onChange={(e) => setModalDeptId(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="">-- Không chọn / Chưa gán --</option>
                  {departments.map((dept) => (
                    <option key={dept.id} value={dept.id}>
                      {dept.name} ({dept.code})
                    </option>
                  ))}
                </select>
                <p className="text-xs text-gray-500 mt-1">
                  Nhân viên và Quản lý đang hoạt động bắt buộc phải thuộc một phòng ban.
                </p>
              </div>

              <div className="flex justify-end space-x-3 pt-3 border-t border-gray-100">
                <button
                  type="button"
                  onClick={() => setEditingUser(null)}
                  className="px-4 py-2 border border-gray-300 rounded-lg text-sm text-gray-700 hover:bg-gray-50"
                >
                  Hủy
                </button>
                <button
                  type="submit"
                  disabled={modalLoading}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium disabled:opacity-50"
                >
                  {modalLoading ? 'Đang lưu...' : 'Lưu thay đổi'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default UserAdminPage;

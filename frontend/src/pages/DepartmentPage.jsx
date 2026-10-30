import React, { useState, useEffect, useCallback } from 'react';
import axiosClient from '../api/axiosClient';
import Pagination from '../components/Pagination';
import { Building2, Plus, Edit, AlertCircle, CheckCircle, RefreshCw } from 'lucide-react';

const DepartmentPage = () => {
  const [departments, setDepartments] = useState([]);
  const [loading, setLoading] = useState(false);
  const [errorAlert, setErrorAlert] = useState('');
  const [successAlert, setSuccessAlert] = useState('');

  // Pagination State
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize] = useState(15);
  const [totalCount, setTotalCount] = useState(0);
  const [totalPages, setTotalPages] = useState(1);

  // Modal State (Create or Edit)
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingDept, setEditingDept] = useState(null); // null means create mode
  const [modalForm, setModalForm] = useState({
    code: '',
    name: '',
    description: '',
  });
  const [modalLoading, setModalLoading] = useState(false);
  const [modalError, setModalError] = useState('');

  const fetchDepartments = useCallback(async () => {
    setLoading(true);
    setErrorAlert('');
    try {
      const params = {
        page: currentPage,
        page_size: pageSize,
      };
      const res = await axiosClient.get('/api/departments/', { params });
      if (res.data && Array.isArray(res.data.results)) {
        setDepartments(res.data.results);
        setTotalCount(res.data.count || 0);
        setTotalPages(res.data.total_pages || Math.ceil((res.data.count || 0) / pageSize) || 1);
      } else if (Array.isArray(res.data)) {
        setDepartments(res.data);
        setTotalCount(res.data.length);
        setTotalPages(1);
      } else {
        setDepartments([]);
        setTotalCount(0);
        setTotalPages(1);
      }
    } catch {
      setErrorAlert('Không thể tải danh sách phòng ban. Vui lòng thử lại.');
    } finally {
      setLoading(false);
    }
  }, [currentPage, pageSize]);

  useEffect(() => {
    fetchDepartments();
  }, [fetchDepartments]);

  const handleOpenCreateModal = () => {
    setEditingDept(null);
    setModalForm({
      code: '',
      name: '',
      description: '',
    });
    setModalError('');
    setIsModalOpen(true);
  };

  const handleOpenEditModal = (dept) => {
    setEditingDept(dept);
    setModalForm({
      code: dept.code,
      name: dept.name,
      description: dept.description || '',
    });
    setModalError('');
    setIsModalOpen(true);
  };

  const handleSaveModal = async (e) => {
    e.preventDefault();
    setModalError('');
    setModalLoading(true);

    try {
      if (editingDept) {
        // Edit mode (PATCH)
        await axiosClient.patch(`/api/departments/${editingDept.id}/`, {
          name: modalForm.name.trim(),
          description: modalForm.description.trim(),
          code: modalForm.code.trim().toUpperCase(),
        });
        setSuccessAlert(`Cập nhật phòng ban ${modalForm.name} thành công.`);
      } else {
        // Create mode (POST)
        await axiosClient.post('/api/departments/', {
          code: modalForm.code.trim().toUpperCase(),
          name: modalForm.name.trim(),
          description: modalForm.description.trim(),
        });
        setSuccessAlert(`Tạo phòng ban ${modalForm.name} thành công.`);
      }
      setIsModalOpen(false);
      fetchDepartments();
    } catch (err) {
      if (err.response?.data) {
        const data = err.response.data;
        const msg =
          typeof data === 'object' ? Object.values(data).flat().join(' ') : String(data);
        setModalError(msg);
      } else {
        setModalError(err.message || 'Lỗi lưu thông tin phòng ban.');
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
            <Building2 className="h-7 w-7 text-blue-600" />
            Quản lý phòng ban
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Quản lý cơ cấu phòng ban và phân bổ tài sản doanh nghiệp
          </p>
        </div>

        <div className="flex items-center space-x-3">
          <button
            onClick={fetchDepartments}
            className="inline-flex items-center px-3 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 transition-colors"
          >
            <RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
            Làm mới
          </button>
          <button
            onClick={handleOpenCreateModal}
            className="inline-flex items-center px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium transition-colors shadow-sm"
          >
            <Plus className="h-4 w-4 mr-1.5" />
            Thêm phòng ban
          </button>
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

      {/* Table */}
      <div className="mt-6 bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 text-left text-sm">
            <thead className="bg-gray-50 text-gray-700 font-semibold">
              <tr>
                <th className="px-6 py-3">Mã phòng ban</th>
                <th className="px-6 py-3">Tên phòng ban</th>
                <th className="px-6 py-3">Mô tả</th>
                <th className="px-6 py-3 text-right">Thao tác</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 bg-white">
              {departments.length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-6 py-12 text-center text-gray-500">
                    {loading ? 'Đang tải dữ liệu...' : 'Chưa có phòng ban nào.'}
                  </td>
                </tr>
              ) : (
                departments.map((dept) => (
                  <tr key={dept.id} className="hover:bg-gray-50/75 transition-colors">
                    <td className="px-6 py-4 whitespace-nowrap font-mono font-medium text-blue-700">
                      {dept.code}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap font-medium text-gray-900">
                      {dept.name}
                    </td>
                    <td className="px-6 py-4 text-gray-600 max-w-md truncate">
                      {dept.description || <span className="italic text-gray-400">Không có mô tả</span>}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-right">
                      <button
                        onClick={() => handleOpenEditModal(dept)}
                        className="inline-flex items-center px-2.5 py-1 text-xs font-medium text-blue-700 bg-blue-50 hover:bg-blue-100 rounded border border-blue-200 transition-colors"
                        title="Chỉnh sửa phòng ban"
                      >
                        <Edit className="w-3.5 h-3.5 mr-1" />
                        Sửa
                      </button>
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

      {/* Modal: Create / Edit Department (STRICT: NO DELETE BUTTON) */}
      {isModalOpen && (
        <div
          role="dialog"
          aria-modal="true"
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm"
        >
          <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-6 border border-gray-100">
            <h3 className="text-lg font-bold text-gray-900 mb-1">
              {editingDept ? 'Chỉnh sửa phòng ban' : 'Tạo phòng ban mới'}
            </h3>
            <p className="text-xs text-gray-500 mb-4">
              {editingDept
                ? `Cập nhật thông tin phòng ban ${editingDept.name}`
                : 'Thêm phòng ban mới vào hệ thống AssetHub'}
            </p>

            {modalError && (
              <div
                role="alert"
                className="mb-4 p-3 bg-red-50 border-l-4 border-red-500 rounded-r-lg text-xs text-red-700"
              >
                {modalError}
              </div>
            )}

            <form onSubmit={handleSaveModal} className="space-y-4">
              <div>
                <label
                  htmlFor="dept_code"
                  className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
                >
                  Mã phòng ban <span className="text-red-500">*</span>
                </label>
                <input
                  id="dept_code"
                  type="text"
                  required
                  value={modalForm.code}
                  onChange={(e) =>
                    setModalForm((prev) => ({ ...prev, code: e.target.value.toUpperCase() }))
                  }
                  placeholder="vd: IT, HR, FIN"
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500 uppercase"
                />
              </div>

              <div>
                <label
                  htmlFor="dept_name"
                  className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
                >
                  Tên phòng ban <span className="text-red-500">*</span>
                </label>
                <input
                  id="dept_name"
                  type="text"
                  required
                  value={modalForm.name}
                  onChange={(e) =>
                    setModalForm((prev) => ({ ...prev, name: e.target.value }))
                  }
                  placeholder="vd: Phòng Công nghệ Thông tin"
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <div>
                <label
                  htmlFor="dept_description"
                  className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
                >
                  Mô tả
                </label>
                <textarea
                  id="dept_description"
                  rows={3}
                  value={modalForm.description}
                  onChange={(e) =>
                    setModalForm((prev) => ({ ...prev, description: e.target.value }))
                  }
                  placeholder="Mô tả chức năng nhiệm vụ của phòng ban..."
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <div className="flex justify-end space-x-3 pt-3 border-t border-gray-100">
                <button
                  type="button"
                  onClick={() => setIsModalOpen(false)}
                  className="px-4 py-2 border border-gray-300 rounded-lg text-sm text-gray-700 hover:bg-gray-50"
                >
                  Hủy
                </button>
                <button
                  type="submit"
                  disabled={modalLoading}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium disabled:opacity-50"
                >
                  {modalLoading ? 'Đang lưu...' : 'Lưu phòng ban'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default DepartmentPage;

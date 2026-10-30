import React, { useState, useEffect, useCallback, useRef } from 'react';
import axiosClient from '../api/axiosClient';
import Pagination from '../components/Pagination';
import { Building2, Plus, Edit, AlertCircle, CheckCircle, RefreshCw, Search, X } from 'lucide-react';

const DepartmentPage = () => {
  const [departments, setDepartments] = useState([]);
  const [loading, setLoading] = useState(false);
  const [errorAlert, setErrorAlert] = useState('');
  const [successAlert, setSuccessAlert] = useState('');

  // Search filter
  const [searchTerm, setSearchTerm] = useState('');

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

  const reqIdRef = useRef(0);

  const fetchDepartments = useCallback(async (isCancelled = () => false) => {
    const isAborted = typeof isCancelled === 'function' ? isCancelled : () => false;
    const currentReqId = ++reqIdRef.current;
    setLoading(true);
    setErrorAlert('');
    try {
      const params = {
        page: currentPage,
        page_size: pageSize,
      };
      if (searchTerm.trim()) {
        params.search = searchTerm.trim();
      }
      const res = await axiosClient.get('/api/departments/', { params });
      if (isAborted() || currentReqId !== reqIdRef.current) return;
      if (res.data && Array.isArray(res.data.results)) {
        setDepartments(res.data.results);
        const count = res.data.count || 0;
        const total = res.data.total_pages || Math.ceil(count / pageSize) || 1;
        setTotalCount(count);
        setTotalPages(total);
        if (currentPage > total && total > 0) {
          setCurrentPage(total);
        }
      } else if (Array.isArray(res.data)) {
        setDepartments(res.data);
        setTotalCount(res.data.length);
        setTotalPages(1);
      } else {
        setDepartments([]);
        setTotalCount(0);
        setTotalPages(1);
      }
    } catch (err) {
      if (isAborted() || currentReqId !== reqIdRef.current) return;
      if (err.response?.status === 404 && currentPage > 1) {
        setCurrentPage((prev) => (prev > 1 ? prev - 1 : 1));
        return;
      }
      setErrorAlert('Không thể tải danh sách phòng ban. Vui lòng thử lại.');
    } finally {
      if (!isAborted() && currentReqId === reqIdRef.current) {
        setLoading(false);
      }
    }
  }, [currentPage, pageSize, searchTerm]);

  useEffect(() => {
    let active = true;
    fetchDepartments(() => !active);
    return () => {
      active = false;
    };
  }, [fetchDepartments]);

  useEffect(() => {
    if (currentPage > totalPages && totalPages > 0) {
      setCurrentPage(totalPages);
    }
  }, [currentPage, totalPages]);

  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape' && isModalOpen && !modalLoading) {
        setIsModalOpen(false);
      }
    };
    if (isModalOpen) {
      window.addEventListener('keydown', handleKeyDown);
    }
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isModalOpen, modalLoading]);

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
            onClick={() => fetchDepartments()}
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

      {/* Toolbar: Search & Count */}
      <div className="mt-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 bg-white p-3.5 rounded-xl border border-gray-200 shadow-xs">
        <div className="relative flex-1 max-w-md">
          <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-gray-400">
            <Search size={16} />
          </div>
          <input
            type="text"
            placeholder="Tìm theo tên hoặc mã phòng ban..."
            value={searchTerm}
            onChange={(e) => {
              setSearchTerm(e.target.value);
              setCurrentPage(1);
            }}
            className="w-full pl-9 pr-8 py-2 border border-gray-300 rounded-lg text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-gray-50/50 hover:bg-white transition-colors"
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

        <div className="text-xs text-gray-500 font-medium sm:pr-2">
          {searchTerm ? (
            <span>
              Tìm thấy <strong className="text-gray-800">{totalCount}</strong> phòng ban
            </span>
          ) : (
            <span>
              Tổng số: <strong className="text-gray-800">{totalCount}</strong> phòng ban
            </span>
          )}
        </div>
      </div>

      {/* Table */}
      <div className="mt-4 bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
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
              {loading && departments.length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-6 py-12 text-center text-gray-400">
                    <RefreshCw className="h-6 w-6 animate-spin mx-auto text-blue-600 mb-2" />
                    Đang tải danh sách phòng ban...
                  </td>
                </tr>
              ) : departments.length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-6 py-12 text-center text-gray-500">
                    {searchTerm ? (
                      <>
                        <Search className="h-8 w-8 text-gray-300 mx-auto mb-2" />
                        <p className="font-medium text-gray-700">Không tìm thấy kết quả phù hợp</p>
                        <p className="text-xs text-gray-400 mt-1">
                          Không có phòng ban nào chứa từ khóa "{searchTerm}".
                        </p>
                        <button
                          onClick={() => {
                            setSearchTerm('');
                            setCurrentPage(1);
                          }}
                          className="mt-3 text-xs text-blue-600 hover:text-blue-800 font-medium underline"
                        >
                          Xóa bộ lọc tìm kiếm
                        </button>
                      </>
                    ) : (
                      <>
                        <Building2 className="h-8 w-8 text-gray-300 mx-auto mb-2" />
                        <p className="font-medium text-gray-700">Chưa có phòng ban nào</p>
                        <p className="text-xs text-gray-400 mt-1">
                          Tạo phòng ban đầu tiên để bắt đầu phân loại tài sản doanh nghiệp.
                        </p>
                        <button
                          onClick={handleOpenCreateModal}
                          className="mt-3 inline-flex items-center px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-medium transition-colors shadow-xs"
                        >
                          <Plus className="h-3.5 w-3.5 mr-1" />
                          Thêm phòng ban mới
                        </button>
                      </>
                    )}
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
          aria-labelledby="dept-modal-title"
          onClick={(e) => {
            if (e.target === e.currentTarget && !modalLoading) setIsModalOpen(false);
          }}
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/40 backdrop-blur-xs animate-in fade-in duration-150"
        >
          <div className="bg-white rounded-2xl shadow-xl max-w-md w-full p-6 border border-slate-200/90">
            <div className="flex items-start justify-between pb-3 border-b border-slate-100 mb-4">
              <div>
                <h3 id="dept-modal-title" className="text-lg font-bold text-slate-900">
                  {editingDept ? 'Chỉnh sửa phòng ban' : 'Tạo phòng ban mới'}
                </h3>
                <p className="text-xs text-slate-500 mt-0.5">
                  {editingDept
                    ? `Cập nhật thông tin phòng ban ${editingDept.name}`
                    : 'Thêm phòng ban mới vào hệ thống AssetHub'}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setIsModalOpen(false)}
                disabled={modalLoading}
                className="p-1.5 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500"
                aria-label="Đóng"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {modalError && (
              <div
                role="alert"
                className="mb-4 p-3 bg-rose-50 border-l-4 border-rose-500 rounded-r-lg text-xs text-rose-700 flex items-start space-x-2"
              >
                <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5 text-rose-500" />
                <div className="flex-1">{modalError}</div>
              </div>
            )}

            <form onSubmit={handleSaveModal} className="space-y-4">
              <div>
                <label
                  htmlFor="dept_code"
                  className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1"
                >
                  Mã phòng ban <span className="text-rose-500">*</span>
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
                  className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm font-mono text-slate-800 uppercase focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-colors"
                />
              </div>

              <div>
                <label
                  htmlFor="dept_name"
                  className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1"
                >
                  Tên phòng ban <span className="text-rose-500">*</span>
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
                  className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-colors"
                />
              </div>

              <div>
                <label
                  htmlFor="dept_description"
                  className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1"
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
                  className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-colors resize-none"
                />
              </div>

              <div className="flex justify-end space-x-3 pt-3 border-t border-slate-100">
                <button
                  type="button"
                  onClick={() => setIsModalOpen(false)}
                  disabled={modalLoading}
                  className="px-4 py-2 border border-slate-300 rounded-lg text-sm font-medium text-slate-700 hover:bg-slate-50 transition-colors focus:outline-none focus:ring-2 focus:ring-slate-400 disabled:opacity-50"
                >
                  Hủy
                </button>
                <button
                  type="submit"
                  disabled={modalLoading}
                  className="inline-flex items-center px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium disabled:opacity-50 transition-colors shadow-xs focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
                >
                  {modalLoading ? (
                    <>
                      <RefreshCw className="w-4 h-4 mr-1.5 animate-spin" />
                      Đang lưu...
                    </>
                  ) : (
                    'Lưu phòng ban'
                  )}
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

import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import axiosClient from '../api/axiosClient';
import {
  Layers,
  Plus,
  Edit,
  AlertCircle,
  CheckCircle,
  RefreshCw,
  Search,
  X,
  Folder,
  Laptop,
  Monitor,
  Printer,
  Server,
  Armchair,
} from 'lucide-react';

const getCategoryIcon = (name) => {
  const lower = (name || '').toLowerCase();
  if (lower.includes('laptop') || lower.includes('máy tính')) return Laptop;
  if (lower.includes('màn hình') || lower.includes('monitor')) return Monitor;
  if (lower.includes('máy in') || lower.includes('photo') || lower.includes('print')) return Printer;
  if (lower.includes('mạng') || lower.includes('server') || lower.includes('switch') || lower.includes('viễn thông'))
    return Server;
  if (lower.includes('bàn') || lower.includes('ghế') || lower.includes('nội thất')) return Armchair;
  return Folder;
};

const CategoryPage = () => {
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(false);
  const [errorAlert, setErrorAlert] = useState('');
  const [successAlert, setSuccessAlert] = useState('');

  // Search filter
  const [searchTerm, setSearchTerm] = useState('');

  // Modal State (Create or Edit)
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingCategory, setEditingCategory] = useState(null); // null = create mode
  const [modalForm, setModalForm] = useState({
    name: '',
    description: '',
  });
  const [modalLoading, setModalLoading] = useState(false);
  const [modalError, setModalError] = useState('');

  // Accessibility Focus Management
  const openerButtonRef = useRef(null);
  const nameInputRef = useRef(null);
  const modalDialogRef = useRef(null);

  const fetchCategories = useCallback(async () => {
    setLoading(true);
    setErrorAlert('');
    try {
      const res = await axiosClient.get('/api/categories/');
      setCategories(Array.isArray(res.data) ? res.data : res.data.results || []);
    } catch {
      setErrorAlert('Không thể tải danh sách danh mục tài sản. Vui lòng thử lại.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchCategories();
  }, [fetchCategories]);

  // Handle Escape key to close modal
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape' && isModalOpen && !modalLoading) {
        handleCloseModal();
      }
    };

    if (isModalOpen) {
      window.addEventListener('keydown', handleKeyDown);
    }
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isModalOpen, modalLoading]);

  // Focus name input when modal opens
  useEffect(() => {
    if (isModalOpen) {
      nameInputRef.current?.focus();
    }
  }, [isModalOpen]);

  const handleOpenCreateModal = (e) => {
    openerButtonRef.current = e?.currentTarget || null;
    setEditingCategory(null);
    setModalForm({
      name: '',
      description: '',
    });
    setModalError('');
    setIsModalOpen(true);
  };

  const handleOpenEditModal = (cat, e) => {
    openerButtonRef.current = e?.currentTarget || null;
    setEditingCategory(cat);
    setModalForm({
      name: cat.name,
      description: cat.description || '',
    });
    setModalError('');
    setIsModalOpen(true);
  };

  const handleCloseModal = () => {
    setIsModalOpen(false);
    // Return focus to the opener element
    if (openerButtonRef.current && typeof openerButtonRef.current.focus === 'function') {
      setTimeout(() => {
        openerButtonRef.current?.focus();
      }, 0);
    }
  };

  const handleSaveModal = async (e) => {
    e.preventDefault();
    setModalError('');
    setModalLoading(true);

    try {
      if (editingCategory) {
        // Edit mode (PATCH)
        await axiosClient.patch(`/api/categories/${editingCategory.id}/`, {
          name: modalForm.name.trim(),
          description: modalForm.description.trim(),
        });
        setSuccessAlert(`Cập nhật danh mục "${modalForm.name.trim()}" thành công.`);
      } else {
        // Create mode (POST)
        await axiosClient.post('/api/categories/', {
          name: modalForm.name.trim(),
          description: modalForm.description.trim(),
        });
        setSuccessAlert(`Tạo danh mục "${modalForm.name.trim()}" thành công.`);
      }
      handleCloseModal();
      fetchCategories();
    } catch (err) {
      if (err.response?.data) {
        const data = err.response.data;
        const msg =
          typeof data === 'object' ? Object.values(data).flat().join(' ') : String(data);
        setModalError(msg);
      } else {
        setModalError(err.message || 'Lỗi lưu thông tin danh mục.');
      }
    } finally {
      setModalLoading(false);
    }
  };

  // Filtered categories
  const filteredCategories = useMemo(() => {
    if (!searchTerm.trim()) return categories;
    const term = searchTerm.toLowerCase().trim();
    return categories.filter(
      (cat) =>
        (cat.name && cat.name.toLowerCase().includes(term)) ||
        (cat.description && cat.description.toLowerCase().includes(term))
    );
  }, [categories, searchTerm]);

  return (
    <div className="w-full py-6 px-4 sm:px-6 lg:px-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between pb-6 border-b border-slate-200/90 gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2.5">
            <Layers className="h-6 w-6 text-blue-600 shrink-0" />
            Danh mục tài sản
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            Phân loại và định danh nhóm tài sản phục vụ quản lý, cấp phát và bảo trì.
          </p>
        </div>

        <div className="flex items-center space-x-3 self-start sm:self-auto">
          <button
            onClick={fetchCategories}
            className="inline-flex items-center px-3 py-2 border border-slate-300 rounded-lg text-sm font-medium text-slate-700 bg-white hover:bg-slate-50 hover:text-slate-900 transition-colors shadow-xs focus:outline-none focus:ring-2 focus:ring-blue-500"
            title="Làm mới danh sách"
            aria-label="Làm mới"
          >
            <RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? 'animate-spin text-blue-600' : 'text-slate-500'}`} />
            Làm mới
          </button>
          <button
            onClick={handleOpenCreateModal}
            className="inline-flex items-center px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium transition-colors shadow-xs focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
          >
            <Plus className="h-4 w-4 mr-1.5" />
            Thêm danh mục
          </button>
        </div>
      </div>

      {/* Alerts */}
      {errorAlert && (
        <div
          role="alert"
          className="my-4 p-4 bg-rose-50 border-l-4 border-rose-500 rounded-r-lg flex items-start space-x-3 text-rose-800 text-sm"
        >
          <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5 text-rose-500" />
          <div className="flex-1">{errorAlert}</div>
          <button
            onClick={() => setErrorAlert('')}
            className="text-rose-500 hover:text-rose-700 font-bold ml-2 focus:outline-none"
            aria-label="Đóng thông báo lỗi"
          >
            ×
          </button>
        </div>
      )}

      {successAlert && (
        <div
          role="status"
          className="my-4 p-4 bg-emerald-50 border-l-4 border-emerald-500 rounded-r-lg flex items-center space-x-3 text-emerald-800 text-sm"
        >
          <CheckCircle className="w-5 h-5 flex-shrink-0 text-emerald-500" />
          <div className="flex-1">{successAlert}</div>
          <button
            onClick={() => setSuccessAlert('')}
            className="text-emerald-500 hover:text-emerald-700 font-bold ml-2 focus:outline-none"
            aria-label="Đóng thông báo thành công"
          >
            ×
          </button>
        </div>
      )}

      {/* Toolbar: Search & Count */}
      <div className="mt-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 bg-white p-3.5 rounded-xl border border-slate-200/90 shadow-xs">
        <div className="relative flex-1 max-w-md">
          <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-slate-400">
            <Search size={16} />
          </div>
          <input
            type="text"
            placeholder="Tìm theo tên hoặc mô tả danh mục..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full pl-9 pr-8 py-2 border border-slate-200 rounded-lg text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-slate-50/50 hover:bg-white transition-colors"
          />
          {searchTerm && (
            <button
              onClick={() => setSearchTerm('')}
              className="absolute inset-y-0 right-0 pr-2.5 flex items-center text-slate-400 hover:text-slate-600"
              title="Xóa tìm kiếm"
              aria-label="Xóa tìm kiếm"
            >
              <X size={14} />
            </button>
          )}
        </div>

        <div className="text-xs text-slate-500 font-medium sm:pr-2">
          {searchTerm ? (
            <span>
              Tìm thấy <strong className="text-slate-800">{filteredCategories.length}</strong> / {categories.length} danh mục
            </span>
          ) : (
            <span>
              Tổng số: <strong className="text-slate-800">{categories.length}</strong> danh mục
            </span>
          )}
        </div>
      </div>

      {/* Table */}
      <div className="mt-4 bg-white border border-slate-200/90 rounded-xl shadow-xs overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
            <thead className="bg-slate-50 text-slate-600 font-semibold">
              <tr>
                <th className="px-5 py-3 w-1/3 min-w-[220px] whitespace-nowrap">Tên danh mục</th>
                <th className="px-5 py-3">Mô tả</th>
                <th className="px-5 py-3 w-32 min-w-[120px] text-right whitespace-nowrap">Thao tác</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {loading && categories.length === 0 ? (
                <tr>
                  <td colSpan={3} className="px-6 py-12 text-center text-slate-400">
                    <RefreshCw className="h-6 w-6 animate-spin mx-auto text-blue-600 mb-2" />
                    Đang tải danh sách danh mục...
                  </td>
                </tr>
              ) : categories.length === 0 ? (
                <tr>
                  <td colSpan={3} className="px-6 py-12 text-center text-slate-500">
                    <Folder className="h-8 w-8 text-slate-300 mx-auto mb-2" />
                    <p className="font-medium text-slate-700">Chưa có danh mục nào</p>
                    <p className="text-xs text-slate-400 mt-1">
                      Hãy tạo danh mục đầu tiên để bắt đầu phân loại tài sản.
                    </p>
                    <button
                      onClick={handleOpenCreateModal}
                      className="mt-3 inline-flex items-center px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-medium transition-colors shadow-xs"
                    >
                      <Plus className="h-3.5 w-3.5 mr-1" />
                      Tạo danh mục mới
                    </button>
                  </td>
                </tr>
              ) : filteredCategories.length === 0 ? (
                <tr>
                  <td colSpan={3} className="px-6 py-10 text-center text-slate-500">
                    <Search className="h-8 w-8 text-slate-300 mx-auto mb-2" />
                    <p className="font-medium text-slate-700">Không tìm thấy kết quả phù hợp</p>
                    <p className="text-xs text-slate-400 mt-1">
                      Không có danh mục nào chứa từ khóa "{searchTerm}".
                    </p>
                    <button
                      onClick={() => setSearchTerm('')}
                      className="mt-3 text-xs text-blue-600 hover:text-blue-800 font-medium underline"
                    >
                      Xóa bộ lọc tìm kiếm
                    </button>
                  </td>
                </tr>
              ) : (
                filteredCategories.map((cat) => {
                  const CatIcon = getCategoryIcon(cat.name);
                  return (
                    <tr
                      key={cat.id}
                      className="group hover:bg-slate-50/80 transition-colors"
                    >
                      <td className="px-5 py-3.5 whitespace-nowrap">
                        <div className="flex items-center space-x-3">
                          <div className="w-8 h-8 rounded-lg bg-slate-100 group-hover:bg-blue-50 text-slate-600 group-hover:text-blue-600 flex items-center justify-center shrink-0 transition-colors">
                            <CatIcon className="w-4 h-4" />
                          </div>
                          <span className="font-semibold text-slate-900 group-hover:text-blue-600 transition-colors">
                            {cat.name}
                          </span>
                        </div>
                      </td>
                      <td className="px-5 py-3.5 text-slate-600">
                        {cat.description ? (
                          <span
                            className="block max-w-xl text-slate-600 line-clamp-2"
                            title={cat.description}
                          >
                            {cat.description}
                          </span>
                        ) : (
                          <span className="italic text-slate-400 text-xs">Không có mô tả</span>
                        )}
                      </td>
                      <td className="px-5 py-3.5 whitespace-nowrap text-right">
                        <button
                          onClick={(e) => handleOpenEditModal(cat, e)}
                          className="inline-flex items-center px-3 py-1.5 text-xs font-medium text-slate-700 bg-white hover:text-blue-700 hover:bg-blue-50/70 border border-slate-200/90 rounded-lg shadow-xs transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500"
                          title={`Chỉnh sửa danh mục ${cat.name}`}
                          aria-label={`Sửa danh mục ${cat.name}`}
                        >
                          <Edit className="w-3.5 h-3.5 mr-1 text-slate-500 group-hover:text-blue-600" />
                          Sửa
                        </button>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Modal: Create / Edit Category (STRICT: NO DELETE BUTTON) */}
      {isModalOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="category-modal-title"
          ref={modalDialogRef}
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/40 backdrop-blur-xs transition-opacity animate-in fade-in duration-150"
        >
          <div className="bg-white rounded-2xl shadow-xl max-w-md w-full p-6 border border-slate-200/90 transform transition-all">
            {/* Modal Header */}
            <div className="flex items-start justify-between pb-3 border-b border-slate-100">
              <div>
                <h3
                  id="category-modal-title"
                  className="text-lg font-bold text-slate-900"
                >
                  {editingCategory ? 'Chỉnh sửa danh mục' : 'Thêm danh mục mới'}
                </h3>
                <p className="text-xs text-slate-500 mt-0.5">
                  {editingCategory
                    ? `Cập nhật thông tin phân loại cho ${editingCategory.name}`
                    : 'Thêm danh mục phân loại mới vào hệ thống quản lý.'}
                </p>
              </div>
              <button
                type="button"
                onClick={handleCloseModal}
                disabled={modalLoading}
                className="text-slate-400 hover:text-slate-700 p-1 rounded-lg hover:bg-slate-100 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500"
                aria-label="Đóng"
                title="Đóng (Esc)"
              >
                <X size={18} />
              </button>
            </div>

            {/* Error in Modal */}
            {modalError && (
              <div
                role="alert"
                className="mt-4 p-3 bg-rose-50 border-l-4 border-rose-500 rounded-r-lg text-xs text-rose-700 flex items-start space-x-2"
              >
                <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5 text-rose-500" />
                <div className="flex-1">{modalError}</div>
              </div>
            )}

            {/* Form */}
            <form onSubmit={handleSaveModal} className="mt-4 space-y-4">
              <div>
                <label
                  htmlFor="cat_name"
                  className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1"
                >
                  Tên danh mục <span className="text-rose-500">*</span>
                </label>
                <input
                  id="cat_name"
                  ref={nameInputRef}
                  type="text"
                  required
                  autoFocus
                  disabled={modalLoading}
                  value={modalForm.name}
                  onChange={(e) =>
                    setModalForm((prev) => ({ ...prev, name: e.target.value }))
                  }
                  placeholder="Ví dụ: Máy tính xách tay, Màn hình, Thiết bị mạng..."
                  className="w-full px-3.5 py-2 border border-slate-300 rounded-lg text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-slate-50 disabled:text-slate-400 transition-colors"
                />
              </div>

              <div>
                <div className="flex items-center justify-between mb-1">
                  <label
                    htmlFor="cat_description"
                    className="block text-xs font-semibold text-slate-700 uppercase tracking-wider"
                  >
                    Mô tả
                  </label>
                  <span className="text-[11px] text-slate-400">Không bắt buộc</span>
                </div>
                <textarea
                  id="cat_description"
                  rows={3}
                  disabled={modalLoading}
                  value={modalForm.description}
                  onChange={(e) =>
                    setModalForm((prev) => ({ ...prev, description: e.target.value }))
                  }
                  placeholder="Mô tả phạm vi áp dụng, tiêu chí hoặc các loại tài sản thuộc danh mục này..."
                  className="w-full px-3.5 py-2 border border-slate-300 rounded-lg text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-slate-50 disabled:text-slate-400 transition-colors resize-none"
                />
              </div>

              {/* Actions */}
              <div className="flex items-center justify-end space-x-3 pt-4 border-t border-slate-100">
                <button
                  type="button"
                  onClick={handleCloseModal}
                  disabled={modalLoading}
                  className="px-4 py-2 border border-slate-300 rounded-lg text-sm font-medium text-slate-700 bg-white hover:bg-slate-50 hover:text-slate-900 transition-colors focus:outline-none focus:ring-2 focus:ring-slate-400 disabled:opacity-50"
                >
                  Hủy
                </button>
                <button
                  type="submit"
                  disabled={modalLoading || !modalForm.name.trim()}
                  className="inline-flex items-center px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium transition-colors shadow-xs focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50"
                >
                  {modalLoading ? (
                    <>
                      <RefreshCw className="w-4 h-4 mr-1.5 animate-spin" />
                      Đang lưu...
                    </>
                  ) : (
                    'Lưu danh mục'
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

export default CategoryPage;

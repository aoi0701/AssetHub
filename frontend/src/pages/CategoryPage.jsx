import React, { useState, useEffect, useCallback } from 'react';
import axiosClient from '../api/axiosClient';
import { Layers, Plus, Edit, AlertCircle, CheckCircle, RefreshCw } from 'lucide-react';

const CategoryPage = () => {
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(false);
  const [errorAlert, setErrorAlert] = useState('');
  const [successAlert, setSuccessAlert] = useState('');

  // Modal State (Create or Edit)
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingCategory, setEditingCategory] = useState(null); // null means create mode
  const [modalForm, setModalForm] = useState({
    name: '',
    description: '',
  });
  const [modalLoading, setModalLoading] = useState(false);
  const [modalError, setModalError] = useState('');

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

  const handleOpenCreateModal = () => {
    setEditingCategory(null);
    setModalForm({
      name: '',
      description: '',
    });
    setModalError('');
    setIsModalOpen(true);
  };

  const handleOpenEditModal = (cat) => {
    setEditingCategory(cat);
    setModalForm({
      name: cat.name,
      description: cat.description || '',
    });
    setModalError('');
    setIsModalOpen(true);
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
        setSuccessAlert(`Cập nhật danh mục ${modalForm.name} thành công.`);
      } else {
        // Create mode (POST)
        await axiosClient.post('/api/categories/', {
          name: modalForm.name.trim(),
          description: modalForm.description.trim(),
        });
        setSuccessAlert(`Tạo danh mục ${modalForm.name} thành công.`);
      }
      setIsModalOpen(false);
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

  return (
    <div className="w-full py-6 px-4 sm:px-6 lg:px-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between pb-6 border-b border-gray-200 gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <Layers className="h-7 w-7 text-blue-600" />
            Quản lý danh mục tài sản
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Phân loại tài sản doanh nghiệp (Laptop, Máy in, Màn hình, Bàn ghế, v.v.)
          </p>
        </div>

        <div className="flex items-center space-x-3">
          <button
            onClick={fetchCategories}
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
            Thêm danh mục
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
                <th className="px-6 py-3">Tên danh mục</th>
                <th className="px-6 py-3">Mô tả</th>
                <th className="px-6 py-3 text-right">Thao tác</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 bg-white">
              {categories.length === 0 ? (
                <tr>
                  <td colSpan={3} className="px-6 py-12 text-center text-gray-500">
                    {loading ? 'Đang tải dữ liệu...' : 'Chưa có danh mục nào.'}
                  </td>
                </tr>
              ) : (
                categories.map((cat) => (
                  <tr key={cat.id} className="hover:bg-gray-50/75 transition-colors">
                    <td className="px-6 py-4 whitespace-nowrap font-medium text-gray-900">
                      {cat.name}
                    </td>
                    <td className="px-6 py-4 text-gray-600 max-w-md truncate">
                      {cat.description || <span className="italic text-gray-400">Không có mô tả</span>}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-right">
                      <button
                        onClick={() => handleOpenEditModal(cat)}
                        className="inline-flex items-center px-2.5 py-1 text-xs font-medium text-blue-700 bg-blue-50 hover:bg-blue-100 rounded border border-blue-200 transition-colors"
                        title="Chỉnh sửa danh mục"
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
      </div>

      {/* Modal: Create / Edit Category (STRICT: NO DELETE BUTTON) */}
      {isModalOpen && (
        <div
          role="dialog"
          aria-modal="true"
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm"
        >
          <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-6 border border-gray-100">
            <h3 className="text-lg font-bold text-gray-900 mb-1">
              {editingCategory ? 'Chỉnh sửa danh mục' : 'Tạo danh mục mới'}
            </h3>
            <p className="text-xs text-gray-500 mb-4">
              {editingCategory
                ? `Cập nhật thông tin danh mục ${editingCategory.name}`
                : 'Thêm danh mục phân loại tài sản mới'}
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
                  htmlFor="cat_name"
                  className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
                >
                  Tên danh mục <span className="text-red-500">*</span>
                </label>
                <input
                  id="cat_name"
                  type="text"
                  required
                  value={modalForm.name}
                  onChange={(e) =>
                    setModalForm((prev) => ({ ...prev, name: e.target.value }))
                  }
                  placeholder="vd: Thiết bị văn phòng, Máy tính xách tay..."
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <div>
                <label
                  htmlFor="cat_description"
                  className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
                >
                  Mô tả
                </label>
                <textarea
                  id="cat_description"
                  rows={3}
                  value={modalForm.description}
                  onChange={(e) =>
                    setModalForm((prev) => ({ ...prev, description: e.target.value }))
                  }
                  placeholder="Mô tả danh mục và các loại tài sản thuộc danh mục này..."
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
                  {modalLoading ? 'Đang lưu...' : 'Lưu danh mục'}
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

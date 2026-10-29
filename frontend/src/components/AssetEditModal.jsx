import React, { useState, useEffect } from 'react';
import axiosClient from '../api/axiosClient';
import { X, AlertCircle } from 'lucide-react';
import AssetStatusBadge from './AssetStatusBadge';

const AssetEditModal = ({ isOpen, onClose, onSuccess, asset, categories = [] }) => {
  const [name, setName] = useState('');
  const [categoryId, setCategoryId] = useState('');
  const [serialNumber, setSerialNumber] = useState('');
  const [specificationText, setSpecificationText] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (asset) {
      setName(asset.name || '');
      setCategoryId(asset.category_id ? String(asset.category_id) : '');
      setSerialNumber(asset.serial_number || '');
      setSpecificationText(
        asset.specification && typeof asset.specification === 'object'
          ? JSON.stringify(asset.specification, null, 2)
          : String(asset.specification || '')
      );
      setError('');
    }
  }, [asset]);

  if (!isOpen || !asset) return null;

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    if (!name.trim()) {
      setError('Vui lòng nhập tên tài sản.');
      return;
    }

    let parsedSpec = {};
    if (specificationText.trim()) {
      try {
        parsedSpec = JSON.parse(specificationText.trim());
      } catch {
        parsedSpec = { description: specificationText.trim() };
      }
    }

    setLoading(true);
    try {
      // STRICT allowlist: do NOT send asset_code, status, department, assigned_to_user
      const payload = {
        name: name.trim(),
        category_id: parseInt(categoryId, 10),
        serial_number: serialNumber.trim(),
        specification: parsedSpec,
      };

      const res = await axiosClient.patch(`/api/assets/${asset.id}/`, payload);
      if (onSuccess) {
        onSuccess(res.data);
      }
      onClose();
    } catch (err) {
      if (err.response?.status === 409) {
        setError(
          err.response.data.detail ||
            'Tài sản đã thanh lý (DISPOSED), không thể sửa đổi thông tin.'
        );
      } else if (err.response?.status === 400 && err.response?.data) {
        const data = err.response.data;
        if (typeof data === 'object') {
          const messages = Object.entries(data)
            .map(([field, msg]) => `${field}: ${Array.isArray(msg) ? msg.join(', ') : msg}`)
            .join(' | ');
          setError(messages);
        } else {
          setError(String(data));
        }
      } else if (err.response?.data?.detail) {
        setError(err.response.data.detail);
      } else {
        setError('Không thể cập nhật tài sản. Vui lòng thử lại.');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="edit-asset-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm"
    >
      <div className="bg-white rounded-2xl shadow-2xl max-w-lg w-full p-6 border border-gray-100 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between pb-4 border-b border-gray-100">
          <div>
            <h2 id="edit-asset-title" className="text-lg font-bold text-gray-900">
              Chỉnh sửa thông tin tài sản
            </h2>
            <p className="text-xs text-gray-500 font-mono mt-0.5">{asset.asset_code}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100"
            aria-label="Đóng"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {error && (
          <div
            role="alert"
            className="my-4 p-3 bg-red-50 border-l-4 border-red-500 rounded-r-lg flex items-start space-x-2 text-xs text-red-700"
          >
            <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
            <div className="flex-1">{error}</div>
          </div>
        )}

        {/* Read-only system properties */}
        <div className="my-4 p-3 bg-gray-50 rounded-lg border border-gray-200 grid grid-cols-2 gap-2 text-xs">
          <div>
            <span className="text-gray-500">Mã tài sản:</span>{' '}
            <span className="font-semibold text-gray-800">{asset.asset_code}</span>
          </div>
          <div>
            <span className="text-gray-500">Trạng thái:</span>{' '}
            <AssetStatusBadge status={asset.status} />
          </div>
          <div>
            <span className="text-gray-500">Phòng ban:</span>{' '}
            <span className="font-medium text-gray-800">
              {asset.department_name || 'Kho công ty (Chưa gán)'}
            </span>
          </div>
          <div>
            <span className="text-gray-500">Người giữ:</span>{' '}
            <span className="font-medium text-gray-800">
              {asset.assigned_to_user_name || 'Không có'}
            </span>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label
              htmlFor="edit-asset-name"
              className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
            >
              Tên tài sản <span className="text-red-500">*</span>
            </label>
            <input
              id="edit-asset-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              required
            />
          </div>

          <div>
            <label
              htmlFor="edit-asset-category"
              className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
            >
              Danh mục <span className="text-red-500">*</span>
            </label>
            <select
              id="edit-asset-category"
              value={categoryId}
              onChange={(e) => setCategoryId(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              required
            >
              <option value="">-- Chọn danh mục --</option>
              {categories.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label
              htmlFor="edit-asset-serial"
              className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
            >
              Số serial
            </label>
            <input
              id="edit-asset-serial"
              type="text"
              value={serialNumber}
              onChange={(e) => setSerialNumber(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          <div>
            <label
              htmlFor="edit-asset-spec"
              className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
            >
              Thông số kỹ thuật
            </label>
            <textarea
              id="edit-asset-spec"
              rows={3}
              value={specificationText}
              onChange={(e) => setSpecificationText(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          <div className="flex justify-end space-x-3 pt-4 border-t border-gray-100">
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              className="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              Hủy
            </button>
            <button
              type="submit"
              disabled={loading}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium disabled:opacity-50 transition-colors"
            >
              {loading ? 'Đang lưu...' : 'Lưu thay đổi'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default AssetEditModal;

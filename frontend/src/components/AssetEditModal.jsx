import React, { useState, useEffect } from 'react';
import axiosClient from '../api/axiosClient';
import { X, AlertCircle, RefreshCw } from 'lucide-react';
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

  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape' && isOpen && !loading) {
        onClose();
      }
    };
    if (isOpen) {
      window.addEventListener('keydown', handleKeyDown);
    }
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, loading, onClose]);

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
      onClick={(e) => {
        if (e.target === e.currentTarget && !loading) onClose();
      }}
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/40 backdrop-blur-xs animate-in fade-in duration-150"
    >
      <div className="bg-white rounded-2xl shadow-xl max-w-lg w-full p-6 border border-slate-200/90 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between pb-4 border-b border-slate-100">
          <div>
            <h2 id="edit-asset-title" className="text-lg font-bold text-slate-900">
              Chỉnh sửa thông tin tài sản
            </h2>
            <p className="text-xs text-slate-500 font-mono mt-0.5">{asset.asset_code}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={loading}
            className="p-1.5 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500"
            aria-label="Đóng"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {error && (
          <div
            role="alert"
            className="my-4 p-3 bg-rose-50 border-l-4 border-rose-500 rounded-r-lg flex items-start space-x-2 text-xs text-rose-700"
          >
            <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5 text-rose-500" />
            <div className="flex-1">{error}</div>
          </div>
        )}

        {/* Read-only system properties */}
        <div className="my-4 p-3 bg-slate-50 rounded-lg border border-slate-200/90 grid grid-cols-2 gap-2 text-xs">
          <div>
            <span className="text-slate-500">Mã tài sản:</span>{' '}
            <span className="font-semibold text-slate-800 font-mono">{asset.asset_code}</span>
          </div>
          <div>
            <span className="text-slate-500">Trạng thái:</span>{' '}
            <AssetStatusBadge status={asset.status} />
          </div>
          <div>
            <span className="text-slate-500">Phòng ban:</span>{' '}
            <span className="font-medium text-slate-800">
              {asset.department_name || 'Kho công ty (Chưa gán)'}
            </span>
          </div>
          <div>
            <span className="text-slate-500">Người giữ:</span>{' '}
            <span className="font-medium text-slate-800">
              {asset.assigned_to_user_name || 'Không có'}
            </span>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label
              htmlFor="edit-asset-name"
              className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1"
            >
              Tên tài sản <span className="text-rose-500">*</span>
            </label>
            <input
              id="edit-asset-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-colors"
              required
            />
          </div>

          <div>
            <label
              htmlFor="edit-asset-category"
              className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1"
            >
              Danh mục <span className="text-rose-500">*</span>
            </label>
            <select
              id="edit-asset-category"
              value={categoryId}
              onChange={(e) => setCategoryId(e.target.value)}
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-colors"
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
              className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1"
            >
              Số serial
            </label>
            <input
              id="edit-asset-serial"
              type="text"
              value={serialNumber}
              onChange={(e) => setSerialNumber(e.target.value)}
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-colors font-mono"
            />
          </div>

          <div>
            <label
              htmlFor="edit-asset-spec"
              className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1"
            >
              Thông số kỹ thuật
            </label>
            <textarea
              id="edit-asset-spec"
              rows={3}
              value={specificationText}
              onChange={(e) => setSpecificationText(e.target.value)}
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm font-mono text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-colors resize-none"
            />
          </div>

          <div className="flex justify-end space-x-3 pt-4 border-t border-slate-100">
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              className="px-4 py-2 border border-slate-300 rounded-lg text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 transition-colors focus:outline-none focus:ring-2 focus:ring-slate-400"
            >
              Hủy
            </button>
            <button
              type="submit"
              disabled={loading}
              className="inline-flex items-center px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium disabled:opacity-50 transition-colors shadow-xs focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
            >
              {loading ? (
                <>
                  <RefreshCw className="w-4 h-4 mr-1.5 animate-spin" />
                  Đang lưu...
                </>
              ) : (
                'Lưu thay đổi'
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default AssetEditModal;

import React, { useState, useEffect } from 'react';
import axiosClient from '../api/axiosClient';
import { X, AlertCircle } from 'lucide-react';

const CreateRequestModal = ({
  isOpen,
  onClose,
  onSuccess,
  initialType = 'ALLOCATE',
  initialAssetId = '',
  categories = [],
  heldAssets = [],
}) => {
  const [requestType, setRequestType] = useState(initialType);
  const [requestedCategoryId, setRequestedCategoryId] = useState('');
  const [assetId, setAssetId] = useState(initialAssetId ? String(initialAssetId) : '');
  const [reason, setReason] = useState('');

  const [availableCategories, setAvailableCategories] = useState(categories);
  const [userAssets, setUserAssets] = useState(heldAssets);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (isOpen) {
      setRequestType(initialType);
      setAssetId(initialAssetId ? String(initialAssetId) : '');
      setRequestedCategoryId('');
      setReason('');
      setError('');

      if (categories.length === 0) {
        axiosClient
          .get('/api/categories/', { params: { all: 'true' } })
          .then((res) => {
            const list = Array.isArray(res.data) ? res.data : res.data.results || [];
            setAvailableCategories(list);
          })
          .catch(() => {});
      } else {
        setAvailableCategories(categories);
      }

      axiosClient
        .get('/api/assets/', { params: { all: 'true' } })
        .then((res) => {
          const list = Array.isArray(res.data) ? res.data : res.data.results || [];
          setUserAssets(list);
        })
        .catch(() => {
          setUserAssets(heldAssets);
        });
    }
  }, [isOpen, initialType, initialAssetId, categories, heldAssets]);

  if (!isOpen) return null;

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    if (!reason.trim()) {
      setError('Vui lòng nhập lý do gửi yêu cầu.');
      return;
    }

    const payload = {
      request_type: requestType,
      reason: reason.trim(),
    };

    if (requestType === 'ALLOCATE') {
      if (!requestedCategoryId) {
        setError('Vui lòng chọn loại danh mục tài sản cần cấp phát.');
        return;
      }
      payload.requested_category_id = parseInt(requestedCategoryId, 10);
    } else {
      if (!assetId) {
        setError('Vui lòng chọn tài sản áp dụng.');
        return;
      }
      payload.asset_id = parseInt(assetId, 10);
    }

    setLoading(true);
    try {
      const res = await axiosClient.post('/api/requests/', payload);
      if (onSuccess) {
        onSuccess(res.data);
      }
      onClose();
    } catch (err) {
      if (err.response?.status === 409) {
        setError(
          err.response.data?.detail ||
            'Bạn đã có một yêu cầu cho tài sản này đang chờ duyệt. Vui lòng chờ quản lý xử lý trước khi tạo mới.'
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
        setError('Không thể tạo yêu cầu. Vui lòng thử lại.');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="create-request-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm"
    >
      <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-6 border border-gray-100 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between pb-3 border-b border-gray-100">
          <h2 id="create-request-title" className="text-lg font-bold text-gray-900">
            Tạo yêu cầu tài sản mới
          </h2>
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

        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
          <div>
            <label
              htmlFor="request-type"
              className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
            >
              Loại yêu cầu <span className="text-red-500">*</span>
            </label>
            <select
              id="request-type"
              value={requestType}
              onChange={(e) => {
                setRequestType(e.target.value);
                setError('');
              }}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="ALLOCATE">Cấp phát tài sản mới</option>
              <option value="RETURN">Trả lại tài sản đang dùng</option>
              <option value="DAMAGE_REPORT">Báo hỏng tài sản</option>
            </select>
          </div>

          {requestType === 'ALLOCATE' ? (
            <div>
              <label
                htmlFor="requested-category"
                className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
              >
                Danh mục cần cấp <span className="text-red-500">*</span>
              </label>
              <select
                id="requested-category"
                value={requestedCategoryId}
                onChange={(e) => setRequestedCategoryId(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                required
              >
                <option value="">-- Chọn danh mục tài sản --</option>
                {availableCategories.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <div>
              <label
                htmlFor="request-asset"
                className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
              >
                Tài sản đang giữ <span className="text-red-500">*</span>
              </label>
              <select
                id="request-asset"
                value={assetId}
                onChange={(e) => setAssetId(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                required
              >
                <option value="">-- Chọn tài sản đang giữ --</option>
                {userAssets.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} ({a.asset_code})
                  </option>
                ))}
              </select>
              {userAssets.length === 0 && (
                <p className="text-xs text-amber-600 mt-1">
                  Bạn hiện không có tài sản nào đang được bàn giao.
                </p>
              )}
            </div>
          )}

          <div>
            <label
              htmlFor="request-reason"
              className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
            >
              Lý do chi tiết <span className="text-red-500">*</span>
            </label>
            <textarea
              id="request-reason"
              rows={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="VD: Cần máy tính mới cho dự án / Máy bị nứt màn hình..."
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              required
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
              {loading ? 'Đang gửi...' : 'Gửi yêu cầu'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default CreateRequestModal;

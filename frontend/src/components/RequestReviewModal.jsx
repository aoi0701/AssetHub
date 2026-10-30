import React, { useState, useEffect } from 'react';
import axiosClient from '../api/axiosClient';
import { X, AlertCircle, CheckCircle2, XCircle } from 'lucide-react';

const RequestReviewModal = ({ isOpen, onClose, onSuccess, requestItem }) => {
  const [action, setAction] = useState('APPROVE'); // 'APPROVE' or 'REJECT'
  const [selectedAssetId, setSelectedAssetId] = useState('');
  const [receivedConfirmed, setReceivedConfirmed] = useState(false);
  const [condition, setCondition] = useState('GOOD');
  const [rejectionReason, setRejectionReason] = useState('');

  const [availableAssets, setAvailableAssets] = useState([]);
  const [loadingAssets, setLoadingAssets] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (isOpen && requestItem) {
      setAction('APPROVE');
      setSelectedAssetId('');
      setReceivedConfirmed(false);
      setCondition('GOOD');
      setRejectionReason('');
      setError('');

      if (requestItem.request_type === 'ALLOCATE') {
        setLoadingAssets(true);
        const params = {
          status: 'AVAILABLE',
          all: 'true',
        };
        if (requestItem.requested_category_id) {
          params.category_id = requestItem.requested_category_id;
        }
        if (requestItem.department_id) {
          params.department_id = requestItem.department_id;
        }

        axiosClient
          .get('/api/assets/', { params })
          .then((res) => {
            const list = Array.isArray(res.data) ? res.data : res.data.results || [];
            setAvailableAssets(list);
          })
          .catch(() => {
            setAvailableAssets([]);
          })
          .finally(() => {
            setLoadingAssets(false);
          });
      }
    }
  }, [isOpen, requestItem]);

  if (!isOpen || !requestItem) return null;

  const handleApprove = async () => {
    setError('');
    const payload = {};

    if (requestItem.request_type === 'ALLOCATE') {
      if (!selectedAssetId) {
        setError('Vui lòng chọn tài sản có sẵn trong kho để cấp phát.');
        return;
      }
      payload.selected_asset_id = parseInt(selectedAssetId, 10);
    } else if (requestItem.request_type === 'RETURN') {
      if (!receivedConfirmed) {
        setError('Bắt buộc phải tích xác nhận đã nhận lại tài sản trước khi duyệt hoàn trả.');
        return;
      }
      payload.received_confirmed = true;
      payload.condition = condition;
    }

    setLoading(true);
    try {
      const res = await axiosClient.post(`/api/requests/${requestItem.id}/approve/`, payload);
      if (onSuccess) onSuccess(res.data);
      onClose();
    } catch (err) {
      if (err.response?.status === 409) {
        setError(
          err.response.data?.detail ||
            'Xung đột trạng thái! Phiếu yêu cầu hoặc tài sản đã được xử lý bởi người khác.'
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
        setError('Không thể phê duyệt phiếu. Vui lòng thử lại.');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleReject = async () => {
    setError('');
    if (!rejectionReason.trim()) {
      setError('Vui lòng nhập lý do từ chối phiếu yêu cầu.');
      return;
    }

    setLoading(true);
    try {
      const res = await axiosClient.post(`/api/requests/${requestItem.id}/reject/`, {
        rejection_reason: rejectionReason.trim(),
      });
      if (onSuccess) onSuccess(res.data);
      onClose();
    } catch (err) {
      if (err.response?.status === 409) {
        setError(
          err.response.data?.detail ||
            'Xung đột trạng thái! Phiếu yêu cầu đã được xử lý bởi người khác.'
        );
      } else if (err.response?.data?.detail) {
        setError(err.response.data.detail);
      } else {
        setError('Không thể từ chối phiếu. Vui lòng thử lại.');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    if (action === 'APPROVE') {
      handleApprove();
    } else {
      handleReject();
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="review-request-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm"
    >
      <div className="bg-white rounded-2xl shadow-2xl max-w-lg w-full p-6 border border-gray-100 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between pb-3 border-b border-gray-100">
          <div>
            <h2 id="review-request-title" className="text-lg font-bold text-gray-900">
              Xét duyệt phiếu yêu cầu #{requestItem.request_code}
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Người yêu cầu: <span className="font-semibold text-gray-700">{requestItem.requester_name || requestItem.requester_username}</span> ({requestItem.department_name})
            </p>
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

        {/* Request Summary */}
        <div className="my-4 p-3 bg-gray-50 rounded-lg border border-gray-200 text-xs space-y-1">
          <div className="flex justify-between">
            <span className="text-gray-500">Loại yêu cầu:</span>
            <span className="font-semibold text-gray-800">
              {requestItem.request_type === 'ALLOCATE'
                ? 'Cấp phát mới'
                : requestItem.request_type === 'RETURN'
                ? 'Thu hồi / Trả lại'
                : 'Báo hỏng tài sản'}
            </span>
          </div>
          {requestItem.requested_category_name && (
            <div className="flex justify-between">
              <span className="text-gray-500">Danh mục yêu cầu:</span>
              <span className="font-semibold text-gray-800">{requestItem.requested_category_name}</span>
            </div>
          )}
          {requestItem.asset_name && (
            <div className="flex justify-between">
              <span className="text-gray-500">Tài sản liên quan:</span>
              <span className="font-semibold text-gray-800">
                {requestItem.asset_name} ({requestItem.asset_code})
              </span>
            </div>
          )}
          <div className="pt-1 text-gray-600 border-t border-gray-200">
            <span className="text-gray-500 font-medium">Lý do của nhân viên:</span>
            <p className="mt-0.5 text-gray-800 italic">"{requestItem.reason}"</p>
          </div>
        </div>

        {/* Action Toggle (Approve vs Reject) */}
        <div className="flex rounded-lg bg-gray-100 p-1 mb-4 text-xs font-semibold">
          <button
            type="button"
            onClick={() => {
              setAction('APPROVE');
              setError('');
            }}
            className={`flex-1 py-1.5 rounded-md flex items-center justify-center transition-colors ${
              action === 'APPROVE'
                ? 'bg-white text-emerald-700 shadow-sm'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            <CheckCircle2 className="w-4 h-4 mr-1.5 text-emerald-600" />
            Phê duyệt
          </button>
          <button
            type="button"
            onClick={() => {
              setAction('REJECT');
              setError('');
            }}
            className={`flex-1 py-1.5 rounded-md flex items-center justify-center transition-colors ${
              action === 'REJECT'
                ? 'bg-white text-rose-700 shadow-sm'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            <XCircle className="w-4 h-4 mr-1.5 text-rose-600" />
            Từ chối
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {action === 'APPROVE' ? (
            <>
              {requestItem.request_type === 'ALLOCATE' && (
                <div>
                  <label
                    htmlFor="review-select-asset"
                    className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
                  >
                    Chọn tài sản có sẵn trong kho để bàn giao <span className="text-red-500">*</span>
                  </label>
                  <select
                    id="review-select-asset"
                    value={selectedAssetId}
                    onChange={(e) => setSelectedAssetId(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    required
                  >
                    <option value="">-- Chọn tài sản khả dụng --</option>
                    {availableAssets.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name} ({a.asset_code}) {a.serial_number ? `- SN: ${a.serial_number}` : ''}
                      </option>
                    ))}
                  </select>
                  {loadingAssets && (
                    <p className="text-xs text-gray-400 mt-1">Đang tìm tài sản phù hợp trong kho...</p>
                  )}
                  {!loadingAssets && availableAssets.length === 0 && (
                    <p className="text-xs text-amber-600 mt-1">
                      Không có tài sản nào thuộc danh mục này đang sẵn sàng trong kho.
                    </p>
                  )}
                </div>
              )}

              {requestItem.request_type === 'RETURN' && (
                <>
                  <div>
                    <label
                      htmlFor="review-condition"
                      className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
                    >
                      Tình trạng tài sản khi thu hồi <span className="text-red-500">*</span>
                    </label>
                    <select
                      id="review-condition"
                      value={condition}
                      onChange={(e) => setCondition(e.target.value)}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                      required
                    >
                      <option value="GOOD">Tốt / Bình thường (Nhập lại kho)</option>
                      <option value="DAMAGED">Hỏng hóc (Chuyển sang chờ sửa chữa)</option>
                    </select>
                  </div>

                  <div className="flex items-center space-x-2 pt-1">
                    <input
                      id="review-received-confirmed"
                      type="checkbox"
                      checked={receivedConfirmed}
                      onChange={(e) => setReceivedConfirmed(e.target.checked)}
                      className="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded"
                      required
                    />
                    <label
                      htmlFor="review-received-confirmed"
                      className="text-xs text-gray-700 font-medium"
                    >
                      Xác nhận đã thực tế tiếp nhận lại tài sản từ nhân viên <span className="text-red-500">*</span>
                    </label>
                  </div>
                </>
              )}

              {requestItem.request_type === 'DAMAGE_REPORT' && (
                <div className="p-3 bg-amber-50 rounded-lg border border-amber-200 text-xs text-amber-800">
                  Phê duyệt phiếu báo hỏng sẽ chuyển trạng thái tài sản thành{' '}
                  <span className="font-semibold">DAMAGED_PENDING_REPAIR</span> để sẵn sàng gửi bảo hành / sửa chữa.
                </div>
              )}
            </>
          ) : (
            <div>
              <label
                htmlFor="review-rejection-reason"
                className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
              >
                Lý do từ chối yêu cầu <span className="text-red-500">*</span>
              </label>
              <textarea
                id="review-rejection-reason"
                rows={3}
                value={rejectionReason}
                onChange={(e) => setRejectionReason(e.target.value)}
                placeholder="Nhập lý do không phê duyệt phiếu yêu cầu..."
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-rose-500"
                required
              />
            </div>
          )}

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
              className={`px-4 py-2 text-white rounded-lg text-sm font-medium disabled:opacity-50 transition-colors ${
                action === 'APPROVE'
                  ? 'bg-emerald-600 hover:bg-emerald-700'
                  : 'bg-rose-600 hover:bg-rose-700'
              }`}
            >
              {loading
                ? 'Đang xử lý...'
                : action === 'APPROVE'
                ? 'Xác nhận duyệt'
                : 'Xác nhận từ chối'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default RequestReviewModal;

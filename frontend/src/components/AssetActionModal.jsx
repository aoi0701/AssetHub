import React, { useState, useEffect } from 'react';
import axiosClient from '../api/axiosClient';
import { X, AlertCircle } from 'lucide-react';

const ACTION_CONFIG = {
  allocate: {
    title: 'Cấp phát tài sản',
    endpoint: (id) => `/api/assets/${id}/allocate/`,
    submitLabel: 'Cấp phát',
  },
  revoke: {
    title: 'Thu hồi tài sản',
    endpoint: (id) => `/api/assets/${id}/revoke/`,
    submitLabel: 'Thu hồi',
  },
  mark_damaged: {
    title: 'Báo hỏng tài sản',
    endpoint: (id) => `/api/assets/${id}/mark-damaged/`,
    submitLabel: 'Báo hỏng',
  },
  send_maintenance: {
    title: 'Gửi bảo dưỡng / sửa chữa',
    endpoint: (id) => `/api/assets/${id}/send-maintenance/`,
    submitLabel: 'Gửi bảo dưỡng',
  },
  complete_maintenance: {
    title: 'Hoàn tất bảo dưỡng',
    endpoint: (id) => `/api/assets/${id}/complete-maintenance/`,
    submitLabel: 'Hoàn tất',
  },
  transfer: {
    title: 'Điều chuyển phòng ban',
    endpoint: (id) => `/api/assets/${id}/transfer/`,
    submitLabel: 'Điều chuyển',
  },
  propose_disposal: {
    title: 'Đề xuất thanh lý',
    endpoint: (id) => `/api/assets/${id}/propose-disposal/`,
    submitLabel: 'Đề xuất thanh lý',
  },
  confirm_disposal: {
    title: 'Xác nhận thanh lý',
    endpoint: (id) => `/api/assets/${id}/confirm-disposal/`,
    submitLabel: 'Xác nhận thanh lý',
  },
};

const AssetActionModal = ({
  isOpen,
  onClose,
  onSuccess,
  asset,
  actionType,
  userRole = 'ADMIN',
  departments = [],
  users = [],
}) => {
  const [reason, setReason] = useState('');
  const [assignedToUserId, setAssignedToUserId] = useState('');
  const [receivedConfirmed, setReceivedConfirmed] = useState(false);
  const [condition, setCondition] = useState('GOOD');
  const [resultStatus, setResultStatus] = useState('AVAILABLE');
  const [targetDepartmentId, setTargetDepartmentId] = useState('');
  const [providerName, setProviderName] = useState('');

  const [departmentUsers, setDepartmentUsers] = useState(users);
  const [allDepartments, setAllDepartments] = useState(departments);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (isOpen) {
      setReason('');
      setAssignedToUserId('');
      setReceivedConfirmed(false);
      setCondition('GOOD');
      setResultStatus('AVAILABLE');
      setTargetDepartmentId('');
      setProviderName('');
      setError('');

      // Fetch users in department if allocating and none provided
      if (actionType === 'allocate' && users.length === 0 && asset?.department_id) {
        axiosClient
          .get('/api/users/', { params: { department_id: asset.department_id, is_active: true, all: 'true' } })
          .then((res) => {
            const list = Array.isArray(res.data) ? res.data : res.data.results || [];
            setDepartmentUsers(list);
          })
          .catch(() => {});
      } else {
        setDepartmentUsers(users);
      }

      // Fetch departments if transfer and none provided
      if (actionType === 'transfer' && departments.length === 0) {
        axiosClient
          .get('/api/departments/', { params: { all: 'true' } })
          .then((res) => {
            const list = Array.isArray(res.data) ? res.data : res.data.results || [];
            setAllDepartments(list);
          })
          .catch(() => {});
      } else {
        setAllDepartments(departments);
      }
    }
  }, [isOpen, actionType, asset, users, departments]);

  if (!isOpen || !asset || !actionType) return null;

  const config = ACTION_CONFIG[actionType] || {
    title: 'Thực hiện thao tác',
    endpoint: (id) => `/api/assets/${id}/`,
    submitLabel: 'Xác nhận',
  };

  // Role validation: Manager cannot transfer or confirm_disposal
  const isManagerForbidden =
    userRole === 'MANAGER' && (actionType === 'transfer' || actionType === 'confirm_disposal');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    if (isManagerForbidden) {
      setError('Quản lý không có quyền thực hiện thao tác này.');
      return;
    }

    if (!reason.trim()) {
      setError('Vui lòng nhập lý do thực hiện.');
      return;
    }

    const payload = {
      reason: reason.trim(),
    };

    if (actionType === 'allocate') {
      if (!assignedToUserId) {
        setError('Vui lòng chọn nhân viên được cấp phát.');
        return;
      }
      payload.assigned_to_user_id = parseInt(assignedToUserId, 10);
    } else if (actionType === 'revoke') {
      if (!receivedConfirmed) {
        setError('Bắt buộc phải tích xác nhận đã thu hồi tài sản thực tế.');
        return;
      }
      payload.received_confirmed = true;
      payload.condition = condition;
    } else if (actionType === 'send_maintenance') {
      if (providerName.trim()) {
        payload.provider_name = providerName.trim();
        payload.reason = `${reason.trim()} (Đơn vị bảo dưỡng: ${providerName.trim()})`;
      }
    } else if (actionType === 'complete_maintenance') {
      payload.result = resultStatus === 'IN_USE' ? 'RETURN_TO_USER' : 'RETURN_TO_STOCK';
      payload.result_status = resultStatus;
    } else if (actionType === 'transfer') {
      const targetId = targetDepartmentId ? parseInt(targetDepartmentId, 10) : null;
      payload.to_department_id = targetId;
      payload.target_department_id = targetId;
    } else if (actionType === 'propose_disposal') {
      if (asset.status === 'IN_USE' && !receivedConfirmed) {
        setError('Tài sản đang sử dụng, vui lòng xác nhận đã thu hồi trước khi đề xuất thanh lý.');
        return;
      }
      payload.received_confirmed = receivedConfirmed;
    }

    setLoading(true);
    try {
      const endpoint = config.endpoint(asset.id);
      const res = await axiosClient.post(endpoint, payload);
      if (onSuccess) {
        onSuccess(res.data);
      }
      onClose();
    } catch (err) {
      if (err.response?.status === 409) {
        const detail =
          err.response.data?.detail ||
          'Xung đột trạng thái tài sản! Dữ liệu tài sản đã thay đổi, vui lòng tải lại trang để xem trạng thái mới nhất.';
        setError(detail);
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
        setError('Thao tác không thành công. Vui lòng thử lại.');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="asset-action-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm"
    >
      <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-6 border border-gray-100 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between pb-3 border-b border-gray-100">
          <div>
            <h2 id="asset-action-title" className="text-lg font-bold text-gray-900">
              {config.title}
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Tài sản: <span className="font-semibold text-gray-700">{asset.name}</span> ({asset.asset_code})
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

        {isManagerForbidden ? (
          <div className="py-6 text-center text-sm text-gray-500">
            Chức năng này chỉ dành riêng cho Quản trị viên hệ thống (Admin).
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="mt-4 space-y-4">
            {/* Conditional input: allocate */}
            {actionType === 'allocate' && (
              <div>
                <label
                  htmlFor="action-assigned-user"
                  className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
                >
                  Nhân viên nhận bàn giao <span className="text-red-500">*</span>
                </label>
                <select
                  id="action-assigned-user"
                  value={assignedToUserId}
                  onChange={(e) => setAssignedToUserId(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  required
                >
                  <option value="">-- Chọn nhân viên trong phòng ban --</option>
                  {departmentUsers.map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.full_name || u.username} ({u.username})
                    </option>
                  ))}
                </select>
              </div>
            )}

            {/* Conditional input: revoke */}
            {actionType === 'revoke' && (
              <>
                <div>
                  <label
                    htmlFor="action-revoke-condition"
                    className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
                  >
                    Tình trạng tài sản khi nhận lại <span className="text-red-500">*</span>
                  </label>
                  <select
                    id="action-revoke-condition"
                    value={condition}
                    onChange={(e) => setCondition(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    required
                  >
                    <option value="GOOD">Bình thường / Tốt (Sẵn sàng cấp phát)</option>
                    <option value="DAMAGED">Hỏng hóc (Cần bảo dưỡng sửa chữa)</option>
                  </select>
                </div>

                <div className="flex items-center space-x-2 pt-1">
                  <input
                    id="action-revoke-confirmed"
                    type="checkbox"
                    checked={receivedConfirmed}
                    onChange={(e) => setReceivedConfirmed(e.target.checked)}
                    className="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded"
                    required
                  />
                  <label
                    htmlFor="action-revoke-confirmed"
                    className="text-xs text-gray-700 font-medium"
                  >
                    Xác nhận đã thực tế thu hồi tài sản từ nhân viên <span className="text-red-500">*</span>
                  </label>
                </div>
              </>
            )}

            {/* Conditional input: send_maintenance */}
            {actionType === 'send_maintenance' && (
              <div>
                <label
                  htmlFor="action-provider-name"
                  className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
                >
                  Đơn vị sửa chữa / bảo hành (tùy chọn)
                </label>
                <input
                  id="action-provider-name"
                  type="text"
                  value={providerName}
                  onChange={(e) => setProviderName(e.target.value)}
                  placeholder="VD: Trung tâm bảo hành Dell / Thợ kỹ thuật ngoài"
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
            )}

            {/* Conditional input: complete_maintenance */}
            {actionType === 'complete_maintenance' && (
              <div>
                <label
                  htmlFor="action-complete-result"
                  className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
                >
                  Kết quả sau bảo dưỡng <span className="text-red-500">*</span>
                </label>
                <select
                  id="action-complete-result"
                  value={resultStatus}
                  onChange={(e) => setResultStatus(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  required
                >
                  <option value="AVAILABLE">Nhập lại kho phòng ban (Sẵn sàng sử dụng)</option>
                  <option value="IN_USE">Bàn giao lại cho người giữ trước đó</option>
                </select>
              </div>
            )}

            {/* Conditional input: transfer */}
            {actionType === 'transfer' && (
              <div>
                <label
                  htmlFor="action-target-department"
                  className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
                >
                  Phòng ban tiếp nhận
                </label>
                <select
                  id="action-target-department"
                  value={targetDepartmentId}
                  onChange={(e) => setTargetDepartmentId(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="">-- Kho công ty chung (Chưa phân về phòng ban nào) --</option>
                  {allDepartments.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name} ({d.code})
                    </option>
                  ))}
                </select>
              </div>
            )}

            {/* Conditional input: propose_disposal */}
            {actionType === 'propose_disposal' && asset.status === 'IN_USE' && (
              <div className="flex items-center space-x-2 pt-1">
                <input
                  id="action-disposal-confirmed"
                  type="checkbox"
                  checked={receivedConfirmed}
                  onChange={(e) => setReceivedConfirmed(e.target.checked)}
                  className="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded"
                  required
                />
                <label
                  htmlFor="action-disposal-confirmed"
                  className="text-xs text-gray-700 font-medium"
                >
                  Xác nhận đã thu hồi tài sản từ người giữ trước khi thanh lý <span className="text-red-500">*</span>
                </label>
              </div>
            )}

            {/* Common input: reason */}
            <div>
              <label
                htmlFor="action-reason"
                className="block text-xs font-semibold text-gray-700 uppercase tracking-wider mb-1"
              >
                Lý do / Ghi chú <span className="text-red-500">*</span>
              </label>
              <textarea
                id="action-reason"
                rows={3}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Nhập lý do chi tiết cho thao tác này..."
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
                {loading ? 'Đang xử lý...' : config.submitLabel}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
};

export default AssetActionModal;

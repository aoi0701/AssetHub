import React, { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import axiosClient from '../api/axiosClient';
import { useAuth } from '../context/AuthContext';
import AssetStatusBadge from '../components/AssetStatusBadge';
import AssetEditModal from '../components/AssetEditModal';
import AssetActionModal from '../components/AssetActionModal';
import AssetAttachments from '../components/AssetAttachments';
import {
  Package,
  ArrowLeft,
  Edit,
  History,
  AlertCircle,
  Clock,
  User,
  Building,
  CheckCircle,
} from 'lucide-react';

const AssetDetailPage = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const isAdmin = user?.role === 'ADMIN';
  const isManager = user?.role === 'MANAGER';

  const [asset, setAsset] = useState(null);
  const [historyLogs, setHistoryLogs] = useState([]);
  const [categories, setCategories] = useState([]);
  const [departments, setDepartments] = useState([]);
  const [departmentUsers, setDepartmentUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [errorAlert, setErrorAlert] = useState('');
  const [successAlert, setSuccessAlert] = useState('');

  // Modals
  const [isEditOpen, setIsEditOpen] = useState(false);
  const [activeAction, setActiveAction] = useState(null); // 'allocate', 'revoke', etc.

  const fetchAssetData = useCallback(async () => {
    setLoading(true);
    setErrorAlert('');
    try {
      const res = await axiosClient.get(`/api/assets/${id}/`);
      setAsset(res.data);

      // Fetch history if Admin or Manager
      if (isAdmin || isManager) {
        try {
          const histRes = await axiosClient.get(`/api/assets/${id}/history/`);
          setHistoryLogs(Array.isArray(histRes.data) ? histRes.data : histRes.data.results || []);
        } catch {
          // If 403 or other, leave empty
          setHistoryLogs([]);
        }
      }

      // Fetch users for department if asset has department
      if (res.data.department_id) {
        try {
          const usersRes = await axiosClient.get('/api/users/', {
            params: { department_id: res.data.department_id, is_active: true },
          });
          setDepartmentUsers(
            Array.isArray(usersRes.data) ? usersRes.data : usersRes.data.results || []
          );
        } catch {
          // ignore
        }
      }
    } catch (err) {
      if (err.response?.status === 404) {
        setErrorAlert('Không tìm thấy tài sản hoặc bạn không có quyền xem tài sản này.');
      } else {
        setErrorAlert('Lỗi tải thông tin tài sản. Vui lòng thử lại.');
      }
    } finally {
      setLoading(false);
    }
  }, [id, isAdmin, isManager]);

  useEffect(() => {
    fetchAssetData();
  }, [fetchAssetData]);

  useEffect(() => {
    axiosClient.get('/api/categories/').then((res) => {
      setCategories(Array.isArray(res.data) ? res.data : res.data.results || []);
    }).catch(() => {});

    if (isAdmin) {
      axiosClient.get('/api/departments/').then((res) => {
        setDepartments(Array.isArray(res.data) ? res.data : res.data.results || []);
      }).catch(() => {});
    }
  }, [isAdmin]);

  if (loading) {
    return (
      <div className="max-w-7xl mx-auto py-12 px-4 sm:px-6 lg:px-8 text-center text-gray-500">
        Đang tải thông tin tài sản...
      </div>
    );
  }

  if (!asset && errorAlert) {
    return (
      <div className="max-w-7xl mx-auto py-8 px-4 sm:px-6 lg:px-8">
        <div role="alert" className="p-4 bg-red-50 border-l-4 border-red-500 rounded-r-lg text-red-700 text-sm mb-4">
          {errorAlert}
        </div>
        <Link to="/assets" className="inline-flex items-center text-blue-600 hover:text-blue-800 text-sm font-medium">
          <ArrowLeft className="w-4 h-4 mr-1" /> Quay lại danh sách tài sản
        </Link>
      </div>
    );
  }

  if (!asset) return null;

  const status = asset.status;

  return (
    <div className="max-w-7xl mx-auto py-8 px-4 sm:px-6 lg:px-8">
      {/* Top Breadcrumb & Actions */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between pb-6 border-b border-gray-200 gap-4">
        <div>
          <button
            onClick={() => navigate('/assets')}
            className="inline-flex items-center text-sm font-medium text-gray-500 hover:text-gray-700 mb-2 transition-colors"
          >
            <ArrowLeft className="w-4 h-4 mr-1" /> Quay lại danh mục tài sản
          </button>
          <div className="flex items-center space-x-3">
            <h1 className="text-2xl font-bold text-gray-900">{asset.name}</h1>
            <AssetStatusBadge status={asset.status} />
          </div>
          <p className="text-xs text-gray-500 font-mono mt-1">Mã tài sản: {asset.asset_code}</p>
        </div>

        {/* Action Buttons based on status & role */}
        <div className="flex flex-wrap items-center gap-2 self-start sm:self-auto">
          {/* Admin Edit Metadata (Forbidden when DISPOSED) */}
          {isAdmin && status !== 'DISPOSED' && (
            <button
              onClick={() => setIsEditOpen(true)}
              className="inline-flex items-center px-3 py-1.5 border border-gray-300 rounded-lg text-xs font-medium text-gray-700 bg-white hover:bg-gray-50 transition-colors shadow-sm"
            >
              <Edit className="w-3.5 h-3.5 mr-1 text-gray-500" />
              Sửa thông tin
            </button>
          )}

          {/* Workflow buttons depending on Status */}
          {status === 'AVAILABLE' && (
            <>
              <button
                onClick={() => setActiveAction('allocate')}
                className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-medium transition-colors shadow-sm"
              >
                Cấp phát
              </button>
              <button
                onClick={() => setActiveAction('mark_damaged')}
                className="px-3 py-1.5 bg-amber-600 hover:bg-amber-700 text-white rounded-lg text-xs font-medium transition-colors shadow-sm"
              >
                Báo hỏng
              </button>
              {isAdmin && (
                <button
                  onClick={() => setActiveAction('transfer')}
                  className="px-3 py-1.5 bg-purple-600 hover:bg-purple-700 text-white rounded-lg text-xs font-medium transition-colors shadow-sm"
                >
                  Điều chuyển
                </button>
              )}
              <button
                onClick={() => setActiveAction('propose_disposal')}
                className="px-3 py-1.5 bg-rose-600 hover:bg-rose-700 text-white rounded-lg text-xs font-medium transition-colors shadow-sm"
              >
                Đề xuất thanh lý
              </button>
            </>
          )}

          {status === 'IN_USE' && (
            <>
              <button
                onClick={() => setActiveAction('revoke')}
                className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-medium transition-colors shadow-sm"
              >
                Thu hồi tài sản
              </button>
              <button
                onClick={() => setActiveAction('propose_disposal')}
                className="px-3 py-1.5 bg-rose-600 hover:bg-rose-700 text-white rounded-lg text-xs font-medium transition-colors shadow-sm"
              >
                Đề xuất thanh lý
              </button>
            </>
          )}

          {status === 'DAMAGED_PENDING_REPAIR' && (
            <>
              <button
                onClick={() => setActiveAction('send_maintenance')}
                className="px-3 py-1.5 bg-yellow-600 hover:bg-yellow-700 text-white rounded-lg text-xs font-medium transition-colors shadow-sm"
              >
                Gửi bảo dưỡng
              </button>
              <button
                onClick={() => setActiveAction('propose_disposal')}
                className="px-3 py-1.5 bg-rose-600 hover:bg-rose-700 text-white rounded-lg text-xs font-medium transition-colors shadow-sm"
              >
                Đề xuất thanh lý
              </button>
            </>
          )}

          {status === 'UNDER_MAINTENANCE' && (
            <button
              onClick={() => setActiveAction('complete_maintenance')}
              className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-xs font-medium transition-colors shadow-sm"
            >
              Hoàn tất bảo dưỡng
            </button>
          )}

          {status === 'PENDING_DISPOSAL' && isAdmin && (
            <button
              onClick={() => setActiveAction('confirm_disposal')}
              className="px-3 py-1.5 bg-gray-900 hover:bg-black text-white rounded-lg text-xs font-medium transition-colors shadow-sm"
            >
              Xác nhận thanh lý
            </button>
          )}
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

      {/* Details Grid */}
      <div className="mt-6 grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Column: Properties Card */}
        <div className="lg:col-span-1 space-y-6">
          <div className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm">
            <h2 className="text-base font-bold text-gray-900 mb-4 pb-2 border-b border-gray-100">
              Thông tin chi tiết
            </h2>

            <dl className="space-y-3 text-sm">
              <div>
                <dt className="text-xs text-gray-400 font-medium">Danh mục</dt>
                <dd className="font-semibold text-gray-800">{asset.category_name || '-'}</dd>
              </div>

              <div>
                <dt className="text-xs text-gray-400 font-medium">Số Serial</dt>
                <dd className="font-mono text-gray-800">{asset.serial_number || 'Chưa cập nhật'}</dd>
              </div>

              <div>
                <dt className="text-xs text-gray-400 font-medium">Phòng ban quản lý</dt>
                <dd className="text-gray-800 flex items-center mt-0.5">
                  <Building className="w-4 h-4 mr-1 text-gray-400" />
                  {asset.department_name ? (
                    <span>
                      {asset.department_name} ({asset.department_code})
                    </span>
                  ) : (
                    <span className="italic text-gray-400">Kho công ty (Chưa gán)</span>
                  )}
                </dd>
              </div>

              <div>
                <dt className="text-xs text-gray-400 font-medium">Người chịu trách nhiệm / sử dụng</dt>
                <dd className="text-gray-800 flex items-center mt-0.5">
                  <User className="w-4 h-4 mr-1 text-gray-400" />
                  {asset.assigned_to_user_name ? (
                    <span>
                      {asset.assigned_to_user_name} (@{asset.assigned_to_user_username})
                    </span>
                  ) : (
                    <span className="italic text-gray-400">Chưa bàn giao</span>
                  )}
                </dd>
              </div>

              <div>
                <dt className="text-xs text-gray-400 font-medium">Thời gian khởi tạo</dt>
                <dd className="text-gray-600 flex items-center mt-0.5 text-xs">
                  <Clock className="w-3.5 h-3.5 mr-1 text-gray-400" />
                  {asset.created_at ? new Date(asset.created_at).toLocaleString('vi-VN') : '-'}
                </dd>
              </div>
            </dl>
          </div>

          {/* Specifications Card */}
          <div className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm">
            <h2 className="text-base font-bold text-gray-900 mb-4 pb-2 border-b border-gray-100">
              Thông số kỹ thuật
            </h2>
            {asset.specification && Object.keys(asset.specification).length > 0 ? (
              typeof asset.specification === 'object' ? (
                <dl className="space-y-2 text-xs">
                  {Object.entries(asset.specification).map(([k, v]) => (
                    <div key={k} className="flex justify-between py-1 border-b border-gray-50">
                      <dt className="font-semibold text-gray-600">{k}:</dt>
                      <dd className="text-gray-800 font-mono">{String(v)}</dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="text-xs text-gray-700 whitespace-pre-wrap">{String(asset.specification)}</p>
              )
            ) : (
              <p className="text-xs text-gray-400 italic">Chưa có thông số kỹ thuật.</p>
            )}
          </div>
        </div>

        {/* Right Column: Lifecycle History Log */}
        <div className="lg:col-span-2">
          <div className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm">
            <div className="flex items-center justify-between pb-3 border-b border-gray-100 mb-4">
              <h2 className="text-base font-bold text-gray-900 flex items-center gap-2">
                <History className="w-5 h-5 text-blue-600" />
                Lịch sử vòng đời tài sản
              </h2>
              <span className="text-xs text-gray-400">{historyLogs.length} bản ghi</span>
            </div>

            {historyLogs.length === 0 ? (
              <div className="py-12 text-center text-sm text-gray-400">
                Chưa có nhật ký hoạt động nào cho tài sản này.
              </div>
            ) : (
              <div className="flow-root">
                <ul className="-mb-8">
                  {historyLogs.map((log, logIdx) => (
                    <li key={log.id || logIdx}>
                      <div className="relative pb-8">
                        {logIdx !== historyLogs.length - 1 ? (
                          <span
                            className="absolute top-4 left-4 -ml-px h-full w-0.5 bg-gray-200"
                            aria-hidden="true"
                          />
                        ) : null}
                        <div className="relative flex space-x-3 items-start">
                          <span className="h-8 w-8 rounded-full bg-blue-50 border border-blue-200 flex items-center justify-center text-blue-600 text-xs font-bold">
                            {logIdx + 1}
                          </span>
                          <div className="min-w-0 flex-1 bg-gray-50/70 p-3 rounded-lg border border-gray-100">
                            <div className="flex items-center justify-between">
                              <span className="text-xs font-bold text-gray-900">
                                {log.action_type}
                              </span>
                              <span className="text-[11px] text-gray-400">
                                {log.created_at ? new Date(log.created_at).toLocaleString('vi-VN') : ''}
                              </span>
                            </div>

                            <p className="text-xs text-gray-700 mt-1">
                              {log.reason || 'Không có lý do kèm theo'}
                            </p>

                            <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-gray-500">
                              {log.actor_name && (
                                <span>
                                  Bởi: <strong>{log.actor_name}</strong>
                                </span>
                              )}
                              {log.from_status && log.to_status && (
                                <span className="font-mono">
                                  ({log.from_status} → {log.to_status})
                                </span>
                              )}
                              {log.to_user_name && (
                                <span>Bàn giao cho: <strong>{log.to_user_name}</strong></span>
                              )}
                              {log.to_department_name && (
                                <span>Phòng ban: <strong>{log.to_department_name}</strong></span>
                              )}
                            </div>
                          </div>
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Attachments Section */}
      <div className="mt-6">
        <AssetAttachments asset={asset} onUploadSuccess={fetchAssetData} />
      </div>

      {/* Edit Modal (Admin only) */}
      {isAdmin && isEditOpen && (
        <AssetEditModal
          isOpen={isEditOpen}
          onClose={() => setIsEditOpen(false)}
          onSuccess={(updated) => {
            setAsset(updated);
            setSuccessAlert('Cập nhật tài sản thành công.');
            fetchAssetData();
          }}
          asset={asset}
          categories={categories}
        />
      )}

      {/* Action Modal */}
      {activeAction && (
        <AssetActionModal
          isOpen={!!activeAction}
          onClose={() => setActiveAction(null)}
          onSuccess={(updated) => {
            setAsset(updated);
            setSuccessAlert('Thực hiện thao tác thành công.');
            fetchAssetData();
          }}
          asset={asset}
          actionType={activeAction}
          userRole={user?.role}
          departments={departments}
          users={departmentUsers}
        />
      )}
    </div>
  );
};

export default AssetDetailPage;

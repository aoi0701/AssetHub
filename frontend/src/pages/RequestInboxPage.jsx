import React, { useState, useEffect } from 'react';
import axiosClient from '../api/axiosClient';
import { useAuth } from '../context/AuthContext';
import RequestReviewModal from '../components/RequestReviewModal';
import CreateRequestModal from '../components/CreateRequestModal';
import Pagination from '../components/Pagination';
import {
  FileText,
  Plus,
  RefreshCw,
  AlertCircle,
  CheckCircle,
  Eye,
} from 'lucide-react';

const RequestInboxPage = () => {
  const { user } = useAuth();
  const isStaff = user?.role === 'ADMIN' || user?.role === 'MANAGER';
  const isUser = user?.role === 'USER';

  const [requests, setRequests] = useState([]);
  const [loading, setLoading] = useState(false);
  const [errorAlert, setErrorAlert] = useState('');
  const [successAlert, setSuccessAlert] = useState('');

  // Pagination State
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize] = useState(15);
  const [totalCount, setTotalCount] = useState(0);
  const [totalPages, setTotalPages] = useState(1);

  // Filters
  const [statusFilter, setStatusFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');

  // Refresh trigger
  const [refreshKey, setRefreshKey] = useState(0);
  const refreshRequests = () => setRefreshKey((prev) => prev + 1);

  // Modals
  const [reviewingRequest, setReviewingRequest] = useState(null);
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [viewingDetail, setViewingDetail] = useState(null);

  // Race condition guard & fetch requests
  useEffect(() => {
    let active = true;

    const loadRequests = async () => {
      setLoading(true);
      setErrorAlert('');
      try {
        const params = {
          page: currentPage,
          page_size: pageSize,
        };
        if (statusFilter) params.status = statusFilter;
        if (typeFilter) params.request_type = typeFilter;

        const res = await axiosClient.get('/api/requests/', { params });
        if (!active) return;

        if (res.data && Array.isArray(res.data.results)) {
          const count = res.data.count || 0;
          const pages = res.data.total_pages || Math.ceil(count / pageSize) || 1;
          setRequests(res.data.results);
          setTotalCount(count);
          setTotalPages(pages);

          if (currentPage > pages && pages > 0) {
            setCurrentPage(pages);
          }
        } else if (Array.isArray(res.data)) {
          setRequests(res.data);
          setTotalCount(res.data.length);
          setTotalPages(1);
          if (currentPage > 1) {
            setCurrentPage(1);
          }
        } else {
          setRequests([]);
          setTotalCount(0);
          setTotalPages(1);
        }
      } catch {
        if (active) {
          setErrorAlert('Không thể tải danh sách phiếu yêu cầu. Vui lòng thử lại.');
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    };

    loadRequests();

    return () => {
      active = false;
    };
  }, [currentPage, pageSize, statusFilter, typeFilter, refreshKey]);

  // Page clamping guard when totalPages drops
  useEffect(() => {
    if (totalPages > 0 && currentPage > totalPages) {
      setCurrentPage(totalPages);
    }
  }, [currentPage, totalPages]);

  const hasActiveFilters = Boolean(statusFilter || typeFilter);

  const handleClearFilters = () => {
    setStatusFilter('');
    setTypeFilter('');
    setCurrentPage(1);
  };

  const requestTypeLabels = {
    ALLOCATE: 'Cấp phát mới',
    RETURN: 'Hoàn trả tài sản',
    DAMAGE_REPORT: 'Báo hỏng tài sản',
  };

  const statusBadges = {
    PENDING: {
      label: 'Chờ duyệt',
      color: 'bg-amber-50 text-amber-700 border-amber-200',
    },
    APPROVED: {
      label: 'Đã phê duyệt',
      color: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    },
    REJECTED: {
      label: 'Đã từ chối',
      color: 'bg-rose-50 text-rose-700 border-rose-200',
    },
  };

  return (
    <div className="w-full py-6 px-4 sm:px-6 lg:px-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between pb-6 border-b border-slate-200/90 gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2">
            <FileText className="h-7 w-7 text-blue-600" />
            {isStaff ? 'Quản lý yêu cầu tài sản' : 'Yêu cầu của tôi'}
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            {isStaff
              ? 'Xem xét, phê duyệt hoặc từ chối các yêu cầu cấp phát, hoàn trả và báo hỏng.'
              : 'Theo dõi tiến độ và kết quả các phiếu yêu cầu bạn đã gửi.'}
          </p>
        </div>

        <div className="flex items-center space-x-3 self-start sm:self-auto">
          <button
            type="button"
            onClick={refreshRequests}
            className="inline-flex items-center px-3 py-2 border border-slate-300 rounded-lg text-sm font-medium text-slate-700 bg-white hover:bg-slate-50 transition-colors shadow-sm"
          >
            <RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
            Làm mới
          </button>

          {isUser && (
            <button
              type="button"
              onClick={() => setIsCreateOpen(true)}
              className="inline-flex items-center px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium transition-colors shadow-sm"
            >
              <Plus className="h-4 w-4 mr-1.5" />
              Tạo yêu cầu mới
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
            type="button"
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
            type="button"
            onClick={() => setSuccessAlert('')}
            className="text-green-500 hover:text-green-700 font-bold ml-2"
          >
            ×
          </button>
        </div>
      )}

      {/* Filters */}
      <div className="mt-6 bg-white p-4 rounded-xl border border-slate-200/90 shadow-sm grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <div>
          <select
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value);
              setCurrentPage(1);
            }}
            className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 text-slate-700 bg-white"
            aria-label="Lọc theo trạng thái yêu cầu"
          >
            <option value="">Tất cả trạng thái</option>
            <option value="PENDING">Chờ duyệt (PENDING)</option>
            <option value="APPROVED">Đã duyệt (APPROVED)</option>
            <option value="REJECTED">Đã từ chối (REJECTED)</option>
          </select>
        </div>

        <div>
          <select
            value={typeFilter}
            onChange={(e) => {
              setTypeFilter(e.target.value);
              setCurrentPage(1);
            }}
            className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 text-slate-700 bg-white"
            aria-label="Lọc theo loại yêu cầu"
          >
            <option value="">Tất cả loại yêu cầu</option>
            <option value="ALLOCATE">Cấp phát mới (ALLOCATE)</option>
            <option value="RETURN">Thu hồi / Trả lại (RETURN)</option>
            <option value="DAMAGE_REPORT">Báo hỏng (DAMAGE_REPORT)</option>
          </select>
        </div>
      </div>

      {/* Table */}
      <div className="mt-6 bg-white border border-slate-200/90 rounded-xl shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
            <thead className="bg-slate-50 text-slate-600 font-semibold">
              <tr>
                <th className="px-4 py-3 whitespace-nowrap">Mã phiếu</th>
                <th className="px-4 py-3 whitespace-nowrap">Loại yêu cầu</th>
                {isStaff && <th className="px-4 py-3 whitespace-nowrap">Người yêu cầu</th>}
                <th className="px-4 py-3 min-w-[160px]">Mục tiêu yêu cầu</th>
                <th className="px-4 py-3 min-w-[180px]">Lý do</th>
                <th className="px-4 py-3 whitespace-nowrap">Trạng thái</th>
                <th className="sticky right-0 bg-slate-50 px-4 py-3 text-right whitespace-nowrap min-w-[210px] shadow-[-6px_0_10px_-4px_rgba(0,0,0,0.06)] z-10">
                  Thao tác
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {requests.length === 0 ? (
                <tr>
                  <td
                    colSpan={isStaff ? 7 : 6}
                    className="px-4 py-12 text-center text-slate-500"
                  >
                    {loading ? (
                      'Đang tải dữ liệu...'
                    ) : hasActiveFilters ? (
                      <div className="flex flex-col items-center justify-center space-y-2">
                        <span>Không tìm thấy phiếu yêu cầu nào phù hợp.</span>
                        <button
                          type="button"
                          onClick={handleClearFilters}
                          className="text-xs font-medium text-blue-600 hover:text-blue-800 underline focus:outline-none"
                        >
                          Xóa bộ lọc
                        </button>
                      </div>
                    ) : (
                      'Không có phiếu yêu cầu nào.'
                    )}
                  </td>
                </tr>
              ) : (
                requests.map((req) => {
                  const badge = statusBadges[req.status] || {
                    label: req.status,
                    color: 'bg-slate-100 text-slate-700 border-slate-200',
                  };

                  return (
                    <tr key={req.id} className="group hover:bg-slate-50/80 transition-colors">
                      <td className="px-4 py-3.5 whitespace-nowrap font-mono font-semibold text-blue-600">
                        {req.request_code}
                      </td>
                      <td className="px-4 py-3.5 whitespace-nowrap">
                        <span className="font-medium text-slate-900">
                          {requestTypeLabels[req.request_type] || req.request_type}
                        </span>
                      </td>
                      {isStaff && (
                        <td className="px-4 py-3.5">
                          <div
                            className="font-medium text-slate-900 max-w-[170px] truncate"
                            title={req.requester_name || req.requester_username}
                          >
                            {req.requester_name || req.requester_username}
                          </div>
                          <div
                            className="text-xs text-slate-400 max-w-[170px] truncate"
                            title={`${req.department_name} (${req.department_code})`}
                          >
                            {req.department_name} ({req.department_code})
                          </div>
                        </td>
                      )}
                      <td className="px-4 py-3.5">
                        {req.request_type === 'ALLOCATE' ? (
                          <span
                            className="font-medium text-slate-800 block max-w-[190px] truncate"
                            title={req.requested_category_name || '-'}
                          >
                            Danh mục: {req.requested_category_name || '-'}
                          </span>
                        ) : (
                          <div className="max-w-[190px]">
                            <span
                              className="font-medium text-slate-800 block truncate"
                              title={req.asset_name || '-'}
                            >
                              {req.asset_name || '-'}
                            </span>
                            {req.asset_code && (
                              <span className="text-xs text-slate-400 block font-mono">
                                ({req.asset_code})
                              </span>
                            )}
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-3.5">
                        <div className="text-slate-600 max-w-[200px] truncate" title={req.reason}>
                          {req.reason}
                        </div>
                      </td>
                      <td className="px-4 py-3.5 whitespace-nowrap">
                        <span
                          className={`inline-block px-2.5 py-0.5 rounded-full text-xs font-semibold border ${badge.color}`}
                        >
                          {badge.label}
                        </span>
                        {req.status === 'REJECTED' && req.rejection_reason && (
                          <div
                            className="text-xs text-rose-600 mt-1 max-w-[180px] truncate"
                            title={`Lý do: ${req.rejection_reason}`}
                          >
                            Lý do: {req.rejection_reason}
                          </div>
                        )}
                      </td>
                      <td className="sticky right-0 bg-white group-hover:bg-slate-50/90 px-4 py-3.5 whitespace-nowrap text-right min-w-[210px] shadow-[-6px_0_10px_-4px_rgba(0,0,0,0.06)] z-10 transition-colors">
                        <div className="flex items-center justify-end gap-2">
                          {/* Manager/Admin review button if PENDING */}
                          {isStaff && req.status === 'PENDING' && (
                            <button
                              type="button"
                              onClick={() => setReviewingRequest(req)}
                              className="inline-flex items-center px-3 py-1.5 text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg shadow-xs transition-colors shrink-0"
                            >
                              Xét duyệt
                            </button>
                          )}

                          <button
                            type="button"
                            onClick={() => setViewingDetail(req)}
                            className="inline-flex items-center px-2.5 py-1.5 text-xs font-medium text-slate-700 bg-white hover:text-blue-700 hover:bg-blue-50/70 border border-slate-200/90 rounded-lg shadow-xs transition-colors shrink-0"
                            title="Xem chi tiết"
                          >
                            <Eye className="w-3.5 h-3.5 mr-1 text-slate-500" />
                            Chi tiết
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })
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

      {/* Review Modal (Manager / Admin) */}
      {reviewingRequest && (
        <RequestReviewModal
          isOpen={!!reviewingRequest}
          onClose={() => setReviewingRequest(null)}
          onSuccess={() => {
            setSuccessAlert('Xử lý phiếu yêu cầu thành công.');
            refreshRequests();
          }}
          requestItem={reviewingRequest}
        />
      )}

      {/* Create Request Modal (User) */}
      {isCreateOpen && (
        <CreateRequestModal
          isOpen={isCreateOpen}
          onClose={() => setIsCreateOpen(false)}
          onSuccess={() => {
            setSuccessAlert('Tạo phiếu yêu cầu thành công.');
            refreshRequests();
          }}
        />
      )}

      {/* Detail Modal */}
      {viewingDetail && (
        <div
          role="dialog"
          aria-modal="true"
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm"
        >
          <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full p-6 border border-gray-100">
            <h3 className="text-lg font-bold text-gray-900 mb-2">
              Phiếu yêu cầu #{viewingDetail.request_code}
            </h3>

            <div className="space-y-2 text-xs py-2 text-gray-700 border-t border-b border-gray-100">
              <div className="flex justify-between">
                <span className="text-gray-400">Loại:</span>
                <span className="font-semibold">{requestTypeLabels[viewingDetail.request_type]}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-400">Người gửi:</span>
                <span className="font-medium">{viewingDetail.requester_name || viewingDetail.requester_username}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-400">Phòng ban:</span>
                <span>{viewingDetail.department_name}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-400">Trạng thái:</span>
                <span className="font-semibold">{viewingDetail.status}</span>
              </div>
              <div>
                <span className="text-gray-400 block mb-0.5">Lý do:</span>
                <p className="p-2 bg-gray-50 rounded text-gray-800">{viewingDetail.reason}</p>
              </div>

              {viewingDetail.rejection_reason && (
                <div>
                  <span className="text-rose-500 font-semibold block mb-0.5">Lý do từ chối:</span>
                  <p className="p-2 bg-rose-50 rounded text-rose-800">{viewingDetail.rejection_reason}</p>
                </div>
              )}

              {viewingDetail.reviewed_by_name && (
                <div className="flex justify-between pt-1 text-[11px] text-gray-400">
                  <span>Người duyệt: {viewingDetail.reviewed_by_name}</span>
                  <span>{new Date(viewingDetail.reviewed_at).toLocaleDateString('vi-VN')}</span>
                </div>
              )}
            </div>

            <div className="flex justify-end pt-4">
              <button
                type="button"
                onClick={() => setViewingDetail(null)}
                className="px-4 py-2 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg text-sm font-medium"
              >
                Đóng
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default RequestInboxPage;

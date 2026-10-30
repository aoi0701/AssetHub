import React, { useState, useEffect, useCallback, useRef } from 'react';
import axiosClient from '../api/axiosClient';
import Pagination from '../components/Pagination';
import { History, RefreshCw, AlertCircle, Clock } from 'lucide-react';

const PersonalHistoryPage = () => {
  const [historyItems, setHistoryItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [errorAlert, setErrorAlert] = useState('');

  // Pagination State
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize] = useState(20);
  const [totalCount, setTotalCount] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [failedTargetPage, setFailedTargetPage] = useState(null);

  const reqIdRef = useRef(0);
  const lastLoadedPageRef = useRef(1);
  const skipFetchRef = useRef(false);

  const fetchHistory = useCallback(
    async (isCancelled = () => false) => {
      if (skipFetchRef.current) {
        skipFetchRef.current = false;
        return;
      }

      const isAborted = typeof isCancelled === 'function' ? isCancelled : () => false;
      const currentReqId = ++reqIdRef.current;
      const targetPage = currentPage;
      setLoading(true);
      setErrorAlert('');
      setFailedTargetPage(null);

      try {
        const res = await axiosClient.get('/api/me/asset-history/', {
          params: { page: targetPage, page_size: pageSize },
        });
        if (isAborted() || currentReqId !== reqIdRef.current) return;

        if (res.data && Array.isArray(res.data.results)) {
          const count = res.data.count || 0;
          const total = res.data.total_pages || Math.ceil(count / pageSize) || 1;
          setHistoryItems(res.data.results);
          setTotalCount(count);
          setTotalPages(total);
          lastLoadedPageRef.current = targetPage;
          if (targetPage > total && total > 0) {
            setCurrentPage(total);
          }
        } else if (Array.isArray(res.data)) {
          setHistoryItems(res.data);
          setTotalCount(res.data.length);
          setTotalPages(1);
          lastLoadedPageRef.current = 1;
        } else {
          setHistoryItems([]);
          setTotalCount(0);
          setTotalPages(1);
          lastLoadedPageRef.current = 1;
        }
      } catch (err) {
        if (isAborted() || currentReqId !== reqIdRef.current) return;
        if (err.response?.status === 404 && targetPage > 1) {
          // 404 recovery: targetPage is invalid, fall back to targetPage - 1
          setCurrentPage((prev) => (prev > 1 ? prev - 1 : 1));
          return;
        }
        // 500 / Network error:
        // Roll back currentPage to lastLoadedPageRef without triggering a re-fetch.
        // Data and page label remain completely consistent with the loaded page.
        if (targetPage !== lastLoadedPageRef.current) {
          skipFetchRef.current = true;
          setCurrentPage(lastLoadedPageRef.current);
        }
        setFailedTargetPage(targetPage);
        setErrorAlert(
          targetPage > 1
            ? `Không thể tải nhật ký lịch sử tài sản cá nhân (trang ${targetPage}). Vui lòng thử lại.`
            : 'Không thể tải nhật ký lịch sử tài sản cá nhân.'
        );
      } finally {
        if (!isAborted() && currentReqId === reqIdRef.current) {
          setLoading(false);
        }
      }
    },
    [currentPage, pageSize]
  );

  useEffect(() => {
    let active = true;
    fetchHistory(() => !active);
    return () => {
      active = false;
    };
  }, [fetchHistory]);

  const handleRetry = () => {
    if (failedTargetPage) {
      if (failedTargetPage === currentPage) {
        fetchHistory();
      } else {
        setCurrentPage(failedTargetPage);
      }
    } else {
      fetchHistory();
    }
  };

  const actionLabels = {
    ALLOCATE: 'Cấp phát',
    RETURN: 'Hoàn trả',
    DAMAGE: 'Báo hỏng',
    MAINTENANCE_START: 'Bảo trì',
    MAINTENANCE_END: 'Hoàn tất bảo trì',
    TRANSFER: 'Điều chuyển',
    DISPOSAL_PROPOSE: 'Đề xuất thanh lý',
    DISPOSAL_CONFIRM: 'Đã thanh lý',
  };

  return (
    <div className="w-full py-6 px-4 sm:px-6 lg:px-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between pb-6 border-b border-gray-200 gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <History className="h-7 w-7 text-blue-600" />
            Nhật ký tài sản cá nhân
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Ghi nhận toàn bộ các lần tiếp nhận, hoàn trả và xử lý thiết bị của bạn.
          </p>
        </div>

        <button
          onClick={() => fetchHistory()}
          className="inline-flex items-center px-3 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 transition-colors self-start sm:self-auto"
        >
          <RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
          Làm mới
        </button>
      </div>

      {/* Alerts */}
      {errorAlert && (
        <div
          role="alert"
          className="my-4 p-4 bg-red-50 border-l-4 border-red-500 rounded-r-lg flex items-center justify-between text-red-700 text-sm gap-3"
        >
          <div className="flex items-center space-x-3 flex-1 min-w-0">
            <AlertCircle className="w-5 h-5 flex-shrink-0" />
            <span className="truncate sm:overflow-visible sm:whitespace-normal">{errorAlert}</span>
            {failedTargetPage && (
              <button
                type="button"
                onClick={handleRetry}
                className="px-2.5 py-1 text-xs font-semibold bg-red-100 hover:bg-red-200 text-red-800 rounded border border-red-300 transition-colors shrink-0 ml-2"
              >
                Thử lại
              </button>
            )}
          </div>
          <button
            onClick={() => {
              setErrorAlert('');
              setFailedTargetPage(null);
            }}
            className="text-red-500 hover:text-red-700 font-bold ml-2 text-base leading-none"
            aria-label="Đóng thông báo lỗi"
          >
            ×
          </button>
        </div>
      )}

      {/* History Timeline / Table */}
      <div className="mt-6 bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 text-left text-sm">
            <thead className="bg-gray-50 text-gray-700 font-semibold">
              <tr>
                <th className="px-6 py-3">Mã tài sản</th>
                <th className="px-6 py-3">Tên tài sản</th>
                <th className="px-6 py-3">Hành động</th>
                <th className="px-6 py-3">Thông điệp sự kiện</th>
                <th className="px-6 py-3">Thời gian</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 bg-white">
              {historyItems.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-6 py-12 text-center text-gray-500">
                    {loading ? 'Đang tải dữ liệu...' : 'Chưa có nhật ký hoạt động tài sản nào.'}
                  </td>
                </tr>
              ) : (
                historyItems.map((item) => (
                  <tr key={item.id} className="hover:bg-gray-50/75 transition-colors">
                    <td className="px-6 py-4 whitespace-nowrap font-mono font-semibold text-gray-900">
                      {item.asset_code_snapshot || item.asset_code || `AST-${item.asset_id}`}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap font-medium text-gray-900">
                      {item.asset_name_snapshot || item.asset_name || '-'}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span className="inline-block px-2.5 py-0.5 rounded-full text-xs font-semibold bg-blue-50 text-blue-700 border border-blue-200">
                        {actionLabels[item.action_type] || item.action_type}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-gray-700">
                      {item.message || 'Sự kiện tài sản liên quan đến bạn'}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-xs text-gray-500 flex items-center pt-5">
                      <Clock className="w-3.5 h-3.5 mr-1 text-gray-400" />
                      {item.created_at ? new Date(item.created_at).toLocaleString('vi-VN') : '-'}
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
        />
      </div>
    </div>
  );
};

export default PersonalHistoryPage;

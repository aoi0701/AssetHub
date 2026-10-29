import React, { useState, useEffect, useCallback } from 'react';
import axiosClient from '../api/axiosClient';
import { History, RefreshCw, AlertCircle, Clock } from 'lucide-react';

const PersonalHistoryPage = () => {
  const [historyItems, setHistoryItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [errorAlert, setErrorAlert] = useState('');

  const fetchHistory = useCallback(async () => {
    setLoading(true);
    setErrorAlert('');
    try {
      const res = await axiosClient.get('/api/me/asset-history/');
      setHistoryItems(Array.isArray(res.data) ? res.data : res.data.results || []);
    } catch {
      setErrorAlert('Không thể tải nhật ký lịch sử tài sản cá nhân.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchHistory();
  }, [fetchHistory]);

  const actionLabels = {
    ALLOCATE: 'Cấp phát',
    RETURN: 'Hoàn trả',
    REVOKE: 'Thu hồi',
    MARK_DAMAGED: 'Báo hỏng',
    DAMAGE_REPORT: 'Báo hỏng',
    MAINTENANCE_START: 'Bảo trì',
    MAINTENANCE_COMPLETE: 'Hoàn tất bảo trì',
    TRANSFER: 'Điều chuyển',
    DISPOSAL_PROPOSE: 'Đề xuất thanh lý',
    DISPOSAL_CONFIRM: 'Đã thanh lý',
  };

  return (
    <div className="max-w-7xl mx-auto py-8 px-4 sm:px-6 lg:px-8">
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
          onClick={fetchHistory}
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
      </div>
    </div>
  );
};

export default PersonalHistoryPage;

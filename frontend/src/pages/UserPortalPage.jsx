import React, { useState, useEffect, useCallback } from 'react';
import axiosClient from '../api/axiosClient';
import AssetStatusBadge from '../components/AssetStatusBadge';
import CreateRequestModal from '../components/CreateRequestModal';
import Pagination from '../components/Pagination';
import { Package, Plus, AlertCircle, CheckCircle, RefreshCw, AlertTriangle, CornerDownLeft } from 'lucide-react';

const UserPortalPage = () => {
  const [assets, setAssets] = useState([]);
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(false);
  const [errorAlert, setErrorAlert] = useState('');
  const [successAlert, setSuccessAlert] = useState('');

  // Pagination State
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize] = useState(15);
  const [totalCount, setTotalCount] = useState(0);
  const [totalPages, setTotalPages] = useState(1);

  // Create Request Modal
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [modalType, setModalType] = useState('ALLOCATE');
  const [modalAssetId, setModalAssetId] = useState('');

  const fetchAssets = useCallback(async (isCancelled) => {
    const isAborted = typeof isCancelled === 'function' ? isCancelled : () => false;
    setLoading(true);
    setErrorAlert('');
    try {
      const res = await axiosClient.get('/api/assets/', {
        params: { page: currentPage, page_size: pageSize },
      });
      if (isAborted()) return;

      if (res.data && Array.isArray(res.data.results)) {
        setAssets(res.data.results);
        setTotalCount(res.data.count || 0);
        setTotalPages(res.data.total_pages || Math.ceil((res.data.count || 0) / pageSize) || 1);
      } else if (Array.isArray(res.data)) {
        setAssets(res.data);
        setTotalCount(res.data.length);
        setTotalPages(1);
      } else {
        setAssets([]);
        setTotalCount(0);
        setTotalPages(1);
      }
    } catch (err) {
      if (isAborted()) return;
      if (err.response?.status === 404 && currentPage > 1) {
        setCurrentPage((prev) => (prev > 1 ? prev - 1 : 1));
      } else {
        setErrorAlert('Không thể tải danh sách tài sản của bạn. Vui lòng thử lại.');
      }
    } finally {
      if (!isAborted()) {
        setLoading(false);
      }
    }
  }, [currentPage, pageSize]);

  useEffect(() => {
    let active = true;
    fetchAssets(() => !active);
    return () => {
      active = false;
    };
  }, [fetchAssets]);

  useEffect(() => {
    if (totalPages > 0 && currentPage > totalPages) {
      setCurrentPage(totalPages);
    }
  }, [currentPage, totalPages]);

  useEffect(() => {
    axiosClient
      .get('/api/categories/')
      .then((res) => {
        setCategories(Array.isArray(res.data) ? res.data : res.data.results || []);
      })
      .catch(() => {});
  }, []);

  const handleOpenAllocate = () => {
    setModalType('ALLOCATE');
    setModalAssetId('');
    setIsModalOpen(true);
  };

  const handleOpenReturn = (assetId) => {
    setModalType('RETURN');
    setModalAssetId(String(assetId));
    setIsModalOpen(true);
  };

  const handleOpenDamageReport = (assetId) => {
    setModalType('DAMAGE_REPORT');
    setModalAssetId(String(assetId));
    setIsModalOpen(true);
  };

  return (
    <div className="w-full py-6 px-4 sm:px-6 lg:px-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between pb-6 border-b border-gray-200 gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <Package className="h-7 w-7 text-blue-600" />
            Tài sản của tôi
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Danh sách thiết bị và tài sản doanh nghiệp đang được bàn giao cho bạn sử dụng.
          </p>
        </div>

        <div className="flex items-center space-x-3 self-start sm:self-auto">
          <button
            onClick={() => fetchAssets()}
            className="inline-flex items-center px-3 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 transition-colors"
          >
            <RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
            Làm mới
          </button>

          <button
            onClick={handleOpenAllocate}
            className="inline-flex items-center px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium transition-colors shadow-sm"
          >
            <Plus className="h-4 w-4 mr-1.5" />
            Gửi yêu cầu cấp mới
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

      {/* Assets List Table */}
      <div className="mt-6 bg-white border border-slate-200/90 rounded-xl shadow-xs overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
            <thead className="bg-slate-50 text-slate-600 font-semibold">
              <tr>
                <th className="px-5 py-3 whitespace-nowrap">Mã tài sản</th>
                <th className="px-5 py-3 whitespace-nowrap">Tên tài sản</th>
                <th className="px-5 py-3 whitespace-nowrap">Danh mục</th>
                <th className="px-5 py-3 whitespace-nowrap">Số Serial</th>
                <th className="px-5 py-3 whitespace-nowrap">Trạng thái</th>
                <th className="px-5 py-3 text-right whitespace-nowrap min-w-[180px]">Thao tác</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {assets.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-6 py-12 text-center text-slate-500">
                    {loading ? (
                      'Đang tải dữ liệu...'
                    ) : (
                      <div className="space-y-2">
                        <p className="text-slate-600">Bạn hiện chưa được bàn giao tài sản nào.</p>
                        <button
                          onClick={handleOpenAllocate}
                          className="text-blue-600 font-medium hover:underline text-xs inline-flex items-center"
                        >
                          <Plus className="w-3.5 h-3.5 mr-1" />
                          Gửi yêu cầu cấp phát tài sản mới ngay
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ) : (
                assets.map((asset) => (
                  <tr key={asset.id} className="hover:bg-slate-50/80 transition-colors">
                    <td className="px-5 py-3.5 whitespace-nowrap font-mono font-semibold text-blue-600">
                      {asset.asset_code}
                    </td>
                    <td className="px-5 py-3.5 whitespace-nowrap">
                      <div className="font-medium text-slate-900">{asset.name}</div>
                      {asset.department_name && (
                        <div className="text-xs text-slate-400">
                          {asset.department_name} ({asset.department_code})
                        </div>
                      )}
                    </td>
                    <td className="px-5 py-3.5 whitespace-nowrap text-slate-600">
                      {asset.category_name || '-'}
                    </td>
                    <td className="px-5 py-3.5 whitespace-nowrap font-mono text-slate-600 text-xs">
                      {asset.serial_number || 'N/A'}
                    </td>
                    <td className="px-5 py-3.5 whitespace-nowrap">
                      <AssetStatusBadge status={asset.status} />
                    </td>
                    <td className="px-5 py-3.5 whitespace-nowrap text-right">
                      <div className="flex items-center justify-end gap-1.5">
                        <button
                          onClick={() => handleOpenDamageReport(asset.id)}
                          className="inline-flex items-center px-2.5 py-1.5 text-xs font-medium text-amber-700 bg-amber-50 hover:bg-amber-100/80 rounded-lg border border-amber-200 transition-colors shadow-2xs focus:outline-none focus:ring-2 focus:ring-amber-500"
                        >
                          <AlertTriangle className="w-3.5 h-3.5 mr-1 text-amber-600" />
                          Báo hỏng
                        </button>
                        <button
                          onClick={() => handleOpenReturn(asset.id)}
                          className="inline-flex items-center px-2.5 py-1.5 text-xs font-medium text-slate-700 bg-white hover:text-blue-700 hover:bg-blue-50/70 rounded-lg border border-slate-200/90 transition-colors shadow-2xs focus:outline-none focus:ring-2 focus:ring-blue-500"
                        >
                          <CornerDownLeft className="w-3.5 h-3.5 mr-1 text-slate-500" />
                          Trả tài sản
                        </button>
                      </div>
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
          loading={loading}
        />
      </div>

      {/* Create Request Modal */}
      <CreateRequestModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onSuccess={() => {
          setSuccessAlert('Gửi yêu cầu thành công. Vui lòng chờ quản lý phê duyệt.');
          fetchAssets();
        }}
        initialType={modalType}
        initialAssetId={modalAssetId}
        categories={categories}
        heldAssets={assets}
      />
    </div>
  );
};

export default UserPortalPage;

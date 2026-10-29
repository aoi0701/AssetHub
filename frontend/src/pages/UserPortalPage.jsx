import React, { useState, useEffect, useCallback } from 'react';
import axiosClient from '../api/axiosClient';
import AssetStatusBadge from '../components/AssetStatusBadge';
import CreateRequestModal from '../components/CreateRequestModal';
import { Package, Plus, AlertCircle, CheckCircle, RefreshCw, AlertTriangle, CornerDownLeft } from 'lucide-react';

const UserPortalPage = () => {
  const [assets, setAssets] = useState([]);
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(false);
  const [errorAlert, setErrorAlert] = useState('');
  const [successAlert, setSuccessAlert] = useState('');

  // Create Request Modal
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [modalType, setModalType] = useState('ALLOCATE');
  const [modalAssetId, setModalAssetId] = useState('');

  const fetchAssets = useCallback(async () => {
    setLoading(true);
    setErrorAlert('');
    try {
      const res = await axiosClient.get('/api/assets/');
      setAssets(Array.isArray(res.data) ? res.data : res.data.results || []);
    } catch {
      setErrorAlert('Không thể tải danh sách tài sản của bạn. Vui lòng thử lại.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAssets();
    axiosClient.get('/api/categories/').then((res) => {
      setCategories(Array.isArray(res.data) ? res.data : res.data.results || []);
    }).catch(() => {});
  }, [fetchAssets]);

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
            onClick={fetchAssets}
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
      <div className="mt-6 bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 text-left text-sm">
            <thead className="bg-gray-50 text-gray-700 font-semibold">
              <tr>
                <th className="px-6 py-3">Mã tài sản</th>
                <th className="px-6 py-3">Tên tài sản</th>
                <th className="px-6 py-3">Danh mục</th>
                <th className="px-6 py-3">Số Serial</th>
                <th className="px-6 py-3">Trạng thái</th>
                <th className="px-6 py-3 text-right">Thao tác</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 bg-white">
              {assets.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-6 py-12 text-center text-gray-500">
                    {loading ? (
                      'Đang tải dữ liệu...'
                    ) : (
                      <div className="space-y-2">
                        <p>Bạn hiện chưa được bàn giao tài sản nào.</p>
                        <button
                          onClick={handleOpenAllocate}
                          className="text-blue-600 font-medium hover:underline text-xs"
                        >
                          Gửi yêu cầu cấp phát tài sản mới ngay
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ) : (
                assets.map((asset) => (
                  <tr key={asset.id} className="hover:bg-gray-50/80 transition-colors">
                    <td className="px-6 py-4 whitespace-nowrap font-mono font-semibold text-blue-600">
                      {asset.asset_code}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="font-medium text-gray-900">{asset.name}</div>
                      {asset.department_name && (
                        <div className="text-xs text-gray-400">
                          {asset.department_name} ({asset.department_code})
                        </div>
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-gray-700">
                      {asset.category_name || '-'}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap font-mono text-gray-600 text-xs">
                      {asset.serial_number || 'N/A'}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <AssetStatusBadge status={asset.status} />
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-right space-x-2">
                      <button
                        onClick={() => handleOpenDamageReport(asset.id)}
                        className="inline-flex items-center px-2.5 py-1 text-xs font-medium text-amber-700 bg-amber-50 hover:bg-amber-100 rounded border border-amber-200 transition-colors"
                      >
                        <AlertTriangle className="w-3.5 h-3.5 mr-1 text-amber-600" />
                        Báo hỏng
                      </button>
                      <button
                        onClick={() => handleOpenReturn(asset.id)}
                        className="inline-flex items-center px-2.5 py-1 text-xs font-medium text-blue-700 bg-blue-50 hover:bg-blue-100 rounded border border-blue-200 transition-colors"
                      >
                        <CornerDownLeft className="w-3.5 h-3.5 mr-1 text-blue-600" />
                        Trả tài sản
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
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

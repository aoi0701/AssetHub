import React, { useState, useEffect } from 'react';
import axiosClient from '../api/axiosClient';
import { X, AlertCircle, Sparkles, Loader2, RefreshCw } from 'lucide-react';

const AssetCreateModal = ({ isOpen, onClose, onSuccess, categories = [], departments = [] }) => {
  const [name, setName] = useState('');
  const [categoryId, setCategoryId] = useState('');
  const [departmentId, setDepartmentId] = useState('');
  const [serialNumber, setSerialNumber] = useState('');
  const [specificationText, setSpecificationText] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const [aiLoading, setAiLoading] = useState(false);
  const [aiSuggestion, setAiSuggestion] = useState(null);
  const [aiNotice, setAiNotice] = useState('');

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

  if (!isOpen) return null;

  const handleAiSuggest = async () => {
    if (!name.trim()) {
      setError('Vui lòng nhập tên tài sản trước khi yêu cầu gợi ý AI.');
      return;
    }
    setAiLoading(true);
    setAiNotice('');
    setAiSuggestion(null);
    setError('');

    try {
      const res = await axiosClient.post('/api/ai/suggest-category/', {
        name: name.trim(),
        description: specificationText.trim(),
      });
      setAiSuggestion(res.data);
    } catch {
      setAiNotice('Dịch vụ AI tạm thời không khả dụng, vui lòng chọn thủ công.');
    } finally {
      setAiLoading(false);
    }
  };

  const handleApplySuggestion = () => {
    if (aiSuggestion?.suggested_category_id) {
      setCategoryId(String(aiSuggestion.suggested_category_id));
      setAiSuggestion(null);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    if (!name.trim()) {
      setError('Vui lòng nhập tên tài sản.');
      return;
    }
    if (!categoryId) {
      setError('Vui lòng chọn danh mục tài sản.');
      return;
    }

    let parsedSpec = {};
    if (specificationText.trim()) {
      try {
        parsedSpec = JSON.parse(specificationText.trim());
      } catch {
        // If not valid JSON, store as text description
        parsedSpec = { description: specificationText.trim() };
      }
    }

    setLoading(true);
    try {
      const payload = {
        name: name.trim(),
        category_id: parseInt(categoryId, 10),
        serial_number: serialNumber.trim(),
        specification: parsedSpec,
      };

      if (departmentId) {
        payload.department_id = parseInt(departmentId, 10);
      }

      const res = await axiosClient.post('/api/assets/', payload);
      if (onSuccess) {
        onSuccess(res.data);
      }
      onClose();
    } catch (err) {
      if (err.response?.status === 400 && err.response?.data) {
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
        setError('Không thể tạo tài sản mới. Vui lòng kiểm tra lại thông tin.');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="create-asset-title"
      onClick={(e) => {
        if (e.target === e.currentTarget && !loading) onClose();
      }}
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/40 backdrop-blur-xs animate-in fade-in duration-150"
    >
      <div className="bg-white rounded-2xl shadow-xl max-w-lg w-full p-6 border border-slate-200/90 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between pb-4 border-b border-slate-100">
          <h2 id="create-asset-title" className="text-lg font-bold text-slate-900">
            Tạo tài sản mới
          </h2>
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

        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
          <div>
            <label
              htmlFor="asset-name"
              className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1"
            >
              Tên tài sản <span className="text-rose-500">*</span>
            </label>
            <input
              id="asset-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="VD: Laptop Dell XPS 15"
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-colors"
              required
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <div className="flex items-center justify-between mb-1">
                <label
                  htmlFor="asset-category"
                  className="block text-xs font-semibold text-slate-700 uppercase tracking-wider"
                >
                  Danh mục <span className="text-rose-500">*</span>
                </label>
                <button
                  type="button"
                  onClick={handleAiSuggest}
                  disabled={aiLoading}
                  className="inline-flex items-center space-x-1 text-xs text-purple-600 hover:text-purple-700 font-medium disabled:opacity-50"
                >
                  {aiLoading ? (
                    <>
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      <span>Đang phân tích...</span>
                    </>
                  ) : (
                    <>
                      <Sparkles className="w-3.5 h-3.5" />
                      <span>Gợi ý danh mục (AI)</span>
                    </>
                  )}
                </button>
              </div>
              <select
                id="asset-category"
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

              {aiNotice && (
                <div
                  role="status"
                  className="mt-2 p-2 bg-yellow-50 border-l-4 border-yellow-400 text-yellow-800 text-xs rounded-r-md"
                >
                  {aiNotice}
                </div>
              )}

              {aiSuggestion && (
                <div className="mt-2 p-2.5 bg-purple-50 border border-purple-200 rounded-lg text-xs text-purple-900">
                  <div className="flex items-center justify-between">
                    <div>
                      <span className="font-semibold text-purple-800">Gợi ý: </span>
                      <span className="font-bold">{aiSuggestion.suggested_category_name}</span>
                    </div>
                    <button
                      type="button"
                      onClick={handleApplySuggestion}
                      className="px-2 py-1 bg-purple-600 hover:bg-purple-700 text-white rounded text-xs font-medium transition-colors"
                    >
                      Áp dụng
                    </button>
                  </div>
                  {aiSuggestion.explanation && (
                    <p className="mt-1 text-purple-700">{aiSuggestion.explanation}</p>
                  )}
                </div>
              )}
            </div>

            <div>
              <label
                htmlFor="asset-department"
                className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1"
              >
                Phòng ban (tùy chọn)
              </label>
              <select
                id="asset-department"
                value={departmentId}
                onChange={(e) => setDepartmentId(e.target.value)}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-colors"
              >
                <option value="">-- Kho công ty (Chưa gán) --</option>
                {departments.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name} ({d.code})
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div>
            <label
              htmlFor="asset-serial"
              className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1"
            >
              Số serial
            </label>
            <input
              id="asset-serial"
              type="text"
              value={serialNumber}
              onChange={(e) => setSerialNumber(e.target.value)}
              placeholder="VD: SN-2026-XPS-001"
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-colors font-mono"
            />
          </div>

          <div>
            <label
              htmlFor="asset-spec"
              className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1"
            >
              Thông số kỹ thuật (Văn bản hoặc JSON)
            </label>
            <textarea
              id="asset-spec"
              rows={3}
              value={specificationText}
              onChange={(e) => setSpecificationText(e.target.value)}
              placeholder='VD: {"cpu": "Core i7", "ram": "16GB", "ssd": "512GB"}'
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
                  Đang tạo...
                </>
              ) : (
                'Tạo tài sản'
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default AssetCreateModal;

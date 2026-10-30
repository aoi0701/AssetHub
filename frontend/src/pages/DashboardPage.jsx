import React, { useState, useEffect, useCallback } from 'react';
import axiosClient from '../api/axiosClient';
import { useAuth } from '../context/AuthContext';
import StatusPieChart from '../components/charts/StatusPieChart';
import CategoryBarChart from '../components/charts/CategoryBarChart';
import DepartmentBarChart from '../components/charts/DepartmentBarChart';
import MonthlyTrendChart from '../components/charts/MonthlyTrendChart';
import {
  LayoutDashboard,
  Boxes,
  CheckCircle,
  Clock,
  AlertTriangle,
  FileQuestion,
  RefreshCw,
  AlertCircle,
  Trash2,
} from 'lucide-react';

const DashboardPage = () => {
  const { user } = useAuth();
  const isAdmin = user?.role === 'ADMIN';

  const [kpis, setKpis] = useState(null);
  const [charts, setCharts] = useState(null);
  const [categories, setCategories] = useState([]);
  const [departments, setDepartments] = useState([]);
  const [loading, setLoading] = useState(false);
  const [errorAlert, setErrorAlert] = useState('');
  const [validationError, setValidationError] = useState('');

  // Filters
  const [categoryId, setCategoryId] = useState('');
  const [departmentId, setDepartmentId] = useState('');
  const [startMonth, setStartMonth] = useState('');
  const [endMonth, setEndMonth] = useState('');

  // Dropdowns
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

  // Month range validation
  const validateMonthRange = useCallback((start, end) => {
    if (!start && !end) return true;
    if (start && !end) return true;
    if (!start && end) return true;

    // Both provided
    const [startYear, startM] = start.split('-').map(Number);
    const [endYear, endM] = end.split('-').map(Number);

    const startIndex = startYear * 12 + startM;
    const endIndex = endYear * 12 + endM;

    if (startIndex > endIndex) {
      setValidationError('Tháng bắt đầu không được lớn hơn tháng kết thúc.');
      return false;
    }

    const diffMonths = endIndex - startIndex + 1;
    if (diffMonths > 12) {
      setValidationError('Khoảng thời gian thống kê không được vượt quá 12 tháng.');
      return false;
    }

    setValidationError('');
    return true;
  }, []);

  const fetchData = useCallback(async () => {
    if (!validateMonthRange(startMonth, endMonth)) {
      return;
    }
    setValidationError('');
    setLoading(true);
    setErrorAlert('');

    const baseParams = {};
    if (categoryId) baseParams.category_id = categoryId;
    if (isAdmin && departmentId) baseParams.department_id = departmentId;

    const chartParams = { ...baseParams };
    if (startMonth) chartParams.start_month = startMonth;
    if (endMonth) chartParams.end_month = endMonth;

    try {
      const [kpiRes, chartsRes] = await Promise.all([
        axiosClient.get('/api/reports/kpis/', { params: baseParams }),
        axiosClient.get('/api/reports/charts/', { params: chartParams }),
      ]);
      setKpis(kpiRes.data);
      setCharts(chartsRes.data);
    } catch (err) {
      if (err.response?.status === 400 && err.response?.data) {
        const msg = typeof err.response.data === 'object'
          ? Object.values(err.response.data).flat().join(' ')
          : String(err.response.data);
        setErrorAlert(msg);
      } else {
        setErrorAlert('Không thể tải số liệu báo cáo thống kê. Vui lòng thử lại.');
      }
    } finally {
      setLoading(false);
    }
  }, [categoryId, departmentId, startMonth, endMonth, isAdmin, validateMonthRange]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleStartMonthChange = (val) => {
    setStartMonth(val);
    validateMonthRange(val, endMonth);
  };

  const handleEndMonthChange = (val) => {
    setEndMonth(val);
    validateMonthRange(startMonth, val);
  };

  return (
    <div className="w-full py-6 px-4 sm:px-6 lg:px-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between pb-6 border-b border-gray-200 gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <LayoutDashboard className="h-7 w-7 text-blue-600" />
            Bảng điều khiển (Dashboard)
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Tổng quan số liệu hiện trạng tài sản, tình trạng sử dụng và phân tích xu hướng.
          </p>
        </div>

        <div className="flex items-center space-x-3 self-start sm:self-auto">
          {kpis?.last_updated && (
            <span className="text-xs text-gray-400">
              Cập nhật lúc: {new Date(kpis.last_updated).toLocaleTimeString('vi-VN')}
            </span>
          )}
          <button
            onClick={fetchData}
            className="inline-flex items-center px-3 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 transition-colors"
          >
            <RefreshCw className={`h-4 w-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
            Làm mới
          </button>
        </div>
      </div>

      {/* Alerts */}
      {validationError && (
        <div
          role="alert"
          className="my-4 p-4 bg-amber-50 border-l-4 border-amber-500 rounded-r-lg flex items-start space-x-3 text-amber-800 text-sm"
        >
          <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
          <div className="flex-1">{validationError}</div>
        </div>
      )}

      {errorAlert && (
        <div
          role="alert"
          className="my-4 p-4 bg-red-50 border-l-4 border-red-500 rounded-r-lg flex items-start space-x-3 text-red-700 text-sm"
        >
          <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
          <div className="flex-1">{errorAlert}</div>
        </div>
      )}

      {/* Filters Bar */}
      <div className="mt-6 bg-white p-4 rounded-xl border border-slate-200/90 shadow-sm grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {/* Category Filter */}
        <div>
          <label className="block text-xs font-semibold text-slate-600 uppercase mb-1">
            Danh mục
          </label>
          <select
            value={categoryId}
            onChange={(e) => setCategoryId(e.target.value)}
            className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 text-slate-700 bg-white"
            aria-label="Lọc theo danh mục"
          >
            <option value="">Tất cả danh mục</option>
            {categories.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>

        {/* Department Filter (Admin only) */}
        {isAdmin && (
          <div>
            <label className="block text-xs font-semibold text-slate-600 uppercase mb-1">
              Phòng ban
            </label>
            <select
              value={departmentId}
              onChange={(e) => setDepartmentId(e.target.value)}
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 text-slate-700 bg-white"
              aria-label="Lọc theo phòng ban"
            >
              <option value="">Toàn công ty</option>
              <option value="unallocated">Kho công ty (Chưa gán)</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name} ({d.code})
                </option>
              ))}
            </select>
          </div>
        )}

        {/* Start Month */}
        <div>
          <label className="block text-xs font-semibold text-slate-600 uppercase mb-1">
            Từ tháng
          </label>
          <input
            type="month"
            value={startMonth}
            onChange={(e) => handleStartMonthChange(e.target.value)}
            placeholder="YYYY-MM"
            aria-label="Tháng bắt đầu"
            className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 text-slate-700 bg-white"
          />
        </div>

        {/* End Month */}
        <div>
          <label className="block text-xs font-semibold text-slate-600 uppercase mb-1">
            Đến tháng
          </label>
          <input
            type="month"
            value={endMonth}
            onChange={(e) => handleEndMonthChange(e.target.value)}
            placeholder="YYYY-MM"
            aria-label="Tháng kết thúc"
            className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 text-slate-700 bg-white"
          />
        </div>
      </div>

      {/* 6 KPI Cards */}
      <div className="mt-6 grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4">
        {/* Card 1: total_active */}
        <div className="bg-white p-5 rounded-xl border border-slate-200/90 shadow-sm hover:border-slate-300 transition-colors flex flex-col justify-between">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">Tổng tài sản</span>
            <div className="w-8 h-8 rounded-lg bg-slate-100 flex items-center justify-center text-blue-600">
              <Boxes className="w-4 h-4" />
            </div>
          </div>
          <div>
            <div className="text-2xl font-bold text-slate-900">
              {kpis?.total_active ?? 0}
            </div>
            <div className="text-xs text-slate-500 mt-1 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
              Đang hoạt động
            </div>
          </div>
        </div>

        {/* Card 2: available */}
        <div className="bg-white p-5 rounded-xl border border-slate-200/90 shadow-sm hover:border-slate-300 transition-colors flex flex-col justify-between">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">Sẵn sàng</span>
            <div className="w-8 h-8 rounded-lg bg-slate-100 flex items-center justify-center text-emerald-600">
              <CheckCircle className="w-4 h-4" />
            </div>
          </div>
          <div>
            <div className="text-2xl font-bold text-slate-900">
              {kpis?.available ?? 0}
            </div>
            <div className="text-xs text-slate-500 mt-1 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
              Trong kho sẵn dùng
            </div>
          </div>
        </div>

        {/* Card 3: in_use */}
        <div className="bg-white p-5 rounded-xl border border-slate-200/90 shadow-sm hover:border-slate-300 transition-colors flex flex-col justify-between">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">Đang sử dụng</span>
            <div className="w-8 h-8 rounded-lg bg-slate-100 flex items-center justify-center text-sky-600">
              <Clock className="w-4 h-4" />
            </div>
          </div>
          <div>
            <div className="text-2xl font-bold text-slate-900">
              {kpis?.in_use ?? 0}
            </div>
            <div className="text-xs text-slate-500 mt-1 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-sky-500" />
              Đã bàn giao
            </div>
          </div>
        </div>

        {/* Card 4: damaged_under_maintenance */}
        <div className="bg-white p-5 rounded-xl border border-slate-200/90 shadow-sm hover:border-slate-300 transition-colors flex flex-col justify-between">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">Hỏng & Sửa chữa</span>
            <div className="w-8 h-8 rounded-lg bg-slate-100 flex items-center justify-center text-amber-600">
              <AlertTriangle className="w-4 h-4" />
            </div>
          </div>
          <div>
            <div className="text-2xl font-bold text-slate-900">
              {kpis?.damaged_under_maintenance ?? 0}
            </div>
            <div className="text-xs text-slate-500 mt-1 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
              Cần khắc phục
            </div>
          </div>
        </div>

        {/* Card 5: pending_disposal */}
        <div className="bg-white p-5 rounded-xl border border-slate-200/90 shadow-sm hover:border-slate-300 transition-colors flex flex-col justify-between">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">Chờ thanh lý</span>
            <div className="w-8 h-8 rounded-lg bg-slate-100 flex items-center justify-center text-purple-600">
              <Trash2 className="w-4 h-4" />
            </div>
          </div>
          <div>
            <div className="text-2xl font-bold text-slate-900">
              {kpis?.pending_disposal ?? 0}
            </div>
            <div className="text-xs text-slate-500 mt-1 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-purple-500" />
              Chờ hội đồng duyệt
            </div>
          </div>
        </div>

        {/* Card 6: pending_requests */}
        <div className="bg-white p-5 rounded-xl border border-slate-200/90 shadow-sm hover:border-slate-300 transition-colors flex flex-col justify-between">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">Yêu cầu chờ</span>
            <div className="w-8 h-8 rounded-lg bg-slate-100 flex items-center justify-center text-rose-600">
              <FileQuestion className="w-4 h-4" />
            </div>
          </div>
          <div>
            <div className="text-2xl font-bold text-slate-900">
              {kpis?.pending_requests ?? 0}
            </div>
            <div className="text-xs text-slate-500 mt-1 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-rose-500" />
              Cần phê duyệt
            </div>
          </div>
        </div>
      </div>

      {/* Analytical Charts Grid */}
      <div className="mt-8 grid grid-cols-1 xl:grid-cols-2 gap-6">
        {/* Chart 1: Status Distribution */}
        <div className="bg-white p-6 rounded-xl border border-slate-200/90 shadow-sm">
          <h3 className="text-sm font-bold text-slate-900 mb-4 pb-2 border-b border-slate-100">
            Phân bố theo trạng thái tài sản
          </h3>
          <StatusPieChart data={charts?.status_distribution} />
        </div>

        {/* Chart 2: Category Distribution */}
        <div className="bg-white p-6 rounded-xl border border-slate-200/90 shadow-sm">
          <h3 className="text-sm font-bold text-slate-900 mb-4 pb-2 border-b border-slate-100">
            Phân bố theo danh mục
          </h3>
          <CategoryBarChart data={charts?.category_distribution} />
        </div>

        {/* Chart 3: Department Distribution (Admin only!) */}
        {isAdmin && (
          <div className="bg-white p-6 rounded-xl border border-slate-200/90 shadow-sm">
            <h3 className="text-sm font-bold text-slate-900 mb-4 pb-2 border-b border-slate-100">
              Phân bố tài sản giữa các phòng ban
            </h3>
            <DepartmentBarChart data={charts?.department_distribution} />
          </div>
        )}

        {/* Chart 4: Monthly Trend */}
        <div className={`bg-white p-6 rounded-xl border border-slate-200/90 shadow-sm ${!isAdmin ? 'xl:col-span-2' : ''}`}>
          <h3 className="text-sm font-bold text-slate-900 mb-4 pb-2 border-b border-slate-100">
            Xu hướng tài sản mới & thanh lý theo tháng
          </h3>
          <MonthlyTrendChart data={charts?.monthly_trend} />
        </div>
      </div>
    </div>
  );
};

export default DashboardPage;

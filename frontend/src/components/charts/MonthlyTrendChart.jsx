import React from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';

const MonthlyTrendChart = ({ data = [] }) => {
  const chartData = (data || []).map((item) => ({
    month: item.month,
    new_assets: item.new_assets,
    disposed_assets: item.disposed_assets,
  }));

  if (!chartData || chartData.length === 0) {
    return (
      <div
        data-testid="monthly-trend-chart"
        className="h-64 flex items-center justify-center text-sm text-gray-400 bg-gray-50/50 rounded-lg border border-dashed border-gray-200"
      >
        Chưa có dữ liệu xu hướng tháng
      </div>
    );
  }

  return (
    <div data-testid="monthly-trend-chart" className="w-full h-72">
      <ResponsiveContainer width="100%" height="100%" minWidth={100} minHeight={250}>
        <BarChart data={chartData} margin={{ top: 10, right: 20, left: 0, bottom: 20 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f0f0f0" />
          <XAxis dataKey="month" tick={{ fontSize: 12, fill: '#4b5563' }} />
          <YAxis allowDecimals={false} tick={{ fontSize: 12, fill: '#6b7280' }} />
          <Tooltip
            contentStyle={{ backgroundColor: '#fff', borderRadius: '8px', border: '1px solid #e5e7eb' }}
          />
          <Legend
            verticalAlign="bottom"
            height={36}
            formatter={(value) => (
              <span className="text-xs text-gray-700 font-medium">
                {value === 'new_assets' ? 'Tài sản mới' : 'Tài sản thanh lý'}
              </span>
            )}
          />
          <Bar dataKey="new_assets" name="new_assets" fill="#10b981" radius={[4, 4, 0, 0]} maxBarSize={32} />
          <Bar dataKey="disposed_assets" name="disposed_assets" fill="#ef4444" radius={[4, 4, 0, 0]} maxBarSize={32} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
};

export default MonthlyTrendChart;

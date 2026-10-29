import React from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';

const CategoryBarChart = ({ data = [] }) => {
  const chartData = (data || []).map((item) => ({
    name: item.category_name,
    count: item.count,
  }));

  if (!chartData || chartData.length === 0) {
    return (
      <div
        data-testid="category-bar-chart"
        className="h-64 flex items-center justify-center text-sm text-gray-400 bg-gray-50/50 rounded-lg border border-dashed border-gray-200"
      >
        Chưa có dữ liệu danh mục
      </div>
    );
  }

  return (
    <div data-testid="category-bar-chart" className="w-full h-72">
      <ResponsiveContainer width="100%" height="100%" minWidth={100} minHeight={250}>
        <BarChart data={chartData} margin={{ top: 10, right: 20, left: 0, bottom: 20 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f0f0f0" />
          <XAxis
            dataKey="name"
            tick={{ fontSize: 12, fill: '#4b5563' }}
            interval={0}
            angle={-15}
            textAnchor="end"
          />
          <YAxis allowDecimals={false} tick={{ fontSize: 12, fill: '#6b7280' }} />
          <Tooltip
            formatter={(value) => [`${value} tài sản`, 'Số lượng']}
            contentStyle={{ backgroundColor: '#fff', borderRadius: '8px', border: '1px solid #e5e7eb' }}
          />
          <Bar dataKey="count" fill="#3b82f6" radius={[4, 4, 0, 0]} maxBarSize={48} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
};

export default CategoryBarChart;

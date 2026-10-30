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
        className="h-64 flex items-center justify-center text-sm text-slate-400 bg-slate-50/50 rounded-lg border border-dashed border-slate-200"
      >
        Chưa có dữ liệu danh mục
      </div>
    );
  }

  return (
    <div data-testid="category-bar-chart" className="w-full h-72">
      <ResponsiveContainer width="100%" height="100%" minWidth={100} minHeight={250}>
        <BarChart
          layout="vertical"
          data={chartData}
          margin={{ top: 10, right: 30, left: 10, bottom: 10 }}
        >
          <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#f1f5f9" />
          <XAxis
            type="number"
            allowDecimals={false}
            tick={{ fontSize: 12, fill: '#64748b' }}
          />
          <YAxis
            type="category"
            dataKey="name"
            width={140}
            tick={{ fontSize: 12, fill: '#334155' }}
            tickLine={false}
          />
          <Tooltip
            formatter={(value) => [`${value} tài sản`, 'Số lượng']}
            contentStyle={{ backgroundColor: '#fff', borderRadius: '8px', border: '1px solid #e2e8f0' }}
          />
          <Bar dataKey="count" fill="#2563eb" radius={[0, 4, 4, 0]} maxBarSize={28} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
};

export default CategoryBarChart;

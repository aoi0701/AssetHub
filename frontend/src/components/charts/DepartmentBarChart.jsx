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

const DepartmentBarChart = ({ data = [] }) => {
  const chartData = (data || []).map((item) => ({
    name: item.department_name || 'Chưa phân bổ',
    count: item.count,
  }));

  if (!chartData || chartData.length === 0) {
    return (
      <div
        data-testid="department-bar-chart"
        className="h-64 flex items-center justify-center text-sm text-slate-400 bg-slate-50/50 rounded-lg border border-dashed border-slate-200"
      >
        Chưa có dữ liệu phòng ban
      </div>
    );
  }

  return (
    <div data-testid="department-bar-chart" className="w-full h-72">
      <ResponsiveContainer width="100%" height="100%" minWidth={100} minHeight={250}>
        <BarChart data={chartData} margin={{ top: 10, right: 20, left: 0, bottom: 20 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
          <XAxis
            dataKey="name"
            tick={{ fontSize: 12, fill: '#64748b' }}
            interval={0}
            angle={-15}
            textAnchor="end"
          />
          <YAxis allowDecimals={false} tick={{ fontSize: 12, fill: '#64748b' }} />
          <Tooltip
            formatter={(value) => [`${value} tài sản`, 'Số lượng']}
            contentStyle={{ backgroundColor: '#fff', borderRadius: '8px', border: '1px solid #e2e8f0' }}
          />
          <Bar dataKey="count" fill="#6366f1" radius={[4, 4, 0, 0]} maxBarSize={44} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
};

export default DepartmentBarChart;

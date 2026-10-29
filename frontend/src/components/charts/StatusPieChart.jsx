import React from 'react';
import {
  PieChart,
  Pie,
  Cell,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';

const STATUS_COLORS = {
  AVAILABLE: '#10b981',
  IN_USE: '#3b82f6',
  DAMAGED_PENDING_REPAIR: '#f59e0b',
  UNDER_MAINTENANCE: '#eab308',
  PENDING_DISPOSAL: '#a855f7',
  DISPOSED: '#6b7280',
};

const DEFAULT_COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899'];

const StatusPieChart = ({ data = [] }) => {
  const chartData = data
    .filter((item) => item.count > 0)
    .map((item) => ({
      name: item.label || item.status,
      value: item.count,
      status: item.status,
    }));

  if (!chartData || chartData.length === 0) {
    return (
      <div
        data-testid="status-pie-chart"
        className="h-64 flex items-center justify-center text-sm text-gray-400 bg-gray-50/50 rounded-lg border border-dashed border-gray-200"
      >
        Chưa có dữ liệu trạng thái
      </div>
    );
  }

  return (
    <div data-testid="status-pie-chart" className="w-full h-72">
      <ResponsiveContainer width="100%" height="100%" minWidth={100} minHeight={250}>
        <PieChart>
          <Pie
            data={chartData}
            cx="50%"
            cy="50%"
            innerRadius={50}
            outerRadius={85}
            paddingAngle={3}
            dataKey="value"
            nameKey="name"
          >
            {chartData.map((entry, index) => {
              const color =
                STATUS_COLORS[entry.status] ||
                DEFAULT_COLORS[index % DEFAULT_COLORS.length];
              return <Cell key={`cell-${index}`} fill={color} />;
            })}
          </Pie>
          <Tooltip
            formatter={(value, name) => [`${value} tài sản`, name]}
            contentStyle={{ backgroundColor: '#fff', borderRadius: '8px', border: '1px solid #e5e7eb' }}
          />
          <Legend
            verticalAlign="bottom"
            height={36}
            iconType="circle"
            formatter={(value) => <span className="text-xs text-gray-700 font-medium">{value}</span>}
          />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
};

export default StatusPieChart;

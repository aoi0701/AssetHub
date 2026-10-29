import React from 'react';

export const STATUS_CONFIG = {
  AVAILABLE: {
    label: 'Sẵn sàng',
    color: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    dot: 'bg-emerald-500',
  },
  IN_USE: {
    label: 'Đang sử dụng',
    color: 'bg-blue-50 text-blue-700 border-blue-200',
    dot: 'bg-blue-500',
  },
  DAMAGED_PENDING_REPAIR: {
    label: 'Hỏng chờ sửa',
    color: 'bg-amber-50 text-amber-700 border-amber-200',
    dot: 'bg-amber-500',
  },
  UNDER_MAINTENANCE: {
    label: 'Đang sửa chữa',
    color: 'bg-yellow-50 text-yellow-800 border-yellow-300',
    dot: 'bg-yellow-500',
  },
  PENDING_DISPOSAL: {
    label: 'Chờ thanh lý',
    color: 'bg-purple-50 text-purple-700 border-purple-200',
    dot: 'bg-purple-500',
  },
  DISPOSED: {
    label: 'Đã thanh lý',
    color: 'bg-gray-100 text-gray-700 border-gray-300',
    dot: 'bg-gray-500',
  },
};

const AssetStatusBadge = ({ status, className = '' }) => {
  const config = STATUS_CONFIG[status] || {
    label: status || 'Không xác định',
    color: 'bg-gray-100 text-gray-600 border-gray-200',
    dot: 'bg-gray-400',
  };

  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border ${config.color} ${className}`}
      data-status={status}
    >
      <span className={`w-1.5 h-1.5 mr-1.5 rounded-full ${config.dot}`} />
      {config.label}
    </span>
  );
};

export default AssetStatusBadge;

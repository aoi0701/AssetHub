import React, { useState, useEffect } from 'react';
import axiosClient from '../api/axiosClient';
import { useAuth } from '../context/AuthContext';
import {
  FileText,
  Image as ImageIcon,
  UploadCloud,
  Download,
  AlertCircle,
  CheckCircle,
  Loader2,
} from 'lucide-react';

const AssetAttachments = ({ asset, onUploadSuccess }) => {
  const { user } = useAuth();
  const isAdmin = user?.role === 'ADMIN';

  // Upload state
  const [uploadKind, setUploadKind] = useState('image');
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const [uploadSuccess, setUploadSuccess] = useState('');

  // Download / preview state
  const [downloadingKind, setDownloadingKind] = useState(null);
  const [downloadError, setDownloadError] = useState('');
  const [previewImageUrl, setPreviewImageUrl] = useState(null);

  // Fetch preview image URL if image key exists
  useEffect(() => {
    let isMounted = true;
    if (asset?.id && asset?.image) {
      axiosClient
        .get(`/api/assets/${asset.id}/attachments/image/download/`)
        .then((res) => {
          if (isMounted && res.data?.download_url) {
            setPreviewImageUrl(res.data.download_url);
          }
        })
        .catch(() => {
          if (isMounted) setPreviewImageUrl(null);
        });
    } else {
      setPreviewImageUrl(null);
    }
    return () => {
      isMounted = false;
    };
  }, [asset?.id, asset?.image]);

  const handleFileChange = (e) => {
    const file = e.target.files?.[0] || null;
    setSelectedFile(file);
    setUploadError('');
    setUploadSuccess('');
  };

  const handleUpload = async (e) => {
    if (e) e.preventDefault();
    if (!selectedFile) {
      setUploadError('Vui lòng chọn một tệp trước khi tải lên.');
      return;
    }

    setUploading(true);
    setUploadError('');
    setUploadSuccess('');

    const formData = new FormData();
    formData.append('kind', uploadKind);
    formData.append('file', selectedFile);

    try {
      const res = await axiosClient.post(
        `/api/assets/${asset.id}/attachments/`,
        formData,
        {
          headers: {
            'Content-Type': undefined,
          },
        }
      );
      setUploadSuccess(res.data?.message || 'Tải tệp lên thành công.');
      setSelectedFile(null);
      // Reset file input element if needed
      const fileInput = document.getElementById('attachment-file-input');
      if (fileInput) fileInput.value = '';

      if (onUploadSuccess) {
        onUploadSuccess();
      }
    } catch (err) {
      // Keep form state and selectedFile intact on error
      const data = err.response?.data;
      let errorMsg = 'Không thể tải tệp lên. Vui lòng thử lại.';
      if (typeof data === 'string') {
        errorMsg = data;
      } else if (data?.detail) {
        errorMsg = data.detail;
      } else if (data?.file) {
        errorMsg = Array.isArray(data.file) ? data.file.join(', ') : data.file;
      } else if (data?.kind) {
        errorMsg = Array.isArray(data.kind) ? data.kind.join(', ') : data.kind;
      }
      setUploadError(errorMsg);
    } finally {
      setUploading(false);
    }
  };

  const handleDownload = async (kind) => {
    setDownloadingKind(kind);
    setDownloadError('');

    try {
      const res = await axiosClient.get(
        `/api/assets/${asset.id}/attachments/${kind}/download/`
      );
      const downloadUrl = res.data?.download_url;
      if (downloadUrl) {
        if (typeof window !== 'undefined' && window.open) {
          window.open(downloadUrl, '_blank');
        }
      }
    } catch (err) {
      const msg =
        err.response?.data?.detail ||
        (err.response?.status === 403
          ? 'Bạn không có quyền tải tệp đính kèm này.'
          : 'Không thể tải xuống tệp đính kèm.');
      setDownloadError(msg);
    } finally {
      setDownloadingKind(null);
    }
  };

  if (!asset) return null;

  const isDisposed = asset.status === 'DISPOSED';

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm space-y-6">
      <div className="flex items-center justify-between pb-3 border-b border-gray-100">
        <h2 className="text-base font-bold text-gray-900 flex items-center gap-2">
          <PaperclipIcon className="w-5 h-5 text-blue-600" />
          Tệp đính kèm & Tài liệu
        </h2>
      </div>

      {/* Global Download Error Alert */}
      {downloadError && (
        <div
          role="alert"
          className="p-3 bg-red-50 border-l-4 border-red-500 rounded-r text-red-700 text-xs flex items-center justify-between"
        >
          <div className="flex items-center gap-2">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            <span>{downloadError}</span>
          </div>
          <button
            onClick={() => setDownloadError('')}
            className="text-red-500 hover:text-red-700 font-bold ml-2"
          >
            ×
          </button>
        </div>
      )}

      {/* Current Attachments Section */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Image Attachment Card */}
        <div className="border border-gray-100 rounded-lg p-4 bg-gray-50/60 flex flex-col justify-between">
          <div className="flex items-center space-x-3 mb-3">
            <div className="p-2 bg-blue-100 text-blue-700 rounded-lg">
              <ImageIcon className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-gray-800">Hình ảnh tài sản</h3>
              <p className="text-xs text-gray-400">JPEG, PNG, WebP (Tối đa 5MB)</p>
            </div>
          </div>

          {asset.image ? (
            <div className="space-y-3">
              {previewImageUrl ? (
                <div className="w-full h-40 bg-gray-200 rounded-md overflow-hidden flex items-center justify-center border border-gray-200">
                  <img
                    src={previewImageUrl}
                    alt={asset.name}
                    className="w-full h-full object-contain"
                  />
                </div>
              ) : (
                <div className="text-xs text-gray-500 italic py-2">
                  Đã đính kèm ảnh (Key: {asset.image.split('/').pop()})
                </div>
              )}
              <button
                type="button"
                onClick={() => handleDownload('image')}
                disabled={downloadingKind === 'image'}
                className="w-full inline-flex items-center justify-center px-3 py-1.5 bg-white border border-gray-300 hover:bg-gray-50 text-gray-700 text-xs font-medium rounded-lg transition-colors shadow-sm gap-1.5"
              >
                {downloadingKind === 'image' ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Download className="w-3.5 h-3.5 text-blue-600" />
                )}
                Tải ảnh xuống
              </button>
            </div>
          ) : (
            <div className="py-6 text-center text-xs text-gray-400 italic">
              Chưa có ảnh đính kèm
            </div>
          )}
        </div>

        {/* Document Attachment Card */}
        <div className="border border-gray-100 rounded-lg p-4 bg-gray-50/60 flex flex-col justify-between">
          <div className="flex items-center space-x-3 mb-3">
            <div className="p-2 bg-amber-100 text-amber-700 rounded-lg">
              <FileText className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-gray-800">Tài liệu / Hướng dẫn</h3>
              <p className="text-xs text-gray-400">Định dạng PDF (Tối đa 10MB)</p>
            </div>
          </div>

          {asset.document ? (
            <div className="space-y-3">
              <div className="p-3 bg-white border border-gray-200 rounded-md flex items-center justify-between text-xs text-gray-700">
                <span className="truncate max-w-[200px] font-mono">
                  {asset.document.split('/').pop()}
                </span>
                <span className="text-[10px] text-gray-400 uppercase font-bold">PDF</span>
              </div>
              <button
                type="button"
                onClick={() => handleDownload('document')}
                disabled={downloadingKind === 'document'}
                className="w-full inline-flex items-center justify-center px-3 py-1.5 bg-white border border-gray-300 hover:bg-gray-50 text-gray-700 text-xs font-medium rounded-lg transition-colors shadow-sm gap-1.5"
              >
                {downloadingKind === 'document' ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Download className="w-3.5 h-3.5 text-amber-600" />
                )}
                Tải tài liệu PDF
              </button>
            </div>
          ) : (
            <div className="py-6 text-center text-xs text-gray-400 italic">
              Chưa có tài liệu đính kèm
            </div>
          )}
        </div>
      </div>

      {/* Admin Upload Section */}
      {isAdmin && (
        <div className="border-t border-gray-100 pt-5">
          <h3 className="text-sm font-semibold text-gray-800 mb-3 flex items-center gap-1.5">
            <UploadCloud className="w-4 h-4 text-blue-600" />
            Tải lên tệp đính kèm mới (Chỉ Admin)
          </h3>

          {isDisposed ? (
            <div className="p-3 bg-gray-100 border border-gray-200 rounded-lg text-xs text-gray-600 italic">
              Không thể tải tệp lên tài sản đã thanh lý.
            </div>
          ) : (
            <form onSubmit={handleUpload} className="space-y-4">
              {/* Error Alert */}
              {uploadError && (
                <div
                  role="alert"
                  className="p-3 bg-red-50 border-l-4 border-red-500 rounded-r text-red-700 text-xs flex items-center justify-between"
                >
                  <div className="flex items-center gap-2">
                    <AlertCircle className="w-4 h-4 flex-shrink-0" />
                    <span>{uploadError}</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setUploadError('')}
                    className="text-red-500 hover:text-red-700 font-bold ml-2"
                  >
                    ×
                  </button>
                </div>
              )}

              {/* Success Alert */}
              {uploadSuccess && (
                <div
                  role="status"
                  className="p-3 bg-green-50 border-l-4 border-green-500 rounded-r text-green-700 text-xs flex items-center justify-between"
                >
                  <div className="flex items-center gap-2">
                    <CheckCircle className="w-4 h-4 flex-shrink-0" />
                    <span>{uploadSuccess}</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setUploadSuccess('')}
                    className="text-green-500 hover:text-green-700 font-bold ml-2"
                  >
                    ×
                  </button>
                </div>
              )}

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                {/* Kind selection */}
                <div>
                  <label
                    htmlFor="attachment-kind-select"
                    className="block text-xs font-medium text-gray-700 mb-1"
                  >
                    Loại tệp
                  </label>
                  <select
                    id="attachment-kind-select"
                    aria-label="Loại tệp"
                    value={uploadKind}
                    onChange={(e) => {
                      setUploadKind(e.target.value);
                      setUploadError('');
                    }}
                    className="w-full text-xs border border-gray-300 rounded-lg px-2.5 py-2 bg-white text-gray-800 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  >
                    <option value="image">Hình ảnh (Ảnh tài sản)</option>
                    <option value="document">Tài liệu (PDF)</option>
                  </select>
                </div>

                {/* File picker */}
                <div className="sm:col-span-2">
                  <label
                    htmlFor="attachment-file-input"
                    className="block text-xs font-medium text-gray-700 mb-1"
                  >
                    Chọn tệp ({uploadKind === 'image' ? 'JPG, PNG, WebP ≤ 5MB' : 'PDF ≤ 10MB'})
                  </label>
                  <input
                    id="attachment-file-input"
                    type="file"
                    aria-label="Chọn tệp"
                    accept={
                      uploadKind === 'image'
                        ? '.jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp'
                        : '.pdf,application/pdf'
                    }
                    onChange={handleFileChange}
                    className="w-full text-xs text-gray-600 file:mr-2 file:py-1.5 file:px-3 file:rounded-md file:border-0 file:text-xs file:font-semibold file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100 border border-gray-300 rounded-lg p-1 bg-white"
                  />
                </div>
              </div>

              {/* Progress Indicator */}
              {uploading && (
                <div className="space-y-1">
                  <div className="flex justify-between text-[11px] text-gray-500">
                    <span>Đang tải tệp lên máy chủ...</span>
                  </div>
                  <div className="w-full bg-gray-200 rounded-full h-1.5 overflow-hidden">
                    <div className="bg-blue-600 h-1.5 rounded-full animate-pulse w-full"></div>
                  </div>
                </div>
              )}

              {/* Upload Action Button */}
              <div className="flex justify-end">
                <button
                  type="submit"
                  disabled={uploading || !selectedFile}
                  className="inline-flex items-center px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-medium transition-colors shadow-sm disabled:opacity-50 disabled:cursor-not-allowed gap-1.5"
                >
                  {uploading ? (
                    <>
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      <span>Đang tải lên...</span>
                    </>
                  ) : (
                    <>
                      <UploadCloud className="w-3.5 h-3.5" />
                      <span>Tải lên</span>
                    </>
                  )}
                </button>
              </div>
            </form>
          )}
        </div>
      )}
    </div>
  );
};

// Helper Paperclip icon in case not exported by lucide
const PaperclipIcon = ({ className }) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
  >
    <path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" />
  </svg>
);

export default AssetAttachments;

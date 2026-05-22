export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export const SUPPORTED_UPLOAD_TYPES = [
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/pdf",
];

export const MAX_UPLOAD_SIZE = 50 * 1024 * 1024; // 50MB

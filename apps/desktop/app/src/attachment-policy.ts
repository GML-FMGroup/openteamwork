const MEBIBYTE = 1024 * 1024;

export const MAX_ATTACHMENT_BYTES = 20 * MEBIBYTE;
export const MAX_MESSAGE_ATTACHMENT_BYTES = 50 * MEBIBYTE;
export const MAX_MESSAGE_ATTACHMENTS = 10;

const DOCUMENT_EXTENSIONS = [".doc", ".docx", ".xls", ".xlsx", ".csv", ".pdf", ".pptx"];
const IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp"];
const TEXT_EXTENSIONS = [
  ".css", ".html", ".ini", ".js", ".json", ".jsx", ".log", ".md", ".py",
  ".toml", ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml",
];
const SUPPORTED_EXTENSIONS = new Set([
  ...DOCUMENT_EXTENSIONS,
  ...IMAGE_EXTENSIONS,
  ...TEXT_EXTENSIONS,
]);
const LEGACY_OFFICE_EXTENSIONS = new Set([".ppt"]);

export const ATTACHMENT_ACCEPT = [...DOCUMENT_EXTENSIONS, ...IMAGE_EXTENSIONS, ...TEXT_EXTENSIONS].join(",");

function fileExtension(fileName: string): string {
  const index = fileName.lastIndexOf(".");
  return index >= 0 ? fileName.slice(index).toLowerCase() : "";
}

/** Return a user-facing preflight error; the Node remains the authoritative validator. */
export function attachmentPreflightError(file: Pick<File, "name" | "size">): string | null {
  const extension = fileExtension(file.name);
  if (LEGACY_OFFICE_EXTENSIONS.has(extension)) {
    return `${file.name} uses a legacy Office format. Convert it to PPTX first.`;
  }
  if (!SUPPORTED_EXTENSIONS.has(extension)) {
    return `${file.name} is not a supported attachment type.`;
  }
  if (file.size <= 0 || file.size > MAX_ATTACHMENT_BYTES) {
    return `${file.name} must be between 1 byte and 20 MB.`;
  }
  return null;
}

# Sessions, Attachments, and Artifacts

OpenPPX stores Session lifecycle metadata and Artifact content on the Node. Desktop, CLI, and future clients operate through the Client API; they never pass an arbitrary client filesystem path to the Agent runtime.

## Session lifecycle

Desktop supports creating, renaming, forking, exporting, archiving, restoring through the archived configuration, and permanently deleting a Session. The display title and archive state are durable Node-owned metadata layered over the Google ADK Session facts.

Deletion is explicit. It removes the selected Session and every Session-scoped Artifact key/version through the Control Plane; it does not silently turn a missing Session into an empty conversation. Forking copies all Artifact versions into the new Session, while archiving retains them. Export returns a bounded JSON representation for user-controlled storage.

## Attachment flow

The Composer supports file selection, drag-and-drop, and clipboard paste. Before a Run starts, Desktop:

1. validates the count and byte limits;
2. reads the selected client file without exposing its original path;
3. uploads it to the selected Node and Session;
4. receives a stable Artifact key and version;
5. sends only those Artifact references with the user message.

Current client limits are:

- no more than 10 files per message;
- 1 byte to 20 MB per file;
- no more than 50 MB for all attachments in one message.

The Node independently validates the filename, extension, MIME, content signature, encoded content, Session ownership, per-file size, and message aggregate. A reference from another Session is rejected. Persisted files are validated again when resolved for a Run, so a stale or externally damaged Artifact cannot bypass the upload boundary.

Supported attachment formats are modern Word `.docx`, Excel `.xlsx`, UTF-8 `.csv`, text-based `.pdf`, PowerPoint `.pptx`, PNG/JPEG/WebP images, and a bounded set of UTF-8 text/code extensions. Legacy `.doc`, `.xls`, and `.ppt` require conversion. Scanned-PDF OCR and encrypted PDFs are not supported in the current preview.

Office archives are checked for unsafe paths, symlinks, encryption, duplicate members, entry count, expanded size, compression ratio, required OOXML parts, content type, XML entities, and malformed XML. PDF page count, spreadsheet cell count, extracted characters, and image pixel count are bounded. The original bytes remain the durable Artifact.

## Artifact panel

The right-side Artifacts section merges durable Node Artifact resources with older transcript projections while preferring the durable resource for duplicate filenames. Opening a durable Artifact downloads its selected version on demand.

Preview behavior:

- images use a bounded inline image preview;
- text, Markdown, JSON, JavaScript, XML, and YAML use an escaped text preview capped at 100,000 characters;
- audio uses the native read-only audio control;
- other formats show metadata and remain downloadable.

Text content is rendered as text, not HTML, so an Artifact cannot inject markup into the Renderer. Downloads use an in-memory data URL in the current developer preview; large-file streaming is a later contract optimization.

## Runtime use

The Client API resolves each reference inside the selected Agent and Session scope and loads bytes through the Google ADK Artifact service. Office, PDF, CSV, and text formats become deterministic bounded text parts; supported images remain validated binary parts for vision-capable models. The runtime does not trust the client-supplied filename as a server path.

Artifacts remain associated with the Session and are available after restarting Desktop or switching Node targets, provided the selected Node still owns that Session.

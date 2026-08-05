# Sessions, Attachments, and Artifacts

OpenPPX stores Session lifecycle metadata and Artifact content on the Node. Desktop, CLI, and future clients operate through the Client API; they never pass an arbitrary client filesystem path to the Agent runtime.

## Session lifecycle

Desktop supports creating, renaming, forking, exporting, archiving, restoring through the archived configuration, and permanently deleting a Session. The display title and archive state are durable Node-owned metadata layered over the Google ADK Session facts.

Deletion is explicit. It removes the selected Session through the Control Plane and does not silently turn a missing Session into an empty conversation. Export returns a bounded JSON representation for user-controlled storage.

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

The Node independently validates the Artifact key, encoded content, Session ownership, and request size. A reference from another Session is rejected.

## Artifact panel

The right-side Artifacts section merges durable Node Artifact resources with older transcript projections while preferring the durable resource for duplicate filenames. Opening a durable Artifact downloads its selected version on demand.

Preview behavior:

- images use a bounded inline image preview;
- text, Markdown, JSON, JavaScript, XML, and YAML use an escaped text preview capped at 100,000 characters;
- audio uses the native read-only audio control;
- other formats show metadata and remain downloadable.

Text content is rendered as text, not HTML, so an Artifact cannot inject markup into the Renderer. Downloads use an in-memory data URL in the current developer preview; large-file streaming is a later contract optimization.

## Runtime use

The Client API resolves each reference inside the selected Agent and Session scope, loads bytes through the Google ADK Artifact service, and creates model input parts using the validated MIME type. The runtime does not trust the client-supplied filename as a server path.

Artifacts remain associated with the Session and are available after restarting Desktop or switching Node targets, provided the selected Node still owns that Session.

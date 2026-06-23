# Media Detection Fix

This build indexes supported video extensions even when Telegram labels a document as `application/octet-stream`.

Supported direct video files: `.mkv`, `.mp4`, `.webm`, `.mov`, `.avi`, `.m4v`, `.ts`, `.m2ts`, `.wmv`, `.flv`.

Supported split uploads:

- `Movie.Name.2026.mkv.001`, `.002`, ...
- `Movie.Name.2026.zip.001`, `.002`, ... (the ZIP must contain a playable video)

Split uploads require parts numbered continuously from `001` and at least two parts. Live indexing starts about 60 seconds after the final part; `/rescan` indexes complete groups when its scan ends.

For title matching, include a readable title and year in the caption or filename, such as `Movie Name 2026 1080p.mkv`. A file can now be recognized as video but still be skipped when metadata lookup cannot identify its movie/show.

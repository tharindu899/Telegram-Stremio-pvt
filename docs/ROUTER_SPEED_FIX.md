# Router behavior replaced by v3.2.0

This older dynamic chunk-striping note is superseded by `V3.2.0_STREAM_ROUTER.md`.

TG Stremio v3.2.0 uses one selected bot per viewer playback. Extra Telegram bots are balanced across concurrent users, not striped across one stream.

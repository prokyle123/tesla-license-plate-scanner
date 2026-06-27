# Architecture

TeslaCam Plate Dashboard is a local Flask application paired with a continuous
scanner process.

1. **Scanner** indexes `RecentClips`, `SentryClips`, and `SavedClips` under a
   configured TeslaCam root.
2. Frames are sampled on a configurable interval.
3. The scanner sends frames to a local CodeProject.AI endpoint. A custom
   license-plate model can be used for ALPR detections, while the standard
   detection endpoint can provide general object detections.
4. Optional local Tesseract OCR and optional CodeProject.AI OCR enrich plate
   data. The dashboard stores results in local SQLite and generated images in
   the local static directory.
5. Flask/Waitress serves the dashboard, clip views, sightings, object views,
   watchlist, runtime settings, health endpoint, and scanner status.

The web UI and scanner share the same SQLite file, which uses WAL mode to make
normal concurrent reads practical on a local device.

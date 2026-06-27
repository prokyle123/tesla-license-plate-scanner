# Tesla License Plate Scanner v1.0.0

## First public release

This is the first packaged public release of Tesla License Plate Scanner: a local-first AI review dashboard for TeslaCam footage, TeslaUSB backups, and folders containing Tesla Dashcam, Sentry Mode, and Saved Clip video.

### Highlights

- Local TeslaCam clip indexing for `RecentClips`, `SentryClips`, and `SavedClips`.
- AI plate-detection integration through a configurable CodeProject.AI custom endpoint.
- Optional local Tesseract OCR and optional CodeProject.AI OCR support.
- Plate sightings, thumbnail review, clips, objects, watchlist, scanner status, settings, and responsive dashboard views.
- Duplicate-reduction workflow for repeated nearby detections.
- Separate Flask web dashboard and background scanner services.
- `systemd` deployment support for Raspberry Pi, Debian, Ubuntu, Kali Linux, and similar Linux systems.
- New TeslaUSB workflow documentation and CodeProject.AI setup guide.
- Dashboard preview updated to reflect the actual app layout using synthetic data only.

### Not included

The release intentionally excludes TeslaCam video, captured plate images, live SQLite databases, private configuration, model weights, and vehicle-owner lookup data.

### Getting started

1. Install CodeProject.AI and configure a compatible custom plate model.
2. Install Tesla License Plate Scanner on a Linux host.
3. Point `teslacam_root` at a TeslaCam folder or TeslaUSB backup location.
4. Set `cpai_base_url` and `cpai_model` in `config.json`.
5. Start the web and scanner services.

See `README.md` and `docs/CODEPROJECT_AI.md` for the full walkthrough.

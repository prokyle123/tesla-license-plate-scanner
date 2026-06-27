# Tesla License Plate Scanner
### AI dashboard for TeslaCam footage, TeslaUSB backups, and Tesla's built-in cameras.

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Web-Flask-111111?logo=flask)
![CodeProject.AI](https://img.shields.io/badge/AI-CodeProject.AI-6D28D9)
![Local First](https://img.shields.io/badge/Privacy-Local--First-16A34A)
![License](https://img.shields.io/badge/License-MIT-16A34A)

Turn Tesla camera footage into a **searchable local intelligence dashboard**.

Tesla vehicles can save a lot of Dashcam, Sentry Mode, and Saved Clip footage — but it normally lives as folders full of video files. Tesla License Plate Scanner is the analysis layer that scans those folders, samples frames, uses a local AI detector to find visible license plates, optionally runs OCR, reduces duplicate captures, and organizes the results by time, camera, clip, confidence, and event.

> **Built to work alongside TeslaUSB.** TeslaUSB runs on a Raspberry Pi and can preserve or back up TeslaCam footage to storage. This project scans that saved footage — or any compatible TeslaCam folder — and turns it into a searchable review system.

![Tesla License Plate Scanner dashboard preview](docs/images/dashboard-preview.svg)

> **Privacy-first:** This repository contains no TeslaCam footage, real plate images, live database, secret configuration, or vehicle-owner lookup data. The preview above uses synthetic demonstration data only.

---

## Why It Exists

Tesla's built-in cameras can capture useful footage before, during, and after an event. The hard part is finding the important moment in a pile of video files.

Instead of opening clip after clip and hunting through footage manually, this project can help turn a TeslaCam archive into a local, searchable visual record of what the vehicle captured.

### What It Can Do

- Scan TeslaCam `RecentClips`, `SentryClips`, and `SavedClips` folders.
- Work with TeslaUSB backups, a Tesla USB drive, external SSDs, NAS shares, or a copied TeslaCam archive.
- Sample clips at a configurable interval rather than processing every video frame.
- Send frames to a local CodeProject.AI custom plate-detection endpoint.
- Generate reviewable plate crops tied to the exact source clip and camera.
- Optionally run Tesseract OCR or CodeProject.AI OCR against detected plate regions.
- Record timestamps, source camera, confidence, clip path, OCR text, and local thumbnails.
- Reduce duplicate sightings across nearby frames and multiple camera views.
- Track general object detections separately from plate results.
- Search, filter, and review everything in a responsive Flask dashboard.
- Run locally on a Raspberry Pi, mini PC, Linux machine, or home server.

---

## TeslaUSB + TeslaCam Workflow

TeslaUSB and Tesla License Plate Scanner solve different parts of the same workflow.

| Layer | Role |
| --- | --- |
| **Tesla vehicle** | Captures Dashcam, Sentry Mode, and Saved Clip camera footage. |
| **TeslaUSB on a Raspberry Pi** | Acts as TeslaCam storage and can automatically preserve or back up video clips to attached storage. |
| **Tesla License Plate Scanner** | Watches TeslaCam-compatible folders, processes saved footage with AI, and builds a searchable dashboard. |

```text
Tesla Built-In Cameras
        ↓
Tesla Dashcam / Sentry / Saved Clips
        ↓
TeslaUSB running on a Raspberry Pi  (optional capture + backup layer)
        ↓
TeslaCam folders on Pi storage, SSD, NAS, or backup drive
        ↓
Tesla License Plate Scanner
        ↓
AI Detection + OCR + Searchable Local Dashboard
```

TeslaUSB is optional. The scanner can analyze any TeslaCam-compatible folder that is locally available or mounted from another system.

---

## How It Works

### 1. Tesla records the scene

The Tesla saves video footage from its built-in cameras to the TeslaCam folder structure. Available camera streams, clips, and event formats can vary by model, software version, and event type.

```text
TeslaCam/
├── RecentClips/
├── SentryClips/
└── SavedClips/
```

### 2. TeslaUSB or another backup method preserves the footage

TeslaUSB can run on a Raspberry Pi in the vehicle, provide the TeslaCam storage interface, and keep a backup copy of video on Pi-attached storage. You can also point this project at a manual copy, NAS archive, or external drive.

### 3. The scanner indexes clips and samples frames

The background scanner finds compatible videos, reads the camera metadata, and pulls frames at a configurable interval. This makes long TeslaCam archives practical to review without treating every frame as a separate AI job.

### 4. A local AI endpoint detects likely plates

Selected frames go to a local CodeProject.AI endpoint. The project expects a compatible custom endpoint in this format:

```text
{cpai_base_url}/v1/vision/custom/{cpai_model}
```

For the default configuration, `cpai_model: "license-plate"` means the app calls:

```text
http://127.0.0.1:32168/v1/vision/custom/license-plate
```

The detector returns likely plate regions and confidence values. The scanner saves a crop and links it back to the original clip, camera label, event, and frame time.

### 5. OCR tries to read the plate text

OCR is optional. When enabled, the project can use local Tesseract OCR and/or a CodeProject.AI OCR endpoint to attempt alphanumeric text extraction.

Because real footage can include blur, glare, rain, darkness, distance, compression artifacts, and poor angles, OCR is a **review aid**, not guaranteed identification.

### 6. Duplicate sightings are reduced

The same vehicle may appear in nearby frames and multiple camera views. The scanner uses available information such as time, source clip, camera, plate text, confidence, and detection position to keep repeated results from overwhelming the dashboard.

### 7. Everything is searchable in one place

The dashboard brings clips, plate sightings, OCR attempts, object detections, scanner status, configuration, and locally generated previews into one browser interface.

![Processing pipeline](docs/images/pipeline.svg)

---

## Dashboard Features

| Area | What It Provides |
| --- | --- |
| **Overview** | KPI cards, detection timeline, latest captures, quick actions, and a live scanner panel. |
| **Global Search** | Quick plate-text search from the top bar. |
| **Clips** | TeslaCam video inventory with linked detections and source information. |
| **Sightings** | Time-ordered detection review with camera, confidence, and media links. |
| **Plate Intelligence** | Plate-text search, OCR metadata, detection history, and image crops. |
| **Objects** | Optional general object detections, separate from plate detections. |
| **Watchlist** | Local-only workflow for plate text you choose to flag or review. |
| **Scanner Status** | Current pass state, queue, next scan, CodeProject.AI availability, and live logs. |
| **Settings** | Detection thresholds, OCR options, cleanup settings, folders, and endpoint configuration. |
| **Responsive UI** | Compact icon rail on desktop, mobile slide-over navigation, light/dark themes, and image-first review tables. |

---

## Requirements

### Hardware

A Linux host, mini PC, Raspberry Pi, or home server can run the project. More CPU and faster storage improve scan speed on larger archives.

You need:

- TeslaCam footage locally available or mounted from another system.
- Python 3.
- Adequate storage for the local SQLite database and generated preview images.
- A separately installed CodeProject.AI Server.
- A compatible custom license-plate model/endpoint.
- Optional Tesseract OCR for local text extraction.

### Project dependencies installed by `install.sh`

```text
python3
python3-venv
python3-pip
python3-opencv
tesseract-ocr
ffmpeg
rsync
```

---

## Install Tesla License Plate Scanner

Clone the repository:

```bash
git clone https://github.com/prokyle123/tesla-license-plate-scanner.git
cd tesla-license-plate-scanner
```

Run the installer on the Linux host that will run the dashboard:

```bash
sudo bash install.sh
```

The installer deploys the application under:

```text
/opt/teslacam-plate-dashboard
```

It creates a local configuration file here:

```text
/opt/teslacam-plate-dashboard/config.json
```

Edit the settings:

```bash
sudo nano /opt/teslacam-plate-dashboard/config.json
```

Restart services after changing configuration:

```bash
sudo systemctl restart teslacam-plate-web.service
sudo systemctl restart teslacam-plate-scanner.service
```

Open the dashboard from a trusted local device:

```text
http://YOUR-LINUX-HOST-IP:5057
```

---

## Install CodeProject.AI Server

CodeProject.AI is the local AI server that this project calls for plate and object detection. It runs on the same machine or another reachable machine on your private network.

**Start here:** [CodeProject.AI Setup Guide](docs/CODEPROJECT_AI.md)

The short version for an x64 Linux machine with Docker is:

```bash
sudo mkdir -p /opt/codeproject-ai/{data,modules}

docker run --name CodeProject.AI -d \
  --restart unless-stopped \
  -p 32168:32168 \
  -v /opt/codeproject-ai/data:/etc/codeproject/ai \
  -v /opt/codeproject-ai/modules:/app/modules \
  codeproject/ai-server
```

Then open the CodeProject.AI dashboard:

```text
http://YOUR-CPAI-HOST:32168
```

For an ARM64 Linux system, use `codeproject/ai-server:arm64`. For Raspberry Pi-specific deployments, CodeProject.AI documents the `codeproject/ai-server:rpi64` image. The included guide explains both paths, health checks, app configuration, and the important custom-model requirement.

> **Important:** CodeProject.AI itself is separate from this project. The `license-plate` endpoint and its compatible model are not bundled in this repository. Configure your custom model in CodeProject.AI, then set `cpai_model` in this project's `config.json` to the endpoint name it exposes.

---

## Example Configuration

Start with the safe example configuration:

```bash
cp config.example.json config.json
```

```json
{
  "app": {
    "host": "0.0.0.0",
    "port": 5057
  },
  "paths": {
    "teslacam_root": "/path/to/TeslaCam"
  },
  "scanner": {
    "folders": ["RecentClips", "SentryClips", "SavedClips"],
    "scan_sleep_s": 60,
    "frame_interval_s": 2,
    "cpai_base_url": "http://127.0.0.1:32168",
    "cpai_model": "license-plate",
    "cpai_min_det_conf": 0.05,
    "ocr_enabled": false,
    "ocr_min_conf": 45,
    "object_detection_enabled": true
  }
}
```

| Setting | Meaning |
| --- | --- |
| `teslacam_root` | Main directory containing the TeslaCam folder. |
| `folders` | TeslaCam folders to scan. |
| `scan_sleep_s` | Seconds between scanner passes. |
| `frame_interval_s` | Sampling interval used inside each video. |
| `cpai_base_url` | CodeProject.AI Server URL. |
| `cpai_model` | Custom plate-detector endpoint/model name. |
| `cpai_min_det_conf` | Minimum detector confidence to store. |
| `ocr_enabled` | Enables local Tesseract OCR. |
| `ocr_cpai_enabled` | Enables CodeProject.AI OCR. |
| `object_detection_enabled` | Enables optional generic object detection. |
| `generated_retention_days` | Days to keep locally generated images before cleanup. |

---

## Service Management

Check service status:

```bash
sudo systemctl status teslacam-plate-web.service --no-pager -l
sudo systemctl status teslacam-plate-scanner.service --no-pager -l
```

Follow dashboard logs:

```bash
sudo journalctl -u teslacam-plate-web.service -f
```

Follow scanner logs:

```bash
sudo journalctl -u teslacam-plate-scanner.service -f
```

Start or stop the full project:

```bash
sudo systemctl start teslacam-plate.target
sudo systemctl stop teslacam-plate.target
```

Disable automatic startup:

```bash
sudo systemctl disable --now teslacam-plate.target
```

---

## Development Run

Run the project without installing `systemd` services:

```bash
cp config.example.json config.json

python3 -m venv venv
. venv/bin/activate

pip install -r requirements.txt
python -m app.webapp --config config.json
```

In a second terminal:

```bash
. venv/bin/activate
python -m scanner.run_scanner --config config.json
```

Then browse to:

```text
http://127.0.0.1:5057
```

---

## Project Layout

```text
tesla-license-plate-scanner/
├── app/                    # Flask app, database, routes, storage, video helpers
├── scanner/                # Frame sampling, AI requests, OCR, deduplication, scan loop
├── templates/              # Dashboard pages
├── static/                 # CSS, JavaScript, and generated local media
├── systemd/                # Web, scanner, and target service definitions
├── tools/                  # Maintenance utilities, including OCR backfill
├── docs/                   # Deployment, CodeProject.AI, privacy, and architecture docs
├── extras/codeproject-ai/  # Example Docker Compose files for CodeProject.AI
├── config.example.json     # Safe example configuration
├── install.sh              # Linux installer
└── requirements.txt        # Python dependencies
```

---

## Privacy, Security, and Responsible Use

This project is intentionally local-first.

The public repository does **not** include:

- TeslaCam footage.
- Captured license-plate images.
- Live SQLite databases.
- Private configuration files.
- Vehicle-owner lookup tools.
- Government or commercial plate databases.
- Cloud upload functionality.
- Built-in user authentication.
- Public-internet exposure controls.

Keep the dashboard on a trusted local network. Use a VPN or authenticated reverse proxy before allowing remote access.

Use this project only with footage you own or are explicitly authorized to review. Detection and OCR results can be wrong; verify important results against the original source video.

---

## Limitations

- Detection quality depends on lighting, distance, blur, weather, glare, camera angle, speed, and video compression.
- OCR can misread plates and is not guaranteed accurate.
- This is a footage-review tool, not a real-time tracking system.
- This does not identify people or vehicle owners.
- Tesla camera availability and video formats can vary by vehicle and software version.
- CodeProject.AI, Docker, and custom detector models are separate dependencies.

---

## Documentation

- [CodeProject.AI Setup Guide](docs/CODEPROJECT_AI.md)
- [Deployment Guide](docs/DEPLOYMENT.md)
- [Architecture Overview](docs/ARCHITECTURE.md)
- [Privacy Notes](docs/PRIVACY.md)
- [Security Guidance](SECURITY.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

---

## Not Affiliated With Tesla or TeslaUSB

Tesla, Dashcam, and Sentry Mode are trademarks or product names of Tesla, Inc. TeslaUSB is a separate community project.

This is an independent home-lab project. It is not affiliated with, endorsed by, sponsored by, or supported by Tesla, Inc. or TeslaUSB.

---

## License

Released under the [MIT License](LICENSE).

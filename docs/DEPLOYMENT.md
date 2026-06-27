# Deployment guide

## Requirements

- Linux host, Raspberry Pi, or other always-on local machine
- Python 3 and `python3-venv`
- `ffmpeg`, `tesseract-ocr`, and `python3-opencv`
- A mounted TeslaCam folder
- CodeProject.AI reachable from the dashboard host

## Fast install

```bash
sudo bash install.sh
```

The installer creates `/opt/teslacam-plate-dashboard`, creates a virtual
environment, installs Python requirements, creates `config.json` from the
example if needed, and enables the web and scanner services.

Review the generated configuration before relying on the scanner:

```bash
sudo nano /opt/teslacam-plate-dashboard/config.json
sudo systemctl restart teslacam-plate-scanner.service teslacam-plate-web.service
```

## Key configuration

| Setting | Purpose |
| --- | --- |
| `paths.teslacam_root` | Folder that contains the TeslaCam clip folders. |
| `scanner.cpai_base_url` | Base URL of CodeProject.AI, usually local port `32168`. |
| `scanner.cpai_model` | Custom model name used by the plate detector endpoint. |
| `scanner.frame_interval_s` | Seconds between sampled frames for plate scanning. |
| `scanner.object_frame_interval_s` | Seconds between sampled frames for general object detection. |
| `scanner.ocr_enabled` | Enables local Tesseract OCR. |
| `scanner.ocr_cpai_enabled` | Enables OCR through CodeProject.AI. |
| `scanner.generated_retention_days` | Retention for generated images. |

## Service operations

```bash
sudo systemctl status teslacam-plate-web.service teslacam-plate-scanner.service --no-pager -l
sudo systemctl restart teslacam-plate-web.service teslacam-plate-scanner.service
sudo journalctl -u teslacam-plate-scanner.service -f
```

Open the dashboard at `http://HOSTNAME-OR-IP:5057` on the trusted local
network. The `/health` endpoint returns a simple JSON health response.

## Manual run

```bash
cp config.example.json config.json
python3 -m venv venv
. venv/bin/activate
pip install -r requirements.txt
python -m app.webapp --config config.json
python -m scanner.run_scanner --config config.json
```

Run these in two terminals for development.

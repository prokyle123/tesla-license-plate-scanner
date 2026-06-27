# CodeProject.AI Server Setup

Tesla License Plate Scanner uses **CodeProject.AI Server** as a separate local AI service. The scanner sends sampled JPEG frames to that service over HTTP, receives detection data, and stores the results locally.

This project does not bundle CodeProject.AI, Docker, or a custom plate detector model. Keep the AI service on the same host or a trusted private network.

> **Default app connection:** `http://127.0.0.1:32168`  
> **Default custom plate endpoint:** `/v1/vision/custom/license-plate`

---

## Choose Where CodeProject.AI Runs

| Host type | Recommended image | Notes |
| --- | --- | --- |
| x64 Linux mini PC / server | `codeproject/ai-server` | Best general starting point for CPU inference. |
| x64 Linux with supported NVIDIA GPU | `codeproject/ai-server:cuda11_7` | Requires Docker GPU setup and `--gpus all`. |
| ARM64 Linux | `codeproject/ai-server:arm64` | General ARM64 image. |
| Raspberry Pi ARM64 | `codeproject/ai-server:rpi64` | Pi-focused image; use the CodeProject.AI dashboard and docs to confirm the modules your model needs are available. |

CodeProject.AI's Raspberry Pi image is resource-focused. If your custom plate model depends on a particular module — for example, a YOLO-based custom vision endpoint — verify that module and its dependencies are installed and running before connecting this app.

---

## Option A — x64 Linux / Mini PC with Docker

Install Docker using the method appropriate for your Linux distribution, then run:

```bash
sudo mkdir -p /opt/codeproject-ai/{data,modules}

docker run --name CodeProject.AI -d \
  --restart unless-stopped \
  -p 32168:32168 \
  -v /opt/codeproject-ai/data:/etc/codeproject/ai \
  -v /opt/codeproject-ai/modules:/app/modules \
  codeproject/ai-server
```

Open the server dashboard:

```text
http://YOUR-CPAI-HOST:32168
```

Check that it is running:

```bash
curl -fsS http://127.0.0.1:32168/v1/status/ping
curl -fsS http://127.0.0.1:32168/v1/status
```

Stop, start, or view logs:

```bash
docker stop CodeProject.AI
docker start CodeProject.AI
docker logs -f CodeProject.AI
```

---

## Option B — Raspberry Pi / ARM64 Docker

For a Raspberry Pi running a 64-bit operating system:

```bash
sudo mkdir -p /opt/codeproject-ai/{data,modules}

docker run --name CodeProject.AI -d \
  --restart unless-stopped \
  -p 32168:32168 \
  -v /opt/codeproject-ai/data:/etc/codeproject/ai \
  -v /opt/codeproject-ai/modules:/app/modules \
  --privileged \
  -v /dev/bus/usb:/dev/bus/usb \
  codeproject/ai-server:rpi64
```

The USB flags allow CodeProject.AI to access supported USB accelerators when applicable. They are not a substitute for installing or configuring the custom model your setup requires.

For a general ARM64 deployment without the Pi-specific image, use:

```bash
docker run --name CodeProject.AI -d \
  --restart unless-stopped \
  -p 32168:32168 \
  -v /opt/codeproject-ai/data:/etc/codeproject/ai \
  -v /opt/codeproject-ai/modules:/app/modules \
  codeproject/ai-server:arm64
```

---

## Option C — Docker Compose

Example Compose files are included in this repository:

```text
extras/codeproject-ai/docker-compose.x86.yml
extras/codeproject-ai/docker-compose.rpi64.yml
```

For x64:

```bash
cd extras/codeproject-ai
docker compose -f docker-compose.x86.yml up -d
```

For Raspberry Pi ARM64:

```bash
cd extras/codeproject-ai
docker compose -f docker-compose.rpi64.yml up -d
```

---

## Configure the Plate-Detection Endpoint

This app calls the endpoint built from these two settings:

```json
{
  "cpai_base_url": "http://127.0.0.1:32168",
  "cpai_model": "license-plate"
}
```

That becomes:

```text
http://127.0.0.1:32168/v1/vision/custom/license-plate
```

The app sends the image in a multipart form field named `image`, along with `min_confidence`.

Test a configured endpoint with a local JPEG:

```bash
curl -sS -X POST \
  -F image=@test-frame.jpg \
  -F min_confidence=0.05 \
  http://127.0.0.1:32168/v1/vision/custom/license-plate
```

A working endpoint should return JSON containing a predictions or results array.

> **Custom model requirement:** The model name `license-plate` is a configuration value, not a built-in guarantee. Install and configure a compatible model in CodeProject.AI, then use the exact endpoint/model name it exposes. This repository does not redistribute model weights.

---

## Connect a Separate AI Host

When CodeProject.AI runs on another device, set the IP address or DNS name in the scanner configuration:

```json
{
  "scanner": {
    "cpai_base_url": "http://192.168.1.50:32168",
    "cpai_model": "license-plate"
  }
}
```

Then restart the scanner:

```bash
sudo systemctl restart teslacam-plate-scanner.service
```

Keep the service on a trusted LAN. Do not expose CodeProject.AI directly to the public internet.

---

## Optional Object Detection and OCR

The project can also call a configurable general object endpoint and a configurable OCR endpoint.

```json
{
  "scanner": {
    "object_detection_enabled": true,
    "object_endpoint": "/v1/vision/detection",
    "ocr_cpai_enabled": false,
    "ocr_cpai_endpoint": "/v1/vision/ocr"
  }
}
```

Endpoint availability depends on the modules configured in your CodeProject.AI instance. Local Tesseract OCR can be enabled separately through this project's `ocr_enabled` setting.

---

## Official References

- [CodeProject.AI Server repository](https://github.com/codeproject/CodeProject.AI-Server)
- [Official Docker instructions](https://codeproject.github.io/codeproject.ai/install/running_in_docker.html)
- [CodeProject.AI Server dashboard](http://localhost:32168)

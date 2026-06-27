# Privacy and data handling

This project processes video captured by Tesla vehicles and can store cropped
license plates, OCR text, object snapshots, timestamps, and camera metadata.
Those records can be sensitive.

The public repository deliberately excludes all of the following:

- SQLite databases and WAL/SHM files
- Runtime `config.json`
- Generated plate crops, annotated frames, and object frames
- Source TeslaCam clips
- Machine-specific file paths and local settings

Before sharing any export or screenshot, remove plate text, identifiable faces,
addresses, exact travel times, and other details that are not necessary for the
technical discussion. Follow the laws that apply where you capture, process,
or share footage.

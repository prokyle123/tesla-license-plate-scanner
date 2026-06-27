# Contributing

Thanks for improving TeslaCam Plate Dashboard.

1. Create a branch from `main`.
2. Keep pull requests focused and explain the test path.
3. Do not commit clips, captured frames, plate crops, SQLite databases,
   `config.json`, IP addresses, tokens, or other private data.
4. Run the lightweight check before opening a pull request:

```bash
python3 -m compileall -q app scanner tools
```

For UI changes, include a scrubbed screenshot or a short description of the
view you changed. Real plate numbers and personally identifying capture data
must be redacted.

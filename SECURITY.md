# Security policy

## Supported versions

Security fixes are applied to the current `main` branch.

## Reporting a vulnerability

Do not open a public issue for a security problem. Use GitHub's private
security-reporting option for this repository, or contact the maintainer
privately with a clear reproduction path and the affected version.

## Local-network safety

The dashboard is intended for a trusted local network. It has no built-in
login, TLS termination, or multi-user authorization layer. Do not expose port
`5057` directly to the public internet. Put it behind an authenticated reverse
proxy or VPN when remote access is needed.

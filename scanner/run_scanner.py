import argparse
from .scanner import scan_loop

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None, help="Path to config.json")
    args = ap.parse_args()
    scan_loop(args.config)

if __name__ == "__main__":
    main()

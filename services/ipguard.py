import sys
from pathlib import Path
import requests


def readAllowedIps(ipFile: Path):
    if not ipFile.exists():
        return None
    ips = []
    for line in ipFile.read_text(encoding="utf-8").splitlines():
        v = line.strip()
        if v and not v.startswith("#"):
            ips.append(v)
    return ips


def publicIp(timeout: int) -> str:
    r = requests.get("https://api.ipify.org", timeout=timeout)
    r.raise_for_status()
    return r.text.strip()


def vpnCheckOrDie(ipFile: Path, timeout: int) -> None:
    allowed = readAllowedIps(ipFile)

    # si ip.txt absent → non bloquant
    if allowed is None:
        try:
            ip = publicIp(timeout)
            print("IP", ip, "(ip.txt absent)")
        except Exception:
            print("IP_CHECK_FAIL")
        return

    if not allowed:
        print("ip.txt vide. Stop par sécurité.")
        sys.exit(1)

    try:
        ip = publicIp(timeout)
    except Exception as e:
        print("IP_CHECK_FAIL", type(e).__name__)
        sys.exit(1)

    if ip in allowed:
        print("IP", ip, "OK (match ip.txt)")
        return

    print("IP", ip, "NOT_ALLOWED")
    print("ip.txt contient:", ",".join(allowed))
    print("Stop: IP VPN a changé.")
    sys.exit(1)


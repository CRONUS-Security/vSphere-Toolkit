#!/usr/bin/env python3

import argparse
import ssl
from typing import Iterable, Optional

from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim


def build_ssl_context(skip_verify: bool) -> ssl.SSLContext:
    """Create an SSL context that optionally skips certificate verification."""
    if skip_verify:
        return ssl._create_unverified_context()
    context = ssl.create_default_context()
    context.check_hostname = True
    return context


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List all virtual machines on vCenter/ESXi",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-H", "--host", required=True, help="vCenter/ESXi host IP or FQDN")
    parser.add_argument("-u", "--user", required=True, help="Username (e.g., administrator@telecore.ad)")
    parser.add_argument("-p", "--password", help="Password (prompt if omitted)")
    parser.add_argument("--port", type=int, default=443, help="API port")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Skip SSL verification (useful for self-signed certs)",
    )
    return parser.parse_args()


def connect_vsphere(host: str, user: str, password: str, port: int, insecure: bool) -> vim.ServiceInstance:
    context = build_ssl_context(skip_verify=insecure)
    return SmartConnect(host=host, user=user, pwd=password, port=port, sslContext=context)


def iter_vms(content) -> Iterable[vim.VirtualMachine]:
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    try:
        for vm in view.view:
            yield vm
    finally:
        view.Destroy()


def vm_primary_ip(vm: vim.VirtualMachine) -> str:
    guest = getattr(vm, "guest", None)
    if not guest:
        return "N/A"
    if getattr(guest, "ipAddress", None):
        return guest.ipAddress
    net = getattr(guest, "net", [])
    if net:
        ip_config = getattr(net[0], "ipConfig", None)
        addresses = getattr(ip_config, "ipAddress", []) if ip_config else []
        if addresses:
            return addresses[0].ipAddress
    return "N/A"


def print_vm_info(vm: vim.VirtualMachine) -> None:
    config = getattr(vm, "config", None)
    guest = getattr(vm, "guest", None)
    print(f"Name: {vm.name}")
    print(f"  Power State: {vm.runtime.powerState}")
    print(f"  Guest OS: {getattr(config, 'guestFullName', None) or 'Unknown'}")
    print(f"  Tools Status: {getattr(guest, 'toolsStatus', None) or 'Unknown'}")
    print(f"  IP Address: {vm_primary_ip(vm)}")
    print(f"  Notes: {getattr(config, 'annotation', None) or 'None'}\n")


def main() -> None:
    args = parse_args()

    import getpass

    password = args.password or getpass.getpass("Enter password: ")

    si = None
    try:
        print(f"Connecting to {args.host}:{args.port} (verify_ssl={'no' if args.insecure else 'yes'})...")
        si = connect_vsphere(args.host, args.user, password, args.port, args.insecure)
        content = si.RetrieveContent()
        print("\n=== Virtual Machines ===\n")
        for vm in iter_vms(content):
            print_vm_info(vm)
        print("Disconnected successfully.")
    except Exception as exc:  # pragma: no cover - pyvmomi specific paths
        print(f"Error: {exc}")
    finally:
        if si:
            Disconnect(si)


if __name__ == "__main__":
    main()
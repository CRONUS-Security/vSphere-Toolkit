#!/usr/bin/env python3

from __future__ import annotations

import ssl
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Optional

import typer
from core.outputter import ResultOutputter
from core.proxy import ProxyConfig, parse_proxy, use_proxy
from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim


app = typer.Typer(help="vSphere 信息探测与资产枚举工具")


class OutputFormat(str, Enum):
	csv = "csv"
	json = "json"
	txt = "txt"


def build_ssl_context(skip_verify: bool) -> ssl.SSLContext:
	if skip_verify:
		return ssl._create_unverified_context()
	context = ssl.create_default_context()
	context.check_hostname = True
	return context


def connect_vsphere(host: str, user: str, password: str, port: int, insecure: bool):
	ssl_context = build_ssl_context(skip_verify=insecure)
	return SmartConnect(host=host, user=user, pwd=password, port=port, sslContext=ssl_context)


def iter_objects(content: Any, vim_type: type) -> Iterable[Any]:
	view = content.viewManager.CreateContainerView(content.rootFolder, [vim_type], True)
	try:
		for obj in view.view:
			yield obj
	finally:
		view.Destroy()


def safe_get(value: Any, attr: str, default: Any = None) -> Any:
	if value is None:
		return default
	return getattr(value, attr, default)


def host_type(content: Any) -> str:
	about = safe_get(content, "about")
	api_type = safe_get(about, "apiType", "Unknown")
	if api_type == "VirtualCenter":
		return "vCenter"
	if api_type == "HostAgent":
		return "ESXi"
	return f"Unknown({api_type})"


def collect_vm_rows(content: Any) -> list[dict[str, Any]]:
	vm_rows: list[dict[str, Any]] = []

	for vm in iter_objects(content, vim.VirtualMachine):
		config = safe_get(vm, "config")
		runtime = safe_get(vm, "runtime")
		summary = safe_get(vm, "summary")
		guest = safe_get(vm, "guest")

		disks = []
		nics = []

		hardware = safe_get(config, "hardware")
		devices = safe_get(hardware, "device", []) or []
		for dev in devices:
			if isinstance(dev, vim.vm.device.VirtualDisk):
				disks.append(
					{
						"label": safe_get(safe_get(dev, "deviceInfo"), "label"),
						"capacity_kb": safe_get(dev, "capacityInKB"),
						"capacity_bytes": safe_get(dev, "capacityInBytes"),
						"unit_number": safe_get(dev, "unitNumber"),
						"controller_key": safe_get(dev, "controllerKey"),
						"backing_file": safe_get(safe_get(dev, "backing"), "fileName"),
						"thin_provisioned": safe_get(safe_get(dev, "backing"), "thinProvisioned"),
					}
				)
			elif isinstance(dev, vim.vm.device.VirtualEthernetCard):
				nics.append(
					{
						"label": safe_get(safe_get(dev, "deviceInfo"), "label"),
						"mac": safe_get(dev, "macAddress"),
						"connected": safe_get(safe_get(dev, "connectable"), "connected"),
						"start_connected": safe_get(safe_get(dev, "connectable"), "startConnected"),
						"network": safe_get(dev, "deviceInfo") and safe_get(dev.deviceInfo, "summary"),
						"key": safe_get(dev, "key"),
					}
				)

		guest_nics = []
		for net in safe_get(guest, "net", []) or []:
			ip_addresses = []
			ip_config = safe_get(net, "ipConfig")
			for ip_item in safe_get(ip_config, "ipAddress", []) or []:
				ip_addresses.append(safe_get(ip_item, "ipAddress"))
			guest_nics.append(
				{
					"network": safe_get(net, "network"),
					"mac": safe_get(net, "macAddress"),
					"connected": safe_get(net, "connected"),
					"ip_addresses": ip_addresses,
				}
			)

		host_obj = safe_get(runtime, "host")

		vm_rows.append(
			{
				"vm_name": safe_get(vm, "name"),
				"vm_moid": safe_get(vm, "_moId"),
				"instance_uuid": safe_get(config, "instanceUuid"),
				"bios_uuid": safe_get(config, "uuid"),
				"power_state": safe_get(runtime, "powerState"),
				"connection_state": safe_get(runtime, "connectionState"),
				"boot_time": safe_get(runtime, "bootTime"),
				"suspend_time": safe_get(runtime, "suspendTime"),
				"guest_state": safe_get(guest, "guestState"),
				"guest_os_full_name": safe_get(config, "guestFullName"),
				"guest_os_id": safe_get(config, "guestId"),
				"guest_host_name": safe_get(guest, "hostName"),
				"guest_primary_ip": safe_get(guest, "ipAddress"),
				"tools_status": safe_get(guest, "toolsStatus"),
				"tools_running_status": safe_get(guest, "toolsRunningStatus"),
				"tools_version": safe_get(guest, "toolsVersion"),
				"cpu_count": safe_get(hardware, "numCPU"),
				"cores_per_socket": safe_get(hardware, "numCoresPerSocket"),
				"memory_mb": safe_get(hardware, "memoryMB"),
				"memory_size_mb": safe_get(safe_get(summary, "config"), "memorySizeMB"),
				"cpu_reservation_mhz": safe_get(safe_get(config, "cpuAllocation"), "reservation"),
				"memory_reservation_mb": safe_get(safe_get(config, "memoryAllocation"), "reservation"),
				"host_name": safe_get(host_obj, "name"),
				"resource_pool": safe_get(safe_get(vm, "resourcePool"), "name"),
				"folder": safe_get(safe_get(vm, "parent"), "name"),
				"annotation": safe_get(config, "annotation"),
				"disks": disks,
				"nics": nics,
				"guest_nics": guest_nics,
			}
		)

	return vm_rows


def collect_datastore_rows(content: Any) -> list[dict[str, Any]]:
	rows: list[dict[str, Any]] = []
	for ds in iter_objects(content, vim.Datastore):
		summary = safe_get(ds, "summary")
		rows.append(
			{
				"name": safe_get(ds, "name"),
				"moid": safe_get(ds, "_moId"),
				"url": safe_get(summary, "url"),
				"type": safe_get(summary, "type"),
				"capacity": safe_get(summary, "capacity"),
				"free_space": safe_get(summary, "freeSpace"),
				"accessible": safe_get(summary, "accessible"),
				"multiple_host_access": safe_get(summary, "multipleHostAccess"),
				"uncommitted": safe_get(summary, "uncommitted"),
			}
		)
	return rows


def collect_network_rows(content: Any) -> list[dict[str, Any]]:
	rows: list[dict[str, Any]] = []
	for net in iter_objects(content, vim.Network):
		summary = safe_get(net, "summary")
		rows.append(
			{
				"name": safe_get(net, "name"),
				"moid": safe_get(net, "_moId"),
				"overall_status": safe_get(net, "overallStatus"),
				"accessible": safe_get(summary, "accessible"),
				"ip_pool_name": safe_get(summary, "ipPoolName"),
				"datacenter": safe_get(safe_get(net, "parent"), "name"),
				"vm_count": len(safe_get(net, "vm", []) or []),
				"host_count": len(safe_get(net, "host", []) or []),
			}
		)
	return rows


def collect_host_rows(content: Any) -> list[dict[str, Any]]:
	rows: list[dict[str, Any]] = []
	for host in iter_objects(content, vim.HostSystem):
		summary = safe_get(host, "summary")
		hardware = safe_get(summary, "hardware")
		quick_stats = safe_get(summary, "quickStats")
		config = safe_get(host, "config")
		network = safe_get(config, "network")
		runtime = safe_get(summary, "runtime")

		pnic_rows = []
		for pnic in safe_get(network, "pnic", []) or []:
			pnic_rows.append(
				{
					"device": safe_get(pnic, "device"),
					"mac": safe_get(pnic, "mac"),
					"driver": safe_get(pnic, "driver"),
					"link_speed_mb": safe_get(safe_get(pnic, "linkSpeed"), "speedMb"),
					"duplex": safe_get(safe_get(pnic, "linkSpeed"), "duplex"),
					"ip": safe_get(safe_get(pnic, "spec"), "ip") and safe_get(pnic.spec.ip, "ipAddress"),
					"subnet": safe_get(safe_get(pnic, "spec"), "ip") and safe_get(pnic.spec.ip, "subnetMask"),
					"mtu": safe_get(pnic, "mtu"),
				}
			)

		vmkernel_rows = []
		for vnic in safe_get(network, "vnic", []) or []:
			ip_cfg = safe_get(vnic, "spec") and safe_get(vnic.spec, "ip")
			vmkernel_rows.append(
				{
					"device": safe_get(vnic, "device"),
					"portgroup": safe_get(vnic, "portgroup"),
					"mac": safe_get(vnic, "mac"),
					"mtu": safe_get(vnic, "spec") and safe_get(vnic.spec, "mtu"),
					"ip": safe_get(ip_cfg, "ipAddress"),
					"subnet": safe_get(ip_cfg, "subnetMask"),
					"dhcp": safe_get(ip_cfg, "dhcp"),
				}
			)

		vcenter_ip = safe_get(runtime, "managementServerIp")

		rows.append(
			{
				"name": safe_get(host, "name"),
				"moid": safe_get(host, "_moId"),
				"connection_state": safe_get(runtime, "connectionState"),
				"power_state": safe_get(runtime, "powerState"),
				"in_maintenance_mode": safe_get(runtime, "inMaintenanceMode"),
				"vcenter_ip": vcenter_ip,
				"managed_by_vcenter": bool(vcenter_ip),
				"product_name": safe_get(safe_get(config, "product"), "name"),
				"product_full_name": safe_get(safe_get(config, "product"), "fullName"),
				"product_version": safe_get(safe_get(config, "product"), "version"),
				"build": safe_get(safe_get(config, "product"), "build"),
				"vendor": safe_get(hardware, "vendor"),
				"model": safe_get(hardware, "model"),
				"uuid": safe_get(hardware, "uuid"),
				"cpu_model": safe_get(hardware, "cpuModel"),
				"cpu_cores": safe_get(hardware, "numCpuCores"),
				"cpu_threads": safe_get(hardware, "numCpuThreads"),
				"memory_size_bytes": safe_get(hardware, "memorySize"),
				"overall_cpu_usage_mhz": safe_get(quick_stats, "overallCpuUsage"),
				"overall_memory_usage_mb": safe_get(quick_stats, "overallMemoryUsage"),
				"uptime_seconds": safe_get(quick_stats, "uptime"),
				"dns_host_name": safe_get(safe_get(network, "dnsConfig"), "hostName"),
				"dns_domain_name": safe_get(safe_get(network, "dnsConfig"), "domainName"),
				"dns_servers": safe_get(safe_get(network, "dnsConfig"), "address"),
				"vm_count": len(safe_get(host, "vm", []) or []),
				"datastore_count": len(safe_get(host, "datastore", []) or []),
				"network_count": len(safe_get(host, "network", []) or []),
				"pnic": pnic_rows,
				"vmkernel_nic": vmkernel_rows,
			}
		)
	return rows


def collect_esxi_user_rows(content: Any) -> list[dict[str, Any]]:
	rows: list[dict[str, Any]] = []
	for host in iter_objects(content, vim.HostSystem):
		account_manager = safe_get(safe_get(host, "configManager"), "accountManager")
		if not account_manager:
			continue

		try:
			groups = account_manager.QueryUserGroups(searchStr="", exactMatch=False, findUsers=True, findGroups=False)
		except Exception as exc:
			rows.append(
				{
					"host_name": safe_get(host, "name"),
					"error": f"QueryUserGroups failed: {exc}",
				}
			)
			continue

		for group in groups or []:
			for user in safe_get(group, "users", []) or []:
				rows.append(
					{
						"host_name": safe_get(host, "name"),
						"group": safe_get(group, "group"),
						"user": safe_get(user, "key"),
						"full_name": safe_get(user, "fullName"),
						"description": safe_get(user, "description"),
					}
				)

	return rows


def collect_target_rows(content: Any) -> list[dict[str, Any]]:
	about = safe_get(content, "about")
	setting = safe_get(content, "setting")
	license_manager = safe_get(content, "licenseManager")
	lic = safe_get(license_manager, "licenses", [])

	return [
		{
			"api_type": safe_get(about, "apiType"),
			"full_name": safe_get(about, "fullName"),
			"name": safe_get(about, "name"),
			"version": safe_get(about, "version"),
			"build": safe_get(about, "build"),
			"vendor": safe_get(about, "vendor"),
			"os_type": safe_get(about, "osType"),
			"instance_uuid": safe_get(about, "instanceUuid"),
			"locale_version": safe_get(about, "localeVersion"),
			"api_version": safe_get(about, "apiVersion"),
			"setting_count": len(safe_get(setting, "setting", []) or []),
			"license_count": len(lic or []),
		}
	]


def build_tables(content: Any) -> dict[str, list[dict[str, Any]]]:
	return {
		"target_info": collect_target_rows(content),
		"hosts": collect_host_rows(content),
		"virtual_machines": collect_vm_rows(content),
		"datastores": collect_datastore_rows(content),
		"networks": collect_network_rows(content),
		"users": collect_esxi_user_rows(content),
	}


@app.callback()
def global_options(
	ctx: typer.Context,
	output_format: OutputFormat = typer.Option(
		OutputFormat.csv,
		"--format",
		"-f",
		help="全局输出格式，可选: csv/json/txt",
		case_sensitive=False,
	),
	proxy: Optional[str] = typer.Option(
		None,
		"--proxy",
		help="全局代理地址，支持 http:// https:// socks5:// ，示例: socks5://127.0.0.1:1080",
	),
) -> None:
	ctx.ensure_object(dict)
	ctx.obj["output_format"] = output_format.value
	try:
		ctx.obj["proxy"] = parse_proxy(proxy)
	except ValueError as exc:
		raise typer.BadParameter(str(exc), param_hint="--proxy") from exc


@app.command(help="探测目标是否为 vSphere，并识别 vCenter / ESXi")
def probe(
	ctx: typer.Context,
	host: str = typer.Option(..., "--host", "-H", help="vCenter/ESXi 地址"),
	user: str = typer.Option(..., "--user", "-u", help="用户名"),
	password: Optional[str] = typer.Option(None, "--password", "-p", prompt=True, hide_input=True),
	port: int = typer.Option(443, "--port", help="API 端口"),
	insecure: bool = typer.Option(False, "--insecure", help="跳过 SSL 校验（自签证书常用）"),
	output_dir: Path = typer.Option(Path("./vSphere-info"), "--output-dir", help="输出目录"),
) -> None:
	proxy_config: Optional[ProxyConfig] = (ctx.obj or {}).get("proxy")

	try:
		with use_proxy(proxy_config) as active_proxy:
			if active_proxy:
				typer.echo(f"[*] 已启用代理: {active_proxy.display_url}")

			si = None
			try:
				si = connect_vsphere(host=host, user=user, password=password or "", port=port, insecure=insecure)
				content = si.RetrieveContent()
				target_kind = host_type(content)
				about = safe_get(content, "about")
				selected_format = (ctx.obj or {}).get("output_format", OutputFormat.csv.value)

				probe_row = {
					"target_type": target_kind,
					"api_type": safe_get(about, "apiType", "Unknown"),
					"product_full_name": safe_get(about, "fullName", "Unknown"),
					"product_name": safe_get(about, "name", "Unknown"),
					"version": safe_get(about, "version", "Unknown"),
					"build": safe_get(about, "build", "Unknown"),
					"instance_uuid": safe_get(about, "instanceUuid", "Unknown"),
					"vendor": safe_get(about, "vendor", "Unknown"),
					"os_type": safe_get(about, "osType", "Unknown"),
				}

				outputter = ResultOutputter(output_dir=output_dir, output_format=selected_format)
				output_path = outputter.write_table("probe_result", [probe_row])

				typer.secho("[+] 探测成功", fg=typer.colors.GREEN)
				typer.echo(f"目标类型: {target_kind}")
				typer.echo(f"产品全称: {safe_get(about, 'fullName', 'Unknown')}")
				typer.echo(f"版本: {safe_get(about, 'version', 'Unknown')}  Build: {safe_get(about, 'build', 'Unknown')}")
				typer.echo(f"实例 UUID: {safe_get(about, 'instanceUuid', 'Unknown')}")
				typer.echo(f"输出文件: {output_path.resolve()}")
			finally:
				if si:
					Disconnect(si)
	except Exception as exc:
		typer.secho(f"[-] 探测失败: {exc}", fg=typer.colors.RED)
		raise typer.Exit(code=1)


@app.command(help="采集 vSphere 资产信息并按全局格式输出到 ./vSphere-info")
def collect(
	ctx: typer.Context,
	host: str = typer.Option(..., "--host", "-H", help="vCenter/ESXi 地址"),
	user: str = typer.Option(..., "--user", "-u", help="用户名"),
	password: Optional[str] = typer.Option(None, "--password", "-p", prompt=True, hide_input=True),
	port: int = typer.Option(443, "--port", help="API 端口"),
	insecure: bool = typer.Option(False, "--insecure", help="跳过 SSL 校验（自签证书常用）"),
	output_dir: Path = typer.Option(Path("./vSphere-info"), "--output-dir", help="输出目录"),
) -> None:
	proxy_config: Optional[ProxyConfig] = (ctx.obj or {}).get("proxy")

	try:
		with use_proxy(proxy_config) as active_proxy:
			if active_proxy:
				typer.echo(f"[*] 已启用代理: {active_proxy.display_url}")

			si = None
			try:
				typer.echo(f"[*] 正在连接 {host}:{port} ...")
				si = connect_vsphere(host=host, user=user, password=password or "", port=port, insecure=insecure)
				content = si.RetrieveContent()
				target_kind = host_type(content)
				selected_format = (ctx.obj or {}).get("output_format", OutputFormat.csv.value)

				typer.secho(f"[+] 已连接，目标类型: {target_kind}", fg=typer.colors.GREEN)
				typer.echo(f"[*] 正在采集并导出 {selected_format.upper()}，请稍候 ...")

				outputter = ResultOutputter(output_dir=output_dir, output_format=selected_format)
				stats = outputter.export_tables(build_tables(content))
				typer.secho(f"[+] 导出完成: {output_dir.resolve()}", fg=typer.colors.GREEN)
				for table_name, count in stats.items():
					typer.echo(f"  - {table_name}: {count} 行")
			finally:
				if si:
					Disconnect(si)
	except Exception as exc:
		typer.secho(f"[-] 采集失败: {exc}", fg=typer.colors.RED)
		raise typer.Exit(code=1)


if __name__ == "__main__":
	app()

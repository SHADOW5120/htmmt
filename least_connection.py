#!/usr/bin/env python3

import requests
import json
import time

# Các thông tin cấu hình
device_url = "http://localhost:8080/wm/device/"
link_url = "http://localhost:8080/wm/topology/links/json"
flow_url = "http://127.0.0.1:8080/wm/staticflowpusher/json"
statistics_url = "http://localhost:8080/wm/statistics/flow/{}/json"

# Danh sách máy chủ và trạng thái kết nối
servers = {}  # Mỗi máy chủ sẽ lưu số kết nối hiện tại
host_to_server = {}  # Bản đồ từ host đến server

# Lấy thông tin các thiết bị (device) từ API
def get_device_info():
    response = requests.get(device_url)
    if response.ok:
        devices = response.json()
        for device_group in devices.values():
            for device in device_group:
                if device['ipv4']:
                    ip = device['ipv4'][0]
                    mac = device['mac'][0]
                    attachment = device['attachmentPoint'][0]
                    switch = attachment['switch']
                    port = str(attachment['port'])
                    servers[ip] = 0  # Khởi tạo mỗi server có 0 kết nối
                    host_to_server[ip] = {"mac": mac, "switch": switch, "port": port}
    else:
        print(f"Failed to fetch devices: {response.status_code}")
        response.raise_for_status()

# Lấy thông tin flow từ switch qua API để tính số kết nối đang hoạt động
def update_server_connections():
    for server_ip, server_data in host_to_server.items():
        switch_id = server_data['switch']
        response = requests.get(statistics_url.format(switch_id))
        if response.ok:
            flows = response.json()
            active_flows = 0
            for flow_entry in flows[0]['flows']:
                if flow_entry['match'].get('ipv4_dst') == server_ip:
                    active_flows += 1
            servers[server_ip] = active_flows
        else:
            print(f"Failed to fetch flows for switch {switch_id}: {response.status_code}")

# Hàm chọn máy chủ có ít kết nối nhất từ các host còn lại
def least_connection(src_ip):
    # Tạo danh sách các máy chủ (host khác với src_ip)
    remaining_servers = {ip: conn for ip, conn in servers.items() if ip != src_ip}
    
    # Chọn máy chủ có ít kết nối nhất
    selected_server = min(remaining_servers, key=remaining_servers.get)
    return selected_server

# Đẩy flow rule đến switch
def push_flow_rule(src_ip, dst_ip, src_mac, dst_mac, in_port, out_port, switch):
    flow_data = {
        'switch': switch,
        'name': f"flow_{src_ip}_to_{dst_ip}",
        'cookie': "0",
        'priority': "32768",
        'in_port': in_port,
        'eth_type': "0x0800",
        'ipv4_src': src_ip,
        'ipv4_dst': dst_ip,
        'eth_src': src_mac,
        'eth_dst': dst_mac,
        'active': "true",
        'actions': f"output={out_port}"
    }
    response = requests.post(flow_url, data=json.dumps(flow_data))
    if response.ok:
        print(f"Flow rule pushed successfully for {src_ip} -> {dst_ip}")
    else:
        print(f"Failed to push flow rule: {response.status_code}")

# Thực hiện cân bằng tải Least Connection
def load_balance_least_connection(src_ip):
    selected_server = least_connection(src_ip)
    server_data = host_to_server[selected_server]

    # Thông tin về switch, cổng kết nối host -> switch và switch -> server
    switch = server_data['switch']
    out_port = server_data['port']
    in_port = host_to_server[src_ip]['port']
    src_mac = host_to_server[src_ip]['mac']
    dst_mac = server_data['mac']

    # Gửi flow từ host nguồn đến máy chủ
    push_flow_rule(src_ip, selected_server, src_mac, dst_mac, in_port, out_port, switch)

# Khởi tạo hệ thống
get_device_info()

# Vòng lặp giám sát và thực hiện cân bằng tải
try:
    while True:
        print("\nUpdating server connections...")
        update_server_connections()
        print("Current server connections:", servers)

        print("\nEnter Source Host IP:")
        src_ip = input().strip()
        if src_ip not in host_to_server:
            print("Invalid Host IP. Try again.")
            continue

        load_balance_least_connection(src_ip)
        time.sleep(20)  # Chu kỳ thực hiện (giả lập)
except KeyboardInterrupt:
    print("\nExiting load balancer...")

#!/usr/bin/env python3

import requests
import json
import networkx as nx
from subprocess import Popen, PIPE
import time

# Hàm thực hiện gọi REST API và xử lý phản hồi JSON dựa trên lựa chọn
def get_response(url, choice):
    print("Link API " + choice)
    print(url)
    response = requests.get(url)
    if response.ok:
        data = response.json()
        if choice == "deviceInfo":
            device_information(data)
        elif choice == "findSwitchLinks":
            find_switch_links(data, switch[h2])
        elif choice == "linkTX":
            link_tx(data, port_key)
    else:
        response.raise_for_status()

# Phân tích dữ liệu JSON để tìm switch kết nối với một thiết bị cụ thể (ví dụ: H4)
def device_information(data):
    global switch, device_mac, host_ports
    switch_dpid = ""
    for devices in data.values():
        for device in devices:
            if device['ipv4']:
                ip = device['ipv4'][0]
                mac = device['mac'][0]
                device_mac[ip] = mac
                for attachment in device['attachmentPoint']:
                    if 'switch' in attachment:
                        switch_dpid = attachment['switch']
                        switch[ip] = switch_dpid
                    elif 'port' in attachment:
                        port_number = attachment['port']
                        switch_short = switch_dpid.split(":")[7]
                        host_ports[f"{ip}::{switch_short}"] = str(port_number)

# Tìm các liên kết cho một switch cụ thể và cập nhật đồ thị để tính toán đường dẫn
def find_switch_links(data, s):
    global switch_links, link_ports, G
    links = []
    for link in data:
        src, dst = link['src-switch'], link['dst-switch']
        src_port, dst_port = str(link['src-port']), str(link['dst-port'])
        src_temp, dst_temp = src.split(":")[7], dst.split(":")[7]
        G.add_edge(int(src_temp, 16), int(dst_temp, 16))
        port_src_to_dst, port_dst_to_src = f"{src_port}::{dst_port}", f"{dst_port}::{src_port}"
        temp_src_to_dst, temp_dst_to_src = f"{src_temp}::{dst_temp}", f"{dst_temp}::{src_temp}"
        link_ports[temp_src_to_dst] = port_src_to_dst
        link_ports[temp_dst_to_src] = port_dst_to_src
        if src == s:
            links.append(dst)
        elif dst == s:
            links.append(src)
    switch_id = s.split(":")[7]
    switch_links[switch_id] = links

# Tính toán đường đi giữa các switch
def find_switch_route():
    global path
    src, dst = int(switch[h2].split(":", 7)[7], 16), int(switch[h1].split(":", 7)[7], 16)
    print(src)
    print(dst)
    for current_path in nx.all_shortest_paths(G, source=src, target=dst):
        path_key = "::".join(f"{int(node):02x}" for node in current_path)
        node_list = [f"00:00:00:00:00:00:00:{node:02x}" for node in current_path]
        path[path_key] = node_list

# Tính toán chi phí liên kết TX (Transmission)
def link_tx(data, key):
    global cost
    port = link_ports[key].split("::")[0]
    for i in data:
        if i['port'] == port:
            cost += int(i['bits-per-second-tx'])

# Tính toán chi phí liên kết trên một đường đi
def get_link_cost():
    global port_key, cost
    for key in path:
        src_short_id = switch[h2].split(":")[7]
        mid = path[key][1].split(":")[7]
        for link in path[key]:
            temp = link.split(":")[7]
            if src_short_id != temp:
                port_key = f"{src_short_id}::{temp}"
                port = link_ports[port_key].split("::")[0]
                stats = f"http://localhost:8080/wm/statistics/bandwidth/{src_short_id}/{port}/json"
                get_response(stats, "linkTX")
                src_short_id = temp
        port_key = f"{switch[h2].split(':')[7]}::{mid}::{switch[h1].split(':')[7]}"
        final_link_tx[port_key] = cost
        cost = 0

# Thực thi một lệnh hệ thống
def system_command(cmd):
    process = Popen(cmd, stdout=PIPE, stderr=PIPE, shell=True)
    stdout, _ = process.communicate()
    print("\n***", stdout.decode(), "\n")

# Đẩy một quy tắc dòng chảy (flow rule) vào một switch
def flow_rule(node, flow_count, in_port, out_port, flow_url):
    flow_data = {
        'switch': f"00:00:00:00:00:00:00:{node}",
        'name': f"flow{flow_count}",
        'cookie': "0",
        'priority': "32768",
        'in_port': in_port,
        'eth_type': "0x0800",
        'ipv4_src': h2,
        'ipv4_dst': h1,
        'eth_src': device_mac[h2],
        'eth_dst': device_mac[h1],
        'active': "true",
        'actions': f"output={out_port}"
    }
    cmd = f"curl -X POST -d '{json.dumps(flow_data)}' {flow_url}"
    system_command(cmd)

    flow_count += 1
    flow_data = {
        'switch': f"00:00:00:00:00:00:00:{node}",
        'name': f"flow{flow_count}",
        'cookie': "0",
        'priority': "32768",
        'in_port': out_port,
        'eth_type': "0x0800",
        'ipv4_src': h1,
        'ipv4_dst': h2,
        'eth_src': device_mac[h1],
        'eth_dst': device_mac[h2],
        'active': "true",
        'actions': f"output={in_port}"
    }
    cmd = f"curl -X POST -d '{json.dumps(flow_data)}' {flow_url}"
    system_command(cmd)

# Thêm các dòng chảy (flows) dựa trên đường đi đã tính toán và chi phí liên kế
def add_flow():
    print("----------TEAM 10----------")
    flow_count = 1
    static_flow_url = "http://127.0.0.1:8080/wm/staticflowpusher/json"
    shortest_path = min(final_link_tx, key=final_link_tx.get)
    print("\n\nShortest Path:", shortest_path)
    current_node = shortest_path.split("::", 2)[0]
    next_node = shortest_path.split("::")[1]
    port = link_ports[f"{current_node}::{next_node}"]
    out_port = port.split("::")[0]
    in_port = host_ports[f"{h2}::{switch[h2].split(':')[7]}"]
    flow_rule(current_node, flow_count, in_port, out_port, static_flow_url)
    flow_count += 2
    best_path = path[shortest_path]
    previous_node = current_node
    for i, current_node in enumerate(best_path):
        if previous_node == best_path[i].split(":")[7]:
            continue
        port = link_ports[f"{best_path[i].split(':')[7]}::{previous_node}"]
        in_port = port.split("::")[0]
        if i + 1 < len(best_path):
            port = link_ports[f"{best_path[i].split(':')[7]}::{best_path[i + 1].split(':')[7]}"]
            out_port = port.split("::")[0]
        else:
            out_port = str(host_ports[f"{h1}::{switch[h1].split(':')[7]}"])
        flow_rule(best_path[i].split(":")[7], flow_count, str(in_port), str(out_port), static_flow_url)
        flow_count += 2
        previous_node = best_path[i].split(":")[7]

# Thực hiện cân bằng tải trên các liên kết
def load_balance():
    link_url = "http://localhost:8080/wm/topology/links/json"
    get_response(link_url, "findSwitchLinks")
    find_switch_route()
    get_link_cost()
    add_flow()

# Khởi tạo và nhận đầu vào từ người dùng cho các máy chủ (hosts)
global h1, h2, h3
h1, h2, h3 = "", "", ""

print("Enter Host 1:")
h1 = f"10.0.0.{input().strip()}"
print("Enter Host 2:")
h2 = f"10.0.0.{input().strip()}"
print("Enter Host 3 (H2's Neighbor):")
h3 = f"10.0.0.{input().strip()}"

while True:
    try:
        # Khởi tạo các biến cần thiết
        switch, device_mac, host_ports, path, switch_links, link_ports, final_link_tx = {}, {}, {}, {}, {}, {}, {}
        port_key, cost = "", 0
        G = nx.Graph()
        requests.put("http://localhost:8080/wm/statistics/config/enable/json")
        get_response("http://localhost:8080/wm/device/", "deviceInfo")
        load_balance()

        # In kết quả
        print("\n\n############ RESULT ############\n\n")
        print("Switch H4:", switch[h3], "\tSwitch H3:", switch[h2])
        print("\n\nSwitch H1:", switch[h1])
        print("\nIP & MAC\n\n", device_mac)
        print("\nHost::Switch Ports\n\n", host_ports)
        print("\nLink Ports (SRC::DST - SRC PORT::DST PORT)\n\n", link_ports)
        print("\nPaths (SRC TO DST)\n\n", path)
        print("\nFinal Link Cost (First To Second Switch)\n\n", final_link_tx)
        print("\n\n#######################################\n\n")

        time.sleep(60)
    except KeyboardInterrupt:
        break

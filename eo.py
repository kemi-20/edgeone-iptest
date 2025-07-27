import subprocess
import ipaddress
from pathlib import Path
import concurrent.futures
import os
import json
import random
from huaweicloudsdkcore.auth.credentials import BasicCredentials
from huaweicloudsdkdns.v2 import DnsClient
from huaweicloudsdkdns.v2.region.dns_region import DnsRegion
from huaweicloudsdkdns.v2.model import *
from huaweicloudsdkcore.exceptions import exceptions

# ===================== 配置区域 =====================
INPUT_FILE = "IPs/eo_hkg.txt"
OUTPUT_FILE = "yes.txt"
TIMEOUT = 0.5  # 每个请求的超时时间，单位：秒
MAX_WORKERS = 50  # 最大并发数
TARGET_HOST = "TARGET_HOST"

# 华为云账号AccessKey，请填写您的AccessKey
access_key_id = 'YOUR_ACCESS_KEY_ID'
access_key_secret = 'YOUR_ACCESS_KEY_SECRET'

# 域名配置
ZONE_NAME = "hw.example.com"
RECORD_NAME = "eo.hw.example.com"

# 线路配置
LINES = ["default_view", "Huadong", "Huanan", "Huazhong", "Huabei", "Dongbei", "Xinan", "Xibei", "Abroad"]
# ====================================================

# 创建认证信息
credentials = BasicCredentials(access_key_id, access_key_secret)

# 创建DNS客户端
client = DnsClient.new_builder() \
    .with_credentials(credentials) \
    .with_region(DnsRegion.value_of("cn-north-4")) \
    .build()

def get_zone_id():
    """获取域名的Zone ID"""
    try:
        request = ListPublicZonesRequest()
        request.name = ZONE_NAME
        response = client.list_public_zones(request)
        if response.zones and len(response.zones) > 0:
            return response.zones[0].id
        else:
            print(f'未找到域名 {ZONE_NAME}')
            return None
    except exceptions.ClientRequestException as e:
        print(f'获取Zone ID失败: {e}')
        return None

def get_all_a_records():
    """获取所有A记录"""
    zone_id = get_zone_id()
    if not zone_id:
        return []
    
    try:
        #request = ListRecordSetsRequest()
        request = ShowRecordSetByZoneRequest()
        request.zone_id = zone_id
        request.type = "A"
        request.name = RECORD_NAME
        response = client.show_record_set_by_zone(request)
        #response = client.list_record_sets(request)
        records = []
        if response.recordsets:
            for recordset in response.recordsets:
                # 只返回记录集信息，不重复每个IP
                records.append({
                    'RecordsetId': recordset.id,
                    'Name': recordset.name,
                    'Records': recordset.records
                })
        return records
    except exceptions.ClientRequestException as e:
        print(f'获取所有A记录失败: {str(e)}')
        return []



def delete_dns_record(recordset_id):
    """删除DNS记录"""
    if not recordset_id:
        print('未找到记录ID，无法删除DNS记录')
        return False

    zone_id = get_zone_id()
    if not zone_id:
        return False

    try:
        request = DeleteRecordSetsRequest()
        request.zone_id = zone_id
        request.recordset_id = recordset_id
        
        response = client.delete_record_sets(request)
        print('成功删除DNS记录')
        return True
    except exceptions.ClientRequestException as e:
        print(f'删除DNS记录失败: {str(e)}')
        return False



def update_dns_records(ips):
    """批量更新DNS记录"""
    zone_id = get_zone_id()
    if not zone_id:
        return False

    try:
        # 只删除eo.hw.example.com.的A记录
        existing_records = get_all_a_records()
        target_name = RECORD_NAME + "."
        
        for record in existing_records:
            # 只删除匹配目标域名的记录
            if record.get('Name') == target_name:
                recordset_id = record['RecordsetId']
                delete_request = DeleteRecordSetsRequest()
                delete_request.zone_id = zone_id
                delete_request.recordset_id = recordset_id
                
                try:
                    client.delete_record_sets(delete_request)
                    print(f' 成功删除DNS记录集: {record.get("Name", "Unknown")}')
                except exceptions.ClientRequestException as e:
                    print(f' 删除DNS记录失败: {e.error_msg}')
            else:
                print(f' 跳过删除其他域名记录: {record.get("Name", "Unknown")}')
        
        # 尝试使用老版本API创建单个记录集包含所有IP
        record_name = RECORD_NAME + "."
        
        # 将IP分批处理，每个记录集包含多个IP以节省配额
        batch_size = min(50,len(ips))  # 每个记录集最多包含50个IP
        total_batches = min(len(LINES), (len(ips) + batch_size - 1) // batch_size) # 计算总批次数
        for batch_num in range(total_batches):
            if len(ips)>batch_size*total_batches+5: # IP够多就随机，不够就按顺序
                batch_ips=[]
                for ip_num in range(batch_size):
                    ip_no=random.randint(2,len(ips)-2) # 随机选择IP
                    batch_ips.append(ips[ip_no])
                    ips.pop(ip_no)
            else:
                start_idx = batch_num * batch_size
                end_idx = min(start_idx + batch_size, len(ips))
                batch_ips = ips[start_idx:end_idx]
            try:
                request = CreateRecordSetWithLineRequest()
                request.zone_id = zone_id
                
                request.body = CreateRecordSetWithLineRequestBody(
                    records=batch_ips,  # 每个记录集包含一批IP
                    ttl=1,
                    type="A",
                    line=LINES[batch_num],
                    name=record_name
                )
                
                response = client.create_record_set_with_line(request)
                print(f' 成功创建DNS记录集 {record_name}，包含{len(batch_ips)}个IP (第{batch_num+1}/{total_batches}批)')
                
            except exceptions.ClientRequestException as e:
                print(f' 创建DNS记录集失败 (第{batch_num+1}批): {e.error_msg}')
                # 继续创建其他批次，不因单个失败而停止
        
        print(f' 所有记录集创建完成，共{total_batches}个记录集，域名 {record_name} 现在解析到{len(ips)}个IP地址')
        return True
            
    except exceptions.ClientRequestException as e:
        print(f'批量更新DNS记录失败: {str(e)}')
        return False

def updatedns(ips):    
    # 获取所有A记录
    print('正在获取所有A记录...')
    records = get_all_a_records()
    if not records:
        print('未找到任何A记录，无需删除')
    else:
        print(f'找到{len(records)}条A记录，准备比对...')

    # 只删除目标域名的A记录
    target_name = RECORD_NAME + "."
    to_delete = []
    for record in records:
        if record.get('Name') == target_name:
            to_delete.append(record['RecordsetId'])
            print(f'找到目标域名记录: {record.get("Name", "Unknown")}，将被删除')
        else:
            print(f'跳过其他域名记录: {record.get("Name", "Unknown")}')
    
    if to_delete and len(ips)!=0:
        print(f'需要删除{len(to_delete)}条目标域名A记录...')
        for rid in to_delete:
            delete_dns_record(rid)
    else:
        print('没有找到目标域名的A记录需要删除')

    # 批量更新所有IP记录
    if ips:
        print(f'批量更新DNS记录集，包含{len(ips)}个IP...')
        update_dns_records(ips)
    else:
        print('没有IP需要更新')

#上为dns推送，下为优选
def expand_ips(line):
    """解析IP或CIDR，返回IP列表"""
    try:
        net = ipaddress.ip_network(line.strip(), strict=False)
        return [str(ip) for ip in net.hosts()]
    except ValueError:
        return [line.strip()]  # 单个IP

def check_ip(ip):
    """使用curl尝试绑定IP访问目标站点"""
    curl_cmd = [
        "curl",
        "-s",                     # 静默模式
        "-o", "/dev/null",       # 忽略输出
        "-w", "%{http_code}",    # 输出HTTP状态码
        "--resolve", f"{TARGET_HOST}:443:{ip}",
        "--max-time", str(TIMEOUT),
        f"https://{TARGET_HOST}"
    ]

    try:
        result = subprocess.run(
            curl_cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT + 2  # 预留少量时间用于命令处理
        )
        status_code = result.stdout.strip()
        if status_code == "200": # HTTP状态码
            print(f"[+] {ip} ✅")
            return ip
        else:
            print(f"[-] {ip} ❌ (HTTP {status_code})")
    except subprocess.TimeoutExpired:
        print(f"[!] {ip} ⏰ 超时")
    except Exception as e:
        print(f"[!] {ip} ⚠️ 错误: {e}")
    return None

def main():
    input_path = Path(INPUT_FILE)
    output_path = Path(OUTPUT_FILE)
    output_path.write_text("")  # 清空 yes.txt

    all_ips = []
    for line in input_path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        all_ips.extend(expand_ips(line))

    print(f"共加载 {len(all_ips)} 个IP，开始检查...\n")

    valid_ips = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(check_ip, ip): ip for ip in all_ips}
        for future in concurrent.futures.as_completed(futures):
            result_ip = future.result()
            if result_ip:
                valid_ips.append(result_ip)

    # 写入有效IP
    output_path.write_text("\n".join(valid_ips), encoding='utf-8')
    print(f"\n✅ 检测完成，{len(valid_ips)} 个IP可用，已写入 {OUTPUT_FILE}")
    updatedns(valid_ips)

if __name__ == "__main__":
    main()
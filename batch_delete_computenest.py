#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量删除计算巢服务实例的CLI工具
读取CSV文件中的ServiceInstanceId，通过阿里云CLI批量删除
支持并发删除
"""

import argparse
import csv
import json
import os
import subprocess
import sys
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from glob import glob


# 环境变量名
ENV_ACCESS_KEY_ID = "ALIYUN_COMPUTENEST_AK"
ENV_ACCESS_KEY_SECRET = "ALIYUN_COMPUTENEST_SK"

# ComputeNest API固定使用cn-hangzhou endpoint
API_REGION = "cn-hangzhou"

# 默认ServiceId
DEFAULT_SERVICE_ID = "service-249322bfe8c045798808"


def run_aliyun_cli(command: List[str]) -> Dict[str, Any]:
    """执行阿里云CLI命令并返回JSON结果"""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode != 0:
            return {"error": result.stderr}
        
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        return {"error": f"JSON解析失败: {e}"}
    except Exception as e:
        return {"error": str(e)}


def configure_aliyun_cli(access_key_id: str, access_key_secret: str, region_id: str):
    """配置阿里云CLI认证"""
    print("正在配置阿里云CLI...")
    
    command = [
        "aliyun", "configure", "set",
        "--profile", "default",
        "--mode", "AK",
        "--access-key-id", access_key_id,
        "--access-key-secret", access_key_secret,
        "--region", region_id
    ]
    
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"配置失败: {result.stderr}")
        sys.exit(1)
    
    print("阿里云CLI配置成功!")


def read_csv_file(filepath: str) -> List[str]:
    """从CSV文件读取ServiceInstanceId列表"""
    instance_ids = []
    
    with open(filepath, 'r', encoding='utf-8-sig') as csvfile:
        reader = csv.DictReader(csvfile)
        
        for row in reader:
            # 尝试多种可能的列名
            instance_id = row.get("ServiceInstanceId") or row.get("serviceInstanceId") or row.get("实例ID")
            if instance_id and instance_id.strip():
                instance_ids.append(instance_id.strip())
    
    return instance_ids


def list_service_instances(status: str = None, service_id: str = None) -> List[Dict[str, Any]]:
    """查询服务实例列表
    
    Args:
        status: 状态过滤（如 DeployedFailed, Deployed 等）
        service_id: 服务ID过滤
    
    Returns:
        服务实例列表
    """
    command = [
        "aliyun", "computenest", "ListServiceInstances",
        "--RegionId", API_REGION,
        "--MaxResults", "100",
    ]
    
    filter_idx = 1
    
    if status:
        command.extend([f"--Filter.{filter_idx}.Name", "Status", f"--Filter.{filter_idx}.Value.1", status])
        filter_idx += 1
    
    if service_id:
        command.extend([f"--Filter.{filter_idx}.Name", "ServiceId", f"--Filter.{filter_idx}.Value.1", service_id])
        filter_idx += 1
    
    result = run_aliyun_cli(command)
    
    if "error" in result:
        print(f"查询失败: {result['error']}")
        return []
    
    instances = result.get("ServiceInstances", [])
    return instances


def delete_service_instance(service_instance_id: str, force: bool = False) -> Dict[str, Any]:
    """删除单个服务实例
    
    Args:
        service_instance_id: 服务实例ID
        force: 是否强制删除（用于删除部署失败的实例）
    """
    command = [
        "aliyun", "computenest", "DeleteServiceInstances",
        "--RegionId", API_REGION,
        "--ServiceInstanceId.1", service_instance_id,
    ]
    
    # 强制删除（用于删除部署失败的实例）
    if force:
        command.extend(["--Force", "true"])
    
    return run_aliyun_cli(command)


def find_csv_files(directory: str) -> List[str]:
    """查找目录下的所有CSV文件"""
    pattern = os.path.join(directory, "*.csv")
    return glob(pattern)


def main():
    parser = argparse.ArgumentParser(
        description="批量删除计算巢服务实例",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  # 设置环境变量
  export ALIYUN_COMPUTENEST_AK=YOUR_AK
  export ALIYUN_COMPUTENEST_SK=YOUR_SK

  # 查询并删除部署失败的实例
  python batch_delete_computenest.py --failed

  # 查询并删除指定状态的实例
  python batch_delete_computenest.py --status DeployedFailed
  python batch_delete_computenest.py --status Deployed

  # 删除指定CSV文件中的实例
  python batch_delete_computenest.py --file computenest_instances_xxx.csv

  # 删除当前目录下所有CSV文件中的实例
  python batch_delete_computenest.py --all-csv

  # 指定并发数
  python batch_delete_computenest.py --failed --concurrency 10
        """
    )
    
    # 数据源选项（互斥）
    source_group = parser.add_argument_group('数据源（选择一种）')
    source_group.add_argument('--failed', action='store_true', 
                              help='查询并删除部署失败的实例（通过API查询）')
    source_group.add_argument('--status', 
                              help='查询并删除指定状态的实例（如: DeployedFailed, Deployed）')
    source_group.add_argument('--file', '-f', help='从指定CSV文件读取实例ID')
    source_group.add_argument('--all-csv', action='store_true', 
                              help='从当前目录下所有CSV文件读取实例ID')
    
    # 其他选项
    parser.add_argument('--service-id', default=DEFAULT_SERVICE_ID,
                        help=f'按服务ID过滤 (默认: {DEFAULT_SERVICE_ID})')
    parser.add_argument('--force', action='store_true', help='强制删除')
    parser.add_argument('--concurrency', '-c', type=int, default=5, help='并发数 (默认: 5)')
    parser.add_argument('--yes', '-y', action='store_true', help='跳过确认直接删除')
    
    args = parser.parse_args()
    
    # 从环境变量读取AK/SK
    access_key_id = os.environ.get(ENV_ACCESS_KEY_ID)
    access_key_secret = os.environ.get(ENV_ACCESS_KEY_SECRET)
    
    if not access_key_id or not access_key_secret:
        print(f"错误: 请设置环境变量 {ENV_ACCESS_KEY_ID} 和 {ENV_ACCESS_KEY_SECRET}")
        sys.exit(1)
    
    # 配置阿里云CLI
    configure_aliyun_cli(access_key_id, access_key_secret, API_REGION)
    
    # 收集要删除的实例
    all_instances = []  # [{ServiceInstanceId, Name, Status}, ...]
    
    if args.failed:
        # 查询部署失败的实例
        print("\n通过API查询部署失败的实例...")
        instances = list_service_instances(status="DeployedFailed", service_id=args.service_id)
        for inst in instances:
            all_instances.append({
                "ServiceInstanceId": inst.get("ServiceInstanceId", ""),
                "Name": inst.get("Name", ""),
                "Status": inst.get("Status", ""),
            })
        print(f"查询到 {len(all_instances)} 个部署失败的实例")
        
    elif args.status:
        # 查询指定状态的实例
        print(f"\n通过API查询状态为 {args.status} 的实例...")
        instances = list_service_instances(status=args.status, service_id=args.service_id)
        for inst in instances:
            all_instances.append({
                "ServiceInstanceId": inst.get("ServiceInstanceId", ""),
                "Name": inst.get("Name", ""),
                "Status": inst.get("Status", ""),
            })
        print(f"查询到 {len(all_instances)} 个实例")
        
    elif args.file or args.all_csv:
        # 从CSV文件读取
        csv_files = []
        if args.file:
            if not os.path.exists(args.file):
                print(f"错误: 文件不存在 {args.file}")
                sys.exit(1)
            csv_files = [args.file]
        else:  # args.all_csv
            csv_files = find_csv_files(".")
            if not csv_files:
                print("当前目录下没有找到CSV文件")
                sys.exit(0)
        
        print(f"找到 {len(csv_files)} 个CSV文件:")
        for f in csv_files:
            print(f"  - {f}")
        
        # 读取所有实例ID
        all_instance_ids = []
        for csv_file in csv_files:
            instance_ids = read_csv_file(csv_file)
            print(f"从 {csv_file} 读取到 {len(instance_ids)} 个实例ID")
            all_instance_ids.extend(instance_ids)
        
        # 去重
        all_instance_ids = list(set(all_instance_ids))
        
        for inst_id in all_instance_ids:
            all_instances.append({
                "ServiceInstanceId": inst_id,
                "Name": "",
                "Status": "",
            })
    else:
        print("错误: 请指定数据源 (--failed, --status, --file 或 --all-csv)")
        parser.print_help()
        sys.exit(1)
    
    if not all_instances:
        print("\n没有找到需要删除的实例")
        sys.exit(0)
    
    print(f"\n共计 {len(all_instances)} 个实例待删除:")
    for idx, inst in enumerate(all_instances, 1):
        name_str = f" ({inst['Name']})" if inst['Name'] else ""
        status_str = f" [{inst['Status']}]" if inst['Status'] else ""
        print(f"  {idx}. {inst['ServiceInstanceId']}{name_str}{status_str}")
    
    # 确认删除
    if not args.yes:
        force_hint = "（强制模式）" if args.force else ""
        confirm = input(f"\n确认要删除这 {len(all_instances)} 个实例吗?{force_hint} (输入 yes 确认): ")
        if confirm.lower() != 'yes':
            print("取消删除")
            sys.exit(0)
    
    force_hint = "强制" if args.force else ""
    print(f"\n开始并发{force_hint}删除 {len(all_instances)} 个实例 (并发数: {args.concurrency})...")
    
    success_count = 0
    failed_count = 0
    failed_list = []
    
    def delete_single(instance_id: str) -> tuple:
        """删除单个实例"""
        result = delete_service_instance(instance_id, force=args.force)
        return instance_id, result
    
    # 提取所有实例ID
    all_instance_ids = [inst["ServiceInstanceId"] for inst in all_instances]
    
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {executor.submit(delete_single, inst_id): inst_id for inst_id in all_instance_ids}
        
        for future in as_completed(futures):
            instance_id = futures[future]
            try:
                inst_id, result = future.result()
                
                if "error" in result:
                    print(f"[失败] {inst_id}: {result['error']}")
                    failed_count += 1
                    failed_list.append(inst_id)
                else:
                    print(f"[成功] {inst_id}")
                    success_count += 1
            except Exception as e:
                print(f"[异常] {instance_id}: {e}")
                failed_count += 1
                failed_list.append(instance_id)
    
    # 打印结果
    print("\n" + "=" * 60)
    print(f"删除完成: 成功 {success_count} 个, 失败 {failed_count} 个")
    
    if failed_list:
        print("\n删除失败的实例:")
        for inst_id in failed_list:
            print(f"  - {inst_id}")
    
    print("\n完成!")


if __name__ == "__main__":
    main()


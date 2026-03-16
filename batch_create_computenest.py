#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量创建计算巢服务实例的CLI工具
通过阿里云CLI调用ComputeNest API批量创建服务实例
支持并发创建和并发等待部署完成
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from typing import List, Dict, Any, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime


# 环境变量名
ENV_ACCESS_KEY_ID = "ALIYUN_COMPUTENEST_AK"
ENV_ACCESS_KEY_SECRET = "ALIYUN_COMPUTENEST_SK"

# ComputeNest API固定使用cn-hangzhou endpoint
API_REGION = "cn-hangzhou"

# 默认配置（DeployRegionId是实际部署实例的区域，通过Parameters传入）
DEFAULT_CONFIG = {
    "DeployRegionId": "cn-hongkong",  # 实例部署区域
    "EcsInstanceType": "ecs.u2i-c1m1.xlarge",
    "VpcOption": "ExistingVPC",
    "InstancePassword": "ComputeNest!",
    "BaseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "ServiceId": "service-249322bfe8c045798808",  # OpenClaw服务ID
    "CreateDingTalk": False,
    "EnableEnterpriseWeChatRobot": False,
}


def check_aliyun_cli_installed() -> bool:
    """检查阿里云CLI是否已安装"""
    try:
        result = subprocess.run(
            ["aliyun", "version"],
            capture_output=True,
            text=True,
            check=False
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def install_aliyun_cli():
    """安装阿里云CLI"""
    system = platform.system().lower()
    
    print("正在安装阿里云CLI...")
    
    if system == "darwin":  # macOS
        print("检测到macOS系统，使用brew安装...")
        # 先检查brew是否存在
        brew_check = subprocess.run(["which", "brew"], capture_output=True, text=True)
        if brew_check.returncode != 0:
            print("未检测到Homebrew，正在安装Homebrew...")
            brew_install_cmd = '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
            os.system(brew_install_cmd)
        
        # 使用brew安装aliyun-cli
        result = subprocess.run(["brew", "install", "aliyun-cli"], capture_output=True, text=True)
        if result.returncode != 0:
            print(f"brew安装失败，尝试使用curl安装...")
            install_via_curl()
    elif system == "linux":
        print("检测到Linux系统，使用curl安装...")
        install_via_curl()
    elif system == "windows":
        print("检测到Windows系统，请手动下载安装阿里云CLI:")
        print("下载地址: https://aliyuncli.alicdn.com/aliyun-cli-windows-latest-amd64.zip")
        print("或使用: scoop install aliyun-cli")
        sys.exit(1)
    else:
        print(f"不支持的操作系统: {system}")
        install_via_curl()
    
    # 验证安装
    if check_aliyun_cli_installed():
        print("阿里云CLI安装成功!")
    else:
        print("阿里云CLI安装失败，请手动安装:")
        print("参考文档: https://help.aliyun.com/document_detail/139508.html")
        sys.exit(1)


def install_via_curl():
    """通过curl安装阿里云CLI"""
    system = platform.system().lower()
    machine = platform.machine().lower()
    
    # 确定下载URL
    if system == "darwin":
        if "arm" in machine or "aarch64" in machine:
            url = "https://aliyuncli.alicdn.com/aliyun-cli-darwin-arm64-latest.tgz"
        else:
            url = "https://aliyuncli.alicdn.com/aliyun-cli-darwin-amd64-latest.tgz"
    else:  # linux
        if "arm" in machine or "aarch64" in machine:
            url = "https://aliyuncli.alicdn.com/aliyun-cli-linux-arm64-latest.tgz"
        else:
            url = "https://aliyuncli.alicdn.com/aliyun-cli-linux-amd64-latest.tgz"
    
    print(f"下载地址: {url}")
    
    # 下载并安装
    commands = [
        f"curl -Lo /tmp/aliyun-cli.tgz {url}",
        "tar -xzf /tmp/aliyun-cli.tgz -C /tmp",
        "sudo mv /tmp/aliyun /usr/local/bin/aliyun || mv /tmp/aliyun ~/bin/aliyun",
        "rm -f /tmp/aliyun-cli.tgz"
    ]
    
    for cmd in commands:
        print(f"执行: {cmd}")
        os.system(cmd)


def ensure_aliyun_cli():
    """确保阿里云CLI已安装"""
    if not check_aliyun_cli_installed():
        print("未检测到阿里云CLI，开始自动安装...")
        install_aliyun_cli()
    else:
        print("阿里云CLI已安装")


def build_bailian_api_key(api_key: str) -> str:
    """
    构建百炼API Key的JSON格式
    
    ROS模板通过 Fn::Select ApiKeyValue 来获取实际的Key值
    
    Args:
        api_key: 百炼API Key (sk-xxx格式)
    
    Returns:
        JSON格式的API Key字符串
    """
    # ROS模板期望的格式：包含 ApiKeyValue 字段
    bailian_config = {
        "ApiKeyValue": api_key
    }
    
    return json.dumps(bailian_config, ensure_ascii=False)


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
            print(f"CLI命令执行失败: {result.stderr}")
            return {"error": result.stderr}
        
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"JSON解析失败: {e}")
        return {"error": str(e)}
    except Exception as e:
        print(f"执行CLI命令时发生错误: {e}")
        return {"error": str(e)}


def configure_aliyun_cli(access_key_id: str, access_key_secret: str, region_id: str):
    """配置阿里云CLI认证"""
    print("正在配置阿里云CLI...")
    
    # 配置默认profile
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


def create_service_instance(params: Dict[str, Any]) -> Dict[str, Any]:
    """创建单个计算巢服务实例"""
    
    # 构建Parameters JSON（RegionId是实际部署区域）
    parameters = {
        "RegionId": params["DeployRegionId"],
        "BaiLianApiKey": params["BaiLianApiKey"],
        "CreateDingTalk": params["CreateDingTalk"],
        "DingTalkClientId": params["DingTalkClientId"],
        "DingTalkClientSecret": params["DingTalkClientSecret"],
        "EcsInstanceType": params["EcsInstanceType"],
        "EnableEnterpriseWeChatRobot": params["EnableEnterpriseWeChatRobot"],
        "EnterpriseWeChatRobotEncodingAESKey": params["EnterpriseWeChatRobotEncodingAESKey"],
        "EnterpriseWeChatRobotToken": params["EnterpriseWeChatRobotToken"],
        "VpcId": params["VpcId"],
        "VpcOption": params["VpcOption"],
        "VSwitchId": params["VSwitchId"],
        "ZoneId": params["ZoneId"],
        "InstancePassword": params["InstancePassword"],
        "PayType": "PostPaid",
        "EnablePublicIp": True,
        "InternetChargeType": "PayByTraffic",
    }
    
    # 构建CLI命令（API endpoint固定使用cn-hangzhou）
    command = [
        "aliyun", "computenest", "CreateServiceInstance",
        "--RegionId", API_REGION,
        "--ServiceId", params["ServiceId"],
        "--Parameters", json.dumps(parameters, ensure_ascii=False),
    ]
    
    print(f"正在创建服务实例（部署区域: {params['DeployRegionId']}）...")
    result = run_aliyun_cli(command)
    
    return result


def get_service_instance(service_instance_id: str) -> Dict[str, Any]:
    """获取服务实例详情"""
    # API endpoint固定使用cn-hangzhou
    command = [
        "aliyun", "computenest", "GetServiceInstance",
        "--RegionId", API_REGION,
        "--ServiceInstanceId", service_instance_id,
    ]
    
    return run_aliyun_cli(command)


def wait_for_instance_ready(service_instance_id: str, timeout: int = 600) -> Dict[str, Any]:
    """等待实例部署完成"""
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        result = get_service_instance(service_instance_id)
        
        if "error" in result:
            print(f"获取实例状态失败: {result['error']}")
            time.sleep(10)
            continue
        
        status = result.get("Status", "")
        progress = result.get("Progress", 0)
        
        print(f"实例 {service_instance_id} 状态: {status}, 进度: {progress}%")
        
        if status == "Deployed":
            return result
        elif status in ["DeployFailed", "Failed"]:
            print(f"实例部署失败!")
            return result
        
        time.sleep(15)
    
    print(f"等待超时，实例 {service_instance_id} 未在 {timeout} 秒内完成部署")
    return get_service_instance(service_instance_id)


def parse_outputs_url(outputs: str) -> str:
    """
    从Outputs中解析访问URL
    
    Args:
        outputs: API返回的Outputs字符串（JSON格式）
    
    Returns:
        解析出的URL，如果解析失败返回原始字符串
    """
    if not outputs:
        return ""
    
    try:
        # Outputs是JSON字符串，解析它
        outputs_dict = json.loads(outputs)
        
        # 尝试获取OpenClaw Addresses
        url = outputs_dict.get("OpenClaw Addresses", "")
        
        # 清理URL（去掉换行符等）
        url = url.strip()
        
        return url if url else outputs
    except (json.JSONDecodeError, TypeError):
        return outputs


def save_results_to_csv(results: List[Dict[str, Any]], filename: str):
    """将结果保存到CSV文件"""
    if not results:
        print("没有结果需要保存")
        return
    
    fieldnames = ["序号", "ServiceInstanceId", "Name", "Status", "CreateTime", "AccessURL"]
    
    with open(filename, 'w', newline='', encoding='utf-8-sig') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
        for idx, result in enumerate(results, 1):
            # 解析Outputs获取访问URL
            outputs = result.get("Outputs", "")
            access_url = parse_outputs_url(outputs)
            
            row = {
                "序号": idx,
                "ServiceInstanceId": result.get("ServiceInstanceId", ""),
                "Name": result.get("Name", ""),
                "Status": result.get("Status", ""),
                "CreateTime": result.get("CreateTime", ""),
                "AccessURL": access_url,
            }
            writer.writerow(row)
    
    print(f"结果已保存到: {filename}")


def print_results_table(results: List[Dict[str, Any]]):
    """打印结果表格到控制台"""
    if not results:
        print("没有结果")
        return
    
    print("\n" + "=" * 140)
    print(f"{'序号':<6} {'ServiceInstanceId':<30} {'Name':<20} {'Status':<12} {'AccessURL'}")
    print("=" * 140)
    
    for idx, result in enumerate(results, 1):
        service_instance_id = result.get("ServiceInstanceId", "")
        name = result.get("Name", "")
        status = result.get("Status", "")
        
        # 解析Outputs获取访问URL
        outputs = result.get("Outputs", "")
        access_url = parse_outputs_url(outputs)
        
        # 截断过长的URL
        if len(access_url) > 70:
            access_url = access_url[:70] + "..."
        
        print(f"{idx:<6} {service_instance_id:<30} {name:<20} {status:<12} {access_url}")
    
    print("=" * 140)


def main():
    parser = argparse.ArgumentParser(
        description="批量创建计算巢服务实例",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  # 设置环境变量
  export ALIYUN_COMPUTENEST_AK=YOUR_AK
  export ALIYUN_COMPUTENEST_SK=YOUR_SK

  # 基本用法
  python batch_create_computenest.py \
    --bailian-api-key sk-xxx \
    --vpc-id vpc-xxx \
    --vswitch-id vsw-xxx \
    --zone-id cn-hongkong-b \
    --count 3

        """
    )
    
    # 百炼配置
    bailian_group = parser.add_argument_group('百炼配置')
    bailian_group.add_argument('--bailian-api-key', required=True, help='百炼API Key (sk-xxx格式)')
    bailian_group.add_argument('--bailian-base-url', default=DEFAULT_CONFIG["BaseUrl"], 
                               help=f'百炼Base URL (默认: {DEFAULT_CONFIG["BaseUrl"]})')
    
    # 钉钉配置（默认关闭）
    dingtalk_group = parser.add_argument_group('钉钉配置')
    dingtalk_group.add_argument('--create-dingtalk', type=lambda x: x.lower() == 'true', 
                                default=False, help='是否创建钉钉机器人 (默认: False)')
    dingtalk_group.add_argument('--dingtalk-client-id', default='', help='钉钉Client ID')
    dingtalk_group.add_argument('--dingtalk-client-secret', default='', help='钉钉Client Secret')
    
    # 企业微信配置（默认关闭）
    wechat_group = parser.add_argument_group('企业微信配置')
    wechat_group.add_argument('--enable-wechat-robot', action='store_true', 
                              help='是否启用企业微信机器人 (默认: False)')
    wechat_group.add_argument('--wechat-aes-key', default='', help='企业微信AES Key')
    wechat_group.add_argument('--wechat-token', default='', help='企业微信Token')
    
    # VPC和网络配置
    network_group = parser.add_argument_group('网络配置')
    network_group.add_argument('--vpc-id', required=True, help='VPC ID')
    network_group.add_argument('--vswitch-id', required=True, help='VSwitch ID')
    network_group.add_argument('--zone-id', required=True, help='可用区ID')
    
    # 实例配置
    instance_group = parser.add_argument_group('实例配置')
    instance_group.add_argument('--ecs-type', default=DEFAULT_CONFIG["EcsInstanceType"], 
                                help=f'ECS实例类型 (默认: {DEFAULT_CONFIG["EcsInstanceType"]})')
    instance_group.add_argument('--count', '-n', type=int, default=1, help='创建实例数量 (默认: 1)')
    instance_group.add_argument('--concurrency', '-c', type=int, default=5, help='并发数 (默认: 5)')
    instance_group.add_argument('--service-id', default=DEFAULT_CONFIG["ServiceId"], 
                                help=f'服务ID (默认: {DEFAULT_CONFIG["ServiceId"]})')
    
    # 输出配置
    output_group = parser.add_argument_group('输出配置')
    output_group.add_argument('--output', '-o', default='', help='输出CSV文件路径')
    output_group.add_argument('--no-wait', action='store_true', help='不等待部署完成直接返回（默认会等待Deployed状态）')
    output_group.add_argument('--timeout', type=int, default=600, help='等待超时时间(秒) (默认: 600)')
    
    args = parser.parse_args()
    
    # 确保阿里云CLI已安装
    ensure_aliyun_cli()
    
    # 从环境变量读取AK/SK
    access_key_id = os.environ.get(ENV_ACCESS_KEY_ID)
    access_key_secret = os.environ.get(ENV_ACCESS_KEY_SECRET)
    
    if not access_key_id or not access_key_secret:
        print(f"错误: 请设置环境变量 {ENV_ACCESS_KEY_ID} 和 {ENV_ACCESS_KEY_SECRET}")
        print(f"例如:")
        print(f"  export {ENV_ACCESS_KEY_ID}=YOUR_ACCESS_KEY_ID")
        print(f"  export {ENV_ACCESS_KEY_SECRET}=YOUR_ACCESS_KEY_SECRET")
        sys.exit(1)
    
    # 配置阿里云CLI（使用API endpoint region）
    configure_aliyun_cli(access_key_id, access_key_secret, API_REGION)
    
    # 构建百炼API Key JSON格式
    bailian_api_key_json = build_bailian_api_key(api_key=args.bailian_api_key)
    
    # 构建创建参数
    create_params = {
        "DeployRegionId": DEFAULT_CONFIG["DeployRegionId"],
        "BaiLianApiKey": bailian_api_key_json,
        "CreateDingTalk": args.create_dingtalk,
        "DingTalkClientId": args.dingtalk_client_id,
        "DingTalkClientSecret": args.dingtalk_client_secret,
        "EcsInstanceType": args.ecs_type,
        "EnableEnterpriseWeChatRobot": args.enable_wechat_robot,
        "EnterpriseWeChatRobotEncodingAESKey": args.wechat_aes_key,
        "EnterpriseWeChatRobotToken": args.wechat_token,
        "VpcId": args.vpc_id,
        "VpcOption": DEFAULT_CONFIG["VpcOption"],
        "VSwitchId": args.vswitch_id,
        "ZoneId": args.zone_id,
        "InstancePassword": DEFAULT_CONFIG["InstancePassword"],
        "ServiceId": args.service_id,
    }
    
    print(f"\n开始批量创建 {args.count} 个计算巢服务实例...")
    print(f"部署区域: {DEFAULT_CONFIG['DeployRegionId']}")
    print(f"ECS类型: {args.ecs_type}")
    print(f"VPC: {args.vpc_id}")
    print(f"VSwitch: {args.vswitch_id}")
    print(f"可用区: {args.zone_id}")
    print(f"并发数: {args.concurrency}")
    print("-" * 60)
    
    created_instances = []
    failed_count = 0
    
    # 并发创建实例
    def create_single_instance(index: int) -> Tuple[int, Dict[str, Any]]:
        """创建单个实例的任务"""
        result = create_service_instance(create_params)
        return index, result
    
    print(f"\n开始并发创建 {args.count} 个实例...")
    
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        # 提交所有创建任务
        futures = {executor.submit(create_single_instance, i): i for i in range(args.count)}
        
        for future in as_completed(futures):
            index = futures[future]
            try:
                idx, result = future.result()
                
                if "error" in result:
                    print(f"[{idx+1}/{args.count}] 创建失败: {result['error']}")
                    failed_count += 1
                    continue
                
                service_instance_id = result.get("ServiceInstanceId", "")
                if service_instance_id:
                    print(f"[{idx+1}/{args.count}] 实例创建成功: {service_instance_id}")
                    created_instances.append({
                        "ServiceInstanceId": service_instance_id,
                        "Status": "Creating",
                    })
                else:
                    print(f"[{idx+1}/{args.count}] 创建返回异常: {result}")
                    failed_count += 1
            except Exception as e:
                print(f"[{index+1}/{args.count}] 创建异常: {e}")
                failed_count += 1
    
    print(f"\n创建完成: 成功 {len(created_instances)} 个, 失败 {failed_count} 个")
    
    # 默认等待部署完成（除非指定--no-wait）
    if not args.no_wait and created_instances:
        print(f"\n并发等待 {len(created_instances)} 个实例部署完成...")
        final_results = []
        
        def wait_single_instance(instance: Dict[str, Any]) -> Dict[str, Any]:
            """等待单个实例部署完成"""
            service_instance_id = instance["ServiceInstanceId"]
            return wait_for_instance_ready(service_instance_id, args.timeout)
        
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            # 提交所有等待任务
            futures = {executor.submit(wait_single_instance, inst): inst["ServiceInstanceId"] 
                      for inst in created_instances}
            
            for future in as_completed(futures):
                instance_id = futures[future]
                try:
                    result = future.result()
                    status = result.get("Status", "Unknown")
                    print(f"实例 {instance_id} 状态: {status}")
                    final_results.append(result)
                except Exception as e:
                    print(f"实例 {instance_id} 等待异常: {e}")
                    final_results.append({"ServiceInstanceId": instance_id, "Status": "Error", "Error": str(e)})
        
        created_instances = final_results
    elif args.no_wait:
        # 不等待的情况下，并发获取一次实例信息
        print("\n并发获取实例信息...")
        time.sleep(5)  # 等待几秒让实例状态更新
        
        def get_single_instance(instance: Dict[str, Any]) -> Dict[str, Any]:
            """获取单个实例信息"""
            service_instance_id = instance["ServiceInstanceId"]
            result = get_service_instance(service_instance_id)
            return result if "error" not in result else instance
        
        final_results = []
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = [executor.submit(get_single_instance, inst) for inst in created_instances]
            for future in as_completed(futures):
                final_results.append(future.result())
        
        created_instances = final_results
    
    # 打印结果表格
    print_results_table(created_instances)
    
    # 保存到CSV
    if args.output:
        save_results_to_csv(created_instances, args.output)
    else:
        # 默认保存到当前目录
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_filename = f"computenest_instances_{timestamp}.csv"
        save_results_to_csv(created_instances, default_filename)
    
    print("\n完成!")


if __name__ == "__main__":
    main()
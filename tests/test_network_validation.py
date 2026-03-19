#!/usr/bin/env python3
"""
OpenClaw 生产模版网络隔离验证测试套件

对线上 ACS/ACK 集群执行完整网络架构验证:
  1. VPC & VSwitch 拓扑 (主网段 + 辅助网段, 6 个 VSwitch)
  2. 安全组规则 (入方向/出方向, 动态 CIDR)
  3. NAT 网关隔离 (OpenClaw 独立 NAT + 专用 EIP)
  4. 每个 OpenClaw VSwitch 的 SNAT 规则
  5. Sandbox Pod 连通性与隔离测试
  6. ALB & Ingress 配置
  7. PrivateZone DNS 解析
  8. Pod 安全加固

暂跳过 (等待 Poseidon 发布):
  - TrafficPolicy / GlobalTrafficPolicy 验证
  - Poseidon 组件检查

前置条件:
  - kubectl 已配置集群 kubeconfig
  - aliyun CLI 已配置有效凭证

用法:
  python tests/test_network_validation.py --stack-name <name> --region <region>
  python tests/test_network_validation.py --cluster-id <id> --region <region> --sg-id <sg-id>
  python tests/test_network_validation.py --kubectl-only  # 跳过云 API 检查
"""

import argparse
import ipaddress
import json
import subprocess
import sys
import time
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any


@dataclass
class TestResult:
    name: str
    passed: bool
    message: str
    category: str = ""
    severity: str = "P1"


class NetworkValidator:
    def __init__(self, stack_name: str = "", cluster_id: str = "", region: str = "",
                 sg_id: str = "", kubectl_only: bool = False, skip_poseidon: bool = True):
        self.stack_name = stack_name
        self.cluster_id = cluster_id
        self.region = region
        self.sg_id = sg_id
        self.kubectl_only = kubectl_only
        self.skip_poseidon = skip_poseidon
        self.results: List[TestResult] = []
        self.stack_outputs: Dict[str, str] = {}
        self.stack_params: Dict[str, str] = {}
        self.vpc_primary_cidr: str = ""
        self.vpc_secondary_cidrs: List[str] = []

    def run_cmd(self, cmd: str, timeout: int = 30) -> Tuple[int, str, str]:
        try:
            proc = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=timeout
            )
            return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", "命令超时"

    def kubectl(self, args: str, timeout: int = 30) -> Tuple[int, str, str]:
        return self.run_cmd(f"kubectl {args}", timeout)

    def aliyun_cli(self, args: str, timeout: int = 30) -> Tuple[int, str, str]:
        return self.run_cmd(f"aliyun {args}", timeout)

    def add_result(self, category: str, name: str, passed: bool, message: str, severity: str = "P1"):
        self.results.append(TestResult(
            name=name, passed=passed, message=message, category=category, severity=severity
        ))
        status = "\033[32m通过\033[0m" if passed else "\033[31m失败\033[0m"
        print(f"  [{status}] {name}: {message}")

    # ==================== 0. Load stack info from ROS ====================
    def _resolve_stack_id(self) -> str:
        """Resolve stack name to stack ID if needed"""
        import re as _re
        if _re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-', self.stack_name):
            return self.stack_name
        rc, out, _ = self.aliyun_cli(
            f"ros ListStacks --RegionId {self.region} --StackName.1 {self.stack_name} --PageSize 5"
        )
        if rc == 0:
            try:
                data = json.loads(out)
                stacks = data.get("Stacks", [])
                if stacks:
                    return stacks[0]["StackId"]
            except (json.JSONDecodeError, KeyError, IndexError):
                pass
        return self.stack_name

    def load_stack_info(self):
        """Load stack outputs and parameters from ROS to get resource IDs"""
        print("\n=== 0. 加载 ROS Stack 信息 ===")

        if self.stack_name and self.region:
            stack_id = self._resolve_stack_id()
            print(f"  Stack ID: {stack_id}")
            rc, out, err = self.aliyun_cli(
                f"ros GetStack --RegionId {self.region} --StackId {stack_id}"
            )
            if rc != 0:
                self.add_result("StackInfo", "加载 Stack", False, f"无法获取 Stack: {err[:200]}")
                return

            try:
                data = json.loads(out)
                status = data.get("Status", "")
                self.add_result("StackInfo", "Stack 状态", status == "CREATE_COMPLETE",
                                f"Status: {status}")

                for o in data.get("Outputs", []):
                    self.stack_outputs[o["OutputKey"]] = o.get("OutputValue", "")

                for p in data.get("Parameters", []):
                    val = p.get("ParameterValue", "")
                    if val != "******":
                        self.stack_params[p["ParameterKey"]] = val

                if not self.cluster_id:
                    self.cluster_id = self.stack_outputs.get("ClusterId", "")
                if not self.sg_id:
                    self.sg_id = self.stack_outputs.get("OpenClawSecurityGroupId", "")

                print(f"  集群 ID: {self.cluster_id}")
                print(f"  SecurityGroup ID: {self.sg_id}")
                print(f"  VPC ID: {self.stack_outputs.get('VpcId', 'N/A')}")
                print(f"  OpenClaw NAT EIP: {self.stack_outputs.get('OpenClawNatEipAddress', 'N/A')}")
                print(f"  Default NAT IP: {self.stack_outputs.get('DefaultNatGatewayIp', 'N/A')}")
                print(f"  API Server IP: {self.stack_outputs.get('ApiServerIntranetIp', 'N/A')}")
                print(f"  ALB DNS: {self.stack_outputs.get('ALB_DNS_Name', 'N/A')}")

            except (json.JSONDecodeError, KeyError) as e:
                self.add_result("StackInfo", "解析 Stack", False, f"解析错误: {e}")

    # ==================== 1. kubectl connectivity ====================
    def test_kubectl_connectivity(self):
        print("\n=== 1. Kubectl 连接性 ===")
        rc, out, err = self.kubectl("cluster-info --request-timeout=15s")
        if rc == 0:
            self.add_result("Connectivity", "kubectl cluster-info", True, "集群可达")
        else:
            self.add_result("Connectivity", "kubectl cluster-info", False,
                            f"无法连接集群: {err[:200]}")
            return False
        return True

    # ==================== 2. VPC & VSwitch topology ====================
    def test_vpc_vswitch_topology(self):
        print("\n=== 2. VPC & VSwitch 拓扑 ===")
        vpc_id = self.stack_outputs.get("VpcId", "")
        if not vpc_id:
            self.add_result("VPC", "VPC ID 可用", False, "Stack 无 VPC ID")
            return

        rc, out, _ = self.aliyun_cli(
            f"vpc DescribeVpcAttribute --RegionId {self.region} --VpcId {vpc_id}"
        )
        if rc != 0:
            self.add_result("VPC", "查询 VPC", False, "无法查询 VPC")
            return

        try:
            vpc = json.loads(out)
            primary_cidr = vpc.get("CidrBlock", "")
            secondary_cidrs = vpc.get("SecondaryCidrBlocks", {}).get("SecondaryCidrBlock", [])

            self.vpc_primary_cidr = primary_cidr
            self.vpc_secondary_cidrs = secondary_cidrs

            self.add_result("VPC", "VPC 主网段", bool(primary_cidr),
                            f"主网段: {primary_cidr}")

            openclaw_cidr = self.stack_params.get("OpenClawCidrBlock", "")
            if openclaw_cidr:
                has_openclaw_cidr = openclaw_cidr in secondary_cidrs
                self.add_result("VPC", "VPC 辅助网段 (OpenClaw)", has_openclaw_cidr,
                                f"预期: {openclaw_cidr}, 辅助网段: {secondary_cidrs}",
                                severity="P0")
            else:
                self.add_result("VPC", "VPC 辅助网段 (OpenClaw)",
                                len(secondary_cidrs) > 0,
                                f"辅助网段: {secondary_cidrs}", severity="P0")

            vsw_ids = vpc.get("VSwitchIds", {}).get("VSwitchId", [])
            self.add_result("VPC", "VSwitch 数量", len(vsw_ids) >= 6,
                            f"发现 {len(vsw_ids)} 个 VSwitch (预期 >= 6)")

        except (json.JSONDecodeError, KeyError) as e:
            self.add_result("VPC", "解析 VPC", False, f"错误: {e}")

        rc, out, _ = self.aliyun_cli(
            f"vpc DescribeVSwitches --RegionId {self.region} --VpcId {vpc_id} --PageSize 50"
        )
        if rc != 0:
            self.add_result("VPC", "查询 VSwitch", False, "无法查询 VSwitch")
            return

        try:
            data = json.loads(out)
            vswitches = data.get("VSwitches", {}).get("VSwitch", [])

            primary_net = ipaddress.ip_network(self.vpc_primary_cidr, strict=False) if self.vpc_primary_cidr else None
            biz_vsw = []
            oc_vsw = []
            for v in vswitches:
                cidr = v.get("CidrBlock", "")
                try:
                    net = ipaddress.ip_network(cidr, strict=False)
                    if primary_net and net.subnet_of(primary_net):
                        biz_vsw.append(v)
                    else:
                        oc_vsw.append(v)
                except ValueError:
                    pass

            self.add_result("VPC", "业务 VSwitch", len(biz_vsw) >= 3,
                            f"发现 {len(biz_vsw)} 个业务 VSwitch: "
                            + ", ".join(f"{v['CidrBlock']}@{v['ZoneId']}" for v in biz_vsw))

            self.add_result("VPC", "OpenClaw VSwitch", len(oc_vsw) >= 3,
                            f"发现 {len(oc_vsw)} 个 OpenClaw VSwitch: "
                            + ", ".join(f"{v['CidrBlock']}@{v['ZoneId']}" for v in oc_vsw))

            biz_zones = set(v["ZoneId"] for v in biz_vsw)
            oc_zones = set(v["ZoneId"] for v in oc_vsw)
            self.add_result("VPC", "可用区覆盖", len(biz_zones) >= 2 and len(oc_zones) >= 2,
                            f"业务可用区: {biz_zones}, OpenClaw 可用区: {oc_zones}")

        except (json.JSONDecodeError, KeyError) as e:
            self.add_result("VPC", "解析 VSwitch", False, f"错误: {e}")

    # ==================== 3. SecurityGroup rules ====================
    def test_security_group_rules(self):
        print("\n=== 3. 安全组规则 ===")
        if not self.sg_id:
            self.add_result("SecurityGroup", "安全组 ID 可用", False, "无安全组 ID")
            return

        rc, out, _ = self.aliyun_cli(
            f"ecs DescribeSecurityGroupAttribute --RegionId {self.region} "
            f"--SecurityGroupId {self.sg_id} --Direction all"
        )
        if rc != 0:
            self.add_result("SecurityGroup", "查询安全组", False, "无法查询安全组")
            return

        try:
            sg = json.loads(out)
            sg_type = sg.get("SecurityGroupType", "normal")
            self.add_result("SecurityGroup", "安全组类型",
                            sg_type in ("enterprise", "normal", None, ""),
                            f"类型: {sg_type or 'normal'} (推荐 enterprise 以支持精细控制)")

            permissions = sg.get("Permissions", {}).get("Permission", [])
            ingress = [p for p in permissions if p.get("Direction") == "ingress"]
            egress = [p for p in permissions if p.get("Direction") == "egress"]

            self.add_result("SecurityGroup", "入方向规则", len(ingress) >= 3,
                            f"{len(ingress)} 条入方向规则")
            self.add_result("SecurityGroup", "出方向规则", len(egress) >= 5,
                            f"{len(egress)} 条出方向规则")

            # Check ingress: business VSwitch CIDRs should be allowed
            ingress_cidrs = [p.get("SourceCidrIp", "") for p in ingress if p.get("Policy") != "Drop"]
            vpc_cidr_prefix = self.vpc_primary_cidr.split(".")[0] + "." if self.vpc_primary_cidr else "192.168."
            biz_cidrs_allowed = any(vpc_cidr_prefix in c for c in ingress_cidrs)
            self.add_result("SecurityGroup", "入方向放行业务 VSwitch", biz_cidrs_allowed,
                            f"允许的源 CIDR: {ingress_cidrs}")

            # Check egress: metadata deny
            metadata_deny = any(
                p.get("DestCidrIp") == "100.100.100.200/32" and p.get("Policy") == "Drop"
                for p in egress
            )
            self.add_result("SecurityGroup", "出方向拒绝 metadata", metadata_deny,
                            "100.100.100.200/32 已拒绝" if metadata_deny else "缺失 metadata 拒绝",
                            severity="P0")

            # Check egress: VPC CIDR deny (dynamic - matches actual VPC CIDR)
            vpc_cidr = self.vpc_primary_cidr or "192.168.0.0/16"
            vpc_deny = any(
                p.get("DestCidrIp") == vpc_cidr and p.get("Policy") == "Drop"
                for p in egress
            )
            self.add_result("SecurityGroup", "出方向拒绝 VPC 主网段", vpc_deny,
                            f"{vpc_cidr} 已拒绝" if vpc_deny
                            else f"缺失 VPC CIDR 拒绝 (预期 {vpc_cidr})",
                            severity="P0")

            # Check egress: OpenClaw CIDR deny
            openclaw_cidr = self.stack_params.get("OpenClawCidrBlock", "10.8.0.0/16")
            oc_deny = any(
                p.get("DestCidrIp") == openclaw_cidr and p.get("Policy") == "Drop"
                for p in egress
            )
            self.add_result("SecurityGroup", "出方向拒绝 OpenClaw 网段", oc_deny,
                            f"{openclaw_cidr} 已拒绝" if oc_deny
                            else f"缺失 OpenClaw CIDR 拒绝 (预期 {openclaw_cidr})",
                            severity="P0")

            # Check egress: DNS to VPC CIDR allowed (CoreDNS in business VSwitch)
            dns_vpc_allow = any(
                p.get("DestCidrIp") == vpc_cidr
                and p.get("Policy") != "Drop"
                and "53" in p.get("PortRange", "")
                for p in egress
            )
            self.add_result("SecurityGroup", "出方向放行 DNS 到 VPC",
                            dns_vpc_allow,
                            f"DNS 53 to {vpc_cidr} 已放行" if dns_vpc_allow
                            else f"缺失 DNS 放行 {vpc_cidr} (CoreDNS 在业务 VSwitch)",
                            severity="P0")

            # Check egress: upstream NAT gateway IP allowed
            default_nat_ip = self.stack_outputs.get("DefaultNatGatewayIp", "")
            if default_nat_ip:
                nat_allowed = any(
                    p.get("DestCidrIp") == f"{default_nat_ip}/32"
                    and p.get("Policy") != "Drop"
                    for p in egress
                )
                self.add_result("SecurityGroup", "出方向放行上游 NAT",
                                nat_allowed,
                                f"NAT IP {default_nat_ip}/32 已放行" if nat_allowed
                                else f"缺失上游 NAT 放行 {default_nat_ip}")

            # Check egress: API server IP allowed
            apiserver_ip = self.stack_outputs.get("ApiServerIntranetIp", "")
            if apiserver_ip:
                api_allowed = any(
                    p.get("DestCidrIp") == f"{apiserver_ip}/32"
                    and p.get("Policy") != "Drop"
                    and "6443" in p.get("PortRange", "")
                    for p in egress
                )
                self.add_result("SecurityGroup", "出方向放行 API Server",
                                api_allowed,
                                f"API Server {apiserver_ip}:6443 已放行" if api_allowed
                                else f"缺失 API Server 放行 {apiserver_ip}")

            # Check egress: Alibaba Cloud DNS allowed
            dns_136 = any(
                p.get("DestCidrIp") == "100.100.2.136/32"
                and p.get("Policy") != "Drop"
                and "53" in p.get("PortRange", "")
                for p in egress
            )
            dns_138 = any(
                p.get("DestCidrIp") == "100.100.2.138/32"
                and p.get("Policy") != "Drop"
                and "53" in p.get("PortRange", "")
                for p in egress
            )
            self.add_result("SecurityGroup", "出方向放行阿里云 DNS",
                            dns_136 and dns_138,
                            f"100.100.2.136: {'正常' if dns_136 else '缺失'}, "
                            f"100.100.2.138: {'正常' if dns_138 else '缺失'}")

            # Check egress: public internet allowed (low priority)
            public_allow = any(
                p.get("DestCidrIp") == "0.0.0.0/0"
                and p.get("Policy") != "Drop"
                for p in egress
            )
            self.add_result("SecurityGroup", "出方向放行公网 (低优先级)",
                            public_allow,
                            "0.0.0.0/0 已放行" if public_allow else "缺失公网放行")

            print("\n  --- 出方向规则详情 ---")
            for p in sorted(egress, key=lambda x: x.get("Priority", 100)):
                policy = p.get("Policy", "Accept")
                print(f"    Priority {p.get('Priority'):>3} | "
                      f"{'DROP' if policy == 'Drop' else 'ALLOW':>5s} | "
                      f"{p.get('IpProtocol', 'all'):>4s} {p.get('PortRange', '-1/-1'):>10s} -> "
                      f"{str(p.get('DestCidrIp', 'N/A')):<20s} | {p.get('Description', '')}")

        except (json.JSONDecodeError, KeyError) as e:
            self.add_result("SecurityGroup", "解析安全组", False, f"错误: {e}")

    # ==================== 4. NAT Gateway isolation ====================
    def test_nat_gateway_isolation(self):
        print("\n=== 4. NAT 网关隔离 ===")
        vpc_id = self.stack_outputs.get("VpcId", "")
        if not vpc_id:
            self.add_result("NAT", "VPC ID 可用", False, "无 VPC ID")
            return

        rc, out, _ = self.aliyun_cli(
            f"vpc DescribeNatGateways --RegionId {self.region} --VpcId {vpc_id} --PageSize 50"
        )
        if rc != 0:
            self.add_result("NAT", "查询 NAT 网关", False, "无法查询 NAT 网关")
            return

        try:
            data = json.loads(out)
            nats = data.get("NatGateways", {}).get("NatGateway", [])
            self.add_result("NAT", "NAT 网关数量", len(nats) >= 2,
                            f"发现 {len(nats)} 个 NAT 网关 (预期 >=2: 集群默认 + OpenClaw)")

            openclaw_nat = [n for n in nats if n.get("Name", "").startswith("openclaw")]
            default_nat = [n for n in nats if not n.get("Name", "").startswith("openclaw")]

            self.add_result("NAT", "OpenClaw 专用 NAT", len(openclaw_nat) >= 1,
                            f"发现: {[n['Name'] for n in openclaw_nat]}" if openclaw_nat
                            else "缺失 OpenClaw NAT", severity="P0")

            self.add_result("NAT", "集群默认 NAT", len(default_nat) >= 1,
                            f"发现: {[n.get('Name', 'unnamed') for n in default_nat]}")

            if openclaw_nat:
                oc_nat = openclaw_nat[0]
                oc_nat_id = oc_nat.get("NatGatewayId", "")
                oc_type = oc_nat.get("NatType", "")
                self.add_result("NAT", "OpenClaw NAT 类型", oc_type == "Enhanced",
                                f"类型: {oc_type}")

                # Check EIP binding
                ips = oc_nat.get("IpLists", {}).get("IpList", [])
                eip_ips = [ip.get("IpAddress", "") for ip in ips]
                self.add_result("NAT", "OpenClaw NAT 绑定 EIP", len(eip_ips) > 0,
                                f"EIPs: {eip_ips}")

                expected_eip = self.stack_outputs.get("OpenClawNatEipAddress", "")
                if expected_eip and eip_ips:
                    self.add_result("NAT", "EIP 匹配 Stack 输出",
                                    expected_eip in eip_ips,
                                    f"预期: {expected_eip}, 实际: {eip_ips}")

                # Check SNAT rules
                snat_table_ids = oc_nat.get("SnatTableIds", {}).get("SnatTableId", [])
                if snat_table_ids:
                    self._check_snat_rules(snat_table_ids[0])

        except (json.JSONDecodeError, KeyError) as e:
            self.add_result("NAT", "解析 NAT", False, f"错误: {e}")

    def _check_snat_rules(self, snat_table_id: str):
        """Check SNAT rules for OpenClaw NAT"""
        rc, out, _ = self.aliyun_cli(
            f"vpc DescribeSnatTableEntries --RegionId {self.region} "
            f"--SnatTableId {snat_table_id} --PageSize 50"
        )
        if rc != 0:
            self.add_result("NAT", "查询 SNAT 规则", False, "无法查询 SNAT 规则")
            return

        try:
            data = json.loads(out)
            entries = data.get("SnatTableEntries", {}).get("SnatTableEntry", [])
            self.add_result("NAT", "SNAT 规则数量", len(entries) >= 3,
                            f"发现 {len(entries)} 条 SNAT 规则 (预期 3 条（3 个 OpenClaw VSwitch）)")

            for entry in entries:
                vsw = entry.get("SourceVSwitchId", "")
                eip = entry.get("SnatIp", "")
                name = entry.get("SnatEntryName", "")
                status = entry.get("Status", "")
                self.add_result("NAT", f"SNAT 规则 '{name}'",
                                status == "Available",
                                f"VSwitchId={vsw}, EIP={eip}, Status={status}")

        except (json.JSONDecodeError, KeyError) as e:
            self.add_result("NAT", "解析 SNAT", False, f"错误: {e}")

    # ==================== 4b. Route Table isolation ====================
    def test_route_table_isolation(self):
        print("\n=== 4b. OpenClaw 路由表隔离 ===")
        vpc_id = self.stack_outputs.get("VpcId", "")
        if not vpc_id:
            self.add_result("RouteTable", "VPC ID 可用", False, "无 VPC ID")
            return

        rc, out, _ = self.aliyun_cli(
            f"vpc DescribeRouteTableList --RegionId {self.region} --VpcId {vpc_id} --PageSize 50"
        )
        if rc != 0:
            self.add_result("RouteTable", "查询路由表", False, "无法查询路由表")
            return

        try:
            data = json.loads(out)
            tables = data.get("RouterTableList", {}).get("RouterTableListType", [])

            system_rt = [t for t in tables if t.get("RouteTableType") == "System"]
            custom_rt = [t for t in tables if t.get("RouteTableType") == "Custom"]

            self.add_result("RouteTable", "自定义路由表存在", len(custom_rt) >= 1,
                            f"发现 {len(custom_rt)} 个自定义路由表, "
                            f"{len(system_rt)} 个系统路由表")

            openclaw_rt = None
            for rt in custom_rt:
                name = rt.get("RouteTableName", "")
                if "openclaw" in name.lower():
                    openclaw_rt = rt
                    break

            if not openclaw_rt and custom_rt:
                openclaw_rt = custom_rt[0]

            if openclaw_rt:
                rt_id = openclaw_rt.get("RouteTableId", "")
                rt_name = openclaw_rt.get("RouteTableName", "")
                self.add_result("RouteTable", "OpenClaw 路由表已识别", True,
                                f"ID: {rt_id}, 名称: {rt_name}")

                vsw_ids = openclaw_rt.get("VSwitchIds", {}).get("VSwitchId", [])
                self.add_result("RouteTable", "路由表 VSwitch 关联",
                                len(vsw_ids) >= 3,
                                f"关联 VSwitch: {vsw_ids} ({len(vsw_ids)} 条, 预期 3)")

                rc2, out2, _ = self.aliyun_cli(
                    f"vpc DescribeRouteEntryList --RegionId {self.region} "
                    f"--RouteTableId {rt_id} --DestinationCidrBlock 0.0.0.0/0"
                )
                if rc2 == 0:
                    entry_data = json.loads(out2)
                    entries = entry_data.get("RouteEntrys", {}).get("RouteEntry", [])
                    if entries:
                        hop = entries[0].get("NextHops", {}).get("NextHop", [])
                        nat_id = self.stack_outputs.get("OpenClawNatGatewayId", "")
                        if hop:
                            actual_hop = hop[0].get("NextHopId", "")
                            hop_type = hop[0].get("NextHopType", "")
                            hop_match = actual_hop == nat_id if nat_id else True
                            self.add_result("RouteTable", "默认路由 -> OpenClaw NAT",
                                            hop_match,
                                            f"NextHop: {actual_hop} ({hop_type})"
                                            + (f" (预期: {nat_id})" if nat_id and not hop_match else ""),
                                            severity="P0")
                        else:
                            self.add_result("RouteTable", "默认路由 -> OpenClaw NAT",
                                            False, "默认路由无下一跳", severity="P0")
                    else:
                        self.add_result("RouteTable", "默认路由存在", False,
                                        "自定义路由表无 0.0.0.0/0 路由", severity="P0")
                else:
                    self.add_result("RouteTable", "查询路由条目", False, "无法查询路由条目")
            else:
                self.add_result("RouteTable", "OpenClaw 路由表已识别", False,
                                "未找到 OpenClaw 自定义路由表", severity="P0")

        except (json.JSONDecodeError, KeyError) as e:
            self.add_result("RouteTable", "解析路由表", False, f"错误: {e}")

    # ==================== 5. Core K8s resources ====================
    def test_core_resources(self):
        print("\n=== 5. 核心 K8s 资源 ===")

        rc, out, _ = self.kubectl("get namespace sandbox-system -o name")
        self.add_result("CoreResources", "sandbox-system 命名空间", rc == 0,
                        "存在" if rc == 0 else "不存在")

        rc, out, _ = self.kubectl("get sandboxset openclaw -n default -o jsonpath='{.status.availableReplicas}'")
        avail = out.strip("'")
        self.add_result("CoreResources", "SandboxSet openclaw", rc == 0 and avail not in ("", "0"),
                        f"可用副本数: {avail}" if avail else "未就绪")

        rc, out, _ = self.kubectl(
            "get pods -n sandbox-system -l app.kubernetes.io/name=ack-sandbox-manager "
            "-o jsonpath='{.items[*].status.phase}'"
        )
        phases = out.strip("'").split()
        running = sum(1 for p in phases if p == "Running")
        self.add_result("CoreResources", "sandbox-manager Pod", running >= 1,
                        f"{running} 运行中 / 共 {len(phases)}")

        rc, out, _ = self.kubectl("get secret sandbox-manager-tls -n sandbox-system -o name")
        self.add_result("CoreResources", "TLS 证书 Secret", rc == 0,
                        "存在" if rc == 0 else "不存在")

        rc, out, _ = self.kubectl("get nodes -o jsonpath='{.items[*].metadata.name}'")
        nodes = out.strip("'").split()
        unique_zones = set()
        for n in nodes:
            for part in n.split("-"):
                if part in ("i", "j", "k", "b", "g", "h", "l", "m", "n", "f", "d", "e"):
                    zone_suffix = n.rsplit("-", 1)[-1] if "-" in n else ""
                    unique_zones.add(zone_suffix)
                    break
        self.add_result("CoreResources", "多可用区节点", len(unique_zones) >= 2,
                        f"检测到可用区: {unique_zones} from nodes: {nodes}")

    # ==================== 6. Sandbox Pod Annotations (ACS-compatible) ====================
    def test_sandbox_pod_annotations(self):
        print("\n=== 6. Sandbox Pod 注解 (ACS 兼容) ===")

        # Check network.alibabacloud.com/security-group-ids (ACS annotation)
        rc, out, _ = self.kubectl(
            "get pods -n default -l app=openclaw -o jsonpath="
            "'{.items[0].metadata.annotations.network\\.alibabacloud\\.com/security-group-ids}'"
        )
        sg_ids = out.strip("'")
        if rc == 0 and sg_ids and sg_ids.startswith("sg-"):
            self.add_result("Annotations", "security-group-ids 注解", True,
                            f"SGs: {sg_ids}")
            if self.sg_id:
                self.add_result("Annotations", "安全组匹配 Stack 输出",
                                self.sg_id in sg_ids,
                                f"注解: {sg_ids}, Stack: {self.sg_id}")
        else:
            self.add_result("Annotations", "security-group-ids 注解", False,
                            f"缺失或无效: {out}", severity="P0")

        # Check network.alibabacloud.com/vswitch-ids (ACS annotation)
        rc, out, _ = self.kubectl(
            "get pods -n default -l app=openclaw -o jsonpath="
            "'{.items[0].metadata.annotations.network\\.alibabacloud\\.com/vswitch-ids}'"
        )
        vsw_ids = out.strip("'")
        if rc == 0 and vsw_ids and "vsw-" in vsw_ids:
            vsw_list = vsw_ids.split(",")
            self.add_result("Annotations", "vswitch-ids 注解", len(vsw_list) >= 3,
                            f"VSwitches: {vsw_list} ({len(vsw_list)} 条)")
        else:
            self.add_result("Annotations", "vswitch-ids 注解", False,
                            f"缺失或无效: {out}", severity="P0")

        # Check network.alibabacloud.com/security-group-id (actual ENI binding)
        rc, out, _ = self.kubectl(
            "get pods -n default -l app=openclaw -o jsonpath="
            "'{.items[0].metadata.annotations.network\\.alibabacloud\\.com/security-group-id}'"
        )
        actual_sg = out.strip("'")
        if rc == 0 and actual_sg.startswith("sg-"):
            self.add_result("Annotations", "ENI 实际安全组", True,
                            f"实际安全组: {actual_sg}")
            if self.sg_id:
                self.add_result("Annotations", "ENI 安全组匹配 OpenClaw",
                                actual_sg == self.sg_id,
                                f"实际: {actual_sg}, 预期: {self.sg_id}",
                                severity="P0")

    # ==================== 7. Sandbox network connectivity ====================
    def test_sandbox_network_connectivity(self):
        print("\n=== 7. Sandbox Pod 网络连通性测试 ===")

        rc, pod_name, _ = self.kubectl(
            "get pods -n default -l app=openclaw "
            "-o jsonpath='{.items[0].metadata.name}' "
            "--field-selector=status.phase=Running"
        )
        pod_name = pod_name.strip("'")
        if not pod_name or rc != 0:
            self.add_result("NetTest", "查找 Sandbox Pod", False, "无运行中的 Sandbox Pod")
            return
        self.add_result("NetTest", "查找 Sandbox Pod", True, f"使用: {pod_name}")

        rc, pod_ip, _ = self.kubectl(
            f"get pod {pod_name} -n default -o jsonpath='{{.status.podIP}}'"
        )
        pod_ip = pod_ip.strip("'")
        if pod_ip:
            openclaw_cidr = self.stack_params.get("OpenClawCidrBlock", "10.8.0.0/16")
            try:
                oc_net = ipaddress.ip_network(openclaw_cidr, strict=False)
                is_openclaw_cidr = ipaddress.ip_address(pod_ip) in oc_net
            except ValueError:
                is_openclaw_cidr = False
            self.add_result("NetTest", "Pod IP 在 OpenClaw 网段内", is_openclaw_cidr,
                            f"Pod IP: {pod_ip} (预期在 {openclaw_cidr})"
                            + ("" if is_openclaw_cidr else " [不在 OpenClaw 网段!]"),
                            severity="P0")

        ns = "default"
        container = "openclaw"

        def _curl_http_code(url: str, timeout_s: int = 3) -> Tuple[int, str]:
            """Run curl from sandbox pod and return (rc, http_code)"""
            curl_cmd = f"curl -s -o /dev/null -w '%{{http_code}}' --connect-timeout {timeout_s} '{url}'"
            rc, out, _ = self.run_cmd(
                f'kubectl exec {pod_name} -n {ns} -c {container} -- bash -c "{curl_cmd}"',
                timeout=timeout_s + 10
            )
            return rc, out.strip().strip("'")

        # 7a. Public internet access (SHOULD ALLOW)
        rc, http_code = _curl_http_code("https://www.alibaba.com", 5)
        self.add_result("NetTest", "出方向: 公网访问 (alibaba.com)",
                        http_code not in ("000", ""),
                        f"HTTP {http_code}")

        # 7b. DNS resolution (SHOULD ALLOW)
        rc, out, err = self.kubectl(
            f"exec {pod_name} -n {ns} -c {container} -- "
            "bash -c 'getent hosts www.alibaba.com 2>&1 || echo FAIL'",
            timeout=10
        )
        dns_ok = rc == 0 and "FAIL" not in out and len(out.strip()) > 0
        self.add_result("NetTest", "出方向: DNS 解析", dns_ok,
                        f"解析成功: {out.strip()[:100]}" if dns_ok else f"DNS 失败: {(out + err)[:150]}")

        # 7c. Metadata service (SHOULD DENY — 000 超时 or 403 平台拦截均视为通过)
        rc, http_code = _curl_http_code("http://100.100.100.200/latest/meta-data/", 3)
        metadata_blocked = http_code in ("000", "", "403")
        if http_code == "403":
            msg = "HTTP 403 (ACS 平台级拦截, metadata 不可访问)"
        elif metadata_blocked:
            msg = f"HTTP {http_code} (超时 = 安全组拒绝)"
        else:
            msg = f"HTTP {http_code} (metadata 可达, 存在安全风险!)"
        self.add_result("NetTest", "出方向: metadata 拒绝 (100.100.100.200)", metadata_blocked,
                        msg, severity="P0" if http_code == "200" else "P1")

        # 7d. Internal VPC network access (SHOULD DENY via SecurityGroup)
        vpc_cidr = self.vpc_primary_cidr or "192.168.0.0/16"
        try:
            vpc_net = ipaddress.ip_network(vpc_cidr, strict=False)
            test_ip = str(list(vpc_net.hosts())[0])
        except (ValueError, IndexError):
            test_ip = "192.168.0.1"
        rc, http_code = _curl_http_code(f"http://{test_ip}", 3)
        internal_blocked = http_code in ("000", "")
        self.add_result("NetTest", f"出方向: VPC 内网拒绝 ({test_ip})", internal_blocked,
                        f"HTTP {http_code} (超时 = 正确拒绝)" if internal_blocked
                        else f"VPC 内网可达: HTTP {http_code}")

        # 7e. sandbox-manager access test
        rc2, sm_ip, _ = self.kubectl(
            "get pods -n sandbox-system -l app.kubernetes.io/name=sandbox-manager "
            "-o jsonpath='{.items[0].status.podIP}' --field-selector=status.phase=Running"
        )
        sm_ip = sm_ip.strip("'")
        if sm_ip:
            rc, http_code = _curl_http_code(f"http://{sm_ip}:8080", 3)
            sm_blocked = http_code in ("000", "")
            self.add_result("NetTest", f"出方向: sandbox-manager ({sm_ip}) 隔离",
                            sm_blocked,
                            f"HTTP {http_code} (超时 = 正确拒绝)" if sm_blocked
                            else f"sandbox-manager 可达: HTTP {http_code} (需 TrafficPolicy)")

        # 7f. NAT isolation: check external IP (多 endpoint 容错)
        expected_eip = self.stack_outputs.get("OpenClawNatEipAddress", "")
        external_ip = ""
        ip_endpoints = [
            ("https://httpbin.org/ip", "grep -oE '[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+'"),
            ("https://api.ipify.org", "cat"),
            ("https://ifconfig.me", "cat"),
        ]
        for url, parse_cmd in ip_endpoints:
            rc, out, _ = self.kubectl(
                f"exec {pod_name} -n {ns} -c {container} -- "
                f"bash -c 'curl -s --connect-timeout 5 {url} | {parse_cmd}'",
                timeout=15
            )
            candidate = out.strip().split(",")[0].strip()
            if rc == 0 and candidate and re.match(r'^\d+\.\d+\.\d+\.\d+$', candidate):
                external_ip = candidate
                break

        if external_ip:
            if expected_eip:
                eip_match = external_ip == expected_eip
                self.add_result("NetTest", "NAT 隔离: 出口 IP 匹配 OpenClaw EIP",
                                eip_match,
                                f"出口 IP: {external_ip}, 预期 OpenClaw EIP: {expected_eip}"
                                + (" [Pod 不在 OpenClaw VSwitch，SNAT 不会经 OpenClaw NAT]"
                                   if not eip_match else ""),
                                severity="P0")
            else:
                self.add_result("NetTest", "NAT 隔离: 出口 IP",
                                True, f"出口 IP: {external_ip} (需手动验证)")
        else:
            self.add_result("NetTest", "NAT 隔离: 出口 IP 检查", False,
                            "无法确定出口 IP (所有 endpoint 均超时)")

        # 7g. Check Pod VSwitch assignment
        rc, vsw_id, _ = self.kubectl(
            f"get pod {pod_name} -n {ns} -o jsonpath="
            "'{.metadata.annotations.network\\.alibabacloud\\.com/vswitch-id}'"
        )
        vsw_id = vsw_id.strip("'")
        if vsw_id:
            rc2, out2, _ = self.aliyun_cli(
                f"vpc DescribeVSwitches --RegionId {self.region} --VSwitchId {vsw_id}"
            )
            vsw_cidr = ""
            vsw_name = ""
            if rc2 == 0:
                try:
                    vdata = json.loads(out2)
                    vsws = vdata.get("VSwitches", {}).get("VSwitch", [])
                    if vsws:
                        vsw_cidr = vsws[0].get("CidrBlock", "")
                        vsw_name = vsws[0].get("VSwitchName", "")
                except (json.JSONDecodeError, KeyError):
                    pass
            openclaw_cidr_param = self.stack_params.get("OpenClawCidrBlock", "10.8.0.0/16")
            try:
                oc_net = ipaddress.ip_network(openclaw_cidr_param, strict=False)
                vsw_net = ipaddress.ip_network(vsw_cidr, strict=False) if vsw_cidr else None
                cidr_match = vsw_net.subnet_of(oc_net) if vsw_net else False
            except ValueError:
                cidr_match = False
            is_openclaw_vsw = "openclaw" in vsw_name.lower() or cidr_match
            self.add_result("NetTest", "Pod 所在 VSwitch 属于 OpenClaw",
                            is_openclaw_vsw,
                            f"VSwitch: {vsw_id} ({vsw_name}, {vsw_cidr})"
                            + (" [ACS 可能不支持 PodVSwitch，Pod 分配至业务 VSwitch]"
                               if not is_openclaw_vsw else ""),
                            severity="P0")

        # 7h. Check which SecurityGroup is actually applied to ENI
        rc, eni_id, _ = self.kubectl(
            f"get pod {pod_name} -n {ns} -o jsonpath="
            "'{.metadata.annotations.network\\.alibabacloud\\.com/allocated-eni-id}'"
        )
        eni_id = eni_id.strip("'")
        if eni_id:
            rc2, out2, _ = self.aliyun_cli(
                f"ecs DescribeNetworkInterfaces --RegionId {self.region} "
                f"--NetworkInterfaceId.1 {eni_id}"
            )
            if rc2 == 0:
                try:
                    eni_data = json.loads(out2)
                    enis = eni_data.get("NetworkInterfaceSets", {}).get("NetworkInterfaceSet", [])
                    if enis:
                        eni_sgs = enis[0].get("SecurityGroupIds", {}).get("SecurityGroupId", [])
                        custom_sg_applied = self.sg_id in eni_sgs if self.sg_id else False
                        self.add_result("NetTest", "ENI 已应用自定义安全组",
                                        custom_sg_applied,
                                        f"ENI 安全组: {eni_sgs}, 预期: {self.sg_id}"
                                        + (" [ACS 忽略 k8s.aliyun.com/eni-security-group-id 注解]"
                                           if not custom_sg_applied else ""),
                                        severity="P0")
                except (json.JSONDecodeError, KeyError):
                    pass

    # ==================== 8. Security hardening ====================
    def test_sandbox_security_hardening(self):
        print("\n=== 8. Sandbox 安全加固 ===")

        rc, out, _ = self.kubectl(
            "get pods -n default -l app=openclaw "
            "-o jsonpath='{.items[0].spec.automountServiceAccountToken}' "
            "--field-selector=status.phase=Running"
        )
        val = out.strip("'")
        self.add_result("Hardening", "automountServiceAccountToken=false",
                        val == "false",
                        f"值: {val}" if val else "未设置 (默认 true)")

        rc, out, _ = self.kubectl(
            "get pods -n default -l app=openclaw "
            "-o jsonpath='{.items[0].spec.enableServiceLinks}' "
            "--field-selector=status.phase=Running"
        )
        val = out.strip("'")
        self.add_result("Hardening", "enableServiceLinks=false",
                        val == "false",
                        f"值: {val}" if val else "未设置 (默认 true)")

        # Check K8s token mount
        rc, out, _ = self.kubectl(
            "get pods -n default -l app=openclaw "
            "-o jsonpath='{.items[0].spec.volumes[*].name}' "
            "--field-selector=status.phase=Running"
        )
        volumes = out.strip("'").split()
        has_sa_token = any("kube-api-access" in v for v in volumes)
        self.add_result("Hardening", "无 ServiceAccount Token 挂载",
                        not has_sa_token,
                        f"Volumes: {volumes}" if not has_sa_token
                        else "警告: 存在 kube-api-access 卷")

    # ==================== 9. ALB & Ingress ====================
    def test_alb_ingress(self):
        print("\n=== 9. ALB & Ingress 配置 ===")

        rc, out, _ = self.kubectl("get ingress -n sandbox-system -o json")
        if rc == 0:
            try:
                ingresses = json.loads(out)
                items = ingresses.get("items", [])
                self.add_result("ALB", "Ingress 资源", len(items) > 0,
                                f"发现 {len(items)} 个 Ingress")

                for ing in items:
                    name = ing.get("metadata", {}).get("name", "unknown")
                    annotations = ing.get("metadata", {}).get("annotations", {})
                    rules = ing.get("spec", {}).get("rules", [])
                    tls = ing.get("spec", {}).get("tls", [])
                    ing_class = annotations.get("kubernetes.io/ingress.class", "")
                    self.add_result("ALB", f"Ingress '{name}'", len(rules) > 0,
                                    f"class={ing_class}, rules={len(rules)}, tls={len(tls)}")
            except json.JSONDecodeError:
                self.add_result("ALB", "Ingress 解析", False, "无效 JSON")
        else:
            self.add_result("ALB", "Ingress 资源", False, "未发现 Ingress")

        rc, out, _ = self.kubectl("get albconfig alb -o jsonpath='{.spec.listeners[*].port}'")
        ports = out.strip("'").split()
        has_443 = "443" in ports
        self.add_result("ALB", "ALB 监听端口 443", has_443,
                        f"端口: {ports}" if ports else "未发现监听器")

        alb_dns = self.stack_outputs.get("ALB_DNS_Name", "")
        if alb_dns:
            self.add_result("ALB", "ALB DNS 名称", True, f"DNS: {alb_dns}")

    # ==================== 10. PrivateZone DNS ====================
    def test_privatezone(self):
        print("\n=== 10. PrivateZone DNS 解析 ===")

        rc, pod_name, _ = self.kubectl(
            "get pods -n default -l app=openclaw "
            "-o jsonpath='{.items[0].metadata.name}' "
            "--field-selector=status.phase=Running"
        )
        pod_name = pod_name.strip("'")
        if not pod_name:
            self.add_result("DNS", "PrivateZone 测试", False, "无 Sandbox Pod")
            return

        domain = self.stack_params.get("E2BDomainAddress", "agent-vpc.infra")

        rc, out, _ = self.run_cmd(
            f'kubectl exec {pod_name} -n default -c openclaw -- '
            f'bash -c "getent hosts test.{domain} 2>&1 || echo LOOKUP_FAILED"',
            timeout=10
        )
        resolved = rc == 0 and "LOOKUP_FAILED" not in out and len(out.strip()) > 5
        self.add_result("DNS", f"PrivateZone 解析 *.{domain}",
                        resolved,
                        f"解析成功: {out.strip()[:100]}" if resolved
                        else f"无法解析: {out[:200]}")

        rc, out, _ = self.kubectl(
            f"exec {pod_name} -n default -c openclaw -- cat /etc/resolv.conf",
            timeout=10
        )
        if rc == 0:
            self.add_result("DNS", "resolv.conf 配置", True,
                            out.replace('\n', ' | ')[:200])

    # ==================== 11. Poseidon / TrafficPolicy (skippable) ====================
    def test_traffic_policies(self):
        if self.skip_poseidon:
            print("\n=== 11. TrafficPolicy / Poseidon (跳过 - 等待发布) ===")
            self.add_result("TrafficPolicy", "Poseidon 组件", True,
                            "跳过 - 等待组件发布", severity="INFO")
            return

        print("\n=== 11. TrafficPolicy / GlobalTrafficPolicy ===")

        rc, out, _ = self.kubectl("get crd globaltrafficpolicies.network.alibabacloud.com -o name")
        self.add_result("TrafficPolicy", "GlobalTrafficPolicy CRD 已注册", rc == 0,
                        "已注册" if rc == 0 else "未注册")

        rc, out, _ = self.kubectl("get globaltrafficpolicy -A -o json")
        if rc == 0:
            try:
                data = json.loads(out)
                items = data.get("items", [])
                self.add_result("TrafficPolicy", "GlobalTrafficPolicy 实例",
                                len(items) > 0,
                                f"发现 {len(items)} 个")
            except json.JSONDecodeError:
                self.add_result("TrafficPolicy", "GlobalTrafficPolicy 解析", False, "无效 JSON")

        rc, out, _ = self.kubectl("get trafficpolicy -n default -o json")
        if rc == 0:
            try:
                data = json.loads(out)
                items = data.get("items", [])
                self.add_result("TrafficPolicy", "TrafficPolicy 实例",
                                len(items) > 0,
                                f"发现 {len(items)} 个")
            except json.JSONDecodeError:
                self.add_result("TrafficPolicy", "TrafficPolicy 解析", False, "无效 JSON")

    # ==================== 运行所有测试 ====================
    def run_all(self):
        print("=" * 70)
        print(" OpenClaw 生产模版网络隔离验证")
        print("=" * 70)
        print(f"  Stack:       {self.stack_name or 'N/A'}")
        print(f"  集群 ID:     {self.cluster_id or '(将从 Stack 加载)'}")
        print(f"  地域:        {self.region or 'N/A'}")
        print(f"  模式:        {'仅 kubectl (无云 API)' if self.kubectl_only else '完整模式 (云 API + kubectl)'}")
        print(f"  Poseidon:    {'跳过' if self.skip_poseidon else '启用'}")
        print("=" * 70)

        if not self.kubectl_only and self.stack_name:
            self.load_stack_info()

        if not self.test_kubectl_connectivity():
            print("\n已终止: 无法连接集群")
            return False

        if not self.kubectl_only:
            self.test_vpc_vswitch_topology()
            self.test_security_group_rules()
            self.test_nat_gateway_isolation()
            self.test_route_table_isolation()

        self.test_core_resources()
        self.test_sandbox_pod_annotations()
        self.test_sandbox_network_connectivity()
        self.test_sandbox_security_hardening()
        self.test_alb_ingress()
        self.test_privatezone()
        self.test_traffic_policies()

        # Summary
        print("\n" + "=" * 70)
        print(" 测试结果汇总")
        print("=" * 70)

        categories = {}
        for r in self.results:
            cat = r.category or "Other"
            if cat not in categories:
                categories[cat] = {"pass": 0, "fail": 0, "p0_fail": 0}
            if r.passed:
                categories[cat]["pass"] += 1
            else:
                categories[cat]["fail"] += 1
                if r.severity == "P0":
                    categories[cat]["p0_fail"] += 1

        total_pass = sum(c["pass"] for c in categories.values())
        total_fail = sum(c["fail"] for c in categories.values())
        total_p0 = sum(c["p0_fail"] for c in categories.values())

        for cat, counts in sorted(categories.items()):
            status = "\033[32m正常\033[0m" if counts["fail"] == 0 else "\033[31m有问题\033[0m"
            p0_tag = f" (\033[31m{counts['p0_fail']} P0\033[0m)" if counts["p0_fail"] > 0 else ""
            print(f"  [{status}] {cat}: {counts['pass']} 通过, {counts['fail']} 失败{p0_tag}")

        print(f"\n  总计: \033[32m{total_pass} 通过\033[0m, "
              f"\033[31m{total_fail} 失败\033[0m"
              f"{f' ({total_p0} P0)' if total_p0 else ''}")

        if total_fail > 0:
            print(f"\n  --- 失败项 ({total_fail}) ---")
            for r in self.results:
                if not r.passed:
                    sev = f"[{r.severity}]" if r.severity != "P1" else ""
                    print(f"    {sev} [{r.category}] {r.name}: {r.message}")

        return total_fail == 0


def main():
    parser = argparse.ArgumentParser(description="OpenClaw 生产模版网络隔离验证")
    parser.add_argument("--stack-name", default="", help="ROS Stack 名称或 ID")
    parser.add_argument("--cluster-id", default="", help="ACS/ACK 集群 ID")
    parser.add_argument("--region", default="cn-hangzhou", help="阿里云地域")
    parser.add_argument("--sg-id", default="", help="OpenClaw 安全组 ID")
    parser.add_argument("--kubectl-only", action="store_true",
                        help="仅运行 kubectl 相关测试")
    parser.add_argument("--skip-poseidon", action="store_true", default=True,
                        help="跳过 Poseidon/TrafficPolicy 测试 (默认: 是)")
    parser.add_argument("--test-poseidon", action="store_true",
                        help="启用 Poseidon/TrafficPolicy 测试")
    args = parser.parse_args()

    skip_poseidon = not args.test_poseidon

    validator = NetworkValidator(
        stack_name=args.stack_name,
        cluster_id=args.cluster_id,
        region=args.region,
        sg_id=args.sg_id,
        kubectl_only=args.kubectl_only,
        skip_poseidon=skip_poseidon,
    )
    success = validator.run_all()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

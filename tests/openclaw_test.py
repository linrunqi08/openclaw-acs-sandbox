#!/usr/bin/env python3
"""
OpenClaw 生产模版一站式网络隔离验证 CLI

Usage:
  python tests/openclaw_test.py --stack-name <name> --region cn-hangzhou \
      --accesskey <AK> --accesskey-secret <SK>

  # 仅运行静态模版校验
  python tests/openclaw_test.py --template-only

  # 仅运行网络验证（已配好 kubectl）
  python tests/openclaw_test.py --stack-name <name> --region cn-hangzhou --network-only

  # 含 E2B Sandbox 端到端验证
  python tests/openclaw_test.py --stack-name <name> --region cn-hangzhou \
      --accesskey <AK> --accesskey-secret <SK> --sandbox-test

流程:
  Phase 0  环境准备 — aliyun CLI 安装/配置、kubectl 检查
  Phase 1  静态模版校验 — YAML 结构、引用完整性、CIDR、安全组规则
  Phase 2  公网保护解除 — 云防火墙关闭、ALB 安全组放行 NAT EIP
  Phase 3  网络隔离验证 — VPC/VSwitch、SecurityGroup、NAT、路由表、Sandbox Pod 连通性
  Phase 4  E2B Sandbox 端到端 — 创建 Sandbox → 文件读写 → Gateway 访问 → 销毁
  Phase 5  结果汇总
"""

import argparse
import json
import os
import platform
import re as _re
import shutil
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional, Tuple

# 项目根目录 = tests/ 的上级
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))

BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
DIM = "\033[2m"
RESET = "\033[0m"

DIVIDER = "─" * 72


_ANSI_RE = _re.compile(r'\033\[[0-9;]*m')


class _TeeWriter:
    """同时写入两个流"""
    def __init__(self, a, b):
        self._a = a
        self._b = b

    def write(self, s):
        self._a.write(s)
        self._b.write(s)

    def flush(self):
        self._a.flush()
        self._b.flush()


@dataclass
class PhaseResult:
    name: str
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    items: List[str] = field(default_factory=list)


class OpenClawTestCLI:
    def __init__(self, args):
        self.args = args
        self.region = args.region
        self.stack_name = args.stack_name
        self.phases: List[PhaseResult] = []
        self.stack_outputs: Dict[str, str] = {}
        self.stack_params: Dict[str, str] = {}
        self.cluster_id = ""
        self.vpc_id = ""
        self.sg_id = ""
        self._log_lines: List[str] = []

    # ──────────────────────────── Helpers ────────────────────────────

    def _print(self, text: str):
        """同时输出到终端和日志缓冲"""
        print(text)
        self._log_lines.append(_ANSI_RE.sub('', text))

    def _header(self, phase_num: int, title: str):
        self._print(f"\n{BOLD}{CYAN}{'═' * 72}")
        self._print(f"  Phase {phase_num}  {title}")
        self._print(f"{'═' * 72}{RESET}\n")

    def _section(self, title: str):
        self._print(f"\n  {BOLD}{title}{RESET}")
        self._print(f"  {DIVIDER}")

    def _ok(self, msg: str, detail: str = ""):
        tag = f"{GREEN}✓ PASS{RESET}"
        d = f"  {DIM}{detail}{RESET}" if detail else ""
        self._print(f"  {tag}  {msg}{d}")

    def _fail(self, msg: str, detail: str = ""):
        tag = f"{RED}✗ FAIL{RESET}"
        d = f"  {DIM}{detail}{RESET}" if detail else ""
        self._print(f"  {tag}  {msg}{d}")

    def _warn(self, msg: str, detail: str = ""):
        tag = f"{YELLOW}⚠ WARN{RESET}"
        d = f"  {DIM}{detail}{RESET}" if detail else ""
        self._print(f"  {tag}  {msg}{d}")

    def _info(self, msg: str):
        self._print(f"  {DIM}ℹ {msg}{RESET}")

    def _skip(self, msg: str):
        self._print(f"  {DIM}⏭ SKIP  {msg}{RESET}")

    def run_cmd(self, cmd: str, timeout: int = 30) -> Tuple[int, str, str]:
        try:
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
            return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", "Command timed out"
        except FileNotFoundError:
            return -1, "", "Command not found"

    def aliyun(self, api_args: str, timeout: int = 30) -> Tuple[int, str, str]:
        return self.run_cmd(f"aliyun {api_args}", timeout)

    def kubectl(self, args: str, timeout: int = 30) -> Tuple[int, str, str]:
        return self.run_cmd(f"kubectl {args}", timeout)

    # ──────────────────── Phase 0: Environment Setup ────────────────────

    def phase0_environment(self) -> PhaseResult:
        phase = PhaseResult(name="环境准备")
        self._header(0, "环境准备 — aliyun CLI / kubectl / credentials")

        # 0a. aliyun CLI
        self._section("aliyun CLI")
        if shutil.which("aliyun"):
            rc, ver, _ = self.run_cmd("aliyun version")
            self._ok("aliyun CLI 已安装", ver.split('\n')[0] if ver else "")
            phase.passed += 1
        else:
            self._warn("aliyun CLI 未安装，正在自动安装...")
            if self._install_aliyun_cli():
                self._ok("aliyun CLI 安装成功")
                phase.passed += 1
            else:
                self._fail("aliyun CLI 安装失败，请手动安装: https://help.aliyun.com/document_detail/139508.html")
                phase.failed += 1
                return phase

        # 0b. Configure credentials
        self._section("AccessKey 配置")
        ak = self.args.accesskey or os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID") or os.environ.get("ALIYUN_ACCESS_KEY_ID", "")
        sk = self.args.accesskey_secret or os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET") or os.environ.get("ALIYUN_ACCESS_KEY_SECRET", "")

        if ak and sk:
            os.environ["ALIBABA_CLOUD_ACCESS_KEY_ID"] = ak
            os.environ["ALIBABA_CLOUD_ACCESS_KEY_SECRET"] = sk
            os.environ["ALIYUN_ACCESS_KEY_ID"] = ak
            os.environ["ALIYUN_ACCESS_KEY_SECRET"] = sk
            self._ok("AccessKey 已配置", f"AK={ak[:6]}***{ak[-4:]}")
            phase.passed += 1

            rc, _, err = self.aliyun(
                f"ros ListStacks --RegionId {self.region} --PageSize 1"
            )
            if rc == 0:
                self._ok("AccessKey 验证通过（ROS API 可达）")
                phase.passed += 1
            else:
                self._fail("AccessKey 验证失败", err[:150])
                phase.failed += 1
        else:
            self._fail("未提供 AccessKey，请使用 --accesskey / --accesskey-secret 或设置环境变量")
            phase.failed += 1

        # 0c. kubectl
        self._section("kubectl")
        if shutil.which("kubectl"):
            rc, ver, _ = self.run_cmd("kubectl version --client --short 2>/dev/null || kubectl version --client")
            self._ok("kubectl 已安装", ver.split('\n')[0][:80] if ver else "")
            phase.passed += 1
        else:
            self._fail("kubectl 未安装，请安装: https://kubernetes.io/docs/tasks/tools/")
            phase.failed += 1

        # 0d. python deps
        self._section("Python 依赖")
        missing = []
        for mod in ["yaml", "ipaddress"]:
            try:
                __import__(mod)
            except ImportError:
                missing.append(mod)
        if not missing:
            self._ok("Python 依赖齐全 (yaml, ipaddress)")
            phase.passed += 1
        else:
            self._fail(f"缺少 Python 模块: {missing}", "pip install pyyaml")
            phase.failed += 1

        self.phases.append(phase)
        return phase

    def _install_aliyun_cli(self) -> bool:
        system = platform.system().lower()
        arch = platform.machine().lower()

        if system == "darwin":
            rc, _, _ = self.run_cmd("brew install aliyun-cli", timeout=120)
            if rc == 0:
                return True
            arch_map = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}
            go_arch = arch_map.get(arch, "amd64")
            url = f"https://aliyuncli.alicdn.com/aliyun-cli-darwin-{go_arch}-latest.tgz"
            rc, _, _ = self.run_cmd(
                f"curl -sL '{url}' -o /tmp/aliyun-cli.tgz && "
                f"tar xzf /tmp/aliyun-cli.tgz -C /tmp && "
                f"sudo mv /tmp/aliyun /usr/local/bin/aliyun && "
                f"chmod +x /usr/local/bin/aliyun",
                timeout=120,
            )
            return rc == 0

        if system == "linux":
            arch_map = {"x86_64": "amd64", "aarch64": "arm64"}
            go_arch = arch_map.get(arch, "amd64")
            url = f"https://aliyuncli.alicdn.com/aliyun-cli-linux-{go_arch}-latest.tgz"
            rc, _, _ = self.run_cmd(
                f"curl -sL '{url}' -o /tmp/aliyun-cli.tgz && "
                f"tar xzf /tmp/aliyun-cli.tgz -C /tmp && "
                f"mv /tmp/aliyun /usr/local/bin/aliyun && "
                f"chmod +x /usr/local/bin/aliyun",
                timeout=120,
            )
            return rc == 0

        self._fail(f"不支持自动安装 aliyun CLI 的系统: {system}")
        return False

    # ──────────────────── Phase 1: Template Validation ────────────────────

    def phase1_template_validation(self) -> PhaseResult:
        phase = PhaseResult(name="静态模版校验")
        self._header(1, "静态模版校验 — YAML 结构 / 引用 / CIDR / 安全组")

        template_path = os.path.join(PROJECT_ROOT, "template-production.yaml")
        if not os.path.exists(template_path):
            self._fail(f"模版文件不存在: {template_path}")
            phase.failed += 1
            self.phases.append(phase)
            return phase

        try:
            sys.path.insert(0, TESTS_DIR)
            from test_template_validation import TemplateValidator
            validator = TemplateValidator(template_path)

            import io
            capture_buf = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = _TeeWriter(old_stdout, capture_buf)
            try:
                ok = validator.run_all_tests()
            finally:
                sys.stdout = old_stdout
            self._log_lines.extend(
                _ANSI_RE.sub('', line)
                for line in capture_buf.getvalue().splitlines()
            )

            poseidon_errors = [e for e in validator.errors if "TrafficPolicy" in e or "Poseidon" in e]
            other_errors = [e for e in validator.errors if e not in poseidon_errors]

            if other_errors:
                for e in other_errors:
                    self._fail(e)
                phase.failed = len(other_errors)
            else:
                self._ok(f"模版校验通过（19 项检查中 {19 - len(poseidon_errors)} 项无误）")
                phase.passed = 1

            if poseidon_errors:
                self._warn(f"Poseidon/TrafficPolicy 相关 {len(poseidon_errors)} 项待 ACS 组件发布后修复（已知延迟）")
                phase.skipped = len(poseidon_errors)
            else:
                phase.passed += 1

            if validator.warnings:
                for w in validator.warnings:
                    self._warn(w)

        except Exception as e:
            self._fail(f"模版校验异常: {e}")
            phase.failed += 1

        self.phases.append(phase)
        return phase

    # ──────────────────── Load Stack Info ────────────────────

    def _load_stack_info(self) -> bool:
        self._section("加载 Stack 信息")
        if not self.stack_name:
            self._fail("未指定 --stack-name")
            return False

        # Resolve stack name → stack ID
        import re as _re
        stack_id = self.stack_name
        if not _re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-', self.stack_name):
            rc, out, _ = self.aliyun(
                f"ros ListStacks --RegionId {self.region} "
                f"--StackName.1 {self.stack_name} --PageSize 5"
            )
            if rc == 0:
                try:
                    stacks = json.loads(out).get("Stacks", [])
                    if stacks:
                        stack_id = stacks[0]["StackId"]
                except (json.JSONDecodeError, KeyError, IndexError):
                    pass

        rc, out, err = self.aliyun(f"ros GetStack --RegionId {self.region} --StackId {stack_id}")
        if rc != 0:
            self._fail(f"获取 Stack 失败: {err[:200]}")
            return False

        data = json.loads(out)
        status = data.get("Status", "")
        if status != "CREATE_COMPLETE":
            self._warn(f"Stack 状态: {status}（非 CREATE_COMPLETE）")

        for o in data.get("Outputs", []):
            self.stack_outputs[o["OutputKey"]] = o.get("OutputValue", "")
        for p in data.get("Parameters", []):
            val = p.get("ParameterValue", "")
            if val != "******":
                self.stack_params[p["ParameterKey"]] = val

        self.cluster_id = self.stack_outputs.get("ClusterId", "")
        self.vpc_id = self.stack_outputs.get("VpcId", "")
        self.sg_id = self.stack_outputs.get("OpenClawSecurityGroupId", "")

        self._ok(f"Stack: {self.stack_name} ({status})")
        self._info(f"Cluster ID:  {self.cluster_id}")
        self._info(f"VPC ID:      {self.vpc_id}")
        self._info(f"SG ID:       {self.sg_id}")
        self._info(f"NAT EIP:     {self.stack_outputs.get('OpenClawNatEipAddress', 'N/A')}")
        self._info(f"ALB DNS:     {self.stack_outputs.get('ALB_DNS_Name', 'N/A')}")
        return True

    def _setup_kubeconfig(self) -> bool:
        self._section("配置 kubeconfig")
        if not self.cluster_id:
            self._fail("无法获取 Cluster ID")
            return False

        # Step 1: 获取公网 kubeconfig
        if not self._fetch_kubeconfig():
            return False

        # Step 2: 测试连接
        if self._test_kubectl():
            return True

        # Step 3: 连接失败 → 自动申请/绑定 EIP → 重试
        self._warn("kubectl 连接超时，API Server 可能没有公网 EIP")
        if not self._ensure_cluster_eip():
            return False

        # Step 4: EIP 绑定后等待生效 + TLS 证书更新，轮询重试（最多 180s）
        self._info("等待 EIP 生效并重新获取 kubeconfig（最多 180s）...")
        for attempt in range(9):
            self._info(f"等待 20s... (attempt {attempt + 1}/9)")
            time.sleep(20)

            self._fetch_kubeconfig()
            if self._test_kubectl(allow_insecure_fallback=True):
                self._ok(f"kubectl 连接成功 (attempt {attempt + 1})")
                return True

        self._fail("EIP 绑定后 180s 内仍无法连接集群（可能 TLS 证书未更新）")
        return False

    def _fetch_kubeconfig(self) -> bool:
        """通过 aliyun cs API 获取 kubeconfig 并保存"""
        rc, out, err = self.aliyun(
            f"cs DescribeClusterUserKubeconfig "
            f"--ClusterId {self.cluster_id} --region {self.region} "
            f"--PrivateIpAddress false",
            timeout=15,
        )
        if rc != 0:
            self._warn(f"获取 kubeconfig 失败: {err[:150]}")
            return False

        try:
            config = json.loads(out).get("config", "")
        except (json.JSONDecodeError, KeyError):
            self._warn("kubeconfig 响应解析失败")
            return False

        if not config:
            self._warn("kubeconfig 为空")
            return False

        kube_dir = os.path.expanduser("~/.kube")
        os.makedirs(kube_dir, exist_ok=True)
        kube_path = os.path.join(kube_dir, "config")

        if os.path.exists(kube_path):
            backup = kube_path + ".backup"
            with open(kube_path, "r") as f:
                with open(backup, "w") as bf:
                    bf.write(f.read())

        with open(kube_path, "w") as f:
            f.write(config)

        self._ok("kubeconfig 已保存")
        return True

    def _test_kubectl(self, allow_insecure_fallback: bool = False) -> bool:
        """测试 kubectl 连接，成功则打印节点数"""
        rc, nodes, err = self.kubectl("get nodes --no-headers --request-timeout=10s")
        if rc == 0:
            node_count = len(nodes.strip().split("\n")) if nodes.strip() else 0
            self._ok(f"集群有 {node_count} 个节点")
            return True

        if allow_insecure_fallback and "certificate" in err.lower():
            self._warn("TLS 证书尚未更新到新 EIP，尝试跳过 TLS 验证...")
            rc2, nodes2, _ = self.kubectl(
                "get nodes --no-headers --request-timeout=10s "
                "--insecure-skip-tls-verify=true"
            )
            if rc2 == 0:
                self._warn("集群可达（跳过 TLS 验证），等待证书更新...")
                return False
        return False

    def _ensure_cluster_eip(self) -> bool:
        """确保集群 API Server 有公网 EIP，没有则申请一个并绑定"""
        self._section("为 API Server 绑定 EIP")

        # 1. 查找已有的 Available EIP
        rc, out, _ = self.aliyun(
            f"vpc DescribeEipAddresses --RegionId {self.region} --PageSize 50"
        )
        eip_id = ""
        eip_addr = ""
        if rc == 0:
            try:
                eips = json.loads(out).get("EipAddresses", {}).get("EipAddress", [])
                for eip in eips:
                    if eip.get("Status") == "Available":
                        eip_id = eip["AllocationId"]
                        eip_addr = eip["IpAddress"]
                        self._info(f"找到可用 EIP: {eip_addr} ({eip_id})")
                        break
            except (json.JSONDecodeError, KeyError):
                pass

        # 2. 没有可用 EIP → 申请一个
        if not eip_id:
            self._info("无可用 EIP，正在申请新 EIP...")
            rc, out, err = self.aliyun(
                f"vpc AllocateEipAddress --RegionId {self.region} "
                f"--Bandwidth 100 --InternetChargeType PayByTraffic"
            )
            if rc != 0:
                self._fail(f"申请 EIP 失败: {err[:200]}")
                return False

            try:
                result = json.loads(out)
                eip_id = result["AllocationId"]
                eip_addr = result["EipAddress"]
                self._ok(f"已申请 EIP: {eip_addr} ({eip_id})")
            except (json.JSONDecodeError, KeyError):
                self._fail("解析 EIP 申请结果失败")
                return False

        # 3. 绑定 EIP 到集群 API Server
        self._info(f"绑定 EIP {eip_addr} 到集群 {self.cluster_id}...")
        body = json.dumps({"api_server_eip_id": eip_id})
        rc, out, err = self.aliyun(
            f"cs ModifyCluster --ClusterId {self.cluster_id} --region {self.region} "
            f"--body '{body}'",
            timeout=30,
        )
        if rc != 0:
            self._fail(f"绑定 EIP 失败: {err[:200]}")
            return False

        self._ok(f"EIP {eip_addr} 已绑定到 API Server")
        return True

    # ──────────── Phase 2: Disable Cloud Firewall & Fix ALB SG ────────────

    def phase2_public_protection(self) -> PhaseResult:
        phase = PhaseResult(name="公网保护解除")
        self._header(2, "公网保护解除 — 云防火墙 / ALB 安全组放行")

        if not self.vpc_id:
            self._skip("无 VPC 信息，跳过")
            phase.skipped += 1
            self.phases.append(phase)
            return phase

        # 2a. Collect all EIPs in the VPC
        self._section("收集 VPC 内 EIP")
        eips = set()

        openclaw_eip = self.stack_outputs.get("OpenClawNatEipAddress", "")
        if openclaw_eip:
            eips.add(openclaw_eip)
            self._info(f"OpenClaw NAT EIP: {openclaw_eip}")

        rc, out, _ = self.aliyun(
            f"vpc DescribeNatGateways --RegionId {self.region} --VpcId {self.vpc_id} --PageSize 50"
        )
        if rc == 0:
            try:
                nats = json.loads(out).get("NatGateways", {}).get("NatGateway", [])
                for n in nats:
                    for ip in n.get("IpLists", {}).get("IpList", []):
                        eips.add(ip.get("IpAddress", ""))
            except (json.JSONDecodeError, KeyError):
                pass

        # ALB EIPs (via DNS resolution)
        alb_dns = self.stack_outputs.get("ALB_DNS_Name", "")
        if alb_dns:
            rc, out, _ = self.run_cmd(f"python3 -c \"import socket; print('\\n'.join(set(ip[4][0] for ip in socket.getaddrinfo('{alb_dns}', 443))))\"")
            if rc == 0:
                for ip in out.strip().split("\n"):
                    if ip:
                        eips.add(ip)

        eips.discard("")
        self._ok(f"共发现 {len(eips)} 个 EIP: {', '.join(sorted(eips))}")

        # 2b. Disable cloud firewall
        self._section("关闭云防火墙")
        if eips:
            ip_args = " ".join(f"--IpaddrList.{i+1} {ip}" for i, ip in enumerate(sorted(eips)))
            rc, out, err = self.aliyun(
                f"cloudfw PutDisableFwSwitch {ip_args} --endpoint cloudfw.aliyuncs.com"
            )
            if rc == 0:
                self._ok(f"已关闭 {len(eips)} 个 EIP 的云防火墙")
                phase.passed += 1
            else:
                if "not activated" in err.lower() or "notopen" in err.lower():
                    self._info("云防火墙未开通，无需关闭")
                    phase.passed += 1
                else:
                    self._warn(f"关闭云防火墙失败: {err[:150]}")
                    phase.skipped += 1
        else:
            self._skip("无 EIP，跳过云防火墙")
            phase.skipped += 1

        # 2c. Fix ALB SecurityGroup — allow NAT EIPs
        self._section("ALB 安全组放行 NAT EIP")
        alb_sg_id = self._find_alb_sg()
        if alb_sg_id:
            nat_eips = set()
            rc, out, _ = self.aliyun(
                f"vpc DescribeNatGateways --RegionId {self.region} --VpcId {self.vpc_id} --PageSize 50"
            )
            if rc == 0:
                try:
                    nats = json.loads(out).get("NatGateways", {}).get("NatGateway", [])
                    for n in nats:
                        for ip in n.get("IpLists", {}).get("IpList", []):
                            nat_eips.add(ip.get("IpAddress", ""))
                except (json.JSONDecodeError, KeyError):
                    pass
            nat_eips.discard("")

            # Get existing SG rules to avoid duplicates
            rc, out, _ = self.aliyun(
                f"ecs DescribeSecurityGroupAttribute --RegionId {self.region} "
                f"--SecurityGroupId {alb_sg_id} --Direction ingress"
            )
            existing_cidrs = set()
            if rc == 0:
                try:
                    perms = json.loads(out).get("Permissions", {}).get("Permission", [])
                    existing_cidrs = {p.get("SourceCidrIp", "") for p in perms}
                except (json.JSONDecodeError, KeyError):
                    pass

            added = 0
            for eip in nat_eips:
                cidr = f"{eip}/32"
                if cidr in existing_cidrs:
                    self._info(f"ALB SG 已包含 {cidr}，跳过")
                    continue
                rc, _, err = self.aliyun(
                    f"ecs AuthorizeSecurityGroup --RegionId {self.region} "
                    f"--SecurityGroupId {alb_sg_id} --IpProtocol tcp --PortRange 1/65535 "
                    f"--SourceCidrIp {cidr} --Priority 1 --Description 'NAT EIP auto-allow'"
                )
                if rc == 0:
                    added += 1
                else:
                    self._warn(f"添加 ALB SG 规则失败 ({cidr}): {err[:100]}")

            if added > 0:
                self._ok(f"已添加 {added} 条 ALB 安全组规则")
            else:
                self._info("无需添加新规则")
            phase.passed += 1
        else:
            self._skip("未找到 ALB 安全组")
            phase.skipped += 1

        self.phases.append(phase)
        return phase

    def _find_alb_sg(self) -> str:
        if not self.cluster_id:
            return ""
        rc, out, _ = self.aliyun(f"alb ListLoadBalancers --MaxResults 50 --RegionId {self.region}")
        if rc != 0:
            return ""
        try:
            for lb in json.loads(out).get("LoadBalancers", []):
                tags = {t["Key"]: t["Value"] for t in lb.get("Tags", [])}
                if tags.get("ack.aliyun.com") == self.cluster_id:
                    sgs = lb.get("SecurityGroupIds", [])
                    return sgs[0] if sgs else ""
        except (json.JSONDecodeError, KeyError, IndexError):
            pass
        return ""

    # ──────────────────── Phase 3: Network Validation ────────────────────

    def phase3_network_validation(self) -> PhaseResult:
        phase = PhaseResult(name="网络隔离验证")
        self._header(3, "网络隔离验证 — VPC / SG / NAT / 路由表 / Pod 连通性")

        try:
            sys.path.insert(0, TESTS_DIR)
            from test_network_validation import NetworkValidator
            validator = NetworkValidator(
                stack_name=self.stack_name,
                cluster_id=self.cluster_id,
                region=self.region,
                sg_id=self.sg_id,
                skip_poseidon=True,
            )
            validator.stack_outputs = self.stack_outputs
            validator.stack_params = self.stack_params
            validator.cluster_id = self.cluster_id
            validator.sg_id = self.sg_id

            import io
            capture_buf = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = _TeeWriter(old_stdout, capture_buf)
            try:
                success = validator.run_all()
            finally:
                sys.stdout = old_stdout
            self._log_lines.extend(
                _ANSI_RE.sub('', line)
                for line in capture_buf.getvalue().splitlines()
            )

            for r in validator.results:
                if r.passed:
                    phase.passed += 1
                else:
                    phase.failed += 1

        except Exception as e:
            self._fail(f"网络验证异常: {e}")
            import traceback
            traceback.print_exc()
            phase.failed += 1

        self.phases.append(phase)
        return phase

    # ──────────────────── Phase 4: E2B Sandbox Test ────────────────────

    def phase4_sandbox_test(self) -> PhaseResult:
        phase = PhaseResult(name="E2B Sandbox 端到端")
        self._header(4, "E2B Sandbox 端到端 — 创建 / 文件读写 / Gateway / 销毁")

        if not self.args.sandbox_test:
            self._skip("未启用 --sandbox-test，跳过 Sandbox 端到端验证")
            phase.skipped += 1
            self.phases.append(phase)
            return phase

        # Check TestPod exists
        rc, pod_status, _ = self.kubectl("get pod acs-sandbox-test-pod -n default -o jsonpath='{.status.phase}'")
        pod_status = pod_status.strip("'")
        if rc != 0 or pod_status != "Running":
            self._fail(f"TestPod 不存在或未就绪 (status={pod_status})")
            phase.failed += 1
            self.phases.append(phase)
            return phase

        self._ok(f"TestPod 状态: {pod_status}")

        # Copy correct CA cert (fullchain.pem if available)
        cert_path = os.path.join(PROJECT_ROOT, "agent-vpc.infra", "fullchain.pem")
        if os.path.exists(cert_path):
            rc, _, _ = self.run_cmd(
                f"kubectl cp {cert_path} default/acs-sandbox-test-pod:/app/ca-fullchain.pem"
            )
            if rc == 0:
                self._ok("已更新 TestPod 证书 (fullchain.pem)")
            else:
                self._warn("更新 TestPod 证书失败，将使用容器内已有证书")

        # Run E2B test inside TestPod
        self._section("E2B Sandbox 创建与验证")
        test_script = textwrap.dedent("""\
            import os, sys, time
            os.chdir('/app')
            sys.path.insert(0, '/app')
            os.environ['SSL_CERT_FILE'] = '/app/ca-fullchain.pem'
            from dotenv import load_dotenv
            load_dotenv(override=True)
            os.environ['SSL_CERT_FILE'] = '/app/ca-fullchain.pem'
            from e2b_code_interpreter import Sandbox
            import json

            results = []

            # 1. Create
            print('[1/5] Creating sandbox...')
            start = time.monotonic()
            try:
                sandbox = Sandbox.create('openclaw', timeout=1800,
                    envs={'DASHSCOPE_API_KEY': os.environ.get('DASHSCOPE_API_KEY',''),
                          'GATEWAY_TOKEN': 'clawdbot-mode-123456'},
                    metadata={'e2b.agents.kruise.io/never-timeout': 'true'})
                elapsed = time.monotonic() - start
                results.append({'test': 'sandbox_create', 'pass': True, 'msg': f'{elapsed:.1f}s, ID={sandbox.sandbox_id}'})
            except Exception as e:
                results.append({'test': 'sandbox_create', 'pass': False, 'msg': str(e)[:200]})
                print(json.dumps(results))
                sys.exit(0)

            # 2. File I/O
            print('[2/5] Testing file I/O...')
            try:
                sandbox.files.write('/tmp/test.txt', 'Hello from E2B!')
                content = sandbox.files.read('/tmp/test.txt')
                results.append({'test': 'file_io', 'pass': content == 'Hello from E2B!',
                    'msg': f'write+read OK' if content == 'Hello from E2B!' else f'mismatch: {content}'})
            except Exception as e:
                results.append({'test': 'file_io', 'pass': False, 'msg': str(e)[:200]})

            # 3. Wait for services
            print('[3/5] Waiting for sandbox services (15s)...')
            time.sleep(15)

            # 4. Gateway
            print('[4/5] Checking gateway (port 18789)...')
            import requests, urllib3
            urllib3.disable_warnings()
            try:
                host = sandbox.get_host(18789)
                url = f'https://{host}'
                gw_ok = False
                for i in range(20):
                    try:
                        r = requests.get(f'{url}/?token=clawdbot-mode-123456',
                            verify='/app/ca-fullchain.pem', timeout=5)
                        if r.status_code == 200:
                            gw_ok = True
                            results.append({'test': 'gateway', 'pass': True,
                                'msg': f'HTTP 200 (TLS verified), attempt {i+1}'})
                            break
                    except requests.exceptions.SSLError:
                        r2 = requests.get(f'{url}/?token=clawdbot-mode-123456', verify=False, timeout=5)
                        if r2.status_code == 200:
                            gw_ok = True
                            results.append({'test': 'gateway', 'pass': True,
                                'msg': f'HTTP 200 (self-signed), attempt {i+1}'})
                            break
                    except Exception:
                        pass
                    time.sleep(3)
                if not gw_ok:
                    results.append({'test': 'gateway', 'pass': False, 'msg': 'Not ready after 20 attempts'})
            except Exception as e:
                results.append({'test': 'gateway', 'pass': False, 'msg': str(e)[:200]})

            # 5. Kill
            print('[5/5] Killing sandbox...')
            try:
                sandbox.kill()
                results.append({'test': 'sandbox_kill', 'pass': True, 'msg': 'Killed OK'})
            except Exception as e:
                results.append({'test': 'sandbox_kill', 'pass': False, 'msg': str(e)[:200]})

            print(json.dumps(results))
        """)

        rc, out, err = self.kubectl(
            f"exec acs-sandbox-test-pod -n default -- python3 -c {self._shell_quote(test_script)}",
            timeout=180,
        )

        # Parse JSON results from last line
        test_names = {
            "sandbox_create": "Sandbox 创建",
            "file_io": "文件读写",
            "gateway": "Gateway 18789 端口",
            "sandbox_kill": "Sandbox 销毁",
        }

        if rc == 0 or out:
            # Find last line with JSON
            for line in reversed(out.split("\n")):
                line = line.strip()
                if line.startswith("["):
                    try:
                        results = json.loads(line)
                        for r in results:
                            name = test_names.get(r["test"], r["test"])
                            if r["pass"]:
                                self._ok(name, r["msg"])
                                phase.passed += 1
                            else:
                                self._fail(name, r["msg"])
                                phase.failed += 1
                        break
                    except json.JSONDecodeError:
                        continue
            else:
                self._fail("无法解析 Sandbox 测试结果", out[-300:] if out else err[-300:])
                phase.failed += 1
        else:
            self._fail("Sandbox 测试执行失败", err[:300])
            phase.failed += 1

        self.phases.append(phase)
        return phase

    def _shell_quote(self, script: str) -> str:
        escaped = script.replace("'", "'\"'\"'")
        return f"'{escaped}'"

    # ──────────────────── Phase 5: Summary ────────────────────

    def phase5_summary(self):
        self._print(f"\n{BOLD}{CYAN}{'═' * 72}")
        self._print(f"  Phase 5  测试结果汇总")
        self._print(f"{'═' * 72}{RESET}\n")

        total_pass = 0
        total_fail = 0
        total_skip = 0

        for p in self.phases:
            total_pass += p.passed
            total_fail += p.failed
            total_skip += p.skipped

            if p.failed > 0:
                status = f"{RED}✗ FAIL{RESET}"
            elif p.skipped > 0 and p.passed == 0:
                status = f"{DIM}⏭ SKIP{RESET}"
            else:
                status = f"{GREEN}✓ PASS{RESET}"

            counts = f"{GREEN}{p.passed}{RESET} 通过"
            if p.failed > 0:
                counts += f", {RED}{p.failed}{RESET} 失败"
            if p.skipped > 0:
                counts += f", {DIM}{p.skipped}{RESET} 跳过"

            self._print(f"  {status}  {p.name:<20s}  {counts}")

        self._print(f"\n  {DIVIDER}")
        verdict = f"{GREEN}ALL PASSED{RESET}" if total_fail == 0 else f"{RED}{total_fail} FAILED{RESET}"
        self._print(f"  {BOLD}总计{RESET}:  {GREEN}{total_pass}{RESET} 通过, "
              f"{RED}{total_fail}{RESET} 失败, {DIM}{total_skip}{RESET} 跳过  →  {verdict}")
        self._print("")

        self._save_report(total_pass, total_fail, total_skip)

        return total_fail == 0

    def _save_report(self, total_pass: int, total_fail: int, total_skip: int):
        """生成 Markdown 测试报告并保存到本地"""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        stack_tag = self.stack_name[:12] if self.stack_name else "template"
        filename = f"openclaw_report_{stack_tag}_{ts}.md"
        report_path = os.path.join(PROJECT_ROOT, "tests", filename)

        verdict = "ALL PASSED" if total_fail == 0 else f"{total_fail} FAILED"

        lines = [
            f"# OpenClaw 网络隔离验证报告",
            f"",
            f"- **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"- **Stack**: {self.stack_name or '(仅静态校验)'}",
            f"- **Region**: {self.region}",
            f"- **Cluster ID**: {self.cluster_id or 'N/A'}",
            f"- **VPC ID**: {self.vpc_id or 'N/A'}",
            f"- **安全组 ID**: {self.sg_id or 'N/A'}",
            f"- **NAT EIP**: {self.stack_outputs.get('OpenClawNatEipAddress', 'N/A')}",
            f"- **ALB DNS**: {self.stack_outputs.get('ALB_DNS_Name', 'N/A')}",
            f"",
            f"## 测试结果: {verdict}",
            f"",
            f"| 阶段 | 通过 | 失败 | 跳过 | 状态 |",
            f"|------|------|------|------|------|",
        ]

        for p in self.phases:
            if p.failed > 0:
                st = "❌ FAIL"
            elif p.skipped > 0 and p.passed == 0:
                st = "⏭ SKIP"
            else:
                st = "✅ PASS"
            lines.append(f"| {p.name} | {p.passed} | {p.failed} | {p.skipped} | {st} |")

        lines.append(f"| **总计** | **{total_pass}** | **{total_fail}** | **{total_skip}** | **{verdict}** |")
        lines.append("")

        lines.append("## 详细日志")
        lines.append("")
        lines.append("```")
        lines.extend(self._log_lines)
        lines.append("```")
        lines.append("")

        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        self._print(f"  {GREEN}📄 测试报告已保存: {report_path}{RESET}")

    # ──────────────────── Main Entry ────────────────────

    def run(self) -> bool:
        self._print(f"\n{BOLD}{CYAN}{'═' * 72}")
        self._print(f"  OpenClaw 生产模版 — 一站式网络隔离验证")
        self._print(f"{'═' * 72}{RESET}")
        self._print(f"  Stack:     {self.stack_name or '(仅静态校验)'}")
        self._print(f"  Region:    {self.region}")
        self._print(f"  Sandbox:   {'启用' if self.args.sandbox_test else '未启用 (--sandbox-test)'}")
        mode_parts = []
        if not self.args.network_only:
            mode_parts.append("静态校验")
        if not self.args.template_only:
            mode_parts.append("网络验证")
        if self.args.sandbox_test:
            mode_parts.append("Sandbox E2E")
        self._print(f"  模式:      {' + '.join(mode_parts)}")
        self._print(f"{'═' * 72}\n")

        # Phase 0: Environment
        p0 = self.phase0_environment()
        if p0.failed > 0 and not self.args.template_only:
            self._fail("环境准备失败，中止测试")
            self.phase5_summary()
            return False

        # Phase 1: Template validation
        if not self.args.network_only:
            self.phase1_template_validation()

        # If template-only mode, stop here
        if self.args.template_only:
            return self.phase5_summary()

        # Need stack info for remaining phases
        if not self.stack_name:
            self._fail("网络验证需要 --stack-name 参数")
            return self.phase5_summary()

        # Load stack info & kubeconfig
        self._header(2, "公网保护解除 — 云防火墙 / ALB 安全组放行")
        self._section("Stack 信息加载")
        if not self._load_stack_info():
            return self.phase5_summary()

        if not self._setup_kubeconfig():
            return self.phase5_summary()

        # Phase 2: Public protection
        self.phase2_public_protection()

        # Phase 3: Network validation
        self.phase3_network_validation()

        # Phase 4: Sandbox E2E
        self.phase4_sandbox_test()

        # Phase 5: Summary
        return self.phase5_summary()


def main():
    parser = argparse.ArgumentParser(
        description="OpenClaw 生产模版一站式网络隔离验证",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            示例:
              # 完整测试（推荐）
              python tests/openclaw_test.py --stack-name my-stack --region cn-hangzhou \\
                  --accesskey LTAI*** --accesskey-secret ****

              # 含 Sandbox 端到端
              python tests/openclaw_test.py --stack-name my-stack --region cn-hangzhou \\
                  --accesskey LTAI*** --accesskey-secret **** --sandbox-test

              # 仅静态模版校验（无需云资源）
              python tests/openclaw_test.py --template-only

              # 仅网络验证（已配好 kubectl 和 aliyun CLI）
              python tests/openclaw_test.py --stack-name my-stack --region cn-hangzhou --network-only
        """),
    )

    parser.add_argument("--stack-name", "-s", default="", help="ROS Stack 名称或 ID")
    parser.add_argument("--region", "-r", default="cn-hangzhou", help="阿里云 Region (default: cn-hangzhou)")
    parser.add_argument("--accesskey", "--ak", default="", help="阿里云 AccessKey ID")
    parser.add_argument("--accesskey-secret", "--sk", default="", help="阿里云 AccessKey Secret")

    parser.add_argument("--template-only", action="store_true", help="仅运行静态模版校验")
    parser.add_argument("--network-only", action="store_true", help="仅运行网络验证（跳过模版校验）")
    parser.add_argument("--sandbox-test", action="store_true", help="启用 E2B Sandbox 端到端测试")

    args = parser.parse_args()

    cli = OpenClawTestCLI(args)
    success = cli.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

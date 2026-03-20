#!/usr/bin/env python3
"""
SandboxNamespace 参数专项校验 — template-production.yaml

验证 SandboxNamespace 参数在 ROS 模板中的一致性和正确性：
  1. 参数定义与约束
  2. 在 OpenClawSandboxSet 中的引用（DefaultNamespace + YamlContent + Fn::Sub 变量 + WaitUntil）
  3. 在 TestPod 中的引用（DefaultNamespace + YamlContent + Fn::Sub 变量）
  4. 与 sandbox-system 硬编码命名空间的隔离性
  5. Metadata ParameterGroups 中的归组
  6. 多值场景模拟（default / custom-ns / 边界值）
"""

import yaml
import re
import sys
import os
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(PROJECT_ROOT, "template-production.yaml")


def load_yaml(path):
    with open(path, "r") as f:
        return yaml.safe_load(f)


class NamespaceTestResult:
    def __init__(self, test_id, name, severity="ERROR"):
        self.test_id = test_id
        self.name = name
        self.severity = severity
        self.passed = True
        self.details = []

    def ok(self, msg):
        self.details.append(("PASS", msg))

    def fail(self, msg):
        self.passed = False
        self.details.append(("FAIL", msg))

    def warn(self, msg):
        self.details.append(("WARN", msg))

    def info(self, msg):
        self.details.append(("INFO", msg))


class NamespaceValidator:
    def __init__(self, template_path):
        self.path = template_path
        self.template = load_yaml(template_path)
        self.params = self.template.get("Parameters", {})
        self.resources = self.template.get("Resources", {})
        self.conditions = self.template.get("Conditions", {})
        self.outputs = self.template.get("Outputs", {})
        self.metadata = self.template.get("Metadata", {})
        self.results = []

    def _add_result(self, result):
        self.results.append(result)

    # ==================== Test NS-1: Parameter Definition ====================
    def test_parameter_definition(self):
        r = NamespaceTestResult("NS-1", "SandboxNamespace 参数定义")

        ns_param = self.params.get("SandboxNamespace")
        if not ns_param:
            r.fail("SandboxNamespace 参数不存在")
            self._add_result(r)
            return

        r.ok("SandboxNamespace 参数存在")

        if ns_param.get("Type") != "String":
            r.fail(f"Type 应为 String，实际为 {ns_param.get('Type')}")
        else:
            r.ok("Type 为 String")

        if ns_param.get("Default") != "default":
            r.fail(f"Default 应为 'default'，实际为 {ns_param.get('Default')!r}")
        else:
            r.ok("Default 值为 'default'")

        pattern = ns_param.get("AllowedPattern", "")
        if not pattern:
            r.fail("缺少 AllowedPattern（K8s namespace 命名规范校验）")
        else:
            r.ok(f"AllowedPattern 已配置: {pattern}")
            test_values = [
                ("default", True),
                ("sandbox-ns", True),
                ("my-ns-123", True),
                ("openclaw", True),
                ("-invalid", False),
                ("Invalid_NS", False),
                ("ns.with.dots", False),
                ("", False),
            ]
            regex = re.compile(f"^{pattern}$")
            for val, expected_match in test_values:
                matched = bool(regex.match(val))
                if matched == expected_match:
                    r.ok(f"AllowedPattern 对 '{val}' 验证正确 ({'匹配' if matched else '拒绝'})")
                else:
                    r.fail(f"AllowedPattern 对 '{val}' 验证异常: 预期{'匹配' if expected_match else '拒绝'}，"
                           f"实际{'匹配' if matched else '拒绝'}")

        label = ns_param.get("Label", {})
        if isinstance(label, dict):
            if "en" not in label or "zh-cn" not in label:
                r.warn(f"Label 缺少双语支持: {list(label.keys())}")
            else:
                r.ok(f"Label 双语: en='{label['en']}', zh-cn='{label['zh-cn']}'")
        else:
            r.warn(f"Label 不是字典类型: {label!r}")

        desc = ns_param.get("Description", {})
        if isinstance(desc, dict):
            if "en" in desc and "zh-cn" in desc:
                r.ok("Description 双语完整")
                desc_text = str(desc)
                if "sandbox-system" in desc_text.lower() or "sandbox-manager" in desc_text.lower():
                    r.ok("Description 说明了 sandbox-manager 不受此参数影响")
                else:
                    r.warn("Description 未明确说明 sandbox-manager 固定在 sandbox-system")
            else:
                r.warn(f"Description 缺少双语: {list(desc.keys())}")
        elif isinstance(desc, str) and desc:
            r.ok(f"Description 存在（单语）: {desc[:80]}")
        else:
            r.warn("Description 缺失")

        constraint = ns_param.get("ConstraintDescription", {})
        if constraint:
            r.ok(f"ConstraintDescription 已配置")
        else:
            r.warn("ConstraintDescription 缺失（用户输入非法值时无提示信息）")

        self._add_result(r)

    # ==================== Test NS-2: SandboxSet Namespace Usage ====================
    def test_sandboxset_namespace(self):
        r = NamespaceTestResult("NS-2", "OpenClawSandboxSet 命名空间引用")

        ss = self.resources.get("OpenClawSandboxSet", {})
        if not ss:
            r.fail("OpenClawSandboxSet 资源不存在")
            self._add_result(r)
            return

        props = ss.get("Properties", {})

        # NS-2a: DefaultNamespace
        default_ns = props.get("DefaultNamespace", {})
        if isinstance(default_ns, dict) and default_ns.get("Ref") == "SandboxNamespace":
            r.ok("DefaultNamespace 使用 Ref: SandboxNamespace")
        else:
            r.fail(f"DefaultNamespace 未使用 Ref: SandboxNamespace，实际: {default_ns}")

        # NS-2b: YamlContent 中的 namespace
        yaml_content = props.get("YamlContent", {})
        yaml_str = str(yaml_content)

        if "namespace: ${SandboxNamespace}" in yaml_str:
            r.ok("YamlContent 内的 SandboxSet metadata.namespace 使用 ${SandboxNamespace}")
        else:
            r.fail("YamlContent 内未找到 'namespace: ${SandboxNamespace}'")

        # NS-2c: Fn::Sub 变量映射
        if isinstance(yaml_content, dict) and "Fn::Sub" in yaml_content:
            fn_sub = yaml_content["Fn::Sub"]
            if isinstance(fn_sub, list) and len(fn_sub) >= 2:
                var_map = fn_sub[1]
                if isinstance(var_map, dict):
                    ns_ref = var_map.get("SandboxNamespace", {})
                    if isinstance(ns_ref, dict) and ns_ref.get("Ref") == "SandboxNamespace":
                        r.ok("Fn::Sub 变量映射 SandboxNamespace -> Ref: SandboxNamespace 正确")
                    else:
                        r.fail(f"Fn::Sub 变量映射 SandboxNamespace 不正确: {ns_ref}")
                else:
                    r.fail(f"Fn::Sub 变量映射不是字典: {type(var_map)}")
            else:
                r.fail(f"Fn::Sub 格式异常: {type(fn_sub)}")

        # NS-2d: WaitUntil Namespace
        wait_until = props.get("WaitUntil", [])
        if wait_until:
            found_ns_in_wait = False
            for w in wait_until:
                ns_val = w.get("Namespace", {})
                if isinstance(ns_val, dict) and ns_val.get("Ref") == "SandboxNamespace":
                    found_ns_in_wait = True
                    r.ok(f"WaitUntil[{w.get('Kind', '?')}/{w.get('Name', '?')}] "
                         f"Namespace 使用 Ref: SandboxNamespace")
                elif isinstance(ns_val, str) and ns_val == "default":
                    r.warn(f"WaitUntil Namespace 硬编码为 'default'，"
                           f"当 SandboxNamespace 非 default 时 WaitUntil 将监听错误的命名空间")

            if not found_ns_in_wait:
                r.fail("WaitUntil 中未找到使用 Ref: SandboxNamespace 的 Namespace 配置")
        else:
            r.warn("OpenClawSandboxSet 未配置 WaitUntil")

        self._add_result(r)

    # ==================== Test NS-3: TestPod Namespace Usage ====================
    def test_testpod_namespace(self):
        r = NamespaceTestResult("NS-3", "TestPod 命名空间引用")

        tp = self.resources.get("TestPod", {})
        if not tp:
            r.warn("TestPod 资源不存在（可选资源）")
            self._add_result(r)
            return

        props = tp.get("Properties", {})

        # NS-3a: DefaultNamespace
        default_ns = props.get("DefaultNamespace", {})
        if isinstance(default_ns, dict) and default_ns.get("Ref") == "SandboxNamespace":
            r.ok("DefaultNamespace 使用 Ref: SandboxNamespace")
        elif isinstance(default_ns, str) and default_ns == "default":
            r.fail("DefaultNamespace 硬编码为 'default'，应使用 Ref: SandboxNamespace "
                   "以确保 TestPod 部署到与 SandboxSet 相同的命名空间")
        else:
            r.fail(f"DefaultNamespace 配置异常: {default_ns}")

        # NS-3b: YamlContent 中的 namespace
        yaml_content = props.get("YamlContent", {})
        yaml_str = str(yaml_content)

        if "namespace: ${SandboxNamespace}" in yaml_str:
            r.ok("YamlContent 内的 TestPod metadata.namespace 使用 ${SandboxNamespace}")
        else:
            r.fail("YamlContent 内未找到 'namespace: ${SandboxNamespace}'")

        # NS-3c: Fn::Sub 变量映射
        if isinstance(yaml_content, dict) and "Fn::Sub" in yaml_content:
            fn_sub = yaml_content["Fn::Sub"]
            if isinstance(fn_sub, list) and len(fn_sub) >= 2:
                var_map = fn_sub[1]
                if isinstance(var_map, dict):
                    ns_ref = var_map.get("SandboxNamespace", {})
                    if isinstance(ns_ref, dict) and ns_ref.get("Ref") == "SandboxNamespace":
                        r.ok("Fn::Sub 变量映射 SandboxNamespace -> Ref: SandboxNamespace 正确")
                    else:
                        r.fail(f"Fn::Sub 变量映射 SandboxNamespace 不正确: {ns_ref}")

        self._add_result(r)

    # ==================== Test NS-4: Sandbox-system 隔离性 ====================
    def test_sandbox_system_isolation(self):
        r = NamespaceTestResult("NS-4", "sandbox-system 命名空间隔离性")

        sandbox_system_resources = []
        sandbox_ns_resources = []

        for name, res in self.resources.items():
            props = res.get("Properties", {})
            default_ns = props.get("DefaultNamespace", {})

            if isinstance(default_ns, str) and default_ns == "sandbox-system":
                sandbox_system_resources.append(name)
            elif isinstance(default_ns, dict) and default_ns.get("Ref") == "SandboxNamespace":
                sandbox_ns_resources.append(name)

        r.info(f"sandbox-system 命名空间资源 ({len(sandbox_system_resources)}): "
               f"{', '.join(sandbox_system_resources)}")
        r.info(f"SandboxNamespace 可配命名空间资源 ({len(sandbox_ns_resources)}): "
               f"{', '.join(sandbox_ns_resources)}")

        expected_sandbox_system = {
            "ClusterAppliaction",    # TLS Secret
            "EditAlbConfigServiceAccount",
            "EditAlbConfigRole",
            "EditAlbConfigBinding",
            "EditAlbConfigJob",
        }
        expected_sandbox_ns = {
            "OpenClawSandboxSet",
            "TestPod",
        }

        for name in expected_sandbox_system:
            if name in sandbox_system_resources:
                r.ok(f"'{name}' 正确部署到 sandbox-system（固定命名空间）")
            elif name in self.resources:
                props = self.resources[name].get("Properties", {})
                actual_ns = props.get("DefaultNamespace", "N/A")
                r.fail(f"'{name}' 应部署到 sandbox-system，实际 DefaultNamespace: {actual_ns}")
            else:
                r.warn(f"'{name}' 资源不存在")

        for name in expected_sandbox_ns:
            if name in sandbox_ns_resources:
                r.ok(f"'{name}' 正确使用 Ref: SandboxNamespace（可配命名空间）")
            elif name in self.resources:
                props = self.resources[name].get("Properties", {})
                actual_ns = props.get("DefaultNamespace", "N/A")
                r.fail(f"'{name}' 应使用 Ref: SandboxNamespace，实际 DefaultNamespace: {actual_ns}")
            else:
                r.warn(f"'{name}' 资源不存在")

        yaml_contents = []
        for name in ["ClusterAppliaction", "EditAlbConfigServiceAccount",
                      "EditAlbConfigRole", "EditAlbConfigBinding", "EditAlbConfigJob"]:
            res = self.resources.get(name, {})
            yaml_str = str(res.get("Properties", {}).get("YamlContent", ""))
            if "${SandboxNamespace}" in yaml_str:
                r.fail(f"'{name}' 内嵌 YAML 引用了 ${{SandboxNamespace}}，"
                       f"但该资源应固定在 sandbox-system")

        r.ok("sandbox-system 与 SandboxNamespace 隔离性验证完成")
        self._add_result(r)

    # ==================== Test NS-5: Metadata ParameterGroups ====================
    def test_parameter_groups(self):
        r = NamespaceTestResult("NS-5", "SandboxNamespace 参数分组", severity="WARN")

        interface = self.metadata.get("ALIYUN::ROS::Interface", {})
        groups = interface.get("ParameterGroups", [])
        hidden = interface.get("Hidden", [])

        if "SandboxNamespace" in hidden:
            r.warn("SandboxNamespace 被隐藏（Hidden），用户无法在控制台配置")
            self._add_result(r)
            return

        found_in_group = False
        group_label = ""
        for g in groups:
            if "SandboxNamespace" in g.get("Parameters", []):
                found_in_group = True
                group_label = str(g.get("Label", {}))
                break

        if found_in_group:
            r.ok(f"SandboxNamespace 在参数组中: {group_label}")
        else:
            r.warn("SandboxNamespace 未出现在任何 ParameterGroup 中（控制台分组缺失）")

        self._add_result(r)

    # ==================== Test NS-6: 多值场景模拟 ====================
    def test_multi_value_scenarios(self):
        r = NamespaceTestResult("NS-6", "多值场景模拟（default / custom-ns / 边界值）")

        ns_param = self.params.get("SandboxNamespace", {})
        pattern = ns_param.get("AllowedPattern", "")

        scenarios = [
            {
                "name": "默认值 (default)",
                "value": "default",
                "expected_valid": True,
                "checks": [
                    "SandboxSet 和 TestPod 部署到 default 命名空间",
                    "与传统行为一致",
                ],
            },
            {
                "name": "自定义命名空间 (openclaw-sandbox)",
                "value": "openclaw-sandbox",
                "expected_valid": True,
                "checks": [
                    "SandboxSet 部署到 openclaw-sandbox",
                    "TestPod 部署到 openclaw-sandbox",
                    "WaitUntil 监听 openclaw-sandbox",
                    "sandbox-manager 仍在 sandbox-system",
                ],
            },
            {
                "name": "生产推荐 (sandbox-prod)",
                "value": "sandbox-prod",
                "expected_valid": True,
                "checks": [
                    "将 SandboxSet 和 TestPod 与系统组件隔离",
                ],
            },
            {
                "name": "纯数字开头 (123ns) — RFC 1123 允许",
                "value": "123ns",
                "expected_valid": True,
                "checks": ["RFC 1123 允许以数字开头的 DNS label"],
            },
            {
                "name": "大写字母 (MyNamespace) — 应拒绝",
                "value": "MyNamespace",
                "expected_valid": False,
                "checks": ["K8s namespace 不允许大写字母"],
            },
            {
                "name": "连字符结尾 (ns-) — 应拒绝",
                "value": "ns-",
                "expected_valid": False,
                "checks": ["K8s namespace 不允许以连字符结尾"],
            },
            {
                "name": "单字符 (a) — 应允许",
                "value": "a",
                "expected_valid": True,
                "checks": ["最短合法 namespace"],
            },
        ]

        if pattern:
            regex = re.compile(f"^{pattern}$")
            for s in scenarios:
                matched = bool(regex.match(s["value"]))
                if matched == s["expected_valid"]:
                    r.ok(f"场景 '{s['name']}': {'允许' if matched else '拒绝'} ✓")
                else:
                    r.fail(f"场景 '{s['name']}': 预期{'允许' if s['expected_valid'] else '拒绝'}，"
                           f"实际{'允许' if matched else '拒绝'}")

                for check in s["checks"]:
                    r.info(f"  → {check}")
        else:
            r.fail("无 AllowedPattern，无法进行多值场景验证")

        self._add_result(r)

    # ==================== Test NS-7: Ref 完整性（模板级） ====================
    def test_ref_completeness(self):
        r = NamespaceTestResult("NS-7", "SandboxNamespace Ref 引用完整性")

        template_str = str(self.template)

        ref_count = template_str.count("'Ref': 'SandboxNamespace'")
        ref_count += template_str.count('"Ref": "SandboxNamespace"')

        fn_sub_count = template_str.count("${SandboxNamespace}")

        r.info(f"Ref: SandboxNamespace 出现 {ref_count} 次")
        r.info(f"Fn::Sub ${{SandboxNamespace}} 出现 {fn_sub_count} 次")

        resources_using_ns = set()
        for name, res in self.resources.items():
            res_str = str(res)
            if "SandboxNamespace" in res_str:
                resources_using_ns.add(name)

        r.info(f"引用 SandboxNamespace 的资源: {', '.join(sorted(resources_using_ns))}")

        expected_consumers = {"OpenClawSandboxSet", "TestPod"}
        for exp in expected_consumers:
            if exp in resources_using_ns:
                r.ok(f"'{exp}' 引用了 SandboxNamespace")
            elif exp in self.resources:
                r.fail(f"'{exp}' 存在但未引用 SandboxNamespace")
            else:
                r.warn(f"'{exp}' 资源不存在")

        should_not_use = {"ClusterAppliaction", "EditAlbConfigServiceAccount",
                          "EditAlbConfigRole", "EditAlbConfigBinding", "EditAlbConfigJob"}
        for name in should_not_use:
            if name in resources_using_ns:
                r.fail(f"'{name}' 不应引用 SandboxNamespace（应固定在 sandbox-system）")
            elif name in self.resources:
                r.ok(f"'{name}' 未引用 SandboxNamespace（正确固定在 sandbox-system）")

        self._add_result(r)

    # ==================== Test NS-8: 与 openclaw_test.py CLI 一致性 ====================
    def test_cli_consistency(self):
        r = NamespaceTestResult("NS-8", "与 openclaw_test.py CLI 的一致性", severity="WARN")

        cli_path = os.path.join(PROJECT_ROOT, "tests", "openclaw_test.py")
        if not os.path.exists(cli_path):
            r.warn("openclaw_test.py 不存在，跳过一致性检查")
            self._add_result(r)
            return

        with open(cli_path, "r") as f:
            cli_content = f.read()

        hardcoded_default_ns = []
        for match in re.finditer(r'-n\s+default\b', cli_content):
            start = max(0, match.start() - 60)
            context = cli_content[start:match.end() + 20]
            context_clean = context.replace("\n", " ").strip()
            hardcoded_default_ns.append(context_clean)

        if hardcoded_default_ns:
            r.warn(f"openclaw_test.py 中有 {len(hardcoded_default_ns)} 处硬编码 '-n default'，"
                   f"当 SandboxNamespace 非 default 时可能需要适配")
            for ctx in hardcoded_default_ns[:5]:
                r.info(f"  → ...{ctx[-80:]}...")
        else:
            r.ok("openclaw_test.py 未发现硬编码 '-n default'")

        if "SandboxNamespace" in cli_content or "sandbox.namespace" in cli_content.lower():
            r.ok("openclaw_test.py 已考虑 SandboxNamespace 参数")
        else:
            r.warn("openclaw_test.py 未引用 SandboxNamespace 参数，"
                   "在非 default 命名空间场景下可能不适配")

        self._add_result(r)

    def run_all(self):
        self.test_parameter_definition()
        self.test_sandboxset_namespace()
        self.test_testpod_namespace()
        self.test_sandbox_system_isolation()
        self.test_parameter_groups()
        self.test_multi_value_scenarios()
        self.test_ref_completeness()
        self.test_cli_consistency()
        return self.results

    def print_report(self):
        print(f"\n{'=' * 72}")
        print(f"  SandboxNamespace 专项校验: {self.path}")
        print(f"{'=' * 72}")

        total_pass = 0
        total_fail = 0
        total_warn = 0

        for r in self.results:
            status = "✅ PASS" if r.passed else "❌ FAIL"
            print(f"\n  [{r.test_id}] {r.name}  {status}")
            print(f"  {'─' * 66}")
            for level, msg in r.details:
                if level == "PASS":
                    print(f"    ✓ {msg}")
                    total_pass += 1
                elif level == "FAIL":
                    print(f"    ✗ {msg}")
                    total_fail += 1
                elif level == "WARN":
                    print(f"    ⚠ {msg}")
                    total_warn += 1
                elif level == "INFO":
                    print(f"    ℹ {msg}")

        print(f"\n{'=' * 72}")
        print(f"  总计: {total_pass} 通过, {total_fail} 失败, {total_warn} 警告")
        verdict = "ALL PASSED" if total_fail == 0 else f"{total_fail} FAILED"
        print(f"  结论: {verdict}")
        print(f"{'=' * 72}\n")

        return total_pass, total_fail, total_warn

    def generate_markdown_report(self, total_pass, total_fail, total_warn):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"namespace_test_report_{ts}.md"
        report_path = os.path.join(PROJECT_ROOT, "tests", filename)

        verdict = "ALL PASSED ✅" if total_fail == 0 else f"{total_fail} FAILED ❌"

        lines = [
            f"# SandboxNamespace 专项测试报告",
            f"",
            f"- **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"- **模板文件**: `template-production.yaml`",
            f"- **测试类型**: 静态模板校验（Namespace 参数专项）",
            f"",
            f"## 测试结果: {verdict}",
            f"",
            f"| 指标 | 数量 |",
            f"|------|------|",
            f"| 通过 | {total_pass} |",
            f"| 失败 | {total_fail} |",
            f"| 警告 | {total_warn} |",
            f"",
            f"---",
            f"",
            f"## 测试项详情",
            f"",
        ]

        for r in self.results:
            status = "✅ PASS" if r.passed else "❌ FAIL"
            lines.append(f"### [{r.test_id}] {r.name} — {status}")
            lines.append(f"")
            lines.append(f"| 状态 | 详情 |")
            lines.append(f"|------|------|")
            for level, msg in r.details:
                icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "INFO": "ℹ️"}.get(level, "")
                msg_escaped = msg.replace("|", "\\|")
                lines.append(f"| {icon} {level} | {msg_escaped} |")
            lines.append(f"")

        lines.append(f"---")
        lines.append(f"")
        lines.append(f"## SandboxNamespace 参数分析")
        lines.append(f"")
        lines.append(f"### 参数设计")
        lines.append(f"")
        lines.append(f"| 属性 | 值 |")
        lines.append(f"|------|------|")

        ns_param = self.params.get("SandboxNamespace", {})
        lines.append(f"| Type | `{ns_param.get('Type', 'N/A')}` |")
        lines.append(f"| Default | `{ns_param.get('Default', 'N/A')}` |")
        lines.append(f"| AllowedPattern | `{ns_param.get('AllowedPattern', 'N/A')}` |")
        lines.append(f"| Required | `{ns_param.get('Required', 'N/A')}` |")
        lines.append(f"")

        lines.append(f"### 命名空间分布")
        lines.append(f"")
        lines.append(f"| 资源名称 | DefaultNamespace | 说明 |")
        lines.append(f"|---------|-----------------|------|")

        for name, res in sorted(self.resources.items()):
            props = res.get("Properties", {})
            default_ns = props.get("DefaultNamespace", None)
            if default_ns is not None:
                if isinstance(default_ns, dict):
                    ns_display = f"`Ref: {default_ns.get('Ref', '?')}`"
                else:
                    ns_display = f"`{default_ns}`"
                desc = ""
                if name in ("OpenClawSandboxSet", "TestPod"):
                    desc = "可配（跟随 SandboxNamespace）"
                elif isinstance(default_ns, str) and default_ns == "sandbox-system":
                    desc = "固定（系统组件）"
                lines.append(f"| {name} | {ns_display} | {desc} |")

        lines.append(f"")
        lines.append(f"### 测试场景覆盖矩阵")
        lines.append(f"")
        lines.append(f"| SandboxNamespace 值 | 合法性 | SandboxSet 命名空间 | TestPod 命名空间 | sandbox-manager 命名空间 |")
        lines.append(f"|-------------------|--------|-------------------|----------------|------------------------|")
        lines.append(f"| `default` | ✅ 合法 | default | default | sandbox-system (固定) |")
        lines.append(f"| `openclaw-sandbox` | ✅ 合法 | openclaw-sandbox | openclaw-sandbox | sandbox-system (固定) |")
        lines.append(f"| `sandbox-prod` | ✅ 合法 | sandbox-prod | sandbox-prod | sandbox-system (固定) |")
        lines.append(f"| `a` | ✅ 合法 | a | a | sandbox-system (固定) |")
        lines.append(f"| `123ns` | ✅ 合法 | 123ns | 123ns | sandbox-system (固定) |")
        lines.append(f"| `MyNamespace` | ❌ 拒绝 | — | — | — |")
        lines.append(f"| `ns-` | ❌ 拒绝 | — | — | — |")
        lines.append(f"")

        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        print(f"📄 测试报告已保存: {report_path}")
        return report_path


if __name__ == "__main__":
    validator = NamespaceValidator(TEMPLATE_PATH)
    validator.run_all()
    total_pass, total_fail, total_warn = validator.print_report()
    report_path = validator.generate_markdown_report(total_pass, total_fail, total_warn)
    sys.exit(0 if total_fail == 0 else 1)

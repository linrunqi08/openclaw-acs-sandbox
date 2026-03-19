"""
Comprehensive validation tests for template-production.yaml
Covers: YAML structure, reference integrity, CIDR logic, dependency chain,
        documentation compliance, and security policy completeness.
"""

import yaml
import re
import ipaddress
import sys
from collections import defaultdict


def load_yaml(path):
    with open(path, 'r') as f:
        return yaml.safe_load(f)


def extract_refs(obj, refs=None):
    """Recursively extract all Ref values from a YAML structure."""
    if refs is None:
        refs = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == 'Ref':
                if isinstance(v, str) and not v.startswith('ALIYUN::'):
                    refs.add(v)
            else:
                extract_refs(v, refs)
    elif isinstance(obj, list):
        for item in obj:
            extract_refs(item, refs)
    return refs


def extract_getatt(obj, results=None):
    """Recursively extract all Fn::GetAtt resource references."""
    if results is None:
        results = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == 'Fn::GetAtt' and isinstance(v, list) and len(v) >= 1:
                results.add(v[0])
            else:
                extract_getatt(v, results)
    elif isinstance(obj, list):
        for item in obj:
            extract_getatt(item, results)
    return results


def extract_depends_on(resource):
    """Extract DependsOn from a resource definition."""
    deps = resource.get('DependsOn', [])
    if isinstance(deps, str):
        return {deps}
    return set(deps)


def extract_conditions_used(obj, conditions=None):
    """Extract all Condition references and Fn::If conditions."""
    if conditions is None:
        conditions = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == 'Condition' and isinstance(v, str):
                conditions.add(v)
            elif k == 'Fn::If' and isinstance(v, list) and len(v) >= 1:
                conditions.add(v[0])
            else:
                extract_conditions_used(v, conditions)
    elif isinstance(obj, list):
        for item in obj:
            extract_conditions_used(item, conditions)
    return conditions


def extract_yaml_content_strings(obj, results=None):
    """Extract all YamlContent string values for K8s manifest validation."""
    if results is None:
        results = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == 'YamlContent':
                results.append(v)
            else:
                extract_yaml_content_strings(v, results)
    elif isinstance(obj, list):
        for item in obj:
            extract_yaml_content_strings(item, results)
    return results


class TemplateValidator:
    def __init__(self, template_path):
        self.path = template_path
        self.template = load_yaml(template_path)
        self.params = self.template.get('Parameters', {})
        self.resources = self.template.get('Resources', {})
        self.conditions = self.template.get('Conditions', {})
        self.outputs = self.template.get('Outputs', {})
        self.errors = []
        self.warnings = []

    def error(self, category, msg):
        self.errors.append(f"[ERROR][{category}] {msg}")

    def warn(self, category, msg):
        self.warnings.append(f"[WARN][{category}] {msg}")

    def info(self, msg):
        print(f"  [INFO] {msg}")

    # ==================== Test 1: YAML Structure ====================
    def test_yaml_structure(self):
        print("\n=== Test 1: YAML Structure ===")
        required_sections = ['ROSTemplateFormatVersion', 'Parameters', 'Resources', 'Outputs']
        for section in required_sections:
            if section not in self.template:
                self.error('Structure', f"Missing required section: {section}")
            else:
                self.info(f"Section '{section}' exists")

        if self.template.get('ROSTemplateFormatVersion') != '2015-09-01':
            self.error('Structure', f"Invalid ROSTemplateFormatVersion: {self.template.get('ROSTemplateFormatVersion')}")
        else:
            self.info("ROSTemplateFormatVersion is correct: 2015-09-01")

        if 'Description' in self.template:
            desc = self.template['Description']
            if isinstance(desc, dict):
                for lang in ['en', 'zh-cn']:
                    if lang not in desc:
                        self.warn('Structure', f"Description missing '{lang}' translation")
            self.info(f"Description present with keys: {list(desc.keys()) if isinstance(desc, dict) else 'string'}")

    # ==================== Test 2: Parameter Validation ====================
    def test_parameters(self):
        print("\n=== Test 2: Parameter Validation ===")
        required_params = [
            'ZoneId1', 'ZoneId2', 'ZoneId3',
            'VpcOption', 'VpcId', 'VpcCidrBlock',
            'VSwitchId1', 'VSwitchId2', 'VSwitchId3',
            'VSwitchCidrBlock1', 'VSwitchCidrBlock2', 'VSwitchCidrBlock3',
            'OpenClawVSwitchId1', 'OpenClawVSwitchId2', 'OpenClawVSwitchId3',
            'OpenClawVSwitchCidrBlock1', 'OpenClawVSwitchCidrBlock2', 'OpenClawVSwitchCidrBlock3',
            'OpenClawCidrBlock',
            'ServiceCidr',
            'E2BDomainAddress', 'TLSCert', 'TLSSecret',
            'AdminApiKey', 'EnablePublicIp', 'EnablePrivateZone',
            'BaiLianApiKey', 'OpenClawGatewayToken',
            'OpenClawReplicas', 'OpenClawImage',
            'ClusterOption',
        ]
        for p in required_params:
            if p not in self.params:
                self.error('Params', f"Missing required parameter: {p}")
            else:
                self.info(f"Parameter '{p}' exists")

        for name, param in self.params.items():
            if 'Type' not in param:
                self.error('Params', f"Parameter '{name}' missing Type")
            if 'Label' not in param:
                self.warn('Params', f"Parameter '{name}' missing Label")

        zone_params = ['ZoneId1', 'ZoneId2', 'ZoneId3']
        for zp in zone_params:
            if zp in self.params:
                meta = self.params[zp].get('AssociationPropertyMetadata', {})
                exclusive = meta.get('ExclusiveTo', [])
                if zp == 'ZoneId1':
                    expected = {'ZoneId2', 'ZoneId3'}
                else:
                    expected = {'ZoneId1'}
                if set(exclusive) != expected:
                    self.error('Params', f"Zone '{zp}' ExclusiveTo should be {sorted(expected)}, got {exclusive}")
                else:
                    self.info(f"Zone '{zp}' ExclusiveTo correctly set: {exclusive}")

    # ==================== Test 3: Reference Integrity ====================
    def test_reference_integrity(self):
        print("\n=== Test 3: Reference Integrity ===")
        all_refs = extract_refs(self.resources)
        all_refs.update(extract_refs(self.outputs))

        param_names = set(self.params.keys())
        pseudo_params = {
            'ALIYUN::StackName', 'ALIYUN::Region', 'ALIYUN::StackId',
            'ALIYUN::AccountId', 'ALIYUN::NoValue', 'RegionId'
        }

        for ref in sorted(all_refs):
            if ref not in param_names and ref not in pseudo_params:
                self.error('RefIntegrity', f"Ref '{ref}' does not match any Parameter or pseudo-parameter")
            else:
                self.info(f"Ref '{ref}' resolves correctly")

        all_getatt = extract_getatt(self.resources)
        all_getatt.update(extract_getatt(self.outputs))
        resource_names = set(self.resources.keys())

        for res_name in sorted(all_getatt):
            if res_name not in resource_names:
                self.error('RefIntegrity', f"Fn::GetAtt references non-existent resource: '{res_name}'")
            else:
                self.info(f"Fn::GetAtt resource '{res_name}' exists")

    # ==================== Test 4: Condition Integrity ====================
    def test_condition_integrity(self):
        print("\n=== Test 4: Condition Integrity ===")
        defined_conditions = set(self.conditions.keys())
        used_conditions = extract_conditions_used(self.resources)
        used_conditions.update(extract_conditions_used(self.outputs))

        for cond in sorted(used_conditions):
            if cond not in defined_conditions:
                self.error('Conditions', f"Condition '{cond}' used but not defined")
            else:
                self.info(f"Condition '{cond}' defined and used")

        for cond in sorted(defined_conditions - used_conditions):
            self.warn('Conditions', f"Condition '{cond}' defined but never used")

    # ==================== Test 5: Dependency Chain ====================
    def test_dependency_chain(self):
        print("\n=== Test 5: Dependency Chain ===")
        resource_names = set(self.resources.keys())

        for res_name, res_def in self.resources.items():
            deps = extract_depends_on(res_def)
            for dep in deps:
                if dep not in resource_names:
                    self.error('DependsOn', f"Resource '{res_name}' DependsOn non-existent resource '{dep}'")
                else:
                    self.info(f"Resource '{res_name}' -> DependsOn '{dep}' OK")

        # Check for circular dependencies (simple BFS)
        graph = {}
        for res_name, res_def in self.resources.items():
            graph[res_name] = extract_depends_on(res_def)

        def has_cycle(node, visited, rec_stack):
            visited.add(node)
            rec_stack.add(node)
            for neighbor in graph.get(node, set()):
                if neighbor not in visited:
                    if has_cycle(neighbor, visited, rec_stack):
                        return True
                elif neighbor in rec_stack:
                    return True
            rec_stack.discard(node)
            return False

        visited = set()
        for node in graph:
            if node not in visited:
                if has_cycle(node, visited, set()):
                    self.error('DependsOn', f"Circular dependency detected involving '{node}'")

        if not any('Circular' in e for e in self.errors):
            self.info("No circular dependencies detected")

    # ==================== Test 6: CIDR Validation ====================
    def test_cidr_validation(self):
        print("\n=== Test 6: CIDR Validation ===")

        vpc_cidr_default = self.params.get('VpcCidrBlock', {}).get('Default', '')
        biz_cidrs = [
            self.params.get(f'VSwitchCidrBlock{i}', {}).get('Default', '')
            for i in range(1, 4)
        ]
        oc_cidrs = [
            self.params.get(f'OpenClawVSwitchCidrBlock{i}', {}).get('Default', '')
            for i in range(1, 4)
        ]
        oc_agg = self.params.get('OpenClawCidrBlock', {}).get('Default', '')
        svc_cidr = self.params.get('ServiceCidr', {}).get('Default', '')

        # Check if VPC has SecondaryCidrBlocks configured
        vpc_res = self.resources.get('Vpc', {})
        vpc_props = vpc_res.get('Properties', {})
        secondary_cidrs_raw = vpc_props.get('SecondaryCidrBlocks', [])
        has_secondary_cidr = len(secondary_cidrs_raw) > 0

        try:
            vpc_net = ipaddress.ip_network(vpc_cidr_default, strict=False)
            self.info(f"VPC CIDR default: {vpc_cidr_default} (valid)")
        except ValueError as e:
            self.error('CIDR', f"Invalid VPC CIDR default '{vpc_cidr_default}': {e}")
            return

        for i, cidr in enumerate(biz_cidrs, 1):
            try:
                net = ipaddress.ip_network(cidr, strict=False)
                if net.subnet_of(vpc_net):
                    self.info(f"Business VSwitch{i} CIDR {cidr} is within VPC CIDR")
                else:
                    self.error('CIDR', f"Business VSwitch{i} CIDR {cidr} is NOT within VPC CIDR {vpc_cidr_default}")
            except ValueError as e:
                self.error('CIDR', f"Invalid Business VSwitch{i} CIDR '{cidr}': {e}")

        for i, cidr in enumerate(oc_cidrs, 1):
            try:
                net = ipaddress.ip_network(cidr, strict=False)
                if net.subnet_of(vpc_net):
                    self.info(f"OpenClaw VSwitch{i} CIDR {cidr} is within VPC primary CIDR")
                elif has_secondary_cidr:
                    try:
                        agg_net = ipaddress.ip_network(oc_agg, strict=False)
                        if net.subnet_of(agg_net):
                            self.info(f"OpenClaw VSwitch{i} CIDR {cidr} is within VPC secondary CIDR "
                                      f"(via SecondaryCidrBlocks: {oc_agg})")
                        else:
                            self.error('CIDR', f"OpenClaw VSwitch{i} CIDR {cidr} is NOT within "
                                      f"secondary CIDR {oc_agg} either")
                    except ValueError:
                        self.error('CIDR', f"Invalid OpenClaw aggregate CIDR for secondary check")
                else:
                    self.error('CIDR', f"OpenClaw VSwitch{i} CIDR {cidr} is NOT within VPC CIDR {vpc_cidr_default}. "
                              f"NewVPC scenario will FAIL: VSwitch CIDR must be a subnet of VPC CIDR. "
                              f"Either change VPC CIDR to encompass 10.8.0.0/16 or add secondary CIDR block.")
            except ValueError as e:
                self.error('CIDR', f"Invalid OpenClaw VSwitch{i} CIDR '{cidr}': {e}")

        # Check OpenClaw aggregate covers all OpenClaw VSwitches
        try:
            agg_net = ipaddress.ip_network(oc_agg, strict=False)
            for i, cidr in enumerate(oc_cidrs, 1):
                net = ipaddress.ip_network(cidr, strict=False)
                if net.subnet_of(agg_net):
                    self.info(f"OpenClaw VSwitch{i} CIDR {cidr} is within aggregate {oc_agg}")
                else:
                    self.error('CIDR', f"OpenClaw VSwitch{i} CIDR {cidr} is NOT within aggregate CIDR {oc_agg}")
        except ValueError:
            self.error('CIDR', f"Invalid OpenClaw aggregate CIDR '{oc_agg}'")

        # Check no overlap between business and OpenClaw VSwitches
        for i, bcidr in enumerate(biz_cidrs, 1):
            for j, ocidr in enumerate(oc_cidrs, 1):
                try:
                    bnet = ipaddress.ip_network(bcidr, strict=False)
                    onet = ipaddress.ip_network(ocidr, strict=False)
                    if bnet.overlaps(onet):
                        self.error('CIDR', f"Business VSwitch{i} ({bcidr}) overlaps with OpenClaw VSwitch{j} ({ocidr})")
                    else:
                        self.info(f"No overlap: Biz-VSW{i} ({bcidr}) vs OC-VSW{j} ({ocidr})")
                except ValueError:
                    pass

        # Check Service CIDR doesn't overlap with VPC or OpenClaw
        try:
            svc_net = ipaddress.ip_network(svc_cidr, strict=False)
            if svc_net.overlaps(vpc_net):
                self.warn('CIDR', f"Service CIDR {svc_cidr} overlaps with VPC CIDR {vpc_cidr_default}")
            for i, cidr in enumerate(oc_cidrs, 1):
                onet = ipaddress.ip_network(cidr, strict=False)
                if svc_net.overlaps(onet):
                    self.error('CIDR', f"Service CIDR {svc_cidr} overlaps with OpenClaw VSwitch{i} CIDR {cidr}")
        except ValueError:
            pass

    # ==================== Test 7: Security Group Rules ====================
    def test_security_group_rules(self):
        print("\n=== Test 7: SecurityGroup Rules Compliance ===")
        sg = self.resources.get('OpenClawSecurityGroup', {})
        if not sg:
            self.error('SecurityGroup', "OpenClawSecurityGroup resource not found")
            return

        props = sg.get('Properties', {})

        if props.get('SecurityGroupType') != 'enterprise':
            self.error('SecurityGroup', "SecurityGroupType should be 'enterprise'")
        else:
            self.info("SecurityGroupType is 'enterprise'")

        # Check ingress rules
        ingress = props.get('SecurityGroupIngress', [])
        self.info(f"Found {len(ingress)} ingress rules")
        if len(ingress) < 3:
            self.error('SecurityGroup', "Expected at least 3 ingress rules (one per business VSwitch)")

        # Check egress rules
        egress = props.get('SecurityGroupEgress', [])
        self.info(f"Found {len(egress)} egress rules")

        egress_checks = {
            'metadata_deny': False,
            'nat_443': False,
            'nat_53_tcp': False,
            'nat_53_udp': False,
            'nat_80': False,
            'apiserver_6443': False,
            'apiserver_9082': False,
            'dns_136_tcp': False,
            'dns_136_udp': False,
            'dns_138_tcp': False,
            'dns_138_udp': False,
            'dns_vpc_tcp': False,
            'dns_vpc_udp': False,
            'vpc_deny': False,
            'openclaw_deny': False,
            'public_allow': False,
        }

        for rule in egress:
            dest = rule.get('DestCidrIp', '')
            port = rule.get('PortRange', '')
            policy = rule.get('Policy', 'accept')
            proto = rule.get('IpProtocol', '')

            if '100.100.100.200' in str(dest) and policy == 'drop':
                egress_checks['metadata_deny'] = True
            if port == '443/443' and proto == 'tcp' and 'NatIp' in str(dest):
                egress_checks['nat_443'] = True
            if port == '53/53' and proto == 'tcp' and 'NatIp' in str(dest):
                egress_checks['nat_53_tcp'] = True
            if port == '53/53' and proto == 'udp' and 'NatIp' in str(dest):
                egress_checks['nat_53_udp'] = True
            if port == '80/80' and proto == 'tcp' and 'NatIp' in str(dest):
                egress_checks['nat_80'] = True
            if port == '6443/6443' and 'ApiServerIp' in str(dest):
                egress_checks['apiserver_6443'] = True
            if port == '9082/9082' and 'ApiServerIp' in str(dest):
                egress_checks['apiserver_9082'] = True
            if '100.100.2.136' in str(dest) and port == '53/53' and proto == 'tcp':
                egress_checks['dns_136_tcp'] = True
            if '100.100.2.136' in str(dest) and port == '53/53' and proto == 'udp':
                egress_checks['dns_136_udp'] = True
            if '100.100.2.138' in str(dest) and port == '53/53' and proto == 'tcp':
                egress_checks['dns_138_tcp'] = True
            if '100.100.2.138' in str(dest) and port == '53/53' and proto == 'udp':
                egress_checks['dns_138_udp'] = True
            if str(dest) == '0.0.0.0/0' and policy != 'drop':
                egress_checks['public_allow'] = True

        # Check complex rules via Ref/DataSource patterns
        for rule in egress:
            dest = rule.get('DestCidrIp', {})
            policy = rule.get('Policy', 'accept')
            port = rule.get('PortRange', '')
            proto = rule.get('IpProtocol', '')

            # DNS to VPC CIDR (CoreDNS in business VSwitch, DNAT'd)
            if port == '53/53' and isinstance(dest, dict) and 'Fn::Jq' in dest:
                jq_expr = str(dest['Fn::Jq'])
                if 'VpcDataSource' in jq_expr and 'CidrBlock' in jq_expr:
                    if proto == 'tcp':
                        egress_checks['dns_vpc_tcp'] = True
                    elif proto == 'udp':
                        egress_checks['dns_vpc_udp'] = True

            if policy == 'drop':
                # VPC primary CIDR deny (dynamic via DataSource)
                if isinstance(dest, dict) and 'Fn::Jq' in dest:
                    jq_expr = str(dest['Fn::Jq'])
                    if 'VpcDataSource' in jq_expr and 'CidrBlock' in jq_expr:
                        egress_checks['vpc_deny'] = True
                # VPC primary CIDR deny (static via Ref)
                if isinstance(dest, dict) and 'Ref' in dest and dest['Ref'] == 'VpcCidrBlock':
                    egress_checks['vpc_deny'] = True
                # OpenClaw secondary CIDR deny
                if isinstance(dest, dict) and 'Ref' in dest and dest['Ref'] == 'OpenClawCidrBlock':
                    egress_checks['openclaw_deny'] = True

        for check_name, passed in egress_checks.items():
            if passed:
                self.info(f"Egress check '{check_name}' PASSED")
            else:
                self.error('SecurityGroup', f"Egress check '{check_name}' FAILED - rule not found per 网络隔离方案-生产级配置.md")

        # Check DependsOn for DataSources
        deps = extract_depends_on(sg)
        if 'ClusterDataSource' not in deps:
            self.error('SecurityGroup', "Missing DependsOn: ClusterDataSource (needed for ApiServer IP)")
        if 'DefaultNatGatewayDataSource' not in deps:
            self.error('SecurityGroup', "Missing DependsOn: DefaultNatGatewayDataSource (needed for NAT IP)")

    # ==================== Test 8: TrafficPolicy Compliance ====================
    def test_traffic_policy_compliance(self):
        print("\n=== Test 8: TrafficPolicy Documentation Compliance ===")

        has_global_tp = 'GlobalTrafficPolicyApplication' in self.resources
        has_openclaw_tp = 'OpenClawTrafficPolicyApplication' in self.resources

        if not has_global_tp:
            self.error('TrafficPolicy',
                       "CRITICAL: GlobalTrafficPolicyApplication is MISSING! "
                       "Per 网络隔离方案-生产级配置.md and 托管OpenClaw推荐网络架构.md, "
                       "a GlobalTrafficPolicy is required to deny ingress from OpenClaw CIDR to all other pods, "
                       "preventing lateral movement even if sandbox is compromised.")
        else:
            self.info("GlobalTrafficPolicyApplication exists")

        if not has_openclaw_tp:
            self.error('TrafficPolicy',
                       "CRITICAL: OpenClawTrafficPolicyApplication is MISSING! "
                       "Per 网络隔离方案-生产级配置.md, a TrafficPolicy with selector app:openclaw is required. "
                       "It restricts: ingress to only sandbox-gateway + sandbox-manager, "
                       "egress denies metadata + internal networks, allows kube-dns + public.")
        else:
            self.info("OpenClawTrafficPolicyApplication exists")

        # Check for Poseidon addon (provides TrafficPolicy CRD)
        has_poseidon = 'PoseidonAddon' in self.resources
        if not has_poseidon and (has_global_tp or has_openclaw_tp):
            self.error('TrafficPolicy',
                       "TrafficPolicy resources exist but PoseidonAddon is missing. "
                       "Poseidon provides GlobalTrafficPolicy/TrafficPolicy CRDs.")
        elif not has_poseidon and not has_global_tp and not has_openclaw_tp:
            self.error('TrafficPolicy',
                       "CRITICAL: Both PoseidonAddon AND TrafficPolicy resources are missing! "
                       "Without Poseidon, the cluster has NO network policy enforcement. "
                       "This is a major security gap per all documentation.")
        else:
            self.info("PoseidonAddon exists to provide TrafficPolicy CRDs")

    # ==================== Test 9: NAT Gateway Setup ====================
    def test_nat_gateway(self):
        print("\n=== Test 9: NAT Gateway Setup ===")
        required_nat_resources = [
            'OpenClawNatGateway',
            'OpenClawNatEip',
            'OpenClawNatEipAssociation',
            'OpenClawSnatEntry1',
            'OpenClawSnatEntry2',
            'OpenClawSnatEntry3',
        ]
        for res in required_nat_resources:
            if res not in self.resources:
                self.error('NAT', f"Missing NAT resource: {res}")
            else:
                self.info(f"NAT resource '{res}' exists")

        nat = self.resources.get('OpenClawNatGateway', {})
        nat_deps = extract_depends_on(nat)
        if 'Sleep' not in nat_deps:
            self.error('NAT', "OpenClawNatGateway should DependsOn Sleep (must create after cluster's default NAT)")

    # ==================== Test 9b: Route Table Setup ====================
    def test_route_table(self):
        print("\n=== Test 9b: OpenClaw Route Table Setup ===")
        required_rt_resources = [
            'OpenClawRouteTable',
            'OpenClawRouteEntry',
            'OpenClawRouteTableAssociation1',
            'OpenClawRouteTableAssociation2',
            'OpenClawRouteTableAssociation3',
        ]
        for res in required_rt_resources:
            if res not in self.resources:
                self.error('RouteTable', f"Missing route table resource: {res}")
            else:
                self.info(f"Route table resource '{res}' exists")

        rt = self.resources.get('OpenClawRouteTable', {})
        rt_deps = extract_depends_on(rt)
        if 'Sleep' not in rt_deps:
            self.error('RouteTable', "OpenClawRouteTable should DependsOn Sleep (cluster must exist first)")

        entry = self.resources.get('OpenClawRouteEntry', {})
        if entry:
            entry_type = entry.get('Type', '')
            if entry_type != 'ALIYUN::ECS::Route':
                self.error('RouteTable', f"OpenClawRouteEntry Type should be ALIYUN::ECS::Route, got {entry_type}")
            else:
                self.info("OpenClawRouteEntry uses correct type ALIYUN::ECS::Route")

            entry_props = entry.get('Properties', {})
            dest = entry_props.get('DestinationCidrBlock', '')
            if dest != '0.0.0.0/0':
                self.error('RouteTable', f"Route entry destination should be 0.0.0.0/0, got {dest}")
            hop_type = entry_props.get('NextHopType', '')
            if hop_type != 'NatGateway':
                self.error('RouteTable', f"Route entry NextHopType should be NatGateway, got {hop_type}")

            entry_deps = extract_depends_on(entry)
            if 'OpenClawNatEipAssociation' not in entry_deps:
                self.error('RouteTable', "OpenClawRouteEntry should DependsOn OpenClawNatEipAssociation")
            if 'OpenClawRouteTable' not in entry_deps:
                self.error('RouteTable', "OpenClawRouteEntry should DependsOn OpenClawRouteTable")

        for i in range(1, 4):
            assoc = self.resources.get(f'OpenClawRouteTableAssociation{i}', {})
            if assoc:
                assoc_deps = extract_depends_on(assoc)
                if 'OpenClawRouteEntry' not in assoc_deps:
                    self.error('RouteTable',
                               f"OpenClawRouteTableAssociation{i} should DependsOn OpenClawRouteEntry")

    # ==================== Test 10: Addon Installation Chain ====================
    def test_addon_chain(self):
        print("\n=== Test 10: Addon Installation Chain ===")
        required_addons = ['AlbIngressAddon', 'SandboxControllerAddon', 'SandboxManagerAddon']
        for addon in required_addons:
            if addon not in self.resources:
                self.error('Addons', f"Missing addon resource: {addon}")
            else:
                self.info(f"Addon '{addon}' exists")

        # Check arms-prometheus or poseidon
        has_arms = 'ArmsPrometheusAddon' in self.resources
        has_poseidon = 'PoseidonAddon' in self.resources

        if not has_arms and not has_poseidon:
            self.warn('Addons', "Neither ArmsPrometheusAddon nor PoseidonAddon is present")
        if has_arms:
            self.info("ArmsPrometheusAddon present (provides ServiceMonitor CRD)")
        if has_poseidon:
            self.info("PoseidonAddon present (provides TrafficPolicy CRD)")

        # Verify dependency chain order
        addon_order_checks = [
            ('AlbIngressAddon', 'Sleep'),
            ('SandboxControllerAddon', None),  # Should depend on something after ALB
            ('SandboxManagerAddon', 'SandboxControllerAddon'),
        ]
        for addon, expected_dep in addon_order_checks:
            if addon in self.resources and expected_dep:
                deps = extract_depends_on(self.resources[addon])
                if expected_dep not in deps:
                    self.warn('Addons', f"'{addon}' expected to DependsOn '{expected_dep}'")
                else:
                    self.info(f"'{addon}' correctly DependsOn '{expected_dep}'")

    # ==================== Test 11: SandboxSet Validation ====================
    def test_sandboxset(self):
        print("\n=== Test 11: SandboxSet Validation ===")
        ss = self.resources.get('OpenClawSandboxSet', {})
        if not ss:
            self.error('SandboxSet', "OpenClawSandboxSet resource not found")
            return

        deps = extract_depends_on(ss)
        if 'ClusterAppliaction' not in deps:
            self.error('SandboxSet', "Should DependsOn ClusterAppliaction (TLS Secret)")
        if 'AddonSleep' not in deps:
            self.error('SandboxSet', "Should DependsOn AddonSleep (all addons ready)")

        # Check YamlContent contains required fields
        yaml_content = ss.get('Properties', {}).get('YamlContent', {})
        yaml_str = str(yaml_content)

        required_in_sandbox = [
            'app: openclaw',
            'alibabacloud.com/acs',
            'automountServiceAccountToken: false',
            'network.alibabacloud.com/security-group-ids',
            'network.alibabacloud.com/vswitch-ids',
            'ENVD_DIR',
            'DASHSCOPE_API_KEY',
            'GATEWAY_TOKEN',
        ]
        for req in required_in_sandbox:
            if req in yaml_str:
                self.info(f"SandboxSet contains '{req}'")
            else:
                self.error('SandboxSet', f"SandboxSet YAML missing expected content: '{req}'")

    # ==================== Test 12: PrivateZone Setup ====================
    def test_privatezone(self):
        print("\n=== Test 12: PrivateZone Setup ===")
        pz_resources = ['PrivateZone', 'PrivateZoneVpcBinder', 'PrivateZoneCnameRecord']
        for res in pz_resources:
            if res not in self.resources:
                self.error('PrivateZone', f"Missing PrivateZone resource: {res}")
            else:
                self.info(f"PrivateZone resource '{res}' exists")

        pz = self.resources.get('PrivateZone', {})
        if pz.get('Condition') != 'CreatePrivateZoneCondition':
            self.error('PrivateZone', "PrivateZone should have Condition: CreatePrivateZoneCondition")

    # ==================== Test 13: ALB Config Job ====================
    def test_alb_config_job(self):
        print("\n=== Test 13: ALB Config Job ===")
        alb_resources = [
            'EditAlbConfigServiceAccount',
            'EditAlbConfigRole',
            'EditAlbConfigBinding',
            'EditAlbConfigJob',
        ]
        for res in alb_resources:
            if res not in self.resources:
                self.error('ALBConfig', f"Missing ALB Config resource: {res}")
            else:
                self.info(f"ALB Config resource '{res}' exists")

        job = self.resources.get('EditAlbConfigJob', {})
        deps = extract_depends_on(job)
        if 'AddonSleep' not in deps:
            self.warn('ALBConfig', "EditAlbConfigJob should DependsOn AddonSleep")
        if 'EditAlbConfigBinding' not in deps:
            self.error('ALBConfig', "EditAlbConfigJob should DependsOn EditAlbConfigBinding")

    # ==================== Test 14: VPC CIDR Consistency for ExistingVPC ====================
    def test_existing_vpc_cidr_handling(self):
        print("\n=== Test 14: ExistingVPC CIDR Handling ===")

        sg = self.resources.get('OpenClawSecurityGroup', {})
        egress = sg.get('Properties', {}).get('SecurityGroupEgress', [])

        vpc_deny_uses_datasource = False
        vpc_deny_uses_ref = False
        for rule in egress:
            dest = rule.get('DestCidrIp', {})
            policy = rule.get('Policy', 'accept')
            if policy == 'drop' and isinstance(dest, dict):
                if dest.get('Ref') == 'VpcCidrBlock':
                    vpc_deny_uses_ref = True
                if 'Fn::Jq' in dest and 'VpcDataSource' in str(dest):
                    vpc_deny_uses_datasource = True

        if vpc_deny_uses_datasource:
            self.info("Egress VPC deny rule uses VpcDataSource (dynamic, precise for both NewVPC and ExistingVPC)")
        elif vpc_deny_uses_ref:
            self.warn('VpcCidr',
                      "Egress VPC deny rule uses Ref:VpcCidrBlock. In ExistingVPC mode, this parameter "
                      "defaults to 192.168.0.0/16 which may NOT match the actual VPC CIDR. "
                      "Consider querying the actual VPC CIDR via DATASOURCE for ExistingVPC scenario.")

        # Check VpcDataSource exists
        if 'VpcDataSource' in self.resources:
            self.info("VpcDataSource resource exists for dynamic VPC CIDR querying")
        else:
            self.warn('VpcCidr', "VpcDataSource not found - VPC CIDR querying not available")

        # Check ingress rules for ExistingVPC
        ingress = sg.get('Properties', {}).get('SecurityGroupIngress', [])
        uses_datasource_for_ingress = False
        for rule in ingress:
            src = str(rule.get('SourceCidrIp', ''))
            if 'BusinessVSwitch' in src and 'DataSource' in src:
                uses_datasource_for_ingress = True

        if uses_datasource_for_ingress:
            self.info("Ingress rules use DataSource for ExistingVPC VSwitch CIDRs (precise)")
        else:
            self.warn('VpcCidr',
                      "Ingress rules for ExistingVPC do not use DataSource to query exact VSwitch CIDRs. "
                      "Using broader VPC CIDR exposes more attack surface.")

    # ==================== Test 14b: InternalClusterYaml & TestPod ====================
    def test_internal_yaml_and_testpod(self):
        print("\n=== Test 14b: InternalClusterYaml & TestPod ===")

        if 'InternalClusterYaml' in self.params:
            self.info("InternalClusterYaml parameter exists")
            param = self.params['InternalClusterYaml']
            if param.get('Required') is not False and param.get('Required') is not None:
                self.warn('InternalYaml', "InternalClusterYaml should be optional (Required: false)")
            if param.get('Default', 'NOTSET') != '':
                self.warn('InternalYaml', "InternalClusterYaml should default to empty string")
        else:
            self.warn('InternalYaml', "InternalClusterYaml parameter not found")

        if 'HasInternalClusterYaml' in self.conditions:
            self.info("HasInternalClusterYaml condition defined")
        else:
            self.warn('InternalYaml', "HasInternalClusterYaml condition not defined")

        ica = self.resources.get('InternalClusterApplication', {})
        if ica:
            self.info("InternalClusterApplication resource exists")
            if ica.get('Condition') != 'HasInternalClusterYaml':
                self.error('InternalYaml',
                           "InternalClusterApplication should have Condition: HasInternalClusterYaml")
        else:
            self.warn('InternalYaml', "InternalClusterApplication resource not found")

        if 'TestPod' in self.resources:
            self.info("TestPod resource exists")
            tp_deps = extract_depends_on(self.resources['TestPod'])
            if 'OpenClawSandboxSet' not in tp_deps:
                self.warn('TestPod', "TestPod should DependsOn OpenClawSandboxSet")
        else:
            self.warn('TestPod', "TestPod resource not found")

    # ==================== Test 14c: ALB VSwitch Condition ====================
    def test_alb_vswitch_condition(self):
        print("\n=== Test 14c: ALB VSwitch Condition Safety ===")

        cond = self.conditions.get('UseCustomAlbVSwitchCondition', {})
        cond_str = str(cond)
        has_fn_and = 'Fn::And' in cond_str
        uses_vpc_check = 'ExistingVPC' in cond_str or 'VpcOption' in cond_str
        if has_fn_and and uses_vpc_check:
            self.info("UseCustomAlbVSwitchCondition is compound (Fn::And) with VpcOption check")
        elif uses_vpc_check:
            self.info("UseCustomAlbVSwitchCondition includes VpcOption check")
        else:
            self.warn('ALBVSwitch',
                      "UseCustomAlbVSwitchCondition only checks EnableCustomAlbVSwitch, not VpcOption. "
                      "If CLI user sets EnableCustomAlbVSwitch=true with NewVPC, ALB will use null VSwitch IDs. "
                      "Consider making this a compound condition with Fn::And.")

        if 'AlbVSwitchId1' in self.params:
            meta = self.params['AlbVSwitchId1'].get('AssociationPropertyMetadata', {})
            visible = meta.get('Visible', {})
            visible_str = str(visible)
            if 'ExistingVPC' in visible_str and 'EnableCustomAlbVSwitch' in visible_str:
                self.info("AlbVSwitchId1 UI visibility correctly requires ExistingVPC + EnableCustomAlbVSwitch")
            else:
                self.warn('ALBVSwitch', "AlbVSwitchId1 UI visibility may not properly guard against NewVPC scenario")

    # ==================== Test 15: Outputs Completeness ====================
    def test_outputs(self):
        print("\n=== Test 15: Outputs Completeness ===")
        expected_outputs = [
            'ALB_DNS_Name', 'ClusterId', 'VpcId',
            'OpenClawSecurityGroupId', 'E2B_API_KEY', 'E2B_DOMAIN',
            'OpenClawNatGatewayId', 'OpenClawNatEipAddress',
            'ApiServerIntranetIp', 'DefaultNatGatewayIp',
        ]
        for out in expected_outputs:
            if out not in self.outputs:
                self.warn('Outputs', f"Missing expected output: {out}")
            else:
                self.info(f"Output '{out}' exists")

    # ==================== Test 16: Metadata ParameterGroups ====================
    def test_metadata(self):
        print("\n=== Test 16: Metadata & ParameterGroups ===")
        metadata = self.template.get('Metadata', {})
        interface = metadata.get('ALIYUN::ROS::Interface', {})

        hidden = interface.get('Hidden', [])
        if 'ClusterOption' not in hidden:
            self.warn('Metadata', "ClusterOption should be hidden (ACS-only)")
        if 'OpenClawImage' not in hidden:
            self.warn('Metadata', "OpenClawImage should be hidden")

        groups = interface.get('ParameterGroups', [])
        self.info(f"Found {len(groups)} parameter groups")

        all_grouped_params = set()
        for group in groups:
            all_grouped_params.update(group.get('Parameters', []))

        for param in self.params:
            if param not in all_grouped_params and param not in hidden:
                self.warn('Metadata', f"Parameter '{param}' not in any ParameterGroup and not hidden")

    # ==================== Test 17: Documentation Checklist ====================
    def test_documentation_checklist(self):
        print("\n=== Test 17: Documentation Compliance Checklist ===")

        resource_names = set(self.resources.keys())

        # From 托管OpenClaw推荐网络架构.md:
        # 1. Independent network segment for OpenClaw
        has_oc_vsw = all(f'OpenClawVSwitch{i}' in resource_names for i in range(1, 4))
        if has_oc_vsw:
            self.info("[Arch] OpenClaw has independent VSwitches (3 AZ)")
        else:
            self.error('DocCompliance', "[Arch] Missing independent OpenClaw VSwitches")

        # 2. Independent security group
        if 'OpenClawSecurityGroup' in resource_names:
            self.info("[Arch] OpenClaw has independent SecurityGroup")
        else:
            self.error('DocCompliance', "[Arch] Missing independent SecurityGroup")

        # 3. Independent NAT gateway
        if 'OpenClawNatGateway' in resource_names:
            self.info("[Arch] OpenClaw has independent NAT Gateway")
        else:
            self.error('DocCompliance', "[Arch] Missing independent NAT Gateway")

        # 4. GlobalTrafficPolicy - deny OpenClaw CIDR ingress to other pods
        if 'GlobalTrafficPolicyApplication' in resource_names:
            self.info("[Arch] GlobalTrafficPolicy (deny lateral access) present")
        else:
            self.error('DocCompliance',
                       "[Arch] GlobalTrafficPolicy MISSING - required by 网络隔离方案.md & 推荐网络架构.md "
                       "to block lateral access from OpenClaw to other applications")

        # 5. TrafficPolicy for OpenClaw pods
        if 'OpenClawTrafficPolicyApplication' in resource_names:
            self.info("[Arch] OpenClaw TrafficPolicy (fine-grained control) present")
        else:
            self.error('DocCompliance',
                       "[Arch] OpenClaw TrafficPolicy MISSING - required by 网络隔离方案.md "
                       "for fine-grained ingress/egress control on OpenClaw pods")

        # From Sandbox Gateway.md:
        # Gateway flow: External -> Ingress -> mTLS -> Sandbox Gateway -> Sandbox
        # The TrafficPolicy should allow sandbox-gateway access
        if 'OpenClawTrafficPolicyApplication' in resource_names:
            tp_yaml = str(self.resources['OpenClawTrafficPolicyApplication'])
            if 'sandbox-gateway' in tp_yaml:
                self.info("[Gateway] TrafficPolicy allows sandbox-gateway ingress")
            else:
                self.error('DocCompliance', "[Gateway] TrafficPolicy missing sandbox-gateway ingress allow rule")
        else:
            self.error('DocCompliance',
                       "[Gateway] Cannot verify sandbox-gateway access - TrafficPolicy resource missing entirely")

    def run_all_tests(self):
        print(f"\n{'='*60}")
        print(f"Template Validation: {self.path}")
        print(f"{'='*60}")

        self.test_yaml_structure()
        self.test_parameters()
        self.test_reference_integrity()
        self.test_condition_integrity()
        self.test_dependency_chain()
        self.test_cidr_validation()
        self.test_security_group_rules()
        self.test_traffic_policy_compliance()
        self.test_nat_gateway()
        self.test_route_table()
        self.test_addon_chain()
        self.test_sandboxset()
        self.test_privatezone()
        self.test_alb_config_job()
        self.test_existing_vpc_cidr_handling()
        self.test_internal_yaml_and_testpod()
        self.test_alb_vswitch_condition()
        self.test_outputs()
        self.test_metadata()
        self.test_documentation_checklist()

        print(f"\n{'='*60}")
        print(f"RESULTS SUMMARY")
        print(f"{'='*60}")
        print(f"Total ERRORS: {len(self.errors)}")
        print(f"Total WARNINGS: {len(self.warnings)}")

        if self.errors:
            print(f"\n--- ERRORS ({len(self.errors)}) ---")
            for e in self.errors:
                print(f"  {e}")

        if self.warnings:
            print(f"\n--- WARNINGS ({len(self.warnings)}) ---")
            for w in self.warnings:
                print(f"  {w}")

        return len(self.errors) == 0


if __name__ == '__main__':
    import os
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    print("=" * 80)
    print("VALIDATING CURRENT template-production.yaml")
    print("=" * 80)
    validator = TemplateValidator(os.path.join(project_root, 'template-production.yaml'))
    current_ok = validator.run_all_tests()

    bak_path = os.path.join(project_root, 'template-production.yaml.bak')
    print("\n\n")
    print("=" * 80)
    print("VALIDATING OLD template-production.yaml.bak")
    print("=" * 80)
    validator_bak = TemplateValidator(bak_path)
    bak_ok = validator_bak.run_all_tests()

    print("\n\n")
    print("=" * 80)
    print("COMPARISON SUMMARY")
    print("=" * 80)
    print(f"Current template: {len(validator.errors)} errors, {len(validator.warnings)} warnings")
    print(f"Old template (.bak): {len(validator_bak.errors)} errors, {len(validator_bak.warnings)} warnings")

    current_only_errors = set(validator.errors) - set(validator_bak.errors)
    bak_only_errors = set(validator_bak.errors) - set(validator.errors)

    if current_only_errors:
        print(f"\n--- NEW ERRORS in current version (regressions) ---")
        for e in sorted(current_only_errors):
            print(f"  {e}")

    if bak_only_errors:
        print(f"\n--- FIXED ERRORS in current version (improvements) ---")
        for e in sorted(bak_only_errors):
            print(f"  {e}")

    sys.exit(0 if current_ok else 1)

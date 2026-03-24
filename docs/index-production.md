
# OpenClaw 企业版 - 生产级部署指南

本文档介绍 OpenClaw 企业版**生产级**部署方案，适用于对网络隔离、安全性、高可用有严格要求的企业客户。


## 方案概览

生产级部署基于 **ACK 托管集群 + VirtualNode (ACS)** 架构，支持 **3 可用区高可用**、**Poseidon TrafficPolicy 网络隔离**和 **PodNetworking VSwitch 隔离**。

- **集群类型**：ACK Pro 托管集群 + VirtualNode（Sandbox Pod 运行在 ACS 弹性算力上）
- **节点管理**：ECS 节点池运行管控组件（sandbox-manager 等），Sandbox Pod 按需弹性
- **网络隔离**：Poseidon TrafficPolicy + PodNetworking + 安全组多层隔离
- **高可用**：3 可用区部署，6 交换机（3 业务 + 3 OpenClaw 隔离）

### 网络架构

- **3 可用区**：跨 AZ 高可用部署
- **6 交换机**：3 个业务交换机 + 3 个 OpenClaw 隔离交换机
- **独立 NAT 网关**：OpenClaw 沙箱使用独立 NAT 网关和 EIP 出公网
- **ALB Ingress**：通过 ALB 负载均衡器作为入口网关
- **PrivateZone**：VPC 内泛域名解析

```
                    ┌─────────────────────────────────────────────────┐
                    │                    VPC                          │
                    │                                                 │
                    │  ┌──────────────┐    ┌──────────────────────┐   │
  用户请求 ──────────┤  │  ALB Ingress │───▶│  sandbox-manager     │   │
                    │  └──────────────┘    │  (sandbox-system ns)  │   │
                    │                      └──────────┬───────────┘   │
                    │                                 │               │
                    │         ┌────────────────────────┘               │
                    │         ▼                                       │
                    │  ┌─────────────────────────────────────────┐    │
                    │  │     OpenClaw 隔离网段 (独立交换机)        │    │
                    │  │                                         │    │
                    │  │  ┌──────────┐ ┌──────────┐ ┌─────────┐ │    │
                    │  │  │ Sandbox1 │ │ Sandbox2 │ │ Sandbox3│ │    │
                    │  │  └────┬─────┘ └────┬─────┘ └────┬────┘ │    │
                    │  │       │            │            │       │    │
                    │  │       └────────────┼────────────┘       │    │
                    │  │                    │                    │    │
                    │  └────────────────────┼────────────────────┘    │
                    │                       │                         │
                    │              ┌────────▼────────┐                │
                    │              │  独立 NAT 网关   │                │
                    │              │  (独立 EIP)      │                │
                    │              └────────┬────────┘                │
                    └───────────────────────┼─────────────────────────┘
                                            │
                                            ▼
                                        公网服务
```

### 网络隔离策略

生产级部署通过多层安全策略实现沙箱网络隔离：

**第一层：VSwitch 隔离（PodNetworking）**
- 通过 PodNetworking CRD 将 OpenClaw 沙箱 Pod 调度到独立的隔离交换机，与业务网络物理隔离

**第二层：Poseidon TrafficPolicy**
- 通过 GlobalTrafficPolicy 和 TrafficPolicy CRD 实现 Kubernetes 层面的精细化网络策略

**第三层：独立 NAT 网关**
- OpenClaw 沙箱使用独立的 NAT 网关和 EIP 出公网，与业务流量完全隔离

**核心安全规则**：
- ✅ 允许：sandbox-manager → Sandbox（管控流量）
- ✅ 允许：Sandbox → 公网（通过独立 NAT）
- ✅ 允许：Sandbox → DNS 服务
- ❌ 拒绝：Sandbox → VPC 内网段（防止横向渗透）
- ❌ 拒绝：Sandbox → 元数据服务（100.100.100.200）
- ❌ 拒绝：其他应用 → OpenClaw 网段（全局隔离）

## 前置条件

1. 拥有阿里云账号，并已完成实名认证
2. 准备 TLS 证书文件（`fullchain.pem` 和 `privkey.pem`），用于 E2B API 的 HTTPS 访问
3. （可选）准备百炼 API Key，用于 OpenClaw 的 AI 能力

## 部署步骤

### 步骤 1：创建服务实例

1. 登录 [计算巢控制台](https://computenest.console.aliyun.com)
2. 找到 **OpenClaw-ACS-Sandbox集群版** 服务
3. 点击 **创建服务实例**

![计算巢创建服务实例页面](img_prod_create.png)

### 步骤 2：选择模板

在创建页面顶部，选择 **生产环境** 模板：

- **测试环境**：单可用区，适合快速验证
- **生产环境**：3 可用区高可用，网络隔离，适合正式使用

### 步骤 3：配置 VPC 与可用区

| 参数 | 说明 | 建议值 |
|------|------|--------|
| **可用区 1/2/3** | 选择 3 个不同的可用区 | 根据地域选择 |
| **选择已有/新建的专有网络** | 新建或使用已有 VPC | 新建专有网络 |
| **专有网络 IPv4 网段** | VPC 主网段 | `192.168.0.0/16` |

### 步骤 4：配置业务交换机

为 3 个可用区分别配置业务交换机网段，用于集群节点和管控组件：

| 参数 | 说明 | 建议值 |
|------|------|--------|
| **业务交换机子网网段 1** | 可用区 1 的业务网段 | `192.168.0.0/24` |
| **业务交换机子网网段 2** | 可用区 2 的业务网段 | `192.168.1.0/24` |
| **业务交换机子网网段 3** | 可用区 3 的业务网段 | `192.168.2.0/24` |

### 步骤 5：配置 OpenClaw 隔离网段

为 OpenClaw 沙箱配置独立的隔离网段，实现与业务网络的物理隔离：

| 参数 | 说明 | 建议值 |
|------|------|--------|
| **OpenClaw 专用交换机网段 1** | 可用区 1 的隔离网段 | `10.8.0.0/24` |
| **OpenClaw 专用交换机网段 2** | 可用区 2 的隔离网段 | `10.8.1.0/24` |
| **OpenClaw 专用交换机网段 3** | 可用区 3 的隔离网段 | `10.8.2.0/24` |
| **OpenClaw 汇总网段** | 覆盖所有隔离网段的汇总 CIDR | `10.8.0.0/16` |

> ⚠️ **重要**：OpenClaw 隔离网段建议使用与 VPC 主网段不同的地址空间（如 `10.8.x.0/24`），便于 TrafficPolicy 和安全组规则精细化控制。汇总网段必须覆盖所有 3 个隔离网段。

### 步骤 6：配置集群参数

| 参数 | 说明 | 建议值 |
|------|------|--------|
| **Service CIDR** | Kubernetes Service 网段 | `172.16.0.0/16` |

> Service CIDR 不能与 VPC 网段和已有集群网段重复，创建后不可修改。

### 步骤 7：配置 E2B 参数

| 参数 | 说明 | 是否必填 |
|------|------|---------|
| **E2B 的访问域名** | E2B API 的访问域名 | 选填 |
| **TLS 证书** | `fullchain.pem` 证书文件 | **必填** |
| **TLS 证书密钥** | `privkey.pem` 私钥文件 | **必填** |
| **是否配置内网域名解析** | 自动创建 PrivateZone | 建议开启 |
| **访问 E2B API 的 API_KEY** | E2B 管理 API 密钥 | 选填 |
| **Sandbox Manager CPU** | sandbox-manager CPU 资源 | 默认即可 |
| **Sandbox Manager 内存** | sandbox-manager 内存资源 | 默认即可 |
| **是否支持公网访问** | ALB 开启公网访问 | 根据需求 |

### 步骤 8：配置 OpenClaw 参数

| 参数 | 说明 | 是否必填 |
|------|------|---------|
| **Sandbox 命名空间** | SandboxSet 和 TestPod 所在的命名空间 | 默认 `default` |
| **百炼 API-KEY** | 百炼模型服务的 API Key | 选填 |
| **OpenClaw 访问 Token** | 访问 OpenClaw 服务的 Token | 选填 |

### 步骤 9：确认并创建

1. 点击 **下一步：确认订单**
2. 确认配置参数和费用
3. 点击 **创建** 开始部署

> 部署预计耗时 **15-22 分钟**，请耐心等待。

## 部署验证

### 查看服务实例状态

部署完成后，在计算巢控制台的 **服务实例** 页面可以看到实例状态变为 **已部署**。


### 自动化测试

1. 在服务实例详情页找到 ACK 集群
2. 进入集群的容器组界面，找到 `acs-sandbox-test-pod`
3. 点击终端登录
4. 执行测试脚本：

```bash
python testopenclaw.py
```

5. 等待脚本验证所有功能通过，日志中出现 **"创建 sandbox 耗时"** 即代表验证通过

## SandboxSet 配置

生产级 SandboxSet 配置示例：

```yaml
apiVersion: agents.kruise.io/v1alpha1
kind: SandboxSet
metadata:
  name: openclaw
  namespace: default
  annotations:
    e2b.agents.kruise.io/should-init-envd: "true"
  labels:
    app: openclaw
spec:
  persistentContents: 
  - filesystem
  replicas: 1
  template:
    metadata:
      labels:        
        alibabacloud.com/acs: "true"
        app: openclaw
      annotations:
        ops.alibabacloud.com/pause-enabled: "true"
    spec:
      restartPolicy: Always
      automountServiceAccountToken: false
      enableServiceLinks: false
      initContainers:
        - name: init
          image: registry-cn-hangzhou.ack.aliyuncs.com/acs/agent-runtime:v0.0.2
          imagePullPolicy: IfNotPresent
          command: [ "sh", "/workspace/entrypoint_inner.sh" ]
          volumeMounts:
            - name: envd-volume
              mountPath: /mnt/envd
          env:
            - name: ENVD_DIR
              value: /mnt/envd
            - name: __IGNORE_RESOURCE__
              value: "true"
          restartPolicy: Always
      containers:
        - name: openclaw
          image: registry-cn-hangzhou.ack.aliyuncs.com/ack-demo/openclaw:2026.3.2
          imagePullPolicy: IfNotPresent
          securityContext:
            readOnlyRootFilesystem: false
            runAsGroup: 0
            runAsUser: 0
          resources:
            requests:
              cpu: 2
              memory: 4Gi
            limits:
              cpu: 2
              memory: 4Gi
          env:
            - name: ENVD_DIR
              value: /mnt/envd
            - name: DASHSCOPE_API_KEY 
              value: sk-xxxxxxxxxxxxxxxxx  # 替换为真实的 API_KEY
            - name: GATEWAY_TOKEN 
              value: clawdbot-mode-123456  # 替换为访问 Token
          volumeMounts:
            - name: envd-volume
              mountPath: /mnt/envd            
          startupProbe:
            tcpSocket:
              port: 18789
            initialDelaySeconds: 5
            periodSeconds: 5
            failureThreshold: 30
          lifecycle:
            postStart:
              exec:
                command: [ "/bin/bash", "-c", "/mnt/envd/envd-run.sh" ]        
      terminationGracePeriodSeconds: 30
      volumes:
        - emptyDir: { }
          name: envd-volume
```

**重要字段说明**

*   `SandboxSet.spec.persistentContents: filesystem` — 在 pause/connect 的过程中只保留文件系统（不保留 IP、内存）
*   `template.spec.restartPolicy: Always`
*   `template.spec.automountServiceAccountToken: false` — Pod 不挂载 Service Account
*   `template.spec.enableServiceLinks: false` — Pod 不注入 Service 环境变量
*   `template.metadata.labels.alibabacloud.com/acs: "true"` — 使用 ACS 算力
*   `template.metadata.annotations.ops.alibabacloud.com/pause-enabled: "true"` — 支持 pause/connect 动作
*   `template.spec.initContainer` — 下载并 copy envd 的环境，保留即可
*   `template.spec.initContainers.restartPolicy: Always`
*   `template.spec.containers.securityContext.runAsNonRoot: true` — Pod 使用普通用户启动
*   `template.spec.containers.securityContext.privileged: false` — 禁用特权配置
*   `template.spec.containers.securityContext.allowPrivilegeEscalation: false`
*   `template.spec.containers.securityContext.seccompProfile.type: RuntimeDefault`
*   `template.spec.containers.securityContext.capabilities.drop: [ALL]`
*   `template.spec.containers.securityContext.readOnlyRootFilesystem: false`

> ⚠️ 如果预期使用 Pause，**一定不要设置** liveness/readiness 的探针，避免在暂停期间的健康检查问题。

**必要的修改**

*   `registry-cn-hangzhou.ack.aliyuncs.com/acs/agent-runtime` — 修改为所在地域的镜像，并且是内网镜像（目前需手动替换，未来会自动注入）
*   `registry-cn-hangzhou.ack.aliyuncs.com/ack-demo/openclaw:2026.3.2` — 替换为客户自己构建的镜像
*   若为了提升拉取速度，也可替换为内网镜像：`registry-${RegionId}-vpc.ack.aliyuncs.com/ack-demo/openclaw:2026.3.2`

**机制简要说明**

通过在 Pod 启动 envd，来支持 E2B SDK 的服务端接口。通过 kubectl 创建上述资源，SandboxSet 创建完成后，可以看到沙箱已经处于可用状态。

## 访问 OpenClaw Web UI

### 域名格式

OpenClaw 沙箱通过 PrivateZone 泛域名解析 + ALB 路由实现访问，域名格式为：

```
<port>-<namespace>--<pod-name>.<e2b-domain>?token=<gateway-token>
                 ↑↑
              双连字符（重要！）
```

**参数说明**：
- **`port`**：OpenClaw Web UI 端口，固定为 `18789`
- **`namespace`**：Pod 所在命名空间，默认为 `default`
- **`pod-name`**：Sandbox Pod 名称，如 `openclaw-abc12`
- **`e2b-domain`**：部署时配置的 E2B 域名
- **`gateway-token`**：SandboxSet 中配置的 `GATEWAY_TOKEN` 值

**示例 URL**：
```
https://18789-default--openclaw-abc12.agent-vpc.infra?token=clawdbot-mode-123456
```

> ⚠️ namespace 和 pod-name 之间必须使用**双连字符 `--`**，使用单连字符会导致 502 错误。

### 获取 Sandbox Pod 名称

```bash
kubectl get pods -n default -l app=openclaw
```

### 配置域名解析

#### 方式一：本地 Host 配置（快速验证）

1. 获取 ALB 访问端点：在服务实例详情页查看 ALB 域名
2. 通过 `ping` 或 `dig` 获取 ALB 公网 IP
3. 配置 `/etc/hosts`：

```bash
sudo vim /etc/hosts
# 添加以下内容（替换为实际的 ALB IP 和 Pod 名称）
39.103.89.43 18789-default--openclaw-abc12.agent-vpc.infra
39.103.89.43 api.agent-vpc.infra
```

#### 方式二：DNS 解析（生产环境）

1. 获取 ALB 访问端点
2. 在 DNS 服务商处，将 ALB 端点以 **CNAME** 记录解析到对应域名
3. 如需内网访问，可通过 PrivateZone 添加内网域名解析

## 使用 E2B SDK 创建沙箱

### 通过 API 创建

```bash
curl --cacert fullchain.pem -X POST --location "https://api.<e2b-domain>/sandboxes" \
    -H "Content-Type: application/json" \
    -H "X-API-Key: <admin-api-key>" \
    -d '{
          "templateID": "openclaw",
          "timeout": 300
        }'
```

返回结果中存在 `sandboxID` 且 `state: "running"` 即表示创建成功。

### 通过 Python SDK 创建

```python
from e2b_code_interpreter import Sandbox

sandbox = Sandbox.create(                
    template="openclaw",    
    request_timeout=60,
    metadata={
        "e2b.agents.kruise.io/never-timeout": "true"
    }
)
result = sandbox.commands.run("whoami")
print(f"Running in sandbox as \"{result.stdout.strip()}\"")
```

### 休眠与唤醒

```python
from e2b_code_interpreter import Sandbox
import time

# 创建 sandbox
sandbox = Sandbox.create("openclaw", timeout=1800)
print(f"Sandbox ID: {sandbox.sandbox_id}")

# 写入测试文件
sandbox.files.write("/tmp/test.txt", "Hello, World!")

# 暂停 sandbox
pause_result = sandbox.beta_pause()
print(f"Pause result: {pause_result}")

# 等待一段时间
time.sleep(60)

# 重新连接
same_sandbox = sandbox.connect(timeout=180)
print(f"Reconnected: {same_sandbox.sandbox_id}")

# 验证文件仍然存在
content = same_sandbox.files.read("/tmp/test.txt")
print(f"File content: {content}")
```

## 网络隔离详解

生产级部署通过 **Poseidon 网络策略组件**和 **PodNetworking CRD** 实现 Kubernetes 层面的网络隔离。

### GlobalTrafficPolicy

全局级策略，保护集群中其他应用不被 OpenClaw 网段访问（防止横向渗透）：
```yaml
apiVersion: network.alibabacloud.com/v1alpha1
kind: GlobalTrafficPolicy
metadata:
  name: global-black-list
spec:
  priority: 1000
  selector: {}
  ingress:
    rules:
      - action: deny
        from:
          - cidr: <openclaw-cidr>  # 拒绝来自 OpenClaw 网段的入站流量
```

### TrafficPolicy

应用级策略，精细化控制 OpenClaw Pod 的出入方向流量：
```yaml
apiVersion: network.alibabacloud.com/v1alpha1
kind: TrafficPolicy
metadata:
  name: openclaw-policy
spec:
  priority: 100
  selector:
    matchLabels:
      app: openclaw
  ingress:
    rules:
      - action: allow
        from:
          - service: { name: sandbox-gateway }  # 仅允许 Gateway 访问
      - action: deny
        from:
          - cidr: 0.0.0.0/0
  egress:
    rules:
      - action: deny
        to:
          - cidr: 100.100.100.200/32  # 拒绝 metadata
      - action: allow
        to:
          - service: { name: kube-dns }  # 允许 DNS
      - action: deny
        to:
          - cidr: 172.16.0.0/12       # 拒绝内网段
          - cidr: 192.168.0.0/16
          - cidr: 10.0.0.0/8
      - action: allow
        to:
          - cidr: 0.0.0.0/0           # 允许公网
```

### PodNetworking

将 Sandbox Pod 调度到 OpenClaw 隔离交换机，并绑定指定安全组：
```yaml
apiVersion: network.alibabacloud.com/v1beta1
kind: PodNetworking
metadata:
  name: openclaw-network
spec:
  allocationType:
    type: Elastic
  selector:
    podSelector:
      matchLabels:
        app: openclaw
  securityGroupIDs:
    - "<sg-id>"
  vSwitchOptions:
    - "<openclaw-vsw-1>"
    - "<openclaw-vsw-2>"
    - "<openclaw-vsw-3>"
```

## 可观测能力
### OpenClaw 日志
SLS k8s原生能力在ACK集群内通过 loongcollector 组件提供，通过CR的方式创建采集配置，对应的CRD资源名为ClusterAliyunPipelineConfig。

![img_16.png](img_16.png)

SLS提供开箱即用的OpenClaw采集配置，可以通过SLS控制台访问OpenClaw日志，对应的SLS的Project为k8s-log-${ack集群id},
- OpenClaw Runtime日志（网关 / 应用）
  - 对应的 logstore 为 openclaw-runtime
  - 对应的采集配置为 openclaw-runtime-config
  - 对应的K8s集群中的CR名为 openclaw-runtime-config
- OpenClaw Session 审计日志
  - 对应的 logstore 为 openclaw-session
  - 对应的采集配置为 openclaw-session-config
  - 对应的K8s集群中的CR名为 openclaw-session-config

针对OpenClaw日志，SLS内置仪表盘覆盖安全审计、成本分析、行为分析三个维度:
- OpenClaw 行为分析大盘: 对 OpenClaw 的运行行为进行全量记录与分类统计
- OpenClaw 审计大盘: 从行为总览、高危命令、提示词注入、数据外泄等维度展开，提供实时行为监控、威胁识别与事后溯源的完整能力
- OpenClaw Token 分析大盘: 从整体概览、模型维度趋势、会话等维度展开，提供用量监控、成本分析与异常发现能力

![img_15.png](img_15.png)

注意：
内置采集配置仅针对demo镜像，自定义镜像的日志路径、容器过滤条件等可能有所不同，可以在ACK集群内通过修改对应的CR进行配置修正。

## 重要时间预估

| 阶段 | 预估时间 |
|------|----------|
| ACK 集群创建 | 8-12 分钟 |
| Addon 安装（VirtualNode、Poseidon 等） | 2-3 分钟 |
| StorageClass 就绪 | 2 分钟 |
| SandboxSet 预热 | 2-3 分钟 |
| LoadBalancer 分配 | 1-2 分钟 |
| **总计** | **15-22 分钟** |

## 常见问题

### 部署失败如何排查？

1. 在计算巢服务实例详情页查看部署日志
2. 进入 ROS 控制台查看 Stack 事件，找到第一个 `CREATE_FAILED` 事件
3. 根据 `StatusReason` 定位根因

### kubeconfig 无法连接？

如果获取的 kubeconfig 使用内网 IP 无法连接，需要为集群绑定 EIP 或使用 VPN 访问。

### Pod 启动慢？

SandboxSet 首次启动需要拉取镜像，约需 2-3 分钟。可通过以下命令查看进度：

```bash
kubectl describe pod -l app=openclaw -n default
```

### 如何扩容沙箱数量？

修改 SandboxSet 的 `replicas` 字段：

```bash
kubectl patch sandboxset openclaw -n default --type merge -p '{"spec":{"replicas": 5}}'
```

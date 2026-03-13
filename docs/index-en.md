# OpenClaw 企业版部署指南

> 基于 ACS Agent Sandbox 构建企业级 AI Agent 应用

## 概述

OpenClaw 是一款开源的 AI 编程助手，支持多平台运行。本服务基于阿里云 ACS（容器计算服务）和 E2B框架，提供企业级的一键部署方案。

### 核心特性

- **秒级沙箱启动**：通过 SandboxSet 预热池 + 镜像缓存实现亚秒级沙箱交付
- **会话状态保持**：支持沙箱休眠与唤醒，保留内存状态
- **持久化存储**：集成 NAS 文件存储，数据跨会话持久化
- **E2B 协议兼容**：支持原生 E2B SDK，无缝迁移现有应用

### 部署方式对比

| 部署方式 | 难度 | 时间 | 适用场景 |
|---------|------|------|---------|
| **计算巢控制台** | ⭐ 简单 | 10-15分钟 | 快速体验、测试环境、生产环境 |
| **手动部署** | ⭐⭐⭐ 复杂 | 30-60分钟 | 定制化需求、学习研究 |

## 前提准备

### 1. 准备域名

E2B 协议需要一个域名（E2B_DOMAIN）来指定后端服务。

- **测试环境**：可使用测试域名，如 `agent-vpc.infra`（需配置 hosts 或 PrivateZone）
- **生产环境**：
  - 参考 [域名注册快速入门](https://help.aliyun.com/document_detail/35789.html)
  - 中国内地部署需要 [域名备案](https://beian.aliyun.com/)

### 2. 获取 TLS 证书

E2B 客户端通过 HTTPS 请求后端，需要申请通配符证书。

**测试环境 - Let's Encrypt 免费证书：**

```bash
# 安装 certbot
brew install certbot  # macOS
# 或 snap install certbot  # Linux

# 申请通配符证书
sudo certbot certonly \
  --manual \
  --preferred-challenges=dns \
  --email your-email@example.com \
  --server https://acme-v02.api.letsencrypt.org/directory \
  --agree-tos \
  -d "*.your.domain.cn"

# 导出证书
sudo cp /etc/letsencrypt/live/your.domain/fullchain.pem ./fullchain.pem
sudo cp /etc/letsencrypt/live/your.domain/privkey.pem ./privkey.pem
```

**生产环境**：推荐 [购买正式证书](https://help.aliyun.com/document_detail/28542.html)

### 3. 获取百炼 API Key

登录 [百炼控制台](https://bailian.console.aliyun.com/) 创建 API Key，用于 AI 模型调用。

### 4. 配置镜像缓存加速（可选但强烈推荐）

镜像缓存可显著加速 ACS Pod 启动，将镜像拉取时间从**分钟级降低到秒级**。

> **重要说明**：镜像缓存功能需要在 **ACS 集群层面** 申请白名单开通，无法通过 ROS 模板自动配置。请在部署前完成以下步骤。

**步骤一：申请镜像缓存白名单**

1. [提交工单](https://smartservice.console.aliyun.com/service/create-ticket) 申请开通镜像缓存功能
2. 工单标题建议填写：「申请开通 ACS 镜像缓存功能」
3. 内容中注明需要开通的地域和账号 UID
4. 等待工单处理完成（通常 1-2 个工作日）

**步骤二：创建镜像缓存**

白名单开通后，创建镜像缓存：

1. 登录 [容器计算服务控制台](https://acs.console.aliyun.com/)
2. 左侧导航栏选择「镜像缓存」→「创建镜像缓存」
3. 配置：
   - **镜像缓存名**：`openclaw-image-cache`
   - **镜像**：`registry-cn-hangzhou.ack.aliyuncs.com/ack-demo/openclaw:2026.3.2`
4. 等待状态变为「制作完成」

**步骤三：在 SandboxSet 中启用镜像缓存**

对于已有集群，需要修改 SandboxSet 配置启用镜像缓存：

```bash
kubectl edit sandboxset openclaw
```

在 Pod template 的 annotations 中添加：

```yaml
apiVersion: agents.kruise.io/v1alpha1
kind: SandboxSet
metadata:
  name: openclaw
spec:
  template:
    metadata:
      annotations:
        # 启用镜像缓存加速
        image.alibabacloud.com/enable-image-cache: "true"
    spec:
      containers:
        - name: openclaw
          image: registry-cn-hangzhou.ack.aliyuncs.com/ack-demo/openclaw:2026.3.2
          # 必须设置为 Always，否则缓存不生效
          imagePullPolicy: Always
```

> **重要**：`imagePullPolicy` 必须设置为 `Always`，镜像缓存才能生效。

**计费说明**：每个地域免费 20 个镜像缓存，超出部分 0.18 元/GiB/月

**支持地域**：华北2（北京）、华东2（上海）、华东1（杭州）、华北6（乌兰察布）、华南1（深圳）、中国香港、新加坡

> **注意**：如果未开通镜像缓存白名单，Pod 启动时会报 403 错误。

## 方式一：计算巢控制台部署（推荐新手）

1. **访问计算巢服务**
   
   打开 [计算巢服务部署链接](https://computenest.console.aliyun.com/)，搜索 **ack-sandbox-manager**

2. **填写部署参数**

   | 参数组 | 参数 | 说明 |
   |-------|------|------|
   | 基本配置 | 地域 | 选择就近地域（cn-hangzhou、cn-beijing 等） |
   | | 可用区 | 选择两个不同的可用区（高可用） |
   | | VPC 配置 | 新建或使用已有 VPC |
   | E2B 配置 | E2B 域名 | 前提准备阶段的域名 |
   | | TLS 证书 | 上传 `fullchain.pem` |
   | | TLS 证书私钥 | 上传 `privkey.pem` |

3. **确认部署**

   点击「确认订单」开始部署，约需 10-15 分钟。

4. **获取访问信息**

   部署成功后，在服务实例详情页查看：
   - **E2B_API_KEY**：访问 E2B API 的密钥
   - **E2B_DOMAIN**：E2B 域名
   - **ClusterId**：ACS 集群 ID

## 部署后配置

### 安装 EIP 组件（可选）

如需为 Pod 分配独立公网 IP，需要安装 `ack-extend-network-controller` 组件：

**方式 A - 控制台安装（推荐）：**

1. 登录 [容器服务控制台](https://cs.console.aliyun.com/)
2. 进入集群详情 → 「组件管理」
3. 搜索 `ack-extend-network-controller`，点击「安装」
4. 配置参数（使用默认值即可），确认安装



**验证安装：**

直至组件显示安装成功

### 为 Pod 配置 EIP

安装 EIP 组件后，可通过 Pod annotation 为沙箱分配独立公网 IP：

```yaml
apiVersion: agents.kruise.io/v1alpha1
kind: SandboxSet
metadata:
  name: openclaw
spec:
  template:
    metadata:
      annotations:
        # 启用 Pod EIP
        network.alibabacloud.com/pod-with-eip: "true"
        # EIP 带宽（Mbps）
        network.alibabacloud.com/eip-bandwidth: "5"
```

**查看 Pod EIP：**

```bash
kubectl get pod -o wide
kubectl describe pod <pod-name> | grep -i eip
```

### 配置域名解析

**本地测试（Hosts 方式）：**

```bash
# 获取 ALB 公网 IP
kubectl get ingress -n sandbox-system

# 配置 hosts
echo "<ALB_IP> api.your.domain.cn" >> /etc/hosts
```

**生产环境（DNS 解析）：**

将域名以 CNAME 记录解析到 ALB 端点。

## 使用 E2B SDK

### 环境配置

```bash
export E2B_DOMAIN=your.domain.cn
export E2B_API_KEY=your-admin-api-key

pip install e2b-code-interpreter
```

### 基本用法

```python
from e2b_code_interpreter import Sandbox

# 创建沙箱（从预热池秒级分配）
sbx = Sandbox.create(template="openclaw", timeout=300)
print(f"Sandbox ID: {sbx.sandbox_id}")

# 执行代码
result = sbx.run_code("print('Hello, OpenClaw!')")
print(result)

# 销毁沙箱
sbx.kill()
```

### 休眠与唤醒（Pause & Resume）

> **注意**：休眠/唤醒功能需联系阿里云开启白名单

```python
from e2b_code_interpreter import Sandbox

# 创建沙箱并执行代码
sbx = Sandbox.create(template="openclaw", timeout=300)
sbx.run_code("a = 1")
sbx.run_code("print(f'Before pause: a = {a}')")

# 休眠沙箱（保留内存状态）
sandbox_id = sbx.sandbox_id
sbx.beta_pause()
print(f"Sandbox {sandbox_id} paused")

# ... 一段时间后 ...

# 唤醒沙箱（恢复内存状态）
sbx = Sandbox.connect(sandbox_id)
sbx.run_code("print(f'After resume: a = {a}')")  # 变量 a 仍然存在

# 销毁沙箱
sbx.kill()
```

**典型应用场景**：
- 长时间不活跃的会话暂停以节省资源
- 用户离线后保存工作状态，上线后恢复
- 跨会话保持执行上下文

## 架构说明

```
┌─────────────────────────────────────────────────────────────┐
│                         ACS 集群                            │
│  ┌──────────────────┐  ┌──────────────────────────────────┐ │
│  │ sandbox-manager  │  │        SandboxSet (预热池)       │ │
│  │  (E2B 兼容 API)  │  │  ┌─────────┐ ┌─────────┐        │ │
│  └────────┬─────────┘  │  │ Sandbox │ │ Sandbox │ ...    │ │
│           │            │  │  + NAS  │ │  + NAS  │        │ │
│  ┌────────▼─────────┐  │  │  + EIP  │ │  + EIP  │        │ │
│  │   ALB Ingress    │  │  └─────────┘ └─────────┘        │ │
│  └────────┬─────────┘  └──────────────────────────────────┘ │
│           │                                                 │
│           │            ┌──────────────────────────────────┐ │
│           │            │         OpenClaw Pod            │ │
│           │            │  (Web UI + AI 编程助手)         │ │
│           │            └──────────────────────────────────┘ │
└───────────┼─────────────────────────────────────────────────┘
            │
    ┌───────▼───────┐
    │  E2B Client   │
    │ (Python/JS)   │
    └───────────────┘
```

## 组件说明

| 组件 | 说明 |
|------|------|
| **SandboxSet** | 管理 Sandbox 的工作负载，维护预热池实现秒级启动 |
| **Sandbox** | 核心 CRD，管理沙箱实例生命周期，支持 Pause/Resume |
| **sandbox-manager** | 无状态后端组件，提供 E2B 兼容 API |
| **agent-runtime** | Sidecar 组件，提供代码执行、文件操作等功能 |
| **ALB Ingress** | 负载均衡入口，处理 HTTPS 请求 |
| **ImageCache** | 镜像缓存，预先缓存容器镜像加速 Pod 启动 |
| **NAS** | 文件存储，提供持久化数据存储 |

## 常见问题

### Q: 沙箱启动慢？

**A**: 
1. 确认已配置镜像缓存（需在 ACS 集群层面申请白名单，详见「配置镜像缓存加速」章节）
2. 增加 SandboxSet 预热副本数：`kubectl edit sandboxset openclaw`
3. 如果 Pod 报 403 错误，说明镜像缓存白名单未开通

### Q: 如何查看沙箱状态？

```bash
kubectl get sandbox -A      # 查看所有沙箱
kubectl get sandboxset -A   # 查看所有 SandboxSet
kubectl describe sandbox <name>  # 查看沙箱详情
```

### Q: 休眠/唤醒失败？

**A**: 确保：
1. 使用阿里云 ACS 集群
2. 已联系阿里云开启休眠/唤醒功能白名单
3. agent-runtime 组件正常运行

### Q: Pod EIP 分配失败？

**A**: 检查：
1. 确认已安装 `ack-extend-network-controller` 组件
2. 检查组件日志：`kubectl logs -n kube-system -l app=ack-extend-network-controller`
3. 确认账户有 EIP 配额：登录 VPC 控制台查看

## 最佳实践

### 资源规划

| 场景 | CPU | 内存 | 预热副本 |
|-----|-----|------|---------|
| 测试环境 | 2 核 | 4Gi | 1 个 |
| 生产环境 | 4 核 | 8Gi | 3-5 个 |
| 高并发 | 8 核 | 16Gi | 10+ 个 |

### 成本优化

- 合理配置预热副本数，避免资源浪费
- 使用休眠功能暂停不活跃的沙箱
- 配置 NAS 生命周期策略，自动清理过期数据

### 安全加固

- 定期更新 TLS 证书
- 使用强密码作为 Gateway Token
- 配置安全组白名单，限制访问来源

## 相关链接

- [OpenKruise Agents 文档](https://openkruise.io/zh/kruiseagents)
- [E2B SDK 文档](https://e2b.dev/docs)
- [阿里云 ACS 文档](https://help.aliyun.com/product/85222.html)
- [百炼大模型服务平台](https://bailian.console.aliyun.com/)

## 技术支持

- **钉钉交流群**：在计算巢服务页面可找到钉钉群二维码
- **工单系统**：[提交工单](https://workorder.console.aliyun.com/)

# OpenClaw Enterprise Edition Deployment Guide

> Building Enterprise AI Agent Application Based on ACS Agent Sandbox

## Overview

OpenClaw is an open source AI programming assistant that supports multi-platform operation. Based on the Alibaba Cloud Container Computing Service (ACS) and OpenKruise Agents framework, this service provides an enterprise-level one-click deployment solution.

### Core Features

-**Second-level sandbox startup**: implements sub-second sandbox delivery through the SandboxSet warm-up pool image cache
-**Session state retention**: supports sandbox hibernation and wake-up, and retains memory state
-**Persistence Storage**: Integrated NAS file storage, data persistence across sessions
-**E2B Protocol Compatible**: Supports native E2B SDK for seamless migration of existing applications

### Comparison of Deployment Methods

| Deployment method | Difficulty | Time | Applicable scenario |
| --------- | ------ | ------ | ------ | --------- |
| **Compute Nest Console** |⭐Simple | 10-15 minutes | Quick experience, test environment, production environment |
| **Manual Deployment** |⭐⭐⭐Complex | 30-60 minutes | Customized needs, learning research |

## Premise preparation

### 1. Prepare the domain name

The E2B protocol requires a domain name (E2B_DOMAIN) to specify the backend service.

-**Test Environment**: You can use the test domain name, such as agent-vpc.infra. (You need to configure hosts or PrivateZone.)
-**Production Environment**:
-Reference [Quick Start to Domain Name Registration](https://help.aliyun.com/document_detail/35789.html)
-Mainland China deployment requires [domain name filing](https://beian.aliyun.com/)

### 2. Obtain a TLS certificate

The E2B client requests the backend through HTTPS and needs to apply for a wildcard certificate.

**Test Environment-Let's Encrypt Free Certificate:**

bash
# Installation certbot
brew install certbot # macOS
# or snap install certbot # Linux

# Apply for a wildcard certificate
sudo certbot certonly \
--manual \
--preferred-challenges=dns \
--email your-email@example.com \
--server https://acme-v02.api.letsencrypt.org/directory \
--agree-tos \
-d "*.your.domain.cn"

# Export Certificate
sudo cp /etc/letsencrypt/live/your.domain/fullchain.pem ./fullchain.pem
sudo cp /etc/letsencrypt/live/your.domain/privkey.pem ./privkey.pem
'''

**Production Environment**: Recommended [Purchase Official Certificate](https://help.aliyun.com/document_detail/28542.html)

### 3. Obtain the API Key

Log on to the [Refining Console](https://bailian.console.aliyun.com/) to create an API Key for AI model calls.

### 4. Configure mirrored cache (recommended)

Mirror caching can significantly accelerate ACS Pod startup, reducing mirror pull time from **minutes to seconds**.

> **Note**: The mirror cache function is currently in the whitelist invitation phase and requires [Submit Work Order](https://smartservice.console.aliyun.com/service/create-ticket) to apply for activation.

**Create a mirrored cache:**

1. Log on to the [Container Computing Service Console](https://acs.console.aliyun.com/)
2. In the left navigation bar, choose "mirror cache" → "create mirror cache 」
3. Configuration:
-**Mirror cache name**:'openclaw-image-cache'
-**Mirror**'
4. Wait for the status to become "production completed 」

**Billing Note**: 20 image caches are free for each region, exceeding 0.18 yuan/GiB/month

**Supported Regions**: China North 2 (Beijing), China East 2 (Shanghai), China East 1 (Hangzhou), China North 6 (Wulanchabu), China South 1 (Shenzhen), Hong Kong, China, and Singapore

## Method 1: Compute Nest console deployment (recommended for beginners)

1. Access to the computing nest service.

Open [Compute Nest Service Deployment Link](https://computenest.console.aliyun.com/) and search for **ack-sandbox-manager**

2. **Fill in the deployment parameters**

| Parameter group | Parameter | Description |
| ------- | ------ | ------ | ------ |
| Basic Configuration | Region | Select the nearest region (cn-hangzhou, cn-beijing, etc.) |
| | Availability Zones | Select two different Availability Zones (High Availability) |
| | VPC configuration | Create or use an existing VPC |
| E2B configuration | E2B domain name | Precondition domain name |
| | TLS Certificate | Upload 'fullchain.pem' |
| | TLS certificate private key | Upload 'privkey.pem' |

3. **Confirm deployment**

Click "Confirm Order" to start the deployment, which takes about 10-15 minutes.

4. **Get access information**

After the deployment is successful, view it on the service instance details page:
-**E2B_API_KEY**: the key for accessing the E2B API
-**E2B_DOMAIN**:E2B Domain
-**ClusterId**:ACS cluster ID

## Post-deployment configuration

### Install EIP components (optional)

To assign an independent public IP address to a pod, install the ack-extend-network-controller component:

**Method A- Console Installation (Recommended):**

1. Log on to the [Container Service Console](https://cs.console.aliyun.com/)
2. Go to Cluster Details → "Component Management 」
3. Search for "ack-extend-network-controller" and click "Install 」
4. Configure the parameters (use the default values) and confirm the installation

**Mode B- Command Line Installation:**

bash
# Obtain the cluster credential
python ros_stack_manager.py kubeconfig --from-stack <stack-name> --region cn-beijing

# Install EIP components
kubectl apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
name: ack-extend-network-controller
namespace: kube-system
data:
config.yaml: |
enableControllers:
-eip
EOF
'''

**Verify Installation:**

bash
kubectl get pods -n kube-system | grep extend-network
'''

### Configure EIP for Pod

After the EIP component is installed, you can assign an independent public IP address to the sandbox through the pod annotation:

yaml
apiVersion: agents.kruise.io/v1alpha1
kind: SandboxSet
metadata:
name: openclaw
spec:
template:
metadata:
annotations:
# Enable Pod EIP
network.alibabacloud.com/pod-with-eip: "true"
# EIP Bandwidth (Mbps)
"5"
'''

**View Pod EIP:**

bash
kubectl get pod -o wide
kubectl describe pod <pod-name> | grep -i eip
'''

### Configure Domain Name Resolution

**Local test (Hosts mode):**

bash
# Obtain the public IP address of ALB
kubectl get ingress -n sandbox-system

# Configure hosts
echo "<ALB_IP> api.your.domain.cn" >> /etc/hosts
'''

**Production environment (DNS resolution):**

Resolve the domain name to the ALB endpoint as a CNAME record.

## Use the E2B SDK

### Environment Configuration

bash
export E2B_DOMAIN=your.domain.cn
export E2B_API_KEY=your-admin-api-key

pip install e2b-code-interpreter
'''

### Basic Usage

python
from e2b_code_interpreter import Sandbox

# Create a sandbox (second-level allocation from the warm-up pool)
sbx = Sandbox.create(template="openclaw", timeout=300)
print(f"Sandbox ID: {sbx.sandbox_id}")

# Execute code
result = sbx.run_code("print('Hello, OpenClaw! ')")
print(result)

# Destroy the sandbox
sbx.kill()
'''

### Pause & Resume

> **Note**: For the sleep/wake-up function, you need to contact Alibaba Cloud to open the whitelist.

python
from e2b_code_interpreter import Sandbox

# Create a sandbox and execute code
sbx = Sandbox.create(template="openclaw", timeout=300)
sbx.run_code("a = 1")
sbx.run_code("print(f'Before pause: a = {a}')")

# Sleep sandbox (keep memory state)
sandbox_id = sbx.sandbox_id
sbx.beta_pause()
print(f"Sandbox {sandbox_id} paused")

#... after a while...

# Wake up the sandbox (restore memory state)
sbx = Sandbox.connect(sandbox_id)
sbx.run_code("print(f'After resume: a = {a}')")# variable a still exists

# Destroy the sandbox
sbx.kill()
'''

**Typical application scenarios**:
-Long inactive sessions pause to save resources
-The user saves the working status after offline and resumes after online
-Pertaining execution context across sessions

## Architecture description

'''
┌─────────────────────────────────────────────────────────────┐
│ ACS Cluster │
│ ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
sandbox-manager SandboxSet (preheating pool)
(E2B compatible API) │ │ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
│ └────────┬─────────┘ │ │ Sandbox │ │ Sandbox │ ... │ │
│ │ │ │ NAS │ │ NAS │ │ │
│ ┌────────▼─────────┐ │ │ EIP │ │ EIP │ │ │
│ │ ALB Ingress │ │ └─────────┘ └─────────┘ │ │
│ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
│ │ │
│ ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
│ │ │ OpenClaw Pod │ │
│ │ │ │ (Web UI AI Programming Assistant) │ │ │
│ │-─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
The --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- --- ---
│
----------------
│ E2B Client │
│ (Python/JS) │
The ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
'''

## Component description

| Component | Description |
|------|------|
| **SandboxSet** | Manage Sandbox workloads, maintain the preheating pool and enable second-level startup |
| **Sandbox** | Core CRD, manages the lifecycle of sandbox instances, and supports Pause/Resume |
| **sandbox-manager** | A stateless backend component that provides E2B-compliant APIs |
| **agent-runtime** | Sidecar components that provide functions such as code execution and file manipulation |
| **ALB Ingress** | The load balancing portal handles HTTPS requests |
| **ImageCache** | Image cache, pre-cache container image to accelerate pod startup |
| **NAS** | File storage provides persistent data storage. |

## Frequently Asked Questions

### Q: Slow start of sandbox?

**A**:
1. Confirm that the mirror cache has been configured (whitelist required)
2. Increase the number of SandboxSet warm-up copies: 'kubectl edit sandboxset openclaw'

### Q: How do I check the sandbox status?

bash
kubectl get sandbox -A# to view all sandboxes
kubectl get sandboxset -A# View all SandboxSet
kubectl describe sandbox <name># to view sandbox details
'''

### Q: Sleep/wake failure?

**A**: Ensure:
1. Use the Alibaba Cloud ACS cluster
2. You have contacted Aliyun to open the white list of sleep/wake-up functions.
3. Normal operation of agent-runtime components

### Q: Pod EIP allocation failed?

**A**: Check:
1. Confirm that the ack-extend-network-controller component is installed
2. Check the component log: 'kubectl logs -n kube-system -l app = ack-extend-network-controller'
3. Confirm that the account has EIP quota: Log in to the VPC console to view

## Best Practices

### Resource Planning

| Scenario | CPU | Memory | Warm copy |
| ----- | ----- | ------ | --------- |
| Test environment | 2 cores | 4Gi | 1 |
| Production environment | 4 cores | 8Gi | 3-5 |
| High concurrency | 8 cores | 16Gi | 10 |

### Cost Optimization

-Reasonable configuration of the number of warm-up copies to avoid waste of resources
-Use the hibernation feature to pause inactive sandboxes
-Configure NAS lifecycle policies to automatically clean up expired data

### Security reinforcement

-Update TLS certificates regularly
-Use strong passwords as Gateway tokens
-Configure a security group whitelist to restrict access sources

## Related Links

-[OpenKruise Agents Documentation](https://openkruise.io/zh/kruiseagents)
-[E2B SDK Documentation](https://e2b.dev/docs)
-[Alibaba Cloud ACS Document](https://help.aliyun.com/product/85222.html)
-[Bailian Big Model Service Platform](https://bailian.console.aliyun.com/)

## Technical Support

-**DingTalk communication group**: you can find the DingTalk group QR code on the computing nest service page
-**Work Order System**:[Submit Work Order](https://workorder.console.aliyun.com/)

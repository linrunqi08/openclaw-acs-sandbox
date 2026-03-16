# ⚠️  声明：本脚本仅用于测试场景临时验证，禁止用于生产环境。
# WARNING: This script is for temporary testing/validation purposes only.
#          DO NOT use in production environments.

from dotenv import load_dotenv
import os
import time
import requests
from e2b_code_interpreter import Sandbox

def main():
    print("Hello from openclaw-demo!")
    load_dotenv(override=True)

    # 步骤1: 创建 sandbox
    print("\n[步骤1] 创建 sandbox...")
    start_time = time.monotonic()
    sandbox = Sandbox.create(
        'openclaw',
        timeout=1800,
        envs={
            "DASHSCOPE_API_KEY": os.environ.get("DASHSCOPE_API_KEY", ""),
            "GATEWAY_TOKEN": os.environ.get("GATEWAY_TOKEN", "clawdbot-mode-123456"),
        },
        metadata={
            "e2b.agents.kruise.io/never-timeout": "true"
        }
    )
    print(f"创建 sandbox 耗时: {time.monotonic() - start_time:.2f} 秒")
    print(f"Sandbox ID: {sandbox.sandbox_id}")
    sandbox.files.write("/tmp/test.txt", "Hello, World!")

    # 等待几秒让服务启动
    print("等待 3 秒让 gateway 启动...")
    time.sleep(3)

    # 步骤3: 等待服务就绪
    print("\n[步骤3] 等待服务就绪...")
    host = sandbox.get_host(18789)
    base_url = f"https://{host}"
    print(f"base_url: {base_url}")

    start_time = time.monotonic()
    ready = False
    while True:
        try:
            response = requests.get(
                f"{base_url}/?token=clawdbot-mode-123456",
                verify=False,
                timeout=5
            )
            print(f"响应状态码: {response.status_code}")
            if response.status_code == 200:
                print("服务已就绪!")
                print(f"响应内容: {response.text[:200]}...")  # 打印前200字符
                ready = True
                break
        except requests.ConnectionError as e:
            print(f"连接错误: {e}")
        except requests.Timeout:
            print("请求超时，继续等待...")
        time.sleep(0.5)
        print("waiting...")

    print(f"等待就绪总耗时: {time.monotonic() - start_time:.2f} 秒")

    # 步骤4: 暂停前等待服务完全稳定
    print("\n[步骤4] 服务已就绪，等待 10 秒让 sandbox 完全稳定后再 pause...")
    time.sleep(10)

    # 步骤5: 暂停 sandbox
    print("\n[步骤5] 执行 sandbox beta_pause...")
    start_time = time.monotonic()
    pause_success = sandbox.beta_pause()
    print(f"pause 耗时: {time.monotonic() - start_time:.2f} 秒")
    print(f"pause success: {pause_success}")  # pause 的结果. None 是预期值，如果有其他错误信息回返回

    # 步骤6: 重新连接 sandbox
    # 等待 10秒让 sandbox 完全暂停
    print("等待 60秒让 sandbox 完全暂停...")
    time.sleep(60)
    print("\n[步骤6] 重新连接 sandbox...")
    start_time = time.monotonic()
    sameSandbox = sandbox.connect(timeout=180)
    connect_time = time.monotonic() - start_time
    print(f"connect 耗时: {connect_time:.2f} 秒")
    print(f"重新连接成功，Sandbox ID: {sameSandbox.sandbox_id}")

    print("\n所有步骤执行完毕!")

if __name__ == "__main__":
    main()

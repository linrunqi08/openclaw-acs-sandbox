# ⚠️  声明：本脚本仅用于测试场景临时验证，禁止用于生产环境。
# WARNING: This script is for temporary testing/validation purposes only.
#          DO NOT use in production environments.

import time
from dotenv import load_dotenv
from e2b_code_interpreter import Sandbox


def main():
    print("Hello from acs-sandbox-test!")
    load_dotenv(override=True)

    # 步骤1: 创建 sandbox
    print("\n[步骤1] 创建 sandbox...")
    start_time = time.monotonic()
    sandbox = Sandbox.create('sandbox', timeout=1800)
    print(f"创建 sandbox 耗时: {time.monotonic() - start_time:.2f} 秒")
    print(f"Sandbox ID: {sandbox.sandbox_id}")
    print(f"envd host: {sandbox.get_host(49983)}")

    # 步骤2: 暂停 sandbox
    print("\n[步骤2] 执行 sandbox beta_pause...")
    start_time = time.monotonic()
    pause_success = sandbox.beta_pause()
    print(f"pause 耗时: {time.monotonic() - start_time:.2f} 秒")
    print(f"pause success: {pause_success}")

    print("等待 60 秒让 sandbox 完全暂停...")
    time.sleep(60)

    # 步骤3: resume 并验证文件持久化
    print("\n[步骤3] 重新连接 sandbox（resume）...")
    start_time = time.monotonic()
    same_sandbox = sandbox.connect(timeout=180)
    print(f"connect 耗时: {time.monotonic() - start_time:.2f} 秒")
    print(f"重新连接成功，Sandbox ID: {same_sandbox.sandbox_id}")


    print("\n所有步骤执行完毕!")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
容器启动入口脚本
职责：
1. 从环境变量读取 TLS 证书内容，写入证书文件
2. 生成 .env 配置文件
3. 启动主进程并保活（转发信号，子进程退出时容器随之退出）
"""

import os
import signal
import subprocess
import sys


# 证书内容（PEM 格式字符串）从环境变量读取，写入此路径
CERT_CONTENT = os.environ.get("SSL_CERT_FILE", "")
CERT_OUTPUT_PATH = "./ca-fullchain.pem"

# 主进程启动命令（通过环境变量或命令行参数传入）
MAIN_COMMAND = os.environ.get("MAIN_COMMAND", "")

# .env 文件输出路径
ENV_FILE_OUTPUT_PATH = os.environ.get("ENV_FILE_OUTPUT_PATH", "./.env")


def write_cert_files():
    """从环境变量 SSL_CERT_FILE 读取公钥证书内容，写入 ca-fullchain.pem"""
    if not CERT_CONTENT:
        print("[entrypoint] SSL_CERT_FILE 环境变量未设置，跳过证书写入")
        return

    cert_dir = os.path.dirname(CERT_OUTPUT_PATH)
    if cert_dir:
        os.makedirs(cert_dir, exist_ok=True)

    with open(CERT_OUTPUT_PATH, "w") as cert_file:
        cert_file.write(CERT_CONTENT)
    print(f"[entrypoint] 证书已写入: {CERT_OUTPUT_PATH}")


def write_env_file():
    """生成 .env 配置文件，E2B_API_KEY / E2B_DOMAIN 从环境变量读取"""
    e2b_api_key = os.environ.get("E2B_API_KEY", "")
    e2b_domain = os.environ.get("E2B_DOMAIN", "agent-vpc.infra")

    env_content = f"""# E2B Environment Variables
# 按照自己的实际情况修改变量
# 默认域名
E2B_DOMAIN={e2b_domain}
# E2B API Key
E2B_API_KEY={e2b_api_key}
# SSL Certificate File
SSL_CERT_FILE=./ca-fullchain.pem
# 访问网关的token
GATEWAY_API_KEY=
# 值设置为自己的Dashscope API Key
DASHSCOPE_API_KEY=
"""

    env_dir = os.path.dirname(ENV_FILE_OUTPUT_PATH)
    if env_dir:
        os.makedirs(env_dir, exist_ok=True)

    with open(ENV_FILE_OUTPUT_PATH, "w") as env_file:
        env_file.write(env_content)
    print(f"[entrypoint] .env 文件已生成: {ENV_FILE_OUTPUT_PATH}")



def start_main_process(command):
    """启动主进程，转发信号，子进程退出时脚本随之退出"""
    if not command:
        print("[entrypoint] MAIN_COMMAND 未设置，容器将保持运行（保活模式）")
        keep_alive()
        return

    print(f"[entrypoint] 启动主进程: {command}")
    process = subprocess.Popen(command, shell=True)

    # 将 SIGTERM / SIGINT 转发给子进程，确保优雅退出
    def forward_signal(signum, _frame):
        print(f"[entrypoint] 收到信号 {signum}，转发给子进程")
        process.send_signal(signum)

    signal.signal(signal.SIGTERM, forward_signal)
    signal.signal(signal.SIGINT, forward_signal)

    exit_code = process.wait()
    print(f"[entrypoint] 主进程已退出，退出码: {exit_code}")
    sys.exit(exit_code)


def keep_alive():
    """无主进程时保持容器运行，收到终止信号后正常退出"""
    def handle_exit(signum, _frame):
        print(f"[entrypoint] 收到信号 {signum}，容器退出")
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_exit)
    signal.signal(signal.SIGINT, handle_exit)

    print("[entrypoint] 容器保活中，等待终止信号...")
    signal.pause()


if __name__ == "__main__":
    print("[entrypoint] 初始化开始")

    write_cert_files()
    write_env_file()

    # 支持通过命令行参数传入主命令，优先级高于环境变量
    command = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else MAIN_COMMAND
    start_main_process(command)

#!/usr/bin/env python3
"""
调试测试脚本 - 用于诊断网络和端口问题
"""

import os
import sys
import socket
import subprocess
import time
import logging
import json
import urllib.request
import urllib.error
from datetime import datetime

logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

def print_section(title):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)

def test_network():
    print_section("1. 网络连接测试")

    test_urls = [
        ("IPv4", "https://ipv4.ip.sb"),
        ("IPify", "https://api.ipify.org"),
        ("Cloudflare", "https://speed.cloudflare.com"),
    ]

    for name, url in test_urls:
        try:
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'curl/7.88.1')
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read().decode('utf-8')
                logger.info(f"[{name}] 成功 - {data.strip()}")
        except Exception as e:
            logger.error(f"[{name}] 失败 - {e}")

def test_env_vars():
    print_section("2. 环境变量分析")

    logger.info(f"HOSTNAME = {os.environ.get('HOSTNAME', 'N/A')}")
    logger.info(f"PWD = {os.environ.get('PWD', 'N/A')}")
    logger.info(f"LANG = {os.environ.get('LANG', 'N/A')}")

    port_related = {}
    url_related = {}
    all_vars = dict(os.environ)

    for key, val in sorted(all_vars.items()):
        key_upper = key.upper()
        if 'PORT' in key_upper or 'PORT' in key:
            port_related[key] = val
        if any(x in key_upper for x in ['URL', 'DOMAIN', 'HOST', 'PUBLIC', 'APP', 'SERVICE']):
            url_related[key] = val

    if port_related:
        logger.info("\n[端口相关环境变量]")
        for k, v in port_related.items():
            logger.info(f"  {k} = {v}")
    else:
        logger.warning("  无端口相关环境变量")

    if url_related:
        logger.info("\n[URL相关环境变量]")
        for k, v in url_related.items():
            logger.info(f"  {k} = {v}")
    else:
        logger.warning("  无URL相关环境变量")

    logger.info("\n[所有环境变量]")
    for k, v in sorted(all_vars.items()):
        logger.info(f"  {k} = {v}")

def test_http_server(port):
    print_section(f"3. HTTP服务器测试 (端口 {port})")

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server_socket.bind(('0.0.0.0', port))
        server_socket.listen(5)
        logger.info(f"[服务器] 绑定成功 0.0.0.0:{port}")
    except Exception as e:
        logger.error(f"[服务器] 绑定失败 - {e}")
        return None

    def handle_request(client_socket, client_address):
        try:
            request = client_socket.recv(8192).decode('utf-8', errors='ignore')
            logger.info(f"[请求] {client_address}")
            logger.info(f"[请求内容]\n{request[:500]}")

            response = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nOK"
            client_socket.sendall(response.encode('utf-8'))
        except Exception as e:
            logger.error(f"[错误] {e}")
        finally:
            client_socket.close()

    import threading
    running = [True]

    def accept_loop():
        while running[0]:
            try:
                server_socket.settimeout(1.0)
                try:
                    client_socket, client_address = server_socket.accept()
                    handle_request(client_socket, client_address)
                except socket.timeout:
                    continue
            except Exception as e:
                if running[0]:
                    logger.error(f"[接受错误] {e}")
                break

    accept_thread = threading.Thread(target=accept_loop, daemon=True)
    accept_thread.start()

    logger.info("[服务器] 等待请求 60 秒...")
    logger.info("[提示] 从外部访问 http://159.195.63.104:{}/sub 查看日志".format(port))
    logger.info("[提示] 从外部访问 https://appkagi.mangoi.in/{}/sub 查看日志".format(port))

    time.sleep(60)
    running[0] = False
    server_socket.close()
    print("\n[服务器] 测试结束")

def test_external_access(port):
    print_section("4. 外部访问测试")

    my_ip = ""
    try:
        req = urllib.request.Request("https://api.ipify.org")
        req.add_header('User-Agent', 'curl/7.88.1')
        with urllib.request.urlopen(req, timeout=5) as resp:
            my_ip = resp.read().decode('utf-8')
            logger.info(f"[本机IP] {my_ip}")
    except Exception as e:
        logger.error(f"[本机IP] 获取失败 - {e}")

    test_paths = [
        f"http://{my_ip}:{port}/",
        f"http://{my_ip}:{port}/sub",
        f"http://{my_ip}:{port}/test",
    ]

    for url in test_paths:
        try:
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'curl/7.88.1')
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read().decode('utf-8', errors='ignore')
                logger.info(f"[访问] {url} -> 成功 ({resp.status})")
        except urllib.error.URLError as e:
            logger.error(f"[访问] {url} -> 失败 (URL Error: {e.reason})")
        except Exception as e:
            logger.error(f"[访问] {url} -> 失败 ({e})")

def test_socket_listen(port):
    print_section("5. 端口监听测试")

    test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    test_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        test_socket.bind(('0.0.0.0', port))
        test_socket.listen(5)
        logger.info(f"[监听] 端口 {port} 可用")
        test_socket.close()
    except Exception as e:
        logger.error(f"[监听] 端口 {port} 失败 - {e}")

def main():
    print_section("Mango 平台调试测试")
    logger.info(f"时间: {datetime.now()}")
    logger.info(f"Python: {sys.version}")
    logger.info(f"平台: {os.name}")

    test_env_vars()
    test_network()
    test_socket_listen(5000)
    test_socket_listen(5001)
    test_socket_listen(8080)

    port = 5000
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            if arg.isdigit():
                port = int(arg)
                break

    test_http_server(port)

if __name__ == "__main__":
    main()

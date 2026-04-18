#!/usr/bin/env python3
"""
最小 Web 调试服务。
用途：确认平台域名/443 是否真正转发到了容器内部端口。
"""

import json
import logging
import os
import socket
import sys
import threading
import time
from datetime import datetime
from urllib.parse import urlparse, parse_qs


logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


def get_public_ip() -> str:
    try:
        import urllib.request

        req = urllib.request.Request("https://api.ipify.org")
        req.add_header("User-Agent", "curl/7.88.1")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.read().decode("utf-8", errors="ignore").strip()
    except Exception as exc:
        return f"unavailable: {exc}"


def detect_ports() -> list[int]:
    port_env_vars = [
        "PORTS_STRING", "PORT", "PORTS",
        "EASYPANEL_PORT", "EASYPANEL_PORTS",
        "MANGO_PORT", "APP_PORT", "INTERNAL_PORT",
    ]

    for var in port_env_vars:
        value = os.environ.get(var, "").strip()
        if value:
            ports = [int(p) for p in value.replace(',', ' ').split() if p.isdigit()]
            if ports:
                logger.info(f"[端口] 环境变量 {var} = {value}")
                return ports

    cli_ports = [int(arg) for arg in sys.argv[1:] if arg.isdigit()]
    if cli_ports:
        logger.info(f"[端口] 命令行参数 = {cli_ports}")
        return cli_ports

    fallback_ports = [443, 80, 8080, 3000, 5000]
    logger.warning(f"[端口] 未检测到端口，按顺序尝试 {fallback_ports}")
    return fallback_ports


def can_bind(port: int) -> tuple[bool, str]:
    test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    test_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        test_socket.bind(("0.0.0.0", port))
        return True, "ok"
    except Exception as exc:
        return False, str(exc)
    finally:
        try:
            test_socket.close()
        except Exception:
            pass


def parse_headers(raw_request: str) -> tuple[str, dict[str, str]]:
    lines = raw_request.split("\r\n")
    request_line = lines[0] if lines else ""
    headers: dict[str, str] = {}

    for line in lines[1:]:
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip()] = value.strip()

    return request_line, headers


def build_response(request_line: str, headers: dict[str, str], bind_port: int, client_address) -> str:
    path = "/"
    if request_line:
        parts = request_line.split()
        if len(parts) >= 2:
            path = parts[1]

    parsed = urlparse(path)
    query = parse_qs(parsed.query)
    now = datetime.now().isoformat()

    env_subset = {}
    for key in sorted(os.environ):
        upper = key.upper()
        if any(token in upper for token in ["PORT", "HOST", "DOMAIN", "URL", "SERVICE", "MANGO", "EASYPANEL"]):
            env_subset[key] = os.environ[key]

    body_obj = {
        "ok": True,
        "message": "domain reached container",
        "time": now,
        "bind_port": bind_port,
        "client": {"ip": client_address[0], "port": client_address[1]},
        "request_line": request_line,
        "path": parsed.path,
        "query": query,
        "headers": headers,
        "interesting_env": env_subset,
        "hostname": socket.gethostname(),
        "public_ip": get_public_ip(),
        "checks": {
            "host": headers.get("Host", ""),
            "x_forwarded_proto": headers.get("X-Forwarded-Proto", ""),
            "x_forwarded_for": headers.get("X-Forwarded-For", ""),
            "x_forwarded_host": headers.get("X-Forwarded-Host", ""),
            "cf_ray": headers.get("CF-Ray", ""),
            "cf_connecting_ip": headers.get("CF-Connecting-IP", ""),
        },
        "tips": [
            "访问 / 和 /sub 都应返回 200 JSON。",
            "如果域名打不开，说明平台流量没有进到这个端口。",
            "如果能打开，把返回的 JSON 和容器日志一起发出来。",
        ],
    }

    body = json.dumps(body_obj, ensure_ascii=False, indent=2)
    body_bytes = body.encode("utf-8")

    return (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: application/json; charset=utf-8\r\n"
        "Cache-Control: no-store\r\n"
        f"Content-Length: {len(body_bytes)}\r\n"
        "Connection: close\r\n\r\n"
        f"{body}"
    )


def serve(port: int) -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", port))
    server.listen(128)

    logger.info(f"[HTTP] 调试服务已启动 0.0.0.0:{port}")
    logger.info(f"[提示] 用系统域名访问: https://你的域名/")
    logger.info(f"[提示] 用系统域名访问: https://你的域名/sub")
    logger.info(f"[提示] 容器内自测: http://127.0.0.1:{port}/sub?ping=1")

    while True:
        client_socket, client_address = server.accept()
        thread = threading.Thread(
            target=handle_client,
            args=(client_socket, client_address, port),
            daemon=True,
        )
        thread.start()


def handle_client(client_socket, client_address, bind_port: int) -> None:
    try:
        raw = client_socket.recv(16384).decode("utf-8", errors="ignore")
        request_line, headers = parse_headers(raw)
        logger.info(f"[请求] {client_address[0]}:{client_address[1]} -> {request_line}")
        for key in ["Host", "X-Forwarded-Proto", "X-Forwarded-For", "X-Forwarded-Host", "CF-Ray", "CF-Connecting-IP"]:
            if key in headers:
                logger.info(f"[请求头] {key}: {headers[key]}")

        response = build_response(request_line, headers, bind_port, client_address)
        client_socket.sendall(response.encode("utf-8"))
    except Exception as exc:
        logger.error(f"[错误] 处理请求失败: {exc}")
    finally:
        try:
            client_socket.close()
        except Exception:
            pass


def main() -> None:
    logger.info(f"[启动] Python {sys.version.split()[0]}")
    logger.info(f"[启动] 时间 {datetime.now().isoformat()}")
    ports = detect_ports()
    logger.info(f"[启动] 可用端口 {ports}")

    primary_port = None
    for port in ports:
        ok, message = can_bind(port)
        if ok:
            primary_port = port
            logger.info(f"[启动] 选择端口 {primary_port}")
            break
        logger.warning(f"[端口] {port} 不可用: {message}")

    if primary_port is None:
        logger.error("[启动] 没有可绑定的端口")
        sys.exit(1)

    unused_ports = [port for port in ports if port != primary_port]
    if unused_ports:
        logger.info(f"[启动] 未使用的候选端口 {unused_ports}")

    serve(primary_port)


if __name__ == "__main__":
    main()

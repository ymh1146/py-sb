#!/usr/bin/env python3
"""
Sing-box Node 启动脚本
VLESS + Trojan + Argo
"""

import os
import sys
import json
import uuid
import socket
import subprocess
import time
import shutil
import re
import signal
import logging
import threading
from pathlib import Path
from typing import Optional, List, Tuple
from datetime import datetime

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ========== 配置区域 ==========
class Config:
    # 固定隧道填写token，不填默认为临时隧道
    ARGO_TOKEN: str = ""

    # CF 优选域名列表
    CF_DOMAINS: List[str] = [
        "cf.090227.xyz",
        "cf.877774.xyz",
        "cf.130519.xyz",
        "cf.008500.xyz",
        "store.ubi.com",
        "saas.sin.fan",
    ]

    # 下载重试次数
    DOWNLOAD_RETRY: int = 3

    # 下载超时时间(秒)
    DOWNLOAD_TIMEOUT: int = 120

# ========== 工具函数 ==========
class Utils:
    @staticmethod
    def curl(url: str, timeout: int = 5, method: str = "GET") -> Tuple[bool, str]:
        """使用Python发送HTTP请求"""
        try:
            import urllib.request
            req = urllib.request.Request(url, method=method)
            req.add_header('User-Agent', 'curl/7.88.1')
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return True, resp.read().decode('utf-8', errors='ignore')
        except Exception:
            return False, ""

    @staticmethod
    def get_public_ip() -> Optional[str]:
        """获取公网IP"""
        for svc in ["https://ipv4.ip.sb", "https://api.ipify.org"]:
            ok, data = Utils.curl(svc, timeout=5)
            if ok and data.strip():
                return data.strip()
        return None

    @staticmethod
    def select_random_cf_domain(domains: List[str]) -> str:
        """随机选择CF优选域名"""
        import random
        return random.choice(domains)

    @staticmethod
    def get_arch() -> Tuple[str, str]:
        """获取系统架构"""
        import platform
        arch = platform.machine()
        if arch == "aarch64":
            return "arm64", "arm64"
        return "amd64", "amd64"

    @staticmethod
    def download_file(url: str, output: Path, timeout: int = 60, retry: int = 3) -> bool:
        """下载文件，支持重试"""
        import urllib.request
        for attempt in range(retry):
            try:
                req = urllib.request.Request(url)
                req.add_header('User-Agent', 'curl/7.88.1')
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    output.write_bytes(resp.read())
                try:
                    os.chmod(output, 0o755)
                except Exception:
                    pass
                logger.info(f"[下载] {output.name} 完成")
                return True
            except Exception as e:
                logger.warning(f"[下载] {output.name} 失败 (尝试 {attempt + 1}/{retry}): {e}")
                if attempt < retry - 1:
                    time.sleep(2)
                continue
        logger.error(f"[下载] {output.name} 多次失败")
        return False

    @staticmethod
    def generate_uuid() -> str:
        """生成UUID"""
        return str(uuid.uuid4())

    @staticmethod
    def generate_self_signed_cert(key_path: Path, cert_path: Path) -> bool:
        """生成自签名证书"""
        try:
            from cryptography import x509
            from cryptography.x509.oid import NameOID
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.backends import default_backend
            import datetime

            key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
                backend=default_backend()
            )

            subject = issuer = x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, 'www.bing.com'),
            ])
            cert = x509.CertificateBuilder().subject_name(subject).issuer_name(
                issuer).public_key(key.public_key()).serial_number(
                x509.random_serial_number()).not_valid_before(
                datetime.datetime.utcnow()).not_valid_after(
                datetime.datetime.utcnow() + datetime.timedelta(days=3650)
            ).sign(key, hashes.SHA256(), default_backend())

            key_path.write_bytes(key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption()
            ))
            cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            return True
        except ImportError:
            return Utils.generate_cert_fallback(key_path, cert_path)
        except Exception as e:
            logger.error(f"[证书] 生成失败: {e}")
            return False

    @staticmethod
    def generate_cert_fallback(key_path: Path, cert_path: Path) -> bool:
        """备用硬编码证书"""
        default_key = """-----BEGIN EC PRIVATE KEY-----
MHcCAQEEIM4792SEtPqIt1ywqTd/0bYidBqpYV/+siNnfBYsdUYsoAoGCCqGSM49
AwEHoUQDQgAE1kHafPj07rJG+HboH2ekAI4r+e6TL38GWASAnngZreoQDF16ARa/
TsyLyFoPkhTxSbehH/OBEjHtSZGaDhMqQ==
-----END EC PRIVATE KEY-----
"""
        default_cert = """-----BEGIN CERTIFICATE-----
MIIBejCCASGgAwIBAgIUFWeQL3556PNJLp/veCFxGNj9crkwCgYIKoZIzj0EAwIw
EzERMA8GA1UEAwwIYmluZy5jb20wHhcNMjUwMTAxMDEwMTAwWhcNMzUwMTAxMDEw
MTAwWjATMREwDwYDVQQDDAhiaW5nLmNvbTBZMBMGByqGSM49AgEGCCqGSM49AwEH
A0IABNZB2nz49O6yRvh26B9npACOK/nuky9/BlgEgJ54Ga3qEAxdegEWv07Mi8ha
D5IU8Um3oR/zgRIx7UmRmg4TKkOjUzBRMB0GA1UdDgQWBBTV1cFID7UISE7PLTBR
BfGbgrkMNzAfBgNVHSMEGDAWgBTV1cFID7UISE7PLTBRBfGbgrkMNzAPBgNVHRMB
Af8EBTADAQH/MAoGCCqGSM49BAMCA0cAMEQCIARDAJvg0vd/ytrQVvEcSm6XTlB+
eQ6OFb9LbLYL9Zi+AiB+foMbi4y/0YUQlTtz7as9S8/lciBF5VCUoVIKS+vX2g==
-----END CERTIFICATE-----
"""
        try:
            key_path.write_text(default_key, encoding='utf-8')
            cert_path.write_text(default_cert, encoding='utf-8')
            return True
        except Exception:
            return False

# ========== HTTP订阅服务器 ==========
class SubServer:
    def __init__(self, port: int, bind: str = "0.0.0.0"):
        self.port = port
        self.bind = bind
        self.sub_content = ""
        self.html_content = ""
        self.server = None
        self._running = False

    def update_content(self, sub_content: str, html_content: str) -> None:
        """更新订阅内容"""
        self.sub_content = sub_content
        self.html_content = html_content

    def handle_request(self, client_socket, client_address):
        """处理HTTP请求"""
        try:
            request = client_socket.recv(4096).decode('utf-8', errors='ignore')
            if not request:
                client_socket.close()
                return

            lines = request.split('\r\n')
            if not lines:
                client_socket.close()
                return

            request_line = lines[0]

            if '/sub' in request_line or '/sub.txt' in request_line:
                body = self.sub_content
                response = f"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nConnection: close\r\nContent-Length: {len(body)}\r\n\r\n{body}"
            elif 'GET / ' in request_line or 'GET / HTTP' in request_line:
                body = self.html_content
                response = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: {len(body.encode('utf-8'))}\r\n\r\n{body}"
            else:
                body = self.html_content
                response = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: {len(body.encode('utf-8'))}\r\n\r\n{body}"

            client_socket.sendall(response.encode('utf-8'))
        except Exception:
            pass
        finally:
            try:
                client_socket.close()
            except Exception:
                pass

    def start(self) -> None:
        """启动服务器"""
        self._running = True
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind((self.bind, self.port))
        self.server.listen(5)
        logger.info(f"[HTTP] 订阅服务已启动 {self.bind}:{self.port}")

        while self._running:
            try:
                self.server.settimeout(1.0)
                try:
                    client_socket, client_address = self.server.accept()
                except socket.timeout:
                    continue
                t = threading.Thread(target=self.handle_request, args=(client_socket, client_address))
                t.daemon = True
                t.start()
            except Exception:
                if self._running:
                    break

    def stop(self) -> None:
        """停止服务器"""
        self._running = False
        if self.server:
            try:
                self.server.close()
            except Exception:
                pass

    def run_in_thread(self) -> threading.Thread:
        """在线程中运行服务器"""
        t = threading.Thread(target=self.start, daemon=True)
        t.start()
        return t

# ========== Argo隧道管理 ==========
class ArgoTunnel:
    def __init__(self, argo_binary: Path, local_port: int, argo_token: str = ""):
        self.argo_binary = argo_binary
        self.local_port = local_port
        self.argo_token = argo_token
        self.process: Optional[subprocess.Popen] = None
        self.domain: str = ""
        self._log_file: Optional[Path] = None

    def start(self, log_file: Path) -> None:
        """启动Argo隧道"""
        self._log_file = log_file
        cmd = [str(self.argo_binary), "tunnel"]

        if self.argo_token:
            cmd.extend(["--token", self.argo_token])
        else:
            cmd.extend([
                "--edge-ip-version", "auto",
                "--protocol", "http2",
                "--no-autoupdate",
                f"--url=http://127.0.0.1:{self.local_port}"
            ])

        self.log_fd = open(log_file, 'w', encoding='utf-8')
        self.process = subprocess.Popen(
            cmd,
            stdout=self.log_fd,
            stderr=subprocess.STDOUT,
            text=True
        )
        logger.info(f"[Argo] 隧道进程已启动 PID: {self.process.pid}")

    def wait_for_domain(self, timeout: int = 30) -> Optional[str]:
        """等待获取域名"""
        if self.argo_token:
            return None

        pattern = re.compile(r'https://([a-zA-Z0-9-]+\.trycloudflare\.com)')
        for i in range(timeout):
            time.sleep(1)
            if self._log_file and self._log_file.exists():
                try:
                    content = self._log_file.read_text(encoding='utf-8', errors='ignore')
                    match = pattern.search(content)
                    if match:
                        self.domain = match.group(1)
                        logger.info(f"[Argo] 域名: {self.domain}")
                        return self.domain
                except Exception:
                    pass
        logger.warning("[Argo] 获取域名超时")
        return None

    def stop(self) -> None:
        """停止Argo隧道"""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
        if hasattr(self, 'log_fd') and self.log_fd:
            try:
                self.log_fd.close()
            except Exception:
                pass

# ========== Sing-box管理 ==========
class SingBox:
    def __init__(self, sb_binary: Path, config_path: Path):
        self.sb_binary = sb_binary
        self.config_path = config_path
        self.process: Optional[subprocess.Popen] = None

    def start(self) -> bool:
        """启动sing-box"""
        cmd = [str(self.sb_binary), "run", "-c", str(self.config_path)]
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        time.sleep(2)

        if self.process.poll() is not None:
            logger.error("[SING-BOX] 启动失败")
            return False
        logger.info(f"[SING-BOX] 已启动 PID: {self.process.pid}")
        return True

    def stop(self) -> None:
        """停止sing-box"""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass

    def is_running(self) -> bool:
        """检查是否在运行"""
        return self.process is not None and self.process.poll() is None

# ========== 配置生成器 ==========
class ConfigGenerator:
    def __init__(self, file_path: Path, uuid_str: str, 
                 private_key: Optional[str] = None,
                 trojan_port: Optional[int] = None,
                 argo_port: int = 8081):
        self.file_path = file_path
        self.uuid = uuid_str
        self.private_key = private_key
        self.trojan_port = trojan_port
        self.argo_port = argo_port

    def generate(self) -> dict:
        """生成sing-box配置"""
        inbounds = []

        # Reality (TCP)
        if self.private_key:
            inbounds.append({
                "type": "vless",
                "tag": "vless-reality-in",
                "listen": "::",
                "listen_port": 443,
                "users": [{"uuid": self.uuid, "flow": "xtls-rprx-vision"}],
                "tls": {
                    "enabled": True,
                    "server_name": "www.nazhumi.com",
                    "reality": {
                        "enabled": True,
                        "handshake": {"server": "www.nazhumi.com", "server_port": 443},
                        "private_key": self.private_key,
                        "short_id": [""]
                    }
                }
            })

        # Trojan
        if self.trojan_port:
            inbounds.append({
                "type": "trojan",
                "tag": "trojan-in",
                "listen": "::",
                "listen_port": self.trojan_port,
                "users": [{"password": self.uuid}],
                "tls": {
                    "enabled": True,
                    "certificate_path": str(self.file_path / "cert.pem"),
                    "key_path": str(self.file_path / "private.key")
                }
            })

        # VLESS for Argo
        inbounds.append({
            "type": "vless",
            "tag": "vless-argo-in",
            "listen": "127.0.0.1",
            "listen_port": self.argo_port,
            "users": [{"uuid": self.uuid}],
            "transport": {
                "type": "ws",
                "path": f"/{self.uuid}-vless"
            }
        })

        return {
            "log": {"level": "warn"},
            "inbounds": inbounds,
            "outbounds": [{"type": "direct", "tag": "direct"}]
        }

    def save(self, path: Path) -> bool:
        """保存配置到文件"""
        try:
            config = self.generate()
            content = json.dumps(config, indent=4, ensure_ascii=False)
            path.write_text(content, encoding='utf-8')
            return True
        except Exception as e:
            logger.error(f"[CONFIG] 保存配置失败: {e}")
            return False

# ========== 订阅生成器 ==========
class SubGenerator:
    def __init__(self, uuid_str: str, public_ip: str,
                 private_key: Optional[str] = None,
                 trojan_port: Optional[int] = None,
                 argo_domain: str = "", cf_domain: str = "",
                 isp: str = "Node"):
        self.uuid = uuid_str
        self.public_ip = public_ip
        self.private_key = private_key
        self.trojan_port = trojan_port
        self.argo_domain = argo_domain
        self.cf_domain = cf_domain
        self.isp = isp

    def generate_sub(self) -> str:
        """生成订阅内容"""
        lines = []

        # VLESS Reality
        if self.private_key:
            line = f"vless://{self.uuid}@{self.public_ip}:443?encryption=none&flow=xtls-rprx-vision&security=reality&sni=www.nazhumi.com&fp=chrome&pbk={self.private_key}&type=tcp#VLESS-{self.isp}"
            lines.append(line)

        # Trojan
        if self.trojan_port:
            line = f"trojan://{self.uuid}@{self.public_ip}:{self.trojan_port}?security=tls&sni=www.bing.com&allowInsecure=1#Trojan-{self.isp}"
            lines.append(line)

        # Argo VLESS
        if self.argo_domain:
            line = f"vless://{self.uuid}@{self.cf_domain}:443?encryption=none&security=tls&sni={self.argo_domain}&type=ws&host={self.argo_domain}&path=%2F{self.uuid}-vless#Argo-{self.isp}"
            lines.append(line)

        return "\n".join(lines)

    def generate_html(self, sub_url: str) -> str:
        """生成HTML页面"""
        vless_link = ""
        trojan_link = ""
        argo_link = ""

        if self.private_key:
            vless_link = f"vless://{self.uuid}@{self.public_ip}:443?encryption=none&flow=xtls-rprx-vision&security=reality&sni=www.nazhumi.com&fp=chrome&pbk={self.private_key}&type=tcp#VLESS-{self.isp}"
        if self.trojan_port:
            trojan_link = f"trojan://{self.uuid}@{self.public_ip}:{self.trojan_port}?security=tls&sni=www.bing.com&allowInsecure=1#Trojan-{self.isp}"
        if self.argo_domain:
            argo_link = f"vless://{self.uuid}@{self.cf_domain}:443?encryption=none&security=tls&sni={self.argo_domain}&type=ws&host={self.argo_domain}&path=%2F{self.uuid}-vless#Argo-{self.isp}"

        vless_html = f'<div class="link"><h3>VLESS</h3><code>{vless_link}</code><button onclick="copyText(\'{vless_link}\')">复制</button></div>' if vless_link else ""
        trojan_html = f'<div class="link"><h3>Trojan</h3><code>{trojan_link}</code><button onclick="copyText(\'{trojan_link}\')">复制</button></div>' if trojan_link else ""
        argo_html = f'<div class="link"><h3>Argo (WS)</h3><code>{argo_link}</code><button onclick="copyText(\'{argo_link}\')">复制</button></div>' if argo_link else ""

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sing-box Node</title>
<style>
body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #1a1a2e; color: #eee; }}
h1 {{ color: #e94560; text-align: center; }}
.link {{ background: #16213e; padding: 15px; margin: 15px 0; border-radius: 8px; }}
.link h3 {{ margin-top: 0; color: #0f3460; }}
code {{ display: block; word-break: break-all; background: #0a0a1a; padding: 10px; border-radius: 4px; font-size: 12px; margin: 10px 0; }}
button {{ background: #e94560; color: white; border: none; padding: 8px 20px; border-radius: 4px; cursor: pointer; }}
button:hover {{ background: #c13651; }}
.info {{ text-align: center; color: #aaa; margin: 20px 0; }}
</style>
</head>
<body>
<h1>Sing-box Node</h1>
<div class="info">
<p>IP: {self.public_ip}</p>
<p>ISP: {self.isp}</p>
<p>UUID: {self.uuid}</p>
</div>
{vless_html}
{trojan_html}
{argo_html}
<div class="link">
<h3>订阅链接</h3>
<code>{sub_url}/sub</code>
<button onclick="copyText('{sub_url}/sub')">复制</button>
</div>
<script>
function copyText(text) {{
    navigator.clipboard.writeText(text).then(() => alert('已复制到剪贴板'));
}}
</script>
</body>
</html>"""

# ========== 主程序 ==========
class SingboxNode:
    def __init__(self):
        self.is_windows = os.name == 'nt'
        self.script_dir = Path(__file__).parent.absolute()
        self.file_path = self.script_dir / ".npm"
        self.config = Config()

        if self.file_path.exists():
            shutil.rmtree(self.file_path)
        self.file_path.mkdir(parents=True, exist_ok=True)

        self.public_ip: Optional[str] = None
        self.best_cf_domain: str = ""
        self.uuid: str = ""
        self.private_key: Optional[str] = None
        self.public_key: Optional[str] = None
        self.isp: str = "Node"
        self.argo_domain: str = ""
        self.http_port: int = 0
        self.trojan_port: Optional[int] = None
        self._shutdown_event = False

        self.sb_binary: Optional[Path] = None
        self.argo_binary: Optional[Path] = None
        self.singbox: Optional[SingBox] = None
        self.sub_server: Optional[SubServer] = None
        self.argo_tunnel: Optional[ArgoTunnel] = None

        if not self.is_windows:
            signal.signal(signal.SIGTERM, self._signal_handler)
            signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum, frame):
        logger.info(f"[信号] 收到信号 {signum}，准备关闭...")
        self._shutdown_event = True
        self.cleanup()
        sys.exit(0)

    def get_available_ports(self) -> List[int]:
        """获取可用端口"""
        port_env_vars = [
            "PORTS_STRING", "PORT", "PORTS",
            "EASYPANEL_PORT", "EASYPANEL_PORTS",
            "MANGO_PORT", "APP_PORT", "INTERNAL_PORT",
        ]

        for var in port_env_vars:
            ports_str = os.environ.get(var, "")
            if ports_str:
                logger.info(f"[端口] 检测到环境变量 {var}: {ports_str}")
                return [int(p) for p in ports_str.replace(',', ' ').split() if p.isdigit()]

        import argparse
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument('-p', '--port', '--ports', dest='ports', type=str, default='')
        try:
            args, _ = parser.parse_known_args(sys.argv[1:])
            if args.ports:
                return [int(p) for p in args.ports.replace(',', ' ').split() if p.isdigit()]
        except Exception:
            pass

        config_file = self.script_dir / "ports.txt"
        if config_file.exists():
            ports_str = config_file.read_text(encoding='utf-8').strip()
            if ports_str:
                return [int(p) for p in ports_str.replace(',', ' ').split() if p.isdigit()]

        logger.warning("[端口] 未检测到端口配置，使用默认端口 5000...")
        return [5000]

    def run(self) -> None:
        """运行主程序"""
        logger.info("[初始化] Sing-box Node 启动中...")

        # 1. 获取公网IP
        logger.info("[网络] 获取公网 IP...")
        self.public_ip = Utils.get_public_ip()
        if not self.public_ip:
            logger.error("[错误] 无法获取公网 IP")
            sys.exit(1)
        logger.info(f"[网络] 公网 IP: {self.public_ip}")

        # 2. CF优选
        logger.info("[CF优选] 测试中...")
        self.best_cf_domain = Utils.select_random_cf_domain(self.config.CF_DOMAINS)
        logger.info(f"[CF优选] {self.best_cf_domain}")

        # 3. 获取端口
        available_ports = self.get_available_ports()
        if not available_ports:
            logger.error("[错误] 未找到端口")
            sys.exit(1)
        logger.info(f"[端口] 发现 {len(available_ports)} 个: {available_ports}")

        # 4. 端口分配
        self.http_port = available_ports[0]
        self.trojan_port = available_ports[0] if len(available_ports) == 1 else available_ports[1]

        # 5. UUID
        uuid_file = self.file_path / "uuid.txt"
        if uuid_file.exists():
            self.uuid = uuid_file.read_text(encoding='utf-8').strip()
        else:
            self.uuid = Utils.generate_uuid()
            uuid_file.write_text(self.uuid, encoding='utf-8')
        logger.info(f"[UUID] {self.uuid}")

        # 6. 下载二进制文件
        sb_arch, argo_arch = Utils.get_arch()
        sb_base_url = f"https://{sb_arch}.ssss.nyc.mn"

        self.sb_binary = self.file_path / "sb"
        self.argo_binary = self.file_path / "cloudflared"

        logger.info(f"[下载] sing-box ({sb_arch})...")
        if not Utils.download_file(f"{sb_base_url}/sb", self.sb_binary, retry=Config.DOWNLOAD_RETRY):
            logger.error("[下载] sing-box 多次失败")
            sys.exit(1)

        logger.info(f"[下载] cloudflared ({argo_arch})...")
        argo_url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{argo_arch}"
        if not Utils.download_file(argo_url, self.argo_binary, retry=Config.DOWNLOAD_RETRY):
            logger.error("[下载] cloudflared 多次失败")
            sys.exit(1)

        # 7. Reality密钥
        logger.info("[密钥] 检查中...")
        key_file = self.file_path / "key.txt"
        if key_file.exists():
            content = key_file.read_text(encoding='utf-8')
            priv_match = re.search(r'PrivateKey:\s*(\S+)', content)
            pub_match = re.search(r'PublicKey:\s*(\S+)', content)
            self.private_key = priv_match.group(1) if priv_match else None
            self.public_key = pub_match.group(1) if pub_match else None
        else:
            try:
                result = subprocess.run([str(self.sb_binary), "generate", "reality-keypair"],
                                        capture_output=True, text=True, timeout=10)
                if result.returncode == 0 and result.stdout:
                    output = result.stdout.strip()
                    key_file.write_text(output, encoding='utf-8')
                    priv_match = re.search(r'PrivateKey:\s*(\S+)', output)
                    pub_match = re.search(r'PublicKey:\s*(\S+)', output)
                    self.private_key = priv_match.group(1) if priv_match else None
                    self.public_key = pub_match.group(1) if pub_match else None
            except Exception as e:
                logger.warning(f"[密钥] 生成失败: {e}")
        logger.info("[密钥] 已就绪")

        # 8. 证书
        logger.info("[证书] 生成中...")
        cert_path = self.file_path / "cert.pem"
        key_path = self.file_path / "private.key"
        if not cert_path.exists() or not key_path.exists():
            if not Utils.generate_self_signed_cert(key_path, cert_path):
                logger.error("[证书] 生成失败")
                sys.exit(1)
        logger.info("[证书] 已就绪")

        # 9. 启动HTTP订阅服务
        self.sub_server = SubServer(self.http_port)
        self.sub_server.run_in_thread()
        time.sleep(1)

        # 10. 生成并保存sing-box配置
        logger.info("[CONFIG] 生成配置...")
        config_path = self.file_path / "config.json"
        config_gen = ConfigGenerator(
            self.file_path,
            self.uuid,
            self.private_key,
            self.trojan_port,
            argo_port=8081
        )
        config_gen.save(config_path)
        logger.info("[CONFIG] 配置已生成")

        # 11. 启动sing-box
        logger.info("[SING-BOX] 启动中...")
        self.singbox = SingBox(self.sb_binary, config_path)
        if not self.singbox.start():
            logger.error("[SING-BOX] 启动失败")
            sys.exit(1)

        # 12. 启动Argo隧道
        argo_port = 8081
        argo_log = self.file_path / "argo.log"
        self.argo_tunnel = ArgoTunnel(self.argo_binary, argo_port, self.config.ARGO_TOKEN)
        logger.info("[Argo] 启动隧道 (HTTP2模式)...")
        self.argo_tunnel.start(argo_log)
        self.argo_domain = self.argo_tunnel.wait_for_domain() or ""

        # 13. 生成订阅
        sub_gen = SubGenerator(
            self.uuid,
            self.public_ip,
            self.private_key,
            self.trojan_port,
            self.argo_domain,
            self.best_cf_domain,
            self.isp
        )
        sub_content = sub_gen.generate_sub()
        sub_url = f"http://{self.public_ip}:{self.http_port}"
        html_content = sub_gen.generate_html(sub_url)

        self.sub_server.update_content(sub_content, html_content)

        # 保存到文件
        (self.file_path / "sub.txt").write_text(sub_content, encoding='utf-8')

        # 14. 打印结果
        logger.info("")
        logger.info("=" * 60)
        logger.info("订阅内容 (可直接导入客户端):")
        logger.info("-" * 60)
        logger.info(sub_content)
        logger.info("=" * 60)
        logger.info("")
        logger.info("代理节点:")
        if self.private_key:
            logger.info(f"  - VLESS (Reality): {self.public_ip}:443")
        if self.trojan_port:
            logger.info(f"  - Trojan (TLS): {self.public_ip}:{self.trojan_port}")
        if self.argo_domain:
            logger.info(f"  - Argo (WS): {self.argo_domain}")
        logger.info("")
        logger.info(f"订阅链接: {sub_url}/sub")
        logger.info(f"Web页面:  {sub_url}/")
        logger.info("=" * 60)
        logger.info("")

        # 15. 保持运行
        logger.info("[运行中] 按 Ctrl+C 停止")
        try:
            while not self._shutdown_event:
                time.sleep(1)
                if self.singbox and not self.singbox.is_running():
                    logger.warning("[警告] sing-box 进程已退出")
                    break
        except KeyboardInterrupt:
            logger.info("\n[停止] 正在关闭服务...")
        finally:
            self.cleanup()

    def cleanup(self) -> None:
        """清理资源"""
        if self.argo_tunnel:
            self.argo_tunnel.stop()
        if self.singbox:
            self.singbox.stop()
        if self.sub_server:
            self.sub_server.stop()
        logger.info("[停止] 服务已关闭")


if __name__ == "__main__":
    app = SingboxNode()
    app.run()

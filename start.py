#!/usr/bin/env python3
"""
Sing-box Node 启动脚本
TUIC + Hysteria2 + Reality + Argo
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

    # 单端口模式 UDP 协议选择: hy2 (默认) 或 tuic
    SINGLE_PORT_UDP: str = "hy2"

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
    def exec_cmd(cmd: List[str], timeout: int = 60, cwd: Optional[str] = None) -> Tuple[int, str, str]:
        """执行命令并返回 (返回码, stdout, stderr)"""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "Command timed out"
        except Exception as e:
            return -1, "", str(e)

    @staticmethod
    def exec_cmd_stream(cmd: List[str], cwd: Optional[str] = None) -> subprocess.Popen:
        """执行命令并实时输出到控制台"""
        return subprocess.Popen(
            cmd,
            stdout=sys.stdout,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=cwd
        )

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
        services = [
            "https://ipv4.ip.sb",
            "https://api.ipify.org",
        ]
        for svc in services:
            ok, data = Utils.curl(svc, timeout=5)
            if ok and data.strip():
                return data.strip()
        return None

    @staticmethod
    def test_domain(domain: str) -> bool:
        """测试域名是否可访问"""
        ok, _ = Utils.curl(f"https://{domain}", timeout=2)
        return ok

    @staticmethod
    def select_random_cf_domain(domains: List[str]) -> str:
        """随机选择可用的CF优选域名"""
        import random
        available = [d for d in domains if Utils.test_domain(d)]
        if available:
            return random.choice(available)
        return domains[0]

    @staticmethod
    def get_arch() -> Tuple[str, str]:
        """获取系统架构，返回 (sb_url_arch, argo_arch)"""
        import platform
        arch = platform.machine()
        if arch == "aarch64":
            return "arm64", "arm64"
        return "amd64", "amd64"

    @staticmethod
    def ensure_dir(path: Path) -> None:
        """确保目录存在"""
        path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def read_file(path: Path) -> Optional[str]:
        """读取文件内容"""
        try:
            return path.read_text(encoding='utf-8')
        except Exception:
            return None

    @staticmethod
    def write_file(path: Path, content: str) -> bool:
        """写入文件内容"""
        try:
            path.write_text(content, encoding='utf-8')
            return True
        except Exception:
            return False

    @staticmethod
    def is_file_executable(path: Path) -> bool:
        """检查文件是否可执行"""
        return path.exists() and os.access(path, os.X_OK)

    @staticmethod
    def make_executable(path: Path) -> None:
        """设置文件可执行"""
        try:
            os.chmod(path, 0o755)
        except Exception:
            pass

    @staticmethod
    def download_file(url: str, output: Path, timeout: int = 60, retry: int = 3) -> bool:
        """下载文件，支持重试"""
        import urllib.request

        for attempt in range(retry):
            try:
                req = urllib.request.Request(url)
                req.add_header('User-Agent', 'curl/7.88.1')
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    content = resp.read()
                    output.write_bytes(content)
                Utils.make_executable(output)
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
    def generate_reality_keys(sb_binary: Path) -> Tuple[Optional[str], Optional[str]]:
        """使用sing-box生成Reality密钥对"""
        code, stdout, stderr = Utils.exec_cmd([str(sb_binary), "generate", "reality-keypair"])
        if code == 0 and stdout:
            priv_match = re.search(r'PrivateKey:\s*(\S+)', stdout)
            pub_match = re.search(r'PublicKey:\s*(\S+)', stdout)
            priv = priv_match.group(1) if priv_match else None
            pub = pub_match.group(1) if pub_match else None
            return priv, pub
        return None, None

    @staticmethod
    def generate_self_signed_cert(key_path: Path, cert_path: Path) -> bool:
        """生成自签名证书"""
        try:
            from cryptography import x509
            from cryptography.x509.oid import NameOID
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.primitives import serialization
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
            cert = x509.CertificateBuilder().subject_name(
                subject
            ).issuer_name(
                issuer
            ).public_key(
                key.public_key()
            ).serial_number(
                x509.random_serial_number()
            ).not_valid_before(
                datetime.datetime.utcnow()
            ).not_valid_after(
                datetime.datetime.utcnow() + datetime.timedelta(days=3650)
            ).sign(key, hashes.SHA256(), default_backend())

            key_bytes = key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption()
            )
            key_path.write_bytes(key_bytes)

            cert_bytes = cert.public_bytes(serialization.Encoding.PEM)
            cert_path.write_bytes(cert_bytes)

            return True
        except ImportError:
            return Utils.generate_cert_with_openssl(key_path, cert_path)
        except Exception as e:
            logger.error(f"[证书] 生成失败: {e}")
            return False

    @staticmethod
    def generate_cert_with_openssl(key_path: Path, cert_path: Path) -> bool:
        """使用OpenSSL生成证书"""
        # 备用硬编码证书（用于无法生成的情况）
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

    @staticmethod
    def get_isp_info() -> str:
        """获取ISP信息"""
        try:
            ok, data = Utils.curl("https://speed.cloudflare.com/meta", timeout=3, method="GET")
            if ok and data:
                try:
                    info = json.loads(data)
                    org = info.get("asOrganization", "")
                    city = info.get("city", "")
                    if org and city:
                        return f"{org}-{city}"
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass
        return "Node"

# ========== HTTP订阅服务器 ==========
class SubServer:
    def __init__(self, sub_content: str, port: int, bind: str = "0.0.0.0"):
        self.sub_content = sub_content
        self.port = port
        self.bind = bind
        self.server = None
        self._running = False

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

            if '/sub' in request_line or '/uuid' in request_line:
                response = f"HTTP/1.1 200 OK\r\n"
                response += "Content-Type: text/plain; charset=utf-8\r\n"
                response += "Connection: close\r\n"
                response += f"Content-Length: {len(self.sub_content)}\r\n"
                response += "\r\n"
                response += self.sub_content
            elif request_line.startswith("GET / ") or request_line == "GET / HTTP/1.1" or "GET / HTTP" in request_line:
                ok_content = "Sing-box Node is running. Subscribe at /sub or /{uuid}"
                response = f"HTTP/1.1 200 OK\r\n"
                response += "Content-Type: text/plain; charset=utf-8\r\n"
                response += "Connection: close\r\n"
                response += f"Content-Length: {len(ok_content)}\r\n"
                response += "\r\n"
                response += ok_content
            else:
                response = "HTTP/1.1 200 OK\r\n"
                response += "Content-Type: text/plain; charset=utf-8\r\n"
                response += "Connection: close\r\n"
                response += "Content-Length: 2\r\n"
                response += "\r\n"
                response += "OK"

            client_socket.sendall(response.encode('utf-8'))
        except Exception:
            pass
        finally:
            try:
                client_socket.close()
            except Exception:
                pass
    
    def update_content(self, content: str) -> None:
        """更新订阅内容"""
        self.sub_content = content
    
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
                
                # 为每个连接创建线程
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
            # 固定隧道模式
            cmd.extend(["--token", self.argo_token])
        else:
            # 临时隧道模式
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
        """等待获取域名（固定隧道模式直接返回空）"""
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
            print("[SING-BOX] 启动失败")
            return False
        print(f"[SING-BOX] 已启动 PID: {self.process.pid}")
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
    def __init__(self, file_path: Path, uuid_str: str, ports: dict, 
                 private_key: Optional[str] = None, single_port_mode: bool = False,
                 argo_port: int = 8081):
        self.file_path = file_path
        self.uuid = uuid_str
        self.ports = ports
        self.private_key = private_key
        self.single_port_mode = single_port_mode
        self.argo_port = argo_port
    
    def generate(self) -> dict:
        """生成sing-box配置"""
        inbounds = []
        
        # TUIC
        if self.ports.get('tuic'):
            inbounds.append({
                "type": "tuic",
                "tag": "tuic-in",
                "listen": "::",
                "listen_port": self.ports['tuic'],
                "users": [{"uuid": self.uuid, "password": "admin"}],
                "congestion_control": "bbr",
                "tls": {
                    "enabled": True,
                    "alpn": ["h3"],
                    "certificate_path": str(self.file_path / "cert.pem"),
                    "key_path": str(self.file_path / "private.key")
                }
            })
        
        # Hysteria2
        if self.ports.get('hy2'):
            inbounds.append({
                "type": "hysteria2",
                "tag": "hy2-in",
                "listen": "::",
                "listen_port": self.ports['hy2'],
                "users": [{"password": self.uuid}],
                "tls": {
                    "enabled": True,
                    "alpn": ["h3"],
                    "certificate_path": str(self.file_path / "cert.pem"),
                    "key_path": str(self.file_path / "private.key")
                }
            })
        
        # VLESS Reality
        if self.ports.get('reality') and self.private_key:
            inbounds.append({
                "type": "vless",
                "tag": "vless-reality-in",
                "listen": "::",
                "listen_port": self.ports['reality'],
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
            print(f"[CONFIG] 保存配置失败: {e}")
            return False

# ========== 订阅生成器 ==========
class SubGenerator:
    def __init__(self, file_path: Path, uuid_str: str, public_ip: str, 
                 ports: dict, public_key: Optional[str] = None,
                 argo_domain: str = "", isp: str = "Node",
                 single_port_mode: bool = False):
        self.file_path = file_path
        self.uuid = uuid_str
        self.public_ip = public_ip
        self.ports = ports
        self.public_key = public_key
        self.argo_domain = argo_domain
        self.isp = isp
        self.single_port_mode = single_port_mode
    
    def generate(self) -> str:
        """生成订阅内容"""
        lines = []
        
        # TUIC
        if self.ports.get('tuic'):
            port = self.ports['tuic']
            line = f"tuic://{self.uuid}:admin@{self.public_ip}:{port}?sni=www.bing.com&alpn=h3&congestion_control=bbr&allowInsecure=1#TUIC-{self.isp}"
            lines.append(line)
        
        # Hysteria2
        if self.ports.get('hy2'):
            port = self.ports['hy2']
            line = f"hysteria2://{self.uuid}@{self.public_ip}:{port}/?sni=www.bing.com&insecure=1#Hysteria2-{self.isp}"
            lines.append(line)
        
        # Reality
        if self.ports.get('reality') and self.public_key:
            port = self.ports['reality']
            line = f"vless://{self.uuid}@{self.public_ip}:{port}?encryption=none&flow=xtls-rprx-vision&security=reality&sni=www.nazhumi.com&fp=chrome&pbk={self.public_key}&type=tcp#Reality-{self.isp}"
            lines.append(line)
        
        # Argo VLESS
        if self.argo_domain and self.ports.get('cf_best'):
            cf_domain = self.ports['cf_best']
            line = f"vless://{self.uuid}@{cf_domain}:443?encryption=none&security=tls&sni={self.argo_domain}&type=ws&host={self.argo_domain}&path=%2F{self.uuid}-vless#Argo-{self.isp}"
            lines.append(line)
        
        return "\n".join(lines)
    
    def save(self) -> bool:
        """保存订阅文件"""
        try:
            content = self.generate()
            list_path = self.file_path / "list.txt"
            sub_path = self.file_path / "sub.txt"
            list_path.write_text(content, encoding='utf-8')
            sub_path.write_text(content, encoding='utf-8')
            return True
        except Exception as e:
            print(f"[订阅] 保存失败: {e}")
            return False

# ========== 主程序 ==========
class SingboxNode:
    def __init__(self):
        # 检测Windows平台
        self.is_windows = os.name == 'nt'

        # 切换到脚本目录
        self.script_dir = Path(__file__).parent.absolute()
        self.file_path = self.script_dir / ".npm"
        self.config = Config()

        # 初始化目录
        if self.file_path.exists():
            shutil.rmtree(self.file_path)
        self.file_path.mkdir(parents=True, exist_ok=True)

        # 状态变量
        self.public_ip: Optional[str] = None
        self.best_cf_domain: str = ""
        self.uuid: str = ""
        self.ports: dict = {}
        self.private_key: Optional[str] = None
        self.public_key: Optional[str] = None
        self.isp: str = "Node"
        self.argo_domain: str = ""
        self.single_port_mode: bool = False
        self._shutdown_event = False

        # 组件
        self.sb_binary: Optional[Path] = None
        self.argo_binary: Optional[Path] = None
        self.singbox: Optional[SingBox] = None
        self.sub_server: Optional[SubServer] = None
        self.argo_tunnel: Optional[ArgoTunnel] = None

        # 信号处理
        if not self.is_windows:
            signal.signal(signal.SIGTERM, self._signal_handler)
            signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """处理信号"""
        logger.info(f"[信号] 收到信号 {signum}，准备关闭...")
        self._shutdown_event = True
        self.cleanup()
        sys.exit(0)
    def get_available_ports(self) -> List[int]:
        """获取可用端口 - 支持多种方式"""
        port_sources = []

        port_env_vars = [
            "PORTS_STRING",
            "PORT",
            "PORTS",
            "EASYPANEL_PORT",
            "EASYPANEL_PORTS",
            "MANGO_PORT",
            "APP_PORT",
            "INTERNAL_PORT",
        ]

        for var in port_env_vars:
            ports_str = os.environ.get(var, "")
            if ports_str:
                logger.info(f"[端口] 检测到环境变量 {var}: {ports_str}")
                port_sources.append(('env', var, ports_str))

        import argparse
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument('-p', '--port', '--ports', dest='ports', type=str, default='')
        parser.add_argument('-h', '--help', action='store_true')
        try:
            args, _ = parser.parse_known_args(sys.argv[1:])
            if args.ports:
                logger.info(f"[端口] 检测到命令行参数: {args.ports}")
                port_sources.append(('arg', 'command line', args.ports))
            if args.help:
                logger.info("用法: python start.py -p 30001 30002 或 --ports 30001,30002,30003")
        except Exception:
            pass

        config_file = self.script_dir / "ports.txt"
        if config_file.exists():
            ports_str = config_file.read_text(encoding='utf-8').strip()
            if ports_str:
                logger.info(f"[端口] 检测到配置文件: {ports_str}")
                port_sources.append(('file', str(config_file), ports_str))

        if not port_sources:
            logger.error("[端口] 未找到端口配置，请使用以下方式之一:")
            logger.error("  1. 环境变量: export PORTS_STRING='30001 30002'")
            logger.error("  2. 命令行: python start.py -p 30001 30002")
            logger.error("  3. 配置文件: echo '30001 30002' > ports.txt")
            return []

        _, _, ports_str = port_sources[-1]
        return [int(p) for p in ports_str.replace(',', ' ').split() if p.isdigit()]

    def get_public_url(self) -> Optional[str]:
        """尝试获取公开访问URL"""
        url_vars = [
            "PUBLIC_URL",
            "APP_URL",
            "APP_DOMAIN",
            "URL",
            "MANGO_URL",
            "SERVICE_URL",
        ]

        for var in url_vars:
            url = os.environ.get(var, "")
            if url:
                logger.info(f"[URL] 检测到: {var} = {url}")
                return url.rstrip('/')

        return None

    def get_app_name(self) -> Optional[str]:
        """从HOSTNAME或域名推断应用名称"""
        hostname = os.environ.get("HOSTNAME", "")
        if hostname and 'mangoi' in os.environ.get("HOSTNAME", "").lower():
            pass

        known_domains = [
            ("mangoi.in", "Mango"),
        ]
        for domain, platform in known_domains:
            if hostname and domain in hostname:
                return hostname.split('.')[0]

        return None
    
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
            logger.error("[错误] 未找到端口 (请设置 PORTS_STRING 环境变量)")
            sys.exit(1)
        logger.info(f"[端口] 发现 {len(available_ports)} 个: {available_ports}")

        # 4. 端口分配
        if len(available_ports) == 1:
            self.ports['udp'] = available_ports[0]
            self.ports['cf_best'] = self.best_cf_domain
            if self.config.SINGLE_PORT_UDP == "tuic":
                self.ports['tuic'] = available_ports[0]
                self.ports['hy2'] = ""
            else:
                self.ports['hy2'] = available_ports[0]
                self.ports['tuic'] = ""
            self.ports['reality'] = ""
            self.ports['http'] = available_ports[0]
            self.single_port_mode = True
        else:
            self.ports['tuic'] = available_ports[0]
            self.ports['hy2'] = available_ports[1]
            self.ports['reality'] = available_ports[0]
            self.ports['http'] = available_ports[1]
            self.ports['cf_best'] = self.best_cf_domain
            self.single_port_mode = False

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
        if not self.single_port_mode:
            logger.info("[密钥] 检查中...")
            key_file = self.file_path / "key.txt"
            if key_file.exists():
                content = key_file.read_text(encoding='utf-8')
                priv_match = re.search(r'PrivateKey:\s*(\S+)', content)
                pub_match = re.search(r'PublicKey:\s*(\S+)', content)
                self.private_key = priv_match.group(1) if priv_match else None
                self.public_key = pub_match.group(1) if pub_match else None
            else:
                priv, pub = Utils.generate_reality_keys(self.sb_binary)
                if priv and pub:
                    output = f"PrivateKey: {priv}\nPublicKey: {pub}"
                    key_file.write_text(output, encoding='utf-8')
                    self.private_key = priv
                    self.public_key = pub
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

        # 9. ISP信息
        logger.info("[ISP] 获取中...")
        self.isp = Utils.get_isp_info()
        logger.info(f"[ISP] {self.isp}")
        
        # 10. 启动HTTP订阅服务
        http_port = self.ports['http']
        self.sub_server = SubServer("", http_port)
        self.sub_server.run_in_thread()
        time.sleep(1)
        
        # 11. 生成并保存sing-box配置
        logger.info("[CONFIG] 生成配置...")
        config_path = self.file_path / "config.json"
        config_gen = ConfigGenerator(
            self.file_path,
            self.uuid,
            self.ports,
            self.private_key,
            self.single_port_mode
        )
        config_gen.save(config_path)
        logger.info("[CONFIG] 配置已生成")

        # 12. 启动sing-box
        logger.info("[SING-BOX] 启动中...")
        self.singbox = SingBox(self.sb_binary, config_path)
        if not self.singbox.start():
            logger.error("[SING-BOX] 启动失败")
            sys.exit(1)

        # 13. 启动Argo隧道
        argo_port = 8081
        argo_log = self.file_path / "argo.log"
        self.argo_tunnel = ArgoTunnel(self.argo_binary, argo_port, self.config.ARGO_TOKEN)
        logger.info("[Argo] 启动隧道 (HTTP2模式)...")
        self.argo_tunnel.start(argo_log)
        self.argo_domain = self.argo_tunnel.wait_for_domain() or ""

        # 14. 生成订阅
        sub_gen = SubGenerator(
            self.file_path,
            self.uuid,
            self.public_ip,
            self.ports,
            self.public_key,
            self.argo_domain,
            self.isp,
            self.single_port_mode
        )
        sub_gen.save()
        self.sub_server.update_content(sub_gen.generate())

        # 15. 打印结果
        public_url = self.get_public_url()
        if public_url:
            sub_url = f"{public_url}/sub"
        else:
            sub_url = f"http://{self.public_ip}:{http_port}/sub"

        logger.info("")
        logger.info("=" * 50)
        if self.single_port_mode:
            logger.info(f"模式: 单端口 ({self.config.SINGLE_PORT_UDP.upper()} + Argo)")
            logger.info("")
            logger.info("代理节点:")
            if self.ports.get('hy2'):
                logger.info(f"  - HY2 (UDP): {self.public_ip}:{self.ports['hy2']}")
            if self.ports.get('tuic'):
                logger.info(f"  - TUIC (UDP): {self.public_ip}:{self.ports['tuic']}")
            if self.argo_domain:
                logger.info(f"  - Argo (WS): {self.argo_domain}")
        else:
            logger.info("模式: 多端口 (TUIC + HY2 + Reality + Argo)")
            logger.info("")
            logger.info("代理节点:")
            logger.info(f"  - TUIC (UDP): {self.public_ip}:{self.ports['tuic']}")
            logger.info(f"  - HY2 (UDP): {self.public_ip}:{self.ports['hy2']}")
            logger.info(f"  - Reality (TCP): {self.public_ip}:{self.ports['reality']}")
            if self.argo_domain:
                logger.info(f"  - Argo (WS): {self.argo_domain}")

        logger.info("")
        if public_url:
            logger.info(f"订阅链接: {sub_url} (通过 {public_url})")
        else:
            logger.info(f"订阅链接: {sub_url}")
        logger.info("=" * 50)
        logger.info("")

        # 16. 保持运行
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

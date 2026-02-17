import logging
import os
import time
from pathlib import Path
from ..utils.logging import Icons, pretty_log

logger = logging.getLogger("GhostAgent")

CONTAINER_NAME = "ghost-agent-sandbox"
CONTAINER_WORKDIR = "/workspace"

class DockerSandbox:
    def __init__(self, host_workspace: Path, tor_proxy: str = None):
        try:
            import docker
            from docker.errors import NotFound, APIError
            self.docker_lib = docker
            self.NotFound = NotFound
            self.APIError = APIError
        except ImportError:
            logger.error("Docker library not found. pip install docker")
            raise

        self.client = self.docker_lib.from_env()
        self.host_workspace = host_workspace.absolute()
        self.tor_proxy = tor_proxy
        self.container = None
        self.image = "python:3.11-slim-bookworm"

        pretty_log("Sandbox Init", f"Mounting {self.host_workspace} -> {CONTAINER_WORKDIR}", icon=Icons.SYSTEM_BOOT)

    def get_stats(self):
        if not self.container: return None
        try: return self.container.stats(stream=False)
        except: return None

    def _is_container_ready(self):
        try:
            self.container.reload()
            return self.container.status == "running"
        except:
            return False

    def ensure_running(self):
        try:
            if not self.container:
                self.container = self.client.containers.get(CONTAINER_NAME)
        except self.NotFound:
            pass 

        if not (self.container and self._is_container_ready()):
            pretty_log("Sandbox", "Initializing High-Performance Environment...", icon="⚙️")
            try:
                try:
                    old = self.client.containers.get(CONTAINER_NAME)
                    old.remove(force=True)
                    time.sleep(1) 
                except self.NotFound: pass

                self.container = self.client.containers.run(
                    self.image,
                    command="sleep infinity",
                    name=CONTAINER_NAME,
                    detach=True,
                    tty=True,
                    volumes={str(self.host_workspace): {'bind': CONTAINER_WORKDIR, 'mode': 'rw'}},
                    mem_limit="512m", 
                    network_mode="bridge",
                )
                
                for _ in range(10):
                    if self._is_container_ready(): break
                    time.sleep(1)
                
            except Exception as e:
                pretty_log("Sandbox Error", f"Failed to start: {e}", level="ERROR")
                raise e

        # Prepare Proxy Env for Installs
        env_vars = {}
        if self.tor_proxy:
            # Docker requires the host address usually, but for socks5h logic 
            # we simply pass the proxy string. Note: Localhost routing from inside docker 
            # is tricky. We assume the user has configured TOR_PROXY to be accessible.
            # However, standard practice: direct container traffic via proxy.
            p_url = self.tor_proxy.replace("socks5://", "socks5h://") 
            env_vars = {"HTTP_PROXY": p_url, "HTTPS_PROXY": p_url}

        exit_code, _ = self.container.exec_run("test -f /root/.supercharged")
        if exit_code != 0:
            pretty_log("Sandbox", "Installing Deep Learning Stack (Wait ~60s)...", icon="📦")
            self.container.exec_run("apt-get update && apt-get install -y coreutils nodejs npm g++ curl wget git procps postgresql-client libpq-dev", environment=env_vars)
            install_cmd = (
                "pip install --no-cache-dir "
                "torch numpy pandas scipy matplotlib seaborn "
                "scikit-learn yfinance beautifulsoup4 networkx requests "
                "pylint black mypy bandit "
                "psycopg2-binary asyncpg sqlalchemy tabulate sqlglot"
            )
            self.container.exec_run(install_cmd, environment=env_vars)
            self.container.exec_run("touch /root/.supercharged")
            pretty_log("Sandbox", "Environment Ready.", icon="✅")

    def execute(self, cmd: str, timeout: int = 300):
        try:
            self.ensure_running()
            if not self._is_container_ready():
                return "Error: Container refused to start.", 1

            cmd_string = f"timeout {timeout}s {cmd}"
            user_id = os.getuid()
            group_id = os.getgid()
            
            exec_result = self.container.exec_run(
                cmd_string, 
                workdir=CONTAINER_WORKDIR, 
                demux=True,
                user=f"{user_id}:{group_id}" 
            )
            
            stdout_bytes, stderr_bytes = exec_result.output
            exit_code = exec_result.exit_code

            output = ""
            if stdout_bytes: output += stdout_bytes.decode("utf-8", errors="replace")
            if stderr_bytes: 
                if output: output += "\n--- STDERR ---\n"
                output += stderr_bytes.decode("utf-8", errors="replace")

            if not output.strip() and exit_code != 0:
                 output = f"[SYSTEM ERROR]: Process failed (Exit {exit_code}) with no output."

            return output, exit_code

        except Exception as e:
            return f"Container Execution Error: {str(e)}", 1
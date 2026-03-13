"""
Code executor — extracts code from agent output into real files,
installs dependencies, runs the app, and performs integration tests.
"""

import os
import re
import json
import time
import shutil
import signal
import logging
import subprocess
import textwrap

import requests

logger = logging.getLogger(__name__)


class CodeExecutor:
    """Handles code extraction, execution, and integration testing."""

    def __init__(self, config: dict):
        self.config = config
        exec_cfg = config.get("code_execution", {})
        self.project_folder = exec_cfg.get("project_folder", "generated_project")
        self.test_timeout = exec_cfg.get("test_timeout_seconds", 30)
        self.server_startup_wait = exec_cfg.get("server_startup_wait_seconds", 5)
        self.localhost_port = exec_cfg.get("localhost_port", 8000)

        # Use absolute path if provided, otherwise resolve relative to this file
        if os.path.isabs(self.project_folder):
            self.project_path = self.project_folder
        else:
            project_root = os.path.dirname(__file__)
            self.project_path = os.path.join(project_root, self.project_folder)

    # ─────────────────────────────────────────────────────────────────────
    # Code extraction
    # ─────────────────────────────────────────────────────────────────────

    def extract_code_files(self, code_output: str) -> dict:
        """
        Parse the agent's code output and extract files.
        Looks for markdown code blocks with filenames like:
            ```python filename.py
            or
            # filename.py
            ```python

        Returns:
            dict mapping filename -> content
        """
        files = {}

        # Pattern 1: ```language filename\n...code...\n```
        pattern1 = re.compile(
            r"```\w*\s+([\w./\\-]+\.\w+)\s*\n(.*?)```",
            re.DOTALL,
        )
        for match in pattern1.finditer(code_output):
            filename = match.group(1).strip()
            content = match.group(2).strip()
            files[filename] = content

        # Pattern 2: ### filename.py or ## filename.py followed by ```
        pattern2 = re.compile(
            r"#{2,4}\s+([\w./\\-]+\.\w+)\s*\n+```\w*\n(.*?)```",
            re.DOTALL,
        )
        for match in pattern2.finditer(code_output):
            filename = match.group(1).strip()
            content = match.group(2).strip()
            if filename not in files:
                files[filename] = content

        # Pattern 3: **filename.py** followed by ```
        pattern3 = re.compile(
            r"\*\*([\w./\\-]+\.\w+)\*\*\s*\n+```\w*\n(.*?)```",
            re.DOTALL,
        )
        for match in pattern3.finditer(code_output):
            filename = match.group(1).strip()
            content = match.group(2).strip()
            if filename not in files:
                files[filename] = content

        # Fallback: if no named files found, save as single main file
        if not files:
            # Extract any code block
            fallback = re.compile(r"```(\w+)?\n(.*?)```", re.DOTALL)
            blocks = fallback.findall(code_output)
            if blocks:
                lang = blocks[0][0] or "py"
                ext_map = {
                    "python": "py", "javascript": "js", "typescript": "ts",
                    "java": "java", "go": "go", "rust": "rs", "ruby": "rb",
                    "py": "py", "js": "js", "ts": "ts",
                }
                ext = ext_map.get(lang, lang)
                all_code = "\n\n".join(block[1].strip() for block in blocks)
                files[f"main.{ext}"] = all_code

        return files

    def write_files(self, files: dict) -> str:
        """
        Write extracted files to the project folder.

        Returns:
            The absolute path of the project folder.
        """
        # Clean and recreate project folder
        if os.path.exists(self.project_path):
            shutil.rmtree(self.project_path)
        os.makedirs(self.project_path, exist_ok=True)

        for filename, content in files.items():
            filepath = os.path.join(self.project_path, filename)
            # Create subdirectories if needed
            os.makedirs(os.path.dirname(filepath), exist_ok=True) if os.path.dirname(filepath) else None
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"  Wrote: {filepath}")

        return self.project_path

    # ─────────────────────────────────────────────────────────────────────
    # Dependency installation
    # ─────────────────────────────────────────────────────────────────────

    def install_dependencies(self) -> dict:
        """
        Detect and install dependencies.

        Returns:
            dict with keys: success, output, error
        """
        result = {"success": True, "output": "", "error": ""}

        req_file = os.path.join(self.project_path, "requirements.txt")
        package_json = os.path.join(self.project_path, "package.json")

        try:
            if os.path.exists(req_file):
                logger.info("  Installing Python dependencies...")
                proc = subprocess.run(
                    ["pip", "install", "-r", req_file],
                    capture_output=True, text=True, timeout=60,
                    cwd=self.project_path,
                )
                result["output"] = proc.stdout
                if proc.returncode != 0:
                    result["success"] = False
                    result["error"] = proc.stderr

            elif os.path.exists(package_json):
                logger.info("  Installing Node.js dependencies...")
                proc = subprocess.run(
                    ["npm", "install"],
                    capture_output=True, text=True, timeout=120,
                    cwd=self.project_path,
                )
                result["output"] = proc.stdout
                if proc.returncode != 0:
                    result["success"] = False
                    result["error"] = proc.stderr
            else:
                result["output"] = "No dependency file found. Skipping install."

        except subprocess.TimeoutExpired:
            result["success"] = False
            result["error"] = "Dependency installation timed out."
        except FileNotFoundError as e:
            result["success"] = False
            result["error"] = f"Command not found: {e}"

        return result

    # ─────────────────────────────────────────────────────────────────────
    # Code execution (syntax check + run)
    # ─────────────────────────────────────────────────────────────────────

    def run_code(self) -> dict:
        """
        Attempt to run the main entry point of the extracted project.

        Returns:
            dict with keys: success, output, error
        """
        result = {"success": True, "output": "", "error": ""}

        # Detect the main file
        main_file = self._detect_main_file()
        if not main_file:
            result["success"] = False
            result["error"] = "Could not detect a main entry point file."
            return result

        ext = os.path.splitext(main_file)[1]
        filepath = os.path.join(self.project_path, main_file)

        try:
            if ext == ".py":
                # First do a syntax check
                proc = subprocess.run(
                    ["python3", "-m", "py_compile", filepath],
                    capture_output=True, text=True, timeout=10,
                )
                if proc.returncode != 0:
                    result["success"] = False
                    result["error"] = f"Syntax error:\n{proc.stderr}"
                    return result

                # Run with timeout (non-server mode — just check it starts)
                proc = subprocess.run(
                    ["python3", filepath, "--help"],
                    capture_output=True, text=True, timeout=self.test_timeout,
                    cwd=self.project_path,
                    env={**os.environ, "TESTING": "1"},
                )
                result["output"] = proc.stdout
                if proc.returncode != 0 and proc.returncode != 2:
                    # returncode 2 is common for --help with argparse
                    result["error"] = proc.stderr
                    # Only fail on actual errors, not missing --help support
                    if "Error" in proc.stderr or "Traceback" in proc.stderr:
                        result["success"] = False

            elif ext in (".js", ".ts"):
                proc = subprocess.run(
                    ["node", "--check", filepath],
                    capture_output=True, text=True, timeout=10,
                )
                if proc.returncode != 0:
                    result["success"] = False
                    result["error"] = f"Syntax error:\n{proc.stderr}"
                    return result
                result["output"] = "Syntax check passed."

        except subprocess.TimeoutExpired:
            result["output"] = "Process ran but timed out (may be expected for servers)."
        except FileNotFoundError as e:
            result["success"] = False
            result["error"] = f"Runtime not found: {e}"

        return result

    # ─────────────────────────────────────────────────────────────────────
    # Integration testing
    # ─────────────────────────────────────────────────────────────────────

    def run_integration_tests(self, test_script: str) -> dict:
        """
        Start the app server, run integration tests, then stop the server.

        Args:
            test_script: Python test code generated by the Integration Test Writer agent.

        Returns:
            dict with keys: success, output, error, test_results
        """
        result = {"success": True, "output": "", "error": "", "test_results": []}

        # Write the test script
        test_file = os.path.join(self.project_path, "_integration_tests.py")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write(test_script)

        server_proc = None
        try:
            # Start the server
            main_file = self._detect_main_file()
            if not main_file:
                result["success"] = False
                result["error"] = "No main file found to start server."
                return result

            server_cmd = self._build_server_command(main_file)
            logger.info(f"  Starting server: {' '.join(server_cmd)}")

            server_proc = subprocess.Popen(
                server_cmd,
                cwd=self.project_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, "PORT": str(self.localhost_port)},
                preexec_fn=os.setsid,
            )

            # Wait for server to start
            logger.info(f"  Waiting {self.server_startup_wait}s for server startup...")
            time.sleep(self.server_startup_wait)

            # Check if server is still running
            if server_proc.poll() is not None:
                stderr = server_proc.stderr.read().decode()
                result["success"] = False
                result["error"] = f"Server failed to start:\n{stderr}"
                return result

            # Check if server is responding
            server_ready = self._wait_for_server(timeout=self.server_startup_wait)
            if not server_ready:
                result["success"] = False
                result["error"] = (
                    f"Server did not respond on port {self.localhost_port} "
                    f"within {self.server_startup_wait}s."
                )
                return result

            logger.info("  Server is up. Running integration tests...")

            # Run the integration tests
            proc = subprocess.run(
                ["python3", test_file],
                capture_output=True, text=True,
                timeout=self.test_timeout,
                cwd=self.project_path,
                env={
                    **os.environ,
                    "TEST_BASE_URL": f"http://localhost:{self.localhost_port}",
                },
            )

            result["output"] = proc.stdout
            if proc.returncode != 0:
                result["success"] = False
                result["error"] = proc.stderr or proc.stdout
            else:
                result["output"] = proc.stdout

        except subprocess.TimeoutExpired:
            result["success"] = False
            result["error"] = "Integration tests timed out."
        except Exception as e:
            result["success"] = False
            result["error"] = f"Integration test error: {type(e).__name__}: {e}"
        finally:
            # Stop the server
            if server_proc and server_proc.poll() is None:
                logger.info("  Stopping server...")
                try:
                    os.killpg(os.getpgid(server_proc.pid), signal.SIGTERM)
                    server_proc.wait(timeout=5)
                except Exception:
                    try:
                        os.killpg(os.getpgid(server_proc.pid), signal.SIGKILL)
                    except Exception:
                        pass

            # Clean up test file
            if os.path.exists(test_file):
                os.remove(test_file)

        return result

    def _wait_for_server(self, timeout: int = 10) -> bool:
        """Poll localhost until the server responds or timeout."""
        url = f"http://localhost:{self.localhost_port}"
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = requests.get(url, timeout=2)
                return True
            except requests.ConnectionError:
                time.sleep(0.5)
            except Exception:
                time.sleep(0.5)
        return False

    # ─────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────

    def _detect_main_file(self) -> str | None:
        """Find the main entry point file in the project folder."""
        candidates = [
            "main.py", "app.py", "server.py", "index.py", "run.py",
            "index.js", "server.js", "app.js", "main.js",
            "index.ts", "server.ts", "app.ts", "main.ts",
        ]
        for name in candidates:
            if os.path.exists(os.path.join(self.project_path, name)):
                return name

        # Fallback: first .py or .js file
        for f in sorted(os.listdir(self.project_path)):
            if f.endswith((".py", ".js")) and not f.startswith("_"):
                return f
        return None

    def _build_server_command(self, main_file: str) -> list:
        """Build the command to start the server."""
        ext = os.path.splitext(main_file)[1]
        filepath = os.path.join(self.project_path, main_file)

        if ext == ".py":
            return ["python3", filepath]
        elif ext in (".js", ".ts"):
            return ["node", filepath]
        else:
            return ["python3", filepath]

    def get_project_file_list(self) -> list:
        """Return a list of all files in the project folder."""
        files = []
        for root, dirs, filenames in os.walk(self.project_path):
            for f in filenames:
                rel = os.path.relpath(os.path.join(root, f), self.project_path)
                files.append(rel)
        return sorted(files)

    def read_file(self, filename: str) -> str:
        """Read a file from the project folder."""
        filepath = os.path.join(self.project_path, filename)
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                return f.read()
        return ""

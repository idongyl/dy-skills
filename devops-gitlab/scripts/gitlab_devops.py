# -*- coding: utf-8 -*-
"""
GitLab DevOps Tool for Copaw Agent
通过 GitLab API v4 实现远程仓库分析和原子提交，无需本地 git 操作
支持自动生成可部署的 Dockerfile
"""

import json
import os
import re
from typing import Dict, Optional, Any, List
import requests


# ==================== 常量定义 ====================
GITLAB_API_BASE = "https://gitlab.com/api/v4"
SECRET_FILE_PATH = r"C:\Users\dong\.copaw.secret\envs.json"

# 核心依赖文件列表
CORE_DEPENDENCY_FILES = [
    "package.json",
    "requirements.txt",
    "pom.xml",
    "go.mod",
    "pyproject.toml",
    "docker-compose.yml",
    "Dockerfile",
    "Cargo.toml",
    "Gemfile",
    "composer.json"
]

# 语言检测指示文件
LANGUAGE_INDICATORS = {
    "python": ["requirements.txt", "pyproject.toml", "setup.py", "Pipfile", "setup.cfg"],
    "node": ["package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"],
    "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
    "go": ["go.mod", "go.sum"],
    "cpp": ["CMakeLists.txt", "Makefile", "compile_commands.json"],
    "rust": ["Cargo.toml"],
    "ruby": ["Gemfile", "Gemfile.lock"],
    "php": ["composer.json"],
    ".net": [".csproj", ".sln"]
}

# 框架检测模式
FRAMEWORK_PATTERNS = {
    "python": {
        "flask": [r"from flask import", r"Flask\(", r"flask"],
        "django": [r"from django", r"django\.conf", r"manage\.py"],
        "fastapi": [r"from fastapi import", r"FastAPI\(", r"fastapi"],
        "uvicorn": [r"uvicorn", r"--host", r"--port"]
    },
    "node": {
        "express": [r"require\(['\"]express['\"]\)", r"from ['\"]express['\"]", r"express\(\)"],
        "next": [r"from ['\"]next['\"]", r"next\(", r"next-config"],
        "nest": [r"@nestjs", r"NestFactory", r"nest start"],
        "fastify": [r"fastify", r"fastify\(\)"]
    },
    "java": {
        "spring": [r"spring-boot", r"SpringApplication", r"@SpringBootApplication"],
        "maven": ["pom.xml"],
        "gradle": ["build.gradle"]
    },
    "cpp": {
        "cmake": ["CMakeLists.txt"],
        "make": ["Makefile"]
    }
}

# 默认端口
DEFAULT_PORTS = {
    "python": {"flask": 5000, "fastapi": 8000, "django": 8000, "default": 8000},
    "node": {"express": 3000, "next": 3000, "nest": 3000, "default": 3000},
    "java": {"spring": 8080, "default": 8080},
    "cpp": {"default": 8080},
    "go": {"default": 8080}
}

# 入口文件模式
ENTRY_FILES = {
    "python": ["main.py", "app.py", "server.py", "run.py", "__main__.py"],
    "node": ["index.js", "server.js", "app.js", "main.js"],
    "java": ["Main.java", "Application.java", "App.java"],
    "cpp": ["main.cpp", "main.c"],
    "go": ["main.go"],
    "rust": ["main.rs"]
}


# ==================== 辅助函数 ====================
import subprocess

def _load_gitlab_token() -> str:
    """
    从 CoPaw CLI 获取解密后的 GitLab Token
    
    Returns:
        str: GitLab API Token (解密后的)
        
    Raises:
        RuntimeError: 无法获取 token
    """
    try:
        result = subprocess.run(
            ['copaw', 'env', 'list'],
            capture_output=True,
            text=True,
            encoding='utf-8'
        )
        if result.returncode != 0:
            raise RuntimeError(f"copaw env list failed: {result.stderr}")
        
        for line in result.stdout.split('\n'):
            line = line.strip()
            if line.startswith('GITLAB_TOKEN'):
                token = line.split()[1]
                if token.startswith('glpat-'):
                    return token
                # 如果是加密格式，抛出错误
                raise ValueError(f"Token is encrypted: {token[:20]}...")
        
        raise ValueError("GITLAB_TOKEN not found in copaw env")
    
    except FileNotFoundError:
        # 兜底：从文件读取（用于非 CoPaw 环境）
        if not os.path.exists(SECRET_FILE_PATH):
            raise FileNotFoundError(
                f"凭证文件不存在: {SECRET_FILE_PATH}"
            )
        with open(SECRET_FILE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        token = data.get("GITLAB_TOKEN")
        if not token:
            raise ValueError("GITLAB_TOKEN not found")
        return token


def _parse_repo_url(repo_url: str) -> str:
    """
    将常规 GitLab 仓库 URL 转换为 API 需要的 project_id 格式
    
    Args:
        repo_url: 仓库 URL，如 "https://gitlab.com/user/repo" 或 "user/repo"
        
    Returns:
        str: URL 编码格式，如 "user%2Frepo"
    """
    # 去除末尾斜杠
    repo_url = repo_url.rstrip('/')
    
    # 处理完整 URL
    if "gitlab.com/" in repo_url:
        # 从 URL 中提取 user/repo 部分
        parts = repo_url.split("gitlab.com/")[-1].split("/")
        if len(parts) >= 2:
            project_id = "/".join(parts[:2])
        else:
            project_id = parts[0]
    else:
        # 已经是 user/repo 格式
        project_id = repo_url
    
    # URL 编码：/ -> %2F
    return project_id.replace("/", "%2F")


def _get_web_url(repo_url: str, branch: Optional[str] = None, commit_sha: Optional[str] = None) -> str:
    """
    生成 GitLab Web 界面链接
    
    Args:
        repo_url: 仓库 URL
        branch: 分支名（可选）
        commit_sha: 提交 SHA（可选）
        
    Returns:
        str: Web 链接
    """
    # 提取 user/repo
    if "gitlab.com/" in repo_url:
        repo_path = repo_url.split("gitlab.com/")[-1].rstrip('/')
    else:
        repo_path = repo_url.rstrip('/')
    
    base_url = f"https://gitlab.com/{repo_path}"
    
    if branch:
        return f"{base_url}/-/tree/{branch}"
    elif commit_sha:
        return f"{base_url}/-/commit/{commit_sha}"
    else:
        return base_url


# ==================== 语言和框架检测 ====================

def _detect_language(files: List[str]) -> Optional[str]:
    """
    检测项目语言
    
    Args:
        files: 文件名列表
        
    Returns:
        str: 检测到的语言 (python, node, java, cpp, go, rust, ruby, php, .net)
    """
    for lang, indicators in LANGUAGE_INDICATORS.items():
        for indicator in indicators:
            if indicator in files:
                return lang
    return None


def _detect_framework(files: List[str], code_content: Dict[str, str], language: str) -> Optional[str]:
    """
    检测项目框架
    
    Args:
        files: 文件名列表
        code_content: 代码文件内容字典
        language: 检测到的语言
        
    Returns:
        str: 检测到的框架
    """
    if language not in FRAMEWORK_PATTERNS:
        return None
    
    framework_patterns = FRAMEWORK_PATTERNS[language]
    
    # 首先检查指示文件
    for framework, indicators in framework_patterns.items():
        for indicator in indicators:
            if indicator in files:
                if isinstance(indicator, str) and indicator in ["pom.xml", "build.gradle", "CMakeLists.txt", "Makefile"]:
                    return framework
    
    # 然后检查代码内容
    for framework, patterns in framework_patterns.items():
        for pattern in patterns:
            if isinstance(pattern, str) and re.search(pattern, " ".join(code_content.values()), re.IGNORECASE):
                return framework
    
    return None


def _detect_port(code_content: Dict[str, str], language: str, framework: Optional[str]) -> int:
    """
    检测应用默认端口
    
    Args:
        code_content: 代码文件内容字典
        language: 检测到的语言
        framework: 检测到的框架
        
    Returns:
        int: 默认端口
    """
    all_content = " ".join(code_content.values())
    
    # 端口模式
    port_patterns = [
        r"\.listen\s*\(\s*(\d+)",  # app.listen(port)
        r"port\s*=\s*(\d+)",      # port = 3000
        r"--port\s+(\d+)",         # --port 3000
        r"server\s*\.port\s*[=:]\s*(\d+)",  # server.port = 8080
        r"app\.run\s*\(\s*[^)]*port\s*[=:]?\s*(\d+)",  # app.run(port=5000)
    ]
    
    for pattern in port_patterns:
        match = re.search(pattern, all_content)
        if match:
            return int(match.group(1))
    
    # 默认端口
    if language in DEFAULT_PORTS:
        if framework and framework in DEFAULT_PORTS[language]:
            return DEFAULT_PORTS[language][framework]
        return DEFAULT_PORTS[language].get("default", 8000)
    
    return 8000


def _detect_entry_point(files: List[str], language: str, code_content: Dict[str, str]) -> Optional[str]:
    """
    检测应用入口文件
    
    Args:
        files: 文件名列表
        language: 检测到的语言
        code_content: 代码文件内容字典
        
    Returns:
        str: 入口文件名
    """
    entry_files = ENTRY_FILES.get(language, [])
    
    # 1. 检查 package.json 的 main 字段
    if language == "node" and "package.json" in code_content:
        try:
            pkg = json.loads(code_content["package.json"])
            if pkg.get("main"):
                return pkg["main"]
        except:
            pass
    
    # 2. 检查 setup.py 的 entry_points
    if language == "python" and "setup.py" in code_content:
        match = re.search(r"entry_points\s*=\s*\{([^}]+)\}", code_content["setup.py"])
        if match:
            return "setup.py"
    
    # 3. 检查常见入口文件
    for entry in entry_files:
        if entry in files:
            return entry
    
    # 4. 从代码内容检测 main 函数
    for filename, content in code_content.items():
        if re.search(r"def\s+main\s*\(", content):
            return filename
    
    return entry_files[0] if entry_files else None


# ==================== Dockerfile 模板生成 ====================

def _generate_python_dockerfile(language: str, framework: Optional[str], entry_point: Optional[str], 
                          port: int, dependencies: Dict[str, str]) -> str:
    """生成 Python Dockerfile"""
    
    # 获取依赖
    deps_content = dependencies.get("requirements.txt", "")
    pyproject = dependencies.get("pyproject.toml", "")
    package_json = dependencies.get("package.json", "")
    
    # 确定运行时
    if "python" in pyproject:
        python_version = "3.11"
        match = re.search(r'python\s*[">=]=?\s*["\']?(\d+\.\d+)', pyproject)
        if match:
            python_version = match.group(1)[:4]
    else:
        python_version = "3.11-slim"
    
    # 确定命令
    if framework in ["fastapi", "flask"]:
        cmd = f'gunicorn --bind :{port} --workers 4 --worker-class uvicorn.workers.UvicornWorker app:app'
        entry = entry_point or "app.py"
    elif framework == "django":
        cmd = f'gunicorn --bind :{port} --workers 4 myproject.wsgi:application'
        entry = entry_point or "manage.py runserver 0.0.0.0:{}".format(port)
    else:
        cmd = f'python {entry_point or "main.py"}'
        entry = entry_point or "main.py"
    
    # 检查是否有 requirements.txt
    has_requirements = bool(deps_content.strip())
    
    dockerfile = f'''# Python Dockerfile - Auto-generated
FROM python:{python_version} AS builder

WORKDIR /app

# Install dependencies
COPY requirements.txt* ./
RUN pip install --no-cache-dir -r requirements.txt 2>/dev/null || true

COPY . .

# Build stage
FROM python:{python_version}

WORKDIR /app

COPY --from=builder /app /app

# Install runtime dependencies
RUN pip install --no-cache-dir gunicorn

EXPOSE {port}

ENV PYTHONUNBUFFERED=1

CMD ["sh", "-c", "{cmd}"]
'''
    
    if not has_requirements:
        dockerfile = '''# Python Dockerfile - Auto-generated
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir gunicorn

COPY . .

EXPOSE {port}

ENV PYTHONUNBUFFERED=1

CMD ["python", "{entry}"]
'''.format(port=port, entry=entry_point or "main.py")
    
    return dockerfile


def _generate_node_dockerfile(language: str, framework: Optional[str], entry_point: Optional[str], 
                           port: int, dependencies: Dict[str, str]) -> str:
    """生成 Node.js Dockerfile"""
    
    package_json = dependencies.get("package.json", "{}")
    try:
        pkg = json.loads(package_json)
    except:
        pkg = {}
    
    scripts = pkg.get("scripts", {})
    start_cmd = scripts.get("start", "node index.js")
    
    # 确定 node 版本
    node_version = "20"
    if "engines" in pkg and "node" in pkg["engines"]:
        node_version = re.sub(r'[^\d.]', '', pkg["engines"]["node"])[:2] or "20"
    
    dockerfile = f'''# Node.js Dockerfile - Auto-generated
FROM node:{node_version}-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci --prefer-offline

COPY . .

# Build stage
FROM node:{node_version}-alpine

WORKDIR /app

COPY --from=builder /app/node_modules ./node_modules
COPY . .

EXPOSE {port}

ENV NODE_ENV=production

CMD ["sh", "-c", "{start_cmd}"]
'''
    
    return dockerfile


def _generate_java_dockerfile(language: str, framework: Optional[str], entry_point: Optional[str], 
                            port: int, dependencies: Dict[str, str]) -> str:
    """生成 Java Dockerfile"""
    
    is_gradle = "build.gradle" in dependencies or "build.gradle.kts" in dependencies
    
    dockerfile = f'''# Java Dockerfile - Auto-generated
FROM maven:3.9-ead-21 AS builder

WORKDIR /app

COPY pom.xml .
RUN mvn dependency:go-offline -B

COPY src ./src
RUN mvn package -DskipTests

# Runtime stage
FROM openjdk:21-slim

WORKDIR /app

COPY --from=builder /app/target/*.jar app.jar

EXPOSE {port}

ENV JAVA_OPTS="-Xmx512m -Xms256m"

CMD ["java", "-jar", "app.jar"]
'''
    
    if is_gradle:
        dockerfile = f'''# Java Dockerfile - Auto-generated
FROM gradle:8.5-jdk21 AS builder

WORKDIR /app

COPY build.gradle* ./
RUN gradle build -x test --no-daemon

COPY . .

FROM openjdk:21-slim

WORKDIR /app

COPY --from=builder /app/build/libs/*.jar app.jar

EXPOSE {port}

ENV JAVA_OPTS="-Xmx512m -Xms256m"

CMD ["java", "-jar", "app.jar"]
'''
    
    return dockerfile


def _generate_cpp_dockerfile(language: str, framework: Optional[str], entry_point: Optional[str], 
                            port: int, dependencies: Dict[str, str]) -> str:
    """生成 C++ Dockerfile"""
    
    use_cmake = "CMakeLists.txt" in dependencies
    
    if use_cmake:
        dockerfile = '''# C++ Dockerfile - Auto-generated
FROM gcc:12 AS builder

WORKDIR /app

COPY CMakeLists.txt .
COPY src/ ./src/
RUN mkdir build && cd build && cmake .. && make

# Runtime stage
FROM debian:bookworm-slim

WORKDIR /app

COPY --from=builder /app/build/app .

EXPOSE {port}

CMD ["./app"]
'''.format(port=port)
    else:
        dockerfile = '''# C++ Dockerfile - Auto-generated
FROM gcc:12

WORKDIR /app

COPY *.cpp ./
COPY *.h ./
RUN g++ -o app main.cpp -lstdc++

EXPOSE {port}

CMD ["./app"]
'''.format(port=port)
    
    return dockerfile


def _generate_go_dockerfile(language: str, framework: Optional[str], entry_point: Optional[str], 
                     port: int, dependencies: Dict[str, str]) -> str:
    """生成 Go Dockerfile"""
    
    return f'''# Go Dockerfile - Auto-generated
FROM golang:1.21 AS builder

WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY . .

RUN CGO_ENABLED=0 GOOS=linux go build -o app .

# Runtime stage
FROM gcr.io/distroless/base-debian12

WORKDIR /app

COPY --from=builder /app/app .

EXPOSE {port}

CMD ["./app"]
'''.format(port=port)


# ==================== 主类 ====================

class GitLabDevOps:
    """
    GitLab DevOps 操作类
    
    提供远程仓库分析和原子提交功能，完全通过 GitLab API v4 实现
    """
    
    def __init__(self, token: Optional[str] = None):
        """
        初始化 GitLab DevOps
        
        Args:
            token: GitLab API Token（可选，默认从文件加载）
        """
        self.token = token or _load_gitlab_token()
        if not self.token:
            raise ValueError("GitLab Token 不能为空")
        self.headers = {
            "PRIVATE-TOKEN": self.token,
            "Content-Type": "application/json"
        }
    
    def _request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """
        发送 API 请求
        
        Args:
            method: HTTP 方法
            endpoint: API 端点
            **kwargs: 请求参数
            
        Returns:
            Dict: 响应数据
            
        Raises:
            requests.HTTPError: API 错误
        """
        url = f"{GITLAB_API_BASE}{endpoint}"
        
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=self.headers,
                **kwargs
            )
            response.raise_for_status()
            return response.json() if response.content else {}
        
        except requests.HTTPError as e:
            error_detail = ""
            try:
                error_detail = e.response.json()
            except:
                error_detail = e.response.text
            
            raise requests.HTTPError(
                f"GitLab API 错误 ({e.response.status_code}): {error_detail}"
            )
        except requests.RequestException as e:
            raise requests.RequestException(f"请求失败: {e}")
    
    def get_repository_tree(self, repo_url: str, path: str = "") -> list:  # type: ignore[return-value]
        """
        获取仓库文件树
        
        Args:
            repo_url: 仓库 URL
            path: 目录路径（可选，默认根目录）
            
        Returns:
            list: 文件/目录列表
        """
        project_id = _parse_repo_url(repo_url)
        endpoint = f"/projects/{project_id}/repository/tree"
        
        params: Dict[str, Any] = {"recursive": False}
        if path:
            params["path"] = path
        
        try:
            return self._request("GET", endpoint, params=params)  # type: ignore[return-value]
        except requests.RequestException as e:
            raise requests.RequestException(f"获取文件树失败: {e}")
    
    def get_default_branch(self, repo_url: str) -> str:
        """
        获取仓库默认分支
        
        Args:
            repo_url: 仓库 URL
            
        Returns:
            str: 默认分支名（通常为 main 或 master）
        """
        project_id = _parse_repo_url(repo_url)
        endpoint = f"/projects/{project_id}"
        
        try:
            data = self._request("GET", endpoint)
            return data.get("default_branch", "main")
        except requests.RequestException as e:
            raise requests.RequestException(f"获取默认分支失败: {e}")
    
    def get_file_content(self, repo_url: str, file_path: str, ref: Optional[str] = None) -> str:
        """
        获取文件内容（Base64 编码后解码）
        
        Args:
            repo_url: 仓库 URL
            file_path: 文件路径
            ref: 分支名或提交 SHA（可选）
            
        Returns:
            str: 文件文本内容
        """
        project_id = _parse_repo_url(repo_url)
        endpoint = f"/projects/{project_id}/repository/files/{file_path}"
        
        params = {"ref": ref or "main"}
        
        try:
            data = self._request("GET", endpoint, params=params)
            import base64
            content = data.get("content", "")
            return base64.b64decode(content).decode('utf-8')
        except requests.RequestException as e:
            raise requests.RequestException(f"读取文件失败: {e}")
    
    def atomic_commit(
        self,
        repo_url: str,
        branch: str,
        commit_message: str,
        files: Dict[str, str],
        source_branch: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        原子提交：创建分支并同时添加多个文件
        
        Args:
            repo_url: 仓库 URL
            branch: 目标分支名
            commit_message: 提交信息
            files: 文件字典 {"path": "content"}
            source_branch: 源分支（可选，默认使用仓库默认分支）
            
        Returns:
            Dict: 提交结果
        """
        import base64
        
        project_id = _parse_repo_url(repo_url)
        
        # 获取默认分支作为源分支
        if not source_branch:
            source_branch = self.get_default_branch(repo_url)
        
        # 获取源分支的最新 commit SHA
        endpoint = f"/projects/{project_id}/repository/branches/{source_branch}"
        try:
            branch_data = self._request("GET", endpoint)
            commit_sha = branch_data.get("commit", {}).get("id", "")
        except:
            commit_sha = source_branch
        
        # 构建 actions
        actions = []
        for file_path, content in files.items():
            encoded_content = base64.b64encode(content.encode('utf-8')).decode('utf-8')
            actions.append({
                "action": "create",
                "file_path": file_path,
                "content": encoded_content,
                "encoding": "base64"
            })
        
        # 构建请求数据
        payload = {
            "branch": branch,
            "start_sha": commit_sha,
            "commit_message": commit_message,
            "actions": actions
        }
        
        endpoint = f"/projects/{project_id}/repository/commits"
        
        try:
            result = self._request("POST", endpoint, json=payload)
            return {
                "success": True,
                "message": "原子提交成功",
                "branch": branch,
                "commit_sha": result.get("id", ""),
                "web_url": _get_web_url(repo_url, branch=branch)
            }
        except requests.RequestException as e:
            error_code = None
            if hasattr(e, 'response') and isinstance(e.response, dict):
                error_code = e.response.get("status_code")
            return {
                "success": False,
                "message": f"原子提交失败: {e}",
                "error_code": error_code
            }


# ==================== 统一入口函数 ====================

def execute(action: str, repo_url: str, **kwargs) -> str:
    """
    DevOps GitLab 统一入口函数
    
    Args:
        action: 操作类型 
            - "fetch_context": 拉取仓库上下文
            - "push_artifacts": 原子推送部署文件
            - "analyze_and_generate_dockerfile": 分析代码库并生成 Dockerfile
        repo_url: GitLab 仓库 URL
        **kwargs: 额外参数
            - dockerfile: push_artifacts 需要
            - manual: push_artifacts 需要
            - target_branch: push_artifacts 需要（默认 "feature/agent-auto-deploy"）
            
    Returns:
        str: JSON 格式的结果字符串
    """
    try:
        devops = GitLabDevOps()
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": "凭证加载失败",
            "message": str(e)
        }, ensure_ascii=False, indent=2)
    
    if action == "fetch_context":
        return _execute_fetch_context(devops, repo_url)
    
    elif action == "push_artifacts":
        return _execute_push_artifacts(devops, repo_url, kwargs)
    
    elif action == "analyze_and_generate_dockerfile":
        return _execute_analyze_and_generate_dockerfile(devops, repo_url, kwargs)
    
    else:
        return json.dumps({
            "success": False,
            "error": "未知操作",
            "message": f"不支持的操作: {action}"
        }, ensure_ascii=False, indent=2)


def _execute_fetch_context(devops: GitLabDevOps, repo_url: str) -> str:
    """
    执行 fetch_context: 拉取仓库上下文
    """
    try:
        # 1. 获取文件树
        tree = devops.get_repository_tree(repo_url)
        
        # 2. 提取文件列表
        file_list = []
        for item in tree:
            if item.get("type") == "blob":
                file_list.append(item.get("name"))
        
        # 3. 检查核心依赖文件
        found_dependencies = {}
        for dep_file in CORE_DEPENDENCY_FILES:
            if dep_file in file_list:
                try:
                    content = devops.get_file_content(repo_url, dep_file)
                    found_dependencies[dep_file] = content
                except Exception:
                    pass  # 静默跳过读取失败的文件
        
        # 4. 构建结果
        result = {
            "success": True,
            "repo_url": repo_url,
            "file_count": len(file_list),
            "files": file_list,
            "dependencies": found_dependencies
        }
        
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": "获取上下文失败",
            "message": str(e)
        }, ensure_ascii=False, indent=2)


def _execute_push_artifacts(devops: GitLabDevOps, repo_url: str, kwargs: dict) -> str:
    """
    执行 push_artifacts: 原子推送部���文件
    """
    dockerfile = kwargs.get("dockerfile", "")
    manual = kwargs.get("manual", "")
    target_branch = kwargs.get("target_branch", "feature/agent-auto-deploy")
    
    if not dockerfile:
        return json.dumps({
            "success": False,
            "error": "参数错误",
            "message": "dockerfile 参数不能为空"
        }, ensure_ascii=False, indent=2)
    
    if not manual:
        manual = "# Deployment Manual\n暂无部署文档"
    
    # 构建文件字典
    files = {
        "Dockerfile": dockerfile,
        "DEPLOYMENT.md": manual
    }
    
    commit_message = f"Add deployment artifacts via Copaw Agent"
    
    try:
        result = devops.atomic_commit(
            repo_url=repo_url,
            branch=target_branch,
            commit_message=commit_message,
            files=files
        )
        
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": "推送失败",
            "message": str(e)
        }, ensure_ascii=False, indent=2)


def _execute_analyze_and_generate_dockerfile(devops: GitLabDevOps, repo_url: str, kwargs: dict) -> str:
    """
    执行 analyze_and_generate_dockerfile: 分析代码库并生成可部署的 Dockerfile
    """
    try:
        # 1. 获取文件树
        tree = devops.get_repository_tree(repo_url)
        
        # 2. 提取文件列表
        file_list = []
        for item in tree:
            if item.get("type") == "blob":
                file_list.append(item.get("name"))
        
        # 3. 检测语言
        language = _detect_language(file_list)
        
        # 4. 读取代码文件用于框架检测
        code_files_to_read = []
        for ext in [".py", ".js", ".ts", ".java", ".cpp", ".go", ".rs"]:
            for f in file_list:
                if f.endswith(ext):
                    code_files_to_read.append(f)
        
        # 限制读取数量
        code_files_to_read = code_files_to_read[:10]
        
        code_content = {}
        for f in code_files_to_read:
            try:
                content = devops.get_file_content(repo_url, f)
                code_content[f] = content
            except:
                pass
        
        # 5. 读取依赖文件
        dependencies = {}
        for dep_file in CORE_DEPENDENCY_FILES:
            if dep_file in file_list:
                try:
                    content = devops.get_file_content(repo_url, dep_file)
                    dependencies[dep_file] = content
                except:
                    pass
        
        # 6. 检测框架
        framework = _detect_framework(file_list, code_content, language) if language else None
        
        # 7. 检测端口
        port = _detect_port(code_content, language, framework) if language else 8000
        
        # 8. 检测入口点
        entry_point = _detect_entry_point(file_list, language, code_content) if language else None
        
        # 9. 生成 Dockerfile
        if not language:
            # 默认通用 Dockerfile
            dockerfile = '''# Auto-generated Dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY . .

EXPOSE 8080

CMD ["python", "main.py"]
'''
        elif language == "python":
            dockerfile = _generate_python_dockerfile(language, framework, entry_point, port, dependencies)
        elif language == "node":
            dockerfile = _generate_node_dockerfile(language, framework, entry_point, port, dependencies)
        elif language == "java":
            dockerfile = _generate_java_dockerfile(language, framework, entry_point, port, dependencies)
        elif language == "cpp":
            dockerfile = _generate_cpp_dockerfile(language, framework, entry_point, port, dependencies)
        elif language == "go":
            dockerfile = _generate_go_dockerfile(language, framework, entry_point, port, dependencies)
        else:
            dockerfile = f'''# Auto-generated Dockerfile for {language}
FROM ubuntu:22.04

WORKDIR /app

COPY . .

EXPOSE {port}

CMD ["echo", "Please configure CMD for {language} application"]
'''
        
        # 10. 构建结果
        result = {
            "success": True,
            "repo_url": repo_url,
            "analysis": {
                "language": language,
                "framework": framework,
                "entry_point": entry_point,
                "detected_port": port,
                "file_count": len(file_list),
                "files_analyzed": len(code_content),
                "dependencies_found": list(dependencies.keys())
            },
            "dockerfile": dockerfile
        }
        
        return json.dumps(result, ensure_ascii=False, indent=2)
    
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": "分析失败",
            "message": str(e)
        }, ensure_ascii=False, indent=2)
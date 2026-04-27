---
name: devops-gitlab
description: |
  GitLab DevOps automation tool for Copaw Agent. Provides remote code analysis and atomic deployment via GitLab API v4 without any local git operations. Use this skill whenever you need to analyze a GitLab repository's codebase structure, detect programming language (Python, Node.js, Java, C++, Go), detect framework (Flask, Django, FastAPI, Express, Next.js, Spring Boot), generate deployable Dockerfile automatically, read dependency files (package.json, requirements.txt, pom.xml, go.mod, pyproject.toml), or push deployment artifacts (Dockerfile, DEPLOYMENT.md) as atomic commits to a new branch. This skill is REQUIRED for any GitLab-related DevOps tasks including repository context fetching, automated deployments, Dockerfile generation, and CI/CD artifact management.
compatibility: Python 3.8+, requests library
---

# DevOps GitLab Skill

This skill provides GitLab repository analysis and atomic deployment capabilities without using local git commands.

## When to Use

Use this skill when:
- User asks to analyze or fetch context from a GitLab repository
- User needs to read dependency files (package.json, requirements.txt, etc.)
- User wants to push deployment files (Dockerfile, docs) to GitLab
- User asks to generate Dockerfile for a project
- User mentions GitLab automation, remote repository operations, or CI/CD
- You need to get repository file tree or dependency information

## Input/Output Formats

### Entry Point

```python
execute(action, repo_url, **kwargs) -> str
```

### Actions

**Action 1: `fetch_context`**
- Input: `repo_url` (e.g., "https://gitlab.com/username/repo")
- Output: JSON string with file list and key dependency file contents

**Action 2: `push_artifacts`**
- Input: `repo_url`, `dockerfile`, `manual`, `target_branch` (optional, default: "feature/agent-auto-deploy")
- Output: JSON string with commit result and web URL

**Action 3: `analyze_and_generate_dockerfile`** (NEW!)
- Input: `repo_url` (optional extra params)
- Output: JSON string with analysis results and auto-generated Dockerfile

### Action 3 Output Structure

```json
{
  "success": true,
  "repo_url": "https://gitlab.com/user/repo",
  "analysis": {
    "language": "python",
    "framework": "fastapi",
    "entry_point": "main.py",
    "detected_port": 8000,
    "file_count": 50,
    "files_analyzed": 8,
    "dependencies_found": ["requirements.txt", "pyproject.toml"]
  },
  "dockerfile": "FROM python:3.11-slim..."
}
```

## Usage Examples

```python
# Fetch repository context
result = execute("fetch_context", "https://gitlab.com/myuser/myproject")
print(result)

# Push deployment artifacts
result = execute(
    "push_artifacts",
    "https://gitlab.com/myuser/myproject",
    dockerfile="FROM python:3.9\nRUN pip install ...",
    manual="# Deployment Guide\nSteps to deploy...",
    target_branch="feature/deploy-v2"
)
print(result)

# Analyze and generate Dockerfile (auto-detect language/framework/port)
result = execute("analyze_and_generate_dockerfile", "https://gitlab.com/myuser/myproject")
print(result)
```

## Auto-Detection Features

The skill automatically detects:

| Language | Indicators | Default Port |
|----------|-----------|--------------|
| Python | requirements.txt, pyproject.toml | 8000 (FastAPI), 5000 (Flask) |
| Node.js | package.json | 3000 |
| Java | pom.xml, build.gradle | 8080 |
| C++ | CMakeLists.txt, Makefile | 8080 |
| Go | go.mod | 8080 |

| Framework | Detection |
|-----------|-----------|
| FastAPI | from fastapi import |
| Flask | from flask import |
| Django | manage.py, django.conf |
| Express | require('express') |
| Next.js | from 'next' |
| Spring Boot | @SpringBootApplication |

## Implementation Details

The skill includes a bundled Python script at `scripts/gitlab_devops.py` containing:

1. **GitLabDevOps Class** - OOP封装所有GitLab交互逻辑
2. **execute函数** - 统一入口
3. **检测函数**:
   - `_detect_language()` - 基于指示文件检测语言
   - `_detect_framework()` - 基于代码模式检测框架
   - `_detect_port()` - 从代码中提取端口
   - `_detect_entry_point()` - 检测入口文件
4. **Dockerfile 模板**:
   - `_generate_python_dockerfile()` - Python (Flask/Django/FastAPI)
   - `_generate_node_dockerfile()` - Node.js (Express/Next.js)
   - `_generate_java_dockerfile()` - Java (Spring Boot)
   - `_generate_cpp_dockerfile()` - C++ (CMake/Make)
   - `_generate_go_dockerfile()` - Go

### Core Features

- **No local git**: 完全使用GitLab REST API v4
- **Error handling**: 所有API调用都有try-except保护
- **Token security**: 凭证从本地加密文件读取
- **Auto-detection**: 自动检测语言/框架/端口/入口点
- **Multi-stage Dockerfiles**: 生产级优化 Dockerfile

### Environment Requirements

- Python 3.8+
- requests library (`pip install requests`)
- GitLab token in `C:\Users\dong\.copaw.secret\envs.json`

## Files

- `scripts/gitlab_devops.py` - Main implementation (load this to execute)
- `references/` - Additional documentation (if needed)

## Important Notes

1. Always use the bundled script - do not write your own GitLab API code
2. Token path is fixed: `C:\Users\dong\.copaw.secret\envs.json`
3. API base: `https://gitlab.com/api/v4`
4. project_id needs URL encoding: `user%2Frepo` format
5. Atomic commit creates branch + files in one request
6. analyze_and_generate_dockerfile returns ready-to-use Dockerfile
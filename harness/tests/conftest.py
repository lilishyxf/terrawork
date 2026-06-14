"""Pytest configuration for harness tests.

启动时加载项目根 .env(若存在),让 LLM provider key 自动可用。
生产代码不强依赖 .env;只有测试入口在此显式加载。
"""
from pathlib import Path
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

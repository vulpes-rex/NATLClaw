#!/usr/bin/env python
from setuptools import setup, find_packages

PY_MODULES = [
    "agent_setup",
    "api_server",
    "brain_index",
    "cli",
    "config",
    "copilot_auth",
    "daily_digest",
    "decision_engine",
    "dump_brain",
    "error_classification",
    "event_config",
    "event_watcher",
    "execution_log",
    "goals",
    "ingest",
    "learning",
    "messaging",
    "metrics",
    "operator_status",
    "persona_loader",
    "poc_smoke",
    "project_context",
    "prompts",
    "scheduler",
    "scheduler_control",
    "second_brain",
    "state",
    "tasks",
    "telemetry",
    "workflow",
]

setup(
    name="natlclaw",
    version="0.1.0",
    description="NATLClaw - Autonomous Second-Brain Agent",
    long_description="A framework for building autonomous agents with memory and learning capabilities.",
    author="Your Name",
    author_email="your.email@example.com",
    url="https://github.com/yourusername/natlclaw",
    packages=find_packages(),
    py_modules=PY_MODULES,
    include_package_data=True,
    install_requires=[
        "agent-framework==1.0.0",
        "python-dotenv>=1.0.0",
        "sentry-sdk[fastapi]>=2.0.0",
        # Add any additional dependencies here
    ],
    entry_points={
        "console_scripts": [
            "natl=cli:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.8",
)
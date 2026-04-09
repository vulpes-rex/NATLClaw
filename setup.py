#!/usr/bin/env python
from setuptools import setup, find_packages

setup(
    name="natlclaw",
    version="0.1.0",
    description="NATLClaw - Autonomous Second-Brain Agent",
    long_description="A framework for building autonomous agents with memory and learning capabilities.",
    author="Your Name",
    author_email="your.email@example.com",
    url="https://github.com/yourusername/natlclaw",
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        "agent-framework==1.0.0",
        "python-dotenv>=1.0.0",
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
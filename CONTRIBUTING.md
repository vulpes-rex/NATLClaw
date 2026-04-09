# Contributing to NATLClaw

We welcome contributions! Here are some guidelines to help you get started.

## Development Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/natlclaw.git
   cd natlclaw
   ```

2. **Install dependencies**
   ```bash
   pip install -e .
   pip install -r requirements.txt
   ```

3. **Install development tools (optional)**
   ```bash
   pip install pytest pytest-cov ruff mypy black
   ```

## Coding Style

- Follow PEP 8 style guide
- Use black for code formatting (included in requirements-dev.txt if needed)
- Use ruff for linting
- Use mypy for type checking (if needed)
- Write type hints for all functions and methods

## Testing

- Write unit tests for new functionality
- Ensure all tests pass before submitting a PR
- Tests should cover edge cases and error handling

## Documentation

- Update docstrings for new functions and classes
- Add comments for complex logic
- Update README if introducing new features or commands

## Pull Request Process

1. Ensure any install or build dependencies are removed before committing
2. Update the README.md with details of changes if needed
3. Increase version number in setup.py if appropriate
4. Ensure all tests pass
5. Code will be reviewed for adherence to style guidelines and architecture

## Contact

Join our Discord server or open an issue for questions.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
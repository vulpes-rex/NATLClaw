# NATLClaw - Autonomous Second-Brain Agent

NATLClaw is a framework for building autonomous agents with memory, learning, and goal-tracking capabilities. It implements a "second brain" architecture inspired by Tiago Forte's PARA method, allowing agents to capture insights, connect knowledge, and maintain context across interactions.

## Key Features

- **Second Brain Memory**: Persistent knowledge storage with atomic notes and topic connections
- **Multiple Workflow Modes**: `second_brain` (knowledge capture), `freeform` (action-oriented), `steps` (customizable), and `coordinator` (multi-agent orchestration)
- **Goal Tracking**: Set, track, and evaluate progress toward objectives
- **Learning & Reflection**: Automatic lesson extraction from agent interactions
- **Health Checks**: Linting and quality assurance for knowledge base
- **Interactive Chat**: Conversational interface with memory and context

## Installation

### From Source

```bash
# Clone the repository
git clone https://github.com/yourusername/natlclaw.git
cd natlclaw

# Install dependencies
pip install -e .

# Run the CLI
python -m natlclaw.cli --help
# or after installation:
natl --help
```

### Dependencies

- Python 3.8+
- `agent-framework==1.0.0`
- `python-dotenv>=1.0.0`

## Usage

### CLI Commands

```bash
# Start the heartbeat scheduler (continuous operation)
natl run

# Run a single heartbeat and exit
natl run --once

# Start an interactive chat session
natl chat

# Brain management
natl brain stats          # Show statistics
natl brain search "React" # Search notes
natl brain add "Insight"  # Add a note
natl brain export -o brain.md  # Export to markdown
natl brain lint           # Run health check

# Persona management
natl persona list         # List available personas

# Configuration
natl config show          # Show resolved config
natl config validate      # Validate configuration
```

### Chat Commands

Once in the chat interface, use these commands:

- `/exit` or `/quit` - Exit the chat
- `/clear` - Clear conversation history
- `/brain` - Show knowledge base summary
- `/goals` - Show active goals
- `/help` - Show this help
- `/add <text>` - Manually add a note to the brain
- Type normally to chat with the agent

## Configuration

NATLClaw uses a `.env` file (default) or environment variables for configuration. See the `.env.example` file for available settings.

Key configuration options include:
- `AGENT_NAME` - Name of the autonomous agent
- `MODEL` - LLM model to use (for supported providers)
- `PROVIDER` - Agent framework provider (`copilot`, `foundry`, `openai`, `ollama`)
- `PERSONA` - Default persona to use
- `STATE_FILE` - Path to state file (default: `./state.json`)
- `MAX_HISTORY` - Maximum execution history to keep

## Project Structure

```
NATLClaw/
├── natlclaw/           # Python package
│   ├── cli.py         # Main CLI entry point
│   ├── config.py      # Configuration loading/validation
│   ├── second_brain.py # Knowledge management
│   ├── workflow.py    # Heartbeat workflows
│   ├── state.py       # Agent state management
│   ├── persona_loader.py # Persona loading
│   ├── agent_setup.py  # Agent creation
│   ├── learning.py     # Lesson extraction
│   ├── goals.py        # Goal tracking
│   ├── scheduler.py    # Heartbeat scheduler
│   ├── prompts/        # Prompt templates
│   └── personas/       # Persona definitions
├── data/              # Persistent data (brain.json, state.json)
├── tests/             # Unit and integration tests
└── docs/             # Documentation
```

## Development

### Running Tests

```bash
# Run all tests
pytest tests/

# Run specific tests
pytest tests/unit/test_second_brain.py -v
```

### Linting

```bash
# Check code quality
ruff .
```

### Type Checking

```bash
mypy .  # if mypy is installed
```

## Personas

Personas define the agent's role, instructions, and available tools. They are configured in `mcp.json`. Available personas include:

- **default** - General-purpose assistant
- **python_developer** - Python coding specialist
- **react_developer** - React frontend expert
- **project_manager** - Project coordination assistant
- **devops_engineer** - DevOps and infrastructure specialist
- **researcher** - Research and analysis expert

## How It Works

### Heartbeat Cycle (Second Brain Mode)

1. **Status Check** - Assess system and knowledge base
2. **Capture** - Create structured JSON note from insights
3. **Connect** - Discover relationships between recent notes
4. **Review** - Synthesize the cycle and identify gaps

Every 10th heartbeat includes a lint check to maintain knowledge quality.

### Memory Model

- **Short-term**: Atomic notes (single observations, errors, insights)
- **Long-term**: Topic graph with connections between notes
- **Context**: Recent execution history and lessons learned

### Knowledge Graph

Notes are tagged with topics, creating a graph where:
- Topics connect related notes
- Co-occurring tags create topic relationships
- Connections link related notes bidirectionally

## Contributing

1. Fork the repository
2. Create a feature branch
3. Implement your changes with tests
4. Ensure code follows existing style and patterns
5. Submit a pull request

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## License

MIT License. See [LICENSE](LICENSE) for details.

## Acknowledgments

- Inspired by Tiago Forte's PARA method and Building a Second Brain
- Influenced by MemGPT / Letta memory hierarchy
- Incorporates concepts from Karpathy's LLM Knowledge Base
- Uses Agent Framework for agent infrastructure
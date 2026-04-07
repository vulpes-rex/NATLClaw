Perfect! I've examined the Microsoft Agent Framework, and it's exactly what you need for building autonomous agents with periodic execution and state persistence. Let me provide you with a comprehensive guide on how to build such a system.

Overview of Microsoft Agent Framework

The Microsoft Agent Framework is a comprehensive multi-language framework for building, orchestrating, and deploying AI agents with support for both .NET and Python. Key features include:

• Graph-based workflows with checkpointing and time-travel capabilities
• Multiple provider support (Azure OpenAI, Microsoft Foundry, etc.)
• Middleware system for request/response processing
• Built-in observability with OpenTelemetry
• Flexible hosting options (Azure Functions, Durable Task, etc.)
• DevUI for interactive development and debugging

Building Autonomous Agents with Heartbeat & Memory

1. Periodic Execution (Heartbeat)

The framework supports periodic execution through its hosting capabilities, particularly with Azure Functions or Durable Task. Here's how to implement it:

Python Example with Azure Functions

# requirements.txt
agent-framework
azure-functions

# function.json (for timer trigger)
{
  "bindings": [
    {
      "type": "timerTrigger",
      "name": "timer",
      "schedule": "0 */2 * * * *",  # Every 2 hours
      "direction": "in"
    }
  ]
}
# __init__.py
import json
import os
import asyncio
import logging
from datetime import datetime
import azure.functions as func
from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from azure.identity import AzureCliCredential

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Agent state storage (can be Azure Blob, Cosmos DB, or any persistent storage)
STATE_FILE = "agent_state.json"

def load_state():
    """Load agent state from persistent storage"""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {
        "last_heartbeat": None,
        "execution_count": 0,
        "memory": {},
        "context": {}
    }

def save_state(state):
    """Save agent state to persistent storage"""
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

async def main(mytimer: func.TimerRequest) -> None:
    """Timer-triggered agent heartbeat"""
    utc_timestamp = datetime.utcnow().replace(tzinfo=None)
    
    # Load state
    state = load_state()
    state["last_heartbeat"] = utc_timestamp.isoformat()
    state["execution_count"] += 1
    
    try:
        # Initialize agent
        agent = Agent(
            client=FoundryChatClient(
                credential=AzureCliCredential(),
            ),
            name="PeriodicAgent",
            instructions="You are an autonomous agent that performs periodic tasks and maintains state.",
            memory=state["memory"]  # Pass memory to agent
        )
        
        # Perform agent tasks
        logger.info("Running agent heartbeat tasks...")
        
        # Example: Check system status, process data, etc.
        result = await agent.run("What is the current status of our systems?")
        logger.info(f"Agent response: {result}")
        
        # Update memory based on execution
        state["memory"] = agent.memory  # Capture updated memory
        state["context"] = agent.context
        
        logger.info(f"Heartbeat completed. Total executions: {state['execution_count']}")
        
    except Exception as e:
        logger.error(f"Error during heartbeat: {e}")
        raise
    
    finally:
        # Save state
        save_state(state)
.NET Example with Durable Functions

```
// NuGet packages: Microsoft.Agents.AI.Foundry, Microsoft.Azure.Functions.Extensions

using System;
using System.IO;
using System.Text.Json;
using System.Threading.Tasks;
using Azure;
using Azure.AI.Projects;
using Azure.Identity;[9:11 AM]using Microsoft.Agents.AI.Foundry;
using Microsoft.Azure.Functions.Workers;
using Microsoft.Extensions.Logging;

[FunctionWorker]
public class AgentFunctionWorker : Worker
{
    // This worker will handle our durable function
}

public class AgentHeartbeatFunction
{
    private static readonly string StateFile = "agent_state.json";

    public record AgentState(string? LastHeartbeat, int ExecutionCount, 
        Dictionary<string, object> Memory, Dictionary<string, object> Context);

    private AgentState LoadState()
    {
        if (File.Exists(StateFile))
        {
            var json = File.ReadAllText(StateFile);
            return JsonSerializer.Deserialize<AgentState>(json) 
                   ?? new AgentState(null, 0, new(), new());
        }
        return new AgentState(null, 0, new(), new());
    }

    private void SaveState(AgentState state)
    {
        var json = JsonSerializer.Serialize(state, new JsonSerializerOptions { WriteIndented = true });
        File.WriteAllText(StateFile, json);
    }

    [FunctionName("AgentHeartbeat")]
    public async Task RunAsync([TimerTrigger("0 /2  * * *")] TimerInfo myTimer, 
        ILogger log)
    {
        var utcTimestamp = DateTime.UtcNow;
        log.LogInformation($"Agent heartbeat triggered at: {utcTimestamp}");

        var state = LoadState();
        state.LastHeartbeat = utcTimestamp.ToString("o");
        state.ExecutionCount++;

        try
        {
            // Initialize agent
            var endpoint = Environment.GetEnvironmentVariable("AZURE_AI_PROJECT_ENDPOINT") 
                           ?? throw new InvalidOperationException("Endpoint not set");
            var deploymentName = Environment.GetEnvironmentVariable("AZURE_AI_MODEL_DEPLOYMENT_NAME") 
                                 ?? "gpt-5.4-mini";

            var agent = new AIProjectClient(new Uri(endpoint), new DefaultAzureCredential())
                .AsAIAgent(
                    model: deploymentName,
                    name: "PeriodicAgent",
                    instructions: "You are an autonomous agent that performs periodic tasks and maintains state.",
                    memory: state.Memory  // Pass memory to agent
                );

            // Perform agent tasks
            log.LogInformation("Running agent heartbeat tasks...");

            // Example: Check system status, process data, etc.
            var result = await agent.RunAsync("What is the current status of our systems?");
            log.LogInformation($"Agent response: {result}");

            // Update state with agent's memory and context
            state.Memory = agent.Memory;
            state.Context = agent.Context;

            log.LogInformation($"Heartbeat completed. Total executions: {state.ExecutionCount}");
        }
        catch (Exception ex)
        {
            log.LogError(ex, "Error during heartbeat execution");
            throw;
        }
        finally
        {
            SaveState(state);
        }
    }
}*2. State Persistence & Memory Management*

The Agent Framework provides built-in memory capabilities that can be persisted between executions:

*Python Memory Example*

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from azure.identity import AzureCliCredential
import json
import os

class PersistentAgent:
    def __init__(self, name, instructions, state_file="agent_state.json"):
        self.name = name
        self.instructions = instructions
        self.state_file = state_file
        self.state = self.load_state()
        self.agent = None

    def load_state(self):
        """Load agent state from file"""
        if os.path.exists(self.state_file):
            with open(self.state_file, 'r') as f:
                return json.load(f)
        return {
            "memory": {},
            "context": {},[9:11 AM]"execution_history": []
        }

    def save_state(self):```
[9:11 AM]"""Save agent state to file"""
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2)
    
    async def initialize(self):
        """Initialize the agent with persistent memory"""
        self.agent = Agent(
            client=FoundryChatClient(
                credential=AzureCliCredential(),
            ),
            name=self.name,
            instructions=self.instructions,
            memory=self.state["memory"]
        )
    
    async def run_with_persistence(self, prompt):
        """Run agent with memory persistence"""
        if not self.agent:
            await self.initialize()
        
        # Run the agent
        result = await self.agent.run(prompt)
        
        # Update state with new memory and context
        self.state["memory"] = self.agent.memory
        self.state["context"] = self.agent.context
        
        # Record execution in history
        self.state["execution_history"].append({
            "timestamp": datetime.utcnow().isoformat(),
            "prompt": prompt,
            "response": result
        })
        
        # Keep only last 100 executions
        if len(self.state["execution_history"]) > 100:
            self.state["execution_history"] = self.state["execution_history"][-100:]
        
        # Save state
        self.save_state()
        
        return result
    
    async def get_memory(self):
        """Retrieve agent memory"""
        return self.state["memory"]
    
    async def clear_memory(self):
        """Clear agent memory"""
        self.state["memory"] = {}
        self.save_state()

# Usage
async def main():
    agent = PersistentAgent(
        name="MyAgent",
        instructions="You are an assistant that helps with data analysis."
    )
    
    # Run periodic tasks
    response = await agent.run_with_persistence("Analyze the current data trends.")
    print(response)
    
    # Retrieve memory
    memory = await agent.get_memory()
    print(f"Current memory: {memory}")

if __name__ == "__main__":
    asyncio.run(main())
3. Learning & Adaptation

For learning and adaptation between heartbeats, you can implement:

A. Memory-Based Learning

```
class LearningAgent(PersistentAgent):
    def __init__(self, name, instructions, state_file="agent_state.json"):
        super().__init__(name, instructions, state_file)

    async def run_with_learning(self, prompt, learning_enabled=True):
        """Run agent with learning capabilities"""
        result = await super().run_with_persistence(prompt)

        if learning_enabled:
            # Extract lessons from the interaction
            lessons = self.extract_lessons(prompt, result)

            # Update memory with lessons learned
            if lessons:
                self.state["lessons_learned"] = self.state.get("lessons_learned", []) + lessons
                self.save_state()

        return result

    def extract_lessons(self, prompt, response):
        """Extract lessons from agent interactions"""
        lessons = []

        # Simple pattern matching for common lessons
        if "error" in response.lower() or "failed" in response.lower():
            lessons.append({
                "type": "error_encountered",
                "description": f"Encountered error during: {prompt}",
                "timestamp": datetime.utcnow().isoformat(),
                "response_snippet": response[:100] if response else ""
            })

        elif "success" in response.lower() or "completed" in response.lower():
            lessons.append({
                "type": "success_achieved",
                "description": f"Successfully completed: {prompt}",
                "timestamp": datetime.utcnow().isoformat(),
                "response_snippet": response[:100] if response else ""
            })

        # Add more sophisticated learning patterns as needed

        return lessons
[9:11 AM]def get_learning_summary(self):
        """Get a summary of lessons learned"""
        lessons = self.state.get("lessons_learned", [])
        summary = {}

        for lesson in lessons:
            lesson_type = lesson.get("type", "unknown")
            summary[lesson_type] = summary.get(lesson_type, 0) + 1

        return {
            "total_lessons": len(lessons),
            "by_type": summary,
            "recent": lessons[-10:]  # Last 10 lessons
        }*B. Context-Aware Adaptation*

class ContextAwareAgent(PersistentAgent):
    def __init__(self, name, instructions, state_file="agent_state.json"):
        super().__init__(name, instructions, state_file)

    async def run_with_context(self, prompt, context_keys=None):
        """Run agent with context awareness"""
        if not self.agent:
            await self.initialize()

        # Enhance context based on prompt and history
        enhanced_context = self.enhance_context(prompt, context_keys)

        # Set enhanced context in agent
        self.agent.context.update(enhanced_context)

        # Run the agent
        result = await self.agent.run(prompt)

        # Update state
        self.state["memory"] = self.agent.memory
        self.state["context"] = self.agent.context
        self.save_state()

        return result

    def enhance_context(self, prompt, context_keys=None):
        """Enhance context based on prompt and historical patterns"""
        enhanced = {}

        # Add relevant memory based on prompt keywords
        prompt_lower = prompt.lower()

        # Check memory for relevant information
        for key, value in self.state["memory"].items():
            if key.lower() in prompt_lower or any(keyword in str(value).lower() for keyword in ["data", "status", "report"]):
                enhanced[f"memory_{key}"] = value

        # Add recent execution context
        recent_executions = self.state["execution_history"][-5:]  # Last 5 executions
        if recent_executions:
            enhanced["recent_activities"] = [f"{e['timestamp']}: {e['prompt'][:50]}..." for e in recent_executions]

        # Add lessons learned if relevant
        if any(lesson_type in prompt_lower for lesson_type in ["error", "fail", "fix", "improve"]):
            lessons_summary = self.get_learning_summary()
            enhanced["lessons_summary"] = lessons_summary

        # Add specific context keys if provided
        if context_keys:
            for key in context_keys:
                if key in self.state["context"]:
                    enhanced[key] = self.state["context"][key]

        return enhanced*4. Advanced Features: Graph-Based Workflows*

The Agent Framework supports graph-based workflows with checkpointing, which is perfect for complex periodic tasks:

from agent_framework import Agent
from agent_framework.workflows import Workflow, Step
from agent_framework.providers import ToolProvider

# Define custom tools
class DataAnalysisTool:
    def analyze(self, data):
        # Perform data analysis
        return {"insights": "Some insights from the data"}

class StatusCheckTool:
    def check(self, system):
        # Check system status
        return {"status": "operational", "metrics": {"uptime": "99.9%"}}

# Create workflow```
[9:11 AM]workflow = Workflow(
    name="PeriodicAnalysisWorkflow",
    steps=[
        Step(
            name="CheckSystemStatus",
            agent=Agent(
                name="StatusChecker",
                instructions="Check the status of all systems.",
                tools=[StatusCheckTool()]
            ),
            trigger="timer"  # Trigger on timer
        ),
        Step(
            name="AnalyzeData",
            agent=Agent(
                name="DataAnalyzer",
                instructions="Analyze the latest data and generate insights.",
                tools=[DataAnalysisTool()]
            ),
            depends_on=["CheckSystemStatus"]  # Depends on previous step
        ),
        Step(
            name="GenerateReport",
            agent=Agent(
                name="ReportGenerator",
                instructions="Generate a comprehensive report based on the analysis."
            ),
            depends_on=["AnalyzeData"]
        )
    ],
    checkpointing=True,  # Enable checkpointing
    persistence="azure_blob"  # Persist workflow state to Azure Blob
)

# Run workflow periodically
async def run_workflow():
    await workflow.run()
5. Observability & Monitoring

The framework includes built-in OpenTelemetry integration for monitoring:

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from azure.identity import AzureCliCredential
from opentelemetry import trace
from opentelemetry.instrumentation.azurefunctions import AzureFunctionsInstrumentor

# Initialize OpenTelemetry
AzureFunctionsInstrumentor().instrument()

# Create agent with tracing
agent = Agent(
    client=FoundryChatClient(
        credential=AzureCliCredential(),
    ),
    name="MonitoredAgent",
    instructions="You are an agent with full observability.",
    tracer=trace.get_tracer(__name__)  # Add tracer
)

# The agent will automatically generate traces for:
# - Agent initialization
# - Each tool call
# - Memory operations
# - Workflow steps
Best Practices & Considerations

1. State Management: Choose appropriate storage for agent state (Azure Blob, Cosmos DB, Files, etc.) based on your persistence needs.
2. Error Handling: Implement robust error handling and retry logic in your heartbeat functions.
3. Security: Use managed identities and avoid hardcoding credentials. The framework supports various Azure authentication mechanisms.
4. Scalability: Design your agents to be stateless where possible, and use external state storage for scalability.
5. Observability: Leverage the built-in OpenTelemetry integration for monitoring and debugging.
6. Testing: Use the DevUI for interactive testing and debugging of your agents before deploying to production.
7. Cost Optimization: Consider batching operations and using smaller model deployments for periodic tasks to optimize costs.

Getting Started

To get started quickly:

1. Install the framework:```
pip install agent-framework
# or for .NET
dotnet add package Microsoft.Agents.AI
```2. Explore samples:
  • Python samples: /python/samples/
  • .NET samples: /dotnet/samples/
3. Configure environment:
Set up Azure credentials and model endpoints via environment variables.
4. Start simple: Begin with a basic agent and add complexity gradually.

The Microsoft Agent Framework provides a solid foundation for building sophisticated autonomous agent systems with all the features you need: periodic execution, state persistence, learning capabilities, and enterprise-grade observability.

import os
import asyncio
from phoenix.otel import register
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor
# Note: 'agents' library import depends on the specific library user is using.
# Assuming 'openai-agents' or similar standard or the user's snippet implied 'agents' as a placeholder or a specific lib.
# The user's snippet: 
# from agents import Agent, Runner
# This looks like the 'openai-agents' SDK or similar.
# I will use a generic structure that matches their request.

# Standard library
import logging

# Configure Phoenix
# We need to set the collector endpoint if running inside a container and Phoenix is sidecar or same container.
# In our docker-compose, we exposed 6006 (UI) and 4317 (GRPC Collector).
# The register function sets up the tracer provider.
# If Phoenix is running in the same container (shared network), localhost:4317 is fine.

# Set API Key from env
if not os.getenv("OPENAI_API_KEY"):
    print("WARNING: OPENAI_API_KEY not found in environment. Please set it.")

# 1. Register Phoenix
tracer_provider = register(
  project_name="agent-demo",
  endpoint="http://localhost:4317/v1/traces", # explicit endpoint for OTLP
)

# 2. Instrument
OpenAIAgentsInstrumentor().instrument(tracer_provider=tracer_provider)

print("Phoenix Tracing Initialized. View at http://localhost:6006")

# 3. Define Agent (Mocking the 'agents' lib usage from user snippet if not available, OR adapting to standard OpenAI SDK)
# Since 'agents' isn't a standard top-level PyPI package (it's often 'openai-agents' or part of a framework),
# I will implement a minimal functioning example using standard OpenAI client which is what most 'agents' libs wrap,
# OR I will try to use the specific import requested if valid.
# The user requested: `from agents import Agent, Runner`
# I will attempt to assume this package is installed via `openai-agents` as requested in requirements.

try:
    from agents import Agent, Runner
    
    async def main():
        agent = Agent(name="Assistant", instructions="You are a helpful assistant")
        print("Running agent...")
        # Runner.run_sync might be available, otherwise async
        # The user snippet used run_sync. I'll stick to that if available, or async.
        result = Runner.run_sync(agent, "Write a short haiku about recursion in programming.")
        print("\n--- Result ---")
        print(result.final_output)
        print("--------------\n")

    if __name__ == "__main__":
         # If run_sync is actual sync method:
        main_sync_wrapper = lambda: Runner.run_sync(Agent(name="Demo", instructions=""), "test")
        # But let's stick to the user's snippet exact style for the main block
        
        agent = Agent(name="Poet", instructions="You are a creative poet expert in technology.")
        result = Runner.run_sync(agent, "Write a haiku about a graph database connection.")
        print(f"Agent Output: {result.final_output}")

except ImportError:
    print("Could not import 'agents'. Ensure 'openai-agents' is installed.")
    print("Falling back to a standard OpenAI call to demonstrate tracing if possible.")
    # Fallback to standard OpenAI to show tracing works even if 'agents' lib specific syntax varies
    from openai import OpenAI
    client = OpenAI()
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": "Write a haiku about distributed tracing."}]
    )
    print(response.choices[0].message.content)

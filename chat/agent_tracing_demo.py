
import os
import asyncio
from agents import Agent, Runner, trace, set_tracing_export_api_key

# Ensure tracing is enabled and key is set
# By default, SDK attempts to send traces to OpenAI backend using OPENAI_API_KEY.
# If using a separate key for tracing vs models, use set_tracing_export_api_key.
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    print("WARNING: OPENAI_API_KEY not set. Tracing might fail or strictly local.")
else:
    print("OPENAI_API_KEY found. Tracing enabled to OpenAI Dashboard.")

async def main():
    # Define an agent
    agent = Agent(
        name="TracingDemoAgent", 
        instructions="You are a helpful assistant demonstrating native tracing."
    )

    print("Starting traced workflow...")
    
    # Wrap workflow in a trace context
    with trace("Demo Workflow"): 
        # 1. Simple Trace
        print("Step 1: Asking for a haiku...")
        result1 = await Runner.run(agent, "Write a haiku about observability.")
        print(f"Agent: {result1.final_output}")
        
        # 2. Nested logic or 2nd step
        print("Step 2: Asking for analysis...")
        result2 = await Runner.run(agent, f"Explain the meaning of: '{result1.final_output}'")
        print(f"Agent: {result2.final_output}")

    print("\nWorkflow complete. Check your OpenAI Traces Dashboard.")

if __name__ == "__main__":
    asyncio.run(main())

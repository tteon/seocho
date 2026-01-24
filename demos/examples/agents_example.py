
from agents import Agent

# 1. Data Support Agent
# Responsible for fetching raw data or schema info.
data_agent = Agent(
    name="DataSupport",
    instructions="""
    You are the Data Support specialist.
    Your capabilities:
    1. Retrieve database schemas.
    2. Check data availability.
    
    If you don't have the answer, say "I need to check the database".
    """
)

# 2. Researcher Agent
# Responsible for analysis and synthesizing information.
research_agent = Agent(
    name="Researcher",
    instructions="""
    You are a Senior Researcher.
    Your goal is to analyze information provided by the DataSupport agent.
    You can synthesize new insights but rely on facts.
    """
)

# 3. Manager Agent (Router)
# The entry point for the user. Handsoff to others as needed.
manager_agent = Agent(
    name="Manager",
    instructions="""
    You are the Manager of this research team.
    Your goal is to answer the user's query by coordinating with your team.
    
    Rules:
    - If the user asks about data or schemas, handoff to 'DataSupport'.
    - If the user asks for analysis or summary, handoff to 'Researcher'.
    - Always answer the user nicely after getting info from your team.
    """,
    handoffs=[data_agent, research_agent]
)

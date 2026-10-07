"""SQL Analyst Agent: natural-language questions -> safe SQL -> grounded answers."""

from sql_agent.agent import AgentResult, SQLAgent
from sql_agent.config import Settings

__all__ = ["AgentResult", "SQLAgent", "Settings"]
__version__ = "0.1.0"

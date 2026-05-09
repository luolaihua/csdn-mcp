"""Bridge: 让 Hermes 以模块方式运行 CSDN MCP Server"""
import sys
sys.path.insert(0, "/home/laihluo/csdn-mcp")
from server import mcp
mcp.run()

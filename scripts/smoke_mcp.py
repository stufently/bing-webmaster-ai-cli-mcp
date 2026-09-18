"""Perform a real MCP stdio initialize and tools/list exchange."""

from __future__ import annotations

import sys

import anyio
from mcp import ClientSession, StdioServerParameters, stdio_client


async def smoke() -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "bing_webmaster_ai_cli_mcp.mcp_server"],
    )
    async with stdio_client(parameters) as streams, ClientSession(*streams) as session:
        initialized = await session.initialize()
        result = await session.list_tools()
    names = [tool.name for tool in result.tools]
    if not names:
        raise RuntimeError("MCP server initialized but returned no tools")
    if any("apply" in name for name in names):
        raise RuntimeError("MCP server exposed a forbidden apply tool")
    print(f"initialized {initialized.server_info.name}; {len(names)} tools")


def main() -> None:
    anyio.run(smoke)


if __name__ == "__main__":
    main()

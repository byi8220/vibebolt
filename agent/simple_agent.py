# Pretty much the most basic agent you could make for Vibebolt.
# Maybe a second agent that returns feedback and can gather is in order.
from agents import Agent, Runner
from agents.mcp import MCPServerStdio
import os
import argparse
import asyncio

# TODO: Be better at prompt engineering
instructions =  "You are a coding assistant who is proficient in writing performant Rust code. " \
                "You will either be given either a task or code snippet and access to a suite " \
                "of tools called `Vibebolt`, which offers a sandboxed environment to build, run, and test your code. " \
                "Perform the task provided by the user, with an emphasis on both correctness and performance. " \
                "If you are given a code snippet, you should review it and suggest ways to speed it up." \
                "If the code snippet is in another language, you should rewrite it in Rust. " \
                "You must write a variety of non-trivial test cases to ensure the code is correct. " \
                "If given a code snippet, you should benchmark your new code against the original code to demonstrate your code is indeed faster. " \
                "If you are given a task, you should consider multiple approaches, and choose the one that is the most performant. " \
                "Consider time complexity, cache awareness, and low level optimizations while designing your code." \
                "If you are repeatedly running into the same issues, take a step back and consider what is wrong with your approach. "

async def main(args):
    stdio_server = MCPServerStdio(
        params = {
            "command": "uv",
            "args": ["run, --with", "docker", "--with", "mcp[cli]", "mcp", "run", args.server],
            "env": os.environ
        },
        cache_tools_list=True,
        name="Vibebolt Server"
    )
    async with stdio_server as server:
        agent = Agent(
            agent = "Vibebolt Coder",
            instructions = instructions,
            mcp_servers=[stdio_server],
        )
        while True:
            prompt = input(">>>")
            result = await Runner.run(agent, prompt)
            print(result)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", type=str, help="Path to stdio server file")
    args = parser.parse_args()

    asyncio.run(main(args))
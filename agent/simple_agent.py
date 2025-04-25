# Pretty much the most basic agent you could make for Vibebolt.
# Maybe a second agent that returns feedback and can gather is in order.
from agents import Agent, Runner
from agents.mcp import MCPServerSse
from openai.types.responses import ResponseTextDeltaEvent
import os
import argparse
import asyncio

# TODO: Be better at prompt engineering
instructions =  """You are a coding assistant who is proficient in writing performant Rust code. 
                You will either be given either a task or code snippet and access to a suite 
                of tools called `Vibebolt`, which offers a sandboxed environment to build, run, and test your code. 
                Perform the task provided by the user, with an emphasis on both correctness and performance following the guidelines below.
                1. If you are given a code snippet, you should review it and suggest ways to speed it up. If the code snippet is in another language, you should rewrite it in Rust. 
                2. You **must** write a variety of non-trivial test cases to ensure the code is correct. 
                3. You **must** run have many test iterations to get a good benchkmark.
                3. If given a code snippet, you should benchmark your new code against the original code to demonstrate your code is indeed faster. 
                4. If you are given a task, you should consider multiple approaches, and choose the one that is the most performant. 
                5. Consider time complexity, cache awareness, and low level optimizations while designing your code.
                6. If you are repeatedly running into the same issues or error, reason about why this is happening.
                7. Explain your reasoning step by step.
                """

async def main(args):
    sse_server = MCPServerSse(
        params = {
            "url": f"http://{args.host}:{args.port}/sse",
            "env": dict(os.environ),
        },
        cache_tools_list=True,
        name="Vibebolt Server"
    )
    async with sse_server as server:
        agent = Agent(
            name = "Vibebolt Coder",
            instructions = instructions,
            mcp_servers=[server],
        )
        while True:
            prompt = input(">>>")
            streaming_result = Runner.run_streamed(agent, prompt)
            async for event in streaming_result.stream_events():
                # Only handle raw response text deltas
                if event.type == "raw_response_event" and isinstance(event.data, ResponseTextDeltaEvent):
                    print(event.data.delta, end="", flush=True)  # prints each token as it arrives
                if event.type == "run_item_stream_event":
                    item = event.item
                    tool_name = getattr(item.raw_item, "name", "")
                    args_name = getattr(item.raw_item, "arugments", "")
                    if item.type == "tool_call_item":
                        print(f"\n Tool called: `{tool_name}` with args: ({args_name})", flush=True)
                    elif item.type == "tool_call_output_item":
                        print(item.output, flush=True)
            print()
    
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, help="Server port", default=8080)
    parser.add_argument("--host", type=str, help="Server host", default="localhost")
    args = parser.parse_args()

    asyncio.run(main(args))